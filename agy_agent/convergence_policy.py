#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convergence_policy.py

!mode 3/3+ 무인 자율 순환의 **수렴 정책 (Convergence & Honesty Contract)**.

■ 배경 (v12 반복 실패의 구조적 원인)
------------------------------------------------
기존 루프는 종료를 오직 "라운드 수(PAIR_MAX_ROUNDS)"로만 판단했다. 그 결과:

  1. 사용자 체감 결함이 이미 줄었는데도 문서/증거 인프라(해시·타임스탬프·게이트 JSON
     포맷) 같은 **비차단 항목**으로 계속 REJECTED 되어 무한 핑퐁에 빠졌다.
  2. 반대로 진짜 **차단 결함**이 전혀 나아지지 않는데도 상한까지 라운드를 소진했다.
  3. "실행하지 않은 테스트를 통과로 기재", "상태를 표현하지 않는 캡처를 근거로 제출"
     같은 **거짓 증거**가 비차단 항목과 뒤섞여 판정이 흐려졌다.

■ 해결
------------------------------------------------
QA는 반려 시 사용자 체감 차단 결함 수를 기계 판독 가능한 마커로 방출한다::

    [BLOCKING_DEFECTS: N]

런너는 이 수치의 **추세**로 수렴/에스컬레이션을 판정한다.

  - ``N == 0``          → 남은 것은 비차단(백로그)뿐 → 승인으로 수렴
  - ``N`` 감소          → 진척 중 → 계속 진행
  - ``N`` 정체/증가 2회 → 조기 에스컬레이션 (상한까지 태우지 않음)

마커가 없으면(None) 기존 동작을 그대로 보존하는 보수적 폴백으로 작동한다.
즉 이 모듈은 "있으면 더 똑똑해지고, 없으면 아무것도 망가뜨리지 않는다".
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

# QA/확장 QA 프롬프트에 공통 주입되는 정책 문구의 식별자 (단일 진실 원천)
BLOCKING_MARKER_SYNTAX = "[BLOCKING_DEFECTS: N]"

# 차단 결함 = 사용자 체감 결함 또는 정직성 위반. 이 항목만 REJECTED 사유가 된다.
BLOCKING_DEFECT_CATEGORIES = (
    "빌드/컴파일 실패",
    "앱/런타임 크래시",
    "사용자 요청 미구현",
    "핵심 기능 미동작·클릭 불가·화면 잘림 등 사용자 체감 결함",
    "Mock/Stub/TODO 껍데기 및 레퍼런스 표절",
    "산출물·테스트·스크린샷에 대한 거짓 주장(정직성 위반)",
)

# 비차단 = 차기 백로그로만 기록. 이 항목 단독으로는 REJECTED 선언 불가.
NON_BLOCKING_DEFECT_CATEGORIES = (
    "코드 스타일 및 리팩토링 권장",
    "문서/증거 파일 포맷, 해시·타임스탬프 신선도 등 메타 인프라",
    "사용자 체감과 무관한 이론적 엣지 케이스",
)

_BLOCKING_RE = re.compile(r"\[\s*BLOCKING_DEFECTS\s*[:：]\s*(\d+)\s*\]", re.IGNORECASE)


def parse_blocking_defect_count(qa_output: Optional[str]) -> Optional[int]:
    """QA 출력에서 ``[BLOCKING_DEFECTS: N]`` 마커를 파싱한다.

    마커가 없으면 ``None`` 을 반환하여 호출부가 기존 동작을 유지하도록 한다.
    마커가 여러 개면 마지막(최종 판정) 값을 신뢰한다.
    """
    if not qa_output or not isinstance(qa_output, str):
        return None
    matches = _BLOCKING_RE.findall(qa_output)
    if not matches:
        return None
    try:
        return max(0, int(matches[-1]))
    except (TypeError, ValueError):
        return None


def has_blocking_marker(qa_output: Optional[str]) -> bool:
    """QA 출력에 차단 결함 마커가 존재하는가."""
    return parse_blocking_defect_count(qa_output) is not None


