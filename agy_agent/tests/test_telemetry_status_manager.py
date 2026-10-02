"""
Comprehensive tests for TelemetryStatusManager
"""

import unittest
import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry_status_manager import (
    make_progress_bar,
    make_antigravity_gauge,
    format_iso_duration,
    get_antigravity_account_email,
    fetch_native_antigravity_usage,
    get_claude_sonnet_status,
    fetch_real_ai_quota_sections,
    build_telemetry_embed,
    update_status_message,
    start_telemetry_status_loop,
    ensure_telemetry_status_loop_running,
    STATUS_CHANNEL_ID,
    STATUS_MESSAGE_ID
)
from session_manager import SessionManager, ChannelSession


import tempfile
import shutil

class TestTelemetryStatusManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = f"{self.temp_dir}/channel_sessions.json"
        self.sm = SessionManager(state_file=self.temp_file, clear_on_startup=False)
        self.patcher = patch("telemetry_status_manager._TELEMETRY_STATE_FILE", f"{self.temp_dir}/telemetry_state.json")
        self.patcher.start()
        import telemetry_status_manager
        self._orig_msg_id = telemetry_status_manager.STATUS_MESSAGE_ID
        self._orig_hash = telemetry_status_manager._get_last_embed_hash()
        telemetry_status_manager.STATUS_MESSAGE_ID = 12345
        telemetry_status_manager._set_last_embed_hash("")

    def tearDown(self):
        import telemetry_status_manager
        telemetry_status_manager.STATUS_MESSAGE_ID = self._orig_msg_id
        telemetry_status_manager._set_last_embed_hash(self._orig_hash)
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_make_progress_bar(self):
        self.assertEqual(make_progress_bar(0, 10), "░░░░░░░░░░")
        self.assertEqual(make_progress_bar(50, 10), "█████░░░░░")
        self.assertEqual(make_progress_bar(100, 10), "██████████")
        self.assertEqual(make_progress_bar(-10, 10), "░░░░░░░░░░")
        self.assertEqual(make_progress_bar(150, 10), "██████████")

    def test_build_telemetry_embed(self):
        embed = build_telemetry_embed(self.sm)
        self.assertIsNotNone(embed)
        self.assertIn("Control Tower", embed.title)
        # New simplified embed has 3 fields: 시스템, AI 쿼터 현황, 작업 현황
        self.assertGreaterEqual(len(embed.fields), 3)

        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("시스템" in name for name in field_names))
        self.assertTrue(any("AI 쿼터 현황" in name for name in field_names))
        self.assertTrue(any("작업 현황" in name for name in field_names))

    def test_build_telemetry_embed_with_busy_channel(self):
        # Mark a channel as busy
        sess = self.sm.get_session(1539483630089273435)
        sess.set_busy(True, "Coder 작업 중 (1/20R)")
        
        embed = build_telemetry_embed(self.sm)
        self.assertIsNotNone(embed)
        # Verify compact task summary reflects busy count
        task_field = [f for f in embed.fields if "작업 현황" in f.name][0]
        self.assertIn("작업 중", task_field.value)

    def test_update_status_message_closed_client(self):
        mock_client = MagicMock()
        mock_client.is_closed.return_value = True

        with patch("telemetry_status_manager.update_status_via_rest_api", return_value=True) as mock_rest:
            async def run_test():
                res = await update_status_message(mock_client, self.sm)
                self.assertTrue(res)
                mock_rest.assert_called_once()

            asyncio.run(run_test())

    def test_update_status_message_success(self):
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False

        mock_channel = AsyncMock()
        mock_message = AsyncMock()
        mock_channel.fetch_message.return_value = mock_message
        mock_client.get_channel.return_value = mock_channel

        async def run_test():
            res = await update_status_message(mock_client, self.sm)
            self.assertTrue(res)
            mock_message.edit.assert_awaited_once()

        asyncio.run(run_test())

    def test_ensure_loop_running(self):
        """Verifies that background loop task remains None due to permanent quota protection kill switch."""
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False

        async def run_test():
            ensure_telemetry_status_loop_running(mock_client, self.sm)
            import telemetry_status_manager
            self.assertIsNone(telemetry_status_manager._loop_task)

        asyncio.run(run_test())

    def test_ensure_loop_running_delegation_when_service_active(self):
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False

        async def run_test():
            import telemetry_status_manager
            telemetry_status_manager._loop_task = None
            ensure_telemetry_status_loop_running(mock_client, self.sm)
            self.assertIsNone(telemetry_status_manager._loop_task)

        asyncio.run(run_test())

    def test_make_antigravity_gauge(self):
        g0 = make_antigravity_gauge(0.0, 10)
        self.assertEqual(g0, "[░░░░░░░░░░]")
        g50 = make_antigravity_gauge(0.5, 10)
        self.assertEqual(g50, "[■■■■■░░░░░]")
        g100 = make_antigravity_gauge(1.0, 10)
        self.assertEqual(g100, "[■■■■■■■■■■]")

    def test_format_iso_duration(self):
        # Far future (days)
        dur = format_iso_duration("2099-01-01T00:00:00Z")
        self.assertIn("일", dur)
        self.assertIn("h", dur)

        # Invalid string fallback
        self.assertEqual(format_iso_duration(""), "N/A")
        self.assertEqual(format_iso_duration("invalid-date"), "invalid-date")

    def test_get_antigravity_account_email(self):
        email = get_antigravity_account_email()
        self.assertIsInstance(email, str)
        self.assertIn("@", email)

    def test_fetch_native_antigravity_usage_mock(self):
        sample_json = {
            "conversation_id": "",
            "status": "SUCCESS",
            "command": {
                "name": "usage",
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {"id": "gemini-weekly", "name": "Weekly Limit Remaining", "remaining_fraction": 0.90, "reset_time": "2026-09-17T18:30:12Z"},
                                {"id": "gemini-5h", "name": "Five Hour Limit Remaining", "remaining_fraction": 0.85, "reset_time": "2026-09-11T16:49:35Z"}
                            ]
                        },
                        {
                            "name": "Claude and GPT models",
                            "buckets": [
                                {"id": "3p-weekly", "name": "Weekly Limit Remaining", "remaining_fraction": 0.35, "reset_time": "2026-09-17T11:19:52Z"},
                                {"id": "3p-5h", "name": "Five Hour Limit Remaining", "remaining_fraction": 1.0, "reset_time": "2026-09-11T17:46:08Z"}
                            ]
                        }
                    ]
                }
            }
        }
        import telemetry_status_manager
        telemetry_status_manager._native_usage_cache = {"time": 0.0, "data": None}

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        import json
        mock_proc.stdout = json.dumps(sample_json)

        with patch("subprocess.run", return_value=mock_proc):
            res = fetch_native_antigravity_usage()
            self.assertEqual(res["account"], get_antigravity_account_email())
            self.assertEqual(len(res["groups"]), 2)
            self.assertEqual(res["groups"][0]["name"], "Gemini Models")

    def test_get_claude_sonnet_status(self):
        import telemetry_status_manager
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()
        telemetry_status_manager._sonnet_cache = {"time": 0.0, "data": None}
        # Test eligible quota (>= 10%)
        telemetry_status_manager._native_usage_cache = {
            "time": 9999999999.0,
            "data": {
                "account": "test@example.com",
                "groups": [
                    {
                        "name": "Claude and GPT models",
                        "buckets": [
                            {"remaining_fraction": 0.385},
                            {"remaining_fraction": 1.0}
                        ]
                    }
                ]
            }
        }
        status = get_claude_sonnet_status()
        self.assertTrue(status["can_use"])
        self.assertEqual(status["percentage_int"], 38)
        self.assertFalse(status["is_exhausted"])

        # Test exhausted / low quota (< 10%)
        telemetry_status_manager._native_usage_cache["data"]["groups"][0]["buckets"][0]["remaining_fraction"] = 0.05
        status_low = get_claude_sonnet_status()
        self.assertFalse(status_low["can_use"])
        self.assertTrue(status_low["is_exhausted"])

    def test_fetch_real_ai_quota_sections(self):
        import telemetry_status_manager
        telemetry_status_manager._cached_gemini_quota = None
        telemetry_status_manager._cached_external_quota = None
        telemetry_status_manager._last_quota_fetch_time = 0.0

        gemini_q, ext_q = fetch_real_ai_quota_sections()
        self.assertIsNotNone(gemini_q)
        self.assertIn("GEMINI MODELS", gemini_q)
        self.assertIn("[주간 한도] Weekly Limit Remaining", gemini_q)
        self.assertIn("[5시간 한도] Five Hour Limit Remaining", gemini_q)
        self.assertIn("■", gemini_q)

        self.assertIsNotNone(ext_q)
        self.assertIn("CLAUDE & GPT MODELS", ext_q)
        self.assertIn("[주간 한도] Weekly Limit Remaining", ext_q)
        self.assertIn("[5시간 한도] Five Hour Limit Remaining", ext_q)
    def test_status_channel_id_updated(self):
        import telemetry_status_manager
        self.assertEqual(telemetry_status_manager.STATUS_CHANNEL_ID, 1550294208588419123)

    def test_start_telemetry_status_loop_default_10min_interval(self):
        import inspect
        import telemetry_status_manager
        sig = inspect.signature(telemetry_status_manager.start_telemetry_status_loop)
        self.assertEqual(sig.parameters["interval_sec"].default, 600)

    def test_single_status_message_discovery_prevents_duplicate_send(self):
        """Verify that when a status message exists in the channel, it edits rather than sends a new message."""
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False
        mock_client.user.id = 123456

        mock_channel = AsyncMock()
        existing_msg = AsyncMock()
        existing_msg.id = 99887766
        existing_msg.author.id = 123456
        mock_emb = MagicMock()
        mock_emb.title = "🎛️ HP ProDesk Control Tower"
        mock_emb.description = "통합 관제"
        existing_msg.embeds = [mock_emb]

        mock_channel.fetch_message.side_effect = Exception("Not cached in memory")
        mock_channel.pins.return_value = [existing_msg]
        mock_client.get_channel.return_value = mock_channel

        import telemetry_status_manager
        telemetry_status_manager.STATUS_MESSAGE_ID = None
        telemetry_status_manager._set_rate_limit_until(0.0)

        async def run_test():
            res = await telemetry_status_manager.update_status_message(mock_client, self.sm)
            self.assertTrue(res)
            # Must edit the discovered message, NEVER send a new one
            existing_msg.edit.assert_awaited_once()
            mock_channel.send.assert_not_called()
            self.assertEqual(telemetry_status_manager.STATUS_MESSAGE_ID, 99887766)

        asyncio.run(run_test())

    def test_telemetry_status_view_structure(self):
        """Verifies that TelemetryStatusView has correct button, persistent timeout, and custom_id."""
        import telemetry_status_manager
        view = telemetry_status_manager.TelemetryStatusView(self.sm)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 1)
        btn = view.children[0]
        self.assertEqual(btn.label, "새로고침")
        self.assertEqual(btn.custom_id, "telemetry_status_refresh")
        comps = view.to_components()
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["components"][0]["custom_id"], "telemetry_status_refresh")

    def test_telemetry_status_view_refresh_callback(self):
        """Verifies that clicking the refresh button defers and updates the status message."""
        import telemetry_status_manager
        view = telemetry_status_manager.TelemetryStatusView(self.sm)
        mock_interaction = AsyncMock()
        mock_interaction.response.is_done.return_value = False
        mock_interaction.client = MagicMock()

        with patch("telemetry_status_manager.update_status_message", return_value=True) as mock_update:
            async def run_callback():
                await view.refresh_button.callback(mock_interaction)
                mock_interaction.response.defer.assert_awaited_once_with()
                mock_update.assert_awaited_once()
                mock_interaction.followup.send.assert_not_called()

            asyncio.run(run_callback())


if __name__ == "__main__":
    unittest.main()
