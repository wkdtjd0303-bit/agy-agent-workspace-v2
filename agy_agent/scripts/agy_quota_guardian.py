#!/usr/bin/env python3
"""
AGY Quota Guardian — Token Leak Prevention Daemon
==================================================
Automated protection against Antigravity CLI token leak:

1. Brain session cleanup: Limits sessions to MAX_SESSIONS (default 20)
2. Quota monitoring: Logs consumption rate per 5-minute window
3. Leak detection: Alerts when quota drops >2% in 5 min without active agy process
4. Orphan process detection: Finds zombie agy/hermes processes

Usage:
    python3 agy_quota_guardian.py                   # One-shot audit
    python3 agy_quota_guardian.py --daemon           # Continuous monitoring (5m interval)
    python3 agy_quota_guardian.py --cleanup-only      # Only clean old sessions
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("QuotaGuardian")

BRAIN_DIR = Path.home() / ".gemini" / "antigravity-cli" / "brain"
MAX_SESSIONS = 20
LEAK_THRESHOLD_PCT = 2.0  # Alert if >2% drop in single interval
POLL_INTERVAL_SEC = 300   # 5 minutes
LOG_FILE = Path(__file__).parent / "quota_guardian.log"

# Add file handler
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


def fetch_quota() -> Optional[Dict]:
    """Fetch quota via antigravity-usage (token-free OAuth REST call)."""
    try:
        res = subprocess.run(
            ["antigravity-usage", "quota", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=10.0
        )
        if res.returncode == 0 and res.stdout.strip().startswith("{"):
            return json.loads(res.stdout)
    except Exception as e:
        logger.warning(f"Quota fetch failed: {e}")
    return None


def parse_quota_buckets(data: Dict) -> Dict[str, float]:
    """Extract key quota percentages."""
    result = {}
    for m in data.get("models", []):
        mid = m.get("modelId", "")
        pct = m.get("remainingPercentage", 0.0) * 100
        if mid == "gemini-3-flash":
            result["gemini_5h"] = pct
        elif mid == "claude-sonnet-4-6":
            result["claude_5h"] = pct
    return result


def cleanup_brain_sessions(max_keep: int = MAX_SESSIONS, dry_run: bool = False) -> Tuple[int, int]:
    """Delete old brain sessions, keeping only the most recent `max_keep`.
    Returns (deleted_count, freed_bytes).
    """
    if not BRAIN_DIR.exists():
        return 0, 0

    sessions = []
    for d in BRAIN_DIR.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            sessions.append((d.stat().st_mtime, d))

    sessions.sort(key=lambda x: x[0], reverse=True)  # newest first

    to_delete = sessions[max_keep:]
    deleted = 0
    freed = 0

    for _, session_dir in to_delete:
        try:
            size = sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())
            if not dry_run:
                shutil.rmtree(session_dir)
            freed += size
            deleted += 1
        except Exception as e:
            logger.warning(f"Failed to delete {session_dir.name}: {e}")

    if deleted > 0:
        logger.info(f"Cleaned {deleted} old sessions, freed {freed / 1024 / 1024:.1f}MB")
    return deleted, freed


def detect_active_agy_processes() -> List[Dict]:
    """Find running agy/antigravity processes."""
    processes = []
    try:
        res = subprocess.run(
            ["ps", "aux"], stdout=subprocess.PIPE, text=True, timeout=5.0
        )
        for line in res.stdout.splitlines():
            if any(kw in line.lower() for kw in ["agy ", "antigravity"]) and "grep" not in line:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    processes.append({
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "time": parts[9],
                        "cmd": parts[10][:80]
                    })
    except Exception:
        pass
    return processes


def detect_orphan_agy_sessions() -> List[str]:
    """Find brain sessions with transcripts modified in last 30 min but no running agy process."""
    orphans = []
    if not BRAIN_DIR.exists():
        return orphans

    active_pids = detect_active_agy_processes()
    if active_pids:
        return orphans  # If agy is running, sessions aren't orphaned

    cutoff = time.time() - 1800  # 30 min
    for d in BRAIN_DIR.iterdir():
        if not d.is_dir():
            continue
        transcript = d / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript.exists() and transcript.stat().st_mtime > cutoff:
            orphans.append(d.name)

    return orphans


_INTERPRETER_OR_TOOL = {
    "bash", "sh", "dash", "zsh", "fish", "grep", "pgrep", "ps", "head", "tail",
    "timeout", "python", "python3", "node", "npm", "npx", "opencode", "hermes",
    "cmdc", "commandcode",
    "systemctl", "journalctl", "ssh", "vim", "nano", "code", "cat", "sed", "awk",
}


def _is_ls_process(pid: int) -> Tuple[bool, str]:
    """Language Server 여부를 실행 파일(exe)/argv0 기반으로 판별 (오탐 방지).

    셸/그리프/파이썬 등 도구의 명령줄에 우연히 'language_server' 문자열이 있어도
    실제 실행 바이너리가 언어서버가 아니면 False 를 반환한다.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="replace")
    except Exception:
        return False, ""
    argv = [a for a in raw.split("\x00") if a]
    if not argv:
        return False, ""
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except Exception:
        exe = ""
    for token in (exe, argv[0]):
        base = os.path.basename(token or "").lower()
        if base in _INTERPRETER_OR_TOOL:
            return False, ""
    blob = f"{exe} {' '.join(argv)}".lower()
    markers = ("exa.language_server", "language_server", "language-server", "jetski")
    if any(m in blob for m in markers):
        return True, blob
    if "cloudcode" in blob and "server" in blob:
        return True, blob
    if "antigravity" in blob and ("server" in blob or "lsp" in blob):
        return True, blob
    return False, ""


