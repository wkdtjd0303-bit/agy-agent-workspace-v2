#!/usr/bin/env python3
"""
🧪 Test Suite: Autonomous E2E Evolution & Self-Verification (AGY 3.0 Standard)
Verifies:
1. AutonomousUiNavigator (UiAutomator XML parsing, Onboarding/Permission bypass, center coordinate calculation)
2. ScenarioGraphEngine (0%~100% Verification Graphs, Checkpoint filtering from 50%/70%, Domain adaptation)
3. AndroidEmulatorManager (Battery & Notification dumpsys parsing, sensor simulation, camera injection)
4. Truthful Visual Diff & State Transition Classification
"""

import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from autonomous_ui_navigator import AutonomousUiNavigator, UiNode, BackKeyLoopDefender
from scenario_graph_engine import ScenarioGraphEngine, ScenarioGraph, ScenarioCheckpoint
from android_emulator_manager import AndroidEmulatorManager


SAMPLE_ONBOARDING_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.pdfflow.app" content-desc="" bounds="[0,0][1080,2400]">
    <node index="0" text="" resource-id="com.pdfflow.app:id/tutorial_dialog" class="android.widget.LinearLayout" package="com.pdfflow.app" bounds="[100,400][980,1800]">
      <node index="0" text="PdfFlow 사용 가이드" resource-id="com.pdfflow.app:id/title" class="android.widget.TextView" bounds="[140,450][800,530]" />
      <node index="1" text="1/5" resource-id="com.pdfflow.app:id/page_num" class="android.widget.TextView" bounds="[820,450][940,530]" />
      <node index="2" text="기존 PDF 글자를 탭해서 바로 수정" class="android.widget.TextView" bounds="[140,900][940,1050]" />
      <node index="3" text="건너뛰기" resource-id="com.pdfflow.app:id/btn_skip" class="android.widget.Button" clickable="true" enabled="true" bounds="[140,1650][450,1750]" />
      <node index="4" text="다음 단계 >" resource-id="com.pdfflow.app:id/btn_next" class="android.widget.Button" clickable="true" enabled="true" bounds="[500,1650][940,1750]" />
    </node>
  </node>
</hierarchy>"""

SAMPLE_PERMISSION_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.google.android.permissioncontroller" bounds="[0,0][1080,2400]">
    <node index="0" text="PdfFlow에서 사진 및 동영상을 촬영하도록 허용하시겠습니까?" class="android.widget.TextView" bounds="[100,900][980,1100]" />
    <node index="1" text="앱 사용 중에만 허용" resource-id="com.android.permissioncontroller:id/permission_allow_foreground_only_button" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,1200][980,1320]" />
    <node index="2" text="이번만 허용" resource-id="com.android.permissioncontroller:id/permission_allow_one_time_button" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,1340][980,1460]" />
    <node index="3" text="허용 안함" resource-id="com.android.permissioncontroller:id/permission_deny_button" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,1480][980,1600]" />
  </node>
</hierarchy>"""

SAMPLE_MAIN_EDITOR_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.pdfflow.app" bounds="[0,0][1080,2400]">
    <node index="0" text="" resource-id="com.pdfflow.app:id/top_toolbar" class="android.widget.LinearLayout" bounds="[0,80][1080,200]">
      <node index="0" text="서명" resource-id="com.pdfflow.app:id/btn_sign" class="android.widget.Button" clickable="true" enabled="true" bounds="[40,90][200,190]" />
      <node index="1" text="텍스트 추가" resource-id="com.pdfflow.app:id/btn_add_text" class="android.widget.Button" clickable="true" enabled="true" bounds="[220,90][440,190]" />
      <node index="2" text="저장" resource-id="com.pdfflow.app:id/btn_save" class="android.widget.Button" clickable="true" enabled="true" bounds="[860,90][1040,190]" />
    </node>
    <node index="1" text="" resource-id="com.pdfflow.app:id/edit_input" class="android.widget.EditText" focusable="true" clickable="true" enabled="true" bounds="[60,300][1020,450]" />
    <node index="2" text="" resource-id="com.pdfflow.app:id/pdf_scroll" class="android.widget.ScrollView" scrollable="true" bounds="[0,500][1080,2200]" />
  </node>
