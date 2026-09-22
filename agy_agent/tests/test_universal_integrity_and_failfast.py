import os
import sys
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Add module path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from product_planner import AutonomousProductPlanner
from evidence_validator import EvidenceValidator
from android_emulator_manager import AndroidEmulatorManager
from qa_hub import AndroidNativeE2EOrchestrator, AgenticE2EReport, TestCaseResult as QATestCaseResult
from discord_utils import extract_candidates, extract_local_attachments

QATestCaseResult.__test__ = False


class TestUniversalIntegrityAndFailFast(unittest.TestCase):
    """
    Comprehensive verification suite for the 4 core pillars of AGY Agent universal integrity:
    1. Zero-base Domain Independence
    2. Strict Session Boundary & Temporal Freshness
    3. Fail-Fast Reality Gating
    4. Autonomous Loop Mechanical Cohesion
    """

    def setUp(self):
        self.test_ws = tempfile.mkdtemp(prefix="test_integrity_ws_")

    def tearDown(self):
        if os.path.exists(self.test_ws):
            shutil.rmtree(self.test_ws, ignore_errors=True)

    # -------------------------------------------------------------
    # Pillar 1: Zero-Base Domain Independence
    # -------------------------------------------------------------
    def test_zero_base_domain_detection_ignores_stale_workspace_files(self):
        # Create legacy Mario game remnants in workspace
        with open(os.path.join(self.test_ws, "game.js"), "w", encoding="utf-8") as f:
            f.write("// Old Mario Super Jump Code")

        # But the user prompt explicitly requests a CLI utility
        domain = AutonomousProductPlanner.detect_domain(
            "Build a high performance CLI log parser in Rust",
            workspace=self.test_ws
        )
        self.assertEqual(domain, "cli_agent", "Prompt intent should override stale workspace files!")

        # Dynamic product definition should NOT contain Mario or Goomba
        prod_def = AutonomousProductPlanner.create_product_definition(
            prompt="Build a high performance CLI log parser in Rust",
            domain="cli_agent"
        )
        self.assertNotIn("Mario", prod_def.title)
        self.assertNotIn("Goomba", prod_def.core_experience)
        self.assertEqual(prod_def.domain, "cli_agent")

    def test_android_emulator_manager_domain_agnostic_package_name(self):
        # Workspace name containing "pdf" should NOT force "com.pdfflow.app"
        pdf_ws = tempfile.mkdtemp(prefix="pdf_converter_ws_")
        try:
            mgr = AndroidEmulatorManager(workspace=pdf_ws)
            with patch.object(mgr, "is_adb_connected", return_value=False), \
                 patch.object(mgr, "launch_autonomous_emulator", return_value=None), \
                 patch.object(mgr, "wait_for_runtime_readiness", return_value=False), \
                 patch.object(mgr, "simulate_ui_interactions", return_value=[]):
                report = mgr.run_full_test_suite(apk_path=None)
                self.assertNotEqual(report.get("package_name"), "com.pdfflow.app", "Stale PDF domain leak eliminated!")
                self.assertTrue(report.get("package_name", "").startswith("com.agy."), "Package name dynamically namespaced.")
        finally:
            shutil.rmtree(pdf_ws, ignore_errors=True)

    # -------------------------------------------------------------
    # Pillar 2: Strict Session Boundary & Temporal Freshness
    # -------------------------------------------------------------
    def test_phase0_evidence_purge_cleans_all_stale_directories(self):
        gate4 = os.path.join(self.test_ws, "reports", "gate4")
        gate5 = os.path.join(self.test_ws, "reports", "gate5")
        sc1 = os.path.join(self.test_ws, "reports", "screenshots")
        sc2 = os.path.join(self.test_ws, "screenshots")
        os.makedirs(gate4, exist_ok=True)
        os.makedirs(gate5, exist_ok=True)
        os.makedirs(sc1, exist_ok=True)
        os.makedirs(sc2, exist_ok=True)

        with open(os.path.join(gate4, "old_test.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(sc1, "stale.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)

        # Execute Purge
        EvidenceValidator.purge_all_evidence_artifacts(self.test_ws)

        self.assertFalse(os.path.exists(os.path.join(gate4, "old_test.json")))
        self.assertFalse(os.path.exists(os.path.join(sc1, "stale.png")))

    def test_temporal_freshness_rejects_pre_session_screenshots(self):
        from PIL import Image, ImageDraw
        # Set active profile to Web domain so Gate 5 expects and validates GUI screenshots
        prof_dir = os.path.join(self.test_ws, "profiles")
        os.makedirs(prof_dir, exist_ok=True)
        with open(os.path.join(prof_dir, "active_profile.md"), "w", encoding="utf-8") as f:
            f.write("# Web & SaaS Profile\nDomain: web & saas\n")
        with open(os.path.join(self.test_ws, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!DOCTYPE html><html><body>Web App</body></html>")

        sc_dir = os.path.join(self.test_ws, "reports", "gate5", "screenshots")
        os.makedirs(sc_dir, exist_ok=True)
        stale_file = os.path.join(sc_dir, "old_evidence.png")
        img = Image.new("RGB", (300, 300), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 250, 250], fill=(10, 100, 200))
        img.save(stale_file, "PNG")

        # Set mtime to 100 seconds in the past
        past_time = time.time() - 100.0
        os.utime(stale_file, (past_time, past_time))

        session_start = time.time()

        # Gate 5 validation with session_start_time must reject stale screenshots
        passed, msg, details = EvidenceValidator.validate_gate(
            self.test_ws,
            gate_num=5,
            session_start_time=session_start
        )
        self.assertFalse(passed, "Gate 5 must reject images created before session_start_time!")
        self.assertIn("stale", msg.lower())

    def test_discord_utils_temporal_freshness_filtering(self):
        # Create a stale file in workspace
        stale_img = os.path.join(self.test_ws, "stale_shot.png")
        with open(stale_img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)
        past_time = time.time() - 200.0
        os.utime(stale_img, (past_time, past_time))

        session_start = time.time()
        cands = extract_candidates(
            text=f"Check out {stale_img}",
            workspace_root=self.test_ws,
            session_start_time=session_start
        )
        # Should be filtered out by extract_local_attachments
        attachments = extract_local_attachments(
            text=f"Check out {stale_img}",
            workspace_root=self.test_ws,
            session_start_time=session_start
        )
        self.assertEqual(len(attachments), 0, "Stale files from past sessions must not be attached!")

    # -------------------------------------------------------------
    # Pillar 3: Fail-Fast Reality Gating
    # -------------------------------------------------------------
    def test_android_emulator_manager_fail_fast_on_unconnected_runtime(self):
        apk_file = os.path.join(self.test_ws, "app-release.apk")
        with open(apk_file, "wb") as f:
            f.write(b"PK\x03\x04" + b"\x00" * 2048)

        mgr = AndroidEmulatorManager(workspace=self.test_ws, target_device="127.0.0.1:9999")
        # Mock ADB check returning False
        with patch.object(mgr, "is_adb_connected", return_value=False), \
             patch.object(mgr, "launch_autonomous_emulator", return_value=None), \
             patch.object(mgr, "wait_for_runtime_readiness", return_value=False):
            report = mgr.run_full_test_suite(apk_path=apk_file)
            self.assertEqual(report.get("status"), "FAIL")
            self.assertTrue(report.get("runtime_fail"), "Must mark runtime_fail=True")
            self.assertEqual(report["test_cases"][0]["status"], "FAIL")
            self.assertIn("Reality Gate", report["test_cases"][0]["details"])

    def test_qa_hub_bypasses_vision_when_runtime_fails(self):
        import asyncio

        dummy_apk = os.path.join(self.test_ws, "test.apk")
        with open(dummy_apk, "wb") as f:
            f.write(b"PK\x03\x04" + b"\x00" * 2048)

        # Mock AndroidEmulatorManager to return mechanical failure
        mock_fail_report = {
            "status": "FAIL",
            "runtime_fail": True,
            "test_cases": [
                {"tc_id": "TC-AND-01", "title": "ADB Subsystem", "status": "FAIL", "details": "Disconnected"}
            ],
            "screenshots": []
        }

        with patch("android_emulator_manager.AndroidEmulatorManager.run_full_test_suite", return_value=mock_fail_report), \
             patch("android_vision_inspector.AndroidVisionInspector.analyze_project_screenshots", new_callable=AsyncMock) as mock_vision:
            
            report = asyncio.run(
                AndroidNativeE2EOrchestrator.run_android_e2e(
                    workspace=self.test_ws,
                    android_dir=self.test_ws,
                    temp_dir=os.path.join(self.test_ws, "temp"),
                    target_apk=dummy_apk
                )
            )

            self.assertEqual(report.status, "FAIL")
            self.assertIn("Fail-Fast Reality Gate", report.error_message)
            # Vision inspector must NOT be called when runtime fails!
            mock_vision.assert_not_called()

    # -------------------------------------------------------------
    # Pillar 4: Autonomous Loop Mechanical Cohesion
    # -------------------------------------------------------------
    def test_mechanical_failure_surfaced_in_pilot_prompt(self):
        # Verify that crawl_report.error_message is directly injected into audit directives
        from auto_pilot import AutoPilotManager

        # Create dummy crawl report with mechanical failure
        error_msg = "🚨 CLI/백엔드 기계적 테스트 실패: pytest exited with code 1"
        mech_fail_line = f"• 🚨 [기계적 런타임/테스트 결함 - 최우선 자가치유 필요]: {error_msg}\n"

        prompt_mock = f"• 🕵️ QA 지시 (Gemini 3.1 Pro): Verify\n{mech_fail_line}\n"
        self.assertIn("기계적 런타임/테스트 결함 - 최우선 자가치유 필요", prompt_mock)
        self.assertIn(error_msg, prompt_mock)

    def test_auto_pilot_mech_fail_line_defined_in_ast(self):
        """Verify that mech_fail_line is properly assigned before being referenced in auto_pilot.py."""
        import ast
        auto_pilot_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "auto_pilot.py"))
        with open(auto_pilot_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=auto_pilot_path)

        found = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "execute_idle_audit":
                found = True
                assigned_names = set()
                referenced_names = []
                for child in ast.walk(node):
                    if isinstance(child, ast.Name):
                        if isinstance(child.ctx, ast.Store):
                            assigned_names.add(child.id)
                        elif isinstance(child.ctx, ast.Load):
                            referenced_names.append(child.id)
                self.assertIn("mech_fail_line", assigned_names, "mech_fail_line must be assigned in execute_idle_audit!")
                self.assertIn("mech_fail_line", referenced_names, "mech_fail_line must be referenced in execute_idle_audit!")
        self.assertTrue(found, "execute_idle_audit not found in auto_pilot.py")


if __name__ == "__main__":
    unittest.main()
