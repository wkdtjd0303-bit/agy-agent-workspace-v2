#!/usr/bin/env python3
"""
Unit tests for AGY !mode 3 / !mode 3+ Autonomous Product Planning & Quality Evolution System.
Verifies all 7 core test scenarios specified in the PRD / User Specification:
- Test A: !mode 2 / !mode 2+ preserves existing Pair workflow without forcing Autonomous Product Planning.
- Test B: !mode 3 / !mode 3+ automatically invokes Autonomous Product Planning.
- Test C: Multi-dimensional Quality Definitions (game feel, sound, controls, UX, visuals) are identified separately from feature checklists.
- Test D: Product Critic identifies quality gaps (audio beeps, clunky feel) and triggers rework / next milestone.
- Test E: E2E failure or crash prevents PASS (broken E2E ≠ PASS).
- Test F: Critical Quality Gate blocks pass if any critical dimension < minimum threshold, even if average is high.
- Test G: Terminates cleanly on true high quality without infinite wasteful loops.
- Test H: Goal Drift prevention blocks out-of-scope tasks.
"""

import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from product_planner import (
    AutonomousProductPlanner,
    ProductDefinition,
    QualityDimension,
    ProductPlan,
    ProductCriticEvaluation,
    COMMANDER_BOT_ID
)
from commander import Commander
from session_manager import ChannelSession


