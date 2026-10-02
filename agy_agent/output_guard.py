#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
output_guard.py — 에이전트 출력 품질 가드 (판정 헤더 + 한국어)

[배경 / 버그]
1. 리뷰어 봇(QA1..QAN, Auditor)은 프롬프트상 `[STATUS: APPROVED]` /
   `[STATUS: REJECTED]`를 **답변 첫 줄**에 표기해야 한다. 그러나 LLM이 가끔
   판정 태그를 누락하거나, 본문 중간/하단에 배치하여 "판정 여부 없이" 메시지가
   올라오는 경우가 있었다.
2. 일부 봇이 한국어가 아닌 영어/중국어 **문장**으로 응답하여 운영자가 내용을
   파악하기 어려운 경우가 있었다. (단어 단위 코드/식별자 혼용은 정상)

[해법]
- 판정 태그 정규화(`ensure_verdict_first_line`):
    * 이미 존재하는 판정 태그를 첫 줄로 승격(promote)한다.
    * 태그가 전혀 없으면, 파이프라인이 계산한 승인/반려 결정(approved)에 따라
      첫 줄에 정규 태그를 결정론적으로 부착한다.
- 한국어 강제(`is_korean_output` + `guard_reviewer_output`):
    * 코드블록/인라인코드/URL/경로를 제외한 '산문'의 문자 분포를 계산하여
      비한국어(영어/중국어/일본어) 문장으로 판정되면, 같은 에이전트에게
      한국어 재작성을 1회 요청한다(repair). 실패 시 원문을 유지한다.
- 모든 동작은 환경변수로 비활성화 가능:
    * `AGY_OUTPUT_GUARD=0`            : 전체 가드 비활성
    * `AGY_OUTPUT_KOREAN_REPAIR=0`    : 한국어 재작성 LLM 재요청만 비활성(감지/판정 헤더는 유지)
    * `AGY_OUTPUT_KOREAN_MIN_PROSE`   : 판정에 필요한 최소 산문 글자 수(기본 20)
    * `AGY_OUTPUT_KOREAN_RATIO`       : 한국어로 인정할 최소 한글 비율(기본 0.15)

이 모듈은 의존성이 없어(discord/asyncio 미의존) 순수 단위 테스트가 가능하다.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

__all__ = [
    "ensure_verdict_first_line",
    "is_korean_output",
    "korean_ratio",
    "promote_existing_verdict",
    "guard_output_async",
    "guard_reviewer_output",
    "KOREAN_REPAIR_INSTRUCTION",
    "KOREAN_OUTPUT_PROMPT_SUFFIX",
    "CODER_REPAIR_NOTE",
    "REWRITE_ONLY_NOTE",
    "build_korean_repair_prompt",
]

# ---------------------------------------------------------------------------
# 판정 태그 정규식
# ---------------------------------------------------------------------------
_STATUS_VALUE = (
    r"(APPROVED(?:\s*\([^)]*\))?|REJECTED|DEFECT_DETECTED|"
    r"AUDIT_PASSED(?:\s*\([^)]*\))?|AUDIT_FAILED|AUDIT_STOP|"
    r"승인(?:\s*\([^)]*\))?|반려|통과|합격|탈락)"
)
# 정규 판정 태그: [STATUS: X] / STATUS: X / [상태: X] / [판정: X] / [결과: X] (대소문자 무시, 전각 콜론 지원)
_TAG_RE = re.compile(r"\[?\s*(?:STATUS|상태|판정|결과)\s*[:：]\s*" + _STATUS_VALUE + r"\s*\]?", re.IGNORECASE)

# 판정 선언 줄의 선행 장식/라벨
_LEAD_DECOR_RE = re.compile(r"^(?:[#*\-•>\s\d.]|__|\*\*)+")
_LABEL_RE = re.compile(
    r"^(?:판정|결론|최종\s*판정|최종\s*결과|감사\s*결과|검증\s*결과|검토\s*결과|"
    r"Verdict|Result|Conclusion|Final\s*Verdict|Status)(?:\*\*|__)?\s*[:：\-]\s*(?:\*\*|__)?",
    re.IGNORECASE,
)


