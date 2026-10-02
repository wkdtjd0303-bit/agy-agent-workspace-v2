#!/usr/bin/env python3
"""
🛡️ safe_trash.py — Agent Trash-Only Safe Deletion & Device Path Management
Provides:
1. Universal device path resolution across the entire system.
2. Safe trash movement using send2trash / gio trash (zero permanent rm -rf).
3. Critical system directory protection guardrails.
4. Auto-healing workspace redirection if active workspace is trashed.
"""

import os
import sys
import shutil
import logging
import subprocess
from typing import Tuple, Dict, Any, Optional, List

logger = logging.getLogger("SafeTrash")

# System & high-level protected paths that must NEVER be deleted
PROTECTED_SYSTEM_DIRS = {
    "/",
    "/root",
    "/bin",
    "/sbin",
    "/usr",
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/local",
    "/etc",
    "/var",
    "/var/log",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/run",
    "/home",
}

# Standard base directory for autocoding projects
# (사용자명 하드코딩 대신 홈 디렉토리 기준으로 유도 — 다른 계정/머신에서도 동일 동작)
DEFAULT_AUTOCODING_BASE = os.path.join(os.path.expanduser("~"), "Desktop", "autocoding")


def resolve_device_path(raw_path: str, base_dir: Optional[str] = None) -> str:
    """
    Resolves any path across the device filesystem:
    - Absolute paths (/foo/bar) are preserved and normalized.
    - User tilde (~/...) paths are expanded.
    - Relative paths are resolved against base_dir (or DEFAULT_AUTOCODING_BASE).
    """
    if not raw_path or not raw_path.strip():
        return os.path.abspath(base_dir or DEFAULT_AUTOCODING_BASE)

    cleaned = raw_path.strip().strip("'\"")
    expanded = os.path.expanduser(cleaned)

    if os.path.isabs(expanded):
        return os.path.abspath(expanded)

    # Relative path resolution
    ref_base = base_dir
    if not ref_base or not os.path.isdir(ref_base):
        ref_base = DEFAULT_AUTOCODING_BASE
        if not os.path.isdir(ref_base):
            ref_base = os.path.expanduser("~/Desktop")

    return os.path.abspath(os.path.join(ref_base, expanded))


def is_protected_path(path: str) -> Tuple[bool, str]:
    """
    Validates whether a path is a critical system or parent directory that cannot be deleted.
    """
    abs_path = os.path.abspath(os.path.expanduser(path))

    # 1. Root & basic system directories
    if abs_path in PROTECTED_SYSTEM_DIRS:
        return True, f"시스템 최상위 보호 디렉토리입니다: `{abs_path}`"

    # 2. Prevent deleting root of user home directly
    user_home = os.path.abspath(os.path.expanduser("~"))
    if abs_path == user_home:
        return True, f"사용자 홈 루트(`~`) 디렉토리는 삭제할 수 없습니다: `{abs_path}`"

    # 3. Prevent deleting user's Desktop directory root itself
    desktop_dir = os.path.abspath(os.path.join(user_home, "Desktop"))
    if abs_path == desktop_dir:
        return True, f"바탕화면(`Desktop`) 루트는 삭제할 수 없습니다: `{abs_path}`"

    # 4. Prevent deleting autocoding root directory itself
    if abs_path == os.path.abspath(DEFAULT_AUTOCODING_BASE):
        return True, f"프로젝트 최상위 허브 디렉토리(`{DEFAULT_AUTOCODING_BASE}`) 자체는 삭제할 수 없습니다."

    # 5. Prevent deleting this agent repository itself
    this_repo = os.path.abspath(os.path.join(DEFAULT_AUTOCODING_BASE, "!05_agy_agent"))
    if abs_path == this_repo:
        return True, f"현재 구동 중인 에이전트 본체 디렉토리(`!05_agy_agent`)는 안전을 위해 삭제할 수 없습니다."

    # 6. Depth safety check (cannot delete /foo or /home/jangsung)
    parts = [p for p in abs_path.split(os.sep) if p]
    if len(parts) < 3 and not abs_path.startswith("/tmp"):
        return True, f"시스템 핵심 상위 경로(깊이 {len(parts)})는 삭제할 수 없습니다: `{abs_path}`"

    return False, ""


