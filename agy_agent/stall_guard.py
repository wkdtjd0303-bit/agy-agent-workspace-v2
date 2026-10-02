#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[StallGuard] 채널 오케스트레이션 교착(정지) 감시 & 자동 해제 가드
==================================================================

## 왜 필요한가 (실제 장애 시나리오)
사용자가 요청을 보내면 봇은 `session.is_busy = True` 로 채널을 점유한 뒤
`execute_channel_prompt` 태스크를 수행한다. 기존 방어 장치는 두 개였지만
둘 다 **오케스트레이션(큐/락/busy) 층은 건드리지 못했다.**

1. `agent_runner` 프로세스 와치독: 자식 CLI 프로세스만 강제 회수
   → 회수 후에도 `session.is_busy` 는 `True` 로 남아 대기열 영구 점유.
2. `dispatch_next_in_queue` 좀비 busy 회수: `current_task.done()` 일 때만 복구
   → **살아는 있고 멈춰 있는(hung) 태스크는 영원히 통과 불가.**
3. `_channel_queue_locks` 는 `async lock.acquire()` 에 **타임아웃이 없음**
   → 락 점유자가 멈추면 해당 채널의 모든 후속 디스패치가 무기한 대기.

그 결과 "요청 → 답장 없음 / 상태 타이머 고정(예: 3506초) / 80분 무응답" 이 발생한다.

## StallGuard 는 무엇을 하나
같은 이벤트 루프 위에서 **독립 주기(10초)** 로 전 채널 세션을 스캔하여:

* **(C) 대기열 정체**: 작업이 비어 있는데 큐의 요청이 `QUEUE_WAIT` 초 이상 방치
  → 디스패치 재호출(자체 교착 해소) + 증거 기록.
* **(A) 좀비 busy**: `is_busy=True` 이나 `current_task` 가 없거나 이미 종료
  → 즉시 회수.
* **(W) 무응답 경보**: `last_progress_at` 기준 실제 진행이 `WARN` 초 이상 끊김
  → 채널에 1회 경보 발송 (`_stall_warned` 로 중복 발송 방지).
* **(D) 심층 작업 관찰**: 진행이 `HARD` 초 이상 끊겼지만 **오케스트레이션 태스크가
  살아있고 추적 프로세스가 고아가 아니면** → 즉시 죽이지 않고 `DEEP` 초까지 관찰.
  (긴 추론/장시간 도구 실행처럼 stdout 이 조용한 '정상 작업'을 오판하지 않는다.)
* **(R) 무응답 강제 회수**: 진행이 회수 임계값 이상 끊김
  — 살아있거나 추적 불가한 작업은 `DEEP`(절대 상한), 추적 프로세스가 이미
  죽은 경우는 `HARD`(유예 취소) 에서 회수
  → ① 프로세스 트리 강제kill ② 오케스트레이션 태스크 cancel
  ③ `is_busy` 해제 ④ **채널 큐 락 강제 교체** ⑤ 채널 알림 ⑥ 대기열 재가동.

**핵심**: 하트비트(`last_progress_at`) 는 *진짜 진행* (CLI stdout 출력,
상태 전이) 에서만 갱신된다. 디스코드 8초 주기 "진행 중..." 임베드 같은
**가짜 하트비트는 갱신하지 않는다.**

**오판 방지 (Liveness-aware)**: stdout 이 15분간 조용하다고 해서 곧바로 멈춘 것으로
단정하지 않는다. Thinking 모델(Claude Opus / DeepSeek R1 등)은 추론 중 아무것도
출력하지 않으므로, `HARD`(기본 4000초)에서는 (D) 관찰 단계로 올리고, 태스크가
살아있으면 `DEEP`(기본 5000초)에서만 강제 회수한다. 태스크 자체가 죽었으면(좀비) 30초에
즉시 회수하고, 추적 프로세스가 이미 죽었으면 DEEP 유예 없이 `HARD`(기본 4000초)에서 회수한다.
(경보/관찰/회수 임계값은 장시간 QA·Thinking 추론을 방해하지 않도록 넉넉하게 두었다.)

