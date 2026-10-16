#!/usr/bin/env python3
"""
Test suite for REQ-001 Cross-Platform E2E enhancements:
1. Subdirectory test discovery in AutonomousE2ELoop (zero-config root execution).
2. Safe coroutine execution in CrossPlatformE2ERunner under active event loops.
3. Async native cross-platform E2E orchestration.
4. Playwright environment limitation classification as BLOCKED.
"""

import os
import sys
import asyncio
import pytest
from unittest.mock import patch, MagicMock

current_dir = os.path.dirname(os.path.abspath(__file__))
pkg_dir = os.path.dirname(current_dir)
if pkg_dir not in sys.path:
    sys.path.insert(0, pkg_dir)

from autonomous_e2e_loop import detect_test_commands
from cross_platform_e2e_runner import CrossPlatformE2ERunner, _safe_run_coroutine
from mechanical_gate import MechanicalGate, MechanicalCheck
import role_authority


def test_detect_test_commands_finds_subpackage_tests(tmp_path):
    """Ensure detect_test_commands finds pytest in candidate subdirectories when root has no tests folder."""
    # Create a subpackage with a tests folder
    subpkg = tmp_path / "my_app"
    subpkg.mkdir()
    (subpkg / "tests").mkdir()
    (subpkg / "tests" / "test_core.py").write_text("def test_dummy(): pass\n", encoding="utf-8")

    cmds = detect_test_commands(str(tmp_path))
    names = [c["name"] for c in cmds]
    assert "pytest:my_app" in names
    spec = next(c for c in cmds if c["name"] == "pytest:my_app")
    assert "my_app" in spec["argv"]


def test_detect_test_commands_ignores_hidden_and_venv(tmp_path):
    """Ensure hidden folders, .venv, and node_modules are ignored when scanning for tests."""
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "tests").mkdir()
    (venv_dir / "tests" / "test_ignore.py").write_text("def test_x(): pass\n", encoding="utf-8")

    cmds = detect_test_commands(str(tmp_path))
    assert cmds == []


def test_safe_run_coroutine_without_loop():
    """Verify _safe_run_coroutine runs a coroutine synchronously when no event loop is active."""
    async def sample_coro():
        await asyncio.sleep(0.01)
        return "sync_result"

    res = _safe_run_coroutine(sample_coro())
    assert res == "sync_result"


def test_safe_run_coroutine_with_active_loop():
    """Verify _safe_run_coroutine runs without raising RuntimeError even inside an active asyncio loop."""
    async def outer():
        # Inside an active running loop
        async def inner():
            await asyncio.sleep(0.01)
            return "nested_loop_result"

        # Calling _safe_run_coroutine while the loop is running
        return _safe_run_coroutine(inner())

    res = asyncio.run(outer())
    assert res == "nested_loop_result"


def test_run_unified_cross_platform_e2e_async_web(tmp_path):
    """Verify run_unified_cross_platform_e2e_async executes for web platform."""
    mock_web_result = {
        "platform": "WEB",
        "target": "http://127.0.0.1:8080",
        "actions": [{"action": "navigate", "url": "http://127.0.0.1:8080"}],
        "screenshots": [],
        "success": True,
        "mechanical_gate": {"verdict": "PASS"}
    }

    async def _test():
        with patch.object(CrossPlatformE2ERunner, "run_web_e2e", return_value=mock_web_result):
            return await CrossPlatformE2ERunner.run_unified_cross_platform_e2e_async(
                target="http://127.0.0.1:8080",
                platform_override="WEB",
                workspace=str(tmp_path)
            )

    res = asyncio.run(_test())
    assert res["platform"] == "WEB"
    assert res["target"] == "http://127.0.0.1:8080"


def test_run_web_e2e_missing_executable_marked_blocked(tmp_path):
    """Verify environment failures (e.g. Playwright browser missing) are marked as BLOCKED."""
    async def _test():
        with patch("playwright.async_api.async_playwright") as mock_pw:
            mock_pw.side_effect = ModuleNotFoundError("No module named 'playwright'")
            return await CrossPlatformE2ERunner.run_web_e2e(
                target_url="http://127.0.0.1:8080",
                workspace=str(tmp_path),
                capture_screenshots=False
            )

    res = asyncio.run(_test())
    assert res["success"] is False
    gate = res["mechanical_gate"]
    assert gate["verdict"] in ("BLOCKED", "FAIL")
    web_check = next((c for c in gate["checks"] if c["name"] == "web_navigation_success"), None)
    assert web_check is not None
    assert web_check["status"] == "BLOCKED"
    assert web_check["is_blocked"] is True


def test_coder_role_authority_rejects_verdict_tags():
    """Verify that role_authority prevents Coder role from asserting [STATUS: APPROVED]."""
    coder_output_with_verdict = (
        "[STATUS: APPROVED]\n"
        "### 구현 완료 보고서\n"
        "모든 기능이 성공적으로 구현되었습니다."
    )
    is_inverted, reason = role_authority.detect_coder_role_inversion(coder_output_with_verdict)
    assert is_inverted is True
    assert "Coder 에이전트가 소스코드 구현 대신 QA/감사관 판정 태그" in reason

    clean, removed = role_authority.strip_coder_verdict_tags(coder_output_with_verdict)
    assert "[STATUS: APPROVED]" not in clean
    assert "[STATUS: APPROVED]" in removed
    assert "### 구현 완료 보고서" in clean
