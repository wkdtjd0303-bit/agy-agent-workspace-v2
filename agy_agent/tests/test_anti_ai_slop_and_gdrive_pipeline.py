import os
import sys
import unittest
import tempfile
import asyncio
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discord_utils import cleanse_agent_output
from evidence_validator import EvidenceValidator
from roadmap_manager import RoadmapManager


class TestAntiAiSlopAndGdrivePipeline(unittest.TestCase):

    def test_cleanse_agent_output_removes_async_task_monologues(self):
        """Verifies that intermediate background task execution monologues are completely filtered out."""
        raw_text = (
            "An asynchronous background task is executing, waiting for completion before continuing.\n"
            "An asynchronous background task is executing, waiting for completion before continuing.\n"
            "Wait for task-60 to complete.\n"
            "The background test execution is running.\n"
            "[task-30] Task completed with exit code: 0. Duration: 10.985s. Log: file:///tmp/task.log\n"
            "Process returned exit code 0.\n"
            "[STATUS: APPROVED]\n\n"
            "요청자(사용자)님, 의뢰하신 작업이 프로덕션 표준으로 완벽하게 구현되었습니다."
        )
        cleaned = cleanse_agent_output(raw_text)
        self.assertNotIn("An asynchronous background task is executing", cleaned)
        self.assertNotIn("Wait for task-60", cleaned)
        self.assertNotIn("[task-30]", cleaned)
        self.assertNotIn("Process returned exit code", cleaned)
        self.assertTrue(cleaned.startswith("[STATUS: APPROVED]"))
        self.assertIn("요청자(사용자)님, 의뢰하신 작업이 프로덕션 표준으로", cleaned)

    def test_cleanse_agent_output_slices_to_final_report_marker(self):
        """Verifies /copy behavior: discards all preceding noise and slices right to official deliverable markers."""
        raw_text = (
            "I will now inspect the test execution results once the task completes.\n"
            "I am waiting for the background build to finish.\n"
            "잠시 후 테스트 실행 결과가 반환되면 검증을 완료하고 빌드 산출물을 생성하겠습니다.\n"
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
            "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
            "성공적으로 검증되었습니다."
        )
        cleaned = cleanse_agent_output(raw_text)
        self.assertTrue(cleaned.startswith("[STATUS: AUDIT_PASSED]"))
        self.assertNotIn("I will now inspect", cleaned)
        self.assertNotIn("잠시 후 테스트 실행 결과가", cleaned)

    def test_detect_ai_slop_prompt_bleed_through(self):
        """Verifies that internal prompt thresholds (50px+, 70px+, 16ms) in UI copy are detected as violations."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "MyScreen.kt")
            with open(src_file, "w", encoding="utf-8") as f:
                f.write(
                    'package com.example.app\n'
                    'import androidx.compose.material3.Text\n'
                    '@Composable\n'
                    'fun MyScreen() {\n'
                    '    Text(text = "좌우 스와이프 페이지 전환(50px+)")\n'
                    '    Text(text = "상단 당겨서 새로고침(70px+)")\n'
                    '}\n'
                )
            is_detected, violations = EvidenceValidator.detect_ai_slop_and_prompt_bleed(
                workspace=td,
                changed_files=["MyScreen.kt"]
            )
            self.assertTrue(is_detected)
            self.assertTrue(len(violations) > 0)
            self.assertIn("Prompt Bleed-through", violations[0])

    def test_detect_ai_slop_backend_poc_buttons_in_ui(self):
        """Verifies that backend infrastructure PoC terms (단절 테스트, 3-Way 자동 병합) in UI are detected."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "SyncDialog.kt")
            with open(src_file, "w", encoding="utf-8") as f:
                f.write(
                    'package com.example.app\n'
                    'import androidx.compose.material3.Button\n'
                    '@Composable\n'
                    'fun SyncDialog() {\n'
                    '    Button(onClick = {}) { Text("단절 테스트") }\n'
                    '    Button(onClick = {}) { Text("3-Way 자동 병합") }\n'
                    '}\n'
                )
            is_detected, violations = EvidenceValidator.detect_ai_slop_and_prompt_bleed(
                workspace=td,
                changed_files=["SyncDialog.kt"]
            )
            self.assertTrue(is_detected)
            self.assertIn("백엔드/인프라 PoC", violations[0])

    def test_detect_ai_slop_cliche_dummy_data(self):
        """Verifies that typical LLM dummy person cliches (김민수 수석연구원, 이지은 디자이너) are caught."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "Collaborators.kt")
            with open(src_file, "w", encoding="utf-8") as f:
                f.write(
                    'package com.example.app\n'
                    'val mockUsers = listOf(\n'
                    '    User(name = "김민수 수석연구원", status = "1페이지 펜 필기 검토 중"),\n'
                    '    User(name = "이지은 디자이너", status = "2페이지 형광펜 하이라이트 작성 중")\n'
                    ')\n'
                )
            is_detected, violations = EvidenceValidator.detect_ai_slop_and_prompt_bleed(
                workspace=td,
                changed_files=["Collaborators.kt"]
            )
            self.assertTrue(is_detected)
            self.assertIn("AI 클리셰 더미 데이터", violations[0])

    def test_clean_production_code_passes_anti_slop_audit(self):
        """Verifies that clean, human-crafted professional UI code passes anti-slop audit with 0 violations."""
        with tempfile.TemporaryDirectory() as td:
            src_file = os.path.join(td, "CleanScreen.kt")
            with open(src_file, "w", encoding="utf-8") as f:
                f.write(
                    'package com.example.app\n'
                    'import androidx.compose.material3.Text\n'
                    '@Composable\n'
                    'fun CleanScreen() {\n'
                    '    Text(text = "페이지 이동", maxLines = 1)\n'
                    '    Text(text = "클라우드 동기화", maxLines = 1)\n'
                    '}\n'
                )
            is_detected, violations = EvidenceValidator.detect_ai_slop_and_prompt_bleed(
                workspace=td,
                changed_files=["CleanScreen.kt"]
            )
            self.assertFalse(is_detected)
            self.assertEqual(len(violations), 0)

    def test_roadmap_manager_synthesizes_domain_aware_milestones_without_b2b_hallucinations(self):
        """Verifies that exhausted roadmap generates domain-aware milestones instead of hardcoded B2B SaaS."""
        with tempfile.TemporaryDirectory() as td:
            # Create a mock PDF viewer project structure
            os.makedirs(os.path.join(td, "app", "src", "main", "java", "com", "example", "pdfviewer"), exist_ok=True)
            with open(os.path.join(td, "app", "src", "main", "java", "com", "example", "pdfviewer", "PdfViewerScreen.kt"), "w") as f:
                f.write("class PdfViewerScreen")

            rm = RoadmapManager(td)
            rm.ensure_exists()
            for ms in rm._milestones:
                ms.mark_done()
            rm.save()

            # Expand multiple times to exhaust all base/advanced templates
            for _ in range(4):
                exp_ms = rm.auto_expand_roadmap()
                for m in exp_ms:
                    m.mark_done()
                rm.save()

            # Now fully exhausted: synthesis must kick in
            synthesized = rm._synthesize_codebase_aware_milestones([m.title for m in rm._milestones])
            self.assertTrue(len(synthesized) > 0)
            titles = [t[0] for t in synthesized]
            all_text = " ".join(titles) + " " + " ".join(t[1][0] for t in synthesized)

            # Strictly ensure NO generic B2B SaaS Webhook / Snapshot Rollback Center
            self.assertNotIn("Webhook 디스패처", all_text)
            self.assertNotIn("클라우드 스토리지 양방향", all_text)
            self.assertNotIn("롤백 제어 센터", all_text)

            # Ensure domain-tailored document/PDF terms are prioritized
            self.assertTrue(any("문서" in t or "탐색" in t or "썸네일" in t or "검색" in t or "주석" in t for t in titles))


if __name__ == "__main__":
    unittest.main()
