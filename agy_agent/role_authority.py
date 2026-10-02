#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
role_authority.py — 멀티 에이전트 역할 권한 단일 진실 공급원(SSOT)

[배경 / 실제 버그]
멀티 에이전트(Coder ➔ QA ➔ Auditor) 파이프라인에서 "역할 전도(Role Inversion)"가
사용자에게 노출되는 두 가지 경로가 있었다.

  1. Coder(구현자)가 `[STATUS: APPROVED/REJECTED]` 같은 **QA 전용 판정 태그**를 선언하여
     마치 자기가 승인/반려를 결정하는 것처럼 게시됨.
  2. 시스템 무결성 가디언(Anti-Mocking / Visual Craft / 요구사항 커버리지 등)의
     **자동 오버라이드 반려문**이 Coder 채널(main_channel)에 그대로 게시되어
     "QA는 APPROVED 인데 Coder가 REJECTED 를 선언"하는 것처럼 보임.

[역할 계약(불변)]
  • 판정 권한(verdict authority)  = QA / Auditor  → `[STATUS: ...]` 선언 가능
  • 구현 권한(implementation)     = Coder         → 판정 태그 선언 절대 금지
  • 시스템 게이트(무결성 가디언)  = 제3 주체       → QA/Auditor 판정을 오버라이드할 수 있으나
                                                    반드시 "판정 주체: 시스템 무결성 가디언"을
                                                    명시하여 Coder/QA 로 오인되지 않게 한다.

이 모듈은 discord/asyncio 의존성이 없어 순수 단위 테스트가 가능하다.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

__all__ = [
    "VERDICT_AUTHORITY_ROLES",
    "IMPLEMENTATION_ROLES",
    "IMPLEMENTATION_ROLE_ALIASES",
    "SYSTEM_GATE_JUDGE",
    "SYSTEM_GATE_BADGE",
    "VERDICT_TAG_RE",
    "BLOCKING_TAG_RE",
    "DIRECT_PATCH_TAG_RE",
    "is_declaration_line",
    "has_verdict_declaration",
    "strip_coder_verdict_tags",
    "detect_coder_role_inversion",
    "detect_qa_role_inversion",
    "role_may_declare_verdict",
    "is_direct_patch_verdict",
    "format_direct_patch_attribution",
    "enforce_role_output_authority",
    "format_system_gate_override",
    "build_coder_role_correction_prompt",
    "build_qa_role_correction_prompt",
    "CODER_RESTORED_NOTE",
]

# ---------------------------------------------------------------------------
# 역할 계약 상수
# ---------------------------------------------------------------------------
VERDICT_AUTHORITY_ROLES = frozenset({"qa", "auditor"})
IMPLEMENTATION_ROLES = frozenset({"coder"})

# 구현(비-판정) 역할 별칭 — 이 역할들은 `[STATUS: ...]` 판정 선언 권한이 없다.
# (research_runner 의 Analyst, consulting_runner 의 Architect, single 모드의 General 포함)
IMPLEMENTATION_ROLE_ALIASES = frozenset({
    "coder", "developer", "engineer", "implementer", "programmer",
    "analyst", "architect", "general", "assistant", "researcher",
})

SYSTEM_GATE_JUDGE = "시스템 무결성 가디언"
SYSTEM_GATE_BADGE = "🧪"

# 판정 태그: [STATUS: APPROVED] / STATUS: REJECTED / [STATUS: AUDIT_PASSED] / [상태: 승인] 등 (전각 콜론 및 한국어 표기 지원)
VERDICT_TAG_RE = re.compile(
    r"\[?\s*(?:STATUS|상태|판정|결과)\s*[:：]\s*"
    r"(APPROVED(?:\s*\([^)]*\))?|REJECTED|DEFECT_DETECTED|"
    r"AUDIT_PASSED(?:\s*\([^)]*\))?|AUDIT_FAILED|AUDIT_STOP|"
    r"승인(?:\s*\([^)]*\))?|반려|통과|합격|탈락)"
    r"\s*\]?",
    re.IGNORECASE,
)
# 차단 결함 수 마커: [BLOCKING_DEFECTS: N]
BLOCKING_TAG_RE = re.compile(
    r"\[?\s*BLOCKING_DEFECTS\s*[:：]\s*\d+\s*\]?",
    re.IGNORECASE,
)
# 직접 패치 예외 표기: `[STATUS: APPROVED (QA Direct Patch)]` / `(Auditor Direct Patch)`
# 검증관이 1~5줄 경미 결함을 직접 수정한 예외 케이스 — 구현 주체가 QA/Auditor 임을 명시해야
# Coder 의 판정으로 오인되지 않는다.
DIRECT_PATCH_TAG_RE = re.compile(
    r"\(\s*(QA|Auditor|qa|auditor)\s+Direct\s+Patch\s*\)",
    re.IGNORECASE,
)

