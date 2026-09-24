#!/usr/bin/env python3
"""
Regression tests for AGY CLI Guard model routing (agent_runner._execute_single_run).

증상: QA 차례에 `opencode-go/mimo-v2.6-flash` 가 OpenRouter(Hermes)로 오라우팅되어
      `HTTP 400: ... is not a valid model ID` 로 실패.

기대: `opencode-go/*` / `opencode/*` 접두사 모델은 Hermes 가 아니라
      OpenCode 어댑터(opencode CLI)로 직행한다.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)


class TestOpenCodeRoutingDispatch(unittest.IsolatedAsyncioTestCase):
    def _session(self, role="qa"):
        s = MagicMock()
        s.workspace = "/fake/workspace"
        s.role = role
        s.conversation_id = "test-conv"
        s.qa_model = "opencode-go/mimo-v2.6-flash"
        return s

    async def test_opencode_go_model_dispatches_to_opencode_not_hermes(self):
        from agent_runner import AgentRunner
        from engine_manager import OpenCodeAdapter, HermesAdapter

        session = self._session(role="qa")
        with patch.object(OpenCodeAdapter, "execute", new_callable=AsyncMock) as mock_oc, \
             patch.object(HermesAdapter, "execute", new_callable=AsyncMock) as mock_hermes:
            mock_oc.return_value = (0, "OpenCode Response", [])

            code, resp, last_err, all_err = await AgentRunner._execute_single_run(
                session=session,
                prompt="test prompt",
                model="opencode-go/mimo-v2.6-flash",
            )

            self.assertEqual(code, 0)
            self.assertEqual(resp, "OpenCode Response")
            mock_oc.assert_awaited_once()
            self.assertEqual(mock_oc.call_args[1].get("model"), "opencode-go/mimo-v2.6-flash")
            mock_hermes.assert_not_awaited()

    async def test_opencode_prefix_model_dispatches_to_opencode(self):
        from agent_runner import AgentRunner
        from engine_manager import OpenCodeAdapter, HermesAdapter

        session = self._session(role="coder")
        with patch.object(OpenCodeAdapter, "execute", new_callable=AsyncMock) as mock_oc, \
             patch.object(HermesAdapter, "execute", new_callable=AsyncMock) as mock_hermes:
            mock_oc.return_value = (0, "OK", [])

            code, resp, _err, _all = await AgentRunner._execute_single_run(
                session=session,
                prompt="test prompt",
                model="opencode/some-model",
            )

            self.assertEqual(code, 0)
            mock_oc.assert_awaited_once()
            mock_hermes.assert_not_awaited()

    async def test_run_prompt_reroutes_opencode_model_even_when_engine_is_hermes(self):
        """엔진이 hermes/openrouter 로 남아 있어도 opencode-go/* 모델은 OpenCode 로 직행해야 한다."""
        from agent_runner import AgentRunner
        from engine_manager import OpenCodeAdapter, HermesAdapter

        session = self._session(role="qa")
        session.engine = "hermes"
        session.is_busy = False
        session.is_stopped = False
        session.current_process = None

        with patch.object(HermesAdapter, "is_installed", return_value=True), \
             patch.object(HermesAdapter, "execute", new_callable=AsyncMock) as mock_hermes, \
             patch.object(OpenCodeAdapter, "execute", new_callable=AsyncMock) as mock_oc:
            mock_oc.return_value = (0, "OpenCode Response", [])

            resp = await AgentRunner.run_prompt(
                session=session,
                prompt="review this",
                model="opencode-go/mimo-v2.6-flash",
                engine="hermes",
            )

            self.assertEqual(resp, "OpenCode Response")
            mock_oc.assert_awaited_once()
            mock_hermes.assert_not_awaited()

    async def test_openrouter_model_still_dispatches_to_hermes(self):
        from agent_runner import AgentRunner
        from engine_manager import OpenCodeAdapter, HermesAdapter

        session = self._session(role="qa")
        with patch.object(OpenCodeAdapter, "execute", new_callable=AsyncMock) as mock_oc, \
             patch.object(HermesAdapter, "execute", new_callable=AsyncMock) as mock_hermes:
            mock_hermes.return_value = (0, "Hermes Response", [])

            code, resp, _err, _all = await AgentRunner._execute_single_run(
                session=session,
                prompt="test prompt",
                model="openrouter/upstage/solar-pro4",
            )

            self.assertEqual(code, 0)
            self.assertEqual(resp, "Hermes Response")
            mock_hermes.assert_awaited_once()
            mock_oc.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
