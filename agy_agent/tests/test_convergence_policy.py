#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_convergence_policy.py

수렴 정책(Convergence & Honesty Contract) 회귀 테스트.

검증 항목:
1. `[BLOCKING_DEFECTS: N]` 마커 파싱 (정상/공백/유니코드 콜론/다중 마커/부재).
2. 마커가 없을 때 None 을 반환하여 기존 동작을 보존하는지(보수적 폴백).
3. 추세 판정: converged / progressing / stagnant / unknown.
4. 정체(stall) 2회 → 조기 에스컬레이션, 감소 시 리셋.
5. force_accept 는 차단 결함 0일 때만.
6. 계약 문구가 차단/비차단/정직성 3요소를 모두 포함하는지.
"""

import os
import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from convergence_policy import (  # noqa: E402
    BLOCKING_DEFECT_CATEGORIES,
    BLOCKING_MARKER_SYNTAX,
    NON_BLOCKING_DEFECT_CATEGORIES,
    ConvergenceTelemetry,
    ConvergenceTracker,
    build_blocking_contract_instruction,
    estimate_blocking_defect_count,
    has_blocking_marker,
    parse_blocking_defect_count,
    resolve_blocking_count,
)


class TestBlockingMarkerParsing(unittest.TestCase):
    def test_parses_standard_marker(self):
        self.assertEqual(parse_blocking_defect_count("[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 3]"), 3)

    def test_parses_zero(self):
        self.assertEqual(parse_blocking_defect_count("[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 0]"), 0)

    def test_parses_with_whitespace_and_unicode_colon(self):
        self.assertEqual(parse_blocking_defect_count("[BLOCKING_DEFECTS :  7 ]"), 7)
        self.assertEqual(parse_blocking_defect_count("[BLOCKING_DEFECTS：5]"), 5)

    def test_last_marker_wins(self):
        text = "[BLOCKING_DEFECTS: 9]\n...\n[BLOCKING_DEFECTS: 2]"
        self.assertEqual(parse_blocking_defect_count(text), 2)

    def test_absent_marker_returns_none(self):
        self.assertIsNone(parse_blocking_defect_count("[STATUS: REJECTED]\n결함이 있습니다."))
        self.assertIsNone(parse_blocking_defect_count(""))
        self.assertIsNone(parse_blocking_defect_count(None))
        self.assertFalse(has_blocking_marker("no marker here"))

    def test_has_marker_true(self):
        self.assertTrue(has_blocking_marker("[BLOCKING_DEFECTS: 1]"))


class TestConvergenceTracker(unittest.TestCase):
    def setUp(self):
        ConvergenceTracker.reset_all()
        self.ch = 4242

    def tearDown(self):
        ConvergenceTracker.reset_all()

    def test_unknown_when_no_records(self):
        self.assertEqual(ConvergenceTracker.classify(self.ch), "unknown")
        self.assertFalse(ConvergenceTracker.should_force_accept(self.ch))
        self.assertFalse(ConvergenceTracker.should_escalate(self.ch))

    def test_converged_when_zero(self):
        ConvergenceTracker.record(self.ch, 2)
        ConvergenceTracker.record(self.ch, 0)
        self.assertEqual(ConvergenceTracker.classify(self.ch), "converged")
        self.assertTrue(ConvergenceTracker.should_force_accept(self.ch))
        self.assertFalse(ConvergenceTracker.should_escalate(self.ch))

    def test_progressing_when_decreasing(self):
        ConvergenceTracker.record(self.ch, 5)
        ConvergenceTracker.record(self.ch, 4)
        ConvergenceTracker.record(self.ch, 2)
        self.assertEqual(ConvergenceTracker.classify(self.ch), "progressing")
        self.assertFalse(ConvergenceTracker.should_escalate(self.ch))
        self.assertFalse(ConvergenceTracker.should_force_accept(self.ch))

    def test_stagnant_after_two_non_decreasing_and_escalates(self):
        ConvergenceTracker.record(self.ch, 3)
        ConvergenceTracker.record(self.ch, 3)
        self.assertEqual(ConvergenceTracker.classify(self.ch), "progressing")  # 1 stall only
        self.assertFalse(ConvergenceTracker.should_escalate(self.ch))
        ConvergenceTracker.record(self.ch, 3)
        self.assertEqual(ConvergenceTracker.classify(self.ch), "stagnant")
        self.assertTrue(ConvergenceTracker.should_escalate(self.ch))

    def test_increasing_counts_escalate(self):
        ConvergenceTracker.record(self.ch, 1)
        ConvergenceTracker.record(self.ch, 4)
        ConvergenceTracker.record(self.ch, 5)
        self.assertTrue(ConvergenceTracker.should_escalate(self.ch))

    def test_progress_resets_stall_streak(self):
        ConvergenceTracker.record(self.ch, 5)
        ConvergenceTracker.record(self.ch, 5)  # stall 1
        ConvergenceTracker.record(self.ch, 3)  # decrease -> reset
        self.assertEqual(ConvergenceTracker.classify(self.ch), "progressing")
        self.assertFalse(ConvergenceTracker.should_escalate(self.ch))

    def test_none_records_are_ignored(self):
        ConvergenceTracker.record(self.ch, None)
        ConvergenceTracker.record(self.ch, 2)
        ConvergenceTracker.record(self.ch, None)
        self.assertEqual(ConvergenceTracker.history(self.ch), [2])

    def test_reset_clears_channel(self):
        ConvergenceTracker.record(self.ch, 3)
        ConvergenceTracker.reset(self.ch)
        self.assertEqual(ConvergenceTracker.classify(self.ch), "unknown")
        self.assertEqual(ConvergenceTracker.history(self.ch), [])

    def test_history_cap(self):
        for i in range(30):
            ConvergenceTracker.record(self.ch, 1)
        self.assertLessEqual(len(ConvergenceTracker.history(self.ch)), 16)

    def test_describe_contains_trend(self):
        ConvergenceTracker.record(self.ch, 3)
        ConvergenceTracker.record(self.ch, 1)
        desc = ConvergenceTracker.describe(self.ch)
        self.assertIn("3", desc)
        self.assertIn("1", desc)


class TestFallbackHeuristic(unittest.TestCase):
    """QA가 마커 계약을 지키지 않아도 수렴 정책이 동작해야 한다 (비활성화 방지)."""

    def test_marker_takes_precedence(self):
        out = "[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 4]\nD1 화면 잘림 발생"
        self.assertEqual(resolve_blocking_count(out), (4, "marker"))

    def test_heuristic_counts_user_visible_defects(self):
        out = (
            "[STATUS: REJECTED]\n"
            "D1 [High] 화면 잘림 — 마지막 행이 하단 바에 반으로 잘려 있습니다\n"
            "D2 [MED] 버튼 클릭해도 아무 반응이 없습니다\n"
            "D3 [LOW] 실행 안 된 테스트가 통과로 기재되어 있습니다"
        )
        count, source = resolve_blocking_count(out)
        self.assertEqual(source, "heuristic")
        self.assertEqual(count, 3)

    def test_heuristic_returns_zero_for_pure_non_blocking(self):
        out = (
            "[STATUS: REJECTED]\n"
            "D1 [LOW] 코드 스타일이 일관되지 않습니다. 리팩토링을 권고합니다.\n"
            "D2 [LOW] 문서 포맷과 해시 표기가 어긋납니다. 문서 정리를 권고합니다."
        )
        count, source = resolve_blocking_count(out)
        self.assertEqual(source, "heuristic")
        self.assertEqual(count, 0)

    def test_heuristic_zero_for_channel1_meta_nitpicks(self):
        """[회귀] 채널1 로그의 Fact Checker 반려는 문서/포맷/환경 제약 같은 비차단 항목뿐이었다.
        이런 반려는 차단 결함 0건으로 해석되어 수렴(강제 승인)되어야 한다 — 무한 반려 루프 방지."""
        out = (
            "[STATUS: REJECTED]\n"
            "D1 [LOW] 클리핑 스크립트가 390px만 고정이라 '1280px 스캔' 항목은 디스크 증거 미비 — 문서/증거 포맷 보강 권고\n"
            "D2 [LOW] .NET 10 종료일 표기가 1일 차이 — 표기 정정 권고\n"
            "D3 [LOW] 라이브 E2E 16/16은 브라우저 미연동으로 재실행 불가 — 환경 제약, 정보 정확성과 무관\n"
        )
        count, source = resolve_blocking_count(out)
        self.assertEqual(count, 0, "문서/포맷/환경 제약은 차단 결함이 아니다")
        self.assertEqual(source, "heuristic")

        ConvergenceTracker.reset_all()
        ConvergenceTracker.record(999, count)
        self.assertTrue(ConvergenceTracker.should_force_accept(999))
        ConvergenceTracker.reset_all()

    def test_none_when_no_defect_statements(self):
        out = "[STATUS: REJECTED]\n좋은 시도였습니다. 다시 해주세요."
        self.assertEqual(resolve_blocking_count(out), (None, "none"))

    def test_estimate_returns_none_when_marker_present(self):
        out = "[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 2]\n문제: 클릭 불가"
        self.assertIsNone(estimate_blocking_defect_count(out))

    def test_heuristic_ignores_headers_and_status_lines(self):
        out = (
            "[STATUS: REJECTED]\n"
            "## 반려 사유 요약\n"
            "```\nscreencap 실패\n```\n"
            "문제: 화면이 깨짐"
        )
        count, _src = resolve_blocking_count(out)
        self.assertEqual(count, 1)


class TestConvergenceTelemetry(unittest.TestCase):
    """'구현했다 = 동작한다' 착각을 막기 위한 계측 자체의 회귀 테스트."""

    def setUp(self):
        ConvergenceTelemetry.reset_all()
        self.ch = 777

    def tearDown(self):
        ConvergenceTelemetry.reset_all()

    def test_records_marker_rate(self):
        ConvergenceTelemetry.record(self.ch, "qa1", 2, "marker", "observed")
        ConvergenceTelemetry.record(self.ch, "qa1", 0, "marker", "force_accept")
        # 개선점 탐색 계약 마커도 방출되면 경고가 없어야 한다(계약 전면 준수).
        ConvergenceTelemetry.record_improvements(self.ch, 3)
        s = ConvergenceTelemetry.summary(self.ch)
        self.assertEqual(s["observations"], 2)
        self.assertEqual(s["marker_present"], 2)
        self.assertEqual(s["marker_emission_rate"], 1.0)
        self.assertEqual(s["force_accepts"], 1)
        self.assertTrue(s["contract_active"])
        self.assertEqual(s["improvement_observed"], 1)
        self.assertEqual(s["total_improvements"], 3)
        self.assertEqual(s["warnings"], [])

    def test_warns_when_marker_never_emitted(self):
        ConvergenceTelemetry.record(self.ch, "qa1", 1, "heuristic", "observed")
        s = ConvergenceTelemetry.summary(self.ch)
        self.assertEqual(s["marker_present"], 0)
        self.assertEqual(s["heuristic_used"], 1)
        self.assertTrue(s["contract_active"])
        self.assertTrue(any("마커" in w for w in s["warnings"]))

    def test_warns_when_never_resolved(self):
        ConvergenceTelemetry.record(self.ch, "qa1", None, "none", "observed")
        s = ConvergenceTelemetry.summary(self.ch)
        self.assertFalse(s["contract_active"])
        self.assertTrue(any("비활성" in w for w in s["warnings"]))

    def test_no_observations_is_flagged(self):
        s = ConvergenceTelemetry.summary(self.ch)
        self.assertEqual(s["observations"], 0)
        self.assertFalse(s["contract_active"])
        self.assertTrue(s["warnings"])

    def test_per_stage_breakdown(self):
        ConvergenceTelemetry.record(self.ch, "qa1", 2, "marker", "observed")
        ConvergenceTelemetry.record(self.ch, "qa2", None, "none", "observed")
        s = ConvergenceTelemetry.summary(self.ch)
        self.assertIn("qa1", s["per_stage"])
        self.assertEqual(s["per_stage"]["qa1"]["marker"], 1)
        self.assertEqual(s["per_stage"]["qa2"]["none"], 1)

    def test_export_writes_physical_report(self):
        import json
        import os
        import tempfile
        ws = tempfile.mkdtemp(prefix="tel_")
        try:
            ConvergenceTelemetry.record(self.ch, "qa1", 3, "marker", "observed")
            path = ConvergenceTelemetry.export(self.ch, ws, milestone="v12.0")
            self.assertIsNotNone(path)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["milestone"], "v12.0")
            self.assertEqual(data["schema"], "convergence_telemetry/v1")
            self.assertEqual(data["observations"], 1)
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    def test_export_without_workspace_returns_none(self):
        self.assertIsNone(ConvergenceTelemetry.export(self.ch, None))


class TestRunnerWiring(unittest.TestCase):
    """수렴 계약이 실제 러너/프롬프트에 배선되어 있는지 고정한다 (문서만 있는 계약 방지)."""

    @classmethod
    def setUpClass(cls):
        runner = WORKSPACE_ROOT / "trio_agent_runner.py"
        with open(runner, "r", encoding="utf-8") as f:
            cls.code = f.read()

    def test_runner_imports_and_uses_convergence_policy(self):
        self.assertIn("from convergence_policy import", self.code)
        self.assertIn("build_blocking_contract_instruction", self.code)
        self.assertIn("resolve_blocking_count(", self.code)
        self.assertIn("ConvergenceTracker.should_force_accept", self.code)
        self.assertIn("ConvergenceTracker.should_escalate", self.code)

    def test_runner_resets_tracker_per_mission(self):
        self.assertIn("ConvergenceTracker.reset(channel_id", self.code)

    def test_runner_records_and_exports_telemetry(self):
        # 마커 미방출에도 죽지 않도록 폴백 해석 + 물리 리포트 기록
        self.assertIn("resolve_blocking_count(", self.code)
        self.assertIn("ConvergenceTelemetry.record(", self.code)
        self.assertIn("ConvergenceTelemetry.export(", self.code)

    def test_qa_prompts_require_blocking_marker(self):
        # QA1 + 확장 QA 프롬프트 양쪽에 마커 규칙이 존재해야 한다.
        self.assertGreaterEqual(self.code.count("BLOCKING_DEFECTS:"), 2)

    def test_contract_covers_honesty_and_nonblocking(self):
        text = build_blocking_contract_instruction()
        self.assertIn("거짓", text)
        self.assertIn("차기 백로그", text)


class TestResearchRunnerWiring(unittest.TestCase):
    """ResearchRunner(Analyst ➔ Fact Checker ➔ Auditor) 에도 수렴/정직성 계약이
    실제로 배선되어 있는지 고정한다. (리서치 파이프라인의 무한 반려·환각 루프 방지)
    """

    @classmethod
    def setUpClass(cls):
        runner = WORKSPACE_ROOT / "research_runner.py"
        with open(runner, "r", encoding="utf-8") as f:
            cls.code = f.read()

    def test_research_runner_imports_and_uses_convergence_policy(self):
        self.assertIn("from convergence_policy import", self.code)
        self.assertIn("build_blocking_contract_instruction", self.code)
        self.assertIn("resolve_blocking_count(", self.code)
        self.assertIn("ConvergenceTracker.should_force_accept", self.code)
        self.assertIn("ConvergenceTracker.should_escalate", self.code)

    def test_research_runner_resets_tracker_per_mission(self):
        self.assertIn("ConvergenceTracker.reset(channel_id", self.code)

    def test_research_runner_records_and_exports_telemetry(self):
        self.assertIn("ConvergenceTelemetry.record(", self.code)
        self.assertIn("ConvergenceTelemetry.export(", self.code)

    def test_fact_checker_prompt_requires_blocking_marker(self):
        self.assertIn("BLOCKING_DEFECTS:", self.code)

    def test_fact_checker_prompt_has_anti_hallucination_rule(self):
        self.assertIn("환각", self.code)
        self.assertIn("골대이동", self.code)


class TestContractInstruction(unittest.TestCase):
    def test_instruction_contains_all_three_pillars(self):
        text = build_blocking_contract_instruction()
        # 차단 결함
        self.assertIn("차단 결함", text)
        # 비차단
        self.assertIn("비차단", text)
        # 정직성
        self.assertIn("정직성", text)
        # 마커 문법
        self.assertIn(BLOCKING_MARKER_SYNTAX, text)

    def test_categories_non_empty(self):
        self.assertGreater(len(BLOCKING_DEFECT_CATEGORIES), 0)
        self.assertGreater(len(NON_BLOCKING_DEFECT_CATEGORIES), 0)

    def test_honesty_is_a_blocking_category(self):
        joined = " ".join(BLOCKING_DEFECT_CATEGORIES)
        self.assertIn("거짓", joined)


if __name__ == "__main__":
    unittest.main()
