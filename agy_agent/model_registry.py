#!/usr/bin/env python3
"""
model_registry.py

Antigravity Dynamic Model Registry & Intelligent Resolver.
Inspects live Antigravity (/model, antigravity-usage quota) catalogs,
ensures zero hardcoded deprecated models (e.g. Gemini 2.5),
and dynamically resolves the best active models for Coder, QA, Auditor, Vision, and Planner.
"""

import os
import sys
import re
import json
import time
import shutil
import logging
import subprocess
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ModelRegistry")

# Fallback Antigravity 3.x official active catalog matching native Antigravity /model
_FALLBACK_OFFICIAL_CATALOG = [
    {
        "label": "Gemini 3.8 Flash (High)",
        "modelId": "gemini-3.8-flash-high",
        "category": "gemini",
        "recommended_roles": ["coder", "vision", "planner", "commander"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.8 Flash (Tiered)",
        "modelId": "gemini-3.8-flash-tiered",
        "category": "gemini",
        "recommended_roles": ["coder", "vision"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.7 Flash (High)",
        "modelId": "gemini-3.7-flash-high",
        "category": "gemini",
        "recommended_roles": ["coder", "qa_fallback", "commander"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.7 Flash (Tiered)",
        "modelId": "gemini-3.7-flash-tiered",
        "category": "gemini",
        "recommended_roles": ["coder", "qa_fallback"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.7 Flash (Medium)",
        "modelId": "gemini-3.7-flash-medium",
        "category": "gemini",
        "recommended_roles": ["commander", "orchestrator"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.1 Pro (High)",
        "modelId": "gemini-3.1-pro-high",
        "category": "gemini",
        "recommended_roles": ["qa", "auditor", "coder_deep"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.1 Pro (Low)",
        "modelId": "gemini-3.1-pro-low",
        "category": "gemini",
        "recommended_roles": ["qa", "fast_review"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.6 Flash (High)",
        "modelId": "gemini-3.6-flash-high",
        "category": "gemini",
        "recommended_roles": ["coder", "qa"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.6 Flash (Tiered)",
        "modelId": "gemini-3.6-flash-tiered",
        "category": "gemini",
        "recommended_roles": ["coder"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.6 Flash (Medium)",
        "modelId": "gemini-3.6-flash-medium",
        "category": "gemini",
        "recommended_roles": ["coder", "qa"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3.5 Flash (High)",
        "modelId": "gemini-3.5-flash-agent",
        "category": "gemini",
        "recommended_roles": ["coder", "qa"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Gemini 3 Flash",
        "modelId": "gemini-3-flash",
        "category": "gemini",
        "recommended_roles": ["vision", "planner", "fast_review"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "GPT-OSS 120B (Medium)",
        "modelId": "gpt-oss-120b-medium",
        "category": "gpt-oss",
        "recommended_roles": ["qa_main", "code_review", "auditor"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Claude Sonnet 4.6 (Thinking)",
        "modelId": "claude-sonnet-4-6",
        "category": "claude",
        "recommended_roles": ["qa_extended", "coder_deep"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    },
    {
        "label": "Claude Opus 4.6 (Thinking)",
        "modelId": "claude-opus-4-6-thinking",
        "category": "claude",
        "recommended_roles": ["architect", "deep_review"],
        "isAutocompleteOnly": False,
        "isExhausted": False,
    }
]

# Migration map for deprecated or autocomplete-only models
DEPRECATED_MIGRATION_MAP = {
    "gemini-2.5-flash": "gemini-3.8-flash-high",
    "gemini-2.5-flash-lite": "gemini-3-flash",
    "gemini-2.5-flash-thinking": "gemini-3.8-flash-high",
    "gemini-2.5-pro": "gemini-3.1-pro-high",
    "gemini-2.0-flash": "gemini-3-flash",
    "gemini-2.0-flash-exp": "gemini-3-flash",
    "gemini-2.0-pro": "gemini-3.1-pro-high",
    "gemini-1.5-pro": "gemini-3.1-pro-high",
    "gemini-1.5-flash": "gemini-3-flash",
}

# Deprecated Antigravity CLI 채팅 모델의 승격 대상 (flash 계열/ pro 계열/ lite 계열)
_SUPPORTED_FLASH_TARGET = "gemini-3.8-flash-high"
_SUPPORTED_PRO_TARGET = "gemini-3.1-pro-high"
_SUPPORTED_LITE_TARGET = "gemini-3-flash"

# 이미지 생성 모델(예: google/gemini-2.5-flash-image = Nano Banana)은 AGY CLI 채팅
# 모델이 아니라 OpenRouter/Google 이미지 API 전용 실모델이므로 절대 치환하지 않는다.
_IMAGE_MODEL_MARKERS = ("flash-image", "pro-image", "image-preview", "nano-banana", "nano_banana")


def migrate_deprecated_model(model_id: Optional[str]) -> Optional[str]:
    """
    [🚫 Deprecated AGY Guard] Antigravity CLI 가 더 이상 서비스하지 않는 구형 Gemini
    채팅 모델을 지원 중인 3.x 모델로 승격한다. 치환 대상이 아니면 None 을 반환한다.

      · gemini-2.5-flash / gemini-2.5-flash-* / gemini-2.0-flash / gemini-1.5-flash → gemini-3.8-flash-high
      · gemini-2.5-flash-lite (또는 `*lite*`)                                          → gemini-3-flash
      · gemini-2.5-pro / gemini-2.0-pro / gemini-1.5-pro                               → gemini-3.1-pro-high

    주의: 이미지 생성 모델(`google/gemini-2.5-flash-image`, `gemini-2.5-flash-image`)과
    외부 provider slug(`google/…`)는 AGY CLI 채팅 모델이 아니므로 건드리지 않는다.
    """
    if not model_id:
        return None
    raw = str(model_id).strip()
    if not raw:
        return None
    m = raw.lower()
    # 외부 provider slug (`provider/model`) 는 이미지 모델을 포함해 OpenRouter 전용이므로 제외.
    if "/" in m:
        return None
    # 이미지 생성 모델은 AGY CLI 채팅 라벨이 아니다 (실모델 ID 보존).
    if any(marker in m for marker in _IMAGE_MODEL_MARKERS):
        return None

    compact = re.sub(r"[\s_]+", "-", m)
    compact = re.sub(r"-+", "-", compact).strip("-")

    if compact in DEPRECATED_MIGRATION_MAP:
        return DEPRECATED_MIGRATION_MAP[compact]

    if "lite" in compact:
        return _SUPPORTED_LITE_TARGET
    if any(tag in compact for tag in ("gemini-2.5-pro", "gemini-2.0-pro", "gemini-1.5-pro")):
        return _SUPPORTED_PRO_TARGET
    if any(tag in compact for tag in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash")):
        return _SUPPORTED_FLASH_TARGET
    # `gemini 2.5 flash`, `gemini-2.5` 등 남은 2.x/1.x 표기도 포괄 승격.
    if re.search(r"gemini-?[12]\.", compact):
        return _SUPPORTED_PRO_TARGET if "pro" in compact else _SUPPORTED_FLASH_TARGET
    return None


class AntigravityModelRegistry:
    """
    Centralized Antigravity Model Registry.
    Dynamically discovers available models from Antigravity CLI (/model API)
    and enforces platform-compliant model selection.
    """

    _cached_models: List[Dict[str, Any]] = list(_FALLBACK_OFFICIAL_CATALOG)
    _last_fetch_time: float = time.time()
    _is_fetching: bool = False
    CACHE_TTL: float = 3600.0  # 1-hour cache — unified with telemetry_status_manager & QuotaManager to prevent quota polling leaks

    @classmethod
    def sync_token(cls) -> None:
        """Syncs Antigravity OAuth token to current user environment."""
        try:
            src = os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token")
            dst = os.path.expanduser("~/.antigravity-oauth-token")
            if os.path.exists(src):
                shutil.copy2(src, dst)
        except Exception as e:
            logger.debug(f"ModelRegistry: token sync failed: {e}")

    @classmethod
    def fetch_agy_cli_models(cls) -> List[Dict[str, Any]]:
        """
        Fetches the authoritative /model catalog directly by executing 'agy models'
        from the Google Antigravity CLI.
        """
        agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy") or "/usr/local/bin/agy"
        try:
            res = subprocess.run(
                [agy_bin, "models"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=6.0
            )
            if res.returncode == 0 and res.stdout.strip():
                cli_models = []
                for line in res.stdout.strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t", 1) if "\t" in line else re.split(r"\s{2,}", line, maxsplit=1)
                    if len(parts) == 2:
                        mid, lbl = parts[0].strip(), parts[1].strip()
                        mid_lower = mid.lower()
                        if "gemini" in mid_lower:
                            cat = "gemini"
                        elif any(k in mid_lower for k in ("claude", "sonnet", "opus")):
                            cat = "claude"
                        elif any(k in mid_lower for k in ("gpt-oss", "gpt", "oss")):
                            cat = "gpt-oss"
                        else:
                            cat = "external"
                        cli_models.append({
                            "modelId": mid,
                            "label": lbl,
                            "category": cat,
                            "isAutocompleteOnly": False,
                            "isExhausted": False,
                            "source": "agy_models_cli"
                        })
                if cli_models:
                    logger.info(f"ModelRegistry: Loaded {len(cli_models)} models from Antigravity CLI (/model).")
                    return cli_models
        except Exception as e:
            logger.warning(f"ModelRegistry: Failed to fetch models from 'agy models': {e}")
        return []

    @classmethod
    def fetch_quota_models(cls) -> List[Dict[str, Any]]:
        """
        Fetches quota-bearing and tiered models from antigravity-usage CLI.
        """
        cls.sync_token()
        usage_bin = (
            shutil.which("antigravity-usage")
            or (os.path.expanduser("~/.local/bin/antigravity-usage") if os.path.exists(os.path.expanduser("~/.local/bin/antigravity-usage")) else None)
            or (os.path.expanduser("~/.npm-global/bin/antigravity-usage") if os.path.exists(os.path.expanduser("~/.npm-global/bin/antigravity-usage")) else None)
        )
        cmd = [usage_bin, "quota", "--json"] if usage_bin else ["npx", "antigravity-usage", "quota", "--json"]

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=6.0
            )
            if res.returncode == 0 and res.stdout.strip().startswith("{"):
                data = json.loads(res.stdout)
                raw_models = data.get("models", [])
                if raw_models:
                    return raw_models
        except Exception as e:
            logger.debug(f"ModelRegistry: Quota fetch error: {e}")
        return []

    @classmethod
    def fetch_models(cls, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches live models from Antigravity:
        1. Primary: Executes 'agy models' to retrieve the official, real-time Antigravity /model catalog.
        2. Secondary: Enriches models with quota metadata (remaining percentage, reset times) from antigravity-usage.
        3. Fallback: Uses _FALLBACK_OFFICIAL_CATALOG if Antigravity CLI is temporarily unavailable.
        Runs CLI and Quota commands concurrently in worker threads to minimize latency.
        """
        now = time.time()
        if not force_refresh and cls._cached_models and (now - cls._last_fetch_time < cls.CACHE_TTL):
            return cls._cached_models

        cls._is_fetching = True
        cli_models = []
        quota_models = []
        try:
            # [🛡️ Quota Leak Fix — 2026-09-23] 'agy models' CLI 실행을 자동 경로에서 제거한다.
            # agy CLI 는 실행될 때마다 Language Server 를 스폰하고, 그 서버가
            # daily-cloudcode-pa.googleapis.com 을 주기 폴링하며 Gemini 한도를 조용히 소모한다.
            # 카탈로그는 토큰프리 OAuth 조회(antigravity-usage) + 정적 폴백만으로 구성하고,
            # `agy models` 는 사용자가 AGY_ALLOW_CLI_CATALOG=1 로 명시 허용한 경우에만 실행한다.
            allow_cli_catalog = (
                force_refresh
                and (os.getenv("AGY_ALLOW_CLI_CATALOG", "0") or "0").strip().lower()
                in ("1", "true", "yes", "on")
            )
            if allow_cli_catalog:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f_cli = executor.submit(cls.fetch_agy_cli_models)
                    f_quota = executor.submit(cls.fetch_quota_models)
                    cli_models = f_cli.result()
                    quota_models = f_quota.result()
            else:
                quota_models = cls.fetch_quota_models()
        except Exception as e:
            logger.warning(f"Parallel fetch error in ModelRegistry: {e}")
            if not quota_models:
                quota_models = cls.fetch_quota_models()
        finally:
            cls._is_fetching = False

        if not cli_models and not quota_models:
            if not cls._cached_models:
                cls._cached_models = list(_FALLBACK_OFFICIAL_CATALOG)
                cls._last_fetch_time = now
            return cls._cached_models

        merged: Dict[str, Dict[str, Any]] = {}

        # 1. Authoritative catalog: `agy models` CLI 결과 > 정적 폴백 카탈로그
        if cli_models:
            for m in cli_models:
                mid = m["modelId"]
                merged[mid] = dict(m)
        else:
            # CLI 를 띄우지 않았으므로 정적 폴백 카탈로그를 기본 골격으로 사용한다.
            for m in _FALLBACK_OFFICIAL_CATALOG:
                merged[m["modelId"]] = dict(m)

        # 2. Enrich with quota data & incorporate tiered non-autocomplete models
        for qm in quota_models:
            mid = qm.get("modelId")
            if not mid:
                continue
            if mid in merged:
                if qm.get("remainingPercentage") is not None:
                    merged[mid]["remainingPercentage"] = qm.get("remainingPercentage")
                if qm.get("resetTime") is not None:
                    merged[mid]["resetTime"] = qm.get("resetTime")
                if qm.get("isExhausted") is not None:
                    merged[mid]["isExhausted"] = qm.get("isExhausted")
                if qm.get("isAutocompleteOnly") is not None:
                    merged[mid]["isAutocompleteOnly"] = qm.get("isAutocompleteOnly")
            else:
                if not qm.get("isAutocompleteOnly", False):
                    mid_lower = mid.lower()
                    if "gemini" in mid_lower:
                        cat = "gemini"
                    elif any(k in mid_lower for k in ("claude", "sonnet", "opus")):
                        cat = "claude"
                    elif any(k in mid_lower for k in ("gpt-oss", "gpt", "oss")):
                        cat = "gpt-oss"
                    else:
                        cat = "external"
                    merged[mid] = {
                        "modelId": mid,
                        "label": qm.get("label", mid),
                        "category": cat,
                        "remainingPercentage": qm.get("remainingPercentage"),
                        "resetTime": qm.get("resetTime"),
                        "isExhausted": qm.get("isExhausted", False),
                        "isAutocompleteOnly": False,
                        "source": "quota_api"
                    }

        cls._cached_models = list(merged.values())
        cls._last_fetch_time = now
        logger.info(f"ModelRegistry: Successfully loaded {len(cls._cached_models)} Antigravity models (authoritative /model + quota).")
        return cls._cached_models

    @classmethod
    def get_selectable_models(cls, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Returns models that are eligible for normal agent chat, coding, and QA.
        Filters out 'isAutocompleteOnly' models (such as gemini-2.5-pro).
        Non-blocking: Always returns immediately from memory/cache. If cache is expired,
        triggers an asynchronous background refresh so Discord UI interactions never stall.
        """
        now = time.time()
        if force_refresh or not cls._cached_models:
            all_models = cls.fetch_models(force_refresh=force_refresh)
        else:
            # Stale-while-revalidate: return cached instantly, update in background if TTL passed
            if (now - cls._last_fetch_time >= cls.CACHE_TTL) and not cls._is_fetching:
                import threading
                def _bg_worker():
                    try:
                        cls.fetch_models(force_refresh=True)
                    except Exception as e:
                        logger.debug(f"Background model refresh error: {e}")
                threading.Thread(target=_bg_worker, daemon=True, name="ModelRegistryBgFetch").start()
            all_models = cls._cached_models

        return [
            m for m in all_models
            if not m.get("isAutocompleteOnly", False)
        ]

    @classmethod
    def get_selectable_model_ids(cls) -> List[str]:
        """Returns list of selectable model IDs."""
        models = cls.get_selectable_models()
        ids = []
        for m in models:
            mid = m.get("modelId")
            if mid and mid not in ids:
                ids.append(mid)
        return ids

    @classmethod
    def is_antigravity_model(cls, model_id: str) -> bool:
        """Checks if a model ID is recognized by Antigravity."""
        if not model_id:
            return False
        clean = model_id.strip().lower()
        if clean.startswith("openrouter/") or "/" in clean:
            return False
        if any(marker in clean for marker in ("qwen", "deepseek", "llama", "solar", "ling", "minimax")):
            return False
        valid_ids = [m.lower() for m in cls.get_selectable_model_ids()]
        if clean in valid_ids:
            return True

        if any(nm in clean for nm in ("gpt-oss-120b-medium", "claude-sonnet-4-6", "claude-opus-4-6-thinking")):
            return True

        # Check normalized root names: e.g. gemini-3.8-flash-high <-> gemini-3.8-flash-tiered
        root_clean = re.sub(r'-(high|middle|low|tiered|agent|extra-low)$', '', clean)
        valid_roots = [re.sub(r'-(high|middle|low|tiered|agent|extra-low)$', '', m) for m in valid_ids]
        return root_clean in valid_roots or any(root_clean == r for r in valid_roots) or clean.startswith("gemini-3")

    @classmethod
    def migrate_if_deprecated(cls, model_id: Optional[str]) -> Optional[str]:
        """
        Antigravity CLI 미지원(구형) Gemini 채팅 모델이면 지원 3.x 모델로 승격해 반환한다.
        승격 대상이 아니면 None. (전 모듈 공용 진입점 — `migrate_deprecated_model` 참조)
        """
        return migrate_deprecated_model(model_id)

    @classmethod
    def resolve_model(
        cls,
        role: str = "coder",
        preferred: Optional[str] = None
    ) -> str:
        """
        Intelligently resolves a model name based on Antigravity's /model values.
        - Automatically upgrades deprecated models (e.g. gemini-2.5 -> gemini-3.8-flash)
        - Strictly prevents selecting autocomplete-only or defunct models
        - Fallbacks to role-appropriate active models in the Antigravity catalog
        """
        role_lower = (role or "coder").lower().strip()

        # 1. If preferred model is specified, check deprecation map first
        if preferred:
            pref_clean = preferred.strip()
            pref_lower = pref_clean.lower()

            # Check direct migration map / any deprecated Gemini 2.x-1.x chat model
            upgraded = migrate_deprecated_model(pref_clean)
            if upgraded:
                logger.info(f"ModelRegistry: Upgraded deprecated model '{pref_clean}' -> '{upgraded}'")
                return upgraded

            # Check if it's an OpenRouter or external model slug
            if pref_lower.startswith("openrouter/") or "/" in pref_lower:
                return pref_clean

            # Check if preferred model is in current Antigravity selectable catalog
            selectable_ids = cls.get_selectable_model_ids()
            for mid in selectable_ids:
                if pref_lower == mid.lower() or pref_lower == mid.lower().replace("-high", "").replace("-medium", "").replace("-low", ""):
                    return mid

            # If user provided a specific valid Antigravity model name (e.g. gemini-3-flash, gemini-3.8-flash-high, gpt-oss-120b-medium, claude-sonnet-4-6, claude-opus-4-6-thinking)
            if pref_lower.startswith("gemini-3") or any(k in pref_lower for k in ("claude", "sonnet", "opus", "gpt-oss")):
                return pref_clean

        # 2. Dynamic resolution by Role using active catalog
        selectable = cls.get_selectable_models()
        selectable_ids = [m.get("modelId", "") for m in selectable if m.get("modelId")]

        # Role mappings based on Antigravity 3.x catalog
        if role_lower in ("coder", "code", "dev"):
            for candidate in ("gemini-3.8-flash-high", "gemini-3.8-flash-tiered", "gemini-3.7-flash-high", "gemini-3-flash", "gemini-3.1-pro-high"):
                if candidate in selectable_ids:
                    return candidate
            return "gemini-3.8-flash-high"

        if role_lower in ("qa", "qa_main", "reviewer", "auditor"):
            for candidate in ("gemini-3.1-pro-high", "gemini-pro-agent", "gemini-3.7-flash-high"):
                if candidate in selectable_ids:
                    return candidate
            return "gemini-3.1-pro-high"

        if role_lower in ("qa_fallback", "qa_backup"):
            for candidate in ("gemini-3.7-flash-high", "gemini-3.8-flash-high", "gemini-3-flash"):
                if candidate in selectable_ids:
                    return candidate
            return "gemini-3.7-flash-high"

        if role_lower in ("vision", "visual", "ocr", "inspector"):
            for candidate in ("gemini-3.8-flash-high", "gemini-3.8-flash-tiered", "gemini-3-flash"):
                if candidate in selectable_ids:
                    return candidate
            return "gemini-3.8-flash-high"

        if role_lower in ("planner", "intent_planner", "mcp_planner"):
            for candidate in ("gemini-3.8-flash-high", "gemini-3-flash", "gemini-3.7-flash-high"):
                if candidate in selectable_ids:
                    return candidate
            return "gemini-3.8-flash-high"

        if role_lower in ("commander", "manager"):
            for candidate in ("gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.8-flash-high"):
                if candidate in selectable_ids:
                    return candidate
            return "gemini-3.7-flash-high"

        # Default safe model
        return "gemini-3.8-flash-high"

    @classmethod
    def get_catalog_summary_text(cls) -> str:
        """
        Formats a clean, human-readable overview of all Antigravity /model values.
        """
        models = cls.fetch_models()
        if not models:
            return "⚠️ Antigravity 모델 카탈로그를 불러올 수 없습니다."

        lines = [
            "📡 **[Google Antigravity 공식 지원 모델 카탈로그]**",
            "*(※ Antigravity `/model` 및 실시간 쿼터 API 동기화)*\n"
        ]

        active_models = [m for m in models if not m.get("isAutocompleteOnly", False)]
        auto_models = [m for m in models if m.get("isAutocompleteOnly", False)]

        lines.append("🟢 **[일반 대화 및 개발·검증 활성 모델군]**")
        for m in active_models:
            label = m.get("label", m.get("modelId", "Unknown"))
            mid = m.get("modelId", "")
            remain = m.get("remainingPercentage")
            remain_str = f"({int(remain * 100)}% 잔여)" if remain is not None else ""
            lines.append(f"• **{label}** (`{mid}`) {remain_str}")

        if auto_models:
            lines.append("\n🔒 **[코드 자동완성(Autocomplete) 전용 모델군 - 대화 선택 불가]**")
            for m in auto_models:
                label = m.get("label", m.get("modelId", "Unknown"))
                mid = m.get("modelId", "")
                lines.append(f"• ~~{label}~~ (`{mid}`) *(자동완성 전용)*")

        return "\n".join(lines)


# Singleton instance helper
model_registry = AntigravityModelRegistry()
