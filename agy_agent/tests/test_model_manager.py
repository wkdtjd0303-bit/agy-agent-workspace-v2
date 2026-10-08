#!/usr/bin/env python3
"""
Unit tests for GlobalModelManager, real-time dynamic model/engine configuration,
interactive UI views, and QA skipping logic.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
import model_manager
from model_manager import GlobalModelManager, ModelDashboardView, CustomModelModal


class TestGlobalModelManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        model_manager.GlobalModelManager.initialize()
        model_manager.GlobalModelManager.set_role_persona("qa2", "designer")

    def test_default_roles_exist(self):
        roles = GlobalModelManager.get_all_roles()
        self.assertIn("coder", roles)
        self.assertIn("qa1", roles)
        self.assertIn("qa2", roles)
        self.assertIn("qa3", roles)
        self.assertIn("qa4", roles)
        self.assertIn("qa5", roles)
        self.assertIn("qa6", roles)
        self.assertIn("auditor", roles)
        self.assertIn("image", roles)
        self.assertIn("vision", roles)
        self.assertIn("commander", roles)

    def test_qa_enable_disable_toggle(self):
        # 1. Enable QA2
        GlobalModelManager.set_role_enabled("qa2", True)
        self.assertTrue(GlobalModelManager.is_qa_enabled(2))
        active_ext = [q["role"] for q in GlobalModelManager.get_active_extended_qas()]
        self.assertIn("qa2", active_ext)

        # 2. Disable QA2
        GlobalModelManager.set_role_enabled("qa2", False)
        self.assertFalse(GlobalModelManager.is_qa_enabled(2))
        active_ext_disabled = [q["role"] for q in GlobalModelManager.get_active_extended_qas()]
        self.assertNotIn("qa2", active_ext_disabled)

        # Restore
        GlobalModelManager.set_role_enabled("qa2", True)

    def test_qa1_enable_disable_toggle(self):
        GlobalModelManager.set_role_enabled("qa1", False)
        self.assertFalse(GlobalModelManager.is_qa_enabled(1))
        
        GlobalModelManager.set_role_enabled("qa1", True)
        self.assertTrue(GlobalModelManager.is_qa_enabled(1))

    def test_bulk_qa_actions(self):
        # All QA on
        GlobalModelManager.set_all_qa_enabled(True)
        self.assertTrue(GlobalModelManager.is_qa_enabled(1))
        self.assertTrue(GlobalModelManager.is_qa_enabled(2))
        self.assertTrue(GlobalModelManager.is_qa_enabled(3))

        # Fast mode: Only QA1 ON
        GlobalModelManager.set_only_primary_qa()
        self.assertTrue(GlobalModelManager.is_qa_enabled(1))
        self.assertFalse(GlobalModelManager.is_qa_enabled(2))
        self.assertFalse(GlobalModelManager.is_qa_enabled(3))

        # Restore
        GlobalModelManager.set_all_qa_enabled(True)

    def test_set_engine_and_model(self):
        # Set QA2 engine and model
        GlobalModelManager.set_role_engine("qa2", "openrouter")
        GlobalModelManager.set_role_model("qa2", "upstage/solar-pro4")
        
        cfg = GlobalModelManager.get_role_config("qa2")
        self.assertEqual(cfg["engine"], "hermes")
        self.assertEqual(cfg["model"], "upstage/solar-pro4")

        # Set Coder model
        GlobalModelManager.set_role_model("coder", "gemini-3.8-flash-high")
        self.assertEqual(config.CODER_MODEL, "gemini-3.8-flash-high")

    def test_dashboard_embed_rendering(self):
        embed = GlobalModelManager.build_dashboard_embed(selected_role="qa2")
        self.assertIn("전역 AI 모델", embed.title)
        self.assertGreaterEqual(len(embed.fields), 3)

    async def test_modal_submission(self):
        parent_view = model_manager.ModelDashboardView(selected_role="qa2")
        modal = model_manager.CustomModelModal("qa2", "upstage/solar-pro4", parent_view)
        modal.model_input._value = "meta-llama/llama-3.3-70b-instruct"

        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.edit_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await modal.on_submit(mock_interaction)
        cfg = model_manager.GlobalModelManager.get_role_config("qa2")
        self.assertEqual(cfg["model"], "meta-llama/llama-3.3-70b-instruct")

        # Restore
        model_manager.GlobalModelManager.set_role_model("qa2", "upstage/solar-pro4")

    async def test_bot_model_command_shortcuts(self):
        import bot
        ctx = MagicMock()
        ctx.author.id = 12345
        ctx.send = AsyncMock()

        # 1. Open dashboard
        await bot.model_command(ctx)
        ctx.send.assert_called()
        view_obj = ctx.send.call_args[1].get("view")
        self.assertEqual(view_obj.__class__.__name__, "ModelDashboardView")

        # 2. Toggle QA2 OFF via CLI shortcut
        await bot.model_command(ctx, "qa2", "off")
        self.assertFalse(model_manager.GlobalModelManager.is_qa_enabled(2))

        # 3. Toggle QA2 ON via CLI shortcut
        await bot.model_command(ctx, "qa2", "on")
        self.assertTrue(model_manager.GlobalModelManager.is_qa_enabled(2))

        # 4. Set QA2 model via CLI shortcut
        await bot.model_command(ctx, "qa2", "openrouter", "upstage/solar-pro4")
        cfg = model_manager.GlobalModelManager.get_role_config("qa2")
        self.assertEqual(cfg["engine"], "hermes")
        self.assertEqual(cfg["model"], "upstage/solar-pro4")

        # 5. Set QA2 persona via CLI shortcut: !model qa2 persona security
        await bot.model_command(ctx, "qa2", "persona", "security")
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa2"), "security")

        # 6. Set QA3 persona via Korean shortcut: !model qa3 직업 카오스
        await bot.model_command(ctx, "qa3", "직업", "카오스")
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa3"), "chaos")

        # 7. Direct top-level persona command: !model persona qa2 designer
        await bot.model_command(ctx, "persona", "qa2", "designer")
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa2"), "designer")

    def test_get_engine_models_dynamic_category_sorting_and_catalog(self):
        from model_manager import get_engine_models
        models = get_engine_models("agy")
        model_ids = [m[0] for m in models]

        # Verify key models in 14-model catalog exist
        expected_ids = [
            "gemini-3.8-flash-high",
            "gemini-3.7-flash-high",
            "gemini-3.1-pro-high",
            "gpt-oss-120b-medium",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
        ]
        for mid in expected_ids:
            self.assertIn(mid, model_ids, f"Model {mid} should be present in AGY catalog")

        # Verify category sorting locks flagship, gpt-oss, and claude near top (first 7 items)
        top_ids = model_ids[:7]
        self.assertIn("gemini-3.8-flash-high", top_ids)
        self.assertIn("gpt-oss-120b-medium", top_ids)
        self.assertIn("claude-sonnet-4-6", top_ids)

    async def test_agent_runner_openrouter_direct_dispatch_no_gemini_fallback(self):
        from agent_runner import AgentRunner
        from engine_manager import HermesAdapter, EngineManager
        session_mock = MagicMock()
        session_mock.workspace = "/fake/workspace"
        session_mock.role = "coder"
        session_mock.conversation_id = "test-conv"

        with patch.object(HermesAdapter, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = (0, "Direct Hermes Response", [])
            
            # Execute with an openrouter model
            code, resp, last_err, all_err = await AgentRunner._execute_single_run(
                session=session_mock,
                prompt="test prompt",
                model="openrouter/upstage/solar-pro4"
            )

            self.assertEqual(code, 0)
            self.assertEqual(resp, "Direct Hermes Response")
            mock_exec.assert_awaited_once()
            call_kwargs = mock_exec.call_args[1]
            self.assertEqual(call_kwargs.get("model"), "openrouter/upstage/solar-pro4")

    def test_normalize_engine_id_openrouter_unification(self):
        from engine_manager import normalize_engine_id
        self.assertEqual(normalize_engine_id("openrouter"), "hermes")
        self.assertEqual(normalize_engine_id("aider"), "hermes")
        self.assertEqual(normalize_engine_id("nous"), "hermes")
        self.assertEqual(normalize_engine_id("hermes-agent"), "hermes")
        self.assertEqual(normalize_engine_id("agy"), "agy")
        self.assertEqual(normalize_engine_id("codex"), "codex")
        self.assertEqual(normalize_engine_id("copilot"), "copilot")
        self.assertEqual(normalize_engine_id("claude"), "claude")

    async def test_role_select_dropdown_callback_defers_and_safe_edits(self):
        """Ensures selecting a bot in !model immediately defers to avoid Discord 3s timeout."""
        view = model_manager.ModelDashboardView(selected_role="qa2")
        role_dropdown = [c for c in view.children if isinstance(c, model_manager.RoleSelectDropdown)][0]
        role_dropdown._values = ["coder"]

        mock_interaction = AsyncMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.is_done = MagicMock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.edit_original_response = AsyncMock()
        mock_interaction.response.edit_message = AsyncMock()

        await role_dropdown.callback(mock_interaction)
        mock_interaction.response.defer.assert_awaited_once()
        self.assertEqual(view.selected_role, "coder")

    async def test_engine_and_model_dropdown_callbacks_defer(self):
        """Ensures router and model preset dropdowns also defer immediately."""
        view = model_manager.ModelDashboardView(selected_role="coder")
        
        # Engine dropdown
        engine_dropdown = [c for c in view.children if isinstance(c, model_manager.EngineSelectDropdown)][0]
        engine_dropdown._values = ["hermes"]
        mock_interaction = AsyncMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.is_done = MagicMock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.edit_original_response = AsyncMock()
        mock_interaction.response.edit_message = AsyncMock()
        await engine_dropdown.callback(mock_interaction)
        mock_interaction.response.defer.assert_awaited_once()

        # Model preset dropdown
        preset_dropdown = [c for c in view.children if isinstance(c, model_manager.ModelPresetDropdown)][0]
        preset_dropdown._values = [preset_dropdown.options[0].value]
        mock_interaction2 = AsyncMock()
        mock_interaction2.response = MagicMock()
        mock_interaction2.response.is_done = MagicMock(return_value=False)
        mock_interaction2.response.defer = AsyncMock()
        mock_interaction2.edit_original_response = AsyncMock()
        mock_interaction2.response.edit_message = AsyncMock()
        await preset_dropdown.callback(mock_interaction2)
        mock_interaction2.response.defer.assert_awaited_once()

    def test_model_registry_non_blocking_instant_return(self):
        """Ensures get_selectable_models returns within milliseconds from cached memory."""
        import time
        from model_registry import AntigravityModelRegistry
        t0 = time.time()
        models = AntigravityModelRegistry.get_selectable_models()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.05, f"get_selectable_models must be non-blocking (<50ms), took {elapsed:.4f}s")
        self.assertTrue(len(models) > 0)

    def test_qa_personas_exist_and_defaults(self):
        """Ensures all 6 QA personas are registered and assigned to roles."""
        self.assertIn("general", model_manager.QA_PERSONAS)
        self.assertIn("designer", model_manager.QA_PERSONAS)
        self.assertIn("security", model_manager.QA_PERSONAS)
        self.assertIn("chaos", model_manager.QA_PERSONAS)
        self.assertIn("sre", model_manager.QA_PERSONAS)
        self.assertIn("architect", model_manager.QA_PERSONAS)

        p2 = model_manager.GlobalModelManager.get_role_persona("qa2")
        self.assertEqual(p2, "designer")
        info2 = model_manager.GlobalModelManager.get_role_persona_info("qa2")
        self.assertIn("디자이너", info2["name"])

    def test_set_role_persona_with_aliases(self):
        """Tests changing persona using direct ID and Korean aliases."""
        # 1. Alias: '보안' -> 'security'
        ok = model_manager.GlobalModelManager.set_role_persona("qa2", "보안")
        self.assertTrue(ok)
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa2"), "security")

        # 2. Alias: '카오스' -> 'chaos'
        ok = model_manager.GlobalModelManager.set_role_persona("qa2", "카오스")
        self.assertTrue(ok)
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa2"), "chaos")

        # 3. Direct: 'designer'
        model_manager.GlobalModelManager.set_role_persona("qa2", "designer")
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa2"), "designer")

    async def test_persona_select_dropdown_callback_defers(self):
        """Ensures PersonaSelectDropdown exists in view and defers immediately."""
        model_manager.GlobalModelManager.set_role_persona("qa2", "designer")
        view = model_manager.ModelDashboardView(selected_role="qa2")
        persona_dropdowns = [c for c in view.children if isinstance(c, model_manager.PersonaSelectDropdown)]
        self.assertEqual(len(persona_dropdowns), 1)
        p_dd = persona_dropdowns[0]
        self.assertEqual(len(p_dd.options), 6)

        p_dd._values = ["security"]
        mock_interaction = AsyncMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.is_done = MagicMock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.edit_original_response = AsyncMock()
        mock_interaction.response.edit_message = AsyncMock()

        await p_dd.callback(mock_interaction)
        mock_interaction.response.defer.assert_awaited_once()
        self.assertEqual(model_manager.GlobalModelManager.get_role_persona("qa2"), "security")

        # Restore
        model_manager.GlobalModelManager.set_role_persona("qa2", "designer")

    def test_active_extended_qas_includes_persona(self):
        """Ensures get_active_extended_qas returns persona field."""
        active_qas = model_manager.GlobalModelManager.get_active_extended_qas()
        qa2_entry = [q for q in active_qas if q["role"] == "qa2"]
        self.assertTrue(len(qa2_entry) > 0)
        self.assertIn("persona", qa2_entry[0])
        self.assertIn("persona_name", qa2_entry[0])

    def test_model_dashboard_view_all_roles_row_weights_within_limits(self):
        """Ensures ModelDashboardView complies with Discord 5-row and 5-width limits across all roles."""
        roles = model_manager.GlobalModelManager.get_all_roles()
        for role in roles:
            view = model_manager.ModelDashboardView(selected_role=role)
            row_widths = [0] * 5
            for item in view.children:
                w = getattr(item, "width", 1)
                r = getattr(item, "row", 0) or 0
                row_widths[r] += w
            for row_idx, total_w in enumerate(row_widths):
                self.assertLessEqual(
                    total_w,
                    5,
                    f"Row {row_idx} exceeded width 5 (weight: {total_w}) for role '{role}'"
                )

    async def test_search_callbacks_trigger_search_modal(self):
        """Ensures both search button and preset dropdown __search__ value open ModelSearchModal."""
        view = model_manager.ModelDashboardView(selected_role="coder")
        btn_search = [c for c in view.children if getattr(c, "custom_id", None) == "btn_model_search"][0]

        mock_interaction = AsyncMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.send_modal = AsyncMock()

        await btn_search.callback(mock_interaction)
        mock_interaction.response.send_modal.assert_awaited_once()
        modal_arg = mock_interaction.response.send_modal.call_args[0][0]
        self.assertIsInstance(modal_arg, model_manager.ModelSearchModal)

        # Dropdown __search__
        preset_dropdown = [c for c in view.children if isinstance(c, model_manager.ModelPresetDropdown)][0]
        preset_dropdown._values = ["__search__"]
        mock_interaction2 = AsyncMock()
        mock_interaction2.response = MagicMock()
        mock_interaction2.response.send_modal = AsyncMock()

        await preset_dropdown.callback(mock_interaction2)
        mock_interaction2.response.send_modal.assert_awaited_once()
        modal_arg2 = mock_interaction2.response.send_modal.call_args[0][0]
        self.assertIsInstance(modal_arg2, model_manager.ModelSearchModal)

    def test_qwen_flash_vs_plus_model_name_formatting(self):
        """Verifies that Qwen 3.7 Flash and Plus are distinctly and accurately formatted."""
        self.assertEqual(GlobalModelManager.format_model_name("qwen/qwen3.7-flash"), "Qwen 3.7 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("qwen3.7-flash"), "Qwen 3.7 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("opencode-go/qwen3.7-flash"), "Qwen 3.7 Flash")
        self.assertEqual(GlobalModelManager.format_model_name("qwen/qwen3.7-plus"), "Qwen 3.7 Plus")
        self.assertEqual(GlobalModelManager.format_model_name("qwen3.7-plus"), "Qwen 3.7 Plus")
        self.assertEqual(GlobalModelManager.format_model_name("qwen/qwen3.7-max"), "Qwen 3.7 Max")
        self.assertEqual(GlobalModelManager.format_model_name("qwen/qwen-2.5-coder-32b-instruct"), "Qwen 2.5 Coder 32B")

    def test_model_cost_tier_ordering(self):
        """Verifies that free/flash models have lower tier numbers than pro/max/opus models."""
        free_tier, _ = model_manager.get_model_cost_tier("poolside/laguna-s-2.1-free")
        flash_tier, _ = model_manager.get_model_cost_tier("qwen/qwen3.7-flash")
        plus_tier, _ = model_manager.get_model_cost_tier("qwen/qwen3.7-plus")
        pro_tier, _ = model_manager.get_model_cost_tier("deepseek/deepseek-v4-pro")
        max_tier, _ = model_manager.get_model_cost_tier("qwen/qwen3.8-max")
        opus_tier, _ = model_manager.get_model_cost_tier("claude-opus-5-5")

        self.assertEqual(free_tier, 0)
        self.assertLessEqual(flash_tier, 2)
        self.assertLess(flash_tier, plus_tier)
        self.assertLess(plus_tier, pro_tier)
        self.assertLess(pro_tier, max_tier)
        self.assertEqual(opus_tier, 5)

    async def test_model_preset_dropdown_cheapest_first_and_paging(self):
        """Verifies that preset dropdown orders cheaper models first and supports in-place paging."""
        GlobalModelManager.set_role_engine("coder", "cmdc")
        view = model_manager.ModelDashboardView(selected_role="coder")
        preset_dropdown = [c for c in view.children if isinstance(c, model_manager.ModelPresetDropdown)][0]

        opt_values = [o.value for o in preset_dropdown.options]
        self.assertTrue(any("flash" in v for v in opt_values), "Flash models should be present on page 0")

        page_options = [o.value for o in preset_dropdown.options if o.value.startswith("__page_")]
        if page_options:
            target_page_val = page_options[0]
            preset_dropdown._values = [target_page_val]
            mock_interaction = AsyncMock()
            mock_interaction.response = MagicMock()
            mock_interaction.response.is_done = MagicMock(return_value=False)
            mock_interaction.response.defer = AsyncMock()
            mock_interaction.edit_original_response = AsyncMock()
            mock_interaction.response.edit_message = AsyncMock()

            await preset_dropdown.callback(mock_interaction)
            self.assertEqual(view.model_page, 1)

    async def test_search_result_select_updates_parent_and_edits_inplace(self):
        """Verifies that selecting a model from search updates parent dashboard and edits in-place."""
        parent_view = model_manager.ModelDashboardView(selected_role="coder")
        parent_view.message = AsyncMock()

        search_select = model_manager.ModelSearchResultSelect(
            selected_role="coder",
            engine="cmdc",
            results=[("qwen/qwen3.7-flash", "Qwen 3.7 Flash")],
            parent_view=parent_view,
        )
        search_select._values = ["qwen/qwen3.7-flash"]

        mock_interaction = AsyncMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.is_done = MagicMock(return_value=False)
        mock_interaction.response.edit_message = AsyncMock()

        await search_select.callback(mock_interaction)

        parent_view.message.edit.assert_awaited_once()
        mock_interaction.response.edit_message.assert_awaited_once()
        self.assertEqual(GlobalModelManager.get_role_model("coder"), "qwen/qwen3.7-flash")


if __name__ == "__main__":
    unittest.main()
