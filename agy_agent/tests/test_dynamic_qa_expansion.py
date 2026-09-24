import os
import sys
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from session_manager import ChannelSession, SessionManager
from engine_manager import EngineManager
from trio_agent_runner import TrioAgentRunner

# Realistic QA mock outputs that pass _validate_qa_inspection_depth quality gate
# (>250 chars, contains file references with extensions, code evidence, improvement suggestions)
_REALISTIC_QA1_PASS = (
    "[STATUS: APPROVED]\n### QA1 코드 검증 결과\n"
    "`handler.py` 파일의 핵심 로직을 검증하였습니다. `class RequestHandler`의 `process()` 메서드(라인 30~55)가 정상이며, "
    "`utils.py` 헬퍼 함수들의 예외 처리도 올바르게 동작합니다.\n\n"
    "**검증 항목:**\n- 라인 30~55: 핸들러 로직 정상 확인 완료\n- 라인 78: 에러 핸들링 완비\n\n"
    "**개선 제안:** 입력 유효성 검사를 별도 미들웨어 레이어로 분리하면 유지보수성이 향상될 것입니다."
)
_REALISTIC_QA2_PASS = (
    "[STATUS: APPROVED]\nQA2 verified edge cases across `handler.py` and `utils.py`. "
    "라인 45의 null 체크와 라인 92의 타임아웃 방어 로직이 안정적입니다. "
    "`class Config` 설정 값 범위 검증 완료. 개선 제안: 리트라이 로직 추가 권고."
)
_REALISTIC_QA3_PASS = (
    "[STATUS: APPROVED]\nQA3 verified architecture of `handler.py`. "
    "`class RequestHandler` SRP 준수 확인 완료. 라인 100~120 모듈 결합도 양호. "
    "`utils.py` 유틸 함수 순수 함수화 확인. 개선 제안: 인터페이스 분리를 통한 확장성 확보 권고."
)
_REALISTIC_QA4_PASS = (
    "[STATUS: APPROVED]\nQA4 verified resilience of `handler.py`. "
    "Race condition 방어 (라인 60~75) mutex 적용 확인. `config.py` 환경 변수 fallback 처리 검증 완료. "
    "개선 제안: 서킷 브레이커 패턴 도입으로 외부 서비스 장애 격리 권고."
)


