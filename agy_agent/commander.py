import os
import sys
import json
import re
import time
import asyncio
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List

import config
from config import AGY_PATH, COMMANDER_MODEL
from mode_classifier import classify_mode, classify_task_type
from codebase_reality_inspector import CodebaseRealityInspector, CodebaseReality
from quality_state_manager import QualityStateManager

logger = logging.getLogger(__name__)


@dataclass
class CommanderDecision:
    mode: str                    # "single" or "pair"
    reason: str                  # Short human-readable reason
    briefing: str                # Tactical one-line briefing from Commander
    task_type: str               # "coding", "research", "question", "general"
    confidence: float            # 0.0 ~ 1.0
    source: str                  # "ai" or "heuristic"
    latency_ms: float            # Time taken in milliseconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Milestone5W1HPlan:
    version: str
    title: str
    domain: str
    why: str                      # 왜 (전략적 당위성 & 핵심 목적)
    what: List[str]               # 무엇을 (핵심 구현 스펙 & 세부 기능 목록)
    how: str                      # 어떻게 (아키텍처 설계 & 기술 구현 방법론)
    who_where: str                # 누가·어디서 (작업 주체 & 디렉토리/파일 타깃)
    when_acceptance: List[str]    # 완료기준 (인수 테스트 & 기계적 무결점 기준)
    visual_asset_spec: str = ""   # 시각 에셋 기획 (Rule 8 공식 승인 & 이미지 생성 가이드)
    visual_approved: bool = False # 시각 에셋 생성 공식 승인 여부
    directive_for_coder: str = "" # Coder에게 전달될 최종 훈령 프롬프트
    source: str = "heuristic"     # "ai" or "heuristic"
    quality_contracts: List[Dict[str, Any]] = field(default_factory=list) # 기계적 검증용 원자적 QC 계약 목록
    delta_analysis: str = ""      # [Closed-loop Delta] 직전 턴 대비 상태 격차 및 병목 분석
    thinking_process: str = ""    # [4-Stage Structured Thinking] 폐루프 제어 4단계 추론 과정

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_discord_embed(self, mode_label: str = "3", engine_disp: str = "Gemini 3.7 Flash") -> Any:
        try:
            import discord
        except ImportError:
            return self.to_dict()

        desc = f"💡 **기획 의도**: {self.why}" if self.why else "이번 단계의 핵심 목표와 기능을 개발합니다."
        embed = discord.Embed(
            title=f"🚀 **{self.title}** (마일스톤 {self.version})",
            description=desc,
            color=0xF1C40F
        )
        if self.thinking_process:
            embed.add_field(
                name="🧠 Commander 4단계 추론 (폐루프 제어)",
                value=self.thinking_process[:1000],
                inline=False
            )
        elif self.delta_analysis:
            embed.add_field(
                name="🔍 Delta 분석 (닫힌 루프 제어)",
                value=self.delta_analysis[:1000],
                inline=False
            )
        what_text = "\n".join(f"• {item}" for item in self.what[:5]) if self.what else "• 핵심 프로덕션 기능 구현"
        embed.add_field(
            name="📋 이번에 만들 것",
            value=what_text[:1000],
            inline=False
        )
        when_text = "\n".join(f"• {item}" for item in self.when_acceptance[:5]) if self.when_acceptance else "• 컴파일/빌드 성공 및 E2E 테스트 통과"
        embed.add_field(
            name="🎯 완료 기준",
            value=when_text[:1000],
            inline=False
        )
        if self.quality_contracts:
            qc_lines = [f"• `{qc.get('id')}` [{qc.get('category', '').upper()}]: {qc.get('description', '')}" for qc in self.quality_contracts[:4]]
            embed.add_field(
                name="📊 Quality Contract (정량 검증식)",
                value="\n".join(qc_lines)[:1000],
                inline=False
            )
        if self.visual_approved and self.visual_asset_spec and self.domain not in ("cli_agent", "daemon", "backend_api"):
            embed.add_field(
                name="🎨 디자인 & 에셋",
                value=self.visual_asset_spec[:1000],
                inline=False
            )
        embed.set_footer(text=f"!stop으로 중단 가능 | {engine_disp}")
        return embed

    def to_discord_message(self, mode_label: str = "3", engine_disp: str = "Gemini 3.7 Flash") -> str:
        """Formats the 5W1H plan as a clean, highly readable standard Discord text message."""
        header = f"👑 **[Commander 사령관 마일스톤 기획 — {self.version}: {self.title}]**"
        desc = f"💡 **기획 의도**: {self.why}" if self.why else "💡 **기획 의도**: 이번 단계의 핵심 목표와 기능을 개발합니다."
        what_items = [f"• {item}" for item in self.what] if self.what else ["• 핵심 프로덕션 기능 구현"]
        what_text = "\n".join(what_items[:7])
        when_items = [f"{item}" if item.startswith(("1.", "2.", "3.", "4.", "5.", "•")) else f"• {item}" for item in self.when_acceptance] if self.when_acceptance else ["• 컴파일/빌드 성공 및 무결점 검증"]
        when_text = "\n".join(when_items[:5])

        parts = [
            header,
            "",
            desc,
            "",
            "📋 **이번에 만들 것**",
            what_text,
            "",
            "🎯 **완료 기준**",
            when_text
        ]

        if self.thinking_process:
            parts.extend([
                "",
                "🧠 **Commander 4단계 추론 (폐루프 제어)**",
                self.thinking_process[:1000]
            ])
        elif self.delta_analysis:
            parts.extend([
                "",
                "🔍 **Delta 분석 (닫힌 루프 제어)**",
                self.delta_analysis[:1000]
            ])

        if self.quality_contracts:
            qc_items = [
                f"• `{qc.get('id')}` [{qc.get('category', '').upper()}]: {qc.get('description', '')} (규칙: {qc.get('verification_rule', '')})"
                for qc in self.quality_contracts[:5]
            ]
            parts.extend([
                "",
                "📊 **Quality Contract (정량 검증식)**",
                "\n".join(qc_items)
            ])

        if self.visual_approved and self.visual_asset_spec and self.domain not in ("cli_agent", "daemon", "backend_api"):
            parts.extend([
                "",
                "🎨 **디자인 & 에셋 지침**",
                self.visual_asset_spec
            ])

        parts.extend([
            "",
            f"*(🛑 `!stop`으로 언제든 중단 가능 | 지휘 엔진: {engine_disp})*"
        ])
        return "\n".join(parts)

    def build_directive_for_coder(self, macro_memory_text: str = "", user_prompt: str = "") -> str:
        what_str = "\n".join(f"  - {t}" for t in self.what) if self.what else "  - 핵심 비즈니스 로직 및 UI 구현"
        when_str = "\n".join(f"  - {t}" for t in self.when_acceptance) if self.when_acceptance else "  - 빌드 성공 및 런타임 에러 0건"
        memory_section = f"\n{macro_memory_text}\n" if macro_memory_text else ""

        qc_section = ""
        if self.quality_contracts:
            qc_lines = [
                f"  - [{qc.get('id')}] ({qc.get('category', '').upper()}/Lv.{qc.get('level', 1)}): {qc.get('description', '')}\n    -> 검증식/규칙: {qc.get('verification_rule', '')}"
                for qc in self.quality_contracts
            ]
            qc_section = (
                f"\n**[📊 Quality Contract (기계적 정량 품질 검증 계약)]**:\n"
                f"QA와 Auditor는 아래 항목을 코드로 검증하여 Pass/Fail을 집계합니다. 반드시 충족하십시오:\n"
                + "\n".join(qc_lines) + "\n"
            )

        directive = (
            f"🎯 [Commander 사령관 5W1H 개발 지침 — 마일스톤 {self.version}: {self.title}]\n\n"
            f"**[1. 왜 (Why - 구현 목적 & 전략 가치)]**:\n"
            f"{self.why}\n\n"
            f"**[2. 무엇을 (What - 집중 구현 스펙)]**:\n"
            f"{what_str}\n\n"
            f"**[3. 어떻게 (How - 아키텍처 & 기술 방법론)]**:\n"
            f"{self.how}\n\n"
            f"**[4. 누가·어디서 (Who & Where - 작업 주체 & 타깃 경로)]**:\n"
            f"{self.who_where}\n\n"
            f"**[5. 완료기준 (When & Acceptance - 기계적 인수 기준)]**:\n"
            f"{when_str}\n"
            f"{qc_section}"
            f"{memory_section}\n"
            f"**[사령관 3대 핵심 훈령 (Commander Directives)]:**\n"
            f"1. **컨텍스트 청정화 및 명령 순응**: 이전 회차의 기억과 잡음을 완전히 비운 청정 상태에서, 이번 5W1H 마일스톤 목표에만 100% 집중하여 구현하십시오.\n"
            f"2. **가시적 실체 구현**: 단순 주석이나 변수명 변경이 아닌, 실제 소스코드와 기능(UI/로직)을 온전히 구현하십시오.\n"
            f"3. **빌드/테스트 완결**: 구현 완료 후 반드시 빌드와 테스트를 실행하여 정상 작동을 검증하십시오.\n"
            f"4. **단일 원자성(Atomic Scope) 및 스코프 과밀 방지**: 마일스톤 범위를 벗어난 과도한 기능 추가(Scope Creep)를 엄격히 금지합니다. 이번 단계에 배정된 단일 핵심 기능에만 온전히 집중하십시오.\n"
            f"5. **내실과 깊이 우선 (Substance over Breadth - Zero-Stub/Zero-Mock)**: `// TODO`, 빈 핸들러, 더미 반환값(Mock) 등 껍데기 코드는 엄격히 금지됩니다. 겉만 번지르르한 10개의 부실한 기능보다, 단 하나의 기능이라도 엣지 케이스, 대용량 처리, 60fps 반응성까지 완벽히 방어된 고품질 코드를 완성하십시오."
        )

        v_str = (self.version or "v1.0").lower()
        is_v1_prototype = ("v1.0" in v_str or v_str in ("v1", "1", "v1.0"))

        if is_v1_prototype:
            directive += (
                "\n\n🎯 **[원형 구현 및 오버엔지니어링 차단 헌법 (Core Fidelity Mandate)]**:\n"
                "1. **최소 규칙 무결성 우선**: 사용자가 요청한 최소 핵심 규칙(피한다, 떨어진다, 부딪히면 끝난다)이 물리적으로 무결하게 작동하는 1회 플레이 가능한 프로토타입을 완성하십시오.\n"
                "2. **조기 최적화/오버엔지니어링 차단**: 복잡한 사운드, 화려한 이펙트, 부가 설정 메뉴 등 곁가지 기능을 철저히 배제하고, 핵심 엔티티 생성/이동/충돌 판정의 기본기 완결에 집중하십시오."
            )
        else:
            directive += (
                "\n\n🛡️ **[외과수술식 정밀 수정 헌법 (Surgical Edit Mandate — 통째 재작성 엄격 금지)]**:\n"
                "1. **초안 감수(Audit & Polish) 관점 준수**: 이전 마일스톤에서 정상 작동이 검증된 기존 코드 뼈대와 파일 구조를 절대 통째로 갈아엎거나 재작성(Scrap & Rewrite)하지 마십시오!\n"
                "2. **정밀 타격(Surgical Edit)**: 기존 정상 동작 코드를 100% 보존한 상태에서, 지정된 함수, 수식 계수(가중치/공식), 식별자(네이밍 리팩토링), 이벤트 훅(Hook)만 핀포인트로 개정(Amend)하십시오.\n"
                "3. **회귀 방어(Zero-Regression)**: 직전 마일스톤의 핵심 기능(이동, 충돌, 기본 루프)이 깨지는 순간 즉시 반려 및 롤백됩니다. 수정 전후 기능 무결성을 자체 검증하십시오."
            )

        if self.visual_approved and self.visual_asset_spec:
            directive += (
                f"\n\n🎨 **[시각 디자인 에셋 생성 및 적용 지침]**:\n"
                f"{self.visual_asset_spec}\n"
                f"• `generate_image` 도구를 반드시 실행하여 실제 에셋(로고/배너/아이콘)을 이미지 파일로 생성하고 소스코드에 연결하십시오.\n"
                f"• 단순 텍스트나 단색 박스로 때우지 말고 실제 고품질 이미지를 배치하여 상용 서비스 수준의 시각 완성도를 확보하십시오."
            )

        if self.domain in ("saas_web", "mobile_app", "game"):
            directive += (
                "\n\n💎 **[상용급 프로덕트 디자인 시스템 필수 기준]**:\n"
                "1. 일관된 컬러 팔레트: Primary, Secondary, Neutral(gray-50~900), Accent 색상을 명확히 정의하십시오. 칙칙한 단색/기본 UI 금지.\n"
                "2. 여백 & 반응형: 8px 그리드(8/16/24/32px) 규칙, 적절한 여백(Whitespace), 카드형 컴포넌트와 부드러운 그림자(box-shadow)를 적용하십시오.\n"
                "3. 타이포그래피 계층: 큰 제목(bold), 중간 소제목(semi-bold), 본문(regular), 캡션의 크기와 굵기를 뚜렷이 구분하십시오.\n"
                "4. 인터랙션 디테일: 버튼 호버/클릭 효과, 부드러운 전환(transition), 로딩 스피너, 에러/성공 토스트, 데이터가 없을 때의 빈 화면(Empty State)을 구현하십시오."
            )

        if user_prompt and not user_prompt.strip().startswith("[📍 Roadmap Ladder") and not user_prompt.strip().startswith("[Zero-Production-Diff]"):
            directive += f"\n\n**[사용자 원본 지시]:**\n{user_prompt[:400]}"

        return directive


COMMANDER_SYSTEM_PROMPT = """You are [👑 Commander], the Chief AI Routing Dispatcher for an advanced coding agent system.
Analyze the user's input prompt and determine whether it is a question/inquiry (Single mode) or an implementation/modification/testing task (Pair 3-agent trio collaboration).

[DECISION RULES]
1. Select "single" and task_type "question" or "general" if the prompt is:
   - A question, inquiry, confirmation, or explanation request (e.g. "원래는 테스트하려면 !qa라고 했었잖아. 그거 맞아?", "이거 어떻게 동작해?", "WebSocket이 뭐야?", "무엇이 개선되었어?")
   - Simple file inspection or status check (e.g. "상태 확인해줘", "폴더 구조 보여줘")
   - A greeting, conversational remark, or theoretical question with no file modification requested.

2. Select "pair" and task_type "coding" | "research" if the prompt is:
   - Creating, modifying, implementing, or building features, components, APIs, UI/UX designs, or configurations (e.g. "이거 수정해줘", "버튼 추가해줘", "버그 고쳐줘", "설정 바꿔줄래?")
   - Bug fixing, refactoring, writing tests, QA verification, or code edits.
   - In-depth research, comparison, or purchasing analysis.

Return ONLY a valid, raw JSON object (without markdown code blocks) in the following format:
{
  "mode": "single" | "pair",
  "reason": "한 줄 판정 근거 (예: 시스템 동작 원리에 대한 단순 질문 확인)",
  "task_type": "coding" | "research" | "question" | "general",
  "briefing": "사령관 관점의 1줄 출동 브리핑 (예: 👤 질문 답변 전용 모드 출동 | 시스템 구조 및 기능에 대해 명확히 답변합니다.)",
  "confidence": 0.95
}
"""

COMMANDER_PLANNING_SYSTEM_PROMPT = """You are [👑 Commander], the Chief Product & Architecture Officer for an autonomous multi-agent software engineering team.
You act directly as the user's executive proxy and technical director.
The user has commissioned an end-to-end software project. You must analyze the user's input prompt with deep contextual reasoning and formulate an authentic, tailored product definition, UX/technical architecture, and a concrete 3-phase milestone roadmap.

[STRICT PLANNING MANDATES]
1. ZERO CANNED BOILERPLATE: Tailor all names, descriptions, and tasks directly and specifically to the user's actual prompt. NEVER output generic copy-paste strings (such as "PulseDesk Web", "DocuFlow Mobile", "FastCLI Core", or generic CRUD).
2. APP NAMING & BRAND IDENTITY: Create an inspiring, creative, professional product brand name with a subtitle tailored to the user's concept (e.g. "머니플로우 (MoneyFlow) — 개인 스마트 가계부").
3. DOMAIN & TECH ARCHITECTURE:
   - For GUI apps (Web/Mobile): Define tailored design tokens, color palette, layout ergonomics, and 48dp touch targets.
   - For CLI / Daemons / Backend / Bots: Define terminal UI (Rich/ANSI, argument parser, progress spinner), RESTful OpenAPI schema, or daemon lifecycle. NEVER mention canvas viewports for non-canvas tools!
4. 3-PHASE MILESTONE ROADMAP (v1.0, v2.0, v3.0):
   - v1.0: Core Architecture & MVP Implementation (focus on core domain logic, fundamental data models, and primary user workflow)
   - v2.0: Deep Features, Interaction & Data Integrity (focus on advanced features, search/filtering, state persistence, error resilience, visual assets & polish)
   - v3.0: Production Hardening, Release & E2E Quality (focus on deployment/export, performance, zero crash, regression E2E tests)
5. CONCRETE TASKS: Under EACH milestone, provide 3 actionable, specific task descriptions that directly build the requested application.
6. INITIAL DIRECTIVE: A clear, inspiring, authoritative instruction to Coder and QA explaining the mission, key technical expectations, and quality standards for v1.0, acting as an executive technical leader representing the user's requirements.
7. VISUAL ASSET AUTHORIZATION & TAXONOMY (사령관 <@1539448922643234876> 시각 에셋 공식 승인 헌법):
   - You act as the user's executive proxy. Under Rule 8, Coder/QA cannot generate images without Commander (<@1539448922643234876>) or user explicit approval.
   - For GUI apps (Games, Mobile apps, SaaS Web apps): Proactively evaluate visual polish needs (e.g. game sprites, joystick, splash screen, brand emblem/badge, empty state hero, or UI icons).
   - When visual assets are beneficial: Explicitly authorize image generation in `visual_asset_spec`, `milestones`, and `initial_directive` using the official approval tag `<@1539448922643234876>`:
     "[🎨 사령관 <@1539448922643234876> 공식 시각 에셋 생성 승인: Type A (풀컷 씬) / Type B (모듈러 독립 파츠)]"
   - Specify Rule 8 taxonomy:
     ① Type A (Full-cut scene/illustration: splash, background, hero banner - 16:9 or 9:16)
     ② Type B (Modular isolated part: sprite, joystick, icon, badge, button, item - 1:1 single object on clean/transparent background)
   - Direct Coder to execute `generate_image`, save to project resources (`res/drawable/`, `assets/`, etc.), and bind in source code.
   - For Headless CLI / Daemon / Backend APIs: Set `visual_asset_spec` to note headless terminal ANSI/Rich or REST schema, with no image generation needed.
8. ATOMIC SINGLE-FEATURE MILESTONES & SUBSTANCE OVER BREADTH (단일 책임 마일스톤 & 내실 우선 원칙):
   - Each milestone must focus on ONE single coherent domain capability. Never pack multiple disparate complex systems (e.g. text search + annotation drawing + redaction + PDF export) into one milestone.
   - Strictly reject superficial feature sprawl and shallow stubs (`// TODO`, dummy mocks, superficial buttons). Prioritize rock-solid error handling, 60fps responsiveness, and zero memory leaks over an endless list of unpolished features.
   - Alternating Feature-Hardening Progression: New capabilities must be backed by deep validation and stability hardening.
9. 4-STAGE CLOSED-LOOP THINKING PROTOCOL (4단계 닫힌 루프 제어 사고 헌법):
   - Structure reasoning strictly based on closed-loop control engineering:
     [Step 1. Observation & Ground Truth]: Only physical evidence (disk files, exit codes, test passes) counts. Disregard self-congratulatory natural language claims.
     [Step 2. Delta & Blocker Analysis]: Calculate the mechanical gap between current codebase reality and the target MVP pipeline.
     [Step 3. Single-Action Scope Guard]: Strictly isolate scope to ONE missing atomic function. Ruthlessly prune premature optimization, fancy particles, or superficial extras.
     [Step 4. Verification Contract]: Define non-negotiable machine-verifiable exit criteria.

Return ONLY a valid, raw JSON object (without markdown code blocks, without ```json):
{
  "title": "Concise Project Title (2-4 words)",
  "app_naming": "Brand Name with Subtitle",
  "domain": "cli_agent" | "daemon" | "backend_api" | "mobile_app" | "saas_web" | "game",
  "platform": "Target tech stack / platform",
  "target_demographic": "Specific target audience and user persona",
  "core_experience": "Core user experience and workflow",
  "key_value_proposition": "Primary value proposition",
  "design_vibe": "Tailored visual theme or architecture specification",
  "monetization_strategy": "Realistic feasibility, monetization, or distribution strategy",
  "visual_asset_spec": "Specification of visual assets and Commander <@1539448922643234876> approval, or 'None required' for headless CLI/backend",
  "milestones": [
    {
      "version": "v1.0",
      "title": "Specific 1st Milestone Title",
      "dimension": "functionality",
      "tasks": [
        "Specific task 1",
        "Specific task 2",
        "Specific task 3"
      ],
      "target_quality": "Specific quality goal for v1.0"
    },
    {
      "version": "v2.0",
      "title": "Specific 2nd Milestone Title",
      "dimension": "ux_ergonomics",
      "tasks": [
        "Specific task 1",
        "Specific task 2",
        "Specific task 3"
      ],
      "target_quality": "Specific quality goal for v2.0"
    },
    {
      "version": "v3.0",
      "title": "Specific 3rd Milestone Title",
      "dimension": "stability_e2e",
      "tasks": [
        "Specific task 1",
        "Specific task 2",
        "Specific task 3"
      ],
      "target_quality": "Specific quality goal for v3.0"
    }
  ],
  "initial_directive": "Comprehensive, authoritative instruction for Coder & QA on v1.0 (including visual asset approval clause if applicable)"
}
"""

