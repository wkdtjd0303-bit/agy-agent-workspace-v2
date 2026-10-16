# -*- coding: utf-8 -*-
"""Unit tests for multi-agent pipeline enhancements:
1. Early termination detection and [TURN_DONE] marker verification
2. Commander hard limits, diminishing returns detection, and decision parsing
3. AutoMetricsHistory PDCA cycle recording and prompt context generation
4. ImageCreatorHub pre-hook notification and channel resolution
"""
import os
import json
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

import os
import sys

# Ensure agy_agent root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from round_control import (
    has_turn_done_marker,
    detect_premature_turn_stop,
    parse_commander_decision,
    detect_diminishing_returns,
    TURN_DONE_MARKER,
    COMMANDER_DECISION_COMPLETE,
    COMMANDER_DECISION_CONTINUE,
    COMMANDER_MAX_CYCLES,
)
from auto_scheduler import AutoMetricsHistory
from commander import Milestone5W1HPlan
from image_creator_hub import ImageCreatorHub


class TestAgentPipelineEnhancements(unittest.TestCase):

    def test_turn_done_marker_detection(self):
        """[TURN_DONE] 토큰 탐지 무결성 검증"""
        self.assertFalse(has_turn_done_marker(""))
        self.assertFalse(has_turn_done_marker("작업을 완료했습니다."))
        self.assertTrue(has_turn_done_marker(f"구현 완료되었습니다.\n{TURN_DONE_MARKER}"))

    def test_detect_premature_turn_stop(self):
        """조기 종료 및 미완료 안내성 문구 감지 검증"""
        # 1. 빈 텍스트
        premature, reason = detect_premature_turn_stop("")
        self.assertTrue(premature)
        self.assertIn("비어있음", reason)

        # 2. 극단적 단답
        premature, reason = detect_premature_turn_stop("확인했습니다.")
        self.assertTrue(premature)
        self.assertIn("단답형", reason)

        # 3. 미래 시제 / 대기 안내 문구
        text_waiting = "코드 분석을 시작합니다. 잠시만 대기하고 있습니다."
        premature, reason = detect_premature_turn_stop(text_waiting)
        self.assertTrue(premature)
        self.assertIn("미래 시제/대기", reason)

        text_will_do = "기능을 추가해야 합니다. 파일 수정 후 계속 진행하도록 하겠습니다."
        premature, reason = detect_premature_turn_stop(text_will_do)
        self.assertTrue(premature)
        self.assertIn("미래 시제/대기", reason)

        # 4. [TURN_DONE] 포함된 정상 완결
        text_done = f"모든 컴포넌트 구현 및 빌드 검증을 완료하였습니다.\n\n{TURN_DONE_MARKER}"
        premature, reason = detect_premature_turn_stop(text_done)
        self.assertFalse(premature)
        self.assertEqual(reason, "")

    def test_parse_commander_decision(self):
        """사령관 의사결정 파싱 ([COMMANDER_DECISION: COMPLETE|CONTINUE]) 검증"""
        self.assertEqual(parse_commander_decision(""), "")
        self.assertEqual(parse_commander_decision("일반 텍스트"), "")

        self.assertEqual(
            parse_commander_decision(f"목표 달성!\n{COMMANDER_DECISION_COMPLETE}"),
            "COMPLETE"
        )
        self.assertEqual(
            parse_commander_decision(f"추가 개선 필요\n{COMMANDER_DECISION_CONTINUE}"),
            "CONTINUE"
        )
        self.assertEqual(
            parse_commander_decision("하위 태스크 진행\n[COMMANDER_DECISION: complete]\n끝"),
            "COMPLETE"
        )

    def test_detect_diminishing_returns(self):
        """수확 체감(무의미한 핑퐁 반복) 감지 검증"""
        # cycle 0, 1은 수확 체감 판정 유예
        diminishing, _ = detect_diminishing_returns(0, 0, cycle=0)
        self.assertFalse(diminishing)
        diminishing, _ = detect_diminishing_returns(0, 0, cycle=1)
        self.assertFalse(diminishing)

        # cycle 2 이상: 2회 연속 0라인 변경
        diminishing, reason = detect_diminishing_returns(0, 0, cycle=2)
        self.assertTrue(diminishing)
        self.assertIn("0라인", reason)

        # cycle 2 이상: 2회 연속 5라인 미만 미미한 변경
        diminishing, reason = detect_diminishing_returns(2, 3, cycle=3)
        self.assertTrue(diminishing)
        self.assertIn("미미", reason)

        # 정상적인 충분한 변경량
        diminishing, _ = detect_diminishing_returns(50, 42, cycle=2)
        self.assertFalse(diminishing)

    def test_commander_plan_directive_has_decision(self):
        """Milestone5W1HPlan 지침에 사령관 의사결정 태그가 올바르게 주입되는지 검증"""
        plan = Milestone5W1HPlan(
            version="v1.1",
            title="테스트 기능 구현",
            domain="saas_web",
            why="단위 테스트 검증",
            what=["테스트 케이스 작성"],
            how="pytest",
            who_where="agy_agent/tests",
            when_acceptance=["모든 테스트 통과"],
            decision="COMPLETE"
        )
        directive = plan.build_directive_for_coder()
        self.assertIn("[COMMANDER_DECISION: COMPLETE]", directive)

        discord_msg = plan.to_discord_message()
        self.assertIn("에픽 완결 (COMPLETE)", discord_msg)

    def test_auto_metrics_history_pdca_lifecycle(self):
        """!auto PDCA 지표 이력 기록 및 프롬프트 주입 포맷 검증"""
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            test_history_file = Path(tmpdir) / "test_metrics_history.json"
            AutoMetricsHistory._cache = None
            with patch("auto_scheduler.METRICS_HISTORY_FILE", test_history_file), \
                 patch("auto_scheduler.STATE_DIR", Path(tmpdir)):
                # 1. 초기 상태: 이력 없음
                self.assertEqual(AutoMetricsHistory.get_history(12345), [])
                self.assertIsNone(AutoMetricsHistory.get_latest(12345))
                initial_context = AutoMetricsHistory.format_pdca_context(12345)
                self.assertIn("최초 실행", initial_context)

                # 2. 1회차 기록
                entry1 = AutoMetricsHistory.record_cycle(
                    channel_id=12345,
                    target_metrics={"CTR": "1.2%", "leads": 5},
                    action_taken="인스타그램 릴스 CTA 버튼 A/B 테스트",
                    observed_result="CTR 1.2% 도달, 신규 리드 5건 유입",
                    hypothesis_delta="첫 3초 훅 변경으로 클릭률 상승 확인"
                )
                self.assertEqual(entry1["cycle"], 1)

                latest = AutoMetricsHistory.get_latest(12345)
                self.assertIsNotNone(latest)
                self.assertEqual(latest["cycle"], 1)
                self.assertEqual(latest["target_metrics"]["CTR"], "1.2%")

                pdca_text = AutoMetricsHistory.format_pdca_context(12345)
                self.assertIn("직전 1회차 대조", pdca_text)
                self.assertIn("CTR: 1.2%", pdca_text)
                self.assertIn("인스타그램 릴스 CTA", pdca_text)
                self.assertIn("PDCA 4단계 실행 프레임워크", pdca_text)

                # 3. 2회차 기록
                entry2 = AutoMetricsHistory.record_cycle(
                    channel_id=12345,
                    target_metrics={"CTR": "2.4%", "leads": 12},
                    action_taken="상단 고정 댓글 링크 프로모션",
                    observed_result="CTR 2.4% 초과 달성, 리드 12건",
                    hypothesis_delta="댓글 상단 핀 고정이 전환율에 주효함"
                )
                self.assertEqual(entry2["cycle"], 2)

                history = AutoMetricsHistory.get_history(12345)
                self.assertEqual(len(history), 2)
                latest2 = AutoMetricsHistory.get_latest(12345)
                self.assertEqual(latest2["cycle"], 2)
                self.assertEqual(latest2["target_metrics"]["leads"], 12)

    def test_image_pre_hook_notification_dispatch(self):
        """이미지 생성 사전 알림(Pre-hook) 디스패치 검증"""
        import asyncio

        mock_bot = MagicMock()
        mock_bot.is_ready.return_value = True
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_bot.get_channel.return_value = mock_channel

        async def _run():
            ok = await ImageCreatorHub.send_pre_hook_notification(
                bot_client=mock_bot,
                prompt="미래지향적 우주선 로고",
                model_display="Gemini 3.8 Flash",
                asset_name="spaceship_logo",
                origin_channel_id=987654321,
            )
            self.assertTrue(ok)
            mock_channel.send.assert_called_once()
            sent_msg = mock_channel.send.call_args[0][0]
            self.assertIn("Gemini 3.8 Flash", sent_msg)
            self.assertIn("spaceship_logo.png", sent_msg)
            self.assertIn("미래지향적 우주선 로고", sent_msg)
            self.assertIn("<#987654321>", sent_msg)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
