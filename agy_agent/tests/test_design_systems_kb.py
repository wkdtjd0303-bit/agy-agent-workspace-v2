#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for design_systems_kb.py — 외부 공개 디자인 표준 그라운딩.

사용자 취향 입력(하우스 룰/레퍼런스)이 없어도 공개 표준(공개 문서)만으로
디테일 기준(형태/엘리베이션/상태/접근성)이 계약에 채워지는지 물리 검증한다.

Tests:
  1. 도메인 → 공개 표준 결정적 매핑
  2. style(사용자/레퍼런스) 지정 오버라이드 + 별칭 + 미지값 무시
  3. 디자인 계약 상속 (기존 필드 보존 + design_system 섹션 추가)
  4. Coder/QA 프롬프트 블록에 표준/문서/형태/접근성/상태 커버리지 노출
  5. 표준이 없는 도메인(game/cli)에서는 그라운딩 생략
  6. craft_inputs 스타일 SSOT → 계약 상속 배선
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

os.environ.setdefault("UNITTEST_RUNNING", "1")

import design_systems_kb as kb  # noqa: E402
from visual_craft_loop import build_design_contract, VisualCraftLoop  # noqa: E402


class TestDesignSystemsKB(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_design_systems_kb_")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, rel: str, content: str) -> str:
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # ---------- 1. 결정적 매핑 ----------
    def test_domain_default_mapping(self):
        self.assertEqual(kb.resolve_design_system("mobile_app")["id"], "material3")
        self.assertEqual(kb.resolve_design_system("android")["id"], "material3")
        self.assertEqual(kb.resolve_design_system("ios")["id"], "apple_hig")
        self.assertEqual(kb.resolve_design_system("saas_web")["id"], "carbon")
        self.assertEqual(kb.resolve_design_system("native_desktop")["id"], "fluent2")
        self.assertIsNone(kb.resolve_design_system("game"), "게임은 공개 표준 매핑이 없어야 함")
        self.assertIsNone(kb.resolve_design_system("cli_agent"))

    # ---------- 2. 사용자 오버라이드 ----------
    def test_style_override_and_alias(self):
        self.assertEqual(
            kb.resolve_design_system("saas_web", {"design_system": "apple_hig"})["id"],
            "apple_hig",
            "사용자 명시 표준이 도메인 기본값을 덮어써야 함",
        )
        self.assertEqual(
            kb.resolve_design_system("mobile_app", {"design_system": "M3"})["id"], "material3",
            "별칭(m3)이 정식 ID로 정규화되어야 함",
        )
        # 미지값은 무시하고 도메인 기본값으로 안전 강등
        self.assertEqual(
            kb.resolve_design_system("saas_web", {"design_system": "없는표준"})["id"], "carbon"
        )

    # ---------- 3. 계약 상속 ----------
    def test_contract_inherits_design_system_without_touching_existing_fields(self):
        contract = build_design_contract("mobile_app")
        self.assertEqual(contract["spacing"]["grid_unit_dp"], 4, "기존 계약 필드는 보존되어야 함")
        self.assertEqual(contract["touch_target"]["min_dp"], 48)
        system = contract.get("design_system")
        self.assertIsInstance(system, dict)
        self.assertEqual(system["id"], "material3")
        self.assertEqual(system["shape_radius_dp"]["full"], 9999)
        self.assertEqual(system["elevation_dp"], [0, 1, 3, 6, 8, 12])
        self.assertIn("a11y", system)
        self.assertIn("required_states", system)

    # ---------- 4. 프롬프트 블록 ----------
    def test_prompt_block_exposes_public_standard_details(self):
        block = VisualCraftLoop.get_contract_prompt_block(self.ws, "mobile_app")
        self.assertIn("시각 완성도 계약", block, "기존 계약 블록은 보존되어야 함")
        self.assertIn("외부 디자인 표준", block)
        self.assertIn("Material Design 3", block)
        self.assertIn("m3.material.io", block)
        self.assertIn("형태(radius)", block)
        self.assertIn("접근성", block)
        self.assertIn("상태 커버리지 필수", block)
        self.assertLess(len(block), 4000, "토큰 다이어트: 블록은 과도하게 길면 안 됨")

    def test_web_domain_uses_carbon_grounding(self):
        block = VisualCraftLoop.get_contract_prompt_block(self.ws, "saas_web")
        self.assertIn("IBM Carbon Design System", block)
        self.assertIn("carbondesignsystem.com", block)

    def test_windows_desktop_uses_fluent_grounding(self):
        self.assertTrue(VisualCraftLoop.is_visual_domain("native_desktop"))
        self.assertTrue(VisualCraftLoop.is_visual_domain("windows"))
        block = VisualCraftLoop.get_contract_prompt_block(self.ws, "native_desktop")
        self.assertIn("Fluent 2", block)
        self.assertIn("fluent2.microsoft.design", block)

    # ---------- 5. 그라운딩 생략 ----------
    def test_no_grounding_for_unmapped_domain(self):
        contract = build_design_contract("cli_agent")
        self.assertNotIn("design_system", contract)
        self.assertEqual(kb.format_design_system_block(contract), "")

    # ---------- 6. craft_inputs SSOT 배선 ----------
    def test_design_system_flows_from_style_tokens(self):
        import craft_inputs
        self._write(
            "reports/gate2/design_tokens.json",
            json.dumps({"design_system": "fluent2", "style_archetype": "Fluent"}, ensure_ascii=False),
        )
        style = craft_inputs.load_style_tokens(self.ws)
        self.assertEqual(style.get("design_system"), "fluent2")
        contract = build_design_contract("saas_web", style=style)
        self.assertEqual(
            contract["design_system"]["id"], "fluent2",
            "사용자가 지정한 표준이 도메인 기본값(carbon)을 덮어써야 함",
        )

    def test_contract_cache_invalidated_by_design_system_change(self):
        import craft_inputs
        craft_inputs.save_inputs(self.ws, {"copy_tone": "톤"})
        self._write(
            "reports/gate2/design_tokens.json",
            json.dumps({"design_system": "material3"}, ensure_ascii=False),
        )
        first = VisualCraftLoop.ensure_design_contract(self.ws, "saas_web")
        self.assertEqual(first["design_system"]["id"], "material3")

        self._write(
            "reports/gate2/design_tokens.json",
            json.dumps({"design_system": "carbon"}, ensure_ascii=False),
        )
        second = VisualCraftLoop.ensure_design_contract(self.ws, "saas_web")
        self.assertEqual(second["design_system"]["id"], "carbon", "표준 변경 시 계약 캐시 무효화")


if __name__ == "__main__":
    unittest.main()
