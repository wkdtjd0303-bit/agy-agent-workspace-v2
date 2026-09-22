"""
Unit tests for command error handling, fuzzy command suggestions, command aliases,
and SelfHealing command-error guards.
"""

import os
import sys
import unittest
import difflib
from unittest.mock import MagicMock, AsyncMock, patch
import discord
from discord.ext import commands

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import bot
import self_healing
from session_manager import SessionManager


class TestCommandErrorAndSuggestions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sm = SessionManager()
        self.shm = self_healing.SelfHealingManager(session_manager=self.sm)

    def test_fuzzy_command_suggestion(self):
        all_cmds = [
            "help", "plan", "setup", "clear", "qa", "qasetup", "auto", "stop", "mode", "read",
            "single", "pair", "status", "quota", "export", "zip", "apk", "drive", "colab", "sim",
            "android_test", "win_test", "selfhealing", "temp", "queue", "clear-queue", "pwd", "cd"
        ]
        
        # Test typo "!sto" -> suggests "stop"
        matches = difflib.get_close_matches("sto", all_cmds, n=3, cutoff=0.5)
        self.assertIn("stop", matches)

        # Test typo "!stauts" -> suggests "status"
        matches = difflib.get_close_matches("stauts", all_cmds, n=3, cutoff=0.5)
        self.assertIn("status", matches)

        # Test typo "!hlep" -> suggests "help"
        matches = difflib.get_close_matches("hlep", all_cmds, n=3, cutoff=0.5)
        self.assertIn("help", matches)

    def test_stop_command_aliases(self):
        # Reload helper modules to ensure all aliases are populated
        bot.reload_helper_modules()
        
        stop_cmd = bot.bot.get_command("stop")
        self.assertIsNotNone(stop_cmd)
        
        expected_aliases = ["sto", "halt", "cancel", "kill", "abort", "중지", "정지", "취소", "그만", "멈춰", "중단"]
        for alias in expected_aliases:
            self.assertIn(alias, stop_cmd.aliases)
            # Ensure it is also indexed in bot.all_commands
            self.assertIn(alias, bot.bot.all_commands)

    def test_other_command_aliases(self):
        bot.reload_helper_modules()
        
        # Auto aliases
        auto_cmd = bot.bot.get_command("auto")
        self.assertIsNotNone(auto_cmd)
        for alias in ["자율", "오토", "autopilot", "autocode"]:
            self.assertIn(alias, auto_cmd.aliases)


        # Pwd aliases
        pwd_cmd = bot.bot.get_command("pwd")
        self.assertIsNotNone(pwd_cmd)
        for alias in ["경로", "위치", "cwd", "dir"]:
            self.assertIn(alias, pwd_cmd.aliases)

    def test_golden5_command_and_embed(self):
        bot.reload_helper_modules()
        import help_manager
        
        # Test golden5 command registration
        cmd = bot.bot.get_command("golden5")
        self.assertIsNotNone(cmd)
        for alias in ["핵심", "5대명령어", "essential", "필수", "골든5"]:
            self.assertIn(alias, cmd.aliases)
            
        # Test get_golden5_embed content
        embed = help_manager.HelpManager.get_golden5_embed()
        self.assertIn("외워야 할 핵심 필수 명령어 5선", embed.title)
        self.assertGreaterEqual(len(embed.fields), 5)
        
        # Test normalize_page for golden5
        self.assertEqual(help_manager.HelpInteractiveView.normalize_page("golden5"), "golden5")
        self.assertEqual(help_manager.HelpInteractiveView.normalize_page("핵심"), "golden5")
        self.assertEqual(help_manager.HelpInteractiveView.normalize_page("essential"), "golden5")

    async def test_on_command_error_command_not_found(self):
        mock_ctx = MagicMock(spec=commands.Context)
        mock_ctx.command = None
        mock_ctx.invoked_with = "unknown_cmd_xyz"
        mock_ctx.message = MagicMock()
        mock_ctx.message.content = "!unknown_cmd_xyz"
        mock_ctx.channel = AsyncMock()

        mock_shm = MagicMock()
        mock_shm.enabled = True
        mock_shm.handle_error = AsyncMock()

        with patch("bot.self_healing_manager", mock_shm), \
             patch("bot.safe_send_discord_message", new_callable=AsyncMock) as mock_send:
            
            error = commands.CommandNotFound('Command "unknown_cmd_xyz" is not found')
            await bot.on_command_error(mock_ctx, error)

            # Assert safe_send_discord_message was called with a user-friendly message
            mock_send.assert_called_once()
            sent_msg = mock_send.call_args[0][1]
            self.assertIn("알 수 없는 명령어입니다", sent_msg)
            
            # Assert self_healing was NEVER invoked for user typo/CommandNotFound
            mock_shm.handle_error.assert_not_called()

    async def test_on_command_error_user_input_errors(self):
        mock_cmd = MagicMock(spec=["name"])
        mock_cmd.name = "cd"

        mock_ctx = MagicMock(spec=commands.Context)
        mock_ctx.command = mock_cmd
        mock_ctx.channel = AsyncMock()

        mock_shm = MagicMock()
        mock_shm.enabled = True
        mock_shm.handle_error = AsyncMock()

        with patch("bot.self_healing_manager", mock_shm), \
             patch("bot.safe_send_discord_message", new_callable=AsyncMock) as mock_send:
            
            # Test MissingRequiredArgument
            param = MagicMock()
            param.name = "path"
            error = commands.MissingRequiredArgument(param)
            await bot.on_command_error(mock_ctx, error)
            
            mock_send.assert_called_once()
            self.assertIn("필수 인수가 누락되었습니다", mock_send.call_args[0][1])
            mock_shm.handle_error.assert_not_called()

    async def test_self_healing_command_error_guard(self):
        """Ensure _run_self_healing_and_resume does NOT execute command error messages as LLM prompts."""
        mock_channel = AsyncMock()
        mock_channel.id = 777888
        mock_channel.send = AsyncMock()
        
        session = self.sm.get_session(777888)
        session.set_busy(True, "busy")
        
        admin_session = self.sm.get_session(self.shm.admin_channel_id)
        
        mock_status_msg = AsyncMock()
        mock_status_msg.delete = AsyncMock()

        admin_channel = MagicMock()
        admin_channel.send = AsyncMock(return_value=mock_status_msg)
        admin_channel.typing.return_value.__aenter__ = AsyncMock()
        admin_channel.typing.return_value.__aexit__ = AsyncMock()

        with patch.object(self_healing.AgentRunner, "run_prompt", new_callable=AsyncMock) as mock_agent_run, \
             patch.object(self_healing, "safe_send_discord_message", new_callable=AsyncMock) as mock_safe_send, \
             patch("bot.reload_helper_modules"):
            
            mock_agent_run.return_value = "Fix applied successfully"
            
            command_error_prompt = "[Unknown 명령어 실행 중 오류 발생]\n명령어: !sto"
            
            await self.shm._run_self_healing_and_resume(
                bot=MagicMock(),
                admin_channel=admin_channel,
                admin_session=admin_session,
                repair_prompt="repair instructions",
                failed_channel=mock_channel,
                failed_prompt=command_error_prompt,
                prompt_key="777888:error"
            )

            # Self healing is decommissioned, should not run prompt
            mock_agent_run.assert_not_called()

    async def test_reset_command_single_channel_isolation(self):
        """Verifies that !reset on a single channel does NOT terminate tasks, processes or queues in other channels."""
        # Setup session 1111 (target) and session 2222 (unrelated active channel)
        sess_1 = self.sm.get_session(1111)
        sess_1.set_conversation_id("conv-1111")
        sess_1.enqueue(None, "task 1")

        sess_2 = self.sm.get_session(2222)
        sess_2.set_conversation_id("conv-2222")
        sess_2.set_busy(True, desc="빌드 및 QA 실행 중")
        sess_2.enqueue(None, "unrelated task 2")

        ctx = MagicMock()
        ctx.channel.id = 1111
        status_msg = MagicMock()
        status_msg.edit = AsyncMock()
        ctx.channel.send = AsyncMock(return_value=status_msg)
        ctx.send = AsyncMock(return_value=status_msg)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager") as mock_apm, \
             patch("session_manager.kill_child_processes") as mock_kill_proc, \
             patch("bot.reload_helper_modules", return_value=30):

            await bot.reset_command(ctx)

            # Target channel 1111 must be reset
            self.assertIsNone(sess_1.conversation_id)
            self.assertEqual(len(sess_1.peek_queue()), 0)
            self.assertFalse(sess_1.is_busy)

            # Unrelated channel 2222 must remain 100% INTACT
            self.assertEqual(sess_2.conversation_id, "conv-2222")
            self.assertEqual(len(sess_2.peek_queue()), 1)
            self.assertTrue(sess_2.is_busy)
            self.assertEqual(sess_2.status, "busy")

            # Global kill_child_processes must NOT have been called
            mock_kill_proc.assert_not_called()

    async def test_reset_command_all_channels_scope(self):
        """Verifies that !reset all cancels all channels and invokes global cleanups."""
        sess_1 = self.sm.get_session(1111)
        sess_1.set_conversation_id("conv-1111")
        sess_2 = self.sm.get_session(2222)
        sess_2.set_conversation_id("conv-2222")

        ctx = MagicMock()
        ctx.channel.id = 1111
        status_msg = MagicMock()
        status_msg.edit = AsyncMock()
        ctx.channel.send = AsyncMock(return_value=status_msg)
        ctx.send = AsyncMock(return_value=status_msg)

        with patch("bot.session_manager", self.sm), \
             patch("bot.auto_pilot_manager") as mock_apm, \
             patch("session_manager.kill_child_processes") as mock_kill_proc, \
             patch("bot.reload_helper_modules", return_value=30):

            await bot.reset_command(ctx, "all")

            # Both channels must be reset
            self.assertIsNone(sess_1.conversation_id)
            self.assertIsNone(sess_2.conversation_id)

            # Global cleanups must have run
            mock_kill_proc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
