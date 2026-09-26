#!/usr/bin/env python3
"""
Commander Intent Coverage Contract (요구사항 커버리지 계약) test suite.

Validates the systemic guard that prevents Commander from "허공 치기"
(planning an off-target ROADMAP/domain-ladder milestone while ignoring the
user's explicit, itemized feedback). The contract:

1. Builds a Requirement Ledger from the user's own wording
   (`FeedbackIntentClassifier.extract_requirement_units`).
2. Audits a milestone plan 1:1 against that ledger (`Commander.audit_intent_coverage`).
3. If the plan is off-target (< 60% covered) and override is permitted, it is
   replaced by the faithful feedback decomposition (`Commander.apply_intent_coverage`).
4. Attaches a visible coverage matrix to Discord output and the Coder directive.
5. Never mutates an authoritative, explicitly preserved caller milestone.
"""

import os
import sys
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from commander import Commander, Milestone5W1HPlan
from feedback_classifier import FeedbackIntentClassifier


USER_COMPLAINT_PROMPT = """앱이 1. 디자인이 좀 통일성? 연결성? 아무튼 매칭이 안되는 구간들이 있어
깔끔하게 해줘

2. 도큐크래프트라는 이름이 마음에 안들어
다른걸로 해줘

3. 앱 아이콘도 좀 라이트 그레이톤? 으로 깔끔한걸로 해줘

4. 앱 메인 페이지도 좀 약해보여
또 ai 이미지 들어가있는게 별로야.
버튼 옵션도 너무 없고
좀 열기, 빈페이지 만들기, 합치기, 변환하기 이런 주요 기능들을 꺼내다주면 좋을듯

4. Pdf를 열면 종종 튕기는데 안정성 이슈가 있는 것 같아. 최적화 부탁해."""


def _off_target_plan() -> Milestone5W1HPlan:
    """A seeded ROADMAP-style milestone with zero relation to the user's feedback."""
    return Milestone5W1HPlan(
        version="v11.0",
        title="주석 레이어 렌더링 & 무손실 내보내기 (Phase 3: 주석 코어)",
        domain="mobile_app",
        why="주석 코어 기능 집중 개발",
        what=[
            "주석(형광펜, 펜 필기, 텍스트) 레이어 분리 저장 및 캔버스 렌더링",
            "터치 제스처 충돌 방지 및 Undo/Redo 제어 무결성",
            "원본 문서 훼손 없는 무손실 주석 병합 및 표준 PDF 내보내기",
        ],
        how="캔버스 렌더링 엔진",
        who_where="• Coder: app/src/main/",
        when_acceptance=["빌드 성공"],
        source="heuristic",
    )


