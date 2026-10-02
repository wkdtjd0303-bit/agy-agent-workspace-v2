#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_planner_visual_gap_analyzer.py

👑 Commander 기획 단계의 **스크린샷 실측 제품 결손(Planner Visual Gap)** 회귀 테스트.

검증 항목:
1. Vision 구조화 라인([PLAN_GAPS]) 결정적 파서 및 카테고리/심각도 정규화
2. 로컬 시각 증거(visual_craft_review.json / visual_qa.json) → 결손 도출 (네트워크 불필요)
3. 스크린샷 수집 필터(diff/빈 프레임 제외) 및 리뷰 영속화/재사용
4. 가치 우선 top gap 선택 및 태스크 생성
5. '전체 E2E 시나리오 테스트 100% 무결점 통과'류 일반론 과제 판정
6. Commander 결정론적 기획 폴백이 일반론 대신 스크린샷 결손을 채택하는지
7. 이미 디스크에 구현된 기능은 결손으로 오탐하지 않는지 (Vision 할루시네이션 차단)
8. 피드백 고정 플랜은 스크린샷 결손으로 덮어쓰지 않는지
"""

import os
import sys
import json
import shutil
import asyncio
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

os.environ.setdefault("UNITTEST_RUNNING", "1")

from PIL import Image  # noqa: E402

from planner_visual_gap_analyzer import (  # noqa: E402
    PlannerVisualGapAnalyzer,
    PlannerVisualGapReview,
    PlannerGap,
    parse_planner_gap_output,
    collect_screenshots,
    gaps_from_visual_craft_review,
    gaps_from_visual_qa,
    REVIEW_REL_PATH,
)
from commander import Commander  # noqa: E402


def _make_nonblank_png(path: str, size=(240, 400)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size, (18, 18, 22))
    for i in range(0, size[0], 24):
        for j in range(0, size[1], 24):
            if (i // 24 + j // 24) % 2 == 0:
                for x in range(i, min(i + 12, size[0])):
                    for y in range(j, min(j + 12, size[1])):
                        img.putpixel((x, y), (60, 200, 240))
    img.save(path)
    return path


SAMPLE_VISION_OUTPUT = """[PLAN_GAP_SUMMARY]: 편집 캔버스는 갖췄지만 레이어/선택/색상 관리가 전무하다.
[PLAN_NEXT_MILESTONE]: 레이어 패널 및 레이어 연산
[PLAN_GAPS]:
- MAJOR | CORE_FEATURE | 레이어 패널 | 상단에 '1개 레이어' 텍스트만 있고 레이어 관리 UI가 없음 | 레이어 추가/삭제/숨김/투명도/블렌드 모드 패널 기획
- MINOR | LAYOUT_PANEL | 우측 패널 모듈화 | 우측을 브러시 옵션이 단독 점유 | 탭/아코디언 구조로 재설계
- MAJOR | UX_WORKFLOW | 선택 및 크롭 도구 | 사각형/올가미 선택, 크롭 도구가 부재 | 마퀴/올가미/매직완드 및 크롭 툴 기획
"""


class TestVisionGapParser(unittest.TestCase):
    def test_parse_structured_lines(self):
        parsed = parse_planner_gap_output(SAMPLE_VISION_OUTPUT)
        self.assertEqual(len(parsed["gaps"]), 3)
        self.assertIn("레이어 패널", [g.feature for g in parsed["gaps"]])
        self.assertEqual(parsed["next_milestone"], "레이어 패널 및 레이어 연산")
        layer = next(g for g in parsed["gaps"] if g.feature == "레이어 패널")
        self.assertEqual(layer.category, "CORE_FEATURE")
        self.assertEqual(layer.severity, "MAJOR")
        self.assertIn("1개 레이어", layer.missing)
        self.assertIn("블렌드", layer.plan_hint)

    def test_category_and_severity_normalized(self):
        parsed = parse_planner_gap_output(
            "[PLAN_GAPS]:\n- WARNING | a11y | 터치 타깃 | 40dp | 48dp로 확장\n"
        )
        self.assertEqual(parsed["gaps"][0].category, "ACCESSIBILITY")
        self.assertEqual(parsed["gaps"][0].severity, "MINOR")

    def test_prose_fallback_restores_one_gap(self):
        parsed = parse_planner_gap_output("레이어 패널이 없고 색상 선택기가 부재합니다. 무엇이 부족한지 정리하면...")
        self.assertEqual(len(parsed["gaps"]), 1)
        self.assertEqual(parsed["gaps"][0].source, "vision")

    def test_empty_input(self):
        self.assertEqual(parse_planner_gap_output("")["gaps"], [])


class TestLocalEvidenceFallback(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_planner_gap_")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write_json(self, rel, payload):
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def test_gaps_from_visual_craft_review(self):
        self._write_json(
            os.path.join("reports", "gate5", "visual_craft_review.json"),
            {
                "verdict": "FAIL",
                "vision_summary": "히어로가 첫 화면을 점유",
                "findings": [
                    {"rule_id": "MARKETING_HERO", "severity": "MAJOR", "title": "첫 화면 마케팅 히어로",
                     "evidence": "상단 40% 배너", "fix": "히어로 제거 후 목록 상단 배치"},
                    {"rule_id": "SMALL_TOUCH_TARGET", "severity": "MAJOR", "title": "터치 타깃 미달",
                     "evidence": "36dp", "fix": "48dp로 확장"},
                ],
            },
        )
        gaps, summary = gaps_from_visual_craft_review(self.ws)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(summary, "히어로가 첫 화면을 점유")
        cats = {g.category for g in gaps}
        self.assertIn("UX_WORKFLOW", cats)
        self.assertIn("ACCESSIBILITY", cats)

    def test_gaps_from_visual_qa_low_score(self):
        self._write_json(
            os.path.join("reports", "gate5", "visual_qa.json"),
            {"verdict": "WARN", "average_score": 61, "summary": "글자 잘림과 대비 부족"},
        )
        gaps, _ = gaps_from_visual_qa(self.ws)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].severity, "MINOR")

    def test_visual_qa_pass_yields_nothing(self):
        self._write_json(
            os.path.join("reports", "gate5", "visual_qa.json"),
            {"verdict": "PASS", "average_score": 92, "summary": "양호"},
        )
        self.assertEqual(gaps_from_visual_qa(self.ws)[0], [])


class TestScreenshotCollectionAndPersistence(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_planner_gap_shot_")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_collect_filters_artifacts_and_blanks(self):
        gate5 = os.path.join(self.ws, "reports", "gate5", "screenshots")
        _make_nonblank_png(os.path.join(gate5, "home.png"))
        _make_nonblank_png(os.path.join(gate5, "before_after_diff.png"))
        _make_nonblank_png(os.path.join(gate5, "00_preview.png"))
        # 빈(검은) 프레임
        os.makedirs(gate5, exist_ok=True)
        Image.new("RGB", (200, 200), (0, 0, 0)).save(os.path.join(gate5, "blank.png"))
        shots = collect_screenshots(self.ws, max_shots=5)
        names = [os.path.basename(s) for s in shots]
        self.assertIn("home.png", names)
        self.assertNotIn("before_after_diff.png", names)
        self.assertNotIn("00_preview.png", names)
        self.assertNotIn("blank.png", names)

    def test_analyze_for_planning_uses_local_evidence_and_persists(self):
        # Vision API가 없어도 로컬 시각 증거만으로 결정적 결손 리뷰가 생성/영속화되어야 한다.
        self._write_gate5_evidence()
        review = asyncio.run(
            PlannerVisualGapAnalyzer.analyze_for_planning(
                workspace=self.ws, root_user_goal="이미지 편집기", domain="saas_web"
            )
        )
        self.assertIsNotNone(review)
        self.assertTrue(review.has_gaps)
        self.assertTrue(os.path.isfile(PlannerVisualGapAnalyzer.review_path(self.ws)))
        reloaded = PlannerVisualGapAnalyzer.load_review(self.ws)
        self.assertTrue(reloaded["gaps"])

    def test_no_physical_evidence_returns_none(self):
        review = asyncio.run(
            PlannerVisualGapAnalyzer.analyze_for_planning(
                workspace=self.ws, root_user_goal="빈 프로젝트", domain="saas_web"
            )
        )
        self.assertIsNone(review)

    def _write_gate5_evidence(self):
        _make_nonblank_png(os.path.join(self.ws, "reports", "gate5", "screenshots", "editor.png"))
        path = os.path.join(self.ws, "reports", "gate5", "visual_craft_review.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "verdict": "FAIL",
                    "vision_summary": "레이어/선택 도구 부재",
                    "findings": [
                        {"rule_id": "MARKETING_HERO", "severity": "MAJOR", "title": "마케팅 히어로",
                         "evidence": "상단 배너", "fix": "히어로 제거"},
                    ],
                },
                f,
                ensure_ascii=False,
            )


class TestGapRankingAndTasks(unittest.TestCase):
    def _review(self):
        return PlannerVisualGapReview(
            verdict="GAPS_FOUND",
            gaps=[
                PlannerGap("POLISH", "MINOR", "여백 정리", "여백 불균일", "8pt 그리드 정규화"),
                PlannerGap("CORE_FEATURE", "MAJOR", "레이어 패널", "관리 UI 부재", "레이어 패널 구현"),
                PlannerGap("UX_WORKFLOW", "MINOR", "작업 내역", "History 패널 부재", "다단계 작업 내역 패널"),
            ],
        )

    def test_top_gap_prefers_core_feature_major(self):
        top = PlannerVisualGapAnalyzer.select_top_gap(self._review())
        self.assertEqual(top["feature"], "레이어 패널")

    def test_build_tasks_includes_defensive_line(self):
        tasks = PlannerVisualGapAnalyzer.build_gap_tasks(self._review())
        self.assertTrue(any("레이어 패널" in t for t in tasks))
        self.assertTrue(any("회귀" in t for t in tasks))

    def test_prompt_block_contains_anti_generic_guidance(self):
        block = PlannerVisualGapAnalyzer.format_prompt_block(self._review())
        self.assertIn("레이어 패널", block)
        self.assertIn("일반론", block)

    def test_is_generic_validation_plan(self):
        self.assertTrue(
            PlannerVisualGapAnalyzer.is_generic_validation_plan(
                "상용 배포 품질 하드닝 및 E2E 회귀 검증",
                ["전체 E2E 시나리오 테스트 100% 무결점 통과"],
            )
        )
        self.assertFalse(
            PlannerVisualGapAnalyzer.is_generic_validation_plan(
                "레이어 패널 및 레이어 연산 구현", ["레이어 추가/삭제/숨김"]
            )
        )


class TestCommanderPlanningIntegration(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_commander_visual_gap_")
        self._seed_mature_web_app()
        self._persist_review()

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, rel, content=""):
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _seed_mature_web_app(self):
        """is_empty=False 이고 컴포넌트가 4개 이상인 saas_web 워크스페이스."""
        self._write("package.json", '{"name": "editor"}')
        for name in ("Editor", "CanvasLayer", "ToolPalette", "ColorPicker"):
            self._write(f"src/{name}.ts", f"export const {name} = () => ({{}});")

    def _persist_review(self):
        path = os.path.join(self.ws, REVIEW_REL_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "verdict": "GAPS_FOUND",
                    "engine": "visual_craft_review",
                    "summary": "레이어/선택 관리 결손",
                    "next_milestone": "레이어 패널",
                    "gaps": [
                        {"category": "CORE_FEATURE", "severity": "MAJOR", "feature": "레이어 패널",
                         "missing": "'1개 레이어' 텍스트만 존재", "plan_hint": "레이어 추가/삭제/숨김/투명도 패널 구현",
                         "source": "visual_craft"},
                        {"category": "UX_WORKFLOW", "severity": "MAJOR", "feature": "선택 및 크롭 도구",
                         "missing": "선택/크롭 도구 부재", "plan_hint": "마퀴/올가미/크롭 툴 구현",
                         "source": "visual_craft"},
                    ],
                },
                f,
                ensure_ascii=False,
            )

    def test_generic_fallback_replaced_by_visual_gap(self):
        reality = Commander.inspect_codebase_reality(self.ws)
        self.assertFalse(reality.is_empty)
        self.assertGreater(len(reality.implemented_components), 3)

        plan = Commander._build_heuristic_adaptive_plan(
            root_user_goal="포토샵 같은 그래픽 편집기를 개발해줘",
            reality=reality,
            domain="saas_web",
            target_version="v4.0",
            workspace=self.ws,
        )
        self.assertEqual(plan.source, "visual_gap")
        self.assertIn("레이어 패널", plan.title)
        joined = " ".join(plan.what)
        self.assertNotIn("전체 E2E 시나리오 테스트 100% 무결점 통과", joined)
        self.assertTrue(plan.visual_gap_analysis)
        self.assertIn("스크린샷", plan.to_discord_message())

    def test_sync_adaptive_plan_carries_visual_gap_digest(self):
        plan = Commander.plan_adaptive_next_milestone(
            root_user_goal="포토샵 같은 그래픽 편집기를 개발해줘",
            workspace=self.ws,
            domain="saas_web",
        )
        self.assertEqual(plan.source, "visual_gap")
        self.assertTrue(plan.visual_gap_analysis)
        # Coder 훈령에도 실 스크린샷 결손 증거가 반영되어야 한다.
        self.assertIn("실 렌더링 스크린샷 실측 결손", plan.directive_for_coder)
        self.assertIn("레이어 패널", plan.directive_for_coder)

    def test_already_implemented_feature_is_not_a_gap(self):
        # 리뷰가 이미 구현된 기능을 결손으로 주장하면 오탐으로 보고 무시해야 한다.
        path = os.path.join(self.ws, REVIEW_REL_PATH)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "verdict": "GAPS_FOUND",
                    "gaps": [
                        {"category": "CORE_FEATURE", "severity": "MAJOR", "feature": "ColorPicker",
                         "missing": "없음", "plan_hint": "구현", "source": "vision"},
                    ],
                },
                f,
                ensure_ascii=False,
            )
        reality = Commander.inspect_codebase_reality(self.ws)
        seed = Commander._visual_gap_seed(
            Commander._load_visual_gap_review(self.ws),
            "포토샵 같은 그래픽 편집기를 개발해줘",
            reality, "saas_web", self.ws, "v4.0",
        )
        self.assertIsNone(seed, "이미 구현된 컴포넌트를 결손으로 오탐했습니다")

    def test_feedback_heuristic_plan_not_overridden(self):
        feedback_plan = Commander._build_heuristic_5w1h_plan(
            milestone_title="사용자 피드백 반영: 버튼 가독성 개선",
            milestone_version="v4.0",
            tasks=["버튼 대비 개선", "터치 타깃 확장"],
            domain="saas_web",
            workspace=self.ws,
            user_prompt="버튼 글씨가 잘 안 보여",
        )
        feedback_plan.source = "feedback"
        review = Commander._load_visual_gap_review(self.ws)
        reality = Commander.inspect_codebase_reality(self.ws)
        guarded = Commander._prefer_visual_gap_over_generic(
            feedback_plan, review, "포토샵 같은 그래픽 편집기를 개발해줘",
            reality, "saas_web", "v4.0", self.ws,
        )
        self.assertEqual(guarded.source, "feedback")
        self.assertNotIn("레이어 패널", guarded.title)


if __name__ == "__main__":
    unittest.main()
