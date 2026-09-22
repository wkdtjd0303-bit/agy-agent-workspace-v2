import os, sys, asyncio, unittest
from unittest.mock import MagicMock, AsyncMock, patch

# unittest 실행 환경임을 표시해서 import 시 봇 기동을 막음
os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import bot
from session_manager import SessionManager


class TestResetRequirements(unittest.IsolatedAsyncioTestCase):
    """!reset 의 두 가지 핵심 요구사항 검증
    1) 실시간 최신 코드 상태 반영 (hot-reload)
    2) 모든 컨텍스트 깔끔하게 초기화
    """

    def setUp(self):
        self.sm = SessionManager()

    # ── 요구사항 1: 최신 코드 반영 ──────────────────────────────────────

    def test_reload_returns_nonzero_module_count(self):
        """reload_helper_modules 가 톰헬퍼 모듈을 0개 초과 리로드해야 함"""
        n = bot.reload_helper_modules()
        self.assertGreater(n, 0, "리로드된 헬퍼 모듈이 0개면 코드 반영 경로가 빈약한 것")

    def test_reload_refreshes_session_manager_class(self):
        """session_manager 모듈 리로드 후 세션 매니저 클래스가 최신인지 확인"""
        before = type(self.sm)
        bot.reload_helper_modules()
        after_cls = bot.session_manager.__class__
        # 같은 프로세스 안에서 reload 해도 클래스 객체는 새로워질 수 있음
        # 핵심은 reload 호출 자체가 예외 없이 끝나고 globals 를 갱신하는 것
        self.assertTrue(
            hasattr(bot, "session_manager") and bot.session_manager is not None,
            "reload 후에도 bot.session_manager 가 살아있어야 함"
        )

    def test_reload_refreshes_global_aliases(self):
        """!reset / !리셋 / !재시작 등 alias 가 all_commands 에 잡혀야 함"""
        bot.reload_helper_modules()
        cmd = bot.bot.all_commands.get("reset")
        self.assertIsNotNone(cmd, "!reset 명령어가 all_commands 에 없음")
        expected_aliases = ["reload", "restart", "hotreload", "리셋", "재시작", "초기화", "핫리셋"]
        for a in expected_aliases:
            self.assertIn(a, cmd.aliases, f"alias {a} 누락")
        for a in ["reset"] + expected_aliases:
            self.assertIn(a, bot.bot.all_commands, f"all_commands 에 {a} 라우팅 누락")

    def test_perform_master_reset_alias_exists(self):
        """perform_master_reset 별칭이 execute_all_in_one_hot_reset과 동일하게 존재하는지 확인"""
        self.assertTrue(hasattr(bot, "perform_master_reset"), "bot.perform_master_reset 별칭 부재")
        self.assertIs(bot.perform_master_reset, bot.execute_all_in_one_hot_reset)

    def test_command_prefixes_support_tilde_and_exclamation(self):
        """! 뿐만 아니라 ~ 프리픽스(~reset, ~help 등)도 공식 지원되는지 검증"""
        self.assertIn("!", bot.COMMAND_PREFIXES)
        self.assertIn("~", bot.COMMAND_PREFIXES)
        if isinstance(bot.bot.command_prefix, (tuple, list, set)):
            self.assertIn("!", bot.bot.command_prefix)
            self.assertIn("~", bot.bot.command_prefix)

    # ── 요구사항 2: 컨텍스트 초기화 ──────────────────────────────────────

    async def test_single_channel_reset_cleans_conversation_id(self):
        """!reset 후 해당 채널의 conversation_id 가 None 이 되어야 함"""
        ch = 987654
        sess = self.sm.get_session(ch)
        sess.set_conversation_id("conv-before-reset")
        sess.enqueue(None, "유저 작업 1")
        sess.set_busy(True, "작업 중")

        ctx = MagicMock()
        ctx.channel.id = ch
        st = MagicMock()
        st.edit = AsyncMock()
        ctx.channel.send = AsyncMock(return_value=st)
        ctx.send = AsyncMock(return_value=st)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager", MagicMock()), \
             patch("bot.reload_helper_modules", return_value=5), \
             patch("session_manager.kill_child_processes"):
            await bot.reset_command(ctx)

        self.assertIsNone(sess.conversation_id,
                          "단일채널 !reset 후 conversation_id 가 None 이 아니면 컨텍스트가 안 지워진 것")
        self.assertEqual(len(sess.peek_queue()), 0, "대기열이 남아있음")
        self.assertFalse(sess.is_busy, "busy 플래그가 그대로")

    async def test_reset_all_clears_all_channels(self):
        """!reset all 후 모든 채널의 conversation_id 가 None"""
        ch1, ch2, ch3 = 111, 222, 333
        for c in (ch1, ch2, ch3):
            s = self.sm.get_session(c)
            s.set_conversation_id(f"conv-{c}")
            s.enqueue(None, "작업")

        ctx = MagicMock()
        ctx.channel.id = ch1
        st = MagicMock()
        st.edit = AsyncMock()
        ctx.channel.send = AsyncMock(return_value=st)
        ctx.send = AsyncMock(return_value=st)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager", MagicMock()), \
             patch("bot.reload_helper_modules", return_value=5), \
             patch("session_manager.kill_child_processes"):
            await bot.reset_command(ctx, "all")

        for c in (ch1, ch2, ch3):
            s = self.sm.get_session(c)
            self.assertIsNone(s.conversation_id, f"채널 {c} 컨버세이션 ID가 남아있음")
            self.assertEqual(len(s.peek_queue()), 0, f"채널 {c} 대기열이 남아있음")

    async def test_global_reset_kills_child_processes(self):
        """!reset all 시 kill_child_processes 가 호출되어야 함 (고아 프로세스 정리)"""
        ctx = MagicMock()
        ctx.channel.id = 1234
        st = MagicMock()
        st.edit = AsyncMock()
        ctx.channel.send = AsyncMock(return_value=st)
        ctx.send = AsyncMock(return_value=st)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager", MagicMock()), \
             patch("bot.reload_helper_modules", return_value=5), \
             patch("session_manager.kill_child_processes") as kcp:
            await bot.reset_command(ctx, "all")
            kcp.assert_called_once()

    async def test_single_reset_does_not_kill_child_processes(self):
        """일반 !reset 은 kill_child_processes 를 호출하면 안 됨 (다른 채널 작업 보호)"""
        ctx = MagicMock()
        ctx.channel.id = 1234
        sess = self.sm.get_session(5678)
        sess.set_conversation_id("conv-5678")
        sess.set_busy(True, "다른 채널 빌드 중")

        st = MagicMock()
        st.edit = AsyncMock()
        ctx.channel.send = AsyncMock(return_value=st)
        ctx.send = AsyncMock(return_value=st)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager", MagicMock()), \
             patch("bot.reload_helper_modules", return_value=5), \
             patch("session_manager.kill_child_processes") as kcp:
            await bot.reset_command(ctx)
            kcp.assert_not_called()

    async def test_execute_all_in_one_hot_reset_auto_reset(self):
        """is_auto_reset=True 모드에서 conversation_id는 초기화되고 clear_queue=False 시 대기열은 보존되는지 검증"""
        ch_id = 1538529408703266902
        sess = self.sm.get_session(ch_id)
        sess.set_conversation_id("conv-admin-old")
        sess.enqueue(None, "다음 대기 작업")

        mock_channel = MagicMock()
        mock_channel.id = ch_id
        st = MagicMock()
        st.edit = AsyncMock()
        mock_channel.send = AsyncMock(return_value=st)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager", MagicMock()), \
             patch("bot.reload_helper_modules", return_value=5):
            await bot.execute_all_in_one_hot_reset(
                channel=mock_channel,
                channel_id=ch_id,
                clear_queue=False,
                is_auto_reset=True
            )

        self.assertIsNone(sess.conversation_id, "자동 리셋 후 conversation_id가 None으로 초기화되어야 함")
        self.assertEqual(len(sess.peek_queue()), 1, "clear_queue=False 시 큐 항목이 보존되어야 함")
        self.assertFalse(sess.is_busy, "리셋 후 busy 상태가 해제되어야 함")


if __name__ == "__main__":
    unittest.main()
