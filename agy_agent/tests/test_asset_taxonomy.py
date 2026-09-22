#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for Visual Asset Taxonomy & Modular Generation Manager
================================================================
"""

import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_taxonomy_manager import (
    AssetGranularity,
    AssetDomain,
    classify_asset_request,
    optimize_prompt_for_taxonomy
)


class TestAssetTaxonomyManager(unittest.TestCase):

    def test_classify_joystick_as_modular_part(self):
        spec = classify_asset_request("joystick")
        self.assertEqual(AssetGranularity.MODULAR_PART, spec["granularity"])
        self.assertTrue(spec["isolated"])
        self.assertEqual("1:1", spec["recommended_aspect_ratio"])

    def test_classify_korean_joystick_keyword(self):
        spec = classify_asset_request("가상 조이스틱 버튼")
        self.assertEqual(AssetGranularity.MODULAR_PART, spec["granularity"])
        self.assertTrue(spec["isolated"])

    def test_classify_splash_as_full_scene(self):
        spec = classify_asset_request("splash_screen")
        self.assertEqual(AssetGranularity.FULL_SCENE, spec["granularity"])
        self.assertFalse(spec["isolated"])
        self.assertEqual("9:16", spec["recommended_aspect_ratio"])

    def test_classify_game_background_as_full_scene(self):
        spec = classify_asset_request("우주 배경 일러스트")
        self.assertEqual(AssetGranularity.FULL_SCENE, spec["granularity"])
        self.assertFalse(spec["isolated"])

    def test_optimize_prompt_modular_part(self):
        prompt = "Pixel art player sword"
        optimized = optimize_prompt_for_taxonomy(prompt, "game_item")
        self.assertIn("isolated single object", optimized)
        self.assertIn("solid neutral background", optimized)

    def test_optimize_prompt_full_scene_no_isolated_suffix(self):
        prompt = "Cyberpunk city night view"
        optimized = optimize_prompt_for_taxonomy(prompt, "game_background")
        self.assertEqual("Cyberpunk city night view", optimized)


if __name__ == "__main__":
    unittest.main()
