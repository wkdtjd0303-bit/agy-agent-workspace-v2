# -*- coding: utf-8 -*-
"""
!reset 회귀 테스트 — "초기화" 와 "코드 반영" 두 축의 실버그를 고정한다.

배경(사용자 보고): "!reset 기능도 제대로 초기화 및 코드반영이라는 동작이 안 되는 경우가 많다"

발견 결함:
  1) [초기화] reset_conversation() 이 대화 ID 만 비우고, 잔존한 목표/보류 컨텍스트
     (root_user_goal, mode3_pending_prompt, wait_for_green, commander_macro_history)를
     남겨두어 !reset 후에도 이전 목표가 되살아났다.
  2) [코드 반영] hot_reload_bridge.sync_in_memory_state() 가 on_message/on_ready/
     on_guild_channel_update 3개만 하드코딩 재바인딩하여 on_command_error/on_error 변경이
     반영되지 않았고, commander_bot.on_ready 를 main.on_ready 로 잘못 덮어썼다.
"""
import os
import sys
import unittest
from unittest.mock import patch

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from session_manager import ChannelSession, SessionManager  # noqa: E402
import hot_reload_bridge  # noqa: E402


class TestHardResetContext(unittest.TestCase):
    """!reset 은 대화 ID뿐 아니라 목표/보류 컨텍스트까지 비워야 한다."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="agy_reset_ctx_")
        self.state_file = os.path.join(self.tmp, "channel_sessions.json")
        self.sm = SessionManager(state_file=self.state_file)

    def _seed(self, ch_id):
        sess = self.sm.get_session(ch_id)
        sess.conversation_id = "c-coder"
        sess.qa_conversation_id = "c-qa1"
        sess.auditor_conversation_id = "c-auditor"
        sess.engine_conversations = {"agy": "c-agy"}
        sess.root_user_goal = "옛 프로젝트 목표"
        sess.mode3_pending_prompt = "보류된 차기 마일스톤"
        sess.wait_for_green = True
        sess.commander_macro_history = [{"cycle": 1, "milestone": "옛 마일스톤"}]
        sess.is_busy = True
        return sess

    def test_reset_channel_session_clears_goal_and_pending_context(self):
        ch = 7001
        sess = self._seed(ch)
        with patch("session_manager.kill_child_processes"):
            res = self.sm.reset_channel_session(ch, clear_queue=True)
        self.assertIsNotNone(res)
        self.assertIsNone(sess.conversation_id)
        self.assertIsNone(sess.root_user_goal, "root_user_goal 이 남으면 !reset 후 옛 목표가 되살아난다")
        self.assertIsNone(sess.mode3_pending_prompt, "보류 프롬프트가 남으면 !reset 후 차기 마일스톤이 자동 재개된다")
        self.assertFalse(sess.wait_for_green, "그린 대기 플래그가 남으면 안 된다")
        self.assertEqual(sess.commander_macro_history, [])
        self.assertFalse(sess.is_busy)

    def test_reset_all_conversations_clears_goal_and_pending_context(self):
        sess1 = self._seed(7101)
        sess2 = self._seed(7102)
        with patch("session_manager.kill_child_processes"):
            self.sm.reset_all_conversations(clear_queues=True)
        for sess in (sess1, sess2):
            self.assertIsNone(sess.conversation_id)
            self.assertIsNone(sess.root_user_goal)
            self.assertIsNone(sess.mode3_pending_prompt)
            self.assertFalse(sess.wait_for_green)
            self.assertEqual(sess.commander_macro_history, [])

    def test_cycle_reset_preserves_root_goal(self):
        """마일스톤 사이클 전환(reset_cycle_workers)은 지속 목표/매크로 메모리를 보존해야 한다."""
        sess = ChannelSession(channel_id=7201, workspace=self.tmp)
        sess.root_user_goal = "지속 프로젝트 목표"
        sess.commander_macro_history = [{"cycle": 1, "milestone": "m1"}]
        sess.conversation_id = "c-coder"
        sess.reset_cycle_workers()
        self.assertIsNone(sess.conversation_id)
        self.assertEqual(sess.root_user_goal, "지속 프로젝트 목표",
                         "사이클 전환에서 root_user_goal 을 지우면 Mode 3 가 목표를 상실한다")
        self.assertEqual(sess.commander_macro_history, [{"cycle": 1, "milestone": "m1"}])


class TestEventCodeReflection(unittest.TestCase):
    """sync_in_memory_state 는 모든 discord 이벤트 핸들러를 최신 함수로 재바인딩해야 한다."""

    @staticmethod
    def _mock_main_class():
        # 이름이 bot.py 최상위 함수와 일치하는 가짜 핸들러들
        async def on_message(message):
            return "old"

        async def on_command_error(ctx, error):
            return "old"

        async def on_error(event_method, *args, **kwargs):
            return "old"

        async def on_commander_ready():
            return "old"

        class MockClient:
            def __init__(self):
                self.all_commands = {}

            def is_ready(self):
                return False

        class MockMain:
            def __init__(self):
                self.bot = MockClient()
                self.commander_bot = MockClient()
                # 이벤트 속성은 인스턴스에 바인딩된 상태를 모사
                self.bot.on_message = on_message
                self.bot.on_command_error = on_command_error
                self.bot.on_error = on_error
                self.commander_bot.on_ready = on_commander_ready

        return MockMain, on_message, on_command_error, on_error, on_commander_ready

    def setUp(self):
        self._orig_main = sys.modules.get("__main__")

    def tearDown(self):
        if self._orig_main is not None:
            sys.modules["__main__"] = self._orig_main
        else:
            sys.modules.pop("__main__", None)

    def test_all_event_handlers_rebound_to_latest_source(self):
        MockMain, old_msg, old_cmd_err, old_err, old_cmd_ready = self._mock_main_class()
        mock_main = MockMain()
        sys.modules["__main__"] = mock_main

        success = hot_reload_bridge.sync_in_memory_state()
        self.assertTrue(success)

        # 1) 이전에 누락되던 에러 핸들러가 최신 함수로 재바인딩되어야 한다.
        self.assertIs(mock_main.bot.on_command_error, mock_main.on_command_error)
        self.assertIsNot(mock_main.bot.on_command_error, old_cmd_err)
        self.assertIs(mock_main.bot.on_error, mock_main.on_error)
        self.assertIsNot(mock_main.bot.on_error, old_err)
        # 2) 기존 3종 이벤트도 최신 함수로 재바인딩.
        self.assertIs(mock_main.bot.on_message, mock_main.on_message)
        self.assertIsNot(mock_main.bot.on_message, old_msg)
        # 3) 보조 클라이언트(commander)는 main.on_ready 가 아니라 자기 함수로 재바인딩되어야 한다.
        self.assertIs(mock_main.commander_bot.on_ready, mock_main.on_commander_ready)
        self.assertIsNot(mock_main.commander_bot.on_ready, mock_main.on_ready)


if __name__ == "__main__":
    unittest.main()
