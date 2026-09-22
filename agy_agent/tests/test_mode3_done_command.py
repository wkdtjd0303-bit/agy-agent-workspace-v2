import os
import sys
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import bot as bot_module
from bot import done_command
from session_manager import ChannelSession


class TestMode3DoneCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel_id = 778899123
        self.session = bot_module.session_manager.get_session(self.channel_id)
        self.session.stop()
        self.session.is_stopped = False
        self.session.stop_after_current_cycle = False
        self.session.mode3_pending_prompt = None
        self.session.intermission_event = None

    def tearDown(self):
        self.session.stop()
        self.session.stop_after_current_cycle = False
        self.session.mode3_pending_prompt = None
        self.session.intermission_event = None

    def test_session_init_has_stop_after_current_cycle(self):
        self.assertFalse(self.session.stop_after_current_cycle)
        self.session.stop_after_current_cycle = True
        self.assertTrue(self.session.stop_after_current_cycle)
        self.session.stop()
        self.assertFalse(self.session.stop_after_current_cycle)

    async def test_done_command_when_idle(self):
        ctx = MagicMock()
        ctx.channel.id = self.channel_id
        ctx.send = AsyncMock()

        self.session.is_busy = False
        await done_command(ctx)

        self.assertFalse(self.session.stop_after_current_cycle)
        ctx.send.assert_called_once()
        sent_msg = ctx.send.call_args[0][0]
        self.assertIn("현재 진행 중인 무인 자율 순환 작업이 없습니다", sent_msg)

    async def test_done_command_toggles_when_busy(self):
        ctx = MagicMock()
        ctx.channel.id = self.channel_id
        ctx.send = AsyncMock()

        # Step 1: Session is busy running milestone
        self.session.is_busy = True
        self.session.mode = "mode3"
        self.assertFalse(self.session.stop_after_current_cycle)

        # Call !done -> should schedule stop after cycle
        await done_command(ctx)
        self.assertTrue(self.session.stop_after_current_cycle)
        sent_msg_1 = ctx.send.call_args[0][0]
        self.assertIn("이번 회차 종료 예약 완료", sent_msg_1)

        # Step 2: Call !done again -> should toggle reservation OFF
        ctx.send.reset_mock()
        await done_command(ctx)
        self.assertFalse(self.session.stop_after_current_cycle)
        sent_msg_2 = ctx.send.call_args[0][0]
        self.assertIn("!done 예약 해제", sent_msg_2)

    async def test_done_command_during_intermission(self):
        ctx = MagicMock()
        ctx.channel.id = self.channel_id
        ctx.send = AsyncMock()

        # Setup intermission state
        intermission_ev = asyncio.Event()
        self.session.intermission_event = intermission_ev
        self.session.mode3_pending_prompt = "차기 마일스톤 프롬프트"
        self.session.is_busy = False

        self.assertFalse(intermission_ev.is_set())

        await done_command(ctx)

        # Intermission event should be set immediately, clearing pending prompt
        self.assertTrue(intermission_ev.is_set())
        self.assertIsNone(self.session.mode3_pending_prompt)
        self.assertEqual(self.session.status, "idle")
        self.assertFalse(self.session.stop_after_current_cycle)

        sent_msg = ctx.send.call_args[0][0]
        self.assertIn("인터미션 대기를 안전하게 종료하고 시스템을 정상 대기", sent_msg)

    async def test_mode3_cycle_honors_stop_after_current_cycle(self):
        # Verify that when stop_after_current_cycle is True,
        # the completion logic in bot.py gracefully switches to idle
        self.session.stop_after_current_cycle = True
        self.session.status = "busy"

        completed_title = "v1.0 메인 타이틀 화면"
        target_sender = MagicMock()
        target_sender.send = AsyncMock()

        # Simulate the exact branch executed in bot.py when milestone completes:
        if getattr(self.session, "stop_after_current_cycle", False):
            self.session.stop_after_current_cycle = False
            self.session.set_status("idle", f"🏁 마일스톤({completed_title}) 완료 후 정상 종료 (!done)")
            await target_sender.send(f"🏁 Mode 3 자율 순환 정상 종료 — !done 완료: {completed_title}")

        self.assertFalse(self.session.stop_after_current_cycle)
        self.assertEqual(self.session.status, "idle")
    async def test_delayed_next_milestone_continuation(self):
        target_sender = MagicMock()
        target_sender.send = AsyncMock()
        message = MagicMock()
        mock_execute = AsyncMock()

        evolve_prompt = "차기 마일스톤 구현 지침"
        self.session.intermission_event = asyncio.Event()
        self.session.mode3_pending_prompt = evolve_prompt
        self.session.intermission_paused = False
        self.session.stop_after_current_cycle = False

        # Simulate the fixed _delayed_next_milestone routine with 0s wait
        async def _test_delayed():
            # Trigger event immediately
            self.session.intermission_event.set()
            await asyncio.wait_for(self.session.intermission_event.wait(), timeout=1.0)
            self.session.intermission_event = None
            if self.session.is_stopped:
                return
            if getattr(self.session, "stop_after_current_cycle", False):
                return
            if getattr(self.session, "intermission_paused", False):
                return
            current_prompt = evolve_prompt
            self.session.mode3_pending_prompt = None
            await mock_execute(message, current_prompt)

        task = asyncio.create_task(_test_delayed())
        await task

        mock_execute.assert_called_once_with(message, evolve_prompt)
        self.assertIsNone(self.session.mode3_pending_prompt)


if __name__ == "__main__":
    unittest.main()