</hierarchy>"""


class TestAutonomousE2EEvolution(unittest.TestCase):

    def test_parse_ui_hierarchy_and_bounds(self):
        """Verify XML hierarchy parsing and center coordinate calculation."""
        nodes = AutonomousUiNavigator.parse_xml_hierarchy(SAMPLE_ONBOARDING_XML)
        self.assertGreater(len(nodes), 3)

        btn_next = next((n for n in nodes if "다음 단계" in n.text), None)
        self.assertIsNotNone(btn_next)
        self.assertTrue(btn_next.clickable)
        self.assertEqual(btn_next.x1, 500)
        self.assertEqual(btn_next.y1, 1650)
        self.assertEqual(btn_next.x2, 940)
        self.assertEqual(btn_next.y2, 1750)
        self.assertEqual(btn_next.center_x, 720)
        self.assertEqual(btn_next.center_y, 1700)

    def test_classify_and_bypass_onboarding(self):
        """Verify intelligent detection and bypass of onboarding modals."""
        nodes = AutonomousUiNavigator.parse_xml_hierarchy(SAMPLE_ONBOARDING_XML)
        state = AutonomousUiNavigator.classify_screen_state(nodes)
        self.assertEqual(state["type"], "ONBOARDING_MODAL")

        action = AutonomousUiNavigator.decide_next_action(nodes)
        self.assertEqual(action["action"], "tap")
        # Should click either '건너뛰기' or '다음 단계'
        self.assertIn(action["x"], [ (140+450)//2, (500+940)//2 ])
        self.assertIn("온보딩", action["reason"])

    def test_classify_and_bypass_permission_prompt(self):
        """Verify intelligent detection and auto-granting of runtime permissions."""
        nodes = AutonomousUiNavigator.parse_xml_hierarchy(SAMPLE_PERMISSION_XML)
        state = AutonomousUiNavigator.classify_screen_state(nodes)
        self.assertEqual(state["type"], "PERMISSION_DIALOG")

        action = AutonomousUiNavigator.decide_next_action(nodes)
        self.assertEqual(action["action"], "tap")
        # Center of '앱 사용 중에만 허용' bounds=[100,1200][980,1320] -> cx=540, cy=1260
        self.assertEqual(action["x"], 540)
        self.assertEqual(action["y"], 1260)
        self.assertIn("권한", action["reason"])

    def test_action_decision_in_main_view(self):
        """Verify targeted action selection (goal keyword, edittext input, scroll) in main editor."""
        nodes = AutonomousUiNavigator.parse_xml_hierarchy(SAMPLE_MAIN_EDITOR_XML)

        # 1. Goal keyword targeting
        act_sign = AutonomousUiNavigator.decide_next_action(nodes, goal_keywords=["서명", "sign"])
        self.assertEqual(act_sign["action"], "tap")
        self.assertEqual(act_sign["x"], (40 + 200) // 2)
        self.assertEqual(act_sign["y"], (90 + 190) // 2)

        # 2. Input targeting
        act_input = AutonomousUiNavigator.decide_next_action(nodes, prefer_action="input")
        self.assertEqual(act_input["action"], "input_text")
        self.assertEqual(act_input["x"], (60 + 1020) // 2)
        self.assertEqual(act_input["y"], (300 + 450) // 2)

        # 3. Scroll targeting
        act_scroll = AutonomousUiNavigator.decide_next_action(nodes, prefer_action="scroll")
        self.assertEqual(act_scroll["action"], "swipe")

    def test_scenario_graph_engine_domain_adaptation(self):
        """Verify dynamic verification graph synthesis for Camera and Editor domains."""
        # Editor Domain
        with patch.object(ScenarioGraphEngine, "inspect_app_capabilities", return_value={
            "app_name": "PdfFlow",
            "category": "editor",
            "permissions": ["android.permission.READ_EXTERNAL_STORAGE", "android.permission.WRITE_EXTERNAL_STORAGE"],
            "activities": ["MainActivity", "EditorActivity"],
            "hardware": ["STORAGE"],
            "prd_text": "PDF 문서 에디터 및 서명"
        }):
            graph_editor = ScenarioGraphEngine.build_verification_graph("/mock/workspace")
            self.assertEqual(graph_editor.app_category, "editor")
            self.assertEqual(len(graph_editor.checkpoints), 6) # 0%, 25%, 50%, 60% (6. 여러 브랜치 탐색), 75%, 100%
            # 50% checkpoint must be editor-specific
            cp_50 = next(cp for cp in graph_editor.checkpoints if cp.percent == 50)
            self.assertIn("EDITOR", cp_50.title)
            self.assertIn("문서", " ".join(cp_50.actions))

            # 60% checkpoint must be 6. 여러 브랜치 탐색
            cp_60 = next(cp for cp in graph_editor.checkpoints if cp.percent == 60)
            self.assertEqual(cp_60.phase, "MULTI_BRANCH_NAVIGATION")
            self.assertIn("6. 여러 브랜치 탐색", cp_60.title)

            # Slicing from 50%
            cps_from_50 = graph_editor.get_checkpoints_from(start_percent=50)
            self.assertEqual(len(cps_from_50), 4) # 50%, 60%, 75%, 100%
            self.assertEqual([c.percent for c in cps_from_50], [50, 60, 75, 100])

            # Slicing from 70%
            cps_from_70 = graph_editor.get_checkpoints_from(start_percent=70)
            self.assertEqual(len(cps_from_70), 2) # 75%, 100%
            self.assertEqual([c.percent for c in cps_from_70], [75, 100])

        # Camera Domain
        with patch.object(ScenarioGraphEngine, "inspect_app_capabilities", return_value={
            "app_name": "SmartCam",
            "category": "camera",
            "permissions": ["android.permission.CAMERA"],
            "activities": ["CameraActivity"],
            "hardware": ["CAMERA"],
            "prd_text": "사진 촬영 및 필터 적용"
        }):
            graph_cam = ScenarioGraphEngine.build_verification_graph("/mock/workspace")
            self.assertEqual(graph_cam.app_category, "camera")
            cp_50_cam = next(cp for cp in graph_cam.checkpoints if cp.percent == 50)
            self.assertIn("CAMERA", cp_50_cam.title)
            self.assertIn("카메라", " ".join(cp_50_cam.actions))

    def test_battery_and_notification_diagnostics_parsing(self):
        """Verify dumpsys batterystats and dumpsys notification parsing in AndroidEmulatorManager."""
        manager = AndroidEmulatorManager()

        sample_batterystats = """
        Battery History:
        Total cpu time: u=150ms s=50ms
        Wake lock app_wake_lock: 300ms
        """

        sample_notification = """
        Current Notifications:
        NotificationRecord(0x123: pkg=com.pdfflow.app user=0 id=1001 tag=null: channelId=doc_channel)
          android.title=문서 저장 완료
          android.text=계약서가 로컬 저장소에 저장되었습니다.
        """

        with patch.object(manager, "is_adb_connected", return_value=True), \
             patch.object(manager, "run_adb_cmd") as mock_adb:
            
            def adb_side_effect(args, timeout=15.0):
                cmd_str = " ".join(args)
                if "logcat" in cmd_str:
                    return 0, "ActivityManager: Process com.pdfflow.app started cleanly", ""
                if "meminfo" in cmd_str:
                    return 0, "TOTAL PSS: 45678", ""
                if "batterystats" in cmd_str:
                    return 0, sample_batterystats, ""
                if "notification" in cmd_str:
                    return 0, sample_notification, ""
                return 0, "", ""

            mock_adb.side_effect = adb_side_effect

            diag = manager.collect_system_diagnostics("com.pdfflow.app")
            self.assertEqual(diag["crashes_detected"], 0)
            self.assertFalse(diag["anr_detected"])
            self.assertEqual(diag["memory_pss_kb"], 45678)
            self.assertIn("Total cpu time:", diag["battery_stats"]["summary"])
            self.assertEqual(len(diag["active_notifications"]), 1)
            self.assertIn("문서 저장 완료", diag["active_notifications"][0]["title"])

    def test_sensor_and_camera_simulation_helpers(self):
        """Verify hardware sensor event and camera dummy media injection helpers."""
        manager = AndroidEmulatorManager()
        with patch.object(manager, "run_adb_cmd", return_value=(0, "OK", "")) as mock_adb:
            ok_sensor = manager.simulate_sensor_event("acceleration", 0.0, 9.8, 0.0)
            self.assertTrue(ok_sensor)
            mock_adb.assert_called_with(["emu", "sensor", "set", "acceleration", "0.0:9.8:0.0"], timeout=4.0)

            ok_camera = manager.inject_camera_dummy_media("test_cam.jpg")
            self.assertTrue(ok_camera)

    def test_dense_button_environment_20_plus(self):
        """Verify parsing, clustering, and decision in dense UI with 20+ buttons (Requirement 2)."""
        nodes = []
        # 1 Primary CTA
        nodes.append(UiNode(
            text="전자 서명 시작", resource_id="com.app:id/btn_sign", class_name="android.widget.Button",
            bounds=(100, 1200, 500, 1300), clickable=True, enabled=True, focusable=True
        ))
        # 2 Fatal crash traps
        nodes.append(UiNode(
            text="DB 초기화 및 강제 종료", resource_id="com.app:id/btn_wipe", class_name="android.widget.Button",
            bounds=(100, 300, 500, 400), clickable=True, enabled=True, focusable=True
        ))
        nodes.append(UiNode(
            text="메모리 파괴 및 크래시", resource_id="com.app:id/btn_mem_crash", class_name="android.widget.Button",
            bounds=(100, 410, 500, 510), clickable=True, enabled=True, focusable=True
        ))
        # 2 Ambiguous nodes
        nodes.append(UiNode(
            text="???", resource_id="com.app:id/iv_icon_btn_0", class_name="android.widget.ImageView",
            bounds=(100, 520, 200, 620), clickable=True, enabled=True, focusable=True
        ))
        nodes.append(UiNode(
            text="", content_desc="", resource_id="com.app:id/iv_icon_btn_1", class_name="android.widget.ImageButton",
            bounds=(210, 520, 310, 620), clickable=True, enabled=True, focusable=True
        ))
        # 6 Boilerplate nodes
        for i, bp in enumerate(["이용약관", "개인정보처리방침", "오픈소스 라이선스", "버전 정보 v3.0", "FAQ 자주묻는질문", "도움말"]):
            nodes.append(UiNode(
                text=bp, resource_id=f"com.app:id/btn_bp_{i}", class_name="android.widget.TextView",
                bounds=(100, 630 + i * 50, 400, 670 + i * 50), clickable=True, enabled=True, focusable=True
            ))
        # 11 Secondary/general buttons
        for i in range(11):
            nodes.append(UiNode(
                text=f"문서 아이템 {i+1}", resource_id=f"com.app:id/btn_doc_{i}", class_name="android.widget.Button",
                bounds=(100, 950 + i * 50, 400, 990 + i * 50), clickable=True, enabled=True, focusable=True
            ))

        # Explicitly verify clustering function
        cluster_info = AutonomousUiNavigator.process_dense_button_environment(nodes, threshold=20)
        self.assertTrue(cluster_info["is_dense"])
        self.assertEqual(cluster_info["total_buttons"], 22)
        self.assertGreaterEqual(cluster_info["primary_ctas_count"], 1)
        self.assertGreaterEqual(cluster_info["hazards_avoided_count"], 2)
        self.assertGreaterEqual(cluster_info["ambiguous_count"], 2)
        self.assertGreaterEqual(cluster_info["boilerplate_count"], 6)

        # Verify decide_next_action integrates dense environment analysis
        decision = AutonomousUiNavigator.decide_next_action(nodes)
        self.assertTrue(decision["is_dense_button_mode"])
        self.assertEqual(decision["dense_analysis"]["total_buttons"], 22)
        # Primary CTA must be chosen over traps and ambiguous elements
        self.assertEqual(decision["target_node"]["text"], "전자 서명 시작")
        self.assertEqual(decision["action"], "tap")

    def test_back_key_loop_defender_consecutive_and_oscillation(self):
        """Verify BackKeyLoopDefender against repetitive back keys and state oscillation (Requirement 5)."""
        nodes = [
            UiNode(text="뒤로", resource_id="com.app:id/btn_back", class_name="android.widget.Button",
                   bounds=(50, 50, 150, 150), clickable=True, enabled=True, focusable=True),
            UiNode(text="새 문서 생성", resource_id="com.app:id/btn_create", class_name="android.widget.Button",
                   bounds=(100, 800, 500, 950), clickable=True, enabled=True, focusable=True)
        ]

        # 1. Consecutive Back Loop Test
        defender = BackKeyLoopDefender(max_consecutive_backs=3)
        defender.record_action("key_back", "screen_state_alpha")
        defender.record_action("key_back", "screen_state_alpha")
        self.assertFalse(defender.is_in_back_loop())

        defender.record_action("key_back", "screen_state_alpha")
        self.assertTrue(defender.is_in_back_loop())

        # When loop detected, decide_next_action must intervene and force forward action
        decision = AutonomousUiNavigator.decide_next_action(nodes, back_loop_defender=defender)
        self.assertTrue(decision.get("is_loop_defense"))
        self.assertEqual(decision["action"], "tap")
        self.assertEqual(decision["target_node"]["text"], "새 문서 생성")
        self.assertEqual(defender.loops_prevented, 1)
        self.assertEqual(defender.consecutive_backs, 0)

        # 2. State Oscillation (Ping-Pong) Loop Test
        defender_osc = BackKeyLoopDefender(oscillation_window=4)
        defender_osc.record_action("tap", "screen_A")
        defender_osc.record_action("key_back", "screen_B")
        defender_osc.record_action("tap", "screen_A")
        self.assertFalse(defender_osc.is_in_back_loop())

        defender_osc.record_action("key_back", "screen_B")
        self.assertTrue(defender_osc.is_in_back_loop())

        decision_osc = AutonomousUiNavigator.decide_next_action(nodes, back_loop_defender=defender_osc)
        self.assertTrue(decision_osc.get("is_loop_defense"))
        self.assertEqual(decision_osc["action"], "tap")
        self.assertEqual(defender_osc.loops_prevented, 1)

    def test_network_error_offline_simulation_and_recovery(self):
        """Verify network error detection, offline simulation, and recovery (Requirement 10)."""
        # 1. UI Hierarchy Detection & Action Decision
        nodes = [
            UiNode(text="네트워크 오류", resource_id="com.app:id/title", class_name="android.widget.TextView",
                   bounds=(100, 200, 900, 300)),
            UiNode(text="서버와 통신할 수 없습니다. 오프라인 상태입니다.", resource_id="com.app:id/msg",
                   class_name="android.widget.TextView", bounds=(100, 310, 900, 450)),
            UiNode(text="네트워크 다시 시도", resource_id="com.app:id/btn_retry", class_name="android.widget.Button",
                   bounds=(200, 500, 800, 600), clickable=True, enabled=True, focusable=True)
        ]
        state = AutonomousUiNavigator.classify_screen_state(nodes)
        self.assertEqual(state["type"], "NETWORK_ERROR_DIALOG")

        decision = AutonomousUiNavigator.decide_next_action(nodes)
        self.assertEqual(decision["action"], "tap")
        self.assertTrue(decision.get("is_network_recovery"))
        self.assertEqual(decision["target_node"]["text"], "네트워크 다시 시도")

        # 2. Emulator Manager Network Simulation
        manager = AndroidEmulatorManager()
        with patch.object(manager, "run_adb_cmd", return_value=(0, "OK", "")):
            with patch.object(manager, "is_adb_connected", return_value=True):
                res_offline = manager.simulate_network_condition(state="offline")
                self.assertEqual(res_offline["status"], "PASS")
                self.assertTrue(res_offline["offline_injected"])

                res_online = manager.simulate_network_condition(state="online")
                self.assertEqual(res_online["status"], "PASS")
                self.assertTrue(res_online["reconnect_ok"])

                res_cycle = manager.simulate_network_condition(state="cycle", duration_sec=0.01)
                self.assertEqual(res_cycle["status"], "PASS")
                self.assertTrue(res_cycle["offline_injected"])
                self.assertTrue(res_cycle["reconnect_ok"])


if __name__ == "__main__":
    unittest.main()
