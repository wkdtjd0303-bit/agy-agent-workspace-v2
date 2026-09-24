import os
import sys
import time
import json
import tempfile
import unittest
import asyncio

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from evidence_validator import EvidenceValidator
from commander import Commander
from auto_pilot import AutoPilotManager, E2ECrawlReport
from self_healing import SelfHealingManager
from session_manager import SessionManager, ChannelSession
from thermal_guard import ThermalGuard


class TestEvidenceValidatorResilience(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ev_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_evaluate_prd_empty_workspace(self):
        res = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertFalse(res["has_prd"])
        self.assertEqual(res["total_requirements"], 0)
        self.assertIsNone(res["next_recommended_target"])

    def test_evaluate_prd_invalid_path(self):
        res = EvidenceValidator.evaluate_prd_feature_coverage("/non/existent/path/99999")
        self.assertFalse(res["has_prd"])
        self.assertEqual(res["total_requirements"], 0)

    def test_evaluate_prd_standard_requirements(self):
        prd_content = """# PRD Test
## Core Requirements
* **REQ-001 (User Authentication)**: Users can register, log in, and log out securely.
* **REQ-002 (Payment Gateway)**: Integrate Stripe and PayPal checkout flows.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

        # Create code file satisfying REQ-001 only
        with open(os.path.join(self.test_dir, "auth_service.py"), "w", encoding="utf-8") as f:
            f.write("def register_user(): pass\ndef login(): pass\ndef logout(): pass\n")

        res = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertTrue(res["has_prd"])
        self.assertEqual(res["total_requirements"], 2)
        self.assertEqual(res["completed_count"], 1)
        self.assertEqual(res["pending_count"], 1)
        self.assertEqual(res["completion_ratio"], 0.5)

        # REQ-002 should be the next recommended target
        target = res["next_recommended_target"]
        self.assertIsNotNone(target)
        self.assertEqual(target["type"], "PRD_REQUIREMENT")
        self.assertEqual(target["target_id"], "REQ-002")

    def test_evaluate_prd_all_completed_optimizes(self):
        prd_content = """# PRD
* **REQ-001 (Telemetry Dashboard)**: Collect and stream CPU RAM metrics.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

        with open(os.path.join(self.test_dir, "metrics.py"), "w", encoding="utf-8") as f:
            f.write("def collect_telemetry_metrics(): cpu = 10; ram = 20; pass\n")

        res = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertTrue(res["has_prd"])
        self.assertEqual(res["completion_ratio"], 1.0)
        target = res["next_recommended_target"]
        self.assertEqual(target["type"], "OPTIMIZATION")
        self.assertIn(target["target_id"], ["OPT-01", "OPT-02", "OPT-03"])


class TestCommanderResilience(unittest.TestCase):
    def test_plan_e2e_operation_all_passed(self):
        async def _run():
            plan = await Commander.plan_e2e_operation(
                target_url="",
                mode="evolve",
                viewport_tests=["390px (PASS)", "1280px (PASS)"],
                horizontal_overflow=False,
                broken_images_count=0,
                interactions=["기본 화면 탐색 완료"],
                console_logs=[],
                cycle=1,
                max_cycles=5,
                workspace=WORKSPACE_ROOT
            )
            self.assertIsInstance(plan, dict)
            self.assertTrue(any(opt in plan["priority_target"] for opt in ["OPT-01", "OPT-02", "OPT-03", "REQ-001", "REQ-"]))
            self.assertIn("briefing", plan)
        asyncio.run(_run())

    def test_plan_e2e_operation_overflow_priority(self):
        async def _run():
            plan = await Commander.plan_e2e_operation(
                target_url="",
                mode="evolve",
                viewport_tests=["390px (FAIL: 가로 오버플로우)"],
                horizontal_overflow=True,
                broken_images_count=0,
                interactions=[],
                console_logs=[],
                cycle=1,
                max_cycles=5,
                workspace=WORKSPACE_ROOT
            )
            self.assertTrue(any(k in plan["priority_target"] for k in ["모바일 가로 스크롤", "모바일 가로 오버플로우", "가로 오버플로우", "Overflow"]))
        asyncio.run(_run())


class TestAutoPilotURLFilter(unittest.TestCase):
    def test_extract_urls_filters_github_commits(self):
        sample_text = (
            "Done! Committed to https://github.com/wkdtjd0303-bit/agy_agent/commit/a190ed9 "
            "and deployed to https://my-awesome-app.web.app for testing."
        )
        urls = AutoPilotManager.extract_urls(sample_text)
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], "https://my-awesome-app.web.app")

    def test_extract_urls_filters_social_and_cdn(self):
        sample_text = "See https://twitter.com/test and https://unpkg.com/react and https://app.vercel.app"
        urls = AutoPilotManager.extract_urls(sample_text)
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], "https://app.vercel.app")