class TestCommanderIntentCoverageContract(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_intent_coverage_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 1. Requirement Ledger extraction ────────────────────────────────
    def test_requirement_ledger_extracts_five_user_points(self):
        units = FeedbackIntentClassifier.extract_requirement_units(USER_COMPLAINT_PROMPT)
        self.assertEqual(len(units), 5, f"Expected 5 request units, got {len(units)}: {units}")
        joined = " ".join(units)
        self.assertIn("통일성", joined)
        self.assertIn("도큐크래프트", joined)
        self.assertIn("아이콘", joined)
        self.assertIn("메인 페이지", joined)
        self.assertIn("Pdf", joined)
        # Preamble narration must be dropped
        for u in units:
            self.assertNotEqual(u.strip(), "앱이")

    def test_requirement_ledger_ignores_pure_meta_and_empty(self):
        self.assertEqual(FeedbackIntentClassifier.extract_requirement_units(""), [])
        self.assertEqual(
            FeedbackIntentClassifier.extract_requirement_units("요청사항\n해줘\n부탁드립니다"),
            [],
        )

    # ── 2. Coverage audit ───────────────────────────────────────────────
    def test_audit_marks_off_target_plan_uncovered(self):
        matrix, uncovered = Commander.audit_intent_coverage(
            USER_COMPLAINT_PROMPT,
            plan_title="주석 레이어 렌더링 & 무손실 내보내기 (Phase 3: 주석 코어)",
            plan_tasks=["주석 레이어 분리 저장 및 캔버스 렌더링", "Undo/Redo 제어 무결성"],
        )
        self.assertEqual(len(matrix), 5)
        covered = sum(1 for m in matrix if m["covered"])
        self.assertLess(covered, 3, f"Off-target plan should be mostly uncovered: {matrix}")

    def test_is_unit_covered_handles_compound_nouns_and_korean_agglutination(self):
        """Verifies that Korean particles ('라는', '명', '팩') do not cause false negative audits."""
        u = "도큐크래프트라는 이름이 마음에 안들어 다른걸로 해줘"
        p = "브랜드 리브랜딩: 도큐크래프트명 전면 교체"
        self.assertTrue(FeedbackIntentClassifier.is_unit_covered(u, p))
        self.assertFalse(FeedbackIntentClassifier.is_unit_covered(u, "주석 레이어 렌더링"))


    # ── 3. Off-target override ──────────────────────────────────────────
    def test_off_target_plan_is_overridden_by_coverage_contract(self):
        plan = _off_target_plan()
        repaired = Commander.apply_intent_coverage(
            plan,
            user_prompt=USER_COMPLAINT_PROMPT,
            workspace=self.tmp,
            allow_override=True,
        )
        self.assertTrue(repaired.title.startswith("사용자 피드백"), repaired.title)
        self.assertNotIn("Phase 3", repaired.title)
        self.assertNotIn("주석 레이어", repaired.title)
        self.assertEqual(repaired.intent_coverage, "5/5")
        self.assertEqual(len(repaired.coverage_matrix), 5)
        self.assertTrue(all(m["covered"] for m in repaired.coverage_matrix))

        tasks_str = " ".join(repaired.what)
        self.assertTrue(any(k in tasks_str for k in ["디자인 통일성", "연결성"]))
        self.assertTrue(any(k in tasks_str for k in ["도큐크래프트", "명칭", "리브랜딩"]))
        self.assertTrue(any(k in tasks_str for k in ["아이콘", "그레이톤"]))
        self.assertTrue("메인" in tasks_str)
        self.assertTrue(any(k in tasks_str for k in ["PDF", "크래시", "안정성", "최적화"]))

    def test_override_disabled_keeps_non_feedback_plan_untouched(self):
        """An authoritative caller milestone must never be mutated."""
        plan = _off_target_plan()
        original_title = plan.title
        original_what = list(plan.what)
        result = Commander.apply_intent_coverage(
            plan,
            user_prompt=USER_COMPLAINT_PROMPT,
            workspace=self.tmp,
            allow_override=False,
        )
        self.assertEqual(result.title, original_title)
        self.assertEqual(result.what, original_what)
        self.assertEqual(result.coverage_matrix, [])

    # ── 4. End-to-end wiring through plan_milestone_5w1h ────────────────
    def test_plan_milestone_attaches_coverage_matrix(self):
        plan = Commander.plan_milestone_5w1h(
            milestone_title="임의 타이틀",
            milestone_version="v2.0",
            tasks=[],
            domain="mobile_app",
            workspace=self.tmp,
            user_prompt=USER_COMPLAINT_PROMPT,
            project_initialized=True,
        )
        self.assertTrue(plan.title.startswith("사용자 피드백"), plan.title)
        self.assertEqual(plan.intent_coverage, "5/5")
        self.assertEqual(len(plan.coverage_matrix), 5)

        msg = plan.to_discord_message()
        self.assertIn("사용자 요청 반영 매트릭스", msg)
        self.assertIn("5/5 반영", msg)

        directive = plan.directive_for_coder
        self.assertIn("사용자 요청 커버리지 계약", directive)
        self.assertIn("5/5 반영", directive)

    def test_adaptive_next_milestone_carries_coverage_matrix(self):
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal=USER_COMPLAINT_PROMPT,
            workspace=self.tmp,
            domain="mobile_app",
        )
        self.assertTrue(plan.title.startswith("사용자 피드백"), plan.title)
        self.assertEqual(plan.intent_coverage, "5/5")
        self.assertEqual(len(plan.coverage_matrix), 5)

    def test_non_feedback_new_goal_has_no_coverage_matrix(self):
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal="새로운 사과 피하기 게임을 처음부터 만들어줘",
            workspace=self.tmp,
            domain="game",
        )
        self.assertEqual(plan.coverage_matrix, [])

    # ── 5. Async LLM divergence is contained by the contract ────────────
    def test_adaptive_async_llm_divergence_still_yields_full_coverage(self):
        """Even if the LLM returns an off-target Phase 3 plan, the async adaptive
        planner must emit a plan covering every user request."""
        import asyncio
        import json
        from unittest.mock import patch, AsyncMock

        hallucinated = json.dumps({
            "title": "v11.0: 주석 레이어 렌더링 & 무손실 내보내기 (Phase 3: 주석 코어)",
            "delta_analysis": "주석 코어",
            "why": "주석 코어 개발",
            "what": ["주석 레이어 분리 저장", "Undo/Redo 제어"],
            "how": "캔버스 렌더링",
            "who_where": "app/src/main/",
            "when_acceptance": ["빌드 성공"],
        })

        async def _run():
            with patch.object(Commander, "_run_raw_llm_query", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = hallucinated
                plan = await Commander.plan_adaptive_next_milestone_async(
                    root_user_goal=USER_COMPLAINT_PROMPT,
                    workspace=self.tmp,
                    domain="mobile_app",
                )
            self.assertTrue(plan.title.startswith("사용자 피드백"), plan.title)
            self.assertNotIn("Phase 3", plan.title)
            self.assertEqual(plan.intent_coverage, "5/5")
            self.assertEqual(len(plan.coverage_matrix), 5)
            self.assertTrue(all(m["covered"] for m in plan.coverage_matrix))

        asyncio.run(_run())

    # ── 6. Duplicate-free idempotency ───────────────────────────────────
    def test_coverage_application_is_idempotent(self):
        plan = _off_target_plan()
        first = Commander.apply_intent_coverage(
            plan, user_prompt=USER_COMPLAINT_PROMPT, workspace=self.tmp, allow_override=True
        )
        what_after_first = list(first.what)
        thinking_after_first = first.thinking_process
        second = Commander.apply_intent_coverage(
            first, user_prompt=USER_COMPLAINT_PROMPT, workspace=self.tmp, allow_override=True
        )
        self.assertEqual(second.what, what_after_first)
        self.assertEqual(second.thinking_process, thinking_after_first)
        self.assertEqual(second.intent_coverage, "5/5")


if __name__ == "__main__":
    unittest.main()
