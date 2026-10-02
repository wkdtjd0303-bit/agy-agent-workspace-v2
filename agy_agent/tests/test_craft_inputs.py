#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for craft_inputs.py + 연동 (P0-1 ~ P2-7 디테일/우선순위 개선).

Tests:
  1. craft_inputs 저장/로드/CLI 라운드트립
  2. 레퍼런스 이미지 해석(우선순위/중복제거/상한) + 스타일 타깃+델타 블록
  3. 발산 지시 블록 (P1-5)
  4. 레퍼런스 델타 파서 (P0-4)
  5. 스타일 SSOT 병합 (design_tokens.json + craft_inputs.json) → 디자인 계약 상속 (P0-2/P2-7)
  6. 계약 캐시 스타일 지문 무효화
  7. ProductValuePlanner 가치 브리프 tie-break (P0-3)
  8. PlanWizard 가치/스타일 브리프 프롬프트 주입
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from types import SimpleNamespace

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

os.environ.setdefault("UNITTEST_RUNNING", "1")

import craft_inputs  # noqa: E402


class TestCraftInputs(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_craft_inputs_")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, rel: str, content: str) -> str:
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _touch_image(self, rel: str) -> str:
        return self._write(rel, "PNGDATA")

    # ---------- 1. 저장/로드 ----------
    def test_save_load_roundtrip(self):
        craft_inputs.save_inputs(self.ws, {
            "reference_images": ["a.png", "b.png"],
            "house_rules": ["여백 넉넉", "Primary CTA 1개"],
            "copy_tone": "동사구 라벨",
            "value_brief": {"who": "1인 개발자", "one_job": "30초 저장", "cost_of_wrong": "신뢰 붕괴"},
        })
        loaded = craft_inputs.load_inputs(self.ws)
        self.assertEqual(loaded["reference_images"], ["a.png", "b.png"])
        self.assertEqual(loaded["house_rules"], ["여백 넉넉", "Primary CTA 1개"])
        self.assertEqual(loaded["copy_tone"], "동사구 라벨")
        self.assertEqual(loaded["value_brief"]["one_job"], "30초 저장")

        # 부분 patch 는 기존 값을 보존한다
        craft_inputs.save_inputs(self.ws, {"copy_tone": "간결체"})
        again = craft_inputs.load_inputs(self.ws)
        self.assertEqual(again["copy_tone"], "간결체")
        self.assertEqual(again["value_brief"]["who"], "1인 개발자")

    def test_cli_main_persists_inputs(self):
        craft_inputs.main([
            "--workspace", self.ws,
            "--house-rule", "4pt 그리드",
            "--copy-tone", "동사구",
            "--who", "개발자", "--one-job", "기록 저장", "--cost", "이탈",
        ])
        loaded = craft_inputs.load_inputs(self.ws)
        self.assertEqual(loaded["house_rules"], ["4pt 그리드"])
        self.assertEqual(loaded["value_brief"]["who"], "개발자")

    # ---------- 2. 레퍼런스 해석 + 타깃 블록 ----------
    def test_resolve_reference_images_priority_and_cap(self):
        explicit = self._touch_image("reports/gate2/refs/home.png")
        self._touch_image("design_refs/ref_0.png")
        self._touch_image("design_refs/ref_1.png")
        craft_inputs.save_inputs(self.ws, {"reference_images": ["reports/gate2/refs/home.png", "missing.png"]})

        refs = craft_inputs.resolve_reference_images(self.ws, max_images=3)
        self.assertEqual(refs[0], explicit, "명시 레퍼런스가 최우선이어야 함")
        self.assertEqual(len(refs), 3, "상한이 적용되어야 함")
        self.assertTrue(all(os.path.isfile(p) for p in refs))

        capped = craft_inputs.resolve_reference_images(self.ws, max_images=1)
        self.assertEqual(capped, [explicit])

    def test_reference_target_block_contains_delta_and_empty_when_none(self):
        # 패키지 전역 design_refs 폴백을 끄면 레퍼런스가 없는 워크스페이스는 빈 블록이어야 한다.
        prev = os.environ.get("CRAFT_PACKAGE_REFS_ENABLED")
        os.environ["CRAFT_PACKAGE_REFS_ENABLED"] = "0"
        try:
            self.assertEqual(craft_inputs.build_reference_target_block(self.ws), "")
        finally:
            if prev is None:
                os.environ.pop("CRAFT_PACKAGE_REFS_ENABLED", None)
            else:
                os.environ["CRAFT_PACKAGE_REFS_ENABLED"] = prev

        self._touch_image("design_refs/ref_0.png")
        block = craft_inputs.build_reference_target_block(self.ws)
        self.assertIn("레퍼런스 스타일 타깃", block)
        self.assertIn("델타 규칙", block)
        self.assertIn("ref_0.png", block)

    def test_package_design_refs_revives_dead_assets(self):
        # P0-1: 패키지 동봉 design_refs/* 는 죽은 자산이 아니라 최후순위 폴백으로 배선된다.
        refs = craft_inputs.resolve_reference_images(self.ws, max_images=2)
        self.assertTrue(refs, "패키지 design_refs/* 가 전역 폴백으로 해석되어야 함")

    # ---------- 3. 발산 ----------
    def test_divergence_block_present(self):
        block = craft_inputs.build_divergence_block()
        self.assertIn("발산", block)
        self.assertIn("대안", block)

    # ---------- 4. 레퍼런스 델타 파서 ----------
    def test_parse_reference_delta(self):
        text = (
            "[REFERENCE_DELTA_VERDICT]: FAIL\n"
            "[REFERENCE_DELTA_SCORE]: 58\n"
            "[REFERENCE_DELTA_SUMMARY]: 밀도가 타깃보다 과밀\n"
            "[REFERENCE_DELTA_DEFECTS]:\n"
            "- MAJOR | DENSITY_GAP | 상단 영역 | 타깃은 여백이 넉넉한데 과밀 | 카드 간 24px 확보\n"
            "- MINOR | TYPO_RHYTHM_GAP | 헤더 | 제목 위계가 평평함 | 제목 1.5rem 강화\n"
        )
        parsed = craft_inputs.parse_reference_delta(text)
        self.assertEqual(parsed["verdict"], "FAIL")
        self.assertEqual(parsed["score"], 58)
        self.assertEqual(parsed["defects"][0]["rule_id"], "DENSITY_GAP")
        self.assertEqual(parsed["defects"][0]["severity"], "MAJOR")

    # ---------- 5. 스타일 SSOT 병합 + 계약 상속 ----------
    def test_style_tokens_merge_and_contract_inherit(self):
        self._write(
            "reports/gate2/design_tokens.json",
            json.dumps({"style_archetype": "Apple Negative Space", "palette": {"soft": "#C4B5FD"}, "card_radius": "rounded-3xl"}, ensure_ascii=False),
        )
        craft_inputs.save_inputs(self.ws, {
            "house_rules": ["여백 넉넉", "Primary CTA 1개"],
            "copy_tone": "동사구 라벨, 마케팅 문구 금지",
        })
        style = craft_inputs.load_style_tokens(self.ws)
        self.assertEqual(style["style_archetype"], "Apple Negative Space")
        self.assertEqual(style["house_rules"], ["여백 넉넉", "Primary CTA 1개"])
        self.assertIn("동사구", style["copy_tone"])

        from visual_craft_loop import VisualCraftLoop, build_design_contract
        contract = build_design_contract("mobile_app", style=style)
        self.assertEqual(contract["style_archetype"], "Apple Negative Space")
        self.assertEqual(contract["color"]["reference_palette"]["soft"], "#C4B5FD")
        self.assertEqual(contract["house_rules"], ["여백 넉넉", "Primary CTA 1개"])
        self.assertIn("동사구", contract["copy_tone"])

        block = VisualCraftLoop.get_contract_prompt_block(self.ws, "mobile_app")
        self.assertIn("카피 톤", block)
        self.assertIn("하우스 룰", block)
        self.assertIn("Thumb Zone", block, "기존 계약 문구는 보존되어야 함")

    def test_contract_cache_invalidated_by_style_change(self):
        from visual_craft_loop import VisualCraftLoop
        craft_inputs.save_inputs(self.ws, {"copy_tone": "톤 A"})
        first = VisualCraftLoop.ensure_design_contract(self.ws, "mobile_app")
        self.assertEqual(first["copy_tone"], "톤 A")

        craft_inputs.save_inputs(self.ws, {"copy_tone": "톤 B"})
        second = VisualCraftLoop.ensure_design_contract(self.ws, "mobile_app")
        self.assertEqual(second["copy_tone"], "톤 B", "스타일 변경 시 계약 캐시가 무효화되어야 함")

    # ---------- 7. 우선순위 tie-break ----------
    def test_product_value_planner_value_brief_tiebreak(self):
        from product_value_planner import select_capability
        stub = SimpleNamespace(
            implemented_components=[], source_files=[], entrypoints=[],
            completed_milestones=[], current_milestone="",
        )
        default_cap = select_capability("cli_agent", reality=stub, workspace=self.ws, root_goal="")
        self.assertEqual(default_cap.id, "cli.interactive", "브리프 없으면 카탈로그 1순위여야 함")

        craft_inputs.save_inputs(self.ws, {"value_brief": {"one_job": "테마"}})
        brief_cap = select_capability("cli_agent", reality=stub, workspace=self.ws, root_goal="")
        self.assertEqual(brief_cap.id, "cli.theme", "가치 브리프가 tie-break로 반영되어야 함")

    # ---------- 8. Wizard 브리프 주입 ----------
    def test_plan_wizard_craft_brief_injection(self):
        from plan_wizard import PlanWizardState
        state = PlanWizardState(channel_id=1, user_id=1, initial_idea="할일 앱")
        state.value_who = "1인 개발자"
        state.value_one_job = "30초 안에 저장"
        state.value_cost_of_wrong = "신뢰 붕괴"
        state.house_rules = "여백 넉넉"
        state.copy_tone = "동사구"
        lines = state.build_craft_brief_lines()
        self.assertTrue(any("30초" in l for l in lines))
        prompt = state.generate_full_prompt()
        self.assertIn("가치/스타일 브리프", prompt)
        self.assertIn("동사구", prompt)


if __name__ == "__main__":
    unittest.main()