class TestSelfHealingFloodControl(unittest.TestCase):
    def test_flood_limiter(self):
        temp_state = tempfile.mktemp(suffix=".json")
        sm = SessionManager(temp_state)
        sh = SelfHealingManager(session_manager=sm)
        channel_id = 123456789

        for _ in range(9):
            sh.record_error(channel_id)
        self.assertFalse(sh.is_flood_limited(channel_id))

        sh.record_error(channel_id)
        self.assertTrue(sh.is_flood_limited(channel_id))

        # Cleanup
        sh.clear_channel_error(channel_id)
        self.assertFalse(sh.is_flood_limited(channel_id))
        if os.path.exists(temp_state):
            os.remove(temp_state)


class TestSessionManagerResilience(unittest.TestCase):
    def test_queue_enqueue_dequeue(self):
        temp_state = tempfile.mktemp(suffix=".json")
        sm = SessionManager(temp_state)
        sess = sm.get_session(987654321)

        self.assertEqual(sess.enqueue(None, "Prompt 1"), 1)
        self.assertEqual(sess.enqueue(None, "Prompt 2"), 2)
        self.assertEqual(len(sess.peek_queue()), 2)

        item = sess.dequeue()
        self.assertEqual(item.prompt, "Prompt 1")
        self.assertEqual(len(sess.peek_queue()), 1)

        sess.clear_queue()
        self.assertEqual(len(sess.peek_queue()), 0)
        if os.path.exists(temp_state):
            os.remove(temp_state)


class TestThermalGuardResilience(unittest.TestCase):
    def test_thermal_tier_thresholds(self):
        tier, temp = ThermalGuard.get_tier(60.0)
        self.assertEqual(tier, "NORMAL")
        self.assertEqual(temp, 60.0)

        tier, temp = ThermalGuard.get_tier(79.0)
        self.assertEqual(tier, "MILD")
        self.assertEqual(temp, 79.0)

        tier, temp = ThermalGuard.get_tier(86.5)
        self.assertEqual(tier, "EMERGENCY")
        self.assertEqual(temp, 86.5)

    def test_get_current_temperature_safe_fallback(self):
        temp = ThermalGuard.get_current_temperature()
        self.assertIsInstance(temp, float)
        self.assertGreaterEqual(temp, 25.0)
        self.assertLessEqual(temp, 115.0)


class TestQuotaManagerResilience(unittest.TestCase):
    def test_exhausted_model_tracking(self):
        from trio_agent_runner import QuotaManager
        test_model = "test-gemini-custom-model"
        self.assertFalse(QuotaManager.is_model_exhausted(test_model))

        QuotaManager.mark_model_exhausted(test_model, duration_sec=10.0)
        self.assertTrue(QuotaManager.is_model_exhausted(test_model))


