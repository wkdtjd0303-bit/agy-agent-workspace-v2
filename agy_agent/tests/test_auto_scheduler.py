#!/usr/bin/env python3
"""
Unit tests for [⏰ !auto] 채널별 자동 알람 / 스케줄 웨이크업 엔진 (auto_scheduler).

검증 범위:
1. 스케줄 표현 파서 (간격/매일/매주/평일/한국어/오류)
2. 다음 발동 시각 계산
3. 채널별 독립 설정 + 기본값 OFF
4. 발동 루프(tick) — 정상 발동 / busy 연기 / 핸들러 미등록
5. 영속화 및 초기화
6. 봇 `!auto` 명령어 라우팅(on/off/message/schedule/now/reset)
"""

import os
import sys
import time
import json
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import auto_scheduler
from auto_scheduler import (
    ChannelAutoScheduler, ScheduleParseError, parse_schedule, compute_next_run,
    humanize_schedule, DEFAULT_MESSAGE,
)

CH_A = 200001
CH_B = 200002


class TestScheduleParsing(unittest.TestCase):
    def test_interval_forms(self):
        for spec, expected in [
            ("every 30m", 30),
            ("every 2h", 120),
            ("every 1h30m", 90),
            ("30분", 30),
            ("2시간", 120),
            ("6h", 360),
            ("90m", 90),
            ("간격 15m", 15),
        ]:
            fields, _ = parse_schedule(spec)
            self.assertEqual(fields["schedule_type"], "interval", spec)
            self.assertEqual(fields["interval_minutes"], expected, spec)

    def test_daily_forms(self):
        for spec in ["daily 09:00", "매일 09:00", "매일", "09:00", "21:30", "오후 6시", "9:30 pm"]:
            fields, _ = parse_schedule(spec)
            self.assertEqual(fields["schedule_type"], "daily", spec)

    def test_weekly_forms(self):
        for spec, day in [
            ("weekly mon 09:00", 0),
            ("매주 월요일 09:00", 0),
            ("매주 금 18:30", 4),
            ("weekly sunday 10:00", 6),
        ]:
            fields, _ = parse_schedule(spec)
            self.assertEqual(fields["schedule_type"], "weekly", spec)
            self.assertEqual(fields["weekly_day"], day, spec)

    def test_weekdays_forms(self):
        for spec in ["weekdays 09:00", "평일 09:00"]:
            fields, _ = parse_schedule(spec)
            self.assertEqual(fields["schedule_type"], "weekdays", spec)
            self.assertEqual(fields["daily_time"], "09:00", spec)

    def test_invalid_schedule_raises(self):
        for spec in ["", "garbage", "매주 ???", "every"]:
            with self.assertRaises(ScheduleParseError, msg=spec):
                parse_schedule(spec)

    def test_interval_bounds(self):
        with self.assertRaises(ScheduleParseError):
            parse_schedule("every 99999h")

    def test_humanize(self):
        self.assertEqual(humanize_schedule({"schedule_type": "interval", "interval_minutes": 30}), "매 30분마다")
        self.assertEqual(humanize_schedule({"schedule_type": "interval", "interval_minutes": 120}), "매 2시간마다")
        self.assertEqual(humanize_schedule({"schedule_type": "daily", "daily_time": "09:00"}), "매일 09:00")
        self.assertEqual(humanize_schedule({"schedule_type": "weekly", "weekly_day": 0}), "매주 월요일 09:00")

    def test_next_run_is_future(self):
        now = time.time()
        for spec in ["every 30m", "daily 09:00", "weekly mon 09:00", "평일 09:00"]:
            fields, _ = parse_schedule(spec)
            nxt = compute_next_run(fields, now=now)
            self.assertGreater(nxt, now, spec)


