import asyncio
import os
import unittest
from unittest.mock import patch

from output_guard import (
    ensure_verdict_first_line,
    is_korean_output,
    korean_ratio,
    promote_existing_verdict,
    guard_output_async,
    KOREAN_OUTPUT_PROMPT_SUFFIX,
)


class TestVerdictFirstLine(unittest.TestCase):
    def test_promote_mid_body_verdict_to_first_line(self):
        text = "코드 검토를 완료했습니다.\n모든 게이트를 통과했습니다.\n[STATUS: APPROVED]"
        out = ensure_verdict_first_line(text, approved=True, kind="qa")
        self.assertTrue(out.startswith("[STATUS: APPROVED]"))
        # 원래 태그 줄(하단)은 제거되고 본문은 보존된다.
        self.assertEqual(out.count("[STATUS: APPROVED]"), 1)
        self.assertIn("코드 검토를 완료했습니다.", out)

    def test_prepend_inferred_approved_tag_when_missing(self):
        text = "모든 요구사항이 충족되었습니다. 검증 완료."
        out = ensure_verdict_first_line(text, approved=True, kind="qa")
        self.assertTrue(out.startswith("[STATUS: APPROVED]\n"))
        self.assertIn(text, out)

    def test_prepend_inferred_rejected_tag_when_missing(self):
        text = "차단 결함이 2건 발견되었습니다. 수정이 필요합니다."
        out = ensure_verdict_first_line(text, approved=False, kind="qa")
        self.assertTrue(out.startswith("[STATUS: REJECTED]\n"))

    def test_auditor_tag_mapping(self):
        passed = ensure_verdict_first_line("감사 결과 문제 없음.", approved=True, kind="auditor")
        failed = ensure_verdict_first_line("보안 결함 발견.", approved=False, kind="auditor")
        self.assertTrue(passed.startswith("[STATUS: AUDIT_PASSED]"))
        self.assertTrue(failed.startswith("[STATUS: AUDIT_FAILED]"))

    def test_existing_first_line_tag_is_preserved_and_normalized(self):
        text = "**판정**: [STATUS: REJECTED]\n결함 목록은 아래와 같습니다."
        out = ensure_verdict_first_line(text, approved=False, kind="qa")
        self.assertTrue(out.startswith("[STATUS: REJECTED]"))
        self.assertEqual(out.count("[STATUS: REJECTED]"), 1)

    def test_kind_none_does_not_inject_verdict(self):
        text = "아키텍처 자문 결과를 정리했습니다."
        out = ensure_verdict_first_line(text, approved=None, kind=None)
        self.assertEqual(out, text)

    def test_incidental_mention_is_not_promoted(self):
        # 본문 중 태그 '언급'은 선언 줄이 아니므로 승격/주입하지 않는다.
        text = "이전 라운드에서 [STATUS: REJECTED]를 선언한 바와 같이 문제가 있었습니다."
        out = promote_existing_verdict(text)
        self.assertEqual(out, text)

    def test_promote_existing_standalone_declaration(self):
        text = "검토 내용 요약\n\n[STATUS: REJECTED]\n\n상세 근거"
        out = promote_existing_verdict(text)
        self.assertTrue(out.startswith("[STATUS: REJECTED]\n"))

    def test_qa_direct_patch_parenthetical_preserved(self):
        text = "직접 패치 완료.\n[STATUS: APPROVED (QA Direct Patch)]"
        out = ensure_verdict_first_line(text, approved=True, kind="qa")
        self.assertTrue(out.startswith("[STATUS: APPROVED (QA Direct Patch)]"))

    def test_first_line_tag_with_trailing_content_not_duplicated(self):
        text = "[STATUS: REJECTED] 결함 3건이 발견되었습니다."
        out = ensure_verdict_first_line(text, approved=False, kind="qa")
        self.assertEqual(out.count("[STATUS: REJECTED]"), 1)
        self.assertTrue(out.startswith("[STATUS: REJECTED]"))
        self.assertIn("결함 3건", out)


class TestKoreanDetection(unittest.TestCase):
    def test_korean_sentence_is_korean(self):
        text = "코드 변경 사항을 검토했습니다. 핸들러의 예외 처리가 적절하며 차단 결함이 없습니다."
        self.assertTrue(is_korean_output(text))

    def test_english_sentence_is_not_korean(self):
        text = "QA2 verified edge cases across the handler and utility modules. No blocking defects were found."
        self.assertFalse(is_korean_output(text))

    def test_chinese_sentence_is_not_korean(self):
        text = "这是一个非常好的实现。所有测试已经通过，没有发现任何缺陷。"
        self.assertFalse(is_korean_output(text))

    def test_korean_with_code_and_identifiers_is_korean(self):
        text = (
            "`EnterpriseHandler.process()` 메서드와 `utils.py` 헬퍼를 검토했습니다. "
            "라인 30~55의 예외 처리가 정상 동작함을 확인했습니다."
        )
        self.assertTrue(is_korean_output(text))

    def test_short_label_is_not_flagged(self):
        self.assertTrue(is_korean_output("main.py"))

    def test_korean_ratio_bounds(self):
        self.assertGreater(korean_ratio("모든 검증을 통과했습니다."), 0.9)
        self.assertLess(korean_ratio("All verification passed successfully."), 0.1)


class TestGuardOutputAsync(unittest.IsolatedAsyncioTestCase):
    @patch.dict(os.environ, {"AGY_OUTPUT_KOREAN_REPAIR": "1"})
    async def test_repairs_non_korean_via_rerun(self):
        calls = []

        async def rerun(prompt: str) -> str:
            calls.append(prompt)
            return "[STATUS: REJECTED]\n차단 결함 1건이 발견되어 반려합니다."

        out = await guard_output_async(
            "Blocking defect found in handler.py. Rejected.",
            approved=False,
            kind="qa",
            rerun=rerun,
        )
        self.assertEqual(len(calls), 1)
        self.assertTrue(out.startswith("[STATUS: REJECTED]"))
        self.assertIn("차단 결함", out)

    async def test_no_rerun_when_already_korean(self):
        calls = []

        async def rerun(prompt: str) -> str:
            calls.append(prompt)
            return "호출되면 안 됨"

        out = await guard_output_async(
            "차단 결함이 없어 승인합니다.",
            approved=True,
            kind="qa",
            rerun=rerun,
        )
        self.assertEqual(calls, [])
        self.assertTrue(out.startswith("[STATUS: APPROVED]"))

    async def test_keeps_original_when_repair_fails(self):
        async def rerun(prompt: str) -> str:
            return "⚠️ 실행 오류"

        original = "Blocking defect found. Rejected."
        with patch.dict(os.environ, {"AGY_OUTPUT_KOREAN_REPAIR": "1"}):
            out = await guard_output_async(
                original, approved=False, kind="qa", rerun=rerun
            )
        # 재작성 실패 시 판정 태그만 부착된 원문이 유지된다.
        self.assertTrue(out.startswith("[STATUS: REJECTED]"))
        self.assertIn("Blocking defect found.", out)

    async def test_guard_disabled_env(self):
        import os

        os.environ["AGY_OUTPUT_GUARD"] = "0"
        try:
            original = "Blocking defect found. Rejected."
            out = await guard_output_async(original, approved=False, kind="qa")
            self.assertEqual(out, original)
        finally:
            os.environ.pop("AGY_OUTPUT_GUARD", None)

    def test_korean_prompt_suffix_mentions_korean(self):
        self.assertIn("한국어", KOREAN_OUTPUT_PROMPT_SUFFIX)


if __name__ == "__main__":
    unittest.main()