# 판정 선언 줄의 선행 장식 / 라벨 (output_guard 와 동일 규약)
_LEAD_DECOR_RE = re.compile(r"^(?:[#*\-•>\s\d.]|__|\*\*)+")
_LABEL_RE = re.compile(
    r"^(?:판정|결론|최종\s*판정|최종\s*결과|감사\s*결과|검증\s*결과|검토\s*결과|"
    r"Verdict|Result|Conclusion|Final\s*Verdict|Status)(?:\*\*|__)?\s*[:：\-]\s*(?:\*\*|__)?",
    re.IGNORECASE,
)

# Coder 가 작성하면 안 되는 QA 리뷰 리포트 헤더 (역할 전도 2차 신호)
_QA_REPORT_HEADERS = (
    "QA 검증 리포트", "QA 검수 보고서", "코드 리뷰 결과", "품질 검토 결과",
    "검증 결과: 승인", "검증 결과: 반려", "심사 결과 보고서", "결함 검증 보고",
    "QA Review Report", "Code Review Report", "QA Verdict",
)

_QA_CODER_COMPLETION_PHRASES = (
    "구현을 완료했습니다", "개발을 완료했습니다", "기능을 추가했습니다",
    "신규 기능을 구현했습니다", "코드를 모두 작성했습니다", "전체 코드를 작성했습니다",
    "I have implemented", "I have created the entire",
)


# ---------------------------------------------------------------------------
# 판정 선언 판별
# ---------------------------------------------------------------------------
def is_declaration_line(line: str) -> bool:
    """줄이 판정 '선언'으로 시작하는지 판정한다.

    `[STATUS: REJECTED]`, `**판정**: [STATUS: APPROVED]`,
    `[BLOCKING_DEFECTS: 2]` → True
    `이전 라운드에서 [STATUS: REJECTED]를 받았습니다` → False (본문 인용)
    """
    if not line:
        return False
    head = _LEAD_DECOR_RE.sub("", line)
    head = _LABEL_RE.sub("", head)
    return bool(VERDICT_TAG_RE.match(head)) or bool(BLOCKING_TAG_RE.match(head))


def has_verdict_declaration(text: str, within_first: int = 3) -> bool:
    """앞쪽 `within_first` 개의 비어있지 않은 줄 안에 판정 선언이 있는지 검사한다."""
    if not text or not isinstance(text, str):
        return False
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return any(is_declaration_line(ln) for ln in lines[:within_first])


# ---------------------------------------------------------------------------
# Coder 출력 정화(판정 태그 물리 제거)
# ---------------------------------------------------------------------------
def strip_coder_verdict_tags(text: str) -> Tuple[str, List[str]]:
    """Coder 출력에서 QA/Auditor 전용 판정 태그 선언을 물리적으로 제거한다.

    Returns:
        ``(clean_text, removed_tags)`` — removed_tags 는 제거된 태그 문자열 목록.

    정책:
      * 판정 '선언 줄'에서만 태그를 제거한다(본문 중 인용은 보존).
      * 태그 제거 후 남은 내용이 있으면 유지(맥락 보존), 비면 줄을 삭제한다.
    """
    if not text or not isinstance(text, str):
        return text or "", []

    removed: List[str] = []
    out: List[str] = []
    for line in text.splitlines():
        if not is_declaration_line(line):
            out.append(line)
            continue
        m = VERDICT_TAG_RE.search(line) or BLOCKING_TAG_RE.search(line)
        if not m:
            out.append(line)
            continue
        removed.append(m.group(0).strip())
        remainder = line.replace(m.group(0), "", 1)
        remainder = re.sub(r"^[\s\-–—:：,.;]+", "", remainder).strip()
        if remainder:
            out.append(remainder)

    clean = "\n".join(out).strip()
    return clean, removed


# ---------------------------------------------------------------------------
# 역할 경계 결정론 강제 (Role Boundary Enforcement)
# ---------------------------------------------------------------------------
def role_may_declare_verdict(role: str) -> bool:
    """해당 역할이 `[STATUS: ...]` 판정을 선언할 권한이 있는지 판정한다.

    판정 권한은 QA/Auditor 전용이다. 구현 역할(Coder/Analyst/Architect/General 등)은
    어떤 경로로 실행되든 판정을 선언할 수 없다.
    """
    r = (role or "").strip().lower()
    if not r:
        return False
    if r in VERDICT_AUTHORITY_ROLES:
        return True
    return r not in IMPLEMENTATION_ROLE_ALIASES