# ---------------------------------------------------------------------------
# 폴백 추정 (Fallback Heuristic)
#   마커는 LLM이 "계약에 따라" 내보내는 것이므로 실제로는 누락될 수 있다.
#   마커가 없을 때 수렴 정책이 조용히 비활성화되는 것을 막기 위해,
#   반려 본문의 결함 서술을 키워드로 분류해 차단/비차단을 추정한다.
# ---------------------------------------------------------------------------
BLOCKING_KEYWORDS: Tuple[str, ...] = (
    # 사용자 체감 / 기능
    "빌드 실패", "컴파일 에러", "컴파일 실패", "크래시", "앱이 죽", "crash",
    "미동작", "동작하지 않", "작동하지 않", "클릭 불가", "클릭해도", "반응 없", "무반응",
    "화면 잘림", "잘려", "오버플로우", "레이아웃 깨", "깨짐", "깨졌", "미구현", "반영되지 않",
    "구현되지 않", "안 된다", "실패", "오작동", "데이터 손실", "손실", "누출",
    "무한 루프", "무한루프", "교착",
    # 코드 사실성
    "스텁", "stub", "목업", "더미", "가짜", "mock", "fake", "dead code", "도달 불가",
    "no-op", "실행되지 않", "잘못된 화면", "빈 화면",
    # 정직성
    "거짓", "위조", "표절", "재탕", "허위", "조작", "정직성",
    "실행하지 않은", "실행 안 된", "실행되지 않은", "통과로 기재", "근거로 제출",
    "기재되어 있", "보고되지 않은",
    "build fail", "not implemented", "broken", "overflow", "clipped", "false claim",
    "plagiar", "unreachable",
)

NON_BLOCKING_KEYWORDS: Tuple[str, ...] = (
    "스타일", "리팩토링", "리팩터링", "주석", "문서", "문서화", "해시", "타임스탬프",
    "신선도", "포맷", "포맿", "네이밍", "변수명", "중복 코드", "권고", "제안", "향후",
    "차기", "백로그", "이후 개선",
    "style", "refactor", "naming", "documentation", "typo", "hash", "timestamp",
    "format", "recommend", "backlog", "nit",
)

_DEFECT_CUE_RE = re.compile(
    r"(결함|문제|위반|미구현|오류|버그|사유|발견|확인 필요|\[D\d|\[High|\[Med|\[Low|"
    r"defect|issue|bug|violation|problem)",
    re.IGNORECASE,
)


def _iter_defect_statements(qa_output: str) -> List[str]:
    """반려 본문에서 '결함 서술' 후보 줄만 뽑는다."""
    out: List[str] = []
    for raw in qa_output.splitlines():
        line = raw.strip()
        if len(line) < 6:
            continue
        if line.startswith(("```", "---", "===", "###", "##", "#")):
            continue
        if "[STATUS:" in line and "BLOCKING_DEFECTS" not in line:
            continue
        if _DEFECT_CUE_RE.search(line):
            out.append(line)
    return out


def estimate_blocking_defect_count(qa_output: Optional[str]) -> Optional[int]:
    """마커 없이 반려 본문에서 차단 결함 수를 추정한다. 판단 불가하면 ``None``.

    결함 서술 줄마다 차단/비차단 키워드를 매칭한다. 양쪽 모두 걸리면(모호)
    **차단으로 leaning** 하여 오승인을 막는다. 결함 서술을 못 찾으면 ``None``.
    """
    if not qa_output or not isinstance(qa_output, str):
        return None
    if has_blocking_marker(qa_output):
        return None  # 마커가 있으면 마커가 유일한 진실 원천

    statements = _iter_defect_statements(qa_output)
    if not statements:
        return None

    blocking_hits = 0
    for line in statements:
        low = line.lower()
        if any(k in low for k in BLOCKING_KEYWORDS):
            blocking_hits += 1
        # 그 외(비차단 키워드 / 무관한 서술)는 차단으로 세지 않는다.
    return blocking_hits


def resolve_blocking_count(qa_output: Optional[str]) -> Tuple[Optional[int], str]:
    """차단 결함 수와 그 근거 출처를 반환한다.

    Returns:
        ``(count, source)`` — source 는 ``"marker"`` | ``"heuristic"`` | ``"none"``
    """
    marker = parse_blocking_defect_count(qa_output)
    if marker is not None:
        return marker, "marker"
    est = estimate_blocking_defect_count(qa_output)
    if est is not None:
        return est, "heuristic"
    return None, "none"


