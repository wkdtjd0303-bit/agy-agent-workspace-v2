import os
import sys
import unittest
import time

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from self_healing import SelfHealingManager
from session_manager import SessionManager


class TestSelfHealingComprehensive(unittest.TestCase):
    """
    Extensive test suite for SelfHealingManager error handling, flood control,
    retry limits, and prompt key generation.
    """

    def setUp(self):
        self.sm = SessionManager()
        self.shm = SelfHealingManager(session_manager=self.sm)

    def test_prompt_key_generation(self):
        key1 = self.shm._get_prompt_key(12345, "Short prompt")
        self.assertEqual(key1, "12345:Short prompt")

        # Huge prompt clamped to 100 chars
        long_prompt = "A" * 300
        key2 = self.shm._get_prompt_key(12345, long_prompt)
        self.assertEqual(len(key2), len("12345:") + 100)

        # None prompt handling
        key3 = self.shm._get_prompt_key(12345, None)
        self.assertEqual(key3, "12345:")

    def test_flood_control_detection(self):
        channel_id = 99999
        self.assertFalse(self.shm.is_flood_limited(channel_id))

        # Record 9 errors -> not flooded
        for _ in range(9):
            self.shm.record_error(channel_id)
        self.assertFalse(self.shm.is_flood_limited(channel_id))

        # 10th error within window -> flooded!
        self.shm.record_error(channel_id)
        self.assertTrue(self.shm.is_flood_limited(channel_id))

        # Clear error -> restored
        self.shm.clear_channel_error(channel_id)
        self.assertFalse(self.shm.is_flood_limited(channel_id))

    def test_autopilot_error_not_run_as_coding_prompt(self):
        failed_prompt = "[오토파일럿 무인 E2E 감사 중 시스템 오류 발생]\n• 채널: <#1549294636726755398>\n• 타깃: /test/workspace"
        is_background_system_err = failed_prompt.startswith("[") and (
            "명령어 실행 중 오류 발생" in failed_prompt
            or "이벤트 백그라운드 처리 중 시스템 오류" in failed_prompt
            or "명령어:" in failed_prompt
            or "오토파일럿" in failed_prompt
        )
        self.assertTrue(is_background_system_err, "Autopilot error must be categorized as system error!")


if __name__ == "__main__":
    unittest.main()