def is_direct_patch_verdict(text: str) -> bool:
    """검증관(QA/Auditor)이 '직접 패치(Direct Patch)' 권한을 행사한 판정인지 판정한다."""
    return bool(text) and isinstance(text, str) and bool(DIRECT_PATCH_TAG_RE.search(text))


def format_direct_patch_attribution(text: str) -> str:
    """직접 패치 판정문에 '구현 주체' 라벨을 붙여 Coder 오인(역할 전도)을 원천 차단한다.

    사용자 증상: `[STATUS: APPROVED (QA Direct Patch)]` 가 Coder 가 승인한 것으로 보이거나,
    반대로 Coder 가 `[STATUS: REJECTED]` 를 선언해 QA 를 뒤집는 것처럼 보였다.
    직접 패치는 구현 주체가 QA/Auditor 임을 기계적으로 명시하여 이 혼동을 제거한다.
    (이미 라벨이 있으면 중복 부착하지 않는다 — 멱등.)
    """
    if not is_direct_patch_verdict(text):
        return text
    if "구현 주체:" in text:
        return text
    who = "Auditor" if re.search(r"\(\s*Auditor\s+Direct\s+Patch", text, re.IGNORECASE) else "QA"
    badge = (
        f"🛠️ **[검증관 직접 패치 보고 — 구현 주체: {who} · 판정 주체: {who}]** "
        f"1~5줄 경미 결함을 검증관이 직접 수정한 예외 케이스입니다. "
        f"(일반 기능 개발의 구현·수정은 Coder 담당)"
    )
    return f"{badge}\n{text.strip()}"


def enforce_role_output_authority(role: str, text: str) -> Tuple[str, List[str]]:
    """역할별 출력 권한을 결정론적으로 강제한다.

    구현 역할이 판정 태그를 선언하면 물리 제거하고, 판정 역할(QA/Auditor)은 원문을 보존한다.
    Returns: ``(authorized_text, removed_tags)``
    """
    if not text or not isinstance(text, str):
        return text or "", []
    if role_may_declare_verdict(role):
        return text, []
    return strip_coder_verdict_tags(text)


# ---------------------------------------------------------------------------
# 역할 전도 탐지
# ---------------------------------------------------------------------------
def detect_coder_role_inversion(output: str) -> Tuple[bool, str]:
    """Coder 가 QA/Auditor 역할을 수행(판정 태그 선언 또는 리뷰 리포트)했는지 탐지한다."""
    if not output or not isinstance(output, str):
        return False, ""

    lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
    if not lines:
        return False, ""

    head_joined = " ".join(lines[:3])

    # 1) 판정 선언 줄 (앞쪽 3줄) — Coder 가 QA 판정 태그를 선언
    if has_verdict_declaration(output, within_first=3):
        return True, (
            "Coder 에이전트가 소스코드 구현 대신 QA/감사관 판정 태그"
            "([STATUS: APPROVED/REJECTED/AUDIT_PASSED])를 선언하며 QA 역할을 수행(역할 전도)했습니다."
        )

    # 2) 최종 판정 라벨 + 태그 조합
    if re.search(
        r"(?:최종\s*판정|판정|결론)\s*[:：]\s*\[?\s*STATUS\s*:",
        head_joined,
        re.IGNORECASE,
    ):
        return True, (
            "Coder 에이전트가 소스코드 구현 대신 QA/감사관 판정 태그"
            "([STATUS: APPROVED/REJECTED/AUDIT_PASSED])를 선언하며 QA 역할을 수행(역할 전도)했습니다."
        )

    # 3) QA 리뷰 리포트 헤더 (구현 산출물 없이 리뷰 보고서만 작성)
    for header in _QA_REPORT_HEADERS:
        if header.lower() in head_joined.lower():
            return True, (
                f"Coder 에이전트가 소스코드 구현 대신 '{header}'를 작성하며 QA 역할을 수행(역할 전도)했습니다."
            )

    return False, ""


def detect_qa_role_inversion(output: str) -> Tuple[bool, str]:
    """QA 가 판정 태그를 누락한 채 Coder 처럼 직접 기능 개발을 선언했는지 탐지한다."""
    if not output or not isinstance(output, str):
        return False, ""

    lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
    if not lines:
        return False, ""

    has_qa_status = bool(VERDICT_TAG_RE.search(output))
    head_joined = " ".join(lines[:3])
    if not has_qa_status and any(ph in head_joined for ph in _QA_CODER_COMPLETION_PHRASES):
        return True, (
            "QA 에이전트가 품질 검증 및 [STATUS: APPROVED/REJECTED] 판정 대신 "
            "직접 기능 개발을 완료했다고 선언(역할 전도)했습니다."
        )
    return False, ""


