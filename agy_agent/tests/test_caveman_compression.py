import os
import sys
import unittest

# Ensure agy_agent path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from trio_agent_runner import TrioAgentRunner, compact_agent_context


class TestCavemanCompression(unittest.TestCase):
    """
    Tests for Caveman Style token compression and output optimization:
    1. Output Token Diet via compact_agent_context
    2. Preservation of crucial multi-agent protocol tags ([STATUS: ...], [VALUE: ...], [ACTION: ...])
    3. Parsing compatibility with telegraphic / noun-ending (개조식) outputs
    4. Elimination of decorative banners and polite filler
    """

    def test_compact_agent_context_strips_decorative_lines_and_greetings(self):
        verbose_text = (
            "안녕하세요! 시니어 QA 검증 결과입니다.\n"
            "────────────────────────────────────────\n"
            "[STATUS: APPROVED]\n"
            "════════════════════════════════════════\n"
            "감사합니다! 주요 변경 사항 검증 완료.\n"
            "----------------------------------------\n"
            "1. 목적: 캐시 스레드 세이프티 확보\n"
            "2. 변경: Mutex 동기화 적용 완료\n"
            "3. 검증 결과: 100회 동시성 테스트 통과\n"
        )
        compacted = compact_agent_context(verbose_text, max_chars=1000)

        # Decorative lines must be gone
        self.assertNotIn("────────────────────────────────────────", compacted)
        self.assertNotIn("════════════════════════════════════════", compacted)
        self.assertNotIn("----------------------------------------", compacted)

        # Polite greetings at line starts must be stripped
        self.assertNotIn("안녕하세요!", compacted)
        self.assertNotIn("감사합니다!", compacted)

        # Crucial status tag and core technical contents must be preserved
        self.assertIn("[STATUS: APPROVED]", compacted)
        self.assertIn("1. 목적: 캐시 스레드 세이프티 확보", compacted)
        self.assertIn("3. 검증 결과: 100회 동시성 테스트 통과", compacted)

    def test_compact_agent_context_truncates_long_code_blocks(self):
        lines = ["[STATUS: APPROVED]", "```python"]
        for i in range(20):
            lines.append(f"    var_{i} = do_something_verbose({i})")
        lines.append("```")
        lines.append("검증 완료")
        code_text = "\n".join(lines)

        compacted = compact_agent_context(code_text, max_chars=2000)
        self.assertIn("[STATUS: APPROVED]", compacted)
        self.assertIn("... [중략: 도구로 적용된 코드 본문 생략 (Token Diet)] ...", compacted)
        self.assertIn("검증 완료", compacted)
        # Should not have all 20 lines
        self.assertNotIn("var_19 = do_something_verbose(19)", compacted)

    def test_compact_agent_context_respects_max_chars_and_preserves_head_tail(self):
        long_body = "핵심 내용 요약. " * 300
        text = f"[STATUS: APPROVED]\n\n{long_body}\n\n최종 판정: 통과"
        compacted = compact_agent_context(text, max_chars=500)

        self.assertLessEqual(len(compacted), 650)
        self.assertIn("[STATUS: APPROVED]", compacted)
        self.assertIn("최종 판정: 통과", compacted)
        self.assertIn("... [중략: 세부 중간 로그 생략 (토큰 다이어트)] ...", compacted)

    def test_caveman_telegraphic_qa_approval_parsing(self):
        # 1. Standard Caveman Approved
        caveman_qa_pass = (
            "[STATUS: APPROVED]\n"
            "1. 목적: 사용자 요청 390px 터치 타깃 검증\n"
            "2. 확인: MainActivity.kt:45 padding 24dp 정상 적용 확인\n"
            "3. 결론: 문법/런타임 결함 없음. 물리 컴파일 통과"
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(caveman_qa_pass))

        # 2. Direct Patch Approved
        caveman_qa_direct_patch = (
            "[STATUS: APPROVED (QA Direct Patch)]\n"
            "• 수정: utils.py:12 오타 정정 및 단위 테스트 통과 완료"
        )
        self.assertTrue(TrioAgentRunner.is_qa_approved(caveman_qa_direct_patch))

        # 3. Caveman Rejection with concrete defect specifics
        caveman_qa_reject = (
            "[STATUS: REJECTED]\n"
            "• 결함: MainActivity.kt:89 NullPointerException 위험 감지\n"
            "• 원인: nullable session_id 미방어\n"
            "• 지침: if-let 또는 safe call(?.) 적용 후 재제출 요청"
        )
        self.assertFalse(TrioAgentRunner.is_qa_approved(caveman_qa_reject))

    def test_caveman_telegraphic_auditor_approval_parsing(self):
        # 1. Standard Caveman Auditor Pass
        caveman_auditor_pass = (
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
            "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
            "• 점수: 98점. 감점 요인 0건 및 사용자 요구 100% 충족 확인.\n\n"
            "### 📁 생성/수정된 핵심 산출물 및 파일\n"
            "• `app.py`: 세션 자동 갱신 로직 추가 [기존 미갱신 ➔ 자동 갱신]\n\n"
            "### 🔍 세부 구현 및 품질 검증 결과\n"
            "• 7대 게이트 무결성 및 런타임 크래시 0건 확보\n\n"
            "### 🚀 실행 및 확인 방법\n"
            "• `python app.py` 실행 후 브라우저 접속\n\n"
            "---\n"
            "### 🛡️ 3중 협업 품질 요약\n"
            "• 👨‍💻 Coder 구현 완료 | 🕵️ QA 통과 | ⚖️ Auditor 최종 승인"
        )
        self.assertTrue(TrioAgentRunner.is_auditor_approved(caveman_auditor_pass))

        # 2. Caveman Auditor Rejection
        caveman_auditor_fail = (
            "[STATUS: AUDIT_FAILED] [VALUE: MEDIUM] [ACTION: REVISE]\n\n"
            "• 반려 사유: 사용자 요청 핵심 기능(소리 효과) 미구현 감지\n"
            "• Coder 조치: sound_manager.py 연동 및 재생 로직 보강 필수"
        )
        self.assertFalse(TrioAgentRunner.is_auditor_approved(caveman_auditor_fail))

        # 3. Caveman Auditor Stop
        caveman_auditor_stop = (
            "[STATUS: AUDIT_STOP] [VALUE: LOW] [ACTION: STOP]\n\n"
            "• 중단 사유: 기획 의도와 무관한 엉뚱한 라이브러리 추가로 인한 기술 부채 유발"
        )
        self.assertFalse(TrioAgentRunner.is_auditor_approved(caveman_auditor_stop))

    def test_consulting_and_research_runner_compact_integration(self):
        from consulting_runner import ConsultingRunner
        from research_runner import ResearchRunner

        sample_output = (
            "안녕하세요!\n"
            "────────────────────────────────────────\n"
            "시장 조사 결과 분석 완료\n"
            "════════════════════════════════════════\n"
            "• 모델 A: 120만원\n"
            "• 모델 B: 150만원\n"
        )
        compacted = compact_agent_context(sample_output, max_chars=200)
        self.assertNotIn("────────────────────────────────────────", compacted)
        self.assertNotIn("════════════════════════════════════════", compacted)
        self.assertNotIn("안녕하세요!", compacted)
        self.assertIn("모델 A: 120만원", compacted)


if __name__ == "__main__":
    unittest.main()
