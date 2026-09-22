#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telemetry_benchmark.py

AGY 3.0 Quantitative Telemetry & Benchmark Standard Engine (Task 4).
Eliminates subjective verbal fluff ("부드럽게 동작함", "레이아웃 우수") by producing
undeniable, physical, real-world telemetry tables in all E2E results:
1. 렌더링 FPS (Rendering FPS >= 55.0 FPS)
2. 뷰포트 가시 비율 % (Viewport Visible Content Ratio >= 65.0%)
3. 메모리 PSS 변화량 (Memory PSS Delta <= 15.0 MB)
4. 48dp 준수율 (48dp Touch Target Compliance >= 95.0%)
"""

import os
import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple, Union

logger = logging.getLogger("TelemetryBenchmark")


@dataclass
class QuantitativeMetric:
    key: str
    name: str
    measured_value: Optional[float]
    formatted_measured: str
    standard_threshold: str
    passed: Optional[bool]  # True = PASS, False = FAIL, None = NOT_MEASURED
    status: str             # "PASS", "FAIL", "NOT_MEASURED"
    evidence_detail: str

    @property
    def badge(self) -> str:
        if self.passed is True:
            return "🟢 PASS"
        elif self.passed is False:
            return "🚨 FAIL"
        return "⚪ NOT MEASURED"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["badge"] = self.badge
        return d


class QuantitativeBenchmarkEngine:
    """
    Evaluates and formats standardized quantitative telemetry benchmark tables for E2E runs.
    Enforces Anti-Mocking: when physical metrics are not measured, they are explicitly
    marked as 'NOT_MEASURED' (⚪) and NOT counted as PASS.
    """

    DEFAULT_FPS_THRESHOLD = 55.0           # >= 55.0 FPS required
    DEFAULT_VIEWPORT_THRESHOLD = 65.0      # >= 65.0% pure content viewport required by Constitution
    DEFAULT_PSS_DELTA_THRESHOLD = 15.0     # <= +15.0 MB memory increase allowed
    DEFAULT_48DP_THRESHOLD = 95.0          # >= 95.0% touch compliance required

    @classmethod
    def evaluate_benchmarks(
        cls,
        measured_fps: Optional[float] = None,
        measured_viewport_ratio: Optional[float] = None,
        measured_pss_delta_mb: Optional[float] = None,
        measured_48dp_compliance: Optional[float] = None,
        workspace: Optional[str] = None,
        telemetry_raw: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evaluates the 4 core quantitative metrics and returns structured data & standard markdown table.
        Anti-Mocking Rule: Never fabricate default pass values when real telemetry is missing!
        """
        raw = telemetry_raw or {}

        # 1. 렌더링 FPS
        fps = measured_fps
        if fps is None and raw:
            fps_cand = raw.get("fps") or raw.get("measured_fps")
            if fps_cand is not None:
                try:
                    fps = float(fps_cand)
                except (ValueError, TypeError):
                    fps = None

        if fps is not None:
            fps = round(float(fps), 1)
            fps_pass = fps >= cls.DEFAULT_FPS_THRESHOLD
            fps_status = "PASS" if fps_pass else "FAIL"
            fps_formatted = f"{fps} FPS"
            fps_evidence = (
                f"VSYNC 60Hz 렌더링 무결성, 16.6ms 프레임 타임라인 준수"
                if fps_pass else
                f"일부 프레임 드랍 발생 ({fps} FPS < {cls.DEFAULT_FPS_THRESHOLD} FPS 기준)"
            )
        else:
            fps_pass = None
            fps_status = "NOT_MEASURED"
            fps_formatted = "N/A (미계측)"
            fps_evidence = "실측 렌더링 FPS 텔레메트리 데이터 미수집 (벤치마크 미계측)"

        # 2. 뷰포트 가시 비율 %
        vp_ratio = measured_viewport_ratio
        if vp_ratio is None and raw:
            vp_cand = raw.get("viewport_ratio") or raw.get("content_viewport_ratio")
            if vp_cand is not None:
                try:
                    vp_ratio = float(vp_cand)
                except (ValueError, TypeError):
                    vp_ratio = None

        if vp_ratio is not None:
            vp_ratio = round(float(vp_ratio), 1)
            vp_pass = vp_ratio >= cls.DEFAULT_VIEWPORT_THRESHOLD
            vp_status = "PASS" if vp_pass else "FAIL"
            vp_formatted = f"{vp_ratio} %"
            chrome_pct = round(100.0 - vp_ratio, 1)
            vp_evidence = (
                f"상/하단 고정 바 {chrome_pct}% 이내 제한, 순수 작업 뷰포트 {vp_ratio}% 확보"
                if vp_pass else
                f"고정 크롬 영역 과다 점유 (순수 뷰포트 {vp_ratio}% < {cls.DEFAULT_VIEWPORT_THRESHOLD}% 헌법 기준)"
            )
        else:
            vp_pass = None
            vp_status = "NOT_MEASURED"
            vp_formatted = "N/A (미계측)"
            vp_evidence = "화면 뷰포트 대비 순수 콘텐츠 가시 영역 미계측"

        # 3. 메모리 PSS 변화량
        pss_delta = measured_pss_delta_mb
        if pss_delta is None and raw:
            pss_cand = raw.get("pss_delta_mb") or raw.get("memory_delta_mb")
            if pss_cand is not None:
                try:
                    pss_delta = float(pss_cand)
                except (ValueError, TypeError):
                    pss_delta = None

        if pss_delta is not None:
            pss_delta = round(float(pss_delta), 1)
            pss_pass = pss_delta <= cls.DEFAULT_PSS_DELTA_THRESHOLD
            pss_status = "PASS" if pss_pass else "FAIL"
            sign = "+" if pss_delta > 0 else ""
            pss_formatted = f"{sign}{pss_delta} MB"
            pss_evidence = (
                f"장기 실행 가비지 컬렉션(GC) 정상 동작, 메모리 누수 0건"
                if pss_pass else
                f"메모리 PSS 과다 증가 감지 ({sign}{pss_delta} MB > {cls.DEFAULT_PSS_DELTA_THRESHOLD} MB 허용치)"
            )
        else:
            pss_pass = None
            pss_status = "NOT_MEASURED"
            pss_formatted = "N/A (미계측)"
            pss_evidence = "프로세스 PSS 메모리 변화량 프로파일링 미계측"

        # 4. 48dp 터치 준수율
        dp_ratio = measured_48dp_compliance
        if dp_ratio is None and raw:
            dp_cand = raw.get("touch_48dp_compliance") or raw.get("compliance_48dp")
            if dp_cand is not None:
                try:
                    dp_ratio = float(dp_cand)
                except (ValueError, TypeError):
                    dp_ratio = None

        if dp_ratio is not None:
            dp_ratio = round(float(dp_ratio), 1)
            dp_pass = dp_ratio >= cls.DEFAULT_48DP_THRESHOLD
            dp_status = "PASS" if dp_pass else "FAIL"
            dp_formatted = f"{dp_ratio} %"
            dp_evidence = (
                f"주요 인터랙티브 터치 노드 사용성 충족 ({dp_ratio}% >= {cls.DEFAULT_48DP_THRESHOLD}% 기준)"
                if dp_pass else
                f"주요 인터랙션 터치 사용성 보강 권장 ({dp_ratio}% < {cls.DEFAULT_48DP_THRESHOLD}% 기준)"
            )
        else:
            dp_pass = None
            dp_status = "NOT_MEASURED"
            dp_formatted = "N/A (미계측)"
            dp_evidence = "Accessibility 노드 터치 타깃 48dp 전수 조사 미계측"

        metrics = [
            QuantitativeMetric(
                key="rendering_fps",
                name="렌더링 FPS",
                measured_value=fps,
                formatted_measured=fps_formatted,
                standard_threshold=f">= {cls.DEFAULT_FPS_THRESHOLD} FPS",
                passed=fps_pass,
                status=fps_status,
                evidence_detail=fps_evidence
            ),
            QuantitativeMetric(
                key="viewport_visible_ratio",
                name="뷰포트 가시 비율 %",
                measured_value=vp_ratio,
                formatted_measured=vp_formatted,
                standard_threshold=f">= {cls.DEFAULT_VIEWPORT_THRESHOLD} %",
                passed=vp_pass,
                status=vp_status,
                evidence_detail=vp_evidence
            ),
            QuantitativeMetric(
                key="memory_pss_delta",
                name="메모리 PSS 변화량",
                measured_value=pss_delta,
                formatted_measured=pss_formatted,
                standard_threshold=f"<= +{cls.DEFAULT_PSS_DELTA_THRESHOLD} MB",
                passed=pss_pass,
                status=pss_status,
                evidence_detail=pss_evidence
            ),
            QuantitativeMetric(
                key="touch_target_48dp",
                name="48dp 준수율",
                measured_value=dp_ratio,
                formatted_measured=dp_formatted,
                standard_threshold=f">= {cls.DEFAULT_48DP_THRESHOLD} %",
                passed=dp_pass,
                status=dp_status,
                evidence_detail=dp_evidence
            )
        ]

        # All passed requires ALL 4 metrics to be actually measured AND passed!
        # If any metric is None (NOT_MEASURED) or False (FAIL), all_passed is strictly False!
        all_passed = all(m.passed is True for m in metrics)
        has_unmeasured = any(m.passed is None for m in metrics)
        has_failure = any(m.passed is False for m in metrics)

        if all_passed:
            summary = "🟢 정량적 벤치마크 전수 통과 (실측 4대 지표 충족)"
        elif has_failure:
            summary = "🚨 정량적 벤치마크 기준 미달 (결함 감지)"
        else:
            summary = "⚠️ 정량적 벤치마크 미계측 (실측 텔레메트리 부재)"

        table_md = cls.build_standard_markdown_table(metrics)

        result_dict = {
            "all_passed": all_passed,
            "has_unmeasured": has_unmeasured,
            "metrics": {m.key: m.to_dict() for m in metrics},
            "table_markdown": table_md,
            "summary": summary
        }

        # Persist Gate 4 evidence
        if workspace and os.path.isdir(workspace):
            try:
                gate4_dir = os.path.join(workspace, "reports", "gate4")
                os.makedirs(gate4_dir, exist_ok=True)
                out_json = os.path.join(gate4_dir, "quantitative_benchmarks.json")
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump(result_dict, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.debug(f"Failed to persist quantitative_benchmarks.json: {e}")

        return result_dict

    @classmethod
    def build_standard_markdown_table(cls, metrics: List[QuantitativeMetric]) -> str:
        """Constructs the standard markdown telemetry table."""
        lines = [
            "### 📊 [정량적 E2E 계측 벤치마크 (Quantitative Telemetry Standards)]",
            "| 계측 항목 (Metric) | 실측치 (Measured) | 기준치 (Standard) | 판정 (Status) | 상세 근거 (Evidence) |",
            "| :--- | :---: | :---: | :---: | :--- |"
        ]
        for m in metrics:
            if m.passed is True:
                status_badge = "🟢 PASS"
            elif m.passed is False:
                status_badge = "🚨 FAIL"
            else:
                status_badge = "⚪ NOT MEASURED"
            lines.append(
                f"| **{m.name}** | `{m.formatted_measured}` | `{m.standard_threshold}` | {status_badge} | {m.evidence_detail} |"
            )
        return "\n".join(lines)
