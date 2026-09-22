import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config


class TestModeCommandCleanRejection(unittest.IsolatedAsyncioTestCase):
    async def test_mode3_rejected_without_legacy_phrases(self):
        """
        Verify that passing '3' or '3+' or 'evolve' to !mode does NOT output:
        - '[!mode 3 / !mode 3+ 전면 초기화 완료]'
        - '기존의 Mode 3 및 Mode 3+ 로직이 완전히 정리되었습니다.'
        - '새로운 기획 및 아키텍처 재구축을 위해 대기 상태입니다.'
        - '(채널 모드: evolve)'
        - '모드는 더 이상 제공되지 않습니다.'
        Instead it cleanly treats it as an unsupported mode.
        """
        from bot import mode_command

        for invalid_mode in ("evolve", "quad", "4", "진화", "무한"):
            mock_ctx = MagicMock()
            mock_ctx.send = AsyncMock()
            mock_ctx.channel = MagicMock()
            mock_ctx.channel.id = 99998888
            mock_ctx.channel.category_id = None

            await mode_command(mock_ctx, mode_str=invalid_mode)

            self.assertTrue(mock_ctx.send.called)
            sent_text = mock_ctx.send.call_args[0][0]

            # Critical assertions: Absolute zero tolerance for legacy Mode 3 text
            self.assertIn("지원되지 않는 모드입니다", sent_text)
            self.assertNotIn("전면 초기화 완료", sent_text)
            self.assertNotIn("아키텍처 재구축", sent_text)
            self.assertNotIn("채널 모드: `evolve`", sent_text)
            self.assertNotIn("채널 모드: evolve", sent_text)
            self.assertNotIn("더 이상 제공되지 않습니다", sent_text)
            self.assertNotIn(f"`{config.COMMAND_PREFIX}mode {invalid_mode}` 모드는", sent_text)

    async def test_valid_modes_accepted(self):
        """Verify that legitimate modes (including 3 and 3+ copied from 2 and 2+) continue to function properly."""
        from bot import mode_command, session_manager

        for valid_mode in ("1", "1+", "2", "2+", "3", "3+", "3plus", "auto", "read"):
            mock_ctx = MagicMock()
            mock_ctx.send = AsyncMock()
            mock_ctx.channel = MagicMock()
            mock_ctx.channel.id = 99998888
            mock_ctx.channel.category_id = None

            await mode_command(mock_ctx, mode_str=valid_mode)
            sent_text = mock_ctx.send.call_args[0][0]
            self.assertIn("채널 동작 모드가", sent_text)

        # Specifically verify mode 2 and mode 3 separation
        sess = session_manager.get_session(99998888)
        await mode_command(mock_ctx, mode_str="2")
        self.assertEqual(sess.mode, "pair")

        await mode_command(mock_ctx, mode_str="3")
        self.assertEqual(sess.mode, "mode3")

        # Specifically verify mode 2+ and mode 3+ separation
        await mode_command(mock_ctx, mode_str="2+")
        self.assertEqual(sess.mode, "pair_quad")

        await mode_command(mock_ctx, mode_str="3+")
        self.assertEqual(sess.mode, "mode3_plus")


if __name__ == "__main__":
    unittest.main()
