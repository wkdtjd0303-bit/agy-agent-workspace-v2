#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_product_value_planner.py

👑 Commander **제품 가치 기획(Product Value Planning)** 회귀 테스트.

배경(사용자 지적):
    Commander가 도메인 사다리 종단에서 습관적으로
    '경계 조건 및 대용량 데이터 입력 시 크래시 방어 / 응답 속도 최적화 및 불필요한 연산 제거 /
     전체 E2E 시나리오 테스트 100% 무결점 통과' 같은 **자기참조적 품질 하드닝**을
    다음 마일스톤으로 세웠다. 이는 기획자의 역할이 아니다.

검증 항목:
1. 하드닝 전용 기획 판정 (제목/과제 표지)
2. 도메인별 사용자 가치 카탈로그 선택 (구현된 기능은 건너뜀)
3. Commander 결정론적 폴백이 하드닝 대신 사용자 체감 신규 기능을 발주하는지
4. 피드백/신호/심도/스크린샷 결손 플랜은 절대 덮어쓰지 않는지
5. 사용자 가치 기능이 소진되면 그때만 하드닝 폴백을 허용하는지
6. RoadmapManager 자동 확장이 하드닝 템플릿보다 가치 기능을 우선하는지
7. Discord 표시/Directive 에 제품 가치 진단이 포함되는지
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

os.environ.setdefault("UNITTEST_RUNNING", "1")

from product_value_planner import (  # noqa: E402
    ProductValuePlanner,
    is_hardening_only_plan,
    normalize_domain,
    catalog_for,
)
from commander import Commander  # noqa: E402
from roadmap_manager import RoadmapManager  # noqa: E402


class _FakeReality:
    """Commander.CodebaseReality 더ck-typing 용 최소 스텁."""

    def __init__(self, components=(), files=(), completed=(), current=None, tech="Web"):
        self.implemented_components = list(components)
        self.source_files = list(files)
        self.entrypoints = []
        self.completed_milestones = list(completed)
        self.current_milestone = current
        self.tech_stack = tech
        self.is_empty = not (components or files)


class TestHardeningDetection(unittest.TestCase):
    def test_exact_quoted_hardening_tasks_are_detected(self):
        # 사용자가 지적한 바로 그 세 과제.
        self.assertTrue(
            is_hardening_only_plan(
                "상용 배포 품질 하드닝 및 E2E 회귀 검증",
                [
                    "경계 조건 및 대용량 데이터 입력 시 크래시 방어",
                    "응답 속도 최적화 및 불필요한 연산 제거",
                    "전체 E2E 시나리오 테스트 100% 무결점 통과",
                ],
            )
        )

    def test_memory_cache_hardening_detected(self):
        self.assertTrue(
            is_hardening_only_plan(
                "초고속 메모리 캐시 풀 및 OOM 선제적 방어 하드닝",
                ["LruCache 메모리 풀", "프레임 드롭 제로화", "전 기능 E2E 회귀 테스트 100% 무결점 통과"],
            )
        )

    def test_value_plan_is_not_hardening(self):
        self.assertFalse(
            is_hardening_only_plan(
                "원클릭 내보내기·공유 및 결과물 저장",
                ["결과물 내보내기 기능", "공유 액션 및 미리보기", "진행률 표시"],
            )
        )

    def test_mixed_value_plus_defense_is_not_hardening_only(self):
        # 가치 기능 1개 + 방어 1개 혼합은 정상 기획이므로 차단하지 않는다.
        self.assertFalse(
            is_hardening_only_plan(
                "검색·필터 구현",
                ["실시간 검색 및 하이라이트", "예외 처리 방어 및 빈 상태 안내"],
            )
        )

    def test_english_production_hardening_detected(self):
        self.assertTrue(
            is_hardening_only_plan(
                "Production Hardening & Headless E2E (자동화 검증 및 패키징)",
                ["pytest 단위 및 E2E 테스트 100% 통과", "배포 패키징"],
            )
        )


class TestCapabilitySelection(unittest.TestCase):
    def test_domain_normalization(self):
        self.assertEqual(normalize_domain("saas"), "saas_web")
        self.assertEqual(normalize_domain("android"), "mobile_app")
        self.assertEqual(normalize_domain("backend"), "backend_api")
        self.assertEqual(normalize_domain(None), "generic")

    def test_selects_unimplemented_capability(self):
        cap = ProductValuePlanner.select_capability("game", reality=_FakeReality(["Canvas", "Player", "Apple"]))
        self.assertIsNotNone(cap)
        self.assertEqual(cap.id, "game.pause_options")

    def test_skips_already_implemented_capability(self):
        # PauseMenu 컴포넌트가 이미 있으면 일시정지/옵션은 결손이 아니다.
        cap = ProductValuePlanner.select_capability(
            "game", reality=_FakeReality(["Canvas", "Player", "PauseMenu", "SettingsScreen"])
        )
        self.assertIsNotNone(cap)
        self.assertNotEqual(cap.id, "game.pause_options")

    def test_capability_skipped_when_already_in_roadmap(self):
        cap = ProductValuePlanner.select_capability(
            "saas_web",
            reality=_FakeReality(["Editor"]),
            existing_titles=["원클릭 내보내기·공유 및 결과물 저장"],
        )
        self.assertIsNotNone(cap)
        self.assertNotEqual(cap.id, "web.export_share")

    def test_build_seed_explicitly_supersedes_hardening(self):
        seed = ProductValuePlanner.build_seed("saas_web", root_goal="그래픽 편집기", reality=_FakeReality(["Editor", "Canvas"]))
        self.assertIsNotNone(seed)
        self.assertFalse(is_hardening_only_plan(seed["title"], seed["what"]))
        self.assertIn("사용자", seed["why"])

    def test_no_candidate_returns_none_when_all_implemented(self):
        # 모든 game detect 토큰을 구현 컴포넌트로 채워 카탈로그를 소진시킨다.
        all_tokens = []
        for cap in catalog_for("game"):
            all_tokens.extend(cap.detect_tokens())
        cap = ProductValuePlanner.select_capability("game", reality=_FakeReality(all_tokens))
        self.assertIsNone(cap)


