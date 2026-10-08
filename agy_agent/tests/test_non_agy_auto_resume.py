#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[♻️ 라우터 오류 자동 재개] 비-AGY 엔진 회귀 테스트
=====================================================

사용자 시나리오:
  느린 무료 모델(opencode space-bunny 등)로 작업 중 라우터가 일시적 오류
  (HTTP 400 / 서비스 정체)를 반환하면 파이프라인이 그대로 중단됐다.
  agent_runner 는 이때 같은 엔진/모델로 **세션을 새로 열어 즉시 재개**한다
  (`_NON_AGY_RESUME_ATTEMPTS`, 기본 2). 이 테스트가 그 계약을 고정한다.

  회복 불가 신호(프라이버시 학습 미동의·잘못된 모델 ID·사용자 중단)는
  재시도해도 소용없으므로 재개하지 않는다.
"""

import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"
os.environ["OPENCODE_BWRAP"] = "0"
os.environ["OPENCODE_SANDBOX"] = "0"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import agent_runner  # noqa: E402
import config  # noqa: E402
import engine_manager  # noqa: E402
from session_manager import ChannelSession  # noqa: E402

STORE_KEY = "opencode:conversation_id"


class TestNonAgyAutoResume(unittest.IsolatedAsyncioTestCase):
    def _fake_adapter(self):
        return types.SimpleNamespace(
            engine_id="opencode",
            name="Fake OpenCode",
            icon="⚡",
            injects_autonomy_directive=True,
            is_installed=lambda: True,
            auth_info="n/a",
            install_command="n/a",
        )

    def _session(self):
        session = ChannelSession(
            channel_id=9200,
            workspace=tempfile.gettempdir(),
            engine_model="opencode-go/space-bunny-free",
        )
        session.role = "coder"
        session.engine = "opencode"
        session.engine_conversations = {STORE_KEY: "ses_old_context"}
        return session

    async def _run(self, session, outcome_sequence):
        calls = {"n": 0}

        async def _fake_run(adapter, **kwargs):
            idx = min(calls["n"], len(outcome_sequence) - 1)
            calls["n"] += 1
            return outcome_sequence[idx]

        fake_ceiling = AsyncMock(side_effect=_fake_run)
        with patch.object(engine_manager.EngineManager, "get_adapter", return_value=self._fake_adapter()), \
             patch.object(agent_runner.AgentRunner, "_run_adapter_with_hard_ceiling", fake_ceiling):
            resp = await agent_runner.AgentRunner._run_prompt_impl(
                session=session,
                prompt="기능 구현해줘",
                model="opencode-go/space-bunny-free",
                engine="opencode",
                role="coder",
            )
        return resp, calls["n"]

    async def test_transient_router_error_resumes_with_fresh_session(self):
        """일시적 provider 400 은 새 세션으로 1회 재개해 작업을 이어간다."""
        session = self._session()
        resp, n = await self._run(session, [
            (-2, "", ["Provider request failed with HTTP 400"]),
            (0, "recovered-body", []),
        ])
        self.assertEqual(n, 2, "일시적 라우터 오류는 새 세션으로 1회 재개해야 한다")
        self.assertEqual(resp, "recovered-body")
        self.assertNotIn(STORE_KEY, session.engine_conversations,
                         "재개 시 오염된 세션 ID 를 비워 새 세션으로 시작해야 한다")

    async def test_privacy_error_is_not_retried(self):
        """프라이버시 학습 미동의 오류는 재시도로 회복되지 않으므로 재개하지 않는다."""
        session = self._session()
        resp, n = await self._run(session, [
            (-2, "", ["This Go model trains on request data. Allow ... Privacy settings"]),
            (0, "should-not-happen", []),
        ])
        self.assertEqual(n, 1, "프라이버시 오류는 재개하면 안 된다")
        self.assertIn("⚠️", resp)


class TestRoleTimeoutDisabled(unittest.IsolatedAsyncioTestCase):
    async def test_zero_timeout_leaves_no_hard_ceiling(self):
        """[⏱️ coder/qa/auditor 타임아웃 해제] NON_AGY_ENGINE_TIMEOUT=0 이면
        느린 정상 생성을 상한으로 끊지 않고 끝까지 기다린다."""
        class _SlowAdapter:
            engine_id = "opencode"
            name = "Slow"
            icon = "⚡"
            injects_autonomy_directive = True

            async def execute(self, **kwargs):
                await asyncio.sleep(0.6)
                return 0, "done-late", []

        session = ChannelSession(channel_id=9201, workspace=tempfile.gettempdir())
        original = getattr(config, "NON_AGY_ENGINE_TIMEOUT", 0.0)
        config.NON_AGY_ENGINE_TIMEOUT = 0  # 무제한
        try:
            code, resp, errors = await agent_runner.AgentRunner._run_adapter_with_hard_ceiling(
                _SlowAdapter(), session=session, prompt="x",
                on_tool_update=None, model="opencode-go/space-bunny-free", role="coder",
            )
        finally:
            config.NON_AGY_ENGINE_TIMEOUT = original
        self.assertEqual((code, resp), (0, "done-late"),
                         "타임아웃 0(무제한)에서는 정상적으로 완료되어야 한다")


if __name__ == "__main__":
    unittest.main()
