#!/usr/bin/env python3
"""
Regression tests for the ZERO-CLARIFICATION / autonomous-progress mandate.

증상: 비대면(Discord) 운영 환경에서 Coder(예: opencode-go/deepseek-v4.1-flash)가
      "먼저 작업 공간 구조를 파악하겠습니다. 두 가지만 여쭙겠습니다." 처럼
      사용자 의사를 되묻고 턴을 종료하여 파이프라인이 멈춤.

기대: 모든 엔진(AGY 네이티브 / OpenCode / Hermes·OpenRouter)의 프롬프트에
      '무질문 자율 진행' 헌법이 강제 주입되어, 되묻지 않고 합리적 가정을 세워
      같은 턴에서 끝까지 자율 완결한다.
"""

import os
import sys
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)


class TestAutonomyNoQuestionMandate(unittest.TestCase):
    def test_agent_runner_header_and_footer_contain_mandate(self):
        from agent_runner import TOOL_SAFETY_HEADER, TOOL_SAFETY_FOOTER

        self.assertIn("ZERO-CLARIFICATION", TOOL_SAFETY_HEADER)
        self.assertIn("무질문 자율 진행", TOOL_SAFETY_HEADER)
        self.assertIn("ask_question", TOOL_SAFETY_HEADER)
        self.assertIn("ZERO-CLARIFICATION", TOOL_SAFETY_FOOTER)

    def test_engine_manager_directive_defined(self):
        import engine_manager

        directive = engine_manager.AUTONOMY_NO_QUESTION_DIRECTIVE
        self.assertIn("무질문 자율 진행", directive)
        self.assertIn("ask_question", directive)

    def test_opencode_prompt_leads_with_mandate(self):
        import engine_manager

        adapter = engine_manager.EngineManager.get_adapter("opencode")
        self.assertIsNotNone(adapter)

        cmd = adapter.build_command(
            "샘플 사용자 요청",
            "/fake/workspace",
            model="opencode-go/deepseek-v4.1-flash",
        )
        self.assertIn("run", cmd)
        prompt = cmd[cmd.index("run") + 1]
        # 지시문이 프롬프트 최상단(사용자에게 보이던 시작 지점)에 위치해야 한다.
        self.assertTrue(prompt.startswith(engine_manager.AUTONOMY_NO_QUESTION_DIRECTIVE))
        self.assertIn("작업 디렉토리(절대경로)", prompt)
        self.assertIn("샘플 사용자 요청", prompt)

    def test_hermes_prompt_contains_mandate(self):
        import engine_manager

        adapter = engine_manager.EngineManager.get_adapter("hermes")
        cmd = adapter.build_command("샘플 사용자 요청", "/fake/workspace")
        self.assertTrue(
            any(
                engine_manager.AUTONOMY_NO_QUESTION_DIRECTIVE in arg
                for arg in cmd
            ),
            "Hermes 프롬프트에 무질문 자율 진행 지시문이 포함되어야 합니다.",
        )

    def test_adapter_injection_flags(self):
        from engine_manager import EngineManager

        for engine_id in ("opencode", "hermes", "aider", "openrouter"):
            adapter = EngineManager.get_adapter(engine_id)
            self.assertTrue(
                getattr(adapter, "injects_autonomy_directive", False),
                f"{engine_id} 어댑터는 자체적으로 지시문을 주입해야 합니다.",
            )
        for engine_id in ("codex", "claude", "copilot"):
            adapter = EngineManager.get_adapter(engine_id)
            self.assertFalse(
                getattr(adapter, "injects_autonomy_directive", False),
                f"{engine_id} 어댑터는 디스패치 계층 주입을 사용해야 합니다.",
            )


class TestDispatchLevelInjection(unittest.IsolatedAsyncioTestCase):
    async def test_non_injecting_adapter_gets_directive_at_dispatch(self):
        from agent_runner import AgentRunner
        from engine_manager import AUTONOMY_NO_QUESTION_DIRECTIVE

        captured = {}

        class FakeAdapter:
            engine_id = "codex"
            injects_autonomy_directive = False

            async def execute(
                self, session=None, prompt=None, on_tool_update=None,
                model=None, role=None, conversation_id_attr=None, timeout=None,
            ):
                captured["prompt"] = prompt
                return (0, "ok", [])

        code, resp, errs = await AgentRunner._run_adapter_with_hard_ceiling(
            FakeAdapter(),
            session=None,
            prompt="원 요청",
            on_tool_update=None,
            model=None,
            role="coder",
        )
        self.assertEqual(code, 0)
        self.assertTrue(captured["prompt"].startswith(AUTONOMY_NO_QUESTION_DIRECTIVE))
        self.assertIn("원 요청", captured["prompt"])

    async def test_injecting_adapter_is_not_double_wrapped(self):
        from agent_runner import AgentRunner

        captured = {}

        class FakeAdapter:
            engine_id = "opencode"
            injects_autonomy_directive = True

            async def execute(
                self, session=None, prompt=None, on_tool_update=None,
                model=None, role=None, conversation_id_attr=None, timeout=None,
            ):
                captured["prompt"] = prompt
                return (0, "ok", [])

        await AgentRunner._run_adapter_with_hard_ceiling(
            FakeAdapter(),
            session=None,
            prompt="원 요청",
            on_tool_update=None,
            model=None,
            role="coder",
        )
        # 자체 주입 어댑터는 디스패치 계층에서 중복 래핑하지 않는다.
        self.assertEqual(captured["prompt"], "원 요청")


if __name__ == "__main__":
    unittest.main()
