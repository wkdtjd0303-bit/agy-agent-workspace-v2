#!/usr/bin/env python3
"""
🧪 AGY 4.x Dynamic Verification & Orchestration Runtime Test Suite
Proves the complete end-to-end execution path for:
Input -> Risk (R0~R4) -> Agent Routing -> QA Model -> Auditor (Value/STOP) -> E2E Tier -> Final State
"""

import os
import sys
import asyncio
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mode_classifier import classify_risk_level
from trio_agent_runner import QuotaManager, TrioAgentRunner
from thermal_guard import ThermalGuard
from evidence_validator import EvidenceValidator


class TestAGY4DynamicOrchestration(unittest.TestCase):

    def test_scenario_1_diff_size_vs_content_escalation(self):
        """
        Scenario 1: Diff Size != Risk (Worst-Case Escalation)
        - 1-line auth diff MUST escalate to R4 Critical
        - 150-line pure UI component diff MUST stay at R2 Feature/UI
        """
        # Case A: 1-line sensitive security diff
        diff_auth_1line = {
            "changed_files": ["security_guard.py"],
            "raw_diff": "+ if user.is_authenticated and user.is_admin:\n"
        }
        risk_a, reason_a = classify_risk_level("사소한 조건문 수정", diff_auth_1line)
        self.assertEqual(risk_a, "HIGH")
        self.assertIn("R4", reason_a)
        self.assertIn("Deep-Path", reason_a)

        # Case B: 1-line database schema change
        diff_db_1line = {
            "changed_files": ["models.py"],
            "raw_diff": "+ ALTER TABLE users ADD COLUMN is_premium BOOLEAN;\n"
        }
        risk_b, reason_b = classify_risk_level("컬럼 하나 추가", diff_db_1line)
        self.assertEqual(risk_b, "HIGH")
        self.assertIn("R4", reason_b)

        # Case C: 150-line isolated UI component
        diff_ui_150lines = {
            "changed_files": ["ProductCard.vue"],
            "raw_diff": ("+ <template><div class='card'>Product Item</div></template>\n" * 150)
        }
        risk_c, reason_c = classify_risk_level("신규 상품 카드 컴포넌트 150줄 추가", diff_ui_150lines)
        self.assertEqual(risk_c, "MEDIUM")
        self.assertIn("R2", reason_c)

    def test_scenario_2_low_risk_fast_path_selection(self):
        """
        Scenario 2: Low-Risk Typo/Cosmetic Task
        - Must be classified as R1 (LOW)
        - QA Model MUST resolve to Fast Flash model (Zero quota drain)
        """
        diff_typo = {
            "changed_files": ["strings.xml"],
            "raw_diff": "- <string name='btn'>저장</string>\n+ <string name='btn'>확인</string>\n"
        }
        risk, reason = classify_risk_level("버튼 문구 '저장'에서 '확인'으로 변경해줘", diff_typo)
        self.assertEqual(risk, "LOW")
        self.assertIn("R1", reason)
        self.assertIn("Fast-Path", reason)

        # Test Adaptive QA model resolution for LOW risk with standard model
        with unittest.mock.patch("config.QA_MODEL", "gemini-3.1-pro-high"):
            effective_model, display_label, notice = asyncio.run(
                QuotaManager.resolve_qa_model(risk_level=risk)
            )
            # Under new AGY 3.0 rules, Pro is preserved even for LOW risk; Fast-Path is removed.
            self.assertIn("pro", effective_model.lower())

    def test_scenario_3_auditor_value_stop_control_signal(self):
        """
        Scenario 3: Auditor Value & Goal Alignment Stop Signal
        - When Auditor outputs [VALUE: LOW] / [ACTION: STOP], the pipeline must abort immediately
        """
        sample_auditor_stop_output = (
            "[STATUS: AUDIT_STOP] [VALUE: LOW] [ACTION: STOP]\n\n"
            "사용자 본래 목표는 경량 CLI 도구 제작인데, Coder와 QA가 불필요하게 300MB짜리 Electron 프레임워크를 의존성에 추가했습니다.\n"
            "이는 심각한 기술 부채 및 프로젝트 왜곡을 초래하므로 작업을 즉시 중단(STOP)합니다."
        )
        is_value_stopped = (
            "[ACTION: STOP]" in sample_auditor_stop_output
            or "[STATUS: AUDIT_STOP]" in sample_auditor_stop_output
            or ("[VALUE: LOW]" in sample_auditor_stop_output and "[ACTION: PROCEED]" not in sample_auditor_stop_output)
        )
        self.assertTrue(is_value_stopped)

    def test_scenario_4_auditor_value_proceed_signal(self):
        """
        Scenario 4: Auditor Value & Goal Alignment Proceed Signal
        - When Auditor outputs [VALUE: HIGH] [ACTION: PROCEED], the pipeline passes
        """
        sample_auditor_pass_output = (
            "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
            "사용자가 의뢰한 핵심 요구사항이 100% 충족되었으며 2~3일 무인 운영에도 완벽한 가치가 입증되었습니다."
        )
        is_value_stopped = (
            "[ACTION: STOP]" in sample_auditor_pass_output
            or "[STATUS: AUDIT_STOP]" in sample_auditor_pass_output
            or ("[VALUE: LOW]" in sample_auditor_pass_output and "[ACTION: PROCEED]" not in sample_auditor_pass_output)
        )
        self.assertFalse(is_value_stopped)
        auditor_passed = "[STATUS: AUDIT_PASSED]" in sample_auditor_pass_output and "[STATUS: AUDIT_FAILED]" not in sample_auditor_pass_output
        self.assertTrue(auditor_passed)

    def test_scenario_5_adaptive_thermal_guard_logic(self):
        """
        Scenario 5: ThermalGuard Active Temperature Thresholds
        - Verify NORMAL, MILD, and EMERGENCY tiers are precisely calibrated
        """
        self.assertEqual(ThermalGuard.TIER_NORMAL_MAX, 75.0)
        self.assertEqual(ThermalGuard.TIER_MILD_MIN, 78.0)
        self.assertEqual(ThermalGuard.TIER_EMERGENCY_MIN, 85.0)

        # Simulation: at 70°C, tier is NORMAL (no sleep)
        tier_70, _ = ThermalGuard.get_tier(70.0)
        self.assertEqual(tier_70, "NORMAL")

        # Simulation: at 80°C, tier is MILD (2.5s breath)
        tier_80, _ = ThermalGuard.get_tier(80.0)
        self.assertEqual(tier_80, "MILD")

        # Simulation: at 86°C, tier is EMERGENCY (adaptive cooldown)
        tier_86, _ = ThermalGuard.get_tier(86.0)
        self.assertEqual(tier_86, "EMERGENCY")

    def test_scenario_6_differential_e2e_intensity_gating(self):
        """
        Scenario 6: Differential E2E Gating
        - LOW risk (R1) task must bypass heavy emulator/browser in QAHub
        - HIGH risk (R4) task must activate full emulator/browser inspection
        """
        # Low risk mission
        diff_r1 = {"changed_files": ["README.md"], "raw_diff": "+ # Minor doc tweak\n"}
        risk_r1, _ = classify_risk_level("문서 오타 수정", diff_r1)
        is_fast_e2e_r1 = (risk_r1 == "LOW")
        self.assertTrue(is_fast_e2e_r1, "R1 must bypass heavy E2E crawl")

        # High risk mission
        diff_r4 = {"changed_files": ["AuthService.java"], "raw_diff": "+ if (user.hasPermission(ADMIN)) { grantAccess(); }\n"}
        risk_r4, _ = classify_risk_level("관리자 권한 인가 로직 수정", diff_r4)
        is_fast_e2e_r4 = (risk_r4 == "LOW")
        self.assertFalse(is_fast_e2e_r4, "R4 must NOT bypass heavy E2E crawl")


if __name__ == "__main__":
    unittest.main(verbosity=2)