def kill_orphaned_language_servers() -> List[int]:
    """Detect and kill orphaned AGY Language Server processes.

    When AGY CLI runs, it spawns a Language Server process that polls Google
    Cloud APIs (loadCodeAssist, fetchAvailableModels) every 6 minutes. These
    servers survive after the main CLI session exits and silently consume
    Antigravity quota for up to ~2 hours.

    This function identifies such orphaned servers and terminates them.
    Returns list of killed PIDs.
    """
    import signal
    killed = []

    # Strategy 1: /proc scan with executable-based (false-positive-safe) detection
    try:
        for pid_entry in Path("/proc").iterdir():
            if not pid_entry.name.isdigit():
                continue
            pid = int(pid_entry.name)
            try:
                is_ls, cmdline = _is_ls_process(pid)
                if not is_ls:
                    continue
                # Skip active AGY CLI sessions
                if "--print" in cmdline or "--dangerously-skip-permissions" in cmdline:
                    continue
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
                logger.info(f"🔪 Killed orphaned Language Server pid={pid} cmd={cmdline[:120]}")
            except (ProcessLookupError, PermissionError, OSError, ValueError):
                continue
    except Exception:
        pass

    # Strategy 2: Fallback via pgrep (same safe exe-based verification)
    if not killed:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "language_server|cloudcode|jetski"],
                stdout=subprocess.PIPE, text=True, timeout=3.0
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid_str in result.stdout.strip().split():
                    if not pid_str.isdigit():
                        continue
                    pid = int(pid_str)
                    try:
                        is_ls, cmdline = _is_ls_process(pid)
                        if not is_ls:
                            continue
                        if "--print" in cmdline or "--dangerously-skip-permissions" in cmdline:
                            continue
                        os.kill(pid, signal.SIGTERM)
                        killed.append(pid)
                    except (ProcessLookupError, PermissionError, OSError):
                        continue
        except Exception:
            pass

    return killed


