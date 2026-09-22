#!/usr/bin/env python3
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from product_planner import AutonomousProductPlanner, normalize_product_title, strip_prompt_directives
from evidence_validator import EvidenceValidator
from android_build_manager import AndroidBuildManager
from director_feedback_hub import DirectorFeedbackHub, VisualTikiTakaTurn


class TestFourCoreDeductionFixes(unittest.IsolatedAsyncioTestCase):
    """
    Test suite verifying the permanent eradication of the 4 core deduction factors (-14 points):
    1. Prompt Directive Tag Stripping & Workspace Tech Stack Priority (product_planner.py)
    2. Top Chrome Density Linter & 70% Viewport Constitution (evidence_validator.py, android_rules.md)
    3. Pre-Flight Compile Check Gate (android_build_manager.py, trio_agent_runner.py)
    4. AutoPilot / Commander Bureaucratic Briefing Muting (auto_pilot.py, director_feedback_hub.py)
    """

    def setUp(self):
        self.test_ws = tempfile.mkdtemp(prefix="test_deduction_fixes_")

    def tearDown(self):
        shutil.rmtree(self.test_ws, ignore_errors=True)

    # -------------------------------------------------------------
    # 1. Prompt Directive Tag Defense & Physical Workspace Stack Priority
    # -------------------------------------------------------------
    def test_strip_prompt_directives_removes_system_tags_and_headers(self):
        sample_prompt = (
            "[CRITICAL SYSTEM & TOOL SAFETY INSTRUCTIONS]\n"
            "1. TOOL USAGE RULES: do not crash\n"
            "[🎯 WORKSPACE FOCUS & CONSTRAINTS]\n"
            "• 🎯 **집중 과제**: REQ-001 (Cross-Platform E2E Autonomous Testing)\n"
            "• 🏛️ **활성 프로파일**: ⚙️ Profile: CLI, Daemon & Automation Agent (cli_agent_rules)\n"
            "[Zero-Production-Diff]: 안드로이드 알PDF 모바일 에디터 개발\n"
            "────────────────────────────────────────\n"
            "상세 개발을 시작합니다."
        )
        cleaned = strip_prompt_directives(sample_prompt)
        self.assertNotIn("Zero-Production-Diff", cleaned)
        self.assertNotIn("CRITICAL SYSTEM", cleaned)
        self.assertNotIn("WORKSPACE FOCUS", cleaned)
        self.assertIn("안드로이드 알PDF 모바일 에디터 개발", cleaned)

    def test_normalize_product_title_ignores_directive_tags(self):
        prompt_with_tag = "[Zero-Production-Diff]: 안드로이드 알PDF 모바일 에디터 개발"
        title = normalize_product_title(prompt_with_tag, domain="mobile_app")
        self.assertNotIn("Zero-Production-Diff", title)
        self.assertNotIn("[", title)
        self.assertEqual(title, "알PDF 모바일 에디터")

        prompt_milestone = "[마일스톤 v1.1]: 주차 메모 앱 UI 개선"
        title_ms = normalize_product_title(prompt_milestone, domain="mobile_app")
        self.assertNotIn("마일스톤", title_ms)
        self.assertEqual(title_ms, "주차 메모 앱")

    def test_detect_domain_prioritizes_physical_android_workspace(self):
        # Create physical Android files in workspace
        with open(os.path.join(self.test_ws, "build.gradle"), "w") as f:
            f.write("// Android Gradle Build")
        with open(os.path.join(self.test_ws, "AndroidManifest.xml"), "w") as f:
            f.write("<manifest></manifest>")

        # Prompt contains CLI/Daemon directives that previously caused misclassification
        confusing_prompt = (
            "[Zero-Production-Diff]: REQ-001 CLI, Daemon & Automation Agent 고도화 시스템 아키텍처"
        )
        domain = AutonomousProductPlanner.detect_domain(confusing_prompt, workspace=self.test_ws)
        self.assertEqual(
            domain, "mobile_app",
            "Physical workspace containing build.gradle and AndroidManifest.xml must take priority over prompt CLI directives!"
        )

    def test_detect_domain_preserves_cli_for_non_android_workspaces(self):
        # Workspace without Android files
        domain = AutonomousProductPlanner.detect_domain(
            "Build a high performance CLI log parser in Rust",
            workspace=self.test_ws
        )
        self.assertEqual(domain, "cli_agent")

    # -------------------------------------------------------------
    # 2. Top Chrome Density Linter & 70% Viewport Constitution
    # -------------------------------------------------------------
    def test_top_chrome_density_linter_detects_overstacking(self):
        # Create a Compose file with 5 stacked top bars exceeding 3 tiers and 150dp
        kt_path = os.path.join(self.test_ws, "OverStackedScreen.kt")
        with open(kt_path, "w", encoding="utf-8") as f:
            f.write(
                "package com.example\n"
                "import androidx.compose.material3.*\n"
                "@Composable\n"
                "fun OverStackedScreen() {\n"
                "    Column {\n"
                "        TopAppBar(title = { Text('Title', modifier = Modifier.basicMarquee()) })\n"
                "        Banner(modifier = Modifier.height(48.dp))\n"
                "        TabRow(selectedTabIndex = 0) {}\n"
                "        Toolbar()\n"
                "        GuideBar()\n"
                "    }\n"
                "}\n"
            )

        lint_res = EvidenceValidator.lint_android_ui_specifications(self.test_ws)
        self.assertTrue(lint_res["is_android_project"])
        self.assertFalse(lint_res["top_chrome_density_passed"])
        self.assertFalse(lint_res["passed"])
        self.assertTrue(len(lint_res["top_chrome_violations"]) > 0)
        self.assertIn("과밀집", lint_res["top_chrome_violations"][0])

    def test_top_chrome_density_linter_passes_with_collapsible_or_nested_scroll(self):
        # Create a Compose file with collapsible / nestedScroll defense
        kt_path = os.path.join(self.test_ws, "CollapsibleScreen.kt")
        with open(kt_path, "w", encoding="utf-8") as f:
            f.write(
                "package com.example\n"
                "import androidx.compose.material3.*\n"
                "@Composable\n"
                "fun CollapsibleScreen() {\n"
                "    val scrollBehavior = TopAppBarDefaults.enterAlwaysScrollBehavior()\n"
                "    Scaffold(\n"
                "        modifier = Modifier.nestedScroll(scrollBehavior.nestedScrollConnection),\n"
                "        topBar = {\n"
                "            Column {\n"
                "                TopAppBar(title = { Text('Title', modifier = Modifier.basicMarquee()) })\n"
                "                AnimatedVisibility(visible = true) {\n"
                "                    Banner()\n"
                "                }\n"
                "                TabRow(selectedTabIndex = 0) {}\n"
                "            }\n"
                "        }\n"
                "    ) {}\n"
                "}\n"
            )

        lint_res = EvidenceValidator.lint_android_ui_specifications(self.test_ws)
        self.assertTrue(lint_res["is_android_project"])
        self.assertTrue(lint_res["top_chrome_density_passed"])

    def test_android_rules_reinforces_70_percent_viewport_and_chrome_density(self):
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles", "android_rules.md")
        self.assertTrue(os.path.isfile(rules_path))
        with open(rules_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("70%", content)
        self.assertIn("65%", content)  # Backward compatible reference
        self.assertIn("3단", content)
        self.assertIn("150dp", content)
        self.assertIn("NestedScrollConnection", content)

    # -------------------------------------------------------------
    # 3. Pre-Flight Compile Check Gate
    # -------------------------------------------------------------
    async def test_preflight_compile_check_skips_non_android_workspace(self):
        res = await AndroidBuildManager.run_preflight_compile_check(self.test_ws)
        self.assertFalse(res["is_android"])
        self.assertTrue(res["passed"])

    async def test_preflight_compile_check_detects_compilation_failure(self):
        # Create gradlew dummy
        with open(os.path.join(self.test_ws, "gradlew"), "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(self.test_ws, "gradlew"), 0o755)

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (
            b"e: /path/to/File.kt: (12, 5): Unresolved reference: NonExistentSymbol\n"
            b"Compilation error in Kotlin compiler\n",
            b""
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            res = await AndroidBuildManager.run_preflight_compile_check(self.test_ws)
            self.assertTrue(res["is_android"])
            self.assertFalse(res["passed"])
            self.assertIn("Unresolved reference", res["error"])

    async def test_preflight_compile_check_succeeds_on_clean_sources(self):
        with open(os.path.join(self.test_ws, "gradlew"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(self.test_ws, "gradlew"), 0o755)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"BUILD SUCCESSFUL in 1s\n", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            res = await AndroidBuildManager.run_preflight_compile_check(self.test_ws)
            self.assertTrue(res["is_android"])
            self.assertTrue(res["passed"])

    # -------------------------------------------------------------
    # 4. AutoPilot / Commander Bureaucratic Briefing Muting
    # -------------------------------------------------------------
    def test_mute_bureaucratic_briefing_persists_to_reports_and_returns_compact_line(self):
        verbose_briefing = (
            "📋 🧪 **[AutoPilot 사전 E2E 시나리오 브리핑 (6-Step Scenario)]**\n"
            "• 검증 방식: 오직 Android MCP 풀 에뮬레이터 테스트만 단일 수행\n"
            "• 1단계: 콜드 스타트\n"
            "• 2단계: UI 레이아웃\n"
            "• 3단계: 핵심 인터랙션\n"
            "• 4단계: 데이터 조작\n"
            "• 5단계: 라이프사이클\n"
            "• 6단계: 크래시 정밀 진단\n"
        )
        crop_file = os.path.join(self.test_ws, "defect_crop.png")
        with open(crop_file, "w") as f:
            f.write("mock")

        compact_msg, saved_path = DirectorFeedbackHub.mute_bureaucratic_briefing(
            workspace=self.test_ws,
            full_briefing=verbose_briefing,
            filename="e2e_scenario_briefing.md",
            compact_status_line="📋 🧪 **[AutoPilot 6-Step E2E 검수 (사이클 2)]** 가동 중...",
            crop_image_path=crop_file
        )

        # Verify saved report
        self.assertTrue(os.path.isfile(saved_path))
        with open(saved_path, "r", encoding="utf-8") as f:
            saved_content = f.read()
        self.assertIn("6-Step Scenario", saved_content)

        # Verify compact message for Discord
        self.assertIn("가동 중...", compact_msg)
        self.assertIn("defect_crop.png", compact_msg)
        self.assertNotIn("• 1단계", compact_msg)
        self.assertNotIn("• 6단계", compact_msg)

    def test_visual_tiki_taka_turn_compact_format(self):
        crop_file = os.path.join(self.test_ws, "crop.png")
        with open(crop_file, "w") as f:
            f.write("mock")

        turn = VisualTikiTakaTurn(
            speaker="QA",
            status="REJECTED",
            concise_feedback="상단 툴바 높이가 160dp로 뷰포트를 침범합니다.\n접기 적용이 필요합니다.",
            crop_image_path=crop_file
        )
        compact_msg = turn.format_discord_message(compact=True)
        self.assertEqual(len(compact_msg.splitlines()), 1)
        self.assertIn("[REJECTED] **QA**", compact_msg)
        self.assertIn("crop.png", compact_msg)


if __name__ == "__main__":
    unittest.main()
