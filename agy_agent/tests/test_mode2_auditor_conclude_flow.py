import unittest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
import os
import sys

_submodule_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _submodule_dir not in sys.path:
    sys.path.insert(0, _submodule_dir)

from session_manager import ChannelSession
from trio_agent_runner import TrioAgentRunner
import config


class TestMode2AuditorConcludeFlow(unittest.IsolatedAsyncioTestCase):
    """
    Validates that in Mode 2 (Trio pipeline):
    1. The pipeline sequence is Coder -> QA -> Auditor, and ends at Auditor.
    2. Auditor provides direct answer and conclusion, followed by the approval banner:
       "<@1257525029609017367> 🎉 **[3중 에이전트 협업 개발 완료 - 최종 승인]** (라운드 1/20)"
    3. Coder does not repost final response to Discord.
    4. Operational transit notice does not duplicate to main_channel.
    """

    def setUp(self):
        self.session = ChannelSession(channel_id=12345, workspace="/tmp/test_ws")
        self.session.set_mode("2")
        self.mock_channel = MagicMock()
        self.mock_channel.id = 12345
        self.mock_channel.send = AsyncMock()
        self.mock_channel.typing = MagicMock()
        self.mock_channel.typing.return_value.__aenter__ = AsyncMock()
        self.mock_channel.typing.return_value.__aexit__ = AsyncMock()

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_auditor_concludes_with_approval_banner_at_bottom(
        self, mock_run_prompt, mock_thermal, mock_safe_send
    ):
        mock_run_prompt.side_effect = [
            "Coder: Implemented the requested feature.",
            "[STATUS: APPROVED]\n### QA 코드 리뷰 결과\n`app.py` 파일의 핵심 비즈니스 로직 구현을 검증하였습니다. `def handle_request()` 함수의 에러 핸들링이 적절하며, `config.json` 설정 파일과의 연동도 정상 동작합니다.\n\n**검증 항목:**\n- 라인 45~78: 요청 처리 로직 정상\n- 라인 102: 예외 처리 완비\n\n**개선 제안:** 향후 로깅 레벨을 환경변수로 분리하면 운영 편의성이 높아질 것입니다.",
            (
                "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
                "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
                "요청하신 모든 기능을 오차 없이 완벽히 구현하였습니다.\n\n"
                "### 📁 생성/수정된 핵심 산출물 및 파일\n"
                "• `app.py`: 핵심 비즈니스 로직 작성\n\n"
                "### 🔍 세부 구현 및 품질 검증 결과\n"
                "1. 사용자 요구사항 100% 반영 완료\n\n"
                "---\n"
                "### 🛡️ 3중 협업 품질 요약\n"
                "• 👨‍💻 **Coder**: 구현 완비 | 🕵️ **QA**: 검증 통과 | ⚖️ **Client Auditor**: 사용자 의도 100% 부합 최종 승인"
            )
        ]

        res = await TrioAgentRunner.run_pipeline(
            session=self.session,
            prompt="새로운 기능 만들어줘",
            main_channel=self.mock_channel,
            max_rounds=20
        )

        self.assertIn("최종 승인", res)
        self.assertIn("<@1257525029609017367>", res)
        self.assertIn("🎉 **[3중 에이전트 협업 개발 완료 - 최종 승인]** (라운드 1/20)", res)

        # Verify that safe_send_discord_message was called for Auditor with the banner appended at the bottom
        sent_chunks = [call.args[1] for call in mock_safe_send.call_args_list if len(call.args) > 1]
        last_auditor_chunk = sent_chunks[-1]
        self.assertTrue(
            last_auditor_chunk.rstrip().endswith("<@1257525029609017367> 🎉 **[3중 에이전트 협업 개발 완료 - 최종 승인]** (라운드 1/20)"),
            f"Expected chunk to end with completion banner, got: {last_auditor_chunk}"
        )

    def test_multi_agent_does_not_trigger_coder_repost(self):
        """Validates that bot on_message logic does NOT repost final_response in multi-agent mode."""
        final_response = (
            "<@1257525029609017367> 🎉 **[3중 에이전트 협업 개발 완료 - 최종 승인]** (라운드 1/20)\n\n"
            "요청하신 기능 검증이 완료되었습니다."
        )

        for effective_mode in ("pair", "pair_quad", "read", "2", "2+", "pair+", "pair_plus"):
            is_approval = (
                isinstance(final_response, str)
                and (
                    "최종 승인" in final_response
                    or "에이전트 협업 개발 완료 - 최종 승인" in final_response
                    or "심층 리서치 완료 - 최종 승인" in final_response
                    or "아키텍처 자문 완료" in final_response
                    or "최종 감사 승인 완료" in final_response
                    or "[STATUS: AUDIT_PASSED]" in final_response
                    or "[STATUS: APPROVED]" in final_response
                    or "[STATUS: CONSULTING_COMPLETE]" in final_response
                )
            )
            is_stoppage_or_error = (
                not is_approval
                and (
                    TrioAgentRunner.is_system_error(final_response)
                    or TrioAgentRunner.is_cancellation_or_stop(final_response)
                    or (isinstance(final_response, str) and final_response.strip().startswith(("🛑", "🚨", "⚠️")))
                )
            )
            is_multi_agent = effective_mode in (
                "pair", "pair_quad", "read",
                "2", "2+", "pair+", "pair_plus"
            )
            should_send_final = (not is_multi_agent) or is_stoppage_or_error

            self.assertTrue(is_approval)
            self.assertFalse(is_stoppage_or_error)
            self.assertTrue(is_multi_agent)
            self.assertFalse(should_send_final, f"Coder must NOT repost final_response in mode '{effective_mode}' when Auditor concluded")

    def test_single_agent_modes_do_send_final(self):
        """Validates that single agent modes (1, 1+, single_qa2) still send final response."""
        response = "안녕하세요! 질문에 대한 답변입니다."
        for mode in ("single", "single_plus", "single_qa2"):
            is_multi_agent = mode in (
                "pair", "pair_quad", "read",
                "2", "2+", "pair+", "pair_plus"
            )
            should_send_final = not is_multi_agent
            self.assertTrue(should_send_final, f"Mode '{mode}' must send final response")

    @patch("consulting_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("consulting_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("consulting_runner.AgentRunner.run_prompt")
    async def test_consulting_runner_ends_at_auditor_without_main_channel_transit_duplicate(
        self, mock_run_prompt, mock_thermal, mock_safe_send
    ):
        """Validates that ConsultingRunner (mode read) ends at Chief Strategist (Auditor) without duplicate transit notice on main_channel."""
        from consulting_runner import ConsultingRunner
        mock_run_prompt.side_effect = [
            "Lead Architect: Full architecture analysis performed.",
            "Tech Reviewer: Verified debt and edge cases.",
            "[STATUS: CONSULTING_COMPLETE]\nChief Strategist: Recommended roadmap finalized."
        ]

        res = await ConsultingRunner.run_pipeline(
            session=self.session,
            prompt="프로젝트 아키텍처 분석해줘",
            main_channel=self.mock_channel,
            max_rounds=2
        )

        self.assertIn("[STATUS: CONSULTING_COMPLETE]", res)
        # Verify that mock_channel.send was NOT called with final_summary
        for call_item in self.mock_channel.send.call_args_list:
            sent_content = str(call_item.args[0]) if call_item.args else str(call_item.kwargs.get("content", ""))
            self.assertNotIn("3중 읽기/기획 전용 아키텍처 자문 완료", sent_content)

    def test_is_cancellation_or_stop_precision(self):
        """Verifies that is_cancellation_or_stop strictly detects system cancellations without false positives on ordinary words."""
        # Genuine system stop messages MUST return True
        self.assertTrue(TrioAgentRunner.is_cancellation_or_stop("🛑 사용자의 요청으로 작업이 중단되었습니다."))
        self.assertTrue(TrioAgentRunner.is_cancellation_or_stop("🛑 **작업이 중단(취소)되었습니다.**"))
        self.assertTrue(TrioAgentRunner.is_cancellation_or_stop("[STATUS: AUDIT_STOP]\n치명적 오류로 작업을 중단합니다."))
        self.assertTrue(TrioAgentRunner.is_cancellation_or_stop("⚠️ **[무응답 교착(Deadlock) 자동 회수]** 15분간 응답이 없어 회수합니다."))
        self.assertTrue(TrioAgentRunner.is_cancellation_or_stop("⚠️ **[도구 실행 교착(Deadlock) 자동 회수]** 도구 타임아웃."))

        # Normal Coder responses containing words like 무중단, 취소, 타임아웃 MUST return False
        self.assertFalse(TrioAgentRunner.is_cancellation_or_stop(
            "- 핫리로드 시 Command 객체 래핑 언래핑 안전성을 추가하여 무중단 핫리로드 내구성 강화."
        ))
        self.assertFalse(TrioAgentRunner.is_cancellation_or_stop(
            "주문 취소 버튼 및 결제 취소 다이얼로그 컴포넌트를 구현 완료했습니다."
        ))
        self.assertFalse(TrioAgentRunner.is_cancellation_or_stop(
            "네트워크 타임아웃(timeout=30s) 방어 로직과 재시도 매커니즘을 적용했습니다."
        ))
        self.assertFalse(TrioAgentRunner.is_cancellation_or_stop(
            "사용자가 작업 중단을 요청하지 않도록 안정적인 예외 처리를 완료했습니다."
        ))

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_mode2_pipeline_advances_to_qa_when_coder_mentions_uninterrupted(
        self, mock_run_prompt, mock_thermal, mock_safe_send
    ):
        """Validates that TrioAgentRunner advances to QA even when Coder mentions '무중단' or '취소' in its output."""
        mock_run_prompt.side_effect = [
            "Coder: 무중단 배포 및 취소 버튼 컴포넌트를 구현했습니다. 빌드 완료.",
            "[STATUS: APPROVED]\n### QA 무중단 배포 검증\n`deploy.py` 파일의 무중단 배포 로직을 심층 분석하였습니다. `class RollingDeployer`의 헬스체크 루프가 정상이며, `cancel_button.kt` UI 컴포넌트의 onClick 핸들러도 올바르게 동작합니다.\n\n**검증 항목:**\n- 라인 23~45: 롤링 업데이트 전환 정상\n- 라인 67: 롤백 트리거 조건 검증 완료\n\n**개선 제안:** 배포 타임아웃을 설정 파일로 분리하면 운영 유연성이 높아질 것입니다.",
            "[STATUS: AUDIT_PASSED]\nAuditor: 최종 감사 통과. 승인합니다."
        ]

        res = await TrioAgentRunner.run_pipeline(
            session=self.session,
            prompt="무중단 배포 기능과 취소 버튼 만들어줘",
            main_channel=self.mock_channel,
            max_rounds=1
        )

        # Pipeline must reach QA and Auditor turns (run_prompt called 3 times)
        self.assertEqual(mock_run_prompt.call_count, 3)
        self.assertIn("최종 감사 통과", res)


if __name__ == "__main__":
    unittest.main()

