#!/usr/bin/env python3
"""
Unit tests for Autonomous Evolution & 95% Intent Anchor System.
Tests:
  1. RoadmapManager does NOT use PRD.md as a priority source (PRD = execution journal).
  2. RoadmapManager auto-expansion is driven by codebase reality, not unmapped PRD REQs.
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

    def test_roadmap_does_not_use_prd_as_priority_source(self):
        """[PRD=일기장] PRD의 REQ 목록은 초기 사다리로 변환되지 않는다."""
        prd_content = """# PRD
## Core Requirements
* **REQ-001 (Telemetry Dashboard)**: Collect CPU, RAM, and battery stats every minute.
* **REQ-002 (Autonomous Patching)**: Automatically fix detected bugs and re-deploy.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

        # No production source on disk -> fall back to the generic template, never PRD REQs.
        rm = RoadmapManager(self.test_dir)
        created = rm.ensure_exists()
        self.assertTrue(created)
        self.assertTrue(rm.load())

        all_titles = " ".join(ms.title for ms in rm._milestones)
        self.assertNotIn("REQ-001", all_titles)
        self.assertNotIn("Telemetry Dashboard", all_titles)

    def test_roadmap_generated_from_codebase_not_prd(self):
        """소스 코드가 있으면 ROADMAP은 코드 실사 기반으로 생성된다(PRD REQ 아님)."""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write("* **REQ-001 (Unrelated Requirement)**: Something from the old plan.\n")
        src = os.path.join(self.test_dir, "app", "src", "main", "java", "com", "example", "pdfviewer")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "PdfViewerScreen.kt"), "w", encoding="utf-8") as f:
            f.write("class PdfViewerScreen")

        rm = RoadmapManager(self.test_dir)
        self.assertTrue(rm.ensure_exists())
        self.assertTrue(rm.load())

        all_titles = " ".join(ms.title for ms in rm._milestones)
        self.assertNotIn("REQ-001", all_titles)
        self.assertIn("문서", all_titles)  # codebase-aware PDF synthesis

    def test_roadmap_auto_expand_ignores_unmapped_prd(self):
        """auto_expand는 미매핑 PRD REQ를 우선하지 않고 코드 실사로 사다리를 확장한다."""
        prd_content = """# PRD
## Requirements
* **REQ-001 (Initial Feature)**: Do initial work.
* **REQ-002 (Secondary Feature)**: Do secondary work.
* **REQ-003 (Unmapped Feature)**: Do unmapped high-priority work.
"""
        with open(os.path.join(self.test_dir, "PRD.md"), "w", encoding="utf-8") as f:
            f.write(prd_content)

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
        expanded_titles = " ".join(m.title for m in new_milestones)
        # PRD REQs must NOT drive prioritization any more.
        self.assertNotIn("REQ-002", expanded_titles)
        self.assertNotIn("REQ-003", expanded_titles)

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
        # [기획자 역할] 클린 디스크 확장은 품질 하드닝이 아니라 '아직 없는 사용자 체감 신규 기능'을
        # 우선 발주해야 한다. 단, 도메인 무관 환각(게임 AI / 사운드 신디사이저)은 금지한다.
        from product_value_planner import is_hardening_only_plan
        self.assertTrue(
            any(
                not is_hardening_only_plan(m.title, [t.text for t in m.tasks])
                for m in new_milestones
            ),
            f"클린 디스크 자동 확장이 사용자 가치 기능을 생성하지 않았습니다: {titles}",
        )
        self.assertFalse(any("신디사이저" in t or "적 AI" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
