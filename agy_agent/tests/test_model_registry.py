#!/usr/bin/env python3
"""
Unit tests for AntigravityModelRegistry (model_registry.py).
Ensures dynamic Antigravity /model parsing, autocomplete-only model filtering,
deprecated model auto-upgrades, and role-based resolution.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from model_registry import AntigravityModelRegistry, DEPRECATED_MIGRATION_MAP


class TestAntigravityModelRegistry(unittest.TestCase):

    def setUp(self):
        AntigravityModelRegistry._cached_models = []
        AntigravityModelRegistry._last_fetch_time = 0.0

    def test_deprecated_model_migration_map(self):
        """Ensures all known deprecated models have valid 3.x upgrade targets."""
        for old_model, target_model in DEPRECATED_MIGRATION_MAP.items():
            self.assertTrue(
                target_model.startswith("gemini-3"),
                f"Deprecated model {old_model} must map to Gemini 3.x, got: {target_model}"
            )

    def test_autocomplete_only_models_filtered_out(self):
        """Ensures isAutocompleteOnly=True models (like gemini-2.5-pro) are excluded from selectable models."""
        mock_models = [
            {"label": "Gemini 2.5 Pro", "modelId": "gemini-2.5-pro", "isAutocompleteOnly": True},
            {"label": "Gemini 3.5 Flash Lite", "modelId": "gemini-2.5-flash", "isAutocompleteOnly": True},
            {"label": "Gemini 3.8 Flash", "modelId": "gemini-3.8-flash-high", "isAutocompleteOnly": False},
            {"label": "Gemini 3.1 Pro (High)", "modelId": "gemini-3.1-pro-high", "isAutocompleteOnly": False},
            {"label": "Gemini 3 Flash", "modelId": "gemini-3-flash", "isAutocompleteOnly": False},
        ]
        with patch.object(AntigravityModelRegistry, "fetch_models", return_value=mock_models):
            selectable = AntigravityModelRegistry.get_selectable_models()
            selectable_ids = [m["modelId"] for m in selectable]

            self.assertNotIn("gemini-2.5-pro", selectable_ids)
            self.assertNotIn("gemini-2.5-flash", selectable_ids)
            self.assertIn("gemini-3.8-flash-high", selectable_ids)
            self.assertIn("gemini-3.1-pro-high", selectable_ids)
            self.assertIn("gemini-3-flash", selectable_ids)

    def test_resolve_deprecated_model_auto_upgrade(self):
        """Tests that requesting gemini-2.5-flash or gemini-2.5-pro automatically resolves to Gemini 3.x."""
        resolved_flash = AntigravityModelRegistry.resolve_model("planner", "gemini-2.5-flash")
        self.assertEqual(resolved_flash, "gemini-3.8-flash-high")

        resolved_lite = AntigravityModelRegistry.resolve_model("planner", "gemini-2.5-flash-lite")
        self.assertEqual(resolved_lite, "gemini-3-flash")

        resolved_pro = AntigravityModelRegistry.resolve_model("qa", "gemini-2.5-pro")
        self.assertEqual(resolved_pro, "gemini-3.1-pro-high")

    def test_resolve_role_defaults(self):
        """Tests that omitting preferred model resolves to Antigravity 3.x official models per role."""
        coder_m = AntigravityModelRegistry.resolve_model("coder")
        self.assertTrue(coder_m.startswith("gemini-3"))

        qa_m = AntigravityModelRegistry.resolve_model("qa")
        self.assertTrue(qa_m.startswith("gemini-3"))

        vision_m = AntigravityModelRegistry.resolve_model("vision")
        self.assertTrue(vision_m.startswith("gemini-3"))

        planner_m = AntigravityModelRegistry.resolve_model("planner")
        self.assertTrue(planner_m.startswith("gemini-3"))

    def test_is_antigravity_model_recognition(self):
        """Tests model validation against Antigravity catalog."""
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("gemini-3.8-flash-high"))
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("gemini-3.1-pro-high"))
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("gpt-oss-120b-medium"))
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("claude-sonnet-4-6"))
        self.assertTrue(AntigravityModelRegistry.is_antigravity_model("claude-opus-4-6-thinking"))
        # External OpenRouter models must be strictly rejected
        self.assertFalse(AntigravityModelRegistry.is_antigravity_model("openrouter/deepseek/deepseek-chat"))
        self.assertFalse(AntigravityModelRegistry.is_antigravity_model("upstage/solar-pro4"))
        self.assertFalse(AntigravityModelRegistry.is_antigravity_model("qwen/qwen3-235b-a22b-2507"))

    def test_fetch_agy_cli_models_parses_live_output(self):
        """Tests parsing of 'agy models' tab-separated CLI output."""
        mock_output = (
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
            "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = mock_output
            mock_run.return_value = mock_proc

            models = AntigravityModelRegistry.fetch_agy_cli_models()
            self.assertEqual(len(models), 3)
            ids = [m["modelId"] for m in models]
            self.assertIn("gemini-3.8-flash-high", ids)
            self.assertIn("claude-sonnet-4-6", ids)
            self.assertIn("gpt-oss-120b-medium", ids)
            self.assertEqual(models[0]["source"], "agy_models_cli")
            self.assertEqual(models[0]["category"], "gemini")
            self.assertEqual(models[1]["category"], "claude")
            self.assertEqual(models[2]["category"], "gpt-oss")

    def test_get_engine_models_prioritizes_live_catalog(self):
        """Tests that model_manager.get_engine_models('agy') prioritizes live /model entries."""
        import model_manager
        models = model_manager.get_engine_models("agy")
        self.assertTrue(len(models) >= 14, f"Expected at least 14 models, got {len(models)}")
        ids = [m[0] for m in models]
        self.assertIn("gpt-oss-120b-medium", ids)
        self.assertIn("claude-sonnet-4-6", ids)
        self.assertIn("claude-opus-4-6-thinking", ids)
        self.assertIn("gemini-3.8-flash-high", ids)

    def test_catalog_summary_text_generation(self):
        """Tests catalog summary output formatting."""
        summary = AntigravityModelRegistry.get_catalog_summary_text()
        self.assertIn("Google Antigravity 공식 지원 모델 카탈로그", summary)
        self.assertIn("Gemini 3", summary)


if __name__ == "__main__":
    unittest.main()