def build_blocking_contract_instruction() -> str:
    """QA 프롬프트에 공통 주입되는 수렴/정직성 계약 문구 (단일 진실 원천)."""
    blocking = "\n".join(f"   - {c}" for c in BLOCKING_DEFECT_CATEGORIES)
    non_blocking = ", ".join(NON_BLOCKING_DEFECT_CATEGORIES)
    return (
        "**[🎯 수렴 & 정직성 계약 (Convergence & Honesty Contract)]**:\n"
        "• **차단 결함(REJECTED 사유)** — 다음에 해당할 때만 반려하십시오:\n"
        f"{blocking}\n"
        f"• **비차단 항목([차기 백로그]로만 기록)** — {non_blocking}. "
        "이 항목 단독으로는 REJECTED를 선언할 수 없습니다.\n"
        "• **정직성 위반은 비차단이 아닙니다**: 계측이 불가능하면 `BLOCKED`로 정직하게 기록하고, "
        "실행하지 않은 테스트의 통과·상태를 표현하지 않는 캡처·재현 불가 수치의 재현을 주장하지 마십시오. "
        "거짓 주장은 즉시 REJECTED입니다.\n"
        f"• **필수 마커**: 판정과 무관하게 둘째 줄에 차단 결함 개수를 `{BLOCKING_MARKER_SYNTAX}` 형식으로 "
        "표기하십시오. (차단 결함이 없으면 0)\n"
        "• **수렴 지향**: 이전 라운드에서 APPROVED된 항목을 재반려하거나 골대를 이동하지 마십시오."
    )


class ConvergenceTracker:
    """채널별 차단 결함 수 추이를 기록하고 수렴/에스컬레이션을 판정한다.

    클래스 레벨 상태(채널 ID 키)를 사용하므로 여러 파이프라인 인스턴스에서 공유된다.
    """

    _history: Dict[int, List[int]] = {}
    _stall_streak: Dict[int, int] = {}
    _HISTORY_CAP = 16

    @classmethod
    def reset(cls, channel_id: int) -> None:
        """특정 채널의 추이 기록을 초기화한다(새 미션 시작 시)."""
        cls._history.pop(channel_id, None)
        cls._stall_streak.pop(channel_id, None)

    @classmethod
    def reset_all(cls) -> None:
        cls._history.clear()
        cls._stall_streak.clear()

    @classmethod
    def record(cls, channel_id: int, blocking_count: Optional[int]) -> None:
        """차단 결함 수를 기록한다. ``None``(마커 없음)은 추세 판정에서 제외한다."""
        if blocking_count is None:
            return
        count = max(0, int(blocking_count))
        hist = cls._history.setdefault(channel_id, [])
        prev = hist[-1] if hist else None
        hist.append(count)
        if len(hist) > cls._HISTORY_CAP:
            del hist[: len(hist) - cls._HISTORY_CAP]

        if prev is None:
            cls._stall_streak[channel_id] = 0
        elif count < prev:
            # 진척: 차단 결함이 줄었으므로 정체 카운터 리셋
            cls._stall_streak[channel_id] = 0
        else:
            # 정체 또는 증가
            cls._stall_streak[channel_id] = cls._stall_streak.get(channel_id, 0) + 1

    @classmethod
    def last_count(cls, channel_id: int) -> Optional[int]:
        hist = cls._history.get(channel_id)
        return hist[-1] if hist else None

    @classmethod
    def history(cls, channel_id: int) -> List[int]:
        return list(cls._history.get(channel_id) or [])

    @classmethod
    def classify(cls, channel_id: int) -> str:
        """``converged`` | ``progressing`` | ``stagnant`` | ``unknown``."""
        last = cls.last_count(channel_id)
        if last is None:
            return "unknown"
        if last == 0:
            return "converged"
        if cls._stall_streak.get(channel_id, 0) >= 2:
            return "stagnant"
        return "progressing"

    @classmethod
    def should_force_accept(cls, channel_id: int) -> bool:
        """QA가 반려했어도 차단 결함이 0이면 비차단(백로그)만 남은 것 → 수렴 승인."""
        return cls.last_count(channel_id) == 0

    @classmethod
    def should_escalate(cls, channel_id: int) -> bool:
        """차단 결함이 2회 연속 줄지 않으면(정체/증가) 조기 에스컬레이션."""
        last = cls.last_count(channel_id)
        if last is None or last <= 0:
            return False
        return cls._stall_streak.get(channel_id, 0) >= 2

    @classmethod
    def describe(cls, channel_id: int) -> str:
        hist = cls.history(channel_id)
        trend = " → ".join(str(v) for v in hist) if hist else "기록 없음"
        return f"차단 결함 추이: {trend} (판정: {cls.classify(channel_id)})"


