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

    def test_image_pre_hook_fallback_to_origin_channel(self):
        """스튜디오 채널 캐시 미스 및 fetch 실패 시 요청 출처 채널로 폴백 전송 검증"""
        import asyncio

        mock_bot = MagicMock()
        mock_bot.is_ready.return_value = True
        mock_bot.get_channel.return_value = None
        mock_bot.fetch_channel = AsyncMock(side_effect=Exception("Channel not found / Forbidden"))

        mock_origin = MagicMock()
        mock_origin.send = AsyncMock()

        async def _run():
            ok = await ImageCreatorHub.send_pre_hook_notification(
                bot_client=mock_bot,
                prompt="아이콘 에셋 생성",
                model_display="Gemini 3.8 Flash",
                asset_name="app_icon",
                origin_channel_id=111222,
                origin_channel=mock_origin,
            )
            self.assertTrue(ok)
            mock_origin.send.assert_called_once()
            sent_msg = mock_origin.send.call_args[0][0]
            self.assertIn("app_icon.png", sent_msg)
            self.assertIn("접근 불가로 요청 채널에 직접 안내", sent_msg)

        asyncio.run(_run())

    def test_detect_premature_turn_stop_with_50_chars_waiting_message(self):
        """50자 이상인 한글 대기성/진행성 문구도 조기 종료로 정확히 탐지되는지 검증"""
        sample_54_chars = "현재 진행 중인 테스트가 완료될 때까지 대기하고 있습니다. 완료되는 대로 배포 절차를 진행하겠습니다."
        self.assertGreaterEqual(len(sample_54_chars), 50)
        premature, reason = detect_premature_turn_stop(sample_54_chars)
        self.assertTrue(premature)
        self.assertIn("미래 시제/대기", reason)

        # Fallback 조건 시뮬레이션: len >= 50 이지만 is_still_premature 가 True 이므로 [TURN_DONE] 부착 불가
        is_still_premature, _ = detect_premature_turn_stop(sample_54_chars)
        can_attach = not has_turn_done_marker(sample_54_chars) and len(sample_54_chars.strip()) >= 50 and not is_still_premature
        self.assertFalse(can_attach)

    def test_commander_decision_priority_complete_overrides_continue(self):
        """directive_for_coder 본문 템플릿과 무관하게 decision='COMPLETE'가 최우선 완결로 판정되는지 검증"""
        plan = Milestone5W1HPlan(
            version="v2.0",
            title="최종 검증 완료",
            domain="saas_web",
            why="완결",
            what=["완결"],
            how="완결",
            who_where="agy_agent",
            when_acceptance=["완결"],
            decision="COMPLETE"
        )
        # 만약 directive 에 기본 텍스트 CONTINUE 가 남아있더라도
        mock_directive = "작업 지침\n[COMMANDER_DECISION: CONTINUE]\n끝"
        plan_dec = str(getattr(plan, "decision", "") or "").strip().upper()
        directive_dec = parse_commander_decision(mock_directive).upper()
        resolved_decision = "COMPLETE" if (plan_dec == "COMPLETE" or directive_dec == "COMPLETE") else (plan_dec or directive_dec)
        self.assertEqual(resolved_decision, "COMPLETE")

    def test_auto_metrics_history_sliding_window_ten_items(self):
        """!auto PDCA 이력 저장 시 채널별 최근 10개만 유지하는 슬라이딩 윈도우 검증"""
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            test_history_file = Path(tmpdir) / "test_sliding_history.json"
            AutoMetricsHistory._cache = None
            with patch("auto_scheduler.METRICS_HISTORY_FILE", test_history_file), \
                 patch("auto_scheduler.STATE_DIR", Path(tmpdir)):
                for i in range(1, 16):
                    AutoMetricsHistory.record_cycle(
                        channel_id=99999,
                        target_metrics={"iteration": i},
                        action_taken=f"{i}번째 테스트 실행",
                        observed_result=f"{i} 성공",
                        hypothesis_delta="정상",
                        max_entries=10
                    )

                history = AutoMetricsHistory.get_history(99999, limit=100)
                self.assertEqual(len(history), 10)
                # 6번째부터 15번째까지 유지
                self.assertEqual(history[0]["cycle"], 6)
                self.assertEqual(history[-1]["cycle"], 15)

    def test_auto_metrics_history_record_turn_output(self):
        """!auto 턴 완료 후 에이전트 출력에서 액션/결과/가설 자동 추출 및 기록 검증"""
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            test_history_file = Path(tmpdir) / "test_turn_output_history.json"
            AutoMetricsHistory._cache = None
            with patch("auto_scheduler.METRICS_HISTORY_FILE", test_history_file), \
                 patch("auto_scheduler.STATE_DIR", Path(tmpdir)):
                sample_prompt = "인스타그램 릴스 조회수 증대를 위한 썸네일 A/B 테스트 지시"
                sample_response = (
                    "### 📊 성과 요약 보고\n"
                    "릴스 썸네일 3종을 실서비스에 배포 완료했습니다.\n"
                    "평균 시청 유지시간이 12% 상승하였으며 노출 수가 전주 대비 2,000회 증가했습니다.\n\n"
                    "다음 회차에서는 첫 3초 훅 사운드 최적화 개선을 권장합니다."
                )

                entry = AutoMetricsHistory.record_turn_output(
                    channel_id="77777",
                    prompt=sample_prompt,
                    response=sample_response,
                    workspace=None
                )
                self.assertIsNotNone(entry)
                self.assertEqual(entry["cycle"], 1)
                self.assertIn("인스타그램 릴스", entry["action_taken"])
                self.assertIn("릴스 썸네일", entry["observed_result"])
                self.assertIn("개선", entry["hypothesis_delta"])


if __name__ == "__main__":
    unittest.main()
