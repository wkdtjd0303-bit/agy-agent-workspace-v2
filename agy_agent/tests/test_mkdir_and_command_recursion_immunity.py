#!/usr/bin/env python3
"""
🧪 Test Suite for !mkdir and Command Callback Recursion Immunity
Tests:
1. Immunity against Command.__call__ self-reference recursion loops in discord.ext.commands.
2. HotReloadBridge and reload_helper_modules automatic callback uncorrupting.
3. !mkdir command execution with and without arguments.
4. Direct on_message prefix interception for !mkdir and all aliases.
"""

import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import bot
import hot_reload_bridge
from discord.ext import commands as ext_commands


class TestMkdirAndRecursionImmunity(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_mkdir_rec_")
        import importlib
        importlib.reload(bot)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_corrupted_command_callback_auto_healing(self):
        """Ensure that if any command in bot.all_commands has a self-referential callback, reload heals it."""
        bot.reload_helper_modules()
        cmd = bot.bot.all_commands.get("mkdir")
        self.assertIsNotNone(cmd, "mkdir command must be present in bot.all_commands")

        # Intentionally inject a circular self-referential callback via _callback (simulating the recursion bug)
        cmd._callback = cmd

        # Verify it was corrupted
        self.assertIs(cmd.callback, cmd)

        # Run reload_helper_modules which contains global integrity sanitization
        bot.reload_helper_modules()

        # Verify it was repaired and is no longer referencing itself or a Command
        repaired_cmd = bot.bot.all_commands.get("mkdir")
        self.assertIsNot(repaired_cmd.callback, repaired_cmd, "Command callback must not be itself")
        self.assertFalse(hasattr(repaired_cmd.callback, "invoke"), "Callback must not be a Command object")
        self.assertTrue(callable(repaired_cmd.callback), "Callback must remain a callable function")

    def test_hot_reload_bridge_sync_recursion_immunity(self):
        """Ensure hot_reload_bridge.sync_in_memory_state does not create circular references."""
        bot.reload_helper_modules()
        cmd = bot.bot.all_commands.get("mkdir")
        self.assertIsNotNone(cmd)

        # Deliberately corrupt callback
        cmd._callback = cmd

        # Run hot_reload_bridge state sync
        hot_reload_bridge.sync_in_memory_state()

        repaired_cmd = bot.bot.all_commands.get("mkdir")
        self.assertIsNot(repaired_cmd.callback, repaired_cmd)
        self.assertFalse(hasattr(repaired_cmd.callback, "invoke"))

    async def test_mkdir_command_execution(self):
        """Test invoking mkdir command directly."""
        ctx = MagicMock()
        ctx.channel.id = 8881234
        ctx.author.id = 9991234
        ctx.send = AsyncMock()

        # No arg -> shows control panel embed
        await bot.mkdir_command(ctx, path="")
        ctx.send.assert_called_once()
        self.assertIn("embed", ctx.send.call_args[1])
        self.assertIn("view", ctx.send.call_args[1])

        ctx.send.reset_mock()
        target = os.path.join(self.temp_dir, "new_subfolder")
        # With path -> creates folder and returns confirmation
        await bot.mkdir_command(ctx, path=target)
        self.assertTrue(os.path.isdir(target))
        ctx.send.assert_called_once()
        self.assertIn("새 디렉토리 생성 및 채널 연동 완료", ctx.send.call_args[1]["embed"].title)

    async def test_on_message_direct_mkdir_interception(self):
        """Verify on_message directly intercepts !mkdir and executes mkdir_command without recursion."""
        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 12345
        msg.webhook_id = None
        msg.is_system = MagicMock(return_value=False)
        msg.channel.id = 777999
        msg.content = "!mkdir"

        mock_ctx = MagicMock()
        with patch.object(bot.bot, "get_context", new_callable=AsyncMock, return_value=mock_ctx), \
             patch("bot.reload_helper_modules"), \
             patch("bot.ensure_commander_bot_started"), \
             patch("bot.ensure_image_bot_started"), \
             patch("bot.ensure_qa2_bot_started"), \
             patch("bot.mkdir_command", new_callable=AsyncMock) as mock_mkdir, \
             patch("bot.check_and_apply_finish_project_freeze", new_callable=AsyncMock):
            await bot.on_message(msg)
            mock_mkdir.assert_called_once()


if __name__ == "__main__":
    unittest.main()
