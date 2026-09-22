#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from director_feedback_hub import DirectorFeedbackHub, CollaborativeCritique, DecisionRecord


class TestDirectorFeedbackHub(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_feedback_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_record_consensus_and_sync_memory(self):
        """Verify that record_consensus creates DECISION_LOG.json and WORKING_MEMORY.md."""
        record = DirectorFeedbackHub.record_consensus(
            workspace=self.test_dir,
            target_component="keycap_button",
            user_query="크기 말고 스타일 톤 자체는 어때 보여요?",
            consensus_summary="키캡 크기를 18px에서 32px로 확대하고 스타일 톤은 유지",
            patch_scope=["PdfEditorScreen.kt", "E2EPipeline.kt"],
            round_index=1
        )
        self.assertEqual(record.target_component, "keycap_button")

        # Verify ledger file exists and contains item
        decisions = DirectorFeedbackHub.load_decisions(self.test_dir)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["target_component"], "keycap_button")
        self.assertIn("18px에서 32px로 확대", decisions[0]["consensus_summary"])

        # Verify WORKING_MEMORY.md is populated
        mem_path = DirectorFeedbackHub.get_memory_path(self.test_dir)
        self.assertTrue(os.path.isfile(mem_path))
        with open(mem_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AGY 3.0 Active Working Memory", content)
        self.assertIn("keycap_button", content)
        self.assertIn("크기 말고 스타일 톤 자체는 어때 보여요?", content)

    def test_synthesize_specialized_critiques(self):
        """Verify multi-persona feedback generation (Asset Specialist vs Tone Evaluator)."""
        critiques = DirectorFeedbackHub.synthesize_specialized_critiques(
            screenshot_path="/dummy/path.png",
            user_query="크기 말고 스타일 톤 자체는 어때 보여요?",
            ui_metadata={"element_name": "Enter/Esc 키캡", "size_px": 18}
        )
        self.assertEqual(len(critiques), 2)
        
        # Asset Specialist
        c1 = critiques[0]
        self.assertEqual(c1.persona_name, "clone-클코")
        self.assertIn("18px", c1.comment)
        self.assertEqual(c1.severity, "CRITICAL")

        # Tone Evaluator
        c2 = critiques[1]
        self.assertEqual(c2.persona_name, "clone-코덱스")
        self.assertIn("톤앤매너 자체는 아주 훌륭합니다", c2.comment)

    def test_lightweight_visual_tiki_taka_turn(self):
        """Verify that record_tiki_taka_turn generates concise conversational feedback and saves quiet telemetry."""
        crop_file = os.path.join(self.test_dir, "defect_crop.png")
        with open(crop_file, "w") as f:
            f.write("mock_png")

        turn = DirectorFeedbackHub.record_tiki_taka_turn(
            workspace=self.test_dir,
            speaker="QA",
            status="REJECTED",
            concise_feedback="상단 탭바 우측 텍스트가 잘려 보입니다. 48dp 터치 슬롭 확보 및 페이드 마스크 적용이 필요합니다.",
            crop_image_path=crop_file,
            detailed_telemetry={"total_dom_nodes": 420, "raw_wcag_ratio": 3.8, "jank_frames": 1},
            round_index=2
        )

        msg = turn.format_discord_message()
        self.assertIn("[REJECTED] **QA**", msg)
        self.assertIn("상단 탭바 우측 텍스트가 잘려 보입니다", msg)
        self.assertIn("결함 부위 크롭 1장", msg)

        # Verify telemetry JSON is saved quietly on disk without polluting chat text
        self.assertIsNotNone(turn.telemetry_file_path)
        self.assertTrue(os.path.isfile(turn.telemetry_file_path))
        with open(turn.telemetry_file_path, "r", encoding="utf-8") as f:
            telemetry_data = json.load(f)
        self.assertEqual(telemetry_data["total_dom_nodes"], 420)
        self.assertEqual(telemetry_data["raw_wcag_ratio"], 3.8)

    def test_extract_concise_tiki_taka_message(self):
        """Verify extraction of 2~3 crisp lines from verbose bureaucratic output."""
        verbose_output = """
[STATUS: REJECTED]
=============================================
# 📱 [상세 감사 리포트] (총 120개 체크리스트 전수 점검)
| 문항 | 상태 | 점수 |
| :--- | :---: | :---: |
| 종합 시각 품질 점수 | 72/100 | ⚠️ |
- 상단 탭바 우측 텍스트가 잘려 보입니다.
- 터치 슬롭 48dp 확보 및 1줄 고정이 필요합니다.
- 패딩을 줄이고 폰트 크기를 미세 축소하십시오.
- 기타 99가지 형식적 항목 통과...
"""
        concise = DirectorFeedbackHub.extract_concise_tiki_taka_message(verbose_output, max_lines=3)
        self.assertIn("상단 탭바 우측 텍스트가 잘려 보입니다", concise)
        self.assertIn("터치 슬롭 48dp 확보 및 1줄 고정이 필요합니다", concise)
        self.assertNotIn("체크리스트 전수 점검", concise)
        self.assertNotIn("| 문항 |", concise)


if __name__ == "__main__":
    unittest.main()

