import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import sys
import tempfile
import shutil
import asyncio

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from session_manager import ChannelSession, SessionManager
import bot as bot_module
from mode_classifier import classify_mode, get_mode_description
from commander import Commander


class TestMode2VsMode3Differentiation(unittest.IsolatedAsyncioTestCase):
    """
    Validates the architectural and operational separation between:
    - Mode 2 (!mode 2, !mode 2+): User-driven (Human-in-the-loop, halts for user input after 1 turn)
    - Mode 3 (!mode 3, !mode 3+): Fully Autonomous Closed-Loop (Commander macro planning, 5 pillars, auto-advances milestones)
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_mode2_mode3_")
        self.session = ChannelSession(channel_id=77778888, workspace=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_session_set_mode_distinct_separation(self):
        # 1. Mode 2 -> 'pair' (Human-in-the-loop 3-Agent)
        self.session.set_mode("2")
        self.assertEqual(self.session.mode, "pair")
        self.session.set_mode("pair")
        self.assertEqual(self.session.mode, "pair")

        # 2. Mode 3 -> 'mode3' (Autonomous Closed-Loop 3-Agent)
        self.session.set_mode("3")
        self.assertEqual(self.session.mode, "mode3")
        self.session.set_mode("mode3")
        self.assertEqual(self.session.mode, "mode3")
        self.session.set_mode("자율3")
        self.assertEqual(self.session.mode, "mode3")

        # 3. Mode 2+ -> 'pair_quad' (Human-in-the-loop Multi-QA)
        for alias_2 in ["2+", "2plus", "2플러스", "pair+", "pair_quad"]:
            self.session.set_mode(alias_2)
            self.assertEqual(self.session.mode, "pair_quad", f"Failed for alias {alias_2}")

        # 4. Mode 3+ -> 'mode3_plus' (Autonomous Closed-Loop Multi-QA)
        for alias_3 in ["3+", "3plus", "3플러스", "mode3+", "mode3_plus", "자율3+"]:
            self.session.set_mode(alias_3)
            self.assertEqual(self.session.mode, "mode3_plus", f"Failed for alias {alias_3}")

    def test_classify_mode_differentiation(self):
        prompt = "React 가계부 웹앱 구현하고 테스트 작성해줘"

        # Mode 2 vs Mode 3
        eff_mode_2, reason_2 = classify_mode(prompt, session_mode="2")
        eff_mode_3, reason_3 = classify_mode(prompt, session_mode="3")
        self.assertEqual(eff_mode_2, "pair")
        self.assertEqual(eff_mode_3, "mode3")
        self.assertIn("사용자 주도", reason_2)
        self.assertIn("무인 자율 순환", reason_3)

        # Mode 2+ vs Mode 3+
        eff_mode_2p, reason_2p = classify_mode(prompt, session_mode="2+")
        eff_mode_3p, reason_3p = classify_mode(prompt, session_mode="3+")
        self.assertEqual(eff_mode_2p, "pair_quad")
        self.assertEqual(eff_mode_3p, "mode3_plus")
        self.assertIn("!mode 2+", reason_2p)
        self.assertIn("!mode 3+", reason_3p)

    async def test_commander_analyze_intent_differentiation(self):
        prompt = "FastAPI 백엔드 CRUD API 엔드포인트 구현해줘"

        decision_2 = await Commander.analyze_intent(prompt, session_mode="2")
        decision_3 = await Commander.analyze_intent(prompt, session_mode="3")
        self.assertEqual(decision_2.mode, "pair")
        self.assertEqual(decision_3.mode, "mode3")

        decision_2p = await Commander.analyze_intent(prompt, session_mode="2+")
        decision_3p = await Commander.analyze_intent(prompt, session_mode="3+")
        self.assertEqual(decision_2p.mode, "pair_quad")
        self.assertEqual(decision_3p.mode, "mode3_plus")

    def test_commander_create_autonomous_product_plan_5_pillars(self):
        prompt = "반려동물 건강 관리 및 백신 접종 기록 관리 모바일 웹앱 만들어줘"
        plan = Commander.create_autonomous_product_plan(prompt, self.test_dir)

        self.assertIn("product_def", plan)
        self.assertIn("initial_directive", plan)
        p_def = plan["product_def"]

        # 5 Macro Pillars Verification
        # 1. Look & Feel / Design vibe
        self.assertTrue(len(p_def.design_vibe) > 0)
        # 2. App naming / Identity
        self.assertTrue(len(p_def.app_naming) > 0)
        # 3. Target demographic
        self.assertTrue(len(p_def.target_demographic) > 0)
        # 4. Monetization & Store launch
        self.assertTrue(len(p_def.monetization_strategy) > 0)
        # 5. Phasing & Milestone structure
        self.assertTrue(os.path.isfile(os.path.join(self.test_dir, "PRD.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.test_dir, "ROADMAP.md")))

        with open(os.path.join(self.test_dir, "ROADMAP.md"), "r", encoding="utf-8") as f:
            roadmap_content = f.read()
        self.assertIn("v1.0", roadmap_content)
        self.assertIn("v2.0", roadmap_content)
        self.assertIn("v3.0", roadmap_content)

    async def test_mode_command_discord_execution(self):
        mock_ctx = MagicMock()
        mock_ctx.send = AsyncMock()
        mock_ctx.channel = MagicMock()
        mock_ctx.channel.id = 55556666
        mock_ctx.channel.category_id = None
        mock_ctx.author = MagicMock()
        mock_ctx.author.id = 12345

        # Test !mode 2
        await bot_module.mode_command(mock_ctx, mode_str="2")
        sess = bot_module.session_manager.get_session(55556666)
        self.assertEqual(sess.mode, "pair")
        sent_msg = mock_ctx.send.call_args[0][0]
        self.assertIn("사용자 주도", sent_msg)

        # Test !mode 3
        await bot_module.mode_command(mock_ctx, mode_str="3")
        sess = bot_module.session_manager.get_session(55556666)
        self.assertEqual(sess.mode, "mode3")
        sent_msg = mock_ctx.send.call_args[0][0]
        self.assertIn("무인 자율 순환", sent_msg)

        # Test !mode 2+
        await bot_module.mode_command(mock_ctx, mode_str="2+")
        sess = bot_module.session_manager.get_session(55556666)
        self.assertEqual(sess.mode, "pair_quad")
        sent_msg = mock_ctx.send.call_args[0][0]
        self.assertIn("사용자 주도", sent_msg)

        # Test !mode 3+
        await bot_module.mode_command(mock_ctx, mode_str="3+")
        sess = bot_module.session_manager.get_session(55556666)
        self.assertEqual(sess.mode, "mode3_plus")
        sent_msg = mock_ctx.send.call_args[0][0]
        self.assertIn("무인 자율 순환", sent_msg)

    def test_command_aliases_in_bot(self):
        pair_cmd = bot_module.bot.get_command("pair")
        self.assertIsNotNone(pair_cmd)
        self.assertIn("2", pair_cmd.aliases)
        self.assertNotIn("3", pair_cmd.aliases)

        pair_plus_cmd = bot_module.bot.get_command("pair+")
        self.assertIsNotNone(pair_plus_cmd)
        self.assertIn("2+", pair_plus_cmd.aliases)
        self.assertNotIn("3+", pair_plus_cmd.aliases)

        mode3_cmd = bot_module.bot.get_command("mode3")
        self.assertIsNotNone(mode3_cmd)
        self.assertIn("3", mode3_cmd.aliases)
        self.assertIn("자율3", mode3_cmd.aliases)

        mode3_plus_cmd = bot_module.bot.get_command("mode3+")
        self.assertIsNotNone(mode3_plus_cmd)
        self.assertIn("3+", mode3_plus_cmd.aliases)
        self.assertIn("3plus", mode3_plus_cmd.aliases)
        self.assertIn("mode3_plus", mode3_plus_cmd.aliases)


if __name__ == "__main__":
    unittest.main()
