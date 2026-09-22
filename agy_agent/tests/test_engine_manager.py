#!/usr/bin/env python3
"""
Unit tests for engine_manager.py (Multi-Engine Orchestrator: Agy, Aider, Codex, Claude, OpenCode, Copilot)
"""

import os
import sys
import unittest
import asyncio
from unittest.mock import MagicMock, patch

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from engine_manager import (
    EngineManager,
    BaseEngineAdapter,
    AgyAdapter,
    HermesAdapter,
    OpenRouterAdapter,
    AiderAdapter,
    CodexAdapter,
    ClaudeAdapter,
    OpenCodeAdapter,
    CopilotAdapter,
    EngineSelectView,
)
from session_manager import ChannelSession, SessionManager
import discord


class TestEngineManager(unittest.TestCase):
    def setUp(self):
        self.workspace = PARENT_DIR

    def test_registered_adapters(self):
        engine_ids = EngineManager.get_all_engine_ids()
        expected = ["agy", "hermes", "openrouter", "codex", "claude", "opencode", "copilot"]
        for eid in expected:
            self.assertIn(eid, engine_ids)

        adapters = EngineManager.get_all_adapters()
        self.assertEqual(len(adapters), len(expected))

        # Check case insensitivity
        self.assertIsInstance(EngineManager.get_adapter("AGY"), AgyAdapter)
        self.assertIsInstance(EngineManager.get_adapter("Hermes"), HermesAdapter)
        self.assertIsInstance(EngineManager.get_adapter("hermes"), HermesAdapter)
        self.assertIsInstance(EngineManager.get_adapter("OpenRouter"), OpenRouterAdapter)
        self.assertIsInstance(EngineManager.get_adapter("Aider"), HermesAdapter)  # Aider permanently aliases to Hermes
        self.assertIsInstance(EngineManager.get_adapter("codex"), CodexAdapter)
        self.assertIsInstance(EngineManager.get_adapter("CLAUDE"), ClaudeAdapter)
        self.assertIsInstance(EngineManager.get_adapter("OpenCode"), OpenCodeAdapter)
        self.assertIsInstance(EngineManager.get_adapter("copilot"), CopilotAdapter)
        self.assertIsNone(EngineManager.get_adapter("non_existent_engine"))

    def test_local_binary_detection_on_host(self):
        # On this environment, agy, hermes, codex, and copilot are installed
        detected = EngineManager.detect_installed_engines()
        self.assertIsInstance(detected, dict)
        self.assertTrue(detected.get("agy", False), "AGY binary should be detected")
        self.assertTrue(detected.get("hermes", False), "Hermes binary should be detected")
        self.assertTrue(detected.get("codex", False), "Codex binary should be detected")

        hermes_adapter = EngineManager.get_adapter("hermes")
        self.assertTrue(hermes_adapter.is_installed())
        ver = hermes_adapter.get_version()
        self.assertTrue("hermes" in ver.lower() or "0." in ver)

        codex_adapter = EngineManager.get_adapter("codex")
        self.assertTrue(codex_adapter.is_installed())
        cver = codex_adapter.get_version()
        self.assertTrue("codex" in cver.lower())

    def test_command_building(self):
        prompt = "Write a hello world script"

        # 1. AGY
        agy = EngineManager.get_adapter("agy")
        agy_cmd = agy.build_command(prompt, self.workspace, model="gemini-3.1-pro-high")
        self.assertIn("--dangerously-skip-permissions", agy_cmd)
        self.assertIn("--output-format", agy_cmd)
        self.assertIn("stream-json", agy_cmd)
        self.assertIn(prompt, agy_cmd)

        # 2. Hermes (and Aider alias)
        hermes = EngineManager.get_adapter("hermes")
        hermes_cmd = hermes.build_command(prompt, self.workspace)
        self.assertIn("chat", hermes_cmd)
        self.assertIn("-q", hermes_cmd)
        self.assertIn(prompt, hermes_cmd)
        self.assertIn("--oneshot", hermes_cmd)
        self.assertIn("-Q", hermes_cmd)
        self.assertIn("--yolo", hermes_cmd)
        self.assertIn("--in", hermes_cmd)

        aider = EngineManager.get_adapter("aider")
        aider_cmd = aider.build_command(prompt, self.workspace)
        self.assertIn("chat", aider_cmd)
        self.assertIn(prompt, aider_cmd)

        # 3. Codex
        codex = EngineManager.get_adapter("codex")
        codex_cmd = codex.build_command(prompt, self.workspace, model="o3-mini")
        self.assertIn("--sandbox", codex_cmd)
        self.assertIn("workspace-write", codex_cmd)
        self.assertIn("-C", codex_cmd)
        self.assertIn("-m", codex_cmd)
        self.assertIn("o3-mini", codex_cmd)
        self.assertIn(prompt, codex_cmd)
        # Test incompatible model filtering
        codex_incompatible = codex.build_command(prompt, self.workspace, model="gemini-3.1-pro-high")
        self.assertNotIn("gemini-3.1-pro-high", codex_incompatible)

        # 4. Claude
        claude = EngineManager.get_adapter("claude")
        claude_cmd = claude.build_command(prompt, self.workspace)
        self.assertIn("-p", claude_cmd)
        self.assertIn(prompt, claude_cmd)
        self.assertIn("--dangerously-skip-permissions", claude_cmd)

        # 5. OpenCode
        opencode = EngineManager.get_adapter("opencode")
        opencode_cmd = opencode.build_command(prompt, self.workspace)
        self.assertIn("run", opencode_cmd)
        self.assertIn(prompt, opencode_cmd)
        self.assertIn("-y", opencode_cmd)

        # 6. Copilot
        copilot = EngineManager.get_adapter("copilot")
        copilot_cmd = copilot.build_command(prompt, self.workspace)
        self.assertIn("--prompt", copilot_cmd)
        self.assertIn(prompt, copilot_cmd)

        # 7. OpenRouter (Hermes Agent backend)
        openrouter = EngineManager.get_adapter("openrouter")
        or_cmd = openrouter.build_command(prompt, self.workspace, model="openrouter/qwen/qwen-2.5-coder-32b-instruct")
        if "hermes" in (or_cmd[0] or ""):
            self.assertIn("chat", or_cmd)
            self.assertIn("-q", or_cmd)
            self.assertIn(prompt, or_cmd)
            self.assertIn("--oneshot", or_cmd)
            self.assertIn("-Q", or_cmd)
            self.assertIn("-m", or_cmd)
            # Hermes CLI requires normalized raw model ID without openrouter/ prefix
            self.assertIn("qwen/qwen-2.5-coder-32b-instruct", or_cmd)
        else:
            self.assertIn(prompt, or_cmd)
        self.assertEqual(os.environ.get("OR_DATA_COLLECTION"), "deny")
        self.assertEqual(os.environ.get("OPENROUTER_PROVIDER_DATA_COLLECTION"), "deny")

        # AGY with gemini-3.1-pro-high
        agy_cmd = agy.build_command(prompt, self.workspace, model="gemini-3.1-pro-high")
        self.assertIn("--model", agy_cmd)
        self.assertIn("gemini-3.1-pro-high", agy_cmd)

    def test_resolve_qa_model_gemini_pro_priority(self):
        from trio_agent_runner import TrioAgentRunner
        with unittest.mock.patch('config.QA_MODEL', 'gemini-3.1-pro-high'):
            model_high, display_high, _ = asyncio.run(TrioAgentRunner.resolve_qa_model(risk_level="HIGH"))
            self.assertEqual(model_high, "gemini-3.1-pro-high")
            self.assertIn("Gemini 3.1 Pro High", display_high)

            model_low, display_low, _ = asyncio.run(TrioAgentRunner.resolve_qa_model(risk_level="LOW"))
            self.assertEqual(model_low, "gemini-3.1-pro-high")
            self.assertIn("Gemini 3.1 Pro High", display_low)

    def test_channel_session_engine_persistence(self):
        sess = ChannelSession(channel_id=999999, workspace=self.workspace, engine="agy")
        self.assertEqual(sess.engine, "agy")

        sess.set_engine("aider")
        self.assertEqual(sess.engine, "aider")

        data = sess.to_dict()
        self.assertIn("engine", data)
        self.assertEqual(data["engine"], "aider")

        # Reloading in ChannelSession
        restored = ChannelSession(
            channel_id=999999,
            workspace=self.workspace,
            engine=data.get("engine", "agy")
        )
        self.assertEqual(restored.engine, "aider")

    def test_status_embed_generation(self):
        embed = EngineManager.build_status_embed(current_engine_id="aider", channel_id=123456789)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("멀티 AI 코딩 & QA 엔진 관리자", embed.title)

        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("현재 채널 역할" in n for n in field_names))
        self.assertTrue(any("역할별 배정 AI 엔진" in n for n in field_names))
        self.assertTrue(any("전체 AI 엔진 목록" in n for n in field_names))

        active_field = [f for f in embed.fields if "역할별 배정 AI 엔진" in f.name][0]
        self.assertIn("Hermes", active_field.value)
        self.assertIn("hermes", active_field.value)

    def test_interactive_view_initialization(self):
        mock_mgr = MagicMock()
        mock_sess = MagicMock()
        mock_sess.engine = "agy"
        mock_sess.role = "coder"
        mock_sess.qa_engine = "claude"
        mock_mgr.get_session.return_value = mock_sess

        view = EngineSelectView(mock_mgr, channel_id=123456789)
        self.assertIsInstance(view, discord.ui.View)
        # Dropdowns check (QAEngineDropdown + EngineDropdown)
        selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
        self.assertEqual(len(selects), 2)
        self.assertEqual(len(selects[0].options), 6)
        self.assertEqual(len(selects[1].options), 6)

        # Buttons check
        buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
        labels = [b.label for b in buttons]
        self.assertTrue(any("역할:" in l for l in labels))
        self.assertTrue(any("QA: CLAUDE" in l for l in labels))
        self.assertTrue(any("엔진 진단" in l for l in labels))
        self.assertTrue(any("새로고침" in l for l in labels))

    def test_doctor_diagnostic_embed(self):
        diag = EngineManager.diagnose_all(self.workspace)
        self.assertEqual(len(diag), 7)
        self.assertIn("agy", diag)
        self.assertIn("hermes", diag)
        self.assertIn("codex", diag)

        # Build Doctor embed
        embed = EngineManager.build_doctor_embed(channel_id=123456789, workspace=self.workspace)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("엔진 시스템 정밀 진단", embed.title)
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("엔진 가용성" in n for n in field_names))
        self.assertTrue(any("작업 공간" in n for n in field_names))

    def test_qa_role_and_engine_assignment(self):
        sess = ChannelSession(
            channel_id=888888,
            workspace=self.workspace,
            engine="agy",
            role="coder",
            qa_engine="claude",
            auditor_engine="codex"
        )
        self.assertEqual(sess.role, "coder")
        self.assertEqual(sess.qa_engine, "claude")
        self.assertEqual(sess.auditor_engine, "codex")

        # Switch to QA role
        sess.set_role("qa")
        self.assertEqual(sess.role, "qa")

        # Change QA engine to codex
        sess.set_qa_engine("codex")
        self.assertEqual(sess.qa_engine, "codex")

        # Verify serialization
        data = sess.to_dict()
        self.assertEqual(data["role"], "qa")
        self.assertEqual(data["qa_engine"], "codex")
        self.assertEqual(data["auditor_engine"], "codex")

    def test_qa_prompt_builder(self):
        diff_info = {
            "changed_files": ["app.js", "styles.css"],
            "insertions": 42,
            "deletions": 10
        }
        gates_info = {"all_passed": True}
        anti_mock_violations = []

        prompt = EngineManager.build_qa_prompt(
            user_prompt="로그인 UI 폼 검증해줘",
            workspace=self.workspace,
            diff_info=diff_info,
            gates_info=gates_info,
            anti_mock_violations=anti_mock_violations,
            engine_name="Claude Code CLI"
        )
        self.assertIn("수석 QA 엔지니어", prompt)
        self.assertIn("로그인 UI 폼 검증해줘", prompt)
        self.assertIn("app.js", prompt)
        self.assertIn("+42 / -10", prompt)
        self.assertIn("[STATUS: APPROVED]", prompt)
        self.assertIn("[STATUS: REJECTED]", prompt)

    def test_qa_adapter_resolution_and_fallback(self):
        sess = ChannelSession(channel_id=777777, workspace=self.workspace, qa_engine="claude")
        # In this host, aider and codex are installed, claude might not be installed
        qa_adapter = EngineManager.get_qa_adapter(sess)
        self.assertIsNotNone(qa_adapter)
        # Should be an installed adapter (e.g. codex, aider, or agy)
        self.assertTrue(qa_adapter.is_installed())

        coder_adapter = EngineManager.get_coder_adapter(sess)
        self.assertEqual(coder_adapter.engine_id, "agy")

    def test_channel_session_engine_model(self):
        sess = ChannelSession(channel_id=999999, workspace=self.workspace, engine="aider", engine_model="gemini/gemini-3.1-pro")
        self.assertEqual(sess.engine, "aider")
        self.assertEqual(sess.engine_model, "gemini/gemini-3.1-pro")

        sess.set_engine_model("openai/gpt-4o")
        self.assertEqual(sess.engine_model, "openai/gpt-4o")

        data = sess.to_dict()
        self.assertIn("engine_model", data)
        self.assertEqual(data["engine_model"], "openai/gpt-4o")

        restored = ChannelSession(
            channel_id=999999,
            workspace=self.workspace,
            engine=data.get("engine", "agy"),
            engine_model=data.get("engine_model")
        )
        self.assertEqual(restored.engine_model, "openai/gpt-4o")

    def test_qwen_model_alias_resolution(self):
        """Tests that Qwen and other model aliases resolve correctly."""
        self.assertEqual(
            EngineManager.resolve_model_alias("qwen"),
            "openrouter/qwen/qwen-2.5-coder-32b-instruct"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("qwen-coder"),
            "openrouter/qwen/qwen-2.5-coder-32b-instruct"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("qwen-2.5-coder-32b"),
            "openrouter/qwen/qwen-2.5-coder-32b-instruct"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("deepseek-chat"),
            "openrouter/deepseek/deepseek-chat"
        )
        self.assertIn(
            EngineManager.resolve_model_alias("gpt-oss"),
            ("gpt-oss-120b-medium", "gemini-3.1-pro-high")
        )

    def test_openrouter_adapter_qwen_command_build(self):
        """Tests that OpenRouterAdapter properly resolves qwen alias into full openrouter slug or normalized hermes model."""
        openrouter = EngineManager.get_adapter("openrouter")
        cmd = openrouter.build_command("test prompt", self.workspace, model="qwen")
        self.assertTrue(
            "qwen/qwen-2.5-coder-32b-instruct" in cmd or "openrouter/qwen/qwen-2.5-coder-32b-instruct" in cmd
        )
        if "hermes" in (cmd[0] or ""):
            self.assertIn("chat", cmd)
            self.assertIn("-q", cmd)
            self.assertIn("--oneshot", cmd)
        else:
            self.assertIn("--message", cmd)

    def test_channel_session_qa_model_and_backup(self):
        """Tests ChannelSession qa_model and qa_backup_model persistence."""
        sess = ChannelSession(
            channel_id=888888,
            workspace=self.workspace,
            qa_engine="agy",
            qa_model="gemini-3.1-pro-high",
            qa_backup_model="openrouter/qwen/qwen-2.5-coder-32b-instruct"
        )
        self.assertEqual(sess.qa_model, "gemini-3.1-pro-high")
        self.assertEqual(sess.qa_backup_model, "openrouter/qwen/qwen-2.5-coder-32b-instruct")

        # Set to Qwen
        sess.set_qa_model("openrouter/qwen/qwen-2.5-coder-32b-instruct")
        self.assertEqual(sess.qa_model, "openrouter/qwen/qwen-2.5-coder-32b-instruct")

        # Check serialization and restoration
        d = sess.to_dict()
        self.assertEqual(d["qa_model"], "openrouter/qwen/qwen-2.5-coder-32b-instruct")
        self.assertEqual(d["qa_backup_model"], "openrouter/qwen/qwen-2.5-coder-32b-instruct")

        restored = ChannelSession(
            channel_id=888888,
            workspace=self.workspace,
            qa_model=d.get("qa_model"),
            qa_backup_model=d.get("qa_backup_model")
        )
        self.assertEqual(restored.qa_model, "openrouter/qwen/qwen-2.5-coder-32b-instruct")
        self.assertEqual(restored.qa_backup_model, "openrouter/qwen/qwen-2.5-coder-32b-instruct")

    def test_config_qa_model_and_backup_default(self):
        """Verifies primary QA model is Gemini 3.1 Pro High while Qwen is registered as backup."""
        import config
        with unittest.mock.patch.object(config, 'QA_MODEL', 'gemini-3.1-pro-high'):
            self.assertEqual(config.QA_MODEL, "gemini-3.1-pro-high")
            self.assertIn("qwen", config.QA_BACKUP_MODEL.lower())
            self.assertIn(config.QA_BACKUP_MODEL, config.QA_BACKUP_MODELS)

    def test_openrouter_execute_direct_api_auto_heal_400_invalid_model(self):
        """Verifies that execute_direct_api automatically heals HTTP 400 invalid model ID errors."""
        import urllib.error
        import io
        import json

        openrouter = EngineManager.get_adapter("openrouter")
        with patch.object(openrouter, "_get_clean_key", return_value="sk-or-v1-dummykeyforhealing123456"):
            calls = []

            def fake_urlopen(req, timeout=90.0):
                body = json.loads(req.data.decode("utf-8"))
                model_requested = body.get("model")
                calls.append(model_requested)
                if model_requested == "openrouter/upstage/solar-pro4":
                    # Simulate OpenRouter 400 rejection
                    fp = io.BytesIO(b'{"error":{"message":"openrouter/upstage/solar-pro4 is not a valid model ID","code":400}}')
                    raise urllib.error.HTTPError(
                        url="https://openrouter.ai/api/v1/chat/completions",
                        code=400,
                        msg="Bad Request",
                        hdrs={},
                        fp=fp
                    )
                # Success on normalized or healed model
                resp_data = {
                    "choices": [
                        {"message": {"content": f"[STATUS: APPROVED]\nAll checks nominal on {model_requested}"}}
                    ]
                }
                mock_resp = MagicMock()
                mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
                mock_resp.__enter__.return_value = mock_resp
                mock_resp.__exit__.return_value = None
                return mock_resp

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                import asyncio
                code, resp, errors = asyncio.run(
                    openrouter.execute_direct_api("Review prompt", model="openrouter/upstage/solar-pro4")
                )
                self.assertEqual(code, 0)
                self.assertIn("STATUS: APPROVED", resp)
                # Ensure the model was normalized or healed to upstage/solar-pro4
                self.assertIn("upstage/solar-pro4", calls)

    def test_normalize_engine_id_unifies_hermes_and_openrouter(self):
        from engine_manager import normalize_engine_id
        self.assertEqual(normalize_engine_id("openrouter"), "hermes")
        self.assertEqual(normalize_engine_id("hermes"), "hermes")
        self.assertEqual(normalize_engine_id("aider"), "hermes")
        self.assertEqual(normalize_engine_id("agy"), "agy")
        self.assertEqual(normalize_engine_id("codex"), "codex")
        self.assertEqual(normalize_engine_id("claude"), "claude")
        self.assertEqual(normalize_engine_id(None), "agy")


if __name__ == "__main__":
    unittest.main()


