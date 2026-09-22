#!/usr/bin/env python3
"""
🧪 Test Suite: E2E Discord Bot Command & UI Integration
Validates:
  1. Registration of `!e2e` and aliases (`cross_test`, `universal_test`, `자율테스트`, `검증`).
  2. Zero-config execution dispatch via `CrossPlatformE2ERunner`.
  3. Delivery of structured 200-Point Audit embed report with 4-state verdict and screenshots.
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

current_dir = os.path.dirname(os.path.abspath(__file__))
pkg_dir = os.path.abspath(os.path.join(current_dir, ".."))
if pkg_dir not in sys.path:
    sys.path.insert(0, pkg_dir)

from bot import bot, e2e_universal_command
from platform_discovery_engine import PlatformDiscoveryResult


class TestE2EBotCommand(unittest.IsolatedAsyncioTestCase):
    """Test suite for Discord Bot !e2e command."""

    def test_01_command_registration(self):
        """Validates that !e2e is registered on the bot with proper aliases."""
        cmd = bot.get_command("e2e")
        self.assertIsNotNone(cmd, "Command !e2e must be registered on bot")
        self.assertIn("cross_test", cmd.aliases)
        self.assertIn("universal_test", cmd.aliases)
        self.assertIn("자율테스트", cmd.aliases)
        self.assertIn("검증", cmd.aliases)

    @patch("bot.reload_helper_modules", lambda: None)
    @patch("cross_platform_e2e_runner.CrossPlatformE2ERunner.discover_platform_and_target")
    @patch("cross_platform_e2e_runner.CrossPlatformE2ERunner.run_unified_cross_platform_e2e")
    async def test_02_e2e_command_execution(self, mock_run, mock_disc):
        """Validates that executing !e2e dispatches discovery and reports 200-point audit verdict."""
        mock_disc.return_value = PlatformDiscoveryResult(
            platform="WEB",
            framework="React SPA",
            runtime="Chromium/Playwright",
            artifact="http://localhost:5173",
            execution_target="http://localhost:5173",
            e2e_adapter="PlaywrightWebAdapter",
            confidence=0.95,
            evidence=["Found package.json", "Vite dev server running"]
        )

        mock_run.return_value = {
            "platform": "WEB",
            "overall_verdict": "PASS",
            "e2e_200_audit": {
                "total_score": 98.5,
                "raw_passed_items": 197,
                "total_items": 200,
                "maturity_level": "Level 4: Universal Autonomous QA Engine",
                "categories": {
                    "A": {"name": "기본 원칙", "passed": 10, "total": 10, "pct": 100.0}
                }
            },
            "mechanical_gate": {
                "verdict": "PASS",
                "passed_checks": 6,
                "total_checks": 6,
                "ai_overruled": False
            },
            "screenshots": []
        }

        # Mock Discord Context
        ctx = MagicMock()
        ctx.channel.id = 123456789
        ctx.send = AsyncMock()

        await e2e_universal_command(ctx, target_arg="")

        # Verify progress message was sent
        self.assertGreaterEqual(ctx.send.call_count, 2)
        final_call_kwargs = ctx.send.call_args_list[-1].kwargs
        embed = final_call_kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("Universal Cross-Platform Autonomous E2E", embed.title)
        self.assertIn("98.5 / 100점", embed.description)


if __name__ == "__main__":
    unittest.main()
