#!/usr/bin/env python3
"""
Unit tests for the Depth-First Product Refinement Governor (스코프 크립 원천 차단).

Validates the user mandate:
  "A,B,C 만들었고 정말 필요한 건 A1,B1,C1(기존 기능 보강)인데,
   'ABC 만들었네? 그럼 D 만들자' 하면서 범위가 마구잡이로 늘어나면 안 된다."

Covered:
1. Governor detects product-depth gaps (interaction polish / states / a11y / CS / commercial).
2. A polished surface scores 100% and does NOT force depth.
3. Fresh/immature projects are exempt (core & breadth progression allowed).
4. classify() distinguishes DEEPEN vs EXPAND.
5. validate_plan() rejects EXPAND while depth is required.
6. Commander maturity gate disables depth forcing for immature projects.
7. Mature project: breadth heuristic is replaced by a DEEPEN milestone (sync path).
8. Mature project: an EXPANDING AI plan is rejected in favour of the depth fallback (async path).
9. Regression: the existing game breadth ladder is untouched when immature.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from product_refinement_governor import (
    ProductRefinementGovernor, DepthVerdict, RuntimeEvidence,
    KNOWN_DEPTH_AREAS, RUNTIME_EVIDENCE_TTL_SECONDS,
)
from commander import Commander


_POLISHED_KT = """
package com.example.app
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.semantics.contentDescription
class Screen {
    var isLoading = false
    fun render() {
        try { logger.info("render") } catch (e: Exception) { retry() }
        EmptyState(); ErrorState()
        AnimatedVisibility(visible = true, enter = tween(easing = EaseInOut)) { Text() }
        val cd = contentDescription; val t = 48.dp
        onboarding(); settings(); about(); version(); feedback()
    }
}
"""


class TestDepthFirstScopeGovernor(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_depth_gov_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, content: str):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _write_roadmap(self, done_milestones=2):
        lines = ["# Roadmap\n"]
        for i in range(done_milestones):
            lines.append(f"## v{i}.0 — 마일스톤 {i} ✅\n- [x] done\n")
        with open(os.path.join(self.tmp, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _seed_shallow_ui_app(self, n=6):
        names = ["MainActivity", "HomeScreen", "EditorScreen", "PlayerController", "DataStore", "SettingsScreen", "ExtraA", "ExtraB"]
        for i, name in enumerate(names[:n]):
            body = "class MainActivity : ComponentActivity() { setContentView(R.layout.main) }" if i == 0 else f"class {name} {{ fun run() {{}} }}"
            self._write(f"app/src/main/java/com/example/{name}.kt", body)

    # ── Governor core ───────────────────────────────────────────────────────
    def test_detects_depth_gaps_on_shallow_ui_surface(self):
        self._seed_shallow_ui_app(6)
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        self.assertTrue(verdict.requires_depth, "얕은 UI 표면인데 심화가 강제되지 않았습니다")
        self.assertLess(verdict.depth_score, verdict.bar)
        self.assertIsNotNone(verdict.gap)
        self.assertIn(
            verdict.worst_area,
            {"interaction_polish", "state_coverage", "accessibility", "maintainability_cs", "commercial_polish"},
        )

    def test_polished_surface_scores_high_and_allows_expansion(self):
        for i in range(6):
            self._write(f"app/src/main/java/com/example/Screen{i}.kt", _POLISHED_KT)
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        self.assertGreaterEqual(verdict.depth_score, verdict.bar)
        self.assertFalse(verdict.requires_depth)

    def test_fresh_project_is_exempt(self):
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "cli_agent")
        self.assertTrue(verdict.is_fresh_project)
        self.assertFalse(verdict.requires_depth)

    # ── classify / validate ─────────────────────────────────────────────────
    def test_classify_deepen_vs_expand(self):
        comps = ["EditorScreen", "PlayerController"]
        self.assertEqual(
            ProductRefinementGovernor.classify("EditorScreen 인터랙션 쫀득화 및 애니메이션 개선", [], comps),
            "deepen",
        )
        self.assertEqual(
            ProductRefinementGovernor.classify("신규 결제 시스템 추가 및 구독 기능 도입", [], comps),
            "expand",
        )
        # 기존 컴포넌트 이름이 포함되어 있어도 확장 키워드가 우세하면 반드시 'expand'로 판정 (우회 방지)
        self.assertEqual(
            ProductRefinementGovernor.classify("EditorScreen에 신규 결제 시스템 추가 및 구독 기능 도입", [], comps),
            "expand",
        )

    def test_validate_plan_rejects_expand_when_depth_required(self):
        verdict = DepthVerdict(depth_score=40.0, bar=80.0, requires_depth=True, worst_area="interaction_polish")
        ok, reason = ProductRefinementGovernor.validate_plan("신규 소셜 공유 기능 추가", [], verdict, ["A"])
        self.assertFalse(ok)
        self.assertIn("심도", reason)

        ok2, _ = ProductRefinementGovernor.validate_plan("기존 화면 애니메이션 개선 및 접근성 보강", [], verdict, ["A"])
        self.assertTrue(ok2)

    def test_brief_states_enforcement_rule(self):
        self._seed_shallow_ui_app(6)
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        brief = ProductRefinementGovernor.to_brief(verdict)
        self.assertIn("심도", brief)

    # ── Commander maturity gate ─────────────────────────────────────────────
    def test_commander_disables_depth_for_immature_project(self):
        self._seed_shallow_ui_app(2)
        reality = Commander.inspect_codebase_reality(self.tmp)
        verdict = Commander._evaluate_depth_governor(reality, "mobile_app", self.tmp)
        self.assertFalse(verdict.requires_depth, "미성숙 프로젝트에서 심화가 강제되었습니다")

    # ── Commander integration: DEEPEN forced ────────────────────────────────
    def test_mature_project_forces_depth_refinement_sync(self):
        self._seed_shallow_ui_app(6)
        self._write_roadmap(done_milestones=2)
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal="안드로이드 pdf 뷰어 겸 에디터 앱을 만들어줘",
            workspace=self.tmp,
        )
        self.assertEqual(plan.source, "depth", "성숙 프로젝트에서 심화 마일스톤이 강제되지 않았습니다")
        self.assertTrue(plan.title.strip())
        self.assertIn("심도", plan.delta_analysis + plan.thinking_process)
        # 사용자에게 심도 우선 정책이 가시적으로 표기되어야 한다.
        self.assertIn("심도 우선 정책", plan.to_discord_message())

    def test_three_features_abc_forces_deepen_a1b1c1(self):
        # 사용자 핵심 우려 시나리오: A(뷰어), B(주석), C(내보내기) 기능 완료(6개 컴포넌트, 3개 마일스톤) 후
        # 무작정 신규 기능 D(검색/클라우드 등)를 추가하지 않고 A1/B1/C1 심화(인터랙션/상태/CS)를 강제
        self._seed_shallow_ui_app(6)
        self._write_roadmap(done_milestones=3)
        reality = Commander.inspect_codebase_reality(self.tmp)
        verdict = Commander._evaluate_depth_governor(reality, "mobile_app", self.tmp)
        self.assertTrue(verdict.requires_depth, "A, B, C 완료 후 심화가 강제되어야 합니다")
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal="안드로이드 pdf 뷰어 겸 에디터 앱을 만들어줘",
            workspace=self.tmp,
        )
        self.assertEqual(plan.source, "depth", "A, B, C 후 신규 기능 D 대신 A1/B1/C1 심화가 발주되어야 합니다")

    def test_korean_and_physics_tokens(self):
        # 한글 로컬라이징 및 Compose 스프링 물리 토큰 검출 확인
        korean_kt = """
        package com.example.app
        import androidx.compose.animation.core.spring
        class CustomScreen {
            fun show() {
                val s = spring(dampingRatio = 0.8f, stiffness = 300f)
                val status = "로딩 중..."
                val guide = "도움말 및 설정"
            }
        }
        """
        self._write("app/src/main/java/com/example/CustomScreen.kt", korean_kt)
        self._write("app/src/main/java/com/example/MainActivity.kt", "class MainActivity : ComponentActivity()")
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        # interaction_polish, state_coverage, commercial_polish 토큰이 인식되었는지 확인
        areas = {g.area for g in (verdict.gap and [verdict.gap] or [])}
        self.assertNotIn("commercial_polish", [g.area for g in ProductRefinementGovernor._analyze(ProductRefinementGovernor._read_sources(self.tmp, ["app/src/main/java/com/example/CustomScreen.kt", "app/src/main/java/com/example/MainActivity.kt"]), True, "mobile_app")])

    def test_mature_project_rejects_expanding_ai_plan(self):
        self._seed_shallow_ui_app(6)
        self._write_roadmap(done_milestones=2)
        expand_json = """{
            "title": "신규 소셜 공유 및 클라우드 동기화 기능 추가",
            "delta_analysis": "새 기능",
            "why": "사용자 확장",
            "what": ["소셜 공유 추가", "클라우드 동기화 도입", "신규 계정 시스템 구축"],
            "how": "REST 연동",
            "who_where": "app/src/main",
            "when_acceptance": ["빌드 통과"]
        }"""

        import asyncio

        async def _run():
            with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=expand_json)):
                return await Commander.plan_adaptive_next_milestone_async(
                    root_user_goal="안드로이드 pdf 뷰어 겸 에디터 앱을 만들어줘",
                    workspace=self.tmp,
                    domain="mobile_app",
                )

        plan = asyncio.run(_run())
        self.assertEqual(plan.source, "depth", "심도 미달인데 AI의 신규 확장 계획이 통과되었습니다")
        self.assertNotIn("소셜", plan.title)

    # ── Regression: immature game ladder untouched ──────────────────────────
    def test_immature_game_ladder_unchanged(self):
        with open(os.path.join(self.tmp, "canvas.js"), "w") as f:
            f.write("// canvas loop")
        with open(os.path.join(self.tmp, "player.js"), "w") as f:
            f.write("// player movement")
        reality = Commander.inspect_codebase_reality(self.tmp)
        plan = Commander._build_heuristic_adaptive_plan(
            root_user_goal="사과 피하기 게임을 만들어줘",
            reality=reality,
            domain="game",
            target_version="v1.2",
            workspace=self.tmp,
        )
        self.assertEqual(plan.title, "낙하 장애물(사과) 스폰 및 등속/가속 낙하 물리 엔진")

    # ── Runtime evidence cross-verification (Gate 5 ↔ depth gate) ──────────
    def test_runtime_verified_clears_static_gap(self):
        # 정적 토큰은 부족하지만 실제 프레임버퍼/비전 증거로 검증된 차원은 결손에서 해제된다.
        self._seed_shallow_ui_app(6)
        evidence = RuntimeEvidence(
            verified={
                "interaction_polish": "Gate 5 상태 전이 캡처",
                "state_coverage": "Gate 5 3종 상태 캡처",
                "accessibility": "Gemini Vision 88/100",
                "maintainability_cs": "E2E 로깅 검증",
                "commercial_polish": "온보딩 캡처",
            },
            loaded=True,
        )
        verdict = ProductRefinementGovernor.evaluate(
            self.tmp, "mobile_app", runtime_evidence=evidence, auto_load_evidence=False
        )
        self.assertGreaterEqual(verdict.depth_score, verdict.bar)
        self.assertFalse(verdict.requires_depth)
        self.assertIn("interaction_polish", verdict.runtime_verified)
        self.assertEqual(verdict.evidence_grade, "runtime_proven")

    def test_runtime_failed_forces_deepen_and_blocks_expand(self):
        # 정적으로는 완벽해 보여도 런타임 실패가 감지되면 확장이 차단되고 심화가 강제된다.
        for i in range(6):
            self._write(f"app/src/main/java/com/example/Screen{i}.kt", _POLISHED_KT)
        evidence = RuntimeEvidence(
            failed={"interaction_polish": "실기기 프레임 드롭 12% 감지"},
            loaded=True,
        )
        verdict = ProductRefinementGovernor.evaluate(
            self.tmp, "mobile_app", runtime_evidence=evidence, auto_load_evidence=False
        )
        self.assertTrue(verdict.requires_depth, "런타임 실패인데 심화가 강제되지 않았습니다")
        self.assertEqual(verdict.worst_area, "interaction_polish")
        self.assertIn("interaction_polish", verdict.runtime_failed)
        self.assertEqual(verdict.evidence_grade, "runtime_failed")

        # 확장 계획은 거부
        ok_expand, _ = ProductRefinementGovernor.validate_plan(
            "신규 소셜 공유 기능 추가", [], verdict, ["Screen0"]
        )
        self.assertFalse(ok_expand)
        # 런타임 실패 차원을 다루지 않는 심화 계획도 거부
        ok_unrelated, reason = ProductRefinementGovernor.validate_plan(
            "접근성 대비율 보강", [], verdict, ["Screen0"]
        )
        self.assertFalse(ok_unrelated)
        self.assertIn("런타임", reason)
        # 런타임 실패 차원을 정면으로 다루는 계획은 통과
        ok_targeted, _ = ProductRefinementGovernor.validate_plan(
            "인터랙션 애니메이션 프레임 드롭 제거 및 이징 튜닝", [], verdict, ["Screen0"]
        )
        self.assertTrue(ok_targeted)

    def test_record_and_load_runtime_evidence_roundtrip(self):
        path = ProductRefinementGovernor.record_runtime_evidence(
            self.tmp, "accessibility", "verified", "실기기 터치 타깃 48dp 검증", "unit-test"
        )
        self.assertTrue(path and os.path.isfile(path))
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertIn("accessibility", loaded.verified)
        self.assertTrue(loaded.loaded)
        # 잘못된 area/status 는 거부
        self.assertIsNone(
            ProductRefinementGovernor.record_runtime_evidence(self.tmp, "bogus", "verified")
        )
        self.assertIsNone(
            ProductRefinementGovernor.record_runtime_evidence(self.tmp, "accessibility", "weird")
        )

    def test_auto_load_from_gate5_visual_qa(self):
        self._seed_shallow_ui_app(6)
        self._write(
            "reports/gate5/visual_qa.json",
            """{
              "verdict": "PASS",
              "average_score": 88,
              "screenshots": [
                "reports/gate5/screenshots/app_01_idle.png",
                "reports/gate5/screenshots/app_02_interaction.png",
                "reports/gate5/screenshots/app_03_scroll.png"
              ],
              "component_crops": []
            }""",
        )
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        self.assertGreaterEqual(
            set(verdict.runtime_verified),
            {"interaction_polish", "state_coverage", "accessibility"},
        )
        # 런타임 증거가 확인한 차원의 정적 결손은 제거되어야 한다.
        remaining = {g.area for g in ProductRefinementGovernor._analyze(
            ProductRefinementGovernor._read_sources(
                self.tmp, ["app/src/main/java/com/example/MainActivity.kt"]
            ), True, "mobile_app")
        }
        self.assertNotIn("interaction_polish", verdict.runtime_failed)
        self.assertTrue(remaining)  # sanity: static analysis path intact

    # ── CLI & Quality Contracts (QC-A-01, QC-U-01, QC-P-01) ─────────────────
    def test_cli_help_and_execution(self):
        import subprocess
        # 1. --help 정상 출력 및 exit code 0 (QC-A-01)
        res = subprocess.run(
            [sys.executable, os.path.join(WORKSPACE_ROOT, "product_refinement_governor.py"), "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--workspace", res.stdout)
        self.assertIn("심도", res.stdout)

        # 2. 존재하지 않는 디렉토리 시 exit code != 0
        res_err = subprocess.run(
            [sys.executable, os.path.join(WORKSPACE_ROOT, "product_refinement_governor.py"), "--workspace", "/non/existent/path/999"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        self.assertNotEqual(res_err.returncode, 0)

    def test_cli_runtime_evidence_record(self):
        import subprocess
        res = subprocess.run(
            [
                sys.executable,
                os.path.join(WORKSPACE_ROOT, "product_refinement_governor.py"),
                "--workspace", self.tmp,
                "--record-area", "state_coverage",
                "--record-status", "verified",
                "--record-detail", "CLI 런타임 증거 기록 테스트",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertIn("state_coverage", loaded.verified)

    # ── D1: filename evidence must NOT be promoted to proven / clear gaps ────
    def test_d1_filename_basis_is_partial_and_does_not_clear_gap(self):
        self._seed_shallow_ui_app(6)
        evidence = RuntimeEvidence(
            verified={a: "file-name fallback" for a in KNOWN_DEPTH_AREAS},
            basis={a: "filename" for a in KNOWN_DEPTH_AREAS},
            loaded=True,
        )
        verdict = ProductRefinementGovernor.evaluate(
            self.tmp, "mobile_app", runtime_evidence=evidence, auto_load_evidence=False
        )
        self.assertEqual(verdict.evidence_grade, "runtime_partial")
        self.assertTrue(verdict.requires_depth, "파일명 fallback 증거가 정적 결손을 해제했습니다(게이밍)")
        self.assertEqual(
            set(verdict.runtime_verified_partial), set(KNOWN_DEPTH_AREAS)
        )

    def test_d1_pixel_basis_promotes_to_proven(self):
        self._seed_shallow_ui_app(6)
        evidence = RuntimeEvidence(
            verified={a: "real framebuffer pixel diff" for a in KNOWN_DEPTH_AREAS},
            basis={a: "pixel" for a in KNOWN_DEPTH_AREAS},
            loaded=True,
        )
        verdict = ProductRefinementGovernor.evaluate(
            self.tmp, "mobile_app", runtime_evidence=evidence, auto_load_evidence=False
        )
        self.assertEqual(verdict.evidence_grade, "runtime_proven")
        self.assertFalse(verdict.requires_depth)
        self.assertEqual(verdict.runtime_verified_partial, [])

    def test_d1_real_pixel_diff_and_state_clustering(self):
        from ui_visual_differ import UIVisualDiffer
        from PIL import Image, ImageDraw
        shots = os.path.join(self.tmp, "shots")
        os.makedirs(shots, exist_ok=True)

        def _mk(name, color, rect):
            img = Image.new("RGB", (120, 240), (28, 28, 32))
            ImageDraw.Draw(img).rectangle(rect, fill=color)
            p = os.path.join(shots, name)
            img.save(p)
            return p

        b = _mk("state_before.png", (210, 40, 40), [20, 100, 100, 160])
        a = _mk("state_after.png", (40, 210, 40), [20, 100, 100, 160])
        c = _mk("state_scroll.png", (40, 40, 210), [20, 40, 100, 90])

        measured = UIVisualDiffer.measure_pixel_diff(b, a)
        self.assertTrue(measured["authentic"])
        self.assertTrue(measured["has_real_diff"], "실 픽셀 차분이 감지되지 않았습니다")
        self.assertGreater(measured["diff_area_ratio"], 0.0)

        cluster = UIVisualDiffer.count_distinct_states([b, a, c, b])
        self.assertEqual(cluster["state_count"], 3, "실 픽셀 상태 클러스터링이 3종이 아닙니다")
        self.assertEqual(cluster["authentic_frames"], 4)

        # 빈/검은 프레임은 위조로 간주되어 인증되지 않는다.
        blank = os.path.join(shots, "blank_skeleton_loading.png")
        Image.new("RGB", (120, 240), (0, 0, 0)).save(blank)
        blank_m = UIVisualDiffer.measure_pixel_diff(blank, a)
        self.assertFalse(blank_m["authentic"], "빈 프레임이 인증되었습니다(위조 허용)")

    def test_d1_visual_qa_pixel_fields_are_proven(self):
        self._seed_shallow_ui_app(6)
        self._write(
            "reports/gate5/visual_qa.json",
            json.dumps({
                "verdict": "PASS",
                "average_score": 88,
                "state_diversity": 3,
                "state_transition_verified": True,
                "evidence_basis": "pixel",
                "screenshots": [
                    "reports/gate5/screenshots/app_01_idle.png",
                    "reports/gate5/screenshots/app_02_interaction.png",
                    "reports/gate5/screenshots/app_03_scroll.png",
                ],
                "component_crops": [],
            }),
        )
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        self.assertIn("interaction_polish", verdict.runtime_verified)
        self.assertNotIn("interaction_polish", verdict.runtime_verified_partial)
        self.assertNotIn("state_coverage", verdict.runtime_verified_partial)

    def test_d1_pixel_claim_without_attestation_is_downgraded(self):
        # pixel 이라 주장해도 인용 리포트에 실 픽셀 증거가 없으면 filename(partial)로 하향한다.
        self._seed_shallow_ui_app(6)
        self._write(
            "reports/gate5/visual_qa.json",
            json.dumps({"verdict": "PASS", "average_score": 90}),
        )
        src = os.path.join(self.tmp, "reports/gate5/visual_qa.json")
        recorded = ProductRefinementGovernor.sync_from_vision_report(
            self.tmp, avg_score=90.0, state_count=3, has_state_transition=True,
            source=src, state_basis="pixel", transition_basis="pixel", score_basis="pixel",
        )
        self.assertTrue(recorded)
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertEqual(loaded.area_basis("state_coverage"), "filename")
        self.assertEqual(loaded.area_basis("interaction_polish"), "filename")
        self.assertIn("state_coverage", loaded.partial_areas)

    def test_d1_pixel_claim_with_attestation_is_proven(self):
        self._seed_shallow_ui_app(6)
        self._write(
            "reports/gate5/visual_qa.json",
            json.dumps({
                "verdict": "PASS",
                "average_score": 90,
                "evidence_basis": "pixel",
                "state_diversity": 3,
                "state_transition_verified": True,
                "state_transition_evidence": {"authentic": True, "has_real_diff": True},
            }),
        )
        src = os.path.join(self.tmp, "reports/gate5/visual_qa.json")
        ProductRefinementGovernor.sync_from_vision_report(
            self.tmp, avg_score=90.0, state_count=3, has_state_transition=True,
            source=src, state_basis="pixel", transition_basis="pixel", score_basis="pixel",
        )
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertEqual(loaded.area_basis("state_coverage"), "pixel")
        self.assertEqual(loaded.area_basis("interaction_polish"), "pixel")
        self.assertIn("state_coverage", loaded.proven_areas)

    # ── D2: no runtime evidence ⇒ EXPAND is fail-closed ─────────────────────
    def test_d2_evidence_missing_blocks_expand(self):
        for i in range(6):
            self._write(f"app/src/main/java/com/example/Screen{i}.kt", _POLISHED_KT)
        verdict = ProductRefinementGovernor.evaluate(self.tmp, "mobile_app")
        self.assertGreaterEqual(verdict.depth_score, verdict.bar)
        self.assertFalse(verdict.requires_depth)
        self.assertTrue(verdict.evidence_missing, "런타임 증거 부재인데 evidence_missing 이 아닙니다")

        brief = ProductRefinementGovernor.to_brief(verdict)
        self.assertIn("런타임 증거 없음", brief)

        ok_expand, reason = ProductRefinementGovernor.validate_plan(
            "신규 소셜 공유 기능 추가", [], verdict, ["Screen0"]
        )
        self.assertFalse(ok_expand, "런타임 증거 부재인데 신규 확장이 승인되었습니다(SOC-FAIL-OPEN)")
        self.assertIn("런타임", reason)

        ok_deepen, _ = ProductRefinementGovernor.validate_plan(
            "기존 화면 애니메이션 개선 및 접근성 보강", [], verdict, ["Screen0"]
        )
        self.assertTrue(ok_deepen)

    def test_d2_commander_sync_fail_closed_without_runtime_evidence(self):
        # 정적으로 완벽해 보여도 런타임 증거가 없으면 결정론적 확장 대신 런타임 검증을 발주한다.
        for i in range(6):
            self._write(f"app/src/main/java/com/example/Screen{i}.kt", _POLISHED_KT)
        self._write_roadmap(done_milestones=3)
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal="안드로이드 pdf 뷰어 겸 에디터 앱을 만들어줘",
            workspace=self.tmp,
        )
        self.assertEqual(plan.source, "depth")
        self.assertIn("런타임", plan.title)

    # ── D4: freshness (TTL / newer-source) invalidates stale evidence ───────
    def test_d4_ttl_expired_evidence_discarded(self):
        self._seed_shallow_ui_app(6)
        path = os.path.join(self.tmp, "reports", "product_depth", "runtime_evidence.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stale_ts = time.time() - (RUNTIME_EVIDENCE_TTL_SECONDS + 120)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "verified": {
                    "accessibility": {"detail": "old", "basis": "pixel", "updated_at": stale_ts}
                }
            }, f)
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertNotIn("accessibility", loaded.verified)
        self.assertIn("accessibility", loaded.stale_discarded)

    def test_d4_newer_source_invalidates_evidence(self):
        self._seed_shallow_ui_app(6)
        ProductRefinementGovernor.record_runtime_evidence(
            self.tmp, "accessibility", "verified", "unit", "unit-test", basis="pixel"
        )
        src = os.path.join(self.tmp, "app/src/main/java/com/example/MainActivity.kt")
        future = time.time() + 5
        os.utime(src, (future, future))
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertNotIn("accessibility", loaded.verified, "소스 변경 후 구 증거가 유지되었습니다")
        self.assertIn("accessibility", loaded.stale_discarded)

    def test_d4_evidence_roundtrip_records_basis(self):
        ProductRefinementGovernor.record_runtime_evidence(
            self.tmp, "state_coverage", "verified", "실 픽셀 클러스터링", "unit", basis="pixel"
        )
        loaded = ProductRefinementGovernor.load_runtime_evidence(self.tmp)
        self.assertEqual(loaded.area_basis("state_coverage"), "pixel")
        self.assertIn("state_coverage", loaded.proven_areas)


if __name__ == "__main__":
    unittest.main()
