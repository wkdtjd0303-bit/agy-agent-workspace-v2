#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brand_ui.py
===========
AGY 프로젝트 **브랜드 에셋 레지스트리 & Discord UI 바인더**.

Image generator 도구가 생성/배치한 브랜드 배너 에셋
(`assets/ai_IMAGE_EDIT_56348.png`)을 실제 사용자 인터페이스(Discord 임베드)에
연결한다.

바인딩 대상 (실제 사용자 노출 UI):
  1. `!help` / `!도움말` 종합 홈(overview) 임베드 상단 배너 이미지
  2. `!brand` / `!브랜드` 전용 브랜드 배너 쇼케이스 명령

[정직성 원칙]
  - 에셋이 디스크에 실제로 존재할 때만 `attachment://` 참조를 생성한다.
    (존재하지 않으면 조용히 no-op → '유령 참조' 원천 차단)
  - 출처(provenance)를 숨기지 않는다. 재생성 실패로 디스크의 기존 실이미지를
    재사용한 경우에도 그 사실을 임베드에 그대로 표기한다.

[CLI/Daemon 프로필 준수]
  - HTML/CSS/프론트엔드 파일을 생성하지 않는다. 순수 Python + Discord SDK 바인딩.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

# 생성 에셋 기본 파일명 (Image generator 결과 반환 메시지 기준)
_ASSET_BASENAME = "ai_IMAGE_EDIT_56348.png"
# Discord `attachment://` 참조에 쓸 안정적인 첨부 파일명 (원본 파일명과 분리)
_ATTACH_FILENAME = "agy_brand_banner.png"

# 출처 표기 (정직 고지)
PROVENANCE = "reused_existing_local_asset"
PROVENANCE_LABEL = "⚠️ 기존 로컬 실이미지 재사용 (AI 재생성 실패 대체)"


def _candidate_asset_paths() -> List[Path]:
    """에셋 후보 경로 목록 (앞에서부터 우선순위)."""
    cands: List[Path] = []

    # 1) 명시적 환경변수 오버라이드
    env_path = os.getenv("AGY_BRAND_ASSET", "").strip()
    if env_path:
        cands.append(Path(env_path).expanduser())

    here = Path(__file__).resolve()          # <...>/agy_agent/brand_ui.py
    project_root = here.parent.parent        # 실제 프로젝트 루트
    workspace_root = project_root.parent     # 상위 autocoding 루트

    # 2) 동일 프로젝트 루트의 assets/
    cands.append(project_root / "assets" / _ASSET_BASENAME)
    # 3) 심볼릭 링크 워크스페이스(!5_agy_agent)의 assets/
    cands.append(workspace_root / "!5_agy_agent" / "assets" / _ASSET_BASENAME)
    # 4) 현재 작업 디렉토리 기준 assets/
    cands.append(Path.cwd() / "assets" / _ASSET_BASENAME)
    # 5) 프로젝트 루트 바로 아래 (생성물이 루트에 떨어진 경우)
    cands.append(project_root / _ASSET_BASENAME)

    # 중복 제거 (순서 보존)
    seen = set()
    uniq: List[Path] = []
    for c in cands:
        key = str(c)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def resolve_banner_path() -> Optional[Path]:
    """실제로 존재하는 브랜드 배너 에셋 경로를 반환. 없으면 None."""
    for cand in _candidate_asset_paths():
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                return cand
        except OSError:
            continue
    return None


def has_banner() -> bool:
    """브랜드 배너 에셋이 실제로 존재하는지 여부."""
    return resolve_banner_path() is not None


def attachment_name() -> str:
    """Discord `attachment://` 에 사용할 첨부 파일명."""
    return _ATTACH_FILENAME


def banner_file() -> Optional[Any]:
    """Discord 업로드용 `discord.File` 객체. 에셋이 없으면 None."""
    path = resolve_banner_path()
    if path is None:
        return None
    try:
        import discord
        return discord.File(str(path), filename=_ATTACH_FILENAME)
    except Exception:
        return None


def banner_files() -> List[Any]:
    """`send(files=...)` 에 바로 넘길 수 있는 파일 리스트 (없으면 빈 리스트)."""
    f = banner_file()
    return [f] if f is not None else []


def apply_brand_banner(embed: Any, *, mode: str = "image") -> bool:
    """
    Discord 임베드에 브랜드 배너를 `attachment://` 로 바인딩한다.

    Args:
        embed: discord.Embed 인스턴스
        mode: "image"(하단 대형 배너) | "thumbnail"(우상단 로고)

    Returns:
        바인딩 성공 여부. 에셋이 없으면 False (no-op, 유령 참조 금지).
    """
    if embed is None or not has_banner():
        return False
    url = f"attachment://{_ATTACH_FILENAME}"
    try:
        if mode == "thumbnail":
            embed.set_thumbnail(url=url)
        else:
            embed.set_image(url=url)
        return True
    except Exception:
        return False


def relative_display_path() -> str:
    """리포트/임베드 표기용 상대 경로."""
    path = resolve_banner_path()
    if path is None:
        return f"assets/{_ASSET_BASENAME} (미배치)"
    try:
        return str(path.relative_to(path.parent.parent.parent))
    except Exception:
        return str(path)


def build_brand_embed(*, with_banner: bool = True) -> Any:
    """
    `!brand` 전용 브랜드 배너 쇼케이스 임베드를 생성한다.

    에셋이 실제로 존재하면 임베드에 이미지로 바인딩하고, 출처/경로를 정직하게 표기한다.
    """
    import discord

    path = resolve_banner_path()
    embed = discord.Embed(
        title="🎨 AGY 프로젝트 브랜드 배너",
        description=(
            "Image generator 도구가 생성·배치한 브랜드 배너 에셋을 실제 UI에 연결한 결과입니다.\n"
            f"• 자동 표시 위치: `!help` 종합 홈 상단 배너\n"
            f"• 수동 표시: `!brand` (본 명령)"
        ),
        color=0x2B6CB0,
    )

    if path is not None:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        embed.add_field(name="에셋 파일", value=f"`{_ASSET_BASENAME}`", inline=True)
        embed.add_field(name="배치 경로", value=f"`{relative_display_path()}`", inline=True)
        embed.add_field(name="용량", value=f"`{size:,} bytes`", inline=True)
        embed.add_field(name="출처", value=PROVENANCE_LABEL, inline=False)
        if with_banner:
            apply_brand_banner(embed, mode="image")
    else:
        embed.add_field(
            name="⚠️ 에셋 미배치",
            value=(
                f"`assets/{_ASSET_BASENAME}` 파일을 찾을 수 없습니다.\n"
                "Image generator 백엔드(OPENROUTER_API_KEY / GEMINI_API_KEY) 설정 후 재생성하세요."
            ),
            inline=False,
        )
        embed.color = 0xEF4444

    embed.set_footer(text="AGY Brand UI · brand_ui.py")
    return embed
