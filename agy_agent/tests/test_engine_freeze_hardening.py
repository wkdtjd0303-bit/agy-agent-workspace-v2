#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[🛡️ 3603초 프리징 원천 차단] 회귀 테스트
=================================================

실제 장애 보고:
  - opencode-go 모델로 장시간 작업하거나, 라우터 HTTP 400 오류가 뜨면
    디스코드 상태 타이머가 3603초(=엔진 타임아웃 3600s + 여유)에 고정된 채
    채널 전체가 프리징된다.

원인(이 테스트가 고정하는 계약):
  1. 엔진 어댑터가 "실행 제한 시간 초과" 로 break 한 뒤 프로세스를 죽이지 않고
     `await proc.wait()` 를 호출 → 멈춘 자식 프로세스가 reap 되지 않아 무한 대기.
  2. 모델/라우터 HTTP 400(잘못된 모델 ID) 은 기다려도 회복되지 않는데 계속 대기.
  3. 비-AGY 엔진 경로는 stdout 실제 진행 시에도 StallGuard 하트비트(touch)를
     갱신하지 않아 900초 강제회수가 동작하지 않음.
  4. !reset 핫리로드 후 대기열 락 전역이 유실되면 dispatch 가 NameError 로 죽음.
"""

import asyncio
import os
import sys
import tempfile
import time
import unittest

os.environ["UNITTEST_RUNNING"] = "1"
os.environ["OPENCODE_BWRAP"] = "0"
os.environ["OPENCODE_SANDBOX"] = "0"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import config  # noqa: E402
import engine_manager  # noqa: E402
import agent_runner  # noqa: E402
from engine_manager import (  # noqa: E402
    OpenCodeAdapter,
    _is_fatal_engine_error,
    _is_http_400_error,
    _terminate_process_tree,
)
from session_manager import ChannelSession  # noqa: E402


class TestFatalErrorDetection(unittest.TestCase):
    def test_invalid_model_is_fatal_anywhere(self):
        for text in (
            "not a valid model ID",
            "Invalid model: opencode-go/nope",
            "model_not_found",
            "unsupported model",
        ):
            self.assertTrue(_is_fatal_engine_error(text), text)

    def test_http_400_is_only_fatal_in_structured_contexts(self):
        # 구조화된 에러 이벤트/stderr 전용 판정
        for text in ("HTTP 400 Bad Request", "status code 400", "400: bad request", "ERR_400"):
            self.assertTrue(_is_http_400_error(text), text)
        # 일반 서술문에는 'HTTP 400' 이 있어도 strong-fatal 로 보면 안 된다(오탐 방지)
        self.assertFalse(_is_fatal_engine_error("we returned HTTP 400 from the API"))
        self.assertFalse(_is_fatal_engine_error("documenting the HTTP 400 response"))

    def test_transient_and_normal_text_are_not_fatal(self):
        for text in (
            "HTTP 429 Too Many Requests, retrying...",
            "tool_use completed",
            '{"type":"text","part":{"text":"hello world"}}',
            "session not found",
            "",
        ):
            self.assertFalse(_is_fatal_engine_error(text), text)
            self.assertFalse(_is_http_400_error(text), text)


class _NeverEndingProc:
    """kill 을 무시하고 wait() 가 영원히 끝나지 않는(멈춘) 프로세스 더미."""

    pid = None

    def kill(self):
        pass

    def terminate(self):
        pass

    async def wait(self):
        await asyncio.sleep(3600)


class _TerminateProcessTreeTest(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_wait_does_not_hang_on_frozen_process(self):
        """핵심: wait() 가 영원히 안 끝나도 헬퍼는 유한 시간 내에 반드시 반환한다."""
        proc = _NeverEndingProc()
        started = time.time()
        await _terminate_process_tree(proc, bounded_wait=0.3)
        elapsed = time.time() - started
        self.assertLess(elapsed, 2.0, "멈춘 프로세스에서 무한 대기하면 안 된다")

    async def test_real_process_is_reclaimed(self):
        proc = await asyncio.create_subprocess_exec(
            "sleep", "60",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        self.assertIsNone(proc.returncode)
        await _terminate_process_tree(proc, bounded_wait=3.0)
        self.assertIsNotNone(proc.returncode, "실제 프로세스가 회수되어야 한다")
        self.assertNotEqual(proc.returncode, 0)


class _HangingAdapter:
    engine_id = "opencode"
    name = "Hanging"
    icon = "⛔"

    async def execute(self, **kwargs):
        await asyncio.sleep(3600)


class TestHardCeiling(unittest.IsolatedAsyncioTestCase):
    async def test_hanging_adapter_is_reclaimed_by_hard_ceiling(self):
        """어댑터가 영원히 안 끝나도 agent_runner 가 상한에서 강제 회수한다."""
        session = ChannelSession(channel_id=9001, workspace=tempfile.gettempdir())
        orig_grace = agent_runner._ADAPTER_CEILING_GRACE_SECONDS
        orig_timeout = getattr(config, "NON_AGY_ENGINE_TIMEOUT", 3600.0)
        agent_runner._ADAPTER_CEILING_GRACE_SECONDS = 0.2
        config.NON_AGY_ENGINE_TIMEOUT = 0.1
        try:
            started = time.time()
            code, resp, errors = await agent_runner.AgentRunner._run_adapter_with_hard_ceiling(
                _HangingAdapter(),
                session=session,
                prompt="x",
                on_tool_update=None,
                model="opencode-go/deepseek-v4.1-flash",
                role="coder",
            )
            elapsed = time.time() - started
        finally:
            agent_runner._ADAPTER_CEILING_GRACE_SECONDS = orig_grace
            config.NON_AGY_ENGINE_TIMEOUT = orig_timeout

        self.assertEqual(code, -2, "하드 상한 초과 시 -2 를 반환해야 한다")
        self.assertIn("하드 타임아웃", resp)
        self.assertIsNone(session.current_process, "멈춘 프로세스 참조가 정리되어야 한다")
        self.assertLess(elapsed, 3.0, "상한을 넘겨 무한 대기하면 안 된다")

    async def test_fast_adapter_result_passes_through(self):
        class _Fast:
            engine_id = "hermes"
            name = "Fast"
            icon = "⚡"

            async def execute(self, **kwargs):
                return 0, "ok-body", []

        session = ChannelSession(channel_id=9002, workspace=tempfile.gettempdir())
        code, resp, errors = await agent_runner.AgentRunner._run_adapter_with_hard_ceiling(
            _Fast(), session=session, prompt="x", on_tool_update=None, model="m", role="coder",
        )
        self.assertEqual((code, resp), (0, "ok-body"))


class _EchoAdapter(OpenCodeAdapter):
    """opencode CLI 대신 고정 stdout 이벤트를 내뿜는 테스트용 어댑터."""

    def __init__(self, payload_line, **kwargs):
        super().__init__(**kwargs)
        self._payload_line = payload_line

    def get_binary_path(self):  # type: ignore[override]
        return "/bin/echo"

    def build_command(self, prompt, workspace, model=None, session_id=None, **kwargs):  # type: ignore[override]
        return ["/bin/echo", self._payload_line]


class _SequenceAdapter(OpenCodeAdapter):
    """호출 순서대로 서로 다른 stdout 이벤트를 내뿜는 테스트용 어댑터."""

    def __init__(self, payload_lines, **kwargs):
        super().__init__(**kwargs)
        self._payload_lines = list(payload_lines)
        self.calls = 0

    def get_binary_path(self):  # type: ignore[override]
        return "/bin/echo"

    def build_command(self, prompt, workspace, model=None, session_id=None, **kwargs):  # type: ignore[override]
        idx = min(self.calls, len(self._payload_lines) - 1)
        self.calls += 1
        return ["/bin/echo", self._payload_lines[idx]]


class TestOpenCodeAdapterFreezeHardening(unittest.IsolatedAsyncioTestCase):
    def _session(self):
        return ChannelSession(channel_id=9100, workspace=tempfile.gettempdir())

    async def test_real_stdout_updates_stall_guard_heartbeat(self):
        """엔진이 실제 출력을 내면 StallGuard 하트비트(touch)가 갱신되어야 한다."""
        payload = '{"type":"text","part":{"text":"hello"}}'
        adapter = _EchoAdapter(payload)
        session = self._session()
        session.last_progress_at = 0.0  # 아주 오래된 하트비트
        code, text, errors = await adapter.execute(
            session=session, prompt="x", on_tool_update=None,
            model="opencode-go/deepseek-v4.1-flash", timeout=10.0,
        )
        self.assertEqual(code, 0)
        self.assertIn("hello", text)
        self.assertGreater(session.last_progress_at, 0.0, "stdout 진행 시 하트비트가 갱신되어야 한다")
        self.assertLess(time.time() - session.last_progress_at, 5.0)

    async def test_router_400_fails_fast(self):
        """HTTP 400(잘못된 모델)은 대기하지 않고 즉시 회수 후 -2 로 반환한다."""
        payload = '{"type":"error","error":{"message":"HTTP 400: not a valid model ID"}}'
        adapter = _EchoAdapter(payload)
        session = self._session()
        started = time.time()
        code, text, errors = await adapter.execute(
            session=session, prompt="x", on_tool_update=None,
            model="opencode-go/does-not-exist", timeout=3600.0,
        )
        elapsed = time.time() - started
        self.assertEqual(code, -2, "치명적 모델/라우터 오류는 -2 로 즉시 실패해야 한다")
        self.assertLess(elapsed, 15.0, "400 오류에서 3600초를 기다리면 안 된다")
        self.assertTrue(any("400" in e or "치명적" in e for e in errors), errors)

    async def test_recoverable_provider_400_retries_with_fresh_session(self):
        """누적 컨텍스트성 provider 400 은 새 세션으로 1회 자가치유 재시도한다."""
        adapter = _SequenceAdapter([
            '{"type":"error","error":{"message":"Provider request failed with HTTP 400"}}',
            '{"type":"text","part":{"text":"recovered"}}',
        ])
        session = self._session()
        # 세션 ID 가 저장돼 있어야 '기존 세션 재사용' 경로를 탄다.
        session.engine_conversations = {"opencode:conversation_id": "ses_old_context"}
        code, text, errors = await adapter.execute(
            session=session, prompt="x", on_tool_update=None,
            model="opencode-go/deepseek-v4.1-flash", timeout=30.0,
        )
        self.assertEqual(adapter.calls, 2, "새 세션으로 정확히 1회 재시도해야 한다")
        self.assertEqual(code, 0, "자가치유 후에는 정상 종료(0)여야 한다")
        self.assertIn("recovered", text)

    async def test_privacy_training_400_does_not_retry(self):
        """'학습 데이터 허용' 프라이버시 오류는 재시도로 회복되지 않으므로 재시도하지 않는다."""
        payload = ('{"type":"error","error":{"message":"Upstream request failed: '
                   'This Go model trains on request data. Allow paid endpoints that train '
                   'on request data in your workspace\'s Privacy settings to use it."}}')
        adapter = _SequenceAdapter([payload])
        session = self._session()
        session.engine_conversations = {"opencode:conversation_id": "ses_old"}
        code, text, errors = await adapter.execute(
            session=session, prompt="x", on_tool_update=None,
            model="opencode-go/some-training-model", timeout=30.0,
        )
        self.assertEqual(adapter.calls, 1, "프라이버시 오류는 재시도하면 안 된다")
        self.assertEqual(code, -2)
        self.assertTrue(any("프라이버시" in e for e in errors), errors)


class TestBotQueueLockHotReloadSafety(unittest.IsolatedAsyncioTestCase):
    async def test_missing_global_falls_back_to_env_default(self):
        """핫리로드로 전역이 유실돼도 NameError 없이 환경변수 기본값을 쓴다."""
        import bot

        had = hasattr(bot, "QUEUE_LOCK_TIMEOUT_SECONDS")
        saved = getattr(bot, "QUEUE_LOCK_TIMEOUT_SECONDS", None)
        try:
            if had:
                del bot.QUEUE_LOCK_TIMEOUT_SECONDS
            value = bot._queue_lock_timeout_seconds()
            self.assertGreater(value, 0.0)
        finally:
            if had:
                bot.QUEUE_LOCK_TIMEOUT_SECONDS = saved


if __name__ == "__main__":
    unittest.main()
