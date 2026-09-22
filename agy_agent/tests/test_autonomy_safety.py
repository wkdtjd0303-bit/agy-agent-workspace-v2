#!/usr/bin/env python3
"""
🧪 AGY 4.x Autonomy Safety Layer & Decision Matrix Test Suite
Validates the 10 Core Autonomy Guards, 4D Decision Matrix, and Bounded Autonomy limits.
"""

import os
import sys
import time
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autonomy_guard import AutonomySafetyLayer, DecisionMatrixEngine, DecisionResult
from roadmap_manager import RoadmapManager


class TestAutonomySafetyLayer(unittest.TestCase):

    def setUp(self):
        # Use isolated dummy channel IDs for each test
        self.channel_id = 99990001
        if self.channel_id in AutonomySafetyLayer._states:
            del AutonomySafetyLayer._states[self.channel_id]

    def tearDown(self):
        if self.channel_id in AutonomySafetyLayer._states:
            del AutonomySafetyLayer._states[self.channel_id]

    # =========================================================================
    # 4-Dimensional Decision Matrix Engine Tests
    # =========================================================================

    def test_decision_matrix_all_quadrants(self):
        """Validates 4D Matrix: Risk x Value x Confidence x Cost routing decisions."""
        # 1. Low Value -> Always STOP to prevent technical debt
        res_stop = DecisionMatrixEngine.evaluate(risk_tier="R2", value_tier="LOW")
        self.assertEqual(res_stop.action, "STOP_LOW_VALUE")
        self.assertIn("사용자 체감 가치가 낮거나", res_stop.reason)

        # 2. R0 Inquiry -> PROCEED_FAST (Single agent bypass)
        res_r0 = DecisionMatrixEngine.evaluate(risk_tier="R0", value_tier="HIGH")
        self.assertEqual(res_r0.action, "PROCEED_FAST")

        # 3. R1 Cosmetic/Trivial -> PROCEED_FAST (Fast-Path)
        res_r1 = DecisionMatrixEngine.evaluate(risk_tier="R1", value_tier="HIGH")
        self.assertEqual(res_r1.action, "PROCEED_FAST")

        # 4. R2 Standard Feature -> PROCEED_NORMAL
        res_r2 = DecisionMatrixEngine.evaluate(risk_tier="R2", value_tier="HIGH")
        self.assertEqual(res_r2.action, "PROCEED_NORMAL")

        # 5. R3 Core Architecture -> PROCEED_DEEP
        res_r3 = DecisionMatrixEngine.evaluate(risk_tier="R3", value_tier="HIGH")
        self.assertEqual(res_r3.action, "PROCEED_DEEP")

        # 6. R4 Critical + High Confidence -> PROCEED_DEEP
        res_r4_high = DecisionMatrixEngine.evaluate(risk_tier="R4", value_tier="HIGH", confidence_tier="HIGH")
        self.assertEqual(res_r4_high.action, "PROCEED_DEEP")

        # 7. R4 Critical + Low Confidence -> AUDITOR_REVIEW
        res_r4_low = DecisionMatrixEngine.evaluate(risk_tier="R4", value_tier="HIGH", confidence_tier="LOW")
        self.assertEqual(res_r4_low.action, "AUDITOR_REVIEW")

    # =========================================================================
    # 10 Core Autonomy Guards Tests
    # =========================================================================

    def test_guard_1_same_task_loop_trap(self):
        """Guard 1: 3 identical tasks trip ESCALATE_TO_HUMAN circuit breaker."""
        task_name = "Fix button alignment and responsive padding"
        
        # 1st and 2nd run: Safe
        AutonomySafetyLayer.record_task_start(self.channel_id, task_name)
        AutonomySafetyLayer.record_task_start(self.channel_id, task_name)
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "", task_name)
        self.assertTrue(safe)
        self.assertEqual(action, "SAFE")

        # 3rd run: Trap tripped
        AutonomySafetyLayer.record_task_start(self.channel_id, task_name)
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "", task_name)
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("루프 트랩", msg)

    def test_guard_2_file_churn_detector(self):
        """Guard 2: Same file modified 5+ times in a single milestone trips ESCALATE_TO_HUMAN."""
        churn_file = "/workspace/src/CriticalView.tsx"
        for _ in range(4):
            AutonomySafetyLayer.record_file_change(self.channel_id, churn_file)
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertTrue(safe)

        # 5th modification: trips guard
        AutonomySafetyLayer.record_file_change(self.channel_id, churn_file)
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("파일 폭주", msg)

    def test_guard_3_milestone_timebox_expiration(self):
        """Guard 3: Milestone running longer than 30 minutes (1800s) trips ESCALATE_TO_HUMAN."""
        st = AutonomySafetyLayer.get_state(self.channel_id)
        # Artificially age milestone start time by 1850 seconds
        st["milestone_start_time"] = time.time() - 1850.0

        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("타임박스 만료", msg)

    def test_guard_3_hybrid_heartbeat_progress(self):
        """Guard 3: Active progress heartbeat allows extension beyond 30m up to hard ceiling."""
        st = AutonomySafetyLayer.get_state(self.channel_id)
        # Age milestone by 2000s (>1800s base timebox)
        st["milestone_start_time"] = time.time() - 2000.0
        # Record active progress heartbeat
        AutonomySafetyLayer.record_progress(self.channel_id, "build_success", "test.apk")

        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertTrue(safe, "Should stay safe when active progress heartbeat is recorded within 10 minutes")

        # But if milestone exceeds hard ceiling (3600s for non-android), it must trip
        st["milestone_start_time"] = time.time() - 3650.0
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("타임박스 만료", msg)

    def test_guard_4_daily_llm_call_quota(self):
        """Guard 4: 500+ daily LLM calls trip STOP action to protect API budget."""
        st = AutonomySafetyLayer.get_state(self.channel_id)
        st["daily_call_count"] = 500

        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "STOP")
        self.assertIn("일일 쿼터 상한", msg)

    def test_guard_6_watchdog_runaway_brake(self):
        """Guard 6: 6 consecutive unattended watchdog runs trip COOLDOWN."""
        for _ in range(5):
            AutonomySafetyLayer.record_watchdog_run(self.channel_id)
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertTrue(safe)

        # 6th run trips COOLDOWN and auto-resets watchdog counter
        AutonomySafetyLayer.record_watchdog_run(self.channel_id)
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "COOLDOWN")
        self.assertIn("Watchdog 브레이크", msg)
        self.assertEqual(AutonomySafetyLayer.get_state(self.channel_id)["watchdog_runs"], 0)

    def test_guard_7_repeated_identical_error(self):
        """Guard 7: Same error snippet occurring twice consecutively trips ESCALATE_TO_HUMAN."""
        err = "NullPointerException: Attempt to invoke virtual method on a null object reference"
        AutonomySafetyLayer.record_error(self.channel_id, err)
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertTrue(safe)

        AutonomySafetyLayer.record_error(self.channel_id, err)
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("동일 결함 반복", msg)

    def test_guard_8_roadmap_expansion_max_depth(self):
        """Guard 8: Auto-expanding roadmap past 5 phases trips STOP action."""
        for _ in range(5):
            AutonomySafetyLayer.record_roadmap_expansion(self.channel_id)

        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "STOP")
        self.assertIn("로드맵 확장 최대 한도", msg)

    def test_guard_9_consecutive_failure_circuit_breaker(self):
        """Guard 9: 3 consecutive QA failures trip ESCALATE_TO_HUMAN circuit breaker."""
        AutonomySafetyLayer.record_qa_result(self.channel_id, passed=False)
        AutonomySafetyLayer.record_qa_result(self.channel_id, passed=False)
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertTrue(safe)

        AutonomySafetyLayer.record_qa_result(self.channel_id, passed=False)
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "")
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("서킷 브레이커", msg)

    def test_guard_10_goal_drift_detector(self):
        """Guard 10: 3 consecutive trivial tasks (readme, formatting, doc) trip Goal Drift breaker."""
        AutonomySafetyLayer.record_task_start(self.channel_id, "README.md 파일 수정")
        AutonomySafetyLayer.record_task_start(self.channel_id, "코드 주석 추가 및 포맷팅")
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(self.channel_id, "", "코드 정리 작업")
        self.assertTrue(safe, "2 trivial tasks should not trip yet")

        AutonomySafetyLayer.record_task_start(self.channel_id, "코드 정리 작업")
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(self.channel_id, "", "단순 주석 보강")
        self.assertFalse(safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("목표 표류 감지", msg)

    def test_guard_12_workspace_confinement(self):
        """Guard 12: Blocks tasks attempting path traversal escaping designated workspace."""
        ws = "/home/jangsung/Desktop/autocoding/!5_agy_agent"
        # Normal task inside workspace: Safe
        safe, action, _ = AutonomySafetyLayer.check_safety_guards(
            self.channel_id, ws, "Implement button in src/ui/Button.kt"
        )
        self.assertTrue(safe)
        self.assertEqual(action, "SAFE")

        # Escaping to sibling directory via ../!01_vibecoding: Blocked
        safe, action, msg = AutonomySafetyLayer.check_safety_guards(
            self.channel_id, ws, "Inspect ../!01_vibecoding/pdfediter_andriod/reports/gate5/visual_qa.json"
        )
        self.assertFalse(safe)
        self.assertEqual(action, "STOP")
        self.assertIn("지정 워크스페이스 격리 헌법 위반", msg)

        # Helper method is_within_workspace check
        self.assertTrue(AutonomySafetyLayer.is_within_workspace(
            "/home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent/bot.py", ws
        ))
        self.assertFalse(AutonomySafetyLayer.is_within_workspace(
            "/home/jangsung/Desktop/autocoding/!01_vibecoding/pdfediter_andriod/app.kt", ws
        ))

    def test_qa_pass_resets_churn_and_failures(self):
        """Verifies that a successful QA PASS cleans up failure count and churn tracking."""
        AutonomySafetyLayer.record_qa_result(self.channel_id, passed=False)
        AutonomySafetyLayer.record_qa_result(self.channel_id, passed=False)
        AutonomySafetyLayer.record_file_change(self.channel_id, "/workspace/test.py")

        st = AutonomySafetyLayer.get_state(self.channel_id)
        self.assertEqual(st["consecutive_failures"], 2)
        self.assertEqual(len(st["file_churn_counter"]), 1)

        # QA Passes!
        AutonomySafetyLayer.record_qa_result(self.channel_id, passed=True)
        self.assertEqual(st["consecutive_failures"], 0)
        self.assertEqual(len(st["file_churn_counter"]), 0)

    # =========================================================================
    # RoadmapManager Bounded Autonomy Expansion Ceiling Test
    # =========================================================================

    def test_roadmap_manager_expansion_ceiling(self):
        """Verifies that RoadmapManager.auto_expand_roadmap hard-stops at MAX_ROADMAP_EXPAND_DEPTH."""
        temp_dir = tempfile.mkdtemp(prefix="rm_ceiling_test_")
        try:
            rm_file = os.path.join(temp_dir, "ROADMAP.md")
            with open(rm_file, "w", encoding="utf-8") as f:
                f.write("# Roadmap\n\n## v1.0 — Initial Phase\n- [x] Initial Task\n")

            rm = RoadmapManager(temp_dir)
            rm.load()

            # Perform 5 expansions (allowed up to 5)
            for i in range(5):
                exp = rm.auto_expand_roadmap()
                self.assertTrue(len(exp) > 0, f"Expansion {i+1} should succeed")

            # 6th expansion must hit the ceiling and return empty list
            exp_6th = rm.auto_expand_roadmap()
            self.assertEqual(len(exp_6th), 0, "6th expansion must be blocked by Bounded Autonomy limit")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_unattended_evolve_auto_recovery_and_skip(self):
        """Verifies that unattended evolve cleanly skips a failing milestone and advances to next."""
        temp_dir = tempfile.mkdtemp(prefix="rm_unattended_recovery_")
        try:
            rm_file = os.path.join(temp_dir, "ROADMAP.md")
            with open(rm_file, "w", encoding="utf-8") as f:
                f.write(
                    "# 🗺️ Project Roadmap\n\n"
                    "## v1.0 — Alpha Feature\n"
                    "- [ ] Sub-task 1\n\n"
                    "## v1.1 — Beta Feature\n"
                    "- [ ] Sub-task 2\n"
                )

            rm = RoadmapManager(temp_dir)
            rm.load()

            cur = rm.get_current_milestone(auto_expand=False)
            self.assertIsNotNone(cur)
            self.assertEqual(cur.version, "v1.0")

            # Simulate Auditor rejection -> auto-recovery skip
            skipped = rm.skip_current_milestone(reason="무인 자율 진화 라운드 실패 자동 복구 (Auto-Recovery)")
            self.assertEqual(skipped.version, "v1.0")
            self.assertEqual(skipped.status, "SKIPPED")

            # Next milestone must immediately be v1.1
            next_ms = rm.get_current_milestone(auto_expand=False)
            self.assertIsNotNone(next_ms)
            self.assertEqual(next_ms.version, "v1.1")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

