#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_nano_banana_hub.py

Unit tests for NanoBananaHub and cross-channel visual asset workflow.
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from config import NANO_BANANA_IMAGE_CHANNEL_ID
from nano_banana_hub import NanoBananaHub
from hybrid_asset_pipeline import HybridAssetPipeline, AssetPlacementResult


class TestNanoBananaHub(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_nano_banana_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_channel_id_and_recognition(self):
        """Verify channel ID 1549422783803691058 is strictly configured and recognized."""
        self.assertEqual(NANO_BANANA_IMAGE_CHANNEL_ID, 1549422783803691058)
        self.assertEqual(NanoBananaHub.CHANNEL_ID, 1549422783803691058)
        self.assertTrue(NanoBananaHub.is_nano_banana_channel(1549422783803691058))
        self.assertTrue(NanoBananaHub.is_nano_banana_channel("1549422783803691058"))
        self.assertFalse(NanoBananaHub.is_nano_banana_channel(123456789))

    def test_classify_and_optimize_modular_button(self):
        """Verify Type B modular button taxonomy classification."""
        req = NanoBananaHub.classify_and_optimize(
            asset_name="btn_login",
            category="button",
            prompt_hint="Mint green tactile rounded action button",
            aspect_ratio="1:1"
        )
        self.assertEqual(req["asset_name"], "btn_login")
        self.assertEqual(req["granularity"], "MODULAR_PART")
        self.assertFalse(req["is_type_a"])
        self.assertTrue(req["is_isolated"])
        self.assertEqual(req["width"], 512)
        self.assertEqual(req["height"], 512)
        self.assertIn("isolated", req["optimized_prompt"].lower())

    def test_classify_and_optimize_full_scene_splash(self):
        """Verify Type A full scene splash taxonomy classification."""
        req = NanoBananaHub.classify_and_optimize(
            asset_name="splash_intro",
            category="splash_screen",
            prompt_hint="Atmospheric dark blue space hero illustration",
            aspect_ratio="9:16"
        )
        self.assertEqual(req["asset_name"], "splash_intro")
        self.assertEqual(req["granularity"], "FULL_SCENE")
        self.assertTrue(req["is_type_a"])
        self.assertFalse(req["is_isolated"])
        self.assertEqual(req["width"], 450)
        self.assertEqual(req["height"], 800)

    def test_generate_and_place_asset_android(self):
        """Verify generating asset and placing into native Android drawable folder."""
        # Setup mock Android workspace
        os.makedirs(os.path.join(self.test_dir, "app", "src", "main"), exist_ok=True)
        with open(os.path.join(self.test_dir, "build.gradle.kts"), "w") as f:
            f.write("// Android build")

        res = NanoBananaHub.generate_and_place_asset(
            workspace=self.test_dir,
            asset_name="btn_action",
            category="button",
            description="High quality action button",
            aspect_ratio="1:1"
        )

        self.assertEqual(res.asset_name, "btn_action")
        self.assertTrue(res.is_verified_on_disk)
        self.assertTrue(os.path.isfile(res.absolute_path))
        self.assertIn("res/drawable/btn_action.png", res.relative_path.replace("\\", "/"))
        self.assertIn("R.drawable.btn_action", res.code_snippets["compose"])

    def test_verify_code_replacement(self):
        """Verify that NanoBananaHub detects source code integration."""
        res = NanoBananaHub.generate_and_place_asset(
            workspace=self.test_dir,
            asset_name="ic_cart",
            category="icon"
        )

        # Before writing code: should be False
        self.assertFalse(NanoBananaHub.verify_code_integration(self.test_dir, "ic_cart"))

        # Write code referencing ic_cart
        src_path = os.path.join(self.test_dir, "App.tsx")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write("import icCart from './assets/ic_cart.png';\nexport const Cart = () => <img src={icCart} />;")

        # After writing code: should be True
        self.assertTrue(NanoBananaHub.verify_code_integration(self.test_dir, "ic_cart"))

    async def test_dispatch_asset_to_channel_async(self):
        """Verify that dispatching to Discord channel constructs embed with file attachment."""
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()

        mock_bot = MagicMock()
        mock_bot.get_channel = MagicMock(return_value=mock_channel)

        # Create dummy physical asset
        dummy_file = os.path.join(self.test_dir, "test_btn.png")
        with open(dummy_file, "wb") as f:
            f.write(b"fake_png_binary_data")

        placement_res = AssetPlacementResult(
            asset_name="test_btn",
            category="button",
            granularity="MODULAR_PART",
            format="png",
            target_dir=self.test_dir,
            relative_path="res/drawable/test_btn.png",
            absolute_path=dummy_file,
            code_snippets={"compose": "// code"},
            is_verified_on_disk=True,
            width=512,
            height=512
        )

        ok = await NanoBananaHub.dispatch_asset_to_channel(
            bot_client=mock_bot,
            placement_result=placement_res,
            origin_channel_id=1539483630089273435,
            prompt_hint="Mint green button"
        )

        self.assertTrue(ok)
        mock_channel.send.assert_called_once()
        call_kwargs = mock_channel.send.call_args[1]
        self.assertIn("embed", call_kwargs)
        self.assertIn("files", call_kwargs)
        self.assertEqual(len(call_kwargs["files"]), 1)
        self.assertEqual(call_kwargs["files"][0].filename, "test_btn.png")

    def test_format_origin_report(self):
        """Verify format of completion report returned to the origin project channel."""
        dummy_file = os.path.join(self.test_dir, "test_badge.png")
        with open(dummy_file, "w") as f:
            f.write("ok")

        placement_res = AssetPlacementResult(
            asset_name="test_badge",
            category="badge",
            granularity="MODULAR_PART",
            format="png",
            target_dir=self.test_dir,
            relative_path="res/drawable/test_badge.png",
            absolute_path=dummy_file,
            code_snippets={"compose": "Image(painter = painterResource(id = R.drawable.test_badge))"},
            is_verified_on_disk=True,
            width=512,
            height=512
        )

        report = NanoBananaHub.format_origin_report(
            placement_result=placement_res,
            origin_channel_id=1539483630089273435,
            platform="android"
        )

        self.assertIn("<#1549422783803691058>", report)
        self.assertIn("test_badge.png", report)
        self.assertIn("res/drawable/test_badge.png", report)
        self.assertIn("R.drawable.test_badge", report)
        self.assertIn("디스크 저장 확인", report)


if __name__ == "__main__":
    unittest.main()
