#!/usr/bin/env python3
"""
live_model_catalog.py

Unified, automatic live model-catalog discovery for every engine exposed by
`!model` (model_manager.ENGINE_PRESETS).

Why this module exists
----------------------
`!model` used to render model pickers from **hardcoded** `ENGINE_PRESETS`
lists. Those lists rot: new upstream models never appear, and retired ones
stay selectable until a human hand-edits the source. The user had to
"manually refresh" the list over and over.

This module makes the catalog **self-healing**:

  * Each engine declares one or more *live providers* (HTTP API and/or CLI).
  * Providers are queried automatically, **unioned** (never shadowing one
    another), cached with a TTL, and revalidated in the background.
  * Static `ENGINE_PRESETS` are demoted to (a) a seed for ordering and
    (b) a guaranteed fallback when every live source is unavailable.
  * A daemon thread refreshes the catalog periodically so `!model` is
    current *without* any human action, and a manual `force_refresh` path
    exists for the dashboard's refresh button.

Design guarantees
-----------------
1. **Non-blocking** — `get_models()` never blocks on network/CLI. It returns
   the cached snapshot instantly and triggers a background refresh when the
   TTL has expired (same stale-while-revalidate pattern already proven by
   `model_registry.AntigravityModelRegistry`).
2. **Union, not shadow** — a partial source can never hide models that
   another healthy source reports. (This was the concrete OpenCode bug:
   the Zen API reported 30 `opencode-go/*` models and silently masked the
   7 `opencode/*` free-tier models that `opencode models` knows about.)
3. **Fail-open** — any provider error degrades to "fewer models", never to
   a crash or an empty picker.
4. **No secret leakage** — API keys are never logged, only masked suffixes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("LiveModelCatalog")

BASE_DIR = Path(__file__).resolve().parent

# How long a snapshot is considered fresh. Model catalogs move slowly, so a
# generous TTL keeps us from hammering provider APIs, while the background
# refresher guarantees the data is never meaningfully stale.
CACHE_TTL: float = 1800.0  # 30 minutes

# Hard ceilings so a runaway source can never blow up memory or the picker.
MAX_MODELS_PER_ENGINE = 600
HTTP_TIMEOUT = 8.0
CLI_TIMEOUT = 20.0

ModelPair = Tuple[str, str]  # (model_id, human_label)


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
def _read_env_key(*names: str) -> str:
    """
    Read the first non-empty key from the environment, then from `.env`.

    `config.py` is not imported here on purpose: this module is imported by
    `model_manager`, which is itself imported by `config`-adjacent code. A
    lazy read keeps the import graph acyclic and makes the module usable
    from scripts/tests without booting the whole app.
    """
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    try:
        env_path = BASE_DIR / ".env"
        if not env_path.is_file():
            return ""
        wanted = set(names)
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in wanted:
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"[LiveCatalog] .env read skipped: {exc}")
    return ""


def _http_json(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
    """GET a JSON document, returning None on any failure."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug(f"[LiveCatalog] HTTP GET failed ({url}): {exc}")
        return None


