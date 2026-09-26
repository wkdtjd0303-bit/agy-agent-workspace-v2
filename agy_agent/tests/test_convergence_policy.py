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
    ConvergenceTracker,
    build_blocking_contract_instruction,
    has_blocking_marker,
    parse_blocking_defect_count,
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
        self.assertIn("parse_blocking_defect_count", self.code)
        self.assertIn("ConvergenceTracker.should_force_accept", self.code)
        self.assertIn("ConvergenceTracker.should_escalate", self.code)

    def test_runner_resets_tracker_per_mission(self):
        self.assertIn("ConvergenceTracker.reset(channel_id", self.code)

    def test_qa_prompts_require_blocking_marker(self):
        # QA1 + 확장 QA 프롬프트 양쪽에 마커 규칙이 존재해야 한다.
        self.assertGreaterEqual(self.code.count("BLOCKING_DEFECTS:"), 2)

    def test_contract_covers_honesty_and_nonblocking(self):
        text = build_blocking_contract_instruction()
        self.assertIn("거짓", text)
        self.assertIn("차기 백로그", text)


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
