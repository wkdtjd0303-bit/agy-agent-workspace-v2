import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trio_agent_runner import detect_codebase_anchor_dict, detect_codebase_anchor
from evidence_validator import EvidenceValidator
from roadmap_manager import RoadmapManager


class TestMode3Perfection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_codebase_anchor_from_android_manifest(self):
        # 1. Setup AndroidManifest with custom package
        app_dir = os.path.join(self.workspace, "app", "src", "main")
        os.makedirs(app_dir, exist_ok=True)
        manifest_path = os.path.join(app_dir, "AndroidManifest.xml")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.docuflow.pdfviewer">\n</manifest>')

        # Also create a main entry file
        entry_dir = os.path.join(app_dir, "java", "com", "docuflow", "pdfviewer")
        os.makedirs(entry_dir, exist_ok=True)
        entry_path = os.path.join(entry_dir, "MainActivity.kt")
        with open(entry_path, "w", encoding="utf-8") as f:
            f.write("package com.docuflow.pdfviewer\nclass MainActivity")

        anchor_dict = detect_codebase_anchor_dict(self.workspace)
        self.assertEqual(anchor_dict.get("root_package"), "com.docuflow.pdfviewer")
        self.assertTrue(any("MainActivity.kt" in f for f in anchor_dict.get("entry_files", [])))

        anchor_str = detect_codebase_anchor(self.workspace)
        self.assertIn("com.docuflow.pdfviewer", anchor_str)
        self.assertIn("Anti-Amnesia Anchor", anchor_str)

    def test_detect_codebase_anchor_from_build_gradle_namespace(self):
        # Setup build.gradle.kts with namespace
        app_dir = os.path.join(self.workspace, "app")
        os.makedirs(app_dir, exist_ok=True)
        gradle_path = os.path.join(app_dir, "build.gradle.kts")
        with open(gradle_path, "w", encoding="utf-8") as f:
            f.write('android {\n    namespace = "com.supergame.engine"\n}')

        anchor_dict = detect_codebase_anchor_dict(self.workspace)
        self.assertEqual(anchor_dict.get("root_package"), "com.supergame.engine")

    def test_evidence_validator_detects_amnesia_package_drift(self):
        # 1. Create canonical manifest
        app_dir = os.path.join(self.workspace, "app", "src", "main")
        os.makedirs(app_dir, exist_ok=True)
        with open(os.path.join(app_dir, "AndroidManifest.xml"), "w") as f:
            f.write('<manifest package="com.docuflow.pdfviewer"></manifest>')

        # 2. Coder drifts and creates file under com.example
        drift_dir = os.path.join(app_dir, "java", "com", "example")
        os.makedirs(drift_dir, exist_ok=True)
        drift_file = os.path.join(drift_dir, "WrongSyncWorker.kt")
        with open(drift_file, "w") as f:
            f.write("package com.example\nclass WrongSyncWorker")

        has_slop, issues = EvidenceValidator.detect_ai_slop_and_prompt_bleed(
            workspace=self.workspace
        )
        self.assertTrue(has_slop)
        self.assertTrue(any("Amnesia Package Drift" in issue for issue in issues), f"Expected package drift issue, got: {issues}")

    def test_sync_milestones_to_prd(self):
        # 1. Create a mock PRD.md
        prd_path = os.path.join(self.workspace, "PRD.md")
        with open(prd_path, "w", encoding="utf-8") as f:
            f.write(
                "# Project Requirements Document\n\n"
                "## 2. Requirements & Tasks\n"
                "- [ ] `[REQ-001]` Core Engine Setup\n"
                "- [ ] `[REQ-002]` Basic Document Parsing\n"
                "- [ ] `[REQ-003]` Advanced Search & Filtering\n"
            )

        mgr = RoadmapManager(self.workspace)
        mgr.ensure_exists()

        # Mark current milestone (v1.0) done
        done_ms = mgr.mark_current_milestone_done()
        self.assertIsNotNone(done_ms)

        # PRD should have tracker section now
        with open(prd_path, "r", encoding="utf-8") as f:
            prd_content = f.read()

        self.assertIn("## 🗺️ Milestone Evolution Tracker", prd_content)
        self.assertIn(f"- [x] **{done_ms.version}**", prd_content)

    def test_auto_expansion_does_not_contain_hardcoded_android_templates(self):
        # Verify that auto_expand_roadmap generates milestones without stale hardcoded sync/merge templates
        mgr = RoadmapManager(self.workspace)
        mgr.ensure_exists()

        # Drain milestones
        for _ in range(len(mgr._milestones)):
            mgr.mark_current_milestone_done()

        # Expanding should use dynamic synthesis
        new_ms = mgr.get_current_milestone(auto_expand=True)
        self.assertIsNotNone(new_ms)

        # Check all milestone titles
        all_titles = [m.title for m in mgr._milestones]
        for title in all_titles:
            # Stale hardcoded templates were "백그라운드 동기화 & 시스템 알림", "3-Way 병합 Delta Sync"
            self.assertNotIn("3-Way 병합 Delta Sync", title)

    def test_android_build_manager_cleanup_old_apks(self):
        from android_build_manager import AndroidBuildManager
        import time

        app_name = "TestApp"
        # Create 7 dummy timestamped APKs with distinct mtimes
        created_paths = []
        for i in range(7):
            p = os.path.join(self.workspace, f"{app_name}-20260920_12000{i}.apk")
            with open(p, "w") as f:
                f.write(f"apk_{i}")
            # set mtime sequentially
            os.utime(p, (1000 + i * 10, 1000 + i * 10))
            created_paths.append(p)

        # Cleanup keeping only 5
        removed = AndroidBuildManager.cleanup_old_apks(self.workspace, app_name, keep_count=5)
        self.assertEqual(removed, 2)

        # Oldest two (0 and 1) should be removed
        self.assertFalse(os.path.exists(created_paths[0]))
        self.assertFalse(os.path.exists(created_paths[1]))
        # Newest five (2 to 6) should exist
        for p in created_paths[2:]:
            self.assertTrue(os.path.exists(p))

    def test_skip_milestone_and_build_skip_prompt(self):
        mgr = RoadmapManager(self.workspace)
        mgr.ensure_exists()

        cur_ms = mgr.get_current_milestone(auto_expand=False)
        self.assertEqual(cur_ms.version, "v1.0")

        reason = "사용자 !next 명령에 의한 수동 건너뛰기"
        skipped = mgr.skip_current_milestone(reason=reason)
        self.assertEqual(skipped.status, "SKIPPED")

        prompt = mgr.build_skip_and_evolve_prompt(skipped, reason=reason)
        self.assertIn("v1.0", prompt)
        self.assertIn("v1.1", prompt)
        self.assertIn(reason, prompt)


if __name__ == "__main__":
    unittest.main()

