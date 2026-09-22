import os
import sys
import unittest
import tempfile
import shutil
import subprocess
from unittest.mock import MagicMock, AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import config
from session_manager import ChannelSession
from evidence_validator import EvidenceValidator
from product_planner import AutonomousProductPlanner, normalize_product_title, APP_QUALITY_DIMENSIONS
from trio_agent_runner import TrioAgentRunner, QuotaManager


class TestZeroProductionDiffMechanicalGate(unittest.IsolatedAsyncioTestCase):
    """
    Pillar 1: Zero-Production-Diff Mechanical Gate
    Blocks transition to QA and Auditor when Coder reports completion
    with 0 git diff lines in production source code, forcing Fail-Fast rework.
    """

    def setUp(self):
        QuotaManager.reset_exhaustion()
        self.temp_dir = tempfile.mkdtemp(prefix="test_zero_prod_diff_")
        # Initialize a real git repository in temp_dir
        subprocess.run(["git", "init"], cwd=self.temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        # Initial commit with a base production file
        os.makedirs(os.path.join(self.temp_dir, "app", "src", "main", "java", "com", "example"), exist_ok=True)
        self.base_kt = os.path.join(self.temp_dir, "app", "src", "main", "java", "com", "example", "MainActivity.kt")
        with open(self.base_kt, "w", encoding="utf-8") as f:
            f.write("// Base initial code\nclass MainActivity {}\n")
        subprocess.run(["git", "add", "."], cwd=self.temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.temp_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        self.session = ChannelSession(
            channel_id=987654321012345678,
            workspace=self.temp_dir,
            mode="pair"
        )
        self.mock_channel = MagicMock()
        self.mock_channel.id = 987654321012345678
        self.mock_channel.send = AsyncMock(return_value=AsyncMock())
        self.mock_channel.typing = MagicMock()
        self.mock_channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        self.mock_channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        self.mock_guild = MagicMock()
        self.mock_noti_ch = MagicMock()
        self.mock_noti_ch.send = AsyncMock()
        self.mock_guild.get_channel.return_value = self.mock_noti_ch
        self.mock_channel.guild = self.mock_guild

    def tearDown(self):
        QuotaManager.reset_exhaustion()
        if hasattr(self, "temp_dir") and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inspect_production_diff_detects_test_only_changes(self):
        """Modifying only test files and JSON reports yields is_zero_production_diff = True."""
        test_dir = os.path.join(self.temp_dir, "tests")
        os.makedirs(test_dir, exist_ok=True)
        test_file = os.path.join(test_dir, "test_feature.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("def test_dummy(): assert True\n")

        reports_dir = os.path.join(self.temp_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        e2e_report = os.path.join(reports_dir, "e2e_test.json")
        with open(e2e_report, "w", encoding="utf-8") as f:
            f.write('{"status": "passed"}\n')

        diff_info = EvidenceValidator.get_git_diff_summary(self.temp_dir)
        prod_metrics = EvidenceValidator.inspect_production_diff(self.temp_dir, diff_info)

        self.assertTrue(prod_metrics["is_zero_production_diff"])
        self.assertEqual(prod_metrics["prod_diff_lines"], 0)
        self.assertEqual(len(prod_metrics["prod_changed_files"]), 0)
        self.assertTrue(len(prod_metrics["non_prod_files"]) >= 2)

    def test_inspect_production_diff_passes_real_prod_diff(self):
        """Modifying production code yields is_zero_production_diff = False with exact diff lines."""
        with open(self.base_kt, "a", encoding="utf-8") as f:
            f.write("fun calculateBonus(): Int = 100\n")

        diff_info = EvidenceValidator.get_git_diff_summary(self.temp_dir)
        prod_metrics = EvidenceValidator.inspect_production_diff(self.temp_dir, diff_info)

        self.assertFalse(prod_metrics["is_zero_production_diff"])
        self.assertGreater(prod_metrics["prod_diff_lines"], 0)
        self.assertTrue(any("MainActivity.kt" in f for f in prod_metrics["prod_changed_files"]))

    def test_inspect_production_diff_detects_untracked_new_prod_file(self):
        """Creating an untracked new production file on disk counts lines and passes."""
        new_svc = os.path.join(self.temp_dir, "app", "src", "main", "java", "com", "example", "BillingService.kt")
        with open(new_svc, "w", encoding="utf-8") as f:
            f.write("package com.example\nclass BillingService {\n    fun pay() = true\n}\n")

        diff_info = EvidenceValidator.get_git_diff_summary(self.temp_dir)
        prod_metrics = EvidenceValidator.inspect_production_diff(self.temp_dir, diff_info)

        self.assertFalse(prod_metrics["is_zero_production_diff"])
        self.assertGreaterEqual(prod_metrics["prod_diff_lines"], 3)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_trio_runner_blocks_zero_prod_diff_fail_fast(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """
        When Coder produces 0 production diff in Round 1, the pipeline blocks transition
        to QA/Auditor, posts the Zero-Production-Diff alert banner, and sends rework feedback to Coder.
        In Round 2, Coder updates production code, allowing QA and Auditor to run and approve.
        """
        call_count = 0

        async def dynamic_run_prompt(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Round 1 Coder: only writes test file
                test_dir = os.path.join(self.temp_dir, "tests")
                os.makedirs(test_dir, exist_ok=True)
                with open(os.path.join(test_dir, "test_anti_mock.py"), "w", encoding="utf-8") as f:
                    f.write("def test_cheating(): pass\n")
                return "Coder: Test code has been added and passed!"
            elif call_count == 2:
                # Round 2 Coder: writes genuine production code
                with open(self.base_kt, "a", encoding="utf-8") as f:
                    f.write("fun genuineFeature(): String = 'Real Production Code'\n")
                return "Coder: Genuinely updated production MainActivity.kt."
            elif call_count == 3:
                # Round 2 QA1
                return "[STATUS: APPROVED]\n### QA Kotlin 코드 검증\n`MainActivity.kt` 파일의 genuineFeature() 함수 구현을 검증하였습니다. `fun genuineFeature()` 반환값이 정상이며, `build.gradle` 빌드 설정과의 호환성도 확인 완료입니다.\n\n**검증 항목:**\n- 라인 15: genuineFeature 함수 시그니처 정상\n- 라인 22: 반환값 타입 String 확인\n\n**개선 제안:** null safety 적용을 위해 반환 타입에 nullable 처리를 고려하면 좋겠습니다."
            elif call_count == 4:
                # Round 2 Auditor
                return "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor approved."
            return "OK"

        mock_run_prompt.side_effect = dynamic_run_prompt

        res = await TrioAgentRunner.run_pipeline(
            session=self.session,
            prompt="Implement billing feature in Android app",
            main_channel=self.mock_channel,
            max_rounds=2
        )

        self.assertIn("최종 승인", res)
        # Total calls: Round 1 Coder, Round 2 Coder, Round 2 QA1, Round 2 Auditor = 4 calls
        # Notice QA1 and Auditor were NEVER called in Round 1!
        self.assertEqual(call_count, 4)

        # Verify that the Zero-Production-Diff banner was sent to the channel
        sent_messages = [call.args[1] for call in mock_safe_send.call_args_list if len(call.args) > 1]
        zero_diff_alerts = [m for m in sent_messages if "Zero-Production-Diff 기계적 차단" in m]
        self.assertTrue(len(zero_diff_alerts) >= 1)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_trio_runner_allows_zero_prod_diff_for_diagnostic(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """Diagnostic and consulting tasks legitimately have 0 production diff and are not blocked."""
        mock_run_prompt.side_effect = [
            "Coder: Diagnostic findings: Database pool exhausted due to leak.",
            "[STATUS: APPROVED]\nQA1 verified diagnosis reasoning.",
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor agreed with diagnosis."
        ]

        res = await TrioAgentRunner.run_pipeline(
            session=self.session,
            prompt="현재 시스템 상태 및 오류 원인을 진단하고 분석해줘",
            main_channel=self.mock_channel,
            max_rounds=1
        )

        self.assertIn("최종 승인", res)
        self.assertEqual(mock_run_prompt.call_count, 3)


class TestProjectTitleNormalizer(unittest.TestCase):
    """
    Pillar 2: Project Title Normalizer (normalize_product_title)
    Refines lengthy conversational user prompts into concise 2~4 word project titles,
    fixing header truncation and visual layout bugs in PRD.md and ROADMAP.md.
    """

    def test_normalize_product_title_conversational_prompts(self):
        """Verifies normalization across various product domains and sentence lengths."""
        # 1. Mobile / PDF
        prompt1 = "알PDF 모바일 안드로이드 에디터 앱을 새로 개발하고 싶습니다. PDF 뷰어와 편집 기능이 들어가야 합니다."
        self.assertEqual(normalize_product_title(prompt1, "mobile_app"), "알PDF 모바일 에디터")

        # 2. Retro / Platformer Game
        prompt2 = "슈퍼 마리오 브라더스 스타일의 레트로 2D 플랫포머 게임을 만들어줘."
        self.assertEqual(normalize_product_title(prompt2, "game"), "마리오 플랫포머 게임")

        # 3. Todo / Utility App
        prompt3 = "사용자가 할 일을 등록하고 체크할 수 있는 모바일 투두리스트 앱을 제작해줘"
        title3 = normalize_product_title(prompt3, "mobile_app")
        self.assertIn("투두", title3)
        self.assertLessEqual(len(title3.split()), 4)

        # 4. Quoted title
        prompt4 = "프로젝트 '플러피 버드' 클론 게임을 작성해줘"
        title4 = normalize_product_title(prompt4, "game")
        self.assertIn("플러피", title4)
        self.assertLessEqual(len(title4.split()), 4)

        # 5. Class method invocation
        self.assertEqual(
            AutonomousProductPlanner.normalize_product_title(prompt1),
            "알PDF 모바일 에디터"
        )

    def test_autonomous_planner_uses_normalized_title_in_prd_and_roadmap(self):
        """AutonomousProductPlanner produces PRD and ROADMAP with normalized title."""
        prompt = "알PDF 모바일 안드로이드 에디터 앱을 새로 개발하고 싶습니다. PDF 뷰어와 편집 기능이 들어가야 합니다."
        plan_ws = tempfile.mkdtemp(prefix="test_plan_ws_")
        try:
            plan = AutonomousProductPlanner.plan_product_and_quality(
                user_request=prompt,
                workspace=plan_ws
            )
            self.assertEqual(plan.product_def.title, "알PDF 모바일 에디터")

            with open(os.path.join(plan_ws, "PRD.md"), "r", encoding="utf-8") as f:
                prd_content = f.read()
            with open(os.path.join(plan_ws, "ROADMAP.md"), "r", encoding="utf-8") as f:
                roadmap_content = f.read()

            self.assertIn("# 📋 Product Requirements Document (PRD) — 알PDF 모바일 에디터", prd_content)
            self.assertIn("# 🗺️ Project Roadmap (AGY Autonomous Quality Ladder: 알PDF 모바일 에디터)", roadmap_content)
            self.assertNotIn("개발하고 싶습니다", prd_content.splitlines()[0])
            self.assertNotIn("개발하고 싶습니다", roadmap_content.splitlines()[0])
        finally:
            shutil.rmtree(plan_ws, ignore_errors=True)


class Test65PercentCanvasViewportConstitution(unittest.TestCase):
    """
    Pillar 3: 65% Canvas Viewport Constitution
    Secures >= 65% pure content/canvas viewport, caps fixed bars at <= 35%,
    and mandates Auto-Hide on scroll.
    """

    def test_profiles_contain_65_percent_viewport_and_auto_hide_rules(self):
        """Verifies android_rules.md and web_rules.md contain the 65% viewport constitution."""
        android_rules_path = os.path.join(WORKSPACE, "profiles", "android_rules.md")
        web_rules_path = os.path.join(WORKSPACE, "profiles", "web_rules.md")

        self.assertTrue(os.path.isfile(android_rules_path))
        self.assertTrue(os.path.isfile(web_rules_path))

        with open(android_rules_path, "r", encoding="utf-8") as f:
            a_content = f.read()
            self.assertIn("65%", a_content)
            self.assertIn("35%", a_content)
            self.assertIn("Auto-Hide", a_content)

        with open(web_rules_path, "r", encoding="utf-8") as f:
            w_content = f.read()
            self.assertIn("65%", w_content)
            self.assertIn("35%", w_content)
            self.assertIn("Auto-Hide", w_content)

    def test_product_planner_dimensions_and_milestones_contain_65_viewport(self):
        """Verifies APP_QUALITY_DIMENSIONS and generated milestones enforce 65% viewport."""
        ux_dim = [item for item in APP_QUALITY_DIMENSIONS if item[0] == "ux_ergonomics"][0]
        self.assertIn("65%", ux_dim[2])
        self.assertIn("35%", ux_dim[2])
        self.assertIn("Auto-Hide", ux_dim[2])

        # Generate PRD and Roadmap for mobile editor
        plan_ws = tempfile.mkdtemp(prefix="test_plan_ws_")
        try:
            AutonomousProductPlanner.plan_product_and_quality(
                user_request="알PDF 모바일 안드로이드 에디터 앱",
                workspace=plan_ws
            )
            with open(os.path.join(plan_ws, "PRD.md"), "r", encoding="utf-8") as f:
                prd_md = f.read()
            with open(os.path.join(plan_ws, "ROADMAP.md"), "r", encoding="utf-8") as f:
                roadmap_md = f.read()
            self.assertIn("65%", prd_md)
            self.assertIn("65%", roadmap_md)
        finally:
            shutil.rmtree(plan_ws, ignore_errors=True)


class TestAndroidUISpecificationLinter(unittest.TestCase):
    """
    Pillar 4: Coder Proactive UI Spec Lint Hook
    Verifies static linting of Android Jetpack Compose source code for:
    1. 48dp Touch Target Standard (Modifier.sizeIn(48.dp))
    2. Text Truncation Defense (basicMarquee)
    """

    def setUp(self):
        self.temp_ws = tempfile.mkdtemp(prefix="test_android_ui_lint_")

    def tearDown(self):
        if hasattr(self, "temp_ws") and os.path.exists(self.temp_ws):
            shutil.rmtree(self.temp_ws, ignore_errors=True)

    def test_lint_detects_missing_48dp_touch_targets(self):
        """Button without 48dp modifier fails touch target check."""
        kt_path = os.path.join(self.temp_ws, "EditorScreen.kt")
        with open(kt_path, "w", encoding="utf-8") as f:
            f.write(
                "package com.example\n"
                "import androidx.compose.material3.*\n"
                "@Composable\n"
                "fun EditorScreen() {\n"
                "    IconButton(onClick = {}) { Icon(...) }\n"
                "}\n"
            )

        lint_res = EvidenceValidator.lint_android_ui_specifications(self.temp_ws)
        self.assertTrue(lint_res["is_android_project"])
        self.assertFalse(lint_res["touch_target_48dp_passed"])
        self.assertFalse(lint_res["passed"])
        self.assertTrue(len(lint_res["touch_target_violations"]) > 0)

    def test_lint_passes_with_48dp_touch_targets(self):
        """Interactive elements with Modifier.sizeIn(48.dp) pass touch target check."""
        kt_path = os.path.join(self.temp_ws, "EditorScreen.kt")
        with open(kt_path, "w", encoding="utf-8") as f:
            f.write(
                "package com.example\n"
                "import androidx.compose.material3.*\n"
                "@Composable\n"
                "fun EditorScreen() {\n"
                "    IconButton(modifier = Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp), onClick = {}) { Icon(...) }\n"
                "}\n"
            )

        lint_res = EvidenceValidator.lint_android_ui_specifications(self.temp_ws)
        self.assertTrue(lint_res["is_android_project"])
        self.assertTrue(lint_res["touch_target_48dp_passed"])
        self.assertTrue(lint_res["passed"])

    def test_lint_detects_missing_marquee_on_top_app_bar(self):
        """TopAppBar without basicMarquee defense fails text marquee check."""
        kt_path = os.path.join(self.temp_ws, "MainTopBar.kt")
        with open(kt_path, "w", encoding="utf-8") as f:
            f.write(
                "package com.example\n"
                "import androidx.compose.material3.*\n"
                "@Composable\n"
                "fun MainTopBar() {\n"
                "    TopAppBar(title = { Text('Very Long Dynamic Document Title') })\n"
                "}\n"
            )

        lint_res = EvidenceValidator.lint_android_ui_specifications(self.temp_ws)
        self.assertTrue(lint_res["is_android_project"])
        self.assertFalse(lint_res["text_marquee_passed"])
        self.assertFalse(lint_res["passed"])
        self.assertTrue(len(lint_res["marquee_violations"]) > 0)

    def test_lint_passes_with_basic_marquee(self):
        """TopAppBar with Modifier.basicMarquee() passes text marquee check."""
        kt_path = os.path.join(self.temp_ws, "MainTopBar.kt")
        with open(kt_path, "w", encoding="utf-8") as f:
            f.write(
                "package com.example\n"
                "import androidx.compose.material3.*\n"
                "@Composable\n"
                "fun MainTopBar() {\n"
                "    TopAppBar(title = { Text('Document Title', modifier = Modifier.basicMarquee()) })\n"
                "}\n"
            )

        lint_res = EvidenceValidator.lint_android_ui_specifications(self.temp_ws)
        self.assertTrue(lint_res["is_android_project"])
        self.assertTrue(lint_res["text_marquee_passed"])
        self.assertTrue(lint_res["passed"])


if __name__ == "__main__":
    unittest.main()
