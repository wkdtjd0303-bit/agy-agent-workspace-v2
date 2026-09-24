#!/usr/bin/env python3
"""
Unit tests for Commander Anti-Repeat Planning (중복 기획 원천 차단).

Validates the user mandate: "진행했으면 진행했다고 체크를 해놔야지 했던거 또하고 하면 안되니깐".

Covered:
1. RoadmapManager does not append a second row for an already-completed equivalent
   capability (v2.0 vs v2.1 same scope) and never downgrades it back to IN_PROGRESS.
2. A pending equivalent milestone is updated in place instead of duplicated.
3. Commander's adaptive heuristic skips the user-feedback bundle once its concrete
   artifacts exist on disk, and advances to the next un-delivered capability.
4. Regression: a fresh workspace still receives the user-feedback milestone.
5. A re-synthesized AI plan that repeats delivered work is rejected.
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from commander import Commander
from roadmap_manager import RoadmapManager
from quality_state_manager import QualityStateManager

FEEDBACK_TITLE = (
    "사용자 피드백 긴급 반영: 브랜드 리뉴얼(로고·앱명·컬러), "
    "버튼 가독성 개선 및 PDF 원본 텍스트 직접 편집"
)
FEEDBACK_GOAL = (
    "pdf 뷰어 및 에디터로 쓸 수 있는 안드로이드 앱. "
    "에디터에서 글자를 넣어도 크기가 조절되지 않는 사용성 문제를 고쳐줘."
)

# Concrete artifacts that prove the feedback bundle is physically delivered.
_DELIVERED_COMPONENTS = [
    "PdfContentStreamEditor.kt",
    "BrandButtons.kt",
    "DirectTextEditDialog.kt",
    "TextSizeLabels.kt",
    "PdfExportEngine.kt",
    "PdfExportManager.kt",
]


class TestCommanderNoRepeatPlanning(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_no_repeat_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_roadmap(self, content: str):
        with open(os.path.join(self.tmp, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(content)

    def _seed_delivered_components(self, ws: str):
        src = os.path.join(ws, "app", "src", "main", "java", "com", "example", "pdfapp")
        os.makedirs(src, exist_ok=True)
        for name in _DELIVERED_COMPONENTS:
            with open(os.path.join(src, name), "w", encoding="utf-8") as f:
                f.write("// delivered artifact\n")

    # ── 1. RoadmapManager idempotency ───────────────────────────────────────
    def test_completed_equivalent_capability_is_not_appended(self):
        self._write_roadmap(
            "# Roadmap\n\n"
            "## v2.0 — 사용자 피드백 긴급 반영: 브랜드 리뉴얼(로고·앱명·컬러), "
            "버튼 가독성 개선 및 PDF 원본 텍스트 직접 편집 ✅\n"
            "- [x] 브랜드 리뉴얼\n- [x] PDF 원본 텍스트 편집\n"
        )
        mgr = RoadmapManager(self.tmp)
        mgr.load()
        before = len(mgr._milestones)

        # Commander tries to re-plan the same scope under a new version (v2.1).
        result = mgr.append_or_update_adaptive_milestone(
            version="v2.1",
            title="사용자 피드백 긴급 반영: 브랜드 리뉴얼(로고·앱명·컬러)·버튼 가독성 개선 및 PDF 원본 텍스트 직접 편집",
            tasks=["브랜드 리뉴얼", "PDF 원본 텍스트 편집"],
            status="IN_PROGRESS",
        )

        self.assertEqual(len(mgr._milestones), before, "동일 역량이 중복 append 되었습니다")
        self.assertTrue(result.is_done, "완료 마일스톤이 IN_PROGRESS 로 강등/부활되었습니다")
        self.assertEqual(result.version, "v2.0")

    def test_pending_equivalent_capability_is_updated_in_place(self):
        self._write_roadmap(
            "# Roadmap\n\n"
            "## v2.0 — 실시간 본문 텍스트 검색 및 뷰포트 하이라이트 🔄\n"
            "- [ ] 검색 인덱싱\n- [ ] 하이라이트 오버레이\n"
        )
        mgr = RoadmapManager(self.tmp)
        mgr.load()
        before = len(mgr._milestones)

        mgr.append_or_update_adaptive_milestone(
            version="v2.1",
            title="실시간 본문 텍스트 검색 및 뷰포트 하이라이트",
            tasks=["검색 인덱싱", "하이라이트 오버레이", "결과 순환 이동"],
            status="IN_PROGRESS",
        )

        self.assertEqual(len(mgr._milestones), before, "동일 역량이 중복 append 되었습니다")

    # ── 2. Commander adaptive guard ─────────────────────────────────────────
    def test_adaptive_plan_skips_delivered_feedback(self):
        self._seed_delivered_components(self.tmp)
        reality = Commander.inspect_codebase_reality(self.tmp)

        plan = Commander._build_heuristic_adaptive_plan(
            root_user_goal=FEEDBACK_GOAL,
            reality=reality,
            domain="mobile_app",
            target_version="v2.1",
            workspace=self.tmp,
        )

        self.assertNotIn("사용자 피드백", plan.title, "이미 전달된 피드백 마일스톤이 재발주되었습니다")
        self.assertFalse(
            any("원본 텍스트 스트림 직접 수정" in t for t in plan.what),
            "이미 구현된 In-place 텍스트 편집이 재기획되었습니다",
        )
        self.assertTrue(plan.title.strip())

    def test_fresh_workspace_still_receives_feedback_milestone(self):
        reality = Commander.inspect_codebase_reality(self.tmp)
        plan = Commander._build_heuristic_adaptive_plan(
            root_user_goal=FEEDBACK_GOAL,
            reality=reality,
            domain="mobile_app",
            target_version="v1.0",
            workspace=self.tmp,
        )
        self.assertIn("사용자 피드백", plan.title)
        self.assertTrue(any("원본 텍스트 스트림 직접 수정" in t for t in plan.what))

    async def test_ai_plan_repeating_delivered_work_is_rejected(self):
        self._seed_delivered_components(self.tmp)
        mock_ai_json = (
            '{"title": "' + FEEDBACK_TITLE + '", '
            '"delta_analysis": "재요청", '
            '"why": "사용자 피드백 반영", '
            '"what": ["첨부파일 컬러 팔레트 기반 브랜드 테마, 신규 로고 및 앱 이름 리뉴얼", '
            '"기존 PDF 내 원본 텍스트 스트림 직접 수정(In-place Text Stream Editing, No-OCR) 엔진 구현"], '
            '"how": "리팩토링", '
            '"who_where": "app/src/main", '
            '"when_acceptance": ["빌드 통과"], '
            '"visual_approved": true}'
        )
        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=mock_ai_json)):
            plan = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal=FEEDBACK_GOAL,
                workspace=self.tmp,
                domain="mobile_app",
            )

        self.assertNotIn("사용자 피드백", plan.title, "이미 전달된 역량을 재제안한 AI 계획이 통과되었습니다")

    # ── 3. Ledger reconciliation ("진행했으면 체크") ──────────────────────────
    async def test_delivered_capability_is_checked_off_in_roadmap(self):
        self._seed_delivered_components(self.tmp)
        self._write_roadmap(
            "# Roadmap\n\n"
            "## v2.0 — 사용자 피드백 긴급 반영: 브랜드 리뉴얼(로고·앱명·컬러), "
            "버튼 가독성 개선 및 PDF 원본 텍스트 직접 편집 🔄\n"
            "- [ ] 브랜드 리뉴얼\n- [ ] PDF 원본 텍스트 편집\n"
        )

        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=None)):
            plan = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal=FEEDBACK_GOAL,
                workspace=self.tmp,
                domain="mobile_app",
            )

        with open(os.path.join(self.tmp, "ROADMAP.md"), "r", encoding="utf-8") as f:
            roadmap = f.read()
        self.assertIn("## v2.0", roadmap)
        self.assertIn("✅", roadmap, "디스크에 실구현된 역량이 ROADMAP에서 완료 체크되지 않았습니다")
        self.assertNotIn("사용자 피드백", plan.title)

    # ── 4. Domain-relevant quality brief ────────────────────────────────────
    def test_irrelevant_quality_dimension_is_not_reported_as_gap(self):
        """A PDF app must not be told its 'worst gap' is GAMEPLAY (0%)."""
        QualityStateManager.evaluate_and_update(
            workspace=self.tmp,
            milestone_version="v2.0",
            qc_results=[
                {"id": "QC-U-01", "category": "ux", "status": "PASS"},
                {"id": "QC-V-01", "category": "visual", "status": "PASS"},
            ],
            functional_ac_passed=True,
        )
        summary = QualityStateManager.format_state_summary_for_commander(self.tmp)
        self.assertNotIn("GAMEPLAY", summary, "도메인 무관 GAMEPLAY 차원이 결손으로 오보되었습니다")
        self.assertIn("UX", summary)
        self.assertIn("VISUAL", summary)


if __name__ == "__main__":
    unittest.main()
