#!/usr/bin/env python3
"""
🧪 Domain-Adaptive E2E Engine Unit & Integration Tests (AGY 3.0 Standard)
Validates the dynamic differentiation, specialized interaction drivers,
and defect detection accuracy for Drawing/Canvas Apps vs High-Performance Games.

Test Matrix:
1. TestArchetypeClassification: Distinguishes Drawing vs Gaming vs Standard UI
2. TestDrawingCanvasDriver: Bezier strokes, signature zigzag, and 7-stage canvas sequence
3. TestGamingEngineDriver: Virtual joystick drags, evasive dash, combo bursts, and 6-stage game sequence
4. TestPixelMutationVerifier: Frame-to-frame pixel deltas, visual freeze detection, and color shift
5. TestExportFileIntegrityVerifier: Detects 0-byte files, header corruption, and all-black blank exports
6. TestDomainDefectDetector: Flags 4 Canvas Defects + 4 Gaming Defects with 100% recall
7. TestScenarioGraphEngineDomainAdaptation: Generates distinct verification graphs for Game vs Canvas
8. TestAutonomousUiNavigatorDomainIntegration: Employs specialized actions (stroke / joystick_drag)
9. TestE2EReadinessScore: Proves 95+ points out of 100 on domain-adaptive readiness
"""

import os
import sys
import unittest
from unittest.mock import MagicMock
from PIL import Image, ImageDraw

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from domain_adaptive_engine import (
    DomainAdaptiveEngine,
    DrawingCanvasDriver,
    GamingEngineDriver,
    PixelMutationVerifier,
    ExportFileIntegrityVerifier,
    DomainDefectDetector,
    PixelDeltaResult,
    ExportFileVerification,
    VerificationTargetSpec,
    VerificationTargetExtractor,
    AdaptiveE2EOrchestrator
)
from scenario_graph_engine import ScenarioGraphEngine
from autonomous_ui_navigator import AutonomousUiNavigator, UiNode


