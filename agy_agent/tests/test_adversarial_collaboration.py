#!/usr/bin/env python3
"""
🧪 AGY 5.0 — Adversarial Collaboration & Tri-Layer Telemetry Test Suite
Validates:
1. 10 Adversarial Missions (A1 ~ A10) across the entire collaboration chain
2. Collaboration Intelligence (Coder, QA, Auditor judgment & anti-collusion)
3. Tri-Layer Telemetry: Decision != Action != Outcome & Post-hoc Calibration
"""

import os
import sys
import time
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autonomy_guard import (
    AutonomySafetyLayer,
    DecisionMatrixEngine,
    DecisionResult,
    TriLayerTelemetryStore,
    TriLayerTelemetryRecord,
    CollaborationIntelligence
)
from mode_classifier import classify_risk_level
from roadmap_manager import RoadmapManager


class TestAdversarialMissions(unittest.TestCase):
    """
    Executes the 10 Adversarial Missions (A1 ~ A10) defined in AGY 5.0 specification.
    """

    def setUp(self):
        self.channel_id = 99990101
        if self.channel_id in AutonomySafetyLayer._states:
            del AutonomySafetyLayer._states[self.channel_id]
        CollaborationIntelligence.reset_channel(self.channel_id)

    def tearDown(self):
        if self.channel_id in AutonomySafetyLayer._states:
            del AutonomySafetyLayer._states[self.channel_id]
        CollaborationIntelligence.reset_channel(self.channel_id)

    # ── A1: Same task loop trap ──────────────────────────────────────────────
    def test_a1_same_task_repeat_escalate(self):
        """A1: Identical task repeated 3 times must trip ESCALATE_TO_HUMAN."""
        task = "Re-align modal action buttons for consistency"
        for _ in range(3):
            AutonomySafetyLayer.record_task_start(self.channel_id, task)

        is_safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "", task)
        self.assertFalse(is_safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("루프 트랩", msg)

    # ── A2: File churn continuous modification ──────────────────────────────
    def test_a2_file_churn_continuous_modification(self):
        """A2: Same file modified 5+ times in a milestone must trip ESCALATE_TO_HUMAN."""
        fpath = "src/core/PaymentRouter.kt"
        for _ in range(5):
            AutonomySafetyLayer.record_file_change(self.channel_id, fpath)

        is_safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(is_safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("파일 폭주", msg)

    # ── A3: Consecutive QA failures circuit breaker ──────────────────────────
    def test_a3_consecutive_qa_failures_circuit_breaker(self):
        """A3: 3 consecutive QA failures must trip ESCALATE_TO_HUMAN circuit breaker."""
        for _ in range(3):
            AutonomySafetyLayer.record_qa_result(self.channel_id, passed=False)

        is_safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(is_safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("서킷 브레이커", msg)

    # ── A4: Roadmap auto-expansion ceiling ───────────────────────────────────
    def test_a4_roadmap_ceiling_depth(self):
        """A4: Auto-expanding beyond 5 phases must trip STOP action."""
        temp_dir = tempfile.mkdtemp(prefix="rm_a4_")
        try:
            rm_file = os.path.join(temp_dir, "ROADMAP.md")
            with open(rm_file, "w", encoding="utf-8") as f:
                f.write("# Roadmap\n\n## v1.0 — Initial\n- [x] Initial Task\n")

            rm = RoadmapManager(temp_dir)
            rm.load()

            for _ in range(5):
                exp = rm.auto_expand_roadmap()
                self.assertTrue(len(exp) > 0)

            # 6th expansion blocked
            exp_blocked = rm.auto_expand_roadmap()
            self.assertEqual(len(exp_blocked), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ── A5: Low-value feature request ────────────────────────────────────────
    def test_a5_low_value_superfluous_request(self):
        """A5: Request with low user value / tech debt must be stopped (STOP_LOW_VALUE)."""
        decision = DecisionMatrixEngine.evaluate(risk_tier="R2", value_tier="LOW")
        self.assertEqual(decision.action, "STOP_LOW_VALUE")
        self.assertIn("사용자 체감 가치가 낮거나", decision.reason)

    # ── A6: High risk + Low confidence gate ──────────────────────────────────
    def test_a6_high_risk_low_confidence_gate(self):
        """A6: High-risk (R4) with low confidence must force AUDITOR_REVIEW."""
        decision = DecisionMatrixEngine.evaluate(risk_tier="R4", value_tier="HIGH", confidence_tier="LOW")
        self.assertEqual(decision.action, "AUDITOR_REVIEW")
        self.assertIn("Auditor 사전 수락 심사 강제", decision.reason)

    # ── A7: Normal low risk cosmetic task ────────────────────────────────────
    def test_a7_normal_low_risk_fast_path(self):
        """A7: Normal cosmetic/typo fix must select PROCEED_FAST (Fast-Path)."""
        diff = {"changed_files": ["README.md"], "raw_diff": "+ fix typo in title"}
        risk, _ = classify_risk_level("README 문서 오타 수정", diff)
        self.assertEqual(risk, "LOW")
        decision = DecisionMatrixEngine.evaluate(risk_tier="R1")
        self.assertEqual(decision.action, "PROCEED_FAST")

    # ── A8: Normal standard feature ──────────────────────────────────────────
    def test_a8_normal_standard_feature(self):
        """A8: Standard UI feature must select PROCEED_NORMAL."""
        diff = {"changed_files": ["src/components/UserProfile.vue"], "raw_diff": "+ template UserProfile"}
        risk, _ = classify_risk_level("신규 사용자 프로필 컴포넌트 추가", diff)
        self.assertEqual(risk, "MEDIUM")
        decision = DecisionMatrixEngine.evaluate(risk_tier="R2")
        self.assertEqual(decision.action, "PROCEED_NORMAL")

    # ── A9: Core system architecture change ──────────────────────────────────
    def test_a9_core_system_architecture_deep(self):
        """A9: Sensitive Auth/DB/Security change must escalate to PROCEED_DEEP."""
        diff = {"changed_files": ["src/auth/jwt_verifier.py"], "raw_diff": "+ def verify_token(): pass"}
        risk, _ = classify_risk_level("인증 토큰 검증 로직 변경", diff)
        self.assertEqual(risk, "HIGH")
        decision = DecisionMatrixEngine.evaluate(risk_tier="R4", confidence_tier="HIGH")
        self.assertEqual(decision.action, "PROCEED_DEEP")

    # ── A10: Coder <-> QA infinite ping-pong loop ────────────────────────────
    def test_a10_coder_qa_infinite_ping_pong_loop(self):
        """A10: Identical defect repeated across 2 rounds trips CollaborationConvergenceBreaker."""
        defect = "NullPointerException in handleUserAuth() at line 42"
        
        # Round 1: Defect reported
        tripped, _ = CollaborationIntelligence.detect_convergence_failure(self.channel_id, defect, round_num=1)
        self.assertFalse(tripped)

        # Round 2: Exact same defect reported again (Ping-Pong Trap)
        tripped, msg = CollaborationIntelligence.detect_convergence_failure(self.channel_id, defect, round_num=2)
        self.assertTrue(tripped)
        self.assertIn("A10: 수렴 실패", msg)
        self.assertIn("Ping-Pong Trap", msg)

    def test_a10_different_defects_with_same_boilerplate_do_not_trip(self):
        """A10: Different defects sharing the same markdown status header must NOT trip Ping-Pong trap."""
        CollaborationIntelligence.reset_channel(self.channel_id)
        header = "[STATUS: REJECTED]\n⚠️ **[품질 게이트 강제 반려 — 필수 요구사항 누락 감지]**\n"
        defect1 = header + "- 결함 1: 사용자 로그인 시 SQLite DB 커넥션 누수 발생"
        defect2 = header + "- 결함 2: 메인 화면 버튼 터치 영역 48dp 미달 및 텍스트 줄바꿈 오류"

        # Round 1
        tripped, _ = CollaborationIntelligence.detect_convergence_failure(self.channel_id, defect1, round_num=1)
        self.assertFalse(tripped)

        # Round 2 (Different defect despite identical header)
        tripped, msg = CollaborationIntelligence.detect_convergence_failure(self.channel_id, defect2, round_num=2)
        self.assertFalse(tripped)
        self.assertEqual(msg, "")


class TestCollaborationIntelligence(unittest.TestCase):
    """
    Tests Collaboration Intelligence: Coder judgment, QA judgment, and Auditor value gate.
    """

    def test_c1_c2_requirement_omission_detection(self):
        """
        C1/C2: When Coder silently omits an explicit requirement, validate_requirement_coverage
        must detect the omission and reject the pass.
        """
        prompt = (
            "다음 2가지 기능을 반드시 구현하십시오:\n"
            "1. [REQ-01] CSV 내보내기 다운로드 기능\n"
            "2. [REQ-02] 다국어 번역 언어 선택기 UI"
        )

        # Case A: Coder implemented only CSV export, completely omitted i18n
        coder_output_partial = """
        function exportCSV(data) {
            const csv = convertToCSV(data);
            downloadFile('export.csv', csv);
        }
        """
        passed, missing, msg = CollaborationIntelligence.validate_requirement_coverage(prompt, coder_output_partial)
        self.assertFalse(passed)
        self.assertTrue(any("다국어 번역" in m for m in missing))
        self.assertIn("필수 요구사항", msg)

        # Case B: Coder implemented both requirements
        coder_output_full = """
        function exportCSV(data) {
            const csv = convertToCSV(data);
            downloadFile('export.csv', csv);
        }
        function renderLanguageSelector() {
            return '<select id="lang"><option value="ko">다국어 번역 언어 선택</option></select>';
        }
        """
        passed_full, missing_full, _ = CollaborationIntelligence.validate_requirement_coverage(prompt, coder_output_full)
        self.assertTrue(passed_full)
        self.assertEqual(len(missing_full), 0)

    def test_c3_auditor_value_bloat_detection(self):
        """
        C3: When request is simple/cosmetic, but Coder modifies 12+ files with superfluous bloat,
        Auditor must flag STOP_LOW_VALUE to prevent technical debt.
        """
        diff_bloated = {
            "has_changes": True,
            "changed_files": [f"file_{i}.ts" for i in range(12)],
            "raw_diff": "+ diff"
        }
        action, reason = CollaborationIntelligence.evaluate_auditor_value_alignment(
            prompt="버튼 오타 간단 수정",
            diff_info=diff_bloated,
            is_cosmetic_only=True
        )
        self.assertEqual(action, "STOP_LOW_VALUE")
        self.assertIn("과잉 수정 감지", reason)


class TestTriLayerTelemetry(unittest.TestCase):
    """
    Tests the Tri-Layer Telemetry Architecture: Decision != Action != Outcome.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="telemetry_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tri_layer_separation_and_recording(self):
        """Verifies that Decision, Action, and Outcome are completely decoupled and recorded."""
        mission_id = "mission-telemetry-001"
        ch_id = 99990202
        mission_text = "Add user profile avatar upload"

        # 1. Layer 1: Decision
        dec_result = DecisionResult(
            action="PROCEED_NORMAL",
            reason="표준 신규 기능 추가",
            risk_tier="R2",
            value_tier="HIGH",
            confidence_tier="HIGH",
            cost_tier="LOW"
        )
        rec = TriLayerTelemetryStore.record_decision(
            mission_id=mission_id,
            channel_id=ch_id,
            mission=mission_text,
            decision_result=dec_result
        )

        self.assertEqual(rec.decision.route, "PROCEED_NORMAL")
        self.assertEqual(rec.decision.risk, "R2")
        self.assertEqual(rec.action.coder_status, "pending")
        self.assertEqual(rec.outcome.final, "pending")

        # 2. Layer 2: Action
        TriLayerTelemetryStore.update_action(
            mission_id=mission_id,
            coder_status="completed",
            qa_verdict="passed",
            auditor_action="proceed",
            rounds_taken=1,
            tool_calls=5
        )
        self.assertEqual(rec.action.coder_status, "completed")
        self.assertEqual(rec.action.qa_verdict, "passed")
        self.assertEqual(rec.action.tool_calls, 5)

        # 3. Layer 3: Outcome
        final_rec = TriLayerTelemetryStore.finalize_outcome(
            mission_id=mission_id,
            final_status="success",
            iterations=1,
            files_changed=3,
            llm_calls=3,
            elapsed=14.2,
            is_converged=True,
            workspace=self.temp_dir
        )

        self.assertIsNotNone(final_rec)
        self.assertEqual(final_rec.outcome.final, "success")
        self.assertEqual(final_rec.outcome.iterations, 1)
        self.assertEqual(final_rec.outcome.calibration_score, 1.0)

        # Verify physical file save
        jsonl_file = os.path.join(self.temp_dir, "reports", "orchestration", "decision_action_outcome.jsonl")
        self.assertTrue(os.path.isfile(jsonl_file))
        with open(jsonl_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("mission-telemetry-001", content)
            self.assertIn('"decision"', content)
            self.assertIn('"action"', content)
            self.assertIn('"outcome"', content)

    def test_post_hoc_calibration_scoring(self):
        """
        Tests calibration score:
        - Well-calibrated FAST route gets 1.0
        - Under-estimated FAST route (taking 3 rounds and 10 files) score drops
        """
        # Case A: Well-calibrated FAST
        rec_fast = TriLayerTelemetryStore.record_decision(
            mission_id="m-fast-01",
            channel_id=1,
            mission="Typo fix",
            decision_result=DecisionMatrixEngine.evaluate(risk_tier="R1")
        )
        out_fast = TriLayerTelemetryStore.finalize_outcome(
            mission_id="m-fast-01",
            final_status="success",
            iterations=1,
            files_changed=1,
            is_converged=True
        )
        self.assertEqual(out_fast.outcome.calibration_score, 1.0)

        # Case B: Under-estimated FAST (actually took 3 iterations and 8 files)
        rec_under = TriLayerTelemetryStore.record_decision(
            mission_id="m-fast-02",
            channel_id=1,
            mission="Complex refactor masked as typo",
            decision_result=DecisionMatrixEngine.evaluate(risk_tier="R1")
        )
        out_under = TriLayerTelemetryStore.finalize_outcome(
            mission_id="m-fast-02",
            final_status="success",
            iterations=3,
            files_changed=8,
            is_converged=True
        )
        # Score must drop significantly due to under-estimation
        self.assertLess(out_under.outcome.calibration_score, 0.5)


if __name__ == "__main__":
    unittest.main()