class TestChannelAutoScheduler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ChannelAutoScheduler._configs = {}
        ChannelAutoScheduler._loaded = False
        # 기존 핸들러는 보존했다가 tearDown 에서 복원한다 (다른 테스트와 상태 공유).
        self._prev_handler = ChannelAutoScheduler._fire_handler
        ChannelAutoScheduler.reset_channel(CH_A)
        ChannelAutoScheduler.reset_channel(CH_B)

    def tearDown(self):
        ChannelAutoScheduler.reset_channel(CH_A)
        ChannelAutoScheduler.reset_channel(CH_B)
        ChannelAutoScheduler._fire_handler = self._prev_handler

    # ------------------------------------------------------------------
    def test_default_is_off(self):
        cfg = ChannelAutoScheduler.get_config(CH_A)
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["message"], DEFAULT_MESSAGE)
        self.assertEqual(cfg["schedule_type"], "interval")
        self.assertEqual(cfg["interval_minutes"], 60)

    def test_channels_are_independent(self):
        ChannelAutoScheduler.set_enabled(CH_A, True)
        ChannelAutoScheduler.set_message(CH_B, "B채널 전용 메시지")
        ChannelAutoScheduler.set_schedule(CH_B, "daily 07:30")

        self.assertTrue(ChannelAutoScheduler.is_enabled(CH_A))
        self.assertFalse(ChannelAutoScheduler.is_enabled(CH_B))
        self.assertEqual(ChannelAutoScheduler.get_config(CH_B)["message"], "B채널 전용 메시지")
        self.assertEqual(ChannelAutoScheduler.get_config(CH_A)["message"], DEFAULT_MESSAGE)

    def test_enable_computes_next_run(self):
        cfg = ChannelAutoScheduler.set_enabled(CH_A, True)
        self.assertTrue(cfg["enabled"])
        self.assertGreater(cfg["next_run_ts"], time.time())
        off = ChannelAutoScheduler.set_enabled(CH_A, False)
        self.assertEqual(off["next_run_ts"], 0.0)

    def test_due_channels(self):
        self.assertEqual(ChannelAutoScheduler.due_channels(), [])
        ChannelAutoScheduler.set_enabled(CH_A, True)
        # 아직 도래하지 않음
        self.assertEqual(ChannelAutoScheduler.due_channels(), [])
        # 강제로 과거 시각 주입
        cfg = ChannelAutoScheduler._touch(CH_A)
        cfg["next_run_ts"] = time.time() - 5
        due = ChannelAutoScheduler.due_channels()
        self.assertEqual([c for c, _ in due], [CH_A])  # int 키

    async def test_tick_fires_and_reschedules(self):
        fired = []

        async def handler(cid, msg, cfg):
            fired.append((cid, msg))
            return True

        ChannelAutoScheduler.set_fire_handler(handler)
        ChannelAutoScheduler.set_message(CH_A, "점검 메시지")
        ChannelAutoScheduler.set_enabled(CH_A, True)
        cfg = ChannelAutoScheduler._touch(CH_A)
        cfg["next_run_ts"] = time.time() - 1

        result = await ChannelAutoScheduler.tick()
        self.assertEqual(result, [CH_A])
        self.assertEqual(fired[0][0], CH_A)
        self.assertEqual(fired[0][1], "점검 메시지")
        after = ChannelAutoScheduler.get_config(CH_A)
        self.assertEqual(after["run_count"], 1)
        self.assertIsNotNone(after["last_run_ts"])
        self.assertGreater(after["next_run_ts"], time.time())

    async def test_tick_defers_when_busy(self):
        async def handler(cid, msg, cfg):
            return False  # 채널 busy

        ChannelAutoScheduler.set_fire_handler(handler)
        ChannelAutoScheduler.set_enabled(CH_A, True)
        cfg = ChannelAutoScheduler._touch(CH_A)
        cfg["next_run_ts"] = time.time() - 1

        result = await ChannelAutoScheduler.tick()
        self.assertEqual(result, [])
        after = ChannelAutoScheduler.get_config(CH_A)
        self.assertEqual(after["missed_count"], 1)
        self.assertEqual(after["run_count"], 0)

    async def test_trigger_now(self):
        calls = []

        async def handler(cid, msg, cfg):
            calls.append(cid)
            return True

        ChannelAutoScheduler.set_fire_handler(handler)
        res = await ChannelAutoScheduler.trigger_now(CH_A)
        self.assertTrue(res["fired"])
        self.assertEqual(calls, [CH_A])

    def test_persistence(self):
        ChannelAutoScheduler.set_enabled(CH_A, True)
        ChannelAutoScheduler.set_message(CH_A, "영속 테스트")
        ChannelAutoScheduler.set_schedule(CH_A, "weekly fri 18:00")

        raw = json.loads(open(auto_scheduler.CONFIG_FILE, "r", encoding="utf-8").read())
        self.assertEqual(raw[str(CH_A)]["message"], "영속 테스트")
        self.assertEqual(raw[str(CH_A)]["weekly_day"], 4)

        ChannelAutoScheduler._configs = {}
        ChannelAutoScheduler._loaded = False
        reloaded = ChannelAutoScheduler.get_config(CH_A)
        self.assertTrue(reloaded["enabled"])
        self.assertEqual(reloaded["message"], "영속 테스트")

    def test_summarize(self):
        ChannelAutoScheduler.set_enabled(CH_A, True)
        s = ChannelAutoScheduler.summarize(CH_A)
        self.assertTrue(s["enabled"])
        self.assertNotEqual(s["next_run_text"], "-")  # 활성 상태면 예약 시각 표시
        self.assertEqual(s["schedule_desc"], "매 1시간마다")

    def test_summarize_when_disabled(self):
        s = ChannelAutoScheduler.summarize(CH_B)
        self.assertFalse(s["enabled"])
        self.assertEqual(s["next_run_text"], "-")


