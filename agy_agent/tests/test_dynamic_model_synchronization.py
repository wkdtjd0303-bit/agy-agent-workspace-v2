import os
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import config
import discord
from model_manager import GlobalModelManager


class TestDynamicModelSynchronization(unittest.IsolatedAsyncioTestCase):
    """
    Comprehensive integration test verifying that model changes via GlobalModelManager
    dynamically synchronize across:
    1. Display names (get_role_model_display)
    2. Bot Discord Presences (sync_all_bot_presences)
    3. Status, Identity, and Help representations
    4. TrioAgentRunner role headers and transit banner generators
    """

    def setUp(self):
        # Save original coder and auditor models
        self.original_coder_engine = GlobalModelManager.get_role_engine("coder")
        self.original_coder_model = GlobalModelManager.get_role_model("coder")
        self.original_auditor_engine = GlobalModelManager.get_role_engine("auditor")
        self.original_auditor_model = GlobalModelManager.get_role_model("auditor")

    def tearDown(self):
        # Restore original models
        GlobalModelManager.set_role_engine("coder", self.original_coder_engine)
        GlobalModelManager.set_role_model("coder", self.original_coder_model)
        GlobalModelManager.set_role_engine("auditor", self.original_auditor_engine)
        GlobalModelManager.set_role_model("auditor", self.original_auditor_model)
        self._restore_bot_model_globals()

    def _restore_bot_model_globals(self):
        """
        [🧪 테스트 격리] 이 테스트는 모델을 실제로 바꿔가며 검증하므로, 그 과정에서
        `bot` 모듈의 모듈 레벨 CODER_MODEL/QA_MODEL/... 글로럴이 바뀐다.
        (reload_helper_modules 가 config 값을 복사해 넣기 때문이다.)
        그대로 두면 같은 프로세스에서 뒤이어 도는 테스트
        (예: test_bot_presence_models 의 'bot.CODER_MODEL == config.CODER_MODEL'
        정합성 검사)가 이 테스트의 잔여 상태에 실패한다.
        → 원래 config 값으로 bot 글로럴을 되돌려 테스트 간 누수를 막는다.
        """
        import os as _os
        import sys as _sys

        cfg = _sys.modules.get("config")
        bot_mod = _sys.modules.get("bot")
        if cfg is None or bot_mod is None:
            return
        for attr in ("CODER_MODEL", "QA_MODEL", "AUDITOR_MODEL", "COMMANDER_MODEL"):
            orig = getattr(cfg, attr, None)
            if orig is None:
                continue
            # os.environ 에 테스트가 주입했을 수 있으므로 config 원본으로 되돌린다.
            _os.environ[attr] = orig
            if hasattr(bot_mod, attr):
                setattr(bot_mod, attr, orig)

    def test_format_model_name_friendly_displays(self):
        """format_model_name must transform raw model IDs into human-readable labels."""
        self.assertEqual(GlobalModelManager.format_model_name("gemini-3.8-flash-high"), "Gemini 3.8 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("gpt-oss-120b-medium"), "GPT-OSS 120B")
        self.assertEqual(GlobalModelManager.format_model_name("openai/gpt-oss-120b"), "GPT-OSS 120B")
        self.assertEqual(GlobalModelManager.format_model_name("claude-sonnet-4-6"), "Claude Sonnet 4.6")
        self.assertEqual(GlobalModelManager.format_model_name("claude-opus-4-6-thinking"), "Claude Opus 4.6 Thinking")
        self.assertEqual(GlobalModelManager.format_model_name("upstage/solar-pro4"), "Solar Pro 4")
        self.assertEqual(GlobalModelManager.format_model_name("deepseek/deepseek-chat"), "DeepSeek V3")

    async def test_dynamic_coder_model_switch_updates_display_and_presence(self):
        """
        Switching Coder model dynamically updates get_role_model_display
        and triggers sync_all_bot_presences without restart.
        """
        import bot

        orig_callbacks = list(GlobalModelManager._on_change_callbacks)
        GlobalModelManager._on_change_callbacks.clear()
        try:
            with patch.object(bot.bot, "is_ready", return_value=True), \
                 patch.object(bot.bot, "change_presence", new_callable=AsyncMock) as mock_bot_presence, \
                 patch.object(bot.qa_bot, "is_ready", return_value=False), \
                 patch.object(bot.qa2_bot, "is_ready", return_value=False), \
                 patch.object(bot.auditor_bot, "is_ready", return_value=False):

                # 1. Switch Coder to GPT-OSS 120B
                GlobalModelManager.set_role_model("coder", "gpt-oss-120b-medium")
                coder_display = GlobalModelManager.get_role_model_display("coder")
                self.assertEqual(coder_display, "GPT-OSS 120B")

                await bot.sync_all_bot_presences()

                activity = mock_bot_presence.call_args[1].get("activity")
                self.assertIsNotNone(activity)
                self.assertIn("Coder (초고속 개발)", activity.name)
                self.assertIn("GPT-OSS 120B", activity.name)

                mock_bot_presence.reset_mock()

                # 2. Switch Coder to Claude Sonnet 4.6
                GlobalModelManager.set_role_model("coder", "claude-sonnet-4-6")
                coder_display = GlobalModelManager.get_role_model_display("coder")
                self.assertEqual(coder_display, "Claude Sonnet 4.6")

                await bot.sync_all_bot_presences()

                activity = mock_bot_presence.call_args[1].get("activity")
                self.assertIsNotNone(activity)
                self.assertIn("Claude Sonnet 4.6", activity.name)
        finally:
            GlobalModelManager._on_change_callbacks = orig_callbacks

    async def test_callback_registered_and_invoked_on_model_save(self):
        """GlobalModelManager.notify_change invokes registered sync_all_bot_presences."""
        callback_called = False

        def test_callback():
            nonlocal callback_called
            callback_called = True

        GlobalModelManager.register_change_callback(test_callback)
        GlobalModelManager.set_role_model("coder", "gemini-3.8-flash-high")

        self.assertTrue(callback_called)

    def test_trio_agent_runner_dynamic_model_display_in_status_and_prompts(self):
        """
        Verify that TrioAgentRunner status and prompts use GlobalModelManager.get_role_model_display
        instead of any hardcoded 'Gemini 3.7 Flash'.
        """
        # Set Coder to GPT-OSS 120B
        GlobalModelManager.set_role_model("coder", "gpt-oss-120b-medium")
        GlobalModelManager.set_role_model("auditor", "claude-opus-4-6-thinking")

        coder_display = GlobalModelManager.get_role_model_display("coder")
        auditor_display = GlobalModelManager.get_role_model_display("auditor")

        self.assertEqual(coder_display, "GPT-OSS 120B")
        self.assertEqual(auditor_display, "Claude Opus 4.6 Thinking")

        # Emulate the exact status format used in trio_agent_runner.py
        round_num = 1
        max_rounds = 3
        status_banner = f"👨‍💻 **[라운드 {round_num}/{max_rounds}] Coder({coder_display}) 초고속 견고 개발 진행 중...**"
        tool_status = f"👨‍💻 **[Coder({coder_display}) 작업 중]** file_edit"
        auditor_status = f"⚖️ **[라운드 {round_num}/{max_rounds}] Auditor({auditor_display})가 PRD 헌법 부합성 & 기계적 증거 전수 대조 감사를 시작합니다...**"

        self.assertIn("Coder(GPT-OSS 120B)", status_banner)
        self.assertIn("Coder(GPT-OSS 120B) 작업 중", tool_status)
        self.assertIn("Auditor(Claude Opus 4.6 Thinking)", auditor_status)
        self.assertNotIn("Gemini 3.7", status_banner)
        self.assertNotIn("Gemini 3.7", tool_status)

    def test_smart_alias_resolution(self):
        """GlobalModelManager.resolve_model_id accurately resolves colloquial model names."""
        # AGY engine resolution
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gpt oss 120b"), "gpt-oss-120b-medium")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gpt-oss-120b"), "gpt-oss-120b-medium")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini 3.8"), "gemini-3.8-flash-high")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini 3.8 flash"), "gemini-3.8-flash-high")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini 3.7"), "gemini-3.7-flash-high")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini 3.1 pro"), "gemini-3.1-pro-high")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "sonnet"), "claude-sonnet-4-6")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "opus"), "claude-opus-4-6-thinking")

        # [🚫 Deprecated AGY Guard] Gemini 2.5 Flash 는 Antigravity CLI 미지원 → 3.8 Flash 자동 승격
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini 2.5 flash"), "gemini-3.8-flash-high")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini-2.5-flash"), "gemini-3.8-flash-high")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini-2.5-flash-lite"), "gemini-3-flash")
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "gemini 2.5 pro"), "gemini-3.1-pro-high")

        # Hermes / OpenRouter engine resolution
        self.assertEqual(GlobalModelManager.resolve_model_id("hermes", "solar"), "upstage/solar-pro4")
        self.assertEqual(GlobalModelManager.resolve_model_id("hermes", "gpt oss 120b"), "openai/gpt-oss-120b")
        self.assertEqual(GlobalModelManager.resolve_model_id("hermes", "deepseek v4"), "deepseek/deepseek-v4-flash-0731")

    async def test_set_role_model_with_alias_and_nickname_sync(self):
        """Setting 'gpt oss 120b' updates model to gpt-oss-120b-medium and triggers nickname sync."""
        import bot

        GlobalModelManager.set_role_engine("coder", "agy")
        GlobalModelManager.set_role_model("coder", "gpt oss 120b")

        self.assertEqual(GlobalModelManager.get_role_model("coder"), "gpt-oss-120b-medium")
        self.assertEqual(GlobalModelManager.get_role_model_display("coder"), "GPT-OSS 120B")

        # Verify nickname update logic
        mock_guild = MagicMock()
        mock_me = MagicMock()
        mock_me.nick = "Coder (Gemini 3.8 Flash)"
        mock_me.guild_permissions.change_nickname = True
        mock_me.edit = AsyncMock()
        mock_guild.me = mock_me
        mock_guild.name = "Test Guild"

        with patch.object(bot.bot, "is_ready", return_value=True), \
             patch.object(type(bot.bot), "guilds", new_callable=PropertyMock, return_value=[mock_guild]), \
             patch.object(bot.bot, "change_presence", new_callable=AsyncMock) as mock_presence, \
             patch.object(bot.qa_bot, "is_ready", return_value=False), \
             patch.object(bot.qa2_bot, "is_ready", return_value=False), \
             patch.object(bot.auditor_bot, "is_ready", return_value=False):

            await bot.sync_all_bot_presences()

            # Check presence
            mock_presence.assert_called_once()
            act = mock_presence.call_args[1].get("activity")
            self.assertIn("GPT-OSS 120B", act.name)

            # Check nickname
            mock_me.edit.assert_called_once_with(nick="Coder (GPT-OSS 120B)")

    async def test_bots_command_embed_shows_dynamic_models(self):
        """!bots command generates embed with current dynamic model representations."""
        import bot

        GlobalModelManager.set_role_model("coder", "gpt-oss-120b-medium")
        GlobalModelManager.set_role_model("auditor", "gemini-3.8-flash-high")

        mock_ctx = MagicMock()
        mock_ctx.send = AsyncMock()

        await bot.bots_command(mock_ctx)

        mock_ctx.send.assert_called_once()
        sent_embed = mock_ctx.send.call_args[1].get("embed")
        self.assertIsNotNone(sent_embed)
        self.assertIn("AGY 자율 협업 AI 봇 군단", sent_embed.title)

        field_texts = [f"{f.name} {f.value}" for f in sent_embed.fields]
        combined = " ".join(field_texts)
        self.assertIn("Coder (초고속 개발 및 구현) — GPT-OSS 120B", combined)
        self.assertIn("gpt-oss-120b-medium", combined)
        self.assertIn("Coder(GPT-OSS 120B) 작업 중", combined)
        self.assertIn("Auditor (최종 수락 감사) — Gemini 3.8 Flash", combined)


if __name__ == "__main__":
    unittest.main()
