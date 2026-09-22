#!/usr/bin/env python3
"""
🧪 Unit & Integration Tests for AGY 3.0 Universal Autonomous E2E 160-Point Audit Engine & 6-Phase Pipeline
Validates:
  1. 160-Point Checklist coverage across 8 categories (Items 1 ~ 160)
  2. Phase 0: Workspace & Evidence Purge (rm -rf stale screenshots & JSONs)
  3. Phase 1: Environment Health Check (Hardware gate: adb_state & boot_completed)
  4. Phase 2: Target Deploy & Foreground Verification (dumpsys mCurrentFocus matching)
  5. Mechanical Gate Strict Evaluation & 95.0+ Threshold Approval (PASS / FAIL / BLOCKED)
  6. Defect Penalization: Blank renders, stale artifacts, wrong app focus, prompt leakage
  7. CrossPlatformE2ERunner integration with 160-Point Audit
"""

import os
import sys
import shutil
import tempfile
import time
import unittest

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from autonomous_e2e_160_auditor import AutonomousE2E160Auditor
from mechanical_gate import MechanicalGate, MechanicalCheck
from cross_platform_e2e_runner import CrossPlatformE2ERunner


class TestAutonomousE2E160Pipeline(unittest.TestCase):

    def setUp(self):
        self.test_ws = tempfile.mkdtemp(prefix="agy_160_test_ws_")

    def tearDown(self):
        shutil.rmtree(self.test_ws, ignore_errors=True)

    def test_01_checklist_160_structure_and_completeness(self):
        """Validates all 160 checklist items across 8 distinct categories are rigorously defined."""
        items = AutonomousE2E160Auditor.CHECKLIST_160
        self.assertEqual(len(items), 160)
        self.assertEqual(len(AutonomousE2E160Auditor.CATEGORIES), 8)

        # Verify items per category (20 items each)
        cat_counts = {}
        for item_no, cat_id, title in items:
            cat_counts[cat_id] = cat_counts.get(cat_id, 0) + 1
            self.assertTrue(bool(title.strip()))

        for cat_id in range(1, 9):
            self.assertEqual(cat_counts[cat_id], 20, f"Category {cat_id} must have exactly 20 items")

    def test_02_phase0_workspace_and_evidence_purge(self):
        """Validates Phase 0 mechanically burns down stale artifacts before E2E start."""
        gate4_dir = os.path.join(self.test_ws, "reports", "gate4")
        gate5_dir = os.path.join(self.test_ws, "reports", "gate5", "screenshots")
        os.makedirs(gate4_dir, exist_ok=True)
        os.makedirs(gate5_dir, exist_ok=True)

        # Create stale artifacts
        stale_shot = os.path.join(gate5_dir, "stale_round_1.png")
        stale_json = os.path.join(gate4_dir, "stale_report.json")
        with open(stale_shot, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        with open(stale_json, "w") as f:
            f.write('{"status": "OLD"}')

        self.assertTrue(os.path.exists(stale_shot))
        self.assertTrue(os.path.exists(stale_json))

        # Execute Phase 0 Purge
        purge_res = AutonomousE2E160Auditor.execute_phase0_workspace_purge(self.test_ws)
        self.assertTrue(purge_res["success"])
        self.assertGreaterEqual(purge_res["purged_count"], 2)

        # Confirm files are burned
        self.assertFalse(os.path.exists(stale_shot))
        self.assertFalse(os.path.exists(stale_json))
        # Directory must remain clean
        self.assertTrue(os.path.isdir(gate4_dir))
        self.assertTrue(os.path.isdir(gate5_dir))

    def test_03_phase1_hardware_gate_and_blocked(self):
        """Validates Phase 1 halts execution with BLOCKED without LLM intervention when hardware gate fails."""
        # Unhealthy ADB state
        res_fail_adb = AutonomousE2E160Auditor.execute_phase1_environment_health_check("ANDROID", adb_state="offline", boot_completed=True)
        self.assertEqual(res_fail_adb["status"], "BLOCKED")
        self.assertFalse(res_fail_adb["is_runnable"])

        # Unhealthy boot_completed
        res_fail_boot = AutonomousE2E160Auditor.execute_phase1_environment_health_check("ANDROID", adb_state="device", boot_completed=False)
        self.assertEqual(res_fail_boot["status"], "BLOCKED")
        self.assertFalse(res_fail_boot["is_runnable"])

        # Healthy state
        res_ok = AutonomousE2E160Auditor.execute_phase1_environment_health_check("ANDROID", adb_state="device", boot_completed=True)
        self.assertEqual(res_ok["status"], "PASS")
        self.assertTrue(res_ok["is_runnable"])

    def test_04_phase2_foreground_focus_verification(self):
        """Validates Phase 2 ensures dumpsys window focus matches target package mechanically."""
        target_pkg = "com.pdfflow.app"

        # Correct target in focus
        focus_ok = "mCurrentFocus=Window{12345 u0 com.pdfflow.app/com.pdfflow.app.MainActivity}"
        res_ok = AutonomousE2E160Auditor.execute_phase2_foreground_verification(focus_ok, target_pkg)
        self.assertEqual(res_ok["status"], "PASS")
        self.assertTrue(res_ok["matched"])

        # Wrong app in focus (e.g. 이전 프로젝트 '드실' 또는 런처)
        focus_wrong = "mCurrentFocus=Window{67890 u0 com.deusil.delivery/com.deusil.MainActivity}"
        res_wrong = AutonomousE2E160Auditor.execute_phase2_foreground_verification(focus_wrong, target_pkg)
        self.assertEqual(res_wrong["status"], "FAIL")
        self.assertFalse(res_wrong["matched"])

    def test_05_160_point_scoring_and_approval_threshold(self):
        """Validates normal clean session achieves >= 95.0 score (98.12) and is APPROVED (PASS)."""
        eval_res = AutonomousE2E160Auditor.evaluate_160_point_checklist({})
        self.assertEqual(eval_res["total_items"], 160)
        self.assertEqual(eval_res["passed_items"], 157)
        self.assertEqual(eval_res["unverified_items"], 3)
        self.assertEqual(eval_res["failed_items"], 0)
        self.assertEqual(eval_res["blocked_items"], 0)
        self.assertEqual(eval_res["total_score"], 98.12)
        self.assertTrue(eval_res["is_approved"])
        self.assertEqual(eval_res["overall_verdict"], "PASS")

        # Verify Category scores (Categories 1, 2, 6 have 1 unverified item each -> 95.0%, others 100%)
        self.assertEqual(eval_res["categories"][1]["passed"], 19)
        self.assertEqual(eval_res["categories"][2]["passed"], 19)
        self.assertEqual(eval_res["categories"][3]["passed"], 20)
        self.assertEqual(eval_res["categories"][4]["passed"], 20)
        self.assertEqual(eval_res["categories"][5]["passed"], 20)
        self.assertEqual(eval_res["categories"][6]["passed"], 19)
        self.assertEqual(eval_res["categories"][7]["passed"], 20)
        self.assertEqual(eval_res["categories"][8]["passed"], 20)

    def test_06_160_point_scoring_penalization_on_rendering_failure(self):
        """Validates rendering failure (blank canvas/nav bar only) drops score below 95.0 and FAILS."""
        eval_res = AutonomousE2E160Auditor.evaluate_160_point_checklist({
            "has_invalid_rendering": True
        })
        self.assertLess(eval_res["total_score"], 95.0)
        self.assertFalse(eval_res["is_approved"])
        self.assertEqual(eval_res["overall_verdict"], "FAIL")

    def test_07_160_point_scoring_penalization_on_wrong_app_focus(self):
        """Validates wrong app running in foreground drops score below 95.0 and FAILS."""
        eval_res = AutonomousE2E160Auditor.evaluate_160_point_checklist({
            "has_wrong_app_focus": True
        })
        self.assertLess(eval_res["total_score"], 95.0)
        self.assertFalse(eval_res["is_approved"])
        self.assertEqual(eval_res["overall_verdict"], "FAIL")

    def test_08_cross_platform_runner_integration(self):
        """Validates CrossPlatformE2ERunner integrates 160-point evaluation."""
        from unittest.mock import patch, MagicMock
        with patch("cross_platform_e2e_runner.TrueBlindAutonomousRunner.execute_true_blind_session") as mock_exec:
            mock_res = MagicMock()
            mock_res.to_dict.return_value = {
                "app_identifier": "test.apk",
                "overall_verdict": "PASS",
                "action_success_pct": 100.0,
                "evidence_bundle": {
                    "screenshots": ["/tmp/shot.png"],
                    "transitions": [{"is_meaningful_mutation": True}]
                }
            }
            mock_exec.return_value = mock_res
            res = CrossPlatformE2ERunner.run_android_e2e("test.apk", workspace=self.test_ws)

            self.assertIn("e2e_160_audit", res)
            audit_160 = res["e2e_160_audit"]
            self.assertGreaterEqual(audit_160["total_score"], 95.0)
            self.assertTrue(audit_160["is_approved"])

    def test_09_android_emulator_manager_phase2_focus_check(self):
        """Validates AndroidEmulatorManager install_and_launch_apk verifies foreground focus mechanically."""
        from android_emulator_manager import AndroidEmulatorManager
        from unittest.mock import patch, MagicMock

        mgr = AndroidEmulatorManager(workspace=self.test_ws)

        # Case A: Target app in foreground -> PASS & foreground_verified=True
        def mock_adb_ok(args, timeout=15.0):
            cmd_str = " ".join(args)
            if "getprop sys.boot_completed" in cmd_str:
                return 0, "1", ""
            if "service check activity" in cmd_str:
                return 0, "Service activity: found", ""
            if "install" in cmd_str:
                return 0, "Success", ""
            if "am start" in cmd_str:
                return 0, "Status: ok", ""
            if "dumpsys window" in cmd_str:
                return 0, "mCurrentFocus=Window{123 u0 com.target.app/com.target.app.MainActivity}", ""
            return 0, "", ""

        dummy_apk = os.path.join(self.test_ws, "target.apk")
        with open(dummy_apk, "wb") as f:
            f.write(b"dummy")

        with patch.object(mgr, "run_adb_cmd", side_effect=mock_adb_ok), patch("time.sleep"):
            res_ok = mgr.install_and_launch_apk(dummy_apk, "com.target.app", "MainActivity")
            self.assertTrue(res_ok["launch_success"])
            self.assertTrue(res_ok.get("foreground_verified", False))

        # Case B: Other app (e.g. '드실') in foreground -> FAIL & foreground_verified=False
        def mock_adb_wrong(args, timeout=15.0):
            cmd_str = " ".join(args)
            if "getprop sys.boot_completed" in cmd_str:
                return 0, "1", ""
            if "service check activity" in cmd_str:
                return 0, "Service activity: found", ""
            if "install" in cmd_str:
                return 0, "Success", ""
            if "am start" in cmd_str:
                return 0, "Status: ok", ""
            if "dumpsys window" in cmd_str:
                return 0, "mCurrentFocus=Window{456 u0 com.deusil.delivery/com.deusil.MainActivity}", ""
            return 0, "", ""

        with patch.object(mgr, "run_adb_cmd", side_effect=mock_adb_wrong), patch("time.sleep"):
            res_wrong = mgr.install_and_launch_apk(dummy_apk, "com.target.app", "MainActivity")
            self.assertFalse(res_wrong["launch_success"])
            self.assertFalse(res_wrong.get("foreground_verified", True))
            self.assertTrue(any("Foreground window mismatch" in err for err in res_wrong["errors"]))

    def test_10_phase3_deterministic_capture_and_freeze_detection(self):
        """Validates Phase 3 detects stale screenshots and consecutive identical duplicate frames (UI Freeze)."""
        now = time.time()
        shot1 = os.path.join(self.test_ws, "shot1.png")
        shot2 = os.path.join(self.test_ws, "shot2.png")
        with open(shot1, "wb") as f:
            f.write(b"frame_1_content_distinct")
        with open(shot2, "wb") as f:
            f.write(b"frame_2_content_different")

        # Distinct shots -> PASS
        res_ok = AutonomousE2E160Auditor.execute_phase3_deterministic_evidence_capture([shot1, shot2], session_start_time=now - 5.0)
        self.assertEqual(res_ok["status"], "PASS")

        # Duplicate shots -> UI Freeze FAIL
        shot3 = os.path.join(self.test_ws, "shot3.png")
        with open(shot3, "wb") as f:
            f.write(b"frame_2_content_different")  # Identical to shot2
        res_freeze = AutonomousE2E160Auditor.execute_phase3_deterministic_evidence_capture([shot1, shot2, shot3], session_start_time=now - 5.0)
        self.assertEqual(res_freeze["status"], "FAIL")
        self.assertIn("UI Freeze", res_freeze["reason"])

    def test_11_phase4_critic_isolation_and_domain_guard(self):
        """Validates Phase 4 catches domain contamination (e.g. game terms in PDF app)."""
        clean_report = {"summary": "PDF text editing and rendering verified", "score": 98}
        res_ok = AutonomousE2E160Auditor.execute_phase4_llm_critic_isolation(clean_report, target_domain="document_utility")
        self.assertEqual(res_ok["status"], "PASS")
        self.assertTrue(res_ok["domain_clean"])

        contaminated_report = {"summary": "PDF app verified, joystick controls and jump sfx feel responsive", "score": 98}
        res_bad = AutonomousE2E160Auditor.execute_phase4_llm_critic_isolation(contaminated_report, target_domain="document_utility")
        self.assertEqual(res_bad["status"], "FAIL")
        self.assertFalse(res_bad["domain_clean"])

    def test_12_phase5_circuit_breaker_and_rollback(self):
        """Validates Phase 5 trips circuit breaker at 5 failures and orders git rollback."""
        res_normal = AutonomousE2E160Auditor.execute_phase5_circuit_breaker(failure_count=3, max_allowed_failures=5)
        self.assertFalse(res_normal["circuit_tripped"])
        self.assertEqual(res_normal["action"], "DISPATCH_DIFF_TO_CODER")

        res_trip = AutonomousE2E160Auditor.execute_phase5_circuit_breaker(failure_count=5, max_allowed_failures=5)
        self.assertTrue(res_trip["circuit_tripped"])
        self.assertEqual(res_trip["status"], "CIRCUIT_BREAKER_TRIPPED")
        self.assertEqual(res_trip["action"], "ROLLBACK_TO_GIT_HEAD")

    def test_13_hardware_blocked_yields_zero_score_and_blocked_verdict(self):
        """Validates that hardware failure (is_environment_blocked) sets overall verdict to BLOCKED and score to 0.0."""
        eval_res = AutonomousE2E160Auditor.evaluate_160_point_checklist({
            "is_environment_blocked": True,
            "adb_state": "offline",
            "boot_completed": False
        })
        self.assertEqual(eval_res["overall_verdict"], "BLOCKED")
        self.assertEqual(eval_res["total_score"], 0.0)
        self.assertFalse(eval_res["is_approved"])
        self.assertGreater(eval_res["blocked_items"], 0)

    def test_14_build_session_data_from_e2e_run_telemetry(self):
        """Validates that build_session_data_from_e2e_run assembles full telemetry and evaluates accurately."""
        # Case A: Healthy run
        session_healthy = AutonomousE2E160Auditor.build_session_data_from_e2e_run(
            workspace=self.test_ws,
            platform="ANDROID",
            phase0_res={"success": True, "purged_count": 5},
            phase1_res={"status": "PASS", "is_runnable": True},
            phase2_res={"status": "PASS", "matched": True},
            phase3_res={"status": "PASS", "evidence_count": 4},
            phase4_res={"status": "PASS", "domain_clean": True},
            phase5_res={"status": "LOOP_PERMITTED", "circuit_tripped": False},
            screenshots=["shot1.png", "shot2.png"]
        )
        self.assertTrue(session_healthy["workspace_purged"])
        self.assertTrue(session_healthy["is_environment_healthy"])
        self.assertFalse(session_healthy["is_environment_blocked"])

        eval_healthy = AutonomousE2E160Auditor.evaluate_160_point_checklist(session_healthy)
        self.assertEqual(eval_healthy["overall_verdict"], "PASS")
        self.assertGreaterEqual(eval_healthy["total_score"], 95.0)
        self.assertTrue(eval_healthy["is_approved"])

        # Case B: Blocked run
        session_blocked = AutonomousE2E160Auditor.build_session_data_from_e2e_run(
            workspace=self.test_ws,
            platform="ANDROID",
            phase1_res={"status": "BLOCKED", "is_runnable": False}
        )
        eval_blocked = AutonomousE2E160Auditor.evaluate_160_point_checklist(session_blocked)
        self.assertEqual(eval_blocked["overall_verdict"], "BLOCKED")
        self.assertEqual(eval_blocked["total_score"], 0.0)
        self.assertFalse(eval_blocked["is_approved"])

    def test_15_per_item_detailed_four_state_integrity(self):
        """Validates that all 160 items have deterministic 4-state statuses (PASS, FAIL, BLOCKED, UNVERIFIED)."""
        eval_res = AutonomousE2E160Auditor.evaluate_160_point_checklist({})
        self.assertEqual(len(eval_res["detailed_items"]), 160)
        valid_states = {"PASS", "FAIL", "BLOCKED", "UNVERIFIED"}
        for item in eval_res["detailed_items"]:
            self.assertIn(item["status"], valid_states)
            self.assertTrue(len(item["evidence"]) > 0)

    def test_16_phase5_unattended_48h_endurance_guard(self):
        """Validates 48h unattended autonomous endurance guard mechanics and resource thresholds."""
        guard_res = AutonomousE2E160Auditor.execute_unattended_endurance_guard(
            uptime_hours=48.0,
            consecutive_clean_cycles=24,
            max_memory_mb=512.0,
            min_disk_free_gb=1.0
        )
        self.assertEqual(guard_res["status"], "PASS")
        self.assertTrue(guard_res["endurance_ready"])
        self.assertTrue(guard_res["watchdog_active"])
        self.assertTrue(guard_res["circuit_breaker_ready"])
        self.assertGreater(guard_res["disk_free_gb"], 0)

        # Test failure under extreme disk constraint
        guard_fail = AutonomousE2E160Auditor.execute_unattended_endurance_guard(
            min_disk_free_gb=9999999.0
        )
        self.assertEqual(guard_fail["status"], "FAIL")
        self.assertFalse(guard_fail["endurance_ready"])


if __name__ == "__main__":
    unittest.main()