class TestDomainAdaptiveE2E(unittest.TestCase):

    def setUp(self):
        # Create in-memory test images for visual diff & mutation tests
        # 1. Blank white canvas (100x100)
        self.img_white = Image.new("RGB", (100, 100), (255, 255, 255))
        # 2. Canvas with a black stroke
        self.img_with_stroke = Image.new("RGB", (100, 100), (255, 255, 255))
        draw = ImageDraw.Draw(self.img_with_stroke)
        draw.line([(10, 10), (90, 90)], fill=(0, 0, 0), width=4)
        # 3. Canvas with a red stroke
        self.img_with_red_stroke = Image.new("RGB", (100, 100), (255, 255, 255))
        draw_red = ImageDraw.Draw(self.img_with_red_stroke)
        draw_red.line([(10, 10), (90, 90)], fill=(255, 0, 0), width=4)
        # 4. All-black canvas (corrupted export bug)
        self.img_all_black = Image.new("RGB", (100, 100), (0, 0, 0))

    # 1. Test Archetype Classification
    def test_archetype_classification(self):
        """Proves that drawing apps and gaming apps are correctly classified into distinct archetypes."""
        draw_type = DomainAdaptiveEngine.classify_app_archetype(
            package_name="com.creative.sketchpaint",
            activity_name="com.creative.sketchpaint.CanvasActivity",
            prd_text="손으로 그림을 그리고 스케치하며 브러시와 팔레트로 색칠하는 그림판 앱"
        )
        self.assertEqual(draw_type, "DRAWING_CANVAS")

        game_type = DomainAdaptiveEngine.classify_app_archetype(
            package_name="com.epic.actionarcade",
            activity_name="com.epic.actionarcade.GameActivity",
            prd_text="가상 조이스틱으로 캐릭터를 조작하여 몬스터를 처치하고 점수를 획득하는 60fps 액션 게임"
        )
        self.assertEqual(game_type, "HIGH_PERF_GAMING")

        standard_type = DomainAdaptiveEngine.classify_app_archetype(
            package_name="com.bank.transfer",
            activity_name="com.bank.transfer.MainActivity",
            prd_text="계좌 이체 및 금융 자산 관리 대시보드"
        )
        self.assertEqual(standard_type, "STANDARD_UI")

    # 2. Test Drawing Canvas Driver
    def test_drawing_canvas_driver_bezier_and_sequence(self):
        """Validates that DrawingCanvasDriver produces smooth Bezier curves and 7 distinct drawing phases."""
        p0 = (100, 100)
        p1 = (300, 50)
        p2 = (500, 200)
        curve = DrawingCanvasDriver.generate_bezier_points(p0, p1, p2, num_steps=10)
        self.assertEqual(len(curve), 11)
        self.assertEqual(curve[0], p0)
        self.assertEqual(curve[-1], p2)

        sequence = DrawingCanvasDriver.plan_canvas_e2e_sequence()
        self.assertEqual(len(sequence), 7)
        action_types = [s["action_type"] for s in sequence]
        self.assertIn("CANVAS_FREEHAND_STROKE", action_types)
        self.assertIn("CANVAS_PALETTE_SELECT", action_types)
        self.assertIn("CANVAS_ERASER_PASS", action_types)
        self.assertIn("CANVAS_UNDO_REDO", action_types)
        self.assertIn("CANVAS_CLEAR", action_types)
        self.assertIn("CANVAS_EXPORT_SAVE", action_types)

    # 3. Test Gaming Engine Driver
    def test_gaming_engine_driver_sequence(self):
        """Validates that GamingEngineDriver produces 6 distinct game-specific interaction phases."""
        sequence = GamingEngineDriver.plan_gaming_e2e_sequence()
        self.assertEqual(len(sequence), 6)
        action_types = [s["action_type"] for s in sequence]
        self.assertIn("GAME_JOYSTICK_DRAG", action_types)
        self.assertIn("GAME_EVASIVE_SWIPES", action_types)
        self.assertIn("GAME_RAPID_COMBO_BURST", action_types)
        self.assertIn("GAME_LONG_PRESS_CHARGE", action_types)
        self.assertIn("GAME_CHAOS_BOUNDS_TOUCH", action_types)
        self.assertIn("GAME_SOFTLOCK_RECOVERY", action_types)

    # 4. Test Pixel Mutation Verifier
    def test_pixel_mutation_verifier(self):
        """Measures pixel changes between blank canvas and stroke-rendered canvas."""
        # Mutation: White -> Stroke
        res = PixelMutationVerifier.analyze_pixel_mutation(self.img_white, self.img_with_stroke)
        self.assertFalse(res.is_visual_frozen)
        self.assertGreater(res.delta_ratio, 0.01)
        self.assertGreater(res.changed_pixels, 0)

        # Stagnation / Freeze: White -> White
        res_frozen = PixelMutationVerifier.analyze_pixel_mutation(self.img_white, self.img_white)
        self.assertTrue(res_frozen.is_visual_frozen)
        self.assertEqual(res_frozen.changed_pixels, 0)
        self.assertEqual(res_frozen.delta_ratio, 0.0)

    # 5. Test Export File Integrity Verifier
    def test_export_file_integrity_verifier(self):
        """Flags zero-byte files, non-existent files, and monochrome blank canvas exports."""
        # 1. Non-existent file
        ver_missing = ExportFileIntegrityVerifier.verify_exported_image("/tmp/non_existent_artwork.png")
        self.assertFalse(ver_missing.is_valid)
        self.assertTrue(ver_missing.is_zero_byte)

        # 2. Valid image bytes
        import io
        buf = io.BytesIO()
        self.img_with_stroke.save(buf, format="PNG")
        ver_valid = ExportFileIntegrityVerifier.verify_exported_image(buf.getvalue())
        self.assertTrue(ver_valid.is_valid)
        self.assertFalse(ver_valid.is_all_black_or_white)

        # 3. All-black corrupted export bug
        buf_black = io.BytesIO()
        self.img_all_black.save(buf_black, format="PNG")
        ver_black = ExportFileIntegrityVerifier.verify_exported_image(buf_black.getvalue())
        self.assertFalse(ver_black.is_valid)
        self.assertTrue(ver_black.is_all_black_or_white)

    # 6. Test Domain Defect Detector (4 Canvas Defects + 4 Gaming Defects)
    def test_canvas_defect_detection(self):
        """Ensures all 4 critical canvas defects are detected with precise evidence."""
        # 1. Stroke mutation failure (Drawing made no pixels)
        zero_delta = PixelDeltaResult(delta_ratio=0.0, changed_pixels=0, total_pixels=10000, is_visual_frozen=True)
        defect_stroke = DomainDefectDetector.evaluate_canvas_stroke_mutation(zero_delta)
        self.assertIsNotNone(defect_stroke)
        self.assertEqual(defect_stroke.defect_id, "DEFECT_CANVAS_STROKE_MUTATION_FAILURE")

        # 2. Color sync mismatch (Selected Red, but R shift is 0)
        defect_color = DomainDefectDetector.evaluate_canvas_color_sync(
            expected_color_rgb=(255, 0, 0),
            actual_shift={"r_shift": 0.0, "g_shift": 45.0, "b_shift": 45.0}
        )
        self.assertIsNotNone(defect_color)
        self.assertEqual(defect_color.defect_id, "DEFECT_CANVAS_COLOR_SYNC_FAILURE")

        # 3. Corrupted export
        corrupted_export = ExportFileVerification(
            file_path="/sdcard/drawing.png",
            file_size_bytes=0,
            is_zero_byte=True,
            is_all_black_or_white=True,
            image_format="NONE",
            dimensions=(0, 0),
            is_valid=False,
            details="Zero-byte file"
        )
        defect_export = DomainDefectDetector.evaluate_canvas_export(corrupted_export)
        self.assertIsNotNone(defect_export)
        self.assertEqual(defect_export.defect_id, "DEFECT_CANVAS_EXPORT_CORRUPTED")

        # 4. Eraser failure
        defect_eraser = DomainDefectDetector.evaluate_eraser_mutation(
            self.img_white, self.img_with_stroke
        )
        self.assertIsNotNone(defect_eraser)
        self.assertEqual(defect_eraser.defect_id, "DEFECT_ERASER_MUTATION_FAILURE")

    def test_gaming_defect_detection(self):
        """Ensures all 4 critical gaming defects are detected with precise evidence."""
        # 1. Visual Freeze
        frozen_seq = [
            PixelDeltaResult(delta_ratio=0.0, changed_pixels=0, total_pixels=1000, is_visual_frozen=True),
            PixelDeltaResult(delta_ratio=0.0, changed_pixels=0, total_pixels=1000, is_visual_frozen=True)
        ]
        defect_freeze = DomainDefectDetector.evaluate_game_visual_freeze(frozen_seq)
        self.assertIsNotNone(defect_freeze)
        self.assertEqual(defect_freeze.defect_id, "DEFECT_GAME_VISUAL_FREEZE")

        # 2. Input Unresponsiveness
        unresponsive_delta = PixelDeltaResult(delta_ratio=0.0001, changed_pixels=1, total_pixels=10000, is_visual_frozen=True)
        defect_input = DomainDefectDetector.evaluate_game_input_unresponsiveness(unresponsive_delta)
        self.assertIsNotNone(defect_input)
        self.assertEqual(defect_input.defect_id, "DEFECT_GAME_INPUT_UNRESPONSIVE")

        # 3. Frame drop / Jank (FPS 18 on 60fps target)
        defect_jank = DomainDefectDetector.evaluate_game_jank_frame_drop(measured_fps=18.5, target_fps=60)
        self.assertIsNotNone(defect_jank)
        self.assertEqual(defect_jank.defect_id, "DEFECT_GAME_JANK_FRAME_DROP")

        # 4. Game Over Soft-lock
        defect_softlock = DomainDefectDetector.evaluate_game_softlock(
            screen_text="GAME OVER",
            screen_delta=PixelDeltaResult(delta_ratio=0.0, changed_pixels=0, total_pixels=1000, is_visual_frozen=True),
            has_interactive_elements=False
        )
        self.assertIsNotNone(defect_softlock)
        self.assertEqual(defect_softlock.defect_id, "DEFECT_GAME_SOFTLOCK")

    # 7. Test Scenario Graph Engine Domain Adaptation
    def test_scenario_graph_engine_domain_adaptation(self):
        """Proves that ScenarioGraphEngine produces fundamentally different graphs for Game vs Canvas."""
        # Canvas workspace mock
        mock_canvas_ws = "/tmp/mock_canvas_project"
        os.makedirs(mock_canvas_ws, exist_ok=True)
        with open(os.path.join(mock_canvas_ws, "PRD.md"), "w") as f:
            f.write("# 그림판 드로잉 캔버스 앱\n터치로 스케치하고 브러시 색칠하는 캔버스")

        canvas_graph = ScenarioGraphEngine.build_verification_graph(workspace=mock_canvas_ws)
        self.assertEqual(canvas_graph.app_category, "drawing")
        canvas_cp_50 = next(c for c in canvas_graph.checkpoints if c.percent == 50)
        self.assertIn("베지에 곡선", " ".join(canvas_cp_50.actions))
        self.assertIn("Pixel Delta", " ".join(canvas_cp_50.assertions))

        # Game workspace mock
        mock_game_ws = "/tmp/mock_game_project"
        os.makedirs(mock_game_ws, exist_ok=True)
        with open(os.path.join(mock_game_ws, "PRD.md"), "w") as f:
            f.write("# 아케이드 60fps 슈팅 게임\n조이스틱으로 회피하고 연타 공격하는 액션 게임")

        game_graph = ScenarioGraphEngine.build_verification_graph(workspace=mock_game_ws)
        self.assertEqual(game_graph.app_category, "game")
        game_cp_50 = next(c for c in game_graph.checkpoints if c.percent == 50)
        self.assertIn("조이스틱", " ".join(game_cp_50.actions))
        self.assertIn("60fps", " ".join(game_cp_50.assertions))

    # 8. Test AutonomousUiNavigator Domain Integration
    def test_navigator_domain_actions(self):
        """Verifies that navigator triggers specialized stroke or joystick_drag when accessibility nodes are absent."""
        canvas_node = UiNode(class_name="android.view.SurfaceView", bounds=(0, 0, 1080, 2400))
        
        # Drawing mode
        canvas_decision = AutonomousUiNavigator.decide_next_action(
            nodes=[canvas_node],
            app_archetype="DRAWING_CANVAS"
        )
        self.assertEqual(canvas_decision.get("action"), "stroke")
        self.assertIn("points", canvas_decision)
        self.assertGreater(len(canvas_decision["points"]), 5)

        # Gaming mode
        game_decision = AutonomousUiNavigator.decide_next_action(
            nodes=[canvas_node],
            app_archetype="HIGH_PERF_GAMING"
        )
        self.assertEqual(game_decision.get("action"), "joystick_drag")
        self.assertIn("center", game_decision)
        self.assertIn("target", game_decision)

    # 9. Test E2E Readiness Score (0-100 target >= 95)
    def test_e2e_readiness_score(self):
        """Proves that with domain drivers, pixel mutation, export verifiers, and defect detectors, score reaches 100."""
        score_res = DomainAdaptiveEngine.compute_e2e_readiness_score(
            has_domain_drivers=True,
            has_pixel_mutation_verifier=True,
            has_file_integrity_verifier=True,
            has_domain_defect_detectors=True,
            detected_critical_defects=0,
            detected_major_defects=0
        )
        self.assertGreaterEqual(score_res["score"], 95.0)
        self.assertEqual(score_res["score"], 100.0)
        self.assertEqual(score_res["grade"], "EXCELLENT (95+)")

    # 10. Test 'What to See' Verification Target Extractor
    def test_verification_target_extractor_canvas_vs_game(self):
        """Validates that VerificationTargetExtractor extracts precise 'What to see' invariants for Canvas vs Game."""
        # 1. Canvas targets
        canvas_targets = VerificationTargetExtractor.extract_targets(archetype="DRAWING_CANVAS")
        target_ids_canvas = [t.target_id for t in canvas_targets]
        self.assertIn("VT_CANVAS_STROKE_RENDER", target_ids_canvas)
        self.assertIn("VT_CANVAS_COLOR_SYNC", target_ids_canvas)
        self.assertIn("VT_CANVAS_ERASER_RESTORE", target_ids_canvas)
        self.assertIn("VT_CANVAS_EXPORT_INTEGRITY", target_ids_canvas)

        # 2. Gaming targets
        game_targets = VerificationTargetExtractor.extract_targets(archetype="HIGH_PERF_GAMING")
        target_ids_game = [t.target_id for t in game_targets]
        self.assertIn("VT_GAME_INPUT_RESPONSIVENESS", target_ids_game)
        self.assertIn("VT_GAME_VISUAL_FREEZE_DEFENSE", target_ids_game)
        self.assertIn("VT_GAME_FPS_STABILITY", target_ids_game)
        self.assertIn("VT_GAME_SOFTLOCK_DEFENSE", target_ids_game)

    # 11. Test Adaptive E2E Orchestrator 5-Phase Planning
    def test_adaptive_e2e_orchestrator_planning(self):
        """Validates that AdaptiveE2EOrchestrator synthesizes complete 5-phase execution plans."""
        plan_canvas = AdaptiveE2EOrchestrator.plan_orchestration_run(archetype="DRAWING_CANVAS")
        self.assertEqual(len(plan_canvas["pipeline_phases"]), 5)
        self.assertEqual(plan_canvas["total_actuation_stages"], 7)
        self.assertGreaterEqual(plan_canvas["total_targets_to_see"], 4)

        plan_game = AdaptiveE2EOrchestrator.plan_orchestration_run(archetype="HIGH_PERF_GAMING")
        self.assertEqual(len(plan_game["pipeline_phases"]), 5)
        self.assertEqual(plan_game["total_actuation_stages"], 6)
        self.assertGreaterEqual(plan_game["total_targets_to_see"], 4)

    # 12. Test Full Actuation Workflows (Drawing & Gaming)
    def test_full_actuation_workflows(self):
        """Validates that execute_full_canvas_workflow and execute_full_gaming_loop run through all stages without error."""
        def _mock_adb(cmd, timeout=3.0):
            return 0, "", ""

        # Test Canvas Full 7-stage Workflow
        canvas_res = DrawingCanvasDriver.execute_full_canvas_workflow(_mock_adb)
        self.assertEqual(canvas_res["total_stages"], 7)
        self.assertTrue(canvas_res["success"])
        self.assertEqual(len(canvas_res["stages_executed"]), 7)

        # Test Gaming Full 6-stage Loop
        game_res = GamingEngineDriver.execute_full_gaming_loop(_mock_adb)
        self.assertEqual(game_res["total_stages"], 6)
        self.assertTrue(game_res["success"])
        self.assertEqual(len(game_res["stages_executed"]), 6)

    # 13. Test 5-Phase Adaptive E2E Session Execution & Disk Evidence
    def test_adaptive_e2e_session_execution_and_evidence(self):
        """Validates that AdaptiveE2EOrchestrator executes all 5 phases and writes Gate 4 JSON evidence."""
        out_dir = "/tmp/test_gate4_reports"
        os.makedirs(out_dir, exist_ok=True)

        session_res = AdaptiveE2EOrchestrator.execute_orchestration_session(
            target_path_or_pkg="com.creative.sketchpad.apk",
            prd_text="손가락으로 그림을 그리고 스케치하는 드로잉 캔버스",
            output_report_dir=out_dir
        )

        self.assertEqual(session_res.archetype, "DRAWING_CANVAS")
        self.assertEqual(session_res.overall_verdict, "PASS")
        self.assertEqual(len(session_res.phases), 5)
        self.assertTrue(all(p.status == "PASS" for p in session_res.phases))
        self.assertTrue(os.path.isfile(session_res.evidence_file))
        self.assertGreater(os.path.getsize(session_res.evidence_file), 100)


if __name__ == "__main__":
    unittest.main()
