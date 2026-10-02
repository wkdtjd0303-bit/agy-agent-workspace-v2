"""
test_brand_asset_binding.py
===========================
Image generator 가 생성/배치한 브랜드 배너 에셋이 **실제로** Discord UI(help overview
및 !brand 명령)에 바인딩되는지 검증한다.

검증 축:
  1. 에셋 실존 및 실이미지(PNG 1024x1024) 무결성
  2. brand_ui 경로 해석/첨부 객체 생성
  3. 임베드 바인딩(`attachment://`) 실동작
  4. help_manager overview 임베드 및 발송 파일 연동
  5. [정직성] 에셋 부재 시 유령 참조(ghost reference) 금지
"""
import os
import struct
import sys

import pytest

_AGY = os.path.join(os.path.dirname(__file__), "..", "agy_agent")
sys.path.insert(0, _AGY)

import brand_ui  # noqa: E402

_ASSET = os.path.join(
    os.path.dirname(__file__), "..", "assets", brand_ui._ASSET_BASENAME
)


# ----------------------------------------------------------------------
# 1. 에셋 실체 검증
# ----------------------------------------------------------------------
def test_brand_asset_exists_on_disk():
    """생성 에셋이 워크스페이스 assets/ 에 실제로 존재해야 한다."""
    assert os.path.isfile(_ASSET), f"브랜드 배너 에셋이 없습니다: {_ASSET}"
    assert os.path.getsize(_ASSET) > 1024


def test_brand_asset_is_real_png_1024():
    """에셋은 위조가 아닌 실제 PNG(1024x1024) 여야 한다 (헤더/IHDR 판정)."""
    with open(_ASSET, "rb") as f:
        header = f.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "PNG 매직바이트 불일치"
    width, height = struct.unpack(">II", header[16:24])
    assert (width, height) == (1024, 1024), f"예상 1024x1024, 실제 {width}x{height}"


# ----------------------------------------------------------------------
# 2. 경로 해석 / 첨부 파일
# ----------------------------------------------------------------------
def test_resolve_banner_path_finds_asset():
    resolved = brand_ui.resolve_banner_path()
    assert resolved is not None
    assert brand_ui.has_banner() is True
    assert resolved.name == brand_ui._ASSET_BASENAME


def test_attachment_name_is_stable_png():
    name = brand_ui.attachment_name()
    assert name and name.endswith(".png")


def test_banner_files_returns_discord_file():
    import discord

    files = brand_ui.banner_files()
    assert len(files) == 1
    assert isinstance(files[0], discord.File)
    assert files[0].filename == brand_ui.attachment_name()


# ----------------------------------------------------------------------
# 3. 임베드 바인딩
# ----------------------------------------------------------------------
def test_apply_brand_banner_binds_image():
    import discord

    embed = discord.Embed(title="test")
    assert brand_ui.apply_brand_banner(embed, mode="image") is True
    assert embed.image.url == f"attachment://{brand_ui.attachment_name()}"


def test_apply_brand_banner_thumbnail_mode():
    import discord

    embed = discord.Embed(title="test")
    assert brand_ui.apply_brand_banner(embed, mode="thumbnail") is True
    assert embed.thumbnail.url == f"attachment://{brand_ui.attachment_name()}"


def test_build_brand_embed_references_asset():
    embed = brand_ui.build_brand_embed()
    assert embed.image.url == f"attachment://{brand_ui.attachment_name()}"
    joined = " ".join(str(f.value) for f in embed.fields)
    assert brand_ui._ASSET_BASENAME in joined


# ----------------------------------------------------------------------
# 4. help_manager 실제 UI 연동
# ----------------------------------------------------------------------
def test_help_overview_embed_binds_banner():
    from help_manager import HelpManager

    embed = HelpManager.get_overview_embed()
    assert embed.image.url == f"attachment://{brand_ui.attachment_name()}"


def test_help_manager_exposes_brand_files():
    from help_manager import get_brand_files

    files = get_brand_files()
    assert len(files) == 1
    assert files[0].filename == brand_ui.attachment_name()


def test_help_view_get_files_returns_banner():
    import help_manager

    view = help_manager.HelpInteractiveView(initial_page="overview")
    files = view.get_files()
    assert len(files) == 1
    assert files[0].filename == brand_ui.attachment_name()


# ----------------------------------------------------------------------
# 5. [정직성] 에셋 부재 시 유령 참조 금지
# ----------------------------------------------------------------------
def test_missing_asset_produces_no_ghost_reference(monkeypatch):
    import discord

    monkeypatch.setattr(brand_ui, "resolve_banner_path", lambda: None)
    assert brand_ui.has_banner() is False
    assert brand_ui.banner_files() == []

    embed = discord.Embed(title="x")
    assert brand_ui.apply_brand_banner(embed) is False
    assert embed.image.url is None  # 유령 참조 없음

    fallback = brand_ui.build_brand_embed()
    assert fallback.image.url is None
    assert "미배치" in " ".join(f.name for f in fallback.fields)
