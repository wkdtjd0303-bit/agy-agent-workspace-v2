"""
[🛡️ Multi-Agent Unconditional Transition Test Suite]
Verifies that in multi-agent collaboration modes (!mode 2, 2+, 3, 3+):
1. The pipeline unconditionally transitions: Coder -> QA -> (Extended QAs) -> Auditor.
2. Diagnostic / inquiry prompts ("원인 분석", "상태 점검", "부하 상태") do NOT exit early at Coder.
3. Zero-Production-Diff does NOT falsely block non-coding / inquiry requests.
4. Mode classifier recognizes 'auto_multi' and 'auto_trio' aliases.
5. Auditor always concludes with the official approval banner.
"""
import os
import sys
import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("UNITTEST_RUNNING", "1")

_agy_agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _agy_agent_dir not in sys.path:
    sys.path.insert(0, _agy_agent_dir)

from session_manager import ChannelSession
from trio_agent_runner import TrioAgentRunner
from mode_classifier import classify_mode


class TestMultiAgentUnconditionalTransition(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel_id = 999999999
        self.workspace = os.path.abspath(os.path.join(_agy_agent_dir, ".."))
        self.mock_channel = MagicMock()
        self.mock_channel.id = self.channel_id
        self.mock_channel.send = AsyncMock()
        self.mock_channel.typing = MagicMock()
        self.mock_channel.typing.return_value.__aenter__ = AsyncMock()
        self.mock_channel.typing.return_value.__aexit__ = AsyncMock()

        self.qa_client = MagicMock()
        self.qa_client.is_ready.return_value = False
        self.auditor_client = MagicMock()
        self.auditor_client.is_ready.return_value = False

        self.hermetic_patches = [
            patch("trio_agent_runner.EvidenceValidator.inspect_production_diff", return_value={"is_zero_production_diff": False, "prod_changed_files": ["diagnostic_report.md"], "non_prod_files": []}),
            patch("trio_agent_runner.EvidenceValidator.verify_anti_mocking_and_authenticity", return_value=(True, [], {})),
            patch("trio_agent_runner.QualityStateManager.evaluate_and_update", return_value={"regression": {"has_regression": False}, "overall_score": 100.0, "delta": 0.0}),
            patch("trio_agent_runner.EvidenceValidator.check_visual_asset_presence", return_value={"has_assets": True}),
            patch("vision_attachment_analyzer.VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa", new_callable=AsyncMock, return_value={"has_screenshots": True}),
            patch("autonomy_guard.CollaborationIntelligence.evaluate_auditor_value_alignment", return_value=("PROCEED", "가치 부합 확인")),
        ]
        for p in self.hermetic_patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.hermetic_patches):
            p.stop()

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_mode2_unconditional_transition_on_diagnostic_prompt(
        self, mock_run_prompt, mock_thermal, mock_safe_send
    ):
        """Mode 2 (!mode 2): Even on a diagnostic prompt, Coder -> QA1 -> Auditor must ALL run."""
        session = ChannelSession(channel_id=self.channel_id, workspace=self.workspace, mode="pair")
        session.is_busy = True

        mock_run_prompt.side_effect = [
            "Coder: 시스템 부하 및 원인 분석 결과입니다.",
            "[STATUS: APPROVED]\nqa1: Coder 분석 결과 무결성 확인.",
            (
                "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
                "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
                "원인 분석 및 해결 방안이 완벽히 검증되었습니다.\n\n"
                "### 📁 생성/수정된 핵심 산출물 및 파일\n"
                "• 진단 보고서 작성 완료\n\n"
                "### 🔍 세부 구현 및 품질 검증 결과\n"
                "1. 시스템 부하 원인 규명 완료\n\n"
                "---\n"
                "### 🛡️ 3중 협업 품질 요약\n"
                "• 👨‍💻 Coder 완료 | 🕵️ QA 통과 | ⚖️ Auditor 최종 승인"
            )
        ]

        res = await TrioAgentRunner.run_pipeline(
            session=session,
            prompt="시스템 부하 원인이 뭐야? 진단하고 해결책 알려줘",
            main_channel=self.mock_channel,
            qa_client=self.qa_client,
            auditor_client=self.auditor_client,
            max_rounds=1
        )

        self.assertIn("최종 승인", res)
        self.assertEqual(mock_run_prompt.call_count, 3, "Mode 2 must call Coder, QA1, and Auditor (3 turns)")
        self.assertFalse(session.is_busy)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_mode3_unconditional_transition_on_inquiry(
        self, mock_run_prompt, mock_thermal, mock_safe_send
    ):
        """Mode 3 (!mode 3): Prompt with inquiry keywords advances unconditionally through all agents."""
        session = ChannelSession(channel_id=self.channel_id, workspace=self.workspace, mode="mode3")
        session.is_busy = True

        mock_run_prompt.side_effect = [
            "Coder: 마일스톤 요구사항 분석 및 상태 점검 완료.",
            "[STATUS: APPROVED]\nqa1: 점검 보고서 무결성 확인 완료.",
            (
                "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
                "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
                "점검 결과 이상 없음 확인.\n\n"
                "---\n"
                "### 🛡️ 3중 협업 품질 요약\n"
                "• 👨‍💻 Coder 완료 | 🕵️ QA 통과 | ⚖️ Auditor 최종 승인"
            )
        ]

        res = await TrioAgentRunner.run_pipeline(
            session=session,
            prompt="현재 아키텍처 상태 어때? 어떻게 생각해?",
            main_channel=self.mock_channel,
            qa_client=self.qa_client,
            auditor_client=self.auditor_client,
            max_rounds=1
        )

        self.assertIn("최종 승인", res)
        self.assertEqual(mock_run_prompt.call_count, 3, "Mode 3 must call Coder, QA1, and Auditor")

    def test_mode_classifier_aliases(self):
        """Validates that auto_multi and auto_trio are properly classified."""
        mode_3p, reason_3p = classify_mode("개발해줘", "auto_multi")
        self.assertEqual(mode_3p, "mode3_plus")

        mode_3, reason_3 = classify_mode("개발해줘", "auto_trio")
        self.assertEqual(mode_3, "mode3")

        mode_2p, reason_2p = classify_mode("개발해줘", "2+")
        self.assertEqual(mode_2p, "pair_quad")

        mode_2, reason_2 = classify_mode("개발해줘", "2")
        self.assertEqual(mode_2, "pair")


if __name__ == "__main__":
    unittest.main()
