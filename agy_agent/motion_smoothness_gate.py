#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
motion_smoothness_gate.py

실측 프레임 타이밍(FPS / jank / 꼬리 지연)을 제품 심도 게이트(interaction_polish)에 연결한다.

■ 배경
  기존 '쫀득한 애니메이션' 판정은 (1) 소스에 모션 토큰이 있는지(정적 토큰),
  (2) 서로 다른 두 상태의 프레임버퍼가 존재하는지(상태 전이)로만 이뤄졌다.
  그러나 "즉시 툭 끊기는 하드 컷"도 두 상태 전이는 만족하므로 '쫀득함'을 입증하지 못한다.
  실제 부드러움은 **프레임 예산**(평균 FPS, p95 프레임 시간, 느린 프레임 비율)으로만
  증명할 수 있다.

■ 역할
  순수/읽기 전용이며 LLM 호출 없이 디스크의 성능 리포트만으로 판정한다. 따라서
  할당량 차단(quota firewall)으로 LLM이 막힌 무인 모드에서도 동일하게 작동한다.

■ 생산자 / 소비자
  - 생산자: E2E 벤치마크 러너가 ``save_report()`` 로 표준 경로에 리포트를 남긴다.
  - 소비자: ProductRefinementGovernor.evaluate 가 리포트를 읽어 interaction_polish 의
            런타임 증거(verified/failed)로 반영한다.

■ 안전 방향 (fail-closed)
  이 게이트는 판정을 **엄격하게만** 만든다. 부드러움이 증명되면 심화 강제를 해제할 수 있고,
  정체(jank)가 측정되면 심화를 강제한다. 리포트가 오래되었거나 표본이 부족하면 아무 판정도
  하지 않아 기존 동작을 보존한다(오탐으로 인한 잘못된 해제 금지).
