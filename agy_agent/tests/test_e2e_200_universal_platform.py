#!/usr/bin/env python3
"""
🧪 Test Suite: AGY Universal Autonomous E2E 200-Point Platform Standard
Validates:
  1. Zero-Configuration Platform & Tech Stack Discovery (Items 1 ~ 60)
     - Web (React, Next.js, HTML5 Game, Vanilla)
     - Android (Compose, Native, WebView, Unity, Flutter)
     - Windows Native (Win32, WPF, Electron)
     - iOS (SwiftUI, Xcode)
  2. Host Execution Capability & 4-State Machine (Items 61 ~ 75)
     - iOS execution on Linux -> BLOCKED with diagnostic reason
     - Android/Web/Windows execution capability
  3. Unified E2E Core Models & Multi-Modal Observation Engine (Items 76 ~ 105, 186 ~ 200)
     - Intent -> Action -> Observation -> State Transition -> Assertion -> Evidence
  4. Universal 200-Point Auditor (AutonomousE2E200Auditor) (Items 1 ~ 200)
     - Strict 95.0 threshold enforcement across 13 categories (A ~ M)
     - Rendering failure & fake claims penalization
  5. CrossPlatformE2ERunner End-to-End Integration
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

current_dir = os.path.dirname(os.path.abspath(__file__))
pkg_dir = os.path.abspath(os.path.join(current_dir, ".."))
if pkg_dir not in sys.path:
    sys.path.insert(0, pkg_dir)

from e2e_core_models import (
    E2EIntent, E2EAction, E2EObservation, StateTransition,
    E2EAssertion, E2EExecutionResult, E2EVerdict
)
from platform_discovery_engine import PlatformDiscoveryEngine, PlatformDiscoveryResult
from observation_engine import UnifiedObservationEngine
from platform_adapters import AdapterFactory, PlaywrightWebAdapter, AndroidAdbAdapter, WindowsNativeAdapter, IosXcuiAdapter
from autonomous_e2e_200_auditor import AutonomousE2E200Auditor
from cross_platform_e2e_runner import CrossPlatformE2ERunner
from mechanical_gate import MechanicalGate, MechanicalCheck


class TestUniversalE2E200Architecture(unittest.TestCase):
    """Test suite for Universal Autonomous E2E 200 Architecture."""

    def setUp(self):
        self.temp_dirs = []

    def tearDown(self):
        for d in self.temp_dirs:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)

    def _create_temp_workspace(self) -> str:
        d = tempfile.mkdtemp(prefix="agy_test_e2e_")
        self.temp_dirs.append(d)
        return d

    def _create_valid_png(self, path: str, width: int = 100, height: int = 100) -> str:
        import zlib, struct
        raw_data = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
        compressed = zlib.compress(raw_data)
        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", compressed)
        png += chunk(b"IEND", b"")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(png)
        return path

    def test_01_zero_config_web_react_discovery(self):
        """Validates zero-config discovery of React Web SPA with dev server scripts."""
        ws = self._create_temp_workspace()
        pkg_json = {
            "name": "my-react-app",
            "dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0"},
            "devDependencies": {"vite": "^5.0.0"},
            "scripts": {"dev": "vite", "build": "vite build"}
        }
        with open(os.path.join(ws, "package.json"), "w") as f:
            json.dump(pkg_json, f)

        res = PlatformDiscoveryEngine.discover(ws)
        self.assertEqual(res.platform, "WEB")
        self.assertIn("React", res.framework)
        self.assertEqual(res.e2e_adapter, "PlaywrightWebAdapter")
        self.assertGreaterEqual(res.confidence, 0.85)
        self.assertEqual(res.dev_server_command, "npm run dev")
        self.assertTrue(res.is_runnable_on_host)

    def test_02_zero_config_hybrid_electron_desktop_discovery(self):
        """Validates that Electron apps in package.json are correctly classified as Desktop Windows/Linux."""
        ws = self._create_temp_workspace()
        pkg_json = {
            "name": "desktop-editor",
            "dependencies": {"electron": "^28.0.0"}
        }
        with open(os.path.join(ws, "package.json"), "w") as f:
            json.dump(pkg_json, f)

        res = PlatformDiscoveryEngine.discover(ws)
        self.assertEqual(res.platform, "WINDOWS")
        self.assertIn("Electron", res.framework)
        self.assertTrue(res.is_hybrid_stack)
        self.assertEqual(res.e2e_adapter, "WindowsNativeAdapter")

    def test_03_zero_config_android_compose_and_webview_discovery(self):
        """Validates Android discovery from build.gradle with Compose or WebView."""
        # 1. Compose App
        ws_compose = self._create_temp_workspace()
        with open(os.path.join(ws_compose, "build.gradle.kts"), "w") as f:
            f.write("composeOptions { kotlinCompilerExtensionVersion = '1.5.1' }\n")
        res_comp = PlatformDiscoveryEngine.discover(ws_compose)
        self.assertEqual(res_comp.platform, "ANDROID")
        self.assertEqual(res_comp.framework, "Jetpack Compose")
        self.assertEqual(res_comp.e2e_adapter, "AndroidAdbAdapter")

        # 2. Android WebView App
        ws_wv = self._create_temp_workspace()
        with open(os.path.join(ws_wv, "build.gradle"), "w") as f:
            f.write("apply plugin: 'com.android.application'\n")
        with open(os.path.join(ws_wv, "AndroidManifest.xml"), "w") as f:
            f.write("<activity android:name='.WebViewActivity'><webview/></activity>\n")
        res_wv = PlatformDiscoveryEngine.discover(ws_wv)
        self.assertEqual(res_wv.platform, "ANDROID")
        self.assertTrue(res_wv.is_hybrid_stack)
        self.assertEqual(res_wv.framework, "Android WebView App")

    def test_04_zero_config_ios_discovery_and_host_blocking_on_linux(self):
        """Validates iOS discovery from Xcode project and proper host capability BLOCKED check on Linux."""
        ws = self._create_temp_workspace()
        os.makedirs(os.path.join(ws, "MyApp.xcodeproj"), exist_ok=True)
        with open(os.path.join(ws, "Podfile"), "w") as f:
            f.write("platform :ios, '16.0'\n")

        res = PlatformDiscoveryEngine.discover(ws)
        self.assertEqual(res.platform, "IOS")
        self.assertEqual(res.e2e_adapter, "IosXcuiAdapter")
        # On Linux, verify that host capability flags is_runnable_on_host=False
        import platform as py_plat
        if py_plat.system().lower() == "linux":
            self.assertFalse(res.is_runnable_on_host)
            self.assertIn("Darwin", res.unsupported_reason)

    def test_05_adapter_factory_instantiation(self):
        """Validates that AdapterFactory creates the correct adapter for each platform."""
        res_web = PlatformDiscoveryResult(platform="WEB", framework="React", runtime="", e2e_adapter="")
        web_ad = AdapterFactory.get_adapter(res_web)
        self.assertTrue(isinstance(web_ad, PlaywrightWebAdapter) or web_ad.__class__.__name__ == "PlaywrightWebAdapter")

        res_droid = PlatformDiscoveryResult(platform="ANDROID", framework="Compose", runtime="", e2e_adapter="")
        droid_ad = AdapterFactory.get_adapter(res_droid)
        self.assertTrue(isinstance(droid_ad, AndroidAdbAdapter) or droid_ad.__class__.__name__ == "AndroidAdbAdapter")

        res_win = PlatformDiscoveryResult(platform="WINDOWS", framework="WPF", runtime="", e2e_adapter="")
        win_ad = AdapterFactory.get_adapter(res_win)
        self.assertTrue(isinstance(win_ad, WindowsNativeAdapter) or win_ad.__class__.__name__ == "WindowsNativeAdapter")

        res_ios = PlatformDiscoveryResult(platform="IOS", framework="SwiftUI", runtime="", e2e_adapter="")
        ios_ad = AdapterFactory.get_adapter(res_ios)
        self.assertTrue(isinstance(ios_ad, IosXcuiAdapter) or ios_ad.__class__.__name__ == "IosXcuiAdapter")

    def test_06_e2e_core_models_serialization(self):
        """Validates Intent, Action, Observation, and ExecutionResult serialization."""
        intent = E2EIntent(
            title="Create Signed PDF Document",
            description="User adds signature and saves document",
            expected_outcomes=["Document saved to disk", "Signature matches SHA-256"]
        )
        self.assertEqual(intent.title, "Create Signed PDF Document")
        self.assertTrue(intent.is_critical_workflow)

        action = E2EAction(action_type="tap", target_selector="btn_sign", coordinates=(150, 300))
        d_act = action.to_dict()
        self.assertEqual(d_act["action_type"], "tap")
        self.assertEqual(d_act["coordinates"], (150, 300))

        exec_res = E2EExecutionResult(
            platform="WEB",
            framework="React SPA",
            target_identifier="https://myapp.com",
            verdict=E2EVerdict.PASS,
            audit_200_score=98.5
        )
        d_exec = exec_res.to_dict()
        self.assertEqual(d_exec["verdict"], "PASS")
        self.assertEqual(d_exec["audit_200_score"], 98.5)

    def test_07_observation_engine_state_transitions(self):
        """Validates UnifiedObservationEngine state transition calculation."""
        obs1 = E2EObservation(is_valid_screen=True, screen_signature="sig_screen_A")
        obs2 = E2EObservation(is_valid_screen=True, screen_signature="sig_screen_B")

        trans = UnifiedObservationEngine.compute_state_transition(obs1, obs2)
        self.assertTrue(trans.is_meaningful_mutation)
        self.assertEqual(trans.transition_type, "NAVIGATION_OR_MUTATION")

        # Stagnant transition
        obs3 = E2EObservation(is_valid_screen=True, screen_signature="sig_screen_A")
        trans_stag = UnifiedObservationEngine.compute_state_transition(obs1, obs3)
        self.assertFalse(trans_stag.is_meaningful_mutation)
        self.assertEqual(trans_stag.transition_type, "STAGNANT_NO_MUTATION")

    def test_08_autonomous_e2e_200_auditor_scoring(self):
        """Validates that AutonomousE2E200Auditor enforces the 95-point threshold across all 13 categories."""
        # 1. Healthy production session -> 100.0 / 100.0 score (PASS)
        healthy = AutonomousE2E200Auditor.audit_session_200({
            "platform": "WEB",
            "has_invalid_rendering": False,
            "has_action_causality": True,
            "has_mechanical_gate": True,
            "has_unverified_support": True,
            "has_zero_config_discovery": True,
            "is_environment_healthy": True
        })
        self.assertEqual(healthy["total_score"], 100.0)
        self.assertEqual(healthy["raw_passed_items"], 200)
        self.assertEqual(healthy["overall_verdict"], "PASS")
        self.assertEqual(healthy["maturity_level"], "Level 4: Universal Autonomous QA Engine")
        self.assertEqual(len(healthy["categories"]), 13)

        # 2. Dead screen session (simulating rendering bug) -> drops below 95.0 (FAIL)
        dead_screen = AutonomousE2E200Auditor.audit_session_200({
            "platform": "ANDROID",
            "has_invalid_rendering": True
        })
        self.assertLess(dead_screen["total_score"], 95.0)
        self.assertEqual(dead_screen["overall_verdict"], "FAIL")

        # 3. Blocked environment session -> BLOCKED verdict with 98.5 score (197 passed, 3 blocked)
        blocked_session = AutonomousE2E200Auditor.audit_session_200({
            "platform": "IOS",
            "is_blocked": True
        })
        self.assertEqual(blocked_session["overall_verdict"], "BLOCKED")
        self.assertEqual(blocked_session["total_score"], 98.5)
        self.assertEqual(blocked_session["raw_passed_items"], 197)
        self.assertEqual(blocked_session["raw_blocked_items"], 3)
        self.assertEqual(blocked_session["raw_failed_items"], 0)

        # 4. Dynamic evidence-based evaluation without explicit boolean flags
        ws = self._create_temp_workspace()
        dynamic_shot = self._create_valid_png(os.path.join(ws, "dynamic_web_shot.png"))
        dynamic_session = AutonomousE2E200Auditor.audit_session_200({
            "platform": "WEB",
            "checks": [
                MechanicalCheck(name="genuine_rendering", passed=True, details="OK"),
                MechanicalCheck(name="web_nav", passed=True, details="OK")
            ],
            "transitions": [
                StateTransition(
                    from_signature="sigA",
                    to_signature="sigB",
                    mutation_pct=12.5,
                    is_meaningful_mutation=True,
                    transition_type="NAVIGATION_OR_MUTATION"
                )
            ],
            "actions": [{"action": "click", "selector": "#btn"}],
            "screenshots": [dynamic_shot],
            "success": True
        })
        self.assertEqual(dynamic_session["total_score"], 100.0)
        self.assertEqual(dynamic_session["overall_verdict"], "PASS")

        # 5. Zero-config failure penalizes Category M items 193~195
        zc_failed = AutonomousE2E200Auditor.audit_session_200({
            "platform": "WEB",
            "has_zero_config_discovery": False
        })
        m_failed_indices = [f["idx"] for f in zc_failed["failed_items"] if f["cat"] == "M"]
        self.assertIn(193, m_failed_indices)
        self.assertIn(194, m_failed_indices)
        self.assertIn(195, m_failed_indices)

    def test_09_cross_platform_e2e_runner_zero_config_dispatch(self):
        """Validates that CrossPlatformE2ERunner autonomously discovers workspace and runs E2E without user specifying platform."""
        ws = self._create_temp_workspace()
        # Create a static web project in ws
        with open(os.path.join(ws, "index.html"), "w") as f:
            f.write("<!DOCTYPE html><html><body><h1>Autonomous AGY App</h1><button id='btn'>Click</button></body></html>")

        # Discovery test without explicit target
        discovery = CrossPlatformE2ERunner.discover_platform_and_target(workspace=ws)
        self.assertEqual(discovery.platform, "WEB")
        self.assertEqual(discovery.e2e_adapter, "PlaywrightWebAdapter")
        self.assertTrue(discovery.is_runnable_on_host)

    def test_10_cross_platform_e2e_runner_ios_blocked_on_linux(self):
        """Validates that running iOS E2E on Linux returns an objective BLOCKED MechanicalGate verdict."""
        import platform as py_plat
        if py_plat.system().lower() == "linux":
            ws = self._create_temp_workspace()
            os.makedirs(os.path.join(ws, "TestApp.xcodeproj"), exist_ok=True)
            res = CrossPlatformE2ERunner.run_unified_cross_platform_e2e(workspace=ws)
            self.assertEqual(res["overall_verdict"], "BLOCKED")
            self.assertEqual(res["mechanical_gate"]["verdict"], "BLOCKED")
            self.assertIn("unsupported_reason", res)

    def test_11_adapter_prevents_stale_screenshot_reuse(self):
        """Validates that adapters do NOT reuse stale screenshots from previous executions (Items 154, 155)."""
        ws = self._create_temp_workspace()
        adapter = AndroidAdbAdapter(workspace=ws)
        stale_shot = os.path.join(adapter.screenshots_dir, "android_old_stale.png")
        self._create_valid_png(stale_shot)

        # Force adb capture failure
        with patch.object(adapter, "_run_adb", side_effect=Exception("screencap timeout")):
            obs = adapter.observe(action_id="act_fresh_01")

        # Crucial: obs must NOT reuse the stale screenshot
        self.assertIsNone(obs.screenshot_path)
        self.assertFalse(obs.is_valid_screen)

    def test_12_android_adapter_blocked_when_adb_missing(self):
        """Validates that AndroidAdbAdapter correctly detects missing ADB as BLOCKED and rejects fake launch (Items 76~78, 90)."""
        ws = self._create_temp_workspace()
        with patch("shutil.which", return_value=None):
            adapter = AndroidAdbAdapter(workspace=ws)
            self.assertFalse(adapter.adb_available)
            self.assertTrue(adapter.is_blocked)
            launched = adapter.launch("com.example.app")
            self.assertFalse(launched)
            self.assertFalse(adapter.is_launched)

            obs = adapter.observe()
            self.assertFalse(obs.is_valid_screen)
            self.assertIn("BLOCKED", obs.console_errors[0])

    def test_13_windows_display_failure_handling(self):
        """Validates that when Xvfb fails to start, windows_x11_display check and gate fail (Item 74)."""
        ws = self._create_temp_workspace()
        with patch("windows_vm_manager.WindowsVMManager.start_xvfb_display", return_value=None):
            res = CrossPlatformE2ERunner.run_windows_e2e(target_exe="app.exe", workspace=ws)
            self.assertEqual(res["overall_verdict"], "FAIL")
            checks = res["mechanical_gate"]["checks"]
            disp_check = next(c for c in checks if c["name"] == "windows_x11_display")
            self.assertFalse(disp_check["passed"])
            self.assertEqual(disp_check["status"], "FAIL")

    def test_14_has_unverified_support_dynamic_evaluation(self):
        """Validates that has_unverified_support dynamically checks 4-state records and penalizes missing support (Items 164, 165)."""
        # Session without 4-state records (plain boolean checks) -> items 164, 165 fail
        plain_session = AutonomousE2E200Auditor.audit_session_200({
            "platform": "WEB",
            "checks": [{"passed": True}]
        })
        k_failed = [f["idx"] for f in plain_session["failed_items"] if f["idx"] in (164, 165)]
        self.assertEqual(len(k_failed), 2)

        # Session with 4-state MechanicalCheck -> items 164, 165 pass
        four_state_session = AutonomousE2E200Auditor.audit_session_200({
            "platform": "WEB",
            "checks": [MechanicalCheck(name="test", passed=True, status="PASS", details="OK")]
        })
        k_failed_4s = [f["idx"] for f in four_state_session["failed_items"] if f["idx"] in (164, 165)]
        self.assertEqual(len(k_failed_4s), 0)


if __name__ == "__main__":
    unittest.main()
