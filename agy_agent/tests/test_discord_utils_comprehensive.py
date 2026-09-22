import os
import sys
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from discord_utils import clean_discord_links, split_message, format_tool_status


class TestDiscordUtilsComprehensive(unittest.TestCase):
    """
    Extensive test suite for discord_utils boundary conditions, edge cases,
    massive chunking, and markdown code block persistence.
    """

    def test_clean_discord_links_edge_cases(self):
        # Raw url label should be unwrapped
        raw = "[https://github.com/foo/bar](https://github.com/foo/bar)"
        self.assertEqual(clean_discord_links(raw), "https://github.com/foo/bar")

        # Descriptive label should be preserved
        descriptive = "[GitHub Repo](https://github.com/foo/bar)"
        self.assertEqual(clean_discord_links(descriptive), "[GitHub Repo](https://github.com/foo/bar)")

        # Mixed links in a single text
        mixed = "Check [https://google.com](https://google.com) and [Docs](https://docs.google.com)"
        cleaned = clean_discord_links(mixed)
        self.assertEqual(cleaned, "Check https://google.com and [Docs](https://docs.google.com)")

        # Non-string / None / Empty inputs
        self.assertEqual(clean_discord_links(""), "")
        self.assertEqual(clean_discord_links(None), "")
        self.assertEqual(clean_discord_links(12345), "12345")

    def test_split_message_short_text(self):
        self.assertEqual(split_message(""), [])
        self.assertEqual(split_message(None), [])
        self.assertEqual(split_message("Hello World", limit=100), ["Hello World"])

    def test_split_message_massive_unbroken_line(self):
        long_line = "A" * 5000
        chunks = split_message(long_line, limit=1000)
        self.assertTrue(len(chunks) >= 5)
        for c in chunks:
            self.assertLessEqual(len(c), 1000)

    def test_split_message_code_block_fence_preservation(self):
        code_body = "\n".join([f"def line_{i}(): pass" for i in range(100)])
        full_text = f"```python\n{code_body}\n```"
        chunks = split_message(full_text, limit=500)
        self.assertGreater(len(chunks), 1)

        # Every intermediate chunk must properly close and reopen the code fence
        for i, c in enumerate(chunks):
            self.assertLessEqual(len(c), 500)
            if i == 0:
                self.assertTrue(c.startswith("```python"))
                self.assertTrue(c.endswith("```"))
            elif i == len(chunks) - 1:
                self.assertTrue(c.startswith("```python"))
                self.assertTrue(c.endswith("```"))
            else:
                self.assertTrue(c.startswith("```python"))
                self.assertTrue(c.endswith("```"))

    def test_format_tool_status_all_tools(self):
        # write_to_file
        status = format_tool_status("write_to_file", {"parameters": {"TargetFile": "src/App.tsx"}})
        self.assertIn("src/App.tsx", status)
        self.assertIn("파일 생성", status)

        # replace_file_content
        status = format_tool_status("replace_file_content", {"parameters": {"TargetFile": "src/index.css"}})
        self.assertIn("src/index.css", status)
        self.assertIn("파일 수정", status)

        # run_command
        status = format_tool_status("run_command", {"parameters": {"CommandLine": "npm test"}})
        self.assertIn("npm test", status)
        self.assertIn("명령어 실행", status)

        # grep_search
        status = format_tool_status("grep_search", {"parameters": {"Query": "TODO"}})
        self.assertIn("TODO", status)

        # search_web
        status = format_tool_status("search_web", {"parameters": {"query": "Vite React"}})
        self.assertIn("Vite React", status)

        # Non-dict or empty info
        status = format_tool_status("unknown_tool", None)
        self.assertIn("unknown_tool", status)

    def test_split_message_invalid_limit_types(self):
        sample_text = "This is a normal message that needs splitting safely."
        # String non-int limit
        chunks = split_message(sample_text, limit="invalid_string")
        self.assertEqual(len(chunks), 1)
    def test_resolve_app_name(self):
        from discord_utils import resolve_app_name
        self.assertEqual(resolve_app_name("PdfFlow.apk"), "PdfFlow")
        self.assertEqual(resolve_app_name("PdfFlow-latest.apk"), "PdfFlow")
        self.assertEqual(resolve_app_name("PdfFlow-20260901_190457.apk"), "PdfFlow")
        self.assertEqual(resolve_app_name("app-release.apk", workspace_root="/home/user/pdfediter_andriod"), "PdfediterAndriod")
        self.assertEqual(resolve_app_name("app-debug.apk", workspace_root="/home/user/!1_vibecoding/pdfediter_andriod"), "PdfediterAndriod")

    def test_ensure_timestamped_deliverable(self):
        import tempfile
        import shutil
        from discord_utils import ensure_timestamped_deliverable

        temp_dir = tempfile.mkdtemp()
        try:
            apk_path = os.path.join(temp_dir, "PdfFlow.apk")
            with open(apk_path, "wb") as f:
                f.write(b"dummy apk content" * 1000)

            ts_name, latest_name, ts_path, latest_path = ensure_timestamped_deliverable(apk_path, workspace_root=temp_dir)
            self.assertTrue(ts_name.startswith("PdfFlow-20"))
            self.assertTrue(ts_name.endswith(".apk"))
            self.assertEqual(latest_name, "PdfFlow-latest.apk")
            self.assertTrue(os.path.isfile(ts_path))
            self.assertTrue(os.path.isfile(latest_path))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_prepare_message_payload_timestamped(self):
        import tempfile
        import shutil
        from discord_utils import prepare_message_payload, DISCORD_MAX_FILE_BYTES

        temp_dir = tempfile.mkdtemp()
        try:
            large_apk = os.path.join(temp_dir, "PdfFlow.apk")
            with open(large_apk, "wb") as f:
                f.seek(DISCORD_MAX_FILE_BYTES + 1024)
                f.write(b"\0")

            response_text = f"Here is the build: `{large_apk}`"
            chunks, files = prepare_message_payload(response_text, workspace_root=temp_dir)
            combined = "".join(chunks)
            self.assertIn("대용량 파일 안내", combined)
            self.assertIn("PdfFlow-latest.apk", combined)
            self.assertIn("PdfFlow-20", combined)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_ensure_timestamped_deliverable_cross_workspace_isolation(self):
        import tempfile
        import shutil
        from discord_utils import ensure_timestamped_deliverable

        agent_ws = tempfile.mkdtemp(prefix="agent_ws_")
        external_ws = tempfile.mkdtemp(prefix="external_project_")
        try:
            ext_apk = os.path.join(external_ws, "MyTestApp.apk")
            with open(ext_apk, "wb") as f:
                f.write(b"APK_MOCK_CONTENT")

            ts_name, latest_name, ts_path, latest_path = ensure_timestamped_deliverable(
                ext_apk, workspace_root=agent_ws
            )
            # Must stay inside external_ws, NOT leaked into agent_ws!
            self.assertTrue(ts_path.startswith(external_ws))
            self.assertTrue(latest_path.startswith(external_ws))
            self.assertFalse(ts_path.startswith(agent_ws))
            self.assertFalse(latest_path.startswith(agent_ws))
            self.assertEqual(len(os.listdir(agent_ws)), 0)
        finally:
            shutil.rmtree(agent_ws, ignore_errors=True)
            shutil.rmtree(external_ws, ignore_errors=True)


    def test_extract_candidates_ignores_source_code_and_docs(self):
        import tempfile, shutil
        from discord_utils import extract_candidates, extract_local_attachments

        temp_dir = tempfile.mkdtemp(prefix="source_ignore_test_")
        try:
            # Create dummy source files
            py_file = os.path.join(temp_dir, "autonomy_guard.py")
            txt_file = os.path.join(temp_dir, "질문.txt")
            md_file = os.path.join(temp_dir, "README.md")
            apk_file = os.path.join(temp_dir, "app-release.apk")
            png_file = os.path.join(temp_dir, "screenshot.png")
            for f in (py_file, txt_file, md_file):
                with open(f, "w") as fp:
                    fp.write("dummy code content")
            with open(apk_file, "wb") as fp:
                fp.write(b"PK0304dummy")
            with open(png_file, "wb") as fp:
                fp.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

            text = f"I modified `{py_file}` and read `{txt_file}` and `{md_file}`. Here is `{apk_file}` and `{png_file}`."
            cands = extract_candidates(text, workspace_root=temp_dir)
            
            # Python, txt, md must NEVER be extracted as attachment candidates
            self.assertNotIn(py_file, cands)
            self.assertNotIn(txt_file, cands)
            self.assertNotIn(md_file, cands)

            # Deliverables (apk, png) MUST be extracted
            self.assertIn(apk_file, cands)
            self.assertIn(png_file, cands)

            valid_atts = extract_local_attachments(text, workspace_root=temp_dir)
            self.assertNotIn(py_file, valid_atts)
            self.assertNotIn(txt_file, valid_atts)
            self.assertNotIn(md_file, valid_atts)
            self.assertIn(apk_file, valid_atts)
            self.assertIn(png_file, valid_atts)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_format_quote_reference_bar_sanitization(self):
        from discord_utils import format_quote_reference_bar

        # Unclosed backtick or code fence inside quote must be sanitized
        raw_quote = "Look at this `unclosed code block and ```fence"
        res = format_quote_reference_bar(raw_quote, author="Tester", max_length=50)
        self.assertNotIn("`", res, "Backticks must be converted to single quotes!")
        self.assertTrue(res.startswith("> 💬 **@Tester** Look at this 'unclosed"))

    def test_safe_send_discord_message_splits_long_quote_content(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from discord_utils import safe_send_discord_message

        mock_target = MagicMock()
        mock_target.send = AsyncMock(return_value=MagicMock())

        # Content of length 1880 + quote reference of length 100 would exceed 1900 chars
        content = "X" * 1880
        quote = "Y" * 100

        async def run_test():
            res = await safe_send_discord_message(
                mock_target,
                content=content,
                quote_reference=quote
            )
            self.assertIsNotNone(res)
            # Should have split into multiple calls because total length > 1900
            self.assertGreater(mock_target.send.call_count, 1)
            for call_args in mock_target.send.call_args_list:
                sent_content = call_args.kwargs.get("content", "")
                self.assertLessEqual(len(sent_content), 1900)

        asyncio.run(run_test())

    def test_safe_send_discord_message_fallback_on_file_error(self):
        import asyncio
        import discord
        from unittest.mock import AsyncMock, MagicMock
        from discord_utils import safe_send_discord_message

        mock_target = MagicMock()
        
        # First call with files fails with 413, second call without files succeeds
        call_count = 0
        async def mock_send(**kwargs):
            nonlocal call_count
            call_count += 1
            if "files" in kwargs and kwargs["files"]:
                resp = MagicMock()
                resp.status = 413
                resp.reason = "Payload Too Large"
                raise discord.errors.HTTPException(resp, "Request Entity Too Large")
            mock_msg = MagicMock()
            mock_msg.content = kwargs.get("content", "")
            return mock_msg

        mock_target.send = AsyncMock(side_effect=mock_send)

        dummy_file = MagicMock(spec=discord.File)
        dummy_file.filename = "heavy.bin"
        dummy_file.fp = b"123"

        async def run_test():
            res = await safe_send_discord_message(
                mock_target,
                content="Important Coder Answer",
                files=[dummy_file]
            )
            self.assertIsNotNone(res)
            self.assertEqual(res.content, "Important Coder Answer")
            self.assertEqual(call_count, 2)

    def test_sanitize_attachment_filename_errno_36_reproduction_and_fix(self):
        import tempfile
        from discord_utils import sanitize_attachment_filename

        err_filename = (
            "ACRwjavs7xm5oO1bxDtUB-VtqNKFhbBMzxkNso9brcLrW2srlPkDDI69Ql4BLPG9-"
            "rR0cDn4uXnANVjGP-JXUTXN5bQnf--uDpyl3nyoL55gkGc9COCA2wTNX_t2zv5-"
            "llhzv29VxPUh2LjOr_UsbGPQXs3zVcA1w22bPk2YK8_EYJ2kSvWHbYkpu981GEOlK7"
            "CmADv_cIk_uDt91Oj5pNquq4lFV5h6CmLw8_pMJMlOgWhuCN5Ie8nk0s_TM1D6FAy5"
            "5kh7DVyhBXTwALefSLv_JS3-VG8xbfUtpuo.png"
        )
        self.assertGreater(len(err_filename), 255)

        # Before sanitization: raw filename would cause Errno 36 on Linux ext4
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(OSError):
                with open(os.path.join(td, err_filename), "wb") as f:
                    f.write(b"data")

            # After sanitization: safe filename within 150 bytes, extension preserved, no Errno 36
            safe_name = sanitize_attachment_filename(err_filename)
            self.assertLessEqual(len(safe_name.encode("utf-8")), 150)
            self.assertTrue(safe_name.endswith(".png"))

            safe_path = os.path.join(td, safe_name)
            with open(safe_path, "wb") as f:
                f.write(b"safe_image_bytes")
            self.assertTrue(os.path.isfile(safe_path))

    def test_sanitize_attachment_filename_security_and_multibyte(self):
        from discord_utils import sanitize_attachment_filename

        # Path traversal prevention
        self.assertEqual(sanitize_attachment_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_attachment_filename("..\\..\\windows\\win.ini"), "win.ini")

        # Dangerous characters sanitized
        res_chars = sanitize_attachment_filename('bad:file*name?"<>.txt')
        self.assertNotIn(":", res_chars)
        self.assertNotIn("*", res_chars)
        self.assertNotIn("?", res_chars)
        self.assertNotIn('"', res_chars)
        self.assertNotIn("<", res_chars)
        self.assertNotIn(">", res_chars)
        self.assertTrue(res_chars.endswith(".txt"))

        # Multibyte Korean long name
        korean_long = "디스코드_첨부_이미지_한글_테스트_" * 20 + ".webp"
        safe_kor = sanitize_attachment_filename(korean_long)
        self.assertLessEqual(len(safe_kor.encode("utf-8")), 150)
        self.assertTrue(safe_kor.endswith(".webp"))

        # Edge cases: None and empty
        self.assertTrue(sanitize_attachment_filename(None).endswith(".bin"))
        self.assertTrue(sanitize_attachment_filename("").endswith(".bin"))


if __name__ == "__main__":
    unittest.main()

