import unittest
import asyncio
from unittest.mock import patch, MagicMock

import sys
import os

_submodule_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _submodule_dir not in sys.path:
    sys.path.insert(0, _submodule_dir)

import config
from session_manager import ChannelSession
from mode_classifier import classify_mode, get_mode_description
from commander import Commander


class TestDynamicMode2Count(unittest.TestCase):
    """
    Tests dynamic calculation of agent count for !mode 2+ (pair_quad)
    across 3중 (0 extended QAs), 4중 (1 extended QA), 5중 (2 extended QAs),
    7중 (4 extended QAs), and 8중 (5 extended QAs).
    """

    def setUp(self):
        self.mock_all_5_qas = [
            {"index": 2, "name": "qa2", "model": "upstage/solar-pro4", "engine": "hermes"},
            {"index": 3, "name": "qa3", "model": "z-ai/glm-5.3-flash", "engine": "hermes"},
            {"index": 4, "name": "qa4", "model": "openai/gpt-oss-120b", "engine": "hermes"},
            {"index": 5, "name": "qa5", "model": "meta-llama/llama-3.3-70b-instruct", "engine": "hermes"},
            {"index": 6, "name": "qa6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "hermes"},
        ]

    def test_0_extended_qas_yields_3_agent_trio(self):
        """When all extended QAs are disabled in !model, !mode 2+ dynamically becomes 3중 협업."""
        session = ChannelSession(channel_id=1001, workspace="/tmp")
        session.set_mode("2+")
        
        with patch.object(session, "get_active_extended_qas", return_value=[]):
            with patch("config.get_extended_qa_configs", return_value=[]):
                # 1. Mode Description
                desc = get_mode_description("2+", session, selected_qas=[])
                self.assertIn("3중 에이전트 협업 모드", desc)
                self.assertIn("Coder ➔ qa1 ➔ Auditor", desc)
                
                # 2. Mode Classifier
                mode, reason = classify_mode("투두앱 만들어줘", session)
                self.assertEqual(mode, "pair_quad")
                self.assertIn("3중", reason)

                # 3. Commander intent analysis
                decision = asyncio.run(Commander.analyze_intent("투두앱 만들어줘", "2+"))
                self.assertEqual(decision.mode, "pair_quad")
                self.assertIn("3중", decision.reason)
                self.assertIn("3중", decision.briefing)

    def test_1_extended_qa_yields_4_agent_quad(self):
        """When 1 extended QA is active (e.g. only QA2 Solar), !mode 2+ dynamically becomes 4중 협업."""
        session = ChannelSession(channel_id=1002, workspace="/tmp")
        session.set_mode("2+")
        active_1 = [self.mock_all_5_qas[0]] # QA2 only
        
        with patch.object(session, "get_active_extended_qas", return_value=active_1):
            with patch("config.get_extended_qa_configs", return_value=active_1):
                desc = get_mode_description("2+", session, selected_qas=active_1)
                self.assertIn("4중 쿼드 에이전트 협업 모드", desc)
                self.assertIn("qa2", desc)
                
                mode, reason = classify_mode("투두앱 만들어줘", session)
                self.assertEqual(mode, "pair_quad")
                self.assertIn("4중", reason)

                decision = asyncio.run(Commander.analyze_intent("투두앱 만들어줘", "2+"))
                self.assertEqual(decision.mode, "pair_quad")
                self.assertIn("4중", decision.reason)

    def test_2_extended_qas_yields_5_agent_collab(self):
        """When 2 extended QAs are active (e.g. QA2, QA3), !mode 2+ dynamically becomes 5중 다중 협업."""
        session = ChannelSession(channel_id=1003, workspace="/tmp")
        session.set_mode("2+")
        active_2 = self.mock_all_5_qas[:2]
        
        with patch.object(session, "get_active_extended_qas", return_value=active_2):
            with patch("config.get_extended_qa_configs", return_value=active_2):
                desc = get_mode_description("2+", session, selected_qas=active_2)
                self.assertIn("5중 다중 에이전트 협업 모드", desc)
                
                mode, reason = classify_mode("투두앱 만들어줘", session)
                self.assertEqual(mode, "pair_quad")
                self.assertIn("5중 다중", reason)

                decision = asyncio.run(Commander.analyze_intent("투두앱 만들어줘", "2+"))
                self.assertEqual(decision.mode, "pair_quad")
                self.assertIn("5중 다중", decision.reason)

    def test_4_extended_qas_yields_7_agent_collab(self):
        """When 4 extended QAs are active (QA2, QA3, QA4, QA6), !mode 2+ dynamically becomes 7중 다중 협업."""
        session = ChannelSession(channel_id=1004, workspace="/tmp")
        session.set_mode("2+")
        active_4 = [self.mock_all_5_qas[0], self.mock_all_5_qas[1], self.mock_all_5_qas[2], self.mock_all_5_qas[4]]
        
        with patch.object(session, "get_active_extended_qas", return_value=active_4):
            with patch("config.get_extended_qa_configs", return_value=active_4):
                desc = get_mode_description("2+", session, selected_qas=active_4)
                self.assertIn("7중 다중 에이전트 협업 모드", desc)
                
                mode, reason = classify_mode("투두앱 만들어줘", session)
                self.assertEqual(mode, "pair_quad")
                self.assertIn("7중 다중", reason)

                decision = asyncio.run(Commander.analyze_intent("투두앱 만들어줘", "2+"))
                self.assertEqual(decision.mode, "pair_quad")
                self.assertIn("7중 다중", decision.reason)

    def test_5_extended_qas_yields_8_agent_collab(self):
        """When all 5 extended QAs are active (QA2..QA6), !mode 2+ dynamically becomes 8중 다중 협업."""
        session = ChannelSession(channel_id=1005, workspace="/tmp")
        session.set_mode("2+")
        
        with patch.object(session, "get_active_extended_qas", return_value=self.mock_all_5_qas):
            with patch("config.get_extended_qa_configs", return_value=self.mock_all_5_qas):
                desc = get_mode_description("2+", session, selected_qas=self.mock_all_5_qas)
                self.assertIn("8중 다중 에이전트 협업 모드", desc)
                
                mode, reason = classify_mode("투두앱 만들어줘", session)
                self.assertEqual(mode, "pair_quad")
                self.assertIn("8중 다중", reason)

                decision = asyncio.run(Commander.analyze_intent("투두앱 만들어줘", "2+"))
                self.assertEqual(decision.mode, "pair_quad")
                self.assertIn("8중 다중", decision.reason)

    def test_commander_dispatch_qa_squad_mode2_engages_all_active_qas(self):
        """Commander.dispatch_qa_squad must engage all active QAs when mode is pair_quad."""
        plan = Commander.dispatch_qa_squad(
            prompt="새로운 쇼핑몰 결제 모듈 작성해줘",
            diff_info=None,
            available_extended_qas=self.mock_all_5_qas,
            mode="pair_quad"
        )
        self.assertEqual(len(plan["selected_qas"]), 5)
        self.assertEqual(plan["selected_indices"], [2, 3, 4, 5, 6])
        self.assertIn("!mode 2+ 다중 협업 모드: 활성화된 5개 QA 전체 연계", plan["strategy_desc"])


if __name__ == "__main__":
    unittest.main()
