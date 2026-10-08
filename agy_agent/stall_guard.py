#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[StallGuard] 채널 오케스트레이션 정지(무응답/대기열 교착) 감시 가드 (관찰 전용)
================================================================================

> **강제 회수는 제거되었습니다.** 이 가드는 어떤 경우에도 실행 중인 작업을 회수하지
> 않습니다 — 프로세스 kill, 오케스트레이션 태스크 cancel, `is_busy` 해제,
> 채널 대기열 락 교체를 **일절 수행하지 않습니다.** 오직 관찰·경보·증거 기록만 남깁니다.

## 왜 강제 회수를 없앴나
Think 모델(Claude Opus / DeepSeek R1 등)의 긴 추론, 장시간 QA(E2E 176+ 항목),
장시간 도구 실행은 stdout 이 오래 조용한 **정상 작업**이다. stdout 침묵만으로 "멈춤"을
단정해 회수하면 정상 작업을 오판해 죽이게 된다(예: qa1 코드 리뷰가 15분 조용했다는
이유만으로 강제 회수). 그래서 회수 동작 자체를 제거했다.

## 무엇을 하나 (10초 주기 상시 스캔)
* **(C) 대기열 정체**: 작업이 비어 있는데 큐의 요청이 `QUEUE_WAIT` 초 이상 방치
  → 디스패치 재호출(자체 교착 해소) + 증거 기록.
* **(W) 무응답 경보**: `last_progress_at` 기준 실제 진행이 `WARN` 초 이상 끊김
  → 채널에 1회 경보 발송 (`_stall_warned` 로 중복 발송 방지). **작업은 계속 실행된다.**

**핵심**: 하트비트(`last_progress_at`) 는 *진짜 진행* (CLI stdout 출력, 상태 전이)에서만
갱신된다. 디스코드 8초 주기 "진행 중..." 임베드 같은 **가짜 하트비트는 갱신하지 않는다.**

(죽은 좀비 `busy` 는 대기열 디스패치 시 `dispatch_next_in_queue` 가 별도로 정리한다.)

## 물리적 증거 (Evidence)
모든 감지/경보는 `reports/watchdog/stalls.json` 에 레코드로 남는다.
(AGENT 는 "안 멈춘다" 라는 주장을 말로 하지 않고 이 파일로 증명한다.)

## 환경변수
* `STALL_GUARD_WARN_SECONDS`   무응답 경보 기준 (기본 3000초)
* `STALL_GUARD_HARD_SECONDS`   심층 관찰 표시 등급 기준 (기본 4000초) — 회수 동작 없음
* `STALL_GUARD_DEEP_SECONDS`   심층 표시 상한 (기본 5000초) — 회수 동작 없음
* `STALL_GUARD_QUEUE_WAIT_SECONDS` 대기열 정체 기준 (기본 60초)
* `STALL_GUARD_TICK_SECONDS`   스캔 주기 (기본 10초)
* `STALL_GUARD_EVIDENCE`       증거 파일 경로
* `QUEUE_LOCK_TIMEOUT_SECONDS` 채널 대기열 락 취득 타임아웃 (기본 20초, bot.py 사용)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("agy.stall_guard")

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EVIDENCE_PATH = os.path.join(_MODULE_DIR, "reports", "watchdog", "stalls.json")


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        if raw is None or str(raw).strip() == "":
            return float(default)
        return float(raw)
    except Exception:
        return float(default)


WARN_SECONDS = _env_float("STALL_GUARD_WARN_SECONDS", 3000.0)
HARD_SECONDS = _env_float("STALL_GUARD_HARD_SECONDS", 4000.0)
# HARD/DEEP 는 이제 회수 트리거가 아니라 `!stall` 표시 등급(심층 관찰/상한)일 뿐이다.
# QA(E2E 176+ 항목)·Thinking 모델의 장시간 추론은 stdout 이 조용해도 정상이므로,
# 이 값들은 관찰 등급으로만 쓰고 어떤 강제 회수도 일으키지 않는다.
DEEP_SECONDS = _env_float("STALL_GUARD_DEEP_SECONDS", max(HARD_SECONDS, 5000.0))
QUEUE_WAIT_SECONDS = _env_float("STALL_GUARD_QUEUE_WAIT_SECONDS", 60.0)
TICK_SECONDS = _env_float("STALL_GUARD_TICK_SECONDS", 10.0)
LOOP_LAG_SECONDS = _env_float("STALL_GUARD_LOOP_LAG_SECONDS", 3.0)

