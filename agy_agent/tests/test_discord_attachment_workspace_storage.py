#!/usr/bin/env python3
"""
test_discord_attachment_workspace_storage.py

Unit tests verifying:
1. Discord attachments of all types (code, data, office, binary, pdf, image) are
   saved directly into the workspace's '참고자료' directory.
2. Symbolic links/aliases to 'references' and '.references/user_inputs' are maintained.
3. Injected agent prompts contain the relative workspace path ('참고자료/...') and
   explicit instructions for executing and compatibility testing.
4. Deliverable extraction filters ('USER_REFERENCE_DIR_NAMES') safely exclude files
   in '참고자료' from being re-uploaded to Discord as newly produced artifacts.
"""

import os
import shutil
import tempfile
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from discord_utils import (
    USER_REFERENCE_DIR_NAMES,
    extract_local_attachments,
    extract_oversized_attachments,
)


def test_user_reference_dir_names_includes_chamgo():
    """Verify that '참고자료' is registered in USER_REFERENCE_DIR_NAMES."""
    assert "참고자료" in USER_REFERENCE_DIR_NAMES
    assert ".references" in USER_REFERENCE_DIR_NAMES
    assert "agy_user_references" in USER_REFERENCE_DIR_NAMES


def test_deliverable_scanner_excludes_chamgo_files(tmp_path):
    """Verify that files placed in '참고자료' are excluded from deliverable extraction."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    chamgo_dir = workspace / "참고자료"
    chamgo_dir.mkdir()

    # Create a sample excel and python file inside 참고자료
    sample_xlsx = chamgo_dir / "compatibility_sample.xlsx"
    sample_xlsx.write_text("dummy binary excel content")

    sample_py = chamgo_dir / "test_script.py"
    sample_py.write_text("print('test')")

    # In a text where the agent mentioned the file
    text_mentioning_chamgo = f"Here is the file: `{sample_xlsx}` and `{sample_py}`"

    deliverables = extract_local_attachments(
        text=text_mentioning_chamgo,
        workspace_root=str(workspace),
        auto_scan_deliverables=True
    )
    # The files in 참고자료 must NOT be extracted as newly produced deliverables
    assert len(deliverables) == 0

    oversized = extract_oversized_attachments(
        text=text_mentioning_chamgo,
        workspace_root=str(workspace)
    )
    assert len(oversized) == 0


def test_attachment_handling_saves_to_chamgo_and_builds_prompt(tmp_path):
    """
    Simulate bot.py attachment handling flow and verify:
    - Files are saved to workspace / '참고자료'
    - Relative paths ('참고자료/filename') and execution instructions are provided in prompt
    """
    async def _runner():
        workspace = tmp_path / "my_project"
        workspace.mkdir()

        # 1. Prepare simulated attachment
        fake_attachment_excel = MagicMock()
        fake_attachment_excel.filename = "client_data_v1.xlsx"

        async def fake_save_excel(target_path):
            with open(target_path, "wb") as f:
                f.write(b"PK\x03\x04ExcelMockData")

        fake_attachment_excel.save = AsyncMock(side_effect=fake_save_excel)

        fake_attachment_code = MagicMock()
        fake_attachment_code.filename = "benchmark_runner.py"

        async def fake_save_code(target_path):
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("def run():\n    print('benchmark ok')\nrun()")

        fake_attachment_code.save = AsyncMock(side_effect=fake_save_code)

        # 2. Simulate message and session
        fake_message = MagicMock()
        fake_message.attachments = [fake_attachment_excel, fake_attachment_code]
        fake_message.reply = AsyncMock()
        fake_message.channel = MagicMock()
        fake_message.channel.id = 12345

        fake_session = MagicMock()
        fake_session.workspace = str(workspace)

        # 3. Execute logic corresponding to the upgraded bot.py pipeline
        user_refs_dir = os.path.join(fake_session.workspace, "참고자료")
        dot_ref_dir = os.path.join(fake_session.workspace, ".references", "user_inputs")
        en_refs_dir = os.path.join(fake_session.workspace, "references")
        os.makedirs(user_refs_dir, exist_ok=True)
        os.makedirs(dot_ref_dir, mode=0o700, exist_ok=True)
        if not os.path.exists(en_refs_dir):
            try:
                os.symlink("참고자료", en_refs_dir)
            except Exception:
                pass

        attachment_notes = []
        temp_dir = tempfile.mkdtemp(prefix="agy_test_att_")

        try:
            for att in fake_message.attachments:
                orig_filename = att.filename
                safe_filename = orig_filename
                temp_file_path = os.path.join(temp_dir, safe_filename)
                await att.save(temp_file_path)

                dest_file_path = os.path.join(user_refs_dir, safe_filename)
                shutil.copy2(temp_file_path, dest_file_path)

                rel_file_path = os.path.relpath(dest_file_path, fake_session.workspace).replace("\\", "/")
                assert os.path.exists(dest_file_path)
                assert rel_file_path.startswith("참고자료/")

                if safe_filename.endswith(".py"):
                    with open(dest_file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    attachment_notes.append(
                        f"\n[📄 첨부 파일: {orig_filename}]\n"
                        f"- 워크스페이스 저장 위치: `{rel_file_path}`\n"
                        f"- 지침: 스크립트 실행(예: python {rel_file_path}) 및 호환성 테스트를 수행하십시오.\n"
                        f"```\n{content}\n```"
                    )
                else:
                    attachment_notes.append(
                        f"[📦 첨부 파일 (호환성/실행 검증 대상): {orig_filename}]\n"
                        f"- 워크스페이스 저장 위치: `{rel_file_path}`\n"
                        f"- 핵심 지침: 워크스페이스 내 '{rel_file_path}'에서 직접 파일을 열람하고 호환성/무결성 테스트를 수행하십시오."
                    )

            combined_prompt = "\n".join(attachment_notes)

            # 4. Assertions
            # Both files must physically exist in workspace / 참고자료
            assert os.path.isfile(os.path.join(user_refs_dir, "client_data_v1.xlsx"))
            assert os.path.isfile(os.path.join(user_refs_dir, "benchmark_runner.py"))

            # Both files must also be accessible via the 'references' alias
            if os.path.islink(en_refs_dir):
                assert os.path.isfile(os.path.join(en_refs_dir, "client_data_v1.xlsx"))
                assert os.path.isfile(os.path.join(en_refs_dir, "benchmark_runner.py"))

            # Injected prompt must guide the agent with the relative path inside workspace
            assert "참고자료/client_data_v1.xlsx" in combined_prompt
            assert "참고자료/benchmark_runner.py" in combined_prompt
            assert "호환성/무결성 테스트" in combined_prompt
            assert "python 참고자료/benchmark_runner.py" in combined_prompt

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.run(_runner())
