#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
product_gate.py

Sequential Milestone Completion Gate — **단일 진실 공급원 (Single Source of Truth)**

■ 배경 (왜 필요한가)
    기존 파이프라인은 "다음 단계로 넘어가는 것"을 우선하여, 마일스톤이 50~60%만
    구현돼도(예: critical dimension 60~75점) 다음 마일스톤으로 진화했다. 그 결과
    "만들어지긴 했지만 말하지 않은 부분은 빠진" 부분 완성 제품이 누적됐다.

■ 핵심 원칙
    "다음 단계로 넘어가는 것"보다 **"현재 단계의 완결"**을 우선한다.
    - 마일스톤 PASS: Overall >= 90 AND 모든 Critical Dimension >= 90 AND
      핵심 요구사항 100% AND Exit Criteria 100% AND E2E PASS AND
      Critical Defect == 0 AND 회귀 PASS.
    - 순차 잠금: 이전 마일스톤이 PASS 되기 전에는 다음 마일스톤을 구현 대상으로
      선택할 수 없다 (A PASS → B UNLOCK → C UNLOCK).
    - Backlog 제한: UX/시각/안정성/성능/테스트/에러처리 등 현재 마일스톤의
      Exit Criteria에 포함된 필수 항목은 "차기 백로그"로 이관할 수 없다.

■ 범위
    순수 로직/상수만 제공한다(무거운 모듈 import 금지 → 순환 import 방지).
    Discord/LLM/E2E 등 실행 계층은 이 모듈의 판정만 소비한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 1. 임계값 (Thresholds) — 숫자가 아니라 "상태 전이 구조"를 위한 계약값
# ---------------------------------------------------------------------------
MILESTONE_PASS_SCORE = 90.0        # 마일스톤 통과 최소 종합 점수
FINAL_PASS_SCORE = 90.0            # 최종 제품 통과 최소 종합 점수
CRITICAL_DIMENSION_MIN_SCORE = 90.0  # 모든 Critical 차원의 최소 점수

# 상태값 분류
PASS_STATUSES: Tuple[str, ...] = ("DONE", "PASSED", "PASS")
CLEARED_STATUSES: Tuple[str, ...] = ("DONE", "PASSED", "PASS", "SKIPPED")

# ---------------------------------------------------------------------------
# 1-b. 개선점 심각도(severity) 정책 — "발견했다"에서 "게이트에 반영한다"로
#    QA가 발견한 개선점을 단순 백로그가 아니라 품질 게이트 신호로 연결한다.
#    - P0(치명) / P1(중요): 게이트 FAIL — 90점 이상이어도 PASS 금지.
#    - P2(권장): 품질 점수 감점(Overall 을 깎아 90점 미만이면 FAIL).
#    - P3(선택): 소폭 감점.
# ---------------------------------------------------------------------------
IMPROVEMENT_SEVERITIES: Tuple[str, ...] = ("P0", "P1", "P2", "P3")
IMPROVEMENT_BLOCKING_SEVERITIES: Tuple[str, ...] = ("P0", "P1")
IMPROVEMENT_PENALTY_BY_SEVERITY: Dict[str, float] = {"P0": 0.0, "P1": 0.0, "P2": 5.0, "P3": 1.0}
IMPROVEMENT_PENALTY_CAP = 20.0


def normalize_severity_counts(severities: Any) -> Dict[str, int]:
    """다양한 형태의 severity 입력을 ``{"P0": n, "P1": n, ...}`` 로 정규화한다."""
    counts: Dict[str, int] = {s: 0 for s in IMPROVEMENT_SEVERITIES}
    if not severities:
        return counts
    if isinstance(severities, dict):
        items: Iterable = severities.items()
    elif isinstance(severities, (list, tuple, set)):
        # 문자열/레벨 목록이면 각 항목을 1건으로 집계
        items = [(str(v), 1) for v in severities]
    else:
        return counts
    for key, val in items:
        level = str(key).strip().upper()
        if level not in counts:
            continue
        if isinstance(val, bool):
            val = int(val)
        try:
            counts[level] += max(0, int(val))
        except (TypeError, ValueError):
            continue
    return counts


