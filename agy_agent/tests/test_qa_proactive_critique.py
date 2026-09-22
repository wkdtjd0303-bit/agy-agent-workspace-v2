#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_qa_proactive_critique.py

Unit tests verifying that QA agents operate as proactive domain-expert design critics
rather than passive rubber-stamping checklists.
Verifies:
1. QA_PERSONAS designer focus prompt enforces visual appeal, typography balance, and NanoBanana asset generation.
2. QA_PERSONAS across all roles mandate "이것이 최선인가? 더 좋은 선택은 없는가?".
3. TrioAgentRunner QA1 prompt incorporates the proactive design critique mandate.
4. TrioAgentRunner Extended QA prompt incorporates the proactive design critique mandate.
5. TrioAgentRunner Coder prompt instructs Coder to actively accept and implement QA's professional design/architecture directives.
6. EngineManager.build_qa_prompt correctly incorporates persona info and proactive critique instructions.
"""

import os
import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))


class TestQAProactiveCritique(unittest.TestCase):

    def test_designer_persona_focus_prompt_enhancements(self):
        from model_manager import QA_PERSONAS, GlobalModelManager
        designer = QA_PERSONAS.get("designer")
        self.assertIsNotNone(designer)
        self.assertIn("🎨", designer.get("icon", ""))
        self.assertIn("디자이너", designer.get("name", ""))

        prompt = designer.get("focus_prompt", "")
        # Must include visual attraction / attention
        self.assertIn("시각적 주목도", prompt)
        # Must include typography & font balance
        self.assertIn("타이포그래피", prompt)
        self.assertIn("폰트", prompt)
        # Must include NanoBanana asset proposal
        self.assertIn("나노바나나", prompt)
        # Must forbid passive rubber-stamping
        self.assertIn("단순 존재 승인 엄금", prompt)

    def test_all_personas_mandate_proactive_critique(self):
        from model_manager import QA_PERSONAS
        for p_id in ("general", "designer", "security", "chaos", "sre", "architect"):
            p_data = QA_PERSONAS.get(p_id)
            self.assertIsNotNone(p_data, f"Persona {p_id} must exist")
            prompt = p_data.get("focus_prompt", "")
            # Must mandate proactive critique / best alternative
            self.assertTrue(
                "최선의" in prompt or "대안" in prompt,
                f"Persona {p_id} must enforce best alternative / critique: {prompt}"
            )

    def test_trio_agent_runner_prompts_contain_proactive_critique(self):
        trio_file = WORKSPACE_ROOT / "trio_agent_runner.py"
        with open(trio_file, "r", encoding="utf-8") as f:
            content = f.read()

        # QA1 Prompt anti-rubber-stamping and proactive mandate
        self.assertIn("수동적 승인봇(Rubber-Stamping) 전면 금지", content)
        self.assertIn("이것이 최선인가? 더 좋은 선택(Better Alternative)은 없는가?", content)
        self.assertIn("나노바나나(#nano-banana-image / NanoBanana Hub)", content)
        self.assertIn("전문가 관점 심층 평가 & 차기 고도화 권고", content)

        # Coder feedback prompt acceptance guidance
        self.assertIn("QA 전문가 관점(디자인/아키텍처/보안) 지적사항 적극 수용", content)

    def test_all_six_personas_have_distinct_deep_critique_criteria(self):
        from model_manager import QA_PERSONAS
        # 1. General (기본값)
        gen = QA_PERSONAS.get("general", {}).get("focus_prompt", "")
        self.assertIn("종합 설계 가치", gen)
        self.assertIn("사용자 체감 가치", gen)
        self.assertIn("Rubber-Stamping 절대 금지", gen)

        # 2. Security
        sec = QA_PERSONAS.get("security", {}).get("focus_prompt", "")
        self.assertIn("공격자 관점", sec)
        self.assertIn("인젝션", sec)
        self.assertIn("경로 탈출", sec)
        self.assertIn("단순 동작 승인 엄금", sec)

        # 3. Chaos
        chaos = QA_PERSONAS.get("chaos", {}).get("focus_prompt", "")
        self.assertIn("극한 카오스 환경", chaos)
        self.assertIn("동시성 & 비동기 경합", chaos)
        self.assertIn("단순 동작 승인 엄금", chaos)

        # 4. SRE
        sre = QA_PERSONAS.get("sre", {}).get("focus_prompt", "")
        self.assertIn("2~3일 장기 가동", sre)
        self.assertIn("알고리즘 비효율", sre)
        self.assertIn("단순 동작 승인 엄금", sre)

        # 5. Architect
        arch = QA_PERSONAS.get("architect", {}).get("focus_prompt", "")
        self.assertIn("1년 뒤 유지보수성", arch)
        self.assertIn("단일 책임 원칙(SRP)", arch)
        self.assertIn("단순 동작 승인 엄금", arch)

    def test_trio_agent_runner_details_all_six_personas(self):
        trio_file = WORKSPACE_ROOT / "trio_agent_runner.py"
        with open(trio_file, "r", encoding="utf-8") as f:
            content = f.read()

        for persona_tag in (
            "기본값 (종합 품질 검증관 / General)",
            "디자이너 봇 (UI/UX Designer)",
            "보안 전문가 (Security & AppSec)",
            "카오스 테스터 (Chaos & Resilience)",
            "성능 & SRE 엔지니어 (Performance & SRE)",
            "수석 아키텍트 (Architect & PO Requirements)"
        ):
            self.assertIn(persona_tag, content, f"TrioAgentRunner must detail {persona_tag}")

    def test_engine_manager_build_qa_prompt_with_multiple_personas(self):
        from engine_manager import EngineManager
        # Designer
        p_des = EngineManager.build_qa_prompt("UI 검증", "/tmp", persona_id="designer")
        self.assertIn("UI/UX 디자이너", p_des)
        self.assertIn("나노바나나", p_des)

        # General
        p_gen = EngineManager.build_qa_prompt("기능 검증", "/tmp", persona_id="general")
        self.assertIn("종합 품질 검증관", p_gen)
        self.assertIn("종합 설계 가치", p_gen)

        # Security
        p_sec = EngineManager.build_qa_prompt("보안 점검", "/tmp", persona_id="security")
        self.assertIn("보안 전문가", p_sec)
        self.assertIn("공격자 관점", p_sec)

        # Architect
        p_arc = EngineManager.build_qa_prompt("구조 점검", "/tmp", persona_id="architect")
        self.assertIn("수석 아키텍트", p_arc)
        self.assertIn("1년 뒤 유지보수성", p_arc)


if __name__ == "__main__":
    unittest.main()