# ---------------------------------------------------------------------------
# 시스템 게이트(무결성 가디언) 오버라이드 문구 생성
# ---------------------------------------------------------------------------
def format_system_gate_override(
    *,
    approved: bool,
    title: str,
    detail: str = "",
    blocking_defects: Optional[int] = None,
    original: str = "",
    judge: str = SYSTEM_GATE_JUDGE,
    status_override: Optional[str] = None,
    verdict_line_override: Optional[str] = None,
) -> str:
    """시스템 무결성 가디언의 판정 오버라이드 메시지를 생성한다.

    - 첫 줄: `[STATUS: APPROVED|REJECTED]` (파이프라인 판정 파서 호환)
      · `status_override` / `verdict_line_override`로 역할별 표기 지정 가능.
    - 둘째 줄: `[BLOCKING_DEFECTS: N]` (수렴 계약/텔레메트리 호환)
    - 셋째 줄: **판정 주체**를 명시 → Coder/QA 로 오인되지 않게 함.
    """
    status = status_override or ("APPROVED" if approved else "REJECTED")
    verdict_line = verdict_line_override or f"[STATUS: {status}]"
    if blocking_defects is None:
        blocking_defects = 0 if approved else 1
    n = max(0, int(blocking_defects))

    parts = [
        verdict_line,
        f"[BLOCKING_DEFECTS: {n}]",
        f"{SYSTEM_GATE_BADGE} **[판정 주체: {judge} · 시스템 자동 게이트 — Coder/QA 판정 아님]**",
        title.strip() if title else "",
    ]
    if detail.strip():
        parts.append(detail.strip())
    if original.strip():
        parts.append(f"\n--- 원본 응답 ---\n{original.strip()}")
    return "\n".join(p for p in parts if p != "")


# ---------------------------------------------------------------------------
# 역할 복귀 교정 프롬프트
# ---------------------------------------------------------------------------
def build_coder_role_correction_prompt(qa_verdict: Optional[str] = None) -> str:
    base = (
        "🚨 **[사령관 긴급 재지시: Coder 역할 복귀 및 실제 소스코드 구현 의무]**\n"
        "당신은 코드 리뷰어(QA)가 아니라 **코드를 직접 수정하고 구현하는 엔지니어(Coder)**입니다!\n"
        "`[STATUS: APPROVED]`, `[STATUS: REJECTED]` 같은 QA 판정 태그 선언은 엄격히 금지됩니다. "
        "(승인/반려 판정은 QA/Auditor 전용 권한입니다.)\n"
        "1. QA가 지적한 문제점이나 요구사항을 해결하기 위해 파일 수정 도구"
        "(`replace_file_content`, `write_to_file` 등)를 즉시 실행하십시오.\n"
        "2. 실제 프로덕션 소스코드 파일을 디스크에 저장하고, 구체적인 코드 변경 사항(diff 요약)을 보고하십시오."
    )
    # QA 가 이미 승인/반려 판정을 내린 경우, Coder 는 그 판정을 뒤집을 권한이 없음을 명시한다.
    if qa_verdict and is_direct_patch_verdict(qa_verdict) and re.search(
        r"STATUS\s*:\s*APPROVED", qa_verdict, re.IGNORECASE
    ):
        base += (
            "\n3. **⚠️ [QA 직접 패치 승인 유지]**: 직전 QA 가 `(QA Direct Patch)` 로 직접 수정·승인했습니다. "
            "당신은 QA 의 승인 판정을 `[STATUS: REJECTED]` 등으로 **뒤집을 권한이 없습니다**. "
            "QA 가 남긴 미비점이 있다면 판정 태그 대신 실제 소스코드 보강과 변경 내역 보고로만 응답하십시오."
        )
    return base


def build_qa_role_correction_prompt() -> str:
    return (
        "🚨 **[사령관 긴급 재지시: QA 역할 복귀 및 판정 의무]**\n"
        "당신은 기능을 새로 개발하는 Coder가 아니라 **품질 검증관(QA)**입니다.\n"
        "1. 답변 첫 줄을 반드시 `[STATUS: APPROVED]` 또는 `[STATUS: REJECTED]`로 시작하십시오.\n"
        "2. 둘째 줄에 `[BLOCKING_DEFECTS: N]`을 표기하십시오.\n"
        "3. 대규모 코드 작성 대신, 결함 위치와 수정 스펙을 명시하여 Coder에게 재작업을 지시하십시오."
    )


CODER_RESTORED_NOTE = (
    "👨‍💻 **[Coder 구현 보고]** — 시스템이 Coder 출력에서 QA 전용 판정 태그를 제거했습니다. "
    "판정(승인/반려)은 QA/Auditor 전용 권한입니다."
)
