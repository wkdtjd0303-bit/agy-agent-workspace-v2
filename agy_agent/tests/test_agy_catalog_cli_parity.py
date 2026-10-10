#!/usr/bin/env python3
"""
[🧭 CLI 정합 & ⏱️ Timeout Guard] 회귀 테스트.

배경(사용자 보고):
  - `!model` 후 무언가를 누르면 "어플리케이션이 적시에 응답하지 않았어요" 오류가 잦다.
  - `!model` 목록에 CLI `/model`(=`agy models`) 에는 없는 "이상한 모델"이 표기된다.

검증:
  1. AGY 엔진 목록(ENGINE_PRESETS / _FALLBACK_OFFICIAL_CATALOG)이 CLI `/model` 14개와
     정확히 일치하고, CLI 에 없는 유령 모델(tiered/agent/3-flash 등)을 노출하지 않는다.
  2. quota API 가 `/model` 에 없는 모델을 반환해도 카탈로그에 추가하지 않는다(보강만).
  3. `agy` 라이브 카탈로그는 authoritative — seed 재병합으로 유령이 부활하지 않는다.
  4. 컴포넌트 상호작용은 대시보드 REST(=3초 초과 위험) 보다 먼저 ack(응답)한다.
"""

import os
import sys
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# CLI `agy models` 실측 출력과 1:1 (2026-10-08)
_AGY_CLI_MODELS = {
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.7-flash-low",
    "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
    "gemini-3.1-pro-high", "gemini-3.1-pro-low",
    "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium",
}

# CLI `/model` 에 없는 대표 유령 모델(quota/정적 목록에만 존재하던 것들)
_GHOST_MODELS = {
    "gemini-3.8-flash-tiered", "gemini-3.7-flash-tiered", "gemini-3.6-flash-tiered",
    "gemini-3.5-flash-agent", "gemini-3-flash",
    "gemini-pro-agent", "gemini-3-flash-agent",
    "gemini-3.5-flash-extra-low", "gemini-3.5-flash-low",
}


class TestAgyCliCatalogParity(unittest.TestCase):
    def test_engine_preset_matches_cli_catalog(self):
        import model_manager
        ids = {mid for mid, _ in model_manager.ENGINE_PRESETS["agy"]["models"]}
        self.assertEqual(ids, _AGY_CLI_MODELS)
        self.assertFalse(ids & _GHOST_MODELS, f"유령 모델 노출: {sorted(ids & _GHOST_MODELS)}")

    def test_fallback_catalog_matches_cli_catalog(self):
        from model_registry import _FALLBACK_OFFICIAL_CATALOG
        ids = {m["modelId"] for m in _FALLBACK_OFFICIAL_CATALOG}
        self.assertEqual(ids, _AGY_CLI_MODELS)
        self.assertFalse(ids & _GHOST_MODELS)

    def test_quota_only_models_are_not_added(self):
        """quota API 의 `/model` 미지원 모델은 카탈로그에 추가되지 않아야 한다(보강만)."""
        from model_registry import AntigravityModelRegistry
        AntigravityModelRegistry._cached_models = []
        AntigravityModelRegistry._last_fetch_time = 0.0
        quota = [
            {"modelId": "gemini-3.8-flash-high", "remainingPercentage": 0.5},
            {"modelId": "gemini-3.8-flash-tiered", "remainingPercentage": 0.4},  # ghost
            {"modelId": "gemini-pro-agent", "remainingPercentage": 0.3},         # ghost
        ]
        with mock.patch.object(AntigravityModelRegistry, "fetch_quota_models", return_value=quota), \
             mock.patch.object(AntigravityModelRegistry, "fetch_agy_cli_models", return_value=[]):
            models = AntigravityModelRegistry.fetch_models(force_refresh=True)

        ids = {m["modelId"] for m in models}
        self.assertIn("gemini-3.8-flash-high", ids)
        self.assertNotIn("gemini-3.8-flash-tiered", ids)
        self.assertNotIn("gemini-pro-agent", ids)
        # 기존 모델은 잔여량 정보로 보강되어야 한다.
        high = next(m for m in models if m["modelId"] == "gemini-3.8-flash-high")
        self.assertEqual(high.get("remainingPercentage"), 0.5)

    def test_agy_live_catalog_is_authoritative(self):
        import live_model_catalog as lmc
        self.assertIn("agy", lmc._LIVE_IS_AUTHORITATIVE)

    def test_picker_shows_only_cli_models(self):
        import live_model_catalog as lmc
        import model_manager as mm
        lmc.LiveModelCatalog._cache["agy"] = {
            "models": [(m, m) for m in sorted(_AGY_CLI_MODELS)],
            "discovered": [],
            "fetched_at": time.time(),
            "live": True,
            "source": "live (test)",
            "error": None,
        }
        try:
            out = {mid for mid, _ in mm.get_engine_models("agy")}
        finally:
            lmc.LiveModelCatalog._cache.pop("agy", None)
        self.assertEqual(out, _AGY_CLI_MODELS)
        self.assertFalse(out & _GHOST_MODELS)

    def test_agy_authoritative_refresh_drops_seed_ghosts(self):
        """seed(구버전 curated)에 유령이 있어도 라이브 authoritative 면 노출되지 않는다."""
        import live_model_catalog as lmc
        with mock.patch.dict(lmc._PROVIDERS, {"agy": lambda s: [(m, m) for m in sorted(_AGY_CLI_MODELS)]}), \
             mock.patch.object(lmc, "_seed_for", return_value=[("gemini-3.8-flash-tiered", "ghost"), ("gemini-3.8-flash-high", "ok")]):
            lmc.LiveModelCatalog._refresh_sync("agy", force=True)
            try:
                out = {m for m, _ in lmc.LiveModelCatalog._cache["agy"]["models"]}
            finally:
                lmc.LiveModelCatalog._cache.pop("agy", None)
        self.assertNotIn("gemini-3.8-flash-tiered", out)
        self.assertEqual(out, _AGY_CLI_MODELS)