class ResolvingDict(dict):
    """
    A dict subclass that is also an awaitable, allowing seamless execution
    whether called synchronously (e.g. in unit tests) or awaited asynchronously (e.g. in TrioAgentRunner).
    """
    def __init__(self, *args, async_coro=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._async_coro = async_coro

    def __await__(self):
        async def _resolve():
            if self._async_coro:
                try:
                    res = await self._async_coro()
                    if isinstance(res, dict):
                        self.update(res)
                except Exception as e:
                    logger.debug(f"Async resolution task failed: {e}")
            return self
        return _resolve().__await__()


class Commander:
    """
    👑 Commander Agent:
    Intelligent AI dispatcher powered by Gemini 3.7 Flash (Middle).
    Analyzes user intent with high contextual reasoning and dispatches to Single or Pair (3-Agent Trio).
    """

    _stats: Dict[str, Any] = {
        "total_analyzed": 0,
        "single_count": 0,
        "pair_count": 0,
        "ai_count": 0,
        "heuristic_count": 0,
        "total_latency_ms": 0.0,
        "last_decision": None
    }

    @classmethod
    async def analyze_intent(
        cls,
        prompt: str,
        session_mode: str = "auto",
        workspace: Optional[str] = None
    ) -> CommanderDecision:
        """
        Main entry point to classify a user prompt.
        If session_mode is manually pinned ('pair' or 'single'), respects it immediately.
        In 'auto' mode, runs fast AI inference with Gemini 3.7 Flash (Low) and falls back safely to heuristics.
        """
        start_time = time.perf_counter()
        clean_prompt = prompt.strip()
        mode_normalized = (session_mode or "auto").lower().strip()

        # 1. Respect manually pinned modes
        if mode_normalized in ("read", "readonly", "read-only", "consult", "consulting", "plan", "planning", "읽기", "기획", "자문", "방향성"):
            return CommanderDecision(
                mode="read",
                reason="수동 고정 모드 (🔍 3중 읽기/기획 전용 아키텍처 자문)",
                briefing="🔍 3중 읽기/기획 전용 부대 출동 | 코드 수정 없이 코드베이스 분석 및 최적 방향성 모색",
                task_type="consulting",
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("3+", "3plus", "3플러스", "mode3_plus", "mode3+") or mode_normalized.replace(" ", "") in ("3+", "3plus", "3플러스", "mode3_plus", "mode3+"):
            task_type = classify_task_type(clean_prompt)
            try:
                import config
                ext_cfgs = config.get_extended_qa_configs()
                multi_cnt = len(ext_cfgs) + 3 if ext_cfgs else 3
                multi_label = f"{multi_cnt}중 다중" if ext_cfgs and len(ext_cfgs) > 1 else ("4중 쿼드" if ext_cfgs else "3중")
            except Exception:
                multi_label = "다중"
            return CommanderDecision(
                mode="mode3_plus",
                reason=f"수동 고정 모드 (🤖 {multi_label} 에이전트 무인 자율 순환 · !mode 3+)",
                briefing=f"🤖 {multi_label} 무인 자율 순환 부대 출동 (폐루프 자동 순환 · !mode 3+)",
                task_type=task_type,
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("3", "mode3", "자율3", "무인3", "3자율", "3차자율") or mode_normalized.replace(" ", "") in ("3", "mode3"):
            task_type = classify_task_type(clean_prompt)
            return CommanderDecision(
                mode="mode3",
                reason="수동 고정 모드 (🤖 3중 에이전트 무인 자율 순환 · !mode 3)",
                briefing="🤖 3중 무인 자율 순환 부대 출동 (폐루프 자동 순환 · !mode 3)",
                task_type=task_type,
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스") or mode_normalized.replace(" ", "") in ("pair+", "pair_plus", "pair_quad", "quad_pair", "2+", "2plus", "2플러스"):
            task_type = classify_task_type(clean_prompt)
            try:
                import config
                ext_cfgs = config.get_extended_qa_configs()
                multi_cnt = len(ext_cfgs) + 3 if ext_cfgs else 3
                multi_label = f"{multi_cnt}중 다중" if ext_cfgs and len(ext_cfgs) > 1 else ("4중 쿼드" if ext_cfgs else "3중")
            except Exception:
                multi_label = "다중"
            briefing = f"👥 {multi_label} 협업부대 출동 (사용자 주도 모드 · !mode 2+)"
            reason = f"수동 고정 모드 (👥 {multi_label} 에이전트 사용자 주도 협업 · !mode 2+)"
            if task_type == "research":
                briefing = f"👥 {multi_label} 심층 리서치부대 출동 (사용자 주도 모드 · !mode 2+)"
                reason = f"수동 고정 모드 (👥 {multi_label} 에이전트 심층 리서치 · !mode 2+)"
            return CommanderDecision(
                mode="pair_quad",
                reason=reason,
                briefing=briefing,
                task_type=task_type,
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("pair", "trio", "dual", "collab", "team", "3중", "페어", "협업", "2"):
            task_type = classify_task_type(clean_prompt)
            briefing = "👥 3중 협업부대 출동 (사용자 주도 모드 · !mode 2)"
            reason = "수동 고정 모드 (👥 3중 에이전트 사용자 주도 협업 · !mode 2)"
            if task_type == "research":
                briefing = "👥 3중 심층 리서치부대 출동 (사용자 주도 모드 · !mode 2)"
                reason = "수동 고정 모드 (👥 3중 에이전트 심층 리서치 · !mode 2)"
            return CommanderDecision(
                mode="pair",
                reason=reason,
                briefing=briefing,
                task_type=task_type,
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo") or mode_normalized.replace(" ", "") in ("single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo"):
            return CommanderDecision(
                mode="single_qa2",
                reason="수동 고정 모드 (🥷 단일 qa2 에이전트)",
                briefing="🥷 단일 qa2 에이전트 출동 (수동 고정 모드 · 1545711652987600937 전용)",
                task_type="qa_review",
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("single_plus", "single+", "1+", "1plus", "1플러스", "single_consultant", "consultant", "컨설턴트") or mode_normalized.replace(" ", "") in ("single+", "1+", "single_plus"):
            consultant_model = getattr(config, "CONSULTANT_MODEL", "openai/gpt-6-astra")
            return CommanderDecision(
                mode="single_plus",
                reason=f"수동 고정 모드 (👤 단일 컨설턴트 에이전트 · {consultant_model})",
                briefing=f"👤 단일 컨설턴트 에이전트 출동 (수동 고정 모드 · {consultant_model})",
                task_type="consulting",
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )
        elif mode_normalized in ("single", "solo", "단일", "싱글"):
            return CommanderDecision(
                mode="single",
                reason="수동 고정 모드 (👤 단일 에이전트)",
                briefing="👤 단일 에이전트 출동 (수동 고정 모드)",
                task_type="general",
                confidence=1.0,
                source="manual",
                latency_ms=0.1
            )

        # 2. Empty prompt check
        if not clean_prompt:
            return CommanderDecision(
                mode="single",
                reason="빈 프롬프트",
                briefing="👤 단일 에이전트 대기",
                task_type="general",
                confidence=1.0,
                source="heuristic",
                latency_ms=0.1
            )

        # 3. Fast-Path Heuristic Check (Token Leak Defense & Zero-Latency Routing)
        heur_mode, heur_reason = classify_mode(clean_prompt, "auto")
        heur_task = classify_task_type(clean_prompt)

        # Clear heuristic indicators save redundant LLM calls and prevent token quota leaks
        is_clear_indicator = (
            heur_task in ("question", "research", "consulting", "ops_marketing")
            or heur_mode == "pair"
            or "질문" in heur_reason
            or "경량" in heur_reason
            or any(kw in clean_prompt.lower() for kw in ("?", "어때", "뭐야", "원인", "해결", "누수", "수정", "개발", "구현", "테스트"))
        )

        decision = None
        if not is_clear_indicator:
            # 4. AI Commander Inference with Gemini 3.7 Flash only when prompt is ambiguous
            try:
                decision = await asyncio.wait_for(
                    cls._run_llm_inference(clean_prompt, workspace),
                    timeout=4.0  # Strict 4.0s timeout to never delay user experience
                )
            except asyncio.TimeoutError:
                logger.warning("Commander AI inference timed out (4.0s). Falling back to instant heuristic engine.")
            except Exception as e:
                logger.warning(f"Commander AI inference error: {e}. Falling back to instant heuristic engine.")

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        # Fallback / Fast-path Heuristic Decision
        if not decision:
            if heur_mode == "pair":
                if heur_task == "research":
                    briefing = "👥 3중 심층 리서치부대 출동 | Analyst ↔ FactChecker ↔ ChiefAuditor 리서치"
                else:
                    briefing = "👥 3중 협업부대 출동 | Coder ↔ QA ↔ Auditor 실시간 협업 개발"
            else:
                briefing = "👤 단일 에이전트 출동 | 단순 질의응답 및 경량 작업 즉시 처리"

            decision = CommanderDecision(
                mode=heur_mode,
                reason=heur_reason,
                briefing=briefing,
                task_type=heur_task,
                confidence=0.95 if is_clear_indicator else 0.85,
                source="heuristic",
                latency_ms=elapsed_ms
            )
        else:
            decision.latency_ms = elapsed_ms

        # 5. Update runtime stats
        cls._record_stats(decision)
        return decision

    @classmethod
    async def _run_llm_inference(
        cls,
        prompt: str,
        workspace: Optional[str] = None
    ) -> Optional[CommanderDecision]:
        """Runs Gemini 3.7 Flash via agy CLI non-interactively."""
        model_name = COMMANDER_MODEL or "gemini-3.7-flash-middle"
        # [🛡️ Token Protection Mandate - Solar Pro 4 Protocol] Force Gemini model; block all external models
        if not model_name.lower().startswith("gemini-") or any(k in model_name.lower() for k in ("claude", "sonnet", "opus", "gpt-oss", "qwen", "deepseek", "solar", "llama", "openrouter")):
            logger.warning(f"Non-Gemini model '{model_name}' blocked in commander; enforcing 'gemini-3.7-flash-middle'")
            model_name = "gemini-3.7-flash-middle"
        cwd = workspace or os.getcwd()
        clamped_prompt = (prompt[:8000] + "...") if len(prompt) > 8000 else prompt

        cmd = [
            AGY_PATH,
            "--model", model_name,
            "--dangerously-skip-permissions",
            "--print",
            f"{COMMANDER_SYSTEM_PROMPT}\n\n[USER INPUT PROMPT TO CLASSIFY]:\n{clamped_prompt}"
        ]

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()
            raw_output = stdout.decode("utf-8", errors="replace").strip()

            if not raw_output:
                return None

            return cls._parse_json_decision(raw_output)
        except asyncio.CancelledError:
            if process:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            raise
        except Exception as e:
            logger.debug(f"Commander LLM inference error: {e}")
            return None
        finally:
            if process and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass

    @classmethod
    def _parse_json_decision(cls, raw_output: str) -> Optional[CommanderDecision]:
        """Safely extracts JSON object from LLM response."""
        try:
            # Strip markdown fences if present
            cleaned = raw_output
            if "```json" in cleaned:
                match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
                if match:
                    cleaned = match.group(1).strip()
            elif "```" in cleaned:
                match = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                if match:
                    cleaned = match.group(1).strip()

            # Find JSON block boundaries
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                cleaned = cleaned[start_idx:end_idx + 1]

            data = json.loads(cleaned)
            if not isinstance(data, dict):
                return None

            mode_raw = str(data.get("mode", "single")).lower().strip()
            mode = "pair" if mode_raw in ("pair", "trio", "collab", "team") else "single"
            reason = str(data.get("reason", "Commander 의도 분석 판정"))
            task_type = str(data.get("task_type", "general")).lower().strip()
            briefing = str(data.get("briefing", ""))
            
            # 🚨 Critical-1: Strict normalization and clamping in [0.0, 1.0]
            raw_conf = data.get("confidence", 0.95)
            try:
                conf_val = float(raw_conf)
                confidence = max(0.0, min(1.0, conf_val))
            except (ValueError, TypeError):
                confidence = 0.95

            if not briefing:
                if mode == "pair":
                    briefing = "👥 3중 협업부대 출동 | Coder ↔ QA ↔ Auditor 실시간 협업"
                else:
                    briefing = "👤 단일 에이전트 출동 | 단순 질의응답 및 경량 답변"

            return CommanderDecision(
                mode=mode,
                reason=reason,
                briefing=briefing,
                task_type=task_type,
                confidence=confidence,
                source="ai",
                latency_ms=0.0
            )
        except Exception as e:
            logger.debug(f"Failed to parse Commander JSON response: {e}. Raw: {raw_output[:100]}")
            return None

    @classmethod
    def reset_stats(cls):
        """Resets statistics for unit test isolation."""
        cls._stats = {
            "total_analyzed": 0,
            "single_count": 0,
            "pair_count": 0,
            "ai_count": 0,
            "heuristic_count": 0,
            "total_latency_ms": 0.0,
            "last_decision": None
        }

    @classmethod
    def _record_stats(cls, decision: CommanderDecision):
        """Updates internal statistics."""
        cls._stats["total_analyzed"] += 1
        if decision.mode == "pair":
            cls._stats["pair_count"] += 1
        else:
            cls._stats["single_count"] += 1

        if decision.source == "ai":
            cls._stats["ai_count"] += 1
        elif decision.source == "heuristic":
            cls._stats["heuristic_count"] += 1

        cls._stats["total_latency_ms"] += decision.latency_ms
        cls._stats["last_decision"] = decision.to_dict()

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Returns formatted statistics dictionary."""
        total = cls._stats["total_analyzed"]
        avg_latency = (cls._stats["total_latency_ms"] / total) if total > 0 else 0.0
        return {
            "commander_model": COMMANDER_MODEL or "gemini-3.7-flash-low",
            "total_analyzed": total,
            "single_count": cls._stats["single_count"],
            "pair_count": cls._stats["pair_count"],
            "ai_count": cls._stats["ai_count"],
            "heuristic_count": cls._stats["heuristic_count"],
            "avg_latency_ms": round(avg_latency, 1),
            "last_decision": cls._stats["last_decision"]
        }

    @classmethod
    def calculate_health_score(
        cls,
        prd_coverage: Optional[Dict[str, Any]],
        viewport_tests: List[str],
        horizontal_overflow: bool,
        broken_images_count: int,
        interactions: List[str],
        console_logs: List[str],
        network_errors: Optional[List[str]] = None,
        test_cases: Optional[List[Any]] = None,
        dom_insights: Optional[Dict[str, Any]] = None
    ) -> Tuple[int, Dict[str, Any]]:
        """
        100-Point Scientific E2E & PRD Quality Score Matrix:
        1. PRD Requirements Completion: 30 pts (completion_ratio * 30)
        2. E2E Scenario TC Pass Rate: 25 pts (pass_ratio * 25)
        3. Runtime Stability & Console/Network Integrity: 20 pts (Console Clean: 12, Network Clean: 8)
        4. Mobile Responsive & UX Web Standards: 15 pts (No Overflow: 6, No Broken Img: 5, 48dp Touch Target: 4)
        5. Data Persistence & Chaos Resilience: 10 pts (Persistence: 5, Chaos/Jitter: 5)
        """
        network_errors = network_errors or []
        test_cases = test_cases or []
        dom_insights = dom_insights or {}

        # 1. PRD Score (Max 30)
        if prd_coverage and prd_coverage.get("has_prd"):
            comp_ratio = float(prd_coverage.get("completion_ratio", 0.0))
            prd_score = int(round(comp_ratio * 30))
        else:
            prd_score = 0

        # Determine if genuine dynamic E2E execution was performed
        real_interactions = [
            act for act in interactions
            if not any(act.startswith(prefix) for prefix in [
                "소스 파일 감지", "정적 파일 탐색", "프로젝트 타입", "워크스페이스"
            ])
        ]
        has_dynamic_viewport = any(
            any(k in vp.lower() for k in ["px", "android", "emulator", "simulator", "mobile", "desktop", "windows", "iphone", "ipad"])
            for vp in viewport_tests
            if "정적" not in vp
        )
        is_dynamic_tested = bool(test_cases or real_interactions or has_dynamic_viewport)

        # 2. E2E TC Score (Max 25)
        tc_deductions = []
        pass_cnt = 0
        warn_cnt = 0
        fail_cnt = 0
        total_tc = len(test_cases) if test_cases else 0
        if test_cases:
            for tc in test_cases:
                st = getattr(tc, "status", None) or (tc.get("status") if isinstance(tc, dict) else "PASS")
                title = getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else "TC")
                details = getattr(tc, "details", None) or (tc.get("details") if isinstance(tc, dict) else "")
                details_str = str(details) if details is not None else ""
                if st == "PASS":
                    pass_cnt += 1
                elif st == "WARN":
                    warn_cnt += 1
                    tc_deductions.append(f"⚠️ 부분 통과(WARN): {title} ({details_str[:60]})")
                else:
                    fail_cnt += 1
                    tc_deductions.append(f"❌ 실패(FAIL): {title} ({details_str[:60]})")
            tc_ratio = (pass_cnt + 0.5 * warn_cnt) / max(1, total_tc)
            e2e_score = int(round(tc_ratio * 25))
        else:
            e2e_score = min(15, len(real_interactions) * 3) if real_interactions else 0

        # 3. Stability & Console/Network (Max 20: Console 12, Network 8)
        if not is_dynamic_tested:
            build_passed = any("빌드 성공" in log or "assembledebug 통과" in log.lower() for log in console_logs)
            stability_score = 5 if build_passed else 0
            console_err_count = 0
            net_err_count = 0
        else:
            console_err_count = sum(1 for log in console_logs if "[ERROR]" in log or "[PAGE_CRASH_ERROR]" in log)
            console_warn_count = sum(1 for log in console_logs if "[WARNING]" in log or "[WARN]" in log)
            console_score = max(0, 12 - (console_err_count * 4) - (console_warn_count * 1))
            
            net_err_count = len(network_errors)
            net_score = max(0, 8 - (net_err_count * 3))
            stability_score = console_score + net_score

        # 4. Mobile Responsive & UX (Max 15: Overflow 6, Broken Img 5, Touch Target 4)
        if not is_dynamic_tested:
            ux_score = 0
        else:
            overflow_score = 0 if horizontal_overflow else 6
            broken_img_score = max(0, 5 - (broken_images_count * 2))
            
            small_targets = dom_insights.get("smallTouchTargets", 0) if isinstance(dom_insights, dict) else 0
            total_buttons = dom_insights.get("totalButtons", 0) if isinstance(dom_insights, dict) else 0
            if total_buttons > 0:
                touch_penalty = min(4, int(round((small_targets / total_buttons) * 4)))
                touch_score = max(0, 4 - touch_penalty)
            else:
                touch_score = 2
            ux_score = overflow_score + broken_img_score + touch_score

        # 5. Persistence & Chaos Resilience (Max 10: Persistence 5, Chaos 5)
        has_reload = any("새로고침" in act or "reload" in act.lower() or "영속성" in act or "persistence" in act.lower() for act in interactions)
        if not has_reload and test_cases:
            has_reload = any(
                "영속성" in str(getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else ""))
                or "persistence" in str(getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else "")).lower()
                or "상태 저장" in str(getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else ""))
                for tc in test_cases
            )

        has_chaos = any("카오스" in act or "chaos" in act.lower() or "연타" in act or "지터" in act or "복원력" in act or "resilience" in act.lower() for act in interactions)
        if not has_chaos and test_cases:
            has_chaos = any(
                "카오스" in str(getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else ""))
                or "chaos" in str(getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else "")).lower()
                or "스트레스" in str(getattr(tc, "title", None) or (tc.get("title") if isinstance(tc, dict) else ""))
                for tc in test_cases
            )

        persistence_score = 5 if has_reload else 0
        chaos_score = 5 if has_chaos else 0
        resilience_score = persistence_score + chaos_score

        total_score = min(100, max(10, prd_score + e2e_score + stability_score + ux_score + resilience_score))

        breakdown = {
            "total": total_score,
            "prd": {"score": prd_score, "max": 30},
            "e2e_tc": {
                "score": e2e_score,
                "max": 25,
                "total_count": total_tc,
                "pass_count": pass_cnt,
                "warn_count": warn_cnt,
                "fail_count": fail_cnt,
                "deductions": tc_deductions
            },
            "stability": {"score": stability_score, "max": 20, "console_errors": console_err_count, "net_errors": net_err_count},
            "ux": {"score": ux_score, "max": 15, "overflow": horizontal_overflow, "broken_images": broken_images_count},
            "resilience": {"score": resilience_score, "max": 10, "persistence": persistence_score, "chaos": chaos_score},
            "summary_text": f"PRD: {prd_score}/30 | TC: {e2e_score}/25 | 안정성: {stability_score}/20 | 모바일UX: {ux_score}/15 | 복원력: {resilience_score}/10"
        }
        return total_score, breakdown

    @classmethod
    async def plan_e2e_operation(
        cls,
        target_url: str,
        mode: str,
        viewport_tests: List[str],
        horizontal_overflow: bool,
        broken_images_count: int,
        interactions: List[str],
        console_logs: List[str],
        cycle: int,
        max_cycles: int,
        workspace: Optional[str] = None,
        network_errors: Optional[List[str]] = None,
        test_cases: Optional[List[Any]] = None,
        dom_insights: Optional[Dict[str, Any]] = None,
        completed_targets: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        👑 Commander Autonomous E2E Strategic Planning:
        Analyzes live crawl diagnostics, E2E quality scoring matrix, and PRD feature coverage
        with Gemini 3.7 Flash (Low) and formulates razor-sharp, actionable tactical directives.
        """
        # 1. Extract PRD Feature Coverage & Gap Analysis
        prd_coverage = None
        if workspace and os.path.isdir(workspace):
            try:
                from evidence_validator import EvidenceValidator
                prd_coverage = EvidenceValidator.evaluate_prd_feature_coverage(workspace)
            except Exception as e:
                logger.debug(f"EvidenceValidator PRD coverage check notice: {e}")

        prd_summary_text = "PRD.md 미발견 (기본 뷰포트 탐색)"
        next_target_info = None
        if prd_coverage and prd_coverage.get("has_prd"):
            req_list = prd_coverage.get("requirements", [])
            comp_ratio = prd_coverage.get("completion_ratio", 0.0) * 100
            pending = [r['id'] for r in req_list if r['status'] == 'PENDING']
            next_target_info = prd_coverage.get("next_recommended_target")
            prd_summary_text = (
                f"PRD 달성률 {comp_ratio:.1f}% ({prd_coverage.get('completed_count')}/{prd_coverage.get('total_requirements')}개 완료) | "
                f"미완성 항목: {', '.join(pending) if pending else '전체 완료'}"
            )

        # 1.5. Extract Roadmap Ladder Milestone
        cur_roadmap_ms = None
        roadmap_summary_text = ""
        if workspace and os.path.isdir(workspace):
            try:
                from roadmap_manager import RoadmapManager
                rm = RoadmapManager(workspace)
                if rm.load() or rm.ensure_exists():
                    cur_roadmap_ms = rm.get_current_milestone(auto_expand=True)
                    roadmap_summary_text = rm.get_progress_summary()
            except Exception as e:
                logger.debug(f"Commander Roadmap extraction notice: {e}")

        # 2. Compute 100-point Quantitative Health Score Matrix
        calc_score, score_breakdown = cls.calculate_health_score(
            prd_coverage=prd_coverage,
            viewport_tests=viewport_tests,
            horizontal_overflow=horizontal_overflow,
            broken_images_count=broken_images_count,
            interactions=interactions,
            console_logs=console_logs,
            network_errors=network_errors,
            test_cases=test_cases,
            dom_insights=dom_insights
        )

        roadmap_block = (
            f"[ROADMAP LADDER ACTIVE MILESTONE]:\n"
            f"- Current Milestone: {cur_roadmap_ms.version} — {cur_roadmap_ms.title}\n"
            f"- Remaining Subtasks: {', '.join(t.text for t in cur_roadmap_ms.tasks if not t.done) if cur_roadmap_ms.tasks else '핵심 기능 개발'}\n"
            f"- Ladder Status: {roadmap_summary_text}\n\n"
        ) if cur_roadmap_ms else ""

        completed_block = (
            f"[COMPLETED TARGETS — DO NOT REPEAT]:\n"
            f"- The following priority targets were ALREADY COMPLETED in previous cycles: {', '.join(completed_targets)}\n"
            f"- CRITICAL: You MUST choose a NEW, DIFFERENT priority target from pending PRD/Roadmap items!\n\n"
        ) if completed_targets else ""

        prompt = (
            f"You are [👑 Commander], the Chief AI Operations Dispatcher.\n"
            f"Review the autonomous E2E inspection results for live deployment: {target_url}\n"
            f"Operation Phase: {mode} (Cycle {cycle}/{max_cycles})\n\n"
            f"{roadmap_block}"
            f"{completed_block}"
            f"[PRD & FEATURE GAP MATRIX]:\n"
            f"- Status: {prd_summary_text}\n"
            f"- Next Recommended Target: {json.dumps(next_target_info, ensure_ascii=False) if next_target_info else 'General Optimization'}\n\n"
            f"[QUANTITATIVE E2E QUALITY SCORE MATRIX]:\n"
            f"- Scientific Health Score: {calc_score}/100\n"
            f"- Breakdown: {score_breakdown['summary_text']}\n\n"
            f"[DIAGNOSTIC DATA]:\n"
            f"- Viewports tested: {', '.join(viewport_tests)}\n"
            f"- Horizontal Overflow: {'YES (Bug detected)' if horizontal_overflow else 'NO (Normal)'}\n"
            f"- Broken Images: {broken_images_count}\n"
            f"- Interactions: {'; '.join(interactions) if interactions else 'Basic navigation'}\n"
            f"- Browser Console logs: {'; '.join(console_logs[:10]) if console_logs else 'Clean'}\n\n"
            f"CRITICAL INSTRUCTION: If horizontal overflow or critical defects are detected, prioritize fixing those defects as top priority. Otherwise, if there are pending PRD requirements or backlog items, prioritize building that specific feature instead of vague styling.\n"
            f"Formulate a concise tactical directive in JSON format:\n"
            f"{{\n"
            f'  "health_score": {calc_score},\n'
            f'  "assessment": "사령관 관점의 전반적인 진단 총평 (1~2줄)",\n'
            f'  "priority_target": "최우선 개선/개발 타격 목표 (구체적 요구사항 명시)",\n'
            f'  "coder_directive": "👨‍💻 Coder에게 내리는 구체적 코드/UI 구현 지시",\n'
            f'  "qa_directive": "🕵️ QA에게 내리는 검증 및 결함 확인 지시",\n'
            f'  "briefing": "디스코드에 공표할 사령관 1줄 작전 선언"\n'
            f"}}"
        )

        try:
            raw_res = await asyncio.wait_for(
                cls._run_raw_llm_query(prompt, workspace),
                timeout=90.0
            )
            if raw_res:
                cleaned = raw_res
                if "```json" in cleaned:
                    m = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                elif "```" in cleaned:
                    m = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                s_idx = cleaned.find("{")
                e_idx = cleaned.rfind("}")
                if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                    data = json.loads(cleaned[s_idx:e_idx+1])
                    fallback_p_target = f"[{cur_roadmap_ms.version}] {cur_roadmap_ms.title}" if cur_roadmap_ms else (f"[{next_target_info['target_id']}] {next_target_info['title']}" if next_target_info else "모바일 반응형 최적화 및 UX 피드백 강화")
                    return {
                        "health_score": calc_score,
                        "score_breakdown": score_breakdown,
                        "assessment": str(data.get("assessment", "라이브 렌더링 및 PRD 명세 탐색 완료")),
                        "priority_target": str(data.get("priority_target", fallback_p_target)),
                        "coder_directive": str(data.get("coder_directive", next_target_info['coder_action_plan'] if next_target_info else "모바일 레이아웃 터치 타겟 및 인터랙션 보강")),
                        "qa_directive": str(data.get("qa_directive", next_target_info['qa_acceptance_criteria'] if next_target_info else "모바일 뷰포트 및 콘솔 에러 전수 검증")),
                        "briefing": str(data.get("briefing", f"E2E 진단 점수 {calc_score}점 ({score_breakdown['summary_text']}) | 자율 최적화 작전을 개시합니다.")),
                        "prd_coverage": prd_coverage,
                        "roadmap_milestone": cur_roadmap_ms.version if cur_roadmap_ms else None,
                        "source": "ai"
                    }
        except Exception as e:
            logger.debug(f"Commander E2E strategic planning fallback: {e}")

        # Heuristic Strategic Fallback
        issues = []
        if horizontal_overflow:
            issues.append("모바일 가로 스크롤(Overflow) 결함")
        if broken_images_count > 0:
            issues.append(f"깨진 이미지 태그 {broken_images_count}개")
        if any("[ERROR]" in log for log in console_logs):
            issues.append("브라우저 콘솔 에러 발생")

        if issues:
            target_desc = ", ".join(issues)
            coder_dir = f"지적된 {target_desc}을(를) 코드/CSS에 즉각 패치하고 배포하십시오."
            qa_dir = "모바일/데스크톱 교차 검증 및 콘솔 에러 무결성을 전수 심사하십시오."
            # If Roadmap milestone exists, combine issues + milestone target instead of ignoring milestone
            if cur_roadmap_ms:
                rem_tasks = [t.text for t in cur_roadmap_ms.tasks if not t.done]
                tasks_str = (": " + ", ".join(rem_tasks[:2])) if rem_tasks else ""
                target_desc = f"[{cur_roadmap_ms.version}] {cur_roadmap_ms.title}{tasks_str} + 핫픽스({', '.join(issues)})"
                coder_dir = (
                    f"1단계: {', '.join(issues)}을(를) 먼저 핫픽스하고, "
                    f"2단계: 로드맵 마일스톤 [{cur_roadmap_ms.version} {cur_roadmap_ms.title}]의 핵심 비즈니스 로직을 실구현하십시오."
                )
                qa_dir = f"핫픽스 무결성 검증 후 [{cur_roadmap_ms.version} {cur_roadmap_ms.title}] 기능 완성도를 교차 심사하십시오."
        elif cur_roadmap_ms:
            rem_tasks = [t.text for t in cur_roadmap_ms.tasks if not t.done]
            tasks_str = (": " + ", ".join(rem_tasks[:2])) if rem_tasks else ""
            target_desc = f"[{cur_roadmap_ms.version}] {cur_roadmap_ms.title}{tasks_str}"
            coder_dir = f"로드맵 마일스톤 [{cur_roadmap_ms.version} {cur_roadmap_ms.title}]의 핵심 비즈니스 로직과 UI 컴포넌트를 소스코드에 실구현하십시오."
            qa_dir = f"[{cur_roadmap_ms.version} {cur_roadmap_ms.title}] 명세가 실제 소스코드와 테스트에서 온전히 동작하는지 전수 검증하십시오."
        elif next_target_info:
            target_desc = f"[{next_target_info['target_id']}] {next_target_info['title']}"
            coder_dir = next_target_info['coder_action_plan']
            qa_dir = next_target_info['qa_acceptance_criteria']
        else:
            ws_context_name = os.path.basename(workspace) if workspace else "프로젝트"
            readme_path = os.path.join(workspace, "README.md") if workspace else ""
            first_intent = ""
            if os.path.isfile(readme_path):
                try:
                    with open(readme_path, "r", encoding="utf-8", errors="ignore") as rf:
                        r_lines = [l.strip().lstrip("#-•* ") for l in rf.readlines() if l.strip() and not l.strip().startswith("```")]
                        first_intent = r_lines[0][:60] if r_lines else ""
                except Exception:
                    first_intent = ""

            if first_intent:
                target_desc = f"[{ws_context_name} 핵심 가치 구현] {first_intent}"
                coder_dir = f"프로젝트 명세 및 README에 명시된 [{first_intent}]의 핵심 비즈니스 로직과 UI 인터랙션을 실구현하고 배포하십시오."
                qa_dir = f"사용자 본래 프로젝트 의도({first_intent})에 95% 이상 부합하는지 기능 및 안정성을 전수 검증하십시오."
            else:
                target_desc = f"[{ws_context_name}] 사용자 핵심 기능 완결성 및 런타임 안정성 고도화"
                coder_dir = f"{ws_context_name}의 핵심 사용자 유저 플로우를 실구현하고 예외 방어(Null check, 크래시 0건)를 완비하십시오."
                qa_dir = "사용자 체감 가치 및 런타임 안정성이 95% 이상 확보되었는지 전수 심사하십시오."

        return {
            "health_score": calc_score,
            "score_breakdown": score_breakdown,
            "assessment": f"다각도 인터랙션 및 PRD 명세 진단 완료 ({prd_summary_text if prd_coverage else '치명적 결함 없음'})",
            "priority_target": target_desc,
            "coder_directive": coder_dir,
            "qa_directive": qa_dir,
            "briefing": f"E2E 진단 점수 {calc_score}점 ({score_breakdown['summary_text']}) | 차기 핵심 과제({target_desc}) 완수를 위해 3중 협업부대 출동을 명령합니다.",
            "prd_coverage": prd_coverage,
            "source": "heuristic"
        }

    @classmethod
    async def _run_raw_llm_query(cls, query_text: str, workspace: Optional[str] = None) -> Optional[str]:
        if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
            return None
        model_name = COMMANDER_MODEL or "gemini-3.7-flash-middle"
        # [🛡️ Token Protection Mandate - Solar Pro 4 Protocol] Force Gemini model; block all external models
        if not model_name.lower().startswith("gemini-") or any(k in model_name.lower() for k in ("claude", "sonnet", "opus", "gpt-oss", "qwen", "deepseek", "solar", "llama", "openrouter")):
            logger.warning(f"Non-Gemini model '{model_name}' blocked in commander raw query; enforcing 'gemini-3.7-flash-middle'")
            model_name = "gemini-3.7-flash-middle"
        cwd = workspace or os.getcwd()
        cmd = [
            AGY_PATH,
            "--model", model_name,
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
            "--print",
            query_text
        ]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            return stdout.decode("utf-8", errors="replace").strip()
        except asyncio.CancelledError:
            if process:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
            raise
        except Exception as e:
            logger.debug(f"Commander raw LLM query error: {e}")
            return None
        finally:
            if process and process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass

    # =========================================================================
    # Intelligent Multi-QA Squad Dispatching & Conflict Resolution (AGY 4.x)
    # =========================================================================

    @classmethod
    def evaluate_task_difficulty(
        cls,
        prompt: str,
        diff_info: Optional[Dict[str, Any]] = None
    ) -> Tuple[float, str, str, bool]:
        """
        Evaluates task difficulty on a 1~10 scale, determines risk level, and checks if debate is needed.
        Returns:
            (difficulty: float 1..10, risk_level: str "LOW"|"MEDIUM"|"HIGH", risk_reason: str, is_debate_needed: bool)
        """
        clean = (prompt or "").lower()
        changed_files_count = len(diff_info.get("changed_files", []) or []) if diff_info else 0
        insertions = (diff_info.get("insertions", 0) or 0) if diff_info else 0
        deletions = (diff_info.get("deletions", 0) or 0) if diff_info else 0
        total_lines = insertions + deletions

        # Debate detection
        debate_keywords = (
            "대안", "비교", "어느 쪽", "의견 대립", "충돌", "장단점", "트레이드오프",
            "tradeoff", "pros and cons", "vs", "versus", "어떤 방식", "설계 대립"
        )
        is_debate_needed = any(k in clean for k in debate_keywords)

        # High difficulty / Deep architecture indicators (7.5~10.0)
        deep_keywords = (
            "아키텍처", "리팩토링", "architecture", "refactor", "인프라", "보안", "security",
            "인증", "auth", "마이그레이션", "migration", "동시성", "concurrency", "thread",
            "데드락", "deadlock", "상태 머신", "state machine", "전면 개편", "파이프라인 개편",
            "대규모", "core", "엔진", "거버넌스", "중재", "마이크로서비스", "분산"
        )
        is_deep = any(k in clean for k in deep_keywords) or (changed_files_count >= 5) or (total_lines >= 250)

        # Low difficulty indicators (1.0~3.0)
        low_keywords = (
            "오타", "문구", "텍스트", "단어", "라벨", "스타일", "css", "color", "색상",
            "margin", "padding", "여백", "주석", "comment", "readme", "단순 확인", "문서"
        )
        is_low = any(k in clean for k in low_keywords) and (changed_files_count <= 2) and (total_lines < 40)

        if is_deep:
            if "전면" in clean or total_lines > 400:
                score = 10.0
            elif total_lines > 250 or changed_files_count >= 6:
                score = 9.0
            elif "아키텍처" in clean or "동시성" in clean or "분산" in clean:
                score = 8.5
            elif "리팩토링" in clean or "인프라" in clean or changed_files_count >= 4:
                score = 8.0
            else:
                score = 7.5
            score_disp = f"{score:g}"
            return score, "HIGH", f"🚨 [R3] 대규모 시스템/아키텍처/보안 핵심 작업 (난이도 {score_disp}/10, {changed_files_count}파일/{total_lines}줄)", is_debate_needed

        if is_low and not is_debate_needed:
            score = 1.0 if ("오타" in clean or total_lines < 10) else (2.0 if total_lines < 25 else 3.0)
            score_disp = f"{score:g}"
            return score, "LOW", f"⚡ [R1] 소규모 경량/문구/스타일 변경 (난이도 {score_disp}/10, {changed_files_count}파일/{total_lines}줄)", False

        # Medium difficulty (4.0~7.0)
        if total_lines > 120 or changed_files_count >= 3:
            score = 7.0
        elif total_lines > 60:
            score = 6.0
        elif "버그" in clean or "fix" in clean:
            score = 5.0
        else:
            score = 4.0
        score_disp = f"{score:g}"
        return score, "MEDIUM", f"🛡️ [R2] 표준 비즈니스 로직/기능 구현 (난이도 {score_disp}/10, {changed_files_count}파일/{total_lines}줄)", is_debate_needed

    @classmethod
    def is_force_all_requested(cls, prompt: str) -> bool:
        """
        Determines if the user explicitly and affirmatively requested all QAs to be dispatched.
        Safeguards against negative phrases such as:
        - "전체 qa 거쳐갈 필요가 없다"
        - "전체 qa 하지 마"
        - "모든 qa 다 돌릴 필요 없어"
        - "7개 모두 거쳐가지 않도록"
        And reports/logs quoting '사용자가 "전체 qa" 요청 시에만'
        """
        if not prompt:
            return False
        clean_p = prompt.lower()

        # Check negative modifiers - if any negative phrasing is present, it is NEVER force_all
        negative_keywords = (
            "필요 없", "거쳐갈 필요", "하지 마", "호출하지", "돌리지 마", "말고", "탈피",
            "방지", "금지", "제외", "선정해서", "줄여", "아닌", "안 해도", "않도록", "불필요",
            "경우에만", "요청 시에만", "요청한 경우"
        )
        if any(neg in clean_p for neg in negative_keywords):
            return False

        # Positive affirmative requests
        force_triggers = (
            "!all_qa", "!allqa", "!mode all_qa", "!mode allqa", "!mode all",
            "전체 qa", "전수 검증", "모든 qa", "7개 모두", "전원 검증", "전부 검증"
        )
        return any(pos in clean_p for pos in force_triggers)

    @classmethod
    def dispatch_qa_squad(
        cls,
        prompt: str,
        diff_info: Optional[Dict[str, Any]] = None,
        available_extended_qas: Optional[List[Dict[str, Any]]] = None,
        mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Commander-directed intelligent QA squad dispatching.
        Instead of executing all 7 QAs sequentially in every round, dispatches an optimal targeted subset.
        Enforces user constraints:
        - Base core squad: qa1 + qa2 + qa6 fixed whenever available.
        - qa7: ONLY if difficulty is >= 7.5/10 AND remaining quota >= 10%.
        - Dynamic selection: Commander (<@1540273467453345812>) selects 1~2 additional QAs matching task characteristics.
        """
        difficulty, risk_level, risk_reason, is_debate = cls.evaluate_task_difficulty(prompt, diff_info)

        if available_extended_qas is None:
            try:
                import config
                available_extended_qas = config.get_extended_qa_configs()
            except Exception:
                available_extended_qas = []

        # Check Claude Sonnet quota status
        sonnet_status = {"can_use": False, "remaining_pct": 0.0, "percentage_int": 0, "reason": "미조회"}
        try:
            from telemetry_status_manager import get_claude_sonnet_status
            sonnet_status = get_claude_sonnet_status()
        except Exception as e:
            logger.debug(f"Could not check Sonnet status: {e}")

        # Map available QAs by index
        qa_by_idx = {cfg["index"]: cfg for cfg in (available_extended_qas or []) if isinstance(cfg, dict) and "index" in cfg}

        # Check if user explicitly asked for all QAs affirmatively
        force_all = cls.is_force_all_requested(prompt)

        # Check if user specifically requested a targeted QA (e.g. QA2 Solar only)
        prompt_lower = (prompt or "").lower()
        has_qa2_only = False
        if ("qa2" in prompt_lower or "solar" in prompt_lower) and not any(neg in prompt_lower for neg in ("제외", "빼고", "말고")):
            if any(k in prompt_lower for k in ("의견만", "리뷰만", "검증만", "만 쓸", "만 사용할", "만 들을", "만 듣고", "만 참여", "만 실행")):
                has_qa2_only = True
            elif any(k in prompt_lower for k in ("qa2만", "solar만", "solar-pro만")):
                has_qa2_only = True
            elif re.search(r"(qa2|solar)[^\w\n]*(의\s*)?(의견|리뷰|검증)?\s*만", prompt_lower):
                has_qa2_only = True

        # Claude Sonnet 4.6 eligibility rule: Strictly difficulty >= 7.5, quota >= 10%, and not exhausted
        try:
            from trio_agent_runner import QuotaManager
            sonnet_exhausted = QuotaManager.is_model_exhausted("claude-sonnet-4-6")
        except Exception:
            sonnet_exhausted = False
        can_call_sonnet = (7 in qa_by_idx) and (difficulty >= 7.5) and sonnet_status.get("can_use", False) and not sonnet_exhausted

        selected_indices: List[int] = []
        strategy_desc = ""

        if has_qa2_only and 2 in qa_by_idx:
            # User specifically asked for QA2 only
            selected_indices = [2]
            strategy_desc = "사용자 자연어 지시: qa2 단독 집중 교차 검증"
        elif force_all or (mode in ("pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스", "3+", "3plus", "3플러스", "mode3_plus") and 7 not in qa_by_idx):
            # Explicit affirmative user request for all QAs or !mode 2+ (pair_quad) collaboration without Sonnet
            selected_indices = [cfg["index"] for cfg in available_extended_qas if cfg["index"] != 7]
            if 7 in qa_by_idx:
                if can_call_sonnet:
                    selected_indices.append(7)
                    strategy_desc = "사용자 명시 지시: 전체 등록된 확장 QA 전수 동시 출동 (qa7 투입)" if force_all else f"👥 !mode 2+ 다중 협업 모드: 활성화된 {len(selected_indices)}개 QA 전체 연계 (qa7 투입)"
                else:
                    sonnet_block_reason = "잔여 쿼터 10% 미만 보존" if not sonnet_status.get("can_use", False) else f"작업 난이도({difficulty}/10) 7.5 미만"
                    strategy_desc = f"사용자 명시 지시: 등록 OpenRouter QA 전원 출동 (qa7은 {sonnet_block_reason} 대기)" if force_all else f"👥 !mode 2+ 다중 협업 모드: 활성화된 {len(selected_indices)}개 QA 전체 연계 (qa7은 {sonnet_block_reason} 대기)"
            else:
                strategy_desc = "사용자 명시 지시: 전체 등록된 확장 QA 전수 동시 출동" if force_all else f"👥 !mode 2+ 다중 협업 모드: 활성화된 {len(selected_indices)}개 QA 전체 연계"
        elif len(available_extended_qas) <= 3:
            # Small pool (<= 3 QAs configured, e.g. lightweight setups or specific unit tests):
            # Dispatch all available non-Sonnet QAs, plus Sonnet if strictly eligible
            selected_indices = [idx for idx in qa_by_idx if idx != 7]
            if 7 in qa_by_idx and can_call_sonnet:
                selected_indices.append(7)
            strategy_desc = f"🎯 사령관 컴팩트 스쿼드 출동: 등록된 {len(selected_indices)}개 QA 전체 연계"
        else:
            # Standard pool (> 3 QAs): Intelligent squad dispatching
            # 1. Base Core Squad: qa2 + qa6 fixed whenever available
            base_indices: List[int] = [idx for idx in (2, 6) if idx in qa_by_idx]

            # 2. Dynamic Candidate Pool from remaining available QAs (e.g. QA3, QA4, QA5, QA7)
            dynamic_pool: List[int] = [idx for idx in qa_by_idx if idx not in base_indices]

            # Remove QA7 from dynamic pool if not eligible (< 7.5 difficulty or < 10% quota)
            if 7 in dynamic_pool and not can_call_sonnet:
                dynamic_pool.remove(7)

            dynamic_chosen: List[int] = []

            if is_debate:
                # Debate mode: pick complementary/contrasting pair (1~2 QAs)
                if can_call_sonnet and 7 in dynamic_pool:
                    dynamic_chosen.append(7)
                    for c in (4, 5, 3):
                        if c in dynamic_pool and c not in dynamic_chosen:
                            dynamic_chosen.append(c)
                            break
                    strategy_desc = "⚖️ 사령관 토론 중재 스쿼드: 기본(qa2/qa6) + 아키텍처 대립(qa7 ↔ qa4/qa5)"
                else:
                    for c in (4, 5, 3):
                        if c in dynamic_pool and len(dynamic_chosen) < 2:
                            dynamic_chosen.append(c)
                    strategy_desc = "⚖️ 사령관 토론 중재 스쿼드: 기본(qa2/qa6) + 심층 교차 검증(qa4 ↔ qa5)"
            elif difficulty >= 7.5:
                # Deep-Path (7.5 ~ 10): qa7 (if eligible) + 1 deep code reviewer (qa4 or qa5)
                if can_call_sonnet and 7 in dynamic_pool:
                    dynamic_chosen.append(7)
                    for c in (4, 5, 3):
                        if c in dynamic_pool and c not in dynamic_chosen:
                            dynamic_chosen.append(c)
                            break
                    strategy_desc = f"🏛️ 사령관 최고심급 스쿼드: 기본(qa2/qa6) + qa7({sonnet_status.get('percentage_int', 13)}% 쿼터 Claude Sonnet 4.6) + 심층 추론(qa4/qa5)"
                else:
                    for c in (4, 5, 3):
                        if c in dynamic_pool and len(dynamic_chosen) < 2:
                            dynamic_chosen.append(c)
                    sonnet_reason = "잔여 쿼터 10% 미만 보존" if not sonnet_status.get("can_use", False) else "난이도 7.5 미만"
                    strategy_desc = f"🏛️ 사령관 고난도 추론 스쿼드: 기본(qa2/qa6) + qa4/qa5 (qa7은 {sonnet_reason}으로 대기)"
            elif difficulty >= 4.0:
                # Normal-Path (4.0 ~ 7.4): Pick 1~2 balanced QAs (qa4 or qa5 or qa3)
                max_dynamic = 2
                for c in (4, 5, 3):
                    if c in dynamic_pool and len(dynamic_chosen) < max_dynamic:
                        dynamic_chosen.append(c)
                if len(dynamic_chosen) < max_dynamic:
                    for c in dynamic_pool:
                        if c not in dynamic_chosen and len(dynamic_chosen) < max_dynamic:
                            dynamic_chosen.append(c)
                strategy_desc = "🛡️ 사령관 균형 교차 스쿼드: 기본(qa2/qa6) + 정밀 코드/엣지케이스(qa4/qa5/qa3)"
            else:
                # Fast-Path (1.0 ~ 4.9): Pick 1 fast QA (qa3) if available
                if 3 in dynamic_pool:
                    dynamic_chosen.append(3)
                elif 4 in dynamic_pool:
                    dynamic_chosen.append(4)
                elif dynamic_pool:
                    dynamic_chosen.append(dynamic_pool[0])
                strategy_desc = "⚡ 사령관 쾌속 경량 스쿼드: 기본(qa2/qa6) + qa3 고속 검증"

            selected_indices = sorted(list(set(base_indices + dynamic_chosen)))
            if not selected_indices and available_extended_qas:
                selected_indices = [available_extended_qas[0]["index"]]

        # Sort selected QAs in pipeline order
        selected_qas = [qa_by_idx[i] for i in sorted(selected_indices) if i in qa_by_idx]
        skipped_qas = [cfg for cfg in (available_extended_qas or []) if isinstance(cfg, dict) and cfg.get("index") not in selected_indices]

        # Compose standby description
        if skipped_qas:
            standby_items = []
            for c in skipped_qas:
                q_idx = c.get("index")
                q_name = f"qa{q_idx}" if q_idx else str(c.get("name", "qa")).lower()
                if q_idx == 7:
                    if difficulty < 7.5:
                        standby_items.append(f"{q_name}(난이도 {difficulty}/10 < 7.5 보존)")
                    else:
                        standby_items.append(f"{q_name}(쿼터 10% 미만 보존)")
                else:
                    standby_items.append(f"{q_name}(토큰 최적화 대기)")
            standby_desc = ", ".join(standby_items)
        else:
            standby_desc = "없음 (전원 출동)"

        # Squad display banner for Discord
        squad_names_str = " ➔ ".join([f"**qa{c.get('index', '')}**" if c.get("index") else f"**{c.get('name', 'qa').lower()}**" for c in selected_qas]) or "없음"
        dispatch_banner = (
            f"🎯 **[사령관 <@1540273467453345812> 지능형 QA 스쿼드 편성]**\n"
            f"• **작업 난이도**: **{difficulty}/10** ({risk_reason})\n"
            f"• **출동 스쿼드**: **qa1** ➔ {squad_names_str}\n"
            f"• **기본 고정**: qa1 + qa2 + qa6\n"
            f"• **편성 전략**: {strategy_desc}\n"
            f"• **대기 QA**: {standby_desc}"
        )

        return {
            "difficulty": difficulty,
            "risk_level": risk_level,
            "risk_reason": risk_reason,
            "is_debate_needed": is_debate,
            "selected_qas": selected_qas,
            "selected_indices": [c["index"] for c in selected_qas if "index" in c],
            "skipped_qas": skipped_qas,
            "sonnet_status": sonnet_status,
            "strategy": strategy_desc,
            "strategy_desc": strategy_desc,
            "dispatch_banner": dispatch_banner
        }

    @classmethod
    def resolve_qa_conflict(
        cls,
        qa_results: Dict[str, Dict[str, Any]],
        diff_info: Optional[Dict[str, Any]] = None,
        prompt: str = "",
        primary_qa_approved: bool = True,
        round_num: int = 1,
        max_rounds: int = 20
    ) -> ResolvingDict:
        """
        Arbitrates conflicts and discrepancies between multiple QA reviewers (사공이 많으면 배가 산으로 가는 문제 해결).
        Resolves the A vs B dilemma by selecting a single, decisive technical directive.
        Returns ResolvingDict so it works seamlessly both synchronously and asynchronously.
        """
        rejections = {k: v for k, v in qa_results.items() if not v.get("approved", True)}
        approvals = {k: v for k, v in qa_results.items() if v.get("approved", True)}

        if not rejections:
            return ResolvingDict({
                "has_conflict": False,
                "is_approved": True,
                "arbitration_text": "",
                "verdict_summary": "✅ 모든 출동 QA 만장일치 승인 완료"
            })

        # Tier 1: Check if any rejection is an objective hallucination contradicted by physical git diff
        changed_files = (diff_info.get("changed_files", []) or []) if diff_info else []
        hallucinated_rejections = []
        for q_name, q_data in list(rejections.items()):
            text = q_data.get("output", "") or ""
            if ("변경 사항 없음" in text or "파일이 생성되지 않" in text or "코드가 작성되지 않" in text or "No changes" in text or "No files" in text) and len(changed_files) > 0:
                hallucinated_rejections.append(q_name)

        if hallucinated_rejections:
            for h_name in hallucinated_rejections:
                logger.info(f"Arbiter: Overriding hallucinated rejection by {h_name} with physical Git diff proof.")
                rejections.pop(h_name, None)
                approvals[h_name] = qa_results[h_name]

        # Tier 1.2: Transient infrastructure timeout or network failure override
        # A) Peer QA evaluated and approved (approvals), OR
        # B) On recurring rounds (round_num > 1), primary QA (Gemini Senior QA) approved and all remaining rejections are purely external infrastructure timeouts
        timeout_rejections = []
        is_all_timeout = bool(rejections) and all(
            any(kw in q_data.get("output", "") for kw in (
                "응답 시간 초과", "타임아웃", "180초", "TimeoutError", "API 호출 실패",
                "모델 호출 중 예외 발생", "인프라 장애", "API 호출 예외"
            ))
            for q_data in rejections.values()
        )
        should_override_timeouts = bool(approvals) or (primary_qa_approved and round_num > 1 and is_all_timeout)

        if should_override_timeouts:
            for q_name, q_data in list(rejections.items()):
                text = q_data.get("output", "")
                is_timeout = any(kw in text for kw in (
                    "응답 시간 초과", "타임아웃", "180초", "TimeoutError", "API 호출 실패",
                    "모델 호출 중 예외 발생", "인프라 장애", "API 호출 예외"
                ))
                if is_timeout:
                    timeout_rejections.append(q_name)

            if timeout_rejections:
                for t_name in timeout_rejections:
                    logger.info(f"Arbiter: Overriding transient timeout rejection by {t_name} (peer approved or recurring timeout on R{round_num}).")
                    rejections.pop(t_name, None)
                    approvals[t_name] = qa_results[t_name]

        if not rejections:
            override_reasons = []
            if hallucinated_rejections:
                override_reasons.append(f"물리적 디스크 검증: {len(changed_files)}개 파일 실제 변경 확인 (허위 누락 지적 기각)")
            if timeout_rejections:
                peer_label = f"출동 동료 QA({', '.join([k for k in approvals.keys() if k not in timeout_rejections])})" if any(k not in timeout_rejections for k in approvals.keys()) else "수석 QA1(Gemini)"
                override_reasons.append(f"인프라 가드: {', '.join(timeout_rejections)} 외부 LLM 응답 지연/타임아웃 발생했으나 {peer_label}이 실질 코드 정상 검증을 완료함")

            if hallucinated_rejections and not timeout_rejections:
                title = "객관적 증거 우선주의 승인"
                summary = "✅ 사령관 중재: 물리 증거 우선주의 승인"
            elif timeout_rejections and not hallucinated_rejections:
                title = "동료 검증 및 인프라 지연 면책 우선주의 승인"
                summary = "✅ 사령관 중재: 동료 검증 우선주의 승인"
            else:
                title = "객관적 증거 및 동료 검증 우선주의 승인"
                summary = "✅ 사령관 중재: 객관적 증거 및 동료 검증 우선주의 승인"

            return ResolvingDict({
                "has_conflict": True,
                "is_approved": True,
                "arbitration_text": (
                    f"⚖️ **[사령관 & 수석감사관 중재 판결: {title}]**\n"
                    + "\n".join([f"- {r}" for r in override_reasons]) + "\n"
                    f"- 판결: 외부 인프라 지연 또는 사실오인 반려를 기각하고 동료 검증 통과를 확정 승인합니다."
                ),
                "verdict_summary": summary
            })

        rej_points = []
        for q_name, q_data in rejections.items():
            out = q_data.get("output", "") or ""
            lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("[STATUS")]
            summary_snippet = " ".join(lines[:3])[:200]
            rej_points.append(f"• **{q_name}**: {summary_snippet}")

        app_points = []
        for q_name, q_data in approvals.items():
            out = q_data.get("output", "") or ""
            lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("[STATUS")]
            summary_snippet = " ".join(lines[:2])[:150]
            app_points.append(f"• **{q_name}**: {summary_snippet}")

        # Deterministic dialectical synthesis:
        # A(Approvals) vs B(Rejections) -> Single authoritative compromise/direction
        rej_names_str = ", ".join(rejections.keys())
        app_names_str = ", ".join(approvals.keys()) if approvals else "없음"

        # Extract essential defect snippet to synthesize directive
        raw_rej_texts = [p.split("**: ", 1)[-1] for p in rej_points]
        clean_issue = " / ".join([s[:100] for s in raw_rej_texts])

        if approvals:
            synthesized_directive = (
                f"기존 {app_names_str}이 승인한 핵심 기능 및 아키텍처 기반을 견지하되, "
                f"반려 측({rej_names_str})이 지적한 핵심 결함 [{clean_issue}]만 핀포인트로 보강하여 단일 결론으로 수렴하십시오. "
                f"상반된 스타일 논쟁으로 기승인된 코드를 전면 번복하지 마십시오."
            )
        else:
            synthesized_directive = (
                f"반려 리뷰어({rej_names_str})가 요구한 핵심 결함 지적사항 [{clean_issue}]을 최우선 보강하여 "
                f"간결하고 안전한 단일 해법으로 구현하십시오."
            )

        arbitration_text = (
            f"⚖️ **[사령관 <@1540273467453345812> & 수석감사관 교차 중재 판결 (Conflict Resolution)]**\n"
            f"────────────────────────────────────────\n"
            f"**[상황 진단]**: 복수 QA 리뷰어 간 관점 충돌 또는 다중 반려 발생 (사공 분쟁 방지 프로토콜)\n"
            + (f"**[승인 측 의견 (A안)]**:\n" + "\n".join(app_points) + "\n" if app_points else "")
            + f"**[반려 측 지적 (B안)]**:\n" + "\n".join(rej_points) + "\n\n"
            f"**[사령관 최종 단일화 지침 (Synthesized Directive for Coder)]**:\n"
            f"{synthesized_directive}\n"
            f"────────────────────────────────────────"
        )

        return ResolvingDict({
            "has_conflict": True,
            "is_approved": False,
            "arbitration_text": arbitration_text,
            "verdict_summary": "⚠️ 사령관 교차 중재: 단일화된 보강 지침 전달 (만장일치 수렴)",
            "synthesized_directive": synthesized_directive,
            "rejected_qa_names": list(rejections.keys())
        })

    @classmethod
    async def create_autonomous_product_plan_async(cls, prompt: str, workspace: str) -> Dict[str, Any]:
        """
        Synthesizes an authentic, tailored product plan using Gemini (COMMANDER_MODEL)
        as the user's executive proxy. Falls back to AutonomousProductPlanner smart heuristic.
        """
        from product_planner import AutonomousProductPlanner
        clean_p = prompt.strip()
        reality = cls.inspect_codebase_reality(workspace)
        planning_query = (
            f"{COMMANDER_PLANNING_SYSTEM_PROMPT}\n\n"
            f"[USER COMMISSIONED PRODUCT PROMPT]:\n{clean_p[:2500]}\n\n"
            f"[AUTHENTIC CODEBASE REALITY ON DISK]:\n{reality.summary_text}\n\n"
            f"[WORKSPACE CONTEXT]:\nWorkspace path: {workspace}\n"
        )

        plan_data = None
        try:
            raw_res = await asyncio.wait_for(
                cls._run_raw_llm_query(planning_query, workspace),
                timeout=60.0
            )
            if raw_res:
                cleaned = raw_res.strip()
                if "```json" in cleaned:
                    m = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                elif "```" in cleaned:
                    m = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                s_idx = cleaned.find("{")
                e_idx = cleaned.rfind("}")
                if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                    plan_data = json.loads(cleaned[s_idx:e_idx+1])
        except Exception as e:
            logger.info(f"Commander LLM product planning note (falling back to smart heuristic): {e}")

        if plan_data and isinstance(plan_data, dict) and plan_data.get("title"):
            try:
                plan = AutonomousProductPlanner.plan_product_from_dict(plan_data, workspace)
                return {
                    "product_def": plan.product_def,
                    "plan": plan,
                    "initial_directive": plan.initial_coder_directive,
                    "milestones": plan.milestones,
                    "visual_asset_spec": getattr(plan, "visual_asset_spec", ""),
                    "source": "ai"
                }
            except Exception as parse_err:
                logger.warning(f"Failed to build plan from AI dict: {parse_err}")

        # Smart Heuristic Fallback
        plan = AutonomousProductPlanner.plan_product_and_quality(prompt, workspace)
        return {
            "product_def": plan.product_def,
            "plan": plan,
            "initial_directive": plan.initial_coder_directive,
            "milestones": plan.milestones,
            "visual_asset_spec": getattr(plan, "visual_asset_spec", ""),
            "source": "heuristic"
        }

    @classmethod
    def create_autonomous_product_plan(cls, prompt: str, workspace: str) -> Dict[str, Any]:
        """
        Synchronous entry point for Commander product planning.
        Returns a ResolvingDict for seamless sync usage in tests or async awaiting in coroutines.
        """
        from product_planner import AutonomousProductPlanner
        sync_plan = AutonomousProductPlanner.plan_product_and_quality(prompt, workspace)

        domain = sync_plan.product_def.domain
        p_title_lower = (prompt + " " + sync_plan.product_def.title).lower()
        is_viewer_or_editor = any(k in p_title_lower for k in ["pdf", "에디터", "편집", "뷰어", "viewer", "editor", "캔버스", "canvas", "드로잉", "그림", "문서"])

        # Format 3-stage milestone ladder for Commander Mode 3: v1.0 MVP, v2.0 UX/Features, v3.0 Production
        if len(sync_plan.milestones) > 3:
            m1 = dict(sync_plan.milestones[0])
            m1["version"] = "v1.0"
            m1["status"] = "IN_PROGRESS"

            if domain in ("cli_agent", "daemon"):
                m2 = {
                    "version": "v2.0",
                    "title": "Robust Input Validation & Rich Terminal UX (예외 방어 및 터미널 UX)",
                    "dimension": "resilience_recovery",
                    "tasks": [
                        "비정상 인자/결측값 입력 시 친절한 안내 메시지 및 방어 로직",
                        "표준 프로세스 종료 코드 준수 및 Rich 콘솔 출력 포맷팅",
                        "SIGINT/SIGTERM 감지 시 안전한 graceful shutdown"
                    ],
                    "target_quality": "크래시 없는 안전한 종료 및 직관적 터미널 UX",
                    "status": "PENDING"
                }
            elif domain == "game":
                m2 = {
                    "version": "v2.0",
                    "title": "Game Visual Assets & Audio (사령관 에셋 승인 및 시각/오디오 연동)",
                    "dimension": "visual_quality",
                    "tasks": [
                        "사령관 <@1539448922643234876> 공식 승인 하에 Rule 8 에셋(Type A/B 스프라이트/배경) generate_image 호출 생성 및 바인딩",
                        "Canvas 60fps 파티클 폭발 및 충돌 피드백 연출",
                        "WebAudio 절차적 사운드 효과음 및 배경음악 연결"
                    ],
                    "target_quality": "상용 아케이드 수준의 시각적 몰입감 및 손맛 피드백",
                    "status": "PENDING"
                }
            elif is_viewer_or_editor:
                m2 = {
                    "version": "v2.0",
                    "title": "UX Ergonomics & 65% Viewport Ratio (캔버스 뷰포트 최적화)",
                    "dimension": "ux_ergonomics",
                    "tasks": [
                        "65% 캔버스 뷰포트 확보: 상단 앱바/알림바/탭바/툴바 및 하단 고정 바 합계 <= 화면 높이의 35%, 순수 콘텐츠 뷰포트 >= 65% 확보",
                        "스크롤 시 보조 바/헤더 자동 숨김(Auto-Hide) 및 크롬 패딩 슬림화",
                        "주요 터치 타깃 최적화 및 가로 스크롤 버그 0px 방어"
                    ],
                    "target_quality": "상/하단 바 과다 중첩 방어, 65% 이상 순수 작업 영역 확보",
                    "status": "PENDING"
                }
            else:
                m2 = {
                    "version": "v2.0",
                    "title": "UX Ergonomics & Responsive Layout (인체공학적 UX 및 반응형 레이아웃)",
                    "dimension": "ux_ergonomics",
                    "tasks": [
                        "화면 크기에 맞춘 자연스러운 반응형 레이아웃 및 여백 최적화",
                        "주요 인터랙션 요소 터치 사용성 및 엄지 영역 접근성 확보",
                        "가로 스크롤 버그 0px 방어 및 키보드 오버레이 가림 방지"
                    ],
                    "target_quality": "쾌적한 화면 구성 및 한 손 조작 인체공학적 UI",
                    "status": "PENDING"
                }

            m3 = dict(sync_plan.milestones[-1])
            m3["version"] = "v3.0"
            m3["status"] = "PENDING"

            mode3_milestones = [m1, m2, m3]
            sync_plan.milestones = mode3_milestones
            AutonomousProductPlanner._sync_prd_md(workspace, sync_plan.product_def, sync_plan.quality_dimensions, prompt, milestones=mode3_milestones)
            AutonomousProductPlanner._sync_roadmap_md(workspace, sync_plan.product_def, mode3_milestones)

        base_dict = {
            "product_def": sync_plan.product_def,
            "plan": sync_plan,
            "initial_directive": sync_plan.initial_coder_directive,
            "milestones": sync_plan.milestones,
            "visual_asset_spec": getattr(sync_plan, "visual_asset_spec", ""),
            "source": "heuristic"
        }

        async def _resolve_coro():
            ai_res = await cls.create_autonomous_product_plan_async(prompt, workspace)
            return ai_res

        return ResolvingDict(base_dict, async_coro=_resolve_coro)

    @classmethod
    def format_macro_history(cls, macro_history: List[Dict[str, Any]]) -> str:
        """
        Formats clean macro history containing ONLY Commander directives
        and Auditor final conclusions, completely omitting intermediate Coder-QA chatter.
        """
        if not macro_history:
            return ""
        lines = ["**[👑 Commander & ⚖️ Auditor 직전 회차 공인 확정 기록 (Macro Memory)]**:"]
        for rec in macro_history[-3:]:
            c_num = rec.get("cycle", 1)
            ms_title = rec.get("milestone", "이전 단계")
            c_dir = rec.get("commander_directive", "")[:180]
            a_con = rec.get("auditor_conclusion", "")[:220]
            lines.append(
                f"• (회차 {c_num}: {ms_title})\n"
                f"  - 👑 사령관 지침: \"{c_dir}\"\n"
                f"  - ⚖️ 감사관 승인 결론: \"{a_con}\""
            )
        return "\n".join(lines)


    @classmethod
    def _build_heuristic_5w1h_plan(
        cls,
        milestone_title: str,
        milestone_version: str,
        tasks: Optional[List[str]],
        domain: str,
        workspace: Optional[str] = None,
        user_prompt: str = "",
        macro_memory_text: str = "",
        delta_analysis: str = "",
        thinking_process: str = ""
    ) -> Milestone5W1HPlan:
        v_num = milestone_version.lower()
        is_v1 = ("v1" in v_num or "1" in v_num or not v_num)
        is_v2 = ("v2" in v_num or "2" in v_num)
        is_v3 = ("v3" in v_num or "3" in v_num)

        ws_label = workspace or "작업 디렉토리"

        clean_user_p = user_prompt.strip()
        is_adaptive_internal = clean_user_p.startswith("사용자 원본 목표:")
        has_user_directive = bool(clean_user_p and not is_adaptive_internal and not clean_user_p.startswith("🎯 [") and not clean_user_p.startswith("!mode"))
        user_lower = clean_user_p.lower() if has_user_directive else ""

        has_design_req = any(k in user_lower for k in ["로고", "이름", "디자인", "컬러", "색상", "버튼", "가독성", "룩앤필", "스타일", "팔레트"])
        has_edit_req = any(k in user_lower for k in ["edit", "에디트", "수정", "글자", "텍스트", "문구", "ocr"])
        has_complaint_or_quality = any(k in user_lower for k in ["별로", "퀄리티", "개선", "피드백", "반영", "바꿔", "떨어", "높여"])

        # Top-level priority: Direct User Feedback & Directives override all generic templates
        if has_user_directive and (has_design_req or has_edit_req or has_complaint_or_quality):
            user_tasks = []
            if has_design_req:
                user_tasks.append("첨부파일 컬러 팔레트 기반 브랜드 테마, 신규 로고 및 앱 이름 리뉴얼")
                user_tasks.append("UI 버튼 시인성 및 가독성 고대비 최적화 (WCAG AA 명도 대비 준수 및 터치 타깃 48dp)")
            if has_edit_req:
                user_tasks.append("기존 PDF 내 원본 텍스트 스트림 직접 수정(In-place Text Stream Editing, No-OCR) 엔진 구현")
                user_tasks.append("수정된 텍스트 및 레이아웃의 실시간 렌더링 및 무손실 파일 저장/내보내기 검증")
            if not user_tasks:
                user_tasks.append(f"사용자 피드백 지시사항 실체 구현 ({clean_user_p[:60]})")
            user_tasks.append("기능 수정 회귀 방어 및 런타임 크래시 0건 검증")

            tasks = user_tasks
            milestone_title = "사용자 피드백 긴급 반영: 브랜드 리뉴얼(로고·앱명·컬러), 버튼 가독성 개선 및 PDF 원본 텍스트 직접 편집"

            why = (
                "사용자가 제기한 피드백을 최우선 반영하여, 불만족스러웠던 로고/앱 이름/디자인 시각 완성도와 "
                "버튼 가독성을 대폭 개선하고, 기존 PDF 원본 문서 내 텍스트를 OCR 없이 직접 수정하는 핵심 기능을 완결하기 위함입니다."
            )
            how = (
                "첨부파일 컬러 토큰 추출 적용, 버튼 고대비 컴포넌트 리팩토링, "
                "PDF Content Stream 파싱 및 Tj/TJ 텍스트 연산자 직접 치환 엔진을 적용합니다."
            )
            who_where = (
                f"• Coder: 브랜드 컬러 에셋, 고대비 버튼 UI 및 PDF 원본 텍스트 직접 수정 엔진 구현\n"
                f"• QA: 버튼 가독성 및 원본 PDF 텍스트 무손실 수정 검증\n"
                f"• Auditor: 사용자 피드백 요구사항 100% 충족 및 상용 품질 최종 승인\n"
                f"• 대상 경로: {ws_label}/"
            )
            visual_asset_spec = (
                "🎨 [Rule 8 시각 디자인 에셋 생성 승인]\n"
                "• Type A: 첨부파일 컬러 팔레트를 반영한 세련된 신규 브랜드 로고 및 앱 아이콘 일러스트 생성\n"
                "• Type B: 높은 가독성과 시인성을 갖춘 직관적인 고대비 버튼 및 도구 아이콘 에셋 배치"
            )
            visual_approved = True

        elif domain == "mobile_app":
            if is_v1:
                why = f"'{milestone_title}'의 핵심 코어 기능을 빠르게 만들어, 스마트폰에서 기본 조작과 데이터 저장이 원활히 동작하는지 확인하기 위함입니다."
            elif is_v2:
                why = f"'{milestone_title}'에 편리한 부가 기능(검색/정렬/필터)과 깔끔한 시각 에셋을 추가하여 사용 편의성과 서비스 완성도를 높이기 위함입니다."
            else:
                why = f"'{milestone_title}'의 앱 안정성과 예외 처리를 철저히 검증하여, 마켓 출시가 가능한 수준의 무결점 앱을 완성하기 위함입니다."

            how = "Compose 기반 반응형 UI와 48dp 터치 타깃 규격을 준수하며, 직관적인 터치 조작감과 안정적인 데이터 저장을 구현합니다."
            who_where = f"• Coder: 모바일 UI 및 핵심 로직 구현\n• QA: 화면 인터랙션 및 빌드 무결점 검수\n• Auditor: 상용 앱 수준 최종 품질 승인\n• 대상 경로: app/src/main/ ({ws_label}/)"
            visual_asset_spec = (
                "🎨 [Rule 8 시각 디자인 에셋 생성 승인]\n"
                "• Type A: 스플래시 화면 및 대표 런처 아이콘 일러스트 생성 (res/drawable/ 저장)\n"
                "• Type B: 화면에 자연스럽게 녹아드는 깔끔한 버튼·배지 디자인 (painterResource 연결)"
            )
            visual_approved = True

        elif domain == "game":
            if is_v1:
                why = f"'{milestone_title}'의 게임 루프와 조작/충돌 시스템을 빠르게 구축하여 게임 본연의 재미와 플레이 감각을 입증하기 위함입니다."
            elif is_v2:
                why = f"'{milestone_title}'에 스테이지, 아이템, 시각 이펙트를 추가하여 몰입감 넘치는 시각적 연출과 게임성을 강화하기 위함입니다."
            else:
                why = f"'{milestone_title}'의 게임 난이도 밸런스와 프레임 안정성을 완벽히 가다듬어 상용 출시 수준의 완성도를 확보하기 위함입니다."

            how = "부드러운 60fps 화면 렌더링, 정확한 충돌 감지, 상태 전환 및 다채로운 파티클 연출을 구현합니다."
            who_where = f"• Coder: 게임 엔진 및 캐릭터/물리 로직 구현\n• QA: 프레임 레이트 및 충돌 무결점 검증\n• Auditor: 인터랙티브 게임 완성도 최종 승인\n• 대상 경로: {ws_label}/"
            visual_asset_spec = (
                "🎨 [Rule 8 시각 디자인 에셋 생성 승인]\n"
                "• Type A: 타이틀 배경 일러스트 및 스테이지 맵 에셋 생성\n"
                "• Type B: 캐릭터, 아이템, 적 스프라이트 및 직관적인 조이스틱/UI 에셋 생성\n"
                "• 60fps 렌더링 기준 준수"
            )
            visual_approved = True

        elif domain in ("cli_agent", "daemon"):
            if is_v1:
                why = f"'{milestone_title}'의 핵심 명령어 처리 엔진을 완성하여 빠르고 직관적인 명령행 실행 기반을 마련하기 위함입니다."
            elif is_v2:
                why = f"'{milestone_title}'의 에러 예외 방어와 보기 편한 실시간 진행 피드백(콘솔 UI)을 적용하기 위함입니다."
            else:
                why = f"'{milestone_title}'의 장시간 무중단 동작 안정성과 리소스 누수 0건을 입증하여 신뢰할 수 있는 도구를 완성하기 위함입니다."

            how = "직관적인 명령행 인자 파싱, 보기 편한 컬러 터미널 출력, 안전한 비동기 작업 및 프로세스 생명주기를 관리합니다."
            who_where = f"• Coder: CLI 엔진 및 비즈니스 로직 작성\n• QA: 명령어 정상 종료 및 예외 처리 검증\n• Auditor: 무결점 동작 최종 승인\n• 대상 경로: {ws_label}/"
            visual_asset_spec = "시각 에셋 불필요 (CLI 콘솔 환경: ANSI 터미널 텍스트 UI 적용)"
            visual_approved = False

        elif domain == "backend_api":
            if is_v1:
                why = f"'{milestone_title}'의 핵심 데이터 모델과 API 엔드포인트를 완성하여 튼튼한 서비스 기반을 마련하기 위함입니다."
            elif is_v2:
                why = f"'{milestone_title}'의 데이터 유효성 검증과 표준 에러 응답 체계를 갖추어 안정적인 서비스 연동을 지원하기 위함입니다."
            else:
                why = f"'{milestone_title}'의 부하 안정성과 API 문서화를 완성하여 누구나 쉽게 호출할 수 있는 완성형 백엔드를 구축하기 위함입니다."

            how = "표준 RESTful 규격, 꼼꼼한 데이터 유효성 검사, 명확한 HTTP 상태 코드 및 비동기 고속 처리를 적용합니다."
            who_where = f"• Coder: API 라우터 및 데이터 서비스 구현\n• QA: 데이터 스키마 및 응답 정합성 검증\n• Auditor: 백엔드 아키텍처 최종 승인\n• 대상 경로: {ws_label}/"
            visual_asset_spec = "시각 에셋 불필요 (백엔드 API 전용 환경: OpenAPI/Swagger 표준 JSON 스키마 및 문서화 준수)"
            visual_approved = False

        else:  # saas_web / default GUI
            if is_v1:
                why = f"'{milestone_title}'의 반응형 레이아웃과 핵심 기능을 구현하여, 브라우저에서 사용자가 즉각 가치를 체감할 수 있도록 만들기 위함입니다."
            elif is_v2:
                why = f"'{milestone_title}'에 편리한 검색/필터 기능과 빈 화면 안내, 세련된 비주얼 디자인을 입혀 상용 서비스 수준으로 끌어올리기 위함입니다."
            else:
                why = f"'{milestone_title}'의 모바일/PC 크로스 브라우징과 성능을 완벽히 다듬어 안정적인 웹 서비스를 완성하기 위함입니다."

            how = "모던 웹 표준 컴포넌트 구조, 모바일과 PC 모두를 고려한 깔끔한 반응형 레이아웃, 직관적인 비동기 피드백(로딩/알림)을 구현합니다."
            who_where = f"• Coder: 웹 UI 컴포넌트 및 프론트엔드 로직 구현\n• QA: 반응형 레이아웃 및 브라우저 콘솔 무결점 검수\n• Auditor: 상용 웹 서비스 완성도 최종 승인\n• 대상 경로: {ws_label}/"
            visual_asset_spec = (
                "🎨 [Rule 8 시각 디자인 에셋 생성 승인]\n"
                "• Type A: 브랜드 로고 및 메인 히어로 배너 일러스트 생성 (assets/images/ 저장)\n"
                "• Type B: Empty State 안내 일러스트 및 주요 기능 아이콘 에셋 배치"
            )
            visual_approved = True

        what_list = list(tasks) if tasks else [
            f"{milestone_title} 핵심 비즈니스 로직 및 인터랙션 구현",
            "반응형 UI 레이아웃 및 사용자 데이터 영속화",
            "예외 방어 및 안정성 보강"
        ]

        when_list = [
            "1. 컴파일/빌드 성공 및 정적 분석(Lint/Type) 에러 0건",
            f"2. {milestone_title} 명시된 기능 스펙 디스크 소스코드 상에 실체 구현 완료 (스텁/TODO/가짜 목업 엄격 배제)",
            "3. 엣지 케이스 및 예외 처리 방어 (비정상 입력/대용량 데이터/I/O 실패 복원력)",
            "4. 콘솔 오류 및 런타임 크래시 0건 (실사용 반응성 및 60fps 무결점 작동)",
            "5. ⚖️ Auditor(감사관) 최종 전수 검증 통과 (PASS) 획득"
        ]

        qcs = QualityStateManager.generate_domain_default_qcs(
            domain=domain,
            title=milestone_title,
            tasks=what_list,
            milestone_version=milestone_version
        )

        plan = Milestone5W1HPlan(
            version=milestone_version,
            title=milestone_title,
            domain=domain,
            why=why,
            what=what_list,
            how=how,
            who_where=who_where,
            when_acceptance=when_list,
            visual_asset_spec=visual_asset_spec,
            visual_approved=visual_approved,
            source="heuristic",
            quality_contracts=qcs,
            delta_analysis=delta_analysis,
            thinking_process=thinking_process
        )
        plan.directive_for_coder = plan.build_directive_for_coder(
            macro_memory_text=macro_memory_text,
            user_prompt=user_prompt
        )
        return plan

    @classmethod
    async def plan_milestone_5w1h_async(
        cls,
        milestone_title: str,
        milestone_version: str = "v1.0",
        tasks: Optional[List[str]] = None,
        domain: Optional[str] = None,
        workspace: Optional[str] = None,
        user_prompt: str = "",
        macro_memory_text: str = ""
    ) -> Milestone5W1HPlan:
        """
        [5W1H Strategic Milestone Planning Engine]
        Synthesizes a comprehensive 5W1H (왜/무엇을/어떻게/누가·어디서/완료기준) master plan.
        Proactively authorizes visual asset generation (generate_image, Rule 8) for UI applications.
        Runs fast LLM query with Gemini 3.7 Flash and safely falls back to domain-adaptive heuristics.
        """
        from product_planner import AutonomousProductPlanner
        dom = domain or AutonomousProductPlanner.detect_domain(user_prompt or milestone_title, workspace)

        # Smart Heuristic baseline
        heuristic_plan = cls._build_heuristic_5w1h_plan(
            milestone_title=milestone_title,
            milestone_version=milestone_version,
            tasks=tasks,
            domain=dom,
            workspace=workspace,
            user_prompt=user_prompt,
            macro_memory_text=macro_memory_text
        )

        user_priority_rule = ""
        if user_prompt and not user_prompt.strip().startswith("🎯 ["):
            user_priority_rule = (
                f"\n[🔥 SUPREME ARCHITECTURAL MANDATE: USER PROMPT IS ABSOLUTE TOP PRIORITY]\n"
                f"The user has directly submitted the following instruction/feedback:\n"
                f"\"{user_prompt}\"\n"
                f"You MUST 100% faithfully prioritize, address, and reflect these user requirements above all else! "
                f"Do NOT default to generic search/filter features or ignore the user's explicit complaints. "
                f"If the user asks for design revamp, logo change, color updates (from attachments), button readability, or in-place PDF text modification, "
                f"your 'title', 'why', 'what', 'how', and 'when_acceptance' MUST DIRECTLY and CONCRETELY address these exact requirements!\n"
            )

        ai_query = (
            f"You are [👑 Commander], the Chief Strategic Product Architect.\n"
            f"Formulate a razor-sharp 5W1H (육하원칙) milestone master plan for the engineering team.\n"
            f"{user_priority_rule}\n"
            f"Target Milestone: {milestone_version} — {heuristic_plan.title or milestone_title}\n"
            f"Domain: {dom}\n"
            f"Candidate Tasks: {', '.join(heuristic_plan.what or tasks or []) if (heuristic_plan.what or tasks) else 'N/A'}\n"
            f"User Prompt: {user_prompt[:2000] if user_prompt else 'N/A'}\n"
            f"Macro History: {macro_memory_text[:1500] if macro_memory_text else 'None'}\n\n"
            f"[CRITICAL COMMANDER ARCHITECTURAL PRINCIPLES]\n"
            f"1. ATOMIC SCOPE DECOMPOSITION (단일 책임 마일스톤): If the target milestone or candidate tasks bundle multiple disparate complex systems (e.g. combining Text Search + Pen Drawing + Redaction + Export), you MUST narrow and decompose the scope to EXACTLY ONE cohesive capability for this milestone. Defer secondary features so Coder does not produce shallow stubs.\n"
            f"2. ZERO-STUB & ANTI-MOCK MANDATE: The Coder is strictly prohibited from writing dummy mocks, placeholder buttons, or '// TODO' stubs. Require deep, substantive algorithmic and data-level implementation.\n"
            f"3. HARDENED ACCEPTANCE CRITERIA: In 'when_acceptance', specify concrete stress/edge-case tests, real-world data validation (e.g., handling edge-case inputs, memory safety, 60fps responsiveness), not just 'build succeeds'.\n"
            f"4. QUALITY CONTRACT (기계적 정량 품질 검증 계약): Formulate 3~5 verifiable atomic QC items (Level 1: AST/Code/Metric, Level 2: Binary Vision). E.g. [QC-G-01], [QC-V-01], [QC-U-01], [QC-P-01]. Each MUST have a concrete verification rule.\n"
            f"5. SMART VISUAL ASSET AUTHORIZATION: Only authorize visual image generation (Rule 8) if this milestone explicitly implements new visual screens or graphical UI assets. For core algorithms, search, or backend logic, state that image generation is deferred to focus on core logic.\n\n"
            f"Output a valid JSON object ONLY with the following structure:\n"
            f"{{\n"
            f'  "title": "단일 마일스톤 타이틀 (사용자 요구사항을 직접 반영하여 구체적으로 명명)",\n'
            f'  "why": "전략적 당위성 및 목적 (왜 이번 단일 마일스톤에 집중해야 하는지 2~3문장)",\n'
            f'  "what": ["구체적 구현 기능 1 (핵심 로직 집중)", "구체적 구현 기능 2 (예외/안정성 방어)", "구체적 구현 기능 3 (사용자 인터랙션 완결)"],\n'
            f'  "how": "아키텍처 및 기술 구현 방법론 (기술 스택, 상태 관리, 뷰포트 등)",\n'
            f'  "who_where": "작업 주체(Coder/QA/Auditor) 및 수정 대상 파일/디렉토리 타깃 경로",\n'
            f'  "when_acceptance": ["기계적 인수 기준 1 (컴파일/빌드 성공)", "기계적 인수 기준 2 (스텁/TODO 없는 실체 구현)", "기계적 인수 기준 3 (실데이터 엣지케이스 검증)", "기계적 인수 기준 4 (Auditor 최종 승인)"],\n'
            f'  "quality_contracts": [\n'
            f'    {{"id": "QC-G-01", "category": "gameplay", "description": "난이도 가속도 공식 구현", "level": 1, "verification_rule": "elapsedTime 기반 속도 가중치 수식 존재"}},\n'
            f'    {{"id": "QC-V-01", "category": "visual", "description": "에셋 이미지 바인딩", "level": 2, "verification_rule": "단색 사각형 대신 이미지/스프라이트 렌더링"}}\n'
            f'  ],\n'
            f'  "visual_asset_spec": "시각 에셋 생성 계획 (필요 시 Rule 8 Type A/Type B 승인 내용, 불필요 시 명시)",\n'
            f'  "visual_approved": true\n'
            f"}}\n"
        )

        try:
            raw_res = await asyncio.wait_for(
                cls._run_raw_llm_query(ai_query, workspace),
                timeout=45.0
            )
            if raw_res:
                cleaned = raw_res.strip()
                if "```json" in cleaned:
                    m = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                elif "```" in cleaned:
                    m = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                s_idx = cleaned.find("{")
                e_idx = cleaned.rfind("}")
                if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                    data = json.loads(cleaned[s_idx:e_idx+1])
                    if isinstance(data, dict) and data.get("why") and data.get("what"):
                        target_title = str(data.get("title", "")).strip() or heuristic_plan.title or milestone_title
                        parsed_qcs = data.get("quality_contracts")
                        if not parsed_qcs or not isinstance(parsed_qcs, list):
                            parsed_qcs = heuristic_plan.quality_contracts

                        ai_plan = Milestone5W1HPlan(
                            version=milestone_version,
                            title=target_title,
                            domain=dom,
                            why=str(data["why"]).strip(),
                            what=list(data["what"]) if isinstance(data["what"], list) else heuristic_plan.what,
                            how=str(data.get("how", heuristic_plan.how)).strip(),
                            who_where=str(data.get("who_where", heuristic_plan.who_where)).strip(),
                            when_acceptance=list(data.get("when_acceptance", heuristic_plan.when_acceptance)) if isinstance(data.get("when_acceptance"), list) else heuristic_plan.when_acceptance,
                            visual_asset_spec=str(data.get("visual_asset_spec", heuristic_plan.visual_asset_spec)).strip(),
                            visual_approved=bool(data.get("visual_approved", heuristic_plan.visual_approved)),
                            source="ai",
                            quality_contracts=parsed_qcs
                        )
                        ai_plan.directive_for_coder = ai_plan.build_directive_for_coder(
                            macro_memory_text=macro_memory_text,
                            user_prompt=user_prompt
                        )
                        return ai_plan
        except Exception as e:
            logger.debug(f"Commander 5W1H AI planning note (fallback to heuristic): {e}")

        return heuristic_plan

    @classmethod
    def plan_milestone_5w1h(
        cls,
        milestone_title: str,
        milestone_version: str = "v1.0",
        tasks: Optional[List[str]] = None,
        domain: Optional[str] = None,
        workspace: Optional[str] = None,
        user_prompt: str = "",
        macro_memory_text: str = ""
    ) -> Milestone5W1HPlan:
        """Synchronous entry point for 5W1H milestone planning."""
        from product_planner import AutonomousProductPlanner
        dom = domain or AutonomousProductPlanner.detect_domain(user_prompt or milestone_title, workspace)
        return cls._build_heuristic_5w1h_plan(
            milestone_title=milestone_title,
            milestone_version=milestone_version,
            tasks=tasks,
            domain=dom,
            workspace=workspace,
            user_prompt=user_prompt,
            macro_memory_text=macro_memory_text
        )

    @classmethod
    def synthesize_mode3_milestone_directive(
        cls,
        milestone_title: str,
        milestone_version: str,
        tasks: List[str],
        macro_memory_text: str = "",
        user_prompt: str = "",
        visual_approved: bool = False,
        workspace: Optional[str] = None,
        domain: Optional[str] = None
    ) -> str:
        """
        [Mode 3 Macro Orchestration Mandate]
        Synthesizes a razor-sharp, isolated 5W1H milestone directive for Coder & QA.
        """
        plan = cls.plan_milestone_5w1h(
            milestone_title=milestone_title,
            milestone_version=milestone_version,
            tasks=tasks,
            domain=domain,
            workspace=workspace,
            user_prompt=user_prompt,
            macro_memory_text=macro_memory_text
        )
        return plan.directive_for_coder

    @classmethod
    def inspect_codebase_reality(cls, workspace: Optional[str]) -> CodebaseReality:
        """Physical disk inspection of workspace to ground Commander planning in authentic reality."""
        return CodebaseRealityInspector.inspect(workspace)

    @classmethod
    def _compute_next_version(cls, reality: CodebaseReality) -> str:
        """Computes the next logical version string based on existing milestones on disk."""
        all_milestones = list(reality.completed_milestones)
        if reality.current_milestone:
            all_milestones.append(reality.current_milestone)

        if not all_milestones:
            return "v1.0"

        max_maj = 1
        max_min = 0
        for cm in all_milestones:
            m = re.search(r'v(\d+)\.(\d+)', cm)
            if m:
                maj, mino = int(m.group(1)), int(m.group(2))
                if (maj, mino) > (max_maj, max_min):
                    max_maj, max_min = maj, mino

        if max_min < 3:
            return f"v{max_maj}.{max_min + 1}"
        else:
            return f"v{max_maj + 1}.0"

    @classmethod
    def _build_heuristic_adaptive_plan(
        cls,
        root_user_goal: str,
        reality: CodebaseReality,
        domain: str,
        target_version: str,
        workspace: Optional[str] = None,
        macro_memory_text: str = ""
    ) -> Milestone5W1HPlan:
        """
        Smart domain-tailored heuristic delta planner grounded in physical disk reality.
        Compares what exists against the user's root goal and selects exactly ONE next missing capability.
        """
        comps = set(c.lower() for c in reality.implemented_components)
        p_lower = root_user_goal.lower()

        # 0. User Feedback & Directive Priority: User directives always supersede autonomous ladders
        has_design_req = any(k in p_lower for k in ["로고", "이름", "디자인", "컬러", "색상", "버튼", "가독성", "ui", "룩앤필", "스타일", "팔레트"])
        has_edit_req = any(k in p_lower for k in ["edit", "에디트", "수정", "글자", "텍스트", "문구", "ocr"])
        has_feedback = any(k in p_lower for k in ["별로", "퀄리티", "개선", "피드백", "반영", "바꿔", "떨어", "높여"])

        if has_design_req or has_edit_req or has_feedback:
            title = "사용자 피드백 긴급 반영: 브랜드 리뉴얼(로고·앱명·컬러), 버튼 가독성 개선 및 PDF 원본 텍스트 직접 편집"
            what = []
            if has_design_req:
                what.append("첨부파일 컬러 팔레트 기반 브랜드 테마, 신규 로고 및 앱 이름 리뉴얼")
                what.append("UI 버튼 시인성 및 가독성 고대비 최적화 (WCAG AA 명도 대비 준수 및 터치 타깃 48dp)")
            if has_edit_req:
                what.append("기존 PDF 내 원본 텍스트 스트림 직접 수정(In-place Text Stream Editing, No-OCR) 엔진 구현")
                what.append("수정된 텍스트 및 레이아웃의 실시간 렌더링 및 무손실 파일 저장/내보내기 검증")
            if not what:
                what.append(f"사용자 피드백 지시사항 실체 구현 ({root_user_goal[:60]})")
            what.append("기능 수정 회귀 방어 및 런타임 크래시 0건 검증")

            how = "첨부파일 컬러 토큰 추출 적용, 버튼 고대비 컴포넌트 리팩토링, PDF Content Stream 파싱 및 Tj/TJ 텍스트 연산자 직접 치환 엔진을 적용합니다."
            why = "사용자가 제기한 피드백을 최우선 반영하여, 불만족스러웠던 로고/앱 이름/디자인 시각 완성도와 버튼 가독성을 대폭 개선하고, 기존 PDF 원본 문서 내 텍스트를 OCR 없이 직접 수정하는 핵심 기능을 완결하기 위함입니다."
            visual_asset_spec = (
                "🎨 [Rule 8 시각 디자인 에셋 생성 승인]\n"
                "• Type A: 첨부파일 컬러 팔레트를 반영한 세련된 신규 브랜드 로고 및 앱 아이콘 일러스트 생성\n"
                "• Type B: 높은 가독성과 시인성을 갖춘 직관적인 고대비 버튼 및 도구 아이콘 에셋 배치"
            )
            visual_approved = True

        # 1. Game Domain (e.g. Avoid Apples / Arcade Dodge / Sonic / Mario / Platformer)
        elif domain == "game" or any(k in p_lower for k in ["게임", "game", "사과", "피하기", "낙하", "dodge", "소닉", "마리오", "플랫포머"]):
            is_dodge_game = any(k in p_lower for k in ["사과", "피하기", "낙하", "dodge", "falling", "apple", "피해", "회피", "obstacle"])
            v_target = (target_version or "v1.0").lower()
            is_v2_loop = ("v2" in v_target or "2." in v_target)
            is_v3_loop = ("v3" in v_target or "3." in v_target)
            is_v4_loop = ("v4" in v_target or "4." in v_target)

            if is_dodge_game:
                if is_v2_loop:
                    # Layered Refinement Step 2: Game Feel & Juice + Naming Refinement
                    title = "Game Feel & Naming Refinement (식별자 정합성 리팩토링, 시간 경과 가속도 곡선 및 충돌 손맛)"
                    what = [
                        "아키텍처 식별자 정합성 통일 (사과flow 등 조잡한 명칭을 AppleDropManager, PlayerController 등으로 리팩토링)",
                        "고정 낙하 속도를 시간 경과(elapsedTime) 및 가속도 계수가 곱해진 체감 난이도 곡선 공식으로 교체",
                        "충돌 발생 시 0.1초 화면 진동(Screen Shake) 및 피격 시각적 플래시 연출",
                        "사과 바닥 충돌 시 파티클 폭발 또는 스쿼시 앤 스트레치(Squash & Stretch) 렌더링",
                        "기존 이동 및 충돌 코어 로직의 회귀(Zero-Regression) 100% 방어"
                    ]
                    how = "외과수술식 정밀 수정(Surgical Edit): 기존 파일의 함수/수식 튜닝 및 렌더 피드백 훅 바인딩"
                    why = "1차 원형이 검증되었으므로, 기존 코드를 갈아엎지 않고 네이밍을 우아하게 정돈하며, 이동 조작감과 충돌 손맛(Juice)을 극대화합니다."
                elif is_v3_loop:
                    # Layered Refinement Step 3: Risk vs Reward (심리전과 딜레마)
                    title = "Risk vs Reward (아슬아슬 스치기 Near Miss 판정, 황금 사과 및 콤보 딜레마)"
                    what = [
                        "플레이어 피격 박스 외곽 5~15px 구간 아슬아슬 '스치기(Graze/Near Miss)' 판정 및 보너스 득점",
                        "스치기 성공 시 화면 상단 'CLOSE!' 플로팅 텍스트 및 실시간 콤보 배수 HUD 렌더링",
                        "희귀 확률로 고속 낙하하는 '황금 사과' 스폰 및 스치기 시 피버 모드 발동",
                        "단순 회피를 넘어 위험 속으로 파고들게 만드는 심리적 딜레마 역학 확립"
                    ]
                    how = "AABB 반경 확장 이중 바운딩 박스 판정 및 콤보 상태 머신 연동"
                    why = "단순히 도망만 치면 지루해지므로, 위험을 감수할수록 더 큰 이득을 얻는 스치기 및 황금 사과 시스템을 도입하여 게임의 깊이를 완성합니다."
                elif is_v4_loop:
                    # Layered Refinement Step 4: Pacing & Escalation (환경과 변주)
                    title = "Pacing & Escalation (시간 경과 바람 편향 기믹, 궤적 진동 및 상단 위험 경고선)"
                    what = [
                        "생존 30초 경과 시 좌우 바람(Wind Vector) 발생 및 사과 낙하 궤적 대각선 편향",
                        "공기 저항 및 sin(t) 기반 미세한 낙하 궤적 흔들림(Wobble) 수식 적용",
                        "사과 낙하 0.5초 전 상단 위험 구역 사전 안내 경고선(Telegraphing) 표시",
                        "시간 경과에 따른 배경 색조 전환 및 긴장감 고조 페이즈 연출"
                    ]
                    how = "삼각함수 기반 위치 벡터 연산 및 캔버스 오버레이 경고선 렌더링"
                    why = "획일적인 수직 낙하 패턴을 탈피하여, 바람과 궤적 흔들림을 통해 플레이어의 예측을 시험하는 동적 긴장감을 부여합니다."
                elif reality.is_empty or not any(k in comps for k in ["game", "engine", "canvas", "screen", "mainactivity", "main", "app"]):
                    title = "코어 캔버스 및 60fps 렌더링 루프 MVP"
                    what = [
                        "60fps requestAnimationFrame 메인 루프 및 더블 버퍼링 캔버스",
                        "해상도 독립적 뷰포트 스케일링 및 키보드/터치 입력 이벤트 리스너 연동",
                        "기본 프로젝트 패키징 및 런타임 크래시 0건 검증"
                    ]
                    how = "HTML5 Canvas 기반 60fps 불변 상태 루프"
                    why = "디스크 실사 분석 결과 게임의 기본 구조가 없으므로, 60fps로 구동될 코어 캔버스 루프와 프로젝트 뼈대를 최우선으로 확립합니다."
                elif not any(k in comps for k in ["player", "character", "controller", "바구니", "플레이어"]):
                    title = "플레이어 좌우 이동 물리 및 입력 제어 시스템"
                    what = [
                        "키보드 좌우 화살표/A-D 및 모바일 터치 슬라이드 입력 바인딩",
                        "플레이어 좌우 이동 속도/가속도 및 화면 경계선(Canvas Boundary) 클램핑",
                        "프레임당 입력 이벤트 수신 및 좌표 증분 정상 동작 검증"
                    ]
                    how = "델타 타임(dt) 기반 수평 이동 물리 및 화면 경계 클램핑"
                    why = "코어 캔버스 루프가 마련되었으므로, 사용자가 제어할 플레이어 엔티티의 좌우 이동 조작과 경계 이탈 방어 로직을 구현합니다."
                elif not any(k in comps for k in ["apple", "hazard", "enemy", "obstacle", "fall", "spawner", "사과"]):
                    title = "낙하 장애물(사과) 스폰 및 등속/가속 낙하 물리 엔진"
                    what = [
                        "상단 무작위 X좌표 사과 엔티티 생성 스포너(Interval Spawner)",
                        "중력 가속도 및 Y축 프레임당 증분 낙하 물리 연산",
                        "화면 하단 도달 시 엔티티 메모리 회수 및 오브젝트 풀링(Object Pool)"
                    ]
                    how = "오브젝트 풀링 패턴 및 델타 타임 기반 Y축 오일러 적분"
                    why = "플레이어 이동이 완성되었으므로, 회피 게임의 핵심인 상단 낙하 사과 오브젝트와 낙하 물리 엔진을 구축합니다."
                elif not any(k in comps for k in ["collision", "hit", "damage", "충돌", "gameover"]):
                    title = "정밀 바운딩 박스(AABB) 충돌 판정 및 게임오버 처리"
                    what = [
                        "플레이어-사과 정밀 충돌 판정 (AABB / 원형 히트박스)",
                        "충돌 발생 시 생명력(HP) 감소 및 게임오버 상태 전환 머신",
                        "피격 시 일시적 점멸/무적 및 게임오버 팝업/화면 동결"
                    ]
                    how = "AABB 바운딩 박스 인터섹션 알고리즘 및 유한 상태 머신(FSM)"
                    why = "플레이어와 사과가 공존하므로, 게임의 핵심 규칙인 충돌 감지와 게임오버 판정 루프를 완성합니다."
                elif not any(k in comps for k in ["score", "hud", "highscore", "점수", "restart"]):
                    title = "실시간 스코어 HUD, 최고 기록 영속화 및 1초 재시작 루프"
                    what = [
                        "사과 회피 성공 시 실시간 점수(Score) 누적 및 캔버스 HUD 렌더링",
                        "로컬 스토리지 기반 최고 기록(High Score) 영구 저장 및 갱신",
                        "게임오버 후 스페이스바/원터치 1초 즉시 재도전(Quick Restart) 루프"
                    ]
                    how = "옵저버 패턴 스코어 이벤트 버스 및 LocalStorage 키-값 영속화"
                    why = "핵심 충돌 규칙이 완성되었으므로, 득점과 재도전 루프를 구현하여 완전한 1회 플레이 사이클을 완성합니다."
                else:
                    # Final Polish & Hardening
                    title = "상용 배포 품질 하드닝 및 E2E 회귀 검증"
                    what = [
                        "10분 연속 플레이 시 메모리 누수 0건 및 60fps 유지",
                        "난이도 점진적 상승 곡선 (시간 경과에 따른 사과 낙하 속도 증가)",
                        "전체 E2E 시나리오 테스트 100% 무결점 통과"
                    ]
                    how = "메모리 프로파일링 및 렌더링 최적화, 자동 회귀 테스트"
                    why = "게임의 모든 규칙과 UI가 완성되었으므로, 상용 릴리즈 수준의 성능과 무결점을 확보합니다."
            else:
                if reality.is_empty or not any(k in comps for k in ["game", "engine", "canvas", "screen", "mainactivity"]):
                    title = "코어 게임 아키텍처 및 렌더링 루프 뼈대 구축"
                    what = [
                        "게임 렌더링 캔버스 및 60fps 틱 루프 엔진 구축",
                        "가상 터치 조이스틱 및 기본 터치 이벤트 리스너 연동",
                        "기본 프로젝트 패키징 및 런타임 크래시 0건 검증"
                    ]
                    how = "HTML5 Canvas / Compose Canvas 기반의 고성능 렌더링 루프와 불변 상태 관리"
                    why = "디스크 파일 분석 결과 게임의 기본 구조가 없으므로, 60fps로 구동될 코어 렌더링 루프와 프로젝트 뼈대를 최우선으로 확립합니다."
                elif not any("title" in c or "menu" in c for c in comps):
                    title = "메인 타이틀 화면 및 시작 메뉴 인터랙션"
                    what = [
                        "게임 로고 및 타이틀 그래픽 레이아웃 구현",
                        "시작/옵션/이어하기 인터랙티브 버튼 및 반응형 UI",
                        "시작 전 카운트다운 및 게임 씬 전환 루프"
                    ]
                    how = "반응형 레이아웃 및 씬 매니저(SceneManager)를 통한 화면 전환 상태 머신"
                    why = "코어 구조는 완비되었으나 메인 타이틀 진입점이 부재하므로, 사용자가 게임을 시작할 수 있는 첫인상 화면을 구현합니다."
                elif not any(k in c for c in comps for k in ["player", "character", "sonic", "mario"]):
                    title = "캐릭터 물리(이동·점프·가속도) 및 조작 로직"
                    what = [
                        "수평 이동 가속/감속 물리 엔진 및 터치 조이스틱 바인딩",
                        "점프 포물선, 중력 가속도 및 바닥 착지 판정 로직",
                        "캐릭터 상태 머신(Idle, Run, Jump, Fall) 구현"
                    ]
                    how = "델타 타임(dt) 기반 오일러 적분 물리 계산 및 정밀 바운딩 박스(AABB) 충돌 처리"
                    why = "타이틀 화면 구축 후 게임의 핵심 조작인 캐릭터 물리 및 점프 조작감을 완벽히 구현하여 조작 내실을 다집니다."
                elif not any(k in c for c in comps for k in ["enemy", "obstacle", "goomba", "hazard"]):
                    title = "적 몬스터 순찰 및 피격/충돌 감지 시스템"
                    what = [
                        "좌우 순찰 AI 및 지형 경계 감지 로직 구현",
                        "플레이어-적 AABB 충돌 판정 및 상단 밟기 공격 로직",
                        "피격 시 넉백(Knockback) 및 일시적 무적 점멸 연출"
                    ]
                    how = "적 엔티티 순찰 상태 머신 및 레이캐스팅/경계 박스 충돌 판정"
                    why = "플레이어 캐릭터가 존재하므로, 게임의 도전 요소인 적 몬스터와 충돌 피격 루프를 구현합니다."
                else:
                    title = "스테이지 스코어링 및 클리어 루프 완성"
                    what = [
                        "코인/아이템 획득 및 실시간 스코어/라이프 HUD 시각화",
                        "스테이지 클리어 골인 지점 도달 시 결과 화면 연동",
                        "로컬 스토리지 최고 기록 영속화 및 원클릭 재시작 루프"
                    ]
                    how = "옵저버 패턴 기반 스코어 이벤트 버스 및 로컬 스토리지 영속화"
                    why = "캐릭터와 적 시스템이 완비되었으므로, 승리/패배 및 점수 기록을 완성하여 완결된 1회 플레이 사이클을 닫습니다."

        # 2. Mobile / PDF Viewer & Editor
        elif domain == "mobile_app" or any(k in p_lower for k in ["pdf", "에디터", "뷰어", "문서"]):
            if reality.is_empty or not any("viewer" in c or "renderer" in c for c in comps):
                title = "코어 PDF 비동기 렌더링 엔진 및 고성능 뷰어 MVP"
                what = [
                    "SAF 파일 열기 연동 및 백그라운드 비트맵 비동기 렌더링 파이프라인 개발",
                    "Compose 기반 양방향 스크롤 및 Pinch-to-Zoom(TransformableState) 구현",
                    "최근 열람 파일 캐시 목록 및 첫 실행 대시보드 UI 구축"
                ]
                how = "Android PdfRenderer + Kotlin Coroutine IO 디스패처 기반 비동기 타일 렌더링"
                why = "디스크에 뷰어가 아직 존재하지 않으므로, 대용량 PDF를 부드럽게 열람할 수 있는 코어 비동기 렌더링 파이프라인을 최우선 구축합니다."
            elif not any("canvas" in c or "annotation" in c for c in comps):
                title = "실시간 주석 캔버스 레이어 및 필기 도구 팔레트"
                what = [
                    "PDF 렌더링 상단 Compose Canvas 오버레이 주석 레이어(펜, 형광펜, 텍스트) 개발",
                    "벡터 Path 모델 기반 무제한 Undo/Redo 상태 관리(ViewModel + StateFlow)",
                    "48dp 터치 타깃 도구 팔레트 및 지우개 모드 구현"
                ]
                how = "Compose PointerInput 감지 및 벡터 스트로크 역변환 좌표계 관리"
                why = "코어 뷰어가 안정화되었으므로, 사용자의 '편집 및 주석' 요구사항을 충족하기 위한 벡터 드로잉 캔버스를 구축합니다."
            elif not any("export" in c or "modifier" in c for c in comps):
                title = "PDF 무손실 합성 및 SAF 내보내기 엔진"
                what = [
                    "사용자 작성 벡터 주석을 원본 PDF 좌표계로 정확히 역산 매핑하는 파이프라인",
                    "android.graphics.pdf.PdfDocument 기반 주석 무손실 합성(Bake)",
                    "SAF URI 저장 및 외부 공유 인텐트 무결점 연동"
                ]
                how = "Matrix 좌표계 역변환 및 원본 PDF 메타데이터 보존 비트맵/벡터 합성 엔진"
                why = "주석 캔버스가 존재하므로, 작성한 주석을 실제 표준 PDF 파일로 영구 합성하여 저장할 수 있는 내보내기 파이프라인을 완성합니다."
            elif not any("pagemanager" in c for c in comps):
                title = "시각적 페이지 관리자(순서 재배치·회전·삭제)"
                what = [
                    "LazyVerticalGrid 기반 페이지 썸네일 그리드 모달 구현",
                    "드래그 앤 드롭 페이지 순서 재배치 및 90도 회전 액션",
                    "특정 페이지 삭제 및 신규 빈 페이지 삽입 후 변경 사항 반영"
                ]
                how = "Compose Reorderable LazyGrid 및 백그라운드 페이지 인덱스 재정렬 로직"
                why = "기본 뷰어와 주석 저장이 완료되었으므로, PDF 문서의 구조를 재편할 수 있는 페이지 조작 도구를 제공합니다."
            elif not any("search" in c for c in comps):
                title = "실시간 본문 텍스트 검색 및 뷰포트 하이라이트"
                what = [
                    "실시간 본문 전문 검색 쿼리 및 비동기 매칭 인덱싱 파이프라인",
                    "검색 일치 단어 뷰포트 하이라이트 오버레이 및 해당 위치 자동 스크롤",
                    "이전/다음 일치 항목 순환 이동 및 일치 카운터 배지"
                ]
                how = "코루틴 비동기 텍스트 인덱서 및 Canvas 하이라이트 경계 박스 렌더링"
                why = "뷰어, 주석, 페이지 편집 기능이 이미 디스크에 완성되어 있으므로, 사용자가 문서 내 특정 단어를 즉시 찾을 수 있는 본문 검색 엔진을 구축합니다."
            elif not any(k in c for c in comps for k in ["cache", "oom", "twotier"]):
                title = "초고속 메모리 캐시 풀 및 OOM 선제적 방어 하드닝"
                what = [
                    "LruCache 비트맵 메모리 풀 및 디스크 2차 캐시 최적화",
                    "대용량 문서 스크롤 시 프레임 드롭 제로화 및 메모리 누수 방어",
                    "전 기능 E2E 회귀 테스트 100% 무결점 통과"
                ]
                how = "Two-Tier LruCache 메모리 관리 및 Android LeakCanary 회귀 방어"
                why = "모든 핵심 기능이 구현되었으므로, 상용 앱 수준의 안정성과 메모리 무결성을 위한 최적화 하드닝을 진행합니다."
            elif not any(k in c for c in comps for k in ["password", "security", "cipher", "lock", "암호"]):
                title = "PDF 보안 문서 암호화 및 비밀번호 잠금 해제"
                what = [
                    "표준 PDF 암호화 문서 감지 및 직관적인 비밀번호 입력 다이얼로그",
                    "올바른 비밀번호 인증 시 문서 복호화 및 세션 뷰포트 언락 파이프라인",
                    "비밀번호 설정 신규 저장 및 3회 연속 오입력 시 브루트포스 방어"
                ]
                how = "Android PdfRenderer 비밀번호 전달 및 표준 PDF AES 암호화 핸들러 연동"
                why = "뷰어, 편집, 캐시 최적화가 완성되었으므로, 기밀/보안 문서를 안전하게 보호하고 열람할 수 있는 암호화 및 잠금 해제 시스템을 구축합니다."
            elif not any(k in c for c in comps for k in ["signature", "stamp", "서명", "도장"]):
                title = "디지털 서명(Signature) 날인 및 직인 도장 합성"
                what = [
                    "전용 터치 패드 기반 손글씨 서명/직인 캡처 및 벡터 곡선 스무딩",
                    "서명 위치 드래그 앤 드롭 배치 및 크기 조절/회전 조작 인터랙션",
                    "원본 문서 원하는 페이지 좌표에 무손실 투명 직인 영구 날인"
                ]
                how = "베지어 곡선 스플라인 렌더링 및 PDF 레이어 합성 엔진 연동"
                why = "상용 PDF 편집 앱의 킬러 기능인 계약서/공문서 디지털 서명 및 직인 날인 기능을 구현합니다."
            else:
                title = "다크 모드 문서 색상 반전 및 야간 열람 편의성 고도화"
                what = [
                    "PDF 렌더링 비트맵 음각 반전 컬러 매트릭스 필터 파이프라인",
                    "시스템 다크/라이트 테마 자동 동기화 및 뷰어 툴바 고대비 모드",
                    "야간 열람 시 눈부심 방지 세피아/흑백 문서 필터 프리셋"
                ]
                how = "ColorMatrixColorFilter 기반 런타임 하드웨어 가속 이미지 필터링"
                why = "모든 비즈니스 로직과 보안 기능이 완비되었으므로, 어두운 환경에서도 쾌적하게 문서를 읽을 수 있는 사용자 경험 편의성을 극대화합니다."

        # 3. SaaS / Web / CLI / Other
        else:
            if reality.is_empty:
                title = "코어 아키텍처 및 기본 MVP 구축"
                what = [
                    "프로젝트 기본 구조 및 빌드/패키징 파이프라인 구축",
                    "핵심 데이터 모델 및 기본 엔트리포인트 UI/CLI 구현",
                    "기본 빌드 성공 및 런타임 에러 0건 검증"
                ]
                how = "표준 모듈러 아키텍처 및 클린 코드 상태 관리"
                why = "디스크가 비어있는 초기 상태이므로, 코어 아키텍처 뼈대를 최우선으로 확립합니다."
            elif len(comps) <= 3:
                title = "사용자 핵심 인터랙션 및 상태 관리 고도화"
                what = [
                    "사용자 입력 유효성 검사 및 실시간 상태 동기화",
                    "에러 핸들링 및 직관적인 피드백/토스트 메시지",
                    "로컬 데이터 영속화 및 복원 로직 구현"
                ]
                how = "단방향 데이터 플로우(UDF) 및 로컬 스토리지 연동"
                why = "기본 골격이 마련되었으므로, 사용자의 핵심 조작에 반응하는 상태 관리 및 인터랙션을 구현합니다."
            else:
                title = "상용 배포 품질 하드닝 및 E2E 회귀 검증"
                what = [
                    "경계 조건 및 대용량 데이터 입력 시 크래시 방어",
                    "응답 속도 최적화 및 불필요한 연산 제거",
                    "전체 E2E 시나리오 테스트 100% 무결점 통과"
                ]
                how = "비동기 파이프라인 최적화 및 견고한 테스트 러너 연동"
                why = "주요 기능이 디스크에 구비되었으므로, 실환경 배포를 위한 상용 안정성 하드닝을 완성합니다."

        completed_label = ", ".join(reality.completed_milestones) if reality.completed_milestones else "초기 상태"
        delta_summary = f"디스크 실사 분석 기반 결손 [{title}] 타깃팅 (이전 완료: {completed_label})"
        comp_preview = ", ".join(sorted(list(comps))[:10]) if comps else "없음"
        thinking_process = (
            f"1. [Observation & Ground Truth] (Base Invariance Check): 직전 마일스톤 [{completed_label}]의 코어 메커니즘 훼손 방지 및 통째 갈아엎기(Scrap & Rewrite) 엄격 배제. 디스크 실사 {reality.tech_stack}, 소스 {len(reality.source_files)}개, 기구현 컴포넌트 [{comp_preview}] 확인.\n"
            f"2. [Delta & Blocker Analysis] (Quality State & Gap Diagnosis): Quality State Store 실측치 분석 기반 기완료 [{completed_label}] 대비 다음 결손 품질 [{title}] 식별.\n"
            f"3. [Single-Action Scope Guard] (Surgical Edit Mandate): 부수적 오버엔지니어링 철저 배제. 기존 파일 100% 보존 하에 '{title}' 대상 함수/수식 계수/네이밍만 외과수술식 정밀 수정.\n"
            f"4. [Verification Contract] (Quality Contracts): 수용 기준 {len(what)}개 및 기계적/불리언 QC 계약 수립 완료."
        )

        return cls._build_heuristic_5w1h_plan(
            milestone_title=title,
            milestone_version=target_version,
            tasks=what,
            domain=domain,
            workspace=workspace,
            user_prompt=f"사용자 원본 목표: {root_user_goal}\n\n{why}",
            macro_memory_text=macro_memory_text,
            delta_analysis=delta_summary,
            thinking_process=thinking_process
        )

    @classmethod
    async def plan_adaptive_next_milestone_async(
        cls,
        root_user_goal: str,
        workspace: str,
        domain: Optional[str] = None,
        macro_memory_text: str = "",
        current_completed_title: Optional[str] = None
    ) -> Milestone5W1HPlan:
        """
        [Adaptive Codebase-Grounded Dynamic Planning Engine]
        Physically inspects the workspace on disk, compares against root_user_goal,
        and formulates the single next missing capability without duplicate hallucinations.
        """
        from product_planner import AutonomousProductPlanner
        dom = domain or AutonomousProductPlanner.detect_domain(root_user_goal, workspace)
        reality = cls.inspect_codebase_reality(workspace)
        target_version = cls._compute_next_version(reality)

        # Baseline heuristic
        heuristic_plan = cls._build_heuristic_adaptive_plan(
            root_user_goal=root_user_goal,
            reality=reality,
            domain=dom,
            target_version=target_version,
            workspace=workspace,
            macro_memory_text=macro_memory_text
        )

        completed_str = ", ".join(reality.completed_milestones) if reality.completed_milestones else "없음 (최초 마일스톤)"
        comps_str = ", ".join(reality.implemented_components[:25]) if reality.implemented_components else "없음 (초기 상태)"

        # Fetch Ground-Truth Quality State Store for closed-loop closed-delta feedback
        quality_state_summary = QualityStateManager.format_state_summary_for_commander(workspace)

        planning_query = (
            f"You are [👑 Commander], Chief Strategic Product Architect for an autonomous multi-agent engineering team.\n"
            f"The user has commissioned an end-to-end software project with this immutable North Star goal:\n"
            f"\"{root_user_goal}\"\n\n"
            f"[AUTHENTIC CODEBASE REALITY ON DISK]:\n"
            f"{reality.summary_text}\n\n"
            f"[ALREADY IMPLEMENTED COMPONENTS ON DISK]:\n"
            f"{comps_str}\n\n"
            f"[COMPLETED MILESTONES SO FAR]:\n"
            f"{completed_str}\n\n"
            f"{quality_state_summary}\n\n"
            f"[MACRO CYCLE HISTORY]:\n"
            f"{macro_memory_text[:1200] if macro_memory_text else 'None'}\n\n"
            f"[TARGET MILESTONE VERSION]: {target_version}\n"
            f"[PROJECT DOMAIN]: {dom}\n\n"
            f"[CRITICAL COMMANDER PLANNING MANDATES]\n"
            f"1. ATOMIC SINGLE-CAPABILITY & SURGICAL REFINE (원자적 단일 마일스톤 & 정밀 수정): Plan EXACTLY ONE cohesive, missing capability needed for \"{root_user_goal}\". "
            f"NEVER scrap and rewrite existing functional code (통째 재작성 엄격 금지)! Treat existing code as a verified draft and plan targeted in-place refinements (Amend/Tune/Wrap).\n"
            f"2. CLOSED-LOOP QUALITY HARVESTING (결손 품질 표적 수렴): Examine the Quality State Store above. Focus this milestone on closing the worst quality gap (Value x Risk x Cost) — whether Gameplay escalation, Visual polish, UX feedback, or Architecture decoupling — without regressing existing ACs.\n"
            f"3. ZERO DUPLICATION (중복 기획 엄금): Components and files already implemented on disk ({comps_str}) "
            f"MUST NOT be re-planned or duplicated! Identify the single most logical UN-IMPLEMENTED feature or hardening step.\n"
            f"4. SUBSTANCE OVER BREADTH (내실 우선 헌법): Zero-stub, zero-mock, real algorithms and edge-case handling. Reject shallow stubs.\n"
            f"5. QUALITY CONTRACT (원자적 기계 검증식): Specify 3~5 concrete boolean/mechanically verifiable QC rules for this milestone.\n"
            f"6. PHYSICAL GROUNDING: Base all target file paths ('who_where') on the actual directories and files that exist in the workspace.\n"
            f"7. 5-STAGE LAYERED REFINEMENT PROTOCOL (5단계 초안 감수 & 수술식 개선 헌법):\n"
            f"   Before the JSON output, you MUST provide a structured <thinking>...</thinking> block using this 5-step template:\n"
            f"   <thinking>\n"
            f"   1. Base Invariance & Regression Check (기존 코어 무결성 및 하위 호환성 확인): 직전 턴에서 검증된 핵심 메커니즘(예: 이동, 생성, 충돌, 루프)을 파괴하지 않고 보존하는지 검증. 통째 갈아엎기(Scrap & Rewrite) 엄격 배제.\n"
            f"   2. Quality State & Gap Diagnosis (품질 저장소 결손 진단): Quality State Store(Gameplay, Visual, UX, Performance, Architecture)의 정량 실측치와 증분 Delta를 확인하여 이번 회차에 타깃팅할 최우선 결핍 영역 진단.\n"
            f"   3. Idea Convergence & Surgical Edit (발산 수렴 및 외과수술식 정밀 타격): 전체 파일 재작성을 금지하고, 기존 코드의 어느 파일(who_where), 어느 함수/식별자/수식 계수(Weight)를 정밀 수정할 것인지 핀포인트 확정.\n"
            f"   4. Action Type Partition (1루프 1심화 스코프 가딩): 네이밍/구조 정리(Refactor)와 물리 수치/로직 조정(Tuning)을 분리하여 한 회차에 하나의 '결'만 손보도록 작업 분리.\n"
            f"   5. Verification Contract & Quality Contracts (기계적 검증식 확정): AI 감상평이 아닌 기계적/불리언으로 판정 가능한 3~5개의 Level 1/Level 2 QC 검증식 및 인수 기준(when_acceptance) 확정.\n"
            f"   </thinking>\n\n"
            f"Output the <thinking> block first, followed ONLY by a valid JSON object with the following structure:\n"
            f"{{\n"
            f'  "title": "단일 마일스톤 타이틀 (예: 실시간 본문 텍스트 검색 및 하이라이트 오버레이)",\n'
            f'  "delta_analysis": "직전 상태 대비 이번에 메워야 할 단 하나의 물리적 결손 요약",\n'
            f'  "why": "디스크 실사 기반 구현 당위성 (이미 구현된 것과 대비하여 왜 이번 단일 기능이 필요한지 2문장)",\n'
            f'  "what": ["핵심 구현 항목 1", "예외/안정성 방어 항목 2", "인터랙션/UI 마감 항목 3"],\n'
            f'  "how": "아키텍처 및 구현 기술 방법론",\n'
            f'  "who_where": "작업 주체 및 수정 대상 구체적 파일 경로 (실제 프로젝트 구조 반영)",\n'
            f'  "when_acceptance": ["기계적 인수 기준 1 (빌드/컴파일 통과)", "기계적 인수 기준 2 (스텁/TODO 없는 실구현)", "기계적 인수 기준 3 (실데이터 엣지케이스 통과)", "기계적 인수 기준 4 (Auditor 최종 승인)"],\n'
            f'  "quality_contracts": [\n'
            f'    {{"id": "QC-G-01", "category": "gameplay", "description": "난이도/속도 가중치 수식 검증", "level": 1, "verification_rule": "시간 경과 가속도 계수 적용"}},\n'
            f'    {{"id": "QC-V-01", "category": "visual", "description": "시각 에셋 적용", "level": 2, "verification_rule": "단색 사각형 대신 이미지/스프라이트 렌더링"}}\n'
            f'  ],\n'
            f'  "visual_asset_spec": "시각 에셋 승인 규격 (필요 시 Rule 8 Type A/Type B, 불필요 시 불필요 명시)",\n'
            f'  "visual_approved": true\n'
            f"}}\n"
        )

        plan = heuristic_plan
        try:
            raw_res = await asyncio.wait_for(
                cls._run_raw_llm_query(planning_query, workspace),
                timeout=65.0
            )
            if raw_res:
                extracted_thinking = ""
                m_think = re.search(r"<thinking>(.*?)</thinking>", raw_res, re.DOTALL)
                if m_think:
                    extracted_thinking = m_think.group(1).strip()

                cleaned = raw_res.strip()
                if "```json" in cleaned:
                    m = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                elif "```" in cleaned:
                    m = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                    if m:
                        cleaned = m.group(1).strip()
                s_idx = cleaned.find("{")
                e_idx = cleaned.rfind("}")
                if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                    data = json.loads(cleaned[s_idx:e_idx+1])
                    if isinstance(data, dict) and data.get("title") and data.get("what"):
                        parsed_qcs = data.get("quality_contracts")
                        if not parsed_qcs or not isinstance(parsed_qcs, list):
                            parsed_qcs = heuristic_plan.quality_contracts

                        delta_analysis = str(data.get("delta_analysis", heuristic_plan.delta_analysis)).strip()
                        ai_plan = Milestone5W1HPlan(
                            version=target_version,
                            title=str(data["title"]).strip(),
                            domain=dom,
                            why=str(data.get("why", heuristic_plan.why)).strip(),
                            what=list(data["what"]) if isinstance(data["what"], list) else heuristic_plan.what,
                            how=str(data.get("how", heuristic_plan.how)).strip(),
                            who_where=str(data.get("who_where", heuristic_plan.who_where)).strip(),
                            when_acceptance=list(data.get("when_acceptance", heuristic_plan.when_acceptance)) if isinstance(data.get("when_acceptance"), list) else heuristic_plan.when_acceptance,
                            visual_asset_spec=str(data.get("visual_asset_spec", heuristic_plan.visual_asset_spec)).strip(),
                            visual_approved=bool(data.get("visual_approved", heuristic_plan.visual_approved)),
                            source="ai",
                            quality_contracts=parsed_qcs,
                            delta_analysis=delta_analysis,
                            thinking_process=extracted_thinking or str(data.get("thinking_process", heuristic_plan.thinking_process)).strip()
                        )
                        ai_plan.directive_for_coder = ai_plan.build_directive_for_coder(
                            macro_memory_text=macro_memory_text,
                            user_prompt=f"사용자 원본 목표: {root_user_goal}"
                        )
                        plan = ai_plan
        except Exception as e:
            logger.info(f"Adaptive LLM planning notice (using domain heuristic): {e}")

        # Sync to ROADMAP.md and PRD.md
        try:
            from roadmap_manager import RoadmapManager
            rm = RoadmapManager(workspace)
            rm.append_or_update_adaptive_milestone(
                version=plan.version,
                title=plan.title,
                tasks=plan.what,
                status="IN_PROGRESS"
            )
        except Exception as rm_err:
            logger.debug(f"Adaptive roadmap sync notice: {rm_err}")

        return plan

    @classmethod
    def plan_adaptive_next_milestone(
        cls,
        root_user_goal: str,
        workspace: str,
        macro_memory_text: str = "",
        domain: Optional[str] = None
    ) -> Milestone5W1HPlan:
        """Synchronous entry point for adaptive codebase-grounded milestone planning."""
        from product_planner import AutonomousProductPlanner
        dom = domain or AutonomousProductPlanner.detect_domain(root_user_goal, workspace)
        reality = cls.inspect_codebase_reality(workspace)
        target_version = cls._compute_next_version(reality)
        plan = cls._build_heuristic_adaptive_plan(
            root_user_goal=root_user_goal,
            reality=reality,
            domain=dom,
            target_version=target_version,
            workspace=workspace,
            macro_memory_text=macro_memory_text
        )

        try:
            from roadmap_manager import RoadmapManager
            rm = RoadmapManager(workspace)
            rm.append_or_update_adaptive_milestone(
                version=plan.version,
                title=plan.title,
                tasks=plan.what,
                status="IN_PROGRESS"
            )
        except Exception as rm_err:
            logger.debug(f"Adaptive roadmap sync notice: {rm_err}")

        return plan






