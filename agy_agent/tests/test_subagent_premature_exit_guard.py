# -*- coding: utf-8 -*-
"""
[🧷 서브에이전트 조기 종료 방어] 회귀 테스트.

배경(실측): Coder가 서브에이전트/백그라운드 작업을 끝내지 않은 채
"조사 에이전트가 도는 동안 ...", "이제 백그라운드에서 두 건이 도는 중입니다",
"While the suite runs, let me double-check ..." 같은 진행 나레이션만 남기고 턴을 끝내면,
그 나레이션이 최종 보고로 게시되어 실제 산출물이 유실된다.

이 테스트는:
1) 실측 위반 나레이션이 감지되는지
2) 정상 완결 보고는 오탐되지 않는지
3) 파이프라인이 감지 시 Coder를 완결까지 재지시(대기)하는지
를 검증한다.
"""
import os
import sys
import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
WORKSPACE_ROOT = BASE_DIR


class TestPendingSubagentDetection(unittest.TestCase):
    def test_real_premature_exit_narrations_are_detected(self):
        from trio_agent_runner import TrioAgentRunner

        samples = [
            "조사 에이전트가 도는 동안 산출물 형식을 정하기 위해 기존 문서 관례를 확인하겠습니다.",
            "이제 백그라운드에서 두 건이 도는 중입니다:",
            "특정 품목 검색이 안 잡힙니다. 병렬로 조사 에이전트를 띄우겠습니다.",
            "두 조사 결과가 도착하면 바로 종합해 리포트를 만들겠습니다.",
            "While the suite runs, let me double-check for stale references and review the final diffs.",
            "I dispatched a research agent; waiting for it to finish.",
        ]
        for s in samples:
            self.assertTrue(
                TrioAgentRunner.detect_pending_subagent_work(s),
                f"pending subagent narration must be detected: {s!r}",
            )

    def test_completed_reports_are_not_flagged(self):
        from trio_agent_runner import TrioAgentRunner

        samples = [
            "",
            None,
            "로그인 버튼 색상을 #4F46E5로 변경했고 회귀 테스트 3개를 통과시켰습니다.",
            "백그라운드 작업을 모두 완료했습니다. 최종 완료 보고입니다.",
            "구현 완료: 결제 모듈을 리팩터링했습니다.",
            "백그라운드 워커 큐를 구현했습니다.",
            "이벤트 루프가 돌고 있는 스레드에서도 코루틴을 안전 실행하도록 구현 완료했습니다.\n\n[TURN_DONE]",
            "수정된 3개 파일을 디스크에 저장하고 테스트를 통과시켰습니다. [TURN_DONE]",
        ]
        for s in samples:
            self.assertFalse(
                TrioAgentRunner.detect_pending_subagent_work(s),
                f"completed report must NOT be flagged: {s!r}",
            )

    def test_wait_max_rounds_env_override(self):
        from trio_agent_runner import (
            subagent_wait_max_rounds,
            SUBAGENT_WAIT_MAX_ROUNDS_ENV,
            SUBAGENT_WAIT_MAX_ROUNDS_DEFAULT,
        )

        old = os.environ.get(SUBAGENT_WAIT_MAX_ROUNDS_ENV)
        try:
            os.environ[SUBAGENT_WAIT_MAX_ROUNDS_ENV] = "0"
            self.assertEqual(subagent_wait_max_rounds(), 0)
            os.environ[SUBAGENT_WAIT_MAX_ROUNDS_ENV] = "3"
            self.assertEqual(subagent_wait_max_rounds(), 3)
            os.environ[SUBAGENT_WAIT_MAX_ROUNDS_ENV] = "not-an-int"
            self.assertEqual(subagent_wait_max_rounds(), SUBAGENT_WAIT_MAX_ROUNDS_DEFAULT)
            os.environ.pop(SUBAGENT_WAIT_MAX_ROUNDS_ENV, None)
            self.assertEqual(subagent_wait_max_rounds(), SUBAGENT_WAIT_MAX_ROUNDS_DEFAULT)
        finally:
            if old is None:
                os.environ.pop(SUBAGENT_WAIT_MAX_ROUNDS_ENV, None)
            else:
                os.environ[SUBAGENT_WAIT_MAX_ROUNDS_ENV] = old


class TestSubagentPrematureExitPipelineGuard(unittest.TestCase):
    def test_pipeline_redrives_coder_until_subagent_work_completes(self):
        from trio_agent_runner import TrioAgentRunner
        from session_manager import ChannelSession

        session = ChannelSession(channel_id=99997, workspace=WORKSPACE_ROOT, mode="mode3")
        main_ch = MagicMock()
        main_ch.send = AsyncMock()
        main_ch.typing = MagicMock()

        class DummyTyping:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        main_ch.typing.return_value = DummyTyping()

        coder_calls = []

        async def mock_coder(session, prompt, on_tool_update=None):
            coder_calls.append(prompt)
            if len(coder_calls) == 1:
                # 조기 종료 나레이션 → 가드가 대기 재지시를 걸어야 한다.
                return "이제 백그라운드에서 두 건이 도는 중입니다. 조사 에이전트가 도는 동안 기다리겠습니다."
            # 완결 보고 → 더 이상 대기 대상이 아니다.
            return "## 최종 완료\n조사 결과를 통합해 `src/plan.py` 에 실제 구현을 반영했습니다."

        async def run_test():
            with patch.object(TrioAgentRunner, "run_coder_turn", side_effect=mock_coder), \
                 patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock), \
                 patch.object(TrioAgentRunner, "run_qa_turn", new_callable=AsyncMock) as mock_qa, \
                 patch.object(TrioAgentRunner, "run_auditor_turn", new_callable=AsyncMock) as mock_aud, \
                 patch(
                     "trio_agent_runner.EvidenceValidator.inspect_production_diff",
                     return_value={
                         "is_zero_production_diff": False,
                         "prod_diff_lines": 12,
                         "prod_changed_files": ["src/plan.py"],
                         "non_prod_files": [],
                     },
                 ), \
                 patch("trio_agent_runner.EvidenceValidator.verify_anti_mocking_and_authenticity", return_value=(True, [], {})), \
                 patch(
                     "trio_agent_runner.QualityStateManager.evaluate_and_update",
                     return_value={"regression": {"has_regression": False}, "overall_score": 100.0, "delta": 0.0},
                 ), \
                 patch("trio_agent_runner.EvidenceValidator.check_visual_asset_presence", return_value={"has_assets": True}), \
                 patch(
                     "vision_attachment_analyzer.VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa",
                     new_callable=AsyncMock,
                     return_value={"has_screenshots": True},
                 ), \
                 patch(
                     "autonomy_guard.CollaborationIntelligence.evaluate_auditor_value_alignment",
                     return_value=("PROCEED", "가치 부합 확인"),
                 ):
                mock_qa.return_value = (
                    "[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n\n"
                    "`src/plan.py` 변경 및 백그라운드 통합 로직을 검증했습니다."
                )
                mock_aud.return_value = "[STATUS: AUDIT_PASSED]\n[VALUE: HIGH]\n최종 감사 승인 완료"

                res = await TrioAgentRunner.run_pipeline(
                    session=session, prompt="플랜 구현", main_channel=main_ch
                )

                # 조기 종료 나레이션 1회 + 완결 보고 1회 = 최소 2회 재지시.
                self.assertGreaterEqual(len(coder_calls), 2)
                self.assertIn("서브에이전트/비동기 작업 완료 대기", coder_calls[1])
                self.assertFalse(TrioAgentRunner.is_system_error(res))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
