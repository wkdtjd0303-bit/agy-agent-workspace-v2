#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discord_screenshot_gallery.py

!mode 3/3+ 무인 자율 순환에서 생성된 **실제 화면 스크린샷을 디스코드에 이미지로 공유**한다.

■ 배경
------------------------------------------------
Visual Craft Loop(VCL)와 Gemini Vision 검사는 스크린샷을 "분석"해서 QA 프롬프트에
텍스트로만 환류할 뿐, 정작 디스코드에는 **이미지가 올라가지 않아** 사용자는 결과 화면을
직접 볼 수 없었다. 이 모듈은 그 마지막 연결(스크린샷 → Discord 첨부)을 담당한다.

■ 안전 장치
------------------------------------------------
- 빈/검은 화면·저해상도 이미지는 시각 무결성 검증에서 제외 (EvidenceValidator 재사용).
- 사용자 레퍼런스 이미지(sha256 / dHash)는 표절 오인을 막기 위해 절대 전송하지 않는다.
- 라운드 간 중복 전송 방지(이미 보낸 해시는 skip).
- 8MB 초과 파일은 Discord 표준 업로드 한도를 넘으므로 제외(필요 시 상위에서 GDrive 처리).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("ScreenshotGallery")

# 스크린샷이 생성되는 대표 디렉터리 (VCL DEFAULT_SCREENSHOT_DIRS 및 E2E 테스트 경로와 정합)
DEFAULT_SCREENSHOT_DIRS: Tuple[str, ...] = (
    os.path.join("reports", "gate5", "screenshots"),
    os.path.join("reports", "screenshots"),
    os.path.join("reports", "qa_screenshots"),
    os.path.join("reports", "e2e"),
    os.path.join("reports"),
    "screenshots",
    os.path.join("tests", "screenshots"),
    os.path.join("test", "screenshots"),
    os.path.join("tests", "e2e", "screenshots"),
    os.path.join("test-results"),
    os.path.join("playwright-report"),
    os.path.join(".playwright"),
    os.path.join("app", "build", "outputs", "roborazzi"),
    os.path.join("app", "build", "reports", "androidTests", "connected"),
    os.path.join("build", "reports", "tests"),
    "",  # 워크스페이스 루트 자체의 screenshot*.png / *.png
)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_EXCLUDED_DIR_SEGMENTS = {".references", "agy_user_references"}
_EXCLUDED_FILENAME_MARKERS = ("diff",)  # 비교용 diff 이미지는 갤러리에서 제외

DEFAULT_MAX_BYTES = int(8.0 * 1024 * 1024)  # Discord 표준 업로드 한도


