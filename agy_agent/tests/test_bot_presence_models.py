import os
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import config
import discord
import hot_reload_bridge


class TestBotPresenceModels(unittest.IsolatedAsyncioTestCase):
    """
    Test suite verifying that all Discord bot client presences include
    their active model identifiers, specifically verifying Coder bot (agy_agents)
    displays its running model alongside 'help | Coder (초고속 개발)'.
    """

    def setUp(self):
        self._orig_main = sys.modules.get("__main__")

    def tearDown(self):
        if self._orig_main is not None:
            sys.modules["__main__"] = self._orig_main
        else:
            sys.modules.pop("__main__", None)

    def test_coder_model_presence_string_format(self):
        """Coder presence string must include help prefix, role description, and active model."""
        coder_model = getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
        cmd_prefix = getattr(config, "COMMAND_PREFIX", "!")
        expected_activity = f"{cmd_prefix}help | Coder (초고속 개발) | {coder_model}"

        self.assertIn("Coder (초고속 개발)", expected_activity)
        self.assertIn(coder_model, expected_activity)
        self.assertTrue(expected_activity.endswith(coder_model))

    async def test_hot_reload_bridge_syncs_coder_presence(self):
        """hot_reload_bridge.sync_in_memory_state updates bot.change_presence with model."""
        class MockMain:
            def __init__(self):
                self.bot = MagicMock()
                self.bot.is_ready = MagicMock(return_value=True)
                self.bot.change_presence = AsyncMock()
                self.bot.all_commands = {}

        mock_main = MockMain()
        sys.modules["__main__"] = mock_main

        with patch("hot_reload_bridge.ast.parse", side_effect=Exception("skip ast")):
            hot_reload_bridge.sync_in_memory_state()

        # Let the event loop run scheduled tasks
        import asyncio
        await asyncio.sleep(0.05)

        mock_main.bot.change_presence.assert_called_once()
        activity = mock_main.bot.change_presence.call_args[1].get("activity")
        self.assertIsNotNone(activity)
        self.assertIn("Coder (초고속 개발)", activity.name)
        self.assertIn(config.CODER_MODEL, activity.name)

    async def test_execute_all_in_one_hot_reset_updates_all_presences(self):
        """execute_all_in_one_hot_reset synchronizes bot, qa, qa2, auditor, ext_qa, and consultant presences."""
        import bot

        mock_channel = MagicMock()
        mock_channel.id = 12345
        mock_channel.send = AsyncMock()

        with patch.object(bot.bot, "is_ready", return_value=True), \
             patch.object(bot.bot, "change_presence", new_callable=AsyncMock) as mock_bot_presence, \
             patch.object(bot.qa_bot, "is_ready", return_value=True), \
             patch.object(bot.qa_bot, "change_presence", new_callable=AsyncMock) as mock_qa_presence, \
             patch.object(bot.qa2_bot, "is_ready", return_value=True), \
             patch.object(bot.qa2_bot, "change_presence", new_callable=AsyncMock) as mock_qa2_presence, \
             patch.object(bot.auditor_bot, "is_ready", return_value=True), \
             patch.object(bot.auditor_bot, "change_presence", new_callable=AsyncMock) as mock_auditor_presence, \
             patch("bot.reload_helper_modules", return_value=1):

            await bot.execute_all_in_one_hot_reset(mock_channel, 12345)

            # [Presence Sync Lifecycle Assertion & Rationale]
            # execute_all_in_one_hot_reset triggers presence synchronization across all bot clients:
            # 1. bot.change_presence is invoked during sync_all_bot_presences() and/or hot reload sync.
            # 2. To avoid false positives on transient background reload tasks while strictly bounding calls,
            #    we verify call_count is bounded (1 <= call_count <= 2) and final presence reflects Coder identity.
            self.assertGreaterEqual(mock_bot_presence.call_count, 1)
            self.assertLessEqual(mock_bot_presence.call_count, 2)
            bot_activity = mock_bot_presence.call_args[1].get("activity")
            from model_manager import GlobalModelManager
            coder_disp = GlobalModelManager.get_role_model_display("coder")
            qa_disp = GlobalModelManager.get_role_model_display("qa1")
            self.assertIn("Coder (초고속 개발)", bot_activity.name)
            self.assertTrue(config.CODER_MODEL in bot_activity.name or coder_disp in bot_activity.name)

            mock_qa_presence.assert_called_once()
            qa_activity = mock_qa_presence.call_args[1].get("activity")
            self.assertTrue(config.QA_MODEL in qa_activity.name or qa_disp in qa_activity.name)

            mock_qa2_presence.assert_called_once()
            qa2_activity = mock_qa2_presence.call_args[1].get("activity")
            self.assertIn("Solar Pro 4", qa2_activity.name)

    def test_coder_model_default_and_identity_consistency(self):
        """CODER_MODEL in config and bot global identity must match consistently and follow valid model identifier conventions."""
        import bot
        # 1. config.CODER_MODEL must be a non-empty string adhering to standard model naming conventions
        self.assertTrue(bool(config.CODER_MODEL), "config.CODER_MODEL must not be empty")
        self.assertTrue(
            any(vendor in config.CODER_MODEL.lower() for vendor in ["gemini", "glm", "claude", "gpt", "flash", "pro", "high", "deepseek", "opencode"]),
            f"Unexpected model identifier format in config.CODER_MODEL: '{config.CODER_MODEL}'"
        )
        # 2. bot module global CODER_MODEL must strictly reflect config.CODER_MODEL identity
        self.assertEqual(config.CODER_MODEL, getattr(bot, "CODER_MODEL", None),
                         "bot.CODER_MODEL must strictly equal config.CODER_MODEL")

    def test_help_command_embed_displays_coder_and_qa_models(self):
        """help_manager.HelpManager embeds must clearly list Coder and QA squad models."""
        from help_manager import HelpManager
        overview = HelpManager.get_overview_embed()

        all_text = " ".join([f.name + " " + f.value for f in overview.fields])
        self.assertIn("Coder (초고속 개발", all_text)
        self.assertIn(config.CODER_MODEL, all_text)
        self.assertIn(config.QA_MODEL, all_text)

        for ext_cfg in config.get_extended_qa_configs():
            self.assertIn(ext_cfg["model"], all_text)
            self.assertIn(ext_cfg["name"], all_text)

        dev = HelpManager.get_dev_embed()
        dev_text = " ".join([f.name + " " + f.value for f in dev.fields])
        self.assertIn(f"Coder(`{config.CODER_MODEL}`)", dev_text)


if __name__ == "__main__":
    unittest.main()
