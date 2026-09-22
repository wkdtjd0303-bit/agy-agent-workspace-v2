import os
import sys
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from thermal_guard import ThermalGuard


class TestThermalGuardComprehensive(unittest.TestCase):
    """
    Extensive test suite for ThermalGuard 3-tier temperature management,
    sensor fallback, and boundary values.
    """

    def test_tier_boundaries(self):
        # Normal Tier (<= 75°C)
        tier, temp = ThermalGuard.get_tier(70.0)
        self.assertEqual(tier, "NORMAL")
        self.assertEqual(temp, 70.0)

        tier, temp = ThermalGuard.get_tier(75.0)
        self.assertEqual(tier, "NORMAL")

        # Mild Tier (78°C <= temp < 85°C)
        tier, temp = ThermalGuard.get_tier(78.0)
        self.assertEqual(tier, "MILD")

        tier, temp = ThermalGuard.get_tier(84.9)
        self.assertEqual(tier, "MILD")

        # Emergency Tier (>= 85°C)
        tier, temp = ThermalGuard.get_tier(85.0)
        self.assertEqual(tier, "EMERGENCY")

        tier, temp = ThermalGuard.get_tier(95.0)
        self.assertEqual(tier, "EMERGENCY")

    def test_sensor_reading_resilience(self):
        # Reads current host temperature without crashing
        temp = ThermalGuard.get_current_temperature()
        self.assertIsInstance(temp, float)
        self.assertGreater(temp, 0.0)
        self.assertLess(temp, 120.0)


if __name__ == "__main__":
    unittest.main()
