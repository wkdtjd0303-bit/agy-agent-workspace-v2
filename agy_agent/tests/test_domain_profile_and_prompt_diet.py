#!/usr/bin/env python3
"""
Unit tests for Domain Profile Architecture, Prompt Injection Diet, and Physical Domain Barriers.
Tests:
  1. Bootstrapping slim core constitution and domain profiles via setup_manager.
  2. Prompt Injection Diet in agent_runner (focused milestone + <= 3 constraints, < 1,000 chars).
  3. EvidenceValidator domain detection and Anti-Mocking Rule 8 physical barrier.
  4. Headless domain Quality Gate 5 and Gate 7 validation.
"""

import os
import sys
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from setup_manager import create_project_constitution
from agent_runner import get_workspace_grounding_context
from evidence_validator import EvidenceValidator


class TestDomainProfileAndPromptDiet(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_profile_diet_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_bootstrap_domain_profiles_and_slim_constitution(self):
        """Verifies that create_project_constitution creates modular profiles and slim core constitution."""
        create_project_constitution(self.test_dir, "cli")

        const_file = os.path.join(self.test_dir, "PROJECT_CONSTITUTION.md")
        self.assertTrue(os.path.isfile(const_file))

        with open(const_file, "r", encoding="utf-8") as f:
            const_lines = f.readlines()
        self.assertLess(len(const_lines), 60, "Slim core constitution must be < 60 lines (< 1 page)")

        const_text = "".join(const_lines)
        self.assertNotIn("48dp", const_text, "Slim constitution must not hardcode mobile 48dp rules")
        self.assertNotIn("Apple 5대 바이브", const_text, "Slim constitution must not hardcode Apple vibes")
        self.assertIn("profiles/active_profile.md", const_text)

        # Check all 4 profile templates exist
        for prof in ["cli_agent_rules.md", "android_rules.md", "web_rules.md", "backend_api_rules.md"]:
            prof_path = os.path.join(self.test_dir, "profiles", prof)
            self.assertTrue(os.path.isfile(prof_path), f"Profile template missing: {prof}")

        # Active profile should be cli_agent
        active_path = os.path.join(self.test_dir, "profiles", "active_profile.md")
        self.assertTrue(os.path.isfile(active_path))
        with open(active_path, "r", encoding="utf-8") as f:
            active_text = f.read()
        self.assertIn("cli_agent_rules", active_text)
        self.assertIn("Headless CLI / Daemon / Bot", active_text)

    def test_prompt_injection_diet_format_and_length(self):
        """Verifies that get_workspace_grounding_context delivers the lean diet (1 milestone + <= 3 constraints)."""
        create_project_constitution(self.test_dir, "bot")

        # Create dummy ROADMAP.md
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write("# Project Roadmap\n- [x] Phase 0: Init\n- [ ] Phase 1: Implement Async Telegram Bot Client\n- [ ] Phase 2: Add Monitoring\n")

        ctx = get_workspace_grounding_context(self.test_dir, "코드 구현해줘")

        self.assertIn("[🎯 WORKSPACE FOCUS & CONSTRAINTS]", ctx)
        self.assertIn("Phase 1: Implement Async Telegram Bot Client", ctx)
        self.assertIn("cli_agent_rules", ctx)
        self.assertIn("핵심 제약", ctx)
        self.assertNotIn("48dp", ctx)
        self.assertNotIn("390px", ctx)
        self.assertLess(len(ctx), 1000, f"Grounding context diet violated: length={len(ctx)} chars")

    def test_domain_detection_across_presets(self):
        """Verifies that EvidenceValidator.get_project_domain accurately classifies domains."""
        # CLI
        create_project_constitution(self.test_dir, "cli")
        self.assertEqual(EvidenceValidator.get_project_domain(self.test_dir), "cli_agent")

        # Android
        ws_android = tempfile.mkdtemp(prefix="test_android_")
        try:
            create_project_constitution(ws_android, "android")
            self.assertEqual(EvidenceValidator.get_project_domain(ws_android), "android")
        finally:
            shutil.rmtree(ws_android, ignore_errors=True)

        # Web
        ws_web = tempfile.mkdtemp(prefix="test_web_")
        try:
            create_project_constitution(ws_web, "web")
            self.assertEqual(EvidenceValidator.get_project_domain(ws_web), "web")
        finally:
            shutil.rmtree(ws_web, ignore_errors=True)

    def test_physical_domain_barrier_blocks_unauthorized_web_mockups(self):
        """Verifies that Anti-Mocking Rule 8 blocks static HTML/CSS files in CLI projects."""
        create_project_constitution(self.test_dir, "cli")

        # Create valid python CLI source
        with open(os.path.join(self.test_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nprint('Running CLI')\n")

        # Clean check
        is_comp, viols, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(self.test_dir)
        self.assertTrue(is_comp, f"Clean CLI project should pass: {viols}")

        # Inject prohibited HTML mockup
        with open(os.path.join(self.test_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!DOCTYPE html><html><body><h1>Fake Mockup</h1></body></html>")

        is_comp, viols, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(self.test_dir)
        self.assertFalse(is_comp, "Prohibited HTML mockup must trigger Anti-Mocking Rule 8")
        self.assertTrue(any("Anti-Mocking Rule 8 - Physical Domain Barrier" in v for v in viols))

        # Gate 3 should also reject
        g3_pass, g3_msg, _ = EvidenceValidator.validate_gate(self.test_dir, 3)
        self.assertFalse(g3_pass, "Gate 3 must reject prohibited web mockups in CLI domain")

    def test_headless_domain_gates_pass_without_gui_screenshots(self):
        """Verifies that Gate 0, Gate 3, Gate 5, and Gate 7 pass cleanly for headless CLI projects."""
        create_project_constitution(self.test_dir, "cli")

        # Gate 0
        g0_pass, g0_msg, _ = EvidenceValidator.validate_gate(self.test_dir, 0)
        self.assertTrue(g0_pass, f"Gate 0 failed: {g0_msg}")

        # Gate 3
        with open(os.path.join(self.test_dir, "cli.py"), "w", encoding="utf-8") as f:
            f.write("import argparse\nparser = argparse.ArgumentParser()\nargs = parser.parse_args()\n")
        g3_pass, g3_msg, _ = EvidenceValidator.validate_gate(self.test_dir, 3)
        self.assertTrue(g3_pass, f"Gate 3 failed: {g3_msg}")

        # Gate 5 (Headless passes without screenshots)
        g5_pass, g5_msg, _ = EvidenceValidator.validate_gate(self.test_dir, 5)
        self.assertTrue(g5_pass, f"Gate 5 failed: {g5_msg}")
        self.assertIn("Headless", g5_msg)

        # Gate 7 (Recognizes cli.py as valid entrypoint release artifact)
        g7_pass, g7_msg, _ = EvidenceValidator.validate_gate(self.test_dir, 7)
        self.assertTrue(g7_pass, f"Gate 7 failed: {g7_msg}")


if __name__ == "__main__":
    unittest.main()
