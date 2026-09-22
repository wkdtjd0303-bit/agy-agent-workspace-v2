"""
Comprehensive unit tests for QA Wizard, Depth Configuration & Deep E2E Engine
"""

import unittest
import sys
import os
import json
import tempfile
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_wizard import (
    QAWizardState,
    QA_DEPTH_MAP,
    QA_FOCUS_GALLERY,
    QA_TIMING_MAP,
    QA_STRICTNESS_MAP,
    QAWizardInteractiveView
)
from qa_hub import resolve_target_and_apk, is_android_project, AgenticE2EOrchestrator


class TestQAWizardAndDeepE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state = QAWizardState(
            channel_id=123456789,
            user_id=987654321,
            workspace=self.temp_dir,
            initial_target="https://example.com"
        )

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_qa_depth_map_completeness(self):
        self.assertIn("deep_exploratory", QA_DEPTH_MAP)
        self.assertIn("chaos_stress", QA_DEPTH_MAP)
        self.assertIn("standard", QA_DEPTH_MAP)
        self.assertIn("quick_smoke", QA_DEPTH_MAP)

        for key, meta in QA_DEPTH_MAP.items():
            self.assertIn("label", meta)
            self.assertIn("desc", meta)
            self.assertIn("badge", meta)

    def test_save_and_load_depth_state(self):
        self.state.depth = "chaos_stress"
        self.state.focus = "perf_stability"
        self.state.strictness = "strict"
        self.state.timing = "post_task"
        
        saved_file = self.state.save_to_workspace()
        self.assertTrue(os.path.isfile(saved_file))

        # Create new state and verify loading
        loaded_state = QAWizardState(
            channel_id=123456789,
            user_id=987654321,
            workspace=self.temp_dir
        )
        self.assertEqual(loaded_state.depth, "chaos_stress")
        self.assertEqual(loaded_state.focus, "perf_stability")
        self.assertEqual(loaded_state.strictness, "strict")
        self.assertEqual(loaded_state.timing, "post_task")

    def test_to_prompt_instructions_includes_depth(self):
        self.state.depth = "deep_exploratory"
        prompt = self.state.to_prompt_instructions()
        self.assertIn("Deep Exploratory", prompt)
        self.assertIn("검증 심도 & 지속 시간", prompt)

    def test_interactive_view_build_embed(self):
        mock_sm = MagicMock()
        mock_bot = MagicMock()
        view = QAWizardInteractiveView(
            state=self.state,
            session_manager=mock_sm,
            bot_client=mock_bot
        )
        embed = view.build_embed()
        self.assertIsNotNone(embed)
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("검증 심도 & 지속 시간" in name for name in field_names))
        self.assertTrue(any("최우선 검증 초점" in name for name in field_names))

    def test_get_workspace_qa_config(self):
        from qa_hub import get_workspace_qa_config
        # Initially empty
        cfg = get_workspace_qa_config(self.temp_dir)
        self.assertEqual(cfg, {})

        # Save config
        self.state.depth = "deep_exploratory"
        self.state.strictness = "strict"
        self.state.save_to_workspace()

        cfg = get_workspace_qa_config(self.temp_dir)
        self.assertEqual(cfg.get("depth"), "deep_exploratory")
        self.assertEqual(cfg.get("strictness"), "strict")

    def test_run_intelligent_e2e_depth_branches(self):
        import asyncio
        # Create a sample test HTML file in temp_dir
        html_path = os.path.join(self.temp_dir, "index.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html>
<head><title>E2E Test App</title></head>
<body>
    <h1>AGY Test Dashboard</h1>
    <button id="btn1" onclick="document.getElementById('res').innerText='Clicked'">시작하기</button>
    <button id="btnSave" onclick="localStorage.setItem('saved', 'true')">저장</button>
    <div id="res"></div>
    <input type="text" id="inp" placeholder="입력" />
    <nav>
        <button class="tab-btn">목록</button>
        <button class="tab-btn">설정</button>
        <button class="tab-btn">통계</button>
    </nav>
</body>
</html>""")

        out_smoke = os.path.join(self.temp_dir, "out_smoke")
        out_chaos = os.path.join(self.temp_dir, "out_chaos")
        out_deep = os.path.join(self.temp_dir, "out_deep")

        async def run_depth_tests():
            # 1. Quick Smoke
            rep_smoke = await AgenticE2EOrchestrator.run_intelligent_e2e(
                url=html_path,
                output_dir=out_smoke,
                depth="quick_smoke"
            )
            self.assertEqual(len(rep_smoke.test_cases), 2)
            self.assertEqual(rep_smoke.test_cases[0].tc_id, "TC-01")
            self.assertEqual(rep_smoke.test_cases[1].tc_id, "TC-02")
            self.assertIn("스모크", rep_smoke.test_cases[1].title)

            # 2. Chaos Stress
            rep_chaos = await AgenticE2EOrchestrator.run_intelligent_e2e(
                url=html_path,
                output_dir=out_chaos,
                depth="chaos_stress"
            )
            tc_ids = [tc.tc_id for tc in rep_chaos.test_cases]
            self.assertIn("TC-03", tc_ids)
            self.assertIn("TC-04", tc_ids)
            self.assertIn("TC-05", tc_ids)
            self.assertTrue(any("카오스" in tc.title or "스트레스" in tc.title for tc in rep_chaos.test_cases))

            # 3. Deep Exploratory
            rep_deep = await AgenticE2EOrchestrator.run_intelligent_e2e(
                url=html_path,
                output_dir=out_deep,
                depth="deep_exploratory",
                workspace=self.temp_dir
            )
            self.assertTrue(len(rep_deep.test_cases) >= 8)
            deep_titles = [tc.title for tc in rep_deep.test_cases]
            self.assertTrue(any("폼 유효성" in t for t in deep_titles))
            self.assertTrue(any("영속성" in t for t in deep_titles))
            self.assertTrue(any("웹 접근성" in t for t in deep_titles))

        asyncio.run(run_depth_tests())

    def test_domain_context_extractor(self):
        from qa_hub import DomainContextExtractor
        # Test PDF domain extraction
        prd_file = os.path.join(self.temp_dir, "PRD.md")
        with open(prd_file, "w", encoding="utf-8") as f:
            f.write("# PdfFlow PRD\nREQ-001: PDF Document Rendering\nREQ-002: Inpainting Stamp")

        ctx = DomainContextExtractor.extract_context(self.temp_dir, dom_title="PdfFlow Editor")
        self.assertEqual(ctx.category, "productivity_editor")
        self.assertEqual(len(ctx.prd_requirements), 2)
        self.assertIn("서명", ctx.action_keywords)

    def test_smart_contextual_input_generator(self):
        from qa_hub import DomainContextExtractor, SmartContextualInputGenerator
        ctx = DomainContextExtractor.extract_context(self.temp_dir, dom_title="Test App")
        
        email_val = SmartContextualInputGenerator.generate_value({"type": "email", "name": "user_email"}, ctx)
        self.assertIn("@", email_val)

        search_val = SmartContextualInputGenerator.generate_value({"type": "text", "placeholder": "검색어를 입력하세요"}, ctx)
        self.assertTrue(len(search_val) > 0)

        phone_val = SmartContextualInputGenerator.generate_value({"type": "tel", "name": "phone_num"}, ctx)
        self.assertIn("010-", phone_val)


if __name__ == "__main__":
    unittest.main()
