#!/usr/bin/env python3
"""
Unit tests for Mode 3 Commander Macro Memory and Coder/QA Context Reset.
Validates:
1. Commander Macro Memory stores and formats ONLY Commander directives and Auditor conclusions.
2. reset_cycle_workers resets Coder, QA1, QA2, and extended QAs' conversation IDs to None (clean slate),
   while preserving Commander's macro memory.
3. Commander.synthesize_mode3_milestone_directive injects macro memory and obedience mandates.
4. Mode 3 transition resets worker contexts and preserves macro memory across milestone cycles.
"""

import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from session_manager import ChannelSession
from commander import Commander


class TestMode3ContextResetAndMacroMemory(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_mode3_reset_")
        self.session = ChannelSession(channel_id=99991111, workspace=self.test_dir, mode="mode3")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_record_and_format_commander_macro_memory(self):
        """Validates that Commander macro memory records and formats only Commander and Auditor summaries."""
        # 1. Initially empty
        self.assertEqual(len(self.session.get_commander_macro_history()), 0)
        self.assertEqual(self.session.format_commander_macro_memory(), "")

        # 2. Record first cycle
        self.session.record_commander_macro_cycle(
            milestone="v1.0 — 코어 아키텍처",
            commander_directive="코어 데이터 모델 및 CRUD 화면 뼈대 구현",
            auditor_conclusion="모든 코어 CRUD 뷰포트 검증 완료 및 최종 승인"
        )
        hist = self.session.get_commander_macro_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["cycle"], 1)
        self.assertEqual(hist[0]["milestone"], "v1.0 — 코어 아키텍처")
        self.assertIn("CRUD 화면 뼈대", hist[0]["commander_directive"])
        self.assertIn("최종 승인", hist[0]["auditor_conclusion"])

        # 3. Format output check
        formatted = self.session.format_commander_macro_memory()
        self.assertIn("v1.0 — 코어 아키텍처", formatted)
        self.assertIn("사령관 지침", formatted)
        self.assertIn("감사관 승인", formatted)
        # Verify no raw intermediate noise
        self.assertNotIn("bash", formatted)
        self.assertNotIn("stderr", formatted)

    def test_reset_cycle_workers_clears_all_workers_preserves_macro_memory(self):
        """reset_cycle_workers must wipe Coder, QA1, QA2, and extended QAs to clean slate (None) but keep macro memory."""
        # Setup dirty context with conversation IDs
        self.session.conversation_id = "coder-conv-uuid-1111"
        self.session.qa_conversation_id = "qa1-conv-uuid-2222"
        self.session.qa2_conversation_id = "qa2-conv-uuid-3333"
        self.session.extended_qa_conversation_ids = {
            "qa3": "qa3-conv-uuid-4444",
            "qa4": "qa4-conv-uuid-5555"
        }
        self.session.auditor_conversation_id = "aud-conv-uuid-6666"

        # Record a macro cycle
        self.session.record_commander_macro_cycle(
            milestone="v1.0 — MVP 완료",
            commander_directive="기본 인터페이스 구축",
            auditor_conclusion="1차 승인 완료"
        )

        # Execute reset_cycle_workers
        self.session.reset_cycle_workers()

        # Assert all worker contexts are reset to None/empty
        self.assertIsNone(self.session.conversation_id, "Coder context must be None")
        self.assertIsNone(self.session.qa_conversation_id, "QA1 context must be None")
        self.assertIsNone(self.session.qa2_conversation_id, "QA2 context must be None")
        self.assertEqual(self.session.extended_qa_conversation_ids, {}, "Extended QAs must be cleared")
        self.assertIsNone(self.session.auditor_conversation_id, "Auditor context must be None")

        # Macro memory MUST remain intact
        hist = self.session.get_commander_macro_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["milestone"], "v1.0 — MVP 완료")

    def test_commander_synthesize_mode3_milestone_directive(self):
        """Commander must synthesize a clear directive with macro memory and full obedience mandate."""
        tasks = ["알림 푸시 서비스 연동", "로컬 SQLite 캐시 레이어 구현"]
        macro_mem = "• (회차 1: v1.0) 사령관 지침 ➔ 코어 구축 | 감사관 승인 ➔ 완료"

        directive = Commander.synthesize_mode3_milestone_directive(
            milestone_title="데이터 영속화 & 알림",
            milestone_version="v2.0",
            tasks=tasks,
            macro_memory_text=macro_mem,
            user_prompt="알림 기능과 데이터 저장 안정화해줘",
            visual_approved=False
        )

        self.assertIn("v2.0: 데이터 영속화 & 알림", directive)
        self.assertIn("알림 푸시 서비스 연동", directive)
        self.assertIn("로컬 SQLite 캐시 레이어 구현", directive)
        self.assertIn("컨텍스트 청정화 및 명령 순응", directive)
        self.assertIn("이전 회차의 기억과 잡음을 완전히 비운 청정 상태", directive)
        self.assertIn("가시적 실체 구현", directive)
        self.assertIn(macro_mem, directive)

    def test_session_serialization_with_macro_memory(self):
        """ChannelSession.to_dict and serialization must preserve commander_macro_history."""
        self.session.record_commander_macro_cycle(
            milestone="v1.0",
            commander_directive="지침 1",
            auditor_conclusion="승인 1"
        )
        data = self.session.to_dict()
        self.assertIn("commander_macro_history", data)
        self.assertEqual(len(data["commander_macro_history"]), 1)
        self.assertEqual(data["commander_macro_history"][0]["milestone"], "v1.0")


if __name__ == "__main__":
    unittest.main()
