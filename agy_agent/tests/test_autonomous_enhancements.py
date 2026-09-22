#!/usr/bin/env python3
"""
Unit tests for Autonomous Evolution & 95% Intent Anchor System.
Tests:
  1. RoadmapManager generates milestones from PRD.md requirements.
  2. RoadmapManager auto-expansion prioritizes unmapped PRD requirements before generic templates.
  3. EvidenceValidator evaluates PRD feature coverage with strict code syntax checks.
  4. Commander plan_e2e_operation integrates active roadmap milestone.
"""

import os
import sys
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

try:
    from agy_agent.roadmap_manager import RoadmapManager
    from agy_agent.evidence_validator import EvidenceValidator
except ImportError:
    from roadmap_manager import RoadmapManager
    from evidence_validator import EvidenceValidator


class TestAutonomousEnhancements(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_auto_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_roadmap_generated_from_prd(self):
        prd_content = """# PRD
## Core Requirements
* **REQ-001 (Telemetry Dashboard)**: Collect CPU, RAM, and battery stats every minute.
* **REQ-002 (Autonomous Patching)**: Automatically fix detected bugs and re-deploy.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

        rm = RoadmapManager(self.test_dir)
        created = rm.ensure_exists()
        self.assertTrue(created)
        self.assertTrue(rm.load())

        cur_ms = rm.get_current_milestone(auto_expand=False)
        self.assertIsNotNone(cur_ms)
        self.assertIn("REQ-001", cur_ms.title)
        self.assertIn("Telemetry Dashboard", cur_ms.title)

    def test_roadmap_auto_expand_prioritizes_unmapped_prd(self):
        prd_content = """# PRD
## Requirements
* **REQ-001 (Initial Feature)**: Do initial work.
* **REQ-002 (Secondary Feature)**: Do secondary work.
* **REQ-003 (Unmapped Feature)**: Do unmapped high-priority work.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

        # Existing roadmap only has REQ-001
        roadmap_content = """# 🗺️ Roadmap
## v1.0 — [REQ-001] Initial Feature ✅
- [x] Initial task
"""
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(roadmap_content)

        rm = RoadmapManager(self.test_dir)
        rm.load()
        self.assertTrue(rm.is_roadmap_exhausted())

        new_milestones = rm.auto_expand_roadmap()
        self.assertTrue(len(new_milestones) > 0)
        # Should include REQ-002 and REQ-003
        expanded_titles = [m.title for m in new_milestones]
        self.assertTrue(any("REQ-002" in t for t in expanded_titles))
        self.assertTrue(any("REQ-003" in t for t in expanded_titles))

    def test_evidence_validator_code_syntax_and_mock_rejection(self):
        prd_content = """# PRD
* **REQ-001 (Control Tower Telemetry)**: Surface Pro 7 hardware telemetry live collection.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

        # File with only mock comments should NOT be counted as completed
        mock_file = os.path.join(self.test_dir, "dummy_mock.py")
        with open(mock_file, "w", encoding="utf-8") as f:
            f.write("# TODO: mock telemetry surface pro 7 hardware live collection\n")

        cov = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertTrue(cov["has_prd"])
        # Should be pending because dummy_mock only has mock/todo and no code syntax
        self.assertEqual(cov["pending_count"], 1)
        self.assertEqual(cov["completed_count"], 0)

        # Add genuine implementation file with real code syntax
        real_file = os.path.join(self.test_dir, "telemetry_service.py")
        with open(real_file, "w", encoding="utf-8") as f:
            f.write("""
import psutil

class TelemetryCollector:
    def collect_surface_hardware_telemetry(self):
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        return {"cpu": cpu, "ram": ram}
""")

        cov2 = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertEqual(cov2["completed_count"], 1)
        self.assertEqual(cov2["pending_count"], 0)

    def test_roadmap_auto_expand_without_prd_focuses_on_intent_first(self):
        # Empty workspace with no PRD.md
        roadmap_content = """# 🗺️ Roadmap
## v1.0 — Initial Setup ✅
- [x] Done
"""
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(roadmap_content)

        rm = RoadmapManager(self.test_dir)
        rm.load()
        new_milestones = rm.auto_expand_roadmap()
        self.assertTrue(len(new_milestones) > 0)
        titles = [m.title for m in new_milestones]
        # Should contain intent-first or quality hardening, NOT game AI or sound synthesizer
        self.assertTrue(any("기획" in t or "품질" in t or "아키텍처" in t for t in titles))
        self.assertFalse(any("신디사이저" in t or "적 AI" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
