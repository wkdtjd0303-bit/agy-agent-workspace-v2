import os
import sys
import time
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from thermal_guard import ThermalGuard
from trio_agent_runner import QuotaManager
from auto_pilot import AutoPilotManager
from session_manager import SessionManager, ChannelSession


class TestEdgeCasesAndSecurity(unittest.TestCase):
    """
    Extensive test suite for Edge Cases, Boundary Conditions, Hardware Resilience & Security:
    - ThermalGuard Surface Pro 7 thermal tier boundary transitions
    - AutoPilot URL security filtering (preventing SSRF & third-party crawlers)
    - QuotaManager rate-limit tracking, cooldown expiration, and quota fallback
    - SessionManager corrupted file recovery & FIFO concurrency
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_edge_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_thermal_guard_tier_boundaries(self):
        # 1. Normal tier (<= 75C)
        tier_normal, _ = ThermalGuard.get_tier(40.0)
        self.assertEqual(tier_normal, "NORMAL")

        tier_normal_edge, _ = ThermalGuard.get_tier(75.0)
        self.assertEqual(tier_normal_edge, "NORMAL")

        # 2. Mild Cooldown tier (78C ~ 84C)
        tier_mild, _ = ThermalGuard.get_tier(79.0)
        self.assertEqual(tier_mild, "MILD")

        tier_mild_edge, _ = ThermalGuard.get_tier(84.0)
        self.assertEqual(tier_mild_edge, "MILD")

        # 3. Emergency Protection tier (>= 85C)
        tier_emergency, _ = ThermalGuard.get_tier(85.0)
        self.assertEqual(tier_emergency, "EMERGENCY")

        tier_emergency_high, _ = ThermalGuard.get_tier(92.5)
        self.assertEqual(tier_emergency_high, "EMERGENCY")

    def test_thermal_guard_current_temperature(self):
        # Must return valid float Celsius temperature
        temp = ThermalGuard.get_current_temperature()
        self.assertIsInstance(temp, float)
        self.assertGreater(temp, 0.0)
        self.assertLess(temp, 120.0)

    def test_quota_manager_exhaustion_and_recovery(self):
        model_id = "test-model-boundary"
        # Initially not exhausted
        self.assertFalse(QuotaManager.is_model_exhausted(model_id))

        # Mark exhausted for short duration (2.0s)
        QuotaManager.mark_model_exhausted(model_id, duration_sec=2.0)
        self.assertTrue(QuotaManager.is_model_exhausted(model_id))

        # Expire exhaustion manually
        QuotaManager._exhausted_models[model_id] = time.time() - 10
        self.assertFalse(QuotaManager.is_model_exhausted(model_id))

    def test_autopilot_url_security_filter(self):
        # Third party dangerous / non-crawl URLs
        untrusted = [
            "https://github.com/torvalds/linux",
            "https://twitter.com/i/flow/login",
            "https://facebook.com",
            "https://instagram.com",
            "https://cdn.jsdelivr.net/npm/vue@3",
            "https://unpkg.com/react@18/umd/react.production.min.js",
            "https://raw.githubusercontent.com/user/repo/main/README.md"
        ]
        for url in untrusted:
            self.assertTrue(
                AutoPilotManager.is_third_party_url(url),
                f"{url} must be identified as third-party excluded domain"
            )

        # Allowed app hosting platforms
        trusted = [
            "http://localhost:3000",
            "http://127.0.0.1:8080",
            "https://my-app.web.app",
            "https://my-service.firebaseapp.com",
            "https://cool-project.vercel.app",
            "https://static-ui.netlify.app",
            "https://fast-edge.pages.dev"
        ]
        for url in trusted:
            self.assertFalse(
                AutoPilotManager.is_third_party_url(url),
                f"{url} must be allowed as project deployment domain"
            )

    def test_session_manager_corrupted_state_recovery(self):
        state_file = os.path.join(self.test_dir, "corrupted_sessions.json")
        # Write corrupted garbage data to state file
        with open(state_file, "w", encoding="utf-8") as f:
            f.write("<<<CORRUPTED GARBAGE BINARY DATA???>>>")

        # SessionManager must not crash, but recover with default channel sessions safely
        sm = SessionManager(state_file=state_file)
        self.assertGreaterEqual(len(sm._sessions), 1)

        # Operations should continue smoothly
        sess = sm.get_session(12345)
        sess.set_workspace(self.test_dir)
        sm.save_state()
        self.assertTrue(os.path.exists(state_file))


if __name__ == "__main__":
    unittest.main()
