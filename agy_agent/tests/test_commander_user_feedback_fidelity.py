#!/usr/bin/env python3
"""
Unit test for Commander User Feedback Fidelity & Precision Targeting.

Validates that Commander faithfully deconstructs user-specified complaints:
  1. Design cohesion / connection across screens
  2. App name change ('도큐크래프트' rebranding)
  3. Light gray minimalist app icon
  4. Main dashboard enhancement (remove AI images, expose 4 action buttons: open, blank page, merge, convert)
  5. PDF opening crash & stability optimization

Ensures Commander NEVER defaults to unrelated roadmap milestones (like Phase 3 annotation core)
or hardcoded obsolete tasks when the user submits explicit direct feedback.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from commander import Commander


USER_COMPLAINT_PROMPT = """앱이 1. 디자인이 좀 통일성? 연결성? 아무튼 매칭이 안되는 구간들이 있어
깔끔하게 해줘

2. 도큐크래프트라는 이름이 마음에 안들어
다른걸로 해줘

3. 앱 아이콘도 좀 라이트 그레이톤? 으로 깔끔한걸로 해줘

4. 앱 메인 페이지도 좀 약해보여
또 ai 이미지 들어가있는게 별로야.
버튼 옵션도 너무 없고
좀 열기, 빈페이지 만들기, 합치기, 변환하기 이런 주요 기능들을 꺼내다주면 좋을듯

4. Pdf를 열면 종종 튕기는데 안정성 이슈가 있는 것 같아. 최적화 부탁해."""


class TestCommanderUserFeedbackFidelity(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_feedback_fidelity_")
        # Pre-seed legacy artifacts to prove Commander does NOT get tricked into false-positive delivery
        src = os.path.join(self.tmp, "app", "src", "main", "java", "com", "example", "pdfapp")
        os.makedirs(src, exist_ok=True)
        for name in ["PdfContentStreamEditor.kt", "BrandButtons.kt", "PdfExportEngine.kt"]:
            with open(os.path.join(src, name), "w", encoding="utf-8") as f:
                f.write("// legacy artifact\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_complaints_decomposed_with_exact_fidelity(self):
        """User's 5 exact complaints must be decomposed 1:1 into concrete engineering targets."""
        decomp = Commander.decompose_user_feedback(USER_COMPLAINT_PROMPT, workspace=self.tmp)

        # 1. Title must reflect the priority feedback
        self.assertIn("사용자 피드백", decomp["title"])
        self.assertNotIn("주석 레이어 렌더링", decomp["title"])
        self.assertNotIn("Phase 3", decomp["title"])

        tasks_str = " ".join(decomp["tasks"])

        # 2. Point 1: Design cohesion & consistency
        self.assertTrue(
            any(k in tasks_str for k in ["디자인 통일성", "연결성"]),
            f"디자인 통일성/연결성이 태스크에 반영되지 않음: {tasks_str}"
        )

        # 3. Point 2: App name change ('도큐크래프트' 대체 리브랜딩)
        self.assertTrue(
            "도큐크래프트" in tasks_str or "명칭" in tasks_str or "리브랜딩" in tasks_str,
            f"도큐크래프트 리브랜딩이 태스크에 반영되지 않음: {tasks_str}"
        )

        # 4. Point 3: Light gray minimalist icon
        self.assertTrue(
            "그레이톤" in tasks_str or "아이콘" in tasks_str,
            f"라이트 그레이톤 아이콘이 태스크에 반영되지 않음: {tasks_str}"
        )

        # 5. Point 4: Main dashboard action buttons (열기, 빈페이지, 합치기, 변환하기)
        self.assertTrue(
            "메인" in tasks_str and any(b in tasks_str for b in ["열기", "빈페이지", "합치기", "변환하기"]),
            f"메인 대시보드 4대 액션 버튼이 태스크에 반영되지 않음: {tasks_str}"
        )

        # 6. Point 5: PDF crash & stability optimization
        self.assertTrue(
            any(k in tasks_str for k in ["PDF", "크래시", "튕김", "안정성", "최적화"]),
            f"PDF 크래시 최적화가 태스크에 반영되지 않음: {tasks_str}"
        )

        # [Defect 3 Negative Assertions]
        # (a) No meaningless follower fragment tasks like '[사용자 요구사항 반영] 다른걸로 해줘'
        self.assertNotIn("[사용자 요구사항 반영] 다른걸로", tasks_str)
        self.assertNotIn("다른걸로 해줘", tasks_str)
        # (b) Dashboard task must appear EXACTLY ONCE (no duplicated entries)
        dash_tasks = [t for t in decomp["tasks"] if "메인 대시보드 개편" in t]
        self.assertEqual(len(dash_tasks), 1, f"Dashboard task duplicated: {dash_tasks}")
        self.assertIn("AI 생성 이미지 배제", dash_tasks[0])
        self.assertIn("4대 핵심 기능 버튼", dash_tasks[0])

    def test_plan_milestone_never_defaults_to_generic_annotation_on_user_input(self):
        """When user provides feedback, plan_milestone_5w1h must target user feedback, NOT generic Phase 3 annotation."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="주석 레이어 렌더링 & 무손실 내보내기 (Phase 3: 주석 코어)",
            milestone_version="v11.0",
            tasks=["주석 레이어 분리 저장 및 캔버스 렌더링"],
            domain="mobile_app",
            workspace=self.tmp,
            user_prompt=USER_COMPLAINT_PROMPT
        )

        # Must NOT be the old Phase 3 annotation core
        self.assertNotIn("주석 레이어 렌더링", plan.title)
        self.assertIn("사용자 피드백", plan.title)

        # Discord message format verification
        msg = plan.to_discord_message(mode_label="3", engine_disp="Gemini 3.8 Flash")
        self.assertIn("👑 **[Commander 사령관 마일스톤 기획", msg)
        self.assertNotIn("주석 레이어 렌더링", msg)

        # Acceptance criteria must target real user elements
        when_str = " ".join(plan.when_acceptance)
        self.assertTrue("컴파일/빌드 성공" in when_str or "에러 0건" in when_str)
        self.assertTrue(any(k in when_str for k in ["4대 기능 버튼", "크래시 0건", "명칭", "아이콘"]))

    def test_adaptive_next_milestone_no_unbound_local_error(self):
        """Validates that plan_adaptive_next_milestone runs with zero UnboundLocalError crashes."""
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal=USER_COMPLAINT_PROMPT,
            workspace=self.tmp,
            domain="mobile_app"
        )
        self.assertIsNotNone(plan)
        self.assertIn("사용자 피드백", plan.title)
        self.assertIsInstance(plan.quality_contracts, list)
        self.assertGreater(len(plan.quality_contracts), 0)
        for qc in plan.quality_contracts:
            self.assertIn("id", qc)
            self.assertIn("verification_rule", qc)

    def test_non_pdf_ecommerce_feedback_fidelity(self):
        """E-commerce shopping cart feedback must NOT hallucinate PDF or 4-dashboard buttons."""
        ecomm_prompt = """1. 결제 화면이 자꾸 멈춰요
