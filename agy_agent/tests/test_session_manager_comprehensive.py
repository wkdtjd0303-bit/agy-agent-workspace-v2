import os
import sys
import tempfile
import unittest
import shutil

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from session_manager import SessionManager, ChannelSession, QueuedItem


class TestSessionManagerComprehensive(unittest.TestCase):
    """
    Extensive test suite for session_manager, channel sessions, queue FIFO,
    persistence, atomic file saves, and multi-channel synchronization.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sess_")
        self.state_file = os.path.join(self.test_dir, "channel_sessions.json")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_channel_session_queue_fifo(self):
        session = ChannelSession(
            channel_id=12345,
            workspace=self.test_dir,
            mode="auto"
        )
        self.assertEqual(len(session.peek_queue()), 0)
        self.assertIsNone(session.dequeue())

        # Enqueue 3 items
        session.enqueue(message=None, prompt="Task 1")
        session.enqueue(message=None, prompt="Task 2")
        session.enqueue(message=None, prompt="Task 3")

        self.assertEqual(len(session.peek_queue()), 3)

        # Dequeue must be FIFO
        item1 = session.dequeue()
        self.assertIsNotNone(item1)
        self.assertEqual(item1.prompt, "Task 1")

        item2 = session.dequeue()
        self.assertIsNotNone(item2)
        self.assertEqual(item2.prompt, "Task 2")

        # Clear remaining
        cleared = session.clear_queue()
        self.assertEqual(cleared, 1)
        self.assertEqual(len(session.peek_queue()), 0)

    def test_session_manager_persistence_and_reload(self):
        sm = SessionManager(state_file=self.state_file)
        sess = sm.get_session(99999)
        sess.set_workspace(self.test_dir)
        sess.set_mode("pair")
        sess.set_conversation_id("conv-12345")
        sess.set_auto(enabled=True, mode="evolve", idle_seconds=300, max_cycles=20)

        # Force save
        sm.save_state()
        self.assertTrue(os.path.exists(self.state_file))

        # Create new manager pointing to same state file
        sm2 = SessionManager(state_file=self.state_file)
        sess2 = sm2.get_session(99999)

        self.assertEqual(sess2.workspace, self.test_dir)
        self.assertEqual(sess2.mode, "pair")
        self.assertEqual(sess2.conversation_id, "conv-12345")
        self.assertTrue(sess2.auto_enabled)
        self.assertEqual(sess2.auto_mode, "evolve")
        self.assertEqual(sess2.auto_idle_seconds, 300)
        self.assertEqual(sess2.auto_max_cycles, 20)

    def test_session_manager_all_modes_and_auto(self):
        sm = SessionManager(state_file=self.state_file)
        sess1 = sm.get_session(1001)
        sess2 = sm.get_session(1002)

        sm.set_all_modes("single")
        self.assertEqual(sess1.mode, "single")
        self.assertEqual(sess2.mode, "single")

        sm.set_all_modes("pair")
        self.assertEqual(sess1.mode, "pair")
        self.assertEqual(sess2.mode, "pair")

        sess1.set_mode("read")
        self.assertEqual(sess1.mode, "read")

        sm.set_all_modes("2+")
        self.assertEqual(sess1.mode, "pair_quad")
        self.assertEqual(sess2.mode, "pair_quad")

        sess1.set_mode("2plus")
        self.assertEqual(sess1.mode, "pair_quad")

        sess2.set_mode("pair+")
        self.assertEqual(sess2.mode, "pair_quad")

        sess2.set_mode("pair_quad")
        self.assertEqual(sess2.mode, "pair_quad")

        sm.set_all_auto(enabled=True, mode="evolve", max_cycles=20)
        self.assertTrue(sess1.auto_enabled)
        self.assertEqual(sess1.auto_mode, "evolve")
        self.assertEqual(sess1.auto_max_cycles, 20)
        self.assertTrue(sess2.auto_enabled)
        self.assertEqual(sess2.auto_mode, "evolve")
        self.assertEqual(sess2.auto_max_cycles, 20)

    def test_session_manager_reset_and_stop_all(self):
        sm = SessionManager(state_file=self.state_file)
        sess = sm.get_session(2001)
        sess.set_conversation_id("conv-active")
        sess.set_busy(True, desc="작업 진행 중")
        sess.enqueue(None, "대기 큐 작업")

        self.assertEqual(len(sm.get_busy_channels()), 1)

        # Reset all
        sm.reset_all_conversations(clear_queues=True)

        self.assertIsNone(sess.conversation_id)
        self.assertFalse(sess.is_busy)
        self.assertEqual(sess.status, "idle")
        self.assertEqual(len(sess.peek_queue()), 0)
        self.assertEqual(len(sm.get_busy_channels()), 0)

    def test_set_workspace_fallback_on_uncreatable_path(self):
        from config import DEFAULT_WORKSPACE
        sess = ChannelSession(channel_id=8888, workspace=self.test_dir)
        # Attempt to set workspace to impossible/uncreatable path
        impossible_path = "/proc/sys/impossible_sub_dir_test_99999"
        sess.set_workspace(impossible_path)
        # Must have fallen back to DEFAULT_WORKSPACE and must physically exist
        self.assertEqual(sess.workspace, os.path.abspath(os.path.expanduser(DEFAULT_WORKSPACE)))
        self.assertTrue(os.path.isdir(sess.workspace))

    def test_session_manager_reset_channel_session(self):
        sm = SessionManager(state_file=self.state_file)
        sess = sm.get_session(3001)
        sess.set_conversation_id("conv-target-channel")
        sess.qa_conversation_id = "conv-qa-target"
        sess.auditor_conversation_id = "conv-auditor-target"
        sess.register_user_reference_image(filename="ref1.png", file_path="/tmp/ref1.png", sha256="hash1")
        sess.set_busy(True, desc="작업 진행 중")
        sess.enqueue(None, "큐 작업 1")
        sess.enqueue(None, "큐 작업 2")

        # Other session to verify it is untouched
        other_sess = sm.get_session(3002)
        other_sess.set_conversation_id("conv-other-channel")
        other_sess.enqueue(None, "다른 채널 큐")

        # Reset specific channel
        res = sm.reset_channel_session(3001, clear_queue=True)
        self.assertIsNotNone(res)
        self.assertIsNone(sess.conversation_id)
        self.assertIsNone(sess.qa_conversation_id)
        self.assertIsNone(sess.auditor_conversation_id)
        self.assertEqual(sess.user_reference_images, [])
        self.assertFalse(sess.is_busy)
        self.assertEqual(sess.status, "idle")
        self.assertEqual(len(sess.peek_queue()), 0)

        # Verify other session remained intact
        self.assertEqual(other_sess.conversation_id, "conv-other-channel")
        self.assertEqual(len(other_sess.peek_queue()), 1)

        # Verify state was saved to disk
        sm2 = SessionManager(state_file=self.state_file)
        loaded_sess = sm2.get_session(3001)
        self.assertIsNone(loaded_sess.conversation_id)
        loaded_other = sm2.get_session(3002)
        self.assertEqual(loaded_other.conversation_id, "conv-other-channel")


if __name__ == "__main__":
    unittest.main()
