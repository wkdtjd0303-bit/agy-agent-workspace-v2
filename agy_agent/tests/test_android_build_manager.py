import os
import sys
import shutil
import tempfile
import unittest
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from android_build_manager import AndroidBuildManager

class TestAndroidBuildManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_android_build_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_find_android_project(self):
        # 1. Root level gradlew
        gradlew_path = os.path.join(self.test_dir, "gradlew")
        with open(gradlew_path, "w") as f:
            f.write("#!/bin/sh\n")
        self.assertEqual(AndroidBuildManager.find_android_project(self.test_dir), self.test_dir)

        # 2. Subdir level (e.g. android/)
        os.remove(gradlew_path)
        sub_android = os.path.join(self.test_dir, "android")
        os.makedirs(sub_android, exist_ok=True)
        sub_gradlew = os.path.join(sub_android, "gradlew")
        with open(sub_gradlew, "w") as f:
            f.write("#!/bin/sh\n")
        self.assertEqual(AndroidBuildManager.find_android_project(self.test_dir), sub_android)

    def test_extract_app_name(self):
        # Test settings.gradle.kts
        settings_file = os.path.join(self.test_dir, "settings.gradle.kts")
        with open(settings_file, "w") as f:
            f.write('rootProject.name = "MyAwesomeApp"\n')
        name = AndroidBuildManager.extract_app_name(self.test_dir)
        self.assertEqual(name, "MyAwesomeApp")

    def test_build_apk_missing_project(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(AndroidBuildManager.build_apk(self.test_dir))
            self.assertFalse(res["success"])
            self.assertIn("gradlew", res["error"])
        finally:
            loop.close()

    def test_build_apk_mock_gradlew(self):
        # Create mock gradlew that produces an APK
        gradlew_path = os.path.join(self.test_dir, "gradlew")
        apk_dir = os.path.join(self.test_dir, "app", "build", "outputs", "apk", "release")
        os.makedirs(apk_dir, exist_ok=True)

        mock_script = f"""#!/bin/sh
echo "Building mock release APK..."
mkdir -p "{apk_dir}"
echo "MOCK_APK_DATA" > "{apk_dir}/app-release.apk"
exit 0
"""
        with open(gradlew_path, "w") as f:
            f.write(mock_script)
        os.chmod(gradlew_path, 0o755)

        settings_file = os.path.join(self.test_dir, "settings.gradle.kts")
        with open(settings_file, "w") as f:
            f.write('rootProject.name = "MockApp"\n')

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(AndroidBuildManager.build_apk(self.test_dir, build_type="release"))
            self.assertTrue(res["success"])
            self.assertIn("MockApp", res["apk_name"])
            self.assertTrue(res["apk_name"].endswith(".apk"))
            self.assertTrue(os.path.isfile(res["apk_path"]))
            # Verify latest copy exists
            latest_apk = os.path.join(self.test_dir, "MockApp-latest.apk")
            self.assertTrue(os.path.isfile(latest_apk))
        finally:
            loop.close()

    def test_apk_command_integration(self):
        from unittest.mock import MagicMock, AsyncMock, patch
        import bot

        gradlew_path = os.path.join(self.test_dir, "gradlew")
        apk_dir = os.path.join(self.test_dir, "app", "build", "outputs", "apk", "release")
        os.makedirs(apk_dir, exist_ok=True)
        with open(os.path.join(apk_dir, "app-release.apk"), "w") as f:
            f.write("DUMMY_DATA")

        with open(gradlew_path, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(gradlew_path, 0o755)

        with open(os.path.join(self.test_dir, "settings.gradle.kts"), "w") as f:
            f.write('rootProject.name = "TestIntegrationApp"\n')

        # Mock discord ctx
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 12345
        status_msg = AsyncMock()
        ctx.send = AsyncMock(return_value=status_msg)

        # Set session workspace
        session = bot.session_manager.get_session(12345)
        session.workspace = self.test_dir

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("gdrive_uploader.GoogleDriveUploader.upload_file", new_callable=AsyncMock) as mock_upload:
                mock_upload.return_value = {
                    "success": True,
                    "download_url": "https://drive.google.com/test_apk",
                    "provider": "Google Drive"
                }
                loop.run_until_complete(bot.apk_command(ctx))
                # Verify status_msg was sent and edited
                self.assertTrue(ctx.send.called)
                mock_upload.assert_called_once()
        finally:
            loop.close()

if __name__ == "__main__":
    unittest.main()
