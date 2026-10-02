"""하드코딩/SSOT 감사 회귀 테스트.

2026-10-01 점검 라운드에서 발견/수정한 '불필요한 하드코딩' 결함을 고정한다.

1. Auditor 최종 감사 트랜짓 배너의 stale 하드코딩 멘션 ID 제거 (SSOT: AUDITOR_MENTION_USER_ID)
2. discord_cli 채널 표시명 오표기 수정 (1538529408703266902 == ANY QUESTION)
3. safe_trash 기본 베이스 경로의 사용자명 하드코딩 제거 (홈 디렉토리 유도)
4. qa_hub 폴백 워크스페이스의 절대경로 하드코딩 제거 (config SSOT 사용)
5. AutoPilot 감사 모델 표시명의 하드코딩 제거 (format_model_name SSOT)
6. bot.py 의 qa2 봇 멘션 하드코딩 제거 (동적 user.id 사용)
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
import discord_cli  # noqa: E402
import safe_trash  # noqa: E402
from model_manager import GlobalModelManager  # noqa: E402

_AGENT_DIR = os.path.join(os.path.dirname(__file__), "..")


def _read(name: str) -> str:
    with open(os.path.join(_AGENT_DIR, name), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def test_no_stale_auditor_mention_hardcode():
    """최종 감사 배너는 하드코딩 ID가 아니라 AUDITOR_MENTION_USER_ID SSOT를 써야 한다."""
    src = _read("trio_agent_runner.py")
    assert "1539459595133521930" not in src, (
        "stale 하드코딩 Auditor 멘션 ID(1539459595133521930)가 남아 있습니다. "
        "AUDITOR_MENTION_USER_ID 를 사용하세요."
    )
    assert "⚖️ <@{AUDITOR_MENTION_USER_ID}>" in src


def test_discord_cli_channel_label_matches_config():
    """1538529408703266902 는 admin/ANY QUESTION 채널이다 (!5_agy_agent 아님)."""
    label = discord_cli.CHANNEL_NICKNAMES.get("1538529408703266902")
    assert label == "ANY QUESTION"
    assert config.ADMIN_CHANNEL_ID == 1538529408703266902


def test_safe_trash_base_is_home_derived_not_username_hardcoded():
    expected = os.path.join(os.path.expanduser("~"), "Desktop", "autocoding")
    assert safe_trash.DEFAULT_AUTOCODING_BASE == expected


def test_qa_hub_fallback_workspace_has_no_absolute_home_literal():
    src = _read("qa_hub.py")
    assert '"/home/jangsung/Desktop/autocoding/!9_qa_hub"' not in src
    assert "config.DEFAULT_CHANNEL_WORKSPACES.get(" in src


def test_autopilot_model_display_uses_ssot_formatter():
    import auto_pilot

    assert auto_pilot.get_autopilot_model_display() == GlobalModelManager.format_model_name(
        config.QA_AUTO_MODEL
    )
    # 기본 설정(gemini-3.1-pro-high)에서 기존 'High' 접미사 하드코딩이 사라졌는지 확인
    assert auto_pilot.get_autopilot_model_display() == "Gemini 3.1 Pro"


def test_bot_qa2_mention_strip_is_dynamic():
    src = _read("bot.py")
    assert '"<@1545711652987600937>"' not in src
    assert 'f"<@{qa2_bot.user.id}>"' in src
