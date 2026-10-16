#!/usr/bin/env python3
"""
test_router_switch_timeout.py
=============================
!model 대시보드에서 라우터(엔진) 변경 시 발생하는
'애플리케이션이 적시에 응답하지 않았어요' (Discord Interaction Timeout) 버그 방지 회귀 테스트.

검증 항목:
1. EngineSelectDropdown.callback 실행 시 동기 블로킹(get_models_blocking)을 절대 호출하지 않아야 한다.
2. 백그라운드 웜업(LiveModelCatalog.warmup)을 비동기로 호출해야 한다.
3. interaction.response.defer() 또는 safe edit 이 3초 이내(즉시)에 정상 완료되어야 한다.
4. 라우터 변경 후 모델이 해당 엔진과 호환되지 않으면 새 엔진의 기본 모델로 즉시 동기화되어야 한다.
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import model_manager as mm
from live_model_catalog import LiveModelCatalog


class TestRouterSwitchTimeout(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        if not mm.GlobalModelManager._initialized:
            mm.GlobalModelManager.initialize()

    async def test_engine_dropdown_callback_is_non_blocking_and_never_calls_blocking_fetch(self):
        """EngineSelectDropdown.callback 은 get_models_blocking 을 절대 호출하지 않고 즉시 완료되어야 한다."""
        view = mm.ModelDashboardView(selected_role="coder")
        dropdown = mm.EngineSelectDropdown(selected_role="coder")
        dropdown._view = view
        dropdown._values = ["hermes"]

        mock_interaction = MagicMock()
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.edit_original_response = AsyncMock()
        mock_interaction.response.edit_message = AsyncMock()

        with patch.object(LiveModelCatalog, "get_models_blocking", side_effect=AssertionError("get_models_blocking must NOT be called in UI callback!")) as mock_blocking, \
             patch.object(LiveModelCatalog, "warmup") as mock_warmup, \
             patch("model_manager._safe_edit_view", new_callable=AsyncMock) as mock_edit:

            # callback 실행
            await dropdown.callback(mock_interaction)

            # 1. get_models_blocking 은 절대 불리지 않아야 함
            mock_blocking.assert_not_called()

            # 2. 대신 백그라운드 warmup 이 트리거되어야 함
            mock_warmup.assert_called_once_with(["hermes"])

            # 3. Discord UI 수정(_safe_edit_view)이 즉시 호출되어야 함
            mock_edit.assert_awaited_once()

    async def test_engine_switch_updates_role_engine_and_resets_incompatible_model(self):
        """라우터 변경 시 새 엔진 설정 및 비호환 모델 자동 치환이 즉시 완료되어야 한다."""
        view = mm.ModelDashboardView(selected_role="coder")
        dropdown = mm.EngineSelectDropdown(selected_role="coder")
        dropdown._view = view
        dropdown._values = ["codex"]

        mock_interaction = MagicMock()
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()

        with patch("model_manager._safe_edit_view", new_callable=AsyncMock):
            await dropdown.callback(mock_interaction)

        role_cfg = mm.GlobalModelManager.get_role_config("coder")
        self.assertEqual(role_cfg["engine"], "codex")
        self.assertTrue(mm.GlobalModelManager.is_model_compatible_with_engine("codex", role_cfg["model"]))


if __name__ == "__main__":
    unittest.main()
