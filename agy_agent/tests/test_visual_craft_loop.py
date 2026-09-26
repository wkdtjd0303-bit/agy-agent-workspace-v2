#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for visual_craft_loop.py — !mode 3/3+ 무인 자율 시각 완성도 폐루프.

Tests:
  1. 디자인 토큰 계약(reports/gate2/design_contract.json) 물리 고정 및 캐싱
  2. 정적 감사 규칙 (MARKETING_HERO / SMALL_TOUCH_TARGET / SPACING_OFF_GRID / TYPOGRAPHY_DRIFT / PALETTE_DRIFT / NESTED_CARDS)
  3. Android UI Hierarchy 기하 감사 (터치 타깃/썸존)
  4. Vision 판정 파서 ([VISUAL_VERDICT]/[VISUAL_DEFECTS] 구조화 라인)
  5. 라운드 실행 → 증거 기록 → Gate 5 물리 반려 연동
  6. 동일 결함 반복 stalled 감지 및 승인 차단 정책
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

from visual_craft_loop import (  # noqa: E402
    VisualCraftLoop,
    build_design_contract,
    audit_source_code,
    audit_ui_hierarchy,
    parse_android_hierarchy,
    parse_vision_verdict,
    find_screenshots,
    craft_max_rounds,
)
from evidence_validator import EvidenceValidator  # noqa: E402


