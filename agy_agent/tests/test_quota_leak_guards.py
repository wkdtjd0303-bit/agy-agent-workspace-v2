#!/usr/bin/env python3
"""
Quota Leak Guards — regression tests for the Antigravity/Gemini quota fixes.

Validates:
1. Commander no longer force-routes every request to Gemini.
2. Non-Gemini Commander models are classified for external routing.
3. AgyAdapter no longer silently falls back to a Gemini model.
4. Telemetry no longer spawns the `agy` CLI (language-server leak) to read quota.
"""

import os
import sys
import types
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import commander as commander_mod
from commander import Commander


class TestCommanderGeminiGuard(unittest.TestCase):

    def test_native_agy_model_classification(self):
        self.assertTrue(Commander._is_native_agy_model("gemini-3.7-flash-middle"))
        self.assertTrue(Commander._is_native_agy_model("claude-opus-4-6-thinking"))
        self.assertTrue(Commander._is_native_agy_model("gpt-oss-120b-medium"))
        self.assertFalse(Commander._is_native_agy_model("opencode-go/mimo-v2.6-flash"))
        self.assertFalse(Commander._is_native_agy_model("openrouter/qwen/qwen-2.5-coder-32b-instruct"))

    def test_no_hard_gemini_force_in_source(self):
        """The old 'force Gemini / block external' warnings must be gone."""
        with open(os.path.join(WORKSPACE_ROOT, "commander.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("blocked in commander raw query", src)
        self.assertNotIn("blocked in commander;", src)

    def test_external_query_routes_openrouter_without_gemini(self):
        """_run_commander_external_query must use the Hermes direct API for OpenRouter models."""
        import asyncio

        fake_adapter = MagicMock()
        fake_adapter.execute_direct_api = AsyncMock(return_value=(0, "EXTERNAL_OK", []))

        async def _run():
            with patch("engine_manager.EngineManager.get_adapter", return_value=fake_adapter):
                return await Commander._run_commander_external_query(
                    "hello", "/tmp", "openrouter/qwen/qwen-2.5-coder-32b-instruct"
                )

        self.assertEqual(asyncio.run(_run()), "EXTERNAL_OK")
        fake_adapter.execute_direct_api.assert_awaited()


class TestAgyAdapterNoGeminiFallback(unittest.TestCase):

    def test_non_native_model_does_not_fall_back_to_gemini(self):
        from engine_manager import EngineManager
        adapter = EngineManager.get_adapter("agy")
        self.assertIsNotNone(adapter)
        cmd = adapter.build_command(
            prompt="hi",
            workspace="/tmp",
            model="openrouter/qwen/qwen-2.5-coder-32b-instruct",
        )
        joined = " ".join(cmd).lower()
        self.assertNotIn("gemini", joined)
        self.assertIn("gpt-oss-120b-medium", joined)

    def test_native_gemini_model_is_preserved(self):
        from engine_manager import EngineManager
        adapter = EngineManager.get_adapter("agy")
        cmd = adapter.build_command(prompt="hi", workspace="/tmp", model="gemini-3.7-flash-middle")
        joined = " ".join(cmd).lower()
        self.assertIn("gemini-3.7-flash-middle", joined)


class TestTelemetryNoAgyCli(unittest.TestCase):

    def test_source_does_not_invoke_agy_usage(self):
        with open(os.path.join(WORKSPACE_ROOT, "telemetry_status_manager.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('"-p", "/usage"', src)
        self.assertNotIn("'agy', '-p', '/usage'", src)

    def test_fetch_usage_never_calls_agy(self):
        import telemetry_status_manager as tsm
        tsm._native_usage_cache = {"time": 0.0, "data": None}

        captured = []

        class _FakeCompleted:
            returncode = 0
            stdout = '{"groups": [{"name": "Gemini Models", "buckets": []}]}'
            stderr = ""

        def _fake_run(cmd, *args, **kwargs):
            captured.append(cmd)
            return _FakeCompleted()

        with patch.object(tsm.subprocess, "run", side_effect=_fake_run):
            result = tsm.fetch_native_antigravity_usage()

        self.assertIsInstance(result, dict)
        for cmd in captured:
            joined = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
            self.assertNotIn("agy", joined.lower().split()[0] if joined else "")
            self.assertNotIn("/usage", joined)


if __name__ == "__main__":
    unittest.main()
