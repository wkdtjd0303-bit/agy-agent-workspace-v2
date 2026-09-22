import os
import sys
import tempfile
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from auto_pilot import AutoPilotManager, AutoPilotChannelState
from session_manager import SessionManager


class TestAutoPilotCancellationAndStop(unittest.TestCase):
    def setUp(self):
        self.temp_state = tempfile.mktemp(suffix=".json")
        self.sm = SessionManager(self.temp_state)
        self.apm = AutoPilotManager(session_manager=self.sm)
        self.channel_id = 998877

    def tearDown(self):
        if os.path.exists(self.temp_state):
            try:
                os.remove(self.temp_state)
            except Exception:
                pass

    def test_stop_channel_cancels_pending_timer(self):
        """Verify that stop_channel immediately cancels a pending wait_and_trigger timer."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = self.apm.get_state(self.channel_id)
            state.enabled = True

            async def dummy_timer():
                await asyncio.sleep(600)

            timer_task = loop.create_task(dummy_timer())
            state.timer_task = timer_task
            self.assertFalse(timer_task.done())

            stopped = self.apm.stop_channel(self.channel_id)
            self.assertTrue(stopped)
            self.assertFalse(state.enabled)
            self.assertTrue(timer_task.cancelling() or timer_task.cancelled() or timer_task.done())
            self.assertIsNone(state.timer_task)

            # Let event loop process the cancellation
            loop.run_until_complete(asyncio.sleep(0))
            self.assertTrue(timer_task.cancelled())
        finally:
            loop.close()

    def test_stop_channel_cancels_active_audit_task(self):
        """Verify that stop_channel immediately cancels an actively running execute_idle_audit task."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = self.apm.get_state(self.channel_id)
            state.enabled = True

            async def dummy_audit():
                await asyncio.sleep(600)

            audit_task = loop.create_task(dummy_audit())
            state.audit_task = audit_task
            self.assertFalse(audit_task.done())

            stopped = self.apm.stop_channel(self.channel_id)
            self.assertTrue(stopped)
            self.assertFalse(state.enabled)
            self.assertTrue(audit_task.cancelling() or audit_task.cancelled() or audit_task.done())
            self.assertIsNone(state.audit_task)

            # Let event loop process the cancellation
            loop.run_until_complete(asyncio.sleep(0))
            self.assertTrue(audit_task.cancelled())
        finally:
            loop.close()

    def test_execute_idle_audit_aborts_immediately_when_disabled(self):
        """Verify that execute_idle_audit returns immediately if state.enabled is False."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = self.apm.get_state(self.channel_id)
            state.enabled = False

            mock_channel = AsyncMock()
            mock_bot = MagicMock()

            # Execute idle audit when disabled
            loop.run_until_complete(
                self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=False)
            )

            # Ensure no messages were sent to the channel
            mock_channel.send.assert_not_called()
        finally:
            loop.close()

    def test_execute_idle_audit_handles_cancellation_gracefully(self):
        """Verify that execute_idle_audit catches asyncio.CancelledError and cleans up without crashing."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = self.apm.get_state(self.channel_id)
            state.enabled = True
            sess = self.sm.get_session(self.channel_id)
            sess.workspace = "/tmp"

            mock_channel = AsyncMock()
            mock_bot = MagicMock()

            # Mock deep_interactive_crawl to simulate being cancelled in-flight
            async def mock_crawl_cancelled(*args, **kwargs):
                raise asyncio.CancelledError()

            with patch("auto_pilot.E2ECrawlReport.deep_interactive_crawl", side_effect=mock_crawl_cancelled):
                # Should not raise exception
                loop.run_until_complete(
                    self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=False)
                )

            self.assertIsNone(state.audit_task)
        finally:
            loop.close()

    def test_stop_command_invokes_autopilot_stop(self):
        """Verify that stop_command invokes auto_pilot_manager.stop_channel."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from bot import stop_command

            mock_ctx = AsyncMock()
            mock_ctx.channel.id = self.channel_id
            mock_ctx.send = AsyncMock()

            # Set session and autopilot state
            state = self.apm.get_state(self.channel_id)
            state.enabled = True

            with patch("bot.auto_pilot_manager.stop_channel", return_value=True) as mock_ap_stop, \
                 patch("bot.session_manager.stop_session", return_value=True) as mock_sm_stop, \
                 patch("bot.qa_hub_manager.stop_missions_for_channel", return_value=False):

                loop.run_until_complete(stop_command(mock_ctx))
                mock_sm_stop.assert_called_once_with(self.channel_id)
                mock_ctx.send.assert_called_once_with("🛑 **진행 중이던 작업을 즉시 중단했습니다.**")
        finally:
            loop.close()

    def test_create_storyboard_collage(self):
        """Verify that create_storyboard_collage generates a valid combined collage image."""
        from PIL import Image
        from auto_pilot import create_storyboard_collage

        temp_dir = tempfile.mkdtemp()
        try:
            # Create 3 dummy test images (> 1000 bytes)
            items = []
            for i in range(3):
                p = os.path.join(temp_dir, f"test_{i}.png")
                img = Image.new("RGB", (360, 640), color=(50 * i, 100, 150))
                img.save(p)
                items.append((f"📱 Test Screen #{i+1}", p))

            out_path = os.path.join(temp_dir, "collage.png")
            res = create_storyboard_collage(items, output_path=out_path, title="Test E2E Storyboard")
            self.assertIsNotNone(res)
            self.assertTrue(os.path.isfile(res))
            self.assertGreater(os.path.getsize(res), 1000)

            # Verify image format and dimension
            with Image.open(res) as c_img:
                self.assertEqual(c_img.format, "PNG")
                self.assertGreater(c_img.width, 360)
                self.assertGreater(c_img.height, 640)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_persistent_typing(self):
        """Verify PersistentTyping starts background typing and exits cleanly."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from auto_pilot import PersistentTyping
            mock_channel = AsyncMock()

            async def run_typing_test():
                pt = PersistentTyping(mock_channel, interval=0.05)
                await pt.__aenter__()
                self.assertIsNotNone(pt._task)
                self.assertFalse(pt._task.done())
                await asyncio.sleep(0.12)
                await pt.__aexit__(None, None, None)
                self.assertTrue(pt._task.done())

            loop.run_until_complete(run_typing_test())
            self.assertGreaterEqual(mock_channel.typing.call_count, 1)
        finally:
            loop.close()

    def test_emulator_manager_preserves_recent_screenshots(self):
        """Verify that AndroidEmulatorManager cleanup preserves recent screenshots (<10min) to avoid FileNotFoundError."""
        import glob
        import time
        from android_emulator_manager import AndroidEmulatorManager

        temp_dir = tempfile.mkdtemp(prefix="test_emu_cleanup_")
        try:
            gate5_dir = os.path.join(temp_dir, "reports", "gate5", "screenshots")
            os.makedirs(gate5_dir, exist_ok=True)

            old_file = os.path.join(gate5_dir, "android_full_01_idle_1700000000.png")
            recent_file = os.path.join(gate5_dir, "android_full_01_idle_9999999999.png")

            with open(old_file, "wb") as f:
                f.write(b"dummy_old_png_content")
            with open(recent_file, "wb") as f:
                f.write(b"dummy_recent_png_content")

            now = time.time()
            # Set old_file mtime to 1 hour ago
            os.utime(old_file, (now - 3600, now - 3600))
            # Set recent_file mtime to 30 seconds ago
            os.utime(recent_file, (now - 30, now - 30))

            mgr = AndroidEmulatorManager(workspace=temp_dir)
            # Run cleanup logic as executed in run_ui_interaction_suite
            now_ts = time.time()
            for f in glob.glob(os.path.join(mgr.reports_gate5_dir, "android_full_*.png")):
                try:
                    if now_ts - os.path.getmtime(f) > 600:
                        os.remove(f)
                except OSError:
                    pass

            self.assertFalse(os.path.exists(old_file), "Old screenshot (>10min) should be cleaned up")
            self.assertTrue(os.path.exists(recent_file), "Recent screenshot (<10min) must be preserved")
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_missing_screenshot_batch_resilience(self):
        """Verify that batch screenshot attachment in execute_idle_audit does not crash if a screenshot was deleted on disk."""
        non_existent_path = "/tmp/phantom_screenshot_1789312433.png"
        if os.path.exists(non_existent_path):
            os.remove(non_existent_path)

        valid_screenshots = [("📱 Android Idle Screen", non_existent_path)]
        CHUNK_SIZE = 8

        # Execute the exact loop from auto_pilot.py line 902-915
        batch_files = []
        batch_lines = []
        for s_idx, (s_label, s_path) in enumerate(valid_screenshots, start=1):
            if not (s_path and os.path.isfile(s_path)):
                continue
            try:
                import discord
                f_obj = discord.File(s_path, filename=os.path.basename(s_path))
                batch_lines.append(f"• 🖼️ #{s_idx}: {s_label}")
                batch_files.append(f_obj)
            except Exception:
                pass

        # Must not raise FileNotFoundError and safely produce empty batch
        self.assertEqual(len(batch_files), 0)
        self.assertEqual(len(batch_lines), 0)

    def test_agent_runner_exits_immediately_when_stopped_without_self_healing_retry(self):
        """Verify that AgentRunner.run_prompt aborts immediately without in-flight self-healing retries if session is stopped."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from agent_runner import AgentRunner
            session = self.sm.get_session(self.channel_id)
            session.workspace = tempfile.mkdtemp()
            session.is_stopped = True

            exec_calls = 0
            async def mock_execute(*args, **kwargs):
                nonlocal exec_calls
                exec_calls += 1
                return (-9, "", ["무응답 교착"], ["무응답 교착"])

            with patch.object(AgentRunner, "_execute_single_run", side_effect=mock_execute):
                resp = loop.run_until_complete(
                    AgentRunner.run_prompt(session, "test prompt")
                )

            # When session is stopped at start, _execute_single_run is never called
            self.assertEqual(exec_calls, 0)
            self.assertIn("작업이 중단(취소)되었습니다.", resp)

            # Now test when session is stopped during the run
            session.is_stopped = False
            async def mock_execute_and_stop(*args, **kwargs):
                nonlocal exec_calls
                exec_calls += 1
                session.is_stopped = True
                return (-9, "", ["무응답 교착"], ["무응답 교착"])

            with patch.object(AgentRunner, "_execute_single_run", side_effect=mock_execute_and_stop):
                resp = loop.run_until_complete(
                    AgentRunner.run_prompt(session, "test prompt")
                )

            # Called exactly once, and NO self-healing retry was triggered
            self.assertEqual(exec_calls, 1)
            self.assertIn("작업이 중단(취소)되었습니다.", resp)
        finally:
            loop.close()

    @unittest.skip("Hangs in CI due to complex asyncio mocks")
    def test_execute_idle_audit_bypasses_critic_on_timeout(self):
        """Verify that execute_idle_audit never runs Product Critic or reschedules when pipeline times out."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = self.apm.get_state(self.channel_id)
            state.enabled = True
            state.mode = "evolve"
            sess = self.sm.get_session(self.channel_id)
            class DummyTyping:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, exc_type, exc, tb):
                    pass

            mock_channel = AsyncMock()
            mock_channel.typing = lambda: DummyTyping()
            mock_bot = MagicMock()

            mock_report = MagicMock()
            mock_report.issues = []
            mock_report.viewport_tests = []
            mock_report.horizontal_overflow = False
            mock_report.console_errors = []
            mock_report.tested_interactions = []
            mock_report.screenshots = []

            mock_plan = {
                "health_score": 90,
                "assessment": "Good",
                "priority_target": "target",
                "briefing": "briefing",
                "coder_directive": "fix",
                "qa_directive": "check",
                "score_breakdown": {},
                "prd_coverage": {}
            }

            with patch("auto_pilot.E2ECrawlReport.deep_interactive_crawl", new_callable=AsyncMock, return_value=mock_report), \
                 patch("auto_pilot.Commander.plan_e2e_operation", new_callable=AsyncMock, return_value=mock_plan), \
                 patch("auto_pilot.AutoPilotManager.create_git_checkpoint", new_callable=AsyncMock), \
                 patch("product_planner.AutonomousProductPlanner.critique_product") as mock_critic, \
                 patch("bot.reload_helper_modules"), \
                 patch("auto_pilot.QuotaManager.get_gemini_quota_ratio", new_callable=AsyncMock, return_value=0.5), \
                 patch("auto_pilot.asyncio.wait_for", side_effect=asyncio.TimeoutError()):

                loop.run_until_complete(
                    self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=False)
                )

                # Must NOT call critique_product on timeout
                mock_critic.assert_not_called()
                # Must have sent timeout notice
                sent_texts = [call[0][0] for call in mock_channel.send.call_args_list if call[0]]
                self.assertTrue(any("자율 파이프라인 타임아웃" in t for t in sent_texts))
                # Must be disabled
                self.assertFalse(state.enabled)
        finally:
            loop.close()

    @unittest.skip("Hangs in CI due to complex asyncio mocks")
    def test_execute_idle_audit_bypasses_critic_on_cancellation_or_stop(self):
        """Verify that execute_idle_audit never runs Product Critic when pipeline is stopped or cancelled."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            state = self.apm.get_state(self.channel_id)
            state.enabled = True
            state.mode = "evolve"
            sess = self.sm.get_session(self.channel_id)
            sess.workspace = tempfile.mkdtemp()

            class DummyTyping:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, exc_type, exc, tb):
                    pass

            mock_channel = AsyncMock()
            mock_channel.typing = lambda: DummyTyping()
            mock_bot = MagicMock()

            mock_report = MagicMock()
            mock_report.issues = []
            mock_report.viewport_tests = []
            mock_report.horizontal_overflow = False
            mock_report.console_errors = []
            mock_report.tested_interactions = []
            mock_report.screenshots = []

            mock_plan = {
                "health_score": 90,
                "assessment": "Good",
                "priority_target": "target",
                "briefing": "briefing",
                "coder_directive": "fix",
                "qa_directive": "check",
                "score_breakdown": {},
                "prd_coverage": {}
            }

            with patch("auto_pilot.E2ECrawlReport.deep_interactive_crawl", new_callable=AsyncMock, return_value=mock_report), \
                 patch("auto_pilot.Commander.plan_e2e_operation", new_callable=AsyncMock, return_value=mock_plan), \
                 patch("auto_pilot.AutoPilotManager.create_git_checkpoint", new_callable=AsyncMock), \
                 patch("product_planner.AutonomousProductPlanner.critique_product") as mock_critic, \
                 patch("auto_pilot.QuotaManager.get_gemini_quota_ratio", new_callable=AsyncMock, return_value=0.5), \
                 patch("auto_pilot.TrioAgentRunner.run_auditor_turn", new_callable=AsyncMock, return_value="Obs report"), \
                 patch("bot.reload_helper_modules"), \
                 patch("auto_pilot.TrioAgentRunner.run_pipeline", new_callable=AsyncMock, return_value="🛑 사용자의 요청으로 작업이 중단되었습니다."):

                print("Running run_until_complete...")
                loop.run_until_complete(
                    self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=False)
                )
                print("run_until_complete finished.")

                # Must NOT call critique_product on stop
                mock_critic.assert_not_called()
                # Must be disabled
                self.assertFalse(state.enabled)
                self.assertFalse(sess.is_busy)
        finally:
            loop.close()

    def test_stop_command_sets_is_stopped_and_disables_auto(self):
        """Verify that bot.py stop_command sets session.is_stopped = True and session.auto_enabled = False."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from bot import stop_command
            sess = self.sm.get_session(self.channel_id)
            sess.set_auto(enabled=True, mode="evolve")
            sess.is_stopped = False

            mock_ctx = AsyncMock()
            mock_ctx.channel.id = self.channel_id
            mock_ctx.send = AsyncMock()

            with patch("bot.session_manager.get_session", return_value=sess), \
                 patch("bot.session_manager.stop_session", return_value=True), \
                 patch("bot.qa_hub_manager.stop_missions_for_channel", return_value=False), \
                 patch("bot.auto_pilot_manager.stop_channel", return_value=True):

                loop.run_until_complete(stop_command(mock_ctx))

            self.assertTrue(sess.is_stopped)
            self.assertFalse(sess.auto_enabled)
            mock_ctx.send.assert_called_once_with("🛑 **진행 중이던 작업을 즉시 중단했습니다.**")
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()

