#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_improvement_discovery_contract.py

QA '개선점 탐색 계약(Improvement Discovery Contract)' 회귀 테스트.

배경: 기존 QA는 "틀린 게 있나?"(차단 결함)만 기계 검증하고 곧바로 승인하여
'어색함/누락/개선 여지/사용자 만족'을 놓쳤다. 본 계약은 QA를 PASS/FAIL 판정기가
아니라 결함 탐지기 + 개선점 탐색기로 강제한다.

검증 항목:
1. `[IMPROVEMENTS: N]` 마커 파싱 (정상/0/공백/유니코드 콜론/다중 마커/부재).
2. 개선점 탐색 계약 문구가 5대 질문·최소 3개·무비판 승인 금지·반증 근거를 포함하는지.
3. QA1 / 확장 QA / 엔진 프롬프트에 계약이 실제로 배선되었는지.
4. 계약 마커가 텔레메트리에 물리 기록되고, 미방출 시 경고가 뜨는지.
"""

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from convergence_policy import (  # noqa: E402
    IMPROVEMENT_DISCOVERY_QUESTIONS,
    IMPROVEMENT_MARKER_SYNTAX,
    ConvergenceTelemetry,
    build_improvement_discovery_instruction,
    parse_improvement_count,
)


class TestImprovementMarkerParsing(unittest.TestCase):
    def test_parses_standard_marker(self):
        self.assertEqual(
            parse_improvement_count("[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n[IMPROVEMENTS: 3]"),
            3,
        )

    def test_parses_zero(self):
        self.assertEqual(parse_improvement_count("[IMPROVEMENTS: 0]"), 0)

    def test_parses_spaces_and_fullwidth_colon(self):
        self.assertEqual(parse_improvement_count("[ IMPROVEMENTS :  5 ]"), 5)
        self.assertEqual(parse_improvement_count("[IMPROVEMENTS：7]"), 7)

    def test_last_marker_wins(self):
        self.assertEqual(parse_improvement_count("[IMPROVEMENTS: 9]\n...\n[IMPROVEMENTS: 2]"), 2)

    def test_missing_marker_returns_none(self):
        self.assertIsNone(parse_improvement_count("[STATUS: APPROVED]"))
        self.assertIsNone(parse_improvement_count(""))
        self.assertIsNone(parse_improvement_count(None))


class TestImprovementDiscoveryInstruction(unittest.TestCase):
    def setUp(self):
        self.text = build_improvement_discovery_instruction()

    def test_contains_five_questions(self):
        for q in IMPROVEMENT_DISCOVERY_QUESTIONS:
            self.assertIn(q, self.text)
        # 사용자가 정의한 5대 질문의 핵심 키워드 고정
        self.assertIn("틀린", self.text)
        self.assertIn("어색", self.text)
        self.assertIn("빠진", self.text)
        self.assertIn("더 좋아질", self.text)
        self.assertIn("사용자가 만족", self.text)

    def test_mandates_min_three_findings_and_anti_rubber_stamp(self):
        self.assertIn("최소 3개", self.text)
        self.assertIn("무비판 승인", self.text)
        self.assertIn("반증 근거", self.text)
        self.assertIn("개선점 0개", self.text)

    def test_marker_syntax_present(self):
        self.assertIn(IMPROVEMENT_MARKER_SYNTAX, self.text)
        self.assertIn("[IMPROVEMENTS:", self.text)

    def test_respects_non_blocking_convergence(self):
        # 개선점은 차기 백로그이며 단독 REJECTED 사유가 아님을 명시해야 한다.
        self.assertIn("차기 백로그", self.text)
        self.assertIn("REJECTED", self.text)


class TestImprovementDiscoveryWiring(unittest.TestCase):
    def setUp(self):
        self.trio = (WORKSPACE_ROOT / "trio_agent_runner.py").read_text(encoding="utf-8")
        self.engine = (WORKSPACE_ROOT / "engine_manager.py").read_text(encoding="utf-8")

    def test_trio_injects_contract_into_both_qa_prompts(self):
        # QA1 + 확장 QA 양쪽에 계약이 주입되어야 한다.
        self.assertGreaterEqual(
            self.trio.count("build_improvement_discovery_instruction"), 2,
            "QA1/확장 QA 프롬프트 양쪽에 개선점 탐색 계약을 주입해야 합니다.",
        )
        self.assertGreaterEqual(self.trio.count("IMPROVEMENTS:"), 2)

    def test_trio_parses_and_records_improvement_telemetry(self):
        self.assertIn("parse_improvement_count", self.trio)
        self.assertIn("ConvergenceTelemetry.record_improvements", self.trio)

    def test_engine_manager_injects_contract(self):
        self.assertIn("build_improvement_discovery_instruction", self.engine)

    def test_engine_manager_build_qa_prompt_contains_contract(self):
        from engine_manager import EngineManager
        prompt = EngineManager.build_qa_prompt("UI 검증", "/tmp")
        self.assertIn("개선점 탐색", prompt)
        self.assertIn("[IMPROVEMENTS:", prompt)


class TestImprovementTelemetry(unittest.TestCase):
    def setUp(self):
        ConvergenceTelemetry.reset_all()

    def test_records_and_summarizes(self):
        ConvergenceTelemetry.record_improvements(1001, 3)
        ConvergenceTelemetry.record_improvements(1001, 2)
        summary = ConvergenceTelemetry.summary(1001)
        self.assertEqual(summary["improvement_observed"], 2)
        self.assertEqual(summary["total_improvements"], 5)
        self.assertEqual(summary["improvements"], [3, 2])

    def test_none_is_ignored(self):
        ConvergenceTelemetry.record_improvements(1002, None)
        summary = ConvergenceTelemetry.summary(1002)
        self.assertEqual(summary["improvement_observed"], 0)

    def test_warns_when_contract_never_emitted(self):
        ConvergenceTelemetry.record(1003, "qa1", 1, "marker", "observed")
        summary = ConvergenceTelemetry.summary(1003)
        self.assertTrue(
            any("IMPROVEMENTS" in w for w in summary["warnings"]),
            f"개선점 마커 미방출 경고가 없습니다: {summary['warnings']}",
        )


if __name__ == "__main__":
    unittest.main()