def file_sha256(path: str, chunk: int = 65536) -> Optional[str]:
    """파일의 sha256 해시(hex). 실패 시 None."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
        return h.hexdigest()
    except Exception:
        return None


def collect_candidate_screenshots(
    workspace: str,
    extra: Optional[Iterable[str]] = None,
    max_scan: int = 32,
    min_mtime: Optional[float] = None,
) -> List[str]:
    """워크스페이스의 대표 스크린샷 디렉터리 + 명시적 후보(extra)를 최신순으로 모은다.

    ``min_mtime`` 이 주어지면 **그 시각 이후에 생성된 스크린샷만** 모은다. 이는 REJECTED 후
    재작업 라운드에서 "이전 라운드 스크린샷을 이번 라운드 것처럼 재사용"하는 문제를 원천 차단한다.

    무결성/중복 판정은 ``select_gallery_screenshots`` 가 담당하고, 여기서는 경로만 모은다.
    """

    def _fresh(path: str) -> bool:
        if min_mtime is None:
            return True
        try:
            return os.path.getmtime(path) >= (min_mtime - 1.0)
        except Exception:
            return False

    candidates: List[str] = []

    for p in (extra or []):
        try:
            if p and os.path.isfile(p) and _fresh(p):
                candidates.append(os.path.abspath(p))
        except Exception:
            continue

    if workspace and os.path.isdir(workspace):
        for rel_dir in DEFAULT_SCREENSHOT_DIRS:
            cdir = os.path.join(workspace, rel_dir)
            if not os.path.isdir(cdir):
                continue
            try:
                for fname in os.listdir(cdir):
                    lower = fname.lower()
                    if not lower.endswith(_IMAGE_EXTS):
                        continue
                    if lower.startswith("00_") or any(m in lower for m in _EXCLUDED_FILENAME_MARKERS):
                        continue
                    fpath = os.path.abspath(os.path.join(cdir, fname))
                    segs = set(os.path.normpath(fpath).split(os.sep))
                    if segs.intersection(_EXCLUDED_DIR_SEGMENTS):
                        continue
                    if not _fresh(fpath):
                        continue
                    candidates.append(fpath)
            except Exception:
                continue

    # 중복 경로 제거
    seen: Set[str] = set()
    unique: List[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    # 최신순 정렬
    def _mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except Exception:
            return 0.0

    unique.sort(key=_mtime, reverse=True)
    return unique[:max_scan]


def select_gallery_screenshots(
    candidates: Sequence[str],
    excluded_hashes: Optional[Any] = None,
    excluded_dhashes: Optional[Any] = None,
    already_sent: Optional[Set[str]] = None,
    max_files: int = 4,
    min_bytes: int = 500,
    max_bytes: int = DEFAULT_MAX_BYTES,
    validate_image: bool = True,
    min_mtime: Optional[float] = None,
) -> List[str]:
    """디스코드로 전송해도 안전한 스크린샷만 골라 최신순으로 반환한다.

    제외 기준: 미존재 / ``min_mtime`` 이전 생성(이전 라운드 재사용) / 너무 작음 /
    8MB 초과 / 이미 전송됨 / 사용자 레퍼런스 이미지 /
    (validate_image=True 시) 빈·검은 화면·저해상도.
    """
    excluded_hash_set = {h for h in (excluded_hashes or set()) if h}
    excluded_dhash_set = {d for d in (excluded_dhashes or set()) if d}
    sent = already_sent if already_sent is not None else set()

    validator = None
    if validate_image:
        try:
            from evidence_validator import EvidenceValidator
            validator = EvidenceValidator
        except Exception:
            validator = None

    selected: List[str] = []
    seen_hashes: Set[str] = set()

    for path in candidates:
        if len(selected) >= max_files:
            break
        try:
            if not path or not os.path.isfile(path):
                continue
            if min_mtime is not None and os.path.getmtime(path) < (min_mtime - 1.0):
                # 이전 라운드에서 생성된 스크린샷 → 이번 라운드 증거로 재사용 금지
                continue
            size = os.path.getsize(path)
            if size < min_bytes or size > max_bytes:
                continue

            h = file_sha256(path)
            if not h:
                continue
            if h in sent or h in seen_hashes:
                continue
            if h in excluded_hash_set:
                continue

            if excluded_dhash_set:
                try:
                    dh = validator.compute_image_dhash(path) if validator else None
                    if dh and any(ref and validator.hamming_distance(dh, ref) <= 3 for ref in excluded_dhash_set):
                        continue
                except Exception:
                    pass

            if validator is not None:
                ok, _err = validator.validate_image_content(path)
                if not ok:
                    continue

            seen_hashes.add(h)
            selected.append(path)
        except Exception:
            continue

    return selected


def count_stale_screenshots(workspace: str, min_mtime: float, max_scan: int = 64) -> int:
    """워크스페이스에 ``min_mtime`` 이전에 생성된(즉 이번 라운드 것이 아닌) 스크린샷 수를 센다.

    "이번 라운드 신규 캡처 0장 + 이전 스크린샷 N장" 상황을 감지하여, 증거 재사용 경고를
    띄우기 위한 신선도 진단에 사용한다.
    """
    stale = 0
    for p in collect_candidate_screenshots(workspace, max_scan=max_scan):
        try:
            if os.path.getmtime(p) < (min_mtime - 1.0):
                stale += 1
        except Exception:
            continue
    return stale


def _friendly_filename(path: str) -> str:
    return os.path.basename(path) or "screenshot.png"


async def send_screenshot_gallery(
    channel: Any,
    candidates: Sequence[str],
    *,
    excluded_hashes: Optional[Any] = None,
    excluded_dhashes: Optional[Any] = None,
    already_sent: Optional[Set[str]] = None,
    max_files: int = 4,
    title: str = "🎨 시각 증거 스크린샷",
    min_bytes: int = 500,
    min_mtime: Optional[float] = None,
) -> int:
    """검증을 통과한 스크린샷을 디스코드 채널에 이미지로 전송하고 전송 개수를 반환한다.

    ``already_sent`` 를 넘기면 전송한 파일의 해시로 갱신하여 라운드 간 재전송을 막는다.
    ``min_mtime``(라운드 시작 시각)을 넘기면 이전 라운드 스크린샷은 재사용/전송하지 않는다.
    """
    paths = select_gallery_screenshots(
        candidates,
        excluded_hashes=excluded_hashes,
        excluded_dhashes=excluded_dhashes,
        already_sent=already_sent,
        max_files=max_files,
        min_bytes=min_bytes,
        min_mtime=min_mtime,
    )
    if not paths:
        return 0

    try:
        import discord
        from discord_utils import safe_send_discord_message
    except Exception as e:  # pragma: no cover - discord is a hard dependency in prod
        logger.debug(f"Screenshot gallery skipped (discord unavailable): {e}")
        return 0

    files = []
    sent_hashes: List[str] = []
    for p in paths:
        try:
            files.append(
                discord.File(
                    p,
                    filename=_friendly_filename(p),
                    description=f"{title} · {os.path.basename(os.path.dirname(p))}",
                )
            )
            h = file_sha256(p)
            if h:
                sent_hashes.append(h)
        except Exception as f_err:
            logger.debug(f"Screenshot gallery file attach skipped ({p}): {f_err}")

    if not files:
        return 0

    header = (
        f"{title}\n"
        f"🖼️ **실제 렌더링 화면 {len(files)}장** (최신순 · 무결성 검증 통과 · 레퍼런스 이미지 제외)"
    )
    try:
        await safe_send_discord_message(channel, header, files=files)
    except Exception as send_err:
        logger.debug(f"Screenshot gallery send failed: {send_err}")
        return 0

    if already_sent is not None:
        already_sent.update(sent_hashes)
    logger.info(f"Screenshot gallery: sent {len(files)} image(s) to Discord")
    return len(files)