class TestCommanderValuePlanningIntegration(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_product_value_")
        # 성숙한 웹 앱: 컴포넌트 4개 (is_empty=False, comps>3)
        self._write("package.json", '{"name": "editor"}')
        for name in ("Editor", "CanvasLayer", "ToolPalette", "ColorPicker"):
            self._write(f"src/{name}.ts", f"export const {name} = () => ({{}});")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, rel, content=""):
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_terminal_fallback_issues_value_not_hardening(self):
        reality = Commander.inspect_codebase_reality(self.ws)
        plan = Commander._build_heuristic_adaptive_plan(
            root_user_goal="포토샵 같은 그래픽 편집기를 개발해줘",
            reality=reality,
            domain="saas_web",
            target_version="v4.0",
            workspace=self.ws,
        )
        self.assertEqual(plan.source, "product_value")
        self.assertFalse(Commander.is_generic_validation_plan(plan.title, plan.what))
        self.assertNotIn("하드닝", plan.title)
        self.assertTrue(plan.product_value_analysis)
        self.assertIn("제품 가치", plan.to_discord_message())
        self.assertIn("제품 가치", plan.directive_for_coder)

    def test_hardening_fallback_when_value_exhausted(self):
        reality = Commander.inspect_codebase_reality(self.ws)
        with patch.object(Commander, "_value_seed", return_value=None):
            plan = Commander._build_heuristic_adaptive_plan(
                root_user_goal="포토샵 같은 그래픽 편집기를 개발해줘",
                reality=reality,
                domain="saas_web",
                target_version="v4.0",
                workspace=self.ws,
            )
        # 가치 기능이 소진된 경우에만 하드닝 폴백을 허용한다.
        self.assertTrue(Commander.is_generic_validation_plan(plan.title, plan.what))

    def test_feedback_plan_is_never_overridden(self):
        plan = Commander._build_heuristic_5w1h_plan(
            milestone_title="사용자 피드백 반영: 버튼 가독성 개선",
            milestone_version="v4.0",
            tasks=["버튼 대비 개선", "터치 타깃 확장"],
            domain="saas_web",
            workspace=self.ws,
            user_prompt="버튼 글씨가 잘 안 보여",
        )
        plan.source = "feedback"
        reality = Commander.inspect_codebase_reality(self.ws)
        guarded = Commander._prefer_value_over_hardening(
            plan, "포토샵 편집기", reality, "saas_web", "v4.0", self.ws
        )
        self.assertIs(guarded, plan)
        self.assertEqual(guarded.source, "feedback")

    def test_signal_plan_is_never_overridden(self):
        plan = Commander._build_heuristic_5w1h_plan(
            milestone_title="상용 배포 품질 하드닝 및 E2E 회귀 검증",
            milestone_version="v4.0",
            tasks=["경계 조건 및 대용량 데이터 입력 시 크래시 방어", "전체 E2E 테스트 100% 통과"],
            domain="saas_web",
            workspace=self.ws,
            user_prompt="",
        )
        plan.source = "signal"
        reality = Commander.inspect_codebase_reality(self.ws)
        guarded = Commander._prefer_value_over_hardening(
            plan, "포토샵 편집기", reality, "saas_web", "v4.0", self.ws
        )
        self.assertIs(guarded, plan)

    def test_non_hardening_plan_is_preserved(self):
        plan = Commander._build_heuristic_5w1h_plan(
            milestone_title="실시간 본문 검색 및 하이라이트",
            milestone_version="v4.0",
            tasks=["검색 인덱싱", "하이라이트 오버레이"],
            domain="saas_web",
            workspace=self.ws,
            user_prompt="",
        )
        reality = Commander.inspect_codebase_reality(self.ws)
        guarded = Commander._prefer_value_over_hardening(
            plan, "포토샵 편집기", reality, "saas_web", "v4.0", self.ws
        )
        self.assertIs(guarded, plan)


class TestRoadmapAutoExpansion(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_pv_roadmap_")
        self._write("main.py", "print('hi')\n")
        self._write(
            "ROADMAP.md",
            "# Roadmap\n\n"
            "## v3.2 — 마지막 단계 ✅\n- [x] 완료\n",
        )

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, rel, content=""):
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path) or self.ws, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_auto_expand_prefers_value_capability_over_hardening(self):
        mgr = RoadmapManager(self.ws)
        mgr.load()
        new_ms = mgr.auto_expand_roadmap()
        self.assertTrue(new_ms, "로드맵 자동 확장이 아무 것도 생성하지 않았습니다")
        titles = [m.title for m in new_ms]
        self.assertTrue(
            any(not is_hardening_only_plan(t, [task.text for task in m.tasks]) for m, t in zip(new_ms, titles)),
            f"자동 확장이 하드닝만 생성했습니다: {titles}",
        )


if __name__ == "__main__":
    unittest.main()
