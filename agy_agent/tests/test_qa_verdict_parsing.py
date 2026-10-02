import os
import sys
import unittest

# Ensure agy_agent path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from trio_agent_runner import TrioAgentRunner
from output_guard import ensure_verdict_first_line


class TestQaVerdictParsing(unittest.TestCase):
    """
    Regression lock for the QA verdict parser (TrioAgentRunner.is_qa_approved).

    Bug: in !mode 2 the QA could approve a task yet the pipeline materialized it as
    `[STATUS: REJECTED]`, because the parser only understood the exact ASCII form
    `[STATUS: APPROVED]`. Legitimate approvals written with a fullwidth colon,
    a Korean status label, or plain natural language were misread as rejections
    and then deterministically converted into an explicit rejection by the output
    guard. These tests ensure both APPROVED and REJECTED decisions are recognized.
    """

    APPROVED_VARIANTS = {
        "ascii_tag": "[STATUS: APPROVED]\napp.py 검토 완료.",
        "fullwidth_colon": "[STATUS：APPROVED]\napp.py 검토 완료.",
        "korean_bracket_label": "[상태: 승인]\napp.py 검토 완료.",
        "korean_verdict_label": "판정: 승인\napp.py 검토 완료.",
        "korean_pass": "[판정: 통과]\napp.py 검토 완료.",
        "verdict_prefix_ascii": "판정: [STATUS: APPROVED]\napp.py 검토 완료.",
        "natural_approve": "app.py 이상 없음을 확인하여 승인합니다.",
        "natural_no_defect": "app.py 결함 없음. 승인합니다.",
        "negated_fix_not_needed": "검증 결과 app.py 이상 없음. 수정 필요 사항은 없습니다.",
        "negated_banryeo_none": "반려 사유 없음. 정상 동작 확인했습니다.",
        "negated_problem_none": "app.py 문제 없음. 정상 동작함을 확인했습니다.",
    }

    REJECTED_VARIANTS = {
        "ascii_tag": "[STATUS: REJECTED]\napp.py 런타임 크래시.",
        "fullwidth_colon": "[STATUS：REJECTED]\napp.py 런타임 크래시.",
        "korean_label": "[상태: 반려]\napp.py 런타임 크래시.",
        "korean_verdict": "판정: 반려\napp.py 런타임 크래시.",
        "natural_defect": "app.py 에 치명적 결함 발견. 수정 필요합니다.",
        "natural_banryeo": "결함이 있어 반려합니다.",
        "natural_defect_still": "치명적 결함은 없으나 핵심 기능 미동작이 확인되어 반려합니다.",
        "natural_bug": "app.py 에 NullPointerException 버그가 발견되었습니다. 보완 바랍니다.",
    }

    def test_approvals_are_recognized(self):
        for name, text in self.APPROVED_VARIANTS.items():
            self.assertTrue(
                TrioAgentRunner.is_qa_approved(text),
                f"Approval variant '{name}' should be recognized as APPROVED.",
            )

    def test_rejections_are_recognized(self):
        for name, text in self.REJECTED_VARIANTS.items():
            self.assertFalse(
                TrioAgentRunner.is_qa_approved(text),
                f"Rejection variant '{name}' should be recognized as REJECTED.",
            )

    def test_guard_normalizes_korean_approval_tag(self):
        out = ensure_verdict_first_line("[상태: 승인]\napp.py 검토 완료.", approved=True, kind="qa")
        self.assertTrue(out.startswith("[STATUS: APPROVED]"))

    def test_guard_normalizes_fullwidth_colon_rejection(self):
        out = ensure_verdict_first_line("[STATUS：REJECTED]\n크래시.", approved=False, kind="qa")
        self.assertTrue(out.startswith("[STATUS: REJECTED]"))

    def test_guard_does_not_reject_unambiguous_approval(self):
        # Regression: "no correction needed" must not be read as a rejection reason.
        text = "app.py 이상 없음. 수정 필요 사항은 없습니다."
        self.assertTrue(TrioAgentRunner.is_qa_approved(text))
        out = ensure_verdict_first_line(text, approved=True, kind="qa")
        self.assertTrue(out.startswith("[STATUS: APPROVED]"))


class TestAuditorVerdictParsing(unittest.TestCase):
    """Same regression lock for TrioAgentRunner.is_auditor_approved (!mode 2 final gate)."""

    APPROVED_VARIANTS = {
        "ascii_tag": "[STATUS: AUDIT_PASSED]\n최종 승인.",
        "fullwidth_colon": "[STATUS：AUDIT_PASSED]\n감사 통과.",
        "korean_label": "[상태: 감사 통과]\n감사 통과.",
        "value_action": "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n최종 승인.",
        "first_line_trailing": "[STATUS: AUDIT_PASSED] 최종 감사 통과.",
        "closing_verdict_trailing": "[STATUS: AUDIT_PASSED]\n\n상세:\n판정: [STATUS: AUDIT_PASSED] 모든 테스트 통과.",
    }

    REJECTED_VARIANTS = {
        "ascii_tag": "[STATUS: AUDIT_FAILED]\n치명 결함.",
        "value_action": "[STATUS: AUDIT_FAILED] [VALUE: LOW] [ACTION: STOP]\n요구사항 불일치.",
        "closing_verdict_overrides": "[STATUS: AUDIT_PASSED]\n초기 검토...\n판정: [STATUS: AUDIT_FAILED]",
    }

    def test_auditor_approvals_are_recognized(self):
        for name, text in self.APPROVED_VARIANTS.items():
            self.assertTrue(
                TrioAgentRunner.is_auditor_approved(text),
                f"Auditor approval variant '{name}' should be recognized as PASSED.",
            )

    def test_auditor_rejections_are_recognized(self):
        for name, text in self.REJECTED_VARIANTS.items():
            self.assertFalse(
                TrioAgentRunner.is_auditor_approved(text),
                f"Auditor rejection variant '{name}' should be recognized as FAILED.",
            )

    def test_body_bullet_mention_does_not_override_top_pass(self):
        # Regression: a bullet mentioning [STATUS: AUDIT_FAILED] in the body must not
        # flip a top-line [STATUS: AUDIT_PASSED] into a failure.
        report = (
            "[STATUS: AUDIT_PASSED]\n\n"
            "검토 개요:\n"
            "- [STATUS: AUDIT_FAILED] 방어 여부: 정상 작동 확인\n"
            "- 결함 없음\n"
        )
        self.assertTrue(TrioAgentRunner.is_auditor_approved(report))


if __name__ == "__main__":
    unittest.main()
