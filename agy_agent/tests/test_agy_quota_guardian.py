"""Tests for agy_quota_guardian.py"""
import json
import os
import shutil
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Patch BRAIN_DIR before import
_test_brain_dir = None


class TestCleanupBrainSessions(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.brain_dir = Path(self.tmpdir)
        # Create 30 fake sessions with staggered mtimes
        for i in range(30):
            d = self.brain_dir / f"session-{i:03d}"
            d.mkdir()
            (d / "transcript.jsonl").write_text("{}")
            os.utime(d, (1000000 + i * 100, 1000000 + i * 100))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cleanup_keeps_max_sessions(self):
        import agy_quota_guardian as guard
        original_brain = guard.BRAIN_DIR
        guard.BRAIN_DIR = self.brain_dir
        try:
            deleted, freed = guard.cleanup_brain_sessions(max_keep=10)
            self.assertEqual(deleted, 20)
            remaining = [d for d in self.brain_dir.iterdir() if d.is_dir()]
            self.assertEqual(len(remaining), 10)
        finally:
            guard.BRAIN_DIR = original_brain

    def test_cleanup_dry_run(self):
        import agy_quota_guardian as guard
        original_brain = guard.BRAIN_DIR
        guard.BRAIN_DIR = self.brain_dir
        try:
            deleted, _ = guard.cleanup_brain_sessions(max_keep=10, dry_run=True)
            self.assertEqual(deleted, 20)
            remaining = [d for d in self.brain_dir.iterdir() if d.is_dir()]
            self.assertEqual(len(remaining), 30)  # Nothing actually deleted
        finally:
            guard.BRAIN_DIR = original_brain

    def test_cleanup_nothing_to_delete(self):
        import agy_quota_guardian as guard
        original_brain = guard.BRAIN_DIR
        guard.BRAIN_DIR = self.brain_dir
        try:
            deleted, _ = guard.cleanup_brain_sessions(max_keep=50)
            self.assertEqual(deleted, 0)
        finally:
            guard.BRAIN_DIR = original_brain


class TestParseQuotaBuckets(unittest.TestCase):
    def test_parse_gemini_and_claude(self):
        import agy_quota_guardian as guard
        data = {
            "models": [
                {"modelId": "gemini-3-flash", "remainingPercentage": 0.4364},
                {"modelId": "claude-sonnet-4-6", "remainingPercentage": 0.5410},
                {"modelId": "gemini-2.5-pro", "remainingPercentage": 0.50},
            ]
        }
        result = guard.parse_quota_buckets(data)
        self.assertAlmostEqual(result["gemini_5h"], 43.64, places=1)
        self.assertAlmostEqual(result["claude_5h"], 54.10, places=1)

    def test_parse_empty(self):
        import agy_quota_guardian as guard
        result = guard.parse_quota_buckets({"models": []})
        self.assertEqual(result, {})


class TestDetectActiveProcesses(unittest.TestCase):
    @patch("subprocess.run")
    def test_detects_agy(self, mock_run):
        import agy_quota_guardian as guard
        mock_run.return_value = MagicMock(
            stdout="jangsung 12345 5.0 2.0 100 200 pts/0 Sl+ 20:40 0:10 agy --dangerously-skip-permissions\n"
        )
        result = guard.detect_active_agy_processes()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pid"], "12345")

    @patch("subprocess.run")
    def test_no_processes(self, mock_run):
        import agy_quota_guardian as guard
        mock_run.return_value = MagicMock(stdout="root 1 0.0 0.1 100 200 ? Ss 18:51 0:00 /sbin/init\n")
        result = guard.detect_active_agy_processes()
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