## 물리적 증거 (Evidence)
모든 감지/조치는 `reports/watchdog/stalls.json` 에 레코드로 남는다.
(AGENT 는 "안 멈춘다" 라는 주장을 말로 하지 않고 이 파일로 증명한다.)

## 환경변수
* `STALL_GUARD_WARN_SECONDS`   무응답 경보 기준 (기본 3000초)
* `STALL_GUARD_HARD_SECONDS`   심층 작업 관찰 기준 (기본 4000초) — 살아있는 작업은 즉시 회수하지 않음
* `STALL_GUARD_DEEP_SECONDS`   살아있는 작업의 절대 상한·강제 회수 기준 (기본 5000초)
* `STALL_GUARD_QUEUE_WAIT_SECONDS` 대기열 정체 기준 (기본 60초)
* `STALL_GUARD_TICK_SECONDS`   스캔 주기 (기본 10초)
* `STALL_GUARD_ZOMBIE_SECONDS` 좀비 busy 판정 기준 (기본 30초)
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
# [🛡️ 오판 방지] 살아있는 작업의 '절대 상한'을 기본 5000초로 둔다.
# QA(E2E 176+ 항목)·Thinking 모델의 장시간 추론은 stdout 이 조용하므로, HARD 에서
# 곧바로 죽이지 않고 이 DEEP 상한까지 관찰한 뒤에만 회수한다.
# (경보는 기본 3000초, 관찰은 4000초 — 너무 타이트하게 채널을 회수하지 않도록 완화.)
DEEP_SECONDS = _env_float("STALL_GUARD_DEEP_SECONDS", max(HARD_SECONDS, 5000.0))
QUEUE_WAIT_SECONDS = _env_float("STALL_GUARD_QUEUE_WAIT_SECONDS", 60.0)
TICK_SECONDS = _env_float("STALL_GUARD_TICK_SECONDS", 10.0)
ZOMBIE_SECONDS = _env_float("STALL_GUARD_ZOMBIE_SECONDS", 30.0)
LOOP_LAG_SECONDS = _env_float("STALL_GUARD_LOOP_LAG_SECONDS", 3.0)

MAX_RECORDS_IN_FILE = 200


def _kill_process_tree(pid: int) -> None:
    """자식 프로세스 트리를 강제 회수 (session_manager 헬퍼 재사용, 실패 시 수동 폴백)."""
    try:
        from session_manager import kill_process_tree  # 지연 import (순환 의존 방지)
        kill_process_tree(pid)
        return
    except Exception:
        pass
    try:
        os.killpg(os.getpgid(pid), 9)
    except Exception:
        pass
    try:
        os.kill(pid, 9)
    except Exception:
        pass


