import os
import sys
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from session_manager import ChannelSession
from mode_classifier import classify_mode, get_mode_description


class TestLegacySinglePlusMode(unittest.TestCase):
    """
    레거시 컨설턴트 모드(!mode 1+ / single+ / consultant)는 기능 제거되어
    단일 에이전트 모드(single)로 통합되었습니다. 본 테스트는 무중단 호환을 검증합니다.
    """

    def setUp(self):
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()

    def tearDown(self):
        from trio_agent_runner import QuotaManager
        QuotaManager.reset_exhaustion()

    def test_session_set_mode_legacy_alias_maps_to_single(self):
        sess = ChannelSession(channel_id=123456789, workspace="/tmp/test_ws")
        test_inputs = [
            "1+", "1 +", "!mode 1+", "single+", "single +",
            "single_plus", "1plus", "1플러스", "consultant", "컨설턴트",
        ]
        for mode_input in test_inputs:
            clean_mode = mode_input.replace("!mode", "").strip()
            sess.set_mode(clean_mode)
            self.assertEqual(sess.mode, "single", f"Failed for legacy mode input: {mode_input}")

    def test_get_mode_description_single(self):
        expected_substr = "👤 **단일 에이전트 모드 (Single · !mode 1)** (초고속 단일 에이전트 답변)"
        for mode_str in ["single_plus", "single+", "1+", "1 +", "single +", "consultant"]:
            desc = get_mode_description(mode_str)
            self.assertEqual(desc, expected_substr, f"Mismatch for mode: {mode_str}")

    def test_classify_mode_legacy_alias(self):
        for session_mode in ["single_plus", "single+", "1+", "consultant"]:
            mode, desc = classify_mode("아키텍처 확장성에 대해 자문해줘", session_mode=session_mode)
            self.assertEqual(mode, "single")
            self.assertIn("단일 에이전트", desc)

    def test_consultant_role_removed_and_image_role_present(self):
        from model_manager import DEFAULT_ROLES
        self.assertNotIn("consultant", DEFAULT_ROLES)
        self.assertIn("image", DEFAULT_ROLES)
        image_role = DEFAULT_ROLES["image"]
        self.assertEqual(image_role["engine"], "agy")
        self.assertEqual(image_role["model"], "gemini-3.8-flash-high")

    def test_config_has_no_consultant_fields(self):
        self.assertFalse(hasattr(config, "CONSULTANT_MODEL"))
        self.assertFalse(hasattr(config, "CONSULTANT_ENGINE"))
        self.assertTrue(hasattr(config, "IMAGE_MODEL"))
        self.assertEqual(getattr(config, "IMAGE_ENGINE", ""), "agy")


if __name__ == "__main__":
    unittest.main()
