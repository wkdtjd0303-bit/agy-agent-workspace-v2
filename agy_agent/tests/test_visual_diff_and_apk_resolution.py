import os
import sys
import shutil
import tempfile
import unittest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from android_emulator_manager import AndroidEmulatorManager
from android_vision_inspector import AndroidVisionInspector
from ui_visual_differ import UIVisualDiffer


class TestVisualDiffAndApkResolution(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_vis_diff_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_apk_resolution_with_sample_apk(self):
        """Test APK package and activity resolution using a real project APK if available."""
        mgr = AndroidEmulatorManager(workspace=self.test_dir)
        sample_apk = "/home/jangsung/Desktop/autocoding/!1_vibecoding/pdfediter_andriod/app-release.apk"
        if os.path.isfile(sample_apk):
            pkg, act = mgr.resolve_apk_package_and_activity(sample_apk, self.test_dir)
            self.assertEqual(pkg, "com.pdfflow.app")
            self.assertEqual(act, "com.pdfflow.app.MainActivity")

    def test_workspace_fallback_resolution(self):
        """Test resolving package name and activity from mock Gradle and AndroidManifest."""
        app_dir = os.path.join(self.test_dir, "app")
        src_dir = os.path.join(app_dir, "src", "main")
        os.makedirs(src_dir, exist_ok=True)

        gradle_file = os.path.join(app_dir, "build.gradle.kts")
        with open(gradle_file, "w", encoding="utf-8") as f:
            f.write('namespace = "com.sample.demo"\napplicationId = "com.sample.demo"\n')

        manifest_file = os.path.join(src_dir, "AndroidManifest.xml")
        with open(manifest_file, "w", encoding="utf-8") as f:
            f.write('''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.sample.demo">
    <application>
        <activity android:name=".HomeActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>''')

        mgr = AndroidEmulatorManager(workspace=self.test_dir)
        pkg, act = mgr.resolve_apk_package_and_activity(None, self.test_dir)
        self.assertEqual(pkg, "com.sample.demo")
        self.assertEqual(act, "com.sample.demo.HomeActivity")

    def test_select_before_after_pair_matches_session_timestamps(self):
        """Test that AndroidVisionInspector pairs state 01 (idle) and state 02/04 of the same session timestamp."""
        img1 = os.path.join(self.test_dir, "android_full_01_idle_1700000000.png")
        img2 = os.path.join(self.test_dir, "android_full_02_interaction_1700000000.png")
        img3 = os.path.join(self.test_dir, "android_full_05_landscape_1700000000.png")  # Rotated landscape

        # Create portrait images for 01 and 02
        Image.new("RGB", (1080, 1920), color="blue").save(img1)
        Image.new("RGB", (1080, 1920), color="green").save(img2)
        # Create landscape image for 05
        Image.new("RGB", (1920, 1080), color="red").save(img3)

        b_path, a_path, b_desc, a_desc = AndroidVisionInspector.select_before_after_pair([img1, img2, img3])
        self.assertEqual(b_path, img1)
        self.assertEqual(a_path, img2)
        # Must NOT pair with state 05 (landscape)
        self.assertNotEqual(a_path, img3)

    def test_ui_visual_differ_rejects_aspect_ratio_mismatch(self):
        """Test that UIVisualDiffer rejects diffing when one image is portrait and the other is landscape."""
        portrait_img = os.path.join(self.test_dir, "portrait.png")
        landscape_img = os.path.join(self.test_dir, "landscape.png")
        out_card = os.path.join(self.test_dir, "out_diff.png")

        # Non-black images to pass stddev check
        p_im = Image.new("RGB", (1080, 1920), color=(100, 150, 200))
        for x in range(200, 400):
            for y in range(200, 400):
                p_im.putpixel((x, y), (250, 50, 50))
        p_im.save(portrait_img)

        l_im = Image.new("RGB", (1920, 1080), color=(50, 200, 100))
        for x in range(300, 600):
            for y in range(300, 600):
                l_im.putpixel((x, y), (20, 20, 220))
        l_im.save(landscape_img)

        result = UIVisualDiffer.create_smart_micro_diff_card(
            before_image_path=portrait_img,
            after_image_path=landscape_img,
            output_path=out_card
        )
        self.assertIsNone(result, "UIVisualDiffer must return None when aspect ratios are fundamentally incompatible")

    def test_ui_visual_differ_succeeds_on_compatible_aspect_ratio(self):
        """Test that UIVisualDiffer succeeds and creates a valid diff card for compatible images."""
        img_a = os.path.join(self.test_dir, "before.png")
        img_b = os.path.join(self.test_dir, "after.png")
        out_card = os.path.join(self.test_dir, "valid_diff.png")

        im1 = Image.new("RGB", (1080, 1920), color=(80, 120, 180))
        for x in range(100, 300):
            for y in range(100, 300):
                im1.putpixel((x, y), (220, 40, 40))
        for x in range(400, 800):
            for y in range(500, 600):
                im1.putpixel((x, y), (40, 220, 40))

        im2 = im1.copy()
        # Add a difference in center
        for x in range(400, 700):
            for y in range(800, 1100):
                im2.putpixel((x, y), (255, 255, 255))

        im1.save(img_a)
        im2.save(img_b)

        result = UIVisualDiffer.create_smart_micro_diff_card(
            before_image_path=img_a,
            after_image_path=img_b,
            output_path=out_card,
            title="[테스트] 인터랙션 정밀 대조"
        )
        self.assertIsNotNone(result)
        self.assertTrue(os.path.isfile(out_card))
        self.assertGreater(os.path.getsize(out_card), 5000)

    def test_android_vision_inspector_roi_crop_and_grounding(self):
        """Test that AndroidVisionInspector crops ROI at 1:1 scale and builds grounded prompt."""
        full_img = os.path.join(self.test_dir, "full_screen.png")
        Image.new("RGB", (1440, 900), color=(30, 30, 40)).save(full_img)

        roi_path = AndroidVisionInspector.extract_component_roi_crop(
            image_path=full_img,
            bbox=(100, 200, 300, 350)
        )
        self.assertIsNotNone(roi_path)
        self.assertTrue(os.path.isfile(roi_path))

        # Check prompt includes conversational intent and grounding metadata
        prompt = AndroidVisionInspector.get_vision_prompt(
            screenshot_path=full_img,
            app_name="Test Game",
            roi_crop_path=roi_path,
            ui_grounding_metadata={"element": "keycap", "font_size": "11px", "target_dp": 24},
            user_query="크기 말고 스타일 톤 자체는 어때 보여요?"
        )
        self.assertIn("사용자 직접 문의 사항", prompt)
        self.assertIn("크기 말고 스타일 톤 자체는 어때 보여요?", prompt)
        self.assertIn("1:1 고해상도 컴포넌트 크롭 (ROI Zoom)", prompt)
        self.assertIn("keycap", prompt)

    def test_analyze_screenshot_signature_backward_compatibility(self):
        """Verify that analyze_screenshot can be called with positional, legacy, or new kwargs without TypeError."""
        import asyncio
        from unittest.mock import patch

        full_img = os.path.join(self.test_dir, "compat_screen.png")
        Image.new("RGB", (200, 200), color=(10, 20, 30)).save(full_img)

        async def _run_tests():
            with patch.object(AndroidVisionInspector, "_analyze_with_direct_api", return_value="Score: 90/100\nClean layout"):
                with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key_for_test"}):
                    # 1. Single positional arg (minimal legacy)
                    res1 = await AndroidVisionInspector.analyze_screenshot(full_img)
                    self.assertTrue(res1["success"])

                    # 2. Legacy 2 args (positional + app_name)
                    res2 = await AndroidVisionInspector.analyze_screenshot(full_img, app_name="LegacyApp")
                    self.assertTrue(res2["success"])

                    # 3. All new optional keyword arguments
                    res3 = await AndroidVisionInspector.analyze_screenshot(
                        screenshot_path=full_img,
                        app_name="FullApp",
                        roi_crop_path=full_img,
                        before_screenshot_path=full_img,
                        ui_grounding_metadata={"dp": 48},
                        user_query="How is the contrast?"
                    )
                    self.assertTrue(res3["success"])

        asyncio.run(_run_tests())

    def test_system_status_bar_and_navigation_bar_diff_masking(self):
        """Test that top status bar (0-5%) and bottom nav bar (95-100%) changes are masked out."""
        w, h = 500, 1000
        base_im = Image.new("RGB", (w, h), color=(50, 50, 60))
        # Draw static central UI widget so stddev/mean passes
        for x in range(100, 400):
            for y in range(300, 400):
                base_im.putpixel((x, y), (120, 150, 200))

        # Image with ONLY clock/battery change in top 3% (y=10..25) and gesture bar change in bottom 2% (y=980..990)
        system_bar_diff_im = base_im.copy()
        for x in range(30, 80):
            for y in range(10, 25):  # Top 1.0~2.5%
                system_bar_diff_im.putpixel((x, y), (255, 255, 255))
        for x in range(200, 300):
            for y in range(980, 990):  # Bottom 98~99%
                system_bar_diff_im.putpixel((x, y), (200, 200, 200))

        # 1. Without masking: diff boxes should be detected
        unmasked_boxes = UIVisualDiffer.detect_pixel_diff_boxes(base_im, system_bar_diff_im, mask_system_bars=False)
        self.assertTrue(len(unmasked_boxes) > 0, "Unmasked diff detection should catch top/bottom changes")

        # 2. With masking enabled: diff boxes should be completely ignored (empty list)
        masked_boxes = UIVisualDiffer.detect_pixel_diff_boxes(base_im, system_bar_diff_im, mask_system_bars=True)
        self.assertEqual(len(masked_boxes), 0, "Masked diff detection must ignore clock/nav-bar changes")

        # 3. With a genuine widget change in the main body (y=500..550), it MUST be detected
        real_change_im = system_bar_diff_im.copy()
        for x in range(150, 350):
            for y in range(500, 550):  # Main content body
                real_change_im.putpixel((x, y), (255, 0, 0))

        real_boxes = UIVisualDiffer.detect_pixel_diff_boxes(base_im, real_change_im, mask_system_bars=True)
        self.assertEqual(len(real_boxes), 1, "Real widget change in main body must be detected")
        bx1, by1, bx2, by2 = real_boxes[0]
        self.assertGreaterEqual(by1, 50, "Detected box top must be below status bar")
        self.assertLessEqual(by2, 950, "Detected box bottom must be above navigation bar")
        self.assertTrue(500 <= by1 <= 505 and 545 <= by2 <= 555)


if __name__ == "__main__":
    unittest.main()


