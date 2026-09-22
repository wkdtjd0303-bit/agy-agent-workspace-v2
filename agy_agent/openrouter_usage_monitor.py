#!/usr/bin/env python3
"""
OpenRouter API Key Usage Monitor & Control Tower Telemetry Integration
----------------------------------------------------------------------
Based on openrouter-usage-monitor (https://github.com/mhd-medfa/openrouter-usage-monitor.git)
Enhanced for AGY 3.0 Control Tower Real-time Status Embed integration.

Features:
- Standalone CLI execution with pretty ANSI output and visual progress bars.
- Modular Python API for telemetry embedding into Discord status messages.
- Automatic caching (45s TTL) to prevent rate limits and ensure fast embed rendering.
- Robust HTTP client with requests and standard urllib.request fallback.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

# Optional requests import with fallback to urllib.request
try:
    import requests
except ImportError:
    requests = None

import urllib.request
import urllib.error

# Cache state
_cached_usage_data: Optional[Dict[str, Any]] = None
_cached_embed_text: Optional[str] = None
_last_usage_fetch_time: float = 0.0
CACHE_TTL_SECONDS = 3600.0  # 1시간: 전체 캐시 TTL 3600s 통일 정책 — 불필요한 OpenRouter API 호출 방지


class Colors:
    """ANSI color codes for pretty CLI terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


def get_api_key() -> str:
    """
    Retrieves and cleans the OpenRouter API key from:
    1. Environment variable OPENROUTER_API_KEY
    2. Local .env file
    3. config.py (if importable)
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        try:
            import config
            api_key = getattr(config, "OPENROUTER_API_KEY", "").strip()
        except Exception:
            pass

    if not api_key:
        env_file = Path(__file__).resolve().parent / ".env"
        if env_file.exists():
            try:
                from dotenv import dotenv_values
                vals = dotenv_values(env_file)
                api_key = (vals.get("OPENROUTER_API_KEY") or "").strip()
            except Exception:
                pass

    # Clean any accidental annotations or wrapper quotes
    for prefix in ("(삭제해야정상동작)", '"(삭제해야정상동작)', "'(삭제해야정상동작)"):
        if api_key.startswith(prefix):
            api_key = api_key[len(prefix):]

    return api_key.strip('"\'; ')


def mask_api_key(key: str) -> str:
    """Masks API key for safe public display (e.g. sk-or-v1-c18...605)."""
    if not key:
        return "N/A"
    if len(key) <= 16:
        return key[:4] + "••••" + key[-4:]
    return f"{key[:14]}...{key[-4:]}"


def get_qa2_model_name() -> str:
    """Retrieves the configured QA2 model dynamically without separate backup clutter."""
    try:
        import config
        return getattr(config, "QA2_MODEL", os.getenv("QA2_MODEL", "upstage/solar-pro4")).strip()
    except Exception:
        return os.getenv("QA2_MODEL", "upstage/solar-pro4").strip()


def fetch_key_info(api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches API key metadata and usage from https://openrouter.ai/api/v1/auth/key."""
    key = api_key or get_api_key()
    if not key:
        return None

    url = "https://openrouter.ai/api/v1/auth/key"
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "AGY-OpenRouter-Monitor/1.0",
        "Accept": "application/json"
    }

    if requests is not None:
        try:
            resp = requests.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass

    # Fallback to standard library urllib
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10.0) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                return json.loads(body)
    except Exception:
        pass

    return None


def fetch_credits(api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches total credits from https://openrouter.ai/api/v1/credits."""
    key = api_key or get_api_key()
    if not key:
        return None

    url = "https://openrouter.ai/api/v1/credits"
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "AGY-OpenRouter-Monitor/1.0",
        "Accept": "application/json"
    }

    if requests is not None:
        try:
            resp = requests.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10.0) as response:
            if response.status == 200:
                body = response.read().decode("utf-8")
                return json.loads(body)
    except Exception:
        pass

    return None


