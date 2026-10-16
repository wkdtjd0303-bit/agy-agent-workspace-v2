#!/usr/bin/env python3
"""
auto_scheduler.py

채널(Discord Channel) 단위 **AI 자동 알람 / 스케줄 웨이크업** 엔진 — `!auto`

[요구 배경]
"마케팅 프로젝트라면 일정 시간마다 coder봇을 깨워주는 기능".
즉, 사용자가 매일 아침 수동으로 프롬프트를 치지 않아도, 정해진 스케줄에 맞춰
지정된 메시지가 해당 채널에 자동 투입되고 → 그 메시지가 마치 사용자가 방금
입력한 것처럼 Coder 파이프라인(`execute_channel_prompt`)으로 dispatch 된다.

[핵심 설계]
1. **채널별 완전 분리**: 채널마다 독립적인 on/off · 메시지 · 스케줄을 가진다.
   (`state/channel_auto_config.json`, 키 = 디스코드 채널 ID)
2. **기본값 = OFF**: 어떤 채널도 명시적으로 `!auto on` 하기 전까지 발동하지 않는다.
3. **SSOT 파일 저장**: 봇 재시작/핫리로드 후에도 설정이 유실되지 않는다.
4. **비차단 루프**: asyncio 태스크가 15초 간격으로 tick 하며, due(예약시간 도래)
   채널만 발동시킨다. 발동은 봇에 등록된 fire handler 로 위임한다(순환 import 방지).
5. **바쁜 채널 보호**: 해당 채널이 현재 작업 중(is_busy)이면 발동을 연기한다.
"""

import os
import re
import sys
import json
import time
import logging
import asyncio
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AutoScheduler")

# Root directory of agy_agent
BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
# [🧪 테스트 격리] 테스트가 운영 상태 파일을 덮어쓰지 못하도록 환경변수로 재지정 가능.
CONFIG_FILE = Path(
    os.getenv("AGY_CHANNEL_AUTO_CONFIG_FILE", str(STATE_DIR / "channel_auto_config.json"))
)
METRICS_HISTORY_FILE = Path(
    os.getenv("AGY_AUTO_METRICS_HISTORY_FILE", str(STATE_DIR / "auto_metrics_history.json"))
)


# 루프 tick 간격(초). 15초 면 분 단위 스케줄까지 무리 없이 커버한다.
TICK_SECONDS = 15
# 바쁜 채널 발동 연기 시 밀어낼 시간(초)
BUSY_DEFER_SECONDS = 60
# 간격 스케줄 허용 범위(분)
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 7 * 24 * 60  # 7일

DEFAULT_MESSAGE = (
    "⏰ 정기 자동 점검 시간입니다.\n"
    "현재 프로젝트의 진행 상태를 점검하고, 남은 작업/개선점을 정리한 뒤 "
    "가시성 높은 다음 단계 1가지를 제안해주세요."
)

# 요일 맵 (0=월요일 … 6=일요일)
WEEKDAY_ALIASES: Dict[str, int] = {
    "mon": 0, "monday": 0, "월": 0, "월요일": 0, "월욜": 0,
    "tue": 1, "tues": 1, "tuesday": 1, "화": 1, "화요일": 1,
    "wed": 2, "weds": 2, "wednesday": 2, "수": 2, "수요일": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "목": 3, "목요일": 3,
    "fri": 4, "friday": 4, "금": 4, "금요일": 4,
    "sat": 5, "saturday": 5, "토": 5, "토요일": 5,
    "sun": 6, "sunday": 6, "일": 6, "일요일": 6, "일욜": 6,
}
WEEKDAY_NAMES_KR = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


class ScheduleParseError(ValueError):
    """스케줄 문자열을 해석할 수 없을 때 발생하는 예외."""


def _now() -> float:
    return time.time()


