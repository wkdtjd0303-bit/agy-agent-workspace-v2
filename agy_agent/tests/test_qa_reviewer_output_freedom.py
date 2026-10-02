#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 리뷰어 프롬프트 분량 자유 회귀 고정.

배경: 과거 QA 리뷰어(Fact Checker / Tech Reviewer / qa1) 프롬프트에
"3~5줄 내외의 명사형 종결 개조식" 이라는 하드코딩된 분량 상한이 있어,
Coder 대비 판정 근거와 고도화 권고를 충분히 서술하지 못했다.
본 테스트는 해당 상한이 재도입되지 않고, QA가 충분한 의견을 개진하도록
지시가 유지됨을 디스크 소스로 검증한다.
"""

import pathlib
import unittest

_AGENT_DIR = pathlib.Path(__file__).resolve().parents[1]
_REMOVED_HARD_CAP = "3~5줄 내외의 명사형 종결 개조식"
_QA_PROMPT_FILES = {
    "research_runner.py": "충분한 검증 의견 개진",
    "consulting_runner.py": "충분한 비판 의견 개진",
    "trio_agent_runner.py": "충분히 구체적으로 작성하십시오",
}

# 비-QA 역할(설계/전략/감사) 프롬프트에서 제거된 분량 상한.
# 설계·전략·감사 답변이 500~800자로 눌려 부실해지지 않도록 재도입을 금지한다.
_REMOVED_NON_QA_CAP = "500~800자 이내"
_NON_QA_PROMPT_FILES = ("research_runner.py", "consulting_runner.py")


class TestQAReviewerOutputFreedom(unittest.TestCase):
    def test_hard_brevity_cap_not_reintroduced(self):
        """QA 리뷰어 분량 상한 문구가 어떤 러너에도 재도입되지 않아야 한다."""
        for name in _QA_PROMPT_FILES:
            src = (_AGENT_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn(
                _REMOVED_HARD_CAP,
                src,
                f"{name} 에 QA 분량 상한이 재도입되었습니다.",
            )

    def test_qa_reviewers_told_to_speak_fully(self):
        """각 QA 리뷰어 프롬프트는 충분한 의견 개진 지시를 유지해야 한다."""
        for name, marker in _QA_PROMPT_FILES.items():
            src = (_AGENT_DIR / name).read_text(encoding="utf-8")
            self.assertIn(
                marker,
                src,
                f"{name} 에서 QA 충분 의견 개진 지시가 누락되었습니다.",
            )

    def test_non_qa_role_brevity_cap_not_reintroduced(self):
        """설계/전략/감사(비-QA) 역할 프롬프트에 분량 상한이 남지 않아야 한다."""
        for name in _NON_QA_PROMPT_FILES:
            src = (_AGENT_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn(
                _REMOVED_NON_QA_CAP,
                src,
                f"{name} 에 비-QA 역할 분량 상한이 재도입되었습니다.",
            )
            self.assertNotIn(
                "케이브맨",
                src,
                f"{name} 에 케이브맨 토큰 다이어트 문구가 재도입되었습니다.",
            )


if __name__ == "__main__":
    unittest.main()