class TestVisualCraftLoop(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="test_visual_craft_")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    # ---------- helpers ----------
    def _write(self, rel: str, content: str) -> str:
        path = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _make_android_workspace(self):
        self._write("settings.gradle", "rootProject.name = 'demo'")
        self._write("app/build.gradle", "plugins { id 'com.android.application' }")
        self._write(
            "app/src/main/java/com/demo/HomeScreen.kt",
            """
package com.demo

import androidx.compose.runtime.Composable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*

@Composable
fun HomeScreen() {
    Column(Modifier.padding(15.dp)) {
        Text("문서를 열면, 바로 집중할 수 있게")
        Text("지금 시작하세요")
        Text("완벽한 문서 경험을 제공합니다")
        Card {
            Card {
                IconButton(modifier = Modifier.size(32.dp)) { }
            }
        }
    }
}
""",
        )

    # ---------- 1. Design Contract ----------
    def test_design_contract_written_and_cached(self):
        contract = VisualCraftLoop.ensure_design_contract(self.ws, "mobile_app", app_name="PDF 뷰어")
        path = os.path.join(self.ws, "reports", "gate2", "design_contract.json")
        self.assertTrue(os.path.isfile(path), "디자인 계약 JSON이 물리 생성되어야 함")

        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["spacing"]["grid_unit_dp"], 4)
        self.assertEqual(saved["touch_target"]["min_dp"], 48)
        self.assertEqual(saved["domain"], "mobile_app")

        # 캐싱: 파일을 수정하지 않고 동일 계약을 반환
        again = VisualCraftLoop.ensure_design_contract(self.ws, "mobile_app")
        self.assertEqual(again["version"], contract["version"])

    def test_contract_prompt_block_only_for_visual_domains(self):
        block = VisualCraftLoop.get_contract_prompt_block(self.ws, "saas_web")
        self.assertIn("시각 완성도 계약", block)
        self.assertIn("Thumb Zone", block)
        self.assertEqual(VisualCraftLoop.get_contract_prompt_block(self.ws, "cli_agent"), "")

    # ---------- 2. Static rules ----------
    def test_marketing_hero_and_small_touch_detected(self):
        self._make_android_workspace()
        contract = build_design_contract("mobile_app")
        findings = audit_source_code(self.ws, contract, "mobile_app")
        rules = {f.rule_id for f in findings}
        self.assertIn("MARKETING_HERO", rules, "히어로/마케팅 카피 구조가 적발되어야 함")
        self.assertIn("SMALL_TOUCH_TARGET", rules, "32dp 터치 타깃이 적발되어야 함")

        hero = next(f for f in findings if f.rule_id == "MARKETING_HERO")
        self.assertEqual(hero.severity, "MAJOR")
        self.assertIn("HomeScreen.kt", hero.file)

    def test_spacing_off_grid_and_palette_drift(self):
        self._write(
            "src/App.css",
            """
.hero { padding: 15px; margin: 13px; }
.card { padding: 17px; margin: 11px; }
.tile { padding: 19px; margin: 21px; gap: 7px; }
.btn { background: #ff00aa; color: #112233; }
.other { background: #ff01aa; }
.third { background: #ff02aa; }
.fourth { background: #ff03aa; }
.fifth { background: #ff04aa; }
.sixth { background: #ff05aa; }
.seventh { background: #ff06aa; }
.eighth { background: #ff07aa; }
.ninth { background: #ff08aa; }
.tenth { background: #ff09aa; }
.eleventh { background: #ff0aaa; }
.twelfth { background: #ff0baa; }
.thirteenth { background: #ff0caa; }
.fourteenth { background: #ff0daa; }
""",
        )
        findings = audit_source_code(self.ws, build_design_contract("saas_web"), "saas_web")
        rules = {f.rule_id: f for f in findings}
        self.assertIn("SPACING_OFF_GRID", rules)
        self.assertIn("PALETTE_DRIFT", rules)
        self.assertIn(rules["PALETTE_DRIFT"].severity, ("MINOR", "MAJOR"))

    def test_clean_source_has_no_blocking_findings(self):
        self._write(
            "src/Clean.tsx",
            """
export const Clean = () => (
  <div className="p-4 gap-4 flex flex-col">
    <h1 className="text-2xl">최근 문서</h1>
    <button className="min-h-[48px] px-4">열기</button>
  </div>
);
""",
        )
        findings = audit_source_code(self.ws, build_design_contract("saas_web"), "saas_web")
        blocking = [f for f in findings if f.severity in ("BLOCKER", "CRITICAL", "MAJOR")]
        self.assertEqual(blocking, [], f"클린 UI 소스는 차단 결함이 없어야 함: {blocking}")

    # ---------- 3. UI hierarchy geometry ----------
    def test_ui_hierarchy_geometry_touch_target(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false" text="" resource-id=""/>
  <node class="android.widget.ImageButton" bounds="[40,100][130,190]" clickable="true" text="" resource-id="btn_small"/>
  <node class="android.widget.Button" bounds="[40,2000][1040,2180]" clickable="true" text="열기" resource-id="btn_open"/>
</hierarchy>"""
        nodes = parse_android_hierarchy(xml)
        self.assertEqual(len(nodes), 3)
        findings, metrics = audit_ui_hierarchy(nodes, build_design_contract("mobile_app"), density=3.0)
        rules = {f.rule_id for f in findings}
        self.assertIn("SMALL_TOUCH_TARGET", rules, "30dp 클릭 위젯이 적발되어야 함")
        self.assertLess(metrics["min_touch_dp"], 48)
        self.assertEqual(metrics["clickable_count"], 2)

    # ---------- 4. Vision verdict parser ----------
    def test_parse_vision_verdict_structured(self):
        text = (
            "[VISUAL_VERDICT]: FAIL\n"
            "[VISUAL_SCORE]: 61\n"
            "[VISUAL_SUMMARY]: 히어로 카드가 첫 화면을 점유\n"
            "[VISUAL_DEFECTS]:\n"
            "- MAJOR | MARKETING_HERO | 상단 40% | 감성 카피가 화면 절반을 점유함 | 히어로 제거 후 목록 상단 배치\n"
            "- MINOR | SPACING | 하단 툴바 | 6px 간격으로 그리드 이탈 | 8/16px 정규화\n"
        )
        parsed = parse_vision_verdict(text)
        self.assertEqual(parsed["verdict"], "FAIL")
        self.assertEqual(parsed["score"], 61)
        self.assertEqual(len(parsed["defects"]), 2)
        self.assertEqual(parsed["defects"][0]["rule_id"], "MARKETING_HERO")
        self.assertEqual(parsed["defects"][0]["severity"], "MAJOR")

    def test_parse_vision_verdict_fallbacks(self):
        parsed = parse_vision_verdict("종합 점수: 92/100. 깔끔하고 여백이 우수함.")
        self.assertEqual(parsed["verdict"], "PASS")
        self.assertEqual(parsed["score"], 92)

        parsed_fail = parse_vision_verdict(
            "카드 중첩이 심하고 랜딩 페이지 구조이며 히어로 영역 때문에 여백 부족. 터치 타깃 미달."
        )
        self.assertEqual(parsed_fail["verdict"], "FAIL")

    # ---------- 5. Round + Gate 5 ----------
    def test_run_round_records_evidence_and_gate5_rejects(self):
        self._make_android_workspace()
        report = asyncio.run(VisualCraftLoop.run_round(
            self.ws, domain="mobile_app", round_index=1, use_vision=False
        ))
        self.assertEqual(report.verdict, "FAIL")
        self.assertTrue(report.blocking_findings)

        evidence_path = os.path.join(self.ws, "reports", "gate5", "visual_craft_review.json")
        self.assertTrue(os.path.isfile(evidence_path), "시각 감사 증거 JSON이 기록되어야 함")

        craft = EvidenceValidator.check_visual_craft_review(self.ws)
        self.assertTrue(craft["checked"])
        self.assertFalse(craft["passed"], "FAIL 감사 증거는 Gate 5 물리 반려로 이어져야 함")
        self.assertIn("시각 완성도", craft["summary"])

        # PASS 로 갱신되면 Gate 5 검사를 통과한다
        with open(evidence_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        payload["verdict"] = "PASS"
        payload["blocking_count"] = 0
        payload["failing_rule_ids"] = []
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        self.assertTrue(EvidenceValidator.check_visual_craft_review(self.ws)["passed"])

        # Vision 단독 FAIL(파싱된 MAJOR 결함 0건)이라도 Gate 5 는 반려해야 한다
        payload["verdict"] = "FAIL"
        payload["blocking_count"] = 0
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        self.assertFalse(EvidenceValidator.check_visual_craft_review(self.ws)["passed"])

    def test_absent_evidence_is_backward_compatible(self):
        craft = EvidenceValidator.check_visual_craft_review(self.ws)
        self.assertFalse(craft["checked"])
        self.assertTrue(craft["passed"], "감사 파일이 없는 레거시 프로젝트는 기존처럼 통과해야 함")

    # ---------- 6. Stalled policy ----------
    def test_stalled_detection_blocks_further_override(self):
        self._make_android_workspace()
        r1 = asyncio.run(VisualCraftLoop.run_round(self.ws, domain="mobile_app", round_index=1, use_vision=False))
        self.assertTrue(VisualCraftLoop.should_block_approval(r1))
        r2 = asyncio.run(VisualCraftLoop.run_round(self.ws, domain="mobile_app", round_index=2, use_vision=False))
        self.assertTrue(r2.stalled, "동일 결함 반복 시 stalled=True 여야 함")
        self.assertFalse(
            VisualCraftLoop.should_block_approval(r2),
            "stalled 상태에서는 무한 핑퐁 방지를 위해 자동 반려를 중단해야 함",
        )
        self.assertLessEqual(craft_max_rounds(), 3)

    def test_fix_directive_contains_rule_and_path(self):
        self._make_android_workspace()
        report = asyncio.run(VisualCraftLoop.run_round(self.ws, domain="mobile_app", round_index=1, use_vision=False))
        directive = VisualCraftLoop.build_fix_directive(report)
        self.assertIn("VISUAL-CRAFT-FIX", directive)
        self.assertIn("MARKETING_HERO", directive)
        self.assertIn("design_contract.json", directive)

    # ---------- 7. Web auto-capture guard ----------
    def test_discover_web_url_and_capture_guard(self):
        from visual_craft_loop import discover_web_url, capture_web_screenshot
        self.assertEqual(discover_web_url(self.ws), "")
        os.makedirs(os.path.join(self.ws, "reports", "gate5"), exist_ok=True)
        with open(os.path.join(self.ws, "reports", "gate5", "deploy_url.txt"), "w", encoding="utf-8") as f:
            f.write("https://example.web.app\n")
        self.assertEqual(discover_web_url(self.ws), "https://example.web.app")

        # 테스트 환경에서는 외부 브라우저/네트워크 캡처를 수행하지 않는다
        shot = asyncio.run(capture_web_screenshot(
            "https://example.web.app", os.path.join(self.ws, "auto_shot.png")
        ))
        self.assertIsNone(shot)

    # ---------- 8. Screenshot discovery ----------
    def test_find_screenshots_excludes_diff_cards_and_blanks(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow 미설치")
        shots_dir = os.path.join(self.ws, "reports", "gate5", "screenshots")
        os.makedirs(shots_dir, exist_ok=True)

        real = os.path.join(shots_dir, "android_full_01_idle.png")
        Image.new("RGB", (360, 720), (240, 240, 245)).save(real)
        # 텍스트/도형이 있는 실제 프레임처럼 보이게 일부 픽셀 변경
        img = Image.open(real)
        for x in range(20, 200):
            for y in range(80, 200):
                img.putpixel((x, y), (30, 30, 60))
        img.save(real)

        blank = os.path.join(shots_dir, "android_full_02_blank.png")
        Image.new("RGB", (360, 720), (0, 0, 0)).save(blank)

        diff_card = os.path.join(shots_dir, "00_diff_card.png")
        Image.new("RGB", (360, 720), (120, 120, 120)).save(diff_card)

        found = find_screenshots(self.ws)
        names = [os.path.basename(p) for p in found]
        self.assertIn("android_full_01_idle.png", names)
        self.assertNotIn("android_full_02_blank.png", names, "빈/검은 프레임은 제외되어야 함")
        self.assertNotIn("00_diff_card.png", names, "합성 diff 카드는 실 프레임이 아니므로 제외")


if __name__ == "__main__":
    unittest.main()
