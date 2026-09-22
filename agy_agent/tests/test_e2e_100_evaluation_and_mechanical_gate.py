#!/usr/bin/env python3
"""
🧪 Test Suite: E2E 100-Point Checklist Verification & Mechanical Gate Engine
Validates:
  1. MechanicalGate: Objective assertion, file/hash/rendering validation, and AI overrule mechanism.
  2. TrueBlindAuditResult: strategy_adaptation_count & strategy_shifts population.
  3. CrossPlatformE2ERunner: Platform detection & cross-platform dispatching.
  4. Verification of the 20 Core Gates of Autonomous E2E.
"""

import os
import sys
import json
import time
import hashlib
import unittest
from unittest.mock import MagicMock, patch

current_dir = os.path.dirname(os.path.abspath(__file__))
pkg_dir = os.path.abspath(os.path.join(current_dir, ".."))
if pkg_dir not in sys.path:
    sys.path.insert(0, pkg_dir)

from mechanical_gate import MechanicalGate, MechanicalCheck, MechanicalGateResult
from true_blind_e2e_runner import TrueBlindAuditResult, TrueBlindAutonomousRunner
from autonomous_ui_navigator import AutonomousUiNavigator, UiNode, BranchNavigationTracker, BackKeyLoopDefender
from cross_platform_e2e_runner import CrossPlatformE2ERunner


