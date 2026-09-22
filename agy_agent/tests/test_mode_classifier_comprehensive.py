import os
import sys
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from mode_classifier import classify_mode, classify_task_type, get_mode_description


class TestModeClassifierComprehensive(unittest.TestCase):
    """
    Extensive test suite for mode_classifier edge cases, boundary conditions,
    and extreme input values.
    """

    def test_classify_task_type_boundary_and_nulls(self):
        # Null and empty
        self.assertEqual(classify_task_type(""), "general")
        self.assertEqual(classify_task_type(None), "general")
        self.assertEqual(classify_task_type("   \n\t  "), "general")

        # Non-string inputs
        self.assertEqual(classify_task_type(12345), "general")
        self.assertEqual(classify_task_type({"key": "val"}), "general")

    def test_classify_task_type_research_intent(self):
        prompts = [
            "미니 PC 가성비 추천해줘",
            "당근마켓에서 서피스 프로 시세 조사해봐",
            "M3 맥북과 서피스 프로 9 스펙 및 가격 비교표 작성해줘",
            "시중 임베디드 AI 하드웨어 시장 조사 및 구매 가이드",
            "다나와 최저가 비교해줘",
        ]
        for p in prompts:
            self.assertEqual(classify_task_type(p), "research", f"Failed for: {p}")

    def test_classify_task_type_coding_intent(self):
        prompts = [
            "로그인 API 엔드포인트 구현해줘",
            "리액트 대시보드 컴포넌트 추가 및 디자인 개선해줘",
            "메모리 누수 버그 수정 및 디버깅",
            "Playwright E2E 테스트 코드 작성 및 검증",
            "FastAPI 백엔드 쿼리 최적화 패치",
        ]
        for p in prompts:
            self.assertEqual(classify_task_type(p), "coding", f"Failed for: {p}")

    def test_classify_mode_pinned_modes(self):
        # Single pinned
        for m in ["single", "solo", "단일", "싱글"]:
            mode, desc = classify_mode("아무리 복잡한 코드 작성 요청이라도", session_mode=m)
            self.assertEqual(mode, "single")
            self.assertIn("단일 에이전트", desc)

        # Mode 2: Pair pinned (Human-in-the-loop)
        for m in ["pair", "trio", "dual", "collab", "team", "3중", "페어", "협업", "2"]:
            mode, desc = classify_mode("단순 오타 수정", session_mode=m)
            self.assertEqual(mode, "pair")
            self.assertIn("협업", desc)

        # Mode 3: Autonomous Trio pinned (Commander closed loop)
        for m in ["3", "mode3", "자율3", "무인3", "3자율", "3차자율"]:
            mode, desc = classify_mode("단순 오타 수정", session_mode=m)
            self.assertEqual(mode, "mode3")
            self.assertIn("무인 자율 순환", desc)

        # Read pinned
        for m in ["read", "readonly", "consult", "planning", "기획", "자문"]:
            mode, desc = classify_mode("코드 구현해줘", session_mode=m)
            self.assertEqual(mode, "read")
            self.assertIn("읽기", desc)

        # Mode 2+: Pair+ pinned (!mode 2+)
        for m in ["2+", "2plus", "2플러스", "pair_plus", "pair+"]:
            mode, desc = classify_mode("작업해줘", session_mode=m)
            self.assertEqual(mode, "pair_quad")
            self.assertIn("!mode 2+", desc)
            self.assertTrue("4중 쿼드" in desc or "다중" in desc)

        # Mode 3+: Autonomous Multi-QA pinned (!mode 3+)
        for m in ["3+", "3plus", "3플러스", "mode3+", "mode3_plus", "자율3+", "무인3+"]:
            mode, desc = classify_mode("작업해줘", session_mode=m)
            self.assertEqual(mode, "mode3_plus")
            self.assertIn("!mode 3+", desc)
            self.assertTrue("4중 쿼드" in desc or "다중" in desc)

        for m in ["pair_quad", "quad_pair"]:
            mode, desc = classify_mode("작업해줘", session_mode=m)
            self.assertEqual(mode, "pair_quad")
            self.assertTrue("4중 쿼드" in desc or "다중" in desc)

    def test_classify_mode_auto_heuristics(self):
        # 1. Trivial edits -> single
        mode, _ = classify_mode("README.md 오타 수정해줘", session_mode="auto")
        self.assertEqual(mode, "single")

        mode, _ = classify_mode("포트 번호 8080으로 변경해줘", session_mode="auto")
        self.assertEqual(mode, "single")

        # 2. Pure questions -> single
        mode, _ = classify_mode("Docker와 Podman의 차이가 뭐야?", session_mode="auto")
        self.assertEqual(mode, "single")

        mode, _ = classify_mode("PRD.md 내용 요약해줘", session_mode="auto")
        self.assertEqual(mode, "single")

        # 3. Development / UI / Bug fix -> pair
        mode, _ = classify_mode("결제 시스템 모듈 신규 구현 및 테스트 코드 작성", session_mode="auto")
        self.assertEqual(mode, "pair")

        mode, _ = classify_mode("앱 크래시 발생 버그 해결하고 핫픽스 패치해줘", session_mode="auto")
        self.assertEqual(mode, "pair")

        # 4. Long / multi-step prompts -> pair
        long_prompt = "1. 첫번째 단계 수행\n2. 두번째 단계 검증\n3. 배포 파이프라인 연동"
        mode, _ = classify_mode(long_prompt, session_mode="auto")
        self.assertEqual(mode, "pair")

        # 5. Code block containing prompt -> pair
        code_prompt = "다음 코드를 리팩토링해줘:\n```python\ndef foo(): pass\n```"
        mode, _ = classify_mode(code_prompt, session_mode="auto")
        self.assertEqual(mode, "pair")

    def test_classify_mode_extreme_inputs(self):
        # Massive prompt (100,000 chars)
        huge_prompt = "기능 개발 " + "a" * 100000
        mode, _ = classify_mode(huge_prompt, session_mode="auto")
        self.assertEqual(mode, "pair")

        # Special characters & Regex injection safety
        special_prompt = "[.*?()+|{}\\\\^$] 정규표현식 버그 수정해줘"
        mode, _ = classify_mode(special_prompt, session_mode="auto")
        self.assertEqual(mode, "pair")

        # Unicode / Emoji heavy
        emoji_prompt = "🚀🔥✨ 웹 대시보드 컴포넌트 구현 및 디자인 적용 🎉🌟"
        mode, _ = classify_mode(emoji_prompt, session_mode="auto")
        self.assertEqual(mode, "pair")

    def test_get_mode_description(self):
        self.assertIn("Auto", get_mode_description("auto"))
        self.assertIn("Read", get_mode_description("read"))
        self.assertIn("Pair", get_mode_description("pair"))
        self.assertIn("Single", get_mode_description("single"))
        self.assertIn("Pair+", get_mode_description("pair_quad"))
        self.assertIn("!mode 2+", get_mode_description("2+"))
        self.assertIn("!mode 2+", get_mode_description("pair+"))
        self.assertIn("!mode 3+", get_mode_description("3+"))
        self.assertIn("!mode 3", get_mode_description("3"))
        self.assertIn("1545711652987600937", get_mode_description("single_qa2"))
        self.assertIn("custom", get_mode_description("custom"))


    def test_classify_task_type_consulting_and_ops(self):
        # Consulting / Directional inquiries
        self.assertEqual(classify_task_type("코딩 강박의 빗장을 어떻게 풀면 좋을지 모르겠네"), "consulting")
        self.assertEqual(classify_task_type("앞으로 프로젝트 방향성에 대해 의견 줘"), "consulting")
        self.assertEqual(classify_task_type("시스템 아키텍처 개선 방향 자문 부탁해"), "consulting")

        # Marketing & Store Ops
        self.assertEqual(classify_task_type("구글 플레이스토어 등록용 앱 설명 및 ASO 키워드 추출해줘"), "ops_marketing")
        self.assertEqual(classify_task_type("신규 기능 릴리즈 노트와 인스타그램 마케팅 홍보 문구 작성해줘"), "ops_marketing")

    def test_classify_mode_consulting_heuristics(self):
        mode, desc = classify_mode("어떻게 접근하면 좋을지 방향성 조언 부탁해", session_mode="auto")
        self.assertEqual(mode, "single")
        self.assertIn("방향성 자문", desc)

        mode, desc = classify_mode("앱스토어 등록용 ASO 키워드와 마케팅 소개글 작성해줘", session_mode="auto")
        self.assertEqual(mode, "single")
        self.assertIn("마케팅 전략", desc)

    def test_legacy_evolve_quad_modes_eliminated(self):
        """Verify that legacy evolve and quad modes are nonexistent in classification."""
        for m in ["evolve", "quad"]:
            mode, desc = classify_mode("새로운 기능 개발해줘", session_mode=m)
            self.assertNotEqual(mode, "evolve")
            self.assertNotEqual(mode, "quad")
            self.assertNotIn("전면 초기화", desc)
            self.assertNotIn("아키텍처 재구축", desc)


if __name__ == "__main__":
    unittest.main()
