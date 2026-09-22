import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from agy_agent.engine_manager import HermesAdapter, OpenRouterAdapter
from agy_agent.session_manager import ChannelSession
from agy_agent.agent_runner import AgentRunner


class TestQA2Resilience(unittest.TestCase):
    def setUp(self):
        self.session = ChannelSession(
            channel_id=12345,
            workspace="/tmp/test_ws",
            role="qa2",
            mode="single_qa2",
            engine="openrouter"
        )

    def test_hermes_adapter_build_command_readonly(self):
        adapter = OpenRouterAdapter()
        cmd = adapter.build_command(
            prompt="코드 변경 사항을 검토해줘",
            workspace="/tmp/test_ws",
            model="upstage/solar-pro4",
            role="qa2",
            mode="single_qa2"
        )
        
        # 1. '-t', 'readonly' flag presence
        self.assertIn("-t", cmd)
        readonly_idx = cmd.index("-t")
        self.assertEqual(cmd[readonly_idx + 1], "readonly")

        # 2. '-q' flag must be followed immediately by the prompt (with guardrail)
        self.assertIn("-q", cmd)
        q_idx = cmd.index("-q")
        prompt_arg = cmd[q_idx + 1]
        self.assertIn("[CRITICAL MANDATE - READONLY QA ENVIRONMENT]", prompt_arg)
        self.assertIn("코드 변경 사항을 검토해줘", prompt_arg)

        # 3. Model prefix 'openrouter/' must be stripped for hermes
        self.assertIn("-m", cmd)
        m_idx = cmd.index("-m")
        self.assertEqual(cmd[m_idx + 1], "upstage/solar-pro4")

    def test_qa2_invalid_tool_fallback_to_agy(self):
        async def _run():
            # Mock adapter.execute to return invalid tool call error
            mock_adapter = MagicMock()
            mock_adapter.is_installed.return_value = True
            mock_adapter.icon = "🪽"
            mock_adapter.name = "Hermes Autonomous Agent"
            mock_adapter.execute = AsyncMock(return_value=(
                1,
                "Model generated invalid tool call: terminal",
                ["Error: unknown tool 'terminal'"]
            ))

            with patch("engine_manager.EngineManager.get_adapter", return_value=mock_adapter):
                with patch.object(AgentRunner, "_execute_single_run", new_callable=AsyncMock) as mock_single_run:
                    mock_single_run.return_value = (0, "✅ AGY 자가 치유 폴백으로 무사히 QA 검증을 완료했습니다.", [], [])
                    
                    status_updates = []
                    async def mock_tool_update(msg):
                        status_updates.append(msg)

                    resp = await AgentRunner.run_prompt(
                        session=self.session,
                        prompt="PR diff 검증 요청",
                        on_tool_update=mock_tool_update,
                        model="upstage/solar-pro4",
                        engine="openrouter",
                        role="qa2"
                    )

                    # Verify fallback was triggered
                    mock_single_run.assert_called_once()
                    self.assertIn("AGY 자가 치유 폴백", resp)
                    # Verify status notification was sent
                    self.assertTrue(any("즉시 안전 전환" in s for s in status_updates))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