def calculate_path_size(target_path: str) -> Tuple[int, int]:
    """Returns (total_bytes, file_count) for path."""
    if not os.path.exists(target_path):
        return 0, 0
    if os.path.isfile(target_path) or os.path.islink(target_path):
        return os.path.getsize(target_path), 1

    total_bytes = 0
    file_count = 0
    try:
        for root, dirs, files in os.walk(target_path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if not os.path.islink(fp):
                        total_bytes += os.path.getsize(fp)
                    file_count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return total_bytes, file_count


def format_bytes(bytes_count: int) -> str:
    """Formats bytes to human readable size."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def send_to_trash(target_path: str, channel_id: Optional[int] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Safely moves a folder or file to OS Trash (FreeDesktop Trash / Recycle Bin).
    NEVER performs permanent deletion (rm -rf / rmdir / unlink).
    Returns (success: bool, message: str, metadata: dict).
    """
    abs_path = os.path.abspath(os.path.expanduser(target_path))

    if not os.path.exists(abs_path):
        return False, f"⚠️ 대상 경로가 존재하지 않습니다:\n`{abs_path}`", {}

    is_prot, prot_msg = is_protected_path(abs_path)
    if is_prot:
        return False, f"⛔ **[삭제 불가 - 보안 가드레일 작동]**\n{prot_msg}", {}

    total_bytes, file_count = calculate_path_size(abs_path)
    is_dir = os.path.isdir(abs_path)
    base_name = os.path.basename(abs_path)

    metadata = {
        "path": abs_path,
        "name": base_name,
        "is_dir": is_dir,
        "size_bytes": total_bytes,
        "size_display": format_bytes(total_bytes),
        "file_count": file_count,
        "method": "send2trash"
    }

    # Step 1: Attempt send2trash
    trashed = False
    last_err = ""
    try:
        import send2trash
        send2trash.send2trash(abs_path)
        trashed = not os.path.exists(abs_path)
    except Exception as e:
        last_err = str(e)
        logger.warning(f"send2trash direct call failed on '{abs_path}': {e}, falling back to gio trash...")

    # Step 2: Fallback to gio trash
    if not trashed and os.path.exists(abs_path):
        try:
            res = subprocess.run(["gio", "trash", abs_path], capture_output=True, text=True, timeout=10.0)
            if res.returncode == 0 and not os.path.exists(abs_path):
                trashed = True
                metadata["method"] = "gio_trash"
            else:
                last_err = res.stderr.strip() or last_err
        except Exception as ge:
            last_err = f"{last_err} | {ge}"

    if not trashed and os.path.exists(abs_path):
        import shutil, time
        try:
            trash_dir = os.path.join(os.path.dirname(abs_path), '.agy_trash')
            os.makedirs(trash_dir, exist_ok=True)
            trash_dest = os.path.join(trash_dir, os.path.basename(abs_path) + f'_{int(time.time())}')
            shutil.move(abs_path, trash_dest)
            metadata["method"] = "manual_trash"
            return True, f'✅ 수동 휴지통 이동: {trash_dest}', metadata
        except Exception as e3:
            last_err = f"{last_err} | {e3}"

    # Step 3: Verify results
    if trashed:
        # Auto-heal workspace if the deleted folder was current channel's workspace
        workspace_switched = False
        new_ws = ""
        if channel_id is not None:
            try:
                from session_manager import SessionManager
                from config import DEFAULT_WORKSPACE
                sm = SessionManager()
                session = sm.get_session(channel_id)
                if os.path.abspath(session.workspace) == abs_path:
                    # Switch to parent dir or DEFAULT_WORKSPACE
                    parent_dir = os.path.dirname(abs_path)
                    if os.path.isdir(parent_dir) and parent_dir != "/":
                        fallback_ws = parent_dir
                    else:
                        fallback_ws = DEFAULT_WORKSPACE
                    session.set_workspace(fallback_ws)
                    workspace_switched = True
                    new_ws = session.workspace
            except Exception as se:
                logger.debug(f"Workspace auto-healing notice: {se}")

        metadata["workspace_switched"] = workspace_switched
        metadata["new_workspace"] = new_ws

        msg = (
            f"🗑️ **`{base_name}`** 이(가) **OS 휴지통**으로 안전하게 이동되었습니다.\n"
            f"• 📦 크기: `{metadata['size_display']}` ({file_count}개 항목)\n"
            f"• 📍 원본 경로: `{abs_path}`\n"
            f"• 🛡️ **안전 보장**: 완전 영구 삭제(`rm -rf`)가 아니므로 OS 휴지통(`~/.local/share/Trash`)에서 언제든 복원할 수 있습니다."
        )
        if workspace_switched:
            msg += f"\n• 🔄 **작업 경로 자동 복원**: 삭제된 폴더 대신 상위 디렉토리(`{new_ws}`)로 세션 CWD가 안전하게 재배정되었습니다."

        return True, msg, metadata
    else:
        return False, f"⚠️ 휴지통 이동 실패: {last_err or '파일 시스템 접근 권한 오류'}", metadata


def create_device_folder(
    path_str: str,
    base_dir: Optional[str] = None,
    initial_files: Optional[List[str]] = None
) -> Tuple[bool, str, str]:
    """
    Creates a folder anywhere on the device.
    Supports:
    - Absolute paths (/opt/..., /home/...)
    - Relative names (auto placed in base_dir or DEFAULT_AUTOCODING_BASE)
    - Tilde expansion (~/...)
    - Optional template/initial file generation (e.g. README.md)
    """
    abs_path = resolve_device_path(path_str, base_dir)

    try:
        os.makedirs(abs_path, exist_ok=True)
        if not os.path.isdir(abs_path):
            return False, abs_path, f"디렉토리를 생성할 수 없습니다: `{abs_path}`"

        created_files = []
        if initial_files:
            for fname in initial_files:
                fn = fname.strip()
                if not fn:
                    continue
                fp = os.path.join(abs_path, fn)
                if not os.path.exists(fp):
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "w", encoding="utf-8") as f:
                        if fn.lower().endswith(".md"):
                            f.write(f"# {os.path.basename(abs_path)}\n\nProject initialized.\n")
                        else:
                            f.write("")
                    created_files.append(fn)

        msg = f"📁 **디렉토리가 성공적으로 준비되었습니다:**\n`{abs_path}`"
        if created_files:
            msg += f"\n• 초기 생성 파일: `{', '.join(created_files)}`"

        return True, abs_path, msg

    except Exception as e:
        return False, abs_path, f"⚠️ 디렉토리 생성 실패: {str(e)}"
