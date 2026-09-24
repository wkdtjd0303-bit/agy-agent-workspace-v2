#!/usr/bin/env python3
"""
🧭 ProductRefinementGovernor (AGY Mode 3 — Depth-First Product Completion Governor)

사용자 핵심 우려 해소 모듈:
  "A, B, C 기능을 만들었고 정말 필요한 건 A1, B1, C1(기존 기능 보강)인데,
   얘는 'ABC 만들었네? 그럼 D 만들자' 하면서 마구잡이로 범위(스코프)가 늘어날 수 있지 않나?"

기존 GapSignalScanner 는 '고장 신호'(테스트 실패·Lint Error·크래시·스텁)만 탐지한다.
이 거버너는 그보다 한 단계 위의 '제품 완성도(Product Depth)' 결손을 탐지한다:

  1. interaction_polish  — 애니메이션/이징/전환/햅틱이 없어 '쫀득하지 않은' 인터랙션
  2. state_coverage      — 로딩/빈 상태/에러·재시도 UX 부재 (실사용 시 구멍)
  3. accessibility       — 콘텐츠 설명·시맨틱스·48dp 터치 타깃·고대비 부재
  4. maintainability_cs  — 전역 예외 처리·구조화 로깅 부재 (CS/유지보수 취약)
  5. commercial_polish   — 온보딩·설정·도움말·버전/피드백 동선 부재 (상품성 미달)

핵심 정책 (Depth-First Policy):
  - 기존 표면(surface)의 심도 점수(depth_score)가 기준선(bar) 미만이면,
    Commander 는 '새 기능 확장(EXPAND, D)'을 발주할 수 없고
    '기존 기능 심화(DEEPEN, A1/B1/C1)'만 발주할 수 있다.
  - 심도 기준선을 통과한 뒤에야 다음 역량으로 확장(EXPAND)할 수 있다.

이 모듈은 순수/읽기 전용이며, LLM 호출 없이 디스크 정적 분석만으로 결정한다.
따라서 할당량 차단(quota firewall)으로 LLM이 막힌 무인 모드에서도 동일하게 작동한다.
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Iterable, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from codebase_reality_inspector import CodebaseReality

logger = logging.getLogger("ProductRefinementGovernor")


# ──────────────────────────────────────────────────────────────────────────
# Static analysis token banks
# ──────────────────────────────────────────────────────────────────────────

_UI_TOKENS = (
    "@composable", "setcontentview", "findviewbyid", "activity", "composable",
    "canvas", "ondraw", "drawrect", "drawimage", "requestanimationframe",
    "<div", "<html", "<body", "react", "usestate", "useeffect",
    "fragment", "layout", "xml", "swiftui", "jetpack", "viewmodel",
    "recyclerview", "lazycolumn", "lazyverticalgrid",
)

_INTERACTION_TOKENS = (
    "animat", "animator", "transition", "tween", "easing", "easein", "easeout",
    "ease_in", "ease_out", "interpolat", "spring", "animatedvisibility",
    "animatefloat", "animatedp", "updatetransition", "motionlayout", "framer",
    "motion", "cubicbezier", "haptic", "vibrat", "feedbackgenerator", "ripple",
    "smoothstep", "lerp", "durationsmillis", "animationspec", "bounce", "scale",
    "squash", "particle", "screenshake", "screen_shake", "fling", "snap",
    "motion_duration", "animation", "animate(", ".animate", "keyframe",
    "damping", "stiffness", "overshoot", "physics", "lottie",
)

_STATE_TOKENS = (
    "loading", "isloading", "circularprogress", "linearprogress", "progressbar",
    "skeleton", "shimmer", "emptystate", "empty_state", "placeholder", "retry",
    "tryagain", "errorstate", "error_state", "nodata", "no_data",
    "contentdescription", "onerror", "fallback", "offline", "timeout",
    "로딩", "불러오는", "재시도", "오류",
)

_A11Y_TOKENS = (
    "contentdescription", "semantics", "accessibility", "importantforaccessibility",
    "aria-", "role=", "touchtarget", "48.dp", "48dp", "mintouchtarget",
    "screenreader", "talkback", "contrast", "highcontrast", "fontscale",
)

_MAINTAIN_TOKENS = (
    "try:", "try {", "try{", "except", "catch", "finally", "logging", "logger",
    "log.d", "log.e", "log.w", "console.error", "console.warn", "errorhandler",
    "onerror", "crashlytics", "telemetry", "result<", "sealed class", "throw ",
    "raise ", "error(", "error_", "wrap_exception", "traceback",
)

_COMMERCIAL_TOKENS = (
    "onboarding", "tutorial", "help", "settings", "preferences", "about",
    "version", "feedback", "rate", "share", "license", "privacy", "faq",
    "guide", "changelog", "whatsnew", "what's new", "first_run", "firstrun",
    "welcome", "tooltip", "support", "terms", "policy",
    "설정", "도움말", "문의", "약관", "버전", "피드백",
)

_IGNORED_DIRS = {
    ".git", "build", ".gradle", "node_modules", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", "dist", ".cache", "out", ".scannerwork", "coverage",
    ".gemini", ".pytest_cache", "reports",
}

_SOURCE_EXTS = (
    ".kt", ".java", ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css",
    ".vue", ".svelte", ".rs", ".go", ".c", ".cpp",
)

# ──────────────────────────────────────────────────────────────────────────
# Runtime evidence integration (Gate 5 framebuffer / Gemini Vision ↔ depth gate)
# ──────────────────────────────────────────────────────────────────────────
# QA 지적: 정적 토큰 존재 여부만으로 '쫀득함/접근성'을 판정하면, 죽은 코드나 주석에
# 토큰만 심어도 심도 게이트를 통과(게이밍)할 수 있다. 따라서 실제 렌더링/상태 전이
# 런타임 증거를 교차 검증하여 아래 두 방향으로 verdict 를 보정한다.
#   • 런타임 검증(verified): 해당 차원의 정적 결손을 해제(증거가 토큰 추정을 이긴다).
#   • 런타임 실패(failed)   : 해당 차원의 심화(DEEPEN)를 강제하고 확장(EXPAND)을 차단.
KNOWN_DEPTH_AREAS = (
    "interaction_polish", "state_coverage", "accessibility",
    "maintainability_cs", "commercial_polish",
)

_RUNTIME_EVIDENCE_REL = os.path.join("reports", "product_depth", "runtime_evidence.json")
_VISUAL_QA_REL = os.path.join("reports", "gate5", "visual_qa.json")

# ── 증거 무결성 정책 (Anti-Gaming Integrity Policy) ─────────────────────────
# D4: 증거 신선도(TTL). 24시간을 넘겼거나, 증거 기록 이후 소스가 변경되면
#     구버전 검증 이력이 현재 정적 분석을 영구 오버라이드하지 못하도록 폐기한다.
RUNTIME_EVIDENCE_TTL_SECONDS = 24 * 60 * 60
# D1: 실 픽셀 차분으로 인정할 최소 변경 면적 비율 (파일명만으로는 절대 통과 불가)
PIXEL_DIFF_MIN_AREA_RATIO = 0.0005
# 평균 시각 점수(0~100) 임계 — 이 미만이면 해당 차원 verified 를 부여하지 않는다.
MIN_VISUAL_SCORE_FOR_TRANSITION = 75.0
MIN_VISUAL_SCORE_FOR_ACCESSIBILITY = 80.0
# 'proven' 승격이 허용되는 증거 기반. 'filename' 은 fallback → 항상 runtime_partial.
_PROVEN_BASES = ("pixel", "runtime")

# 런타임 실패 결손을 다루는 계획인지 판별하기 위한 영역별 키워드 뱅크
_AREA_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "interaction_polish": (
        "애니메이션", "이징", "전환", "햅틱", "쫀득", "스프링", "리플", "모션",
        "animat", "transition", "tween", "easing", "spring", "haptic", "ripple",
        "motion", "fps", "프레임",
    ),
    "state_coverage": (
        "로딩", "빈 상태", "빈화면", "에러", "오류", "재시도", "스켈레톤", "플레이스홀더",
        "loading", "empty", "error", "retry", "skeleton", "placeholder", "offline",
    ),
    "accessibility": (
        "접근성", "콘텐츠 설명", "시맨틱", "터치 타깃", "터치타깃", "대비", "스크린리더",
        "a11y", "accessibility", "contentdescription", "semantics", "contrast",
        "touch target", "48dp", "talkback",
    ),
    "maintainability_cs": (
        "예외", "로깅", "로그", "오류 처리", "크래시", "장애", "진단", "유지보수",
        "exception", "logging", "logger", "crash", "traceback", "try", "catch",
    ),
    "commercial_polish": (
        "온보딩", "설정", "도움말", "버전", "피드백", "약관", "가이드", "튜토리얼",
        "onboarding", "settings", "help", "version", "feedback", "tutorial", "guide",
    ),
}

# Gate 5 프레임버퍼 파일명에서 상태 전이 다양성을 계측하기 위한 상태 토큰
_STATE_NAME_TOKENS = (
    "idle", "interaction", "interact", "scroll", "form", "input", "landscape",
    "multi", "high_dpi", "highdpi", "selected", "modal", "dialog", "active",
    "dark", "viewer", "search", "annotation", "empty", "error",
)


# ──────────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class SurfaceGap:
    """A product-depth gap on an already-existing user-visible surface."""
    area: str                # interaction_polish | state_coverage | accessibility | maintainability_cs | commercial_polish
    title: str               # milestone title (Korean, product-grade)
    detail: str
    evidence: str
    severity: str = "medium"  # high | medium | low
    value: float = 0.7
    risk: float = 0.2
    cost: float = 0.35
    actions: List[str] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)

    @property
    def priority(self) -> float:
        weight = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(self.severity, 0.3)
        return round((self.value * weight) / max(0.1, self.risk * self.cost), 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "area": self.area,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "severity": self.severity,
            "value": self.value,
            "risk": self.risk,
            "cost": self.cost,
            "priority": self.priority,
            "actions": list(self.actions),
            "verification": list(self.verification),
        }


@dataclass
class RuntimeEvidence:
    """Real runtime/E2E verification signals for the 5 product-depth dimensions.

    Producer: Gate 5 framebuffer captures + Gemini Vision (AndroidVisionInspector),
    or any E2E runner that calls ``record_runtime_evidence``.
    Consumer: ``ProductRefinementGovernor.evaluate`` overlays this on the static
    token analysis so the depth gate is grounded in observed behaviour, not just
    keyword presence (closes the 'static token gaming' blind spot).

    ``basis`` records *how* each verified area was proven:
      • "pixel"    — real framebuffer pixel diff / clustering (strong → proven)
      • "runtime"  — trusted programmatic E2E runner (strong → proven)
      • "filename" — screenshot file-name fallback (weak → runtime_partial only)
    """
    verified: Dict[str, str] = field(default_factory=dict)   # area -> evidence source/detail
    failed: Dict[str, str] = field(default_factory=dict)     # area -> runtime failure reason
    basis: Dict[str, str] = field(default_factory=dict)      # area -> pixel|runtime|filename
    avg_visual_score: Optional[float] = None
    state_diversity: int = 0
    sources: List[str] = field(default_factory=list)
    stale_discarded: List[str] = field(default_factory=list)
    loaded: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.verified and not self.failed

    def area_basis(self, area: str) -> str:
        """Returns the evidence basis for an area (defaults to trusted 'runtime')."""
        return self.basis.get(area) or "runtime"

    @property
    def proven_areas(self) -> List[str]:
        return [a for a in self.verified if self.area_basis(a) in _PROVEN_BASES]

    @property
    def partial_areas(self) -> List[str]:
        return [a for a in self.verified if self.area_basis(a) not in _PROVEN_BASES]


@dataclass
class DepthVerdict:
    """The governor's binding verdict for the next autonomous milestone."""
    depth_score: float = 100.0
    bar: float = 80.0
    requires_depth: bool = False
    applicable_dimensions: List[str] = field(default_factory=list)
    worst_area: str = ""
    reason: str = ""
    gap: Optional[SurfaceGap] = None
    is_fresh_project: bool = False
    runtime_verified: List[str] = field(default_factory=list)
    runtime_verified_partial: List[str] = field(default_factory=list)
    runtime_failed: List[str] = field(default_factory=list)
    stale_discarded: List[str] = field(default_factory=list)
    evidence_grade: str = "static_baseline"  # static_baseline | runtime_partial | runtime_proven | runtime_failed
    # D2: 성숙 프로젝트인데 런타임 증거가 전혀 없음 → 신규 확장(EXPAND) fail-closed.
    evidence_missing: bool = False

    @property
    def headline(self) -> str:
        if self.is_fresh_project:
            return "신규/초기 프로젝트 — 심도 게이트 비적용 (코어 역량 우선)"
        if self.requires_depth:
            return f"심도 우선 강제 (depth {self.depth_score:.0f}% < bar {self.bar:.0f}%) — 최우선 결손: {self.worst_area}"
        if self.evidence_missing:
            return (
                f"심도 정적 기준선 통과 (depth {self.depth_score:.0f}% ≥ bar {self.bar:.0f}%) "
                "— 단, 런타임 증거 부재로 신규 확장(EXPAND) 보류"
            )
        return f"심도 기준선 통과 (depth {self.depth_score:.0f}% ≥ bar {self.bar:.0f}%) — 확장(EXPAND) 허용"

    @property
    def evidence_grade_label(self) -> str:
        return {
            "static_baseline": "정적 토큰 분석 (런타임 증거 미연결)",
            "runtime_partial": "런타임 증거 부분 검증 (파일명 fallback 포함 — Proven 미달)",
            "runtime_proven": "런타임 프레임버퍼/상태전이 증거로 전 차원 검증 완료",
            "runtime_failed": "런타임 실패 감지 — 해당 결손 심화(DEEPEN) 강제",
        }.get(self.evidence_grade, self.evidence_grade)


