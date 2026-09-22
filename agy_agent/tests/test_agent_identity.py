import os
import sys
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import bot
import config
from engine_manager import EngineManager, HermesAdapter, OpenRouterAdapter
from session_manager import SessionManager


class TestAgentIdentity(unittest.IsolatedAsyncioTestCase):
    """
    Test suite verifying agent identity separation (agy vs hermes),
    identity command execution (!identity, !whoami), natural language ping detection,
    and OpenRouter/Hermes endpoint stability.
    """

    def setUp(self):
        self.channel_id = 999123456
        self.session = bot.session_manager.get_session(self.channel_id)

    async def test_identity_command_with_agy_engine(self):
        """When channel session engine is agy, !identity command replies with 'agy'."""
        self.session.engine = "agy"
        ctx = MagicMock()
        ctx.channel.id = self.channel_id
        ctx.send = AsyncMock()

        cmd = bot.bot.all_commands.get("identity")
        self.assertIsNotNone(cmd, "identity command must be registered in bot.all_commands")
        self.assertIn("whoami", cmd.aliases)
        self.assertIn("id", cmd.aliases)
        self.assertIn("정체성", cmd.aliases)

        with patch("bot.reload_helper_modules", return_value=1):
            await cmd.callback(ctx)
        ctx.send.assert_called_once()
        first_arg = ctx.send.call_args[0][0]
        self.assertEqual(first_arg, "agy")
        embed = ctx.send.call_args[1].get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("agy", embed.title.lower())

    async def test_identity_command_with_hermes_engine(self):
        """When channel session engine is hermes, !identity command replies with 'hermes'."""
        self.session.engine = "hermes"
        ctx = MagicMock()
        ctx.channel.id = self.channel_id
        ctx.send = AsyncMock()

        cmd = bot.bot.all_commands.get("identity")
        with patch("bot.reload_helper_modules", return_value=1):
            await cmd.callback(ctx)
        ctx.send.assert_called_once()
        first_arg = ctx.send.call_args[0][0]
        self.assertEqual(first_arg, "hermes")
        embed = ctx.send.call_args[1].get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("hermes", embed.title.lower())

    async def test_on_message_natural_language_ping_agy(self):
        """When user sends test ping to agy channel, bot replies with 'agy'."""
        self.session.engine = "agy"
        message = MagicMock()
        message.author.bot = False
        message.webhook_id = None
        message.is_system.return_value = False
        message.channel.id = self.channel_id
        message.channel.category_id = None
        message.channel.category = None
        message.content = '한번 agy agent로 동작하는 봇은"agy"라고, hermes agnet로 구동하는 봇은 "hermes"라고 답장해줄래?? 그래서 다들 정상적으로 구동하고 있는지 테스트 할거야.'
        message.reply = AsyncMock()
        message.attachments = []

        with patch("bot.ensure_commander_bot_started"), \
             patch("bot.ensure_consultant_bot_started"):
            await bot.on_message(message)

        message.reply.assert_called_once()
        reply_text = message.reply.call_args[0][0]
        self.assertEqual(reply_text, "agy")

    async def test_on_message_natural_language_ping_hermes(self):
        """When user sends test ping to hermes channel, bot replies with 'hermes'."""
        self.session.engine = "hermes"
        message = MagicMock()
        message.author.bot = False
        message.webhook_id = None
        message.is_system.return_value = False
        message.channel.id = self.channel_id
        message.channel.category_id = None
        message.channel.category = None
        message.content = '한번 agy agent로 동작하는 봇은"agy"라고, hermes agnet로 구동하는 봇은 "hermes"라고 답장해줄래?? 그래서 다들 정상적으로 구동하고 있는지 테스트 할거야.'
        message.reply = AsyncMock()
        message.attachments = []

        with patch("bot.ensure_commander_bot_started"), \
             patch("bot.ensure_consultant_bot_started"):
            await bot.on_message(message)

        message.reply.assert_called_once()
        reply_text = message.reply.call_args[0][0]
        self.assertEqual(reply_text, "hermes")

    def test_hermes_adapter_llama4_scout_fallback(self):
        """HermesAdapter automatically falls back from nonexistent llama-4-scout (404) to llama-3.3-70b-instruct."""
        adapter = HermesAdapter()
        cmd = adapter.build_command(
            prompt="Hello",
            workspace="/tmp",
            model="meta-llama/llama-4-scout"
        )
        self.assertIn("-m", cmd)
        idx = cmd.index("-m")
        self.assertEqual(cmd[idx + 1], "meta-llama/llama-3.3-70b-instruct")

    def test_openrouter_adapter_llama4_scout_fallback(self):
        """OpenRouterAdapter automatically falls back from nonexistent llama-4-scout (404) to openrouter/meta-llama/llama-3.3-70b-instruct."""
        adapter = OpenRouterAdapter()
        cmd = adapter.build_command(
            prompt="Hello",
            workspace="/tmp",
            model="meta-llama/llama-4-scout"
        )
        self.assertIn("-m", cmd)
        idx = cmd.index("-m")
        self.assertIn(cmd[idx + 1], ["openrouter/meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-3.3-70b-instruct"])

    def test_hermes_engine_registered_and_configured(self):
        """Ensures Hermes engine adapter is fully registered and config settings are present."""
        adapter = EngineManager.get_adapter("hermes")
        self.assertIsNotNone(adapter, "Hermes adapter must be registered in EngineManager")
        self.assertEqual(adapter.engine_id, "hermes")
        self.assertTrue(hasattr(config, "HERMES_MODEL"))
        self.assertTrue(hasattr(config, "HERMES_ENGINE"))
        self.assertTrue(hasattr(config, "DEFAULT_ENGINE"))


if __name__ == "__main__":
    unittest.main()
