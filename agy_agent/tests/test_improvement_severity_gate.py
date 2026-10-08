#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_improvement_severity_gate.py

'개선점 심각도(severity) → 품질 게이트 연결' 회귀 테스트.

배경: 1단계(개선점 탐색 계약)는 QA가 개선점을 *발견/보고*하게 만들었지만,
발견된 개선점이 실제 품질 게이트/자동 수정 루프에 반영되지 않아
"개선점 3개 발견 → APPROVED"로 끝나는 반쪽짜리였다. 본 계약은:

  1. 개선점마다 P0~P3 심각도를 필수 표기하고,
  2. P0/P1 이 남아 있으면 점수가 90점 이상이어도 제품 PASS를 금지하며,
  3. P2/P3 은 품질 점수를 감점하고,
  4. 발견된 개선점을 차기 Coder iteration 지시문으로 자동 환류한다.

추가로 'render/canvas 존재만으로 92점'을 주던 휴리스틱을 제거하여
"렌더 성공 ≠ 디자인 품질"을 점수에 반영한다.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from convergence_policy import (  # noqa: E402
    ConvergenceTelemetry,
    build_improvement_discovery_instruction,
    collect_blocking_improvements,
    evaluate_improvement_gate,
    format_improvement_directive,
    has_blocking_improvements,
    parse_improvement_severities,
)
from product_gate import (  # noqa: E402
    IMPROVEMENT_BLOCKING_SEVERITIES,
    improvement_blocking_count,
    improvement_score_penalty,
    normalize_severity_counts,
    evaluate_milestone_gate,
)


def _dims(critical=95.0):
    return {
        "functionality": {"score": critical, "is_critical": True},
        "visual_quality": {"score": critical, "is_critical": True},
    }


# GPT 피드백 재현용: QA가 P0/P1 개선점을 발견했음에도 APPROVED 하는 출력.
QA_APPROVED_WITH_BLOCKING_IMPROVEMENTS = """[STATUS: APPROVED]
[BLOCKING_DEFECTS: 0]
[IMPROVEMENTS: 3]
**[개선점 탐색]**
- [P1] [대시보드 CTA] 핵심 버튼이 배경과 대비가 약해 즉시 인식이 어려움 · 사용자 이탈 · CTA 대비 강화
- [P2] 카드 간 vertical spacing 불균형 · 시각적 리듬 저해 · 간격 토큰 정렬
- [P3] 일부 보조 문구가 약간 답답함 · 선택적 개선
"""


class TestSeverityParsing(unittest.TestCase):
    def test_parses_bracket_dash_and_colon_formats(self):
        text = (
            "- [P1] 첫 번째\n"
            "[P2] 두 번째\n"
            "P2: 세 번째\n"
            "- [P0] 치명\n"
        )
        counts = parse_improvement_severities(text)
        self.assertEqual(counts["P0"], 1)
        self.assertEqual(counts["P1"], 1)
        self.assertEqual(counts["P2"], 2)
        self.assertEqual(counts["P3"], 0)

    def test_no_labels_returns_all_zero(self):
        counts = parse_improvement_severities("[IMPROVEMENTS: 2]\n- 여백 개선\n- 버튼 개선")
        self.assertEqual(sum(counts.values()), 0)

    def test_does_not_match_versions_or_words(self):
        counts = parse_improvement_severities("P1.2 release notes\nPDF exporter\n")
        self.assertEqual(sum(counts.values()), 0)

    def test_none_safe(self):
        self.assertEqual(sum(parse_improvement_severities(None).values()), 0)
        self.assertEqual(sum(parse_improvement_severities("").values()), 0)


class TestImprovementGate(unittest.TestCase):
    def test_p1_blocks_even_when_blocking_defects_zero(self):
        gate = evaluate_improvement_gate(QA_APPROVED_WITH_BLOCKING_IMPROVEMENTS)
        self.assertFalse(gate.passed)
        self.assertTrue(has_blocking_improvements(QA_APPROVED_WITH_BLOCKING_IMPROVEMENTS))
        self.assertEqual(len(gate.blocking_items), 1)
        self.assertIn("P0/P1", " ".join(gate.reasons))

    def test_p2_only_does_not_block_but_penalizes(self):
        gate = evaluate_improvement_gate("[IMPROVEMENTS: 2]\n- [P2] a\n- [P2] b")
        self.assertTrue(gate.passed)
        self.assertEqual(gate.score_penalty, 10.0)

    def test_strict_requires_labels(self):
        gate = evaluate_improvement_gate(
            "[IMPROVEMENTS: 2]\n- 여백\n- 대비", strict=True
        )
        self.assertFalse(gate.passed)
        self.assertTrue(any("severity" in r or "심각도" in r for r in gate.reasons))

    def test_strict_requires_marker(self):
        gate = evaluate_improvement_gate("- [P2] a", strict=True)
        self.assertFalse(gate.passed)

    def test_collect_blocking_items(self):
        items = collect_blocking_improvements(QA_APPROVED_WITH_BLOCKING_IMPROVEMENTS)
        self.assertEqual(len(items), 1)
        self.assertIn("CTA", items[0])

    def test_format_directive_injects_items(self):
        directive = format_improvement_directive(QA_APPROVED_WITH_BLOCKING_IMPROVEMENTS)
        self.assertIn("자동 환류", directive)
        self.assertIn("P1", directive)
        self.assertIn("P2", directive)


