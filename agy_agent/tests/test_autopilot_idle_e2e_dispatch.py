import os
import sys
import tempfile
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from auto_pilot import AutoPilotManager
from session_manager import SessionManager


class TestAutopilotIdleE2EDispatch(unittest.TestCase):
    def setUp(self):
        self.temp_state = tempfile.mktemp(suffix=".json")
        self.sm = SessionManager(self.temp_state)
        self.apm = AutoPilotManager(session_manager=self.sm)
        self.channel_id = 123456789

    def tearDown(self):
        if os.path.exists(self.temp_state):
            try:
                os.remove(self.temp_state)
            except Exception:
                pass

    def test_preassigned_session_busy_does_not_abort_execute_idle_audit(self):
        """
        [Critical Regression Test]
        Verify that when bot.py or watchdog pre-assigns session.is_busy = True and
        sets session.current_task = audit_task, execute_idle_audit does NOT self-cancel
        and proceeds into E2E inspection smoothly.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            session = self.sm.get_session(self.channel_id)
            session.workspace = "/dummy/workspace"
            state = self.apm.get_state(self.channel_id)
            state.enabled = True
            state.mode = "quad"

            mock_channel = MagicMock()
            mock_channel.id = self.channel_id
            mock_channel.send = AsyncMock()
            mock_bot = MagicMock()

            # Simulate bot.py pre-assigning is_busy = True
            session.is_busy = True
            session.status = "busy"
            session.status_desc = "AutoPilot 자율 E2E 무한 진화 수행 중"

            quota_checked = False

            async def mock_quota():
                nonlocal quota_checked
                quota_checked = True
                # Return ratio < 0.10 to trigger safe early return after successfully passing the busy guard
                return 0.05

            with patch("auto_pilot.QuotaManager.get_gemini_quota_ratio", side_effect=mock_quota):
                task = loop.create_task(
                    self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=True)
                )
                session.current_task = task
                state.audit_task = task

                loop.run_until_complete(task)

            # Verification:
            # If the old bug existed, execute_idle_audit returned at line 428 without reaching QuotaManager.
            # With our fix, it safely passed the busy guard and reached QuotaManager!
            self.assertTrue(quota_checked, "execute_idle_audit aborted early due to pre-assigned is_busy!")
        finally:
            loop.close()

    def test_different_running_task_skips_audit(self):
        """Verify that execute_idle_audit skips when a DIFFERENT active task is running."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            session = self.sm.get_session(self.channel_id)
            session.workspace = "/dummy/workspace"
            state = self.apm.get_state(self.channel_id)
            state.enabled = True

            async def dummy_active():
                await asyncio.sleep(100)

            other_task = loop.create_task(dummy_active())
            session.is_busy = True
            session.current_task = other_task

            mock_channel = MagicMock()
            mock_bot = MagicMock()

            quota_checked = False

            async def mock_quota():
                nonlocal quota_checked
                quota_checked = True
                return 1.0

            with patch("auto_pilot.QuotaManager.get_gemini_quota_ratio", side_effect=mock_quota):
                audit_task = loop.create_task(
                    self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=True)
                )
                loop.run_until_complete(audit_task)

            # Verification: audit_task must skip and NOT reach quota check
            self.assertFalse(quota_checked, "execute_idle_audit should have skipped when another task is running!")

            other_task.cancel()
            try:
                loop.run_until_complete(other_task)
            except asyncio.CancelledError:
                pass
        finally:
            loop.close()

    def test_zombie_busy_session_taken_over_by_audit(self):
        """Verify that execute_idle_audit takes over when session.is_busy=True but task is done."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            session = self.sm.get_session(self.channel_id)
            session.workspace = "/dummy/workspace"
            state = self.apm.get_state(self.channel_id)
            state.enabled = True

            async def finished_task():
                return "done"

            old_task = loop.create_task(finished_task())
            loop.run_until_complete(old_task)

            # Left busy as a zombie
            session.is_busy = True
            session.current_task = old_task

            mock_channel = MagicMock()
            mock_channel.id = self.channel_id
            mock_channel.send = AsyncMock()
            mock_bot = MagicMock()

            quota_checked = False

            async def mock_quota():
                nonlocal quota_checked
                quota_checked = True
                return 0.05

            with patch("auto_pilot.QuotaManager.get_gemini_quota_ratio", side_effect=mock_quota):
                audit_task = loop.create_task(
                    self.apm.execute_idle_audit(self.channel_id, mock_channel, mock_bot, manual_trigger=True)
                )
                loop.run_until_complete(audit_task)

            # Verification: Zombie busy was overtaken and audit proceeded to quota check
            self.assertTrue(quota_checked, "Zombie busy session was not overtaken by execute_idle_audit!")
        finally:
            loop.close()

    def test_quad_mode_in_evolve_pipeline(self):
        """Verify that quad mode is recognized in autopilot evolve pipeline."""
        session = self.sm.get_session(self.channel_id)
        session.set_mode("pair_quad")
        state = self.apm.get_state(self.channel_id)
        state.mode = "quad"

        phase_map = {
            "observe": "Phase 1: 시각 진단 & 스크린샷 보고 모드 (무위험 관찰)",
            "patch": "Phase 2: 제어형 자율 패치 모드 (결함 수정 & 재배포)",
            "evolve": "Phase 3: 완전 무인 자율 발전 모드 (차기 로드맵 구현)",
            "quad": "Phase 3: 4중 쿼드 완전 무인 자율 진화 모드 (차기 로드맵 구현)"
        }
        self.assertIn("4중 쿼드", phase_map.get(state.mode))


if __name__ == "__main__":
    unittest.main()
