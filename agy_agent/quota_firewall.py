#!/usr/bin/env python3
"""
🛡️ Antigravity Quota Firewall (Gemini 한도 자동 누수 차단막)
============================================================
사용자가 직접 요청하지 않은 **무인/자율(unattended) 컨텍스트**에서
Antigravity CLI(`agy`)의 Gemini 계열 모델 호출을 원천 차단합니다.

배경 (2026-09-23 포렌식):
  - `agy` CLI 는 실행될 때마다 Language Server 를 스폰하고, 그 서버가
    daily-cloudcode-pa.googleapis.com 을 주기적으로 폴링하면서
    Gemini 5시간 한도를 조용히 소모한다.
  - 텔레메트리 `/usage` 프로브, `agy models` 카탈로그 조회, Commander
    Gemini 강제 폴백, 자율 루프(Mode 3 Auto-Cycle) 등 **사용자 의사와 무관한**
    경로가 다수 존재하여, 자는 동안에도 한도가 줄어드는 현상이 발생했다.

동작:
  - `with unattended():` 블록 안에서는 Gemini 계열 모델 호출이 기본 차단된다.
  - 차단/허용 이력은 state/quota_audit.jsonl 에 JSONL 로 기록된다.
  - 운영상 긴급 허용이 필요하면 환경변수 `AGY_UNATTENDED_GEMINI=allow` 로 해제.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("QuotaFirewall")

_UNATTENDED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agy_unattended", default=False
)

_AUDIT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "state", "quota_audit.jsonl"
)

_GEMINI_MARKERS = ("gemini",)


def is_unattended() -> bool:
    """현재 실행 컨텍스트가 무인/자율 모드인지 여부."""
    return bool(_UNATTENDED.get())


class _UnattendedContext:
    """`with unattended():` 컨텍스트 매니저 (중첩 지원)."""

    def __enter__(self):
        self._token = _UNATTENDED.set(True)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            _UNATTENDED.reset(self._token)
        except Exception:
            _UNATTENDED.set(False)
        return False


def unattended() -> _UnattendedContext:
    """무인/자율 컨텍스트 진입 (Gemini 자동 호출 차단 구역)."""
    return _UnattendedContext()


def is_gemini_model(model: Optional[str]) -> bool:
    """Gemini 계열(퍼스트파티) 모델 여부."""
    if not model:
        return False
    low = str(model).lower()
    return any(m in low for m in _GEMINI_MARKERS)


def _allow_unattended_gemini() -> bool:
    val = (os.getenv("AGY_UNATTENDED_GEMINI", "block") or "block").strip().lower()
    return val in ("allow", "1", "true", "yes", "on")


@dataclass
class GuardDecision:
    allowed: bool
    reason: str = ""


def record_audit(
    event: str,
    model: Optional[str],
    source: str,
    allowed: bool,
    reason: str = "",
) -> None:
    """차단/허용 이력을 state/quota_audit.jsonl 에 기록한다 (실패해도 무시)."""
    try:
        os.makedirs(os.path.dirname(_AUDIT_PATH), exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "model": model,
            "source": source,
            "allowed": allowed,
            "unattended": is_unattended(),
            "reason": reason,
            "pid": os.getpid(),
        }
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # audit 실패가 본 작업을 막아서는 안 된다
        logger.debug(f"QuotaFirewall audit write failed: {e}")


def check_model(model: Optional[str], source: str = "") -> GuardDecision:
    """
    Antigravity CLI(`agy`) 모델 호출 허용 여부를 판정한다.

    - 무인/자율 컨텍스트 + Gemini 계열 모델  → 차단 (기본)
    - 그 외(사용자 직접 요청, 비-Gemini 모델) → 허용
    """
    gemini = is_gemini_model(model)
    if gemini and is_unattended() and not _allow_unattended_gemini():
        reason = (
            f"무인 자율 컨텍스트에서 Gemini 모델 자동 호출을 차단했습니다 "
            f"(model={model}). Antigravity 한도 자동 소모 방지 가드가 작동했습니다. "
            f"(긴급 허용: AGY_UNATTENDED_GEMINI=allow)"
        )
        record_audit("blocked", model, source, allowed=False, reason=reason)
        return GuardDecision(allowed=False, reason=reason)

    record_audit("allowed", model, source, allowed=True)
    return GuardDecision(allowed=True)
