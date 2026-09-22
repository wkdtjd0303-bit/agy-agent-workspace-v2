#!/usr/bin/env python3
"""
🛡️ AutonomySafetyLayer — Bounded Autonomy & Multi-Dimensional Orchestration Guard (AGY 4.x)

Ensures that "Infinite Evolution (!auto on evolve)" never runs away into unconstrained infinite loops.
Implements:
1. 10 Core Autonomy Guards (Repeated tasks, file churning, timeboxes, resource bounds, max depth)
2. 4-Dimensional Decision Engine (Risk + Value + Confidence + Cost)
3. Explicit Escalation to Human ([ESCALATE_TO_HUMAN])
"""

import os
import time
import json
import logging
import shutil
from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger("AutonomyGuard")


@dataclass
class DecisionResult:
    action: str              # PROCEED_FAST | PROCEED_NORMAL | PROCEED_DEEP | AUDITOR_REVIEW | STOP_LOW_VALUE | ESCALATE_TO_HUMAN
    reason: str
    risk_tier: str           # R0 ~ R4
    value_tier: str          # LOW | MEDIUM | HIGH
    confidence_tier: str     # LOW | MEDIUM | HIGH
    cost_tier: str           # LOW | MEDIUM | HIGH


class AutonomySafetyLayer:
    """
    10 Core Autonomy Guards & Circuit Breakers for Bounded Autonomous Operation.
    """
    # Guard Limits
    MAX_TASK_REPEATS = 3                # Guard 1: Same task name repeat limit
    MAX_FILE_CHURN = 5                  # Guard 2: Same file modification repeat limit in a milestone
    MILESTONE_TIMEBOX_SECONDS = 1800.0  # Guard 3: 30 minutes max per milestone
    DAILY_LLM_CALL_LIMIT = 500          # Guard 4: Daily LLM calls per project
    MIN_DISK_FREE_GB = 2.0              # Guard 5: Minimum 2GB free disk space
    MAX_WATCHDOG_CONSECUTIVE_RUNS = 6   # Guard 6: Force cooldown after 6 unattended watchdog cycles
    MAX_SAME_ERROR_REPEATS = 2          # Guard 7: Same error repeating 2 times trips circuit breaker
    MAX_ROADMAP_EXPAND_DEPTH = 5        # Guard 8: Max auto-expansion phases beyond initial roadmap
    CONSECUTIVE_FAILURE_LIMIT = 3       # Guard 9: Consecutive QA failures before full halt
    MAX_TRIVIAL_TASK_REPEATS = 3        # Guard 10: Max consecutive trivial tasks before Goal Drift trip
    
    # In-memory tracking state: {channel_id: ChannelSafetyState}
    _states: Dict[int, Any] = {}

    @classmethod
    def get_state(cls, channel_id: int):
        if channel_id not in cls._states:
            cls._states[channel_id] = {
                "recent_tasks": [],               # List of task strings
                "file_churn_counter": {},          # file_path -> int
                "milestone_start_time": time.time(),
                "last_progress_time": 0.0,         # Explicit progress heartbeat timestamp (0.0 until recorded)
                "daily_call_count": 0,
                "daily_call_date": time.strftime("%Y-%m-%d"),
                "watchdog_runs": 0,
                "recent_errors": [],              # List of error strings
                "consecutive_failures": 0,
                "roadmap_expansions": 0,
                "is_escalated": False,
                "escalation_reason": ""
            }
        # Reset daily counter at midnight
        cur_date = time.strftime("%Y-%m-%d")
        if cls._states[channel_id]["daily_call_date"] != cur_date:
            cls._states[channel_id]["daily_call_date"] = cur_date
            cls._states[channel_id]["daily_call_count"] = 0
        return cls._states[channel_id]

    @classmethod
    def record_progress(cls, channel_id: int, signal_name: str = "progress", detail: str = ""):
        """
        Signals meaningful forward progress (e.g. file modified, build success, test executed).
        Refreshes the activity heartbeat to prevent false-positive stagnation trip.
        """
        st = cls.get_state(channel_id)
        st["last_progress_time"] = time.time()
        logger.debug(f"[AutonomyGuard] Progress heartbeat on channel {channel_id}: {signal_name} ({detail})")

    @classmethod
    def record_llm_call(cls, channel_id: int):
        st = cls.get_state(channel_id)
        st["daily_call_count"] += 1

    @classmethod
    def record_file_change(cls, channel_id: int, file_path: str):
        st = cls.get_state(channel_id)
        cnt = st["file_churn_counter"].get(file_path, 0) + 1
        st["file_churn_counter"][file_path] = cnt
        cls.record_progress(channel_id, "file_change", file_path)

    @classmethod
    def reset_milestone(cls, channel_id: int):
        """Resets milestone start time, last progress time, and file churn for a fresh user task."""
        st = cls.get_state(channel_id)
        now = time.time()
        st["milestone_start_time"] = now
        st["last_progress_time"] = now
        st["file_churn_counter"].clear()
        st["is_escalated"] = False
        st["escalation_reason"] = ""

    @classmethod
    def record_task_start(cls, channel_id: int, task_name: str, is_new_user_task: bool = False):
        st = cls.get_state(channel_id)
        now = time.time()
        # If this is a direct new user task or channel was idle for > 15 minutes, refresh milestone timer
        if is_new_user_task or (now - st.get("last_progress_time", 0.0) > 900.0) or (now - st["milestone_start_time"] > 3600.0):
            st["milestone_start_time"] = now
            st["last_progress_time"] = now
            st["file_churn_counter"].clear()
            st["is_escalated"] = False
            st["escalation_reason"] = ""
        st["recent_tasks"].append(task_name.strip().lower())
        if len(st["recent_tasks"]) > 10:
            st["recent_tasks"].pop(0)
        cls.record_progress(channel_id, "task_start", task_name)

    @classmethod
    def record_error(cls, channel_id: int, error_snippet: str):
        st = cls.get_state(channel_id)
        st["recent_errors"].append(error_snippet.strip().lower()[:200])
        if len(st["recent_errors"]) > 10:
            st["recent_errors"].pop(0)

    @classmethod
    def record_watchdog_run(cls, channel_id: int):
        st = cls.get_state(channel_id)
        st["watchdog_runs"] += 1

    @classmethod
    def reset_watchdog_runs(cls, channel_id: int):
        st = cls.get_state(channel_id)
        st["watchdog_runs"] = 0

    @classmethod
    def record_qa_result(cls, channel_id: int, passed: bool):
        st = cls.get_state(channel_id)
        if passed:
            st["consecutive_failures"] = 0
            st["file_churn_counter"].clear()
            st["milestone_start_time"] = time.time()
        else:
            st["consecutive_failures"] += 1

    @classmethod
    def record_roadmap_expansion(cls, channel_id: int) -> int:
        st = cls.get_state(channel_id)
        st["roadmap_expansions"] += 1
        return st["roadmap_expansions"]

    @classmethod
    def check_safety_guards(cls, channel_id: int, workspace: str, current_task: str = "") -> Tuple[bool, str, str]:
        """
        Evaluates the 10 Core Autonomy Guards.
        Returns: (is_safe, guard_action, detail_message)
        guard_action is one of: 'SAFE' | 'STOP' | 'ESCALATE_TO_HUMAN' | 'COOLDOWN'
        """
        st = cls.get_state(channel_id)

        # 1. Guard 9: Consecutive Failure Circuit Breaker
        if st["consecutive_failures"] >= cls.CONSECUTIVE_FAILURE_LIMIT:
            st["is_escalated"] = True
            st["escalation_reason"] = f"연속 {st['consecutive_failures']}회 QA 실패 누적"
            return False, "ESCALATE_TO_HUMAN", f"🚨 [Guard 9: 서킷 브레이커] 연속 {st['consecutive_failures']}회 QA 실패로 자동 롤백 및 사용자 개입이 필요합니다."

        # 2. Guard 1: Same Task Loop Trap
        if current_task:
            task_clean = current_task.strip().lower()
            matching_repeats = sum(1 for t in st["recent_tasks"] if t == task_clean)
            if matching_repeats >= cls.MAX_TASK_REPEATS:
                return False, "ESCALATE_TO_HUMAN", f"🔁 [Guard 1: 루프 트랩] 동일 태스크('{current_task[:40]}...')가 {matching_repeats}회 반복 실행되었습니다. 무한 루프 방지를 위해 작업을 중단합니다."

        # 3. Guard 2: File Churning Detector
        for fpath, count in st["file_churn_counter"].items():
            if count >= cls.MAX_FILE_CHURN:
                return False, "ESCALATE_TO_HUMAN", f"🌪️ [Guard 2: 파일 폭주] 파일 `{os.path.basename(fpath)}`이(가) 한 마일스톤 내에서 {count}회 반복 수정되었습니다. 불안정한 수정 루프를 방지합니다."

        # 4. Guard 3: Milestone Timebox & Stagnation Detection (Hybrid Activity-Aware Breaker)
        elapsed_milestone = time.time() - st["milestone_start_time"]
        last_prog = st.get("last_progress_time", st["milestone_start_time"])
        stagnation_elapsed = time.time() - last_prog

        is_android = bool(workspace and (
            os.path.exists(os.path.join(workspace, "app", "build.gradle")) or
            os.path.exists(os.path.join(workspace, "app", "build.gradle.kts")) or
            os.path.exists(os.path.join(workspace, "build.gradle"))
        ))

        base_timebox = 5400.0 if is_android else cls.MILESTONE_TIMEBOX_SECONDS  # 90m for Android, 30m for other
        hard_ceiling = 7200.0 if is_android else 3600.0  # 120m for Android, 60m for other
        has_recent_progress = (stagnation_elapsed < 600.0)  # Active progress within last 10 minutes

        # Check hard ceiling first
        if elapsed_milestone > hard_ceiling:
            return False, "ESCALATE_TO_HUMAN", f"⏳ [Guard 3: 타임박스 만료] 현재 마일스톤이 {elapsed_milestone/60:.1f}분 동안 완료되지 못했습니다 (절대 상한 {hard_ceiling/60:.0f}분). 전략 재검토가 필요합니다."

        # Check base timebox when no recent progress
        if elapsed_milestone > base_timebox and not has_recent_progress:
            return False, "ESCALATE_TO_HUMAN", f"⏳ [Guard 3: 타임박스 만료] 현재 마일스톤이 {elapsed_milestone/60:.1f}분 동안 완료되지 못했습니다 (상한 {base_timebox/60:.0f}분). 전략 재검토가 필요합니다."

        # 5. Guard 4: Daily LLM Quota / Call Breaker
        if st["daily_call_count"] >= cls.DAILY_LLM_CALL_LIMIT:
            return False, "STOP", f"💳 [Guard 4: 일일 쿼터 상한] 오늘 자율 LLM 호출 횟수({st['daily_call_count']}회) 한도에 도달했습니다. 비용 및 쿼터 보호를 위해 내일까지 대기합니다."

        # 6. Guard 5: Free Disk Space Threshold
        if workspace and os.path.isdir(workspace):
            try:
                total, used, free = shutil.disk_usage(workspace)
                free_gb = free / (1024 ** 3)
                if free_gb < cls.MIN_DISK_FREE_GB:
                    return False, "STOP", f"💾 [Guard 5: 디스크 용량 고갈] 여유 디스크 공간이 {free_gb:.2f}GB (기준 {cls.MIN_DISK_FREE_GB}GB 미만)로 부족합니다."
            except Exception:
                pass

        # 7. Guard 6: Watchdog Runaway Brake
        if st["watchdog_runs"] >= cls.MAX_WATCHDOG_CONSECUTIVE_RUNS:
            cls.reset_watchdog_runs(channel_id)
            return False, "COOLDOWN", f"🛑 [Guard 6: Watchdog 브레이크] 무인 감시견이 {cls.MAX_WATCHDOG_CONSECUTIVE_RUNS}회 연속 무인 작업을 수행했습니다. 하드웨어 안정화를 위해 10분간 의도적 휴식에 들어갑니다."

        # 8. Guard 7: Repeated Identical Error Detector
        if len(st["recent_errors"]) >= cls.MAX_SAME_ERROR_REPEATS:
            recent_tail = st["recent_errors"][-cls.MAX_SAME_ERROR_REPEATS:]
            if len(set(recent_tail)) == 1 and len(recent_tail[0]) > 20:
                return False, "ESCALATE_TO_HUMAN", f"⚠️ [Guard 7: 동일 결함 반복] 동일한 에러 패턴이 {cls.MAX_SAME_ERROR_REPEATS}회 연속 발생하여 Coder 자율 수렴이 불가합니다. 개발자 직접 확인을 요청합니다."

        # 9. Guard 8: Roadmap Auto-Expansion Max Depth
        if st["roadmap_expansions"] >= cls.MAX_ROADMAP_EXPAND_DEPTH:
            return False, "STOP", f"🏁 [Guard 8: 로드맵 확장 최대 한도] 로드맵 자동 확장 깊이({st['roadmap_expansions']}차)가 상한에 도달했습니다. 추가 확장은 사용자의 새로운 기획/승인이 필요합니다."

        # 10. Guard 10: Goal Drift & Trivial Work Detector (Anti-Stagnation)
        trivial_keywords = ["readme", "주석", "문서화", "포맷팅", "formatting", "단순 주석", "코드 정리", "refactor only"]
        if current_task and any(k in current_task.lower() for k in trivial_keywords):
            matching_trivial = sum(1 for t in st["recent_tasks"] if any(k in t for k in trivial_keywords))
            if matching_trivial >= cls.MAX_TRIVIAL_TASK_REPEATS:
                return False, "ESCALATE_TO_HUMAN", f"🎯 [Guard 10: 목표 표류 감지 (Goal Drift)] 사소한 문서/주석/정리 작업이 {matching_trivial}회 연속 반복되었습니다. 핵심 기능 개발로 복귀하기 위해 사용자 방향 재설정이 필요합니다."

        # 11. Guard 11: Trash-Only Deletion Enforcement (휴지통 보존 삭제 헌법)
        if current_task:
            task_lower = current_task.lower()
            destructive_patterns = ["rm -rf", "rm -r", "rmdir /s", "shutil.rmtree"]
            if any(p in task_lower for p in destructive_patterns):
                return False, "ESCALATE_TO_HUMAN", "🛡️ [Guard 11: 휴지통 보존 삭제 헌법 위반] 영구 삭제(rm -rf) 시도가 감지되었습니다. 모든 파일 및 디렉토리 삭제는 OS 휴지통(~/.local/share/Trash)으로 안전 이동되어야 합니다."

        # 12. Guard 12: Strict Workspace Confinement & Boundary Isolation (지정 워크스페이스 격리 헌법)
        if current_task and workspace:
            import re
            traversal_patterns = [r"\.\./", r"\.\.\\", r"cd\s+\.\."]
            for pat in traversal_patterns:
                if re.search(pat, current_task):
                    return False, "STOP", f"🔒 [Guard 12: 지정 워크스페이스 격리 헌법 위반] 상위 또는 인접 프로젝트 디렉토리 접근 시도(`..`)가 감지되었습니다. 작업은 오직 지정된 워크스페이스(`{workspace}`) 내에서만 허용됩니다."

        return True, "SAFE", "모든 자율 안전 가드레일 정상 (Bounded Autonomy Nominal)"

    @classmethod
    def is_within_workspace(cls, target_path: str, workspace: str) -> bool:
        """Verifies whether target_path strictly resides within workspace root (handling symlinks gracefully)."""
        if not target_path or not workspace:
            return True
        try:
            abs_ws = os.path.abspath(workspace)
            abs_tgt = os.path.abspath(target_path)
            # 1. Direct path containment
            if abs_tgt == abs_ws or abs_tgt.startswith(abs_ws.rstrip(os.sep) + os.sep):
                return True
            # 2. Canonical realpath containment (resolving symlinks)
            real_ws = os.path.realpath(abs_ws)
            real_tgt = os.path.realpath(abs_tgt)
            if real_tgt == real_ws or real_tgt.startswith(real_ws.rstrip(os.sep) + os.sep):
                return True
            # 3. If workspace itself has symlinked children, check underlying target roots
            if os.path.isdir(abs_ws):
                for item in os.listdir(abs_ws):
                    item_path = os.path.join(abs_ws, item)
                    if os.path.islink(item_path):
                        real_item = os.path.realpath(item_path)
                        if real_tgt == real_item or real_tgt.startswith(real_item.rstrip(os.sep) + os.sep):
                            return True
            return False
        except Exception:
            return False

    @classmethod
    def safe_trash(cls, target_path: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Safely moves a target file or folder to OS Trash via safe_trash.send_to_trash."""
        try:
            import safe_trash
            return safe_trash.send_to_trash(target_path)
        except Exception as e:
            return False, f"휴지통 이동 실패: {e}", {}



# ==============================================================================
# 4-Dimensional Decision Matrix Engine: Risk + Value + Confidence + Cost
# ==============================================================================

class DecisionMatrixEngine:
    @staticmethod
    def evaluate(
        risk_tier: str,           # R0 | R1 | R2 | R3 | R4
        value_tier: str = "HIGH", # LOW | MEDIUM | HIGH
        confidence_tier: str = "HIGH", # LOW | MEDIUM | HIGH
        cost_tier: str = "LOW"    # LOW | MEDIUM | HIGH
    ) -> DecisionResult:
        """
        Computes the optimal autonomous execution decision using a 4-dimensional matrix:
        Risk (R0~R4) x Value (L/M/H) x Confidence (L/M/H) x Cost (L/M/H)
        """
        r = risk_tier.upper().strip()
        v = value_tier.upper().strip()
        cf = confidence_tier.upper().strip()
        cs = cost_tier.upper().strip()

        # Rule 1: Zero/Low Value is stopped regardless of risk to prevent waste
        if v == "LOW":
            return DecisionResult(
                action="STOP_LOW_VALUE",
                reason="사용자 체감 가치가 낮거나 불필요한 기술 부채를 유발하는 작업 (Bypass & Stop)",
                risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
            )

        # Rule 2: R0 Inquiry / Consulting -> Single agent
        if r == "R0":
            return DecisionResult(
                action="PROCEED_FAST",
                reason="단순 질문 및 개념 자문 (Single Agent Fast Response)",
                risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
            )

        # Rule 3: R1 Cosmetic / Trivial -> Fast Path
        if r == "R1":
            return DecisionResult(
                action="PROCEED_FAST",
                reason="무위험 경량 작업 (Coder -> Fast QA -> Immediate Pass)",
                risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
            )

        # Rule 4: High Risk (R4) or Low Confidence with Medium/High Cost -> Escalate or Deep QA
        if r == "R4":
            if cf == "LOW":
                return DecisionResult(
                    action="AUDITOR_REVIEW",
                    reason="R4 치명적 영역(보안/DB/인증) 및 낮은 신뢰도: Auditor 사전 수락 심사 강제",
                    risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
                )
            return DecisionResult(
                action="PROCEED_DEEP",
                reason="R4 치명적/코어 영역: Deep Pro QA + Pro Auditor + Full E2E 전수 검증",
                risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
            )

        # Rule 5: R3 System Impact
        if r == "R3":
            if cf == "LOW" and cs == "HIGH":
                return DecisionResult(
                    action="AUDITOR_REVIEW",
                    reason="시스템 영향(R3) 고비용 작업이며 신뢰도가 낮아 Auditor 방향성 재검토 필요",
                    risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
                )
            return DecisionResult(
                action="PROCEED_DEEP",
                reason="시스템 아키텍처 및 코어 비즈니스 로직: Pro QA + Auditor + 표준 E2E",
                risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
            )

        # Rule 6: R2 Standard Feature / UI
        if r == "R2":
            if v == "MEDIUM" and cf == "LOW" and cs == "HIGH":
                return DecisionResult(
                    action="AUDITOR_REVIEW",
                    reason="R2 작업 중 신뢰도가 낮고 비용이 높아 Auditor 사전 방향성 조율 권장",
                    risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
                )
            return DecisionResult(
                action="PROCEED_NORMAL",
                reason="표준 신규 기능 및 컴포넌트: Coder -> QA -> Auditor (선택적 E2E)",
                risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
            )

        # Default fallback
        return DecisionResult(
            action="PROCEED_NORMAL",
            reason="표준 자율 협업 파이프라인 진행",
            risk_tier=r, value_tier=v, confidence_tier=cf, cost_tier=cs
        )


# ==============================================================================
# Tri-Layer Telemetry Architecture: Decision != Action != Outcome (AGY 5.0)
# ==============================================================================

@dataclass
class DecisionData:
    risk: str                    # R0 | R1 | R2 | R3 | R4
    value: str                   # LOW | MEDIUM | HIGH
    confidence: float            # 0.0 ~ 1.0
    cost: str                    # LOW | MEDIUM | HIGH
    route: str                   # PROCEED_FAST | PROCEED_NORMAL | PROCEED_DEEP | AUDITOR_REVIEW | STOP_LOW_VALUE
    reason: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ActionData:
    coder_status: str = "pending"   # pending | completed | failed | skipped
    qa_verdict: str = "pending"     # pending | passed | defect_detected | skipped
    auditor_action: str = "pending" # pending | proceed | revise | stop | skipped
    rounds_taken: int = 1
    tool_calls: int = 0
    defects: List[str] = field(default_factory=list)


@dataclass
class OutcomeData:
    final: str = "pending"          # success | defect_detected | stopped | escalated
    iterations: int = 1
    files_changed: int = 0
    llm_calls: int = 0
    elapsed: float = 0.0
    is_converged: bool = False
    escalation_reason: str = ""
    calibration_score: float = 1.0  # Evaluates whether initial decision matched final reality


@dataclass
class TriLayerTelemetryRecord:
    mission_id: str
    channel_id: int
    mission: str
    decision: DecisionData
    action: ActionData = field(default_factory=ActionData)
    outcome: OutcomeData = field(default_factory=OutcomeData)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "channel_id": self.channel_id,
            "mission": self.mission,
            "decision": {
                "risk": self.decision.risk,
                "value": self.decision.value,
                "confidence": round(self.decision.confidence, 2),
                "cost": self.decision.cost,
                "route": self.decision.route,
                "reason": self.decision.reason,
                "timestamp": self.decision.timestamp
            },
            "action": {
                "coder": self.action.coder_status,
                "qa": self.action.qa_verdict,
                "auditor": self.action.auditor_action,
                "rounds_taken": self.action.rounds_taken,
                "tool_calls": self.action.tool_calls,
                "defects": self.action.defects
            },
            "outcome": {
                "final": self.outcome.final,
                "iterations": self.outcome.iterations,
                "files_changed": self.outcome.files_changed,
                "llm_calls": self.outcome.llm_calls,
                "elapsed": round(self.outcome.elapsed, 2),
                "is_converged": self.outcome.is_converged,
                "escalation_reason": self.outcome.escalation_reason,
                "calibration_score": round(self.outcome.calibration_score, 2)
            }
        }


class TriLayerTelemetryStore:
    """
    Stores and evaluates Tri-Layer Telemetry (Decision != Action != Outcome).
    Allows AGY to analyze whether its initial routing decision was accurate relative to actual outcome.
    """
    _records: Dict[str, TriLayerTelemetryRecord] = {}

    @classmethod
    def record_decision(
        cls,
        mission_id: str,
        channel_id: int,
        mission: str,
        decision_result: DecisionResult
    ) -> TriLayerTelemetryRecord:
        conf_val = 0.95 if decision_result.confidence_tier == "HIGH" else (0.75 if decision_result.confidence_tier == "MEDIUM" else 0.45)
        dec = DecisionData(
            risk=decision_result.risk_tier,
            value=decision_result.value_tier,
            confidence=conf_val,
            cost=decision_result.cost_tier,
            route=decision_result.action,
            reason=decision_result.reason,
            timestamp=time.time()
        )
        rec = TriLayerTelemetryRecord(
            mission_id=mission_id,
            channel_id=channel_id,
            mission=mission,
            decision=dec,
            action=ActionData(),
            outcome=OutcomeData()
        )
        cls._records[mission_id] = rec
        return rec

    @classmethod
    def get_record(cls, mission_id: str) -> Optional[TriLayerTelemetryRecord]:
        return cls._records.get(mission_id)

    @classmethod
    def update_action(
        cls,
        mission_id: str,
        coder_status: Optional[str] = None,
        qa_verdict: Optional[str] = None,
        auditor_action: Optional[str] = None,
        rounds_taken: Optional[int] = None,
        tool_calls: Optional[int] = None,
        defect: Optional[str] = None
    ):
        rec = cls._records.get(mission_id)
        if not rec:
            return
        if coder_status:
            rec.action.coder_status = coder_status
        if qa_verdict:
            rec.action.qa_verdict = qa_verdict
        if auditor_action:
            rec.action.auditor_action = auditor_action
        if rounds_taken is not None:
            rec.action.rounds_taken = rounds_taken
        if tool_calls is not None:
            rec.action.tool_calls += tool_calls
        if defect:
            rec.action.defects.append(defect[:200])

    @classmethod
    def finalize_outcome(
        cls,
        mission_id: str,
        final_status: str,
        iterations: int = 1,
        files_changed: int = 0,
        llm_calls: int = 0,
        elapsed: float = 0.0,
        is_converged: bool = True,
        escalation_reason: str = "",
        workspace: Optional[str] = None
    ) -> Optional[TriLayerTelemetryRecord]:
        rec = cls._records.get(mission_id)
        if not rec:
            return None

        # Post-hoc Calibration Analysis: Did Decision accurately anticipate actual execution effort?
        calib = 1.0
        route = rec.decision.route
        if route == "STOP_LOW_VALUE":
            calib = 1.0 if final_status in ("stopped", "stop") else 0.5
        elif route == "PROCEED_FAST":
            # FAST route expects low iterations (<=1) and low files_changed (<=3)
            if iterations > 1 or files_changed > 3 or not is_converged:
                calib = max(0.2, 1.0 - (iterations - 1) * 0.3 - max(0, files_changed - 2) * 0.1)
        elif route == "PROCEED_NORMAL":
            # NORMAL expects 1~2 iterations, <=6 files
            if iterations > 3 or files_changed > 10:
                calib = max(0.3, 1.0 - (iterations - 2) * 0.2)
        elif route == "PROCEED_DEEP":
            calib = 1.0 if is_converged else 0.8
        elif route == "AUDITOR_REVIEW":
            calib = 1.0 if rec.action.auditor_action in ("stop", "revise", "proceed") else 0.8

        rec.outcome = OutcomeData(
            final=final_status,
            iterations=iterations,
            files_changed=files_changed,
            llm_calls=llm_calls,
            elapsed=elapsed,
            is_converged=is_converged,
            escalation_reason=escalation_reason,
            calibration_score=round(calib, 2)
        )

        # Save to JSONL on disk if workspace available
        if workspace and os.path.isdir(workspace):
            try:
                rep_dir = os.path.join(workspace, "reports", "orchestration")
                os.makedirs(rep_dir, exist_ok=True)
                jsonl_path = os.path.join(rep_dir, "decision_action_outcome.jsonl")
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.debug(f"Telemetry save failed: {e}")

        return rec


# ==============================================================================
# Collaboration Intelligence & Convergence Engine (AGY 5.0)
# ==============================================================================

class CollaborationIntelligence:
    """
    Evaluates Collaboration Quality, Anti-Collusion, and Convergence across agents.
    Prevents Coder <-> QA infinite ping-pong loops and catches requirement omission or false passes.
    """
    _round_defect_history: Dict[int, List[str]] = {}

    @classmethod
    def reset_channel(cls, channel_id: int):
        if channel_id in cls._round_defect_history:
            cls._round_defect_history[channel_id].clear()

    @staticmethod
    def _extract_core_defect_signature(defect_text: str) -> str:
        """
        Extracts the substantive defect description, stripping standard markdown boilerplate,
        status tags, and warning banners.
        """
        if not defect_text or not isinstance(defect_text, str):
            return ""
        import re
        lines = defect_text.splitlines()
        content_lines = []
        skip_keywords = [
            "[status: rejected]", "[status: approved]", "품질 게이트 강제 반려",
            "협업 수렴 실패", "시니어 qa", "검증 결과", "안티 모킹", "quality gate",
            "필수 요구사항 누락", "ping-pong trap", "결함 보강 재작업"
        ]
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            line_lower = line_str.lower()
            if any(kw in line_lower for kw in skip_keywords):
                continue
            cleaned_line = re.sub(r'^[#*>\s\-`]+', '', line_str).strip()
            cleaned_line = re.sub(r'[*`_]', '', cleaned_line).strip()
            if len(cleaned_line) > 5:
                content_lines.append(cleaned_line)

        if content_lines:
            return " ".join(content_lines).lower().strip()
        return defect_text.strip().lower()

    @classmethod
    def detect_convergence_failure(cls, channel_id: int, current_defect: str, round_num: int) -> Tuple[bool, str]:
        """
        Adversarial Test A10: Detects if Coder and QA are stuck in a non-converging ping-pong loop.
        If the same core defect appears across 2 rounds without resolution, trips convergence breaker.
        """
        if channel_id not in cls._round_defect_history:
            cls._round_defect_history[channel_id] = []
        
        hist = cls._round_defect_history[channel_id]
        clean_def = current_defect.strip().lower()
        core_sig = cls._extract_core_defect_signature(current_defect)
        
        # Check similarity or repetition with previous defect
        if len(hist) >= 1:
            prev_item = hist[-1]
            prev_raw = prev_item if isinstance(prev_item, str) else prev_item.get("raw", "")
            prev_core = prev_item if isinstance(prev_item, str) else prev_item.get("core", "")

            # Exact match of raw or core
            is_exact = (clean_def == prev_raw) or (bool(core_sig) and core_sig == prev_core)

            # Substantive repetition of core defect (guard against boilerplate header false-positives)
            is_substantive = False
            if core_sig and prev_core and len(core_sig) > 20 and len(prev_core) > 20:
                from difflib import SequenceMatcher
                sim_ratio = SequenceMatcher(None, core_sig, prev_core).ratio()
                if sim_ratio >= 0.85 or (core_sig[:200] in prev_core or prev_core[:200] in core_sig):
                    is_substantive = True

            if is_exact or is_substantive:
                display_def = (core_sig[:60] if core_sig else clean_def[:60])
                return True, f"🚨 [A10: 수렴 실패(Ping-Pong Trap)] Coder와 QA가 동일한 결함('{display_def}...')을 두고 2회 연속 평행선을 달리고 있습니다. 무한 반복 방지를 위해 중단하고 사용자 개입을 요청합니다."

        hist.append({"raw": clean_def, "core": core_sig})
        return False, ""

    @staticmethod
    def extract_pure_user_prompt(prompt: str) -> str:
        """
        Extracts only the actual user request from a complex prompt containing
        system prompts, tool safety headers, workspace constraints, profile rules,
        attachments, and feedback banners.
        """
        if not prompt or not isinstance(prompt, str):
            return ""

        import re

        # 1. Try to extract from standard user prompt delimiter blocks
        user_block_patterns = [
            r"사용자가\s+다음과\s+같은\s+작업을\s+요청했습니다\s*:\s*\n[─━=-]{5,}\n(.*?)\n[─━=-]{5,}",
            r"\*\*\[🎯?\s*사용자\s*원본\s*요청[^\n]*\]\*\*:\s*\n[═─━=-]{5,}\n(.*?)\n[═─━=-]{5,}",
            r"\*\*\[🎯?\s*사용자\s*원본\s*의뢰[^\n]*\]\*\*:\s*\n(.*?)(?:\n\*\*\[|\Z)",
            r"\[🚨\s*최우선\s*검증\s*대상\s*-\s*사용자\s*원본\s*요청[^\n]*\]:\s*\n[─━=-]{5,}\n(.*?)\n[─━=-]{5,}",
        ]

        extracted = None
        for pat in user_block_patterns:
            m = re.search(pat, prompt, re.DOTALL)
            if m:
                extracted = m.group(1).strip()
                break

        target = extracted if extracted else prompt

        # 2. Strip system safety headers and workspace constraints if still present
        target = re.sub(r"\[CRITICAL SYSTEM & TOOL SAFETY INSTRUCTIONS\].*?(?=(?:사용자가|\[🎯 WORKSPACE FOCUS|$))", "", target, flags=re.DOTALL)
        target = re.sub(r"\[🎯 WORKSPACE FOCUS & CONSTRAINTS\].*?(?=(?:사용자가|• 🔒 핵심|$))", "", target, flags=re.DOTALL)
        target = re.sub(r"• 🔒 핵심 제약.*?\n", "", target)
        target = re.sub(r"• 🎯 \*\*집중 과제\*\*.*?\n", "", target)
        target = re.sub(r"• 🏛️ \*\*활성 프로파일\*\*.*?\n", "", target)

        # 3. Strip attachments, vision blocks, feedback banners
        split_markers = [
            "[📄 첨부 파일 내용:",
            "[🖼️ 사용자 제공 참고",
            "[🔍 Gemini Vision",
            "[피드백 반영 및 자율 운영 보강 요청",
            "[피드백 반영 및 운영/전략 보강 요청",
            "[qa1 반려 사유]",
            "[qa2 반려 사유]",
            "[qa3 반려 사유]",
            "[STATUS: REJECTED]",
            "[STATUS: AUDIT_FAILED]",
            "시니어 QA 리뷰 및 최종 감사관",
            "**[검증 관점]:**",
            "**[검증 및 판정 규칙]:**",
            "**[핵심 개발 지침",
            "**[핵심 실행 지침"
        ]
        for sm in split_markers:
            if sm in target:
                target = target.split(sm)[0].strip()

        return target.strip()

    @staticmethod
    def validate_requirement_coverage(
        prompt: str,
        code_diff_or_output: str
    ) -> Tuple[bool, List[str], str]:
        """
        Collaboration Test C1/C2: Anti-Collusion & Requirement Omission Detector.
        Ensures Coder did not silently drop explicit requirements or produce a hollow mock pass.
        Returns: (passed, missing_requirements, details)
        """
        import re

        # Extract only the pure user request, ignoring system safety rules and background headers
        pure_user_prompt = CollaborationIntelligence.extract_pure_user_prompt(prompt)

        is_inquiry = any(kw in pure_user_prompt.lower() for kw in [
            "질문", "궁금", "설명", "알려", "어떻게 생각해", "구현되어 있는지", "의견에 대해서",
            "왜 이런 일", "원인이", "원인은", "원인 분석", "이유가", "이유는", "어때", "진단", "해석",
            "이상하지 않아", "몇 점", "몇점", "평가", "검토", "리뷰", "의혹", "점검", "확인해줘", "체크해줘",
            "있을까", "될까", "가능할까", "맞을까"
        ])
        if is_inquiry and not any(act in pure_user_prompt.lower() for act in ["해결해줘", "구현해줘", "고쳐줘", "수정해줘"]):
            return True, [], "질의응답 및 아키텍처/원인 분석 요청 (요구사항 검증 정상 통과)"

        target_prompt = pure_user_prompt
        req_patterns = [
            r"(?:\[REQ-?\d+\]|REQ-?\d+[:\s]+)([^\n]+)",
            r"(?:필수 구현|핵심 요구|반드시 구현)[^\n]*:\s*([^\n]+)",
            r"(?:이번에 만들 것|구현할 기능)[^\n]*:\s*\n*((?:[•\-\*]\s*[^\n]+\n*)+)"
        ]

        system_instruction_exclusions = [
            "tool usage rules", "script & process execution safety", "service safety",
            "link formatting", "run_command & build safety", "universal value creation",
            "anti-mocking mandate", "default timestamped build artifact",
            "commander-approved visual asset", "cross-platform e2e autonomous testing",
            "profile:", "집중 과제", "핵심 제약", "검증: pytest", "규격:",
            "컨텍스트 청정화", "명령 순응", "가시적 실체 구현", "빌드/테스트 완결",
            "완료 기준", "디자인 & 에셋", "정적 분석", "컴파일/빌드 성공",
            "콘솔 오류", "전수 검증 통과", "auditor", "감사관", "품질 게이트",
            "사령관 명령", "마스터플랜", "지침 준수", "도구 안전", "단위 테스트 완결",
            "최종 전수 검증", "무결점 작동", "린트 에러 0건", "크래시 0건"
        ]

        clean_requirements = []
        for pat in req_patterns:
            matches = re.findall(pat, target_prompt, re.IGNORECASE)
            for m in matches:
                # Handle bulleted blocks
                if "\n" in m:
                    lines = [ln.strip().lstrip("•-* ").strip() for ln in m.splitlines() if ln.strip()]
                    for item in lines:
                        cleaned = re.sub(r"^(?:\d+[\.\)]\s*|\[REQ-?\d+\]\s*)+", "", item).strip()
                        target_req = cleaned if len(cleaned) >= 4 else item
                        lower_req = target_req.lower()
                        if any(ex in lower_req for ex in system_instruction_exclusions):
                            continue
                        if len(target_req) >= 4 and target_req not in clean_requirements:
                            clean_requirements.append(target_req)
                    continue

                item = m.strip().strip("-*• ")
                cleaned = re.sub(r"^(?:\d+[\.\)]\s*|\[REQ-?\d+\]\s*)+", "", item).strip()
                target_req = cleaned if len(cleaned) >= 4 else item
                lower_req = target_req.lower()
                if any(ex in lower_req for ex in system_instruction_exclusions):
                    continue
                if len(target_req) >= 4 and target_req not in clean_requirements:
                    clean_requirements.append(target_req)

        if not clean_requirements:
            return True, [], "명시적 세부 요구사항 목록 없음 (기본 통과)"

        code_lower = code_diff_or_output.lower()
        missing = []
        for req in clean_requirements:
            raw_tokens = re.findall(r"[가-힣]{2,}|[a-zA-Z0-9_]{3,}", req)
            words = [w.lower() for w in raw_tokens if not w.isdigit()]
            matched_words = 0
            for w in words:
                stem = re.sub(r'(?:도|가|이|을|를|은|는|에|의|로|으로|에서|서|함|됨)$', '', w)
                if w in code_lower or (len(stem) >= 2 and stem in code_lower):
                    matched_words += 1
            if words and (matched_words / len(words)) < 0.20:
                missing.append(req)

        if missing:
            return False, missing, f"⚠️ Coder 산출물에서 필수 요구사항 {len(missing)}건 누락 감지 ({', '.join(missing[:2])})"
        return True, [], "모든 필수 요구사항 반영 확인 완료"

    @staticmethod
    def evaluate_auditor_value_alignment(
        prompt: str,
        diff_info: Dict[str, Any],
        is_cosmetic_only: bool = False
    ) -> Tuple[str, str]:
        """
        Collaboration Test C3: Auditor Value Judgment.
        Determines whether the implemented change creates genuine user value or introduces bloat/debt.
        """
        changed_files = diff_info.get("changed_files", [])
        
        # Filter changed files to actual production code files, ignoring deleted files or test/report artifacts
        prod_code_files = [
            f for f in changed_files
            if any(f.lower().endswith(ext) for ext in [".py", ".kt", ".java", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".vue", ".json", ".xml", ".gradle"])
            and not any(t in f.lower() for t in ["test", "mock", "stub", ".tmp", "reports/", "screenshot", "삭제예정"])
        ]
        
        # Monorepo guard: If changed files belong to multiple distinct subprojects in a monorepo,
        # isolate prod_code_files to the target project referenced in the prompt.
        prompt_lower = prompt.lower()
        project_keywords = [kw for kw in ["pdf", "idus", "imoticon", "agent", "phonecase", "vibecoding"] if kw in prompt_lower]
        if project_keywords:
            scoped_files = [f for f in prod_code_files if any(kw in f.lower() for kw in project_keywords)]
            if scoped_files:
                prod_code_files = scoped_files
            elif any(f.startswith(("!8_", "!93_", "!5_", "!1_")) for f in prod_code_files):
                prod_code_files = []

        is_minor_request = ("간단" in prompt.lower() or "오타" in prompt.lower() or "경량" in prompt.lower() or is_cosmetic_only)
        is_continuous_override = any(kw in prompt.lower() for kw in ["계속", "무한", "끝까지", "전체 시스템", "다시 시도", "스킵 없이"])

        # Scenario 1: Minor request but bloated production file modifications (e.g. 10+ prod files)
        if is_minor_request and not is_continuous_override and len(prod_code_files) >= 10:
            return "STOP_LOW_VALUE", f"과잉 수정 감지: 단순 작업 요청에 {len(prod_code_files)}개 파일이 수정되어 불필요한 기술 부채 및 회귀 위험을 유발합니다."
            
        # Scenario 2: Zero substance changes (only comments or mock files)
        if diff_info.get("has_changes", False):
            # Check if this was a cleanup/deletion operation or inquiry
            deleted_files = diff_info.get("deleted_files", [])
            is_inquiry_or_cleanup = any(kw in prompt.lower() for kw in ["질문", "궁금", "설명", "알려", "정리", "삭제", "청소", "cleanup", "clean", "remove"])
            if (len(changed_files) == 0 and len(deleted_files) > 0) or is_inquiry_or_cleanup:
                return "PROCEED", "질의응답 또는 정리(삭제) 작업 정상 확인"

            prod_files = [f for f in changed_files if not any(t in f.lower() for t in ["test", "mock", "stub", ".tmp", "reports/", "screenshot", "삭제예정"])]
            if not prod_files and len(changed_files) > 0 and "test" not in prompt.lower():
                return "STOP_LOW_VALUE", "비가시적 눈속임 감지: 프로덕션 코드 변경 없이 테스트/목/임시 파일만 추가되었습니다."

        return "PROCEED", "가치 부합 및 정당한 수정 확인"
