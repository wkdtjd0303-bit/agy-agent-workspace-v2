#!/usr/bin/env python3
"""
Unit tests for [📺 채널별 모델 설정] (channel-scoped model overrides).

검증 범위:
1. 채널 오버라이드 write/read 격리 (다른 채널·전역에 영향 0)
2. Ambient Channel Context (contextvars) 자동 적용
3. 엔진/모델 정합성 가드가 채널 스코프에서도 동작
4. 영속화(파일 저장/로드) 및 초기화(reset)
5. 대시보드 임베드 채널 스코프 배지
"""

import os
import sys
import json
import unittest
from unittest.mock import MagicMock, AsyncMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import model_manager
from model_manager import GlobalModelManager


CH_A = 100001
CH_B = 100002


class TestChannelModelOverrides(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        GlobalModelManager.initialize()
        GlobalModelManager.load_channel_overrides(force=True)
        # 테스트 채널 잔여 오버라이드 청소
        GlobalModelManager.clear_channel_override(CH_A)
        GlobalModelManager.clear_channel_override(CH_B)

    def tearDown(self):
        GlobalModelManager.clear_channel_override(CH_A)
        GlobalModelManager.clear_channel_override(CH_B)

    # ------------------------------------------------------------------
    # 1. 격리성
    # ------------------------------------------------------------------
    def test_channel_override_isolation(self):
        g_coder_before = GlobalModelManager.get_role_model("coder")

        GlobalModelManager.set_role_model("coder", "opencode-go/deepseek-v4-pro", channel_id=CH_A)

        self.assertEqual(
            GlobalModelManager.get_role_model("coder", channel_id=CH_A),
            "opencode-go/deepseek-v4-pro",
        )
        # 전역 설정은 그대로
        self.assertEqual(GlobalModelManager.get_role_model("coder"), g_coder_before)
        # 다른 채널도 전역 값
        self.assertEqual(GlobalModelManager.get_role_model("coder", channel_id=CH_B), g_coder_before)

    def test_channel_enabled_toggle_isolation(self):
        GlobalModelManager.set_role_enabled("qa2", False, channel_id=CH_A)
        self.assertFalse(GlobalModelManager.is_qa_enabled(2, channel_id=CH_A))
        self.assertTrue(GlobalModelManager.is_qa_enabled(2, channel_id=CH_B))
        self.assertTrue(GlobalModelManager.is_qa_enabled(2))

    def test_channel_persona_isolation(self):
        GlobalModelManager.set_role_persona("qa3", "security", channel_id=CH_A)
        self.assertEqual(GlobalModelManager.get_role_persona("qa3", channel_id=CH_A), "security")
        self.assertNotEqual(GlobalModelManager.get_role_persona("qa3", channel_id=CH_B), "security")

    def test_channel_engine_set_reports_coherence(self):
        """AGY 엔진 + OpenCode 모델 조합은 채널 스코프에서도 자동 교정된다."""
        GlobalModelManager.set_role_model("coder", "opencode-go/deepseek-v4-pro", channel_id=CH_A)
        GlobalModelManager.set_role_engine("coder", "agy", channel_id=CH_A)
        eng = GlobalModelManager.get_role_engine("coder", channel_id=CH_A)
        mdl = GlobalModelManager.get_role_model("coder", channel_id=CH_A)
        self.assertEqual(eng, "agy")
        self.assertFalse(mdl.startswith("opencode-go/"))

    def test_bulk_qa_ops_channel_scope(self):
        GlobalModelManager.set_all_qa_enabled(False, channel_id=CH_A)
        self.assertFalse(GlobalModelManager.is_qa_enabled(1, channel_id=CH_A))
        self.assertFalse(GlobalModelManager.is_qa_enabled(3, channel_id=CH_A))
        GlobalModelManager.set_only_primary_qa(channel_id=CH_A)
        self.assertTrue(GlobalModelManager.is_qa_enabled(1, channel_id=CH_A))
        self.assertFalse(GlobalModelManager.is_qa_enabled(3, channel_id=CH_A))

    def test_active_extended_qas_channel_scope(self):
        GlobalModelManager.set_role_enabled("qa2", False, channel_id=CH_A)
        roles = [q["role"] for q in GlobalModelManager.get_active_extended_qas(channel_id=CH_A)]
        self.assertNotIn("qa2", roles)
        roles_other = [q["role"] for q in GlobalModelManager.get_active_extended_qas(channel_id=CH_B)]
        self.assertIn("qa2", roles_other)

    # ------------------------------------------------------------------
    # 2. Ambient Channel Context
    # ------------------------------------------------------------------
    def test_ambient_channel_context(self):
        GlobalModelManager.set_role_model("coder", "opencode-go/glm-5.3-flash", channel_id=CH_A)
        default_global = GlobalModelManager.get_role_model("coder", channel_id=CH_B)

        with GlobalModelManager.channel_context(CH_A):
            self.assertEqual(GlobalModelManager.get_role_model("coder"), "opencode-go/glm-5.3-flash")
            # 컨텍스트 안에서의 쓰기도 채널 오버라이드로 간다
            GlobalModelManager.set_role_model("qa2", "upstage/solar-pro4")

        self.assertEqual(GlobalModelManager.get_role_model("qa2", channel_id=CH_A), "upstage/solar-pro4")
        # 컨텍스트 이탈 후 전역 복귀
        self.assertEqual(GlobalModelManager.get_role_model("coder", channel_id=CH_B), default_global)

    def test_ambient_context_not_leaking(self):
        with GlobalModelManager.channel_context(CH_A):
            pass
        self.assertIsNone(GlobalModelManager.get_active_channel())

    # ------------------------------------------------------------------
    # 3. 영속화 / 초기화
    # ------------------------------------------------------------------
    def test_persistence_and_reset(self):
        GlobalModelManager.set_role_model("coder", "opencode-go/kimi-k3", channel_id=CH_A)
        GlobalModelManager.save_channel_overrides()

        raw = json.loads(open(model_manager.CHANNEL_CONFIG_FILE, "r", encoding="utf-8").read())
        self.assertIn(str(CH_A), raw)
        self.assertEqual(raw[str(CH_A)]["coder"]["model"], "opencode-go/kimi-k3")

        # 강제 재로딩 후에도 유지
        GlobalModelManager.load_channel_overrides(force=True)
        self.assertEqual(GlobalModelManager.get_role_model("coder", channel_id=CH_A), "opencode-go/kimi-k3")

        # 역할 단위 리셋
        GlobalModelManager.clear_channel_override(CH_A, "coder")
        self.assertFalse(GlobalModelManager.has_channel_override("coder", channel_id=CH_A))

        # 채널 단위 리셋
        GlobalModelManager.clear_channel_override(CH_A)
        self.assertEqual(GlobalModelManager.get_channel_overrides(CH_A), {})

    # ------------------------------------------------------------------
    # 4. 대시보드 임베드
    # ------------------------------------------------------------------
    def test_dashboard_embed_channel_scope(self):
        embed_global = GlobalModelManager.build_dashboard_embed(selected_role="coder")
        self.assertIn("전역", embed_global.title)

        GlobalModelManager.set_role_model("coder", "opencode-go/glm-5.3", channel_id=CH_A)
        embed_ch = GlobalModelManager.build_dashboard_embed("coder", channel_id=CH_A)
        self.assertIn("채널 전용", embed_ch.title)
        self.assertIn(str(CH_A), embed_ch.description)

    def test_get_all_roles_channel_scope(self):
        GlobalModelManager.set_role_model("coder", "opencode-go/grok-4.7", channel_id=CH_A)
        roles_ch = GlobalModelManager.get_all_roles(channel_id=CH_A)
        roles_global = GlobalModelManager.get_all_roles()
        self.assertEqual(roles_ch["coder"]["model"], "opencode-go/grok-4.7")
        self.assertNotEqual(roles_global["coder"]["model"], "opencode-go/grok-4.7")

    # ------------------------------------------------------------------
    # 5. 대시보드 뷰가 채널 ID 를 보존
    # ------------------------------------------------------------------
    async def test_dashboard_view_keeps_channel_id(self):
        view = model_manager.ModelDashboardView(selected_role="qa2", channel_id=CH_A)
        self.assertEqual(view.channel_id, CH_A)
        # 뷰 렌더링이 채널 오버라이드를 반영해도 예외 없이 동작
        GlobalModelManager.set_role_enabled("qa2", False, channel_id=CH_A)
        view.update_components()

    # ------------------------------------------------------------------
    # 6. 파이프라인 연동 (핵심 가치)
    # ------------------------------------------------------------------
    async def test_channel_override_applies_to_trio_pipeline(self):
        """채널 오버라이드가 실제 Coder 턴 해석에 반영된다 (Ambient Context 경유)."""
        from unittest.mock import patch, AsyncMock
        from session_manager import ChannelSession
        from trio_agent_runner import TrioAgentRunner

        GlobalModelManager.set_role_model("coder", "opencode-go/deepseek-v4-pro", channel_id=CH_A)
        session = ChannelSession(channel_id=CH_A, workspace="/tmp")

        with GlobalModelManager.channel_context(CH_A):
            with patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as m:
                m.return_value = "작업 완료"
                await TrioAgentRunner.run_coder_turn(session, "테스트 요청")
                kwargs = m.call_args.kwargs
                self.assertEqual(kwargs.get("model"), "opencode-go/deepseek-v4-pro")
                self.assertEqual(kwargs.get("engine"), "opencode")

        # 컨텍스트 이탈 후에는 전역 모델로 복귀
        with patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as m2:
            m2.return_value = "ok"
            await TrioAgentRunner.run_coder_turn(session, "다른 요청")
            self.assertNotEqual(m2.call_args.kwargs.get("model"), "opencode-go/deepseek-v4-pro")

    async def test_channel_override_applies_to_extended_qa_list(self):
        """채널에서 qa2 를 끄면 해당 채널의 확장 QA 파이프라인에서 제외된다."""
        from session_manager import ChannelSession
        from trio_agent_runner import TrioAgentRunner

        GlobalModelManager.set_role_enabled("qa2", False, channel_id=CH_A)
        session = ChannelSession(channel_id=CH_A, workspace="/tmp")
        with GlobalModelManager.channel_context(CH_A):
            active = TrioAgentRunner._resolve_active_qa_configs(session) \
                if hasattr(TrioAgentRunner, "_resolve_active_qa_configs") else None
            if active is None:
                # 헬퍼가 없으면 모델 관리자 레벨에서 직접 검증
                roles = [q["role"] for q in GlobalModelManager.get_active_extended_qas(channel_id=CH_A)]
                self.assertNotIn("qa2", roles)
            else:
                self.assertNotIn("qa2", [c.get("role") for c in active])

    async def test_custom_model_modal_writes_channel_override(self):
        view = model_manager.ModelDashboardView(selected_role="coder", channel_id=CH_A)
        modal = model_manager.CustomModelModal("coder", "opencode-go/space-bunny-free", view)
        modal.model_input._value = "opencode-go/longcat-2.0"

        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.edit_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await modal.on_submit(mock_interaction)
        self.assertEqual(
            GlobalModelManager.get_role_model("coder", channel_id=CH_A),
            "opencode-go/longcat-2.0",
        )


if __name__ == "__main__":
    unittest.main()