def _run_cli(binary: Optional[str], args: List[str]) -> str:
    """Run a CLI and return stdout, or '' on any failure. Never raises."""
    if not binary:
        return ""
    try:
        proc = subprocess.run(
            [binary] + args,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            # Keep the child from inheriting a huge/odd environment.
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        return proc.stdout or ""
    except Exception as exc:
        logger.debug(f"[LiveCatalog] CLI {' '.join([binary] + args)} failed: {exc}")
        return ""


def _resolve_binary(env_var: str, candidates: List[str]) -> Optional[str]:
    """Locate an engine CLI without requiring it to be on PATH."""
    from_env = (os.getenv(env_var) or "").strip()
    if from_env and os.path.isfile(from_env) and os.access(from_env, os.X_OK):
        return from_env
    for cand in candidates:
        path = os.path.expanduser(cand)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _dedupe_union(*groups: List[ModelPair]) -> List[ModelPair]:
    """Union several (id, label) lists, first-seen wins, order preserved."""
    out: List[ModelPair] = []
    seen: set = set()
    for group in groups:
        for pair in group or []:
            try:
                mid, label = pair
            except (TypeError, ValueError):
                continue
            mid = (mid or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append((mid, (label or mid).strip()))
            if len(out) >= MAX_MODELS_PER_ENGINE:
                return out
    return out


def _split_discovered(live: List[ModelPair], seed: List[ModelPair]) -> Tuple[List[ModelPair], List[ModelPair]]:
    """
    Split a live list into (curated, discovered).

    "discovered" = models the live providers reported that are **not** in the
    hand-curated seed, i.e. exactly the models the user used to have to add by
    hand. Callers surface these first so a brand-new upstream model is visible
    immediately instead of being buried past the Discord 25-option limit.
    """
    seed_ids = {mid for mid, _ in seed or []}
    known = [p for p in live if p[0] in seed_ids]
    fresh = [p for p in live if p[0] not in seed_ids]
    return known, fresh


def _order_by_seed(live: List[ModelPair], seed: List[ModelPair]) -> List[ModelPair]:
    """
    Order a live list so curated/known models keep their curated rank and
    newly-discovered models are appended (alphabetically) at the end.

    This preserves the long-tail hand-tuning of ENGINE_PRESETS while still
    surfacing brand-new upstream models.
    """
    rank = {mid: idx for idx, (mid, _) in enumerate(seed)}
    known = [p for p in live if p[0] in rank]
    fresh = [p for p in live if p[0] not in rank]
    known.sort(key=lambda p: rank[p[0]])
    fresh.sort(key=lambda p: p[0].lower())
    return known + fresh


# -----------------------------------------------------------------------------
# per-engine live providers
# -----------------------------------------------------------------------------
def _discover_opencode(seed: List[ModelPair]) -> List[ModelPair]:
    """
    OpenCode = the router's own authoritative catalog: `opencode models` CLI.

    [🎯 라우터 정합성] 이전에는 OpenCode Zen Go API(`/zen/go/v1/models`)를 CLI 와
    **합집합으로 병합**했다. 그러나 Zen API 는 라우터가 실제로 라우팅하지 않는 모델까지
    반환한다(실측: API opencode-go 36개 vs 라우터 29개 → 7개 유령 모델이 `!model` 에 노출).
    라우터가 실제 제공하는 모델만 보여주기 위해 CLI 카탈로그만 authoritative 로 사용한다.
    CLI 조회가 실패하면 curated seed 로 폴백한다(fail-open).
    """
    binary = _resolve_binary(
        "OPENCODE_PATH",
        [
            "~/.opencode/bin/opencode",
            "~/.local/bin/opencode",
            "/usr/local/bin/opencode",
            "/usr/bin/opencode",
        ],
    )
    if not binary:
        logger.debug("[LiveCatalog] opencode CLI not found; using seed")
        return list(seed)

    out = _run_cli(binary, ["models"])
    live: List[ModelPair] = []
    for line in out.splitlines():
        mid = line.strip()
        # `opencode models` prints one bare model id per line.
        if mid and "/" in mid and not mid.lower().startswith(("warning", "error", "timestamp")):
            live.append((mid, _pretty_model_label(mid, seed)))
    if not live:
        logger.debug("[LiveCatalog] opencode: CLI unavailable; returning seed")
        return list(seed)
    return _order_by_seed(live, seed)


def _discover_openrouter(seed: List[ModelPair]) -> List[ModelPair]:
    """
    Hermes / OpenRouter: live `GET https://openrouter.ai/api/v1/models`.

    This replaces a 13-entry handcrafted list with the full upstream
    catalog (hundreds of models), including the free tier.
    """
    key = _read_env_key("OPENROUTER_API_KEY")
    if not key:
        logger.debug("[LiveCatalog] OPENROUTER_API_KEY missing; using seed only")
        return list(seed)

    payload = _http_json(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    items = (payload or {}).get("data") or []
    if not items:
        return list(seed)

    live: List[ModelPair] = []
    for item in items:
        mid = str((item or {}).get("id") or "").strip()
        if not mid:
            continue
        # Drop `:batch` variants (73+ near-duplicates that add no value).
        if ":batch" in mid:
            continue
        arch = item.get("architecture") or {}
        ins = arch.get("input_modalities") or []
        outs = arch.get("output_modalities") or []
        # Keep only chat/text-capable models: this is a coding-agent picker,
        # so image-generation-only and embedding endpoints are noise.
        if ins and "text" not in ins:
            continue
        if outs and "text" not in outs:
            continue
        live.append((mid, _pretty_model_label(mid, seed)))

    if not live:
        return list(seed)
    return _order_by_seed(live, seed)


def _discover_codex(seed: List[ModelPair]) -> List[ModelPair]:
    """OpenAI Codex CLI: `codex debug models` renders the raw catalog JSON."""
    binary = _resolve_binary(
        "CODEX_PATH",
        [
            "~/.npm-global/bin/codex",
            "/home/jangsung/.npm-global/bin/codex",
            "~/.local/bin/codex",
            "/usr/local/bin/codex",
            "/usr/bin/codex",
        ],
    )
    if not binary:
        logger.debug("[LiveCatalog] codex CLI not found; using seed only")
        return list(seed)

    out = _run_cli(binary, ["debug", "models"])
    if not out.strip():
        return list(seed)
    # Some builds print a WARNING banner before the JSON payload.
    start = out.find("{")
    if start < 0:
        return list(seed)
    try:
        payload = json.loads(out[start:])
    except Exception as exc:
        logger.debug(f"[LiveCatalog] codex catalog parse failed: {exc}")
        return list(seed)

    live: List[ModelPair] = []
    for m in payload.get("models") or []:
        slug = str((m or {}).get("slug") or "").strip()
        if not slug:
            continue
        # `visibility: hide` entries are internal/hidden from normal use.
        if str(m.get("visibility") or "list") == "hide":
            continue
        display = m.get("display_name") or slug
        live.append((slug, f"{display} (Codex)"))
    if not live:
        return list(seed)
    return _order_by_seed(live, seed)


def _discover_agy(seed: List[ModelPair]) -> List[ModelPair]:
    """
    Antigravity: already has a live registry (agy CLI + quota catalog).
    Delegate to it so there is exactly one source of truth.

    `force_refresh=True` is mandatory here: `AntigravityModelRegistry` boots with
    its *static* fallback catalog already loaded and a fresh timestamp, so a plain
    call would hand back the very seed we are trying to improve on and the
    catalog would never learn about a newly released Antigravity model.
    We are already on a background/refresh thread, so the quota CLI's few
    seconds of latency costs the UI nothing.
    """
    try:
        from model_registry import AntigravityModelRegistry  # type: ignore

        live = AntigravityModelRegistry.get_selectable_models(force_refresh=True)
        pairs: List[ModelPair] = []
        for m in live or []:
            mid = (m or {}).get("modelId")
            if not mid:
                continue
            label = m.get("label") or mid
            pairs.append((mid, str(label)))
        if pairs:
            return _order_by_seed(_dedupe_union(pairs), seed)
    except Exception as exc:
        logger.debug(f"[LiveCatalog] AGY registry unavailable: {exc}")
    return list(seed)


def _discover_claude(seed: List[ModelPair]) -> List[ModelPair]:
    """
    Anthropic Claude Code CLI exposes no model-listing command, and no
    ANTHROPIC_API_KEY is configured. Keep the curated seed; if the CLI does
    appear later its presence is recorded for diagnostics.
    """
    binary = _resolve_binary("CLAUDE_PATH", ["~/.local/bin/claude", "/usr/local/bin/claude"])
    if binary:
        logger.debug("[LiveCatalog] claude CLI present but exposes no model catalog command")
    return list(seed)


def _discover_copilot(seed: List[ModelPair]) -> List[ModelPair]:
    """
    GitHub Copilot CLI exposes no model-listing command and no
    GITHUB_TOKEN/COPILOT_TOKEN is configured. Keep the curated seed.
    """
    return list(seed)


def _filter_by_engine(engine: str, pairs: List[ModelPair]) -> List[ModelPair]:
    """
    Drop live models that do not belong to `engine` under this project's own
    ownership contract (`model_manager.GlobalModelManager.model_owner_engine`).

    Why this matters: `opencode models` happily reports the *whole* unified
    Zen catalog, including `openrouter/*` and `openai/*` slugs. Offering those
    while the role engine is `opencode` would be rejected downstream by
    `is_model_compatible_with_engine` (which maps them to `hermes`) and the
    role's engine would be auto-flipped — the exact "selected bot != running
    bot" regression this project already fixed once. Filtering here keeps the
    picker and the runtime engine in lockstep.
    """
    eng = canonical_engine(engine)
    out: List[ModelPair] = []
    for mid, label in pairs:
        low = (mid or "").lower()
        if eng == "opencode":
            # OpenCode CLI only resolves provider ids under its own namespaces.
            if low.startswith(("opencode-go/", "opencode/")):
                out.append((mid, label))
        elif eng == "hermes":
            if not low.startswith(("opencode-go/", "opencode/")):
                out.append((mid, label))
        else:
            out.append((mid, label))
    return out


def _pretty_model_label(mid: str, seed: List[ModelPair]) -> str:
    """
    Human label for a live model id.

    Prefers an existing curated label (so the hand-written Korean
    descriptions survive), otherwise derives one from the id itself.
    """
    for s_id, s_label in seed or []:
        if s_id == mid:
            return s_label
    # Fall back to a readable name from the id.
    tail = mid.rsplit("/", 1)[-1]
    name = re.sub(r"[-_]+", " ", tail).strip()
    name = re.sub(r"\s+", " ", name)
    if name:
        name = name[:1].upper() + name[1:]
    flags = []
    if ":free" in mid:
        flags.append("무과금")
    if mid.startswith("opencode/"):
        flags.append("free tier")
    suffix = f" ({', '.join(flags)})" if flags else ""
    provider = mid.rsplit("/", 1)[0] if "/" in mid else ""
    return f"{name} · {provider}{suffix}" if provider else f"{name}{suffix}"


def _discover_cmdc(seed: List[ModelPair]) -> List[ModelPair]:
    """
    Command Code (cmdc):
    `cmdc --list-models` CLI 로 실시간 모델 목록을 파싱합니다.
    """
    binary = _resolve_binary(
        "CMDC_PATH",
        [
            "/home/jangsung/.npm-global/bin/cmdc",
            "~/.npm-global/bin/cmdc",
            "/home/jangsung/.npm-global/bin/commandcode",
            "/usr/local/bin/cmdc",
            "/usr/bin/cmdc",
            "cmdc",
        ],
    )
    if not binary:
        logger.debug("[LiveCatalog] cmdc CLI not found; using seed")
        return list(seed)

    out = _run_cli(binary, ["--list-models"])
    if not out:
        return list(seed)

    live: List[ModelPair] = []
    ignore_headers = (
        "Available models", "Open Source", "Stealth", "Anthropic",
        "OpenAI", "Google", "Sakana", "Meta", "xAI", "Decision models",
        "Pass the full id", "cmd --model", "Docs:",
    )
    for line in out.splitlines():
        line = line.strip()
        if not line or any(line.startswith(h) for h in ignore_headers):
            continue
        parts = line.split(None, 1)
        mid = parts[0].strip()
        if mid in ("cmd", "typesafe/jev", "the") or not re.match(r"^[a-zA-Z0-9_\-\.\/]+$", mid) or mid.startswith("http") or "{" in mid:
            continue
        desc = parts[1].strip() if len(parts) > 1 else ""
        label = f"{mid} ({desc})" if desc else _pretty_model_label(mid, seed)
        live.append((mid, label))

    if not live:
        return list(seed)
    return _order_by_seed(live, seed)


# Registry: canonical engine id -> (seed provider, live provider)
_PROVIDERS: Dict[str, Callable[[List[ModelPair]], List[ModelPair]]] = {
    "agy": _discover_agy,
    "opencode": _discover_opencode,
    "hermes": _discover_openrouter,
    "openrouter": _discover_openrouter,
    "codex": _discover_codex,
    "claude": _discover_claude,
    "copilot": _discover_copilot,
    "cmdc": _discover_cmdc,
    "commandcode": _discover_cmdc,
}

# Engines whose upstream offers no enumerable catalog (no `models` command and
# no API key configured). Their providers intentionally return the curated
# seed, so we must not advertise them as "live" in the dashboard.
_CURATED_ONLY_ENGINES = {"claude", "copilot"}

# Engines whose live source is the vendor's *own complete* CLI catalog
# (`opencode models`, `codex debug models`, `cmdc --list-models`). For these the live response is
# authoritative: merging the curated seed back in would re-surface models the
# vendor has already retired, which is exactly the "list is out of date" problem this module exists to fix.
_LIVE_IS_AUTHORITATIVE = {"opencode", "codex", "cmdc", "commandcode"}


def canonical_engine(engine: Optional[str]) -> str:
    eng = (engine or "agy").lower().strip()
    if eng in ("commandcode", "command-code", "command_code", "command"):
        return "cmdc"
    return "hermes" if eng in ("openrouter", "nous", "hermes-agent", "hermes_agent") else eng


def _seed_for(engine: str) -> List[ModelPair]:
    """Curated ENGINE_PRESETS seed for an engine, read lazily to avoid cycles."""
    try:
        from model_manager import ENGINE_PRESETS  # type: ignore

        return list(ENGINE_PRESETS.get(engine, {}).get("models", []))
    except Exception as exc:
        logger.debug(f"[LiveCatalog] seed read failed for {engine}: {exc}")
        return []


# -----------------------------------------------------------------------------
# catalog
# -----------------------------------------------------------------------------
class LiveModelCatalog:
    """
    Thread-safe, stale-while-revalidate cache of per-engine model catalogs.
    """

    # engine -> {"models": [(id,label)...], "fetched_at": float, "live": bool,
    #            "source": str, "error": str|None}
    _cache: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()
    _refreshing: set = set()
    _auto_thread: Optional[threading.Thread] = None
    _auto_stop = threading.Event()

    # -- public API --------------------------------------------------------
    @classmethod
    def get_models(
        cls, engine: Optional[str], force_refresh: bool = False
    ) -> List[ModelPair]:
        """
        Return the current model list for `engine`.

        Returns instantly (never blocks on I/O). If the snapshot is missing
        or expired, a background refresh is scheduled; the next call
        observes the new data.
        """
        eng = canonical_engine(engine)
        seed = _seed_for(eng)

        with cls._lock:
            entry = cls._cache.get(eng)
            fresh = bool(entry and (time.time() - entry["fetched_at"]) < CACHE_TTL)

        if force_refresh:
            cls._spawn_refresh(eng, blocking=False)
        elif not fresh:
            # Cold start or stale: populate in the background, return seed now.
            if not entry:
                return list(seed)
            cls._spawn_refresh(eng, blocking=False)

        if entry and entry.get("models"):
            return list(entry["models"])
        return list(seed)

    @classmethod
    def get_models_blocking(
        cls, engine: Optional[str], force_refresh: bool = True
    ) -> List[ModelPair]:
        """
        Synchronous fetch. Used by the explicit "refresh" dashboard action,
        where the user is actively waiting and an up-to-date answer matters.
        """
        eng = canonical_engine(engine)
        cls._refresh_sync(eng, force=force_refresh)
        with cls._lock:
            entry = cls._cache.get(eng)
        if entry and entry.get("models"):
            return list(entry["models"])
        return _seed_for(eng)

    @classmethod
    def get_status(cls, engine: Optional[str]) -> Dict[str, Any]:
        """Diagnostics for the dashboard footer."""
        eng = canonical_engine(engine)
        with cls._lock:
            entry = dict(cls._cache.get(eng) or {})
        seed = _seed_for(eng)
        if not entry or not entry.get("models"):
            refreshing = eng in cls._refreshing
            return {
                "engine": eng,
                "count": len(seed),
                "new_count": 0,
                "newest": [],
                "live": False,
                "source": "불러오는 중…" if refreshing else "curated (live 미조회)",
                "age_sec": None,
                "refreshing": refreshing,
                "error": entry.get("error") if entry else None,
            }
        newest = list(entry.get("discovered") or [])
        return {
            "engine": eng,
            "count": len(entry["models"]),
            "new_count": len(newest),
            "newest": newest,
            "live": bool(entry.get("live")),
            "source": entry.get("source", "live"),
            "age_sec": int(time.time() - entry.get("fetched_at", 0)),
            "refreshing": eng in cls._refreshing,
            "error": entry.get("error"),
        }

    @classmethod
    def get_new_models(cls, engine: Optional[str]) -> List[ModelPair]:
        """
        Models the live providers reported that the curated seed never contained.

        The `!model` picker promotes these to the top of the dropdown so a fresh
        upstream release is visible on the very next `!model` invocation instead
        of being buried past Discord's 25-option Select limit.
        """
        eng = canonical_engine(engine)
        with cls._lock:
            entry = cls._cache.get(eng) or {}
        return list(entry.get("discovered") or [])

    @classmethod
    def invalidate(cls, engine: Optional[str] = None) -> None:
        """Drop cached snapshots so the next read re-fetches."""
        with cls._lock:
            if engine is None:
                cls._cache.clear()
            else:
                cls._cache.pop(canonical_engine(engine), None)

    @classmethod
    def is_live_supported(cls, engine: Optional[str]) -> bool:
        return canonical_engine(engine) in _PROVIDERS

    @classmethod
    def start_auto_refresh(cls, interval: float = CACHE_TTL) -> None:
        """
        Start the daemon refresher. This is what removes the need for manual
        "refresh the list" work: the catalog keeps itself current.
        """
        if cls._auto_thread and cls._auto_thread.is_alive():
            return
        cls._auto_stop.clear()

        def _loop():
            while not cls._auto_stop.wait(interval):
                try:
                    for eng in list(_PROVIDERS.keys()):
                        cls._refresh_sync(eng, force=True)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug(f"[LiveCatalog] auto-refresh cycle error: {exc}")

        t = threading.Thread(target=_loop, daemon=True, name="LiveModelCatalogRefresh")
        t.start()
        cls._auto_thread = t
        logger.info(f"[LiveCatalog] auto-refresher started (every {int(interval / 60)}min)")

    @classmethod
    def stop_auto_refresh(cls) -> None:
        cls._auto_stop.set()

    @classmethod
    def warmup(cls, engines: Optional[List[str]] = None) -> None:
        """Kick off an initial background fetch for the given engines."""
        for eng in engines or list(_PROVIDERS.keys()):
            cls._spawn_refresh(eng, blocking=False)

    # -- internals ---------------------------------------------------------
    @classmethod
    def _spawn_refresh(cls, engine: str, blocking: bool = False) -> None:
        if engine in cls._refreshing:
            return
        cls._refreshing.add(engine)
        if blocking:
            try:
                cls._refresh_sync(engine, force=True)
            finally:
                cls._refreshing.discard(engine)
            return

        def _worker():
            try:
                cls._refresh_sync(engine, force=True)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(f"[LiveCatalog] bg refresh error [{engine}]: {exc}")
            finally:
                with cls._lock:
                    cls._refreshing.discard(engine)

        threading.Thread(target=_worker, daemon=True, name=f"LiveCatalog-{engine}").start()

    @classmethod
    def _refresh_sync(cls, engine: str, force: bool = True) -> None:
        eng = canonical_engine(engine)
        provider = _PROVIDERS.get(eng)
        if not provider:
            return
        seed = _seed_for(eng)
        if not force:
            with cls._lock:
                entry = cls._cache.get(eng)
            if entry and (time.time() - entry["fetched_at"]) < CACHE_TTL:
                return

        t0 = time.time()
        error: Optional[str] = None
        live: List[ModelPair] = []
        curated_only = eng in _CURATED_ONLY_ENGINES
        if not curated_only:
            try:
                live = provider(seed) or []
            except Exception as exc:
                error = str(exc)
                logger.debug(f"[LiveCatalog] provider failed [{eng}]: {exc}")

        # Enforce engine ownership so the picker can never offer a model that
        # the runtime engine would refuse (and auto-flip the role's engine).
        try:
            live = _filter_by_engine(eng, live)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"[LiveCatalog] engine filter skipped [{eng}]: {exc}")

        # Keep the curated seed as a floor so a degraded/empty live response
        # can never leave the picker empty.
        authoritative = eng in _LIVE_IS_AUTHORITATIVE
        merged = _dedupe_union(live, seed) if not authoritative else _dedupe_union(live)
        # If the live source reported strictly fewer models than the curated
        # seed and nothing was added, treat it as a degraded response and
        # serve the seed so previously-usable models don't vanish.
        live_added = len({m for m, _ in live} - {m for m, _ in seed})
        # [🛡 Last-known-good] A transient upstream hiccup (Antigravity's quota
        # API times out intermittently, and CLIs sometimes print a truncated
        # list) must never make models the user could already select disappear
        # from the picker. So the shrink check is expressed against what we
        # *actually served last time*, not merely against the curated seed:
        # a refresh that shrinks the list while adding nothing new is a partial
        # response, so we keep the previous good snapshot.
        with cls._lock:
            prev = dict(cls._cache.get(eng) or {})
        prev_models: List[ModelPair] = list(prev.get("models") or [])
        prev_discovered: List[ModelPair] = list(prev.get("discovered") or [])
        prev_ids = {m for m, _ in prev_models}
        # genuinely new relative to what the user can already see
        added_vs_prev = {m for m, _ in live} - prev_ids if live else set()
        shrunk_without_gain = bool(live and prev_models and len(merged) < len(prev_models) and not added_vs_prev)

        # "Degraded" = the live source returned a strict subset of the curated
        # seed and contributed nothing new. That signals a truncated/partial
        # response rather than a genuine shrink, so we keep the curated list.
        degraded = bool(live) and live_added == 0 and len(live) < len(seed)

        served_from_lkg = bool(
            (degraded or not live or shrunk_without_gain)
            and prev_models
            and len(prev_models) > len(seed)
        )

        if curated_only:
            source = "curated (업stream 카탈로그 미제공)"
            is_live = False
        elif served_from_lkg:
            source = "live (직전 정상 스냅샷 유지)"
            is_live = True
        elif degraded:
            source = "curated (live degraded)"
            is_live = False
        elif live:
            source = f"live ({int((time.time() - t0) * 1000)}ms)"
            is_live = True
        else:
            source = "curated (live 조회 실패)"
            is_live = False

        if curated_only:
            models_out: List[ModelPair] = list(seed)
            discovered: List[ModelPair] = []
        elif served_from_lkg:
            models_out = prev_models
            discovered = prev_discovered
        elif degraded:
            models_out = list(seed)
            discovered = []
        else:
            models_out = merged
            _, discovered = _split_discovered(live, seed)

        with cls._lock:
            cls._cache[eng] = {
                "models": models_out,
                "discovered": list(discovered),
                "fetched_at": time.time(),
                "live": is_live,
                "source": source,
                "error": error,
            }
        if discovered:
            logger.info(
                f"[LiveCatalog] {eng}: {len(discovered)} newly discovered model(s) "
                f"-> {', '.join(m for m, _ in discovered[:8])}"
                + (" …" if len(discovered) > 8 else "")
            )
        if degraded:
            logger.warning(
                f"[LiveCatalog] {eng}: live source returned fewer models than the "
                f"curated seed; keeping curated list to avoid hiding valid models."
            )