2. 상품 이미지가 너무 늦게 떠요
3. 장바구니 담기 버튼이 안 눌립니다"""

        decomp = Commander.decompose_user_feedback(ecomm_prompt, workspace=self.tmp, domain="saas_web")

        # 1. Title must not contain raw newlines or broken numbers
        self.assertNotIn("\n", decomp["title"])
        self.assertNotIn("1. 결제", decomp["title"])
        self.assertIn("사용자 피드백", decomp["title"])

        # 2. Must not hallucinate PDF or 4-action dashboard
        ac_str = " ".join(decomp["acceptance_criteria"])
        self.assertNotIn("PDF", ac_str)
        self.assertNotIn("4대 기능 버튼(열기·빈페이지·합치기·변환하기)", ac_str)

        qc_str = " ".join(qc.get("description", "") + " " + qc.get("verification_rule", "") for qc in decomp["quality_contracts"])
        self.assertNotIn("PDF", qc_str)

        # 3. Tasks must faithfully target payment, image loading, cart
        tasks_str = " ".join(decomp["tasks"])
        self.assertTrue(any(k in tasks_str for k in ["결제", "트랜잭션"]))
        self.assertTrue(any(k in tasks_str for k in ["이미지", "성능"]))
        self.assertTrue(any(k in tasks_str for k in ["장바구니", "버튼"]))

        # 4. plan_milestone_5w1h should preserve quality contracts and acceptance criteria
        plan = Commander.plan_milestone_5w1h(
            milestone_title="초기 MVP",
            milestone_version="v1.0",
            tasks=[],
            domain="saas_web",
            workspace=self.tmp,
            user_prompt=ecomm_prompt
        )
        plan_ac_str = " ".join(plan.when_acceptance)
        self.assertNotIn("PDF", plan_ac_str)
        self.assertNotIn("4대 기능 버튼(열기·빈페이지·합치기·변환하기)", plan_ac_str)
        self.assertTrue(any(k in plan_ac_str for k in ["결제", "장바구니"]))

    def test_mode3_prefix_prompt_fidelity(self):
        """Prompts starting with !mode 3 prefix must strip prefix and trigger feedback decomposition."""
        prefix_prompt = "!mode 3\n1. 결제 화면이 자꾸 멈춰요\n2. 장바구니 담기 버튼이 안 눌립니다"
        plan = Commander.plan_milestone_5w1h(
            milestone_title="임의 타이틀",
            milestone_version="v1.0",
            tasks=[],
            domain="saas_web",
            workspace=self.tmp,
            user_prompt=prefix_prompt
        )
        self.assertIn("사용자 피드백", plan.title)
        tasks_str = " ".join(plan.what)
        self.assertTrue(any(k in tasks_str for k in ["결제", "장바구니"]))

    def test_auto_expand_roadmap_prioritizes_disk_signals_and_no_annotation_ladder(self):
        """D1: auto_expand_roadmap must ground in disk signals and NEVER generate hardcoded Phase 3 annotation core."""
        from roadmap_manager import RoadmapManager
        from gap_signal_scanner import GapSignal, GapSignalScanner
        from unittest.mock import patch

        mock_signal = GapSignal(
            area="crash",
            title="런타임 OOM 크래시 방어 및 메모리 누수 제로화",
            detail="비트맵 대용량 로딩 시 OOM 방어",
            evidence="Fatal Exception in crash.log",
            actions=["LruCache 비트맵 메모리 풀 최적화"]
        )

        rm = RoadmapManager(self.tmp)
        with patch.object(GapSignalScanner, "select_next", return_value=mock_signal):
            new_ms = rm.auto_expand_roadmap()

        self.assertTrue(len(new_ms) > 0)
        titles = [m.title for m in new_ms]
        # Must prioritize the disk gap signal
        self.assertIn("런타임 OOM 크래시 방어 및 메모리 누수 제로화", titles)
        # Must NEVER contain the old hardcoded Phase 3 annotation ladder
        for t in titles:
            self.assertNotIn("주석 레이어 렌더링", t)
            self.assertNotIn("Phase 3: 주석 코어", t)

    def test_bot_new_goal_routing_not_confused_by_hyphens_or_domain_nouns(self):
        """D2: bot.py routing must recognize new project requests vs colloquial feedback via real classify_user_intent."""
        from bot import classify_user_intent

        # 1. '새 쇼핑몰 앱 만들어줘 - 이름은 ShopMall' on uninitialized project -> new goal
        new_goal1, fb_sig1 = classify_user_intent("새 쇼핑몰 앱 만들어줘 - 이름은 ShopMall", project_initialized=False)
        self.assertTrue(new_goal1, "새 쇼핑몰 앱 만들어줘는 신규 프로젝트에서 신규 목표로 판정되어야 함")
        self.assertFalse(fb_sig1, "새 쇼핑몰 앱 만들어줘는 피드백 신호로 오분류되면 안 됨")

        # 2. 'e-commerce 대시보드 만들어줘' on uninitialized project -> new goal
        new_goal2, fb_sig2 = classify_user_intent("e-commerce 대시보드 만들어줘", project_initialized=False)
        self.assertTrue(new_goal2, "e-commerce 대시보드 만들어줘는 신규 프로젝트에서 신규 목표로 판정되어야 함")
        self.assertFalse(fb_sig2)

        # 3. Legitimate defect feedback with bullet on initialized project -> feedback, NOT new goal
        defect_prompt = "1. 결제 화면이 자꾸 멈춰요\n2. 장바구니 담기가 안됨"
        new_goal3, fb_sig3 = classify_user_intent(defect_prompt, project_initialized=True)
        self.assertFalse(new_goal3, "피드백은 신규 목표로 오판정되어 로드맵을 초기화하면 안 됨")
        self.assertTrue(fb_sig3, "결제 멈춤/장바구니 안됨 불만은 피드백 신호로 감지되어야 함")

        # 4. P0-1/P1 Colloquial modification requests on initialized project must NOT wipe roadmap
        colloquial_tests = [
            ("디자인 좀 통일성 있게 만들어줘", False, False),
            ("메인 페이지 좀 더 예쁘게 만들어줘", False, False),
            ("버튼 좀 더 크게 만들어줘", False, False),
        ]
        for prompt, exp_new_goal, exp_fb in colloquial_tests:
            is_new, has_fb = classify_user_intent(prompt, project_initialized=True)
            self.assertEqual(is_new, exp_new_goal, f"'{prompt}' must have is_new_goal={exp_new_goal}")

        # 5. Enriched defect keywords on initialized project
        defect_colloquial = [
            ("버튼이 안 눌려요", False, True),
            ("로딩이 너무 버벅거려", False, True),
            ("앱이 자꾸 튕겨요", False, True),
            ("비정상 종료되고 강제종료 돼요", False, True),
        ]
        for prompt, exp_new_goal, exp_fb in defect_colloquial:
            is_new, has_fb = classify_user_intent(prompt, project_initialized=True)
            self.assertEqual(is_new, exp_new_goal, f"'{prompt}' must not reset goal on existing project")
            self.assertEqual(has_fb, exp_fb, f"'{prompt}' must be recognized as feedback signal")

        # 6. Explicit new project creation on initialized project
        explicit_reset_tests = [
            ("처음부터 새로 만들어줘", True, False),
            ("신규 프로젝트로 새로 개발해줘", True, False),
        ]
        for prompt, exp_new_goal, exp_fb in explicit_reset_tests:
            is_new, has_fb = classify_user_intent(prompt, project_initialized=True)
            self.assertEqual(is_new, exp_new_goal, f"'{prompt}' must reset project when explicit")

    def test_clean_disk_auto_expand_produces_at_most_one_milestone_and_zero_phase_strings(self):
        """P0-2/P1: Clean disk auto-expansion must produce at most 1 quality milestone and 0 Phase N strings."""
        from roadmap_manager import RoadmapManager

        clean_tmp = tempfile.mkdtemp(prefix="test_clean_disk_")
        try:
            rm = RoadmapManager(clean_tmp)
            new_ms = rm.auto_expand_roadmap()

            self.assertLessEqual(len(new_ms), 1, "Clean disk should emit at most 1 quality milestone, not pad with 3 templates")
            for m in new_ms:
                self.assertNotIn("Phase ", m.title)
                self.assertNotIn("Phase 3: 주석 코어", m.title)
                self.assertNotIn("주석 레이어 렌더링", m.title)
        finally:
            shutil.rmtree(clean_tmp, ignore_errors=True)

    def test_auto_expand_roadmap_with_active_goal(self):
        """P0-2/P1: auto_expand_roadmap(active_goal=...) decomposes active user feedback as top priority milestone."""
        from roadmap_manager import RoadmapManager

        feedback_goal = "1. 결제 화면이 자꾸 멈춰요\n2. 장바구니 담기 버튼이 안 눌립니다"
        rm = RoadmapManager(self.tmp)
        new_ms = rm.auto_expand_roadmap(active_goal=feedback_goal)

        self.assertTrue(len(new_ms) >= 1)
        top_m = new_ms[0]
        self.assertIn("사용자 피드백", top_m.title)
        tasks_str = " ".join(t.text for t in top_m.tasks)
        self.assertTrue(any(k in tasks_str for k in ["결제", "장바구니"]))
        for m in new_ms:
            self.assertNotIn("Phase ", m.title)


    def test_ecommerce_development_goal_not_titled_as_feedback(self):
        """D3: '전자상거래 앱 개발 - 결제 연동' must NOT be titled as user feedback or crash fix."""
        ecomm_dev_prompt = "전자상거래 앱 개발 - 결제 연동"
        plan = Commander.plan_milestone_5w1h(
            milestone_title="임의 타이틀",
            milestone_version="v1.0",
            tasks=[],
            domain="saas_web",
            workspace=self.tmp,
            user_prompt=ecomm_dev_prompt
        )
        self.assertNotIn("사용자 피드백 긴급 반영", plan.title)
        self.assertNotIn("결제 프로세스 안정화", plan.title)
        self.assertNotIn("화면 프리징 및 멈춤 원인 분석", " ".join(plan.what))

    def test_numbered_new_product_spec_not_mislabeled_as_feedback(self):
        """D-1: Numbered new product specs ('쇼핑몰 앱 만들어줘 1. 상품 목록 2. 장바구니 3. 결제') must NOT be titled as feedback."""
        spec_prompt = "쇼핑몰 앱 만들어줘\n1. 상품 목록\n2. 장바구니\n3. 결제"
        plan = Commander.plan_milestone_5w1h(
            milestone_title="쇼핑몰 MVP 아키텍처",
            milestone_version="v1.0",
            tasks=[],
            domain="saas_web",
            workspace=self.tmp,
            user_prompt=spec_prompt,
            project_initialized=False
        )
        # Must retain original new product title, not be hijacked as "사용자 피드백 긴급 반영"
        self.assertNotIn("사용자 피드백 긴급 반영", plan.title)
        self.assertEqual("쇼핑몰 MVP 아키텍처", plan.title)

        # What list must contain the numbered items
        what_str = " ".join(plan.what)
        self.assertIn("상품 목록", what_str)
        self.assertIn("장바구니", what_str)
        self.assertIn("결제", what_str)

    def test_roadmap_manager_completed_and_next_version_methods(self):
        """D-2: RoadmapManager must provide get_completed_milestones and get_next_milestone_version."""
        from roadmap_manager import RoadmapManager, RoadmapMilestone, RoadmapTask

        rm = RoadmapManager(self.tmp)
        rm.ensure_exists()

        completed = rm.get_completed_milestones()
        self.assertIsInstance(completed, list)

        next_ver = rm.get_next_milestone_version()
        self.assertTrue(next_ver.startswith("v"))
        # Must match semantic version format v(\d+)\.(\d+)
        import re
        self.assertIsNotNone(re.match(r"^v\d+\.\d+$", next_ver))

    def test_auto_expand_roadmap_guards_against_internal_directives(self):
        """D-3: auto_expand_roadmap must guard against internal automated prompts like 🎯 [AGY 무인 자율 순환]."""
        from roadmap_manager import RoadmapManager

        clean_tmp = tempfile.mkdtemp(prefix="test_internal_guard_")
        try:
            rm = RoadmapManager(clean_tmp)
            internal_prompt = "🎯 [AGY 무인 자율 순환: v1.0 실행 완료] 다음 단계를 진행합니다."
            new_ms = rm.auto_expand_roadmap(active_goal=internal_prompt)

            for m in new_ms:
                self.assertNotIn("🎯 [", m.title)
                self.assertNotIn("사용자 피드백 긴급 반영: 🎯", m.title)
        finally:
            shutil.rmtree(clean_tmp, ignore_errors=True)

    def test_decompose_user_feedback_preserves_short_items(self):
        """D-4: decompose_user_feedback must preserve short items (e.g. '결제', '장바구니') instead of dropping len <= 5."""
        short_prompt = "1. 결제\n2. 장바구니\n3. 목록"
        decomp = Commander.decompose_user_feedback(short_prompt, workspace=self.tmp)
        tasks_str = " ".join(decomp["tasks"])
        self.assertIn("결제", tasks_str)
        self.assertIn("장바구니", tasks_str)
        self.assertIn("목록", tasks_str)

    def test_colloquial_feedback_phrases_route_to_feedback_plan(self):
        """P1 D-1: Colloquial phrases like '디자인 통일성 있게 만들어줘', '버튼 좀 더 크게 만들어줘' must route to feedback plan."""
        phrases = [
            ("디자인 통일성 있게 만들어줘", "디자인 통일성"),
            ("버튼 좀 더 크게 만들어줘", "버튼 UI"),
            ("전체적으로 좀 깔끔하게 만들어줘", "디자인 통일성"),
        ]
        for phrase, expected_keyword in phrases:
            plan = Commander.plan_milestone_5w1h(
                milestone_title="기존 마일스톤",
                milestone_version="v2.0",
                tasks=[],
                domain="mobile_app",
                workspace=self.tmp,
                user_prompt=phrase,
                project_initialized=True
            )
            self.assertIn(
                "사용자 피드백",
                plan.title,
                f"구어체 피드백 '{phrase}'이 피드백 플랜으로 라우팅되지 않음 (title: {plan.title})"
            )
            tasks_str = " ".join(plan.what)
            self.assertTrue(
                any(k in tasks_str for k in [expected_keyword, "디자인 통일성", "버튼", "요구사항"]),
                f"'{phrase}'에 대한 태스크가 생성되지 않음: {tasks_str}"
            )

    def test_crash_stability_colloquial_keywords(self):
        """P2: Colloquial stability keywords ('튕겨요', '버벅거려요', '안 열려요') must trigger crash stability task."""
        phrases = ["앱이 자꾸 튕겨요", "스크롤할 때 너무 버벅거려요", "문서가 안 열려요"]
        for phrase in phrases:
            decomp = Commander.decompose_user_feedback(phrase, workspace=self.tmp)
            tasks_str = " ".join(decomp["tasks"])
            self.assertIn(
                "안정성 및 크래시 방어",
                tasks_str,
                f"크래시 키워드 '{phrase}'가 안정성 태스크로 분해되지 않음: {tasks_str}"
            )

    def test_empty_input_guard_returns_clean_default_tasks(self):
        """P2: Empty or whitespace input in decompose_user_feedback must return clean default tasks without empty string."""
        for empty_val in ["", "   ", "\n\n"]:
            decomp = Commander.decompose_user_feedback(empty_val, workspace=self.tmp)
            self.assertIn("사용자 피드백 긴급 반영: 품질 고도화", decomp["title"])
            for t in decomp["tasks"]:
                self.assertTrue(len(t.strip()) > 5, f"태스크가 비어있거나 너무 짧음: {repr(t)}")
                self.assertNotIn("[]", t)
                self.assertNotEqual("[사용자 피드백 실체 구현] ", t)

    def test_no_vfeedback_in_user_feedback_milestone_defaults(self):
        """P1 D-2: Default milestone_version must be semantic (v1.0), never 'vFeedback'."""
        plan = Commander.plan_user_feedback_milestone("화면 개선")
        self.assertEqual(plan.version, "v1.0")
        self.assertNotEqual(plan.version, "vFeedback")

    def test_bot_classify_user_intent_fidelity(self):
        """P2: bot.py classify_user_intent must use shared constants symmetrically."""
        from bot import classify_user_intent

        # 1. Existing project + colloquial feedback: NOT new goal, no project wipe
        is_new, has_fb = classify_user_intent("디자인 좀 통일성 있게 만들어줘", project_initialized=True)
        self.assertFalse(is_new, "구어체 피드백이 신규 프로젝트로 오인되어 프로젝트가 리셋됨")

        # 2. Existing project + explicit reset: new goal
        is_new, _ = classify_user_intent("새 프로젝트 만들어줘", project_initialized=True)
        self.assertTrue(is_new, "명시적 새 프로젝트 요청이 인식되지 않음")

        # 3. Defect keywords: has_feedback_signal = True
        for defect in ["앱이 자꾸 튕겨", "버튼이 안 눌려", "결제할 때 버벅거려", "로딩 안 됨"]:
            _, has_fb = classify_user_intent(defect, project_initialized=True)
            self.assertTrue(has_fb, f"결함 키워드 '{defect}'가 피드백 시그널로 인식되지 않음")

    def test_naming_rebranding_requires_noun_anchor_not_generic_verbs(self):
        """Round 7 Defect 1: Naming / rebranding requires explicit noun anchor. '바꿔' alone must NOT trigger rebranding."""
        # 1. '바꿔' alone in UI/design context must NOT trigger 명칭 리브랜딩
        decomp1 = Commander.decompose_user_feedback("화면이 좀 답답한데 시원하게 좀 바꿔줘", workspace=self.tmp)
        self.assertNotIn("명칭 리브랜딩", decomp1["title"])
        self.assertNotIn("[명칭 리브랜딩]", " ".join(decomp1["tasks"]))
        self.assertIn("디자인 통일성", decomp1["title"])

        decomp2 = Commander.decompose_user_feedback("느낌이 촌스러워서 요즘 스타일로 바꿔줘", workspace=self.tmp)
        self.assertNotIn("명칭 리브랜딩", decomp2["title"])
        self.assertNotIn("[명칭 리브랜딩]", " ".join(decomp2["tasks"]))

        # 2. Explicit noun anchor (이름, 앱명, 도큐크래프트) DOES trigger 명칭 리브랜딩
        decomp3 = Commander.decompose_user_feedback("도큐크래프트라는 이름이 마음에 안들어 다른걸로 해줘", workspace=self.tmp)
        self.assertIn("명칭 리브랜딩", decomp3["title"])
        self.assertIn("[명칭 리브랜딩]", " ".join(decomp3["tasks"]))

        decomp4 = Commander.decompose_user_feedback("앱 이름 바꿔줘", workspace=self.tmp)
        self.assertIn("명칭 리브랜딩", decomp4["title"])
        self.assertIn("[명칭 리브랜딩]", " ".join(decomp4["tasks"]))

    def test_default_inversion_colloquial_feedback_routing(self):
        """Round 7 Defect 2: Default Inversion ensures colloquial phrases route to feedback plan without generic fallback."""
        test_cases = [
            ("화면이 좀 답답한데 시원하게 좀 바꿔줘", ["디자인"]),
            ("앱이 전반적으로 조잡해 보여서 세련되게 다듬어줘", ["디자인"]),
            ("글씨가 너무 작아서 잘 안보여 키워줘", ["폰트", "글씨", "가독성"]),
            ("홈 화면이 허전해 기능 좀 채워줘", ["메인", "홈"]),
        ]
        for prompt, expected_words in test_cases:
            plan = Commander.plan_milestone_5w1h(
                milestone_title="기존 마일스톤",
                milestone_version="v2.0",
                tasks=[],
                domain="mobile_app",
                workspace=self.tmp,
                user_prompt=prompt,
                project_initialized=True
            )
            self.assertIn(
                "사용자 피드백",
                plan.title,
                f"구어체 피드백 '{prompt}'이 피드백 플랜으로 라우팅되지 않고 제네릭으로 낙하함: {plan.title}"
            )
            tasks_str = " ".join(plan.what)
            self.assertTrue(
                any(w in tasks_str for w in expected_words),
                f"'{prompt}'에 대한 기대 키워드 {expected_words}가 태스크에 없음: {tasks_str}"
            )

    def test_ai_override_lock_in_async_planning(self):
        """Round 7 Defect 3: AI hallucination in async planning is locked and rejected."""
        import asyncio
        from unittest.mock import patch, AsyncMock

        # Simulated hallucinated LLM response returning unrelated Phase 3 annotation milestone
        hallucinated_json = json.dumps({
            "title": "v11.0: 주석 레이어 렌더링 & 무손실 내보내기 (Phase 3: 주석 코어)",
            "why": "주석 코어 기능 집중 개발",
            "what": [
                "주석 레이어 분리 저장 및 캔버스 렌더링",
                "터치 제스처 충돌 방지 및 Undo/Redo 제어"
            ],
            "how": "캔버스 렌더링 엔진",
            "when_acceptance": ["빌드 성공"]
        })

        async def _run_test():
            with patch.object(Commander, "_run_raw_llm_query", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = hallucinated_json

                # 1. plan_milestone_5w1h_async with user feedback prompt
                plan = await Commander.plan_milestone_5w1h_async(
                    milestone_title="임의 타이틀",
                    milestone_version="v2.0",
                    tasks=[],
                    domain="mobile_app",
                    workspace=self.tmp,
                    user_prompt="디자인 통일성 있게 만들어줘",
                    project_initialized=True
                )
                # Anti-Hallucination lock must reject "Phase 3: 주석 코어" and lock to user feedback
                self.assertNotIn("Phase 3", plan.title)
                self.assertNotIn("주석 레이어", plan.title)
                self.assertIn("사용자 피드백", plan.title)
                self.assertIn("디자인 통일성", plan.title)
                tasks_str = " ".join(plan.what)
                self.assertNotIn("주석 레이어 분리", tasks_str)
                self.assertIn("디자인 통일성", tasks_str)

                # 2. plan_user_feedback_milestone_async
                fb_plan = await Commander.plan_user_feedback_milestone_async(
                    user_prompt="버튼 좀 더 크게 만들어줘",
                    workspace=self.tmp,
                    milestone_version="v1.0"
                )
                self.assertNotIn("Phase 3", fb_plan.title)
                self.assertIn("사용자 피드백", fb_plan.title)
                fb_tasks_str = " ".join(fb_plan.what)
                self.assertNotIn("주석 레이어 분리", fb_tasks_str)
                self.assertIn("버튼", fb_tasks_str)

        asyncio.run(_run_test())

    def test_graceful_shutdown_mechanism_in_bot(self):
        """QC-P-01: bot.py _graceful_shutdown handles cleanup safely without exception."""
        from bot import _graceful_shutdown
        # Must execute cleanly without error
        try:
            _graceful_shutdown(signum=None)
        except Exception as e:
            self.fail(f"_graceful_shutdown raised unexpected exception: {e}")

    def test_graceful_shutdown_terminates_active_child_process_and_cleans_temp_dirs(self):
        """QC-P-01: _graceful_shutdown actively terminates live child processes and wipes registered scratch dirs."""
        import subprocess
        from bot import _graceful_shutdown, register_temp_dir, session_manager

        # 1. Spawn a live dummy child process in its own session/process group
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.assertIsNone(proc.poll(), "Child process should be running")

        # 2. Register a dummy session with the live child process
        class DummySession:
            def __init__(self, p):
                self.current_process = p

        dummy_session = DummySession(proc)
        session_manager._sessions["test_shutdown_session"] = dummy_session

        # 3. Create and register a temporary scratch directory
        scratch_dir = tempfile.mkdtemp(prefix="test_shutdown_scratch_")
        self.assertTrue(os.path.isdir(scratch_dir), "Scratch dir must exist initially")
        register_temp_dir(scratch_dir)

        try:
            # 4. Invoke graceful shutdown (signum=None so sys.exit is not called)
            _graceful_shutdown(signum=None)

            # 5. Verify child process was terminated
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            self.assertIsNotNone(proc.poll(), "Child process must be terminated after graceful shutdown")

            # 6. Verify scratch directory was deleted
            self.assertFalse(os.path.exists(scratch_dir), "Registered scratch dir must be deleted on shutdown")
        finally:
            if proc.poll() is None:
                proc.kill()
            session_manager._sessions.pop("test_shutdown_session", None)
            shutil.rmtree(scratch_dir, ignore_errors=True)

    def test_feedback_classifier_vocabulary_overlap_and_anti_hallucination(self):
        """Round 8 Rec 1: Generalized vocabulary overlap and version divergence replace brittle blacklist."""
        from feedback_classifier import FeedbackIntentClassifier

        # 1. Vocabulary overlap calculation
        cand = "v11.0: 주석 레이어 렌더링 & 무손실 내보내기 (Phase 3: 주석 코어)"
        ref = ["사용자 피드백 디자인 개선: 디자인 통일성 개편", "앱 디자인 좀 통일성 있게 만들어줘"]
        overlap = FeedbackIntentClassifier.compute_vocabulary_overlap(cand, ref)
        self.assertEqual(overlap, 0.0, f"Unrelated annotation candidate must have 0.0 overlap, got {overlap}")

        # 2. is_phantom_or_divergent detects off-target hallucination
        is_phantom = FeedbackIntentClassifier.is_phantom_or_divergent(
            candidate_title=cand,
            expected_title="사용자 피드백 디자인 개선: 디자인 통일성 개편",
            expected_version="v1.0",
            user_prompt="앱 디자인 좀 통일성 있게 만들어줘"
        )
        self.assertTrue(is_phantom, "Hallucinated Phase 3 milestone must be detected as phantom")

        # 3. Major version divergence detection (v11 vs v1)
        divergent_ver = FeedbackIntentClassifier.is_phantom_or_divergent(
            candidate_title="v11.0: 메인 화면 리뉴얼",
            expected_title="메인 화면 리뉴얼",
            expected_version="v1.0",
            user_prompt="메인 화면 리뉴얼"
        )
        self.assertTrue(divergent_ver, "Major version divergence (v11 vs v1) must be detected as divergent")

        # 4. Legitimate AI proposal with semantic overlap is ACCEPTED
        valid_cand = "사용자 피드백 디자인 개선: 메인 UI 및 디자인 통일성 리팩토링"
        is_valid_phantom = FeedbackIntentClassifier.is_phantom_or_divergent(
            candidate_title=valid_cand,
            expected_title="사용자 피드백 디자인 개선: 디자인 통일성 개편",
            expected_version="v1.0",
            user_prompt="앱 디자인 좀 통일성 있게 만들어줘"
        )
        self.assertFalse(is_valid_phantom, "Legitimate candidate with semantic overlap must NOT be marked phantom")

    def test_contextual_label_framing_taxonomy(self):
        """Round 8 Rec 3: Feedback milestone titles use contextual prefixes aligned with user intent."""
        # 1. Defect/Crash prompt -> 긴급 반영
        defect_prompt = "PDF 열면 자꾸 튕기는데 안정성 최적화 부탁해"
        decomp_defect = Commander.decompose_user_feedback(defect_prompt, workspace=self.tmp)
        self.assertTrue(decomp_defect["title"].startswith("사용자 피드백 긴급 반영"))

        # 2. Design/UI polish prompt -> 디자인 개선
        design_prompt = "앱이 전반적으로 조잡해 보여서 세련되게 다듬어줘"
        decomp_design = Commander.decompose_user_feedback(design_prompt, workspace=self.tmp)
        self.assertTrue(decomp_design["title"].startswith("사용자 피드백 디자인 개선"))

        # 3. Feature expansion prompt -> 기능 확장
        feature_prompt = "검색 기능이랑 필터 정렬 기능 추가해줘"
        decomp_feature = Commander.decompose_user_feedback(feature_prompt, workspace=self.tmp)
        self.assertTrue(decomp_feature["title"].startswith("사용자 피드백 기능 확장"))

        # 4. General directive prompt -> 지시 반영
        general_prompt = "이 지시사항에 맞춰 다음 마일스톤 반영해줘"
        decomp_general = Commander.decompose_user_feedback(general_prompt, workspace=self.tmp)
        self.assertTrue(decomp_general["title"].startswith("사용자 피드백 지시 반영"))

        # 5. All 4 prefixes maintain 100% backward compatibility by including "사용자 피드백"
        for decomp in [decomp_defect, decomp_design, decomp_feature, decomp_general]:
            self.assertIn("사용자 피드백", decomp["title"])

    def test_feedback_intent_classifier_sot_consistency(self):
        """Round 8 Rec 2: FeedbackIntentClassifier ensures single source of truth consistency."""
        from feedback_classifier import FeedbackIntentClassifier
        from bot import classify_user_intent

        test_cases = [
            ("디자인 통일성 있게 다듬어줘", True, False, True, "design"),
            ("PDF 열 때 튕겨요", True, False, True, "defect"),
            ("검색 기능 추가해줘", True, False, True, "feature"),
            ("새 프로젝트 처음부터 만들어줘", True, True, False, "new_project"),
        ]

        for prompt, initialized, exp_new_goal, exp_directive, exp_type in test_cases:
            verdict = FeedbackIntentClassifier.classify(prompt, project_initialized=initialized)
            self.assertEqual(verdict.is_new_goal, exp_new_goal, f"Failed is_new_goal for '{prompt}'")
            self.assertEqual(verdict.is_feedback_directive, exp_directive, f"Failed is_feedback_directive for '{prompt}'")
            self.assertEqual(verdict.intent_type, exp_type, f"Failed intent_type for '{prompt}'")

            # Check bot.py tuple delegation matches
            bot_tuple = classify_user_intent(prompt, project_initialized=initialized)
            self.assertEqual(bot_tuple, verdict.to_bot_tuple())

    def test_authority_gate_preserves_explicit_caller_milestone_and_tasks(self):
        """Authority Gate: Explicit caller milestone and tasks are preserved when preserve_caller_milestone=True."""
        explicit_title = "Mode3 특화 마일스톤: 사령관 고유 지시"
        explicit_tasks = ["사령관 특화 태스크 1", "사령관 특화 태스크 2"]
        user_prompt_with_feedback = "!mode 3\n앱 튕김 및 디자인 통일성 개선해줘"

        plan = Commander.plan_milestone_5w1h(
            milestone_title=explicit_title,
            milestone_version="v2.0",
            tasks=explicit_tasks,
            domain="mobile_app",
            workspace=self.tmp,
            user_prompt=user_prompt_with_feedback,
            preserve_caller_milestone=True
        )

        # Title must preserve the authoritative caller title, NOT overwritten by decompose_user_feedback
        self.assertEqual(plan.title, explicit_title)
        # What-tasks must preserve the caller's explicit tasks
        self.assertEqual(plan.what, explicit_tasks)
        # Quality contracts and acceptance criteria are enriched from feedback decomposition
        self.assertGreater(len(plan.quality_contracts), 0)

    def test_synthesize_mode3_milestone_directive_uses_authority_gate(self):
        """synthesize_mode3_milestone_directive uses preserve_caller_milestone=True to protect synthesized plan."""
        directive = Commander.synthesize_mode3_milestone_directive(
            milestone_title="핵심 코어 리팩토링",
            milestone_version="v3.0",
            tasks=["코어 모듈 재작성"],
            user_prompt="디자인 좀 세련되게 다듬어줘",
            workspace=self.tmp
        )
        self.assertIn("핵심 코어 리팩토링", directive)
        self.assertIn("코어 모듈 재작성", directive)

    def test_feedback_intent_classifier_new_project_edge_cases(self):
        """FeedbackIntentClassifier accurately detects new project creations across natural Korean phrasings."""
        from feedback_classifier import FeedbackIntentClassifier

        new_project_phrasings = [
            "새로운 음악 앱 만들어줘",
            "새 앱 만들고 싶어",
            "새로운 게임 개발해줘",
            "새 프로젝트 처음부터 만들어줘",
            "완전히 새로운 서비스 구상해줘"
        ]
        for phrasing in new_project_phrasings:
            verdict = FeedbackIntentClassifier.classify(phrasing, project_initialized=True)
            self.assertTrue(verdict.is_new_goal, f"Expected is_new_goal=True for phrasing: '{phrasing}'")
            self.assertEqual(verdict.intent_type, "new_project", f"Expected intent_type='new_project' for: '{phrasing}'")

    def test_version_regex_anchoring_no_false_phantom(self):
        """Bare numbers like '메인 4 개 핵심 버튼' must NOT be parsed as version numbers."""
        from feedback_classifier import FeedbackIntentClassifier

        prompt = "메인 4 개 핵심 버튼 배치 및 대시보드 리뉴얼"
        is_phantom = FeedbackIntentClassifier.is_phantom_or_divergent(
            candidate_title="v1.0: 대시보드 리뉴얼",
            expected_title="대시보드 리뉴얼",
            expected_version="v1.0",
            user_prompt=prompt
        )
        self.assertFalse(is_phantom, "Prompt with bare numbers ('4 개') must NOT trigger version divergence")

    def test_dynamic_rebranding_without_hardcoded_name(self):
        """User feedback renaming decomposes dynamically without hardcoded '도큐크래프트' dependency."""
        custom_rebrand_prompt = "QuickDoc이라는 이름이 마음에 안들어 다른걸로 바꿔줘"
        decomp = Commander.decompose_user_feedback(custom_rebrand_prompt, workspace=self.tmp)
        tasks_str = " ".join(decomp["tasks"])
        self.assertIn("QuickDoc", tasks_str)
        self.assertIn("[명칭 리브랜딩] 'QuickDoc' 대체", tasks_str)

        # Also test Korean name extraction with '라는'
        korean_prompt = "노트마스터라는 이름이 마음에 안들어 다른걸로 해줘"
        decomp_ko = Commander.decompose_user_feedback(korean_prompt, workspace=self.tmp)
        tasks_ko = " ".join(decomp_ko["tasks"])
        self.assertIn("노트마스터", tasks_ko)
        self.assertIn("[명칭 리브랜딩] '노트마스터' 대체", tasks_ko)
        # Ensure particle '라는' was not captured as part of the name
        self.assertNotIn("'노트마스터라는'", tasks_ko)

    def test_non_actionable_chitchat_and_inquiry_guard(self):
        """Chit-chat ('안녕') and status inquiries ('현재 상태 알려줘') are classified as non-actionable."""
        from feedback_classifier import FeedbackIntentClassifier
        from bot import classify_user_intent

        chitchat_cases = ["안녕", "안녕하세요", "하이", "hello", "hi"]
        for msg in chitchat_cases:
            verdict = FeedbackIntentClassifier.classify(msg, project_initialized=True)
            self.assertFalse(verdict.is_actionable, f"'{msg}' must be non-actionable")
            self.assertFalse(verdict.is_feedback_directive, f"'{msg}' must not be feedback directive")
            self.assertFalse(verdict.has_feedback_signal, f"'{msg}' must not have feedback signal")
            self.assertEqual(verdict.intent_type, "chit_chat")
            bot_tuple = classify_user_intent(msg, project_initialized=True)
            self.assertEqual(bot_tuple, (False, False), f"bot_tuple for '{msg}' must be (False, False)")

        inquiry_cases = ["현재 상태 알려줘", "진행 상황 알려줘", "status", "지금 어디까지 됨?"]
        for msg in inquiry_cases:
            verdict = FeedbackIntentClassifier.classify(msg, project_initialized=True)
            self.assertFalse(verdict.is_actionable, f"'{msg}' must be non-actionable")
            self.assertFalse(verdict.is_feedback_directive, f"'{msg}' must not be feedback directive")
            self.assertEqual(verdict.intent_type, "inquiry")
            bot_tuple = classify_user_intent(msg, project_initialized=True)
            self.assertEqual(bot_tuple, (False, False), f"bot_tuple for '{msg}' must be (False, False)")

    def test_defect_a_exact_app_name_extraction_without_particle_verb_hijacking(self):
        """Defect A: App name extraction extracts exact name and rejects particles ('을', '는') or verbs ('바꿔줘')."""
        cases = [
            ("앱 이름을 'PaperSmith'로 바꿔줘", "'PaperSmith'"),
            ("이름을 DigitalDoc으로 바꿔줘", "'DigitalDoc'"),
            ("QuickDoc이라는 이름이 마음에 안들어 다른걸로 바꿔줘", "'QuickDoc'"),
            ("2. 도큐크래프트라는 이름이 마음에 안들어 다른걸로 해줘", "'도큐크래프트'"),
            ("앱 이름 바꿔줘", "기존 앱 명칭"),
            ("앱 이름이 마음에 안들어", "기존 앱 명칭"),
            ("앱 이름을 변경해줘", "기존 앱 명칭"),
        ]
        for prompt, expected_name_str in cases:
            decomp = Commander.decompose_user_feedback(prompt, workspace=self.tmp)
            tasks_str = " ".join(decomp["tasks"])
            self.assertIn(f"[명칭 리브랜딩] {expected_name_str} 대체", tasks_str, f"Prompt '{prompt}' failed to extract '{expected_name_str}'. Tasks: {tasks_str}")
            # Ensure particle '을' or verb '바꿔줘' is NEVER captured as the name
            self.assertNotIn("[명칭 리브랜딩] '을' 대체", tasks_str)
            self.assertNotIn("[명칭 리브랜딩] '바꿔줘' 대체", tasks_str)
            self.assertNotIn("[명칭 리브랜딩] '변경해줘' 대체", tasks_str)

    def test_defect_b_punctuated_and_emoji_chitchat_guard(self):
        """Defect B: Chit-chat with punctuation, emojis, or laughter is strictly guarded as non-actionable."""
        from feedback_classifier import FeedbackIntentClassifier
        from bot import classify_user_intent

        punctuated_chitchat = [
            "안녕!", "고마워!", "hello!!", "안녕하세요~", "ㅎㅎ 안녕 ^^",
            "수고했어~!", "thanks!", "고마워요 :)", "감사합니다!!"
        ]
        for msg in punctuated_chitchat:
            verdict = FeedbackIntentClassifier.classify(msg, project_initialized=True)
            self.assertFalse(verdict.is_actionable, f"'{msg}' must be non-actionable")
            self.assertFalse(verdict.is_feedback_directive, f"'{msg}' must not be feedback directive")
            self.assertEqual(verdict.intent_type, "chit_chat", f"'{msg}' must be classified as chit_chat")
            bot_tuple = classify_user_intent(msg, project_initialized=True)
            self.assertEqual(bot_tuple, (False, False), f"bot_tuple for '{msg}' must be (False, False)")

    def test_defect_c_negative_complaint_signals_override_inquiry(self):
        """Defect C: Feedback containing complaint/negative words is routed as defect, NOT silenced as inquiry."""
        from feedback_classifier import FeedbackIntentClassifier
        from bot import classify_user_intent

        complaint_cases = [
            "현재 상태가 안 좋아",
            "진행 상황이 이상해",
            "문제 있어",
            "화면 상태가 엉망이야",
            "진행 상황에 버그가 있어",
            "상태가 심각하고 불량해"
        ]
        for msg in complaint_cases:
            verdict = FeedbackIntentClassifier.classify(msg, project_initialized=True)
            self.assertTrue(verdict.is_actionable, f"Complaint '{msg}' must be actionable")
            self.assertTrue(verdict.is_feedback_directive, f"Complaint '{msg}' must be a feedback directive")
            self.assertEqual(verdict.intent_type, "defect", f"Complaint '{msg}' must have intent_type='defect'")
            self.assertIn("사용자 피드백 긴급 반영", verdict.title_prefix)
            bot_tuple = classify_user_intent(msg, project_initialized=True)
            self.assertEqual(bot_tuple, (False, True), f"bot_tuple for complaint '{msg}' must be (False, True)")

    def test_defect_d_preamble_fragments_and_conjunctions_filtered(self):
        """Defect D: Preamble fragments ('앱이', '그리고', '1.', '요청사항:') are filtered out from generated tasks."""
        prompt_with_preamble = """앱이
