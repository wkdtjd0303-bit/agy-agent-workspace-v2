#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_role_authority.py

역할 권한(Role Authority) SSOT 회귀 테스트.

검증 항목:
1. Coder 출력에서 QA/Auditor 판정 태그 '선언 줄'이 물리 제거되는지 (본문 인용은 보존).
2. Coder 판정 태그 선언 / 판정 라벨 / QA 리포트 헤더 → 역할 전도 탐지.
3. QA 판정 누락 + 개발 완료 선언 → 역할 전도 탐지.
4. 시스템 게이트 오버라이드가 판정 주체(시스템 무결성 가디언)를 명시하는지.
5. [핵심] QA/시스템 판정문이 Coder 채널(main_channel)로 직접 게시되지 않는지(소스 불변식).
"""

import os
import re
import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import role_authority as RA  # noqa: E402

_TRIO_SRC = WORKSPACE_ROOT / "trio_agent_runner.py"


class TestStripCoderVerdictTags(unittest.TestCase):
    def test_strips_declaration_lines_and_keeps_body(self):
        raw = (
            "[STATUS: REJECTED]\n"
            "[BLOCKING_DEFECTS: 2]\n"
            "NPE 수정 완료\n"
            "- `app/Main.kt`: null check 추가\n"
        )
        clean, removed = RA.strip_coder_verdict_tags(raw)
        self.assertNotIn("[STATUS: REJECTED]", clean)
        self.assertNotIn("[BLOCKING_DEFECTS: 2]", clean)
        self.assertIn("NPE 수정 완료", clean)
        self.assertEqual(removed, ["[STATUS: REJECTED]", "[BLOCKING_DEFECTS: 2]"])

    def test_preserves_inline_quotation(self):
        raw = "QA가 [STATUS: REJECTED]를 선언하여 결함을 수정했습니다."
        clean, removed = RA.strip_coder_verdict_tags(raw)
        self.assertEqual(removed, [])
        self.assertEqual(clean, raw)

    def test_preserves_content_after_tag_on_same_line(self):
        raw = "[STATUS: APPROVED] 구현 완료 보고드립니다."
        clean, removed = RA.strip_coder_verdict_tags(raw)
        self.assertEqual(removed, ["[STATUS: APPROVED]"])
        self.assertIn("구현 완료 보고드립니다", clean)
        self.assertNotIn("STATUS", clean)


class TestRoleInversionDetection(unittest.TestCase):
    def test_coder_verdict_tag_declaration_is_inversion(self):
        is_inv, reason = RA.detect_coder_role_inversion(
            "[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n승인합니다."
        )
        self.assertTrue(is_inv)
        self.assertIn("QA/감사관 판정 태그", reason)

    def test_coder_auditor_label_is_inversion(self):
        is_inv, _ = RA.detect_coder_role_inversion("최종 판정: [STATUS: AUDIT_PASSED]\n통과")
        self.assertTrue(is_inv)

    def test_coder_qa_report_header_is_inversion(self):
        is_inv, reason = RA.detect_coder_role_inversion("QA 검증 리포트\n결함 없음")
        self.assertTrue(is_inv)
        self.assertIn("QA 역할", reason)

    def test_coder_normal_report_is_not_inversion(self):
        is_inv, reason = RA.detect_coder_role_inversion(
            "요청하신 기능을 구현 완료했습니다.\n- `Main.kt`: 타이머 로직 추가"
        )
        self.assertFalse(is_inv)
        self.assertEqual(reason, "")

    def test_qa_missing_verdict_and_coding_is_inversion(self):
        is_inv, reason = RA.detect_qa_role_inversion("요청하신 신규 기능을 구현 완료했습니다.\n코드를 모두 작성했습니다.")
        self.assertTrue(is_inv)
        self.assertIn("QA 에이전트가 품질 검증 및 [STATUS: APPROVED/REJECTED] 판정 대신", reason)

    def test_qa_verdict_outputs_are_not_inversion(self):
        self.assertFalse(RA.detect_qa_role_inversion("[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n통과")[0])
        self.assertFalse(RA.detect_qa_role_inversion("[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 1]\nNPE")[0])


class TestSystemGateOverride(unittest.TestCase):
    def test_rejection_declares_authority_and_blocking_marker(self):
        msg = RA.format_system_gate_override(
            approved=False,
            title="🚨 **[시스템 게이트 자동 반려 — Anti-Mocking]**",
            blocking_defects=2,
            detail="표절 적발",
        )
        lines = msg.splitlines()
        self.assertEqual(lines[0], "[STATUS: REJECTED]")
        self.assertEqual(lines[1], "[BLOCKING_DEFECTS: 2]")
        self.assertIn("판정 주체: 시스템 무결성 가디언", lines[2])

    def test_approval_override_has_zero_blocking(self):
        msg = RA.format_system_gate_override(approved=True, title="수렴 승인")
        self.assertTrue(msg.startswith("[STATUS: APPROVED]"))
        self.assertIn("[BLOCKING_DEFECTS: 0]", msg)

    def test_auditor_status_override(self):
        msg = RA.format_system_gate_override(
            approved=False,
            title="감사 거부",
            verdict_line_override="[STATUS: AUDIT_FAILED] [ACTION: STOP]",
        )
        self.assertTrue(msg.startswith("[STATUS: AUDIT_FAILED] [ACTION: STOP]"))


class TestCoderChannelInvariant(unittest.TestCase):
    """[핵심] QA/시스템 판정문은 Coder 채널(main_channel)로 게시되면 안 된다."""

    _FORBIDDEN = (
        r"safe_send_discord_message\(\s*main_channel\s*,\s*latest_qa_output\s*\)",
        r"safe_send_discord_message\(\s*main_channel\s*,\s*current_ext_output\s*\)",
        r"safe_send_discord_message\(\s*main_channel\s*,\s*arbitration_text\s*\)",
    )

    def test_no_verdict_posted_on_coder_channel(self):
        src = _TRIO_SRC.read_text(encoding="utf-8", errors="ignore")
        for pat in self._FORBIDDEN:
            self.assertIsNone(re.search(pat, src), f"역할 전도 게시 패턴 적발: {pat}")

    def test_pipeline_uses_role_authority(self):
        src = _TRIO_SRC.read_text(encoding="utf-8", errors="ignore")
        self.assertGreaterEqual(src.count("role_authority.format_system_gate_override("), 5)
        self.assertIn("post_verdict_message(", src)
        self.assertIn("strip_coder_verdict_tags(coder_post_text)", src)
        self.assertIn("strip_coder_verdict_tags(latest_coder_output)", src)


class TestOtherRunnersRoleSeparation(unittest.TestCase):
    """Research/Consulting 러너의 구현자(Analyst/Architect)도 판정 태그를 선언하지 못한다."""

    def test_research_runner_sanitizes_analyst_output(self):
        src = (WORKSPACE_ROOT / "research_runner.py").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("import role_authority", src)
        self.assertIn("strip_coder_verdict_tags(latest_analyst_output)", src)

    def test_consulting_runner_sanitizes_architect_output(self):
        src = (WORKSPACE_ROOT / "consulting_runner.py").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("import role_authority", src)
        self.assertIn("strip_coder_verdict_tags(latest_architect_output)", src)

    def test_trio_uses_ssot_coder_correction_prompt(self):
        src = _TRIO_SRC.read_text(encoding="utf-8", errors="ignore")
        # SSOT 교정 프롬프트를 사용한다(직전 QA 판정을 인자로 전달하여 '판정 뒤집기 금지'를 명시).
        self.assertIn("role_authority.build_coder_role_correction_prompt(", src)


if __name__ == "__main__":
    unittest.main()