# ──────────────────────────────────────────────────────────────────────────
# Governor
# ──────────────────────────────────────────────────────────────────────────

class ProductRefinementGovernor:
    """Depth-First product completion gate for Commander's autonomous planning."""

    DEPTH_BAR = 80.0
    MIN_SURFACE_FILES = 2
    MAX_FILE_BYTES = 250_000
    MAX_FILES = 3000

    DIMENSION_WEIGHTS: Dict[str, float] = {
        "interaction_polish": 0.30,
        "state_coverage": 0.20,
        "accessibility": 0.20,
        "maintainability_cs": 0.20,
        "commercial_polish": 0.10,
    }

    # ── Public API ─────────────────────────────────────────────────────────

    @classmethod
    def evaluate(
        cls,
        workspace: Optional[str],
        domain: str = "",
        reality: Optional["CodebaseReality"] = None,
        target_version: str = "v1.0",
        runtime_evidence: Optional[RuntimeEvidence] = None,
        auto_load_evidence: bool = True,
    ) -> DepthVerdict:
        """Analyzes the existing surface and returns a binding depth-first verdict.

        When runtime evidence (Gate 5 framebuffer / Gemini Vision / E2E) is available,
        it is overlaid on the static token analysis: verified dimensions are cleared and
        runtime-failed dimensions force a DEEPEN, so the gate cannot be bypassed by
        merely planting tokens in dead code or comments.
        """
        source_files: List[str] = []
        if reality is not None and getattr(reality, "source_files", None):
            source_files = list(reality.source_files)

        if not workspace or not os.path.isdir(workspace):
            return DepthVerdict(is_fresh_project=True, reason="워크스페이스 무효 — 심도 게이트 비적용")

        is_empty = bool(getattr(reality, "is_empty", False)) if reality is not None else False
        if not source_files:
            source_files = cls._list_source_files(workspace)
        if is_empty or len(source_files) < cls.MIN_SURFACE_FILES:
            return DepthVerdict(
                is_fresh_project=True,
                reason=f"실구현 소스 {len(source_files)}개 — 코어 역량 구축이 우선 (심도 게이트 비적용)",
                depth_score=100.0,
            )

        files = cls._read_sources(workspace, source_files)
        if not files:
            return DepthVerdict(is_fresh_project=True, reason="소스 판독 실패 — 심도 게이트 비적용")

        has_ui = cls._has_ui_surface(files)
        applicable = cls._applicable_dimensions(files, has_ui=has_ui)
        gaps = cls._analyze(files, has_ui=has_ui, domain=domain)

        # [Runtime Cross-Verification] 실제 렌더링/상태 전이 증거로 정적 분석을 교차 검증한다.
        evidence = runtime_evidence
        if evidence is None and auto_load_evidence:
            evidence = cls.load_runtime_evidence(workspace)
        runtime_verified: List[str] = []
        runtime_failed: List[str] = []
        runtime_partial: List[str] = []
        if evidence is not None and (evidence.verified or evidence.failed):
            gaps, applicable, runtime_verified, runtime_failed, runtime_partial = cls._apply_runtime_evidence(
                gaps, applicable, evidence
            )

        score = cls._depth_score(gaps, applicable)
        worst = max(gaps, key=lambda g: g.priority) if gaps else None

        requires = bool(gaps) and score < cls.DEPTH_BAR
        proven = [a for a in runtime_verified if a not in runtime_partial]
        grade = cls._evidence_grade(applicable, runtime_verified, runtime_failed, proven)
        # D2: 성숙 UI 프로젝트인데 런타임 증거가 전혀 없으면(정적 기준선) 신규 확장을
        #     fail-closed 로 보류한다 (SOC-FAIL-OPEN 금지). 신규/비UI 프로젝트는 예외.
        evidence_missing = bool(has_ui) and grade == "static_baseline"
        stale = list(getattr(evidence, "stale_discarded", []) or []) if evidence is not None else []
        if evidence_missing:
            reason_tail = (
                "런타임 증거 부재로 신규 확장(EXPAND)은 승인되지 않습니다"
                "(게이밍 리스크 — Gate 5 실기기 검증 필요)."
            )
        else:
            reason_tail = "기존 기능 심화(DEEPEN)를 강제합니다." if requires else "확장(EXPAND)이 허용됩니다."
        reason = (
            f"기존 표면 {len(source_files)}개 파일의 제품 심도 {score:.1f}% "
            f"(기준선 {cls.DEPTH_BAR:.0f}%, 검증등급 {grade}). "
            + reason_tail
        )
        return DepthVerdict(
            depth_score=score,
            bar=cls.DEPTH_BAR,
            requires_depth=requires,
            applicable_dimensions=applicable,
            worst_area=worst.area if worst else "",
            reason=reason,
            gap=worst if requires else None,
            runtime_verified=runtime_verified,
            runtime_verified_partial=runtime_partial,
            runtime_failed=runtime_failed,
            stale_discarded=stale,
            evidence_grade=grade,
            evidence_missing=evidence_missing,
        )

    @classmethod
    def classify(cls, title: str, tasks: Optional[Iterable[str]] = None,
                 components: Optional[Iterable[str]] = None) -> str:
        """Classifies a candidate milestone as 'deepen' | 'expand' | 'neutral'."""
        text = f"{title or ''} {' '.join(list(tasks or []))}".lower()
        if not text.strip():
            return "neutral"

        comps = [str(c).lower() for c in (components or []) if len(str(c)) >= 4]
        touches_existing = any(c in text for c in comps)

        deepen_kw = (
            "개선", "보강", "튜닝", "완성", "최적화", "폴리시", "polish", "refine",
            "하드닝", "hardening", "리팩토링", "refactor", "안정", "회귀", "버그",
            "수정", "보완", "고도화", "정밀", "쫀득", "애니메이션", "접근성", "로딩",
            "에러", "예외", "로깅", "ux", "마감", "심화", "리파인", "감수", "유지보수",
            "인터랙션", "사용성", "일관성", "품질", "다듬", "정돈", "커버리지", "방어",
        )
        expand_kw = (
            "추가", "신규", "새로운", "도입", "새 기능", "new feature", "add ",
            "시스템 구축", "기능 구축", "확장", "지원 시작", "새로",
        )
        deepen_score = sum(1 for k in deepen_kw if k in text)
        expand_score = sum(1 for k in expand_kw if k in text)

        # 1. 명백한 신규 확장 의도가 담긴 강한 키워드 (Scope Creep 핵심 주범)
        strong_expand_kw = ("신규", "새로운", "새 기능", "new feature", "도입")
        if any(k in text for k in strong_expand_kw):
            return "expand"

        # 2. 확장 키워드가 우세하거나 같으면(혼합된 경우) 신규 확장(EXPAND)으로 판정하여 우회 방지
        if expand_score >= deepen_score and expand_score > 0:
            return "expand"
            
        if touches_existing:
            return "deepen"
        if deepen_score > 0:
            return "deepen"
        return "neutral"

    @classmethod
    def validate_plan(
        cls,
        title: str,
        tasks: Optional[Iterable[str]],
        verdict: DepthVerdict,
        components: Optional[Iterable[str]] = None,
    ) -> tuple[bool, str]:
        """Returns (ok, reason). Rejects breadth expansion while depth is required."""
        if verdict is None:
            return True, "심도 게이트 비적용"
        kind = cls.classify(title, tasks, components)
        # D2 fail-closed: 런타임 증거가 전혀 없는 상태에서는 신규 확장을 승인하지 않는다.
        # (정적 토큰만으로 확장을 허용하면 게이밍에 뚫린다 — SOC-FAIL-OPEN 금지)
        if getattr(verdict, "evidence_missing", False) and kind == "expand":
            return False, (
                "런타임 증거 부재 상태에서 신규 확장(EXPAND)은 승인되지 않습니다(게이밍 리스크). "
                "Gate 5 실기기 프레임버퍼/비전 검증을 먼저 수행하거나 기존 기능 심화(DEEPEN)로 대체해야 합니다."
            )
        if not verdict.requires_depth:
            return True, "심도 게이트 비적용"
        if kind == "expand":
            return False, (
                f"심도 우선 정책 위반: 기존 표면 심도 {verdict.depth_score:.0f}% < {verdict.bar:.0f}% 인데 "
                f"'{title}' 는 신규 확장(EXPAND)으로 판정됨. 기존 기능 심화(DEEPEN)로 대체해야 함."
            )
        # [Runtime Failure Targeting] 런타임에서 실제로 실패한 결손은 반드시 계획에 포함되어야 한다.
        failed = list(getattr(verdict, "runtime_failed", []) or [])
        if failed:
            plan_text = f"{title or ''} {' '.join(str(t) for t in (tasks or []))}".lower()
            addressed = any(
                any(k in plan_text for k in _AREA_KEYWORDS.get(area, ()))
                for area in failed
            )
            if not addressed:
                return False, (
                    f"런타임 실패 결손 {failed} 을(를) 다루지 않는 계획입니다. "
                    f"실기기 프레임버퍼/비전 검증에서 실패한 차원을 우선 심화(DEEPEN)해야 합니다."
                )
        return True, f"심도 우선 정책 준수 ({kind})"

    # ── Runtime evidence (Gate 5 ↔ depth gate) ──────────────────────────────

    @classmethod
    def record_runtime_evidence(
        cls,
        workspace: str,
        area: str,
        status: str,
        detail: str = "",
        source: str = "",
        basis: str = "runtime",
    ) -> Optional[str]:
        """Persists a runtime verification/failure signal into the canonical evidence file.

        status: "verified" | "failed". Returns the written path, or None on invalid input.
        basis : "pixel" (real framebuffer diff), "runtime" (trusted E2E), or
                "filename" (weak fallback → never promoted to ``runtime_proven``).
        """
        area = (area or "").strip()
        status = (status or "").strip().lower()
        basis = (basis or "runtime").strip().lower()
        if basis not in ("pixel", "runtime", "filename"):
            basis = "runtime"
        if area not in KNOWN_DEPTH_AREAS or status not in ("verified", "failed"):
            logger.warning(f"[Governor] 잘못된 런타임 증거 기록 요청: area={area}, status={status}")
            return None
        if not workspace or not os.path.isdir(workspace):
            return None
        path = os.path.join(workspace, _RUNTIME_EVIDENCE_REL)
        data: Dict[str, Any] = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        bucket = data.setdefault(status, {})
        bucket[area] = {
            "detail": detail,
            "source": source,
            "basis": basis,
            "updated_at": __import__("time").time(),
        }
        data["last_updated"] = __import__("time").time()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            logger.info(f"[Governor] 런타임 증거 기록 [{status}] {area} (basis={basis}) -> {path}")
            return path
        except Exception as e:
            logger.warning(f"[Governor] 런타임 증거 기록 실패: {e}")
            return None

    @classmethod
    def load_runtime_evidence(cls, workspace: Optional[str]) -> RuntimeEvidence:
        """Loads runtime evidence from the canonical file and auto-derives from Gate 5 visual_qa.json."""
        ev = RuntimeEvidence()
        if not workspace or not os.path.isdir(workspace):
            return ev

        import time as _time
        now = _time.time()
        newest_src = cls._newest_source_mtime(workspace)

        canonical = os.path.join(workspace, _RUNTIME_EVIDENCE_REL)
        if os.path.isfile(canonical):
            try:
                with open(canonical, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                for status in ("verified", "failed"):
                    for area, rec in (data.get(status) or {}).items():
                        if area not in KNOWN_DEPTH_AREAS:
                            continue
                        rec = rec or {}
                        stale = cls._is_stale(rec.get("updated_at"), now, newest_src)
                        if stale:
                            ev.stale_discarded.append(area)
                            logger.warning(
                                f"[Governor] 런타임 증거 폐기 [{status}] {area}: {stale} ({canonical})"
                            )
                            continue
                        detail = str(rec.get("detail") or rec.get("source") or status)
                        basis = str(rec.get("basis") or "runtime")
                        if status == "verified":
                            cls._mark_verified(ev, area, detail, basis)
                        else:
                            ev.failed[area] = detail
                ev.sources.append(canonical)
            except Exception as e:
                logger.warning(f"[Governor] 런타임 증거 판독 실패: {e}")

        # Gate 5 visual_qa.json 자동 파생 (프레임버퍼 상태 전이/비전 점수)
        vqa = os.path.join(workspace, _VISUAL_QA_REL)
        if os.path.isfile(vqa):
            try:
                with open(vqa, "r", encoding="utf-8") as f:
                    vqa_data = json.load(f) or {}
                stale = cls._is_stale(cls._visual_qa_timestamp(vqa_data, vqa), now, newest_src)
                if stale:
                    ev.stale_discarded.append("visual_qa")
                    logger.warning(f"[Governor] Gate5 visual_qa 증거 폐기: {stale} ({vqa})")
                else:
                    cls._derive_evidence_from_visual_qa(vqa_data, vqa, ev)
                    ev.sources.append(vqa)
            except Exception as e:
                logger.warning(f"[Governor] visual_qa 파생 실패: {e}")

        if ev.stale_discarded:
            logger.warning(
                f"[Governor] 만료/무효 런타임 증거 {len(ev.stale_discarded)}건 폐기: {ev.stale_discarded}"
            )
        ev.loaded = bool(ev.verified or ev.failed or ev.sources)
        if not ev.loaded and (os.path.isfile(canonical) or os.path.isfile(vqa)):
            logger.warning("[Governor] 증거 파일은 존재하나 유효한 런타임 증거가 없습니다 (static_baseline 유지).")
        return ev

    # ── Evidence integrity helpers (freshness / basis) ──────────────────────

    @classmethod
    def _newest_source_mtime(cls, workspace: str) -> Optional[float]:
        """Newest mtime among real source files (invalidates pre-change evidence)."""
        newest: Optional[float] = None
        try:
            for rel in cls._list_source_files(workspace):
                try:
                    m = os.path.getmtime(os.path.join(workspace, rel))
                except Exception:
                    continue
                if newest is None or m > newest:
                    newest = m
        except Exception:
            return None
        return newest

    @staticmethod
    def _is_stale(
        updated_at: Optional[float], now: float, newest_source_mtime: Optional[float]
    ) -> Optional[str]:
        """Returns a stale reason if the evidence must be discarded, else None."""
        if updated_at is None:
            return "타임스탬프 없음 — 신선도 검증 불가"
        try:
            ts = float(updated_at)
        except (TypeError, ValueError):
            return "타임스탬프 형식 오류"
        if now - ts > RUNTIME_EVIDENCE_TTL_SECONDS:
            return f"TTL({RUNTIME_EVIDENCE_TTL_SECONDS // 3600}h) 만료"
        if newest_source_mtime is not None and newest_source_mtime > ts + 1.0:
            return "증거 기록 이후 소스 변경됨(재검증 필요)"
        return None

    @staticmethod
    def _visual_qa_timestamp(vqa_data: Dict[str, Any], path: str) -> Optional[float]:
        """Best-effort freshness timestamp for a Gate 5 visual_qa.json payload."""
        ts = vqa_data.get("timestamp")
        if isinstance(ts, str) and ts:
            try:
                import datetime as _dt
                return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
        try:
            return os.path.getmtime(path)
        except Exception:
            return None

    @staticmethod
    def _source_attests_pixel_basis(source: str) -> bool:
        """True only when the cited Gate 5 report actually contains real pixel evidence.

        Prevents a caller from merely *claiming* ``basis="pixel"`` without a genuine
        framebuffer diff: the report must declare ``evidence_basis == "pixel"`` or carry
        an authentic state-transition / multi-frame pixel-clustering result.
        """
        if not source or not os.path.isfile(source):
            return False
        try:
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            return False
        if str(data.get("evidence_basis", "")).lower() == "pixel":
            return True
        te = data.get("state_transition_evidence") or {}
        se = data.get("state_diversity_evidence") or {}
        if isinstance(te, dict) and te.get("authentic") and te.get("has_real_diff"):
            return True
        try:
            return int(se.get("authentic_frames", 0) or 0) >= 2
        except (TypeError, ValueError, AttributeError):
            return False

    @classmethod
    def _mark_verified(cls, ev: RuntimeEvidence, area: str, detail: str, basis: str) -> None:
        """Sets verified evidence with monotonic basis strength (never downgrade proven→filename)."""
        basis = basis if basis in ("pixel", "runtime", "filename") else "runtime"
        prev_basis = ev.basis.get(area)
        if area in ev.verified and prev_basis in _PROVEN_BASES and basis not in _PROVEN_BASES:
            return  # 기존 proven 증거를 weak fallback 으로 덮어쓰지 않는다.
        if area not in ev.verified or basis in _PROVEN_BASES:
            ev.verified[area] = detail
            ev.basis[area] = basis

    @classmethod
    def _derive_evidence_from_visual_qa(
        cls, vqa_data: Dict[str, Any], source_path: str, ev: RuntimeEvidence
    ) -> None:
        """Derives per-dimension runtime evidence from a Gate 5 visual_qa.json payload.

        D1: when the payload carries real pixel evidence (``state_diversity`` /
        ``state_transition_verified`` / ``evidence_basis == 'pixel'``) it is trusted as
        proven. Otherwise it falls back to screenshot file-name tokens, which are
        recorded as weak ``filename`` basis and can never be promoted to proven.
        """
        score = vqa_data.get("average_score")
        try:
            score_val = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_val = None
        if score_val is not None:
            ev.avg_visual_score = score_val

        declared_basis = str(vqa_data.get("evidence_basis", "")).lower()
        try:
            real_state_count = int(vqa_data.get("state_diversity") or 0)
        except (TypeError, ValueError):
            real_state_count = 0
        real_transition = bool(vqa_data.get("state_transition_verified"))

        # 파일명 fallback (실 픽셀 증거가 없을 때만 사용)
        shot_names: List[str] = []
        for s in (vqa_data.get("screenshots") or []):
            if isinstance(s, str):
                shot_names.append(os.path.basename(s).lower())
        for c in (vqa_data.get("component_crops") or []):
            if isinstance(c, str):
                shot_names.append(os.path.basename(c).lower())
        state_tokens = set()
        for name in shot_names:
            for tok in _STATE_NAME_TOKENS:
                if tok in name:
                    state_tokens.add(tok)
        filename_state_count = len(state_tokens)

        if declared_basis == "pixel" or real_state_count or real_transition:
            state_count = max(real_state_count, 0)
            state_basis = "pixel" if (declared_basis == "pixel" or real_state_count) else "filename"
            transition_basis = "pixel" if real_transition else "filename"
            has_transition = bool(real_transition)
        else:
            state_count = filename_state_count
            state_basis = "filename"
            transition_basis = "filename"
            has_transition = False

        if state_count:
            ev.state_diversity = max(ev.state_diversity, state_count)

        verdict = str(vqa_data.get("verdict", "")).upper()
        anti_forgery = str(vqa_data.get("anti_forgery_audit", "")).upper()
        authentic = verdict == "PASS" or anti_forgery.startswith("PASS")

        # 인터랙션 쫀득화: 실 픽셀 상태 전이가 검증된 경우에만 proven.
        if authentic and has_transition and (score_val is None or score_val >= MIN_VISUAL_SCORE_FOR_TRANSITION):
            cls._mark_verified(
                ev, "interaction_polish",
                f"Gate 5 실 픽셀 상태 전이 검증 (basis={transition_basis}) ({source_path})",
                transition_basis,
            )
        elif authentic and filename_state_count >= 2:
            cls._mark_verified(
                ev, "interaction_polish",
                f"Gate 5 파일명 fallback 상태 {filename_state_count}종 (partial) ({source_path})",
                "filename",
            )
        # 상태 커버리지: 3종 이상 상태 캡처 (실 픽셀 클러스터링 또는 파일명 fallback)
        if authentic and state_count >= 3:
            cls._mark_verified(
                ev, "state_coverage",
                f"Gate 5 다중 UI 상태 {state_count}종 (basis={state_basis}) ({source_path})",
                state_basis,
            )
        # 접근성: 비전 감사가 터치 타깃/대비를 합격 판정
        if authentic and score_val is not None and score_val >= MIN_VISUAL_SCORE_FOR_ACCESSIBILITY:
            score_basis = "pixel" if (declared_basis == "pixel" or real_transition) else "filename"
            cls._mark_verified(
                ev, "accessibility",
                f"Gemini Vision 시각 품질 {score_val:.0f}/100 (터치 타깃·대비 합격) ({source_path})",
                score_basis,
            )
        # 런타임 실패: 비전 점수가 명백히 미달하면 폴리시 심화를 강제
        if score_val is not None and score_val < 60:
            ev.failed.setdefault(
                "interaction_polish",
                f"Gate 5 Gemini Vision 시각 품질 {score_val:.0f}/100 — 폴리시 미달 ({source_path})",
            )

    @classmethod
    def sync_from_vision_report(
        cls,
        workspace: str,
        avg_score: Optional[float] = None,
        state_count: int = 0,
        has_state_transition: bool = False,
        source: str = "",
        state_basis: str = "pixel",
        transition_basis: str = "pixel",
        score_basis: str = "pixel",
    ) -> Dict[str, str]:
        """Records runtime depth evidence derived from a live Gate 5 vision run.

        Called by AndroidVisionInspector after a genuine framebuffer + Gemini Vision audit.
        ``*_basis`` marks how each signal was proven: "pixel" (real framebuffer diff —
        proven), "runtime" (trusted E2E — proven), or "filename" (weak fallback →
        runtime_partial only, never promoted to proven).

        Returns a mapping of {area: "verified"|"failed"} for observability.
        """
        recorded: Dict[str, str] = {}
        if not workspace or not os.path.isdir(workspace):
            logger.warning("[Governor] sync_from_vision_report: 유효하지 않은 워크스페이스 — 증거 미기록")
            return recorded
        # [Anti-Gaming Attestation] 'pixel' 기반 주장은 인용된 Gate5 리포트가 실제 픽셀
        # 증거(evidence_basis=pixel / authentic 프레임)를 담고 있을 때만 인정한다.
        if "pixel" in (state_basis, transition_basis, score_basis):
            if not cls._source_attests_pixel_basis(source):
                logger.warning(
                    "[Governor] pixel 기반 증거 주장이 인용 리포트에서 입증되지 않음 → "
                    f"filename(partial) 으로 하향 (source={source})"
                )
                state_basis = transition_basis = score_basis = "filename"
        if has_state_transition and (avg_score is None or avg_score >= MIN_VISUAL_SCORE_FOR_TRANSITION):
            if cls.record_runtime_evidence(
                workspace, "interaction_polish", "verified",
                f"실기기 상태 전이(State1→State2) 프레임버퍼 대조 완료 (basis={transition_basis})",
                source, basis=transition_basis,
            ):
                recorded["interaction_polish"] = "verified"
        if state_count >= 3:
            if cls.record_runtime_evidence(
                workspace, "state_coverage", "verified",
                f"다중 UI 상태 {state_count}종 프레임버퍼 캡처 (basis={state_basis})",
                source, basis=state_basis,
            ):
                recorded["state_coverage"] = "verified"
        if avg_score is not None and avg_score >= MIN_VISUAL_SCORE_FOR_ACCESSIBILITY:
            if cls.record_runtime_evidence(
                workspace, "accessibility", "verified",
                f"Gemini Vision 시각 품질 {avg_score:.0f}/100 (터치 타깃·대비 합격) (basis={score_basis})",
                source, basis=score_basis,
            ):
                recorded["accessibility"] = "verified"
        if avg_score is not None and avg_score < 60:
            if cls.record_runtime_evidence(
                workspace, "interaction_polish", "failed",
                f"Gemini Vision 시각 품질 {avg_score:.0f}/100 — 시각적 완성도 미달", source,
                basis=score_basis,
            ):
                recorded["interaction_polish"] = "failed"
        if not recorded:
            logger.warning(
                "[Governor] Gate5 비전 리포트에서 유효한 심도 증거가 파생되지 않았습니다 "
                f"(state_count={state_count}, transition={has_state_transition}, avg_score={avg_score})."
            )
        return recorded

    @classmethod
    def _apply_runtime_evidence(
        cls,
        gaps: List[SurfaceGap],
        applicable: List[str],
        evidence: RuntimeEvidence,
    ) -> Tuple[List[SurfaceGap], List[str], List[str], List[str], List[str]]:
        """Overlays runtime evidence.

        Only PROVEN evidence (real pixel / trusted runtime) clears a static gap.
        Weak ``filename`` fallback evidence is reported but deliberately does NOT
        clear a gap, so a screenshot file name alone cannot unblock expansion (D1).
        """
        verified = [a for a in evidence.verified if a in KNOWN_DEPTH_AREAS]
        proven = {a for a in verified if evidence.area_basis(a) in _PROVEN_BASES}
        partial = [a for a in verified if a not in proven]
        failed = [a for a in evidence.failed if a in KNOWN_DEPTH_AREAS]

        revised: List[SurfaceGap] = []
        for g in gaps:
            if g.area in proven:
                continue  # 실 픽셀/런타임 증거가 정적 결손을 해제
            if g.area in partial:
                g.evidence = f"[RUNTIME PARTIAL — proven 미달] {evidence.verified[g.area]} | {g.evidence}"
            if g.area in evidence.failed:
                g.severity = "high"
                g.evidence = f"[RUNTIME FAILED] {evidence.failed[g.area]} | {g.evidence}"
            revised.append(g)

        existing = {g.area for g in revised}
        for area in failed:
            if area not in applicable:
                applicable.append(area)
            if area not in existing:
                revised.append(cls._forced_runtime_gap(area, evidence.failed[area]))
        return revised, applicable, verified, failed, partial

    @classmethod
    def _forced_runtime_gap(cls, area: str, reason: str) -> SurfaceGap:
        titles = {
            "interaction_polish": "런타임 검증 실패 복구: 인터랙션 쫀득화(모션·이징·햅틱) 정밀 재작업",
            "state_coverage": "런타임 검증 실패 복구: 로딩·빈 상태·에러/재시도 UX 완성",
            "accessibility": "런타임 검증 실패 복구: 접근성(콘텐츠 설명·48dp 터치·고대비) 완성",
            "maintainability_cs": "런타임 검증 실패 복구: 예외 처리·구조화 로깅·장애 진단 표준화",
            "commercial_polish": "런타임 검증 실패 복구: 온보딩·설정·도움말·피드백 동선 완성",
        }
        return SurfaceGap(
            area=area,
            title=titles.get(area, f"런타임 검증 실패 복구: {area}"),
            detail="실기기 프레임버퍼/비전 검증에서 해당 차원의 런타임 실패가 감지되었습니다.",
            evidence=f"[RUNTIME FAILED] {reason}",
            severity="high", value=0.95, risk=0.2, cost=0.3,
            actions=[
                "런타임 실패가 감지된 차원을 실제 렌더링/동작 기준으로 재작업",
                "재작업 후 동일 프레임버퍼/상태 전이 시나리오로 재검증",
                "기존 기능 회귀(Zero-Regression) 0건 검증",
            ],
            verification=[
                "런타임 재검증에서 해당 차원 실패 0건",
                "실기기 프레임버퍼/상태 전이 증거 재생성",
            ],
        )

    @classmethod
    def runtime_verification_gap(cls, verdict: Optional[DepthVerdict] = None) -> SurfaceGap:
        """D2 fail-closed milestone: prove the surface at runtime before any EXPAND.

        Emitted when a mature UI project has only static-token evidence, so a new
        breadth feature (D) cannot be approved until real framebuffer/vision proof
        is captured (Gate 5). This is the deterministic counterpart of
        ``validate_plan``'s EXPAND rejection.
        """
        return SurfaceGap(
            area="interaction_polish",
            title="Gate 5 실기기 런타임 검증 확정: 상태 전이·터치 타깃·대비 실측 증거 확보",
            detail=(
                "정적 토큰 분석만으로는 '쫀득한 동작/실사용 완성도'를 입증할 수 없습니다. "
                "신규 확장(EXPAND) 전에 실기기 프레임버퍼 상태 전이·Gemini Vision 품질 증거를 확정해야 합니다."
            ),
            evidence=(
                "[RUNTIME EVIDENCE MISSING] 정적 기준선(static_baseline) — "
                f"런타임 검증 등급 {getattr(verdict, 'evidence_grade', 'static_baseline')}"
            ),
            severity="high", value=0.9, risk=0.2, cost=0.35,
            actions=[
                "실기기(또는 헤드리스 에뮬레이터)에서 Idle→Interaction 상태 전이 프레임버퍼를 실제 캡처",
                "UIVisualDiffer 실 픽셀 차분으로 상태 전이를 검증(파일명 아님)하고 Gate 5 visual_qa.json 생성",
                "Gemini Vision으로 터치 타깃·대비·타이포 품질 점수를 산출해 심도 거버너에 기록",
            ],
            verification=[
                "runtime_evidence.json 에 basis=pixel/runtime 증거가 기록됨",
                "evidence_grade 가 static_baseline 을 벗어남(runtime_partial 이상)",
                "기존 기능 회귀(Zero-Regression) 0건",
            ],
        )

    @classmethod
    def _evidence_grade(
        cls,
        applicable: List[str],
        verified: List[str],
        failed: List[str],
        proven: Optional[List[str]] = None,
    ) -> str:
        if failed:
            return "runtime_failed"
        if verified:
            proven_set = set(proven if proven is not None else verified)
            if set(applicable) and set(applicable) <= proven_set:
                return "runtime_proven"
            return "runtime_partial"
        return "static_baseline"

    @classmethod
    def to_brief(cls, verdict: DepthVerdict) -> str:
        """Compact brief for injection into Commander's LLM planning prompt."""
        if verdict is None or verdict.is_fresh_project:
            return "[DEPTH-FIRST GOVERNOR]: 신규/초기 프로젝트 — 코어 역량 구축을 우선하십시오."
        lines = [
            "[DEPTH-FIRST GOVERNOR — 제품 심도 우선 정책]",
            f"• 기존 표면 심도 점수: {verdict.depth_score:.1f}% (기준선 {verdict.bar:.0f}%)",
            f"• 런타임 교차검증 등급: {verdict.evidence_grade_label}"
            + (f" (verified={verdict.runtime_verified})" if verdict.runtime_verified else "")
            + (f" (partial={verdict.runtime_verified_partial})" if verdict.runtime_verified_partial else "")
            + (f" (failed={verdict.runtime_failed})" if verdict.runtime_failed else ""),
            f"• 판정: {verdict.headline}",
        ]
        if verdict.stale_discarded:
            lines.append(
                f"• ⚠️ 신선도(TTL/소스변경) 만료로 폐기된 증거: {verdict.stale_discarded} — 재검증 필요."
            )
        if verdict.evidence_missing:
            lines.append(
                "• 🚫 [런타임 증거 없음 — 신규 확장 승인 불가(게이밍 리스크)] "
                "정적 토큰만으로는 확장(EXPAND)을 승인하지 않습니다. "
                "Gate 5 실기기 프레임버퍼/비전 검증을 먼저 통과하거나 기존 기능 심화(DEEPEN)만 발주하십시오."
            )
        if verdict.runtime_failed:
            lines.append(
                "• 런타임 실패 강제 규칙: 실기기 프레임버퍼/비전에서 실패한 차원"
                f" {verdict.runtime_failed} 을(를) 반드시 이번 마일스톤에 포함해 재작업하십시오."
            )
        if verdict.gap is not None:
            g = verdict.gap
            lines.append(
                f"• 최우선 심화 결손 [{g.area}/{g.severity}] {g.title} "
                f"(priority={g.priority}, evidence={g.evidence})"
            )
            lines.append(
                "• 강제 규칙: 심도 기준선 미달 동안 신규 기능 확장(D) 금지. "
                "기존 기능을 A1/B1/C1 으로 심화(DEEPEN)하는 단 하나의 마일스톤만 발주하십시오."
            )
        elif not verdict.evidence_missing:
            lines.append("• 심도 기준선을 통과했으므로 다음 미구현 역량으로 확장(EXPAND)할 수 있습니다.")
        return "\n".join(lines)

    # ── Analysis internals ─────────────────────────────────────────────────

    @classmethod
    def _list_source_files(cls, workspace: str) -> List[str]:
        out: List[str] = []
        count = 0
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS and not d.startswith(".")]
            for fname in files:
                if fname.lower().endswith(_SOURCE_EXTS):
                    rel = os.path.relpath(os.path.join(root, fname), workspace)
                    low = rel.lower()
                    if "test" in low or "spec" in os.path.basename(low):
                        continue
                    out.append(rel)
                    count += 1
                    if count >= cls.MAX_FILES:
                        return out
        return out

    @classmethod
    def _read_sources(cls, workspace: str, source_files: Iterable[str]) -> Dict[str, str]:
        files: Dict[str, str] = {}
        for rel in source_files:
            path = os.path.join(workspace, rel)
            try:
                if os.path.getsize(path) > cls.MAX_FILE_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    files[rel] = f.read()
            except Exception:
                continue
        return files

    @staticmethod
    def _has_ui_surface(files: Dict[str, str]) -> bool:
        blob = " ".join(files.values()).lower()
        return any(tok in blob for tok in _UI_TOKENS)

    @staticmethod
    def _blob(files: Dict[str, str]) -> str:
        return " ".join(files.values()).lower()

    @classmethod
    def _applicable_dimensions(cls, files: Dict[str, str], has_ui: bool) -> List[str]:
        applicable: List[str] = []
        if has_ui:
            applicable.extend(["interaction_polish", "state_coverage", "accessibility"])
        if len(files) >= 2:
            applicable.append("maintainability_cs")
        if has_ui or len(files) >= 3:
            applicable.append("commercial_polish")
        return applicable

    @classmethod
    def _depth_score(cls, gaps: List[SurfaceGap], applicable: List[str]) -> float:
        if not applicable:
            return 100.0
        total_w = sum(cls.DIMENSION_WEIGHTS.get(d, 0.1) for d in applicable)
        gap_areas = {g.area for g in gaps}
        lost_w = sum(cls.DIMENSION_WEIGHTS.get(d, 0.1) for d in applicable if d in gap_areas)
        return round(max(0.0, 100.0 * (1.0 - lost_w / max(0.01, total_w))), 1)

    @classmethod
    def _analyze(cls, files: Dict[str, str], has_ui: bool, domain: str) -> List[SurfaceGap]:
        blob = cls._blob(files)
        gaps: List[SurfaceGap] = []

        def hits(tokens) -> int:
            return sum(1 for t in tokens if t in blob)

        # 1. interaction polish — "쫀득한 동작" 결손 (단순 존재를 넘어 충분한 모션 밀도 검증)
        min_interaction = 1 if len(files) <= 2 else min(3, max(2, len(files) // 3))
        if has_ui and hits(_INTERACTION_TOKENS) < min_interaction:
            gaps.append(SurfaceGap(
                area="interaction_polish",
                title="기존 기능 인터랙션 쫀득화: 모션 이징·전환 애니메이션·촉각 피드백 정밀 튜닝",
                detail="UI 표면은 존재하나 애니메이션/이징/전환/햅틱/스프링 물리 토큰이 부족하여 동작이 '툭툭 끊기고 정적'으로 보입니다.",
                evidence=f"UI 표면 {len(files)}개 대비 인터랙션 토큰 {hits(_INTERACTION_TOKENS)}건 (권장 ≥{min_interaction})",
                severity="high", value=0.9, risk=0.25, cost=0.4,
                actions=[
                    "핵심 상호작용(화면 전환·리스트·버튼·상태 변화)에 이징 곡선 기반 애니메이션 적용",
                    "터치/충돌/완료 시점에 햅틱·리플 등 즉각 시각·촉각 피드백 부여",
                    "프레임 드롭 없이 60fps 유지 및 기존 로직 회귀(Zero-Regression) 0건 검증",
                ],
                verification=[
                    "애니메이션/이징/전환 API 호출이 실제 소스에 존재",
                    "기존 기능 동작 회귀 0건 (기존 AC 재통과)",
                    "60fps 또는 프레임 드롭 0건 실측",
                ],
            ))

        # 2. state coverage — 로딩/빈 상태/에러·재시도
        if has_ui and hits(_STATE_TOKENS) < 2:
            gaps.append(SurfaceGap(
                area="state_coverage",
                title="사용자 상태 커버리지 완성: 로딩·빈 상태·에러/재시도 UX 및 스켈레톤 표준화",
                detail="UI 표면에 로딩/빈 상태/에러·재시도 상태 처리가 부족하여 실사용 시 빈 화면·멈춤으로 보입니다.",
                evidence=f"로딩/빈상태/에러 토큰 {hits(_STATE_TOKENS)}건 (권장 ≥2)",
                severity="medium", value=0.8, risk=0.2, cost=0.35,
                actions=[
                    "로딩·빈 상태·에러 상태를 공통 컴포넌트/상태 모델로 표준화",
                    "네트워크/IO 실패 시 재시도(Retry) 및 오프라인 안내 동선 구현",
                    "스켈레톤/플레이스홀더로 체감 대기 시간 최소화",
                ],
                verification=[
                    "로딩/빈상태/에러 상태 분기가 실제 소스에 존재",
                    "에러 발생 시 재시도 UI 노출 검증",
                ],
            ))

        # 3. accessibility
        if has_ui and hits(_A11Y_TOKENS) == 0:
            gaps.append(SurfaceGap(
                area="accessibility",
                title="접근성 및 터치 사용성 완성: 콘텐츠 설명·시맨틱스·48dp 터치 타깃·고대비",
                detail="UI 표면에 접근성(콘텐츠 설명·시맨틱스) 및 터치 타깃 규격 토큰이 없어 접근성/사용성 품질이 미달입니다.",
                evidence="접근성(콘텐츠 설명·시맨틱스·48dp·고대비) 토큰 0건",
                severity="medium", value=0.7, risk=0.15, cost=0.3,
                actions=[
                    "인터랙티브 요소에 콘텐츠 설명/시맨틱 라벨 부여",
                    "최소 48dp 터치 타깃 및 명도 대비(WCAG AA) 확보",
                    "키보드/스크린리더 내비게이션 순서 검증",
                ],
                verification=[
                    "콘텐츠 설명/시맨틱 토큰이 실제 소스에 존재",
                    "터치 타깃 최소 규격 준수",
                ],
            ))

        # 4. maintainability / CS
        if len(files) >= 2:
            guarded = sum(1 for c in files.values() if any(t in c.lower() for t in _MAINTAIN_TOKENS))
            ratio = guarded / max(1, len(files))
            if ratio < 0.30:
                gaps.append(SurfaceGap(
                    area="maintainability_cs",
                    title="유지보수·CS 대응 기반 강화: 전역 예외 처리, 구조화 로깅 및 장애 진단 표준화",
                    detail="예외 처리/로깅이 적용된 소스 비율이 낮아 런타임 장애 시 원인 추적과 CS 대응이 어렵습니다.",
                    evidence=f"예외처리/로깅 적용 파일 {guarded}/{len(files)} ({ratio*100:.0f}%, 권장 ≥30%)",
                    severity="medium", value=0.75, risk=0.2, cost=0.35,
                    actions=[
                        "핵심 진입점/IO 경계에 전역 예외 처리(try-catch/finally) 적용",
                        "구조화 로깅(레벨·컨텍스트) 도입 및 사용자 친화적 에러 메시지 표준화",
                        "치명 오류 발생 시 복구/진단 정보 수집 동선 마련",
                    ],
                    verification=[
                        "예외 처리/로깅 적용 파일 비율 30% 이상",
                        "강제 예외 주입 시 크래시 없이 로그 기록 및 정상 복귀",
                    ],
                ))

        # 5. commercial polish
        if (has_ui or len(files) >= 3) and hits(_COMMERCIAL_TOKENS) == 0:
            gaps.append(SurfaceGap(
                area="commercial_polish",
                title="상품성 마감: 온보딩·설정·도움말·버전/피드백 동선 및 브랜드 일관성",
                detail="온보딩/설정/도움말/버전/피드백 등 상품성 마감 요소가 없어 '데모'처럼 보이고 실제 상품성이 떨어집니다.",
                evidence="상품성(온보딩·설정·도움말·버전·피드백) 토큰 0건",
                severity="low", value=0.6, risk=0.2, cost=0.3,
                actions=[
                    "첫 실행 온보딩/가이드 및 핵심 가치 전달 동선 추가",
                    "설정·도움말·버전/변경내역·피드백(평점/공유) 동선 마련",
                    "브랜드 컬러/타이포 토큰 일관성 점검 및 적용",
                ],
                verification=[
                    "온보딩/설정/도움말 진입점이 실제 소스에 존재",
                    "버전/피드백 동선 노출 검증",
                ],
            ))

        return gaps


def main() -> int:
    """CLI entrypoint for standalone inspection of product depth (QC-A-01, QC-U-01, QC-P-01)."""
    import argparse
    import signal
    import sys

    def _sigint_handler(signum, frame):
        print("\n\033[33m[NOTICE] 사용자에 의해 작업이 안전하게 취소되었습니다 (SIGINT / Graceful Shutdown).\033[0m")
        sys.exit(130)

    signal.signal(signal.SIGINT, _sigint_handler)

    parser = argparse.ArgumentParser(
        description="AGY Product Refinement Governor — 5차원 제품 심도 및 스코프 가드 정적 분석기"
    )
    parser.add_argument(
        "--workspace", "-w", type=str, default=".",
        help="검사할 프로젝트 루트 디렉토리 (기본값: 현재 디렉토리)"
    )
    parser.add_argument(
        "--domain", "-d", type=str, default="",
        help="프로젝트 도메인 (예: mobile_app, game, cli_agent, web)"
    )
    parser.add_argument(
        "--bar", "-b", type=float, default=80.0,
        help="심도 기준선 점수 (기본값: 80.0)"
    )
    parser.add_argument(
        "--no-runtime-evidence", action="store_true",
        help="Gate 5 런타임(프레임버퍼/비전) 증거 교차검증을 비활성화하고 정적 분석만 수행"
    )
    parser.add_argument(
        "--record-area", type=str, default="",
        choices=[""] + list(KNOWN_DEPTH_AREAS),
        help="런타임 증거를 기록할 심도 차원 (Gate 5/E2E 러너가 호출)"
    )
    parser.add_argument(
        "--record-status", type=str, default="verified", choices=["verified", "failed"],
        help="--record-area 와 함께 기록할 상태 (기본값: verified)"
    )
    parser.add_argument(
        "--record-detail", type=str, default="",
        help="--record-area 와 함께 기록할 상세 근거"
    )
    parser.add_argument(
        "--record-basis", type=str, default="runtime",
        choices=["pixel", "runtime", "filename"],
        help="증거 기반: pixel(실 프레임버퍼)/runtime(E2E 신뢰)/filename(약한 fallback → partial)"
    )
    args = parser.parse_args()

    ws = os.path.abspath(args.workspace)
    if not os.path.isdir(ws):
        print(f"\033[31m[ERROR] 워크스페이스 디렉토리가 존재하지 않습니다: {ws}\033[0m", file=sys.stderr)
        return 1

    # 런타임 증거 기록 모드 (Gate 5 / E2E 러너가 파이프라인에서 호출)
    if args.record_area:
        path = ProductRefinementGovernor.record_runtime_evidence(
            ws, args.record_area, args.record_status, args.record_detail,
            source="cli", basis=args.record_basis,
        )
        if path:
            print(f"\033[1;32m[OK] 런타임 증거 기록 완료: [{args.record_status}] {args.record_area} (basis={args.record_basis}) -> {path}\033[0m")
            return 0
        print("\033[31m[ERROR] 런타임 증거 기록 실패 (area/status/workspace 확인)\033[0m", file=sys.stderr)
        return 1

    print("\033[1;36m[1/3] 🔍 프로젝트 소스코드 탐색 및 표면 정적 분석 중...\033[0m")
    ProductRefinementGovernor.DEPTH_BAR = args.bar
    verdict = ProductRefinementGovernor.evaluate(
        workspace=ws, domain=args.domain,
        auto_load_evidence=not args.no_runtime_evidence,
    )

    print("\033[1;34m[2/3] 🧭 5차원 제품 심도(Product Depth) 산출 중...\033[0m")
    score_color = "\033[32m" if verdict.depth_score >= verdict.bar else "\033[33m"
    print(f"      • 심도 점수: {score_color}{verdict.depth_score:.1f}%\033[0m (기준선: {verdict.bar:.0f}%)")
    print(f"      • 심도 강제 여부: {'\033[33m심화(DEEPEN) 강제\033[0m' if verdict.requires_depth else '\033[32m확장(EXPAND) 허용\033[0m'}")
    grade_color = {
        "static_baseline": "\033[90m", "runtime_partial": "\033[36m",
        "runtime_proven": "\033[32m", "runtime_failed": "\033[31m",
    }.get(verdict.evidence_grade, "\033[0m")
    print(f"      • 런타임 검증 등급: {grade_color}{verdict.evidence_grade_label}\033[0m")
    if verdict.runtime_verified:
        print(f"        - 런타임 검증 차원: \033[32m{', '.join(verdict.runtime_verified)}\033[0m")
    if verdict.runtime_verified_partial:
        print(f"        - 약한(파일명 fallback) 증거 차원: \033[36m{', '.join(verdict.runtime_verified_partial)}\033[0m")
    if verdict.runtime_failed:
        print(f"        - 런타임 실패 차원: \033[31m{', '.join(verdict.runtime_failed)}\033[0m")
    if verdict.stale_discarded:
        print(f"        - 신선도 만료로 폐기된 증거: \033[33m{', '.join(verdict.stale_discarded)}\033[0m")
    if verdict.evidence_missing:
        print("        - \033[31m런타임 증거 없음 → 신규 확장(EXPAND) 승인 불가 (게이밍 리스크)\033[0m")

    print("\033[1;32m[3/3] 📊 기획 가이드 브리프 생성 완료:\033[0m")
    brief = ProductRefinementGovernor.to_brief(verdict)
    for line in brief.splitlines():
        print(f"  {line}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
