"""
Comprehensive tests for OpenRouter Usage Monitor & Control Tower Integration
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openrouter_usage_monitor


class TestOpenRouterUsageMonitor(unittest.TestCase):
    def setUp(self):
        # Reset cache before each test
        openrouter_usage_monitor._cached_usage_data = None
        openrouter_usage_monitor._last_usage_fetch_time = 0.0

    def test_mask_api_key(self):
        self.assertEqual(openrouter_usage_monitor.mask_api_key(""), "N/A")
        masked_short = openrouter_usage_monitor.mask_api_key("sk-12345678")
        self.assertIn("••••", masked_short)
        
        long_key = "sk-or-v1-abcdef1234567890abcdef1234567890xyz"
        masked = openrouter_usage_monitor.mask_api_key(long_key)
        self.assertTrue(masked.startswith("sk-or-v1-abcde"))
        self.assertTrue(masked.endswith("0xyz"))
        self.assertIn("...", masked)

    def test_make_progress_bar(self):
        bar_0 = openrouter_usage_monitor.make_progress_bar(0.0, 10)
        self.assertEqual(bar_0, "░░░░░░░░░░")

        bar_50 = openrouter_usage_monitor.make_progress_bar(50.0, 10)
        self.assertEqual(bar_50, "█████░░░░░")

        bar_100 = openrouter_usage_monitor.make_progress_bar(100.0, 10)
        self.assertEqual(bar_100, "██████████")

    @patch("openrouter_usage_monitor.fetch_key_info")
    @patch("openrouter_usage_monitor.fetch_credits")
    def test_get_openrouter_usage_data_success(self, mock_credits, mock_key_info):
        mock_key_info.return_value = {
            "data": {
                "label": "my-test-key",
                "usage": 1.25,
                "limit": 10.0,
                "limit_remaining": 8.75,
                "usage_daily": 0.25,
                "usage_monthly": 1.25,
                "is_free_tier": False,
                "limit_reset": "monthly"
            }
        }
        mock_credits.return_value = {
            "data": {
                "total_credits": 20.0,
                "total_usage": 1.25
            }
        }

        data = openrouter_usage_monitor.get_openrouter_usage_data(api_key="sk-or-v1-test", force_refresh=True)

        self.assertTrue(data["configured"])
        self.assertIsNone(data["error"])
        self.assertEqual(data["label"], "my-test-key")
        self.assertEqual(data["usage"], 1.25)
        self.assertEqual(data["limit"], 10.0)
        self.assertEqual(data["limit_remaining"], 8.75)
        self.assertEqual(data["percent_used"], 12.5)
        self.assertEqual(data["percent_remain"], 87.5)
        self.assertFalse(data["is_free_tier"])
        self.assertEqual(data["total_credits"], 20.0)

    @patch("openrouter_usage_monitor.fetch_key_info")
    @patch("openrouter_usage_monitor.fetch_credits")
    def test_format_openrouter_telemetry_field(self, mock_credits, mock_key_info):
        mock_key_info.return_value = {
            "data": {
                "label": "sk-or-v1-c18...605",
                "usage": 0.0005,
                "limit": 5.0,
                "limit_remaining": 4.9995,
                "is_free_tier": True,
                "limit_reset": "monthly"
            }
        }
        mock_credits.return_value = {"data": {"total_credits": 0.0}}

        field_text = openrouter_usage_monitor.format_openrouter_telemetry_field(api_key="sk-or-v1-test", force_refresh=True)

        self.assertIsNotNone(field_text)
        self.assertLess(len(field_text), 1024)
        self.assertIn("OpenRouter 지표", field_text)
        self.assertIn("$0.0005", field_text)
        self.assertIn("$5.0000", field_text)
        self.assertIn("Free Tier", field_text)
        self.assertIn("qa2", field_text)

    def test_format_unconfigured_key(self):
        field_text = openrouter_usage_monitor.format_openrouter_telemetry_field(api_key="")
        self.assertIn("OPENROUTER_API_KEY", field_text)
        self.assertLess(len(field_text), 1024)

    @patch("openrouter_usage_monitor.fetch_key_info", return_value=None)
    def test_format_api_error_handling(self, mock_key_info):
        field_text = openrouter_usage_monitor.format_openrouter_telemetry_field(api_key="sk-dummy-key", force_refresh=True)
        self.assertIn("재시도 대기", field_text)
        self.assertLess(len(field_text), 1024)


if __name__ == "__main__":
    unittest.main()