"""

from __future__ import annotations

import os
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("MotionSmoothnessGate")

# ──────────────────────────────────────────────────────────────────────────
# 임계값 (프레임 예산) — 60fps 표준
# ──────────────────────────────────────────────────────────────────────────
SLOW_FRAME_THRESHOLD_MS = 16.67     # 60fps 1프레임 예산
MIN_AVG_FPS = 55.0                   # 평균 FPS 하한 (60fps 대비 92% 여유)
MAX_P95_FRAME_MS = 20.0              # p95 프레임 시간 상한 (꼬리 지연)
MAX_SLOW_FRAME_RATIO = 0.05          # 느린 프레임 비율 상한 (5%)
MIN_SAMPLE_FRAMES = 30               # 통계적 유의성 최소 표본 수

# 표준 리포트 경로 (워크스페이스 상대)
REPORT_REL = os.path.join("reports", "product_depth", "performance_benchmark.json")

# 기본 모션 토큰 (거버너가 자체 토큰 뱅크를 주입하면 그쪽이 우선)
MOTION_TOKENS = (
    "animat", "animator", "transition", "tween", "easing", "easein", "easeout",
    "ease_in", "ease_out", "interpolat", "spring", "animatedvisibility",
    "cubicbezier", "haptic", "vibrat", "ripple", "motionlayout", "framer",
    "lerp", "durationsmillis", "animationspec", "bounce", "squash", "particle",
    "keyframe", "damping", "stiffness", "overshoot", "lottie", "motion",
)

# ──────────────────────────────────────────────────────────────────────────
# 판정 모델
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class FrameTimingVerdict:
    """프레임 타이밍 실측 판정 결과."""
    measured: bool = False           # 유효한 측정치가 있었는가
    smooth: bool = False             # '쫀득함'(프레임 예산 충족) 여부
    avg_fps: Optional[float] = None
    p95_ms: Optional[float] = None
    slow_frame_ratio: Optional[float] = None
    sample_count: int = 0
    reason: str = "측정치 없음"

    def metrics_line(self) -> str:
        fps = f"{self.avg_fps:.1f}fps" if self.avg_fps is not None else "fps N/A"
        p95 = f"p95 {self.p95_ms:.1f}ms" if self.p95_ms is not None else "p95 N/A"
        slow = (
            f"느린프레임 {self.slow_frame_ratio * 100:.1f}%"
            if self.slow_frame_ratio is not None else "느린프레임 N/A"
        )
        return f"{fps} / {p95} / {slow} / {self.sample_count}프레임"

    def detail(self) -> str:
        return f"{self.reason} ({self.metrics_line()})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "measured": self.measured,
            "smooth": self.smooth,
            "avg_fps": self.avg_fps,
            "p95_ms": self.p95_ms,
            "slow_frame_ratio": self.slow_frame_ratio,
            "sample_count": self.sample_count,
            "reason": self.reason,
        }


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def assess_frame_timing(
    avg_fps: Any,
    p95_ms: Any,
    slow_frames: Any = 0,
    sample_count: Any = 0,
    slow_threshold_ms: float = SLOW_FRAME_THRESHOLD_MS,
) -> FrameTimingVerdict:
    """원시 프레임 타이밍 수치를 '쫀득함' 판정으로 환산한다.

    표본이 부족하거나 수치가 유효하지 않으면 ``measured=False`` 를 반환하여
    호출부가 기존 동작을 보존하도록 한다(오탐 방지).
    """
    fps = _coerce_float(avg_fps)
    p95 = _coerce_float(p95_ms)
    try:
        samples = int(sample_count or 0)
    except (TypeError, ValueError):
        samples = 0
    try:
        slow = int(slow_frames or 0)
    except (TypeError, ValueError):
        slow = 0

    if fps is None or fps <= 0 or p95 is None or p95 <= 0:
        return FrameTimingVerdict(measured=False, reason="프레임 타이밍 수치 누락/무효")
    if samples < MIN_SAMPLE_FRAMES:
        return FrameTimingVerdict(
            measured=False, avg_fps=fps, p95_ms=p95, sample_count=samples,
            reason=f"표본 부족({samples} < {MIN_SAMPLE_FRAMES}) — 판정 보류",
        )

    slow_ratio = max(0.0, min(1.0, slow / max(1, samples)))

    violations: List[str] = []
    if fps < MIN_AVG_FPS:
        violations.append(f"평균 FPS {fps:.1f} < {MIN_AVG_FPS:.0f}")
    if p95 > MAX_P95_FRAME_MS:
        violations.append(f"p95 프레임 {p95:.1f}ms > {MAX_P95_FRAME_MS:.0f}ms")
    if slow_ratio > MAX_SLOW_FRAME_RATIO:
        violations.append(f"느린 프레임 {slow_ratio * 100:.1f}% > {MAX_SLOW_FRAME_RATIO * 100:.0f}%")

    if violations:
        return FrameTimingVerdict(
            measured=True, smooth=False, avg_fps=fps, p95_ms=p95,
            slow_frame_ratio=slow_ratio, sample_count=samples,
            reason="프레임 예산 위반(jank): " + ", ".join(violations),
        )
    return FrameTimingVerdict(
        measured=True, smooth=True, avg_fps=fps, p95_ms=p95,
        slow_frame_ratio=slow_ratio, sample_count=samples,
        reason="프레임 예산 충족(60fps 쫀득한 동작 입증)",
    )


# ──────────────────────────────────────────────────────────────────────────
# 리포트 입출력
# ──────────────────────────────────────────────────────────────────────────
def report_path(workspace: str) -> str:
    return os.path.join(workspace, REPORT_REL)


def save_report(workspace: str, payload: Dict[str, Any]) -> Optional[str]:
    """E2E 벤치마크 러너가 프레임 타이밍 성능 리포트를 표준 경로에 기록한다.

    ``payload`` 는 최소한 ``avg_fps``, ``p95_ms`` 를 포함해야 하며 ``slow_frames``,
    ``sample_count`` 를 권장한다. ``updated_at`` 이 없으면 현재 시각을 채운다.
    """
    if not workspace or not os.path.isdir(workspace) or not isinstance(payload, dict):
        return None
    data = dict(payload)
    data.setdefault("updated_at", time.time())
    data.setdefault("schema", "performance_benchmark/v1")
    path = report_path(workspace)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        logger.info(f"[MotionGate] 성능 리포트 기록 -> {path}")
        return path
    except Exception as e:
        logger.warning(f"[MotionGate] 성능 리포트 기록 실패: {e}")
        return None


def load_report(workspace: Optional[str]) -> Optional[Dict[str, Any]]:
    """표준 경로의 프레임 타이밍 성능 리포트를 읽는다. 없으면 ``None``."""
    if not workspace or not os.path.isdir(workspace):
        return None
    path = report_path(workspace)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return None
        data.setdefault("_path", path)
        return data
    except Exception as e:
        logger.debug(f"[MotionGate] 성능 리포트 판독 실패: {e}")
        return None


def report_timestamp(report: Optional[Dict[str, Any]]) -> Optional[float]:
    """리포트의 신선도 판정용 타임스탬프(초). 없으면 파일 mtime, 그것도 없으면 None."""
    if not report:
        return None
    ts = _coerce_float(report.get("updated_at"))
    if ts is not None:
        return ts
    path = report.get("_path")
    if path and os.path.isfile(path):
        try:
            return os.path.getmtime(path)
        except Exception:
            return None
    return None


def has_motion_tokens(
    source_map: Dict[str, str], tokens: Optional[Iterable[str]] = None
) -> bool:
    """소스 파일들에 측정 가능한 모션(애니메이션/이징/스프링/햅틱) 토큰이 존재하는가.

    모션이 아예 없는 UI 는 프레임 예산이 아무리 좋아도 '쫀득한 동작'을 입증할 수 없다.
    """
    bank = tuple(tokens if tokens is not None else MOTION_TOKENS)
    if not bank:
        return False
    blob = " ".join(v or "" for v in source_map.values()).lower()
    return any(t in blob for t in bank)


def evaluate(
    workspace: Optional[str],
    source_map: Optional[Dict[str, str]] = None,
    motion_tokens: Optional[Iterable[str]] = None,
) -> FrameTimingVerdict:
    """워크스페이스의 성능 리포트를 읽어 최종 '쫀득함' 판정을 반환한다.

    ``source_map`` 이 주어지면 모션 토큰 존재 여부까지 반영해 ``smooth`` 를 확정한다.
    리포트가 없거나 표본이 부족하면 ``measured=False`` (기존 동작 보존).
    """
    report = load_report(workspace)
    if not report:
        return FrameTimingVerdict(measured=False, reason="성능 리포트 없음")
    verdict = assess_frame_timing(
        report.get("avg_fps"),
        report.get("p95_ms"),
        report.get("slow_frames", 0),
        report.get("sample_count", 0),
    )
    if not verdict.measured:
        return verdict
    if verdict.smooth and source_map is not None and not has_motion_tokens(source_map, motion_tokens):
        # 프레임 예산은 좋지만 애니메이션이 아예 없으면 '쫀득함'이 아니다.
        verdict.smooth = False
        verdict.reason = "측정 가능한 모션 토큰 부재 — '쫀득한 동작' 입증 불가"
    return verdict


if __name__ == "__main__":  # pragma: no cover
    import sys
    logging.basicConfig(level=logging.INFO)
    ws = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    v = evaluate(ws)
    print(json.dumps(v.to_dict(), ensure_ascii=False, indent=2))
