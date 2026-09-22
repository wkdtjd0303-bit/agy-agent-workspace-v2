import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from session_manager import ChannelSession, SessionManager
from mode_classifier import classify_mode, get_mode_description
from commander import Commander
from engine_manager import EngineManager, OpenRouterAdapter


class TestSinglePlusMode(unittest.TestCase):
    """
    Unit tests for !mode 1+ / !mode single+ (Consultant Mode with GPT Astra).
    Strictly uses mocked/static tests with NO real network calls to avoid API charges.
    """

    def setUp(self):
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()

    def tearDown(self):
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()

    def test_session_set_mode_single_plus(self):
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        test_inputs = [
            "1+",
            "1 +",
            "!mode 1+",
            "single+",
            "single +",
            "single_plus",
            "1plus",
            "1플러스",
            "consultant",
            "컨설턴트"
        ]
        for mode_input in test_inputs:
            # Strip command prefix if simulated
            clean_mode = mode_input.replace("!mode", "").strip()
            sess.set_mode(clean_mode)
            self.assertEqual(sess.mode, "single_plus", f"Failed for mode input: {mode_input}")

    def test_get_mode_description_single_plus(self):
        c_model = getattr(config, "CONSULTANT_MODEL", "openai/gpt-6-astra")
        expected_substr = f'👤 단일 에이전트 모드 (Single · !mode 1+ ) (주의 필요 : 컨설턴트 모드로 동작합니다. 동작 모델 "{c_model}")'

        for mode_str in ["single_plus", "single+", "1+", "1 +", "single +", "consultant"]:
            desc = get_mode_description(mode_str)
            self.assertEqual(desc, expected_substr, f"Mismatch for mode: {mode_str}")

    def test_classify_mode_single_plus(self):
        c_model = getattr(config, "CONSULTANT_MODEL", "openai/gpt-6-astra")
        for session_mode in ["single_plus", "single+", "1+", "consultant"]:
            mode, desc = classify_mode("아키텍처 확장성에 대해 자문해줘", session_mode=session_mode)
            self.assertEqual(mode, "single_plus")
            self.assertIn("단일 컨설턴트 에이전트", desc)
            self.assertIn(c_model, desc)

    def test_resolve_model_alias_astra(self):
        self.assertEqual(EngineManager.resolve_model_alias("astra"), "openrouter/openai/gpt-6-astra")
        self.assertEqual(EngineManager.resolve_model_alias("gpt-6-astra"), "openrouter/openai/gpt-6-astra")
        self.assertEqual(EngineManager.resolve_model_alias("openai/gpt-6-astra"), "openrouter/openai/gpt-6-astra")

    @patch.object(OpenRouterAdapter, "execute_direct_api", new_callable=AsyncMock)
    def test_openrouter_adapter_consultant_execution(self, mock_direct_api):
        mock_direct_api.return_value = (0, "컨설턴트 분석 결과입니다.", [])
        adapter = OpenRouterAdapter()
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        sess.mode = "single_plus"
        sess.role = "consultant"

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(adapter, "_get_clean_key", return_value="sk-or-dummy-key-test"), \
                 patch.object(adapter, "get_binary_path", return_value=None):
                code, resp, errors = loop.run_until_complete(
                    adapter.execute(
                        session=sess,
                        prompt="시스템 확장성 자문 요청",
                        model="openai/gpt-6-astra"
                    )
                )
            self.assertEqual(code, 0)
            self.assertEqual(resp, "컨설턴트 분석 결과입니다.")
            self.assertEqual(errors, [])
            mock_direct_api.assert_called_once()
        finally:
            loop.close()

    @patch.object(OpenRouterAdapter, "get_binary_path", return_value="/home/jangsung/.local/bin/hermes")
    def test_openrouter_adapter_consultant_cli_execution(self, mock_get_bin):
        """When Hermes CLI is present, consultant mode executes via CLI enabling file inspection and modification."""
        from engine_manager import BaseEngineAdapter
        adapter = OpenRouterAdapter()
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        sess.mode = "single_plus"
        sess.role = "consultant"

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(adapter, "_get_clean_key", return_value="sk-or-dummy-key-test"), \
                 patch.object(adapter.__class__.__bases__[0], "execute", new_callable=AsyncMock) as mock_super_exec:
                mock_super_exec.return_value = (0, "Hermes file inspection & edit completed", [])
                code, resp, errors = loop.run_until_complete(
                    adapter.execute(
                        session=sess,
                        prompt="hello.py 함수 내용 수정해줘",
                        model="openai/gpt-6-astra"
                    )
                )
            self.assertEqual(code, 0)
            self.assertEqual(resp, "Hermes file inspection & edit completed")
            mock_super_exec.assert_called_once()
        finally:
            loop.close()

    @patch.object(OpenRouterAdapter, "execute_direct_api", new_callable=AsyncMock)
    def test_openrouter_adapter_consultant_error_no_backup_leak(self, mock_direct_api):
        mock_direct_api.return_value = (1, "", ["Quota exceeded"])
        adapter = OpenRouterAdapter()
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        sess.mode = "single_plus"
        sess.role = "consultant"

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(adapter, "_get_clean_key", return_value="sk-or-dummy-key-test"), \
                 patch.object(adapter, "get_binary_path", return_value=None):
                code, resp, errors = loop.run_until_complete(
                    adapter.execute(
                        session=sess,
                        prompt="시스템 확장성 자문 요청",
                        model="openai/gpt-6-astra"
                    )
                )
            self.assertEqual(code, 1)
            self.assertIn("OpenRouter Consultant direct API call failed", errors[0])
            # Ensure it only called once and did not call backup model
            self.assertEqual(mock_direct_api.call_count, 1)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
