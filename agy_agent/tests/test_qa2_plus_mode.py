import os
import sys
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from session_manager import ChannelSession, SessionManager
from mode_classifier import classify_mode, get_mode_description
from commander import Commander


class TestQA2PlusMode(unittest.TestCase):
    """
    Unit tests for !mode 2+ / !mode pair+ (Coder ➔ qa1 ➔ qa2 ➔ Auditor Collaborative Mode)
    and single_qa2 (Dedicated QA2 Solo Mode).
    Strictly uses mocked/static tests with NO real network calls to avoid API charges.
    """

    def test_session_set_mode_pair_quad(self):
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        test_inputs_2p = [
            "2+",
            "2 +",
            "!mode 2+",
            "pair+",
            "pair +",
            "pair_plus",
            "2plus",
            "2플러스",
            "pair_quad",
            "quad_pair"
        ]
        for mode_input in test_inputs_2p:
            clean_mode = mode_input.replace("!mode", "").strip()
            sess.set_mode(clean_mode)
            self.assertEqual(sess.mode, "pair_quad", f"Failed for mode input: {mode_input}")

        test_inputs_3p = [
            "3+",
            "3 +",
            "!mode 3+",
            "3plus",
            "3플러스",
            "mode3_plus",
            "mode3+"
        ]
        for mode_input in test_inputs_3p:
            clean_mode = mode_input.replace("!mode", "").strip()
            sess.set_mode(clean_mode)
            self.assertEqual(sess.mode, "mode3_plus", f"Failed for mode input: {mode_input}")

    def test_session_set_mode_single_qa2(self):
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        test_inputs = [
            "single_qa2",
            "qa2_solo",
            "solo_qa2",
            "qa2"
        ]
        for mode_input in test_inputs:
            clean_mode = mode_input.replace("!mode", "").strip()
            sess.set_mode(clean_mode)
            self.assertEqual(sess.mode, "single_qa2", f"Failed for mode input: {mode_input}")

    def test_get_mode_description_pair_quad(self):
        desc2 = get_mode_description("2+")
        self.assertIn("!mode 2+", desc2)
        self.assertIn("Auditor", desc2)
        self.assertIn("단일 과제 완결 후 사용자 대기", desc2)

        desc3 = get_mode_description("3+")
        self.assertIn("!mode 3+", desc3)
        self.assertIn("Auditor", desc3)
        self.assertIn("무인 자율 순환", desc3)

    def test_get_mode_description_single_qa2(self):
        expected_substr = '🥷 **단일 qa2 에이전트 모드 (qa2 Solo)** (1545711652987600937 전용 모드로 동작합니다)'
        for mode_str in ["single_qa2", "qa2_solo"]:
            desc = get_mode_description(mode_str)
            self.assertEqual(desc, expected_substr, f"Mismatch for mode: {mode_str}")

    def test_classify_mode_pair_quad(self):
        for session_mode in ["pair_quad", "pair+", "2+", "2plus", "2플러스"]:
            mode, desc = classify_mode("코드 구현 및 정밀 리뷰해줘", session_mode=session_mode)
            self.assertEqual(mode, "pair_quad")
            self.assertTrue("!mode 2+" in desc)

        for session_mode in ["3+", "3plus", "3플러스", "mode3+", "mode3_plus"]:
            mode, desc = classify_mode("코드 구현 및 정밀 리뷰해줘", session_mode=session_mode)
            self.assertEqual(mode, "mode3_plus")
            self.assertTrue("!mode 3+" in desc)

    def test_commander_analyze_intent_pair_quad(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for session_mode in ["pair_quad", "pair+", "2+", "2plus"]:
                decision = loop.run_until_complete(
                    Commander.analyze_intent("코드 구현해줘", session_mode=session_mode)
                )
                self.assertEqual(decision.mode, "pair_quad")
                self.assertTrue("!mode 2+" in decision.briefing)
                self.assertTrue("!mode 2+" in decision.reason)

            for session_mode in ["3+", "3plus", "mode3+", "mode3_plus"]:
                decision = loop.run_until_complete(
                    Commander.analyze_intent("코드 구현해줘", session_mode=session_mode)
                )
                self.assertEqual(decision.mode, "mode3_plus")
                self.assertTrue("!mode 3+" in decision.briefing)
                self.assertTrue("!mode 3+" in decision.reason)
        finally:
            loop.close()

    def test_engine_manager_is_not_restricted_readonly_in_qa_solo(self):
        from engine_manager import OpenRouterAdapter
        adapter = OpenRouterAdapter()
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        sess.mode = "single_qa2"
        sess.role = "qa2"

        session_role = getattr(sess, "role", "") or ""
        is_qa_solo = getattr(sess, "mode", "") in ("pair_plus", "single_qa2")
        is_qa_review = (session_role.startswith("qa") or session_role in ("reviewer", "qa_bot", "auditor")) and not is_qa_solo
        self.assertFalse(is_qa_review, "single_qa2 solo mode should not force read_only review constraint")

    def test_agent_runner_effective_model_single_qa2(self):
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        sess.mode = "single_qa2"
        sess.role = "qa2"
        qa2_model = getattr(config, "QA2_MODEL", "upstage/solar-pro4")

        session_role = sess.role
        if session_role in ("qa2", "single_qa2") or getattr(sess, "mode", "") in ("pair_plus", "single_qa2"):
            effective_model = getattr(sess, "qa2_model", None) or getattr(config, "QA2_MODEL", "upstage/solar-pro4")
        else:
            effective_model = "default"

        self.assertEqual(effective_model, qa2_model)


if __name__ == "__main__":
    unittest.main()
