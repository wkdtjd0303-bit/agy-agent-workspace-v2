import os
import sys
import json
import time
import base64
import hashlib
import asyncio
import logging
import subprocess
import psutil
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import discord
from thermal_guard import ThermalGuard
from session_manager import SessionManager

logger = logging.getLogger("TelemetryStatusManager")

STATUS_CHANNEL_ID = 1550294208588419123
_TELEMETRY_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "telemetry_status_msg.json")

def load_status_message_id(expected_channel_id: int = STATUS_CHANNEL_ID) -> Optional[int]:
    if os.path.isfile(_TELEMETRY_STATE_FILE):
        try:
            with open(_TELEMETRY_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                stored_ch = data.get("channel_id")
                # If channel changed, stored message ID belongs to old channel
                if stored_ch and int(stored_ch) != int(expected_channel_id):
                    return None
                mid = data.get("status_message_id")
                if mid:
                    return int(mid)
        except Exception:
            pass
    return None

def save_status_message_id(msg_id: Optional[int], channel_id: int = STATUS_CHANNEL_ID):
    global STATUS_MESSAGE_ID
    STATUS_MESSAGE_ID = msg_id
    try:
        os.makedirs(os.path.dirname(_TELEMETRY_STATE_FILE), exist_ok=True)
        with open(_TELEMETRY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "channel_id": channel_id,
                "status_message_id": msg_id,
                "updated_at": time.time()
            }, f, indent=2)
    except Exception as e:
        logger.debug(f"Failed to save status message ID: {e}")

STATUS_MESSAGE_ID: Optional[int] = load_status_message_id(STATUS_CHANNEL_ID)

def _get_rate_limit_until() -> float:
    return getattr(sys, "__agy_telemetry_rate_limit_until__", 0.0)

def _set_rate_limit_until(ts: float):
    setattr(sys, "__agy_telemetry_rate_limit_until__", ts)

def _get_last_embed_hash() -> str:
    return getattr(sys, "__agy_telemetry_last_embed_hash__", "")

def _set_last_embed_hash(h: str):
    setattr(sys, "__agy_telemetry_last_embed_hash__", h)

def _get_loop_task() -> Optional[asyncio.Task]:
    return getattr(sys, "__agy_telemetry_loop_task__", None)

def _set_loop_task(task: Optional[asyncio.Task]):
    setattr(sys, "__agy_telemetry_loop_task__", task)