def improvement_blocking_count(severities: Any) -> int:
    """P0/P1(게이트 FAIL) 개선점 총 개수."""
    counts = normalize_severity_counts(severities)
    return sum(counts.get(s, 0) for s in IMPROVEMENT_BLOCKING_SEVERITIES)


def improvement_score_penalty(severities: Any) -> float:
    """개선점 severity 기반 품질 점수 감점(상한 적용)."""
    counts = normalize_severity_counts(severities)
    penalty = sum(
        IMPROVEMENT_PENALTY_BY_SEVERITY.get(level, 0.0) * n for level, n in counts.items()
    )
    return round(min(penalty, IMPROVEMENT_PENALTY_CAP), 1)

# ---------------------------------------------------------------------------
# 2. Backlog 이관 금지 카테고리 (PRD §9)
#    "단순한 선택적 개선사항"만 Backlog로 이동할 수 있다.
# ---------------------------------------------------------------------------
FORBIDDEN_BACKLOG_CATEGORIES: Tuple[str, ...] = (
    "ux_improvement",         # UX 개선
    "visual_polish",          # 시각적 완성도
    "critical_edge_case",     # 중요한 edge case
    "stability",              # 안정성
    "performance",            # 성능
    "testing",                # 테스트 부족
    "usability",              # 제품 사용성
    "core_feature_detail",    # 핵심 기능의 세부 완성
    "error_handling",         # 중요한 에러 처리
)

_FORBIDDEN_BACKLOG_KEYWORDS: Tuple[str, ...] = (
    "ux", "사용성", "usability", "인체공학", "ergonom",
    "시각", "visual", "디자인", "polish", "완성도", "애니메이션", "렌더링",
    "edge case", "엣지", "경계조건", "경계 조건", "예외 시나리오",
    "안정성", "stability", "크래시", "crash", "메모리", "누수", "leak",
    "성능", "performance", "최적화", "최적", "지연", "latency", "fps",
    "테스트", "test", "e2e", "회귀", "regression", "검증",
    "에러", "error", "예외", "exception", "복구", "recovery", "방어",
    "핵심 기능", "코어", "core", "핵심 요구",
)


def is_forbidden_backlog_category(text: Optional[str] = None, category: Optional[str] = None) -> bool:
    """해당 항목이 '절대 백로그로 미루면 안 되는' 카테고리인가?"""
    if category and str(category).strip().lower().replace("-", "_") in FORBIDDEN_BACKLOG_CATEGORIES:
        return True
    low = str(text or "").lower()
    if not low:
        return False
    return any(k in low for k in _FORBIDDEN_BACKLOG_KEYWORDS)


def may_defer_to_backlog(
    text: Optional[str] = None,
    *,
    category: Optional[str] = None,
    required_by_exit_criteria: bool = False,
) -> bool:
    """항목을 차기 백로그로 이관해도 되는가?

    - 현재 마일스톤의 Exit Criteria 필수 항목 + 금지 카테고리 → 이관 불가(False).
    - 단순 선택적 개선사항 → 이관 가능(True).
    """
    if required_by_exit_criteria and is_forbidden_backlog_category(text, category):
        return False
    return True