def get_openrouter_usage_data(api_key: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Fetches and aggregates OpenRouter usage metrics with in-memory caching.
    Guarantees clean dictionary return with safe default values.
    """
    global _cached_usage_data, _last_usage_fetch_time
    now = time.time()

    if not force_refresh and _cached_usage_data is not None and (now - _last_usage_fetch_time < CACHE_TTL_SECONDS):
        return _cached_usage_data

    key = get_api_key() if api_key is None else api_key
    if not key:
        return {
            "configured": False,
            "error": "OPENROUTER_API_KEY 미설정",
            "usage": 0.0,
            "limit": None,
            "limit_remaining": None,
            "label": "None",
            "is_free_tier": True,
            "total_credits": 0.0,
            "percent_used": 0.0,
            "percent_remain": 100.0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    key_data = fetch_key_info(key)
    credits_data = fetch_credits(key)

    if not key_data:
        # If fetch failed but we have a stale cache, return stale cache
        if _cached_usage_data is not None:
            return _cached_usage_data
        return {
            "configured": True,
            "error": "API 응답 지연 (재시도 대기)",
            "usage": 0.0,
            "limit": None,
            "limit_remaining": None,
            "label": mask_api_key(key),
            "is_free_tier": True,
            "total_credits": 0.0,
            "percent_used": 0.0,
            "percent_remain": 100.0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    info = key_data.get("data", {})
    usage = float(info.get("usage", 0.0) or 0.0)
    limit = info.get("limit")
    limit_val = float(limit) if limit is not None else None
    limit_remaining = info.get("limit_remaining")
    limit_rem_val = float(limit_remaining) if limit_remaining is not None else None

    # Calculate percentage
    if limit_val and limit_val > 0:
        percent_used = min(100.0, max(0.0, (usage / limit_val) * 100.0))
        percent_remain = max(0.0, 100.0 - percent_used)
    else:
        percent_used = 0.0
        percent_remain = 100.0

    credits_val = 0.0
    if credits_data and "data" in credits_data:
        c_info = credits_data.get("data", {})
        credits_val = float(c_info.get("total_credits", 0.0) or 0.0)

    result = {
        "configured": True,
        "error": None,
        "label": info.get("label") or mask_api_key(key),
        "usage": usage,
        "usage_daily": float(info.get("usage_daily", 0.0) or 0.0),
        "usage_weekly": float(info.get("usage_weekly", 0.0) or 0.0),
        "usage_monthly": float(info.get("usage_monthly", 0.0) or 0.0),
        "limit": limit_val,
        "limit_remaining": limit_rem_val,
        "limit_reset": info.get("limit_reset", "monthly"),
        "is_free_tier": bool(info.get("is_free_tier", False)),
        "rate_limit": info.get("rate_limit", {}),
        "total_credits": credits_val,
        "percent_used": percent_used,
        "percent_remain": percent_remain,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    _cached_usage_data = result
    _last_usage_fetch_time = now
    return result


def _get_display_width(s: str) -> int:
    """Returns visual width in monospace font, treating emoji/wide characters as 2 columns."""
    return sum(2 if ord(c) > 0x2000 else 1 for c in s)


def _pad_col(s: str, width: int) -> str:
    """Pads string to visual monospace width."""
    dw = _get_display_width(s)
    return s + " " * max(0, width - dw)


def make_progress_bar(percent: float, length: int = 12) -> str:
    """Generates visual text progress bar matching Control Tower style."""
    filled = int(round(length * max(0.0, min(100.0, percent)) / 100.0))
    return "█" * filled + "░" * (length - filled)


def format_openrouter_telemetry_field(api_key: Optional[str] = None, force_refresh: bool = False) -> str:
    """
    Formats OpenRouter usage into Discord Status Embed format.
    Matches the exact monospace box-drawing style of Antigravity AI Engine Status.
    Strictly guaranteed to stay well under 1024 characters.
    """
    global _cached_embed_text, _last_usage_fetch_time
    data = get_openrouter_usage_data(api_key=api_key, force_refresh=force_refresh)

    if not data.get("configured"):
        return (
            "```text\n"
            "⚠️ OpenRouter API 키가 .env에 설정되지 않았습니다.\n"
            "OPENROUTER_API_KEY를 등록하면 실시간 쿼터 관제가 활성화됩니다.\n"
            "```"
        )

    if data.get("error"):
        err = data["error"]
        qa2_model = get_qa2_model_name()
        return (
            f"```text\n"
            f"⚠️ OpenRouter 연동 상태: {err}\n"
            f"```\n"
            f"🕵️‍♂️ **연동 모델**: qa2 (`{qa2_model}`)"
        )

    label = data.get("label", "sk-or-v1-...")
    if len(label) > 24:
        label = label[:11] + "..." + label[-8:]
    tier_str = "Free Tier" if data.get("is_free_tier") else "Paid Tier"

    used = data.get("usage", 0.0)
    limit = data.get("limit")
    rem = data.get("limit_remaining")
    pct_used = data.get("percent_used", 0.0)
    pct_rem = data.get("percent_remain", 100.0)

    used_str = f"${used:.4f}"
    if limit is not None:
        limit_str = f"${limit:.4f}"
        rem_str = f"${rem:.4f}" if rem is not None else f"${(limit - used):.4f}"
        pct_status = f"{pct_rem:.1f}% 남음" if pct_rem < 99.9 else "100% 남음"
    else:
        limit_str = "무제한"
        rem_str = "무제한"
        pct_status = "무제한"

    # Status indicator emoji
    if pct_used >= 80.0:
        indicator = "🔴"
    elif pct_used >= 50.0:
        indicator = "🟡"
    else:
        indicator = "🟢"

    status_col = f"{indicator} {pct_used:.1f}%"

    # Monospace table matching Antigravity AI Engine Status table dimensions
    c1_hdr = _pad_col("[OpenRouter 지표]", 21)
    c2_hdr = _pad_col("금액($)", 7)
    c3_hdr = _pad_col("상태/비율", 9)

    r1_col1 = _pad_col("누적 사용 (Used)", 21)
    r1_col2 = _pad_col(used_str, 7)
    r1_col3 = _pad_col(status_col, 9)

    r2_col1 = _pad_col("잔여 한도 (Remain)", 21)
    r2_col2 = _pad_col(rem_str, 7)
    r2_col3 = _pad_col(pct_status, 9)

    r3_col1 = _pad_col("월간 한도 (Limit)", 21)
    r3_col2 = _pad_col(limit_str, 7)
    r3_col3 = _pad_col("월간리셋", 9)

    bar = make_progress_bar(pct_used, length=12)

    lines = [
        "```text",
        f"🔑 {label} ({tier_str})",
        "┌───────────────────────┬─────────┬───────────┐",
        f"│ {c1_hdr} │ {c2_hdr} │ {c3_hdr} │",
        "├───────────────────────┼─────────┼───────────┤",
        f"│ {r1_col1} │ {r1_col2} │ {r1_col3} │",
        f"│ {r2_col1} │ {r2_col2} │ {r2_col3} │",
        f"│ {r3_col1} │ {r3_col2} │ {r3_col3} │",
        "└───────────────────────┴─────────┴───────────┘",
        "```",
        f"**소진율**  {bar} **{pct_used:.1f}%** ({used_str} / {limit_str})",
        f"🕵️‍♂️ **연동 모델**: qa2 (`{get_qa2_model_name()}`)"
    ]

    return "\n".join(lines)


# ==============================================================================
# CLI Pretty Printer (Matching openrouter-usage-monitor)
# ==============================================================================

def print_header():
    """Prints the openrouter-usage-monitor signature banner."""
    print()
    print(f"{Colors.BOLD}{Colors.CYAN}╔══════════════════════════════════════════════════════╗{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}║         🔑  OpenRouter API Key Monitor  🔑           ║{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}╚══════════════════════════════════════════════════════╝{Colors.RESET}")
    print()


def print_section(title: str):
    print(f"{Colors.BOLD}{Colors.BLUE}┌─ {title} {'─' * max(2, 50 - len(title))}┐{Colors.RESET}")


def print_row(label: str, value: str, indent: int = 2):
    padding = ' ' * indent
    print(f"{padding}{Colors.DIM}│{Colors.RESET} {label:<22} {Colors.BOLD}{value}{Colors.RESET}")


def print_section_end():
    print(f"{Colors.BLUE}└{'─' * 53}┘{Colors.RESET}")
    print()


def run_cli():
    """Runs interactive or terminal CLI monitor."""
    print_header()
    key = get_api_key()
    if not key:
        print(f"{Colors.RED}✗ No OPENROUTER_API_KEY found in environment or .env.{Colors.RESET}")
        try:
            key = input(f"{Colors.CYAN}Enter your OpenRouter API key: {Colors.RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)
        if not key:
            print(f"{Colors.RED}✗ No key provided. Exiting.{Colors.RESET}")
            sys.exit(1)

    print(f"{Colors.DIM}  Using key: {mask_api_key(key)}{Colors.RESET}")
    print(f"{Colors.CYAN}  Fetching usage data from OpenRouter API...{Colors.RESET}\n")

    data = get_openrouter_usage_data(api_key=key, force_refresh=True)

    if not data.get("configured") or data.get("error"):
        print(f"{Colors.RED}✗ Failed to retrieve key information: {data.get('error')}{Colors.RESET}")
        sys.exit(1)

    # Key Information
    print_section("Key Information")
    print_row("Label:", data.get("label", "N/A"))
    print_row("Free Tier:", "Yes ✓" if data.get("is_free_tier") else "No (Paid)")
    print_section_end()

    # Usage & Credits
    print_section("Usage & Credits")
    used = data.get("usage", 0.0)
    limit = data.get("limit")
    rem = data.get("limit_remaining")
    pct_used = data.get("percent_used", 0.0)

    print_row("Used:", f"${used:.4f}")
    if limit is not None:
        print_row("Limit:", f"${limit:.4f}")
        rem_val = rem if rem is not None else (limit - used)
        rem_color = Colors.GREEN if pct_used < 50.0 else Colors.YELLOW if pct_used < 80.0 else Colors.RED
        print_row("Remaining:", f"{rem_color}${rem_val:.4f}{Colors.RESET}")
        bar = make_progress_bar(pct_used, length=24)
        print()
        print(f"  {Colors.DIM}│{Colors.RESET} Progress:  [{bar}] {pct_used:.1f}%")
    else:
        print_row("Limit:", "Unlimited (No limit set)")

    print_section_end()

    # Rate limits & Active Role
    print_section("AGY Integration & Active Engine")
    print_row("QA2 Model:", get_qa2_model_name())
    print_row("Telemetry Target:", "Channel 1550294208588419123")
    print_section_end()

    print(f"{Colors.DIM}  Last checked: {data.get('timestamp')}{Colors.RESET}")
    print(f"{Colors.GREEN}✓ OpenRouter Monitor Done!{Colors.RESET}\n")


if __name__ == "__main__":
    run_cli()