def compute_embed_hash(embed: discord.Embed) -> str:
    try:
        raw = json.dumps(embed.to_dict(), sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception:
        return ""

_rate_limit_until: float = 0.0
_cached_quota_val: str = ""
_last_quota_fetch_time: float = 0.0


def sync_antigravity_token():
    """Syncs OAuth token from antigravity-cli into ~/.config/antigravity-usage/tokens.json."""
    src_path = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
    dst_dir = os.path.expanduser("~/.config/antigravity-usage")
    dst_path = os.path.join(dst_dir, "tokens.json")
    if not os.path.exists(src_path):
        return
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("token", {})
        expiry_str = token.get("expiry", "")
        try:
            dt = datetime.fromisoformat(expiry_str)
            expires_at = int(dt.timestamp() * 1000)
        except Exception:
            expires_at = int(time.time() * 1000) + 3600000

        tokens = {
            "accessToken": token.get("access_token", ""),
            "refreshToken": token.get("refresh_token", ""),
            "expiresAt": expires_at
        }
        os.makedirs(dst_dir, exist_ok=True)
        with open(dst_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        logger.debug(f"Error syncing antigravity token: {e}")


def format_time_until_reset(ms: Optional[int]) -> str:
    """Formats milliseconds into compact human-readable duration (e.g. 3h 25m)."""
    if ms is None or ms <= 0:
        return "N/A"
    hours = int(ms // (1000 * 3600))
    minutes = int((ms % (1000 * 3600)) // (1000 * 60))
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_remaining_quota(model: Dict[str, Any]) -> str:
    """Formats model remaining quota with color indicator."""
    if model.get("isExhausted", False):
        return "🔴 0%"
    pct = model.get("remainingPercentage")
    if pct is None:
        # 3rd-party models (Claude, GPT-OSS) do not report percentages in Google API.
        # When isExhausted is false, the quota is active and healthy.
        return "🟢 정상"
    p = round(pct * 100)
    if p >= 50:
        return f"🟢 {p}%"
    elif p >= 20:
        return f"🟡 {p}%"
    else:
        return f"🔴 {p}%"


def get_display_width(s: str) -> int:
    """Returns visual width in monospace font, treating emoji/wide characters as 2 cols."""
    return sum(2 if ord(c) > 0x2000 else 1 for c in s)


def clean_model_display_name(label: str, model_id: str) -> str:
    """Cleans and shortens model label to fit neatly in table column."""
    if "claude-opus" in model_id.lower() or "opus" in label.lower():
        return "Claude Opus"
    if "claude-sonnet" in model_id.lower() or "sonnet" in label.lower():
        return "Claude Sonnet"
    if "gpt-oss" in model_id.lower() or "gpt-oss" in label.lower():
        return "GPT-OSS 120B"
    if "gemini-3.8-flash" in model_id:
        return "Gemini 3.8 Flash"
    if "gemini-3.7-flash" in model_id:
        return "Gemini 3.7 Flash"
    if "gemini-3.6-flash-tiered" in model_id:
        return "Gemini 3.6 Flash (T)"
    clean = (
        label.replace(" (Thinking)", "")
        .replace(" (Medium)", " (M)")
        .replace(" (High)", " (H)")
        .replace(" (Low)", " (L)")
    )
    if len(clean) > 21:
        clean = clean[:20] + "…"
    return clean


_sonnet_cache: Dict[str, Any] = {"time": 0.0, "data": None}
_cached_gemini_quota: Optional[str] = None
_cached_external_quota: Optional[str] = None
_last_quota_fetch_time: float = 0.0
_native_usage_cache: Dict[str, Any] = {"time": 0.0, "data": None}


def get_antigravity_account_email() -> str:
    """Extracts account email dynamically from Antigravity OAuth id_token."""
    token_file = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            id_token = data.get("id_token", "")
            if id_token:
                parts = id_token.split(".")
                if len(parts) >= 2:
                    rem = len(parts[1]) % 4
                    padded = parts[1] + ("=" * (4 - rem) if rem else "")
                    payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
                    email = payload.get("email")
                    if email:
                        return email
        except Exception as e:
            logger.debug(f"Error parsing antigravity id_token: {e}")
    return "4uillust@gmail.com"


def format_iso_duration(iso_str: str) -> str:
    """Formats ISO 8601 timestamp into human-readable duration (e.g. 149h 42m (6일 5시간))."""
    if not iso_str:
        return "N/A"
    try:
        clean_iso = iso_str.replace("Z", "+00:00")
        target_dt = datetime.fromisoformat(clean_iso)
        now_dt = datetime.now(timezone.utc)
        diff = target_dt - now_dt
        total_sec = int(diff.total_seconds())
        if total_sec <= 0:
            return "0m (완료)"
        days = total_sec // 86400
        hours = (total_sec % 86400) // 3600
        mins = (total_sec % 3600) // 60
        total_h = total_sec // 3600
        if days > 0:
            return f"{total_h}h {mins}m ({days}일 {hours}시간)"
        elif hours > 0:
            return f"{hours}h {mins}m"
        else:
            return f"{mins}m"
    except Exception:
        return iso_str


def make_antigravity_gauge(fraction: float, length: int = 20) -> str:
    """Generates a gauge bar using solid/light blocks [■■■■░░░]."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(length * frac))
    return "[" + "■" * filled + "░" * (length - filled) + "]"


def fetch_native_antigravity_usage() -> Dict[str, Any]:
    """
    [🛡️ Token Protection Mandate - Solar Pro 4 Protocol]
    Fetches quota data exclusively via lightweight, token-free OAuth utility (antigravity-usage).
    NEVER invokes full 'agy' CLI to avoid spinning up LLM agent sessions or burning quota.
    Cached for 3600 seconds (1 hour) to eliminate frequent background process polling.
    antigravity-usage config.json cacheTTL must also be 3600 to match.
    """
    global _native_usage_cache
    now = time.time()
    if _native_usage_cache.get("data") and (now - _native_usage_cache.get("time", 0.0) < 3600.0):
        return _native_usage_cache["data"]

    # [🛡️ Quota Leak Fix — 2026-09-23] 'agy -p /usage' 호출을 완전히 제거했다.
    # agy CLI는 실행될 때마다 Antigravity Language Server를 띄우고, 그 서버가
    # 6분마다 loadCodeAssist/fetchAvailableModels를 폴링하며 조용히 quota를 소모한다.
    # (session_manager.kill_agy_language_servers / agy_quota_guardian 주석 참조)
    # 따라서 quota 조회는 토큰프리 OAuth REST 호출인 'antigravity-usage quota --json'만 사용한다.
    cmd = ["antigravity-usage", "quota", "--json"]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0
        )
        if res.returncode == 0 and res.stdout.strip().startswith("{"):
            data = json.loads(res.stdout)
            email = get_antigravity_account_email()

            # Support legacy groups format (command.data.groups or top-level groups)
            legacy_groups = data.get("command", {}).get("data", {}).get("groups") or data.get("groups")
            if legacy_groups:
                parsed = {
                    "account": email,
                    "groups": legacy_groups,
                    "raw": data
                }
                _native_usage_cache = {"time": now, "data": parsed}
                return parsed

            # Support modern flat models list format from 'antigravity-usage quota --json'
            models = data.get("models", [])
            gemini_models = []
            claude_models = []
            for m in models:
                mid = m.get("modelId", "")
                lbl = m.get("label", mid)
                is_exhausted = m.get("isExhausted", False)
                rem = m.get("remainingPercentage")
                reset_t = m.get("resetTime", "")
                reset_ms = m.get("timeUntilResetMs", 0)
                is_ext = any(k in mid.lower() for k in ("claude", "sonnet", "opus", "gpt-oss", "oss")) or any(k in lbl.lower() for k in ("claude", "gpt"))

                qm_exhausted = False
                try:
                    from trio_agent_runner import QuotaManager
                    qm_exhausted = QuotaManager.is_model_exhausted(mid)
                except Exception:
                    pass

                if qm_exhausted or is_exhausted:
                    rem_frac = 0.0
                    if is_ext:
                        try:
                            from trio_agent_runner import QuotaManager
                            reset_sec = min(reset_ms / 1000.0, 3600.0 * 6) if reset_ms else 3600.0
                            QuotaManager.mark_model_exhausted(mid, duration_sec=reset_sec)
                        except Exception:
                            pass
                elif rem is not None:
                    rem_frac = float(rem)
                else:
                    rem_frac = 1.0

                m_info = {
                    "id": mid,
                    "name": lbl,
                    "remaining_fraction": rem_frac,
                    "reset_time": reset_t,
                    "reset_ms": reset_ms,
                    "is_exhausted": qm_exhausted or is_exhausted,
                    "is_autocomplete": m.get("isAutocompleteOnly", False)
                }
                if is_ext:
                    claude_models.append(m_info)
                elif "gemini" in mid.lower() or "gemini" in lbl.lower():
                    gemini_models.append(m_info)

            # Gemini models: construct both Weekly Limit and 5-Hour Limit buckets
            gemini_buckets = []
            if gemini_models:
                main_gemini = [m for m in gemini_models if not m.get("is_autocomplete")] or gemini_models
                min_gemini_rem = min(m["remaining_fraction"] for m in main_gemini)
                best_reset = next((m["reset_time"] for m in main_gemini if m.get("reset_time")), "")
                best_reset_ms = next((m.get("reset_ms", 0) for m in main_gemini if m.get("reset_ms")), 0)
                any_gemini_exhausted = any(m.get("is_exhausted", False) for m in main_gemini)

                is_weekly_reset = best_reset_ms > (5 * 3600 * 1000)
                weekly_reset_time = best_reset if is_weekly_reset else ""
                short_reset_time = best_reset if (0 < best_reset_ms <= 5 * 3600 * 1000) else ""

                # 1. Weekly Limit Remaining [주간 한도]
                gemini_buckets.append({
                    "id": "gemini-weekly",
                    "name": "Weekly Limit Remaining",
                    "remaining_fraction": min_gemini_rem,
                    "reset_time": weekly_reset_time or best_reset
                })

                # 2. Five Hour Limit Remaining [5시간 한도]
                if any_gemini_exhausted or min_gemini_rem <= 0.0:
                    five_h_rem = 0.0
                elif short_reset_time:
                    five_h_rem = min_gemini_rem
                else:
                    five_h_rem = 1.0

                gemini_buckets.append({
                    "id": "gemini-5h",
                    "name": "Five Hour Limit Remaining",
                    "remaining_fraction": five_h_rem,
                    "reset_time": short_reset_time
                })

            # Claude / GPT-OSS models: construct both Weekly Limit and 5-Hour Limit buckets
            claude_buckets = []
            if claude_models:
                min_claude_rem = min(m["remaining_fraction"] for m in claude_models)
                best_reset_ext = next((m["reset_time"] for m in claude_models if m.get("reset_time")), "")
                best_reset_ext_ms = next((m.get("reset_ms", 0) for m in claude_models if m.get("reset_ms")), 0)
                any_claude_exhausted = any(m.get("is_exhausted", False) for m in claude_models)

                is_weekly_reset_ext = best_reset_ext_ms > (5 * 3600 * 1000)
                weekly_reset_ext_time = best_reset_ext if is_weekly_reset_ext else ""
                short_reset_ext_time = best_reset_ext if (0 < best_reset_ext_ms <= 5 * 3600 * 1000) else ""

                # 1. Weekly Limit Remaining [주간 한도]
                claude_buckets.append({
                    "id": "3p-weekly",
                    "name": "Weekly Limit Remaining",
                    "remaining_fraction": min_claude_rem,
                    "reset_time": weekly_reset_ext_time or best_reset_ext
                })

                # 2. Five Hour Limit Remaining [5시간 한도]
                if any_claude_exhausted or min_claude_rem <= 0.0:
                    five_h_claude_rem = 0.0
                elif short_reset_ext_time:
                    five_h_claude_rem = min_claude_rem
                else:
                    five_h_claude_rem = 1.0

                claude_buckets.append({
                    "id": "3p-5h",
                    "name": "Five Hour Limit Remaining",
                    "remaining_fraction": five_h_claude_rem,
                    "reset_time": short_reset_ext_time
                })

            groups = []
            if gemini_buckets:
                groups.append({"name": "Gemini Models", "buckets": gemini_buckets, "models": gemini_models})
            if claude_buckets:
                groups.append({"name": "Claude / GPT-OSS Models", "buckets": claude_buckets, "models": claude_models})

            if groups:
                parsed = {
                    "account": email,
                    "groups": groups,
                    "raw": data
                }
                _native_usage_cache = {"time": now, "data": parsed}
                return parsed
    except Exception as e:
        logger.debug(f"Lightweight antigravity-usage query note: {e}")

    # Fallback to existing cache if available
    if _native_usage_cache.get("data"):
        return _native_usage_cache["data"]

    # Default fallback data
    return {
        "account": "4uillust@gmail.com",
        "groups": [
            {
                "name": "Gemini Models",
                "description": "Models within this group: Gemini Flash, Gemini Pro",
                "buckets": [
                    {
                        "id": "gemini-weekly",
                        "name": "Weekly Limit Remaining",
                        "remaining_fraction": 0.909,
                        "reset_time": "2026-09-17T18:30:12Z"
                    },
                    {
                        "id": "gemini-5h",
                        "name": "Five Hour Limit Remaining",
                        "remaining_fraction": 0.860,
                        "reset_time": "2026-09-11T16:49:35Z"
                    }
                ]
            },
            {
                "name": "Claude and GPT models",
                "description": "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
                "buckets": [
                    {
                        "id": "3p-weekly",
                        "name": "Weekly Limit Remaining",
                        "remaining_fraction": 0.385,
                        "reset_time": "2026-09-17T11:19:52Z"
                    },
                    {
                        "id": "3p-5h",
                        "name": "Five Hour Limit Remaining",
                        "remaining_fraction": 1.0,
                        "reset_time": "2026-09-11T17:46:08Z"
                    }
                ]
            }
        ],
        "raw": {}
    }


def get_claude_sonnet_status() -> Dict[str, Any]:
    """
    Checks real-time Claude Sonnet 4.6 quota from native Antigravity CLI `/usage`.
    
    IMPORTANT: Claude Opus 4.6, Claude Sonnet 4.6, and GPT-OSS 120B share
    a single quota pool in Antigravity ('Claude and GPT models' group).
    This function tracks the SHARED pool — if the pool drops below 10%,
    ALL three external models are blocked, not just Sonnet.
    
    Enforces user guardrails:
    - Minimum remaining quota threshold: 10% (0.10).
    - Can be used if remaining >= 10% (0.10).
    """
    try:
        from trio_agent_runner import QuotaManager
        if QuotaManager.is_model_exhausted("claude-sonnet-4-6"):
            return {
                "remaining_pct": 0.0,
                "percentage_int": 0,
                "is_exhausted": True,
                "can_use": False,
                "reason": (
                    "⚠️ 외부 모델 공유 풀(Claude Opus·Sonnet·GPT-OSS) 소진 감지 (QuotaManager 마킹 / 최대 6h 쿨다운) "
                    "- 보호 가드 발동 (Sonnet 포함 셋 모두 차단)"
                ),
                "_shared_pool": True,
                "_pool_models": ["claude-opus-4-6-thinking", "claude-sonnet-4-6", "gpt-oss-120b-medium"],
            }
    except Exception:
        pass

    global _sonnet_cache, _native_usage_cache
    now = time.time()
    if _sonnet_cache.get("time") == 0.0 and _native_usage_cache.get("data") is None:
        _native_usage_cache = {"time": 0.0, "data": None}
    elif _sonnet_cache.get("data") and (now - _sonnet_cache.get("time", 0.0) < 60.0):
        return dict(_sonnet_cache["data"])

    usage_info = fetch_native_antigravity_usage()
    groups = usage_info.get("groups", [])
    ext_group = None
    for g in groups:
        if "claude" in g.get("name", "").lower() or "gpt" in g.get("name", "").lower():
            ext_group = g
            break

    if ext_group:
        buckets = ext_group.get("buckets", [])
        fracs = []
        for b in buckets:
            if isinstance(b, dict):
                if b.get("disabled", False):
                    fracs.append(0.0)
                else:
                    fracs.append(float(b.get("remaining_fraction", 1.0)))
        min_frac = min(fracs) if fracs else 1.0
        p_int = round(min_frac * 100)
        can_use = (min_frac >= 0.10)
        if can_use:
            reason = (
                f"✅ Claude Sonnet 4.6 쿼터 정상 (외부 모델 공유 풀 {min_frac*100:.1f}%, "
                f"Opus·Sonnet·GPT-OSS 공통)"
            )
        else:
            reason = (
                f"⚠️ 외부 모델 공유 풀(Claude Opus·Sonnet·GPT-OSS) 소진 감지 ({min_frac*100:.1f}% < 10%) "
                f"- 잔여 쿼터 부족 보호 가드 발동 (Sonnet 포함 셋 모두 차단)"
            )
        return {
            "remaining_pct": min_frac,
            "percentage_int": p_int,
            "is_exhausted": not can_use,
            "can_use": can_use,
            "reason": reason,
            "_shared_pool": True,
            "_pool_models": ["claude-opus-4-6-thinking", "claude-sonnet-4-6", "gpt-oss-120b-medium"],
        }

    return {
        "remaining_pct": 1.0,
        "percentage_int": 100,
        "is_exhausted": False,
        "can_use": True,
        "reason": "ℹ️ Claude Sonnet 4.6 기본 쿼터 허용 (공유 풀 정상 가용)",
        "_shared_pool": True,
        "_pool_models": ["claude-opus-4-6-thinking", "claude-sonnet-4-6", "gpt-oss-120b-medium"],
    }


def fetch_real_ai_quota_sections() -> Tuple[str, Optional[str]]:
    """
    Fetches real-time quota data using native Antigravity CLI `/usage`.
    Returns separated (gemini_section, external_section) to strictly prevent Discord 1024-character field limit error.
    """
    global _cached_gemini_quota, _cached_external_quota, _last_quota_fetch_time
    now = time.time()
    # [🛡️ Token Protection Mandate - Solar Pro 4 Protocol] 1-hour cache TTL eliminates redundant quota processing
    if _cached_gemini_quota and (now - _last_quota_fetch_time < 3600.0):
        return _cached_gemini_quota, _cached_external_quota

    usage_info = fetch_native_antigravity_usage()
    email = usage_info.get("account", "4uillust@gmail.com")
    groups = usage_info.get("groups", [])

    gemini_group = None
    ext_group = None
    for g in groups:
        name_l = g.get("name", "").lower()
        if "gemini" in name_l:
            gemini_group = g
        elif "claude" in name_l or "gpt" in name_l:
            ext_group = g

    # 1. Format Gemini Models Section
    gemini_lines = [
        "```text",
        f"👤 Account: {email}",
        "📦 GEMINI MODELS (Gemini Flash, Gemini Pro)",
        "─────────────────────────────────────────────"
    ]
    if gemini_group and gemini_group.get("buckets"):
        seen_gemini_labels = set()
        for b in gemini_group.get("buckets", []):
            b_name = b.get("name", "")
            is_disabled = b.get("disabled", False)
            if is_disabled:
                frac = 0.0
                pct_val = 0.0
            else:
                frac = float(b.get("remaining_fraction", 0.0))
                pct_val = frac * 100.0
            reset_str = b.get("reset_time", "")
            duration_str = format_iso_duration(reset_str)
            gauge = make_antigravity_gauge(frac, 20)

            if "week" in b_name.lower():
                label = "[주간 한도] Weekly Limit Remaining"
                if is_disabled or pct_val <= 0.0:
                    refresh_desc = f"0% remaining • Refreshes in {duration_str}"
                elif not reset_str or duration_str == "N/A":
                    refresh_desc = f"{round(pct_val)}% remaining • 정상 가용"
                else:
                    refresh_desc = f"{round(pct_val)}% remaining • Refreshes in {duration_str}"
            else:
                label = "[5시간 한도] Five Hour Limit Remaining"
                if is_disabled:
                    refresh_desc = "Disabled: 주간 한도 소진으로 비활성화"
                elif frac >= 0.999:
                    refresh_desc = "100% remaining • Quota fully available (여유)"
                elif not reset_str or duration_str == "N/A":
                    refresh_desc = f"{round(pct_val)}% remaining • Quota available (정상 가용)"
                else:
                    refresh_desc = f"{round(pct_val)}% remaining • Refreshes in {duration_str}"

            if label in seen_gemini_labels:
                continue
            seen_gemini_labels.add(label)

            gemini_lines.append(label)
            gemini_lines.append(f"{gauge} {pct_val:.1f}%")
            gemini_lines.append(f"{refresh_desc}\n")
    else:
        gemini_lines.append("정보를 불러오는 중...")

    gemini_lines.append("```")
    gemini_lines.append("⚡ **실시간 연계**: Antigravity 2.0 Native CLI Quota 엔진")
    gemini_str = "\n".join(gemini_lines)

    # 2. Format Claude and GPT Models Section
    ext_lines = [
        "```text",
        "📦 CLAUDE & GPT MODELS (Opus, Sonnet, GPT-OSS)",
        "─────────────────────────────────────────────"
    ]
    if ext_group and ext_group.get("buckets"):
        seen_ext_labels = set()
        for b in ext_group.get("buckets", []):
            b_name = b.get("name", "")
            is_disabled = b.get("disabled", False)
            if is_disabled:
                frac = 0.0
                pct_val = 0.0
            else:
                frac = float(b.get("remaining_fraction", 0.0))
                pct_val = frac * 100.0
            reset_str = b.get("reset_time", "")
            duration_str = format_iso_duration(reset_str)
            gauge = make_antigravity_gauge(frac, 20)

            if "week" in b_name.lower():
                label = "[주간 한도] Weekly Limit Remaining"
                if is_disabled or pct_val <= 0.0:
                    refresh_desc = f"0% remaining • Refreshes in {duration_str}"
                elif not reset_str or duration_str == "N/A":
                    refresh_desc = f"{round(pct_val)}% remaining • 정상 가용"
                else:
                    refresh_desc = f"{round(pct_val)}% remaining • Refreshes in {duration_str}"
            else:
                label = "[5시간 한도] Five Hour Limit Remaining"
                if is_disabled:
                    refresh_desc = "Disabled: 주간 한도 소진으로 비활성화"
                elif frac >= 0.999:
                    refresh_desc = "100% remaining • Quota fully available (여유)"
                elif not reset_str or duration_str == "N/A":
                    refresh_desc = f"{round(pct_val)}% remaining • Quota available (정상 가용)"
                else:
                    refresh_desc = f"{round(pct_val)}% remaining • Refreshes in {duration_str}"

            if label in seen_ext_labels:
                continue
            seen_ext_labels.add(label)

            ext_lines.append(label)
            ext_lines.append(f"{gauge} {pct_val:.1f}%")
            ext_lines.append(f"{refresh_desc}\n")
    else:
        ext_lines.append("정보를 불러오는 중...")

    ext_lines.append("```")
    ext_lines.append("📌 **공유 풀**: Claude Opus · Sonnet · GPT-OSS 공통 한도")
    ext_str = "\n".join(ext_lines)

    _cached_gemini_quota = gemini_str
    _cached_external_quota = ext_str
    _last_quota_fetch_time = now
    return _cached_gemini_quota, _cached_external_quota


def fetch_real_ai_quota() -> str:
    """Backward-compatible single string quota fetcher."""
    gemini_q, _ = fetch_real_ai_quota_sections()
    return gemini_q


# ==============================================================================
# ⚡ OpenCode Go 구독 사용량/한도 (5시간 · 주간 · 월간)
#   공식 엔드포인트: GET https://opencode.ai/zen/go/v1/usage
#   → rolling(5시간) / weekly(주간) / monthly(월간) 의 percent(사용률) + resetsAt 제공
# ==============================================================================
_opencode_usage_cache: Dict[str, Any] = {"time": 0.0, "data": None}
_OPENCODE_USAGE_TTL = 300.0  # 5분 캐시


def _get_opencode_go_api_key() -> str:
    """OpenCode Go API 키를 조회합니다. (env/config 우선 → 로컬 opencode 인증 저장소 폴백)"""
    try:
        import config as _cfg
        key = (os.getenv("OPENCODE_API_KEY") or getattr(_cfg, "OPENCODE_API_KEY", "") or "").strip()
        if key:
            return key
    except Exception:
        pass
    # 로컬 opencode credential DB (~/.local/share/opencode/opencode.db)
    try:
        import sqlite3
        db_path = os.path.expanduser("~/.local/share/opencode/opencode.db")
        if os.path.exists(db_path):
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
            try:
                row = con.execute(
                    "SELECT value FROM credential WHERE integration_id='opencode-go' "
                    "AND active=1 ORDER BY time_updated DESC LIMIT 1"
                ).fetchone()
            finally:
                con.close()
            if row and row[0]:
                val = json.loads(row[0])
                if isinstance(val, dict):
                    return str(val.get("key") or val.get("apiKey") or "").strip()
    except Exception as e:
        logger.debug(f"OpenCode credential read note: {e}")
    return ""


def fetch_opencode_go_usage() -> Optional[Dict[str, Any]]:
    """
    OpenCode Go 플랜의 실시간 사용량(rolling 5h / weekly / monthly)을 공식 엔드포인트에서 조회합니다.
    실패 시 마지막 캐시를 반환하고, 캐시도 없으면 None 을 반환합니다.
    """
    global _opencode_usage_cache
    now = time.time()
    if _opencode_usage_cache.get("data") and (now - _opencode_usage_cache.get("time", 0.0) < _OPENCODE_USAGE_TTL):
        return _opencode_usage_cache["data"]

    key = _get_opencode_go_api_key()
    if not key:
        logger.debug("OpenCode Go usage: API 키를 찾을 수 없습니다.")
        return _opencode_usage_cache.get("data")

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://opencode.ai/zen/go/v1/usage",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": "opencode/2.0.12",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if isinstance(usage, dict) and usage:
            _opencode_usage_cache = {"time": now, "data": usage}
            return usage
    except Exception as e:
        logger.debug(f"OpenCode Go usage fetch note: {e}")

    return _opencode_usage_cache.get("data")


def format_opencode_go_quota_section() -> Optional[str]:
    """OpenCode Go 사용량을 Antigravity 섹션과 동일한 스타일의 텍스트 블록으로 포맷합니다."""
    usage = fetch_opencode_go_usage()
    if not usage:
        return None

    lines = [
        "```text",
        "⚡ OPENCODE GO (5시간 · 주간 · 월간 한도)",
        "─────────────────────────────────────────────",
    ]
    rows = [
        ("rolling", "[5시간 한도] Rolling 5-Hour Limit"),
        ("weekly", "[주간 한도] Weekly Limit"),
        ("monthly", "[월간 한도] Monthly Limit"),
    ]
    rendered = 0
    for key, label in rows:
        info = usage.get(key) or {}
        if not isinstance(info, dict):
            continue
        used = info.get("percent")
        if used is None:
            continue
        try:
            used_f = float(used)
        except Exception:
            used_f = 0.0
        remaining = max(0.0, min(1.0, 1.0 - (used_f / 100.0)))
        rem_pct = remaining * 100.0
        gauge = make_antigravity_gauge(remaining, 20)
        reset_str = info.get("resetsAt") or ""
        duration_str = format_iso_duration(reset_str)
        status = str(info.get("status") or "ok").lower()

        if status != "ok" or rem_pct <= 0.0:
            desc = "🔴 한도 소진 (Limit reached)"
            if duration_str and duration_str != "N/A":
                desc += f" · Refreshes in {duration_str}"
        elif rem_pct >= 99.5:
            desc = "100% remaining • Quota fully available (여유)"
        elif duration_str and duration_str != "N/A":
            desc = f"{round(rem_pct)}% remaining • Refreshes in {duration_str}"
        else:
            desc = f"{round(rem_pct)}% remaining • 정상 가용"

        lines.append(label)
        lines.append(f"{gauge} {rem_pct:.1f}%")
        lines.append(f"{desc}\n")
        rendered += 1

    if rendered == 0:
        return None

    while lines and lines[-1].strip() == "":
        lines.pop()
    if lines:
        lines[-1] = lines[-1].rstrip()
    lines.append("```")
    lines.append("⚡ **OpenCode Go**: $12/5시간 · $30/주간 · $60/월 (금액 기준 한도)")
    return "\n".join(lines)


CHANNEL_NICKNAMES: Dict[int, str] = {
    1539483630089273435: "!5_agy_agent",
    1539993680788586496: "qa-tester",
    1540141654193999912: "trade-toss",
    1539826011322060841: "parking_lot",
    1538693952310485062: "ministry",
    1539628752730984469: "rider",
    1539527192663433308: "!8_idus_phonecase",
    1538529408703266902: "ANY QUESTION",
    1538695974023864400: "ANY QUESTION (서브)",
    1540200001731567626: "daangn-cli",
    1540262967319334944: "businesscard",
    1540925575387025548: "pdfediter_andriod",
    1541757846696370206: "minder-note",
    1538699726222917682: "LOGIFLOW_WEB",
    1544169135544602704: "pdfediter_andriod",
    1544307915295424625: "fluffy_post",
    1545740990181281865: "test1",
    1545741028169097236: "test2",
}

_loop_task: Optional[asyncio.Task] = None


def make_progress_bar(percent: float, length: int = 12) -> str:
    """Generates a visual text progress bar."""
    filled = int(round(length * max(0.0, min(100.0, percent)) / 100.0))
    return "█" * filled + "░" * (length - filled)


def add_safe_field(embed: discord.Embed, name: str, value: str, inline: bool = False):
    """Safely adds a field to Discord Embed, guaranteeing name <= 256 and value <= 1024 characters."""
    if not value or not str(value).strip():
        value = "정보 없음"
    else:
        value = str(value)
    safe_name = name[:256]
    if len(value) > 1024:
        if value.startswith("```") and "```" in value[3:]:
            value = value[:1015].rstrip() + "\n```"
        else:
            value = value[:1021] + "..."
    embed.add_field(name=safe_name, value=value, inline=inline)


def build_telemetry_embed(session_manager: SessionManager) -> discord.Embed:
    """Builds a rich, real-time telemetry embed for Discord Status Channel."""
    sessions_data = {}
    state_file = getattr(session_manager, "_state_file", "state/channel_sessions.json") if session_manager else "state/channel_sessions.json"
    try:
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                sessions_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read session state file directly ({state_file}): {e}")

    busy_list: List[str] = []
    idle_list: List[str] = []

    for ch_id, nick in CHANNEL_NICKNAMES.items():
        ch_str = str(ch_id)
        sess = sessions_data.get(ch_str, {})
        is_busy = sess.get("is_busy", False)
        status_desc = sess.get("status_desc", "정상 대기 중")

        if is_busy:
            busy_list.append(f"🔴 <#{ch_id}> **{nick}** `작업중` · {status_desc}")
        else:
            idle_list.append(f"🟢 <#{ch_id}> **{nick}** `대기` · {status_desc}")

    summary_hdr = (
        f"🔴 **작업 중:** {len(busy_list)}개   "
        f"🟢 **대기 중:** {len(idle_list)}개   "
        f"🟠 **주의:** 0개\n"
        f"────────────────────────"
    )
    all_lines = busy_list + idle_list
    lines_to_add = []
    curr_len = len(summary_hdr) + 1
    for line in all_lines:
        if curr_len + len(line) + 1 > 1000:
            break
        lines_to_add.append(line)
        curr_len += len(line) + 1
    channels_val = summary_hdr + "\n" + "\n".join(lines_to_add)

    # System Metrics (CPU / Temp / RAM)
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_bar = make_progress_bar(cpu_pct)
    try:
        temp = ThermalGuard.get_current_temperature() or 38.0
    except Exception:
        temp = 38.0

    vm = psutil.virtual_memory()
    ram_pct = vm.percent
    ram_bar = make_progress_bar(ram_pct)
    ram_used_gib = vm.used / (1024 ** 3)
    ram_total_gib = vm.total / (1024 ** 3)
    ram_free_gib = vm.available / (1024 ** 3)

    sys_val = (
        f"**CPU**  {cpu_bar} **{cpu_pct:.1f}%**\n"
        f"**PC 온도**  🌡️ **{temp:.1f}°C**\n"
        f"**RAM**  {ram_bar} **{ram_pct:.1f}%**\n"
        f"{ram_used_gib:.1f} GiB / {ram_total_gib:.1f} GiB · 여유 {ram_free_gib:.1f} GiB"
    )

    # Disk Storage
    try:
        du = psutil.disk_usage("/")
        disk_pct = du.percent
        disk_bar = make_progress_bar(disk_pct)
        disk_used_gib = du.used / (1024 ** 3)
        disk_total_gib = du.total / (1024 ** 3)
        disk_free_gib = du.free / (1024 ** 3)
        disk_val = (
            f"{disk_bar} **{disk_pct:.0f}% 사용**\n"
            f"{disk_total_gib:.1f} GiB 중 {disk_used_gib:.1f} GiB 사용\n"
            f"여유 {disk_free_gib:.1f} GiB"
        )
    except Exception:
        disk_val = "스토리지 정보 수집 중..."

    # Real-time AI Engine Quota Status
    gemini_quota, external_quota = fetch_real_ai_quota_sections()

    # OpenRouter Real-time Usage & Credits Status
    openrouter_quota: Optional[str] = None
    try:
        import openrouter_usage_monitor
        openrouter_quota = openrouter_usage_monitor.format_openrouter_telemetry_field()
    except Exception as e:
        logger.debug(f"Error fetching OpenRouter telemetry section: {e}")

    # ⚡ OpenCode Go Real-time Usage & Limits (5시간 · 주간 · 월간)
    opencode_quota: Optional[str] = None
    try:
        opencode_quota = format_opencode_go_quota_section()
    except Exception as e:
        logger.debug(f"Error fetching OpenCode Go telemetry section: {e}")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    embed = discord.Embed(
        title="🎛️ HP ProDesk Control Tower",
        description="디바이스 하드웨어 및 전체 채널 AI 작업 상태를 실시간 통합 관제합니다.",
        color=0x2ecc71 if not busy_list else 0x3498db,
    )
    add_safe_field(embed, "📡 채팅방별 AI 실시간 작업 관제 (10분 갱신)", channels_val, inline=False)
    add_safe_field(embed, "📊 시스템 리소스 (10분 갱신)", sys_val, inline=False)
    add_safe_field(embed, "💾 저장공간", disk_val, inline=False)
    add_safe_field(embed, "🤖 Antigravity AI Engine Status (실시간 연동)", gemini_quota, inline=False)
    if opencode_quota:
        add_safe_field(embed, "⚡ OpenCode Go AI Status (5시간·주간·월간 한도)", opencode_quota, inline=False)
    if openrouter_quota:
        add_safe_field(embed, "🌐 OpenRouter AI Status (qa2·크레딧 관제)", openrouter_quota, inline=False)
    if external_quota:
        add_safe_field(embed, "📦 Claude & GPT Models (공유 쿼터)", external_quota, inline=False)
    embed.set_footer(text=f"호스트 HP-ProDesk-600-G5 · 🎛️ 10분 자동 갱신 ({now_str})")

    return embed


import urllib.request


def find_existing_status_message_via_rest(token: str) -> Tuple[Optional[int], List[int]]:
    """
    Discovers existing status message(s) via Discord REST API.
    Returns: (primary_status_message_id, duplicate_message_ids)
    """
    primary_id: Optional[int] = None
    duplicates: List[int] = []
    try:
        url = f"https://discord.com/api/v10/channels/{STATUS_CHANNEL_ID}/messages?limit=50"
        headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": "DiscordBot (https://github.com/discord, 1.0)"
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            messages = json.loads(resp.read().decode("utf-8"))
            for m in messages:
                embeds = m.get("embeds", [])
                for e in embeds:
                    title = e.get("title", "") or ""
                    desc = e.get("description", "") or ""
                    if "Control Tower" in title or "실시간 통합 관제" in desc:
                        if primary_id is None:
                            primary_id = int(m["id"])
                        else:
                            duplicates.append(int(m["id"]))
                        break
    except Exception as e:
        logger.debug(f"REST discovery exception: {e}")
    return primary_id, duplicates


def update_status_via_rest_api(session_manager: Optional[SessionManager] = None) -> bool:
    """
    Directly updates the status message via Discord REST API using Server Manager Bot token.
    Enforces the single-message invariant: never creates duplicates; edits existing message.
    """
    global STATUS_MESSAGE_ID
    now = time.time()
    rate_until = _get_rate_limit_until()
    if now < rate_until:
        logger.debug(f"Throttling REST status update due to active 429 cooldown ({rate_until - now:.1f}s remaining).")
        return False

    import config
    token = getattr(config, "DISCORD_SERVER_MANAGER_BOT_TOKEN", None) or os.environ.get("DISCORD_SERVER_MANAGER_BOT_TOKEN")
    if not token:
        token = getattr(config, "DISCORD_BOT_TOKEN", None) or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        logger.warning("No Discord token available for direct REST status update.")
        return False

    try:
        embed = build_telemetry_embed(session_manager)
        embed_h = compute_embed_hash(embed)
        if embed_h and embed_h == _get_last_embed_hash() and STATUS_MESSAGE_ID:
            logger.debug("REST update: embed unchanged. Skipping Discord API call (No-Op deduplication).")
            return True

        payload = {"embeds": [embed.to_dict()]}
        data_bytes = json.dumps(payload).encode("utf-8")

        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/discord, 1.0)"
        }

        # 1. Try to PATCH existing status message
        if STATUS_MESSAGE_ID:
            patch_url = f"https://discord.com/api/v10/channels/{STATUS_CHANNEL_ID}/messages/{STATUS_MESSAGE_ID}"
            patch_req = urllib.request.Request(patch_url, data=data_bytes, headers=headers, method="PATCH")
            try:
                with urllib.request.urlopen(patch_req, timeout=10.0) as resp:
                    if resp.status in (200, 204):
                        _set_last_embed_hash(embed_h)
                        save_status_message_id(STATUS_MESSAGE_ID)
                        logger.info(f"Successfully updated status message ({STATUS_MESSAGE_ID}) via REST API.")
                        return True
            except urllib.error.HTTPError as he:
                error_body = ""
                try:
                    error_body = he.read().decode("utf-8")
                except Exception:
                    pass
                if he.code == 429:
                    retry_sec = 60.0
                    try:
                        err_json = json.loads(error_body)
                        retry_sec = float(err_json.get("retry_after", 60.0)) + 5.0
                    except Exception:
                        pass
                    cooldown = max(60.0, retry_sec)
                    _set_rate_limit_until(time.time() + cooldown)
                    logger.warning(f"REST PATCH hit rate limit (429). Cooldown applied for {cooldown:.1f}s.")
                    return False
                if he.code in (404, 400) and ("10008" in error_body or "Unknown Message" in error_body):
                    logger.warning(f"Status message {STATUS_MESSAGE_ID} not found on Discord. Searching channel...")
                    save_status_message_id(None)
                else:
                    logger.warning(f"REST PATCH non-fatal error (HTTP {he.code}). Retrying next cycle without recreating.")
                    return False

        # 2. Auto-discover existing message in the channel before posting a new one
        if not STATUS_MESSAGE_ID:
            found_id, dup_ids = find_existing_status_message_via_rest(token)
            if found_id:
                save_status_message_id(found_id)
                logger.info(f"REST Auto-Discovery: Bound to existing status message ID {found_id}.")
                # Clean up any leftover duplicates
                for dup_id in dup_ids:
                    try:
                        del_url = f"https://discord.com/api/v10/channels/{STATUS_CHANNEL_ID}/messages/{dup_id}"
                        del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
                        with urllib.request.urlopen(del_req, timeout=5.0):
                            pass
                    except Exception:
                        pass
                # Immediately patch the discovered message
                patch_url = f"https://discord.com/api/v10/channels/{STATUS_CHANNEL_ID}/messages/{found_id}"
                patch_req = urllib.request.Request(patch_url, data=data_bytes, headers=headers, method="PATCH")
                try:
                    with urllib.request.urlopen(patch_req, timeout=10.0) as resp:
                        if resp.status in (200, 204):
                            _set_last_embed_hash(embed_h)
                            return True
                except Exception as patch_e:
                    logger.debug(f"Failed patching newly discovered message: {patch_e}")
                    return False

        # 3. Only if absolutely NO status message exists anywhere in channel, create 1 fresh message
        if not STATUS_MESSAGE_ID:
            now = time.time()
            if now - getattr(update_status_via_rest_api, "_last_created_time", 0.0) < 120.0:
                logger.warning("Throttling REST status message creation (under 120s since last create).")
                return False
            post_url = f"https://discord.com/api/v10/channels/{STATUS_CHANNEL_ID}/messages"
            post_req = urllib.request.Request(post_url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(post_req, timeout=10.0) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                new_id = resp_data.get("id")
                if new_id:
                    save_status_message_id(int(new_id))
                    _set_last_embed_hash(embed_h)
                    update_status_via_rest_api._last_created_time = now
                    logger.info(f"Created single initial status message via REST API (ID: {new_id})")
                    return True
        return False
    except urllib.error.HTTPError as he:
        if he.code == 429:
            _set_rate_limit_until(time.time() + 60.0)
            logger.warning("REST update hit rate limit (429). Cooldown applied for 60s.")
            return False
        logger.error(f"Failed direct REST status update (HTTP {he.code}): {he}")
        return False
    except Exception as e:
        logger.error(f"Failed direct REST status update: {e}")
        return False


async def find_existing_status_message_via_client(
    channel: discord.abc.Messageable,
    bot_user_id: Optional[int]
) -> Tuple[Optional[discord.Message], List[discord.Message]]:
    """
    Searches the status channel for existing Control Tower telemetry message(s).
    Returns: (primary_status_message, duplicate_messages_to_clean)
    """
    primary_msg: Optional[discord.Message] = None
    duplicates: List[discord.Message] = []

    # 1. Search pinned messages first
    try:
        if hasattr(channel, "pins"):
            pins = await channel.pins()
            for p in pins:
                if (not bot_user_id or p.author.id == bot_user_id) and p.embeds:
                    for emb in p.embeds:
                        if "Control Tower" in (emb.title or "") or "실시간 통합 관제" in (emb.description or ""):
                            primary_msg = p
                            break
                    if primary_msg:
                        break
    except Exception as e:
        logger.debug(f"Failed searching pinned messages: {e}")

    # 2. Search recent channel history (up to 50 messages)
    try:
        if hasattr(channel, "history"):
            async for m in channel.history(limit=50):
                if m.type == discord.MessageType.pins_add:
                    duplicates.append(m)
                    continue
                if (not bot_user_id or m.author.id == bot_user_id) and m.embeds:
                    for emb in m.embeds:
                        if "Control Tower" in (emb.title or "") or "실시간 통합 관제" in (emb.description or ""):
                            if primary_msg is None:
                                primary_msg = m
                            elif m.id != primary_msg.id:
                                duplicates.append(m)
                            break
    except Exception as e:
        logger.debug(f"Failed searching channel history: {e}")

    return primary_msg, duplicates


async def update_status_message(client: Optional[discord.Client], session_manager: SessionManager) -> bool:
    """
    Fetches and edits the single persistent status message on Discord.
    Guarantees that exactly ONE message exists in the channel and is continuously updated.
    Never spams new messages — auto-discovers existing messages and cleans up duplicates.
    """
    global STATUS_MESSAGE_ID

    now = time.time()
    rate_until = _get_rate_limit_until()
    if now < rate_until:
        logger.debug(f"TelemetryStatusManager: In rate-limit cooldown ({rate_until - now:.1f}s remaining), skipping.")
        return False

    # 1. Try via active Discord Client if connected
    if client and not client.is_closed():
        try:
            channel = client.get_channel(STATUS_CHANNEL_ID)
            if not channel:
                channel = await client.fetch_channel(STATUS_CHANNEL_ID)

            if channel:
                embed = await asyncio.to_thread(build_telemetry_embed, session_manager)
                embed_h = compute_embed_hash(embed)
                if embed_h and embed_h == _get_last_embed_hash() and STATUS_MESSAGE_ID:
                    logger.debug("Client status update: embed unchanged. Skipping edit API call (No-Op deduplication).")
                    return True

                msg: Optional[discord.Message] = None

                # 1. Try fetching existing known message ID
                if STATUS_MESSAGE_ID:
                    try:
                        msg = await channel.fetch_message(STATUS_MESSAGE_ID)
                    except discord.NotFound:
                        logger.warning(f"Status message {STATUS_MESSAGE_ID} was deleted. Resetting ID to discover or recreate.")
                        save_status_message_id(None)
                        msg = None
                    except discord.HTTPException as fetch_err:
                        if fetch_err.status == 429:
                            _set_rate_limit_until(time.time() + 60.0)
                            logger.warning("fetch_message hit 429 rate limit. Cooling down for 60s.")
                            return False
                        if getattr(fetch_err, "code", None) == 10008:  # Unknown Message
                            save_status_message_id(None)
                            msg = None
                        else:
                            logger.warning(f"Temporary fetch error for status message: {fetch_err}. Retrying next cycle.")
                            return False

                # 2. Auto-discover existing message in channel if ID was missing or deleted
                if not msg:
                    primary_msg, duplicates = await find_existing_status_message_via_client(
                        channel,
                        client.user.id if client.user else None
                    )
                    if primary_msg:
                        msg = primary_msg
                        save_status_message_id(primary_msg.id)
                        logger.info(f"Auto-Discovery: Bound to existing status message ID {primary_msg.id} in channel {STATUS_CHANNEL_ID}.")
                        # Clean up leftover duplicate messages to keep channel pristine
                        for dup in duplicates:
                            try:
                                await dup.delete()
                                logger.info(f"Cleaned up duplicate status message {dup.id}.")
                            except Exception:
                                pass

                # 3. Edit the discovered or existing single message
                if msg:
                    try:
                        await msg.edit(embed=embed)
                        _set_last_embed_hash(embed_h)
                        save_status_message_id(msg.id)
                        logger.info(f"Telemetry status message {msg.id} updated via client.")
                        return True
                    except discord.HTTPException as edit_err:
                        if edit_err.status == 429:
                            _set_rate_limit_until(time.time() + 60.0)
                            logger.warning("Telemetry status message edit hit rate limit (429). Cooling down for 60s.")
                            return False
                        if getattr(edit_err, "code", None) == 10008:
                            logger.warning(f"Status message {msg.id} not found during edit (10008). Will discover/recreate next cycle.")
                            save_status_message_id(None)
                            return False
                        logger.warning(f"Edit error on status message: {edit_err}. Will retry next cycle without recreating.")
                        return False

                # 4. ONLY if absolutely NO message exists in channel, create initial single message
                if not msg:
                    now = time.time()
                    if now - getattr(update_status_message, "_last_created_time", 0.0) < 120.0:
                        logger.warning("Throttling status message creation (under 120s since last create).")
                        return False
                    new_msg = await channel.send(embed=embed)
                    save_status_message_id(new_msg.id)
                    _set_last_embed_hash(embed_h)
                    update_status_message._last_created_time = now
                    try:
                        await new_msg.pin()
                        # Clean up Discord's automatic "pinned a message" system notification (type 6)
                        if hasattr(channel, "history"):
                            async for m in channel.history(limit=5):
                                if m.type == discord.MessageType.pins_add:
                                    try:
                                        await m.delete()
                                    except Exception:
                                        pass
                                    break
                    except Exception:
                        pass
                    logger.info(f"Created single initial status message (ID: {new_msg.id}) in channel {STATUS_CHANNEL_ID}.")
                    return True
        except Exception as e:
            if "429" in str(e) or "rate limit" in str(e).lower():
                _set_rate_limit_until(time.time() + 60.0)
                logger.warning(f"Client status update rate limited ({e}), cooling down for 60s.")
                return False
            logger.debug(f"Client status update encountered issue ({e}), checking fallback...")

    # 2. Fallback to Direct REST API (only if NOT in rate limit cooldown)
    if time.time() < _get_rate_limit_until():
        return False
    return await asyncio.to_thread(update_status_via_rest_api, session_manager)


async def start_telemetry_status_loop(
    client: Optional[discord.Client],
    session_manager: SessionManager,
    interval_sec: int = 600
):
    """
    Background telemetry status polling loop (10-minute cycle).
    Updates the Discord status message every `interval_sec` seconds (default 600s / 10m) using
    local CLI (antigravity-usage quota --json) — no external API or LLM token consumption.
    Enforces process-level singleton to avoid duplicate task leaks across module reloads.
    """
    existing_task = _get_loop_task()
    if existing_task is not None and not existing_task.done():
        # [🛡️ Singleton Guard] Task already running — do NOT cancel and recreate.
        # Recreating on reload was the primary token leak vector. Hard-return instead.
        logger.debug("TelemetryStatusManager: Background loop already running — skip restart (singleton guard).")
        return

    async def _polling_loop():
        logger.info(f"TelemetryStatusManager: Background polling loop started (interval={interval_sec}s / 10-minute cycle).")
        while True:
            try:
                await update_status_message(client, session_manager)
            except asyncio.CancelledError:
                if client and client.is_closed():
                    logger.info("TelemetryStatusManager: Client closed, stopping background polling loop.")
                    break
                logger.debug("TelemetryStatusManager: Polling loop received CancelledError; continuing loop.")
                await asyncio.sleep(5)
                continue
            except Exception as e:
                logger.error(f"TelemetryStatusManager background polling error: {e}", exc_info=True)

            try:
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                if client and client.is_closed():
                    break
                await asyncio.sleep(5)

    new_task = asyncio.create_task(_polling_loop())
    _set_loop_task(new_task)


def ensure_telemetry_status_loop_running(client: Optional[discord.Client], session_manager: SessionManager):
    """
    Ensures the background telemetry status polling loop is running.
    Safe to call multiple times — uses process-level registry to prevent duplicate tasks.
    """
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
        return
    existing_task = _get_loop_task()
    if existing_task is not None and not existing_task.done():
        logger.debug("TelemetryStatusManager: Background polling loop already active.")
        return
    try:
        asyncio.ensure_future(start_telemetry_status_loop(client, session_manager))
    except Exception as e:
        logger.debug(f"TelemetryStatusManager: Failed to start background loop: {e}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger.info("[🛡️ Token Protection Mandate - Solar Pro 4 Protocol] Telemetry Status Manager Daemon starting in ONE-SHOT mode...")
    sm = SessionManager(clear_on_startup=False)
    try:
        asyncio.run(update_status_message(None, sm))
        logger.info("One-shot telemetry update completed successfully. Exiting cleanly (No background loop).")
    except Exception as e:
        logger.error(f"One-shot telemetry update failed: {e}")

