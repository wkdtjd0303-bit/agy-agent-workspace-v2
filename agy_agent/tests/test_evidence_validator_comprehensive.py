import os
import sys
import json
import tempfile
import shutil
import unittest
from unittest.mock import patch
from PIL import Image

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from evidence_validator import EvidenceValidator


class TestEvidenceValidatorComprehensive(unittest.TestCase):
    """
    Extensive test suite for EvidenceValidator & 7 Quality Gates Engine:
    - Physical file & JSON validation
    - Deep visual image pixel variance analysis (anti-blank/solid screen)
    - Anti-forgery synthetic artifact scanner (PIL/Canvas fake screen drawers)
    - Structured QA report issue parsing (Critical, Major, Minor)
    - PRD feature coverage & next target recommendation engine
    - Full Quality Gate 0~7 validation
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_evidence_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_validate_file_basic_and_json(self):
        # Non-existent
        ok, msg = EvidenceValidator.validate_file(os.path.join(self.test_dir, "ghost.txt"))
        self.assertFalse(ok)

        # Empty file
        empty_path = os.path.join(self.test_dir, "empty.txt")
        with open(empty_path, "w") as f:
            pass
        ok, msg = EvidenceValidator.validate_file(empty_path, min_bytes=10)
        self.assertFalse(ok)

        # Valid JSON
        json_path = os.path.join(self.test_dir, "data.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"status": "PASS", "count": 42}, f)
        ok, msg = EvidenceValidator.validate_file(json_path, is_json=True)
        self.assertTrue(ok)

        # Invalid JSON
        bad_json_path = os.path.join(self.test_dir, "bad.json")
        with open(bad_json_path, "w", encoding="utf-8") as f:
            f.write("{unquoted_bad_json: true")
        ok, msg = EvidenceValidator.validate_file(bad_json_path, is_json=True)
        self.assertFalse(ok)

    def test_validate_image_pixel_variance(self):
        # 1. Solid / Blank screen (all black)
        blank_path = os.path.join(self.test_dir, "blank.png")
        blank_img = Image.new("RGB", (300, 300), color=(0, 0, 0))
        blank_img.save(blank_path)

        ok, msg = EvidenceValidator.validate_image_content(blank_path)
        self.assertFalse(ok, "Solid black screen must fail validation")
        self.assertIn("Blank/Solid", msg)

        # 2. Rich UI screenshot (multi-color pixel buffer with high variance)
        rich_path = os.path.join(self.test_dir, "rich_ui.png")
        width, height = 300, 300
        pixel_data = bytearray()
        for y in range(height):
            for x in range(width):
                r = (x * 255) // width
                g = (y * 255) // height
                b = (x * y) % 256
                pixel_data.extend((r, g, b))
        rich_img = Image.frombytes("RGB", (width, height), bytes(pixel_data))
        rich_img.save(rich_path)

        ok, msg = EvidenceValidator.validate_image_content(rich_path)
        self.assertTrue(ok, "Rich multi-color image with high pixel variance must pass")

        # 3. Half-empty / Black hole comparison card (Left half black, Right half rich UI)
        split_path = os.path.join(self.test_dir, "half_black.png")
        split_img = Image.new("RGB", (600, 300), color=(0, 0, 0))
        split_img.paste(rich_img, (300, 0))
        split_img.save(split_path)

        ok_split, msg_split = EvidenceValidator.validate_image_content(split_path)
        self.assertFalse(ok_split, "Comparison image with black hole left half must fail validation")
        self.assertIn("black hole", msg_split.lower())

        # 4. UIVisualDiffer safety test (rejects black hole before image)
        from ui_visual_differ import UIVisualDiffer
        out_card = os.path.join(self.test_dir, "test_diff_card.png")
        res = UIVisualDiffer.create_smart_micro_diff_card(
            before_image_path=blank_path,
            after_image_path=rich_path,
            output_path=out_card
        )
        self.assertIsNone(res, "UIVisualDiffer must return None when Before image is an empty black screenshot")

    def test_detect_synthetic_artifacts(self):
        # Create a fake PIL drawing script that mimics screenshot generation
        fake_script = os.path.join(self.test_dir, "generate_screenshots.py")
        with open(fake_script, "w", encoding="utf-8") as f:
            f.write("from PIL import ImageDraw\ndraw = ImageDraw.Draw(img)\ndraw.rectangle([0,0,10,10])\n")

        is_forged, scripts = EvidenceValidator.detect_synthetic_artifacts(self.test_dir)
        self.assertTrue(is_forged)
        self.assertGreaterEqual(len(scripts), 1)

    def test_parse_structured_qa_report_json(self):
        report_text = """
