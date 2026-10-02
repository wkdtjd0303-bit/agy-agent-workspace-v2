#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[StallGuard] 채널 오케스트레이션 교착(정지) 감시 & 자동 해제 회귀 테스트

검증 대상 (실제 장애: 요청 → 80분 무응답 / 상태 타이머 3506초 고정):
  1. 하트비트(touch) 문맥 — '진짜 진행' 만 하트비트를 갱신하는가
  2. 대기열 정체(queue_nudge) — 작업 없는데 큐만 쌓이면 재가동하는가
  3. 무응답 경보(stall_warn) — 기준 초과 시 1회 발송 후 중복 발송 금지
  4. 강제 회수(stall_recover) — 프로세스/태스크/busy/락을 한 번에 해제하는가
  4-1. 심층 작업 관찰(deep_work_notice) — HARD~DEEP 구간에서 살아있는 태스크를 죽이지 않는가
  4-2. 죽은 추적 프로세스 — 프로세스가 죽었으면 DEEP 유예 없이 HARD 에서 회수하는가
  5. 좀비 busy(zombie_recover) — busy 인데 태스크가 죽었으면 즉시 회수하는가
  6. 대기열 락 타임아웃 — 점유 락 강제 교체로 채널 영구 블락 차단 (bot.py)
  7. 증거 파일(reports/watchdog/stalls.json) 물리 기록
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import stall_guard
from stall_guard import StallGuard
from session_manager import ChannelSession


class _FakeSessionManager:
    def __init__(self, sessions):
        self._sessions = {s.channel_id: s for s in sessions}

    def get_all_sessions(self):
        return dict(self._sessions)


class _AliveTask:
    """살아있는 오케스트레이션 태스크 더미 (done()=False / cancel() 기록)."""

    def __init__(self):
        self.cancel_called = False

    def done(self):
        return False

    def cancel(self):
        self.cancel_called = True


def _make_session(channel_id=4242, busy=True, progress_age=0.0, busy_age=0.0, queue=None):
    sess = ChannelSession(channel_id=channel_id, workspace=tempfile.gettempdir())
    now = 1_000_000.0
    sess.last_progress_at = now - progress_age
    if busy:
        sess.is_busy = True
        sess.status = "busy"
        sess.busy_since = now - busy_age
    if queue:
        sess.queue.extend(queue)
    return sess, now