class ConvergenceTelemetry:
    """수렴 계약이 **실제로 작동하는지** 증명하기 위한 계측.

    과거의 가장 큰 착각은 "구현했다 = 동작한다"였다. 이 클래스는 매 라운드의
    마커 방출 여부·근거 출처(marker/heuristic/none)·수렴/에스컬레이션 발생 횟수를
    기록하고, 워크스페이스에 물리 리포트(``reports/convergence_telemetry.json``)로 남긴다.

    판독:
        ``marker_emission_rate`` 가 낮으면 QA 프롬프트 계약이遵守되지 않은 것이며,
        ``heuristic_used`` 가 그 보정 작동 횟수다. 둘 다 0이면 수렴 정책이 사실상 죽은 상태다.
    """

    _data: Dict[int, Dict[str, Any]] = {}

    @classmethod
    def _bucket(cls, channel_id: int) -> Dict[str, Any]:
        return cls._data.setdefault(
            channel_id,
            {
                "rounds_observed": 0,
                "marker_present": 0,
                "heuristic_used": 0,
                "unresolved": 0,
                "force_accepts": 0,
                "escalations": 0,
                "blocking_counts": [],
                "stages": {},
            },
        )

    @classmethod
    def reset(cls, channel_id: Optional[int] = None) -> None:
        if channel_id is None:
            cls._data.clear()
        else:
            cls._data.pop(channel_id, None)

    @classmethod
    def reset_all(cls) -> None:
        cls._data.clear()

    @classmethod
    def record(
        cls,
        channel_id: int,
        stage: str,
        count: Optional[int],
        source: str,
        outcome: str = "observed",
    ) -> None:
        """한 번의 판정 관측을 기록한다.

        Args:
            stage: ``"qa1"`` / ``"qa2"`` … 판정 단계
            count: 차단 결함 수 (``None`` 이면 미해결)
            source: ``"marker"`` | ``"heuristic"`` | ``"none"``
            outcome: ``"observed"`` | ``"force_accept"`` | ``"escalate"``
        """
        b = cls._bucket(channel_id)
        b["rounds_observed"] += 1
        if source == "marker":
            b["marker_present"] += 1
        elif source == "heuristic":
            b["heuristic_used"] += 1
        else:
            b["unresolved"] += 1
        if count is not None:
            b["blocking_counts"].append(int(count))
        stages = b["stages"].setdefault(
            stage, {"observations": 0, "marker": 0, "heuristic": 0, "none": 0, "counts": []}
        )
        stages["observations"] += 1
        stages[source if source in ("marker", "heuristic", "none") else "none"] += 1
        if count is not None:
            stages["counts"].append(int(count))
        if outcome == "force_accept":
            b["force_accepts"] += 1
        elif outcome == "escalate":
            b["escalations"] += 1

    @classmethod
    def summary(cls, channel_id: int) -> Dict[str, Any]:
        """사람이 판독 가능한 요약 + 경고 플래그를 반환한다."""
        b = cls._data.get(channel_id)
        if not b:
            return {
                "channel_id": channel_id,
                "observations": 0,
                "marker_emission_rate": None,
                "contract_active": False,
                "warnings": ["관측 데이터 없음 — 수렴 계약이 한 번도 실행되지 않음"],
            }
        obs = b["rounds_observed"]
        resolved = b["marker_present"] + b["heuristic_used"]
        rate = (b["marker_present"] / obs) if obs else None
        warnings: List[str] = []
        if obs and b["marker_present"] == 0:
            warnings.append(
                "QA가 [BLOCKING_DEFECTS] 마커를 한 번도 내보내지 않음 — 프롬프트 계약 미준수, "
                "휴리스틱 폴백에만 의존 중"
            )
        if obs and b["unresolved"] == obs:
            warnings.append("차단 결함 수를 전혀 판정하지 못함 — 수렴 정책 비활성")
        return {
            "channel_id": channel_id,
            "observations": obs,
            "marker_present": b["marker_present"],
            "heuristic_used": b["heuristic_used"],
            "unresolved": b["unresolved"],
            "resolved": resolved,
            "marker_emission_rate": rate,
            "force_accepts": b["force_accepts"],
            "escalations": b["escalations"],
            "blocking_counts": list(b["blocking_counts"]),
            "per_stage": b["stages"],
            "contract_active": bool(obs and resolved > 0),
            "warnings": warnings,
        }

    @classmethod
    def export(
        cls,
        channel_id: int,
        workspace: Optional[str] = None,
        milestone: str = "",
    ) -> Optional[str]:
        """요약을 워크스페이스에 물리 리포트로 기록한다(감사 가능 산출물)."""
        if not workspace or not os.path.isdir(workspace):
            return None
        try:
            reports_dir = os.path.join(workspace, "reports")
            os.makedirs(reports_dir, exist_ok=True)
            path = os.path.join(reports_dir, "convergence_telemetry.json")
            payload = cls.summary(channel_id)
            payload["milestone"] = milestone
            payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            payload["schema"] = "convergence_telemetry/v1"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return path
        except Exception:
            return None
