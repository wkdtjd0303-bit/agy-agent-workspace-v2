"""AGY_AGENT.md 로더 — 모든 에이전트 프롬프트에 코딩 행동규범을 주입한다.

``AGY_AGENT.md`` 안의 ``<!-- AGY_RUNTIME_BEGIN --> ... <!-- AGY_RUNTIME_END -->``
구간만 추출해 토큰 다이어트를 지킨다. 파일이 없거나 형식이 어긋나면 조용히 빈
문자열을 반환하여 프롬프트/실행에 절대 영향을 주지 않는다(무중단 폴백).

사용처:
  - ``engine_manager.AUTONOMY_NO_QUESTION_DIRECTIVE`` (전 엔진 프롬프트 최상단)
  - ``agent_runner.TOOL_SAFETY_HEADER`` (AGY 네이티브 CLI 프롬프트)
"""
from __future__ import annotations

import os
import re

_GUIDELINES_FILENAME = "AGY_AGENT.md"
_BEGIN = "<!-- AGY_RUNTIME_BEGIN -->"
_END = "<!-- AGY_RUNTIME_END -->"
_MAX_CHARS = 1200


def load_agent_guidelines() -> str:
    """AGY_AGENT.md 의 런타임 요약 구간을 반환한다 (없으면 "")."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _GUIDELINES_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return ""

    digest = raw
    m = re.search(re.escape(_BEGIN) + r"(.*?)" + re.escape(_END), raw, re.DOTALL)
    if m:
        digest = m.group(1)
    digest = digest.strip()
    if len(digest) > _MAX_CHARS:
        digest = digest[:_MAX_CHARS].rstrip() + "\n…(이하 생략)"
    return digest


AGY_AGENT_GUIDELINES = load_agent_guidelines()