# ---------------------------------------------------------------------------
# 3. GateResult
# ---------------------------------------------------------------------------
@dataclass
class GateResult:
    """마일스톤/최종 게이트 판정 결과."""

    passed: bool
    scope: str                       # "milestone" | "final"
    score: float = 0.0
    blocking_reasons: List[str] = field(default_factory=list)
    dimension_failures: List[str] = field(default_factory=list)
    exit_criteria_missing: List[str] = field(default_factory=list)
    # 개선점 severity 반영 결과 (P2 감점/유효 점수/P0·P1 차단)
    effective_score: float = 0.0
    improvement_penalty: float = 0.0
    improvement_blocking: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "scope": self.scope,
            "score": self.score,
            "effective_score": self.effective_score,
            "improvement_penalty": self.improvement_penalty,
            "improvement_blocking": list(self.improvement_blocking),
            "blocking_reasons": list(self.blocking_reasons),
            "dimension_failures": list(self.dimension_failures),
            "exit_criteria_missing": list(self.exit_criteria_missing),
        }

    def summary(self) -> str:
        if self.passed:
            return f"✅ [{self.scope}] PASS ({self.score:.1f}점)"
        return (
            f"🚫 [{self.scope}] FAIL ({self.score:.1f}점) — "
            + " / ".join(self.blocking_reasons)
        )


# ---------------------------------------------------------------------------
# 4. 정규화 헬퍼
# ---------------------------------------------------------------------------
def _normalize_dimensions(dimensions: Any) -> List[Tuple[str, float, bool]]:
    """다양한 형태의 차원 입력을 (name, score, is_critical) 리스트로 정규화한다.

    지원 형태:
      - {"functionality": {"score": 92, "is_critical": True}, ...}
      - {"functionality": 92, ...}
      - {"functionality": (92, True), ...}
      - QualityDimension-like 객체의 dict
    """
    out: List[Tuple[str, float, bool]] = []
    if not dimensions:
        return out
    if isinstance(dimensions, dict):
        items: Iterable = dimensions.items()
    else:
        items = dimensions  # type: ignore[assignment]

    for key, val in items:
        name = str(key)
        score = 0.0
        is_crit = False
        if isinstance(val, dict):
            score = float(val.get("score", 0.0) or 0.0)
            is_crit = bool(val.get("is_critical", False))
            name = str(val.get("name", name))
        elif isinstance(val, (tuple, list)) and len(val) >= 2:
            score = float(val[0] or 0.0)
            is_crit = bool(val[1])
        elif isinstance(val, (int, float)):
            score = float(val)
        else:
            # QualityDimension-like object
            score = float(getattr(val, "score", 0.0) or 0.0)
            is_crit = bool(getattr(val, "is_critical", False))
            name = str(getattr(val, "name", name))
        out.append((name, score, is_crit))
    return out


def _resolve_exit_criteria(exit_criteria: Any) -> Tuple[bool, List[str]]:
    """Exit Criteria 충족 여부와 미충족 항목 목록을 반환한다.

    - None → (True, []) : 미지정이면 게이트에서 강제하지 않는다(보수적 폴백).
    - bool → 그대로.
    - list/tuple → 각 항목의 met 여부 판정(미충족 텍스트 수집).
    """
    if exit_criteria is None:
        return True, []
    if isinstance(exit_criteria, bool):
        return exit_criteria, []
    if isinstance(exit_criteria, (list, tuple, set)):
        missing: List[str] = []
        for item in exit_criteria:
            if isinstance(item, dict):
                met = bool(item.get("met", item.get("passed", item.get("done", False))))
                label = str(item.get("text", item.get("label", item.get("name", "exit"))))
            else:
                met = bool(item)
                label = str(item)
            if not met:
                missing.append(label)
        return (len(missing) == 0), missing
    return bool(exit_criteria), []


