#!/usr/bin/env python3
"""
🧪 Test Suite: True Blind Real-World Autonomous E2E Evaluation
Verifies TrueBlindAutonomousRunner under strictly zero-mock conditions:
1. Physical APK Zero-Hint Inspection on Real Android Binaries (aapt dump badging)
2. Screen & Network Defect Detection Engine (sub-48dp, 2-line wrap, crash, ANR)
3. Physical ADB Device Connection Enforcement (Anti-Mocking: strictly no fake harness)
4. Physical Real-World Autonomous E2E Sessions (gated by real device connectivity)
5. Gate 4 Audit Report Evidence Generation
"""

import os
import sys
import time
import json
import unittest

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from true_blind_e2e_runner import TrueBlindAutonomousRunner, TrueBlindAuditResult
from android_emulator_manager import AndroidEmulatorManager
from autonomous_ui_navigator import AutonomousUiNavigator, BranchNavigationTracker, UiNode


class TestTrueBlindRealWorldE2E(unittest.TestCase):

    def setUp(self):
        self.mgr = AndroidEmulatorManager()

    @staticmethod
    def _resolve_apk(filename: str, legacy_path: str) -> Optional[str]:
        candidates = [
            legacy_path,
            os.path.join(parent_dir, filename),
            os.path.join(os.path.dirname(parent_dir), filename),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def test_physical_apk_zero_hint_inspection(self):
        """
        Scenario 1: Physical APK Zero-Hint Inspection on Real Android Binaries.
        Directly extracts package, main activity, permissions, and archetypes from physical APKs
        without any prior human hints or genre pre-assignment.
        """
        fluffy_apk = self._resolve_apk(
            "FluffyPost-latest.apk",
            "/home/jangsung/Desktop/autocoding/!1_vibecoding/fluffy_post_cloud_odyssey_android/FluffyPost-latest.apk"
        )
        if not fluffy_apk:
            self.skipTest("FluffyPost APK binary not found on local disk - skipping physical APK test")
        self.assertIsNotNone(fluffy_apk, "FluffyPost APK binary must exist on system")
        info_game = TrueBlindAutonomousRunner.introspect_unknown_target(fluffy_apk)
        self.assertEqual(info_game["package_name"], "com.fluffypost.cloudodyssey")
        self.assertEqual(info_game["main_activity"], "com.fluffypost.cloudodyssey.MainActivity")
        self.assertTrue(info_game["is_game_canvas"])
        self.assertEqual(info_game["inferred_archetype"], "HIGH_PERF_GAMING")
        self.assertIn("android.permission.INTERNET", info_game["permissions"])

        park_apk = self._resolve_apk(
            "ParkMemo-latest.apk",
            "/home/jangsung/Desktop/autocoding/!1_vibecoding/parking_lot/ParkMemo-latest.apk"
        )
        if not park_apk:
            self.skipTest("ParkMemo APK binary not found on local disk - skipping physical APK test")
        self.assertIsNotNone(park_apk, "ParkMemo APK binary must exist on system")
        info_util = TrueBlindAutonomousRunner.introspect_unknown_target(park_apk)
        self.assertEqual(info_util["package_name"], "com.autocoding.parkmemo")
        self.assertEqual(info_util["main_activity"], "com.autocoding.parkmemo.ui.MainActivity")
        self.assertEqual(info_util["inferred_archetype"], "UTILITY_PRODUCTIVITY")
        self.assertIn("android.permission.INTERNET", info_util["permissions"])

    def test_screen_and_network_defect_detection_logic(self):
        """
        Scenario 2: Autonomous Defect Detection Algorithm Verification.
        Directly verifies defect scanners on parsed UI hierarchies:
        - 2-line button text wrapping (\n)
        - Sub-48dp touch target crowding (< 44px)
        - Fatal Crash banner in UI
        - ANR (Application Not Responding) dialog
        - Unhandled network error
        """
        defect_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation="0">
          <node index="0" text="" class="android.widget.FrameLayout" package="com.app" bounds="[0,0][1080,2400]">
            <node index="0" text="새 계약서 작성" resource-id="com.app:id/btn_action" class="android.widget.Button" clickable="true" enabled="true" bounds="[60,100][500,200]" />
            <node index="1" text="결제 진행하기\\n(부가세 별도)" resource-id="com.app:id/btn_wrapped" class="android.widget.Button" clickable="true" enabled="true" bounds="[60,240][500,380]" />
            <node index="2" text="초소형 버튼" resource-id="com.app:id/btn_tiny" class="android.widget.Button" clickable="true" enabled="true" bounds="[60,400][90,430]" />
            <node index="3" text="치명적 예외: 앱이 중단되었습니다. (FATAL EXCEPTION: NullPointerException)" resource-id="com.app:id/tv_crash" class="android.widget.TextView" bounds="[60,500][1000,600]" />
            <node index="4" text="경고: com.app isn't responding (응답하지 않습니다)" resource-id="com.app:id/tv_anr" class="android.widget.TextView" bounds="[60,650][1000,750]" />
          </node>
        </hierarchy>"""
        nodes = AutonomousUiNavigator.parse_xml_hierarchy(defect_xml)
        defects = TrueBlindAutonomousRunner.detect_screen_defects(nodes)
        defect_ids = [d["defect_id"] for d in defects]

        self.assertIn("DEFECT_2LINE_BUTTON_WRAP", defect_ids)
        self.assertIn("DEFECT_TOUCH_TARGET_CROWDING", defect_ids)
        self.assertIn("DEFECT_CRASH", defect_ids)
        self.assertIn("DEFECT_ANR", defect_ids)

        # Network defect detection
        net_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation="0">
          <node index="0" text="" class="android.widget.FrameLayout" package="com.app" bounds="[0,0][1080,2400]">
            <node index="0" text="500 Internal Server Error" class="android.widget.TextView" bounds="[60,100][1000,200]" />
          </node>
        </hierarchy>"""
        net_nodes = AutonomousUiNavigator.parse_xml_hierarchy(net_xml)
        net_defects = TrueBlindAutonomousRunner.detect_network_defects(net_nodes, is_offline=True)
        self.assertTrue(any(d["defect_id"] == "DEFECT_UNHANDLED_NETWORK_DISCONNECT" for d in net_defects))

    def test_device_disconnected_exception_enforcement(self):
        """
        Scenario 3: Anti-Mocking Enforcement — Refusal of Fake Execution When Device Disconnected.
        Verifies that execute_true_blind_session strictly raises ConnectionError
        when no physical device/emulator is connected via ADB, rather than mocking success.
        """
        disconnected_mgr = AndroidEmulatorManager()
        disconnected_mgr.target_device = "non_existent_device:9999"
        
        with self.assertRaises(ConnectionError):
            TrueBlindAutonomousRunner.execute_true_blind_session(
                target_path_or_pkg="/mock/unknown/AppEnterprise.apk",
                emulator_mgr=disconnected_mgr
            )

    def test_multi_branch_navigation_tracker_architecture(self):
        """
        Scenario 3.5: 6. 여러 브랜치 탐색 (Multi-Branch Navigation Tracker Architecture).
        Verifies branch registration, hierarchical branch tree tracking, breadcrumbs,
        backtracking triggers, and serialization under zero-mock conditions.
        """
        tracker = BranchNavigationTracker(max_branch_depth=4)
        tracker.register_branch("설정 메뉴", parent_branch="Root")
        tracker.register_branch("테마 설정", parent_branch="설정 메뉴")
        self.assertIn("설정 메뉴", tracker.discovered_branches)
        self.assertIn("테마 설정", tracker.discovered_branches)
        self.assertIn("설정 메뉴", tracker.branch_tree["Root"])
        self.assertIn("테마 설정", tracker.branch_tree["설정 메뉴"])

        tracker.record_step("screen_main", "btn_settings", branch_hint="설정 메뉴")
        self.assertEqual(tracker.current_depth, 1)
        self.assertEqual(tracker.breadcrumbs, ["Root", "설정 메뉴"])

        # Backtrack trigger test
        self.assertTrue(tracker.should_backtrack("screen_main", ["btn_settings"]))
        popped = tracker.backtrack_pop()
        self.assertEqual(popped, "screen_main")
        self.assertEqual(tracker.current_depth, 0)
        self.assertEqual(tracker.breadcrumbs, ["Root"])

        # Serialization test
        data = tracker.to_dict()
        self.assertEqual(data["step"], "6. 여러 브랜치 탐색")
        self.assertEqual(data["backtracks_executed"], 1)

        # Autonomous step decision with branch node
        node_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation="0">
          <node index="0" text="" class="android.widget.FrameLayout" package="com.app" bounds="[0,0][1080,2400]">
            <node index="0" text="환경설정 탭" resource-id="com.app:id/tab_settings" class="android.widget.Button" clickable="true" enabled="true" bounds="[60,100][500,200]" />
          </node>
        </hierarchy>"""
        nodes = AutonomousUiNavigator.parse_xml_hierarchy(node_xml)
        decision = AutonomousUiNavigator.decide_next_action(nodes, branch_tracker=tracker)
        self.assertTrue(decision.get("is_branch_entry"))
        self.assertEqual(decision.get("branch_name"), "환경설정 탭")

    def test_true_blind_audit_report_evidence_generation(self):
        """
        Scenario 4: Physical Gate 4 Evidence File Verification.
        Verifies the Gate 4 True Blind audit report structure and content on disk.
        """
        report_path = os.path.join(parent_dir, "reports", "gate4", "true_blind_audit_report.json")
        self.assertTrue(os.path.exists(report_path), "Gate 4 True Blind report must exist on disk")
        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("metrics", data)
        self.assertIn("coverage_pct", data["metrics"])
        self.assertIn("bug_discovery_recall_pct", data["metrics"])
        self.assertIn("bug_discovery_precision_pct", data["metrics"])
        self.assertIn("evidence_bundle", data)
        self.assertIn("supported_capabilities", data["evidence_bundle"])
        self.assertIn("6. 여러 브랜치 탐색", data["evidence_bundle"]["supported_capabilities"])

    def test_true_blind_business_app_physical_adb_e2e(self):
        """
        Scenario 5: Truly Unknown Business Application Physical ADB E2E.
        Only executes when a physical Android device or AVD emulator is connected.
        Installs real APK binary to the device and measures raw physical UI dump responses.
        """
        if not self.mgr.is_adb_connected():
            self.skipTest("Physical Android device/emulator required via ADB (connect real device or launch AVD)")

        park_apk = self._resolve_apk("ParkMemo-latest.apk", "/home/jangsung/Desktop/autocoding/!1_vibecoding/parking_lot/ParkMemo-latest.apk")
        self.assertIsNotNone(park_apk, "ParkMemo APK binary must exist on system")
        result = TrueBlindAutonomousRunner.execute_true_blind_session(
            target_path_or_pkg=park_apk,
            max_steps=6,
            is_game_target=False,
            emulator_mgr=self.mgr
        )
        self.assertIn(result.overall_verdict, ["PASS", "FAIL"])
        self.assertGreaterEqual(result.actions_executed, 1)

    def test_true_blind_game_canvas_physical_adb_e2e(self):
        """
        Scenario 6: Unknown Game Canvas SurfaceView Physical ADB E2E.
        Only executes when a physical Android device or AVD emulator is connected.
        """
        if not self.mgr.is_adb_connected():
            self.skipTest("Physical Android device/emulator required via ADB (connect real device or launch AVD)")

        fluffy_apk = self._resolve_apk("FluffyPost-latest.apk", "/home/jangsung/Desktop/autocoding/!1_vibecoding/fluffy_post_cloud_odyssey_android/FluffyPost-latest.apk")
        self.assertIsNotNone(fluffy_apk, "FluffyPost APK binary must exist on system")
        result = TrueBlindAutonomousRunner.execute_true_blind_session(
            target_path_or_pkg=fluffy_apk,
            is_game_target=True,
            emulator_mgr=self.mgr
        )
        self.assertIn(result.overall_verdict, ["PASS", "FAIL"])
        self.assertGreaterEqual(result.actions_executed, 1)

    def test_requirement_6_multi_branch_physical_adb_navigation(self):
        """
        Scenario 7: 6. 여러 브랜치 탐색 Physical ADB Exploration & Backtracking.
        Only executes when a physical Android device or AVD emulator is connected.
        """
        if not self.mgr.is_adb_connected():
            self.skipTest("Physical Android device/emulator required via ADB (connect real device or launch AVD)")

        park_apk = self._resolve_apk("ParkMemo-latest.apk", "/home/jangsung/Desktop/autocoding/!1_vibecoding/parking_lot/ParkMemo-latest.apk")
        self.assertIsNotNone(park_apk, "ParkMemo APK binary must exist on system")
        result = TrueBlindAutonomousRunner.execute_true_blind_session(
            target_path_or_pkg=park_apk,
            max_steps=8,
            is_game_target=False,
            emulator_mgr=self.mgr
        )
        self.assertIn("multi_branch_navigation", result.evidence_bundle)
        mb_info = result.evidence_bundle["multi_branch_navigation"]
        self.assertEqual(mb_info["step"], "6. 여러 브랜치 탐색")


if __name__ == "__main__":
    unittest.main()

