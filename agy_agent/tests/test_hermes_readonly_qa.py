#!/usr/bin/env python3
"""
Unit and integration tests verifying Hermes Agent Read-Only QA setup:
- Zero workspace write access (write_file, patch, terminal excluded from toolset)
- Default engine configured as 'hermes' for all OpenRouter QA bots (no manual !command required)
- Verification that Hermes CLI executes with '-t readonly' for QA roles
"""

import os
import sys
import unittest
import subprocess
from unittest.mock import MagicMock, patch, AsyncMock

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

import config
from engine_manager import EngineManager, HermesAdapter, OpenRouterAdapter
from session_manager import ChannelSession


class TestHermesReadOnlyQA(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = PARENT_DIR
        try:
            from model_manager import GlobalModelManager
            GlobalModelManager.set_all_qa_enabled(True)
        except Exception:
            pass

    def test_default_extended_qa_engine_is_hermes(self):
        """Verify all OpenRouter extended QA bots default to 'hermes' without requiring user !commands."""
        self.assertEqual(config.QA2_ENGINE, "hermes")
        configs = config.get_extended_qa_configs()
        self.assertTrue(len(configs) >= 5, "Should have QA2..QA6 configured")
        for cfg in configs:
            model_lower = cfg.get("model", "").lower()
            # Skip native AGY platform models (they use 'agy' engine per protocol)
            native_keywords = ("sonnet", "gemini", "gpt-oss", "claude", "opus")
            if any(kw in model_lower for kw in native_keywords):
                continue
            self.assertIn(
                cfg.get("engine"),
                ("hermes", "opencode", "openrouter"),
                f"{cfg['name']} should default to a non-agy external engine"
            )

    def test_hermes_adapter_qa_command_has_readonly_toolset(self):
        """Verify HermesAdapter automatically injects '-t readonly' when role is QA reviewer."""
        hermes = HermesAdapter()
        # 1. QA2 role -> MUST have -t readonly
        cmd_qa2 = hermes.build_command("review diff", self.workspace, role="qa2")
        self.assertIn("-t", cmd_qa2)
        idx = cmd_qa2.index("-t")
        self.assertEqual(cmd_qa2[idx + 1], "readonly")

        # 2. General QA role -> MUST have -t readonly
        cmd_qa = hermes.build_command("review diff", self.workspace, role="qa")
        self.assertIn("-t", cmd_qa)
        idx_qa = cmd_qa.index("-t")
        self.assertEqual(cmd_qa[idx_qa + 1], "readonly")

        # 3. Explicit read_only flag -> MUST have -t readonly
        cmd_ro = hermes.build_command("inspect files", self.workspace, read_only=True)
        self.assertIn("-t", cmd_ro)
        idx_ro = cmd_ro.index("-t")
        self.assertEqual(cmd_ro[idx_ro + 1], "readonly")

        # 4. Coder role -> MUST NOT have -t readonly (coder must be able to edit files)
        cmd_coder = hermes.build_command("write feature", self.workspace, role="coder")
        if "-t" in cmd_coder:
            idx_c = cmd_coder.index("-t")
            self.assertNotEqual(cmd_coder[idx_c + 1], "readonly")

    def test_openrouter_adapter_qa_command_has_readonly_toolset(self):
        """Verify OpenRouterAdapter command also automatically injects '-t readonly' for QA roles."""
        or_adapter = OpenRouterAdapter()
        cmd_qa = or_adapter.build_command("review diff", self.workspace, role="qa2")
        if "hermes" in (cmd_qa[0] or ""):
            self.assertIn("-t", cmd_qa)
            idx = cmd_qa.index("-t")
            self.assertEqual(cmd_qa[idx + 1], "readonly")

    def test_readonly_toolset_schema_guarantees_zero_write_tools(self):
        """Verify the 'readonly' toolset physically contains zero write/execution tools."""
        sys.path.insert(0, "/home/jangsung/.hermes/hermes-agent")
        try:
            from toolsets import resolve_toolset
            tools = resolve_toolset("readonly")
            # Must contain read tools
            self.assertIn("read_file", tools)
            self.assertIn("search_files", tools)
            # Strictly must NOT contain write or execution tools
            self.assertNotIn("write_file", tools)
            self.assertNotIn("patch", tools)
            self.assertNotIn("terminal", tools)
            self.assertNotIn("process_manage", tools)
            self.assertNotIn("execute_code", tools)
        finally:
            if "/home/jangsung/.hermes/hermes-agent" in sys.path:
                sys.path.remove("/home/jangsung/.hermes/hermes-agent")

    async def test_openrouter_adapter_executes_hermes_cli_with_readonly(self):
        """Verify OpenRouterAdapter.execute dispatches directly to Hermes CLI in readonly mode for QA."""
        or_adapter = OpenRouterAdapter()
        session = ChannelSession(channel_id=12345, workspace=self.workspace, role="qa2")

        with patch.object(HermesAdapter, "execute", new_callable=AsyncMock) as mock_super_exec:
            mock_super_exec.return_value = (0, "QA review completed via Hermes readonly", [])
            code, resp, errors = await or_adapter.execute(
                session=session,
                prompt="Review PR diff",
                model="upstage/solar-pro4"
            )
            self.assertEqual(code, 0)
            self.assertIn("QA review completed via Hermes readonly", resp)
            mock_super_exec.assert_called_once()
            call_kwargs = mock_super_exec.call_args[1]
            self.assertEqual(call_kwargs.get("role"), "qa2")
            self.assertTrue(call_kwargs.get("read_only"), "QA role must have read_only=True")

    def test_physical_write_blocking_in_readonly_mode(self):
        """Physical test: verify Hermes CLI in readonly mode cannot create files."""
        target_file = "/tmp/test_hermes_strict_qa_write_block.txt"
        if os.path.exists(target_file):
            os.remove(target_file)

        # Attempt to make Hermes write a file using -t readonly
        cmd = [
            "/home/jangsung/.local/bin/hermes",
            "chat",
            "-t", "readonly",
            "-q", f"Create file {target_file} with content 'illegal write'",
            "--oneshot",
            "-Q",
            "--yolo",
            "--in", self.workspace
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10.0)
            output = proc.stdout + proc.stderr
            # 2. Output should indicate tool absence or refusal
            self.assertTrue(
                "write_file" not in output or "don't have" in output.lower() or "can't" in output.lower() or "read_file" in output,
                f"Unexpected output: {output}"
            )
        except subprocess.TimeoutExpired:
            pass

        # 1. File must NOT exist
        self.assertFalse(
            os.path.exists(target_file),
            f"Security breach: {target_file} was created under -t readonly!"
        )


if __name__ == "__main__":
    unittest.main()
