#!/usr/bin/env python3
"""
🧭 `!model` SSOT 라우팅 정합성 회귀 테스트
=========================================

배경(사용자 보고):
    "!model 로 오픈코드(OpenCode) 봇으로 세팅해뒀는데 실제로는 오픈라우터
     (Hermes/OpenRouter) 봇이 동작했다."

원인:
    1. `is_model_compatible_with_engine("opencode", ...)` 가 모델에 '/' 만 있으면
       호환으로 오인 → `upstage/solar-pro4`, `deepseek/deepseek-chat` 같은
       Hermes 전용 slug 가 OpenCode 엔진에 그대로 저장됨 → 런타임 OpenRouter 분기로 샘.
    2. `run_qa_turn` / `run_auditor_turn` / `run_qa2_turn` 이 채널 세션의 오래된
       `qa_engine`(=agy) / `qa2_engine`(=openrouter) / `auditor_engine`(=agy) 값을
       `!model`(GlobalModelManager) 보다 우선 사용 → 화면 표시와 실제 실행이 불일치.

본 테스트는 위 두 결함이 재발하지 않음을 고정한다.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from model_manager import GlobalModelManager  # noqa: E402
from session_manager import ChannelSession  # noqa: E402
from trio_agent_runner import TrioAgentRunner  # noqa: E402


HERMES_SLUGS = ["upstage/solar-pro4", "deepseek/deepseek-chat", "openai/gpt-oss-120b", "qwen/qwen3-235b-a22b-2507"]
OPENCODE_MODELS = ["opencode-go/deepseek-v4.1-flash", "opencode-go/mimo-v2.6-flash"]


class TestOpenCodeEngineCompatibility(unittest.TestCase):
    """OpenCode 엔진은 opencode-go/* · opencode/* 모델만 실제로 실행 가능하다."""

    def test_hermes_slugs_are_not_opencode_compatible(self):
        for slug in HERMES_SLUGS:
            self.assertFalse(
                GlobalModelManager.is_model_compatible_with_engine("opencode", slug),
                msg=f"Hermes slug '{slug}' 가 OpenCode 호환으로 오인됨",
            )

    def test_opencode_models_are_opencode_compatible(self):
        for mid in OPENCODE_MODELS:
            self.assertTrue(GlobalModelManager.is_model_compatible_with_engine("opencode", mid))

    def test_switching_engine_to_opencode_replaces_hermes_model(self):
        orig_roles = GlobalModelManager._roles
        orig_init = GlobalModelManager._initialized
        try:
            GlobalModelManager._initialized = True
            GlobalModelManager._roles = {
                "coder": {"engine": "hermes", "model": "upstage/solar-pro4", "name": "Coder"},
            }
            with patch.object(GlobalModelManager, "save", return_value=None):
                ok = GlobalModelManager.set_role_engine("coder", "opencode")
            self.assertTrue(ok)
            cfg = GlobalModelManager._roles["coder"]
            self.assertEqual(cfg["engine"], "opencode")
            self.assertTrue(cfg["model"].startswith(("opencode-go/", "opencode/")))
        finally:
            GlobalModelManager._roles = orig_roles
            GlobalModelManager._initialized = orig_init

    def test_selecting_hermes_model_on_opencode_harmonizes_engine_back(self):
        orig_roles = GlobalModelManager._roles
        orig_init = GlobalModelManager._initialized
        try:
            GlobalModelManager._initialized = True
            GlobalModelManager._roles = {
                "coder": {"engine": "opencode", "model": "opencode-go/mimo-v2.6-flash", "name": "Coder"},
            }
            with patch.object(GlobalModelManager, "save", return_value=None):
                GlobalModelManager.set_role_model("coder", "upstage/solar-pro4")
            cfg = GlobalModelManager._roles["coder"]
            # OpenCode 는 Solar 를 실행할 수 없으므로 모델 소속 엔진(Hermes)으로 정합화되어야 한다.
            self.assertEqual(cfg["engine"], "hermes")
            self.assertEqual(cfg["model"], "upstage/solar-pro4")
        finally:
            GlobalModelManager._roles = orig_roles
            GlobalModelManager._initialized = orig_init


class TestRuntimeEnginePrefersGlobalModelSSOT(unittest.TestCase):
    """런타임 역할 엔진은 stale 세션값이 아니라 `!model` 전역 설정을 우선해야 한다."""

    def setUp(self):
        self.orig_roles = GlobalModelManager._roles
        self.orig_init = GlobalModelManager._initialized

    def tearDown(self):
        GlobalModelManager._roles = self.orig_roles
        GlobalModelManager._initialized = self.orig_init

    def _force_global(self, role, engine, model):
        GlobalModelManager._initialized = True
        GlobalModelManager._roles = {
            role: {"engine": engine, "model": model, "name": role},
        }

    def test_run_qa_turn_uses_global_engine_not_stale_session(self):
        self._force_global("qa1", "opencode", "opencode-go/mimo-v2.6-flash")
        # 세션에는 오래된 값(agy)이 남아 있다.
        session = ChannelSession(channel_id=1, workspace="/tmp", qa_engine="agy")

        async def _run():
            with patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as m:
                m.return_value = "[STATUS: APPROVED]"
                await TrioAgentRunner.run_qa_turn(session, "review")
                kwargs = m.call_args.kwargs
                self.assertEqual(kwargs.get("model"), "opencode-go/mimo-v2.6-flash")

        asyncio.run(_run())

    def test_run_auditor_turn_engine_prefers_global(self):
        self._force_global("auditor", "opencode", "opencode-go/deepseek-v4.1-flash")
        session = ChannelSession(channel_id=2, workspace="/tmp", auditor_engine="agy")

        seen = {}

        async def _fake_run_prompt(session=None, prompt="", on_tool_update=None, model=None,
                                   conversation_id_attr=None, engine=None, role=None):
            # run_auditor_turn 은 engine 을 kwarg 가 아니라 프록시 세션에 실어 보낸다.
            seen["engine"] = engine or getattr(session, "engine", None)
            seen["model"] = model
            return "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]"

        async def _run():
            with patch("trio_agent_runner.AgentRunner.run_prompt", side_effect=_fake_run_prompt):
                await TrioAgentRunner.run_auditor_turn(session, "audit")

        asyncio.run(_run())
        self.assertEqual(seen.get("engine"), "opencode")
        self.assertEqual(seen.get("model"), "opencode-go/deepseek-v4.1-flash")

    def test_run_qa2_turn_engine_prefers_global(self):
        self._force_global("qa2", "opencode", "opencode-go/deepseek-v4-flash")
        session = ChannelSession(channel_id=3, workspace="/tmp", qa2_engine="openrouter")

        async def _run():
            with patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as m:
                m.return_value = "[STATUS: APPROVED]"
                await TrioAgentRunner.run_qa2_turn(session, "cross-check")
                kwargs = m.call_args.kwargs
                self.assertEqual(kwargs.get("model"), "opencode-go/deepseek-v4-flash")

        asyncio.run(_run())


class TestAgentRunnerModelBasedDispatch(unittest.TestCase):
    """모델 소속 엔진 판정이 하드코딩 휴리스틱과 일치한다 (OpenCode/Hermes)."""

    def test_model_owner_engine_classification(self):
        self.assertEqual(GlobalModelManager.model_owner_engine("opencode-go/mimo-v2.6-flash"), "opencode")
        self.assertEqual(GlobalModelManager.model_owner_engine("upstage/solar-pro4"), "hermes")
        self.assertEqual(GlobalModelManager.model_owner_engine("gemini-3.8-flash-high"), "agy")


class _FakeSession:
    def __init__(self, **kw):
        self.engine = kw.get("engine", "agy")
        self.qa_engine = kw.get("qa_engine", "agy")
        self.qa2_engine = kw.get("qa2_engine", "openrouter")
        self.auditor_engine = kw.get("auditor_engine", "agy")


class _FakeSessionManager:
    def __init__(self):
        self.sessions = {1: _FakeSession()}
        self.saved = False

    def get_all_sessions(self):
        return self.sessions

    def save_state(self):
        self.saved = True


class TestSessionHealingFromGlobalModel(unittest.TestCase):
    """`!model` 변경/기동 시 채널 세션의 stale 엔진 값이 SSOT 로 정합화되어야 한다."""

    def setUp(self):
        self.orig_roles = GlobalModelManager._roles
        self.orig_init = GlobalModelManager._initialized

    def tearDown(self):
        GlobalModelManager._roles = self.orig_roles
        GlobalModelManager._initialized = self.orig_init

    def test_heals_stale_session_engines(self):
        import bot

        GlobalModelManager._initialized = True
        GlobalModelManager._roles = {
            "coder": {"engine": "opencode", "model": "opencode-go/deepseek-v4.1-flash", "name": "coder"},
            "qa1": {"engine": "opencode", "model": "opencode-go/mimo-v2.6-flash", "name": "qa1"},
            "qa2": {"engine": "opencode", "model": "opencode-go/deepseek-v4-flash", "name": "qa2"},
            "auditor": {"engine": "opencode", "model": "opencode-go/deepseek-v4.1-flash", "name": "auditor"},
        }
        fake_mgr = _FakeSessionManager()
        with patch.object(bot, "session_manager", fake_mgr):
            healed = bot.sync_sessions_with_global_model()
        self.assertEqual(healed, 1)
        sess = fake_mgr.sessions[1]
        self.assertEqual(sess.engine, "opencode")
        self.assertEqual(sess.qa_engine, "opencode")
        self.assertEqual(sess.qa2_engine, "opencode")
        self.assertEqual(sess.auditor_engine, "opencode")


if __name__ == "__main__":
    unittest.main()