class TestProductGateIntegration(unittest.TestCase):
    def test_p1_blocks_gate_with_high_score(self):
        """90점 이상이어도 P1 개선점이 있으면 제품 PASS 금지 (GPT #5)."""
        result = evaluate_milestone_gate(
            overall_score=96,
            dimensions=_dims(),
            e2e_passed=True,
            critical_defect_count=0,
            regression_passed=True,
            improvement_severities=parse_improvement_severities(
                QA_APPROVED_WITH_BLOCKING_IMPROVEMENTS
            ),
        )
        self.assertFalse(result.passed)
        self.assertTrue(
            any("P0/P1" in r for r in result.blocking_reasons), result.blocking_reasons
        )

    def test_p2_penalty_drops_below_90(self):
        """P2 개선점은 품질 점수를 감점하여 90 미만이면 FAIL (GPT #4)."""
        result = evaluate_milestone_gate(
            overall_score=94,
            dimensions=_dims(),
            e2e_passed=True,
            improvement_severities={"P2": 3},
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.improvement_penalty, 15.0)
        self.assertEqual(result.effective_score, 79.0)

    def test_no_improvements_preserves_existing_pass(self):
        """기존 동작 보존: severity 미지정 시 종전 판정과 동일."""
        result = evaluate_milestone_gate(
            overall_score=96, dimensions=_dims(), e2e_passed=True
        )
        self.assertTrue(result.passed, result.blocking_reasons)

    def test_helpers(self):
        counts = normalize_severity_counts({"p1": 2, "P2": "1", "bad": 9})
        self.assertEqual(counts["P1"], 2)
        self.assertEqual(counts["P2"], 1)
        self.assertEqual(improvement_blocking_count({"P0": 1, "P2": 5}), 1)
        self.assertEqual(improvement_score_penalty({"P2": 2, "P3": 3}), 13.0)


class TestInstructionContract(unittest.TestCase):
    def setUp(self):
        self.text = build_improvement_discovery_instruction()

    def test_requires_severity_labels(self):
        for level in IMPROVEMENT_BLOCKING_SEVERITIES:
            self.assertIn(level, self.text)
        self.assertIn("P3", self.text)
        self.assertIn("심각도", self.text)

    def test_states_gate_connection(self):
        self.assertIn("90점", self.text)
        self.assertIn("P0/P1", self.text)


class TestTelemetrySeverity(unittest.TestCase):
    def setUp(self):
        ConvergenceTelemetry.reset_all()

    def test_records_severity_distribution(self):
        ConvergenceTelemetry.record_improvements(
            2001, 3, {"P0": 0, "P1": 1, "P2": 2, "P3": 0}
        )
        summary = ConvergenceTelemetry.summary(2001)
        self.assertEqual(summary["improvement_severities"]["P1"], 1)
        self.assertEqual(summary["improvement_severities"]["P2"], 2)
        self.assertEqual(summary["blocking_improvements"], 1)
        self.assertTrue(
            any("P0/P1" in w for w in summary["warnings"]), summary["warnings"]
        )

    def test_warns_on_unlabeled_improvements(self):
        ConvergenceTelemetry.record_improvements(2002, 2, {"P0": 0, "P1": 0, "P2": 0, "P3": 0})
        summary = ConvergenceTelemetry.summary(2002)
        self.assertEqual(summary["unlabeled_improvements"], 1)
        self.assertTrue(
            any("severity" in w for w in summary["warnings"]), summary["warnings"]
        )


class TestNoRenderKeywordInflation(unittest.TestCase):
    """GPT #9: canvas/render 키워드 존재만으로 92점을 주지 않는다."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sev_gate_planner_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_code_only_without_interactions_does_not_get_92(self):
        from product_planner import AutonomousProductPlanner

        mock_e2e = MagicMock()
        mock_e2e.test_cases = [{"status": "PASS", "title": "TC"}]
        mock_e2e.console_logs = []
        mock_e2e.interactions = []      # 실제 인터랙션 실증 없음
        mock_e2e.screenshots = []       # 실제 렌더 스크린샷 없음

        with open(os.path.join(self.test_dir, "game.js"), "w", encoding="utf-8") as f:
            f.write(
                "function render(){ drawSprite(); particleEmitter(); }\n"
                "class Player { update(){ this.render(); } }\n"
            )

        eval_res = AutonomousProductPlanner.critique_product(
            workspace=self.test_dir,
            e2e_report=mock_e2e,
            coder_output="Implemented canvas render and particle emitter keywords.",
            mode="evolve",
        )
        self.assertLess(
            eval_res.dimension_scores["functionality"].score, 90.0,
            "render/코드 존재만으로 기능성이 90점 이상이면 안 됩니다.",
        )
        self.assertLess(
            eval_res.dimension_scores["visual_quality"].score, 90.0,
            "canvas 키워드 존재만으로 시각 완성도가 90점 이상이면 안 됩니다.",
        )


if __name__ == "__main__":
    unittest.main()