class TestEvidenceValidatorCorruptData(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ev_corrupt_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_corrupted_backlog_json_does_not_crash(self):
        os.makedirs(os.path.join(self.test_dir, "reports", "deferred"), exist_ok=True)
        backlog_file = os.path.join(self.test_dir, "reports", "deferred", "minor_backlog.json")
        with open(backlog_file, "w", encoding="utf-8") as f:
            f.write("{invalid_json: true, broken")

        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write("# PRD\n* **REQ-001 (Core System)**: Basic system initialization.\n")

        res = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertTrue(res["has_prd"])
        self.assertEqual(len(res["minor_backlog_items"]), 0)


class TestDesignVisionPipeline(unittest.TestCase):
    def test_generate_react_component(self):
        from design_vision_pipeline import DesignVisionPipeline
        tokens = {
            "style_archetype": "Apple Negative Space & Pinterest Bento",
            "palette": {"soft_lavender": "#C4B5FD"}
        }
        code = DesignVisionPipeline.generate_react_component(tokens, component_name="TestHero")
        self.assertIn("export const TestHero: React.FC", code)
        self.assertIn("min-h-[48px]", code)
        self.assertIn("overflow-x-hidden", code)
        self.assertIn("Apple Negative Space", code)


class TestDiscordAttachmentPerformance(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_attach_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_prepare_message_payload_ignores_old_oversized_file(self):
        from discord_utils import prepare_message_payload, extract_candidates
        # Create an old 10MB file in test_dir
        old_file = os.path.join(self.test_dir, "ParkMemo.aab")
        with open(old_file, "wb") as f:
            f.seek(10 * 1024 * 1024 - 1)
            f.write(b"\0")
        
        # Set mtime to 1 hour ago
        old_time = time.time() - 3600
        os.utime(old_file, (old_time, old_time))

        routine_text = "Here is the summary of the latest code refactoring."
        # Call with default arguments (auto_scan_deliverables defaults to False)
        candidates = extract_candidates(routine_text, workspace_root=self.test_dir)
        self.assertEqual(len(candidates), 0, "Default extract_candidates must NOT pick up old static files!")

        chunks, files = prepare_message_payload(routine_text, workspace_root=self.test_dir)
        self.assertEqual(len(files), 0)
        self.assertNotIn("대용량 파일", chunks[0], "Old static oversized files must NOT generate large file notices!")

    def test_default_async_prepare_message_payload_ignores_old_oversized_file(self):
        from discord_utils import async_prepare_message_payload
        old_file = os.path.join(self.test_dir, "ParkMemo.aab")
        with open(old_file, "wb") as f:
            f.seek(10 * 1024 * 1024 - 1)
            f.write(b"\0")
        old_time = time.time() - 3600
        os.utime(old_file, (old_time, old_time))

        routine_text = "Standard response text without release mention."
        async def _run():
            chunks, files = await async_prepare_message_payload(routine_text, workspace_root=self.test_dir)
            self.assertEqual(len(files), 0)
            self.assertNotIn("대용량 파일 Google Drive 다운로드", chunks[0])
        asyncio.run(_run())

    def test_explicitly_mentioned_file_is_recognized_by_default(self):
        from discord_utils import extract_oversized_attachments
        old_file = os.path.join(self.test_dir, "ParkMemo.aab")
        with open(old_file, "wb") as f:
            f.seek(10 * 1024 * 1024 - 1)
            f.write(b"\0")

        # Explicit mention in text
        mention_text = "빌드가 완료되었습니다: [다운로드](ParkMemo.aab)"
        oversized = extract_oversized_attachments(mention_text, workspace_root=self.test_dir)
        self.assertEqual(len(oversized), 1)
        self.assertEqual(os.path.basename(oversized[0][0]), "ParkMemo.aab")


class TestEvidenceValidatorAdvanced(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ev_adv_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_validate_file_checks(self):
        # 1. Non-existent file
        ok, msg = EvidenceValidator.validate_file(os.path.join(self.test_dir, "non_existent.json"))
        self.assertFalse(ok)

        # 2. Min bytes failure
        tiny_file = os.path.join(self.test_dir, "tiny.txt")
        with open(tiny_file, "w") as f:
            f.write("hi")
        ok, msg = EvidenceValidator.validate_file(tiny_file, min_bytes=10)
        self.assertFalse(ok)

        # 3. Valid JSON
        json_file = os.path.join(self.test_dir, "valid.json")
        with open(json_file, "w") as f:
            json.dump({"key": "val"}, f)
        ok, msg = EvidenceValidator.validate_file(json_file, is_json=True)
        self.assertTrue(ok)

    def test_blank_solid_image_detection(self):
        from PIL import Image
        # Create solid white 200x200 image
        white_img_path = os.path.join(self.test_dir, "white_screen.png")
        img = Image.new("RGB", (200, 200), color=(255, 255, 255))
        img.save(white_img_path)

        ok, msg = EvidenceValidator.validate_image_content(white_img_path)
        self.assertFalse(ok, "Solid white screen must be flagged as blank/solid")
        self.assertIn("Blank/Solid color screen detected", msg)

    def test_valid_rich_image_detection(self):
        from PIL import Image
        rich_img_path = os.path.join(self.test_dir, "rich_ui.png")
        width, height = 300, 300
        pixel_data = bytearray()
        for y in range(height):
            for x in range(width):
                r = (x * 255) // width
                g = (y * 255) // height
                b = (x * y) % 256
                pixel_data.extend((r, g, b))
        img = Image.frombytes("RGB", (width, height), bytes(pixel_data))
        img.save(rich_img_path)

        ok, msg = EvidenceValidator.validate_image_content(rich_img_path)
        self.assertTrue(ok, "Rich UI image must pass validation")


class TestAutoPilotBoundaryConditions(unittest.TestCase):
    def test_excluded_domains_filter(self):
        excluded = [
            "https://raw.githubusercontent.com/foo/bar/master/img.png",
            "https://gist.github.com/user/12345",
            "https://bit.ly/sample-link",
            "https://coupang.com/vp/products/123",
            "https://naver.com",
            "https://github.com/wkdtjd0303-bit/agy_agent/commit/a190ed9"
        ]
        for url in excluded:
            self.assertTrue(
                AutoPilotManager.is_third_party_url(url),
                f"{url} should be recognized as an excluded third-party domain"
            )

    def test_internal_and_project_urls_allowed(self):
        allowed = [
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "https://my-ag-project.web.app",
            "https://my-ag-project.firebaseapp.com",
            "https://my-service.vercel.app",
            "https://my-frontend.netlify.app",
            "https://my-app.pages.dev",
            "https://my-backend.render.com",
            "https://my-server.railway.app"
        ]
        for url in allowed:
            self.assertFalse(
                AutoPilotManager.is_third_party_url(url),
                f"{url} should be allowed for AutoPilot crawling"
            )

    def test_autopilot_max_cycles_default_and_sync(self):
        temp_state = tempfile.mktemp(suffix=".json")
        sm = SessionManager(temp_state)
        apm = AutoPilotManager(session_manager=sm)
        state = apm.get_state(12345)
        self.assertEqual(state.max_cycles, 20)
        self.assertEqual(state.mode, "evolve")

        state.max_cycles = 25
        state.mode = "evolve"
        apm.sync_to_session(12345)

        sess = sm.get_session(12345)
        self.assertEqual(sess.auto_max_cycles, 25)
        self.assertEqual(sess.auto_mode, "evolve")


class TestQAHubEvolveRoundHandling(unittest.TestCase):
    def test_qa_mission_packet_evolve_max_rounds(self):
        from qa_hub import QAMissionPacket
        packet = QAMissionPacket(
            request_id="QA_TEST_001",
            source_channel_id=12345,
            source_channel_name="test_channel",
            workspace=WORKSPACE_ROOT,
            target_url_or_apk="https://test.web.app",
            mission="E2E 진화 테스트",
            current_round=1,
            max_rounds=20,
            mode="evolve"
        )
        self.assertEqual(packet.max_rounds, 20)
        self.assertEqual(packet.mode, "evolve")
class TestAuditorMentionOnPass(unittest.TestCase):
    def test_auditor_mention_user_id_configured(self):
        from config import AUDITOR_MENTION_USER_ID
        self.assertEqual(AUDITOR_MENTION_USER_ID, "1257525029609017367")

    def test_mention_injection_format(self):
        from config import AUDITOR_MENTION_USER_ID
        raw_output = "[STATUS: AUDIT_PASSED]\n\n모든 테스트가 완료되었습니다."
        user_mention = f"<@{AUDITOR_MENTION_USER_ID}>"
        expected = raw_output.replace("[STATUS: AUDIT_PASSED]", f"{user_mention} [STATUS: AUDIT_PASSED]", 1)
        self.assertTrue(expected.startswith("<@1257525029609017367> [STATUS: AUDIT_PASSED]"))


class TestAuditorSelfHealingContinuation(unittest.TestCase):
    def test_auditor_stop_triggers_self_healing_before_max_rounds(self):
        """Verifies that Auditor value objection triggers self-healing retry instead of hard abort."""
        from autonomy_guard import CollaborationIntelligence

        diff_bloated = {
            "has_changes": True,
            "changed_files": [f"component_{i}.ts" for i in range(12)],
            "raw_diff": "+ diff"
        }
        val_action, val_reason = CollaborationIntelligence.evaluate_auditor_value_alignment(
            prompt="간단 오타 수정",
            diff_info=diff_bloated,
            is_cosmetic_only=True
        )
        self.assertEqual(val_action, "STOP_LOW_VALUE")

        # In round 1 of 3, is_value_stopped must route to feedback_for_coder, not early abort
        round_num = 1
        max_rounds = 3
        is_value_stopped = (val_action == "STOP_LOW_VALUE")
        self.assertTrue(is_value_stopped)
        self.assertTrue(round_num < max_rounds, "Should allow self-healing retry when round_num < max_rounds")


class TestEarlyTerminationAndMockCompletionDefense(unittest.TestCase):
    def test_tool_safety_header_and_footer_has_pattern_mandate(self):
        """Verify TOOL_SAFETY_HEADER and FOOTER mandate Pattern for find_by_name to prevent schema crashes."""
        from agent_runner import TOOL_SAFETY_HEADER, TOOL_SAFETY_FOOTER
        self.assertIn("find_by_name", TOOL_SAFETY_HEADER)
        self.assertIn("Pattern", TOOL_SAFETY_HEADER)
        self.assertIn("missing property 'Pattern'", TOOL_SAFETY_HEADER)
        self.assertIn("find_by_name", TOOL_SAFETY_FOOTER)
        self.assertIn("Pattern", TOOL_SAFETY_FOOTER)

    def test_is_system_error_catches_early_termination(self):
        """Verify TrioAgentRunner.is_system_error recognizes early termination and agent errors."""
        from trio_agent_runner import TrioAgentRunner
        self.assertTrue(TrioAgentRunner.is_system_error("⚠️ **[에이전트 조기 종료 오류]** 작업이 완결되지 못했습니다."))
        self.assertTrue(TrioAgentRunner.is_system_error("⚠️ **[에이전트 조기 종료 감지]** Coder 에이전트가 조기 종료했습니다."))
        self.assertTrue(TrioAgentRunner.is_system_error("⚠️ **[비-AGY 엔진 실행 오류]** 유효한 응답이 없습니다."))
        self.assertFalse(TrioAgentRunner.is_system_error("🎉 3중 에이전트 협업 완료"))
        self.assertFalse(TrioAgentRunner.is_system_error("[STATUS: APPROVED] 정상 승인"))

    def test_no_mock_completion_fallback_on_empty_response(self):
        """Verify that AgentRunner does NOT return '작업을 완료했습니다.' when response is empty."""
        from agent_runner import AgentRunner
        from session_manager import ChannelSession
        session = ChannelSession(channel_id=99999, workspace=WORKSPACE_ROOT)

        # Mock _execute_single_run returning returncode 0 with empty accumulated_response and tool error
        async def run_test():
            original_exec = AgentRunner._execute_single_run
            try:
                # Return empty response with missing property Pattern error
                async def mock_exec(*args, **kwargs):
                    return (0, "", ["TOOL_ERROR: missing property 'Pattern'"], ["TOOL_ERROR: missing property 'Pattern'"])
                AgentRunner._execute_single_run = mock_exec
                res = await AgentRunner.run_prompt(session, "테스트 요청")
                self.assertNotIn("작업을 완료했습니다.", res)
                self.assertIn("에이전트 조기 종료 오류", res)
            finally:
                AgentRunner._execute_single_run = original_exec

        asyncio.run(run_test())

    def test_quote_reference_bar_and_reaction_toolbar(self):
        """Verify Quote/Reference Bar formatting and Reaction Toolbar emoji defaults."""
        from discord_utils import format_quote_reference_bar, DEFAULT_TOOLBAR_EMOJIS, attach_reaction_toolbar
        
        # Test Quote/Reference Bar formatting
        quote_bar = format_quote_reference_bar("버그 수정 요청합니다.", author="Sjang")
        self.assertIn("> 💬", quote_bar)
        self.assertIn("@Sjang", quote_bar)
        self.assertIn("버그 수정 요청합니다.", quote_bar)

        # Test empty input handling
        self.assertEqual(format_quote_reference_bar(""), "")
        self.assertEqual(format_quote_reference_bar(None), "")

        # Test default toolbar emojis
        self.assertEqual(DEFAULT_TOOLBAR_EMOJIS, ["👍", "👎", "🔄", "🛑"])

        # Test attach_reaction_toolbar on mock message
        class MockMessage:
            def __init__(self):
                self.reactions_added = []
            async def add_reaction(self, emoji):
                self.reactions_added.append(emoji)

        mock_msg = MockMessage()
        asyncio.run(attach_reaction_toolbar(mock_msg))
        self.assertEqual(mock_msg.reactions_added, ["👍", "👎", "🔄", "🛑"])

    def test_validate_requirement_coverage_vision_filter_and_inquiry(self):
        """Verify that Gemini Vision breakdowns and inquiry queries are not falsely parsed as coding requirements."""
        from autonomy_guard import CollaborationIntelligence

        # Prompt containing user inquiry + OCR Gemini Vision breakdown of screenshot
        prompt_with_vision = (
            "한번씩 이렇게.. 분명 내가 꽤 어려운 요청사항을 넣었거든? 근데 제대로 읽지도 않고 작업을 완료했다고 coder가 답변을 주는 경우가 종종 생겨.\n"
            "1. 왜 이런 일이 생기는가?\n"
            "2. 개선작업\n\n"
            "[🔍 Gemini Vision 정밀 시각 분석 리포트]:\n"
            "* 계층 구조 (Top-to-Bottom):\n"
            "  1. User Prompt (지시층): 단락 텍스트\n"
            "  2. Quote/Reference Bar: 세로선과 함께 축약된 이전 지시 컨텍스트\n"
            "  3. Agent Avatar & Header: 아바타, 봇 이름\n"
            "  4. Status Callout / Action Logs: 인라인 상태 블록\n"
            "  5. Reaction & Toolbar (우측 플로팅): 유저 피드백 이모지 카운터\n"
            "  6. Final Response: 실제 텍스트 출력부"
        )
        coder_output = "원인 분석 및 개선작업을 완료했습니다. 도구 스키마 에러를 해결하고 자가 치유를 보강했습니다."

        passed, missing, details = CollaborationIntelligence.validate_requirement_coverage(prompt_with_vision, coder_output)
        self.assertTrue(passed)
        self.assertEqual(missing, [])
        self.assertNotIn("Quote/Reference Bar", missing)
        self.assertNotIn("Reaction & Toolbar", missing)

    def test_validate_requirement_coverage_ignores_system_safety_headers(self):
        """Verify that system safety headers (TOOL USAGE RULES, REQ-001) are not treated as user requirements."""
        from autonomy_guard import CollaborationIntelligence

        raw_prompt = (
            "[CRITICAL SYSTEM & TOOL SAFETY INSTRUCTIONS]\n"
            "1. TOOL USAGE RULES (CRITICAL - AVOIDS SYSTEM CRASH):\n"
            "   - find_by_name: Pattern required\n"
            "2. SCRIPT & PROCESS EXECUTION SAFETY (AVOIDS DEADLOCKS/HANGS):\n"
            "   - timeout required\n"
            "3. SERVICE SAFETY:\n"
            "   - no systemctl\n"
            "4. LINK FORMATTING FOR DISCORD:\n"
            "   - clean urls\n"
            "5. RUN_COMMAND & BUILD SAFETY (AVOIDS BACKGROUND TASK DEADLOCKS):\n"
            "   - no heavy builds\n"
            "6. UNIVERSAL VALUE CREATION & ANTI-MOCKING MANDATE:\n"
            "   - direct answer first\n"
            "7. DEFAULT TIMESTAMPED BUILD ARTIFACT MANDATE:\n"
            "   - timestamped builds\n"
            "8. COMMANDER-APPROVED VISUAL ASSET TAXONOMY & MODULAR GENERATION RULE:\n"
            "   - approval required\n\n"
            "[🎯 WORKSPACE FOCUS & CONSTRAINTS]\n"
            "• 🎯 **집중 과제**: REQ-001 (Cross-Platform E2E Autonomous Testing)**\n"
            "• 🏛️ **활성 프로파일**: ⚙️ Profile: CLI, Daemon & Automation Agent (cli_agent_rules)\n\n"
            "사용자가 다음과 같은 작업을 요청했습니다:\n"
            "────────────────────────────────────────\n"
            "새로운 버그 방어 로직을 작성해줘\n"
            "────────────────────────────────────────\n"
        )
        coder_output = "새로운 버그 방어 로직을 작성하고 검증했습니다."

        passed, missing, details = CollaborationIntelligence.validate_requirement_coverage(raw_prompt, coder_output)
        self.assertTrue(passed)
        self.assertEqual(missing, [])
        self.assertNotIn("TOOL USAGE RULES (CRITICAL - AVOIDS SYSTEM CRASH)", missing)
        self.assertNotIn("REQ-001 (Cross-Platform E2E Autonomous Testing)", missing)

    def test_is_qa_approved_top_approval_not_vetoed_by_in_text_rejected_mentions(self):
        """Verify that line 1 [STATUS: APPROVED] is not vetoed by in-text mentions of REJECTED."""
        from trio_agent_runner import TrioAgentRunner

        # Detailed review with [STATUS: APPROVED] on line 1 and mentions of REJECTED in discussion
        review_text = (
            "[STATUS: APPROVED]\n\n"
            "사용자님께서 지시하신 에이전트 조기 종료 방어 체계 코드를 정밀 검증했습니다.\n"
            "검토 결과 결함이 발견되지 않아 최종 승인합니다.\n\n"
            "--- 검토 상세 ---\n"
            "1. 3중 협업 Fail-Fast 방어막 추가:\n"
            "   - Coder가 단문으로 대답하거나 조기 종료할 경우 즉시 중단(Reject)시키는 가드가 설치되었습니다.\n"
            "2. 이전 라운드 [STATUS: REJECTED] 피드백 루프 해소 확인 완료.\n"
            "3. 회귀 테스트 3개 모두 통과.\n\n"
            "[최종 QA 코멘트]\n"
            "모든 예외 처리가 완벽히 복구되었습니다. 승인합니다."
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(review_text))

    def test_is_auditor_approved_top_passed_not_vetoed_by_in_text_failed_mentions(self):
        """Verify that line 1 [STATUS: AUDIT_PASSED] is not vetoed by in-text mentions of AUDIT_FAILED."""
        from trio_agent_runner import TrioAgentRunner

        review_text = (
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
            "사용자 가치 분석 리포트:\n"
            "1. 이전 라운드의 [STATUS: AUDIT_FAILED] 지적 사항이 완벽히 해결되었습니다.\n"
            "2. 기술 부채 없이 실질적인 방어 코드가 프로덕션에 구현되었습니다.\n"
            "3. 모든 E2E 검증 통과.\n"
        )
        self.assertTrue(TrioAgentRunner.is_auditor_approved(review_text))

    def test_in_flight_self_healing_retry_on_trivial_completion(self):
        """Verify that TrioAgentRunner attempts in-flight self-recovery when Coder returns a trivial response."""
        from trio_agent_runner import TrioAgentRunner
        from session_manager import ChannelSession
        from unittest.mock import patch, AsyncMock, MagicMock

        session = ChannelSession(channel_id=99998, workspace=WORKSPACE_ROOT, mode="mode3")
        main_ch = MagicMock()
        main_ch.send = AsyncMock()
        main_ch.typing = MagicMock()

        # Mock typing context manager
        class DummyTyping:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        main_ch.typing.return_value = DummyTyping()

        # Call sequence: 1st call returns "완료했습니다.", 2nd call (recovery) returns substantive code
        coder_calls = []
        async def mock_coder(session, prompt, on_tool_update=None):
            coder_calls.append(prompt)
            if len(coder_calls) == 1:
                return "완료했습니다."
            return "## 구현 완료\nPageManagerDialog의 드래그 앤 드롭 및 엣지 케이스 로직을 추가 작성했습니다."

        async def run_test():
            with patch.object(TrioAgentRunner, "run_coder_turn", side_effect=mock_coder), \
                 patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock) as mock_send, \
                 patch.object(TrioAgentRunner, "run_qa_turn", new_callable=AsyncMock) as mock_qa, \
                 patch.object(TrioAgentRunner, "run_auditor_turn", new_callable=AsyncMock) as mock_aud, \
                 patch("trio_agent_runner.EvidenceValidator.inspect_production_diff", return_value={"is_zero_production_diff": False, "prod_changed_files": ["PageManagerDialog.kt"], "non_prod_files": []}), \
                 patch("trio_agent_runner.EvidenceValidator.verify_anti_mocking_and_authenticity", return_value=(True, [], {})), \
                 patch("trio_agent_runner.QualityStateManager.evaluate_and_update", return_value={"regression": {"has_regression": False}, "overall_score": 100.0, "delta": 0.0}), \
                 patch("trio_agent_runner.EvidenceValidator.check_visual_asset_presence", return_value={"has_assets": True}), \
                 patch("vision_attachment_analyzer.VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa", new_callable=AsyncMock, return_value={"has_screenshots": True}):
                
                mock_qa.return_value = (
                    "[STATUS: APPROVED]\n\n"
                    "QA 검증을 완료했습니다. Coder가 수정한 소스코드 `PageManagerDialog.kt` 및 `TwoTierCacheManager.kt`를 "
                    "상세히 분석한 결과, 드래그 앤 드롭 재정렬 및 90도 회전 시의 상태 불변성이 올바르게 구현되었음을 확인했습니다.\n\n"
                    "```kotlin\nfun testDragAndDrop() {\n    val result = pageManager.reorder(0, 2)\n    assertTrue(result.isSuccess)\n}\n```\n"
                    "라인 45부터 라인 120까지의 메모리 누수 방어 로직과 60fps 렌더링 인터랙션이 충족되어 프로덕션 승인합니다."
                )
                mock_aud.return_value = "[STATUS: AUDIT_PASSED]\n[VALUE: HIGH]\n최종 감사 승인 완료"

                res = await TrioAgentRunner.run_pipeline(
                    session=session,
                    prompt="마일스톤 구현",
                    main_channel=main_ch
                )

                # Should have retried Coder turn in-flight
                self.assertEqual(len(coder_calls), 2)
                self.assertIn("사령관 긴급 재지시", coder_calls[1])
                self.assertFalse(TrioAgentRunner.is_system_error(res))
                self.assertIn("최종 승인", res)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()


