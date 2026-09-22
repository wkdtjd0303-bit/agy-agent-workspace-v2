#!/usr/bin/env python3
"""
Unit tests for android_mcp_tools.py (Phase 1.5 Contract & Tools)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from android_mcp_tools import AndroidMCPToolLayer, ToolExecutionResult


class TestAndroidMCPTools(unittest.TestCase):

    def setUp(self):
        self.mock_emu = MagicMock()
        self.mcp = AndroidMCPToolLayer(emulator_manager=self.mock_emu, evidence_dir="/tmp/test_mcp_evidence")

    def test_navigate_page(self):
        # 1. Test semantic page navigation (페이지 1 이동)
        self.mock_emu.run_adb_cmd.return_value = (0, "", "")
        with patch.object(self.mcp, "get_ui_tree") as mock_tree:
            mock_tree.return_value = ToolExecutionResult(
                tool_name="get_ui_tree",
                success=True,
                data={"nodes": [{"text": "1페이지", "center": (100, 200)}]}
            )
            res = self.mcp.navigate_page(target_page=1)
            self.assertTrue(res.success)
            self.assertEqual(res.data["target_page"], 1)

    def test_restart_app(self):
        # 2. Test app restart & zero crash assertion (앱 재실행)
        self.mock_emu.run_adb_cmd.side_effect = [
            (0, "force-stop ok", ""), # force-stop
            (0, "am start ok", ""),      # am start
            (0, "logcat clean", "")      # logcat
        ]
        res = self.mcp.restart_app("com.pdfflow.app", "MainActivity")
        self.assertTrue(res.success)
        self.assertTrue(res.precondition_passed)
        self.assertTrue(res.postcondition_passed)
        self.assertTrue(res.assertions_passed)

    def test_verify_file(self):
        # 3. Test physical file existence and size assertion
        self.mock_emu.run_adb_cmd.return_value = (0, "-rw-rw---- 1 u0_a123 u0_a123 20480 2026-09-05 12:00 /sdcard/output.pdf", "")
        res = self.mcp.verify_file("/sdcard/output.pdf", min_bytes=1024)
        self.assertTrue(res.success)
        self.assertEqual(res.data["size_bytes"], 20480)

    def test_action_trace_checkpoint(self):
        # 4. Test reproducible action trace recording
        mock_assert = ToolExecutionResult(tool_name="verify_file", success=True, assertions_passed=True)
        chk = self.mcp.record_checkpoint(
            action_name="save_pdf",
            action_desc="PDF 파일 저장 및 검증",
            assertions=[mock_assert],
            before_screenshot="/tmp/before.png",
            after_screenshot="/tmp/after.png"
        )
        self.assertTrue(chk.success)
        self.assertEqual(len(self.mcp.trace_history), 1)

        trace_file = self.mcp.export_action_trace("/tmp/test_mcp_evidence")
        self.assertTrue(os.path.isfile(trace_file))

    def test_step_context_atomic_execution(self):
        # 5. Test StepContext atomic assertion binding
        mock_screen_res = ToolExecutionResult(
            tool_name="capture_screen",
            success=True,
            evidence_path="/tmp/test.png"
        )
        with patch.object(self.mcp, "capture_screen", return_value=mock_screen_res):
            with self.mcp.step_context("test_action", "Atomic test action") as ctx:
                result = ToolExecutionResult(
                    tool_name="test_action",
                    success=True,
                    assertions_passed=True,
                    precondition_passed=True,
                    postcondition_passed=True
                )
                ctx.set_action_result(result)
            self.assertEqual(len(self.mcp.trace_history), 1)
            self.assertTrue(self.mcp.trace_history[-1].success)

    def test_mcp_server_protocol(self):
        # 6. Test JSON-RPC 2.0 MCP Server handler
        from android_mcp_server import AndroidMCPServer
        server = AndroidMCPServer(self.mcp)
        
        # Test initialize
        init_res = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "test_client", "version": "1.0"}}
        })
        self.assertEqual(init_res["id"], 1)
        self.assertIn("serverInfo", init_res["result"])

        # Test tools/list
        list_res = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })
        tools = list_res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("tap_element", tool_names)
        self.assertIn("inspect_screen_vision", tool_names)
        self.assertIn("verify_file", tool_names)

    def test_intent_planner(self):
        # 7. Test Intent Planner workflow compilation
        from android_mcp_planner import TestIntentPlanner
        intent = TestIntentPlanner.create_intent_from_goal(
            "PDF 문서를 열고 편집한 뒤 저장하고 재실행 후 변경사항이 유지되는지 검증한다.",
            app_name="PDFflow",
            package_name="com.pdfflow.app"
        )
        self.assertGreaterEqual(len(intent.steps), 3)
        self.assertEqual(intent.steps[0].tool_name, "launch_app")
        self.assertTrue(any("Export" in cap or "Persistence" in cap for cap in intent.detected_capabilities))

    def test_mcp_client(self):
        # 8. Test AndroidMCPClient decoupled protocol invocation
        from android_mcp_server import AndroidMCPServer, AndroidMCPClient
        server = AndroidMCPServer(self.mcp)
        client = AndroidMCPClient(server=server)
        client.initialize()
        tools = client.list_tools()
        self.assertGreaterEqual(len(tools), 10)
        tool_names = [t["name"] for t in tools]
        self.assertIn("tap_element", tool_names)
        self.assertIn("launch_app", tool_names)

    def test_autonomous_blind_agent_decision(self):
        # 9. Test Autonomous Blind Agent dynamic decision logic
        from android_mcp_planner import AutonomousBlindAgent
        decision = AutonomousBlindAgent._decide_next_action(
            goal="페이지 2로 이동하여 텍스트를 입력하고 저장하라",
            step_id=1,
            visible_texts=["확인"],
            ui_nodes=[{"text": "확인", "class": "android.widget.Button"}],
            vision_data={"findings": []},
            history=[],
            package_name="com.example.app"
        )
        # Should detect roadblock/dialog popup "확인" and clear it
        self.assertEqual(decision["tool_name"], "tap_element")
        self.assertEqual(decision["arguments"]["text_or_id"], "확인")

    def test_autonomous_blind_agent_state_exploration(self):
        # 10. Test state-based exploration when no popups exist (Zero goal regexes)
        from android_mcp_planner import AutonomousBlindAgent
        decision = AutonomousBlindAgent._decide_next_action(
            goal="어떤 미지의 앱에 대한 자율 탐색 목표",
            step_id=2,
            visible_texts=["설정", "시작하기"],
            ui_nodes=[
                {"text": "설정", "class": "android.widget.Button", "clickable": True},
                {"text": "시작하기", "class": "android.widget.Button", "clickable": True}
            ],
            vision_data={"findings": ["Main screen rendered"]},
            history=[],
            package_name="com.unknown.app"
        )
        self.assertEqual(decision["tool_name"], "tap_element")
        self.assertEqual(decision["arguments"]["text_or_id"], "설정")

    def test_inspect_screen_vision_physical_analysis(self):
        # 11. Test real physical PIL inspection in inspect_screen_vision
        import tempfile
        from PIL import Image, ImageDraw
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            im = Image.new("RGB", (1080, 1920), color=(255, 255, 255))
            draw = ImageDraw.Draw(im)
            draw.rectangle([50, 50, 200, 200], fill=(0, 0, 255), outline=(0, 0, 0))
            draw.rectangle([300, 300, 500, 500], fill=(255, 0, 0))
            im.save(tmp.name)
            tmp_path = tmp.name


        with patch.object(self.mcp, "capture_screen", return_value=ToolExecutionResult(tool_name="capture_screen", success=True, evidence_path=tmp_path)):
            v_res = self.mcp.inspect_screen_vision("화면을 분석하라")
            self.assertTrue(v_res.success)
            self.assertIn("resolution", v_res.data)
            self.assertEqual(v_res.data["resolution"], "1080x1920")
            self.assertFalse(v_res.data["is_blank_canvas"])


    def test_inspect_screen_vision_downsampling(self):
        # 12. Verify PIL downsampling & JPEG compression for token minimization
        import tempfile
        from PIL import Image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            im = Image.new("RGB", (1080, 2400), color=(100, 150, 200))
            im.save(tmp.name)
            tmp_path = tmp.name

        captured_parts = []
        mock_resp = MagicMock()
        mock_resp.text = '{"vision_summary": "Optimized", "is_modal_or_dialog": false}'

        def fake_gen(*args, **kwargs):
            contents = kwargs.get("contents") or (args[1] if len(args) > 1 else [])
            captured_parts.extend(contents)
            return mock_resp

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_gen

        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_types.Part.from_bytes = lambda data, mime_type: MagicMock(data=data, mime_type=mime_type)
        mock_genai.types = mock_types
        mock_genai.Client.return_value = mock_client

        mock_google = MagicMock()
        mock_google.genai = mock_genai

        with patch.object(self.mcp, "capture_screen", return_value=ToolExecutionResult(tool_name="capture_screen", success=True, evidence_path=tmp_path)):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
                with patch.dict(sys.modules, {"google": mock_google, "google.genai": mock_genai, "google.genai.types": mock_types}):
                    res = self.mcp.inspect_screen_vision("Check screen")
                    self.assertTrue(res.success)
                    self.assertTrue(len(captured_parts) >= 2)
                    part = captured_parts[0]
                    # Verify JPEG compression and downsampling
                    self.assertEqual(part.mime_type, "image/jpeg")
                    # Check downsampled dimensions using PIL
                    import io
                    with Image.open(io.BytesIO(part.data)) as down_im:
                        self.assertEqual(down_im.size[0], 720)
                        self.assertEqual(down_im.size[1], 1600)

        try:
            os.remove(tmp_path)
        except OSError:
            pass

    def test_on_demand_vision_skips_when_ui_tree_sufficient(self):
        # 13. Verify On-Demand Vision skips expensive API calls when UI text is present
        from android_mcp_planner import AutonomousBlindAgent
        with patch.object(self.mcp, "launch_app", return_value=ToolExecutionResult(tool_name="launch_app", success=True)):
            with patch.object(self.mcp, "get_ui_tree", return_value=ToolExecutionResult(
                tool_name="get_ui_tree",
                success=True,
                data={"nodes": [
                    {"text": "문서 열기", "class": "android.widget.Button", "clickable": True},
                    {"text": "설정", "class": "android.widget.Button", "clickable": True}
                ]}
            )):
                with patch.object(self.mcp, "inspect_screen_vision") as mock_vis:
                    with patch.object(AutonomousBlindAgent, "_decide_next_action", return_value={
                        "goal_achieved": True,
                        "tool_name": "verify_app_state",
                        "arguments": {"package_name": "com.test.app"},
                        "intent": "Test finished"
                    }):
                        res = AutonomousBlindAgent.run_agentic_loop(
                            goal="문서 열기 버튼 클릭",
                            tool_layer=self.mcp,
                            package_name="com.test.app",
                            max_steps=1
                        )
                        self.assertTrue(res["goal_achieved"])
                        # inspect_screen_vision MUST NOT be called because UI tree has 2 texts and clickable node
                        mock_vis.assert_not_called()

    def test_vision_inspector_honest_error_handling(self):
        # 14. Verify AndroidVisionInspector returns success: False and NO fake scores on error
        from android_vision_inspector import AndroidVisionInspector
        import asyncio
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"dummy image data")
            tmp_path = tmp.name

        async def run_test():
            with patch.object(AndroidVisionInspector, "_analyze_with_direct_api", return_value=None):
                with patch("agent_runner.AgentRunner.run_prompt", side_effect=asyncio.TimeoutError("Timeout")):
                    result = await AndroidVisionInspector.analyze_screenshot(tmp_path, app_name="TestApp")
                    self.assertFalse(result["success"])
                    self.assertEqual(result["score"], 0)
                    self.assertIn("timed out", result["error"])
                    # Ensure NO fake mock texts like '🎨 [종합 시각 품질 점수]'
                    self.assertNotIn("analysis_text", result)

        try:
            asyncio.run(run_test())
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()




