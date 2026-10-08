#!/usr/bin/env python3
"""
test_live_model_catalog.py
==========================
`!model` 자동 모델 카탈로그 회귀 테스트.

검증 대상
---------
1. `LiveModelCatalog` : 엔진별 실시간 조회 → union → 캐시 → 자동 갱신 파이프라인
2. `model_manager.get_engine_models()` : 실시간 카탈로그 위임 + curated 폴백
3. `ModelPresetDropdown` : Discord Select 25개 하드캡 안에서 **신규 모델이 반드시
   보인다** (핵심 회귀). 자동화 이전에는 신규 모델이 목록 끝에 붙어 25개를 넘어
   영영 화면에 나타나지 않았다.
4. `ModelSearchResultView` : 25개 초과 결과 페이지네이션
5. 엔진 소유권 필터 : opencode 드롭다운에 openrouter/* 가 섞이지 않는다

실행:
    cd agy_agent && python3 tests/test_live_model_catalog.py
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import live_model_catalog as lmc  # noqa: E402


def _set_env(name: str, value: str) -> None:
    os.environ[name] = value


class TestLiveModelCatalogCore(unittest.TestCase):
    """순수 로직(union/정렬/소유권 필터) — 네트워크 불필요."""

    def setUp(self) -> None:
        lmc.LiveModelCatalog._cache.clear()
        lmc.LiveModelCatalog._refreshing.clear()

    def test_dedupe_union_first_seen_wins(self):
        out = lmc._dedupe_union(
            [("a/1", "A1"), ("a/2", "A2")],
            [("a/2", "dup"), ("a/3", "A3")],
        )
        self.assertEqual([m for m, _ in out], ["a/1", "a/2", "a/3"])
        self.assertEqual(dict(out)["a/2"], "A2")

    def test_order_by_seed_keeps_curated_rank_then_appends_fresh(self):
        seed = [("p/keep", "k"), ("p/first", "f")]
        live = [("p/new", "n"), ("p/first", "f"), ("p/keep", "k")]
        out = [m for m, _ in lmc._order_by_seed(live, seed)]
        self.assertEqual(out, ["p/keep", "p/first", "p/new"])

    def test_split_discovered_separates_new_upstream_models(self):
        seed = [("p/known", "k")]
        live = [("p/known", "k"), ("p/brand-new", "n")]
        known, fresh = lmc._split_discovered(live, seed)
        self.assertEqual([m for m, _ in known], ["p/known"])
        self.assertEqual([m for m, _ in fresh], ["p/brand-new"])

    def test_engine_ownership_filter_blocks_cross_engine_slugs(self):
        pairs = [("opencode-go/x", "a"), ("opencode/y", "b"), ("openrouter/z", "c")]
        oc = [m for m, _ in lmc._filter_by_engine("opencode", pairs)]
        self.assertEqual(oc, ["opencode-go/x", "opencode/y"])
        hermes = [m for m, _ in lmc._filter_by_engine("hermes", pairs)]
        self.assertEqual(hermes, ["openrouter/z"])

    def test_pretty_label_prefers_curated_then_derives(self):
        seed = [("opencode-go/known", "깔끔한 한글 라벨")]
        self.assertEqual(lmc._pretty_model_label("opencode-go/known", seed), "깔끔한 한글 라벨")
        derived = lmc._pretty_model_label("openrouter/acme/zeta-1", [])
        self.assertIn("Zeta 1", derived)
        self.assertIn("acme", derived)

    def test_pretty_label_flags_free_tier(self):
        self.assertIn("무과금", lmc._pretty_model_label("x/y-model:free", []))
        self.assertIn("free tier", lmc._pretty_model_label("opencode/y-model", []))

    def test_canonical_engine_aliases(self):
        self.assertEqual(lmc.canonical_engine("OpenRouter"), "hermes")
        self.assertEqual(lmc.canonical_engine("nous"), "hermes")
        self.assertEqual(lmc.canonical_engine("OpenCode"), "opencode")

    def test_degraded_response_keeps_curated_list(self):
        """live 가 curated 보다 축소되면 사용 가능한 모델을 숨기지 않는다."""
        seed = [("p/a", "a"), ("p/b", "b"), ("p/c", "c")]
        with mock.patch.dict(lmc._PROVIDERS, {"unit": lambda s: [("p/a", "a")]}), \
             mock.patch.object(lmc, "_seed_for", return_value=seed):
            lmc.LiveModelCatalog._refresh_sync("unit", force=True)
            try:
                entry = lmc.LiveModelCatalog._cache["unit"]
                self.assertFalse(entry["live"])
                self.assertIn("degraded", entry["source"])
                self.assertEqual(len(entry["models"]), 3)
            finally:
                lmc.LiveModelCatalog._cache.pop("unit", None)
                lmc._PROVIDERS.pop("unit", None)

    def test_curated_only_engine_never_claims_live(self):
        with mock.patch.object(lmc, "_CURATED_ONLY_ENGINES", {"claude"}):
            lmc.LiveModelCatalog._refresh_sync("claude", force=True)
            entry = lmc.LiveModelCatalog._cache.get("claude") or {}
            self.assertFalse(entry.get("live", False))
            self.assertIn("미제공", entry.get("source", ""))


class TestLiveModelCatalogProviders(unittest.TestCase):
    """프로바이더 계층 — CLI/HTTP 를 목으로 대체한다."""

    def setUp(self) -> None:
        lmc.LiveModelCatalog._cache.clear()
        lmc.LiveModelCatalog._refreshing.clear()

    def tearDown(self) -> None:
        lmc.LiveModelCatalog._cache.clear()
        lmc.LiveModelCatalog._refreshing.clear()

    def test_opencode_uses_router_cli_only(self):
        """[🎯 라우터 정합성] 라우터 CLI(`opencode models`)만이 authoritative.

        Zen Go API 는 라우터가 실제로 라우팅하지 않는 모델까지 반환하므로 무시한다.
        (실측: API 36개 vs 라우터 29개 → 7개 유령 모델이 `!model` 에 노출되던 버그)
        """
        seed = [("opencode-go/known", "Known")]
        cli_out = "opencode/space-bunny-free\nopencode/big-pickle\n"
        api_payload = {"data": [{"id": "known"}, {"id": "ghost-only-in-api"}]}

        with mock.patch.object(lmc, "_run_cli", return_value=cli_out), \
             mock.patch.object(lmc, "_http_json", return_value=api_payload), \
             mock.patch.object(lmc, "_resolve_binary", return_value="/bin/opencode"), \
             mock.patch.object(lmc, "_read_env_key", return_value="secret-key"):
            out = [m for m, _ in lmc._discover_opencode(seed)]

        # CLI 의 free 티어가 그대로 살아있고,
        self.assertIn("opencode/space-bunny-free", out)
        self.assertIn("opencode/big-pickle", out)
        # 라우터(CLI)에 없는 seed 전용/Zen API 전용 모델은 포함되지 않는다.
        self.assertNotIn("opencode-go/known", out)
        self.assertNotIn("opencode-go/ghost-only-in-api", out)

    def test_opencode_survives_total_source_failure(self):
        with mock.patch.object(lmc, "_run_cli", return_value=""), \
             mock.patch.object(lmc, "_http_json", return_value=None), \
             mock.patch.object(lmc, "_resolve_binary", return_value="/bin/opencode"), \
             mock.patch.object(lmc, "_read_env_key", return_value="k"):
            out = lmc._discover_opencode([("opencode-go/known", "Known")])
        self.assertEqual([m for m, _ in out], ["opencode-go/known"])

    def test_openrouter_filters_image_and_batch_variants(self):
        payload = {
            "data": [
                {"id": "vendor/chat", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
                {"id": "vendor/chat:batch", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
                {"id": "vendor/image", "architecture": {"input_modalities": ["image"], "output_modalities": ["image"]}},
                {"id": "vendor/embed", "architecture": {"input_modalities": ["text"], "output_modalities": ["embedding"]}},
            ]
        }
        with mock.patch.object(lmc, "_http_json", return_value=payload), \
             mock.patch.object(lmc, "_read_env_key", return_value="k"):
            out = [m for m, _ in lmc._discover_openrouter([])]
        self.assertEqual(out, ["vendor/chat"])

    def test_openrouter_without_key_falls_back_to_seed(self):
        with mock.patch.object(lmc, "_read_env_key", return_value=""):
            out = [m for m, _ in lmc._discover_openrouter([("openrouter/a", "A")])]
        self.assertEqual(out, ["openrouter/a"])

    def test_codex_parses_json_after_warning_banner(self):
        out_txt = 'WARNING: something\n{"models": [{"slug": "gpt-x", "display_name": "GPT X", "visibility": "list"}, {"slug": "hidden", "visibility": "hide"}]}'
        with mock.patch.object(lmc, "_run_cli", return_value=out_txt), \
             mock.patch.object(lmc, "_resolve_binary", return_value="/bin/codex"):
            out = [m for m, _ in lmc._discover_codex([])]
        self.assertEqual(out, ["gpt-x"])

    def test_provider_exception_never_breaks_refresh(self):
        def boom(_seed):
            raise RuntimeError("network down")

        with mock.patch.dict(lmc._PROVIDERS, {"boomengine": boom}):
            lmc.LiveModelCatalog._refresh_sync("boomengine", force=True)
            try:
                entry = lmc.LiveModelCatalog._cache["boomengine"]
                self.assertFalse(entry["live"])
                self.assertTrue(entry["error"])
            finally:
                lmc.LiveModelCatalog._cache.pop("boomengine", None)
                lmc._PROVIDERS.pop("boomengine", None)

    def test_get_models_is_non_blocking_on_cold_start(self):
        """콜드 스타트에서 즉시 반환하고 백그라운드 갱신을 예약해야 한다."""
        out = lmc.LiveModelCatalog.get_models("opencode")
        self.assertIsInstance(out, list)
        self.assertGreater(len(out), 0)  # curated seed 로도 비어있지 않아야 한다

    def test_get_status_exposes_new_model_metadata(self):
        lmc.LiveModelCatalog._cache["hermes"] = {
            "models": [("a", "A"), ("b", "B")],
            "discovered": [("b", "B")],
            "fetched_at": __import__("time").time(),
            "live": True,
            "source": "live (12ms)",
            "error": None,
        }
        st = lmc.LiveModelCatalog.get_status("hermes")
        self.assertEqual(st["count"], 2)
        self.assertEqual(st["new_count"], 1)
        self.assertTrue(st["live"])
        self.assertEqual([m for m, _ in lmc.LiveModelCatalog.get_new_models("hermes")], ["b"])
        del lmc.LiveModelCatalog._cache["hermes"]

    # -- last-known-good retention ---------------------------------------
    # 업스트림이 순간적으로 죽을 때 사용자가 고를 수 있던 모델이 사라지면
    # "자동화"가 오히려 사용성을 해친다. 직전 정상 스냅샷을 유지해야 한다.
    def _prime_good_snapshot(self, eng="opencode"):
        good = [("opencode-go/a", "A"), ("opencode-go/b", "B"), ("opencode-go/c", "C")]
        lmc.LiveModelCatalog._cache[eng] = {
            "models": list(good),
            "discovered": [("opencode-go/c", "C")],
            "fetched_at": __import__("time").time(),
            "live": True,
            "source": "live (10ms)",
            "error": None,
        }
        return good

    def test_lkg_survives_total_outage(self):
        good = self._prime_good_snapshot()
        with mock.patch.dict(lmc._PROVIDERS, {"opencode": lambda s: []}), \
             mock.patch.object(lmc, "_seed_for", return_value=[("p/seed", "s")]):
            lmc.LiveModelCatalog._refresh_sync("opencode", force=True)
            st = lmc.LiveModelCatalog.get_status("opencode")
        self.assertEqual([m for m, _ in lmc.LiveModelCatalog._cache["opencode"]["models"]], [m for m, _ in good])
        self.assertTrue(st["live"])
        self.assertIn("유지", st["source"])
        self.assertEqual(st["new_count"], 1)

    def test_lkg_survives_truncated_response(self):
        good = self._prime_good_snapshot()
        truncated = [("opencode-go/a", "A")]
        with mock.patch.dict(lmc._PROVIDERS, {"opencode": lambda s: truncated}), \
             mock.patch.object(lmc, "_seed_for", return_value=[("p/seed", "s")]):
            lmc.LiveModelCatalog._refresh_sync("opencode", force=True)
        self.assertEqual([m for m, _ in lmc.LiveModelCatalog._cache["opencode"]["models"]], [m for m, _ in good])

    def test_lkg_survives_provider_exception(self):
        good = self._prime_good_snapshot()

        def boom(_seed):
            raise RuntimeError("upstream 500")

        with mock.patch.dict(lmc._PROVIDERS, {"opencode": boom}), \
             mock.patch.object(lmc, "_seed_for", return_value=[("p/seed", "s")]):
            lmc.LiveModelCatalog._refresh_sync("opencode", force=True)
            st = lmc.LiveModelCatalog.get_status("opencode")
        self.assertEqual([m for m, _ in lmc.LiveModelCatalog._cache["opencode"]["models"]], [m for m, _ in good])
        self.assertEqual(st["error"], "upstream 500")
        self.assertTrue(st["live"])  # 예외가 났어도 사용자에게는 살아 있는 목록을 준다

    def test_lkg_not_used_when_live_is_healthy(self):
        """정상 갱신에서는 반드시 새 스냅샷으로 교체되어야 한다 (캐시가 굳지 않게)."""
        self._prime_good_snapshot()
        fresh = [("opencode-go/a", "A"), ("opencode-go/z", "Z")]
        with mock.patch.dict(lmc._PROVIDERS, {"opencode": lambda s: fresh}), \
             mock.patch.object(lmc, "_seed_for", return_value=[("p/seed", "s")]):
            lmc.LiveModelCatalog._refresh_sync("opencode", force=True)
        self.assertEqual(
            [m for m, _ in lmc.LiveModelCatalog._cache["opencode"]["models"]],
            ["opencode-go/a", "opencode-go/z"],
        )
        lmc.LiveModelCatalog._cache.pop("opencode", None)


class TestModelManagerPicker(unittest.TestCase):
    """`!model` 픽커가 실제로 무엇을 보여주는지 검증 (Discord 런타임 불필요)."""

    @classmethod
    def setUpClass(cls):
        import model_manager  # noqa: WPS433
        cls.mm = model_manager
        if not model_manager.GlobalModelManager._initialized:
            model_manager.GlobalModelManager.initialize()

    def _seed_cache(self, engine, models, discovered):
        lmc.LiveModelCatalog._cache[lmc.canonical_engine(engine)] = {
            "models": list(models),
            "discovered": list(discovered),
            "fetched_at": __import__("time").time(),
            "live": True,
            "source": "live (10ms)",
            "error": None,
        }

    def test_new_opencode_models_are_visible_in_dropdown(self):
        """핵심 회귀: curated 35개 뒤에 붙던 신규 9개가 25개 캡에 잘려 보이지 않던 버그."""
        curated = [(f"opencode-go/curated-{i}", f"Curated {i}") for i in range(35)]
        new = [
            ("opencode/space-bunny-free", "Space Bunny (free)"),
            ("opencode/nemotron-3-ultra-free", "Nemotron 3 Ultra (free)"),
            ("opencode-go/gpt-9-codex", "GPT-9 Codex"),
        ]
        self._seed_cache("opencode", curated + new, new)
        try:
            dd = self.mm.ModelPresetDropdown("coder")
            values = [o.value for o in dd.options]
            labels = {o.value: (o.description or "") for o in dd.options}
            self.assertLessEqual(len(values), 25)
            for mid, _ in new:
                self.assertIn(mid, values, f"신규 모델 {mid} 이(가) 드롭다운에 보이지 않음")
            self.assertIn("🆕", labels["opencode/space-bunny-free"])
            self.assertIn(f"전체 {len(curated) + len(new)}개", dd.placeholder)
        finally:
            lmc.LiveModelCatalog._cache.pop("opencode", None)

    def test_current_model_is_always_pinned_into_dropdown(self):
        curated = [(f"opencode-go/curated-{i}", f"Curated {i}") for i in range(40)]
        new = [(f"opencode-go/new-{i}", f"New {i}") for i in range(20)]
        self._seed_cache("opencode", curated + new, new)
        with mock.patch.object(
            self.mm.GlobalModelManager, "get_role_config",
            return_value={"engine": "opencode", "model": "opencode-go/curated-39", "enabled": True},
        ):
            try:
                dd = self.mm.ModelPresetDropdown("coder")
                self.assertIn("opencode-go/curated-39", [o.value for o in dd.options])
            finally:
                lmc.LiveModelCatalog._cache.pop("opencode", None)

    def test_dropdown_never_exceeds_discord_limit(self):
        curated = [(f"opencode-go/c{i}", f"C{i}") for i in range(100)]
        self._seed_cache("opencode", curated, [])
        try:
            dd = self.mm.ModelPresetDropdown("coder")
            self.assertLessEqual(len(dd.options), 25)
            # 나머지는 검색 진입점으로 도달 가능해야 한다.
            self.assertIn("__search__", [o.value for o in dd.options])
        finally:
            lmc.LiveModelCatalog._cache.pop("opencode", None)

    def test_dropdown_survives_empty_catalog(self):
        with mock.patch.object(self.mm, "get_engine_models", return_value=[]), \
             mock.patch.object(
                 self.mm.GlobalModelManager, "get_role_config",
                 return_value={"engine": "opencode", "model": "", "enabled": True},
             ):
            dd = self.mm.ModelPresetDropdown("coder")
            self.assertEqual(len(dd.options), 1)

    def test_search_result_view_paginates_beyond_25(self):
        results = [(f"v/model-{i}", f"Model {i}") for i in range(60)]
        view = self.mm.ModelSearchResultView("coder", "hermes", "model", results, 60)
        self.assertEqual(view.page_count, 3)
        selects = [c for c in view.children if type(c).__name__ == "ModelSearchResultSelect"]
        self.assertEqual(len(selects[0].options), 25)
        self.assertEqual([o.value for o in selects[0].options][0], "v/model-0")

        view.page = 2
        view._render()
        selects = [c for c in view.children if type(c).__name__ == "ModelSearchResultSelect"]
        self.assertEqual(len(selects[0].options), 10)

    def test_search_result_view_single_page_has_no_pager(self):
        view = self.mm.ModelSearchResultView("coder", "agy", "g", [("a", "A")], 1)
        self.assertEqual(view.page_count, 1)
        self.assertTrue(all(type(c).__name__ != "Button" for c in view.children))

    def test_search_result_view_empty_results_is_valid(self):
        view = self.mm.ModelSearchResultView("coder", "agy", "zzz", [], 0)
        self.assertEqual(view.page_count, 1)
        self.assertEqual(len(view.children), 0)

    def test_get_engine_models_delegates_to_live_catalog(self):
        cached = [("opencode-go/live-1", "Live 1")]
        with mock.patch.object(
            lmc.LiveModelCatalog, "get_models", return_value=cached
        ) as m:
            out = self.mm.get_engine_models("opencode")
        m.assert_called_once()
        self.assertEqual(out, cached)

    def test_get_engine_models_falls_back_when_catalog_module_missing(self):
        """live_model_catalog 를 못 불러도 curated ENGINE_PRESETS 로 비지 않는다."""
        with mock.patch.dict(sys.modules, {"live_model_catalog": None}):
            out = self.mm.get_engine_models("codex")
        self.assertTrue(out)
        self.assertEqual(out, list(self.mm.ENGINE_PRESETS["codex"]["models"]))

    def test_catalog_status_note_reports_new_models(self):
        self._seed_cache("opencode", [("a", "A"), ("b", "B")], [("b", "B")])
        try:
            note = self.mm.GlobalModelManager._build_catalog_status_note("opencode")
            self.assertIn("🆕신규 1", note)
            self.assertIn("자동갱신", note)
        finally:
            lmc.LiveModelCatalog._cache.pop("opencode", None)

    def test_catalog_status_note_never_raises(self):
        note = self.mm.GlobalModelManager._build_catalog_status_note("__no_such_engine__")
        self.assertIsInstance(note, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