class StallGuard:
    """채널 세션의 무응답/대기열 교착을 감지하고 자동으로 해제한다."""

    def __init__(
        self,
        session_manager: Any,
        *,
        send_fn: Optional[Callable[..., Any]] = None,
        dispatch_fn: Optional[Callable[[int], Any]] = None,
        lock_registry: Optional[Dict[int, Any]] = None,
        warn_seconds: float = WARN_SECONDS,
        hard_seconds: float = HARD_SECONDS,
        deep_seconds: float = DEEP_SECONDS,
        queue_wait_seconds: float = QUEUE_WAIT_SECONDS,
        zombie_seconds: float = ZOMBIE_SECONDS,
        tick_seconds: float = TICK_SECONDS,
        evidence_path: str = DEFAULT_EVIDENCE_PATH,
        clock: Callable[[], float] = time.time,
        notify_fn: Optional[Callable[[int, str], Any]] = None,
    ):
        # 기본값 보정: 경보 < 심층관찰 < 강제회수 순서를 항상 유지한다.
        self.warn_seconds = max(1.0, float(warn_seconds))
        self.hard_seconds = max(self.warn_seconds + 1.0, float(hard_seconds))
        # DEEP 은 HARD 보다 반드시 커야 한다. (HARD 에서 즉시 죽이지 않기 위함)
        self.deep_seconds = max(self.hard_seconds + 1.0, float(deep_seconds))
        self.queue_wait_seconds = max(1.0, float(queue_wait_seconds))
        self.zombie_seconds = max(1.0, float(zombie_seconds))
        self.tick_seconds = max(0.5, float(tick_seconds))

        self.session_manager = session_manager
        self.dispatch_fn = dispatch_fn
        self.lock_registry = lock_registry
        self.evidence_path = evidence_path
        self.clock = clock
        self.records: List[Dict[str, Any]] = []

        # send_fn (async, 디스코드 전송) 은 선택 사항. 있으면 그걸 쓰고 없으면 notify_fn 을 쓴다.
        self._send_fn = send_fn
        self._notify_fn = notify_fn

        self._stall_warned: Dict[int, float] = {}
        self._deep_notified: Dict[int, float] = {}
        self._last_queue_nudge: Dict[int, float] = {}
        self._recovered_channels: set = set()

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
                    "zombie_seconds": self.zombie_seconds,
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
        """전 세션을 1회 스캔한다. 감지된 이벤트(경보/회수/큐니지) 목록을 반환."""
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
            # 진행 없음 상태로 돌아왔으면 경보/심층관찰 플래그를 초기화한다.
            self._stall_warned.pop(channel_id, None)
            self._deep_notified.pop(channel_id, None)
            return None

        # ---------------- (A) 좀비 busy: busy 인데 태스크가 없거나 이미 종료 ----------------
        task = getattr(session, "current_task", None)
        task_alive = bool(task is not None and not getattr(task, "done", lambda: True)())
        if not task_alive and silence >= self.zombie_seconds:
            detail = (
                f"busy 상태이나 오케스트레이션 태스크가 존재하지 않음 "
                f"(무응답 {int(silence)}초) → 즉시 회수"
            )
            logger.error(f"[StallGuard] 채널 {channel_id}: {detail}")
            self._recover(session, channel_id, silence, busy_seconds, detail, action="zombie_recover")
            return self._event(session, "zombie_recover", silence, busy_seconds, detail)

        # ---------------- 추적 중인 프로세스의 생존 여부 ----------------
        # 추적 프로세스가 이미 종료(returncode 세팅)됐다면 심층 관찰 유예를 주지 않는다.
        # (단, stale 참조로 인한 조기 회수를 막기 위해 '유예 취소'로만 사용하고
        #  별도의 조기 강제 회수 트리거로는 쓰지 않는다 → 기존 동작보다 느슨해지지 않음.)
        proc = getattr(session, "current_process", None)
        process_tracked = proc is not None
        process_returncode = getattr(proc, "returncode", None) if process_tracked else None
        process_dead = process_tracked and process_returncode is not None
        # 살아있는(또는 추적 불가한) 작업이면 DEEP 까지 유예, 죽은 프로세스면 HARD 에서 회수.
        recover_threshold = self.hard_seconds if process_dead else self.deep_seconds

        # ---------------- 진행이 살아있으면 경보/심층관찰 잠금 해제 ----------------
        # (실제 하트비트가 잠금 시점보다 뒤면 진행이 재개된 것 → 다음 정지에 다시 경보)
        warned_at = self._stall_warned.get(channel_id)
        if warned_at is not None and progress_at > warned_at:
            self._stall_warned.pop(channel_id, None)
        deep_at = self._deep_notified.get(channel_id)
        if deep_at is not None and progress_at > deep_at:
            self._deep_notified.pop(channel_id, None)
        if silence < self.warn_seconds:
            self._stall_warned.pop(channel_id, None)
            self._deep_notified.pop(channel_id, None)
            return None

        # ---------------- (R) 무응답 강제 회수 (회수 임계값 도달) ----------------
        if silence >= recover_threshold:
            basis = (
                "추적 프로세스 종료(고아 의심)"
                if process_dead
                else f"절대 상한 {int(self.deep_seconds)}초"
            )
            detail = (
                f"실제 진행이 {int(silence)}초간 중단 "
                f"({basis}, 경과 {int(busy_seconds)}초) → 강제 회수"
            )
            logger.error(f"[StallGuard] 채널 {channel_id}: {detail}")
            self._recover(session, channel_id, silence, busy_seconds, detail, action="stall_recover")
            return self._event(session, "stall_recover", silence, busy_seconds, detail)

        # ---------------- (D) 심층 작업 관찰: HARD 초과했지만 태스크가 살아있음 ----------------
        # stdout 이 조용해도 태스크가 살아있으면 Thinking 모델의 긴 추론/장시간 도구일 수
        # 있으므로 즉시 죽이지 않는다. DEEP(절대 상한)까지만 관찰하고 회수한다.
        if silence >= self.hard_seconds and not self._deep_notified.get(channel_id):
            self._deep_notified[channel_id] = progress_at  # 관찰 시점 하트비트로 잠금
            # 심층 관찰 알림이 경보를 대체하므로 별도 경보 중복 발송을 막는다.
            self._stall_warned[channel_id] = progress_at
            detail = (
                f"진행이 {int(silence)}초간 끊겼으나 태스크가 살아있음 → 즉시 회수 보류. "
                f"절대 상한 {int(self.deep_seconds)}초까지 관찰."
            )
            logger.warning(f"[StallGuard] 채널 {channel_id}: {detail}")
            self._notify(
                channel_id,
                (
                    "🟠 **[StallGuard 심층 작업 관찰]**\n"
                    f"⏱️ stdout 진행이 **{int(silence)}초** 동안 끊겼습니다 (관찰 기준 {int(self.hard_seconds)}초).\n"
                    f"📌 현재 상태: `{getattr(session, 'status_desc', '')}`\n"
                    "🧠 태스크가 살아있어 자동 회수를 **보류**합니다 (긴 추론/장시간 도구 가능성).\n"
                    f"🛡️ 절대 상한 **{int(self.deep_seconds)}초**까지 회복되지 않으면 강제 회수합니다."
                ),
            )
            return self._event(session, "deep_work_notice", silence, busy_seconds, detail)

        # ---------------- (W) 무응답 경보 (채널 1회) ----------------
        if silence >= self.warn_seconds and not self._stall_warned.get(channel_id):
            self._stall_warned[channel_id] = progress_at  # 경보 시점의 하트비트를 기준으로 잠금
            detail = (
                f"무응답 {int(silence)}초 경과 (기준 {int(self.warn_seconds)}초) → 경보 발송. "
                f"{int(self.deep_seconds)}초까지 회복되지 않으면 자동 회수."
            )
            logger.warning(f"[StallGuard] 채널 {channel_id}: {detail}")
            self._notify(
                channel_id,
                (
                    "🟡 **[StallGuard 무응답 경보]**\n"
                    f"⏱️ 실제 진행이 **{int(silence)}초** 동안 끊겼습니다 (진행 기준 {int(self.warn_seconds)}초).\n"
                    f"📌 현재 상태: `{getattr(session, 'status_desc', '')}`\n"
                    f"🛡️ 절대 상한 {int(self.deep_seconds)}초까지 회복되지 않으면 "
                    "**작업 강제 회수 + 채널 대기열 자동 재가동**을 수행합니다."
                ),
            )
            return self._event(session, "stall_warn", silence, busy_seconds, detail)

        return None

    # ------------------------------------------------------------------
    # 자동 회수
    # ------------------------------------------------------------------
    def _recover(
        self,
        session: Any,
        channel_id: Optional[int],
        silence: float,
        busy_seconds: float,
        detail: str,
        action: str = "stall_recover",
    ) -> None:
        """프로세스/태스크/락/busy 를 한 번에 해제하고 채널을 살려낸다."""
        # 1) 자식 프로세스 트리 강제 회수
        proc = getattr(session, "current_process", None)
        pid = getattr(proc, "pid", None)
        if pid:
            try:
                _kill_process_tree(int(pid))
            except Exception as err:
                logger.debug(f"StallGuard: 프로세스 회수 실패(무시): {err}")
            try:
                proc.kill()
            except Exception:
                pass
        # 죽었거나 고아가 된 프로세스 참조는 항상 정리한다. (pid 가 없어도 잔존 참조 제거)
        if proc is not None:
            try:
                session.current_process = None
            except Exception:
                pass

        # 2) 오케스트레이션 태스크 취소 (finally 절이 busy 해제/대기열 재가동을 수행)
        task = getattr(session, "current_task", None)
        if task is not None and not getattr(task, "done", lambda: True)():
            try:
                task.cancel()
            except Exception as err:
                logger.debug(f"StallGuard: 태스크 취소 실패: {err}")

        # 3) busy 상태 강제 해제 (태스크가 사라진 좀비 busy 까지 커버)
        try:
            session.set_busy(False, "🟡 StallGuard 자동 회수됨 (무응답 복구)")
        except Exception:
            session.is_busy = False
        try:
            session.set_status("error", f"⚠️ {int(silence)}초 무응답 → StallGuard 자동 회수")
        except Exception:
            pass

        # 4) 채널 대기열 락 강제 교체 (점유자가 멈춰 락이 영구 보관된 경우 해소)
        if self.lock_registry is not None and channel_id is not None:
            try:
                self.lock_registry[channel_id] = asyncio.Lock()
                logger.warning(f"[StallGuard] 채널 {channel_id} 대기열 락 강제 교체 완료")
            except Exception as err:
                logger.debug(f"StallGuard: 락 교체 실패: {err}")

        # 5) 채널 알림
        self._notify(
            channel_id,
            (
                "⛔ **[StallGuard 자동 회수 완료]**\n"
                f"⏱️ 무응답 **{int(silence)}초** / 작업 경과 **{int(busy_seconds)}초**\n"
                f"🔧 조치: 프로세스 트리 강제 회수 → 태스크 취소 → busy 해제 → 대기열 락 교체\n"
                "▶️ 대기 중인 요청이 있으면 즉시 재가동합니다. (동일 요청 재전송도 가능)"
            ),
        )

        # 6) 대기열 재가동
        self._schedule_dispatch(channel_id)
        if channel_id is not None:
            self._recovered_channels.add(channel_id)
            self._stall_warned.pop(channel_id, None)
            self._deep_notified.pop(channel_id, None)

    # ------------------------------------------------------------------
    # 감시 루프 (이벤트 루프 블로킹까지 감지)
    # ------------------------------------------------------------------
    async def run(self) -> None:
        logger.info(
            f"[StallGuard] 가동: 경보 {int(self.warn_seconds)}s / "
            f"심층관찰 {int(self.hard_seconds)}s / 강제회수 {int(self.deep_seconds)}s / "
            f"대기열 {int(self.queue_wait_seconds)}s / 주기 {self.tick_seconds}s"
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
    lock_registry: Optional[Dict[int, Any]] = None,
    **kwargs: Any,
) -> StallGuard:
    """bot.py 에서 쓰는 팩토리 (환경변수 기반 기본값 적용)."""
    return StallGuard(
        session_manager,
        send_fn=send_fn,
        dispatch_fn=dispatch_fn,
        lock_registry=lock_registry,
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
    "ZOMBIE_SECONDS",
    "LOOP_LAG_SECONDS",
    "DEFAULT_EVIDENCE_PATH",
]