def _resolve_requirements(critical_requirements_satisfied: Any) -> Tuple[bool, List[str]]:
    """핵심 요구사항 충족 여부. bool | ratio(float) | dict/list 지원."""
    if critical_requirements_satisfied is None:
        return True, []
    if isinstance(critical_requirements_satisfied, bool):
        return critical_requirements_satisfied, ([] if critical_requirements_satisfied else ["핵심 요구사항 미충족"])
    if isinstance(critical_requirements_satisfied, (int, float)):
        return (float(critical_requirements_satisfied) >= 1.0), (
            [] if float(critical_requirements_satisfied) >= 1.0 else ["핵심 요구사항 100% 미달"]
        )
    if isinstance(critical_requirements_satisfied, (list, tuple, set, dict)):
        vals = (
            list(critical_requirements_satisfied.values())
            if isinstance(critical_requirements_satisfied, dict)
            else list(critical_requirements_satisfied)
        )
        missing = [str(v) for v in vals if not (bool(v) if not isinstance(v, dict) else bool(v.get("met", False)))]
        return (len(missing) == 0), (["핵심 요구사항 미충족"] if missing else [])
    return bool(critical_requirements_satisfied), []


# ---------------------------------------------------------------------------
# 5. 핵심 게이트 평가
# ---------------------------------------------------------------------------
def evaluate_milestone_gate(
    *,
    overall_score: float,
    dimensions: Any,
    critical_requirements_satisfied: Any = True,
    exit_criteria: Any = None,
    e2e_passed: bool,
    critical_defect_count: int = 0,
    regression_passed: bool = True,
    improvement_severities: Any = None,
) -> GateResult:
    """현재 마일스톤이 PASS 조건을 만족하는지 판정한다.

    PASS 조건 (모두 만족해야 PASS):
      - overall_score >= MILESTONE_PASS_SCORE (90)  ← P2/P3 개선점 감점 반영
      - 모든 Critical Dimension >= CRITICAL_DIMENSION_MIN_SCORE (90)
      - 핵심 요구사항 100%
      - Exit Criteria 100%
      - E2E PASS
      - Critical Defect == 0
      - 회귀 PASS
      - P0/P1 개선점 0건 (90점 이상이어도 P0/P1이 남아 있으면 PASS 금지)
    """
    reasons: List[str] = []
    dims = _normalize_dimensions(dimensions)

    sev = normalize_severity_counts(improvement_severities)
    penalty = improvement_score_penalty(sev)
    blocking_imp = [
        f"{level} x{sev[level]}" for level in IMPROVEMENT_BLOCKING_SEVERITIES if sev.get(level)
    ]
    blocking_imp_total = improvement_blocking_count(sev)
    effective_score = round(float(overall_score) - penalty, 1)

    dim_failures = [
        f"{name}({score:.0f}<{CRITICAL_DIMENSION_MIN_SCORE:.0f})"
        for name, score, is_crit in dims
        if is_crit and score < CRITICAL_DIMENSION_MIN_SCORE
    ]
    req_ok, req_reasons = _resolve_requirements(critical_requirements_satisfied)
    exit_ok, exit_missing = _resolve_exit_criteria(exit_criteria)

    if effective_score < MILESTONE_PASS_SCORE:
        if penalty > 0:
            reasons.append(
                f"Overall {overall_score:.1f} - 개선 감점 {penalty:.1f} = {effective_score:.1f} "
                f"< {MILESTONE_PASS_SCORE:.0f}"
            )
        else:
            reasons.append(f"Overall {overall_score:.1f} < {MILESTONE_PASS_SCORE:.0f}")
    if blocking_imp_total:
        reasons.append(f"P0/P1 개선점 {blocking_imp_total}건 미해결({', '.join(blocking_imp)}) — 품질 게이트 FAIL")
    if dim_failures:
        reasons.append(f"Critical 차원 미달: {', '.join(dim_failures)}")
    if not req_ok:
        reasons.extend(req_reasons or ["핵심 요구사항 미충족"])
    if not exit_ok:
        reasons.append(f"Exit Criteria 미충족({len(exit_missing)}건)")
    if not e2e_passed:
        reasons.append("E2E 미통과")
    if int(critical_defect_count or 0) != 0:
        reasons.append(f"Critical Defect {int(critical_defect_count)}건")
    if not regression_passed:
        reasons.append("회귀 테스트 실패")

    return GateResult(
        passed=(len(reasons) == 0),
        scope="milestone",
        score=float(overall_score),
        blocking_reasons=reasons,
        dimension_failures=dim_failures,
        exit_criteria_missing=exit_missing,
        effective_score=effective_score,
        improvement_penalty=penalty,
        improvement_blocking=blocking_imp,
    )


