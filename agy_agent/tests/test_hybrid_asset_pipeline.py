#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from hybrid_asset_pipeline import HybridAssetPipeline, AssetPlacementResult
from asset_taxonomy_manager import AssetGranularity


class TestHybridAssetPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_hybrid_asset_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_detect_project_platform_and_resolve_directory(self):
        """Verify Android and Web platform detection and automatic directory resolution."""
        # 1. Android workspace setup
        android_dir = os.path.join(self.test_dir, "android_ws")
        os.makedirs(os.path.join(android_dir, "app", "src", "main"), exist_ok=True)
        with open(os.path.join(android_dir, "build.gradle.kts"), "w") as f:
            f.write("// Android Gradle")

        plat = HybridAssetPipeline.detect_project_platform(android_dir)
        self.assertEqual(plat, "android")

        target_dir = HybridAssetPipeline.resolve_resource_directory(android_dir)
        self.assertTrue(os.path.isdir(target_dir))
        self.assertTrue(target_dir.endswith(os.path.join("app", "src", "main", "res", "drawable")))

        # 2. Web workspace setup
        web_dir = os.path.join(self.test_dir, "web_ws")
        os.makedirs(os.path.join(web_dir, "public"), exist_ok=True)
        with open(os.path.join(web_dir, "package.json"), "w") as f:
            f.write('{"dependencies": {"react": "^18.0.0"}}')

        w_plat = HybridAssetPipeline.detect_project_platform(web_dir)
        self.assertEqual(w_plat, "web")

        w_target_dir = HybridAssetPipeline.resolve_resource_directory(web_dir)
        self.assertTrue(os.path.isdir(w_target_dir))
        self.assertTrue(w_target_dir.endswith(os.path.join("public", "assets")))

    def test_generate_and_place_modular_asset(self):
        """Verify modular Type B asset (e.g. app logo / seal) generation and code snippets."""
        res = HybridAssetPipeline.generate_and_place_asset(
            workspace=self.test_dir,
            asset_name="app_logo",
            category="logo",
            description="Official corporate brand seal",
            aspect_ratio="1:1"
        )

        self.assertEqual(res.asset_name, "app_logo")
        self.assertEqual(res.granularity, "MODULAR_PART")
        self.assertTrue(res.is_verified_on_disk)
        self.assertTrue(os.path.isfile(res.absolute_path))
        self.assertGreater(os.path.getsize(res.absolute_path), 500)

        # Verify Code Snippets
        snippets = res.code_snippets
        self.assertIn("R.drawable.app_logo", snippets["compose"])
        self.assertIn("painterResource", snippets["compose"])
        self.assertIn("@drawable/app_logo", snippets["android_xml"])
        self.assertIn("import appLogo", snippets["react_tsx"])

    def test_generate_and_place_full_scene_splash(self):
        """Verify full-cut Type A asset (e.g. 9:16 mobile splash screen) generation."""
        res = HybridAssetPipeline.generate_and_place_asset(
            workspace=self.test_dir,
            asset_name="splash_intro",
            category="splash_screen",
            description="Cinematic onboarding hero background",
            aspect_ratio="9:16"
        )

        self.assertEqual(res.asset_name, "splash_intro")
        self.assertEqual(res.granularity, "FULL_SCENE")
        self.assertTrue(res.is_verified_on_disk)
        self.assertEqual(res.width, 450)
        self.assertEqual(res.height, 800)

    def test_verify_code_integration_detection(self):
        """Verify verification of asset usage in actual source files."""
        # Asset placed
        HybridAssetPipeline.generate_and_place_asset(
            workspace=self.test_dir,
            asset_name="custom_badge",
            category="security_badge"
        )

        # Before code integration
        self.assertFalse(HybridAssetPipeline.verify_code_integration(self.test_dir, "custom_badge"))

        # Write Kotlin source referencing R.drawable.custom_badge
        src_dir = os.path.join(self.test_dir, "app", "src", "main", "java", "com", "example")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "MainScreen.kt"), "w", encoding="utf-8") as f:
            f.write("""
package com.example
import androidx.compose.foundation.Image
import androidx.compose.ui.res.painterResource

@Composable
fun MainScreen() {
    Image(painter = painterResource(R.drawable.custom_badge), contentDescription = null)
}
""")

        # After code integration
        self.assertTrue(HybridAssetPipeline.verify_code_integration(self.test_dir, "custom_badge"))


if __name__ == "__main__":
    unittest.main()
