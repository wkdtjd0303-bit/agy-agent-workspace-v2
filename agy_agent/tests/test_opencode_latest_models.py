#!/usr/bin/env python3
"""
Unit tests for OpenCode Router latest model catalog update.
Verifies that all newly added OpenCode Go models (Qwen 3.8 Flash/Max, GLM 5.3 Flash,
MiMo 2.6 Flash, GPT-6 Luna, LongCat 2.0, etc.) are properly registered,
resolved via smart aliases, and displayed in the !model command dropdowns.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from engine_manager import EngineManager, OpenCodeAdapter
from model_manager import (
    GlobalModelManager,
    ENGINE_PRESETS,
    get_engine_models,
    ModelPresetDropdown,
)


class TestOpenCodeLatestModels(unittest.TestCase):
    def setUp(self):
        GlobalModelManager.initialize()

    def test_opencode_adapter_supported_models_count_and_content(self):
        adapter = EngineManager.get_adapter("opencode")
        self.assertIsNotNone(adapter)
        models = adapter.supported_models

        # At least 35 models supported
        self.assertGreaterEqual(len(models), 35)

        # Critical new models must be present
        expected_new_models = [
            "opencode-go/glm-5.3-flash",
            "opencode-go/mimo-v2.6-flash",
            "opencode-go/qwen3.8-flash",
            "opencode-go/qwen3.8-max",
            "opencode-go/gpt-6-luna",
            "opencode-go/gpt-5.6-luna",
            "opencode-go/longcat-2.0",
            "opencode-go/space-bunny-free",
            "opencode-go/hy4-preview",
            "opencode-go/muse-spark-1.3-contributor",
            "opencode-go/qwen3.7-plus",
            "opencode-go/deepseek-v4.1-flash",
            "opencode-go/deepseek-v4-pro",
        ]
        for m in expected_new_models:
            self.assertIn(m, models, f"Model {m} must be in adapter.supported_models")

    def test_get_engine_models_opencode_labels(self):
        presets = get_engine_models("opencode")
        model_map = dict(presets)

        self.assertIn("opencode-go/glm-5.3-flash", model_map)
        self.assertIn("초고속", model_map["opencode-go/glm-5.3-flash"])

        self.assertIn("opencode-go/mimo-v2.6-flash", model_map)
        self.assertIn("초고속", model_map["opencode-go/mimo-v2.6-flash"])

        self.assertIn("opencode-go/qwen3.8-flash", model_map)
        self.assertIn("Qwen", model_map["opencode-go/qwen3.8-flash"])

        self.assertIn("opencode-go/gpt-6-luna", model_map)
        self.assertIn("GPT-6", model_map["opencode-go/gpt-6-luna"])

    def test_resolve_model_id_smart_aliases_for_opencode(self):
        cases = [
            # Exact slugs without prefix
            ("glm-5.3-flash", "opencode-go/glm-5.3-flash"),
            ("qwen3.8-flash", "opencode-go/qwen3.8-flash"),
            ("qwen3.8-max", "opencode-go/qwen3.8-max"),
            ("mimo-v2.6-flash", "opencode-go/mimo-v2.6-flash"),
            ("gpt-6-luna", "opencode-go/gpt-6-luna"),
            ("longcat-2.0", "opencode-go/longcat-2.0"),
            ("space-bunny-free", "opencode-go/space-bunny-free"),

            # Fuzzy alias resolution
            ("glm-flash", "opencode-go/glm-5.3-flash"),
            ("glm", "opencode-go/glm-5.3-flash"),
            ("qwen", "opencode-go/qwen3.8-flash"),
            ("mimo-flash", "opencode-go/mimo-v2.6-flash"),
            ("mimo-pro", "opencode-go/mimo-v2.6-pro"),
            ("mimo", "opencode-go/mimo-v2.6-flash"),
            ("luna", "opencode-go/gpt-6-luna"),
            ("deepseek-pro", "opencode-go/deepseek-v4-pro"),
            ("deepseek", "opencode-go/deepseek-v4.1-flash"),
            ("kimi-code", "opencode-go/kimi-k2.7-code"),
            ("kimi", "opencode-go/kimi-k3"),
            ("longcat", "opencode-go/longcat-2.0"),
            ("bunny", "opencode-go/space-bunny-free"),
            ("hy4", "opencode-go/hy4-preview"),
        ]

        for user_input, expected in cases:
            resolved = GlobalModelManager.resolve_model_id("opencode", user_input)
            self.assertEqual(
                resolved,
                expected,
                f"Input '{user_input}' expected '{expected}' but got '{resolved}'",
            )

    def test_set_role_model_auto_harmonization(self):
        # Setting an opencode-go model automatically harmonizes role engine to opencode
        GlobalModelManager.set_role_model("coder", "qwen3.8-flash")
        cfg = GlobalModelManager.get_role_config("coder")
        self.assertEqual(cfg["engine"], "opencode")
        self.assertEqual(cfg["model"], "opencode-go/qwen3.8-flash")

        # Setting another new opencode model
        GlobalModelManager.set_role_model("coder", "glm-5.3-flash")
        cfg = GlobalModelManager.get_role_config("coder")
        self.assertEqual(cfg["engine"], "opencode")
        self.assertEqual(cfg["model"], "opencode-go/glm-5.3-flash")

    def test_model_preset_dropdown_options(self):
        GlobalModelManager.set_role_engine("coder", "opencode")
        dropdown = ModelPresetDropdown("coder")
        self.assertLessEqual(len(dropdown.options), 25)
        self.assertGreaterEqual(len(dropdown.options), 10)

        # Make sure options have non-truncated labels
        for opt in dropdown.options:
            self.assertGreater(len(opt.label), 0)
            self.assertLessEqual(len(opt.label), 100)

    def test_format_model_name_for_new_opencode_models(self):
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/qwen3.8-flash"), "Qwen 3.8 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/qwen3.8-max"), "Qwen 3.8 Max")
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/glm-5.3-flash"), "GLM 5.3 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/mimo-v2.6-flash"), "MiMo 2.6 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/gpt-6-luna"), "GPT-6 Luna")
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/longcat-2.0"), "LongCat 2.0")


if __name__ == "__main__":
    unittest.main()