def evaluate_final_gate(
    *,
    all_milestones_passed: bool,
    overall_score: float,
    dimensions: Any,
    critical_requirements_satisfied: Any = True,
    exit_criteria: Any = None,
    e2e_passed: bool,
    critical_defect_count: int = 0,
    regression_passed: bool = True,
    improvement_severities: Any = None,
) -> GateResult:
    """최종 제품 릴리즈 게이트. 마일스톤 게이트 조건에 '전체 마일스톤 PASS'를 추가한다."""
    result = evaluate_milestone_gate(
        overall_score=overall_score,
        dimensions=dimensions,
        critical_requirements_satisfied=critical_requirements_satisfied,
        exit_criteria=exit_criteria,
        e2e_passed=e2e_passed,
        critical_defect_count=critical_defect_count,
        regression_passed=regression_passed,
        improvement_severities=improvement_severities,
    )
    if not all_milestones_passed:
        result.blocking_reasons.append("전체 마일스톤 미완료")
    result.scope = "final"
    result.passed = len(result.blocking_reasons) == 0
    return result


# ---------------------------------------------------------------------------
# 6. 순차 잠금 (Sequential Lock)
# ---------------------------------------------------------------------------
def _milestone_id(ms: Any, index: int) -> str:
    if isinstance(ms, dict):
        return str(ms.get("id") or ms.get("version") or f"MS-{index}")
    return str(getattr(ms, "id", "") or getattr(ms, "version", "") or f"MS-{index}")


def _milestone_status(ms: Any) -> str:
    if isinstance(ms, dict):
        status = ms.get("status", "PENDING")
    else:
        status = getattr(ms, "status", "PENDING")
    return str(status or "PENDING").upper()


def is_milestone_passed(ms: Any) -> bool:
    """마일스톤이 PASS(=LOCKED) 되었는가?"""
    if isinstance(ms, dict):
        if bool(ms.get("passed", False)):
            return True
    return _milestone_status(ms) in PASS_STATUSES


def is_milestone_locked(ms: Any) -> bool:
    """PASS된 마일스톤은 LOCK되어 임의 변경이 금지된다."""
    return is_milestone_passed(ms)


def _is_cleared(ms: Any) -> bool:
    return _milestone_status(ms) in CLEARED_STATUSES


def first_startable_index(milestones: Sequence[Any]) -> int:
    """구현을 시작할 수 있는 첫 마일스톤 인덱스. 없으면 -1.

    이전 마일스톤이 모두 DONE/PASS/SKIPPED 되어야 해당 마일스톤이 해금된다.
    """
    for idx, ms in enumerate(milestones or []):
        if _is_cleared(ms):
            continue
        # 진행 중(IN_PROGRESS)이거나 시작 가능한 첫 미완 마일스톤
        return idx
    return -1


def can_start_milestone(milestones: Sequence[Any], target_index: int) -> bool:
    """target 인덱스의 마일스톤을 구현 대상으로 선택할 수 있는가?

    모든 선행 마일스톤이 CLEARED(DONE/PASS/SKIPPED)여야 True.
    """
    if target_index is None or target_index < 0 or target_index >= len(milestones or []):
        return False
    for prior in list(milestones)[:target_index]:
        if not _is_cleared(prior):
            return False
    return True


def milestone_lock_map(milestones: Sequence[Any]) -> Dict[str, bool]:
    """마일스톤 id → locked 여부 맵."""
    result: Dict[str, bool] = {}
    for idx, ms in enumerate(milestones or []):
        result[_milestone_id(ms, idx)] = not can_start_milestone(milestones, idx)
    return result