def _default_config() -> Dict[str, Any]:
    """채널 자동 알람의 기본 설정 (항상 OFF)."""
    return {
        "enabled": False,
        "message": DEFAULT_MESSAGE,
        "schedule_type": "interval",      # interval | daily | weekly | weekdays
        "interval_minutes": 60,
        "daily_time": "09:00",
        "weekly_day": 0,                  # 0=월 … 6=일
        "weekly_time": "09:00",
        "next_run_ts": 0.0,
        "last_run_ts": 0.0,
        "run_count": 0,
        "missed_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }


# -----------------------------------------------------------------------------
# 스케줄 문자열 파서
# -----------------------------------------------------------------------------
_TIME_RE = re.compile(r"(\d{1,2})\s*[:시]\s*(\d{2})?\s*(am|pm|오전|오후)?", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"(?:(\d+)\s*(?:시간|hour|hours|hr|hrs|h))?\s*(?:(\d+)\s*(?:분|min|minute|minutes|m))?",
    re.IGNORECASE,
)


def parse_time_token(text: str) -> Optional[str]:
    """
    '09:00', '9시', '9:30', '9am', '9:30 pm', '21', '오후 6시' 등 → 'HH:MM' (24시간제).
    해석 불가 시 None.
    """
    if not text:
        return None
    t = text.strip()
    is_pm = bool(re.search(r"(pm|오후)", t, re.IGNORECASE))
    is_am = bool(re.search(r"(am|오전)", t, re.IGNORECASE))

    m = re.search(r"(\d{1,2})\s*[:시]\s*(\d{1,2})?", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
    else:
        m2 = re.search(r"(\d{1,2})", t)
        if not m2:
            return None
        hour = int(m2.group(1))
        minute = 0

    if is_pm and hour < 12:
        hour += 12
    if is_am and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_duration_minutes(text: str) -> Optional[int]:
    """'90m', '2h', '1시간 30분', '1h30m' → 총 분. 해석 불가 시 None."""
    if not text:
        return None
    total = 0
    matched = False
    for h, mnt in _DURATION_RE.findall(text):
        if h:
            total += int(h) * 60
            matched = True
        if mnt:
            total += int(mnt)
            matched = True
    return total if matched and total > 0 else None


def parse_schedule(spec: str) -> Tuple[Dict[str, Any], str]:
    """
    사람이 친한 스케줄 표현을 정규화된 설정 Dict + 한국어 설명으로 변환한다.

    지원 형식:
      • 간격  : 'every 30m', 'every 2h', 'every 1h30m', '30분', '2시간', '6h', '90m'
      • 매일  : 'daily 09:00', '매일 09:00', '매일', '09:00'
      • 매주  : 'weekly mon 09:00', '매주 월요일 09:00', '매주 금 18:30'
      • 평일  : 'weekdays 09:00', '평일 09:00'

    Returns: (schedule_fields_dict, human_readable_description)
    Raises : ScheduleParseError
    """
    s = (spec or "").strip().lower()
    if not s:
        raise ScheduleParseError("스케줄 값을 입력해주세요. 예: `every 30m`, `daily 09:00`, `weekly mon 09:00`")

    # ── 1. 간격(interval) 스케줄 ────────────────────────────────────────────
    if re.search(r"(every|간격|마다|주기)", s) or re.match(r"^\d+\s*(m|h|분|시간)", s):
        dur_part = re.sub(r"(every|간격|마다|주기)", " ", s)
        minutes = _parse_duration_minutes(dur_part)
        if minutes is None:
            raise ScheduleParseError(
                f"간격을 해석할 수 없습니다: `{spec}`\n예: `every 30m`, `every 2h`, `every 1h30m`"
            )
        if minutes < MIN_INTERVAL_MINUTES:
            minutes = MIN_INTERVAL_MINUTES
        if minutes > MAX_INTERVAL_MINUTES:
            raise ScheduleParseError(
                f"간격은 {MIN_INTERVAL_MINUTES}분 ~ {MAX_INTERVAL_MINUTES}분(7일) 사이여야 합니다: `{spec}`"
            )
        return (
            {"schedule_type": "interval", "interval_minutes": minutes},
            f"매 {minutes}분마다" if minutes < 60 else f"매 {minutes // 60}시간" + (f" {minutes % 60}분" if minutes % 60 else "") + "마다",
        )

    # ── 2. 매일(daily) ─────────────────────────────────────────────────────
    if re.search(r"(daily|매일|每天|everyday|every day)", s):
        hm = parse_time_token(re.sub(r"(daily|매일|每天|everyday|every day)", " ", s))
        return (
            {"schedule_type": "daily", "daily_time": hm or "09:00"},
            f"매일 {hm or '09:00'}",
        )

    # ── 3. 평일(weekdays: 월~금) ───────────────────────────────────────────
    if re.search(r"(weekday|weekdays|평일|주중)", s):
        hm = parse_time_token(re.sub(r"(weekday|weekdays|평일|주중)", " ", s))
        return (
            {"schedule_type": "weekdays", "daily_time": hm or "09:00"},
            f"평일(월~금) {hm or '09:00'}",
        )

    # ── 4. 매주(weekly) ────────────────────────────────────────────────────
    week_match = re.search(
        r"(weekly|매주|주마다)\s*([가-힣a-zA-Z]+)?\s*(.*)", s
    )
    if week_match:
        day_token = (week_match.group(2) or "").strip()
        rest = week_match.group(3) or ""
        day_idx = None
        if day_token:
            day_idx = WEEKDAY_ALIASES.get(day_token)
            if day_idx is None:
                # '월요일'처럼 붙어 들어온 경우 부분 일치 탐색
                for alias, idx in WEEKDAY_ALIASES.items():
                    if day_token.startswith(alias) or alias in day_token:
                        day_idx = idx
                        break
        if day_idx is None:
            # 요일 생략 → 시간 표현에서 요일을 찾아본다 (예: '매주 09:00 금')
            for token in re.findall(r"[가-힣a-zA-Z]+", s):
                if token in WEEKDAY_ALIASES:
                    day_idx = WEEKDAY_ALIASES[token]
                    break
        if day_idx is None:
            raise ScheduleParseError(
                f"요일을 찾을 수 없습니다: `{spec}`\n예: `weekly mon 09:00`, `매주 월요일 09:00`"
            )
        hm = parse_time_token(rest) or parse_time_token(s) or "09:00"
        return (
            {"schedule_type": "weekly", "weekly_day": day_idx, "weekly_time": hm},
            f"매주 {WEEKDAY_NAMES_KR[day_idx]} {hm}",
        )

    # ── 5. 시간만 단독 입력 ('09:00') → 매일 ───────────────────────────────
    hm = parse_time_token(s)
    if hm:
        return (
            {"schedule_type": "daily", "daily_time": hm},
            f"매일 {hm}",
        )

    raise ScheduleParseError(
        f"스케줄을 해석할 수 없습니다: `{spec}`\n"
        "지원 형식: `every 30m` · `every 2h` · `daily 09:00` · `weekly mon 09:00` · `평일 09:00`"
    )


def humanize_schedule(cfg: Dict[str, Any]) -> str:
    """저장된 설정 Dict → 한국어 스케줄 설명."""
    stype = cfg.get("schedule_type", "interval")
    if stype == "interval":
        minutes = int(cfg.get("interval_minutes", 60) or 60)
        if minutes < 60:
            return f"매 {minutes}분마다"
        h, m = divmod(minutes, 60)
        return f"매 {h}시간" + (f" {m}분" if m else "") + "마다"
    if stype == "weekdays":
        return f"평일(월~금) {cfg.get('daily_time', '09:00')}"
    if stype == "weekly":
        day_idx = int(cfg.get("weekly_day", 0) or 0)
        day_name = WEEKDAY_NAMES_KR[day_idx] if 0 <= day_idx <= 6 else "월요일"
        return f"매주 {day_name} {cfg.get('weekly_time', '09:00')}"
    return f"매일 {cfg.get('daily_time', '09:00')}"


def _parse_hhmm(value: str) -> Tuple[int, int]:
    try:
        h, m = str(value).split(":")
        return int(h), int(m)
    except Exception:
        return 9, 0


def compute_next_run(cfg: Dict[str, Any], now: Optional[float] = None) -> float:
    """
    설정 기준 '다음 발동 예정 시각(unix ts)'을 계산한다.
    (과거 시각은 절대 반환하지 않는다 — 최소 now + 1초.)
    """
    now = _now() if now is None else now
    local = datetime.datetime.fromtimestamp(now)
    stype = cfg.get("schedule_type", "interval")

    if stype == "interval":
        minutes = int(cfg.get("interval_minutes", 60) or 60)
        return now + max(1, minutes) * 60

    if stype == "weekdays":
        h, m = _parse_hhmm(cfg.get("daily_time", "09:00"))
        for offset in range(0, 8):
            cand = (local + datetime.timedelta(days=offset)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
            if cand.weekday() <= 4 and cand.timestamp() > now:
                return cand.timestamp()
        return now + 24 * 3600

    if stype == "weekly":
        day_idx = int(cfg.get("weekly_day", 0) or 0)
        h, m = _parse_hhmm(cfg.get("weekly_time", "09:00"))
        for offset in range(0, 8):
            cand = (local + datetime.timedelta(days=offset)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
            if cand.weekday() == day_idx and cand.timestamp() > now:
                return cand.timestamp()
        return now + 24 * 3600

    # daily (기본)
    h, m = _parse_hhmm(cfg.get("daily_time", "09:00"))
    cand = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand.timestamp() <= now:
        cand = cand + datetime.timedelta(days=1)
    return cand.timestamp()


def _format_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# [🔁 Hot-Reload Safety] importlib.reload() 로 모듈이 갈려도 설정/루프 상태는 유지된다.
#   (reload 후 새 클래스가 빈 상태로 시작하면 발동 루프가 중복 기동되거나 예약이 유실된다)
_existing_as_mod = sys.modules.get("auto_scheduler")
_existing_as_cls = getattr(_existing_as_mod, "ChannelAutoScheduler", None) if _existing_as_mod else None


class ChannelAutoScheduler:
    """
    채널별 자동 알람 설정 저장소 + 발동 루프.

    발동 자체는 봇(bot.py)이 등록한 fire handler 가 수행하며,
    이 클래스는 '언제 누구를 깨울지'만 결정하는 순수 엔진 역할을 한다.
    """

    _configs: Dict[str, Dict[str, Any]] = getattr(_existing_as_cls, "_configs", {}) if _existing_as_cls else {}
    _loaded: bool = getattr(_existing_as_cls, "_loaded", False) if _existing_as_cls else False
    _fire_handler: Any = getattr(_existing_as_cls, "_fire_handler", None) if _existing_as_cls else None
    _loop_task: Optional[asyncio.Task] = getattr(_existing_as_cls, "_loop_task", None) if _existing_as_cls else None
    _running: bool = getattr(_existing_as_cls, "_running", False) if _existing_as_cls else False

    # ------------------------------------------------------------------
    # 저장 / 로드
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, force: bool = False) -> Dict[str, Dict[str, Any]]:
        if cls._loaded and not force:
            return cls._configs
        cls._configs = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for cid, cfg in data.items():
                        if isinstance(cfg, dict):
                            merged = _default_config()
                            merged.update(cfg)
                            cls._configs[str(cid)] = merged
            except Exception as e:
                logger.error(f"[AutoScheduler] Failed to load {CONFIG_FILE}: {e}")
        cls._loaded = True
        return cls._configs

    @classmethod
    def save(cls) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cls._configs, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_FILE)
        except Exception as e:
            logger.error(f"[AutoScheduler] Failed to save {CONFIG_FILE}: {e}")

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------
    @classmethod
    def get_config(cls, channel_id: int) -> Dict[str, Any]:
        cls.load()
        cfg = cls._configs.get(str(channel_id))
        if cfg is None:
            return _default_config()
        merged = _default_config()
        merged.update(cfg)
        return merged

    @classmethod
    def list_configs(cls) -> Dict[int, Dict[str, Any]]:
        """모든 채널 설정을 {채널ID(int): 설정} 형태로 반환한다."""
        cls.load()
        out: Dict[int, Dict[str, Any]] = {}
        for cid in list(cls._configs.keys()):
            try:
                out[int(cid)] = cls.get_config(int(cid))
            except Exception:
                continue
        return out

    @classmethod
    def is_enabled(cls, channel_id: int) -> bool:
        return bool(cls.get_config(channel_id).get("enabled", False))

    # ------------------------------------------------------------------
    # 변경
    # ------------------------------------------------------------------
    @classmethod
    def _touch(cls, channel_id: int) -> Dict[str, Any]:
        cls.load()
        key = str(channel_id)
        if key not in cls._configs:
            cls._configs[key] = _default_config()
        return cls._configs[key]

    @classmethod
    def set_enabled(cls, channel_id: int, enabled: bool) -> Dict[str, Any]:
        cfg = cls._touch(channel_id)
        cfg["enabled"] = bool(enabled)
        cfg["updated_at"] = _now()
        if enabled:
            # 활성화 시점에 다음 발동 시각을 (재)계산한다.
            cfg["next_run_ts"] = compute_next_run(cfg)
        else:
            cfg["next_run_ts"] = 0.0
        cls.save()
        return cls.get_config(channel_id)

    @classmethod
    def set_message(cls, channel_id: int, message: str) -> Dict[str, Any]:
        msg = (message or "").strip()
        if not msg:
            raise ScheduleParseError("알람 메시지를 입력해주세요.")
        cfg = cls._touch(channel_id)
        cfg["message"] = msg[:1500]
        cfg["updated_at"] = _now()
        cls.save()
        return cls.get_config(channel_id)

    @classmethod
    def set_schedule(cls, channel_id: int, spec: str) -> Tuple[Dict[str, Any], str]:
        fields, desc = parse_schedule(spec)
        cfg = cls._touch(channel_id)
        cfg.update(fields)
        cfg["updated_at"] = _now()
        # 이미 활성화된 상태라면 다음 발동 시각을 즉시 재계산한다.
        if cfg.get("enabled"):
            cfg["next_run_ts"] = compute_next_run(cfg)
        cls.save()
        return cls.get_config(channel_id), desc

    @classmethod
    def reset_channel(cls, channel_id: int) -> None:
        cls.load()
        cls._configs.pop(str(channel_id), None)
        cls.save()

    # ------------------------------------------------------------------
    # 발동 판정 / 상태 전이
    # ------------------------------------------------------------------
    @classmethod
    def due_channels(cls, now: Optional[float] = None) -> List[Tuple[int, Dict[str, Any]]]:
        """발동 시각이 도달한(enabled) 채널 목록을 반환한다."""
        now = _now() if now is None else now
        due: List[Tuple[int, Dict[str, Any]]] = []
        for cid, cfg in cls.list_configs().items():
            if not cfg.get("enabled"):
                continue
            nxt = float(cfg.get("next_run_ts") or 0.0)
            if nxt and nxt <= now:
                due.append((cid, cfg))
        return due

    @classmethod
    def mark_run(cls, channel_id: int, now: Optional[float] = None) -> Dict[str, Any]:
        """발동 완료 처리: last_run/run_count 갱신 + 다음 발동 시각 예약."""
        now = _now() if now is None else now
        cfg = cls._touch(channel_id)
        cfg["last_run_ts"] = now
        cfg["run_count"] = int(cfg.get("run_count", 0) or 0) + 1
        cfg["next_run_ts"] = compute_next_run(cfg, now=now + 1)
        cfg["updated_at"] = now
        cls.save()
        return cls.get_config(channel_id)

    @classmethod
    def defer_run(cls, channel_id: int, seconds: int = BUSY_DEFER_SECONDS) -> Dict[str, Any]:
        """바쁜 채널 등으로 발동을 연기한다 (지연 횟수 누적)."""
        cfg = cls._touch(channel_id)
        cfg["missed_count"] = int(cfg.get("missed_count", 0) or 0) + 1
        cfg["next_run_ts"] = _now() + seconds
        cfg["updated_at"] = _now()
        cls.save()
        return cls.get_config(channel_id)

    # ------------------------------------------------------------------
    # 발동 핸들러 / 루프
    # ------------------------------------------------------------------
    @classmethod
    def set_fire_handler(cls, handler) -> None:
        """async handler(channel_id:int, message:str, cfg:dict) -> bool 등록."""
        cls._fire_handler = handler

    @classmethod
    async def start(cls) -> bool:
        """백그라운드 발동 루프를 시작한다(이미 실행 중이면 무시)."""
        if cls._running and cls._loop_task and not cls._loop_task.done():
            return False
        cls._running = True
        cls._loop_task = asyncio.create_task(cls._loop())
        logger.info("[AutoScheduler] Channel auto-wakeup scheduler started.")
        return True

    @classmethod
    async def stop(cls) -> None:
        cls._running = False
        if cls._loop_task and not cls._loop_task.done():
            cls._loop_task.cancel()
            try:
                await cls._loop_task
            except asyncio.CancelledError:
                pass
        cls._loop_task = None

    @classmethod
    async def _loop(cls) -> None:
        while cls._running:
            try:
                await asyncio.sleep(TICK_SECONDS)
                await cls.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[AutoScheduler] tick error: {e}")
                await asyncio.sleep(5)

    @classmethod
    async def tick(cls) -> List[int]:
        """한 주기: 도달한 채널을 발동시키고 발동된 채널 ID 목록을 반환한다."""
        fired: List[int] = []
        for cid, cfg in cls.due_channels():
            handler = cls._fire_handler
            if handler is None:
                logger.debug(f"[AutoScheduler] No fire handler registered; skip channel {cid}.")
                cls.defer_run(cid)
                continue
            try:
                ok = await handler(cid, cfg.get("message", DEFAULT_MESSAGE), cfg)
            except Exception as e:
                logger.warning(f"[AutoScheduler] fire handler failed for channel {cid}: {e}")
                ok = False
            if ok:
                cls.mark_run(cid)
                fired.append(cid)
            else:
                cls.defer_run(cid)
        return fired

    @classmethod
    async def trigger_now(cls, channel_id: int) -> Dict[str, Any]:
        """수동 즉시 발동(`!auto now`). fire handler 의 결과를 그대로 돌려준다."""
        cfg = cls.get_config(channel_id)
        handler = cls._fire_handler
        if handler is None:
            return {"fired": False, "reason": "발동 핸들러 미등록"}
        ok = await handler(channel_id, cfg.get("message", DEFAULT_MESSAGE), cfg)
        return {"fired": bool(ok), "cfg": cfg}

    # ------------------------------------------------------------------
    # 표시용 요약
    # ------------------------------------------------------------------
    @classmethod
    def summarize(cls, channel_id: int) -> Dict[str, Any]:
        cfg = cls.get_config(channel_id)
        enabled = bool(cfg.get("enabled"))
        return {
            "enabled": enabled,
            "message": cfg.get("message", DEFAULT_MESSAGE),
            "schedule_desc": humanize_schedule(cfg),
            "schedule_type": cfg.get("schedule_type", "interval"),
            "next_run_ts": float(cfg.get("next_run_ts") or 0.0),
            "next_run_text": _format_ts(cfg.get("next_run_ts")) if enabled else "-",
            "last_run_text": _format_ts(cfg.get("last_run_ts")),
            "run_count": int(cfg.get("run_count", 0) or 0),
            "missed_count": int(cfg.get("missed_count", 0) or 0),
        }


# =============================================================================
# 📊 [!auto] 데이터 기반 PDCA (Plan-Do-Check-Act) 지표 이력 저장소
# =============================================================================

class AutoMetricsHistory:
    """
    정기 마케팅/투자/운영 자동화를 위해 회차별 목표 지표, 실행 액션, 관측 결과,
    가설 수정 사항을 영속 기록하고(state/auto_metrics_history.json),
    다음 스케줄 실행 시 에이전트에게 직전 데이터를 주입하는 PDCA 관리자.
    """
    _cache: Optional[Dict[str, Any]] = None

    @classmethod
    def _ensure_loaded(cls) -> Dict[str, Any]:
        if cls._cache is not None:
            return cls._cache
        if METRICS_HISTORY_FILE.is_file():
            try:
                with open(METRICS_HISTORY_FILE, "r", encoding="utf-8") as f:
                    cls._cache = json.load(f)
            except Exception as e:
                logger.warning(f"[AutoMetricsHistory] Failed to read {METRICS_HISTORY_FILE}: {e}")
                cls._cache = {}
        else:
            cls._cache = {}
        return cls._cache

    @classmethod
    def _save(cls) -> None:
        if cls._cache is None:
            return
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = METRICS_HISTORY_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cls._cache, f, ensure_ascii=False, indent=2)
            tmp.replace(METRICS_HISTORY_FILE)
        except Exception as e:
            logger.error(f"[AutoMetricsHistory] Failed to save {METRICS_HISTORY_FILE}: {e}")

    @classmethod
    def record_cycle(
        cls,
        channel_id: int,
        target_metrics: Optional[Dict[str, Any]] = None,
        action_taken: str = "",
        observed_result: str = "",
        hypothesis_delta: str = "",
    ) -> Dict[str, Any]:
        """한 주기의 PDCA 실행 내역을 기록한다."""
        data = cls._ensure_loaded()
        cid = str(channel_id)
        if cid not in data:
            data[cid] = []

        now_ts = _now()
        date_str = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
        cycle_num = len(data[cid]) + 1

        entry = {
            "cycle": cycle_num,
            "timestamp": now_ts,
            "date_str": date_str,
            "target_metrics": target_metrics or {},
            "action_taken": action_taken or "정기 점검 및 개선 작업",
            "observed_result": observed_result,
            "hypothesis_delta": hypothesis_delta,
        }
        data[cid].append(entry)
        cls._save()
        return entry

    @classmethod
    def get_history(cls, channel_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """채널의 최근 N회차 기록을 반환한다."""
        data = cls._ensure_loaded()
        records = data.get(str(channel_id), [])
        return records[-limit:] if limit else records

    @classmethod
    def get_latest(cls, channel_id: int) -> Optional[Dict[str, Any]]:
        """채널의 직전 회차 기록을 반환한다."""
        hist = cls.get_history(channel_id, limit=1)
        return hist[-1] if hist else None

    @classmethod
    def format_pdca_context(cls, channel_id: int) -> str:
        """
        차기 스케줄 실행 시 에이전트 프롬프트에 주입할 데이터 기반 PDCA 컨텍스트를 생성한다.
        """
        latest = cls.get_latest(channel_id)
        if not latest:
            return (
                "\n\n[📊 !auto 데이터 기반 PDCA 그로스 루프 (최초 실행)]\n"
                "• 직전 회차 데이터: 없음 (현재 회차가 최초 기준선 수립 주기입니다)\n"
                "• 지침: 사용자가 지정한 정량 목표 지표(조회수, 전환율, 클릭률, 수익률 등)의 현재 상태를 "
                "확인하고, 기준선(Baseline)을 수립한 뒤 첫 번째 개선 가설을 실행하십시오."
            )

        metrics_str = ", ".join(f"{k}: {v}" for k, v in latest.get("target_metrics", {}).items()) or "정기 자동화 지표"
        return (
            f"\n\n[📊 !auto 데이터 기반 PDCA 그로스 루프 (직전 {latest.get('cycle', 1)}회차 대조)]\n"
            f"• 직전 실행 일시: `{latest.get('date_str', 'n/a')}`\n"
            f"• 이전 목표/관측 지표: {metrics_str}\n"
            f"• 이전 적용 조치: {latest.get('action_taken', '기본 자동화')}\n"
            f"• 이전 관측 결과: {latest.get('observed_result', '지표 추적 중')}\n"
            f"• 이전 가설/원인 분석: {latest.get('hypothesis_delta', '데이터 축적 중')}\n\n"
            "**[PDCA 4단계 실행 프레임워크]**:\n"
            "1. **[지표 비교 (Metrics Delta)]**: 직전 주기 지표와 현재 상태를 대조하십시오.\n"
            "2. **[병목 진단 (Bottleneck)]**: 지표가 기대에 못 미친 핵심 원인을 1~2가지로 좁히십시오.\n"
            "3. **[이번 주기 실험 (Experiment & Action)]**: 원인을 해결하기 위해 이번 주기에 바꿀 단 하나의 구체적 변수를 결정하고 실행하십시오.\n"
            "4. **[다음 주기 검증 기준 요약]**: 다음 스케줄에서 어떤 수치가 얼마나 변하면 성공으로 볼지 기록하십시오."
        )