class TestStallGuardDetection(unittest.TestCase):
    def _guard(self, sessions, tmp, **kw):
        notifications = []
        dispatches = []
        evidence_path = kw.pop("evidence_path", os.path.join(tmp, "stalls.json"))
        guard = StallGuard(
            _FakeSessionManager(sessions),
            notify_fn=lambda ch, text: notifications.append((ch, text)),
            dispatch_fn=lambda ch: dispatches.append(ch),
            lock_registry=kw.pop("lock_registry", {}),
            evidence_path=evidence_path,
            **kw,
        )
        return guard, notifications, dispatches

    def test_progress_recent_means_no_action(self):
        """진행 하트비트가 싱싱하면 아무것도 하지 않는다 (정상 작업 보호)."""
        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(progress_age=5.0, busy_age=60.0)
            guard, notes, dispatches = self._guard([sess], tmp)
            events = guard.scan(now=now)
            self.assertEqual(events, [])
            self.assertEqual(notes, [])
            self.assertEqual(dispatches, [])
            self.assertTrue(sess.is_busy)

    def test_touch_resets_stall_clock(self):
        """ChannelSession.touch() 가 하트비트와 경보 플래그를 초기화한다."""
        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(progress_age=500.0)
            sess._stall_warned = True
            sess.touch()
            self.assertLess(now - sess.last_progress_at, 5.0)
            self.assertFalse(sess._stall_warned)

    def test_set_busy_records_busy_since(self):
        """set_busy(True) 가 busy 시작 시각을 기록하고, False 가 해제한다."""
        sess = ChannelSession(channel_id=7, workspace=tempfile.gettempdir())
        sess.set_busy(True, "작업 중")
        self.assertGreater(sess.busy_since, 0.0)
        sess.set_busy(False, "대기")
        self.assertEqual(sess.busy_since, 0.0)
        self.assertFalse(sess.is_busy)

    def test_warn_emitted_exactly_once(self):
        """무응답 경보는 채널당 1회만 발송되고 진행 재개 시 재무장한다."""
        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(progress_age=350.0, busy_age=350.0)
            sess.current_task = _AliveTask()  # 살아있는 오케스트레이션 태스크
            guard, notes, _ = self._guard([sess], tmp, warn_seconds=300, hard_seconds=900)

            first = guard.scan(now=now)
            self.assertEqual([e["action"] for e in first], ["stall_warn"])
            self.assertEqual(len(notes), 1)
            self.assertIn("무응답", notes[0][1])

            second = guard.scan(now=now + 10)
            self.assertEqual(second, [])
            self.assertEqual(len(notes), 1, "중복 경보 발송 금지")

            # 실제 진행이 재개되면(하트비트 갱신) 경보 잠금이 풀린다
            sess.touch()
            self.assertEqual(guard.scan(now=now + 12), [])  # 진행 중 스캔 → 잠금 해제
            sess.last_progress_at = (now + 12) - 350        # 다시 350초 정지
            third = guard.scan(now=now + 12)
            self.assertEqual([e["action"] for e in third], ["stall_warn"])
            self.assertEqual(len(notes), 2)

    def test_hard_recovery_releases_task_busy_and_lock(self):
        """절대 상한(DEEP) 초과 시 태스크 취소 + busy 해제 + 락 교체 + 알림 + 대기열 재가동."""
        with tempfile.TemporaryDirectory() as tmp:
            # 살아있는 태스크는 HARD(900)에서 곧바로 죽이지 않고 DEEP 상한에서 회수한다.
            sess, now = _make_session(progress_age=2000.0, busy_age=2000.0)
            lock_registry = {}
            guard, notes, dispatches = self._guard(
                [sess], tmp, lock_registry=lock_registry,
                warn_seconds=300, hard_seconds=900, deep_seconds=1800,
            )

            async def scenario():
                task = asyncio.ensure_future(asyncio.sleep(60))
                sess.current_task = task
                lock_registry[sess.channel_id] = asyncio.Lock()
                old_lock = lock_registry[sess.channel_id]
                await old_lock.acquire()  # 점유된 락(누군가 영구 점유한 상태)

                events = guard.scan(now=now)
                await asyncio.sleep(0)  # 취소/디스패치 스케줄러 진행
                await asyncio.sleep(0)
                return events, old_lock, task

            events, old_lock, task = asyncio.run(scenario())

            self.assertEqual([e["action"] for e in events], ["stall_recover"])
            self.assertTrue(task.cancelled(), "정지된 오케스트레이션 태스크가 취소되어야 한다")
            self.assertFalse(sess.is_busy, "busy 점유가 해제되어야 한다")
            self.assertIsNot(
                lock_registry[sess.channel_id], old_lock, "점유된 채널 대기열 락이 교체되어야 한다"
            )
            self.assertFalse(lock_registry[sess.channel_id].locked())
            self.assertEqual(len(notes), 1, "회수 알림이 채널로 전달되어야 한다")
            self.assertIn("자동 회수", notes[0][1])
            self.assertEqual(dispatches, [sess.channel_id], "대기열 재가동이 호출되어야 한다")
            self.assertEqual(sess.status, "error")

    def test_live_task_is_observed_not_killed_between_hard_and_deep(self):
        """[오판 방지] HARD 초과라도 태스크가 살아있으면 DEEP 상한까지 관찰만 한다.

        실제 장애: qa1 코드 리뷰(Thinking 모델)가 15분간 stdout 이 조용했지만
        정상 동작 중이었는데 900초에 강제 회수됐다. 이제는 deep_work_notice 만 발송한다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(progress_age=1000.0, busy_age=1000.0)
            sess.current_task = _AliveTask()  # 살아있는 오케스트레이션 태스크
            sess.current_process = None
            # 실제 장애 당시 상태 문자열 그대로 재현:
            sess.status_desc = "🕵️ qa1 코드 리뷰 중 (1/20R · HIGH-Path)"
            guard, notes, dispatches = self._guard(
                [sess], tmp, warn_seconds=300, hard_seconds=900, deep_seconds=1800
            )

            events = guard.scan(now=now)
            self.assertEqual([e["action"] for e in events], ["deep_work_notice"])
            self.assertFalse(sess.current_task.cancel_called, "살아있는 태스크를 죽이면 안 된다")
            self.assertTrue(sess.is_busy, "busy 점유를 유지해야 한다")
            self.assertEqual(dispatches, [])
            self.assertEqual(len(notes), 1)
            self.assertIn("보류", notes[0][1])

            # 관찰 알림은 중복 발송하지 않는다.
            self.assertEqual(guard.scan(now=now + 10), [])

            # 절대 상한(DEEP)을 넘기면 그때는 회수한다.
            events_deep = guard.scan(now=now + 900)  # silence = 1900 > 1800
            self.assertEqual([e["action"] for e in events_deep], ["stall_recover"])
            self.assertFalse(sess.is_busy)

    def test_dead_tracked_process_loses_deep_grace(self):
        """추적 프로세스가 이미 죽었으면 DEEP 유예를 주지 않고 HARD 에서 회수한다.

        (살아있거나 추적 불가한 작업에만 심층 관찰 유예를 준다.)
        """
        class _DeadProc:
            pid = None
            returncode = 0

            def kill(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(progress_age=1000.0, busy_age=1000.0)
            sess.current_task = _AliveTask()
            sess.current_process = _DeadProc()
            guard, notes, dispatches = self._guard(
                [sess], tmp, warn_seconds=300, hard_seconds=900, deep_seconds=1800
            )

            async def scenario():
                evs = guard.scan(now=now)
                await asyncio.sleep(0)
                return evs

            events = asyncio.run(scenario())
            # 1000초는 DEEP(1800) 미만이지만 프로세스가 죽었으므로 HARD(900) 에서 회수된다.
            self.assertEqual([e["action"] for e in events], ["stall_recover"])
            self.assertIn("고아", events[0]["detail"])
            self.assertFalse(sess.is_busy)
            self.assertIsNone(sess.current_process)
            self.assertEqual(dispatches, [sess.channel_id])

    def test_deep_threshold_order_is_enforced(self):
        """경보 < 심층관찰 < 강제회수(절대 상한) 순서가 항상 유지된다."""
        with tempfile.TemporaryDirectory() as tmp:
            g = StallGuard(_FakeSessionManager([]), evidence_path=os.path.join(tmp, "x.json"),
                           warn_seconds=5000, hard_seconds=10, deep_seconds=5)
            self.assertGreater(g.deep_seconds, g.hard_seconds)

    def test_zombie_busy_recovered_promptly(self):
        """busy 인데 오케스트레이션 태스크가 없으면(좀비) 즉시 회수한다."""
        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(progress_age=60.0, busy_age=60.0)
            sess.current_task = None
            guard, notes, dispatches = self._guard(
                [sess], tmp, zombie_seconds=30, warn_seconds=300, hard_seconds=900
            )

            async def scenario():
                events = guard.scan(now=now)
                await asyncio.sleep(0)
                return events

            events = asyncio.run(scenario())
            self.assertEqual([e["action"] for e in events], ["zombie_recover"])
            self.assertFalse(sess.is_busy)
            self.assertEqual(dispatches, [sess.channel_id])

    def test_stale_idle_queue_is_nudged(self):
        """작업이 비어 있는데 큐의 요청만 오래 방치되면 디스패치를 재호동한다."""
        from session_manager import QueuedItem

        with tempfile.TemporaryDirectory() as tmp:
            sess, now = _make_session(busy=False, progress_age=0.0)
            sess.is_busy = False
            sess.queue.append(QueuedItem(message=None, prompt="스토어 운영해줘", created_at=now - 240))
            guard, notes, dispatches = self._guard([sess], tmp, queue_wait_seconds=60)

            async def scenario():
                events = guard.scan(now=now)
                await asyncio.sleep(0)
                # 쿨다운 중 중복 디스패치 금지 → 즉시 재스캔해도 한 번만
                events2 = guard.scan(now=now + 1)
                await asyncio.sleep(0)
                return events, events2

            events, events2 = asyncio.run(scenario())
            self.assertEqual([e["action"] for e in events], ["queue_nudge"])
            self.assertEqual(dispatches, [sess.channel_id])
            self.assertEqual(events[0]["queue_depth"], 1)
            self.assertEqual(events2, [])
            self.assertEqual(len(dispatches), 1)

    def test_evidence_file_is_written_physically(self):
        """감지/조치는 디스크 증거 파일로 남는다 (말이 아닌 파일로 증명)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stalls.json")
            sess, now = _make_session(progress_age=1000.0, busy_age=1000.0)
            sess.current_task = None
            guard, _, _ = self._guard([sess], tmp, evidence_path=path, zombie_seconds=30)

            async def scenario():
                events = guard.scan(now=now)
                await asyncio.sleep(0)
                return events

            asyncio.run(scenario())
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["schema"], "stall_guard/v1")
            self.assertGreaterEqual(payload["total_events"], 1)
            self.assertIn(payload["events"][-1]["action"], ("stall_recover", "zombie_recover"))
            self.assertEqual(payload["events"][-1]["channel_id"], sess.channel_id)

    def test_threshold_order_is_enforced(self):
        """경보 < 강제회수 순서가 항상 유지되도록 방어한다."""
        with tempfile.TemporaryDirectory() as tmp:
            g = StallGuard(_FakeSessionManager([]), evidence_path=os.path.join(tmp, "x.json"),
                           warn_seconds=5000, hard_seconds=10)
            self.assertGreater(g.hard_seconds, g.warn_seconds)

    def test_run_loop_scans_periodically_and_survives_errors(self):
        """감시 루프는 주기적으로 스캔하고, 깨진 세션 예외가 나와도 멈추지 않는다."""
        from session_manager import QueuedItem

        class _BrokenSession:
            """queue 접근마다 예외를 던지는 오염된 세션 (가드 생존력 검증용)."""

            channel_id = 999001

            @property
            def queue(self):
                raise ValueError("깨진 세션")

        with tempfile.TemporaryDirectory() as tmp:
            sess, _ = _make_session(busy=False, progress_age=0.0)
            sess.is_busy = False
            sess.queue.append(QueuedItem(message=None, prompt="요청", created_at=0.001))
            dispatches = []
            guard = StallGuard(
                _FakeSessionManager([_BrokenSession(), sess]),
                dispatch_fn=lambda ch: dispatches.append(ch),
                evidence_path=os.path.join(tmp, "stalls.json"),
                queue_wait_seconds=1.0,
                tick_seconds=0.05,
            )

            async def scenario():
                task = asyncio.ensure_future(guard.run())
                await asyncio.sleep(0.3)
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                return guard.records, list(dispatches)

            records, calls = asyncio.run(scenario())
            self.assertGreaterEqual(len(records), 1, "루프가 계속 스캔을 수행해야 한다")
            self.assertGreaterEqual(len(calls), 1, "깨진 세션과 무관하게 정상 세션은 처리되어야 한다")