# ---------------------------------------------------------------------------
# 7. 마일스톤 스펙 (PRD §4) — 각 마일스톤이 반드시 가져야 하는 정보
# ---------------------------------------------------------------------------
@dataclass
class MilestoneSpec:
    """마일스톤 1개의 명확한 범위 정의 (ID/목표/요구사항/Exit Criteria/의존성)."""

    id: str
    goal: str
    required_requirements: List[str] = field(default_factory=list)
    functional: List[str] = field(default_factory=list)
    ux: List[str] = field(default_factory=list)
    quality: List[str] = field(default_factory=list)
    test_requirements: List[str] = field(default_factory=list)
    exit_criteria: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "required_requirements": list(self.required_requirements),
            "functional": list(self.functional),
            "ux": list(self.ux),
            "quality": list(self.quality),
            "test_requirements": list(self.test_requirements),
            "exit_criteria": list(self.exit_criteria),
            "dependencies": list(self.dependencies),
        }

    def to_markdown(self) -> str:
        lines = [f"### {self.id} — {self.goal}"]
        if self.required_requirements:
            lines.append("**필수 요구사항:**")
            lines += [f"- {r}" for r in self.required_requirements]
        if self.exit_criteria:
            lines.append("**Exit Criteria:**")
            lines += [f"- ✅ {c}" for c in self.exit_criteria]
        if self.dependencies:
            lines.append(f"**Dependencies:** {', '.join(self.dependencies)}")
        return "\n".join(lines)


def build_milestone_spec(
    *,
    index: int,
    version: str,
    title: str,
    tasks: Sequence[str],
    dimension: str = "",
    target_quality: str = "",
    dependencies: Optional[Sequence[str]] = None,
) -> MilestoneSpec:
    """기존 마일스톤 dict(Roadmap/ProductPlan)로부터 명시적 스펙을 도출한다."""
    tasks = [str(t) for t in (tasks or [])]
    exit_criteria = [
        "모든 기능 요구사항 구현 완료",
        "정상 시나리오 테스트 PASS",
        "경계조건(edge case) 테스트 PASS",
        "E2E 테스트 PASS",
        "Critical Defect 0건",
        f"Overall Score >= {MILESTONE_PASS_SCORE:.0f}",
        "모든 Critical Dimension >= 90",
    ]
    if target_quality:
        exit_criteria.insert(1, f"품질 목표 달성: {target_quality}")
    return MilestoneSpec(
        id=f"MS-{index + 1}",
        goal=title,
        required_requirements=tasks,
        functional=tasks,
        quality=[target_quality] if target_quality else [],
        test_requirements=["E2E PASS", "회귀 테스트 PASS"],
        exit_criteria=exit_criteria,
        dependencies=list(dependencies or []),
    )


__all__ = [
    "MILESTONE_PASS_SCORE",
    "FINAL_PASS_SCORE",
    "CRITICAL_DIMENSION_MIN_SCORE",
    "PASS_STATUSES",
    "CLEARED_STATUSES",
    "FORBIDDEN_BACKLOG_CATEGORIES",
    "IMPROVEMENT_SEVERITIES",
    "IMPROVEMENT_BLOCKING_SEVERITIES",
    "IMPROVEMENT_PENALTY_BY_SEVERITY",
    "IMPROVEMENT_PENALTY_CAP",
    "normalize_severity_counts",
    "improvement_blocking_count",
    "improvement_score_penalty",
    "GateResult",
    "MilestoneSpec",
    "is_forbidden_backlog_category",
    "may_defer_to_backlog",
    "evaluate_milestone_gate",
    "evaluate_final_gate",
    "is_milestone_passed",
    "is_milestone_locked",
    "first_startable_index",
    "can_start_milestone",
    "milestone_lock_map",
    "build_milestone_spec",
]