class TestDynamicQARegistry(unittest.TestCase):
    """Unit tests for config.get_extended_qa_configs and dynamic QA registry."""

    def test_default_config_discovers_qa2(self):
        """By default, QA2 is configured with solar-pro4."""
        with patch.dict(os.environ, {"QA2_MODEL": "upstage/solar-pro4", "QA3_MODEL": ""}, clear=False):
            configs = config.get_extended_qa_configs()
            qa_indices = [c["index"] for c in configs]
            self.assertIn(2, qa_indices)
            qa2 = next(c for c in configs if c["index"] == 2)
            self.assertEqual(qa2["model"], "upstage/solar-pro4")
            self.assertIn(qa2["engine"], ("openrouter", "hermes"))

    def test_multi_qa_discovery_plug_and_play(self):
        """Setting QA2, QA3, QA4 dynamically creates 3 QA configs."""
        env_override = {f"QA{i}_MODEL": "" for i in range(2, 11)}
        env_override.update({f"DISCORD_AGY_QA{i}_BOT_TOKEN": "" for i in range(2, 11)})
        env_override.update({f"DISCORD_QA{i}_BOT_TOKEN": "" for i in range(2, 11)})
        env_override.update({
            "QA2_MODEL": "upstage/solar-pro4",
            "QA3_MODEL": "deepseek/deepseek-chat",
            "QA4_MODEL": "qwen/qwen-2.5-coder-32b-instruct",
            "QA3_ENGINE": "openrouter",
            "DISCORD_QA3_BOT_TOKEN": "mock_qa3_token",
        })
        with patch.dict(os.environ, env_override, clear=False):
            configs = config.get_extended_qa_configs()
            self.assertEqual(len(configs), 3)
            self.assertEqual(configs[0]["index"], 2)
            self.assertEqual(configs[1]["index"], 3)
            self.assertEqual(configs[1]["model"], "deepseek/deepseek-chat")
            self.assertEqual(configs[1]["token"], "mock_qa3_token")
            self.assertEqual(configs[2]["index"], 4)
            self.assertEqual(configs[2]["model"], "qwen/qwen-2.5-coder-32b-instruct")
            self.assertEqual(configs[2]["token"], "")

    def test_qa_removal_graceful_handling(self):
        """When QA2 is empty and only QA3 is defined, it handles non-contiguous setups."""
        env_override = {f"QA{i}_MODEL": "" for i in range(2, 11)}
        env_override["QA3_MODEL"] = "deepseek/deepseek-chat"
        with patch.dict(os.environ, env_override, clear=False):
            configs = config.get_extended_qa_configs()
            self.assertEqual(len(configs), 1)
            self.assertEqual(configs[0]["index"], 3)
            self.assertEqual(configs[0]["model"], "deepseek/deepseek-chat")

    def test_zero_extended_qas_handled(self):
        """When all QA2..QA10 are empty strings, returns empty list without error."""
        env_override = {f"QA{i}_MODEL": "" for i in range(2, 11)}
        with patch.dict(os.environ, env_override, clear=False):
            configs = config.get_extended_qa_configs()
            self.assertEqual(configs, [])

    def test_token_priority_prefers_discord_qa_bot_token(self):
        """DISCORD_QA{i}_BOT_TOKEN has higher priority than legacy DISCORD_AGY_QA{i}_BOT_TOKEN."""
        env_override = {
            "QA3_MODEL": "deepseek/deepseek-chat",
            "DISCORD_QA3_BOT_TOKEN": "primary_qa3_token",
            "DISCORD_AGY_QA3_BOT_TOKEN": "legacy_qa3_token",
        }
        with patch.dict(os.environ, env_override, clear=False):
            configs = config.get_extended_qa_configs()
            qa3 = next(c for c in configs if c["index"] == 3)
            self.assertEqual(qa3["token"], "primary_qa3_token")

        # When primary is not set, falls back to legacy
        env_fallback = {
            "QA3_MODEL": "deepseek/deepseek-chat",
            "DISCORD_QA3_BOT_TOKEN": "",
            "DISCORD_AGY_QA3_BOT_TOKEN": "legacy_qa3_token",
        }
        with patch.dict(os.environ, env_fallback, clear=False):
            configs = config.get_extended_qa_configs()
            qa3 = next(c for c in configs if c["index"] == 3)
            self.assertEqual(qa3["token"], "legacy_qa3_token")

    def test_validate_openrouter_setup(self):
        """Validates API key formats, empty string, and valid key check."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            valid, msg = config.validate_openrouter_setup()
            self.assertFalse(valid)
            self.assertIn("설정되지 않았습니다", msg)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "dummy_short"}, clear=False):
            valid, msg = config.validate_openrouter_setup()
            self.assertFalse(valid)
            self.assertIn("유효하지 않습니다", msg)

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-0123456789abcdef"}, clear=False):
            valid, msg = config.validate_openrouter_setup()
            self.assertTrue(valid)
            self.assertIn("설정 완료", msg)


class TestEngineManagerModelAliases(unittest.TestCase):
    """Unit tests for model alias resolution across OpenRouter models."""

    def test_deepseek_alias_resolution(self):
        self.assertEqual(
            EngineManager.resolve_model_alias("deepseek"),
            "openrouter/deepseek/deepseek-chat"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("deepseek-chat"),
            "openrouter/deepseek/deepseek-chat"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("deepseek/deepseek-chat"),
            "openrouter/deepseek/deepseek-chat"
        )

    def test_qwen_alias_resolution(self):
        self.assertEqual(
            EngineManager.resolve_model_alias("qwen"),
            "openrouter/qwen/qwen-2.5-coder-32b-instruct"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("qwen/qwen-2.5-coder-32b-instruct"),
            "openrouter/qwen/qwen-2.5-coder-32b-instruct"
        )

    def test_solar_alias_resolution(self):
        self.assertEqual(
            EngineManager.resolve_model_alias("solar"),
            "openrouter/upstage/solar-pro4"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("upstage/solar-pro4"),
            "openrouter/upstage/solar-pro4"
        )

    def test_provider_prefix_auto_openrouter(self):
        self.assertEqual(
            EngineManager.resolve_model_alias("meta-llama/llama-3.3-70b-instruct"),
            "openrouter/meta-llama/llama-3.3-70b-instruct"
        )


class TestSessionManagerExtendedQA(unittest.TestCase):
    """Unit tests for ChannelSession conversation id management for multiple QAs."""

    def test_extended_qa_conv_id_tracking(self):
        session = ChannelSession(channel_id=123, workspace="/tmp")
        self.assertIsNone(session.get_extended_qa_conversation_id("qa2"))
        self.assertIsNone(session.get_extended_qa_conversation_id("qa3"))

        session.set_extended_qa_conversation_id("qa2", "conv_qa2_xyz")
        session.set_extended_qa_conversation_id("qa3", "conv_qa3_abc")

        self.assertEqual(session.qa2_conversation_id, "conv_qa2_xyz")
        self.assertEqual(session.get_extended_qa_conversation_id("qa2"), "conv_qa2_xyz")
        self.assertEqual(session.get_extended_qa_conversation_id("qa3"), "conv_qa3_abc")

        # Verify serialization
        data = session.to_dict()
        self.assertEqual(data["extended_qa_conversation_ids"].get("qa3"), "conv_qa3_abc")

        # Verify reset
        session.reset_conversation()
        self.assertIsNone(session.get_extended_qa_conversation_id("qa2"))
        self.assertIsNone(session.get_extended_qa_conversation_id("qa3"))

    def test_legacy_session_object_without_extended_qa_attribute_safety(self):
        """Simulate an in-memory session created before extended_qa_conversation_ids was introduced."""
        session = ChannelSession(channel_id=456, workspace="/tmp")
        # Explicitly delete the internal attribute to simulate an older in-memory instance
        if hasattr(session, "_extended_qa_conversation_ids"):
            delattr(session, "_extended_qa_conversation_ids")
        if hasattr(session, "qa2_conversation_id"):
            delattr(session, "qa2_conversation_id")

        # Must not raise AttributeError
        self.assertEqual(session.extended_qa_conversation_ids, {})
        self.assertIsNone(session.get_extended_qa_conversation_id("qa2"))
        self.assertIsNone(session.get_extended_qa_conversation_id("qa3"))

        # Setting must work cleanly
        session.set_extended_qa_conversation_id("qa2", "conv_legacy_qa2")
        self.assertEqual(session.get_extended_qa_conversation_id("qa2"), "conv_legacy_qa2")
        self.assertEqual(session.qa2_conversation_id, "conv_legacy_qa2")

        session.set_qa2_conversation_id("conv_legacy_qa2_updated")
        self.assertEqual(session.get_extended_qa_conversation_id("qa2"), "conv_legacy_qa2_updated")

        session.set_extended_qa_conversation_id("qa4", "conv_legacy_qa4")
        self.assertEqual(session.get_extended_qa_conversation_id("qa4"), "conv_legacy_qa4")

        # to_dict must not crash
        serialized = session.to_dict()
        self.assertEqual(serialized["extended_qa_conversation_ids"]["qa4"], "conv_legacy_qa4")

        # reset must not crash
        session.reset_conversation()
        self.assertEqual(session.extended_qa_conversation_ids, {})
        self.assertIsNone(session.get_extended_qa_conversation_id("qa2"))


class TestTrioAgentRunnerMultiQAPipeline(unittest.IsolatedAsyncioTestCase):
    """Integration and flow tests for TrioAgentRunner multi-QA pipeline execution."""

    def setUp(self):
        import tempfile
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()
        self.temp_dir = tempfile.mkdtemp(prefix="test_trio_multi_qa_")
        self.session = ChannelSession(
            channel_id=1539483630089273435,
            workspace=self.temp_dir,
            mode="pair_quad"  # !mode 2+ activates multi-QA
        )
        self.mock_channel = MagicMock()
        self.mock_channel.id = 1539483630089273435
        self.mock_channel.send = AsyncMock(return_value=AsyncMock())
        self.mock_channel.typing = MagicMock()
        self.mock_channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        self.mock_channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        self.mock_guild = MagicMock()
        self.mock_noti_ch = MagicMock()
        self.mock_noti_ch.send = AsyncMock()
        self.mock_guild.get_channel.return_value = self.mock_noti_ch
        self.mock_channel.guild = self.mock_guild

    def tearDown(self):
        import shutil
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()
        if hasattr(self, "temp_dir") and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_standard_mode_never_invokes_extended_qas(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """In !mode 2 (pair), extended QAs are never invoked even if configured in .env."""
        self.session.mode = "pair"  # Standard 3-role pipeline

        # Return coder -> qa1 approved -> auditor passed
        mock_run_prompt.side_effect = [
            "Coder implemented feature successfully.",
            "[STATUS: APPROVED]\n### QA 코드 검증 결과\n`main.py` 파일의 핵심 로직을 검증하였습니다. `class Handler`의 process() 메서드가 정상이며, `utils.py` 헬퍼 함수들의 예외 처리도 올바르게 동작합니다.\n\n**검증 항목:**\n- 라인 30~55: 핸들러 로직 정상 확인 완료\n- 라인 78: 에러 핸들링 완비\n\n**개선 제안:** 입력 유효성 검사를 별도 미들웨어 레이어로 분리하면 유지보수성이 높아질 것입니다.",
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAll user requirements fulfilled."
        ]

        with patch("config.get_extended_qa_configs", return_value=[
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-chat", "engine": "openrouter"},
        ]):
            res = await TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="Add new feature",
                main_channel=self.mock_channel,
                max_rounds=1
            )

        self.assertIn("최종 승인", res)
        # Exactly 3 calls: Coder, QA1, Auditor (Extended QAs untouched)
        self.assertEqual(mock_run_prompt.call_count, 3)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_multi_qa_full_pass_chain(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """In !mode 2+, QA1 -> QA2 -> QA3 -> Auditor executes in full sequence."""
        self.session.mode = "pair_quad"

        # Coder -> QA1 pass -> QA2 pass -> QA3 pass -> Auditor pass
        mock_run_prompt.side_effect = [
            "Coder implemented multi-platform feature.",
            "[STATUS: APPROVED]\n### QA1 코드 검증 결과\n`api_handler.py` 파일의 핵심 로직을 검증하였습니다. `class EnterpriseHandler`의 `process()` 메서드(라인 30~55)가 정상이며, `utils.py` 헬퍼 함수들의 예외 처리도 올바르게 동작합니다.\n\n**검증 항목:**\n- 라인 30~55: 핸들러 로직 정상 확인 완료\n- 라인 78: 에러 핸들링 완비\n\n**개선 제안:** 입력 유효성 검사를 별도 미들웨어 레이어로 분리하면 유지보수성이 높아질 것입니다.",
            "[STATUS: APPROVED]\nQA2 verified edge cases across `api_handler.py` and `utils.py`. 라인 45의 null 체크와 라인 92의 타임아웃 방어 로직이 안정적입니다. 개선 제안: 리트라이 로직 추가 권고.",
            "[STATUS: APPROVED]\nQA3 verified architecture of `api_handler.py`. `class EnterpriseHandler` SRP 준수 확인 완료. 라인 100~120 모듈 결합도 양호. 개선 제안: 인터페이스 분리를 통한 확장성 확보 권고.",
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor verified complete alignment."
        ]

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter", "token": ""},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-chat", "engine": "openrouter", "token": ""},
        ]

        with patch("config.get_extended_qa_configs", return_value=active_qas):
            res = await TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="Implement enterprise API",
                main_channel=self.mock_channel,
                max_rounds=1
            )

        self.assertIn("5중 에이전트 협업 개발 완료", res)
        self.assertEqual(mock_run_prompt.call_count, 5)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_multi_qa_rejection_at_qa3_loops_to_coder(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """If QA3 rejects, pipeline loops back to Coder with QA3 feedback, skips previously approved QAs (QA1, QA2), and re-evaluates only QA3."""
        self.session.mode = "pair_quad"  # !mode 2+

        # Round 1: Coder -> QA1 pass -> QA2 pass -> QA3 REJECT
        # Round 2: Coder fixes -> (QA1 & QA2 skipped via incremental re-review) -> QA3 pass -> Auditor pass
        mock_run_prompt.side_effect = [
            # Round 1
            "Coder initial commit.",
            _REALISTIC_QA1_PASS,
            _REALISTIC_QA2_PASS,
            "[STATUS: REJECTED]\nQA3 found missing concurrency defense in `handler.py` 라인 60~75. mutex 미적용으로 race condition 위험. 개선 제안: threading.Lock 적용 필수.",
            # Round 2
            "Coder added concurrency mutexes.",
            # QA1 and QA2 were approved in Round 1 -> Skipped with 0 redundant LLM calls!
            _REALISTIC_QA3_PASS,
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor fully satisfied."
        ]

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-chat", "engine": "openrouter"},
        ]

        with patch("config.get_extended_qa_configs", return_value=active_qas):
            res = await TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="Implement concurrency safe cache",
                main_channel=self.mock_channel,
                max_rounds=2
            )

        self.assertIn("5중 에이전트 협업 개발 완료", res)
        # 7 calls total: Round 1 (Coder, QA1, QA2, QA3=4) + Round 2 (Coder, QA3, Auditor=3, QA1/QA2 skipped!)
        self.assertEqual(mock_run_prompt.call_count, 7)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_multi_qa_with_no_bot_tokens_succeeds_without_crash(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """When extended QAs have no Discord bot tokens, they run seamlessly and log to main_channel."""
        self.session.mode = "pair_quad"

        mock_run_prompt.side_effect = [
            "Coder output.",
            _REALISTIC_QA1_PASS,
            _REALISTIC_QA2_PASS,
            _REALISTIC_QA3_PASS,
            _REALISTIC_QA4_PASS,
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor passed."
        ]

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter", "token": ""},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-chat", "engine": "openrouter", "token": ""},
            {"index": 4, "name": "QA4", "model": "meta-llama/llama-3.3-70b-instruct", "engine": "openrouter", "token": ""},
        ]

        with patch("config.get_extended_qa_configs", return_value=active_qas):
            res = await TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="Build system",
                main_channel=self.mock_channel,
                qa2_client=None,
                extended_qa_clients={},  # No bot clients at all
                max_rounds=1
            )

        self.assertIn("6중 에이전트 협업 개발 완료", res)
        self.assertEqual(mock_run_prompt.call_count, 6)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_multi_qa_timeout_triggers_rejection_and_coder_feedback(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """When an extended QA turn times out (TimeoutError), it is safely rejected and sent back to Coder."""
        self.session.mode = "pair_quad"

        # Round 1: Coder -> QA1 pass -> QA2 TIMEOUT (via patch)
        # Round 2: Coder fixes -> (QA1 skipped via incremental re-review) -> QA2 pass -> Auditor pass
        mock_run_prompt.side_effect = [
            # Round 1
            "Coder initial commit.",
            _REALISTIC_QA1_PASS,
            # Round 2
            "Coder second commit.",
            # QA1 was already approved in Round 1 -> Skipped!
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor passed."
        ]

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
        ]

        with patch.object(
            TrioAgentRunner,
            "run_extended_qa_turn",
            side_effect=[asyncio.TimeoutError(), _REALISTIC_QA2_PASS]
        ):
            with patch("config.get_extended_qa_configs", return_value=active_qas):
                res = await TrioAgentRunner.run_pipeline(
                    session=self.session,
                    prompt="Test with timeout",
                    main_channel=self.mock_channel,
                    max_rounds=2
                )

        self.assertIn("4중 에이전트 협업 개발 완료", res)
        self.assertEqual(mock_run_prompt.call_count, 4)


class TestOpenRouterAdapter(unittest.IsolatedAsyncioTestCase):
    """Unit tests for OpenRouterAdapter execution logic and dynamic QA roles."""

    def setUp(self):
        from engine_manager import OpenRouterAdapter
        self.adapter = OpenRouterAdapter()

    async def test_missing_api_key_returns_early_error(self):
        session = ChannelSession(channel_id=1, workspace="/tmp", role="qa2")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False):
            with patch("config.OPENROUTER_API_KEY", ""):
                code, out, errs = await self.adapter.execute(session, "prompt")
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertTrue(any("OPENROUTER_API_KEY가 설정되지 않았거나" in e for e in errs))

    async def test_dynamic_qa_role_triggers_direct_api(self):
        session = ChannelSession(channel_id=1, workspace="/tmp", role="qa3")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-v1-validkey1234567890"}, clear=False):
            with patch("config.OPENROUTER_API_KEY", "sk-or-v1-validkey1234567890"):
                with patch.object(self.adapter, "get_binary_path", return_value=None):
                    with patch.object(self.adapter, "execute_direct_api", new_callable=AsyncMock) as mock_direct:
                        mock_direct.return_value = (0, "Direct API Review Result", [])
                        code, out, errs = await self.adapter.execute(session, "Review prompt")
                        self.assertEqual(code, 0)
                        self.assertEqual(out, "Direct API Review Result")
                        mock_direct.assert_called_once()


class TestQACommand(unittest.IsolatedAsyncioTestCase):
    """Unit tests for !qa command in bot.py."""

    async def test_qa_command_embed_rendering(self):
        import bot
        mock_ctx = MagicMock()
        mock_ctx.channel.id = 1539483630089273435
        mock_ctx.send = AsyncMock()

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter", "token": ""},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-chat", "engine": "openrouter", "token": "mock_token"},
        ]

        with patch("config.get_extended_qa_configs", return_value=active_qas):
            with patch("config.validate_openrouter_setup", return_value=(True, "✅ OpenRouter API 키 설정 완료")):
                await bot.qa_command(mock_ctx)

        mock_ctx.send.assert_called_once()
        sent_embed = mock_ctx.send.call_args.kwargs.get("embed")
        self.assertIsNotNone(sent_embed)
        self.assertIn("AGY QA 리뷰어", sent_embed.title)
        field_names = [f.name for f in sent_embed.fields]
        self.assertTrue(any("qa1" in n.lower() for n in field_names))
        self.assertTrue(any("확장 QA" in n for n in field_names))
        self.assertTrue(any("OpenRouter" in n for n in field_names))


class TestStatusApprovalParsers(unittest.IsolatedAsyncioTestCase):
    """Unit tests for robust status tag parsing (preventing false rejections)."""

    def setUp(self):
        self.session = ChannelSession(
            channel_id=1539483630089273435,
            workspace="/tmp",
            mode="pair_quad"
        )
        self.mock_channel = MagicMock()
        self.mock_channel.id = 1539483630089273435
        self.mock_channel.send = AsyncMock(return_value=AsyncMock())
        self.mock_channel.typing = MagicMock()
        self.mock_channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        self.mock_channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        self.mock_guild = MagicMock()
        self.mock_noti_ch = MagicMock()
        self.mock_noti_ch.send = AsyncMock()
        self.mock_guild.get_channel.return_value = self.mock_noti_ch
        self.mock_channel.guild = self.mock_guild

    def test_solar_pro_report_with_in_text_rejected_mention_evaluates_as_approved(self):
        """The exact Solar Pro 4 report containing '[STATUS: REJECTED]' in the feedback loop description."""
        report = (
            "[STATUS: APPROVED]\n\n"
            "🕵️‍♂️ 시니어 QA2 독립 교차 감사 리포트\n"
            "1차 QA가 승인한 OpenRouter 기반 동적 다중 QA 확장 체계에 대해 재검증을 수행했습니다.\n\n"
            "#### 3. 런타임 결함 및 예외 방어\n"
            "- 반려 피드백 루프: 확장 QA 중 한 곳이라도 [STATUS: REJECTED] 시 해당 사유가 Coder에 전달되고, "
            "QA1부터 재검증하는 수렴 루프가 설계되어 있습니다.\n\n"
            "#### 5. 결론\n"
            "판정: [STATUS: APPROVED]\n"
            "추가 수정 없이 현재 구성으로 배포 가능합니다."
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(report))

    def test_explicit_rejection_at_start_evaluates_as_rejected(self):
        report = (
            "[STATUS: REJECTED]\n"
            "결함 발견: 동시성 락이 누락되었습니다."
        )
        self.assertFalse(TrioAgentRunner.is_qa_approved(report))

    def test_direct_patch_approval_evaluates_as_approved(self):
        report = (
            "[STATUS: APPROVED (QA Direct Patch)]\n"
            "단순 오타 수정 후 컴파일 검증 완료"
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(report))

    def test_final_verdict_rejection_overrides_accidental_approved_at_top(self):
        report = (
            "[STATUS: APPROVED]\n"
            "초기 검토 중...\n"
            "치명적 결함 발견.\n"
            "판정: [STATUS: REJECTED]"
        )
        self.assertFalse(TrioAgentRunner.is_qa_approved(report))

    def test_auditor_passed_with_in_text_failed_mention(self):
        report = (
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
            "이전 라운드의 [STATUS: AUDIT_FAILED] 사유가 완벽히 해소되었습니다.\n"
            "판정: [STATUS: AUDIT_PASSED]"
        )
        self.assertTrue(TrioAgentRunner.is_auditor_approved(report))

    def test_auditor_explicit_failed(self):
        report = (
            "[STATUS: AUDIT_FAILED] [VALUE: LOW] [ACTION: STOP]\n"
            "요구사항 불일치"
        )
        self.assertFalse(TrioAgentRunner.is_auditor_approved(report))

    def test_top_approved_with_sentence_containing_rejected_bullet_point_evaluates_as_approved(self):
        """Top approved with a bullet point in lines 2..4 discussing rejection defense."""
        report = (
            "[STATUS: APPROVED]\n\n"
            "검토 개요:\n"
            "- [STATUS: REJECTED] 방어 여부: 정상 작동 확인\n"
            "- 결함 없음\n"
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(report))

    def test_closing_verdict_with_trailing_explanation_evaluates_as_approved(self):
        """Closing verdict line that includes trailing sentences."""
        report = (
            "[STATUS: APPROVED]\n\n"
            "상세 검토 내역:\n"
            "1. 정상 작동\n"
            "판정: [STATUS: APPROVED] 모든 테스트가 정상적으로 통과되었습니다."
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(report))

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_full_pipeline_solar_pro_report_does_not_falsely_reject(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """Full pipeline integration: Solar Pro 4 report passes QA2 in round 1 without false rejection."""
        self.session.mode = "pair_quad"

        solar_pro_output = (
            "[STATUS: APPROVED]\n\n"
            "🕵️‍♂️ 시니어 QA2 독립 교차 감사 리포트\n"
            "- 반려 피드백 루프: 확장 QA 중 한 곳이라도 [STATUS: REJECTED] 시 해당 사유가 Coder에 전달됩니다.\n"
            "판정: [STATUS: APPROVED]"
        )

        mock_run_prompt.side_effect = [
            "Coder output",
            _REALISTIC_QA1_PASS,
            solar_pro_output,
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor approved."
        ]

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
        ]

        with patch("config.get_extended_qa_configs", return_value=active_qas):
            res = await TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="Verify pipeline without false rejection",
                main_channel=self.mock_channel,
                max_rounds=1
            )

        self.assertIn("4중 에이전트 협업 개발 완료", res)
        # Exactly 4 calls in Round 1: Coder -> QA1 -> QA2 -> Auditor
        self.assertEqual(mock_run_prompt.call_count, 4)


class TestModelAliasResolutionAndExtendedQAs(unittest.TestCase):
    """Unit tests for user-requested QA2..QA6 model resolution, natural language normalization, and hot-plugging."""

    def test_openrouter_slug_resolutions(self):
        """Standard OpenRouter provider slugs automatically map with openrouter/ prefix."""
        test_cases = [
            ("upstage/solar-pro4", "openrouter/upstage/solar-pro4"),
            ("deepseek/deepseek-v4-flash-0731", "openrouter/deepseek/deepseek-v4-flash-0731"),
            ("qwen/qwen3-235b-a22b-2507", "openrouter/qwen/qwen3-235b-a22b-2507"),
            ("meta-llama/llama-4-scout", "openrouter/meta-llama/llama-4-scout"),
            ("inclusionai/ling-3.0-flash-fin:free", "openrouter/inclusionai/ling-3.0-flash-fin:free"),
        ]
        for raw, expected in test_cases:
            resolved = EngineManager.resolve_model_alias(raw)
            self.assertEqual(resolved, expected, f"Failed for {raw}")

    def test_natural_language_and_spaces_normalization(self):
        """Natural language or friendly model names with spaces and mixed case normalize to OpenRouter slug."""
        test_cases = [
            ("DeepSeek V4 Flash 0731", "openrouter/deepseek/deepseek-v4-flash-0731"),
            ("deepseek v4 flash", "openrouter/deepseek/deepseek-v4-flash-0731"),
            ("DeepSeek V4", "openrouter/deepseek/deepseek-v4-flash-0731"),
            ("Qwen 3", "openrouter/qwen/qwen3-235b-a22b-2507"),
            ("qwen3", "openrouter/qwen/qwen3-235b-a22b-2507"),
            ("Llama 4 Scout", "openrouter/meta-llama/llama-4-scout"),
            ("llama 4", "openrouter/meta-llama/llama-4-scout"),
            ("llama-4", "openrouter/meta-llama/llama-4-scout"),
            ("Ling 3.0 Flash", "openrouter/inclusionai/ling-3.0-flash-fin:free"),
            ("ling", "openrouter/inclusionai/ling-3.0-flash-fin:free"),
            ("Solar Pro 4", "openrouter/upstage/solar-pro4"),
            ("solar-pro4", "openrouter/upstage/solar-pro4"),
        ]
        for raw, expected in test_cases:
            resolved = EngineManager.resolve_model_alias(raw)
            self.assertEqual(resolved, expected, f"Failed for {raw}")

    def test_generic_provider_slug_with_slash(self):
        """Any custom OpenRouter provider/model slug with slash is recognized automatically."""
        self.assertEqual(
            EngineManager.resolve_model_alias("cohere/command-r-plus"),
            "openrouter/cohere/command-r-plus"
        )
        self.assertEqual(
            EngineManager.resolve_model_alias("openrouter/meta-llama/llama-4-scout"),
            "openrouter/meta-llama/llama-4-scout"
        )

    def test_qa2_through_qa6_full_env_template(self):
        """Verifies that setting QA2..QA6 in environment configures all 5 extended QAs in order."""
        env_override = {
            "QA2_MODEL": "upstage/solar-pro4",
            "QA2_ENGINE": "openrouter",
            "QA3_MODEL": "deepseek/deepseek-v4-flash-0731",
            "QA3_ENGINE": "openrouter",
            "QA4_MODEL": "qwen/qwen3-235b-a22b-2507",
            "QA4_ENGINE": "openrouter",
            "QA5_MODEL": "meta-llama/llama-4-scout",
            "QA5_ENGINE": "openrouter",
            "QA6_MODEL": "inclusionai/ling-3.0-flash-fin:free",
            "QA6_ENGINE": "openrouter",
            "DISCORD_AGY_QA3_BOT_TOKEN": "token_qa3",
            "DISCORD_AGY_QA4_BOT_TOKEN": "",
            "DISCORD_AGY_QA5_BOT_TOKEN": "token_qa5",
            "DISCORD_AGY_QA6_BOT_TOKEN": "",
            "QA7_MODEL": "",
        }
        with patch.dict(os.environ, env_override, clear=False):
            configs = config.get_extended_qa_configs()
            self.assertEqual(len(configs), 5)
            self.assertEqual([c["index"] for c in configs], [2, 3, 4, 5, 6])
            self.assertEqual(configs[0]["model"], "upstage/solar-pro4")
            self.assertEqual(configs[1]["model"], "deepseek/deepseek-v4-flash-0731")
            self.assertEqual(configs[1]["token"], "token_qa3")
            self.assertEqual(configs[2]["model"], "qwen/qwen3-235b-a22b-2507")
            self.assertEqual(configs[2]["token"], "")
            self.assertEqual(configs[3]["model"], "meta-llama/llama-4-scout")
            self.assertEqual(configs[3]["token"], "token_qa5")
            self.assertEqual(configs[4]["model"], "inclusionai/ling-3.0-flash-fin:free")
            self.assertEqual(configs[4]["token"], "")

    def test_free_tag_preserved_for_openrouter(self):
        """Verifies that model slugs with :free tags preserve the tag when resolved."""
        resolved = EngineManager.resolve_model_alias("inclusionai/ling-3.0-flash-fin:free")
        direct_target = (resolved or "").replace("openrouter/", "")
        self.assertEqual(direct_target, "inclusionai/ling-3.0-flash-fin:free")


class TestClaudeSonnetGuardAndSquadDispatch(unittest.IsolatedAsyncioTestCase):
    """Unit tests for Claude Sonnet 4.6 (QA7) unlocking, quota & difficulty guards, and Commander arbitration."""

    def setUp(self):
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()
        import telemetry_status_manager
        telemetry_status_manager._sonnet_cache = {"time": 0.0, "data": None}

    def tearDown(self):
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()
        import telemetry_status_manager
        telemetry_status_manager._sonnet_cache = {"time": 0.0, "data": None}

    def test_claude_sonnet_aliases_and_engine_discovery(self):
        """QA7 configured with claude-sonnet-4-6 auto-infers engine='agy'."""
        env_override = {
            "QA7_MODEL": "claude-sonnet-4-6",
            "QA7_ENGINE": "",
        }
        with patch.dict(os.environ, env_override, clear=False):
            configs = config.get_extended_qa_configs()
            qa7 = next((c for c in configs if c["index"] == 7), None)
            self.assertIsNotNone(qa7)
            self.assertEqual(qa7["engine"], "agy")
            self.assertEqual(qa7["model"], "claude-sonnet-4-6")

    def test_claude_sonnet_quota_threshold_guard(self):
        """Telemetry quota check: >= 10% allows Sonnet, < 10% blocks Sonnet."""
        from telemetry_status_manager import get_claude_sonnet_status
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()
        import subprocess

        # Mock quota output with 12.8% remaining (>= 10%)
        mock_healthy_proc = MagicMock(
            returncode=0,
            stdout=json.dumps({"models": [{"modelId": "claude-sonnet-4-6", "remainingPercentage": 0.128, "isExhausted": False}]})
        )
        with patch("subprocess.run", return_value=mock_healthy_proc):
            with patch("telemetry_status_manager._sonnet_cache", {"time": 0.0, "data": None}), \
                 patch("telemetry_status_manager._native_usage_cache", {"time": 0.0, "data": None}):
                status = get_claude_sonnet_status()
                self.assertTrue(status["can_use"])
                self.assertGreaterEqual(status["remaining_pct"], 0.10)
                self.assertEqual(status["percentage_int"], 13)

        # Mock quota output with 8% remaining (< 10%) -> blocked
        mock_low_proc = MagicMock(
            returncode=0,
            stdout=json.dumps({"models": [{"modelId": "claude-sonnet-4-6", "remainingPercentage": 0.08, "isExhausted": False}]})
        )
        with patch("subprocess.run", return_value=mock_low_proc):
            with patch("telemetry_status_manager._sonnet_cache", {"time": 0.0, "data": None}), \
                 patch("telemetry_status_manager._native_usage_cache", {"time": 0.0, "data": None}):
                status = get_claude_sonnet_status()
                self.assertFalse(status["can_use"])
                self.assertLess(status["remaining_pct"], 0.10)
                self.assertIn("잔여 쿼터 부족", status["reason"])

    def test_squad_dispatch_fast_path_never_calls_sonnet(self):
        """Difficulty 1~3 (Fast-Path) must never dispatch QA7 Sonnet even with 100% quota."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.5}):
            # Short styling prompt -> Low difficulty (<= 3)
            res = Commander.dispatch_qa_squad(
                prompt="버튼 CSS margin과 color 색상 오타만 수정해줘",
                diff_info={"changed_files": ["style.css"], "insertions": 2, "deletions": 1},
                available_extended_qas=available_qas
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertLessEqual(res["difficulty"], 3)
            self.assertNotIn(7, selected_indices)
            # Base core squad (QA2 + QA6) + fast QA3 DeepSeek Flash
            self.assertEqual(selected_indices, [2, 3, 6])
            self.assertIn(2, selected_indices)
            self.assertIn(6, selected_indices)
            self.assertIn(3, selected_indices)

    def test_squad_dispatch_normal_path_never_calls_sonnet(self):
        """Difficulty 4~7 (Normal-Path) must dispatch base squad (QA2, QA6) + balanced QAs (QA4, QA5) and keep Sonnet on standby."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.5}):
            res = Commander.dispatch_qa_squad(
                prompt="로그인 폼에 이메일 형식 유효성 검사 함수와 에러 팝업을 추가해줘",
                diff_info={"changed_files": ["login.js"], "insertions": 30, "deletions": 5},
                available_extended_qas=available_qas
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertGreaterEqual(res["difficulty"], 4)
            self.assertLessEqual(res["difficulty"], 7)
            self.assertNotIn(7, selected_indices)
            # Base core squad (QA2 + QA6) + balanced dynamic QAs (QA4 + QA5)
            self.assertEqual(selected_indices, [2, 4, 5, 6])
            self.assertIn(2, selected_indices)
            self.assertIn(6, selected_indices)
            self.assertIn(4, selected_indices)
            self.assertIn(5, selected_indices)

    def test_squad_dispatch_deep_path_with_quota_calls_sonnet(self):
        """Difficulty 8~10 (Deep-Path) with quota >= 10% dispatches QA7 Claude Sonnet."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.20, "percentage_int": 20}):
            res = Commander.dispatch_qa_squad(
                prompt="코어 엔진 아키텍처 리팩토링 및 다중 스레드 동시성 데드락 해결",
                diff_info={"changed_files": [f"f{i}.py" for i in range(6)], "insertions": 300, "deletions": 100},
                available_extended_qas=available_qas
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertGreaterEqual(res["difficulty"], 8)
            self.assertIn(7, selected_indices)  # Sonnet dispatched!
            self.assertIn("Claude Sonnet 4.6", res["dispatch_banner"])

    def test_squad_dispatch_deep_path_low_quota_blocks_sonnet(self):
        """Difficulty 8~10 with quota < 10% safely excludes Sonnet and uses alternative models."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": False, "remaining_pct": 0.05, "percentage_int": 5}):
            res = Commander.dispatch_qa_squad(
                prompt="코어 엔진 아키텍처 리팩토링 및 다중 스레드 동시성 데드락 해결",
                diff_info={"changed_files": [f"f{i}.py" for i in range(6)], "insertions": 300, "deletions": 100},
                available_extended_qas=available_qas
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertGreaterEqual(res["difficulty"], 8)
            self.assertNotIn(7, selected_indices)  # Sonnet blocked!
            self.assertIn("10% 미만 보존", res["dispatch_banner"])

    def test_conflict_resolution_tier1_fact_evidence_superiority(self):
        """Tier 1: When a QA reviewer hallucinates '파일 미생성' but Git diff proves files changed, Arbiter overrides rejection."""
        from commander import Commander
        qa_results = {
            "QA2": {"approved": True, "output": "[STATUS: APPROVED]\n검증 완료."},
            "QA4": {"approved": False, "output": "[STATUS: REJECTED]\n변경 사항 없음. 파일이 생성되지 않았습니다."}
        }
        diff_info = {"changed_files": ["app.py", "test.py"], "insertions": 50, "deletions": 2}
        resolution = Commander.resolve_qa_conflict(qa_results, diff_info=diff_info)
        self.assertTrue(resolution["is_approved"])
        self.assertIn("객관적 증거 우선주의 승인", resolution["arbitration_text"])

    def test_conflict_resolution_tier2_dialectical_synthesis(self):
        """Tier 2 & 3: Conflicting feedback between reviewers produces synthesized directives for Coder."""
        from commander import Commander
        qa_results = {
            "QA2": {"approved": True, "output": "[STATUS: APPROVED]\n실용적인 기본 CRUD 구현 우수함."},
            "QA5": {"approved": False, "output": "[STATUS: REJECTED]\n동시 요청 시 race condition 방어 로직 누락됨."}
        }
        diff_info = {"changed_files": ["api.py"], "insertions": 20, "deletions": 0}
        resolution = Commander.resolve_qa_conflict(qa_results, diff_info=diff_info)
        self.assertFalse(resolution["is_approved"])
        self.assertTrue(resolution["has_conflict"])
        self.assertIn("단일화된 보강 지침", resolution["verdict_summary"])
        self.assertIn("Synthesized Directive for Coder", resolution["arbitration_text"])
        self.assertIn("동시 요청 시 race condition", resolution["arbitration_text"])

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_extended_qas_operate_seamlessly_when_offline_or_not_ready(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """Extended QAs (QA3..QA7) run with 100% functionality even when their Discord bots are completely offline."""
        session = ChannelSession(channel_id=999, workspace="/tmp")
        session.mode = "pair_quad"  # Multi-QA mode

        # Mock offline bot client
        mock_offline_client = MagicMock()
        mock_offline_client.is_ready.return_value = False
        mock_offline_client.user = None

        offline_clients = {
            3: mock_offline_client,
            4: mock_offline_client,
            5: mock_offline_client,
            6: mock_offline_client,
            7: mock_offline_client,
        }

        mock_channel = AsyncMock()
        mock_channel.id = 999
        mock_channel.send = AsyncMock()
        mock_channel.typing = MagicMock()
        mock_channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)

        # Responses: Coder -> QA1 -> QA3 -> QA4 -> Auditor
        mock_run_prompt.side_effect = [
            "Coder implementation complete.",
            _REALISTIC_QA1_PASS,
            _REALISTIC_QA3_PASS,
            _REALISTIC_QA4_PASS,
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor verified complete alignment."
        ]

        active_qas = [
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter", "token": "dummy_token"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter", "token": "dummy_token"},
        ]

        with patch("config.get_extended_qa_configs", return_value=active_qas):
            res = await TrioAgentRunner.run_pipeline(
                session=session,
                prompt="Implement distributed queue handler",
                main_channel=mock_channel,
                max_rounds=1,
                extended_qa_clients=offline_clients
            )

        # Pipeline completed successfully without any crash or stall
        self.assertIn("에이전트 협업 개발 완료", res)
        self.assertIn("최종 승인", res)
        # Verified that messages were routed to main_channel since bots were offline
        self.assertTrue(mock_safe_send.called)
        self.assertTrue(mock_channel.send.called)

    def test_commander_conflict_resolution_overrides_infrastructure_timeout(self):
        """When QA4 encounters a 180s timeout rejection, but peer QAs (QA2, QA5) approved, Arbiter overrides timeout and approves."""
        from commander import Commander
        qa_results = {
            "QA2": {"approved": True, "output": "[STATUS: APPROVED]\nSolar Pro 4 검증 통과."},
            "QA5": {"approved": True, "output": "[STATUS: APPROVED]\nLlama 4 Scout 검증 통과."},
            "QA4": {
                "approved": False,
                "output": (
                    "[STATUS: REJECTED]\n"
                    "⚠️ **[QA4 (qwen/qwen3-235b-a22b-2507) 모델 응답 시간 초과 (180초 타임아웃)]**\n"
                    "OpenRouter 또는 LLM 엔진의 응답이 180초 내에 완료되지 않아 안전 가드에 의해 타임아웃되었습니다."
                )
            }
        }
        diff_info = {"changed_files": ["core.py"], "insertions": 40, "deletions": 5}
        resolution = Commander.resolve_qa_conflict(qa_results, diff_info=diff_info)
        self.assertTrue(resolution["is_approved"])
        self.assertTrue(resolution["has_conflict"])
        self.assertIn("인프라 가드: QA4", resolution["arbitration_text"])
        self.assertIn("동료 검증 통과를 확정 승인", resolution["arbitration_text"])

    @patch("engine_manager.safe_tool_notify", new_callable=AsyncMock)
    async def test_openrouter_adapter_fast_backup_model_retry(self, mock_notify):
        """When primary model fails or times out in OpenRouterAdapter.execute, it retries with fast backup model."""
        from engine_manager import OpenRouterAdapter
        adapter = OpenRouterAdapter()
        session = ChannelSession(channel_id=123, workspace="/tmp", role="qa4")

        with patch.object(adapter, "_get_clean_key", return_value="sk-or-v1-validkey123456789"), \
             patch.object(adapter, "get_binary_path", return_value=None), \
             patch.object(adapter, "execute_direct_api", new_callable=AsyncMock) as mock_api:
            # Primary model (qwen 235B) fails with timeout, backup model succeeds
            mock_api.side_effect = [
                (1, "", ["URLError timed out"]),
                (0, "[STATUS: APPROVED]\nVerified with backup Qwen 2.5 Coder 32B.", [])
            ]

            code, resp, errors = await adapter.execute(
                session=session,
                prompt="Review PR diff",
                model="qwen/qwen3-235b-a22b-2507"
            )

            self.assertEqual(code, 0)
            self.assertIn("[STATUS: APPROVED]", resp)
            self.assertEqual(mock_api.call_count, 2)
            # Verify backup model call was made
            second_call_kwargs = mock_api.call_args_list[1]
            self.assertIn("qwen-2.5-coder-32b-instruct", second_call_kwargs.kwargs.get("model", ""))

    @patch("agent_runner.safe_tool_notify", new_callable=AsyncMock)
    @patch("agent_runner.AgentRunner._execute_single_run", new_callable=AsyncMock)
    async def test_agent_runner_extended_qa_roles_fallback_to_agy(self, mock_single_run, mock_notify):
        """When OpenRouter engine fails on extended QA roles (qa3..qa7), AgentRunner gracefully falls back to AGY."""
        from agent_runner import AgentRunner
        from engine_manager import EngineManager
        session = ChannelSession(channel_id=123, workspace="/tmp", engine="openrouter", role="qa4")

        mock_adapter = MagicMock()
        mock_adapter.icon = "🌐"
        mock_adapter.name = "OpenRouter Universal LLM Engine"
        mock_adapter.is_installed.return_value = True
        mock_adapter.execute = AsyncMock(return_value=(1, "", ["API Connection Failed"]))

        mock_single_run.return_value = (0, "[STATUS: APPROVED]\nAGY fallback verified code.", [], [])

        with patch.object(EngineManager, "get_adapter", return_value=mock_adapter):
            res = await AgentRunner.run_prompt(
                session=session,
                prompt="Review PR",
                model="qwen/qwen3-235b-a22b-2507"
            )

            self.assertIn("[STATUS: APPROVED]", res)
            self.assertIn("AGY fallback", res)
            self.assertTrue(mock_single_run.called)
            # Verify fallback model used was a valid fallback (gemini-3.7-flash-high or opencode-go fallback)
            called_model = mock_single_run.call_args.kwargs.get("model")
            self.assertIn(called_model, ("gemini-3.7-flash-high", "opencode-go/mimo-v2.6-flash"))

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    async def test_multi_qa_pipeline_timeout_resilience_with_commander_arbitration(self, mock_run_prompt, mock_thermal, mock_safe_send):
        """When QA4 times out after 180s but peer QA (QA2) approved, Commander arbitration overrides timeout and pipeline succeeds."""
        import trio_agent_runner
        session = ChannelSession(channel_id=123, workspace="/tmp", mode="pair_quad")
        mock_channel = MagicMock()
        mock_channel.id = 123
        mock_channel.send = AsyncMock()
        mock_channel.typing = MagicMock()
        mock_channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_guild = MagicMock()
        mock_noti_ch = MagicMock()
        mock_noti_ch.send = AsyncMock()
        mock_guild.get_channel.return_value = mock_noti_ch
        mock_channel.guild = mock_guild

        # Coder -> QA1 -> Auditor (normal prompt calls)
        mock_run_prompt.side_effect = [
            "Coder implementation complete.",
            _REALISTIC_QA1_PASS,
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nAuditor approved."
        ]

        active_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter", "role": "qa2"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter", "role": "qa4"}
        ]

        with patch.object(trio_agent_runner.TrioAgentRunner, "run_extended_qa_turn", side_effect=[
            _REALISTIC_QA2_PASS,
            asyncio.TimeoutError("180s timeout"),
        ]) as mock_ext_qa_turn:
            with patch("config.get_extended_qa_configs", return_value=active_qas):
                res = await trio_agent_runner.TrioAgentRunner.run_pipeline(
                    session=session,
                    prompt="Implement distributed queue handler",
                    main_channel=mock_channel,
                    max_rounds=1
                )

            self.assertIn("최종 승인", res)
            self.assertEqual(mock_ext_qa_turn.call_count, 2)

    def test_squad_dispatch_pinned_mode_2plus_dispatches_all_openrouter_qas(self):
        """In !mode 2+ (pinned), all configured OpenRouter QAs are dispatched, while Sonnet is guarded on diff < 8."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.5}):
            # In pair_quad mode without explicit force_all, dispatches targeted squad (Base [2, 6] + Balanced [4, 5])
            res = Commander.dispatch_qa_squad(
                prompt="로그인 폼에 이메일 유효성 검사 추가해줘",
                diff_info={"changed_files": ["login.js"], "insertions": 15, "deletions": 2},
                available_extended_qas=available_qas,
                mode="pair_quad"  # Pinned !mode 2+
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertNotIn(7, selected_indices)
            self.assertIn(7, [q["index"] for q in res["skipped_qas"]])
            self.assertIn("대기", res["dispatch_banner"])

            # Explicit user instruction ("전체 qa") forces all available OpenRouter QAs
            res_all = Commander.dispatch_qa_squad(
                prompt="전체 qa 로그인 폼 전수 검증해줘",
                diff_info={"changed_files": ["login.js"], "insertions": 15, "deletions": 2},
                available_extended_qas=available_qas,
                mode="pair_quad"
            )
            selected_all = [q["index"] for q in res_all["selected_qas"]]
            self.assertEqual(selected_all, [2, 3, 4, 5, 6])

    def test_squad_dispatch_pinned_mode_2plus_includes_sonnet_on_high_diff_and_quota(self):
        """In !mode 2+ (pinned) on high difficulty (>= 7.5) with healthy quota, Sonnet is included in targeted deep squad."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.25, "percentage_int": 25}):
            res = Commander.dispatch_qa_squad(
                prompt="마이크로서비스 아키텍처 재설계 및 분산 트랜잭션 동시성 제어 구현",
                diff_info={"changed_files": [f"service_{i}.py" for i in range(8)], "insertions": 500, "deletions": 150},
                available_extended_qas=available_qas,
                mode="pair_quad"  # Pinned !mode 2+
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            # Targeted deep squad: Base (2, 6) + Sonnet (7) + Deep reasoning (4)
            self.assertEqual(selected_indices, [2, 4, 6, 7])
            self.assertIn(7, selected_indices)
            self.assertIn("Claude Sonnet 4.6", res["strategy"])

    def test_squad_dispatch_pinned_mode_blocks_sonnet_when_quota_insufficient(self):
        """Even in pinned mode, if quota < 10%, Sonnet is strictly guarded and kept on standby."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": False, "remaining_pct": 0.05, "reason": "5.0% < 10.0%"}):
            res = Commander.dispatch_qa_squad(
                prompt="마이크로서비스 아키텍처 재설계 및 분산 트랜잭션 동시성 제어 구현",
                diff_info={"changed_files": [f"service_{i}.py" for i in range(8)], "insertions": 500, "deletions": 150},
                available_extended_qas=available_qas,
                mode="pair_quad"  # Pinned !mode 2+
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertEqual(selected_indices, [2])
            self.assertNotIn(7, selected_indices)

    def test_squad_dispatch_without_diff_info_succeeds(self):
        """dispatch_qa_squad should seamlessly work when diff_info is omitted or None."""
        from commander import Commander
        available_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
        ]
        # Call without diff_info argument (keyword call as in bot.py)
        res1 = Commander.dispatch_qa_squad(
            prompt="일반적인 기능 개선 및 API 연결",
            available_extended_qas=available_qas,
            mode="pair_quad"
        )
        self.assertIn("selected_qas", res1)
        self.assertGreaterEqual(len(res1["selected_qas"]), 1)

        # Call with diff_info=None
        res2 = Commander.dispatch_qa_squad(
            prompt="일반적인 기능 개선 및 API 연결",
            diff_info=None,
            available_extended_qas=available_qas,
            mode="pair_quad"
        )
        self.assertEqual([q["index"] for q in res1["selected_qas"]], [q["index"] for q in res2["selected_qas"]])

        # Call with prompt only (diff_info=None, available_extended_qas=None)
        res3 = Commander.dispatch_qa_squad(prompt="단순 질문 또는 요청")
        self.assertIn("selected_qas", res3)
        self.assertIn("difficulty", res3)

    def test_channel_session_extended_qa_conversation_ids_initialized(self):
        """ChannelSession initializes extended_qa_conversation_ids cleanly and avoids AttributeError."""
        sess = ChannelSession(channel_id=999, workspace="/tmp")
        self.assertIsInstance(sess.extended_qa_conversation_ids, dict)
        sess.set_extended_qa_conversation_id("qa4", "conv-qa4-12345")
        self.assertEqual(sess.get_extended_qa_conversation_id("qa4"), "conv-qa4-12345")
        data = sess.to_dict()
        self.assertIn("extended_qa_conversation_ids", data)
        self.assertEqual(data["extended_qa_conversation_ids"]["qa4"], "conv-qa4-12345")

    def test_commander_arbitration_overrides_timeout_when_peer_qa_approved(self):
        """When QA4 times out but peer QA (QA2) approved, Arbiter overrides timeout."""
        from commander import Commander
        squad_results = {
            "QA2": {
                "approved": True,
                "output": "[STATUS: APPROVED]\nQA2 passed.",
                "model": "upstage/solar-pro4",
                "index": 2
            },
            "QA4": {
                "approved": False,
                "output": "[STATUS: REJECTED]\n⚠️ [QA4 (qwen/qwen3-235b-a22b-2507) 모델 응답 시간 초과 (180초 타임아웃)]",
                "model": "qwen/qwen3-235b-a22b-2507",
                "index": 4
            }
        }
        res = Commander.resolve_qa_conflict(
            qa_results=squad_results,
            diff_info={"changed_files": ["app.py"]},
            prompt="Simple fix"
        )
        self.assertTrue(res["is_approved"])
        self.assertTrue(res["has_conflict"])
        self.assertIn("사령관 중재", res["verdict_summary"])

    def test_commander_arbitration_single_timeout_on_round_1_gives_retry(self):
        """When an isolated QA times out on Round 1 and no peer QA approved, Arbiter yields retry feedback."""
        from commander import Commander
        squad_results = {
            "QA2": {
                "approved": False,
                "output": "[STATUS: REJECTED]\n⚠️ [QA2 (upstage/solar-pro4) 모델 응답 시간 초과 (180초 타임아웃)]",
                "model": "upstage/solar-pro4",
                "index": 2
            }
        }
        res = Commander.resolve_qa_conflict(
            qa_results=squad_results,
            diff_info={"changed_files": ["app.py"]},
            prompt="Simple fix",
            primary_qa_approved=True,
            round_num=1
        )
        self.assertFalse(res["is_approved"])
        self.assertTrue(res["has_conflict"])
        self.assertIn("사령관", res["verdict_summary"])

    def test_commander_arbitration_recurring_timeout_on_round_2_overrides(self):
        """When isolated QA timeout recurs on Round 2+ and primary QA approved, Arbiter overrides to prevent deadlock."""
        from commander import Commander
        squad_results = {
            "QA2": {
                "approved": False,
                "output": "[STATUS: REJECTED]\n⚠️ [QA2 (upstage/solar-pro4) 모델 응답 시간 초과 (180초 타임아웃)]",
                "model": "upstage/solar-pro4",
                "index": 2
            }
        }
        res = Commander.resolve_qa_conflict(
            qa_results=squad_results,
            diff_info={"changed_files": ["app.py"]},
            prompt="Simple fix",
            primary_qa_approved=True,
            round_num=2
        )
        self.assertTrue(res["is_approved"])
        self.assertTrue(res["has_conflict"])
        self.assertIn("사령관 중재", res["verdict_summary"])



class TestAutoPilotInitResilience(unittest.TestCase):
    """Unit tests verifying AutoPilotManager init compatibility and kwargs resilience."""

    def test_autopilot_init_accepts_extended_qa_and_kwargs(self):
        from auto_pilot import AutoPilotManager
        sm = SessionManager()
        mock_qa2 = MagicMock()
        mock_ext = {2: mock_qa2, 3: MagicMock()}
        apm = AutoPilotManager(
            session_manager=sm,
            qa2_bot=mock_qa2,
            extended_qa_bots=mock_ext,
            unexpected_custom_kwarg="safe_fallback"
        )
        self.assertEqual(apm.qa2_bot, mock_qa2)
        self.assertIn(2, apm.extended_qa_bots)
        self.assertEqual(getattr(apm, "unexpected_custom_kwarg", None), "safe_fallback")

    def test_reload_helper_modules_handles_legacy_autopilot_gracefully(self):
        """Even if sys.modules['auto_pilot'].AutoPilotManager lacks qa2_bot/extended_qa_bots, reload_helper_modules catches TypeError gracefully."""
        from session_manager import SessionManager
        import bot

        class MockLegacyAutoPilotManager:
            def __init__(self, session_manager, commander_bot=None, qa_bot=None, auditor_bot=None, qa_hub_manager=None, self_healing_manager=None):
                self.session_manager = session_manager
                self.states = {}

        with patch.dict(sys.modules, {"auto_pilot": MagicMock(AutoPilotManager=MockLegacyAutoPilotManager)}):
            # This should not raise TypeError
            bot.reload_helper_modules()
            self.assertIsNotNone(bot.auto_pilot_manager)


class TestIntelligentSquadSelectionAndArbitration(unittest.IsolatedAsyncioTestCase):
    """Dedicated tests for intelligent squad selection, Sonnet 7.5 gate, incremental skip, and sync/async ResolvingDict."""

    def setUp(self):
        self.full_extended_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "name": "QA3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "openrouter"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "openrouter"},
            {"index": 5, "name": "QA5", "model": "meta-llama/llama-4-scout", "engine": "openrouter"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "openrouter"},
            {"index": 7, "name": "QA7", "model": "claude-sonnet-4-6", "engine": "agy"},
        ]

    def test_base_squad_qa2_qa6_always_locked_in_whenever_available(self):
        """QA2 (Solar Pro 4) and QA6 (Ling 3.0 Flash) must always be locked into the squad."""
        from commander import Commander
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.5}):
            res = Commander.dispatch_qa_squad(
                prompt="간단한 리팩토링 및 헬퍼 함수 분리",
                available_extended_qas=self.full_extended_qas
            )
            selected_indices = [q["index"] for q in res["selected_qas"]]
            self.assertIn(2, selected_indices)
            self.assertIn(6, selected_indices)

    def test_claude_sonnet_strictly_gated_by_difficulty_7_5_threshold(self):
        """QA7 Claude Sonnet 4.6 is strictly gated: standby below 7.5, dispatched at >= 7.5 with healthy quota."""
        from commander import Commander
        # Case A: Difficulty < 7.5 (e.g. 7.4) -> Sonnet MUST be on standby even with 100% quota
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 1.0}):
            with patch.object(Commander, "evaluate_task_difficulty", return_value=(7.4, "MEDIUM", "중간 난이도", False)):
                res_low = Commander.dispatch_qa_squad(
                    prompt="비즈니스 로직 작성",
                    available_extended_qas=self.full_extended_qas
                )
                sel_low = [q["index"] for q in res_low["selected_qas"]]
                self.assertNotIn(7, sel_low)
                self.assertIn(7, [q["index"] for q in res_low["skipped_qas"]])

        # Case B: Difficulty >= 7.5 (e.g. 7.5) with healthy quota -> Sonnet MUST be dispatched
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": True, "remaining_pct": 0.2, "percentage_int": 20}):
            with patch.object(Commander, "evaluate_task_difficulty", return_value=(7.5, "HIGH", "심층 작업", False)):
                res_high = Commander.dispatch_qa_squad(
                    prompt="핵심 아키텍처 재설계",
                    available_extended_qas=self.full_extended_qas
                )
                sel_high = [q["index"] for q in res_high["selected_qas"]]
                self.assertIn(7, sel_high)
                self.assertIn(2, sel_high)
                self.assertIn(6, sel_high)

        # Case C: Difficulty >= 7.5 but quota < 10% -> Sonnet MUST be blocked to preserve quota
        with patch("telemetry_status_manager.get_claude_sonnet_status", return_value={"can_use": False, "remaining_pct": 0.08, "percentage_int": 8}):
            with patch.object(Commander, "evaluate_task_difficulty", return_value=(8.0, "HIGH", "초고난도", False)):
                res_blocked = Commander.dispatch_qa_squad(
                    prompt="핵심 아키텍처 재설계",
                    available_extended_qas=self.full_extended_qas
                )
                sel_blocked = [q["index"] for q in res_blocked["selected_qas"]]
                self.assertNotIn(7, sel_blocked)
                self.assertIn(7, [q["index"] for q in res_blocked["skipped_qas"]])

    async def test_resolving_dict_supports_both_sync_and_async_await(self):
        """Commander.resolve_qa_conflict returns ResolvingDict which works both synchronously and with await."""
        from commander import Commander, ResolvingDict
        qa_results = {
            "QA2": {"approved": True, "output": "[STATUS: APPROVED]\n검증 통과"},
            "QA4": {"approved": False, "output": "[STATUS: REJECTED]\n에러 처리 구문 보강 필요"}
        }

        # Sync access (subscripting directly without await)
        sync_res = Commander.resolve_qa_conflict(
            qa_results=qa_results,
            prompt="API 호출 함수 구현",
            primary_qa_approved=True,
            round_num=1
        )
        self.assertIsInstance(sync_res, ResolvingDict)
        self.assertIn("is_approved", sync_res)
        self.assertIn("verdict_summary", sync_res)

        # Async access (awaiting the ResolvingDict)
        async_res = await Commander.resolve_qa_conflict(
            qa_results=qa_results,
            prompt="API 호출 함수 구현",
            primary_qa_approved=True,
            round_num=1
        )
        self.assertEqual(async_res["is_approved"], sync_res["is_approved"])
        self.assertEqual(async_res["verdict_summary"], sync_res["verdict_summary"])

    def test_arbitration_synthesizes_definitive_directive_on_conflict(self):
        """When QAs conflict on design choices, Commander synthesizes a single authoritative directive to prevent ping-pong loops."""
        from commander import Commander
        qa_results = {
            "QA2": {"approved": True, "output": "[STATUS: APPROVED]\n함수형 구조로 잘 작성됨."},
            "QA4": {"approved": False, "output": "[STATUS: REJECTED]\n클래스 기반 객체지향 패턴으로 리팩토링해야 함."}
        }
        res = Commander.resolve_qa_conflict(
            qa_results=qa_results,
            prompt="데이터 파서 구현",
            primary_qa_approved=True,
            round_num=1
        )
        # Commander synthesizes a single unified directive for Coder to fix without ping-pong
        self.assertFalse(res["is_approved"])
        self.assertTrue(res["has_conflict"])
        self.assertIn("단일화된 보강 지침", res["verdict_summary"])
        self.assertIn("synthesized_directive", res)
        self.assertIn("반려", res["synthesized_directive"])

    def test_user_complaint_about_all_qas_never_triggers_force_all(self):
        """User complaining about QA 1~7 all being called must NEVER trigger force_all or dispatch all QAs."""
        from commander import Commander
        user_complaint = (
            "분명 내가 여러차례 qa봇은 1~7까지 전부 다 거쳐갈 필요가 없다. "
            "commander봇(1540273467453345812)이 해당 작업에 잘 어울리는 피드백을 줄걸로 예상되는 "
            "qa봇들을 그때마다 선정해서 피어리뷰해주기를 바란다. 라고 얘기했었고, 그렇게 수정을 했었거든? "
            "분명?? 근데 다시 또 이렇게 qa봇 1~7까지 전부 다 거쳐가는 형식으로 돌아와져 있네;; 너가 수정을 좀 해줄래??"
        )
        self.assertFalse(Commander.is_force_all_requested(user_complaint))

        res = Commander.dispatch_qa_squad(
            prompt=user_complaint,
            available_extended_qas=self.full_extended_qas
        )
        selected_names = [q["name"] for q in res["selected_qas"]]
        skipped_names = [q["name"] for q in res["skipped_qas"]]

        # Base squad QA2 and QA6 must be selected
        self.assertIn("QA2", selected_names)
        self.assertIn("QA6", selected_names)
        # Must NOT dispatch all 6 extended QAs
        self.assertLess(len(selected_names), 5)
        # QA7 Claude Sonnet must be skipped because difficulty is < 7.5
        self.assertNotIn("QA7", selected_names)
        self.assertIn("QA7", skipped_names)
        self.assertTrue(len(skipped_names) >= 2)

    def test_is_force_all_requested_handles_negative_modifiers_and_reports(self):
        """is_force_all_requested correctly discriminates affirmative commands vs negative phrasing / reports."""
        from commander import Commander
        # Negative phrases must return False
        self.assertFalse(Commander.is_force_all_requested("전체 qa 거쳐갈 필요가 없다"))
        self.assertFalse(Commander.is_force_all_requested("전체 qa 하지 마라"))
        self.assertFalse(Commander.is_force_all_requested("모든 qa 다 돌릴 필요 없어"))
        self.assertFalse(Commander.is_force_all_requested("7개 모두 거쳐가지 않도록 해줘"))
        self.assertFalse(Commander.is_force_all_requested("전부 검증할 필요 없다"))
        self.assertFalse(Commander.is_force_all_requested('사용자가 명시적으로 "전체 qa", "전수 검증" 등을 요청한 경우에만'))

        # Explicit affirmative requests must return True
        self.assertTrue(Commander.is_force_all_requested("!all_qa"))
        self.assertTrue(Commander.is_force_all_requested("!mode all_qa"))
        self.assertTrue(Commander.is_force_all_requested("전체 qa 전부 출동해주세요"))
        self.assertTrue(Commander.is_force_all_requested("7개 모두 출동해줘"))

    def test_incremental_review_only_re_evaluates_rejected_qas(self):
        """In multi-round review, previously approved QAs are preserved and only rejected QAs are pending."""
        squad_qas = [
            {"index": 2, "name": "QA2", "model": "upstage/solar-pro4"},
            {"index": 4, "name": "QA4", "model": "qwen/qwen3-235b-a22b-2507"},
            {"index": 6, "name": "QA6", "model": "inclusionai/ling-3.0-flash-fin:free"}
        ]
        # Simulate round 1 where QA2 and QA6 approved, but QA4 rejected
        previously_approved = {1, 2, 6}

        pending = [q for q in squad_qas if q.get("index") not in previously_approved]
        passed = [q for q in squad_qas if q.get("index") in previously_approved]

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["name"], "QA4")
        self.assertEqual([q["name"] for q in passed], ["QA2", "QA6"])


if __name__ == "__main__":
    unittest.main()



