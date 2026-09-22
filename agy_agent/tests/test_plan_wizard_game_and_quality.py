import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from plan_wizard import PlanWizardState, WIZARD_OPTIONS_MAP, PLATFORM_GALLERY
except ImportError:
    from agy_agent.plan_wizard import PlanWizardState, WIZARD_OPTIONS_MAP, PLATFORM_GALLERY


class TestPlanWizardGameAndQuality(unittest.TestCase):
    def test_smart_preset_vampire_survivors(self):
        state = PlanWizardState(channel_id=123, user_id=456, initial_idea="뱀파이어 서바이벌 같은 생존 2D 게임")
        self.assertTrue(state.is_game_domain())
        self.assertIn("로그라이크", state.target_persona)
        self.assertIn("뱀파이어 서바이벌", state.benchmark_reference)
        self.assertIn("60fps", state.quality_tier)
        self.assertIn("가상 조이스틱", state.control_scheme)
        self.assertIn("재도전", state.game_loop)

        # Verify generated Game PRD contains explicit REQs and benchmark
        prd = state.generate_prd_content()
        self.assertIn("GAME SPECIFICATION", prd)
        self.assertIn("뱀파이어 서바이벌", prd)
        self.assertIn("REQ-001", prd)
        self.assertIn("REQ-002", prd)
        self.assertIn("REQ-003", prd)
        self.assertIn("REQ-004", prd)
        self.assertIn("WebAudio", prd)
        self.assertIn("Screen Shake", prd)

    def test_smart_preset_suika_game(self):
        state = PlanWizardState(channel_id=123, user_id=456, initial_idea="수박게임 같은 과일 합성 퍼즐")
        self.assertTrue(state.is_game_domain())
        self.assertIn("수박게임", state.benchmark_reference)
        self.assertIn("물리", state.quality_tier)
        self.assertIn("과즙 팝 파티클", state.quality_tier)
        
        prd = state.generate_prd_content()
        self.assertIn("수박게임 (Suika Game)", prd)
        self.assertIn("물리 충돌 합성", prd)

    def test_smart_preset_tetris(self):
        state = PlanWizardState(channel_id=123, user_id=456, initial_idea="레트로 테트리스 블록 게임")
        self.assertTrue(state.is_game_domain())
        self.assertIn("테트리스", state.benchmark_reference)
        self.assertIn("하드드롭", state.benchmark_reference)

    def test_smart_preset_general_saas(self):
        state = PlanWizardState(channel_id=123, user_id=456, initial_idea="토스 같은 주식 및 가계부 자산관리 앱")
        self.assertFalse(state.is_game_domain())
        self.assertIn("토스", state.benchmark_reference)
        self.assertIn("금융", state.target_persona)

        prd = state.generate_prd_content()
        self.assertIn("PROJECT REQUIREMENTS DOCUMENT (PRD)", prd)
        self.assertNotIn("GAME SPECIFICATION", prd)
        self.assertIn("토스 (Toss)", prd)
        self.assertIn("REQ-001", prd)
        self.assertIn("REQ-002", prd)

    def test_custom_overrides_in_prd(self):
        state = PlanWizardState(channel_id=123, user_id=456, initial_idea="간단한 미니게임")
        self.assertTrue(state.is_game_domain())

        # Manually adjust quality and benchmark via settings
        state.benchmark_reference = "플래피 버드 원터치 점프"
        state.quality_tier = "Tier 1: 미니멀 픽셀 아트 & 60fps"
        state.custom_notes = "광고 제거 버튼 추가"

        prd = state.generate_prd_content()
        self.assertIn("플래피 버드 원터치 점프", prd)
        self.assertIn("Tier 1: 미니멀 픽셀 아트", prd)
        self.assertIn("광고 제거 버튼 추가", prd)


if __name__ == "__main__":
    unittest.main()