### 3중 협업 QA 종합 평가 결과

```json
{
  "status": "APPROVED",
  "issues": [
    {"severity": "MINOR", "description": "1px 버튼 마진 여백 미세 불일치"},
    {"severity": "MAJOR", "description": "다크모드 토글 시 깜빡임"}
  ]
}
```
        """
        parsed = EvidenceValidator.parse_structured_qa_report(report_text)
        self.assertEqual(parsed["status"], "APPROVED")
        self.assertEqual(len(parsed["minor_issues"]), 1)
        self.assertEqual(len(parsed["major_issues"]), 1)
        self.assertEqual(len(parsed["critical_issues"]), 0)

    def test_parse_structured_qa_report_heuristic(self):
        report_text = """
[STATUS: REJECTED]
🚨 Critical 결함:
- 메인 결제 버튼 클릭 시 앱 강제 종료 크래시 발생

⚠️ Major 결함:
- 모바일 390px 뷰포트에서 헤더 메뉴 겹침 현상

💡 Minor 개선점:
- 로딩 스피너 애니메이션 속도 0.2초 단축 권장
        """
        parsed = EvidenceValidator.parse_structured_qa_report(report_text)
        self.assertEqual(parsed["status"], "REJECTED")
        self.assertGreaterEqual(len(parsed["critical_issues"]), 1)
        self.assertGreaterEqual(len(parsed["major_issues"]), 1)
        self.assertGreaterEqual(len(parsed["minor_issues"]), 1)

    def test_evaluate_prd_feature_coverage(self):
        prd_file = os.path.join(self.test_dir, "PRD.md")
        with open(prd_file, "w", encoding="utf-8") as f:
            f.write("""
