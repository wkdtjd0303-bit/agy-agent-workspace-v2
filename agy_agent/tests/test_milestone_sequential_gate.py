#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_milestone_sequential_gate.py

PRD §15 검증 시나리오 (Sequential Milestone Completion Gate).

핵심 원칙: "다음 단계로 넘어가는 것"보다 "현재 단계의 완결"을 우선한다.
A가 90점 게이트 + Exit Criteria 100%를 통과해야만 B가 해금된다.

검증 항목(PRD §15):
  1. A가 89점이면 B로 넘어가지 않는가?
  2. A가 90점이어도 필수 Exit Criteria 하나가 빠지면 넘어가지 않는가?
  3. A PASS 후 B가 시작되는가? (순차 잠금)
  4. B 작업 중 A를 망가뜨리면 regression에서 잡히는가?
  5. Critical defect가 0이어도 품질 점수가 90 미만이면 PASS하지 않는가?
  6. QA가 "나중에 개선"이라 해도 현재 Milestone 필수 항목이면 통과시키지 않는가?
  7. 최종 제품은 전체 Milestone과 최종 통합 QA를 모두 통과해야 하는가?
추가: 수렴(force-accept)은 필요조건일 뿐 / 완료 PRD 아카이브.
"""

import os
import sys
import tempfile
import shutil
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from product_gate import (  # noqa: E402
    CRITICAL_DIMENSION_MIN_SCORE,
    MILESTONE_PASS_SCORE,
    FORBIDDEN_BACKLOG_CATEGORIES,
    can_start_milestone,
    evaluate_final_gate,
    evaluate_milestone_gate,
    is_forbidden_backlog_category,
    may_defer_to_backlog,
)
from convergence_policy import convergence_is_sufficient  # noqa: E402
from roadmap_manager import RoadmapManager, RoadmapMilestone  # noqa: E402
from prd_archive import archive_prd, list_archived_prds  # noqa: E402


def _dims(critical=90.0, secondary=70.0):
    return {
        "functionality": {"score": critical, "is_critical": True},
        "controls_feel": {"score": critical, "is_critical": True},
        "audio_sfx": {"score": critical, "is_critical": True},
        "level_gameplay": {"score": secondary, "is_critical": False},
    }


class TestGateThresholds(unittest.TestCase):
    def test_thresholds_are_90(self):
        self.assertEqual(MILESTONE_PASS_SCORE, 90.0)
        self.assertEqual(CRITICAL_DIMENSION_MIN_SCORE, 90.0)

    def test_all_conditions_required(self):
        # 완전 충족 → PASS
        ok = evaluate_milestone_gate(
            overall_score=92, dimensions=_dims(), e2e_passed=True,
            critical_defect_count=0, regression_passed=True,
        )
        self.assertTrue(ok.passed, ok.blocking_reasons)

    def test_scenario_5_zero_defects_but_below_90_fails(self):
        """Critical defect 0이어도 종합 점수 90 미만이면 PASS 불가."""
        r = evaluate_milestone_gate(
            overall_score=85, dimensions=_dims(critical=88), e2e_passed=True,
            critical_defect_count=0, regression_passed=True,
        )
        self.assertFalse(r.passed)
        self.assertTrue(any("Overall" in x or "Critical" in x for x in r.blocking_reasons))

    def test_low_critical_dimension_blocks_even_with_high_others(self):
        dims = _dims()
        dims["audio_sfx"] = {"score": 80, "is_critical": True}  # 90 미만
        r = evaluate_milestone_gate(overall_score=93, dimensions=dims, e2e_passed=True)
        self.assertFalse(r.passed)
        self.assertTrue(any("audio_sfx" in x for x in r.dimension_failures))

    def test_e2e_failure_blocks(self):
        r = evaluate_milestone_gate(overall_score=95, dimensions=_dims(), e2e_passed=False)
        self.assertFalse(r.passed)
        self.assertIn("E2E 미통과", r.blocking_reasons)


class TestSequentialLock(unittest.TestCase):
    def test_scenario_1_89_does_not_unlock(self):
        """A가 89점이면 게이트 FAIL → B로 넘어갈 수 없다."""
        r = evaluate_milestone_gate(overall_score=89, dimensions=_dims(), e2e_passed=True)
        self.assertFalse(r.passed)

    def test_scenario_2_exit_criteria_missing_blocks(self):
        """A가 90점이어도 필수 Exit Criteria 하나가 빠지면 넘어가지 않는다."""
        exit_criteria = [
            {"text": "모든 기능 구현", "met": True},
            {"text": "경계조건 테스트 PASS", "met": False},
        ]
        r = evaluate_milestone_gate(
            overall_score=92, dimensions=_dims(), e2e_passed=True, exit_criteria=exit_criteria,
        )
        self.assertFalse(r.passed)
        self.assertIn("경계조건 테스트 PASS", r.exit_criteria_missing)

    def test_scenario_3_a_pass_then_b_unlocks(self):
        """A PASS 전에는 B가 잠기고, A PASS 후 B가 해금된다."""
        ms_a = {"id": "A", "status": "IN_PROGRESS"}
        ms_b = {"id": "B", "status": "PENDING"}
        chain = [ms_a, ms_b]

        self.assertFalse(can_start_milestone(chain, 1), "A 미완결 상태에서 B가 해금되면 안 된다")
        # A PASS(=DONE)
        ms_a["status"] = "DONE"
        self.assertTrue(can_start_milestone(chain, 1), "A PASS 후 B가 해금되어야 한다")

    def test_roadmap_sequential_lock_enforced(self):
        tmp = tempfile.mkdtemp(prefix="gate_lock_")
        try:
            mgr = RoadmapManager(tmp)
            mgr.ensure_exists()
            # B(v1.1)를 앞당겨 IN_PROGRESS 로 조작해도 A(v1.0) 가 우선 선택되어야 한다.
            for m in mgr._milestones:
                if m.version == "v1.1":
                    m.status = "IN_PROGRESS"
            mgr.save()
            cur = mgr.get_current_milestone(auto_expand=False)
            self.assertIsNotNone(cur)
            self.assertEqual(cur.version, "v1.0", "선행 마일스톤이 완결되기 전 다음 단계가 선택됨")

            # A PASS → B 해금
            mgr.mark_current_milestone_done()
            nxt = mgr.get_current_milestone(auto_expand=False)
            self.assertIsNotNone(nxt)
            self.assertEqual(nxt.version, "v1.1")
            self.assertTrue(mgr._milestones[0].is_locked, "PASS된 A는 LOCK 되어야 한다")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_partial_milestone_not_force_promoted_to_done(self):
        """부분 완성(미완 태스크) 마일스톤은 새 마일스톤 append 시 DONE으로 강제 승격되지 않는다."""
        tmp = tempfile.mkdtemp(prefix="gate_partial_")
        try:
            mgr = RoadmapManager(tmp)
            # v1.0 은 일부 태스크만 완료된 부분 완성 상태
            with open(mgr.roadmap_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Roadmap\n\n"
                    "## v1.0 — 코어 기능 🔄\n"
                    "- [x] 일부 기능\n"
                    "- [ ] 나머지 기능\n"
                )
            mgr.load()
            mgr.append_or_update_adaptive_milestone(
                version="v2.0", title="신규 기능", tasks=["a", "b"], status="IN_PROGRESS",
            )
            v1 = next(m for m in mgr._milestones if m.version == "v1.0")
            self.assertNotEqual(v1.status, "DONE", "부분 완성이 DONE 으로 강제 승격됨")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRegressionAndFinal(unittest.TestCase):
    def test_scenario_4_regression_blocks(self):
        """B 작업 중 A가 망가뜨려지면(회귀) 게이트가 FAIL 처리한다."""
        r = evaluate_milestone_gate(
            overall_score=95, dimensions=_dims(), e2e_passed=True, regression_passed=False,
        )
        self.assertFalse(r.passed)
        self.assertIn("회귀 테스트 실패", r.blocking_reasons)

    def test_scenario_7_final_requires_all_milestones(self):
        """최종 제품은 전체 마일스톤 PASS + 통합 E2E 를 모두 통과해야 한다."""
        partial = evaluate_final_gate(
            all_milestones_passed=False, overall_score=95, dimensions=_dims(), e2e_passed=True,
        )
        self.assertFalse(partial.passed)
        self.assertIn("전체 마일스톤 미완료", partial.blocking_reasons)

        final = evaluate_final_gate(
            all_milestones_passed=True, overall_score=95, dimensions=_dims(),
            e2e_passed=True, critical_defect_count=0, regression_passed=True,
        )
        self.assertTrue(final.passed, final.blocking_reasons)

        no_e2e = evaluate_final_gate(
            all_milestones_passed=True, overall_score=95, dimensions=_dims(), e2e_passed=False,
        )
        self.assertFalse(no_e2e.passed)


class TestBacklogPolicy(unittest.TestCase):
    def test_scenario_6_required_item_cannot_be_deferred(self):
        """현재 마일스톤의 필수(Exit Criteria) 항목은 '나중에 개선'으로 미룰 수 없다."""
        self.assertFalse(
            may_defer_to_backlog("성능 최적화가 필요합니다", required_by_exit_criteria=True),
            "Exit Criteria 필수 항목(성능)이 백로그로 이관됨",
        )
        for text in ["UX 개선", "시각적 완성도 부족", "안정성/크래시 방어", "테스트 부족", "중요한 에러 처리"]:
            self.assertFalse(
                may_defer_to_backlog(text, required_by_exit_criteria=True),
                f"필수 항목 '{text}' 이 백로그로 이관됨",
            )

    def test_optional_item_may_be_deferred(self):
        self.assertTrue(may_defer_to_backlog("선택적 다국어 지원은 차기로", required_by_exit_criteria=True))

    def test_forbidden_categories_defined(self):
        self.assertIn("performance", FORBIDDEN_BACKLOG_CATEGORIES)
        self.assertIn("testing", FORBIDDEN_BACKLOG_CATEGORIES)
        self.assertTrue(is_forbidden_backlog_category(category="stability"))


class TestConvergenceNotSufficient(unittest.TestCase):
    def test_blocking_zero_alone_is_not_pass(self):
        """[BLOCKING_DEFECTS: 0] 만으로는 PASS 가 아니다 (게이트 미통과)."""
        self.assertFalse(convergence_is_sufficient("[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 0]", gate_passed=False))
        self.assertTrue(convergence_is_sufficient("[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 0]", gate_passed=True))
        self.assertFalse(convergence_is_sufficient("[BLOCKING_DEFECTS: 2]", gate_passed=True))


class TestPrdArchive(unittest.TestCase):
    def test_archive_moves_prd_and_leaves_stub(self):
        tmp = tempfile.mkdtemp(prefix="prd_arch_")
        try:
            prd = os.path.join(tmp, "PRD.md")
            with open(prd, "w", encoding="utf-8") as f:
                f.write("# 📋 PRD — 테트리스 게임\n\n## 1. 개요\n내용\n")
            dest = archive_prd(tmp, reason="final_release")
            self.assertIsNotNone(dest)
            self.assertTrue(os.path.isfile(dest))
            self.assertIn("completed_prds", dest)
            # 루트 PRD.md 는 스텁으로 대체
            self.assertTrue(os.path.isfile(prd))
            with open(prd, encoding="utf-8") as f:
                stub = f.read()
            self.assertIn("아카이브", stub)
            self.assertEqual(len(list_archived_prds(tmp)), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_archive_noop_when_no_prd(self):
        tmp = tempfile.mkdtemp(prefix="prd_arch_none_")
        try:
            self.assertIsNone(archive_prd(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