MAX_RECORDS_IN_FILE = 200


class StallGuard:
    """채널 세션의 무응답/대기열 교착을 감지·경보한다 (강제 회수 없음)."""

    def __init__(
        self,
        session_manager: Any,
        *,
        send_fn: Optional[Callable[..., Any]] = None,
        dispatch_fn: Optional[Callable[[int], Any]] = None,
        warn_seconds: float = WARN_SECONDS,
        hard_seconds: float = HARD_SECONDS,
        deep_seconds: float = DEEP_SECONDS,
        queue_wait_seconds: float = QUEUE_WAIT_SECONDS,
        tick_seconds: float = TICK_SECONDS,
        evidence_path: str = DEFAULT_EVIDENCE_PATH,
        clock: Callable[[], float] = time.time,
        notify_fn: Optional[Callable[[int, str], Any]] = None,
    ):
        # 기본값 보정: 경보 < 심층관찰 < 심층상한 순서를 유지한다(표시 등급용).
        self.warn_seconds = max(1.0, float(warn_seconds))
        self.hard_seconds = max(self.warn_seconds + 1.0, float(hard_seconds))
        self.deep_seconds = max(self.hard_seconds + 1.0, float(deep_seconds))
        self.queue_wait_seconds = max(1.0, float(queue_wait_seconds))
        self.tick_seconds = max(0.5, float(tick_seconds))

        self.session_manager = session_manager
        self.dispatch_fn = dispatch_fn
        self.evidence_path = evidence_path
        self.clock = clock
        self.records: List[Dict[str, Any]] = []

        # send_fn (async, 디스코드 전송) 은 선택 사항. 있으면 그걸 쓰고 없으면 notify_fn 을 쓴다.
        self._send_fn = send_fn
        self._notify_fn = notify_fn

        self._stall_warned: Dict[int, float] = {}
        self._last_queue_nudge: Dict[int, float] = {}

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------
    def _sessions(self) -> List[Any]:
        try:
            return list(self.session_manager.get_all_sessions().values())
        except Exception as err:
            logger.error(f"StallGuard: 세션 목록 조회 실패: {err}")
            return []

    def _now(self) -> float:
        try:
            return float(self.clock())
        except Exception:
            return time.time()

    def _event(
        self,
        session: Any,
        action: str,
        silence: float,
        busy_seconds: float,
        detail: str = "",
    ) -> Dict[str, Any]:
        task = getattr(session, "current_task", None)
        proc = getattr(session, "current_process", None)
        return {
            "channel_id": getattr(session, "channel_id", None),
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": action,
            "silence_seconds": round(float(silence), 1),
            "busy_seconds": round(float(busy_seconds), 1),
            "status": getattr(session, "status", ""),
            "status_desc": str(getattr(session, "status_desc", ""))[:160],
            "task_alive": bool(task is not None and not task.done()) if task is not None else False,
            "process_pid": getattr(proc, "pid", None),
            "queue_depth": len(list(getattr(session, "queue", []) or [])),
            "detail": detail,
        }

    def _record(self, event: Dict[str, Any]) -> None:
        self.records.append(event)
        if len(self.records) > 1000:
            self.records = self.records[-500:]
        try:
            path = self.evidence_path
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            payload = {
                "schema": "stall_guard/v1",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "thresholds": {
                    "warn_seconds": self.warn_seconds,
                    "hard_seconds": self.hard_seconds,
                    "deep_seconds": self.deep_seconds,
                    "queue_wait_seconds": self.queue_wait_seconds,
                    "tick_seconds": self.tick_seconds,
                },
                "total_events": len(self.records),
                "events": self.records[-MAX_RECORDS_IN_FILE:],
            }
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception as err:
            logger.debug(f"StallGuard: 증거 기록 실패(무시): {err}")

    def _notify(self, channel_id: Optional[int], text: str) -> None:
        """채널 알림. 어떤 실패도 가드 자체를 죽이지 않는다."""
        if channel_id is None:
            return
        fn = self._notify_fn or self._send_fn
        if fn is None:
            return
        try:
            result = fn(channel_id, text)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    loop.create_task(self._await_safely(result))
        except Exception as err:
            logger.debug(f"StallGuard: 채널 알림 실패(무시): {err}")

    @staticmethod
    async def _await_safely(coro) -> None:
        try:
            await coro
        except Exception as err:
            logger.debug(f"StallGuard: 알림 코루틴 실패(무시): {err}")

    def _schedule_dispatch(self, channel_id: Optional[int]) -> None:
        if channel_id is None or self.dispatch_fn is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            loop.create_task(self._await_safely(self.dispatch_fn(channel_id)))
        except Exception as err:
            logger.debug(f"StallGuard: 대기열 재가동 실패: {err}")

    # ------------------------------------------------------------------
    # 감시 로직
    # ------------------------------------------------------------------
    def scan(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """전 세션을 1회 스캔한다. 감지된 이벤트(경보/큐니지) 목록을 반환."""
        now = self._now() if now is None else float(now)
        events: List[Dict[str, Any]] = []
        for session in self._sessions():
            try:
                event = self._scan_session(session, now)
            except Exception:
                logger.error(f"StallGuard: 세션 스캔 중 예외\n{traceback.format_exc()}")
                continue
            if event:
                events.append(event)
                self._record(event)
        return events

    def _scan_session(self, session: Any, now: float) -> Optional[Dict[str, Any]]:
        channel_id = getattr(session, "channel_id", None)
        busy = bool(getattr(session, "is_busy", False))
        queue = list(getattr(session, "queue", []) or [])

        progress_at = float(getattr(session, "last_progress_at", 0.0) or 0.0)
        busy_since = float(getattr(session, "busy_since", 0.0) or 0.0)
        if progress_at <= 0.0:
            # 하트비트 미기록(레거시 세션) → busy 시작 시점 또는 현재 시점을 기준으로 본다.
            progress_at = busy_since if busy_since > 0.0 else now
        silence = max(0.0, now - progress_at)
        busy_seconds = max(0.0, now - busy_since) if (busy and busy_since > 0.0) else 0.0

        # ---------------- (C) 대기열 정체: 작업 없는데 요청만 쌓임 ----------------
        if queue and not busy:
            # created_at 이 0.0 이어도 유효한 과거 시각이므로 falsy 폴백으로 갈아타지 않는다.
            oldest: Optional[float] = None
            for item in queue:
                raw_created = getattr(item, "created_at", None)
                try:
                    created = float(raw_created)
                except (TypeError, ValueError):
                    created = None
                if created is None:
                    created = now
                oldest = created if oldest is None else min(oldest, created)
            waited = max(0.0, now - (oldest if oldest is not None else now))
            if waited >= self.queue_wait_seconds:
                last = self._last_queue_nudge.get(channel_id, 0.0)
                if now - last >= self.queue_wait_seconds:
                    self._last_queue_nudge[channel_id] = now
                    detail = (
                        f"작업이 비어 있는데 대기열 {len(queue)}건이 {int(waited)}초간 방치 "
                        f"→ 디스패치 재호출"
                    )
                    logger.warning(f"[StallGuard] 채널 {channel_id}: {detail}")
                    self._schedule_dispatch(channel_id)
                    return self._event(session, "queue_nudge", silence, busy_seconds, detail)
            return None

        if not busy:
            # 진행 없음 상태로 돌아왔으면 경보 플래그를 초기화한다.
            self._stall_warned.pop(channel_id, None)
            return None

        # ---------------- 진행이 살아있으면 경보 잠금 해제 ----------------
        # (실제 하트비트가 잠금 시점보다 뒤면 진행이 재개된 것 → 다음 정지에 다시 경보)
        warned_at = self._stall_warned.get(channel_id)
        if warned_at is not None and progress_at > warned_at:
            self._stall_warned.pop(channel_id, None)
        if silence < self.warn_seconds:
            self._stall_warned.pop(channel_id, None)
            return None

        # ---------------- (W) 무응답 경보 (채널 1회, 관찰 전용) ----------------
        # [🚫 강제 회수 제거] stdout 침묵만으로 작업을 회수하지 않는다.
        # Think 모델의 긴 추론/장시간 도구 실행은 stdout 이 조용한 정상 작업이므로,
        # 여기서는 경보만 남기고 작업은 계속 실행되게 둔다.
        if not self._stall_warned.get(channel_id):
            self._stall_warned[channel_id] = progress_at  # 경보 시점의 하트비트를 기준으로 잠금
            detail = (
                f"무응답 {int(silence)}초 경과 (기준 {int(self.warn_seconds)}초) → 경보 발송 "
                f"(강제 회수 없음 — 작업 계속 실행)"
            )
            logger.warning(f"[StallGuard] 채널 {channel_id}: {detail}")
            self._notify(
                channel_id,
                (
                    "🟡 **[StallGuard 무응답 경보]**\n"
                    f"⏱️ 실제 진행이 **{int(silence)}초** 동안 끊겼습니다 (진행 기준 {int(self.warn_seconds)}초).\n"
                    f"📌 현재 상태: `{getattr(session, 'status_desc', '')}`\n"
                    "✅ **강제 회수가 제거되어 작업을 중단하지 않습니다.** "
                    "긴 추론/장시간 도구 실행은 정상일 수 있으니 그대로 계속 진행됩니다."
                ),
            )
            return self._event(session, "stall_warn", silence, busy_seconds, detail)

        return None

    # ------------------------------------------------------------------
    # 감시 루프 (이벤트 루프 블로킹까지 감지)
    # ------------------------------------------------------------------
    async def run(self) -> None:
        logger.info(
            f"[StallGuard] 가동(관찰 전용 · 강제 회수 없음): 경보 {int(self.warn_seconds)}s / "
            f"대기열 정체 {int(self.queue_wait_seconds)}s / 주기 {self.tick_seconds}s"
        )
        while True:
            started = self._now()
            try:
                self.scan(now=started)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(f"[StallGuard] 스캔 예외\n{traceback.format_exc()}")

            work_elapsed = max(0.0, self._now() - started)
            sleep_for = max(0.2, self.tick_seconds - work_elapsed)
            try:
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                raise

            # 이벤트 루프 블로킹 프로브: sleep 예상 종료 시각 대비 실제 지연.
            expected = started + work_elapsed + sleep_for
            drift = self._now() - expected
            if drift > LOOP_LAG_SECONDS:
                logger.warning(
                    f"[StallGuard] 이벤트 루프 블로킹 의심: {drift:.1f}s 지연 "
                    f"(기준 {LOOP_LAG_SECONDS}s) — 동기 호출(blocking I/O) 임시 점검 필요"
                )


def build_guard(
    session_manager: Any,
    *,
    send_fn: Optional[Callable[..., Any]] = None,
    dispatch_fn: Optional[Callable[[int], Any]] = None,
    **kwargs: Any,
) -> StallGuard:
    """bot.py 에서 쓰는 팩토리 (환경변수 기반 기본값 적용)."""
    return StallGuard(
        session_manager,
        send_fn=send_fn,
        dispatch_fn=dispatch_fn,
        **kwargs,
    )


__all__ = [
    "StallGuard",
    "build_guard",
    "WARN_SECONDS",
    "HARD_SECONDS",
    "DEEP_SECONDS",
    "QUEUE_WAIT_SECONDS",
    "TICK_SECONDS",
    "LOOP_LAG_SECONDS",
    "DEFAULT_EVIDENCE_PATH",
]
