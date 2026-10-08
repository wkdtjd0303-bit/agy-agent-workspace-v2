#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prd_archive.py

완료된 PRD 아카이브.

[배경/니즈]
    PRD는 프로젝트 착수 시 기획 헌법으로 생성되지만, 제품이 완성된 뒤에도
    워크스페이스 루트의 `PRD.md` 로 남아 다음 작업의 기획 신호와 뒤섞인다.
    완료된 PRD는 별도 보관소로 이동시키고, 루트에는 "아카이브됨" 스텁만 남긴다.

[원칙]
    - 원본은 삭제하지 않고 `docs/completed_prds/` 로 보존한다(감사 추적).
    - 루트 `PRD.md` 는 다음 작업에 방해되지 않도록 최소 스텁으로 대체한다.
    - 실패해도 예외를 전파하지 않는다(무중단 폴백).
"""

from __future__ import annotations

import os
import re
import time
import shutil
import logging
from typing import Optional

logger = logging.getLogger("PrdArchive")

ARCHIVE_DIRNAME = os.path.join("docs", "completed_prds")


def _slug(text: str) -> str:
    s = re.sub(r"[^\w가-힣]+", "_", str(text or "").strip())
    return s.strip("_")[:40] or "prd"


def _detect_app_name(prd_path: str) -> str:
    """PRD.md 첫 제목에서 제품명을 추출한다."""
    try:
        with open(prd_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r"^#\s+(.+)$", line.strip())
                if m:
                    title = re.sub(r"^[^A-Za-z가-힣]*", "", m.group(1)).strip()
                    return _slug(title)
    except Exception:
        pass
    return "prd"


def archive_prd(workspace: str, reason: str = "completed", app_name: str = "") -> Optional[str]:
    """워크스페이스의 `PRD.md` 를 `docs/completed_prds/` 로 이동하고 스텁을 남긴다.

    Returns:
        아카이브된 파일의 절대경로 (PRD.md 가 없으면 None).
    """
    if not workspace or not os.path.isdir(workspace):
        return None
    prd_path = os.path.join(workspace, "PRD.md")
    if not os.path.isfile(prd_path):
        return None

    try:
        archive_dir = os.path.join(workspace, ARCHIVE_DIRNAME)
        os.makedirs(archive_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        slug = _slug(app_name) if app_name else _detect_app_name(prd_path)
        dest = os.path.join(archive_dir, f"PRD_{slug}_{stamp}.md")

        # 동일 타임스탬프 충돌 방지
        suffix = 0
        while os.path.exists(dest):
            suffix += 1
            dest = os.path.join(archive_dir, f"PRD_{slug}_{stamp}_{suffix}.md")

        shutil.move(prd_path, dest)

        stub = (
            "# 📋 PRD (아카이브됨 · Archived)\n\n"
            f"> 이 프로젝트의 PRD는 완료(`{reason}`)되어 보관소로 이동되었습니다.\n"
            f"> 다음 작업의 기획 신호와 뒤섞이지 않도록 본 파일은 기록 스텁만 유지합니다.\n"
            f"* **원본**: `{os.path.relpath(dest, workspace)}`\n"
            f"* **보관 시각**: {stamp}\n"
            f"* **재기획 필요 시**: 보관소의 원본 PRD 또는 새 기획을 참조하십시오.\n"
        )
        with open(prd_path, "w", encoding="utf-8") as f:
            f.write(stub)
        logger.info(f"[PrdArchive] PRD 아카이브 완료: {os.path.relpath(dest, workspace)}")
        return dest
    except Exception as e:
        logger.warning(f"[PrdArchive] PRD 아카이브 실패(무중단): {e}")
        return None


def list_archived_prds(workspace: str) -> list:
    """보관소에 모인 완료 PRD 목록을 반환한다."""
    archive_dir = os.path.join(workspace or "", ARCHIVE_DIRNAME)
    if not os.path.isdir(archive_dir):
        return []
    try:
        return sorted(
            os.path.join(archive_dir, f)
            for f in os.listdir(archive_dir)
            if f.lower().endswith(".md")
        )
    except Exception:
        return []


__all__ = ["ARCHIVE_DIRNAME", "archive_prd", "list_archived_prds"]