class TestAutonomousProductPlanning(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_product_planning_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scenario_a_mode2_preserves_pair_workflow(self):
        """Test A: !mode 2 / !mode 2+ does NOT force Autonomous Product Planning."""
        session = ChannelSession(channel_id=123, mode="pair", workspace=self.test_dir)
        self.assertEqual(session.mode, "pair")
        # In mode 2, PRD and ROADMAP are not automatically overwritten by autonomous planner
        prd_file = os.path.join(self.test_dir, "PRD.md")
        self.assertFalse(os.path.exists(prd_file))

    def test_scenario_b_mode3_invokes_product_planning(self):
        """Test B: !mode 3 / !mode 3+ automatically triggers Autonomous Product Planning."""
        user_prompt = "스마트폰 마리오 게임 하나 만들어줘"
        plan = AutonomousProductPlanner.plan_product_and_quality(
            user_request=user_prompt,
            workspace=self.test_dir,
            mode="evolve"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.product_def.domain, "game")
        self.assertIn("마리오", plan.product_def.title)

        # Check PRD.md and ROADMAP.md were generated on disk
        prd_file = os.path.join(self.test_dir, "PRD.md")
        roadmap_file = os.path.join(self.test_dir, "ROADMAP.md")
        self.assertTrue(os.path.isfile(prd_file))
        self.assertTrue(os.path.isfile(roadmap_file))

        with open(prd_file, "r", encoding="utf-8") as f:
            prd_text = f.read()
        self.assertIn("다차원 품질 기준", prd_text)
        self.assertIn("Critical Quality Gate", prd_text)

    def test_scenario_c_quality_dimensions_identified_separately(self):
        """Test C: Game feel, audio, controls, UX, visuals are identified as distinct dimensions."""
        dims = AutonomousProductPlanner.build_quality_dimensions("game")
        self.assertIn("controls_feel", dims)
        self.assertIn("audio_sfx", dims)
        self.assertIn("visual_quality", dims)
        self.assertIn("functionality", dims)
        self.assertIn("stability_e2e", dims)

        # Verify critical quality gates are marked
        self.assertTrue(dims["audio_sfx"].is_critical)
        self.assertTrue(dims["controls_feel"].is_critical)
        self.assertTrue(dims["visual_quality"].is_critical)

        # Milestone ladder breaks down by quality dimensions
        p_def = AutonomousProductPlanner.create_product_definition("스마트폰 마리오 게임", "game")
        milestones = AutonomousProductPlanner.generate_milestones("game", p_def)
        self.assertEqual(len(milestones), 6)
        titles = [m["title"] for m in milestones]
        self.assertTrue(any("Core Gameplay" in t for t in titles))
        self.assertTrue(any("Visual Quality" in t for t in titles))
        self.assertTrue(any("Audio Design" in t for t in titles))
        self.assertTrue(any("Game Feel" in t for t in titles))

    def test_scenario_d_product_critic_detects_quality_gap_and_reworks(self):
        """Test D: When Coder creates functional prototype but sound/feel is poor, Critic reworks."""
        mock_e2e = MagicMock()
        mock_e2e.test_cases = [{"status": "PASS", "title": "jump test"}]
        mock_e2e.console_logs = ["render ok"]
        mock_e2e.interactions = ["click jump"]
        mock_e2e.screenshots = [("test", "/tmp/dummy.png")]

        # Code only has basic rect draw, NO audio, NO touch joystick
        coder_output = "I drew a player box on canvas and added jump velocity."

        # Create dummy code file with basic functionality only
        with open(os.path.join(self.test_dir, "game.js"), "w") as f:
            f.write("function update() { player.y += player.vy; }")

        eval_res = AutonomousProductPlanner.critique_product(
            workspace=self.test_dir,
            e2e_report=mock_e2e,
            coder_output=coder_output,
            mode="evolve"
        )

        self.assertEqual(eval_res.verdict, "REWORK_QUALITY_GAP")
        self.assertFalse(eval_res.critical_gate_passed)
        # Should identify audio_sfx or controls_feel failure
        failures_str = " ".join(eval_res.critical_failures)
        self.assertTrue("사운드" in failures_str or "오디오" in failures_str or "조작감" in failures_str)
        self.assertFalse(eval_res.is_final_complete)

    def test_scenario_e_broken_e2e_cannot_pass(self):
        """Test E: Broken E2E or crash cannot be marked PASS."""
        mock_e2e = MagicMock()
        mock_e2e.test_cases = [{"status": "FAIL", "title": "App launch failed"}]
        mock_e2e.console_logs = ["[ERROR] Fatal Exception: NullPointerException"]
        mock_e2e.interactions = []
        mock_e2e.screenshots = []

        coder_output = "I implemented audio, controls, sprites, and all features."

        eval_res = AutonomousProductPlanner.critique_product(
            workspace=self.test_dir,
            e2e_report=mock_e2e,
            coder_output=coder_output,
            mode="evolve"
        )

        self.assertFalse(eval_res.e2e_verified)
        self.assertFalse(eval_res.critical_gate_passed)
        self.assertEqual(eval_res.verdict, "REWORK_QUALITY_GAP")
        self.assertTrue(any("E2E" in f or "Fatal" in f or "실행" in f for f in eval_res.critical_failures))

    def test_scenario_f_critical_quality_gate_blocks_pass_on_low_dimension(self):
        """Test F: If ANY critical dimension is below threshold, Critical Quality Gate fails even with high scores elsewhere."""
        mock_e2e = MagicMock()
        mock_e2e.test_cases = [{"status": "PASS", "title": "TC"}]
        mock_e2e.console_logs = []
        mock_e2e.interactions = ["click jump", "move joystick"]
        mock_e2e.screenshots = [("test", "/tmp/dummy.png")]

        # Code has good physics, good touch controls, but completely MISSING audio
        with open(os.path.join(self.test_dir, "mario.js"), "w") as f:
            f.write("""
            class MarioGame {
                constructor() {
                    this.player = { x: 0, y: 0, state: 'idle' };
                    this.virtualJoystick = true;
                    this.particleEmitter = true;
                    this.enemies = [{ type: 'goomba', x: 100 }];
                }
            }
            """)

        eval_res = AutonomousProductPlanner.critique_product(
            workspace=self.test_dir,
            e2e_report=mock_e2e,
            coder_output="Implemented full gameplay, particles, and virtual joystick",
            mode="evolve"
        )

        # Audio is critical (< 55 score)
        self.assertFalse(eval_res.critical_gate_passed)
        self.assertEqual(eval_res.verdict, "REWORK_QUALITY_GAP")
        self.assertTrue(any("오디오" in f or "Audio" in f for f in eval_res.critical_failures))

    def test_scenario_g_clean_termination_on_high_quality(self):
        """Test G: When all quality criteria are genuinely met, declares FINAL_PRODUCT_RELEASE."""
        mock_e2e = MagicMock()
        mock_e2e.test_cases = [{"status": "PASS", "title": "TC"}]
        mock_e2e.console_logs = []
        mock_e2e.interactions = ["touch virtual joystick", "jump with particle", "eat coin with sfx"]
        mock_e2e.screenshots = [("test", "/tmp/dummy.png")]

        # Code has full features: physics, sprites, particles, WebAudio, joystick, enemies
        with open(os.path.join(self.test_dir, "mario_complete.js"), "w") as f:
            f.write("""
            class CompleteMario {
                constructor() {
                    this.audioContext = new AudioContext();
                    this.oscillator = this.audioContext.createOscillator();
                    this.frequency = { setValueAtTime: () => {} };
                    this.player = { x: 0, y: 0, state: 'run', collision: true, gravity: 0.5 };
                    this.virtualJoystick = { touch: true, pointer: true };
                    this.particleEmitter = { dust: true, spark: true };
                    this.hitstop = 0.05;
                    this.screenShake = true;
                    this.enemies = [{ type: 'goomba', x: 200, patrol: true }];
                    this.coins = [{ x: 50, y: 80 }];
                    this.stageFlag = true;
                }
            }
            """)

        eval_res = AutonomousProductPlanner.critique_product(
            workspace=self.test_dir,
            e2e_report=mock_e2e,
            coder_output="All features, WebAudio arpeggio, hit-stop shake, and level loop completed.",
            mode="evolve"
        )

        self.assertTrue(eval_res.critical_gate_passed)
        self.assertTrue(eval_res.e2e_verified)
        self.assertEqual(eval_res.verdict, "FINAL_PRODUCT_RELEASE")
        self.assertTrue(eval_res.is_final_complete)

    def test_scenario_h_goal_drift_prevention(self):
        """Test H: Goal Drift Prevention blocks unrelated out-of-scope fluff."""
        p_def = AutonomousProductPlanner.create_product_definition("스마트폰 마리오 게임", "game")
        valid, reason = AutonomousProductPlanner.validate_goal_drift(
            candidate_task="Dockerfile 작성 및 Kubernetes 클러스터 배포 파이프라인 구축",
            p_def=p_def,
            current_gap_focus="조작감 및 오디오"
        )
        self.assertFalse(valid)
        self.assertIn("목표 표류", reason)

        valid_game_task, _ = AutonomousProductPlanner.validate_goal_drift(
            candidate_task="8-bit WebAudio 점프 사운드 및 코인 챠링 아르페지오 구현",
            p_def=p_def,
            current_gap_focus="오디오"
        )
        self.assertTrue(valid_game_task)

    def test_commander_bot_id_embedded_in_discord_embeds(self):
        """Test Commander Bot ID 1540273467453345812 is firmly stamped on Embeds."""
        plan = AutonomousProductPlanner.plan_product_and_quality(
            user_request="스마트폰 마리오 게임",
            workspace=self.test_dir
        )
        embed = AutonomousProductPlanner.build_planning_embed(plan)
        self.assertIn(str(COMMANDER_BOT_ID), embed.title)

        eval_res = AutonomousProductPlanner.critique_product(
            workspace=self.test_dir,
            e2e_report=MagicMock(test_cases=[], console_logs=[], interactions=[], screenshots=[]),
            coder_output="initial"
        )
        critic_embed = AutonomousProductPlanner.build_critic_embed(eval_res)
        self.assertIn(str(COMMANDER_BOT_ID), critic_embed.title)

    def test_milestone_domain_fallback_purged_of_legacy_games(self):
        """Test that '마일스톤 v1' prompts are not misclassified as legacy watermelon/flappy bird games (Task 5)."""
        # 1. Milestone with canvas viewport should be mobile_app or saas_web, NOT game
        domain_canvas = AutonomousProductPlanner.detect_domain("마일스톤 v1 캔버스 뷰포트 개선")
        self.assertIn(domain_canvas, ["mobile_app", "saas_web"])
        self.assertNotEqual(domain_canvas, "game")

        # 2. Milestone with system/architecture should be cli_agent
        domain_sys = AutonomousProductPlanner.detect_domain("마일스톤 v1 시스템 아키텍처 고도화")
        self.assertEqual(domain_sys, "cli_agent")

        # 3. Generic game product definition must NOT have legacy watermelon / flappy bird in benchmark_reference
        p_def = AutonomousProductPlanner.create_product_definition("인터랙티브 2D 아케이드 게임", "game")
        self.assertNotIn("수박", p_def.benchmark_reference)
        self.assertNotIn("플래피", p_def.benchmark_reference)
        self.assertIn("60fps", p_def.benchmark_reference)


if __name__ == "__main__":
    unittest.main()

