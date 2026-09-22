import os
import sys
import unittest

# Ensure agy_agent path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from discord_utils import (
    shorten_path,
    shorten_paths_for_readability,
    optimize_readability,
    format_tool_status,
    split_message
)
from trio_agent_runner import TrioAgentRunner, compact_agent_context
from model_manager import GlobalModelManager


class TestTokenEngineeringAndPathDiet(unittest.TestCase):
    """
    Comprehensive tests for Token Engineering, Path Diet & Readability Optimization:
    1. Shortening internal absolute paths to concise relative paths (./...)
    2. Preventing user readability degradation and context bloat from excessive internal directories
    3. Output text compaction (Caveman 300~500 chars token diet)
    4. 100% preservation of !mode and !model orchestration intents
    """

    def setUp(self):
        self.workspace = "/home/jangsung/Desktop/autocoding/!5_agy_agent"

    def test_shorten_path_with_workspace(self):
        # 1. Exact workspace root
        self.assertEqual(shorten_path(self.workspace, self.workspace), "./")
        
        # 2. File in workspace root
        target_file = "/home/jangsung/Desktop/autocoding/!5_agy_agent/bot.py"
        self.assertEqual(shorten_path(target_file, self.workspace), "./bot.py")

        # 3. Subdirectory file in workspace
        sub_file = "/home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent/trio_agent_runner.py"
        self.assertEqual(shorten_path(sub_file, self.workspace), "./agy_agent/trio_agent_runner.py")

        # 4. Reports deep artifact
        report_file = "/home/jangsung/Desktop/autocoding/!5_agy_agent/reports/gate5/screenshots/shot.png"
        self.assertEqual(shorten_path(report_file, self.workspace), "./reports/gate5/screenshots/shot.png")

    def test_shorten_path_without_explicit_workspace(self):
        # Autocoding project pattern auto-detection
        target = "/home/jangsung/Desktop/autocoding/my_cool_project/src/index.ts"
        self.assertEqual(shorten_path(target), "./src/index.ts")

        # Fallback empty / none
        self.assertEqual(shorten_path(""), "")
        self.assertEqual(shorten_path(None), "")

    def test_shorten_paths_for_readability_in_text(self):
        raw_text = (
            "수정된 파일 목록:\n"
            "• `/home/jangsung/Desktop/autocoding/!5_agy_agent/bot.py` (라우터 보강)\n"
            "• `/home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent/agent_runner.py` (토큰 다이어트)\n"
            "실행 로그: `/home/jangsung/Desktop/autocoding/!5_agy_agent/reports/gate1/report.json`"
        )
        cleaned = shorten_paths_for_readability(raw_text, workspace=self.workspace)
        
        # Absolute path prefixes should not appear
        self.assertNotIn("/home/jangsung/Desktop/autocoding/!5_agy_agent/", cleaned)
        # Relative paths should appear
        self.assertIn("./bot.py", cleaned)
        self.assertIn("./agy_agent/agent_runner.py", cleaned)
        self.assertIn("./reports/gate1/report.json", cleaned)
        # Character count should be significantly reduced
        self.assertLess(len(cleaned), len(raw_text))

    def test_format_tool_status_uses_shortened_paths(self):
        # 1. write_to_file
        tool_info = {"parameters": {"TargetFile": "/home/jangsung/Desktop/autocoding/!5_agy_agent/app.py"}}
        status = format_tool_status("write_to_file", tool_info, workspace=self.workspace)
        self.assertIn("./app.py", status)
        self.assertNotIn("/home/jangsung", status)

        # 2. replace_file_content
        tool_info_replace = {"parameters": {"TargetFile": "/home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent/bot.py"}}
        status_rep = format_tool_status("replace_file_content", tool_info_replace, workspace=self.workspace)
        self.assertIn("./agy_agent/bot.py", status_rep)
        self.assertNotIn("/home/jangsung", status_rep)

        # 3. view_file
        tool_info_view = {"parameters": {"AbsolutePath": "/home/jangsung/Desktop/autocoding/!5_agy_agent/PRD.md"}}
        status_view = format_tool_status("view_file", tool_info_view, workspace=self.workspace)
        self.assertIn("./PRD.md", status_view)

        # 4. run_command with long paths
        tool_info_cmd = {"parameters": {"CommandLine": "python3 /home/jangsung/Desktop/autocoding/!5_agy_agent/test.py"}}
        status_cmd = format_tool_status("run_command", tool_info_cmd, workspace=self.workspace)
        self.assertIn("./test.py", status_cmd)
        self.assertNotIn("/home/jangsung", status_cmd)

    def test_optimize_readability_integrates_path_shortening_and_newline_cleanup(self):
        messy_text = (
            "검증 리포트\n\n\n\n"
            "════════════════════════════════════════\n"
            "타깃: /home/jangsung/Desktop/autocoding/!5_agy_agent/config.py\n\n\n"
            "완료되었습니다."
        )
        optimized = optimize_readability(messy_text, workspace=self.workspace)

        # Path should be converted to relative ./
        self.assertIn("./config.py", optimized)
        self.assertNotIn("/home/jangsung/Desktop/autocoding/!5_agy_agent", optimized)
        # Excessive newlines (4) collapsed to at most 2
        self.assertNotIn("\n\n\n", optimized)

    def test_compact_agent_context_shortens_paths_and_preserves_status(self):
        agent_output = (
            "[STATUS: APPROVED]\n\n"
            "안녕하세요! Coder 작업 결과를 보고합니다.\n"
            "────────────────────────────────────────\n"
            "수정된 파일: /home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent/model_manager.py\n"
            "1. 목적: 토큰 낭비 방지 및 디렉토리 축약\n"
            "2. 변경: 절대경로 ➔ 상대경로 (./) 치환 로직 탑재\n"
            "3. 검증 결과: 단위 테스트 100% 통과\n"
            "```python\n"
            "def dummy_code_1(): pass\n"
            "def dummy_code_2(): pass\n"
            "def dummy_code_3(): pass\n"
            "def dummy_code_4(): pass\n"
            "def dummy_code_5(): pass\n"
            "def dummy_code_6(): pass\n"
            "def dummy_code_7(): pass\n"
            "def dummy_code_8(): pass\n"
            "def dummy_code_9(): pass\n"
            "def dummy_code_10(): pass\n"
            "def dummy_code_11(): pass\n"
            "def dummy_code_12(): pass\n"
            "def dummy_code_13(): pass\n"
            "def dummy_code_14(): pass\n"
            "def dummy_code_15(): pass\n"
            "def dummy_code_16(): pass\n"
            "def dummy_code_17(): pass\n"
            "def dummy_code_18(): pass\n"
            "def dummy_code_19(): pass\n"
            "def dummy_code_20(): pass\n"
            "```\n"
        )
        compacted = compact_agent_context(agent_output, max_chars=1500)

        # 1. Essential status tag preserved
        self.assertIn("[STATUS: APPROVED]", compacted)
        # 2. Greeting stripped
        self.assertNotIn("안녕하세요!", compacted)
        # 3. Path shortened
        self.assertNotIn("/home/jangsung/Desktop/autocoding/!5_agy_agent/", compacted)
        self.assertIn("./agy_agent/model_manager.py", compacted)
        # 4. Code block truncated cleanly (15-line cap: lines 16+ are truncated)
        self.assertIn("... [중략: 도구로 적용된 코드 본문 생략 (Token Diet)] ...", compacted)
        self.assertNotIn("def dummy_code_20(): pass", compacted)
        # 5. Technical bullet points preserved
        self.assertIn("1. 목적: 토큰 낭비 방지 및 디렉토리 축약", compacted)
        self.assertIn("3. 검증 결과: 단위 테스트 100% 통과", compacted)

    def test_mode_and_model_intent_preservation(self):
        """
        Ensures that optimizing token consumption and path shortening does NOT
        alter or disrupt !mode or !model semantics and resolutions.
        """
        # Coder model resolution remains intact
        coder_m = GlobalModelManager.get_role_model("coder")
        self.assertTrue(coder_m is not None and len(coder_m) > 0)

        # QA model resolution remains intact
        qa_m = GlobalModelManager.get_role_model("qa")
        self.assertTrue(qa_m is not None and len(qa_m) > 0)

        # Auditor model resolution remains intact
        auditor_m = GlobalModelManager.get_role_model("auditor")
        self.assertTrue(auditor_m is not None and len(auditor_m) > 0)

        # Status parsers remain 100% functional
        approved_text = "[STATUS: APPROVED]\n• 변경: ./bot.py 토큰 최적화 완료"
        self.assertTrue(TrioAgentRunner.is_qa_approved(approved_text))

        auditor_approved = "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n### 💬 사용자 질문 직접 답변\n완료되었습니다."
        self.assertTrue(TrioAgentRunner.is_auditor_approved(auditor_approved))


if __name__ == "__main__":
    unittest.main()