def _is_declaration_line(line: str) -> bool:
    """줄이 판정 '선언'으로 시작하는지 판정한다(본문 중 언급과 구분).

    예) `[STATUS: REJECTED]`, `**판정**: [STATUS: APPROVED]`,
        `[STATUS: REJECTED] 결함 3건` → True
        `이전 라운드에서 [STATUS: REJECTED]를 선언했음` → False (본문 언급)
    """
    if not line:
        return False
    head = _LEAD_DECOR_RE.sub("", line)
    head = _LABEL_RE.sub("", head)
    return bool(_TAG_RE.match(head))


def _first_nonempty_index(lines) -> int:
    for i, line in enumerate(lines):
        if line.strip():
            return i
    return 0


def _strip_line_decoration(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^(?:[#*\-•>\s]|__)+", "", cleaned)
    cleaned = re.sub(
        r"^(?:판정|결론|최종\s*판정|최종\s*결과|검증\s*결과|검토\s*결과|감사\s*결과|"
        r"Verdict|Result|Conclusion|Final\s*Verdict|Status)\s*[:：\-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:\*\*|__)+", "", cleaned)
    return cleaned.strip()


def _desired_tag(approved: Optional[bool], kind: str) -> Optional[str]:
    if approved is None:
        return None
    k = (kind or "").lower()
    if k in ("qa", "reviewer", "fact_checker", "qa1", "qa_bot"):
        return "APPROVED" if approved else "REJECTED"
    if k in ("auditor", "audit", "client_auditor"):
        return "AUDIT_PASSED" if approved else "AUDIT_FAILED"
    # kind 미지정(예: 자문 전략가) → 판정 강제 없음
    return None


def ensure_verdict_first_line(
    text: str,
    *,
    approved: Optional[bool],
    kind: str = "qa",
) -> str:
    """판정 태그를 답변 첫 줄로 정규화한다.

    - 태그가 이미 있으면 첫 줄로 승격(중복 제거).
    - 태그가 없고 kind/approved가 주어지면 정규 태그를 첫 줄에 부착.
    - kind=None 또는 approved=None이면 태그가 없을 때 원문을 그대로 둔다.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return text or ""

    lines = text.splitlines()
    decl_idx = None
    decl_tag = None
    for i, line in enumerate(lines):
        if _is_declaration_line(line):
            m = _TAG_RE.search(line)
            decl_idx = i
            decl_tag = m.group(1).strip() if m else None
            break

    # 1) 판정 태그가 전혀 없음 → 결정론적으로 부착 (kind/approved가 있을 때만)
    if decl_idx is None:
        desired = _desired_tag(approved, kind)
        if not desired:
            return text
        body = text.strip()
        return f"[STATUS: {desired}]\n{body}" if body else f"[STATUS: {desired}]"

    tag = (decl_tag or "").strip()
    _m_tag = re.match(
        r"(APPROVED|REJECTED|DEFECT_DETECTED|AUDIT_PASSED|AUDIT_FAILED|AUDIT_STOP|승인|반려|통과|합격|탈락)(.*)",
        tag,
        re.IGNORECASE,
    )
    if _m_tag:
        base_tok = _m_tag.group(1).upper()
        if base_tok in ("승인", "통과", "합격"):
            base_tok = "AUDIT_PASSED" if (kind or "").lower() in ("auditor", "audit") else "APPROVED"
        elif base_tok in ("반려", "탈락"):
            base_tok = "AUDIT_FAILED" if (kind or "").lower() in ("auditor", "audit") else "REJECTED"
        tag = base_tok + _m_tag.group(2)

    # 2) 이미 첫 줄(비어있지 않은 첫 줄)에 선언되어 있음 → 표준 표기로 정리
    first_idx = _first_nonempty_index(lines)
    if decl_idx == first_idx:
        remainder = _TAG_RE.sub("", lines[decl_idx])
        remainder = _strip_line_decoration(remainder)
        lines[decl_idx] = f"[STATUS: {tag}] {remainder}".rstrip() if remainder else f"[STATUS: {tag}]"
        return "\n".join(lines)

    # 3) 본문 중간/하단에 선언 → 해당 줄을 제거하고 첫 줄로 승격
    del lines[decl_idx]
    body = "\n".join(lines).strip("\n").strip()
    return f"[STATUS: {tag}]\n{body}" if body else f"[STATUS: {tag}]"


def promote_existing_verdict(text: str) -> str:
    """태그가 존재할 때만 첫 줄로 승격한다(결정적, 판정 추론 없음).

    Coder 등 판정자가 아닌 에이전트의 본문에 우연히 태그 인용이 있을 수 있으므로
    이 함수는 '선언 줄'로 인정되는 경우에만 승격한다.
    """
    return ensure_verdict_first_line(text, approved=None, kind=None)


# ---------------------------------------------------------------------------
# 언어 감지 (한국어 산문 비율)
# ---------------------------------------------------------------------------
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_URL_RE = re.compile(r"https?://\S+")
_HANGUL_RE = re.compile(r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]")
# 중국어(한자) + 일본어 가나
_CJK_RE = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\u3400-\u4DBF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
# 산문 판정에서 제외할 경로/식별자성 토큰
_PATHLIKE_RE = re.compile(r"[A-Za-z0-9_\-./\\]+[/\\][A-Za-z0-9_\-./\\]+")
_EXT_TOKEN_RE = re.compile(
    r"\b[A-Za-z0-9_\-]+\.(?:py|js|ts|tsx|jsx|kt|java|xml|json|ya?ml|md|css|html|sh|gradle|toml|cfg|ini|txt|apk)\b",
    re.IGNORECASE,
)


def _prose_only(text: str) -> str:
    """코드블록/인라인코드/URL/경로/확장자 토큰을 제거한 '산문'만 남긴다."""
    if not text:
        return ""
    t = _FENCED_CODE_RE.sub(" ", text)
    t = _INLINE_CODE_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    t = _PATHLIKE_RE.sub(" ", t)
    t = _EXT_TOKEN_RE.sub(" ", t)
    return t


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except Exception:
        return default


def korean_ratio(text: str) -> float:
    """산문에서 한글 문자가 차지하는 비율(0.0~1.0). 판정 불가 시 1.0(한국어 간주)."""
    prose = _prose_only(text or "")
    hangul = len(_HANGUL_RE.findall(prose))
    cjk = len(_CJK_RE.findall(prose))
    latin = len(_LATIN_RE.findall(prose))
    total = hangul + cjk + latin
    if total == 0:
        return 1.0
    return hangul / total


def is_korean_output(text: str, min_prose: Optional[int] = None, threshold: Optional[float] = None) -> bool:
    """응답이 한국어 산문으로 작성되었는지 판정한다.

    - 코드/경로/식별자를 제외한 산문 기준.
    - 산문이 너무 짧으면(min_prose 미만) 판정 불가로 보고 True(한국어) 처리하여
      불필요한 재작성 요청을 방지한다.
    - 한글이 전혀 없고 비한국어 산문이 충분하면 False(비한국어).
    """
    if not text or not isinstance(text, str):
        return True
    if min_prose is None:
        min_prose = int(_env_float("AGY_OUTPUT_KOREAN_MIN_PROSE", 20))
    if threshold is None:
        threshold = _env_float("AGY_OUTPUT_KOREAN_RATIO", 0.15)

    prose = _prose_only(text)
    hangul = len(_HANGUL_RE.findall(prose))
    cjk = len(_CJK_RE.findall(prose))
    latin = len(_LATIN_RE.findall(prose))
    total = hangul + cjk + latin
    if total < max(1, int(min_prose)):
        return True
    if hangul == 0:
        return False
    return (hangul / total) >= threshold


KOREAN_OUTPUT_PROMPT_SUFFIX = (
    "\n\n🚨 **[출력 언어 절대 규칙 — 한국어 전용]**\n"
    "- 모든 설명/판정/근거 문장은 **반드시 자연스러운 한국어로만** 작성하십시오.\n"
    "- 영어/중국어/일본어 등 외국어 문장 출력은 엄격히 금지됩니다. (코드·명령어·파일명·식별자는 원문 유지 가능)\n"
)

KOREAN_REPAIR_INSTRUCTION = (
    "🚨 **[출력 언어 강제: 한국어 전용]**\n"
    "직전 답변이 한국어가 아닌 외국어(영어/중국어/일본어 등) 문장으로 작성되어 시스템 규칙을 위반했습니다.\n"
    "이 시스템의 모든 에이전트 보고/판정/설명은 **반드시 자연스러운 한국어 문장**으로만 작성해야 합니다.\n"
    "- 직전 답변의 판정(합격/반려)과 검증 근거는 그대로 유지하십시오.\n"
    "- 첫 줄은 반드시 `[STATUS: ...]` 태그로 시작하십시오. (해당 없는 역할은 제외)\n"
    "- 코드/명령어/파일명/식별자는 원문 그대로 두어도 되지만, 설명 문장은 한국어로 쓰십시오.\n"
    "- 동일한 내용을 처음부터 끝까지 한국어로 다시 작성하여 출력하십시오."
)

# 재작성 시 도구 호출/파일 수정을 막기 위한 추가 지침 (Coder/Analyst/Architect 공통)
REWRITE_ONLY_NOTE = (
    "※ **이번 한국어 재작성 요청에서는 절대 도구를 호출하거나 파일을 생성/수정/탐색하지 마십시오.** "
    "직전에 보고한 작업 내역과 검증 결과만 한국어 문장으로 다시 작성하여 텍스트로 출력하십시오."
)
CODER_REPAIR_NOTE = REWRITE_ONLY_NOTE


def build_korean_repair_prompt(previous_output: str, max_chars: int = 8000) -> str:
    prev = (previous_output or "").strip()
    if len(prev) > max_chars:
        prev = prev[:max_chars] + "\n...(이하 생략)"
    return f"{KOREAN_REPAIR_INSTRUCTION}\n\n--- [직전 답변 원문] ---\n{prev}"


def _looks_like_system_error(text: str) -> bool:
    if not text or not isinstance(text, str):
        return True
    stripped = text.strip()
    if not stripped:
        return True
    return stripped.startswith("⚠️") or stripped.startswith("🛑")


async def guard_output_async(
    text: str,
    *,
    approved: Optional[bool] = None,
    kind: Optional[str] = None,
    rerun: Optional[Callable[[str], "object"]] = None,
    max_repair: int = 1,
    repair_note: str = "",
) -> str:
    """판정 헤더 정규화 + (필요 시) 한국어 재작성 1회를 수행한다.

    Args:
        text: 원본 에이전트 출력.
        approved: 파이프라인이 계산한 승인(True)/반려(False) 결정. None이면 추론 안 함.
        kind: "qa" | "auditor" | None. None이면 판정 태그 강제를 하지 않는다.
        rerun: 한국어 재작성을 수행할 async callable(prompt) -> str.
        max_repair: 최대 재작성 시도 횟수.
        repair_note: 재작성 프롬프트에 덧붙일 역할별 추가 지침(예: Coder 파일수정 금지).
    """
    if not text or not isinstance(text, str):
        return text or ""
    if os.environ.get("AGY_OUTPUT_GUARD", "1") == "0":
        return text

    # 1) 판정 태그 첫 줄 강제
    text = ensure_verdict_first_line(text, approved=approved, kind=kind)

    # 2) 한국어 강제 (비한국어 감지 시 1회 재작성)
    if (
        rerun is not None
        and os.environ.get("AGY_OUTPUT_KOREAN_REPAIR", "1") != "0"
        and max_repair > 0
        and not is_korean_output(text)
    ):
        try:
            repair_prompt = build_korean_repair_prompt(text)
            if repair_note:
                repair_prompt = f"{repair_prompt}\n\n{repair_note}"
            repaired = await rerun(repair_prompt)
        except Exception:
            repaired = None
        if isinstance(repaired, str) and not _looks_like_system_error(repaired) and is_korean_output(repaired):
            text = ensure_verdict_first_line(repaired, approved=approved, kind=kind)

    return text


# 하위 호환용 별칭
guard_reviewer_output = guard_output_async