class TestBotQueueLockRecovery(unittest.IsolatedAsyncioTestCase):
    """bot.py 대기열 락 — 무제한 대기 → 타임아웃 + 강제 교체 회수."""

    async def asyncSetUp(self):
        import bot
        self.bot = bot

    async def test_stuck_lock_is_replaced_and_acquired(self):
        cid = 777001
        bot = self.bot
        held = asyncio.Lock()
        await held.acquire()  # 점유 상태 (누군가 멈춰서 안 놓아줌)
        bot._channel_queue_locks[cid] = held

        with patch.object(bot, "QUEUE_LOCK_TIMEOUT_SECONDS", 0.2), patch.object(
            bot, "_thermal_cooldown_holds_lock", return_value=False
        ):
            got = await bot._acquire_queue_lock_or_recover(cid)

        self.assertIsNotNone(got, "타임아웃 후에도 락을 취득할 수 있어야 한다")
        self.assertIsNot(got, held, "점유된 락은 교체되어야 한다")
        self.assertTrue(got.locked())
        self.assertTrue(held.locked(), "원래 점유자는 자기 락을 그대로 둔다")
        # 정리
        got.release()
        held.release()
        bot._channel_queue_locks.pop(cid, None)

    async def test_free_lock_acquired_without_replacement(self):
        cid = 777002
        bot = self.bot
        bot._channel_queue_locks.pop(cid, None)
        original = bot.get_channel_queue_lock(cid)
        got = await bot._acquire_queue_lock_or_recover(cid)
        self.assertIs(got, original, "정상 경로에서는 락을 교체하지 않는다")
        got.release()
        bot._channel_queue_locks.pop(cid, None)

    async def test_thermal_cooldown_lock_is_not_replaced(self):
        """열 관리 비상 쿨다운 중의 락 점유는 정상 → 교체하지 않고 기다렸다가 그대로 취득."""
        cid = 777003
        bot = self.bot
        held = asyncio.Lock()
        await held.acquire()
        bot._channel_queue_locks[cid] = held

        async def release_soon():
            await asyncio.sleep(0.25)
            held.release()  # 쿨다운이 끝나고 점유자가 락을 놓는 상황

        with patch.object(bot, "QUEUE_LOCK_TIMEOUT_SECONDS", 0.1), patch.object(
            bot, "THERMAL_LOCK_GRACE_SECONDS", 2.0
        ), patch.object(bot, "_thermal_cooldown_holds_lock", return_value=True):
            releaser = asyncio.create_task(release_soon())
            got = await bot._acquire_queue_lock_or_recover(cid)
            await releaser

        self.assertIs(got, held, "쿨다운 점유는 교체하지 않고 기다려서 그대로 취득한다")
        self.assertIs(bot._channel_queue_locks[cid], held, "락 객체가 유지되어야 한다")
        if got.locked():
            got.release()
        bot._channel_queue_locks.pop(cid, None)


class _FakeCtx:
    """`!stall` 명령 검증용 최소 컨텍스트."""

    def __init__(self, channel_id=555001):
        self.channel = type("C", (), {"id": channel_id})()
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class TestStallCommand(unittest.IsolatedAsyncioTestCase):
    async def test_stall_command_renders_overview(self):
        """`!stall` 이 임계값·채널 현황·교착 이력·증거 경로를 한 화면에 보여준다."""
        import bot

        cmd = bot.stall_command
        self.assertEqual(cmd.name, "stall", "stall 명령이 등록되어 있어야 한다")
        self.assertIn("정지감시", list(cmd.aliases), "한글 별칭이 등록되어 있어야 한다")

        ctx = _FakeCtx()
        await cmd.callback(ctx)

        self.assertEqual(len(ctx.sent), 1, "응답을 1회 보내야 한다")
        embed = ctx.sent[0][1].get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("StallGuard", embed.title)
        field_names = " | ".join(f.name for f in embed.fields)
        self.assertIn("임계값", field_names)
        self.assertIn("물리적 증거", field_names)


if __name__ == "__main__":
    unittest.main()
