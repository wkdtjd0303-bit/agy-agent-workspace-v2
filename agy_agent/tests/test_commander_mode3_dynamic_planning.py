#!/usr/bin/env python3
"""
Unit tests for Commander Mode 3 Dynamic AI Planning and Anti-Mocking Verification.
Validates:
1. Dynamic bespoke product planning (no canned boilerplate strings like PulseDesk Web or DocuFlow Mobile).
2. Domain-tailored milestones without blind 65% canvas viewport injection for CLI/Daemon/Backend.
3. Mobile editor apps maintain 65% canvas viewport quality constitution.
4. Dynamic ResolvingDict returns immediate sync baseline and resolves with async AI planning.
5. Mode 3 explicit command tagging and milestone ladder generation.
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

from commander import Commander, ResolvingDict
from product_planner import AutonomousProductPlanner


class TestCommanderMode3DynamicPlanning(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_mode3_dyn_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sync_create_autonomous_product_plan_cli_no_65_percent_tasks(self):
        """CLI tool requests must not contain '65% 캔버스 뷰포트' in milestone tasks."""
        prompt = "주식 포트폴리오 백테스팅 고성능 CLI 자동화 도구 개발해줘"
        plan_dict = Commander.create_autonomous_product_plan(prompt, self.test_dir)

        p_def = plan_dict["product_def"]
        self.assertIn("cli_agent", p_def.domain)
        self.assertNotIn("PulseDesk", p_def.app_naming)
        self.assertNotIn("DocuFlow", p_def.app_naming)

        # Milestones check
        milestones = plan_dict["milestones"]
        self.assertEqual(len(milestones), 3)
        v1_tasks = milestones[0]["tasks"]
        for task in v1_tasks:
            self.assertNotIn("65%", task)

        # PRD & ROADMAP file verification
        prd_file = os.path.join(self.test_dir, "PRD.md")
        roadmap_file = os.path.join(self.test_dir, "ROADMAP.md")
        self.assertTrue(os.path.isfile(prd_file))
        self.assertTrue(os.path.isfile(roadmap_file))

        with open(roadmap_file, "r", encoding="utf-8") as f:
            r_content = f.read()
        self.assertNotIn("65% 캔버스 뷰포트", r_content)
        self.assertIn("v1.0", r_content)
        self.assertIn("v2.0", r_content)
        self.assertIn("v3.0", r_content)

    def test_sync_create_autonomous_product_plan_mobile_editor_retains_65_percent(self):
        """Mobile editor apps retain 65% viewport constitution in UX and roadmap."""
        prompt = "알PDF 모바일 안드로이드 에디터 앱 개발해줘"
        plan_dict = Commander.create_autonomous_product_plan(prompt, self.test_dir)

        p_def = plan_dict["product_def"]
        self.assertEqual(p_def.domain, "mobile_app")

        prd_file = os.path.join(self.test_dir, "PRD.md")
        roadmap_file = os.path.join(self.test_dir, "ROADMAP.md")
        self.assertTrue(os.path.isfile(prd_file))
        self.assertTrue(os.path.isfile(roadmap_file))

        with open(prd_file, "r", encoding="utf-8") as f:
            p_content = f.read()
        self.assertIn("65%", p_content)

        with open(roadmap_file, "r", encoding="utf-8") as f:
            r_content = f.read()
        self.assertIn("65%", r_content)

    async def test_async_create_autonomous_product_plan_ai_synthesis(self):
        """Async planner parses JSON from LLM and produces bespoke ProductPlan."""
        mock_ai_json = """{
            "title": "가계부 스마트 매니저",
            "app_naming": "머니로그 (MoneyLog) — 스마트 개인 가계부",
            "domain": "saas_web",
            "platform": "반응형 웹 (모바일 390px ~ 데스크톱 1280px)",
            "target_demographic": "스마트 소비 및 예산 관리를 원하는 20~40대",
            "core_experience": "수입/지출 내역 원터치 등록 및 실시간 소비 대시보드 시각화",
            "key_value_proposition": "군더더기 없는 지출 기록과 직관적 예산 시각화",
            "design_vibe": "모던 미니멀 에메랄드 테마 (#10B981, #0F172A)",
            "monetization_strategy": "기본 기능 무료 + 고급 자산 분석 프리미엄 모델",
            "milestones": [
                {
                    "version": "v1.0",
                    "title": "핵심 지출/수입 CRUD 및 실시간 대시보드",
                    "dimension": "functionality",
                    "tasks": [
                        "수입/지출 내역 등록 및 카테고리별 목록 조회 UI 구현",
                        "로컬 스토리지를 이용한 실시간 잔액 계산 및 데이터 영속화",
                        "기본 입력 유효성 검사 및 반응형 레이아웃 구성"
                    ],
                    "target_quality": "수입/지출 기본 기능 정상 동작"
                },
                {
                    "version": "v2.0",
                    "title": "월별 예산 통계 및 차트 시각화",
                    "dimension": "ux_ergonomics",
                    "tasks": [
                        "카테고리별 지출 비율 도넛 차트 시각화",
                        "기간별 내역 검색 및 필터링 기능 구현",
                        "예산 초과 시각 알림 배지 구현"
                    ],
                    "target_quality": "통계 시각화 및 검색/필터링 완비"
                },
                {
                    "version": "v3.0",
                    "title": "데이터 CSV 내보내기 및 E2E 무결점 검증",
                    "dimension": "stability_e2e",
                    "tasks": [
                        "지출 데이터 CSV 내보내기/가져오기 구현",
                        "콘솔 에러 0건 및 모바일/웹 크로스 브라우징 E2E 검증",
                        "프로덕션 빌드 최적화 및 패키징"
                    ],
                    "target_quality": "전 기능 E2E 테스트 100% 무결점 통과"
                }
            ],
            "initial_directive": "🎯 [Commander 사령관 1차 마일스톤 개발 지침: 가계부 스마트 매니저]\\n핵심 지출/수입 등록 및 실시간 잔액 계산 MVP를 구축하십시오."
        }"""

        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=mock_ai_json)):
            plan = await Commander.create_autonomous_product_plan_async("가계부 웹앱 만들어줘", self.test_dir)

            self.assertEqual(plan["source"], "ai")
            p_def = plan["product_def"]
            self.assertEqual(p_def.title, "가계부 스마트 매니저")
            self.assertEqual(p_def.app_naming, "머니로그 (MoneyLog) — 스마트 개인 가계부")
            self.assertIn("모던 미니멀 에메랄드 테마", p_def.design_vibe)

            milestones = plan["milestones"]
            self.assertEqual(len(milestones), 3)
            self.assertEqual(milestones[0]["version"], "v1.0")
            self.assertIn("지출/수입", milestones[0]["title"])

            # Verify saved PRD.md & ROADMAP.md
            with open(os.path.join(self.test_dir, "PRD.md"), "r", encoding="utf-8") as f:
                prd = f.read()
            self.assertIn("머니로그", prd)

            with open(os.path.join(self.test_dir, "ROADMAP.md"), "r", encoding="utf-8") as f:
                roadmap = f.read()
            self.assertIn("핵심 지출/수입 CRUD", roadmap)

            # Verify visual asset approval injection for GUI app
            self.assertIn("<@1539448922643234876>", plan["initial_directive"])
            self.assertIn("<@1539448922643234876>", plan["visual_asset_spec"])
            self.assertIn("Type A", plan["visual_asset_spec"])
            self.assertIn("Type B", plan["visual_asset_spec"])

    def test_resolving_dict_sync_and_async_behavior(self):
        """ResolvingDict behaves as normal dict synchronously and resolves on await."""
        base = {"status": "baseline", "count": 1}

        async def _mock_coro():
            return {"status": "ai_enhanced", "count": 99}

        rd = ResolvingDict(base, async_coro=_mock_coro)
        # Synchronous access
        self.assertEqual(rd["status"], "baseline")
        self.assertEqual(rd["count"], 1)

        # Async awaiting resolves and updates
        async def _test():
            resolved = await rd
            self.assertEqual(resolved["status"], "ai_enhanced")
            self.assertEqual(resolved["count"], 99)

        import asyncio
        asyncio.run(_test())

    def test_sync_create_autonomous_product_plan_game_has_visual_asset_approval(self):
        """Game and Mobile apps include explicit Commander <@1539448922643234876> visual asset approvals."""
        prompt = "2D 캐주얼 아케이드 점프 게임 제작해줘"
        plan_dict = Commander.create_autonomous_product_plan(prompt, self.test_dir)

        p_def = plan_dict["product_def"]
        self.assertEqual(p_def.domain, "game")

        # 1. visual_asset_spec check
        vis_spec = plan_dict.get("visual_asset_spec", "")
        self.assertIn("<@1539448922643234876>", vis_spec)
        self.assertIn("Type A", vis_spec)
        self.assertIn("Type B", vis_spec)
        self.assertIn("generate_image", vis_spec)

        # 2. initial_directive check
        directive = plan_dict["initial_directive"]
        self.assertIn("<@1539448922643234876>", directive)
        self.assertIn("시각 에셋 공식 승인", directive)

        # 3. v2 milestone task check
        v2_tasks = plan_dict["milestones"][1]["tasks"]
        v2_tasks_str = " ".join(v2_tasks)
        self.assertIn("<@1539448922643234876>", v2_tasks_str)
        self.assertIn("generate_image", v2_tasks_str)

        # 4. PRD.md & ROADMAP.md persistence
        with open(os.path.join(self.test_dir, "PRD.md"), "r", encoding="utf-8") as f:
            prd = f.read()
        self.assertIn("<@1539448922643234876>", prd)
        self.assertIn("시각 에셋 승인 규격", prd)

        with open(os.path.join(self.test_dir, "ROADMAP.md"), "r", encoding="utf-8") as f:
            roadmap = f.read()
        self.assertIn("<@1539448922643234876>", roadmap)
        self.assertIn("generate_image", roadmap)

    def test_sync_create_autonomous_product_plan_cli_omits_visual_asset_approval(self):
        """CLI tool requests must not include visual asset image generation tasks or approvals."""
        prompt = "서버 로그 분석 고성능 CLI 자동화 도구 개발해줘"
        plan_dict = Commander.create_autonomous_product_plan(prompt, self.test_dir)

        p_def = plan_dict["product_def"]
        self.assertEqual(p_def.domain, "cli_agent")

        vis_spec = plan_dict.get("visual_asset_spec", "")
        self.assertIn("헤드리스", vis_spec)
        self.assertNotIn("generate_image 호출 및", vis_spec)

        directive = plan_dict["initial_directive"]
        self.assertNotIn("generate_image 호출", directive)

        # Milestones must not have generate_image tasks
        for ms in plan_dict["milestones"]:
            for t in ms["tasks"]:
                self.assertNotIn("generate_image", t)

    def test_roadmap_manager_evolve_prompt_injects_commander_visual_approval(self):
        """RoadmapManager.build_evolve_prompt injects Commander <@1539448922643234876> approval for v2.0 polish."""
        from roadmap_manager import RoadmapManager
        roadmap_content = (
            "# 🗺️ Project Roadmap\n\n"
            "## v1.0 — 코어 MVP 🔄\n"
            "- [ ] 기본 기능 구현 완료\n\n"
            "## v2.0 — 심층 UI & 비주얼 폴리싱\n"
            "- [ ] [사령관 <@1539448922643234876> 승인] generate_image 고품질 에셋 생성 및 UI 바인딩\n"
            "- [ ] 검색 및 필터링 구현\n\n"
            "## v3.0 — 상용 배포 릴리즈\n"
            "- [ ] E2E 검증 통과\n"
        )
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(roadmap_content)

        rm = RoadmapManager(self.test_dir)
        evolve_prompt = rm.build_evolve_prompt("이전 v1.0 승인 완료")

        self.assertIn("<@1539448922643234876>", evolve_prompt)
        self.assertIn("시각 에셋 생성 승인", evolve_prompt)
        self.assertIn("Type A", evolve_prompt)
        self.assertIn("Type B", evolve_prompt)
        self.assertIn("generate_image", evolve_prompt)

    def test_adaptive_sonic_game_multi_loop_codebase_grounding(self):
        """
        Validates user's Sonic Game example:
        Turn 1: Empty workspace -> Core game loop & architecture
        Turn 2: Core loop exists on disk -> Main title screen & menu
        Turn 3: Title exists on disk -> Sonic character physics & movement
        Turn 4: Character exists on disk -> Enemy patrol & collision system
        """
        root_goal = "안드로이드에서 구동 가능한 소닉 게임을 만들어줘"

        # Turn 1: Empty workspace
        plan_t1 = Commander.plan_adaptive_next_milestone(
            root_user_goal=root_goal,
            workspace=self.test_dir
        )
        self.assertEqual(plan_t1.version, "v1.0")
        self.assertIn("코어 게임", plan_t1.title)
        self.assertTrue(any("루프" in t or "엔진" in t for t in plan_t1.what))

        # Simulate Coder creating core game engine & loop files
        src_dir = os.path.join(self.test_dir, "app", "src", "main", "java", "com", "example", "sonic")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "MainActivity.kt"), "w") as f:
            f.write("class MainActivity")
        with open(os.path.join(src_dir, "GameLoop.kt"), "w") as f:
            f.write("class GameLoop")

        # Turn 2: Disk now has GameLoop & MainActivity
        plan_t2 = Commander.plan_adaptive_next_milestone(
            root_user_goal=root_goal,
            workspace=self.test_dir
        )
        self.assertEqual(plan_t2.version, "v1.1")
        self.assertIn("타이틀", plan_t2.title)
        self.assertTrue(any("메뉴" in t or "타이틀" in t for t in plan_t2.what))

        # Simulate Coder creating TitleScreen
        with open(os.path.join(src_dir, "TitleScreen.kt"), "w") as f:
            f.write("class TitleScreen")

        # Turn 3: Disk now has TitleScreen
        plan_t3 = Commander.plan_adaptive_next_milestone(
            root_user_goal=root_goal,
            workspace=self.test_dir
        )
        self.assertEqual(plan_t3.version, "v1.2")
        self.assertIn("캐릭터", plan_t3.title)
        self.assertTrue(any("물리" in t or "조작" in t or "점프" in t for t in plan_t3.what))

        # Simulate Coder creating SonicPlayer
        with open(os.path.join(src_dir, "SonicPlayer.kt"), "w") as f:
            f.write("class SonicPlayer")

        # Turn 4: Disk now has SonicPlayer
        plan_t4 = Commander.plan_adaptive_next_milestone(
            root_user_goal=root_goal,
            workspace=self.test_dir
        )
        self.assertEqual(plan_t4.version, "v1.3")
        self.assertIn("적 몬스터", plan_t4.title)
        self.assertTrue(any("순찰" in t or "충돌" in t for t in plan_t4.what))

    def test_adaptive_pdf_app_prevents_duplicate_page_manager(self):
        """
        Validates PDF App scenario:
        Existing codebase has Viewer, Canvas, Export, and PageManager.
        Commander re-inspects disk, acknowledges them, and DOES NOT re-plan PageManager.
        Instead, plans the un-implemented next feature (Search & Highlight Overlay).
        """
        root_goal = "읽기 및 편집이 가능한 pdf 뷰어 겸 에디터 안드로이드 어플"

        src_dir = os.path.join(self.test_dir, "app", "src", "main", "java", "com", "example", "pdfapp")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(src_dir, "MainActivity.kt"), "w") as f:
            f.write("class MainActivity")
        with open(os.path.join(src_dir, "PdfViewerScreen.kt"), "w") as f:
            f.write("class PdfViewerScreen")
        with open(os.path.join(src_dir, "AnnotationCanvas.kt"), "w") as f:
            f.write("class AnnotationCanvas")
        with open(os.path.join(src_dir, "PdfExportEngine.kt"), "w") as f:
            f.write("class PdfExportEngine")
        with open(os.path.join(src_dir, "PageManagerDialog.kt"), "w") as f:
            f.write("class PageManagerDialog")

        # Existing ROADMAP.md with v1.0, v2.0, v3.0 completed
        roadmap_content = (
            "# Roadmap\n\n"
            "## v1.0 — 코어 PDF 뷰어 MVP ✅\n"
            "- [x] 렌더링 파이프라인\n\n"
            "## v2.0 — 주석 캔버스 레이어 ✅\n"
            "- [x] 펜 드로잉\n\n"
            "## v3.0 — 페이지 조작 도구 ✅\n"
            "- [x] 페이지 관리자\n"
        )
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(roadmap_content)

        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal=root_goal,
            workspace=self.test_dir
        )

        # Version must advance past v3.0
        self.assertEqual(plan.version, "v3.1")
        # Must NOT duplicate PageManager or Canvas!
        self.assertNotIn("페이지 관리자", plan.title)
        self.assertNotIn("주석 캔버스", plan.title)
        # Must plan text search!
        self.assertIn("검색", plan.title)
        self.assertTrue(any("검색" in t for t in plan.what))

        # Check that ROADMAP.md has the new adaptive milestone recorded
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "r", encoding="utf-8") as f:
            updated_rm = f.read()
        self.assertIn("v3.1", updated_rm)
        self.assertIn("검색", updated_rm)


if __name__ == "__main__":
    unittest.main()


