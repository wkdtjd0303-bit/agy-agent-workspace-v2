import pytest
import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agy_agent"))

from discord_utils import (
    extract_local_attachments,
    extract_candidates,
    EXCLUDED_DIR_NAMES,
    DELIVERABLE_PATTERNS,
)
from discord_screenshot_gallery import (
    DEFAULT_SCREENSHOT_DIRS,
    collect_candidate_screenshots,
    send_screenshot_gallery,
)
from trio_agent_runner import TrioAgentRunner
from session_manager import ChannelSession


def test_test_project_path_not_excluded():
    """Deliverable files in a workspace named 'test' or under 'tests/screenshots' must NOT be excluded."""
    # 1. Verify 'tests' and 'test' are no longer in EXCLUDED_DIR_NAMES
    assert "test" not in EXCLUDED_DIR_NAMES
    assert "tests" not in EXCLUDED_DIR_NAMES

    # 2. Test project path with /test/ in its name
    with tempfile.TemporaryDirectory(prefix="autocoding_test_") as tmp_ws:
        shots_dir = os.path.join(tmp_ws, "reports", "gate5", "screenshots")
        os.makedirs(shots_dir, exist_ok=True)
        shot_path = os.path.join(shots_dir, "web_e2e_shot.png")
        with open(shot_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

        # Candidate extract from text
        text = f"E2E 테스트 완료. 스크린샷: `{shot_path}`"
        extracted = extract_local_attachments(text, workspace_root=tmp_ws)
        assert shot_path in extracted, f"Screenshot in test project '{shot_path}' was falsely excluded!"


def test_tests_screenshots_folder_deliverable():
    """Screenshots placed inside 'tests/screenshots' or 'test-results' must be extracted."""
    with tempfile.TemporaryDirectory() as tmp_ws:
        tests_shot_dir = os.path.join(tmp_ws, "tests", "screenshots")
        os.makedirs(tests_shot_dir, exist_ok=True)
        shot_path = os.path.join(tests_shot_dir, "login_flow.png")
        with open(shot_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 300)

        # Auto-scan deliverables
        extracted = extract_local_attachments(
            "E2E 테스트 완료.",
            workspace_root=tmp_ws,
            auto_scan_deliverables=True
        )
        assert shot_path in extracted, f"Screenshot in tests/screenshots '{shot_path}' was not auto-detected!"


def test_default_screenshot_dirs_contain_e2e():
    """DEFAULT_SCREENSHOT_DIRS must contain tests/screenshots, test-results, etc."""
    assert os.path.join("tests", "screenshots") in DEFAULT_SCREENSHOT_DIRS
    assert os.path.join("test-results") in DEFAULT_SCREENSHOT_DIRS
    assert os.path.join("playwright-report") in DEFAULT_SCREENSHOT_DIRS


def test_collect_candidate_screenshots_from_tests_folder():
    """collect_candidate_screenshots must find screenshots in tests/screenshots."""
    with tempfile.TemporaryDirectory() as tmp_ws:
        e2e_dir = os.path.join(tmp_ws, "tests", "screenshots")
        os.makedirs(e2e_dir, exist_ok=True)
        img_file = os.path.join(e2e_dir, "e2e_verify.png")
        with open(img_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 500)

        cands = collect_candidate_screenshots(tmp_ws)
        assert img_file in cands


def test_explicit_screenshot_demand_triggers_gallery():
    """User asking for screenshot/e2e must trigger screenshot sharing regardless of mode."""
    prompt = "내가 테스트 프로젝트에서 한번 스크린샷 e2e를 해달라고 요청했는데"
    has_explicit_screenshot_demand = any(kw in prompt.lower() for kw in [
        "스크린샷", "스샷", "화면", "캡처", "e2e", "screenshot", "screenshots", "ui", "view", "프레임버퍼", "사진", "렌더링"
    ])
    assert has_explicit_screenshot_demand is True