class TestAutoSchedulerLoop(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ChannelAutoScheduler._configs = {}
        ChannelAutoScheduler._loaded = False
        ChannelAutoScheduler.reset_channel(CH_A)

    async def test_loop_start_stop(self):
        started = await ChannelAutoScheduler.start()
        self.assertTrue(started)
        # 중복 시작 방지
        again = await ChannelAutoScheduler.start()
        self.assertFalse(again)
        await ChannelAutoScheduler.stop()
        self.assertFalse(ChannelAutoScheduler._running)


class TestBotAutoCommand(unittest.IsolatedAsyncioTestCase):
    """봇 `!auto` 명령어 라우팅 테스트.

    ※ bot.auto_command 는 hot-reload 패턴으로 auto_scheduler 를 reload 하므로,
      클래스 참조를 고정하지 않고 **모듈 속성으로 동적 접근**한다.
    """

    def setUp(self):
        # 핸들러가 미등록 상태(다른 테스트가 초기화한 경우)면 봇 핸들러를 다시 건다.
        import bot
        if self._S()._fire_handler is None:
            self._S().set_fire_handler(bot._fire_auto_channel_wakeup)
        self._S().reset_channel(CH_A)

    def tearDown(self):
        self._S().reset_channel(CH_A)

    def _S(self):
        import auto_scheduler
        return auto_scheduler.ChannelAutoScheduler

    def _ctx(self):
        ctx = MagicMock()
        ctx.channel.id = CH_A
        ctx.author.id = 777
        ctx.send = AsyncMock()
        ctx.reply = AsyncMock()
        return ctx

    async def test_auto_command_on_off(self):
        import bot
        ctx = self._ctx()

        await bot.auto_command(ctx, "on")
        self.assertTrue(self._S().is_enabled(CH_A))

        await bot.auto_command(ctx, "off")
        self.assertFalse(self._S().is_enabled(CH_A))

    async def test_auto_command_message_and_schedule(self):
        import bot
        ctx = self._ctx()

        await bot.auto_command(ctx, "message", "마케팅 성과 점검해줘")
        self.assertEqual(self._S().get_config(CH_A)["message"], "마케팅 성과 점검해줘")

        await bot.auto_command(ctx, "schedule", "daily", "08:30")
        cfg = self._S().get_config(CH_A)
        self.assertEqual(cfg["schedule_type"], "daily")
        self.assertEqual(cfg["daily_time"], "08:30")

        # 잘못된 스케줄 → 경고 응답 + 기존 설정 유지
        await bot.auto_command(ctx, "schedule", "garbage")
        ctx.reply.assert_called()
        self.assertEqual(self._S().get_config(CH_A)["daily_time"], "08:30")

    async def test_auto_command_on_with_schedule(self):
        import bot
        ctx = self._ctx()
        await bot.auto_command(ctx, "on", "every", "30m")
        cfg = self._S().get_config(CH_A)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["interval_minutes"], 30)

    async def test_auto_command_reset(self):
        import bot
        ctx = self._ctx()
        self._S().set_enabled(CH_A, True)
        await bot.auto_command(ctx, "reset")
        self.assertEqual(self._S().get_config(CH_A)["enabled"], False)

    async def test_auto_command_status_dashboard(self):
        import bot
        ctx = self._ctx()
        await bot.auto_command(ctx)
        ctx.send.assert_called()
        kwargs = ctx.send.call_args[1]
        self.assertEqual(kwargs["view"].__class__.__name__, "AutoDashboardView")

    async def test_auto_command_unknown_shows_help(self):
        import bot
        ctx = self._ctx()
        await bot.auto_command(ctx, "unknownthing")
        ctx.reply.assert_called()
        self.assertIn("사용법", ctx.reply.call_args[0][0])

    async def test_auto_now_fires_channel(self):
        """`!auto now` 는 fire handler 로 즉시 발동하고 확인 응답을 돌려준다."""
        import bot
        ctx = self._ctx()
        self._S().set_message(CH_A, "즉시 점검")

        ch = MagicMock()
        ch.send = AsyncMock()
        with patch.object(bot.bot, "get_channel", return_value=ch), \
             patch.object(bot.session_manager, "get_session", return_value=MagicMock(is_busy=False)):
            await bot.auto_command(ctx, "now")
            self.assertTrue(ctx.reply.called)
            self.assertIn("즉시 발동", ctx.reply.call_args[0][0])
            # 발동 배너가 실제로 채널에 전송되었는지
            self.assertGreaterEqual(ch.send.await_count, 1)

    async def test_fire_handler_dispatches_to_pipeline(self):
        """등록된 fire handler 는 채널을 깨워 execute_channel_prompt 로 파이프라인에 투입한다."""
        import bot
        self._S().set_message(CH_A, "웨이크업 디스패치")

        ch = MagicMock()
        ch.send = AsyncMock()
        with patch.object(bot, "execute_channel_prompt", new_callable=AsyncMock) as mock_exec, \
             patch.object(bot.bot, "get_channel", return_value=ch), \
             patch.object(bot.session_manager, "get_session", return_value=MagicMock(is_busy=False)):
            handler = self._S()._fire_handler
            self.assertIsNotNone(handler)
            ok = await handler(CH_A, "웨이크업 디스패치", {})
            self.assertTrue(ok)
            await asyncio.sleep(0.2)
            self.assertTrue(mock_exec.called)

    async def test_fire_handler_defers_busy_channel(self):
        """작업 중(busy) 채널은 발동을 연기한다(False → scheduler 가 뒤로 미룬다)."""
        import bot
        handler = self._S()._fire_handler
        ch = MagicMock()
        ch.send = AsyncMock()
        with patch.object(bot.bot, "get_channel", return_value=ch), \
             patch.object(bot.session_manager, "get_session", return_value=MagicMock(is_busy=True)):
            ok = await handler(CH_A, "바쁜 채널", {})
            self.assertFalse(ok)
            self.assertEqual(ch.send.await_count, 0)


if __name__ == "__main__":
    unittest.main()
