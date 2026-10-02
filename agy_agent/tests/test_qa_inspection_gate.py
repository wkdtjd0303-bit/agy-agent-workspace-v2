#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_qa_inspection_gate.py

QA 최소 검수 심도 게이트(`TrioAgentRunner._validate_qa_inspection_depth`)의
과도한 기계 제약이 제거되었음을 검증하는 회귀 테스트.

[배경] 과거에는 QA 승인문에 '파일 확장자 인용 + 코드 토큰(def/class/라인 N)'을
정규식으로 기계 강제하여, 파일명/코드 토큰이 없는 정당한 총평형 승인도
'형식적 승인(Rubber-stamp)'으로 반려 → Coder↔QA 무한 루프를 유발했다.
이제는 '실제 검토 산문이 거의 없는 극단적 단답 승인'만 차단한다.
"""

import os
import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

os.environ["UNITTEST_RUNNING"] = "1"

from trio_agent_runner import TrioAgentRunner  # noqa: E402


class TestQAInspectionGate(unittest.TestCase):

    def test_natural_language_approval_without_file_or_code_tokens_passes(self):
        """파일명/코드 토큰 없는 총평형 승인도 더 이상 기계 반려되지 않는다."""
        approval = (
            "[STATUS: APPROVED]\n"
            "[BLOCKING_DEFECTS: 0]\n"
            "사용자가 요청한 기능이 실제 소스코드에 반영되어 정상 동작함을 확인했습니다. "
            "런타임 크래시나 차단 결함은 발견되지 않았으며, 예외 처리도 적절합니다. "
            "비차단 개선 사항은 차기 백로그로 기록합니다."
        )
        ok, reason = TrioAgentRunner._validate_qa_inspection_depth(approval, "/tmp")
        self.assertTrue(ok, reason)

    def test_short_conversational_approval_passes_when_substantive(self):
        """간결하더라도 검증 근거가 서술된 승인은 통과한다."""
        approval = (
            "[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n"
            "빌드 성공과 핵심 시나리오 동작을 직접 확인했고 차단 결함이 없어 승인합니다."
        )
        ok, reason = TrioAgentRunner._validate_qa_inspection_depth(approval, "/tmp")
        self.assertTrue(ok, reason)

    def test_tag_only_rubber_stamp_is_rejected(self):
        """태그만 있고 실제 검토 산문이 없는 극단적 단답 승인은 차단한다."""
        ok, reason = TrioAgentRunner._validate_qa_inspection_depth(
            "[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]", "/tmp"
        )
        self.assertFalse(ok)
        self.assertIn("실질적인 검토 내용", reason)

    def test_empty_output_is_rejected(self):
        ok, reason = TrioAgentRunner._validate_qa_inspection_depth("", "/tmp")
        self.assertFalse(ok)
        self.assertIn("비어있", reason)


if __name__ == "__main__":
    unittest.main()