1. 디자인 좀 깔끔하게 해줘
그리고
2. 버튼 크기 좀 키워줘"""
        decomp = Commander.decompose_user_feedback(prompt_with_preamble, workspace=self.tmp)
        tasks = decomp["tasks"]
        for t in tasks:
            self.assertNotEqual(t, "[사용자 요구사항 반영] 앱이", "Preamble fragment '앱이' must not become a task")
            self.assertNotEqual(t, "[사용자 요구사항 반영] 그리고", "Conjunction '그리고' must not become a task")
            self.assertFalse(t.startswith("[사용자 요구사항 반영] 1."), "List marker must not become a task")
            self.assertFalse(t.startswith("[사용자 요구사항 반영] 2."), "List marker must not become a task")

        # Also test bullet list with colon preamble
        colon_prompt = """수정 요청사항:
1. 폰트 글씨 크기 키워줘"""
        decomp_colon = Commander.decompose_user_feedback(colon_prompt, workspace=self.tmp)
        for t in decomp_colon["tasks"]:
            self.assertNotIn("수정 요청사항:", t)

    def test_defect_b_single_line_multi_request_cumulative_matching(self):
        """Defect B: Single line / unbulleted multi-request captures ALL categories without silent dropping."""
        single_line_prompt = "디자인 통일성이 없어. 앱 이름도 마음에 안들어. 아이콘은 라이트 그레이톤으로. PDF 열면 튕겨."
        decomp = Commander.decompose_user_feedback(single_line_prompt, workspace=self.tmp)
        tasks_str = " ".join(decomp["tasks"])

        # All 4 categories must be present
        self.assertIn("디자인 통일성", tasks_str, f"Design cohesion missing in: {tasks_str}")
        self.assertIn("명칭 리브랜딩", tasks_str, f"Naming rebrand missing in: {tasks_str}")
        self.assertIn("아이콘 리뉴얼", tasks_str, f"App icon missing in: {tasks_str}")
        self.assertTrue(
            any(k in tasks_str for k in ["안정성 및 크래시 방어", "PDF 문서", "크래시"]),
            f"PDF crash stability missing in: {tasks_str}"
        )

        # Title must display +N or all categories
        self.assertIn("사용자 피드백", decomp["title"])
        self.assertTrue("외 1건" in decomp["title"] or "PDF" in decomp["title"])

    def test_defect_a_anti_hallucination_5w1h_fields_anchoring(self):
        """Defect A: In feedback mode, hallucinated why/how/when/visual fields from LLM are strictly blocked."""
        import asyncio
        from unittest.mock import patch

        hallucinated_json = json.dumps({
            "title": "사용자 피드백 디자인 개선: 디자인 통일성 개편",
            "why": "주석 코어 기능 집중 개발 및 캔버스 레이어 아키텍처 수립",
            "what": ["주석 레이어 분리 저장 및 캔버스 렌더링"],
            "how": "캔버스 렌더링 엔진 및 무손실 병합 코어 모듈 구축",
            "who_where": "com.example.pdfapp.annotation",
            "when_acceptance": ["빌드 성공 (1건)"],
            "visual_asset_spec": "주석 툴바 및 펜 도구 비트맵 에셋 생성",
            "visual_approved": False
        })

        async def _run_test():
            with patch.object(Commander, "_run_raw_llm_query", return_value=hallucinated_json):
                plan = await Commander.plan_milestone_5w1h_async(
                    milestone_title="사용자 피드백 디자인 개선",
                    milestone_version="v2.0",
                    tasks=[],
                    domain="mobile_app",
                    workspace=self.tmp,
                    user_prompt="디자인 좀 깔끔하게 해주고 아이콘도 라이트 그레이톤으로 바꿔줘",
                    preserve_caller_milestone=True
                )
                # 1. Hallucinated why must be blocked and anchored to tailored heuristic why
                self.assertNotIn("주석 코어", plan.why, f"Hallucinated why leaked into plan: {plan.why}")
                self.assertTrue("디자인" in plan.why or "통일성" in plan.why, f"Expected tailored why, got: {plan.why}")

                # 2. Hallucinated how must be blocked and anchored to tailored heuristic how
                self.assertNotIn("캔버스 렌더링", plan.how, f"Hallucinated how leaked into plan: {plan.how}")

                # 3. Hallucinated 1-item when_acceptance must be blocked and anchored to tailored criteria
                when_str = " ".join(plan.when_acceptance)
                self.assertNotIn("빌드 성공 (1건)", when_str, f"Hallucinated when leaked: {when_str}")
                self.assertTrue(len(plan.when_acceptance) >= 2, f"Expected full tailored criteria, got: {plan.when_acceptance}")

                # 4. Hallucinated visual_asset_spec must be blocked
                self.assertNotIn("주석 툴바", plan.visual_asset_spec)

                # 5. Hallucinated who_where must be blocked and anchored to tailored heuristic who_where
                self.assertNotIn("annotation", plan.who_where, f"Hallucinated who_where leaked into plan: {plan.who_where}")

        asyncio.run(_run_test())

    def test_defect_1_feedback_transition_breaks_infinite_loop_across_cycles(self):
        """Defect 1: Consecutive cycles with identical root_user_goal must transition to next capability once delivered."""
        import asyncio
        from roadmap_manager import RoadmapManager

        async def _run():
            # Cycle 1: First time feedback is received -> plans feedback milestone
            plan1 = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal=USER_COMPLAINT_PROMPT,
                workspace=self.tmp,
                domain="mobile_app"
            )
            self.assertIn("사용자 피드백", plan1.title)

            # Simulate Cycle 1 completion by recording in Roadmap and macro memory
            rm = RoadmapManager(self.tmp)
            rm.append_or_update_adaptive_milestone(
                version=plan1.version,
                title=plan1.title,
                tasks=plan1.what,
                status="DONE"
            )
            macro_history_text = (
                f"**[👑 Commander & ⚖️ Auditor 직전 회차 공인 확정 기록 (Macro Memory)]**:\n"
                f"• (회차 1: {plan1.title})\n"
                f"  - 👑 사령관 지침: \"{plan1.directive_for_coder[:150]}\"\n"
                f"  - ⚖️ 감사관 승인 결론: \"사용자 5대 피드백 전수 구현 승인 PASS\""
            )

            # Cycle 2: Same root_user_goal, but feedback was delivered in Cycle 1 -> MUST transition!
            plan2 = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal=USER_COMPLAINT_PROMPT,
                workspace=self.tmp,
                domain="mobile_app",
                macro_memory_text=macro_history_text
            )

            # Plan title MUST change and NOT repeat the user feedback milestone
            self.assertNotEqual(plan1.title, plan2.title, f"Commander looped on identical title: {plan1.title}")
            self.assertNotIn("사용자 피드백", plan2.title, f"Commander failed to transition to next capability: {plan2.title}")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()


