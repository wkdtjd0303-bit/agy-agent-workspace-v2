import unittest
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_manager import should_route_away_from_agy, SessionManager
from model_registry import AntigravityModelRegistry
from engine_manager import EngineManager
import telemetry_status_manager


class TestUnblockOpusAndGptOss(unittest.TestCase):
    def test_should_route_away_from_agy_allows_native_opus_and_gpt_oss(self):
        """Native Opus and GPT-OSS are allowed on agy engine."""
        self.assertFalse(should_route_away_from_agy("claude-opus-4-6-thinking"))
        self.assertFalse(should_route_away_from_agy("opus"))
        self.assertFalse(should_route_away_from_agy("claude-opus"))
        self.assertFalse(should_route_away_from_agy("gpt-oss-120b-medium"))
        self.assertFalse(should_route_away_from_agy("gpt-oss"))
        self.assertFalse(should_route_away_from_agy("oss"))
        self.assertFalse(should_route_away_from_agy("claude-sonnet-4-6"))

        # Third-party OpenRouter models should still route away to hermes
        self.assertTrue(should_route_away_from_agy("openrouter/openai/gpt-oss-120b"))
        self.assertTrue(should_route_away_from_agy("qwen/qwen-2.5-coder-32b-instruct"))
        self.assertTrue(should_route_away_from_agy("deepseek/deepseek-chat"))

    def test_model_registry_validates_opus_and_gpt_oss(self):
        """AntigravityModelRegistry recognizes Opus and GPT-OSS as valid Antigravity models."""
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("claude-opus-4-6-thinking"))
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("gpt-oss-120b-medium"))
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("claude-sonnet-4-6"))

    def test_engine_manager_native_models_contain_opus_and_gpt_oss(self):
        """EngineManager AntigravityAdapter lists Opus and GPT-OSS in supported models."""
        adapter = EngineManager.get_adapter("agy")
        self.assertIsNotNone(adapter)
        models = adapter.supported_models
        self.assertIn("claude-opus-4-6-thinking", models)
        self.assertIn("gpt-oss-120b-medium", models)

    def test_telemetry_clean_model_display_name_shows_unlocked(self):
        """clean_model_display_name cleanly formats Opus, Sonnet, and GPT-OSS without clutter."""
        self.assertEqual(
            telemetry_status_manager.clean_model_display_name("Claude Opus 4.6", "claude-opus-4-6-thinking"),
            "Claude Opus"
        )
        self.assertEqual(
            telemetry_status_manager.clean_model_display_name("GPT-OSS 120B Medium", "gpt-oss-120b-medium"),
            "GPT-OSS 120B"
        )
        self.assertEqual(
            telemetry_status_manager.clean_model_display_name("Claude Sonnet 4.6", "claude-sonnet-4-6"),
            "Claude Sonnet"
        )

    def test_trio_agent_runner_preserves_qa_model_for_opus_and_gpt_oss(self):
        """TrioAgentRunner does not force-override base QA model when Opus or GPT-OSS is configured."""
        import config
        from trio_agent_runner import TrioAgentRunner, QuotaManager
        QuotaManager.reset_exhaustion()

        async def _run():
            with patch.object(config, "QA_MODEL", "claude-opus-4-6-thinking"):
                fast_m, disp, _ = await TrioAgentRunner.resolve_qa_model(is_auto=False, risk_level="NORMAL")
                self.assertEqual(fast_m, "claude-opus-4-6-thinking")

            with patch.object(config, "QA_MODEL", "gpt-oss-120b-medium"):
                fast_m2, disp2, _ = await TrioAgentRunner.resolve_qa_model(is_auto=False, risk_level="NORMAL")
                self.assertEqual(fast_m2, "gpt-oss-120b-medium")

        asyncio.run(_run())

    def test_trio_agent_runner_preserves_auditor_model_for_opus_and_gpt_oss(self):
        """TrioAgentRunner does not force-override Auditor model when Opus or GPT-OSS is configured."""
        import config
        from trio_agent_runner import TrioAgentRunner
        from session_manager import ChannelSession
        from unittest.mock import AsyncMock

        async def _run():
            session = ChannelSession(channel_id=123, workspace="/tmp")
            with patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nApproved."
                # Mock GlobalModelManager to return None (no explicit !model override),
                # so run_auditor_turn falls through to config.AUDITOR_MODEL
                with patch("trio_agent_runner.GlobalModelManager.get_role_model", return_value=None), \
                     patch("trio_agent_runner.GlobalModelManager.get_role_engine", return_value=None):

                    with patch.object(config, "AUDITOR_MODEL", "claude-opus-4-6-thinking"):
                        await TrioAgentRunner.run_auditor_turn(session, "Evaluate code")
                        call_model = mock_run.call_args.kwargs.get("model")
                        self.assertEqual(call_model, "claude-opus-4-6-thinking")

                    with patch.object(config, "AUDITOR_MODEL", "gpt-oss-120b-medium"):
                        await TrioAgentRunner.run_auditor_turn(session, "Evaluate code")
                        call_model = mock_run.call_args.kwargs.get("model")
                        self.assertEqual(call_model, "gpt-oss-120b-medium")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