class TestMechanicalGateAnd100Checklist(unittest.TestCase):
    """Test suite for Mechanical Gate and 100 Checklist Engine."""

    def setUp(self):
        self.tmp_files = []

    def tearDown(self):
        for f in self.tmp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_01_mechanical_gate_ai_overrule(self):
        """Mandate 100: AI Auditor claims PASS, but Mechanical Gate OVERRULES to FAIL if evidence missing."""
        # 1. Successful check
        check_pass = MechanicalCheck(name="dummy_ok", passed=True, details="OK")
        res_pass = MechanicalGate.evaluate_gate(
            gate_name="GATE_TEST",
            ai_verdict="PASS",
            checks=[check_pass]
        )
        self.assertEqual(res_pass.verdict, "PASS")
        self.assertFalse(res_pass.ai_overruled)

        # 2. Failed check where AI says PASS -> must be OVERRULED
        check_fail = MechanicalCheck(name="missing_apk", passed=False, details="File not found")
        res_overruled = MechanicalGate.evaluate_gate(
            gate_name="GATE_CRITICAL",
            ai_verdict="PASS",
            checks=[check_pass, check_fail]
        )
        self.assertEqual(res_overruled.verdict, "FAIL")
        self.assertTrue(res_overruled.ai_overruled)
        self.assertIn("AI OVERRULED", res_overruled.summary)
        self.assertEqual(res_overruled.failed_checks, 1)

    def test_02_mechanical_gate_physical_checks(self):
        """Validates PID, File existence, SHA-256 hash calculation, and rendering checks."""
        # Process alive check with current process PID
        current_pid = os.getpid()
        c_pid = MechanicalGate.assert_process_alive(current_pid)
        self.assertTrue(c_pid.passed)

        c_dead_pid = MechanicalGate.assert_process_alive(99999999)
        self.assertFalse(c_dead_pid.passed)

        # File and SHA-256 check
        tmp_path = "/tmp/agy_mech_test.txt"
        self.tmp_files.append(tmp_path)
        content = b"AGY 3.0 Mechanical Evidence Test String"
        expected_hash = hashlib.sha256(content).hexdigest()
        with open(tmp_path, "wb") as f:
            f.write(content)

        c_file = MechanicalGate.assert_file_exists_and_not_empty(tmp_path, min_bytes=10)
        self.assertTrue(c_file.passed)

        c_sha = MechanicalGate.assert_sha256_checksum(tmp_path, expected_hash)
        self.assertTrue(c_sha.passed)

        c_bad_sha = MechanicalGate.assert_sha256_checksum(tmp_path, "00000000000000000000000000000000")
        self.assertFalse(c_bad_sha.passed)

    def test_03_true_blind_audit_result_strategy_adaptation(self):
        """Validates TrueBlindAuditResult stores and serializes strategy_adaptation_count and strategy_shifts."""
        result = TrueBlindAuditResult(
            app_identifier="com.example.testapp",
            discovered_features=["Feature A", "Feature B"],
            expected_ground_truth=["Feature A", "Feature B"],
            missed_features=[],
            false_positives=[],
            actions_executed=12,
            assertions_count=10,
            recovery_attempts=2,
            critical_bugs_missed=0,
            coverage_pct=100.0,
            action_success_pct=100.0,
            assertion_accuracy_pct=100.0,
            recovery_rate_pct=100.0,
            strategy_adaptation_count=3,
            strategy_shifts=[
                "BackKeyLoopDefender: 뒤로가기 루프 감지 후 전방 노드('새 계약서')로 탐색 전략 선회",
                "AutonomousRecovery: 동일 화면 정체(2회) 감지로 롤백 탈출 전략 선회",
                "MultiBranchTracker: 하위 브랜치 탐색 완료로 상위 브랜치('설정') 백트래킹 전략 선회"
            ]
        )
        d = result.to_dict()
        self.assertIn("strategy_adaptation_count", d["metrics"])
        self.assertIn("strategy_shifts", d["metrics"])
        self.assertEqual(d["metrics"]["strategy_adaptation_count"], 3)
        self.assertEqual(len(d["metrics"]["strategy_shifts"]), 3)

    def test_04_autonomous_navigator_strategy_shifts(self):
        """Validates that AutonomousUiNavigator decisions properly tag is_strategy_shift and strategy_shift_reason."""
        # Test stagnant attempts trigger
        nodes = [UiNode(text="Some Button", clickable=True, x1=10, y1=10, x2=100, y2=50)]
        decision = AutonomousUiNavigator.decide_next_action(
            nodes=nodes,
            stagnant_attempts=2
        )
        self.assertTrue(decision.get("is_recovery"))
        self.assertTrue(decision.get("is_strategy_shift"))
        self.assertIn("AutonomousRecovery", decision.get("strategy_shift_reason", ""))

        # Test BackKeyLoopDefender trigger
        back_defender = BackKeyLoopDefender(consecutive_backs=3, max_consecutive_backs=3)
        decision_loop = AutonomousUiNavigator.decide_next_action(
            nodes=nodes,
            back_loop_defender=back_defender
        )
        self.assertTrue(decision_loop.get("is_loop_defense"))
        self.assertTrue(decision_loop.get("is_strategy_shift"))
        self.assertIn("BackKeyLoopDefender", decision_loop.get("strategy_shift_reason", ""))

    def test_05_cross_platform_platform_detection(self):
        """Validates CrossPlatformE2ERunner platform detection across URL, APK, and Windows binaries."""
        self.assertEqual(CrossPlatformE2ERunner.detect_target_platform("https://myapp.com/dashboard"), "WEB")
        self.assertEqual(CrossPlatformE2ERunner.detect_target_platform("index.html"), "WEB")
        self.assertEqual(CrossPlatformE2ERunner.detect_target_platform("PdfFlow-latest.apk"), "ANDROID")
        self.assertEqual(CrossPlatformE2ERunner.detect_target_platform("com.enterprise.pdfeditor"), "ANDROID")
        self.assertEqual(CrossPlatformE2ERunner.detect_target_platform("desktop_app.exe"), "WINDOWS")

    def test_06_rendering_viewport_and_nav_bar_only_detection(self):
        """Validates that nav-bar-only screens and unrendered canvases are strictly rejected (Items 13~17)."""
        from PIL import Image, ImageDraw

        # 1. Total blank solid black screen -> FAIL
        p_solid = "/tmp/agy_test_solid_black.png"
        self.tmp_files.append(p_solid)
        img_solid = Image.new("RGB", (360, 640), color=(0, 0, 0))
        img_solid.save(p_solid)
        chk_solid = MechanicalGate.assert_genuine_rendering(p_solid)
        self.assertFalse(chk_solid.passed)
        self.assertEqual(chk_solid.metadata.get("rejection_reason"), "TOTAL_SOLID_BLANK_SCREEN")

        # 2. Navigation-bar-only screen (content viewport is 100% black, but bottom has nav bar) -> FAIL
        p_navbar = "/tmp/agy_test_nav_bar_only.png"
        self.tmp_files.append(p_navbar)
        img_nav = Image.new("RGB", (360, 640), color=(0, 0, 0))
        draw_nav = ImageDraw.Draw(img_nav)
        # Draw navigation bar at bottom 600..640 with white triangles/circles
        draw_nav.rectangle([(0, 600), (360, 640)], fill=(30, 30, 30))
        draw_nav.polygon([(60, 620), (80, 610), (80, 630)], fill=(255, 255, 255))
        draw_nav.ellipse([(170, 610), (190, 630)], fill=(255, 255, 255))
        draw_nav.rectangle([(280, 610), (300, 630)], fill=(255, 255, 255))
        img_nav.save(p_navbar)

        chk_nav = MechanicalGate.assert_genuine_rendering(p_navbar)
        self.assertFalse(chk_nav.passed)
        self.assertTrue(chk_nav.metadata.get("is_nav_bar_only"))
        self.assertEqual(chk_nav.metadata.get("rejection_reason"), "NAV_BAR_ONLY_EMPTY_CONTENT")

        # 3. Unrendered Canvas / Crash Remnant (92% black background + tiny isolated white box + nav bar) -> FAIL
        p_unrendered = "/tmp/agy_test_unrendered_canvas.png"
        self.tmp_files.append(p_unrendered)
        img_unrend = Image.new("RGB", (360, 640), color=(0, 0, 0))
        draw_unrend = ImageDraw.Draw(img_unrend)
        draw_unrend.rectangle([(100, 100), (160, 140)], fill=(240, 240, 240))  # tiny box
        draw_unrend.rectangle([(0, 600), (360, 640)], fill=(20, 20, 20))
        img_unrend.save(p_unrendered)

        chk_unrend = MechanicalGate.assert_genuine_rendering(p_unrendered)
        self.assertFalse(chk_unrend.passed)
        self.assertEqual(chk_unrend.metadata.get("rejection_reason"), "UNRENDERED_SPARSE_CANVAS_OR_CRASH_REMNANT")

        # 4. Genuine App Screen with buttons, headers, text, vibrant colors -> PASS
        p_genuine = "/tmp/agy_test_genuine_app.png"
        self.tmp_files.append(p_genuine)
        img_genuine = Image.new("RGB", (360, 640), color=(245, 245, 250))
        draw_gen = ImageDraw.Draw(img_genuine)
        # Header
        draw_gen.rectangle([(0, 0), (360, 80)], fill=(41, 128, 185))
        # Card 1
        draw_gen.rectangle([(20, 100), (340, 220)], fill=(255, 255, 255), outline=(200, 200, 200))
        # Button
        draw_gen.rectangle([(40, 250), (320, 310)], fill=(46, 204, 113))
        # Bottom nav bar
        draw_gen.rectangle([(0, 590), (360, 640)], fill=(52, 73, 94))
        img_genuine.save(p_genuine)

        chk_genuine = MechanicalGate.assert_genuine_rendering(p_genuine)
        self.assertTrue(chk_genuine.passed)
        self.assertTrue(chk_genuine.metadata.get("valid_app_screen"))

    def test_07_action_screen_mutation_causality(self):
        """Validates Action <-> Screenshot causality and mutation calculation (Items 21~26)."""
        from PIL import Image, ImageDraw

        p1 = "/tmp/agy_test_before.png"
        p2_same = "/tmp/agy_test_after_same.png"
        p3_mut = "/tmp/agy_test_after_mut.png"
        self.tmp_files.extend([p1, p2_same, p3_mut])

        img1 = Image.new("RGB", (360, 640), color=(255, 255, 255))
        draw1 = ImageDraw.Draw(img1)
        draw1.rectangle([(50, 50), (200, 100)], fill=(100, 100, 255))
        img1.save(p1)
        img1.save(p2_same)

        # 1. Exact identical screenshot after action -> FAIL
        chk_same = MechanicalGate.assert_action_screen_mutation(p1, p2_same)
        self.assertFalse(chk_same.passed)
        self.assertEqual(chk_same.metadata.get("mutation_pct"), 0.0)

        # 2. Mutated screen after tap (e.g. dialog popped up) -> PASS
        img3 = img1.copy()
        draw3 = ImageDraw.Draw(img3)
        draw3.rectangle([(30, 200), (330, 450)], fill=(255, 100, 100))
        img3.save(p3_mut)

        chk_mut = MechanicalGate.assert_action_screen_mutation(p1, p3_mut)
        self.assertTrue(chk_mut.passed)
        self.assertGreater(chk_mut.metadata.get("mutation_pct", 0), 1.0)

    def test_08_unique_screenshot_detection(self):
        """Validates duplicate screenshot filtering (Item 18)."""
        from PIL import Image
        p_a = "/tmp/agy_test_unique_a.png"
        p_b = "/tmp/agy_test_unique_b.png"
        self.tmp_files.extend([p_a, p_b])

        img = Image.new("RGB", (200, 200), color=(128, 128, 128))
        img.save(p_a)
        img.save(p_b)

        # Testing duplicate
        chk_dup = MechanicalGate.assert_unique_screenshot(p_b, [p_a])
        self.assertFalse(chk_dup.passed)
        self.assertIn("duplicate", chk_dup.details.lower())

    def test_09_intent_achievement_verification(self):
        """Validates Intent Achievement assertion distinguishing clicks from outcomes (Item 56)."""
        # 1. Intent satisfied across all conditions -> PASS
        reqs_ok = {
            "ui_button_tapped": True,
            "document_text_injected": True,
            "disk_file_persisted": True,
            "hash_verified_after_reboot": True
        }
        chk_intent_ok = MechanicalGate.assert_intent_achievement("Add & Persist PDF Text", reqs_ok)
        self.assertTrue(chk_intent_ok.passed)
        self.assertEqual(chk_intent_ok.status, "PASS")

        # 2. Button was tapped, but disk persistence failed -> FAIL
        reqs_fail = {
            "ui_button_tapped": True,
            "document_text_injected": True,
            "disk_file_persisted": False,
            "hash_verified_after_reboot": False
        }
        chk_intent_fail = MechanicalGate.assert_intent_achievement("Add & Persist PDF Text", reqs_fail)
        self.assertFalse(chk_intent_fail.passed)
        self.assertEqual(chk_intent_fail.status, "FAIL")
        self.assertIn("disk_file_persisted", chk_intent_fail.details)

    def test_10_mechanical_gate_4_state_machine(self):
        """Validates 4-state machine evaluation: PASS, FAIL, UNVERIFIED, BLOCKED (Item 97)."""
        c_pass = MechanicalCheck(name="c1", passed=True, details="OK")
        c_unverified = MechanicalCheck(name="c2", passed=False, details="Could not verify", is_unverified=True)
        c_blocked = MechanicalCheck(name="c3", passed=False, details="Environment missing", is_blocked=True)
        c_fail = MechanicalCheck(name="c4", passed=False, details="Crash detected")

        # UNVERIFIED verdict
        res_unver = MechanicalGate.evaluate_gate("GATE_TEST", "PASS", [c_pass, c_unverified])
        self.assertEqual(res_unver.verdict, "UNVERIFIED")

        # BLOCKED verdict
        res_block = MechanicalGate.evaluate_gate("GATE_TEST", "PASS", [c_pass, c_blocked])
        self.assertEqual(res_block.verdict, "BLOCKED")

        # FAIL verdict takes precedence over UNVERIFIED
        res_fail = MechanicalGate.evaluate_gate("GATE_TEST", "PASS", [c_pass, c_fail, c_unverified])
        self.assertEqual(res_fail.verdict, "FAIL")

    def test_11_autonomous_e2e_100_auditor_score(self):
        """Validates AutonomousE2E100Auditor scores 98/100 on healthy evidence and penalizes dead screens."""
        from true_blind_e2e_runner import AutonomousE2E100Auditor

        # 1. Healthy production session -> 100% score (or >= 98 points)
        healthy_res = AutonomousE2E100Auditor.audit_session({
            "has_invalid_rendering": False,
            "has_action_causality": True,
            "has_mechanical_gate": True,
            "has_unverified_support": True,
            "is_environment_healthy": True
        })
        self.assertEqual(healthy_res["total_score"], 100)
        self.assertEqual(healthy_res["overall_verdict"], "PASS")
        self.assertEqual(healthy_res["maturity_level"], "Level 4: Autonomous QA Engineer")

        # 2. Dead screen session (replicates user-reported bug) -> strictly penalized
        dead_screen_res = AutonomousE2E100Auditor.audit_session({
            "has_invalid_rendering": True
        })
        self.assertLess(dead_screen_res["total_score"], 95)
        self.assertEqual(dead_screen_res["overall_verdict"], "FAIL")

    def test_12_cross_platform_e2e_runner_100_audit_and_cleanup(self):
        """Validates CrossPlatformE2ERunner integrates 100-Point Audit and resource cleanup for 2~3 day operation."""
        # 1. Resource cleanup invocation
        test_ws = "/tmp/agy_test_ws_cleanup"
        os.makedirs(os.path.join(test_ws, "reports", "gate5", "screenshots"), exist_ok=True)
        CrossPlatformE2ERunner.cleanup_stale_processes_and_buffers(workspace=test_ws)

        # 2. Android E2E integration with mocked session returning pass
        import cross_platform_e2e_runner
        import true_blind_e2e_runner
        with patch.object(cross_platform_e2e_runner.TrueBlindAutonomousRunner, "execute_true_blind_session") as mock_exec, \
             patch.object(true_blind_e2e_runner.TrueBlindAutonomousRunner, "execute_true_blind_session", mock_exec):
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {
                "app_identifier": "com.enterprise.testapp",
                "overall_verdict": "PASS",
                "evidence_bundle": {
                    "e2e_100_audit": {
                        "total_score": 98,
                        "overall_verdict": "PASS"
                    }
                }
            }
            mock_exec.return_value = mock_result

            res = CrossPlatformE2ERunner.run_android_e2e("dummy.apk", workspace=test_ws)
            self.assertIn("e2e_100_audit", res)
            self.assertEqual(res["e2e_100_audit"]["total_score"], 98)
            self.assertEqual(res["mechanical_gate"]["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()

