"""
Comprehensive unit tests verifying direct in-project-channel QA execution for channel 1540925575387025548
and other arbitrary project channels across the AGY 3.0 platform.
"""

import unittest
import os
import json
import tempfile
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_hub import (
    QAHubManager,
    QAMissionPacket,
    is_android_project,
    is_windows_project,
    resolve_target_and_apk,
)
from session_manager import SessionManager, ChannelSession
from config import QA_HUB_CHANNEL_ID


class TestInChannelQAExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.session_manager = SessionManager(state_file=os.path.join(self.temp_dir, "sessions.json"))
        self.channel_id = 1540925575387025548
        self.project_name = "pdfediter_andriod"
        self.workspace = os.path.join(self.temp_dir, self.project_name)
        os.makedirs(self.workspace, exist_ok=True)
        with open(os.path.join(self.workspace, "build.gradle"), "w") as f:
            f.write("")

        self.session = self.session_manager.get_session(self.channel_id)
        self.session.set_workspace(self.workspace)

        self.qa_hub_mgr = QAHubManager(
            session_manager=self.session_manager
        )

    async def asyncTearDown(self):
        import shutil
        self.qa_hub_mgr.stop_missions_for_channel(self.channel_id)
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_direct_in_channel_qa_routing(self):
        """Verify that dispatching from 1540925575387025548 executes directly in source channel."""
        mock_source_channel = AsyncMock()
        mock_source_channel.id = self.channel_id
        mock_source_channel.name = self.project_name
        mock_source_channel.send = AsyncMock()
        mock_source_channel.typing = MagicMock()
        mock_source_channel.typing.return_value.__aenter__ = AsyncMock()
        mock_source_channel.typing.return_value.__aexit__ = AsyncMock()

        mock_bot = AsyncMock()
        mock_bot.get_channel = MagicMock(return_value=mock_source_channel)

        packet = QAMissionPacket(
            request_id="test_req_1540925575387025548",
            source_channel_id=self.channel_id,
            source_channel_name=self.project_name,
            target_url_or_apk=self.workspace,
            mission="실사용자 UI 및 다크모드/AI OCR 종합 E2E 검증",
            max_rounds=5,
            current_round=1,
            mode="evolve",
            workspace=self.workspace
        )

        with patch("qa_hub.AndroidNativeE2EOrchestrator.run_android_e2e", new_callable=AsyncMock) as mock_e2e, \
             patch("qa_hub.TrioAgentRunner.run_pipeline", new_callable=AsyncMock) as mock_trio, \
             patch("qa_hub.ThermalGuard.check_task_start_cooldown", new_callable=AsyncMock), \
             patch("qa_hub.safe_send_discord_message", new_callable=AsyncMock) as mock_safe_send, \
             patch.object(self.qa_hub_mgr, "dispatch_mission", new_callable=AsyncMock):

            from qa_hub import AgenticE2EReport, TestCaseResult
            mock_rep = AgenticE2EReport()
            mock_rep.test_cases.append(TestCaseResult("TC-AND-01", "ADB OS 무결성", "PASS", "에뮬레이터 정상", 500))
            mock_rep.dom_insights = {"vision_score": 95, "vision_summary": "UI 48dp 터치 슬롭 준수 확인"}
            mock_e2e.return_value = mock_rep
            mock_trio.return_value = "[STATUS: APPROVED]\n3중 협업 평가 통과 및 가시적 신기능 검증 완료"

            await self.qa_hub_mgr._process_mission(packet, mock_bot, mock_source_channel)

            # Assertions:
            # 1. safe_send_discord_message called with exec_channel (mock_source_channel)
            sent_channels = [call.args[0] for call in mock_safe_send.call_args_list if call.args]
            for ch in sent_channels:
                self.assertEqual(ch.id, self.channel_id, f"Message sent to wrong channel: {getattr(ch, 'id', None)}")

            # 2. TrioAgentRunner.run_pipeline invoked with main_channel = mock_source_channel for all turns
            self.assertGreaterEqual(mock_trio.call_count, 1)
            for call in mock_trio.call_args_list:
                _, trio_kwargs = call
                self.assertEqual(trio_kwargs.get("main_channel").id, self.channel_id)

    def test_universal_anti_mocking_and_visible_value_in_prompts(self):
        """Verify that agent_runner.py has the Universal Value Creation mandate."""
        from agent_runner import TOOL_SAFETY_HEADER
        self.assertIn("UNIVERSAL VALUE CREATION & ANTI-MOCKING MANDATE", TOOL_SAFETY_HEADER)
        self.assertIn("가시적 실체 구현 헌법", TOOL_SAFETY_HEADER)
        self.assertIn("비가시적 눈속임", TOOL_SAFETY_HEADER)

    async def test_dispatch_mission_delegates_to_autopilot_when_present(self):
        """Verify that dispatch_mission immediately delegates to auto_pilot_manager when available."""
        mock_source_channel = AsyncMock()
        mock_source_channel.id = self.channel_id
        mock_source_channel.name = self.project_name

        mock_auto_pilot = MagicMock()
        mock_state = MagicMock()
        mock_auto_pilot.get_state.return_value = mock_state
        mock_auto_pilot.execute_idle_audit = AsyncMock()

        self.qa_hub_mgr.auto_pilot_manager = mock_auto_pilot

        res = await self.qa_hub_mgr.dispatch_mission(
            source_channel=mock_source_channel,
            target_url_or_apk=self.workspace,
            mission="E2E 진화 검증",
            bot_client=AsyncMock(),
            mode="evolve"
        )

        self.assertTrue(res.startswith("QA_"))

    async def test_dispatch_mission_falls_back_when_autopilot_none(self):
        """Verify that dispatch_mission safely falls back to normal processing when auto_pilot_manager is None."""
        mock_source_channel = AsyncMock()
        mock_source_channel.id = self.channel_id
        mock_source_channel.name = self.project_name
        mock_source_channel.send = AsyncMock()

        self.qa_hub_mgr.auto_pilot_manager = None

        with patch.object(self.qa_hub_mgr, "_process_mission", new_callable=AsyncMock) as mock_process:
            res = await self.qa_hub_mgr.dispatch_mission(
                source_channel=mock_source_channel,
                target_url_or_apk=self.workspace,
                mission="일반 패치 검증",
                bot_client=AsyncMock(),
                mode="patch"
            )

            self.assertTrue(res.startswith("QA_"))
            self.assertIn(res, self.qa_hub_mgr.channel_tasks[self.channel_id])


if __name__ == "__main__":
    unittest.main()