# PRD
* **REQ-001 (Telemetery Dashboard)**: Hardware telemetry monitoring.
* **REQ-002 (3-Agent Trio Pipeline)**: Collaborative Coder, QA, Auditor pipeline.
* **REQ-003 (Self-Healing)**: Daemon auto-recovery watchdog.
            """)

        # Create dummy src file implementing REQ-001 and REQ-002
        src_dir = os.path.join(self.test_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, "app.py"), "w", encoding="utf-8") as f:
            f.write("def telemetry_dashboard(): pass\ndef trio_pipeline(): pass\n")

        res = EvidenceValidator.evaluate_prd_feature_coverage(self.test_dir)
        self.assertTrue(res["has_prd"])
        self.assertEqual(res["total_requirements"], 3)
        self.assertGreaterEqual(res["completed_count"], 1)
        self.assertIsNotNone(res["next_recommended_target"])

    def test_verify_anti_mocking_and_authenticity_detection(self):
        # 1. Clean nominal workspace
        clean_ws = os.path.join(self.test_dir, "clean_ws")
        os.makedirs(os.path.join(clean_ws, "src"), exist_ok=True)
        with open(os.path.join(clean_ws, "src", "Main.kt"), "w", encoding="utf-8") as f:
            f.write("class Main { fun run() = true }\n")
        is_clean, violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(clean_ws)
        self.assertTrue(is_clean)
        self.assertEqual(len(violations), 0)

        # 2. Direct model mutation and UI bypass in test file (Rule 2)
        mock_ws = os.path.join(self.test_dir, "mock_ws")
        test_dir = os.path.join(mock_ws, "app", "src", "test")
        os.makedirs(test_dir, exist_ok=True)
        m_copy = "." + "co" + "py(text" + " = \"[공개나리]\", isEdited = true)"
        m_render = "render" + "Composite" + "PageBitmap"
        fake_test_content = (
            "class FakeDocumentTest {\n"
            "    fun test_manipulation() {\n"
            f"        val edited = gonggaeBlock{m_copy}\n"
            f"        val bitmap = PdfCoreEngine.{m_render}(page, listOf(edited))\n"
            "        bitmap.compress(Bitmap.CompressFormat.PNG, 100, outputStream)\n"
            "    }\n"
            "}\n"
        )
        with open(os.path.join(test_dir, "FakeDocumentTest.kt"), "w", encoding="utf-8") as f:
            f.write(fake_test_content)
        is_mock_clean, mock_violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(mock_ws)
        self.assertFalse(is_mock_clean, "Must detect UI layer bypass and direct model mutation in test file")
        self.assertTrue(any("UI Layer Bypass" in v for v in mock_violations))

        # 3. Synthetic canvas drawing script (Rule 3)
        forged_ws = os.path.join(self.test_dir, "forged_ws")
        os.makedirs(os.path.join(forged_ws, "scripts"), exist_ok=True)
        with open(os.path.join(forged_ws, "scripts", "generate_screenshots.py"), "w", encoding="utf-8") as f:
            f.write("from PIL import ImageDraw\nimg = Image.new('RGB', (390, 844))\ndraw = ImageDraw.Draw(img)\ndraw.rectangle([0,0,10,10])\n")
        is_forged_clean, forged_violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(forged_ws)
        self.assertFalse(is_forged_clean, "Must detect synthetic PIL screenshot generator script")
        self.assertTrue(any("Synthetic Screenshot" in v for v in forged_violations))

    def test_parse_qa_report_solar_pro_report_with_rejected_in_body(self):
        """Validates that parse_structured_qa_report properly handles reports with '[STATUS: REJECTED]' in the explanation."""
        solar_report_text = (
            "[STATUS: APPROVED]\n\n"
            "🕵️‍♂️ 시니어 QA2 독립 교차 감사 리포트\n"
            "- 반려 피드백 루프: 확장 QA 중 한 곳이라도 [STATUS: REJECTED] 시 해당 사유가 Coder에 전달됩니다.\n"
            "판정: [STATUS: APPROVED]"
        )
        parsed = EvidenceValidator.parse_structured_qa_report(solar_report_text)
        self.assertEqual(parsed["status"], "APPROVED")

    @patch("subprocess.run")
    def test_git_diff_summary_excludes_pyc_and_prioritizes_core_files(self, mock_run):
        """Validates that get_git_diff_summary filters out *.pyc files and prioritizes core agent files."""
        import subprocess as sp
        def fake_run(cmd, *args, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "status" in cmd_str:
                return sp.CompletedProcess(cmd, 0, stdout=" M agy_agent/commander.py\n M agy_agent/bot.py\n?? test.pyc\n", stderr="")
            if "diff" in cmd_str:
                return sp.CompletedProcess(cmd, 0, stdout="diff --git a/agy_agent/commander.py b/agy_agent/commander.py\n+commander\ndiff --git a/agy_agent/bot.py b/agy_agent/bot.py\n+bot\n", stderr="")
            if "rev-parse" in cmd_str:
                return sp.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        summary = EvidenceValidator.get_git_diff_summary(WORKSPACE_ROOT)
        self.assertTrue(summary.get("has_git", False))
        
        # Verify no .pyc in changed_files
        changed = summary.get("changed_files", [])
        self.assertFalse(any(f.endswith(".pyc") or "__pycache__" in f for f in changed), "changed_files must not contain .pyc or __pycache__")

        # Verify key core files appear in raw_diff when modified in repository
        raw_diff = summary.get("raw_diff", "")
        self.assertIn("commander.py", raw_diff)
        self.assertIn("bot.py", raw_diff)


if __name__ == "__main__":
    unittest.main()


