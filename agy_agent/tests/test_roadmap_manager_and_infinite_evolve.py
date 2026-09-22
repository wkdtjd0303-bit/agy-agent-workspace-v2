import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from roadmap_manager import RoadmapManager, RoadmapMilestone, RoadmapTask, _DEFAULT_ROADMAP_TEMPLATE
from qa_hub import QAMissionPacket


class TestRoadmapManagerAndInfiniteEvolve(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.temp_dir.name
        self.mgr = RoadmapManager(self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ensure_exists_and_load(self):
        created = self.mgr.ensure_exists()
        self.assertTrue(created)
        self.assertTrue(os.path.isfile(self.mgr.roadmap_path))
        self.assertEqual(len(self.mgr._milestones), 6)
        
        cur = self.mgr.get_current_milestone(auto_expand=False)
        self.assertIsNotNone(cur)
        self.assertEqual(cur.version, "v1.0")

    def test_mark_done_and_progression(self):
        self.mgr.ensure_exists()
        m1 = self.mgr.mark_current_milestone_done()
        self.assertIsNotNone(m1)
        self.assertEqual(m1.version, "v1.0")
        self.assertEqual(m1.status, "DONE")

        m2 = self.mgr.get_current_milestone(auto_expand=False)
        self.assertIsNotNone(m2)
        self.assertEqual(m2.version, "v1.1")

    def test_skip_milestone(self):
        self.mgr.ensure_exists()
        skipped = self.mgr.skip_current_milestone(reason="핫픽스 한도 초과")
        self.assertIsNotNone(skipped)
        self.assertEqual(skipped.version, "v1.0")
        self.assertEqual(skipped.status, "SKIPPED")
        self.assertTrue(any("핫픽스 한도 초과" in t.text for t in skipped.tasks))

        next_ms = self.mgr.get_current_milestone(auto_expand=False)
        self.assertIsNotNone(next_ms)
        self.assertEqual(next_ms.version, "v1.1")

    def test_auto_expansion_when_exhausted(self):
        self.mgr.ensure_exists()
        # Mark or skip all 6 initial milestones
        for _ in range(6):
            cur = self.mgr.get_current_milestone(auto_expand=False)
            self.assertIsNotNone(cur)
            self.mgr.mark_current_milestone_done()

        # At this point, initial 6 are all done
        # Calling get_current_milestone(auto_expand=True) MUST automatically expand!
        next_gen = self.mgr.get_current_milestone(auto_expand=True)
        self.assertIsNotNone(next_gen)
        # Should be v3.1 or higher
        self.assertTrue(next_gen.version.startswith("v3.") or next_gen.version.startswith("v4."))
        self.assertEqual(next_gen.status, "PENDING")
        self.assertTrue(len(self.mgr._milestones) >= 9)

    def test_build_skip_and_evolve_prompt(self):
        self.mgr.ensure_exists()
        skipped = self.mgr.skip_current_milestone(reason="테스트 스킵")
        prompt = self.mgr.build_skip_and_evolve_prompt(skipped)
        self.assertIn("이전 마일스톤", prompt)
        self.assertIn("신규 개발 목표", prompt)
        self.assertIn("v1.1", prompt)

    def test_qa_mission_packet_hotfix_tracking(self):
        packet = QAMissionPacket(
            request_id="QA_TEST_1",
            source_channel_id=12345,
            source_channel_name="test-channel",
            workspace="/tmp",
            target_url_or_apk="/tmp/app.apk",
            mission="E2E 검증",
            current_round=1,
            hotfix_round=0,
            mode="evolve"
        )
        self.assertEqual(packet.hotfix_round, 0)
        self.assertEqual(packet.mode, "evolve")

        # Next hotfix increment
        packet.hotfix_round += 1
        self.assertEqual(packet.hotfix_round, 1)

    def test_detect_workspace_target_python_service(self):
        from roadmap_manager import detect_workspace_target
        # Create a mock python service workspace
        with tempfile.TemporaryDirectory() as py_dir:
            with open(os.path.join(py_dir, "bot.py"), "w") as f:
                f.write("# bot")
            target_path, display_name = detect_workspace_target(py_dir)
            self.assertEqual(target_path, py_dir)
            self.assertIn("Python Service", display_name)

    def test_detect_workspace_target_web_app(self):
        from roadmap_manager import detect_workspace_target
        with tempfile.TemporaryDirectory() as web_dir:
            with open(os.path.join(web_dir, "package.json"), "w") as f:
                f.write('{"name": "my-cool-webapp"}')
            target_path, display_name = detect_workspace_target(web_dir)
            self.assertEqual(target_path, web_dir)
            self.assertEqual(display_name, "my-cool-webapp (Web App)")

    def test_detect_workspace_target_android(self):
        from roadmap_manager import detect_workspace_target
        with tempfile.TemporaryDirectory() as and_dir:
            with open(os.path.join(and_dir, "gradlew"), "w") as f:
                f.write("# gradle")
            with open(os.path.join(and_dir, "build.gradle"), "w") as f:
                f.write("// build")
            # Without APK, returns app name
            target_path, display_name = detect_workspace_target(and_dir)
            self.assertIn("Android App", display_name)

            # With timestamped APK, returns APK basename
            apk_path = os.path.join(and_dir, "MyApp-20260906_120000.apk")
            with open(apk_path, "w") as f:
                f.write("apk")
            target_path, display_name = detect_workspace_target(and_dir)
            self.assertEqual(target_path, apk_path)
            self.assertEqual(display_name, "MyApp-20260906_120000.apk")


if __name__ == "__main__":
    unittest.main()