def run_audit(prev_quota: Optional[Dict] = None) -> Dict:
    """Run a single audit cycle. Returns current quota data for next comparison."""
    logger.info("=" * 60)
    logger.info("AGY Quota Guardian — Audit Cycle")

    # 1. Quota check
    quota_data = fetch_quota()
    if quota_data:
        buckets = parse_quota_buckets(quota_data)
        gemini = buckets.get("gemini_5h", -1)
        claude = buckets.get("claude_5h", -1)
        logger.info(f"Gemini 5H: {gemini:.2f}% | Claude 5H: {claude:.2f}%")

        # Leak detection
        if prev_quota:
            prev_buckets = parse_quota_buckets(prev_quota)
            prev_gemini = prev_buckets.get("gemini_5h", gemini)
            drop = prev_gemini - gemini
            if drop > LEAK_THRESHOLD_PCT:
                active = detect_active_agy_processes()
                if not active:
                    logger.warning(
                        f"⚠️ LEAK DETECTED: Gemini dropped {drop:.2f}% "
                        f"in {POLL_INTERVAL_SEC}s with NO active agy process!"
                    )
                else:
                    logger.info(f"Gemini consumed {drop:.2f}% (active agy processes: {len(active)})")
    else:
        logger.warning("Could not fetch quota data")

    # 2. Session count check
    if BRAIN_DIR.exists():
        session_count = sum(1 for d in BRAIN_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
        logger.info(f"Brain sessions: {session_count}")
        if session_count > MAX_SESSIONS:
            cleanup_brain_sessions(MAX_SESSIONS)

    # 3. Process check
    active = detect_active_agy_processes()
    if active:
        for p in active:
            logger.info(f"Active: PID={p['pid']} CPU={p['cpu']}% MEM={p['mem']}% CMD={p['cmd']}")
    else:
        logger.info("No active agy processes")

    # 4. Orphan detection
    orphans = detect_orphan_agy_sessions()
    if orphans:
        logger.warning(f"⚠️ {len(orphans)} orphan sessions detected (recent activity, no running process)")

    # 5. [🛡️ Quota Leak Prevention] Kill orphaned AGY Language Servers
    #    These are background processes spawned by AGY CLI that survive after
    #    the main session ends, polling Google APIs (loadCodeAssist) every 6min.
    killed_ls = kill_orphaned_language_servers()
    if killed_ls:
        logger.warning(f"🔪 Killed {len(killed_ls)} orphaned AGY Language Server(s): {killed_ls}")

    return quota_data or {}


def daemon_loop():
    """Continuous monitoring loop with graceful shutdown."""
    import signal
    _running = True

    def _shutdown(signum, frame):
        nonlocal _running
        logger.info(f"AGY Quota Guardian received signal {signum}, shutting down gracefully")
        _running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("AGY Quota Guardian daemon started")
    prev_quota = None
    while _running:
        try:
            prev_quota = run_audit(prev_quota)
        except Exception as e:
            logger.error(f"Audit cycle error: {e}")
        # Use interruptible sleep to honor shutdown signal promptly
        for _ in range(POLL_INTERVAL_SEC):
            if not _running:
                break
            time.sleep(1)
    logger.info("AGY Quota Guardian daemon stopped")


def main():
    parser = argparse.ArgumentParser(description="AGY Quota Guardian")
    parser.add_argument("--daemon", action="store_true", help="Run continuous monitoring")
    parser.add_argument("--cleanup-only", action="store_true", help="Only clean old sessions")
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS, help="Max sessions to keep")
    parser.add_argument("--dry-run", action="store_true", help="Preview cleanup without deleting")
    args = parser.parse_args()

    max_keep = args.max_sessions

    if args.cleanup_only:
        deleted, freed = cleanup_brain_sessions(max_keep, dry_run=args.dry_run)
        action = "Would delete" if args.dry_run else "Deleted"
        print(f"{action} {deleted} sessions, {freed / 1024 / 1024:.1f}MB freed")
        # Always kill orphaned Language Servers during cleanup
        killed_ls = kill_orphaned_language_servers()
        if killed_ls:
            print(f"Killed {len(killed_ls)} orphaned AGY Language Server(s): {killed_ls}")
            logger.info(f"[cleanup-only] Killed {len(killed_ls)} orphaned Language Server(s): {killed_ls}")
        return

    if args.daemon:
        daemon_loop()
    else:
        run_audit()


if __name__ == "__main__":
    main()
