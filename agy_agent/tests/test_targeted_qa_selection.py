import os
import sys
import unittest
from unittest.mock import patch, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from session_manager import ChannelSession
from mode_classifier import get_mode_description, classify_mode
from commander import Commander


class TestTargetedQASelection(unittest.TestCase):
    def setUp(self):
        self.mock_configs = [
            {"index": 2, "role": "qa2", "name": "qa2", "model": "upstage/solar-pro4", "engine": "openrouter"},
            {"index": 3, "role": "qa3", "name": "qa3", "model": "deepseek/deepseek-v4-flash-0731", "engine": "hermes"},
            {"index": 4, "role": "qa4", "name": "qa4", "model": "qwen/qwen3-235b-a22b-2507", "engine": "hermes"},
            {"index": 5, "role": "qa5", "name": "qa5", "model": "meta-llama/llama-3.3-70b-instruct", "engine": "hermes"},
            {"index": 6, "role": "qa6", "name": "qa6", "model": "inclusionai/ling-3.0-flash-fin:free", "engine": "hermes"},
        ]

    def test_parse_qa_selection_single(self):
        # By index number
        indices, cfgs, label = config.parse_qa_selection("2", self.mock_configs)
        self.assertEqual(indices, [2])
        self.assertEqual(len(cfgs), 1)
        self.assertEqual(cfgs[0]["model"], "upstage/solar-pro4")

        # By role string
        indices, cfgs, label = config.parse_qa_selection("qa2", self.mock_configs)
        self.assertEqual(indices, [2])

        # By model keyword
        indices, cfgs, label = config.parse_qa_selection("solar", self.mock_configs)
        self.assertEqual(indices, [2])
        self.assertIn("solar-pro4", label)

    def test_parse_qa_selection_multiple(self):
        # Comma separated
        indices, cfgs, label = config.parse_qa_selection("2,4", self.mock_configs)
        self.assertEqual(indices, [2, 4])
        self.assertEqual(len(cfgs), 2)

        # Keyword mixed
        indices, cfgs, label = config.parse_qa_selection("solar, qwen", self.mock_configs)
        self.assertEqual(indices, [2, 4])

    def test_parse_qa_selection_reset_and_all(self):
        for kw in ("all", "전체", "reset", "초기화", ""):
            indices, cfgs, label = config.parse_qa_selection(kw, self.mock_configs)
            self.assertIsNone(indices)
            self.assertEqual(len(cfgs), len(self.mock_configs))

    def test_session_filtering(self):
        session = ChannelSession(channel_id=999, workspace="/tmp")
        session.set_mode("2+")
        self.assertEqual(session.mode, "pair_quad")

        # By default, all are active
        active_all = session.get_active_extended_qas(self.mock_configs)
        self.assertEqual(len(active_all), 5)

        # Select only QA2
        session.set_selected_qa_indices([2])
        self.assertEqual(session.selected_qa_indices, [2])
        active_filtered = session.get_active_extended_qas(self.mock_configs)
        self.assertEqual(len(active_filtered), 1)
        self.assertEqual(active_filtered[0]["name"].lower(), "qa2")
        self.assertEqual(active_filtered[0]["model"], "upstage/solar-pro4")

        # Reset to all
        session.set_selected_qa_indices(None)
        self.assertIsNone(session.selected_qa_indices)
        self.assertEqual(len(session.get_active_extended_qas(self.mock_configs)), 5)

    def test_mode_description_with_session_qa_selection(self):
        session = ChannelSession(channel_id=999, workspace="/tmp")
        session.set_mode("2+")

        # Full squad description
        full_desc = get_mode_description("2+", session, selected_qas=self.mock_configs)
        self.assertIn("8중 다중 에이전트 협업 모드", full_desc)
        self.assertIn("qa2", full_desc)
        self.assertIn("qa6", full_desc)

        # Filtered to QA2 only
        session.set_selected_qa_indices([2])
        qa2_desc = get_mode_description("2+", session, selected_qas=[self.mock_configs[0]])
        self.assertIn("4중 쿼드 에이전트 협업 모드", qa2_desc)
        self.assertIn("qa2", qa2_desc)
        self.assertNotIn("qa3", qa2_desc)
        self.assertNotIn("qa6", qa2_desc)

    def test_commander_dispatch_natural_language_filter(self):
        # Prompt explicitly stating wanting only QA2 solar opinions
        prompt = "지금 다중 협업에서 qa2 solar 의 의견만 듣고 싶다 다른 건 안 쓸래"
        plan = Commander.dispatch_qa_squad(
            prompt=prompt,
            diff_info=None,
            available_extended_qas=self.mock_configs,
            mode="pair_quad"
        )
        self.assertEqual(plan["selected_indices"], [2])
        self.assertEqual(len(plan["selected_qas"]), 1)
        self.assertEqual(plan["selected_qas"][0]["name"].lower(), "qa2")
        self.assertIn("qa2 단독 집중 교차 검증", plan["strategy_desc"])

    def test_commander_dispatch_pre_filtered_pool(self):
        # When session already provides pre-filtered pool (only QA2)
        filtered_pool = [self.mock_configs[0]]
        plan = Commander.dispatch_qa_squad(
            prompt="일반 코딩 작업 진행해줘",
            diff_info=None,
            available_extended_qas=filtered_pool,
            mode="pair_quad"
        )
        self.assertEqual(plan["selected_indices"], [2])
        self.assertEqual(len(plan["selected_qas"]), 1)

    def test_openrouter_and_hermes_solar_model_id_normalization(self):
        from engine_manager import OpenRouterAdapter, HermesAdapter
        ora = OpenRouterAdapter()
        cmd1 = ora.build_command("test", "/tmp", model="upstage/solar-pro4")
        m_idx1 = cmd1.index("-m") + 1
        self.assertEqual(cmd1[m_idx1], "upstage/solar-pro4")
        self.assertFalse(cmd1[m_idx1].startswith("openrouter/"))

        cmd2 = ora.build_command("test", "/tmp", model="openrouter/upstage/solar-pro4")
        m_idx2 = cmd2.index("-m") + 1
        self.assertEqual(cmd2[m_idx2], "upstage/solar-pro4")

        ha = HermesAdapter()
        cmd3 = ha.build_command("test", "/tmp", model="openrouter/upstage/solar-pro4")
        m_idx3 = cmd3.index("-m") + 1
        self.assertEqual(cmd3[m_idx3], "upstage/solar-pro4")


if __name__ == "__main__":
    unittest.main()