class TestInteractionAckOrdering(unittest.IsolatedAsyncioTestCase):
    async def test_search_result_select_acks_before_dashboard_edit(self):
        """검색 결과 선택 시 대시보드 REST 호출보다 먼저 상호작용을 ack 해야 한다."""
        import model_manager as mm

        order = []
        parent = mock.MagicMock()
        parent.selected_role = "coder"
        parent.message = mock.MagicMock()

        async def _parent_edit(*_a, **_k):
            order.append("parent_edit")
        parent.message.edit = _parent_edit

        interaction = mock.MagicMock()
        interaction.response.is_done = mock.MagicMock(return_value=False)

        async def _ack_edit(*_a, **_k):
            order.append("ack")
        interaction.response.edit_message = _ack_edit

        sel = mm.ModelSearchResultSelect(
            "coder", "agy", [("gemini-3.8-flash-high", "Gemini 3.8 Flash")], parent_view=parent
        )
        sel._values = ["gemini-3.8-flash-high"]

        with mock.patch.object(mm.GlobalModelManager, "set_role_model", return_value=True):
            await sel.callback(interaction)

        self.assertTrue(order, "상호작용 응답이 전혀 발생하지 않음")
        self.assertEqual(order[0], "ack", f"ack 가 대시보드 edit 보다 먼저여야 함: {order}")
        self.assertIn("parent_edit", order, "ack 이후 대시보드 갱신이 수행되어야 함")

    async def test_preset_page_nav_defers_immediately(self):
        """모델 목록 페이지 이동도 ack(defer)를 먼저 호출해야 한다."""
        import model_manager as mm
        view = mm.ModelDashboardView(selected_role="coder")
        dropdown = [c for c in view.children if isinstance(c, mm.ModelPresetDropdown)][0]
        dropdown._values = ["__page_0__"]

        interaction = mock.MagicMock()
        interaction.response.is_done = mock.MagicMock(return_value=False)
        interaction.response.defer = mock.AsyncMock()
        interaction.edit_original_response = mock.AsyncMock()
        interaction.response.edit_message = mock.AsyncMock()

        await dropdown.callback(interaction)
        interaction.response.defer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
