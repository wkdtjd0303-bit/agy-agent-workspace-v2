"""
🎛️ Mode Registry — 동작 모드 단일 진실 공급원 (Single Source of Truth)
=====================================================================

배경 (왜 필요한가):
    이전에는 `!mode 2` / `!mode 2+` / `!mode 3` / `!mode 3+` 의 구분이
    여러 모듈(session_manager.set_mode, mode_classifier.classify_mode,
    commander.dispatch_qa_squad, trio_agent_runner.is_multi_qa_pipeline,
    bot.py execute_channel_prompt)에 **각자 다른 문자열 튜플로 하드코딩**되어
    있었다. 그 결과:

      1. `!mode 2+` 로 세팅했는데도 `!pwd` 표시(등록 QA 전체 기준)와
         실제 런타임 파이프라인(활성 QA 기준)이 서로 다른 에이전트 수를
         보여주는 '표시-실행 불일치'가 발생했고,
      2. 심층 리서치로 분류되는 프롬프트가 들어오면 `pair_quad`(`!mode 2+`)
         임에도 불구하고 확장 QA가 전혀 없는 `ResearchRunner`(Analyst ➔
         FactChecker ➔ Auditor, 즉 3중)로 조용히 강등되어 `!mode 2` 처럼
         동작했다.
      3. 활성 확장 QA가 0대인데도 `+` 모드가 경고 없이 3중으로 붕괴했다.

    본 모듈은 모든 모드 해석/파이프라인 형태 결정을 하나로 모아,
    **표시(Display) == 실행(Execution)** 을 구조적으로 보장한다.

설계 원칙:
    - 순수 함수/상수만 제공한다. (무거운 모듈 import 금지 → 순환 import 방지)
    - 정규화 결과는 기존 내부 문자열(`pair`, `pair_quad`, `mode3`,
      `mode3_plus`, `single`, `single_qa2`, `read`, `auto`)을 그대로 사용하여
      하위 호환성을 100% 유지한다.
    - `+` 계열(pair_quad / mode3_plus)은 "다중 확장 QA 파이프라인"으로,
      비-`+` 계열(pair / mode3)은 "3중 코어 파이프라인"으로 엄격히 구분한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. 정규 모드 (Canonical modes)
# ---------------------------------------------------------------------------
MODE_AUTO = "auto"
MODE_READ = "read"
MODE_SINGLE = "single"
MODE_SINGLE_QA2 = "single_qa2"
MODE_PAIR = "pair"                    # !mode 2  — 사용자 주도 3중 협업
MODE_PAIR_QUAD = "pair_quad"          # !mode 2+ — 사용자 주도 다중 협업
MODE_TRIO_AUTO = "mode3"              # !mode 3  — 무인 자율 순환 3중
MODE_MULTI_AUTO = "mode3_plus"        # !mode 3+ — 무인 자율 순환 다중

ALL_CANONICAL_MODES = (
    MODE_AUTO,
    MODE_READ,
    MODE_SINGLE,
    MODE_SINGLE_QA2,
    MODE_PAIR,
    MODE_PAIR_QUAD,
    MODE_TRIO_AUTO,
    MODE_MULTI_AUTO,
)

# 다중 확장 QA 파이프라인을 사용하는 `+` 모드 (단 하나의 정의)
MULTI_QA_MODES = (MODE_PAIR_QUAD, MODE_MULTI_AUTO)

# 무인 자율 순환(폐루프) 모드
AUTONOMOUS_MODES = (MODE_TRIO_AUTO, MODE_MULTI_AUTO)

# 3중 코어 역할 파이프라인 (Coder ↔ qa1 ↔ Auditor)
CORE_PIPELINE_MODES = (MODE_PAIR, MODE_TRIO_AUTO)

# 사용자 주도(pair/pair_quad) + 무인 자율(mode3/mode3_plus) = 모든 다중 에이전트 모드.
# (단일 에이전트/읽기 전용/자동 분류는 제외)
MULTI_AGENT_MODES = (MODE_PAIR, MODE_PAIR_QUAD, MODE_TRIO_AUTO, MODE_MULTI_AUTO)

# 사용자에게 보여줄 라벨
MODE_LABELS: Dict[str, str] = {
    MODE_AUTO: "auto",
    MODE_READ: "read",
    MODE_SINGLE: "1",
    MODE_SINGLE_QA2: "qa2",
    MODE_PAIR: "2",
    MODE_PAIR_QUAD: "2+",
    MODE_TRIO_AUTO: "3",
    MODE_MULTI_AUTO: "3+",
}

# ---------------------------------------------------------------------------
# 2. 별칭(alias) 테이블 — set_mode / classify_mode 의 모든 입력을 포괄한다.
#    (기존 set_mode 의 매핑을 그대로 흡수 + classify_mode 의 auto_trio/auto_multi
#     계열까지 포함하여 두 모듈 간 해석 차이를 제거)
# ---------------------------------------------------------------------------
_ALIAS_TABLE: Dict[str, str] = {}


def _register(canonical: str, aliases: Tuple[str, ...]) -> None:
    for a in aliases:
        _ALIAS_TABLE[a.lower().strip()] = canonical


_register(MODE_AUTO, ("auto", "automatic", "smart", "ai", "자율", "자동", "오토"))
_register(MODE_READ, (
    "read", "readonly", "read-only", "consult", "consulting",
    "plan", "planning", "읽기", "기획", "자문", "방향성",
))
_register(MODE_MULTI_AUTO, (
    "3+", "3plus", "3플러스", "mode3_plus", "mode3+",
    "자율3+", "무인3+", "auto_multi", "multi_auto",
))
_register(MODE_TRIO_AUTO, (
    "3", "mode3", "자율3", "무인3", "3자율", "3차자율",
    "auto_trio", "trio_auto",
))
_register(MODE_PAIR_QUAD, (
    "pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스",
))
_register(MODE_SINGLE_QA2, (
    "single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo", "solo_qa2",
))
_register(MODE_PAIR, ("pair", "trio", "dual", "team", "collab", "3중", "페어", "협업", "2"))
_register(MODE_SINGLE, (
    "single", "solo", "단일", "싱글", "1",
    # 레거시 컨설턴트 모드(!mode 1+)는 단일 에이전트 모드로 통합 (무중단 호환)
    "single_plus", "single+", "1+", "1plus", "1플러스",
    "single_consultant", "consultant", "컨설턴트",
))


def is_known_mode(raw: Any) -> bool:
    """정규화 가능한 '알려진' 모드 입력인지 판정한다 (unknown → auto 강등과 구분)."""
    if raw is None:
        return False
    if hasattr(raw, "mode"):
        raw = getattr(raw, "mode", "") or ""
    text = str(raw).lower().strip()
    if not text:
        return False
    if text in _ALIAS_TABLE or text.replace(" ", "") in _ALIAS_TABLE:
        return True
    first = text.split()[0]
    return first in _ALIAS_TABLE


def normalize_mode(raw: Any) -> str:
    """
    임의의 모드 입력 문자열/세션을 정규 모드로 해석한다.

    - `ChannelSession`/`mode` 속성을 가진 객체를 넘기면 그 `.mode` 값을 사용한다.
    - 공백이 섞인 복합 입력(`"2+ qa2"`)은 첫 토큰을 기준으로 해석한다.
    - 알 수 없는 입력은 안전하게 `auto` 로 수렴한다.
    """
    if raw is None:
        return MODE_AUTO
    if hasattr(raw, "mode"):
        raw = getattr(raw, "mode", MODE_AUTO) or MODE_AUTO

    text = str(raw).lower().strip()
    if not text:
        return MODE_AUTO

    # 1) 전체 문자열 일치
    if text in _ALIAS_TABLE:
        return _ALIAS_TABLE[text]

    # 2) 공백 제거 일치 (예: "mode 3+", "2 plus" 는 없지만 방어적으로)
    compact = text.replace(" ", "")
    if compact in _ALIAS_TABLE:
        return _ALIAS_TABLE[compact]

    # 3) 첫 토큰 일치 (예: "2+ qa2", "3+ all")
    first = text.split()[0]
    if first in _ALIAS_TABLE:
        return _ALIAS_TABLE[first]

    return MODE_AUTO


# ---------------------------------------------------------------------------
# 3. 파이프라인 형태 질의 (SSOT)
# ---------------------------------------------------------------------------
def is_multi_qa_mode(mode: Any) -> bool:
    """`+` 다중 확장 QA 파이프라인 모드인가? (pair_quad / mode3_plus)"""
    return normalize_mode(mode) in MULTI_QA_MODES


def is_autonomous_mode(mode: Any) -> bool:
    """무인 자율 순환(폐루프) 모드인가? (mode3 / mode3_plus)"""
    return normalize_mode(mode) in AUTONOMOUS_MODES


def is_core_pipeline_mode(mode: Any) -> bool:
    """3중 코어 파이프라인 모드인가? (pair / mode3)"""
    return normalize_mode(mode) in CORE_PIPELINE_MODES


def is_multi_agent_mode(mode: Any) -> bool:
    """다중 에이전트 모드인가? (pair / pair_quad / mode3 / mode3_plus)

    단일/읽기/자동 분류 모드는 False. 여러 모듈에 흩어져 있던
    `_multi_modes = ("pair", "mode3", "pair_quad", ...)` 하드코딩을 대체한다.
    """
    return normalize_mode(mode) in MULTI_AGENT_MODES


def uses_extended_qa(mode: Any) -> bool:
    """확장 QA(qa2..qa10) 를 파이프라인에 투입해야 하는가?"""
    return is_multi_qa_mode(mode)


def mode_label(mode: Any) -> str:
    return MODE_LABELS.get(normalize_mode(mode), "auto")


# ---------------------------------------------------------------------------
# 4. 세션 해석 (표시 == 실행 보장)
# ---------------------------------------------------------------------------
def _active_extended_qas(session: Any) -> List[Dict[str, Any]]:
    """세션 기준 활성 확장 QA 목록을 일관되게 조회한다."""
    try:
        if session is not None and hasattr(session, "get_active_extended_qas"):
            cfgs = session.get_active_extended_qas()
            if cfgs is not None:
                return list(cfgs)
    except Exception:
        pass
    try:
        import config  # lazy import (순환 import 방지)
        return list(config.get_extended_qa_configs() or [])
    except Exception:
        return []


def resolve_mode(mode: Any, session: Any = None) -> Dict[str, Any]:
    """
    모드 + 세션을 하나의 해석 결과로 정규화한다.

    Returns dict:
        mode           : 정규 모드 문자열
        label          : '2+', '3+' 등 사용자 표기 라벨
        is_multi_qa    : 확장 QA 파이프라인 여부
        is_autonomous  : 무인 자율 순환 여부
        extended_qas   : 활성 확장 QA 설정 목록
        extended_count : 활성 확장 QA 수
        agent_count    : 실제 투입 에이전트 수 (멀티면 3+N, 아니면 3)
        chain          : 사람이 읽는 협업 체인
        warning        : 표시-실행 불일치/붕괴 경고 문자열 (없으면 "")
    """
    canonical = normalize_mode(mode if session is None else session)
    if session is None and hasattr(mode, "mode"):
        canonical = normalize_mode(mode)

    multi = canonical in MULTI_QA_MODES
    autonomous = canonical in AUTONOMOUS_MODES

    extended: List[Dict[str, Any]] = _active_extended_qas(session) if multi else []
    ext_count = len(extended)

    if canonical in (MODE_SINGLE, MODE_AUTO):
        agent_count = 1
    elif canonical == MODE_READ:
        agent_count = 3  # Lead Architect ↔ Tech Reviewer ↔ Chief Strategist
    elif canonical == MODE_SINGLE_QA2:
        agent_count = 1
    else:
        agent_count = 3 + ext_count if multi else 3

    ext_names = " ➔ ".join(
        [f"{c.get('name', 'qa').lower()}" for c in extended if isinstance(c, dict)]
    )
    if multi and ext_names:
        chain = f"Coder ➔ qa1 ➔ {ext_names} ➔ Auditor"
    elif canonical == MODE_READ:
        chain = "Lead Architect ➔ Tech Reviewer ➔ Chief Strategist"
    elif canonical == MODE_AUTO:
        chain = "자동 분류 (프롬프트별)"
    elif canonical == MODE_SINGLE:
        chain = "단일 에이전트"
    elif canonical == MODE_SINGLE_QA2:
        chain = "qa2 단독"
    else:
        chain = "Coder ➔ qa1 ➔ Auditor"

    warning = ""
    if multi and ext_count == 0:
        warning = (
            f"⚠️ **[{mode_label(canonical)} 모드 무결성 경고]** 활성 확장 QA가 0대이므로 "
            f"이 작업은 3중 코어 파이프라인으로 축소 실행됩니다. "
            f"`!model` 에서 QA2~QA6을 활성화하거나 `!qa all` 로 QA 필터를 초기화하세요."
        )

    return {
        "mode": canonical,
        "label": mode_label(canonical),
        "is_multi_qa": multi,
        "is_autonomous": autonomous,
        "extended_qas": extended,
        "extended_count": ext_count,
        "agent_count": agent_count,
        "chain": chain,
        "warning": warning,
    }


def describe_pipeline(mode: Any, session: Any = None) -> str:
    """'8중 다중', '4중 쿼드', '3중' 등 실제 투입 규모 라벨."""
    info = resolve_mode(mode if session is None else session, session)
    n = info["agent_count"]
    if not info["is_multi_qa"]:
        # `!pwd` 표시-실행 일치: 단일/자동 모드에 "3중"이라고 잘못 찍지 않는다.
        if info["mode"] == MODE_AUTO:
            return "자동 분류"
        if n == 1:
            return "단일"
        return "3중"
    if n >= 5:
        return f"{n}중 다중"
    if n == 4:
        return "4중 쿼드"
    return "3중"
