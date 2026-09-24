#!/usr/bin/env python3
"""
model_manager.py

Centralized Real-time Model & Engine Configuration Manager for AGY Agent Platform.
Provides:
1. Global state management (state/global_model_config.json) & real-time synchronization.
2. In-memory hot-updates to config.py and all active channel sessions without restarting.
3. Persistent .env file two-way synchronization.
4. Interactive Discord UI (Buttons, Select Menus, Modals) for:
   - Toggling roles ON/OFF (especially QA1~QA6 for automatic pipeline skipping).
   - Selecting Routers/Engines (Antigravity, OpenRouter, Hermes, Codex, Copilot, Claude).
   - Selecting Model presets or typing custom models (e.g. upstage/solar-pro4).
5. Fast CLI command shortcuts: !model qa2 off, !model qa2 openrouter upstage/solar-pro4, etc.
"""

import os
import sys
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
import discord
from discord.ui import View, Select, Button, Modal, TextInput

logger = logging.getLogger("ModelManager")

# Root directory of agy_agent
BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
CONFIG_FILE = STATE_DIR / "global_model_config.json"
ENV_FILE = BASE_DIR / ".env"

# Supported Routers / Engines and their curated presets
ENGINE_PRESETS: Dict[str, Dict[str, Any]] = {
    "agy": {
        "name": "Google Antigravity (AGY)",
        "icon": "🔵",
        "description": "Google Antigravity 네이티브 통합 개발/검증 엔진",
        "models": [
            ("gemini-3.8-flash-high", "Gemini 3.8 Flash High (초고속 개발/비전)"),
            ("gemini-3.8-flash-tiered", "Gemini 3.8 Flash Tiered (동적 부하분산)"),
            ("gemini-3.7-flash-high", "Gemini 3.7 Flash High (균형잡힌 코딩)"),
            ("gemini-3.7-flash-tiered", "Gemini 3.7 Flash Tiered"),
            ("gemini-3.7-flash-medium", "Gemini 3.7 Flash Middle (경량 관제)"),
            ("gemini-3.1-pro-high", "Gemini 3.1 Pro High (심층 추론/검증)"),
            ("gemini-3.1-pro-low", "Gemini 3.1 Pro Low (경량 추론)"),
            ("gemini-3.6-flash-high", "Gemini 3.6 Flash High"),
            ("gemini-3.6-flash-tiered", "Gemini 3.6 Flash Tiered"),
            ("gemini-3.6-flash-medium", "Gemini 3.6 Flash Medium"),
            ("gemini-3.6-flash-low", "Gemini 3.6 Flash Low"),
            ("gemini-3.5-flash-agent", "Gemini 3.5 Flash High"),
            ("gemini-3-flash", "Gemini 3 Flash (경량/고속)"),
            ("gpt-oss-120b-medium", "GPT-OSS 120B Medium (오픈소스 거대 추론)"),
            ("claude-sonnet-4-6", "Claude Sonnet 4.6 Thinking (고난도 추론)"),
            ("claude-opus-4-6-thinking", "Claude Opus 4.6 Thinking (최상위 추론)"),
        ]
    },
    "hermes": {
        "name": "Hermes Agent (OpenRouter API)",
        "icon": "🪽",
        "description": "OpenRouter API 연동 Hermes 자율 에이전트 (오픈라우터 = Hermes Agent)",
        "models": [
            ("upstage/solar-pro4", "Upstage Solar Pro 4 (한국어/코드 최적화)"),
            ("deepseek/deepseek-v4-flash-0731", "DeepSeek V4 Flash (초고속)"),
            ("meta-llama/llama-3.3-70b-instruct", "Meta Llama 3.3 70B Instruct"),
            ("qwen/qwen3-235b-a22b-2507", "Qwen 3 235B A22B (대형 MoE)"),
            ("inclusionai/ling-3.0-flash-fin:free", "InclusionAI Ling 3.0 Flash (무과금)"),
            ("deepseek/deepseek-chat", "DeepSeek V3 Chat"),
            ("deepseek/deepseek-r1", "DeepSeek R1 (추론 특화)"),
            ("openai/gpt-6-astra", "OpenAI GPT-6 Astra (수석 자문)"),
            ("openai/gpt-4o", "OpenAI GPT-4o"),
            ("anthropic/claude-3.7-sonnet", "Anthropic Claude 3.7 Sonnet"),
            ("anthropic/claude-3.5-sonnet", "Anthropic Claude 3.5 Sonnet"),
            ("qwen/qwen-2.5-coder-32b-instruct", "Qwen 2.5 Coder 32B Instruct"),
            ("mistralai/mistral-large-2411", "Mistral Large 2411"),
        ]
    },
    "codex": {
        "name": "OpenAI Codex CLI",
        "icon": "🟩",
        "description": "OpenAI Codex CLI 헤드리스 엔진",
        "models": [
            ("o3-mini", "OpenAI o3-mini (초고속 추론)"),
            ("gpt-4o", "OpenAI GPT-4o"),
            ("gpt-4.1", "OpenAI GPT-4.1 Preview"),
            ("o1", "OpenAI o1 (심층 추론)"),
            ("gpt-4o-mini", "OpenAI GPT-4o-mini"),
        ]
    },
    "copilot": {
        "name": "GitHub Copilot CLI",
        "icon": "🐙",
        "description": "GitHub Copilot CLI 연동 엔진",
        "models": [
            ("claude-3.7-sonnet", "Claude 3.7 Sonnet (Copilot)"),
            ("claude-3.5-sonnet", "Claude 3.5 Sonnet (Copilot)"),
            ("gpt-4o", "GPT-4o (Copilot)"),
            ("o1", "o1 Preview (Copilot)"),
            ("o3-mini", "o3-mini (Copilot)"),
        ]
    },
    "claude": {
        "name": "Anthropic Claude CLI",
        "icon": "🟪",
        "description": "Anthropic Claude 공식 CLI",
        "models": [
            ("claude-3-7-sonnet-20250219", "Claude 3.7 Sonnet"),
            ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet"),
            ("claude-3-opus-20240229", "Claude 3 Opus"),
            ("claude-3-5-haiku-20241022", "Claude 3.5 Haiku"),
        ]
    },
    "opencode": {
        "name": "OpenCode CLI",
        "icon": "⚡",
        "description": "오픈소스 자율 코딩 에이전트 CLI (멀티 프로바이더 / 세션 지속 / MCP)",
        "models": [
            ("opencode-go/deepseek-v4.1-flash", "DeepSeek V4.1 Flash (초고속 코딩)"),
            ("opencode-go/deepseek-v4-pro", "DeepSeek V4 Pro (심층 추론/설계)"),
            ("opencode-go/glm-5.3-flash", "GLM 5.3 Flash (초고속 추론/코딩)"),
            ("opencode-go/glm-5.3", "GLM 5.3 (고성능 코딩/추론)"),
            ("opencode-go/mimo-v2.6-flash", "MiMo V2.6 Flash (초고속 관제)"),
            ("opencode-go/mimo-v2.6-pro", "MiMo V2.6 Pro (고성능 검증)"),
            ("opencode-go/qwen3.8-flash", "Qwen 3.8 Flash (최신 초고속)"),
            ("opencode-go/qwen3.8-max", "Qwen 3.8 Max (최상위 대형 모델)"),
            ("opencode-go/gpt-6-luna", "GPT-6 Luna (최신 수석 자문/코딩)"),
            ("opencode-go/gpt-5.6-luna", "GPT-5.6 Luna (차세대 추론)"),
            ("opencode-go/kimi-k3", "Kimi K3 (장문 컨텍스트/코딩)"),
            ("opencode-go/kimi-k2.7-code", "Kimi K2.7 Code (코딩 특화)"),
            ("opencode-go/grok-4.7", "Grok 4.7 (xAI 최신 추론)"),
            ("opencode-go/minimax-m3", "MiniMax M3 (대규모 멀티모달)"),
            ("opencode-go/qwen3.7-max", "Qwen 3.7 Max (고성능 추론)"),
            ("opencode-go/qwen3.7-plus", "Qwen 3.7 Plus (균형 코딩)"),
            ("opencode-go/qwen3.6-plus", "Qwen 3.6 Plus (경량 코딩)"),
            ("opencode-go/longcat-2.0", "LongCat 2.0 (초장문 컨텍스트)"),
            ("opencode-go/space-bunny-free", "Space Bunny (무과금/초고속)"),
            ("opencode-go/hy4-preview", "Hunyuan 4 Preview (신규 고성능)"),
            ("opencode-go/muse-spark-1.3-contributor", "Muse Spark 1.3 (창의적 추론)"),
            ("opencode-go/deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("opencode-go/deepseek-v4-flash-vision-exp", "DeepSeek V4 Flash Vision Exp"),
            ("opencode-go/omen-alpha", "Omen Alpha (실험적 추론)"),
            ("opencode-go/mimo-v2.5-pro", "MiMo V2.5 Pro"),
            ("opencode-go/mimo-v2.5", "MiMo V2.5"),
            ("opencode-go/minimax-m2.7", "MiniMax M2.7"),
            ("opencode-go/minimax-m2.5", "MiniMax M2.5"),
            ("opencode-go/kimi-k2.6", "Kimi K2.6"),
            ("opencode-go/glm-5.2", "GLM 5.2"),
            ("opencode-go/glm-5.1", "GLM 5.1"),
            ("opencode-go/grok-4.6", "Grok 4.6"),
            ("opencode-go/hy3", "Hunyuan 3"),
            ("opencode-go/muse-spark-1.2-contributor", "Muse Spark 1.2"),
            ("opencode-go/deepseek-flash", "DeepSeek Flash"),
        ]
    }
}
# OpenRouter is executed by Hermes Agent: 100% synonymous alias
ENGINE_PRESETS["openrouter"] = ENGINE_PRESETS["hermes"]


def get_engine_models(engine: str) -> List[Tuple[str, str]]:
    """
    Returns the list of available model (id, label) presets for the given engine.
    For 'agy', the live /model catalog fetched directly from Antigravity CLI ('agy models')
    is prioritized as the authoritative primary source, with static presets acting only
    as fallback/supplementary.
    """
    canonical_eng = "hermes" if (engine or "").lower().strip() in ("hermes", "openrouter") else (engine or "agy").lower().strip()

    if canonical_eng == "agy":
        try:
            from model_registry import AntigravityModelRegistry
            live_models = AntigravityModelRegistry.get_selectable_models()
            if live_models:
                results: List[Tuple[str, str]] = []
                seen_ids = set()
                # 1. Authoritative live /model catalog comes FIRST
                for m in live_models:
                    mid = m.get("modelId")
                    if mid and mid not in seen_ids:
                        lbl = m.get("label") or mid
                        pct = m.get("remainingPercentage")
                        if pct is not None:
                            rem_str = f" ({int(pct * 100)}%)"
                            if not lbl.endswith("%") and not lbl.endswith("%)"):
                                lbl = f"{lbl}{rem_str}"
                        results.append((mid, lbl))
                        seen_ids.add(mid)

                # 2. Supplement any missing static models as fallback
                static_models = ENGINE_PRESETS.get("agy", {}).get("models", [])
                for mid, lbl in static_models:
                    if mid not in seen_ids:
                        results.append((mid, lbl))
                        seen_ids.add(mid)

                cat_map = {m.get("modelId"): m.get("category", "") for m in live_models}

                # 3. Dynamic Category-based Sort:
                # Top Flagships & Category Locks (Gemini Flagships > GPT-OSS > Claude > Gemini Standard > Gemini Medium/Low)
                def _agy_model_sort_key(item: Tuple[str, str]) -> Tuple[int, int, str]:
                    m_id = item[0].lower()
                    cat = cat_map.get(item[0]) or ""
                    if not cat:
                        if "gpt-oss" in m_id or "gpt_oss" in m_id:
                            cat = "gpt-oss"
                        elif any(k in m_id for k in ("claude", "sonnet", "opus")):
                            cat = "claude"
                        elif "gemini" in m_id:
                            cat = "gemini"
                        else:
                            cat = "external"

                    # Tier 0: Flagships and High-priority Category Locks
                    if m_id == "gemini-3.8-flash-high":
                        return (0, 0, m_id)
                    if m_id == "gemini-3.7-flash-high":
                        return (0, 1, m_id)
                    if m_id == "gemini-3.1-pro-high":
                        return (0, 2, m_id)
                    if cat == "gpt-oss":
                        return (0, 3, m_id)
                    if cat == "claude":
                        sub_order = 0 if "sonnet" in m_id else (1 if "opus" in m_id else 2)
                        return (0, 4 + sub_order, m_id)

                    # Tier 1: Gemini Standard & Tiered High-Capacity models
                    if cat == "gemini":
                        if m_id == "gemini-3-flash":
                            return (1, 0, m_id)
                        if "flash-tiered" in m_id:
                            return (1, 1, m_id)
                        if "flash-high" in m_id:
                            return (1, 2, m_id)
                        if "pro-high" in m_id:
                            return (1, 3, m_id)
                        if "flash-middle" in m_id:
                            return (1, 4, m_id)
                        # Tier 2: Gemini Medium / Low tiers
                        if "flash-medium" in m_id:
                            return (2, 0, m_id)
                        if "flash-low" in m_id:
                            return (2, 1, m_id)
                        if "pro-low" in m_id:
                            return (2, 2, m_id)
                        return (2, 3, m_id)

                    # Tier 3: External or uncategorized models
                    return (3, 0, m_id)

                results.sort(key=_agy_model_sort_key)
                return results
        except Exception as e:
            logger.debug(f"Could not load dynamic Antigravity models: {e}")

    if canonical_eng == "opencode":
        # [🔄 AGY 패턴 준수] OpenCode 실시간 프로바이더/모델 카탈로그를 우선 사용하고,
        # 조회 실패 시 정적 프리셋으로 폴백합니다.
        try:
            from engine_manager import EngineManager
            adapter = EngineManager.get_adapter("opencode")
            live_models = list(adapter.supported_models) if adapter else []
        except Exception as e:
            logger.debug(f"Could not load dynamic OpenCode models: {e}")
            live_models = []
        if live_models:
            preset_list = ENGINE_PRESETS.get("opencode", {}).get("models", [])
            label_map = {mid: lbl for mid, lbl in preset_list}
            order_map = {mid: idx for idx, (mid, _) in enumerate(preset_list)}
            # 프리셋 정렬 순서를 우선 존중하여 정렬
            sorted_live = sorted(live_models, key=lambda m: (order_map.get(m, 999), m))
            return [(m, label_map.get(m, m.replace("opencode-go/", "").title())) for m in sorted_live]

    return list(ENGINE_PRESETS.get(canonical_eng, {}).get("models", []))


# ==============================================================================
# QA Persona (Job/Role Specialization) Definitions
# ==============================================================================
QA_PERSONAS: Dict[str, Dict[str, str]] = {
    "general": {
        "id": "general",
        "name": "기본값 (종합 품질 검증관)",
        "icon": "🕵️",
        "short_desc": "최선 대안 평가, 요구사항/품질/안정성 종합 심의",
        "badge": "🕵️ 기본 종합",
        "focus_prompt": (
            "1. [최선의 선택인가? 종합 설계 가치 심의] Coder가 작성한 코드가 단순히 '돌아간다/빌드된다'는 수준을 넘어, 사용자 요구를 가장 우아하고 효과적으로 실현한 최선의 설계인가? 더 나은 구현이나 직관적인 UX/CLI 대안은 없는가?\n"
            "2. [사용자 체감 가치 & 조작 동선 검증] 사용자가 실제 결과물을 마주했을 때 즉각 체감할 수 있는 에러 메시지의 명확성, 로딩/진행 피드백, 빈 화면 가이드 등 Coder가 간과한 사용성 디테일 적극 보강 지시\n"
            "3. [Coder 설계 맹점 색출 & 구체적 대안 제시] Coder가 미처 생각하지 못한 파라미터 유효성 검증, 타입 안정성, 자원 해제 누락, 결함 가능성을 파고들어 구체적 개선 스펙 제시\n"
            "4. [모킹/가짜 코드 원천 차단] 껍데기 함수, 단순 주석, 이전 라운드 재탕 등 눈속임을 철저히 색출하고 실제 동작하는 프로덕션 비즈니스 로직 구현 확인\n"
            "5. [단순 존재 승인 엄금 (Rubber-Stamping 절대 금지)] 기본값이라고 해서 단순히 '어 코드 있네. 잘했어 통과'식의 기계적 승인은 직무유기임. 종합 품질과 설계 수준이 미달하면 과감히 [STATUS: REJECTED]를 선언하고 Coder를 트레이닝/견인할 것"
        )
    },
    "designer": {
        "id": "designer",
        "name": "UI/UX 디자이너 (프론트엔드)",
        "icon": "🎨",
        "short_desc": "시각적 주목도, 폰트/타이포 밸런스, 나노바나나 에셋 제안",
        "badge": "🎨 UI/UX 디자이너",
        "focus_prompt": (
            "1. [최선의 선택인가? 시각적 주목도 & 이목 집중] 단순히 돌아가는 페이지를 넘어, 이 화면이 사용자의 이목을 집중시킬 만큼 매력적이고 완성도 높은 최선의 디자인인가? 더 나은 시각적 대안은 없는가? 밋밋하고 평범한 텍스트 박스로 방치되지 않았는가?\n"
            "2. [타이포그래피 & 폰트 밸런스] 폰트가 촌스럽거나 조화롭지 않은가? 글자가 너무 작아 가독성을 해치거나 너무 비대하여 레이아웃 균형을 깨뜨리지 않는가? (폰트 크기/굵기/자간/행간 계층 구조 심의)\n"
            "3. [나노바나나(#nano-banana-image) 비주얼 에셋 적극 요구] 단순 텍스트나 사각형 대신 NanoBanana Hub를 통한 고품질 일러스트, 아이콘, 엠블럼, 배경 에셋을 생성/배치하여 완성도를 극대화하도록 구체적인 비주얼 대안을 제안/지시\n"
            "4. [모바일(390px)~데스크톱(1280px) 반응형 & 터치] 48dp 터치 타깃, 가로 스크롤 방어, 여백(Padding/Margin) 밸런스, 컬러 하모니 및 명도 대비율\n"
            "5. [마이크로 인터랙션 & 상태 감성] 로딩 스피너/스켈레톤, 호버/액티브 피드백, 빈 화면(Empty State) 디자인 등 Coder가 미처 생각지 못한 UI/UX 디테일 제시\n"
            "6. [단순 존재 승인 엄금] 단순히 '페이지 만들었네. 잘했어 통과'식의 기계적 승인 절대 금지. 디자이너 관점에서 심미성과 시각적 완성도가 부족하다면 단호히 [STATUS: REJECTED] 선언하고 구체적 스타일/에셋 가이드 제시"
        )
    },
    "security": {
        "id": "security",
        "name": "보안 전문가 (취약점 & AppSec)",
        "icon": "🛡️",
        "short_desc": "심층 방어, 공격 표면 최소화, 인젝션/탈출 차단",
        "badge": "🛡️ 보안 전문가",
        "focus_prompt": (
            "1. [최선의 보안 설계인가? 공격자 관점(Attacker Mindset) 침투 심의] 단순히 '에러가 안 난다'를 넘어, 악의적인 해커가 요청을 보냈을 때 우회하거나 시스템 권한을 탈취할 빈틈이 전혀 없는 최선의 방어 구조인가?\n"
            "2. [인젝션 및 경로 탈출 원천 차단] 쉘 명령어 인젝션(`run_command`, `subprocess`, `os.system` 오용), 디렉터리 경로 탈출(`../` Path Traversal), SQL/템플릿 인젝션 위험을 전수 색출하고 화이트리스트 검증 강제\n"
            "3. [민감정보 & 최소 권한 원칙(Least Privilege)] API 키/토큰/비밀번호 하드코딩 차단, 로그 내 개인정보/자격증명 유출 방지, 파일/프로세스 실행 권한의 과도한 부여 차단\n"
            "4. [보안 아키텍처 대안 제시] Coder가 보안을 고려하지 않고 짠 얕은 로직에 대해 안전한 토큰 검증, 암호화, 샌드박싱 등 Coder가 미처 생각지 못한 보안 설계 대안을 구체적으로 제시\n"
            "5. [단순 동작 승인 엄금] 기능이 잘 돌아가더라도 보안 취약점이나 안전하지 않은 관행이 남아 있다면 절대 통과시키지 말고 단호히 [STATUS: REJECTED] 선언"
        )
    },
    "chaos": {
        "id": "chaos",
        "name": "카오스 테스터 (극한 엣지케이스)",
        "icon": "💥",
        "short_desc": "회복 탄력성, 우아한 장애 복구, 경합/경계값 방어",
        "badge": "💥 카오스 테스터",
        "focus_prompt": (
            "1. [최선의 복원력인가? 극한 카오스 환경 심의] 해피 패스(정상 흐름)만 믿고 짠 코드가 극한의 지연, 네트워크 단절, 프로세스 강제 종료 시에도 우아하게 복구(Graceful Degradation)되는가? 더 견고한 자가 복구 대안은 없는가?\n"
            "2. [동시성 & 비동기 경합(Race Condition)] 비동기 이벤트의 도착 순서 역전, 중복 클릭/요청, 데드락, 이벤트 루프 차단 위험을 집요하게 공격하고 원자적(Atomic) 락/디바운스 대안 제시\n"
            "3. [극한 경계값(Boundary) 폭격] Empty/Null/None, 음수, NaN, 수십 MB 초대형 페이로드, 이모지/특수문자 폭탄 등 Coder가 '설마' 하고 넘긴 엣지 케이스를 파고들어 안전한 방어 로직 강제\n"
            "4. [타임아웃 & 서킷 브레이커 설계 대안 제시] 외부 API나 서브프로세스가 응답하지 않을 때 무한 대기(Hang)에 빠지지 않도록 지수 백오프, 타임아웃, 폴백(Fallback) 설계를 구체적으로 지시\n"
            "5. [단순 동작 승인 엄금] 정상 입력에서만 돌아간다고 승인하지 말고, 극한의 악조건에서 뻗는 코드는 가차 없이 [STATUS: REJECTED] 선언"
        )
    },
    "sre": {
        "id": "sre",
        "name": "성능 & SRE 엔지니어 (시스템 안정성)",
        "icon": "⚡",
        "short_desc": "자원 최적화, 장기 실행 안정성, 메모리 누수 방어",
        "badge": "⚡ 성능 & SRE",
        "focus_prompt": (
            "1. [최선의 고성능·안정성 설계인가? 2~3일 장기 가동 심의] 수천 수만 회의 반복 요청과 며칠간의 무인 장기 가동 시에도 리소스 고갈 없이 버티는 최선의 엔지니어링 아키텍처인가?\n"
            "2. [알고리즘 비효율 & 블로킹 I/O 제거] O(N^2) 비효율 중첩 루프, 불필요한 반복 디스크 I/O, 동기 네트워크 블로킹을 색출하고 비동기 배치(Batch), 인덱싱(Map/Set), 캐싱 대안 제시\n"
            "3. [메모리/소켓/프로세스 누수 원천 차단] 닫히지 않은 파일 디스크립터, 미해제 소켓 연결, 전역 컬렉션 누적 릭, 좀비 서브프로세스를 정밀 점검하고 컨텍스트 매니저(`try-finally`, `with`) 필수화\n"
            "4. [부하 분산 & 관측 가능성(Telemetry)] CPU 100% 스파이크 방지, 비동기 큐 도입, 지표 수집 및 헬스체크 등 Coder가 미처 생각지 못한 대규모 운영 설계를 제안\n"
            "5. [단순 동작 승인 엄금] 1회성 테스트만 통과했다고 승인하지 말고, 누수나 장기 가동 위험이 있다면 단호히 [STATUS: REJECTED] 선언"
        )
    },
    "architect": {
        "id": "architect",
        "name": "수석 아키텍트 (PO 요구사항 감사)",
        "icon": "🏛️",
        "short_desc": "최적 모듈 설계, 결합도 최소화, 미래 기술 부채 방지",
        "badge": "🏛️ 수석 아키텍트",
        "focus_prompt": (
            "1. [최선의 구조적 설계인가? 1년 뒤 유지보수성 심의] 기능만 급조한 스파게티/하드코딩이 아닌, 모듈 간 결합도가 낮고(Decoupled) 향후 변경에 유연하게 대응할 수 있는 최선의 아키텍처인가?\n"
            "2. [단일 책임 원칙(SRP) & 계층 분리] 한 모듈/파일에 비즈니스 로직, 데이터 접근, UI가 뒤섞인 강결합을 비판하고, 깔끔한 레이어드/클린 아키텍처 분리 대안 제시\n"
            "3. [추상화 & 확장성 패턴] 인터페이스 추상화, 팩토리, 의존성 주입(DI) 등을 적용하여 향후 엔진/엔드포인트 교체 시 기존 코드를 수정하지 않아도 되는 OCP 구조 제시\n"
            "4. [기술 부채 원천 거부 & Coder 설계 견인] Coder가 단기적 땜질로 얼버무리려는 얕은 설계를 엄단하고, 장기적 코드베이스의 질서와 패턴을 정립하는 고수준 설계 지침 전달\n"
            "5. [단순 동작 승인 엄금] 단순히 돌아간다고 해서 누더기 설계를 승인하지 말고, 아키텍처 품질이 미달하면 과감히 [STATUS: REJECTED] 선언"
        )
    }
}


DEFAULT_ROLES: Dict[str, Dict[str, Any]] = {
    "coder": {
        "name": "Coder (개발)",
        "role_key": "coder",
        "icon": "👨‍💻",
        "enabled": True,
        "engine": "opencode",
        "model": "opencode-go/deepseek-v4.1-flash",
        "env_model_key": "CODER_MODEL",
        "env_engine_key": "DEFAULT_AI_ENGINE",
        "persona": "general",
        "description": "다중 파일 생성, 반응형 UI 조립, 컴파일/빌드/배포 완결"
    },
    "qa1": {
        "name": "qa1",
        "role_key": "qa1",
        "icon": "🕵️",
        "enabled": True,
        "engine": "opencode",
        "model": "opencode-go/deepseek-v4.1-flash",
        "env_model_key": "QA_MODEL",
        "env_engine_key": None,
        "persona": "general",
        "description": "Playwright 4대 기기 뷰포트 & MCP 도구 물리 단언 및 코드 리뷰"
    },
    "qa2": {
        "name": "qa2",
        "role_key": "qa2",
        "icon": "🕵️‍♂️",
        "enabled": True,
        "engine": "agy",
        "model": "gemini-3.8-flash-high",
        "env_model_key": "QA2_MODEL",
        "env_engine_key": "QA2_ENGINE",
        "index": 2,
        "persona": "designer",
        "description": "Hermes Agent (OpenRouter) 기반 한국어/코드 특화 교차 검증"
    },
    "qa3": {
        "name": "qa3",
        "role_key": "qa3",
        "icon": "🕵️‍♂️",
        "enabled": True,
        "engine": "opencode",
        "model": "opencode-go/mimo-v2.6-flash",
        "env_model_key": "QA3_MODEL",
        "env_engine_key": "QA3_ENGINE",
        "index": 3,
        "persona": "chaos",
        "description": "초고속 코드 엣지케이스 검증"
    },
    "qa4": {
        "name": "qa4",
        "role_key": "qa4",
        "icon": "🕵️‍♂️",
        "enabled": True,
        "engine": "agy",
        "model": "gpt-oss-120b-medium",
        "env_model_key": "QA4_MODEL",
        "env_engine_key": "QA4_ENGINE",
        "index": 4,
        "persona": "security",
        "description": "초대형 MoE 모델 기반 아키텍처 정밀 검증"
    },
    "qa5": {
        "name": "qa5",
        "role_key": "qa5",
        "icon": "🕵️‍♂️",
        "enabled": True,
        "engine": "agy",
        "model": "gemini-3.1-pro-high",
        "env_model_key": "QA5_MODEL",
        "env_engine_key": "QA5_ENGINE",
        "index": 5,
        "persona": "sre",
        "description": "시니어 크로스 검증"
    },
    "qa6": {
        "name": "qa6",
        "role_key": "qa6",
        "icon": "🕵️‍♂️",
        "enabled": True,
        "engine": "agy",
        "model": "gemini-3.8-flash-high",
        "env_model_key": "QA6_MODEL",
        "env_engine_key": "QA6_ENGINE",
        "index": 6,
        "persona": "architect",
        "description": "무과금 경량 크로스 검증"
    },
    "auditor": {
        "name": "Client Auditor (최종 감사)",
        "role_key": "auditor",
        "icon": "⚖️",
        "enabled": True,
        "engine": "opencode",
        "model": "opencode-go/deepseek-v4.1-flash",
        "env_model_key": "AUDITOR_MODEL",
        "env_engine_key": None,
        "persona": "architect",
        "description": "PO 관점 PRD/헌법 100% 전수 대조, 최종 승인 및 차기 로드맵 기획"
    },
    "consultant": {
        "name": "Senior Consultant (수석 자문)",
        "role_key": "consultant",
        "icon": "🧙‍♂️",
        "enabled": False,
        "engine": "opencode",
        "model": "opencode-go/glm-5.3",
        "env_model_key": "CONSULTANT_MODEL",
        "env_engine_key": "CONSULTANT_ENGINE",
        "persona": "general",
        "description": "대규모 분산 아키텍처, 전략 수립 및 심층 컨설팅 자문"
    },
    "commander": {
        "name": "Commander (관제 사령관)",
        "role_key": "commander",
        "icon": "👑",
        "enabled": True,
        "engine": "opencode",
        "model": "opencode-go/mimo-v2.6-flash",
        "env_model_key": "COMMANDER_MODEL",
        "env_engine_key": None,
        "persona": "general",
        "description": "실시간 의도 분석 & Single/Pair/Quad 스마트 배차 관제"
    }
}


_existing_mod = sys.modules.get("model_manager")
_existing_gmm = getattr(_existing_mod, "GlobalModelManager", None) if _existing_mod else None


class GlobalModelManager:
    """
    Central Controller for managing active models, engines, and QA pipeline steps globally.
    Persists configuration to state/global_model_config.json, updates in-memory config,
    and synchronizes with .env file.
    """
    _roles: Dict[str, Dict[str, Any]] = getattr(_existing_gmm, "_roles", {}) if _existing_gmm else {}
    _initialized: bool = getattr(_existing_gmm, "_initialized", False) if _existing_gmm else False
    _on_change_callbacks: List[Any] = getattr(_existing_gmm, "_on_change_callbacks", []) if _existing_gmm else []

    @classmethod
    def register_change_callback(cls, callback):
        """Registers a callback to be notified when model configuration changes."""
        if callback not in cls._on_change_callbacks:
            cls._on_change_callbacks.append(callback)

    @classmethod
    def notify_change(cls):
        """Notifies registered callbacks about model/role configuration updates."""
        for cb in list(cls._on_change_callbacks):
            try:
                import inspect
                if inspect.iscoroutinefunction(cb):
                    try:
                        import asyncio
                        loop = asyncio.get_running_loop()
                        loop.create_task(cb())
                    except RuntimeError:
                        pass
                else:
                    cb()
            except Exception as e:
                logger.debug(f"Model change callback error: {e}")

    @staticmethod
    def format_model_name(model_id: Optional[str]) -> str:
        """
        Formats raw model ID into user-friendly display name.
        e.g. 'gemini-3.8-flash-high' -> 'Gemini 3.8 Flash'
             'gpt-oss-120b-medium' -> 'GPT-OSS 120B'
             'openai/gpt-oss-120b' -> 'GPT-OSS 120B'
             'upstage/solar-pro4' -> 'Solar Pro 4'
             'deepseek/deepseek-v4-flash-0731' -> 'DeepSeek V4 Flash'
             'meta-llama/llama-3.3-70b-instruct' -> 'Meta Llama 3.3 70B'
        """
        if not model_id:
            return "Unknown"
        m = str(model_id).strip()
        m_lower = m.lower()

        if "gpt-oss" in m_lower or "gpt_oss" in m_lower or "oss-120b" in m_lower or "oss 120b" in m_lower or "gptoss" in m_lower:
            return "GPT-OSS 120B"
        if "gemini-3.8-flash" in m_lower or "gemini 3.8" in m_lower or "gemini-3.8" in m_lower:
            return "Gemini 3.8 Flash"
        if "gemini-3.7-flash" in m_lower or "gemini 3.7" in m_lower or "gemini-3.7" in m_lower:
            return "Gemini 3.7 Flash"
        if "gemini-3.6-flash" in m_lower:
            return "Gemini 3.6 Flash"
        if "gemini-3.5-flash" in m_lower:
            return "Gemini 3.5 Flash"
        if "gemini-3.1-pro" in m_lower or "gemini 3.1" in m_lower:
            return "Gemini 3.1 Pro"
        if "gemini-3-flash" in m_lower:
            return "Gemini 3 Flash"
        if "solar-pro" in m_lower:
            return "Solar Pro 4"
        if "deepseek-v4.1" in m_lower or "deepseek v4.1" in m_lower:
            return "DeepSeek V4.1 Pro" if "pro" in m_lower else "DeepSeek V4.1 Flash"
        if "deepseek-v4-pro" in m_lower or "deepseek v4 pro" in m_lower:
            return "DeepSeek V4 Pro"
        if "deepseek-v4" in m_lower:
            return "DeepSeek V4 Flash"
        if "deepseek-r1" in m_lower:
            return "DeepSeek R1"
        if "deepseek-chat" in m_lower or "deepseek-v3" in m_lower:
            return "DeepSeek V3"
        if "qwen3.8-max" in m_lower or "qwen-3.8-max" in m_lower:
            return "Qwen 3.8 Max"
        if "qwen3.8" in m_lower or "qwen-3.8" in m_lower:
            return "Qwen 3.8 Flash"
        if "qwen3.7-max" in m_lower:
            return "Qwen 3.7 Max"
        if "qwen3.7" in m_lower:
            return "Qwen 3.7 Plus"
        if "qwen3-235b" in m_lower or "qwen-3" in m_lower:
            return "Qwen 3 235B"
        if "glm-5.3-flash" in m_lower:
            return "GLM 5.3 Flash"
        if "glm-5.3" in m_lower:
            return "GLM 5.3"
        if "mimo-v2.6-flash" in m_lower:
            return "MiMo 2.6 Flash"
        if "mimo-v2.6-pro" in m_lower:
            return "MiMo 2.6 Pro"
        if "gpt-6-luna" in m_lower:
            return "GPT-6 Luna"
        if "gpt-5.6-luna" in m_lower:
            return "GPT-5.6 Luna"
        if "kimi-k2.7" in m_lower:
            return "Kimi K2.7 Code"
        if "kimi-k3" in m_lower:
            return "Kimi K3"
        if "longcat" in m_lower:
            return "LongCat 2.0"
        if "space-bunny" in m_lower:
            return "Space Bunny"
        if "hy4" in m_lower:
            return "Hunyuan 4 Preview"
        if "llama-3.3-70b" in m_lower:
            return "Meta Llama 3.3 70B"
        if "ling-3.0" in m_lower:
            return "Ling 3.0 Flash"
        if "gpt-6-astra" in m_lower:
            return "GPT-6 Astra"
        if "claude-sonnet-4-6" in m_lower:
            return "Claude Sonnet 4.6"
        if "claude-opus-4-6" in m_lower:
            if "thinking" in m_lower:
                return "Claude Opus 4.6 Thinking"
            return "Claude Opus 4.6"
        if "claude-3.7-sonnet" in m_lower or "claude-3-7-sonnet" in m_lower:
            return "Claude 3.7 Sonnet"
        if "claude-3.5-sonnet" in m_lower or "claude-3-5-sonnet" in m_lower:
            return "Claude 3.5 Sonnet"
        if "o3-mini" in m_lower:
            return "OpenAI o3-mini"
        if "gpt-4o-mini" in m_lower:
            return "GPT-4o Mini"
        if "gpt-4o" in m_lower:
            return "GPT-4o"

        clean_part = m.split("/")[-1] if "/" in m else m
        clean_part = clean_part.replace("-high", "").replace("-middle", "").replace("-medium", "").replace("-low", "").replace("-tiered", "")
        return clean_part.replace("-", " ").replace("_", " ").title()

    @classmethod
    def get_role_model_display(cls, role: str) -> str:
        """Returns the formatted display model name for a given role (coder, qa1, auditor, etc.)."""
        if not cls._initialized:
            cls.initialize()
        role = role.lower().strip()
        cfg = cls._roles.get(role, {})
        model_id = cfg.get("model")
        if not model_id:
            try:
                import config
                if role == "coder":
                    model_id = getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
                elif role in ("qa", "qa1"):
                    model_id = getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
                elif role == "auditor":
                    model_id = getattr(config, "AUDITOR_MODEL", "gemini-3.1-pro-high")
                elif role == "commander":
                    model_id = getattr(config, "COMMANDER_MODEL", "gemini-3.7-flash-medium")
                elif role == "consultant":
                    model_id = getattr(config, "CONSULTANT_MODEL", "openai/gpt-6-astra")
                elif role == "qa2":
                    model_id = getattr(config, "QA2_MODEL", "upstage/solar-pro4")
            except Exception:
                pass
        return cls.format_model_name(model_id)

    @classmethod
    def initialize(cls):
        """Initializes global model config from disk, falling back to .env / defaults."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        # [🔁 Hot-Reload Safety] _roles 딕셔너리는 importlib.reload() 를 넘어 공유되는 객체이므로
        # '새 dict 로 교체'하지 않고 제자리(in-place)에서 갱신한다. 새로 교체하면 bot.py 등
        # 기존 참조가 옛 상태를 계속 들고 있어 모델 변경이 반영되지 않는 stale 버그가 생긴다.
        if not isinstance(cls._roles, dict):
            cls._roles = {}
        cls._roles.clear()
        cls._roles.update({k: dict(v) for k, v in DEFAULT_ROLES.items()})

        # 1. Read existing config file if present
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        for k, data in saved.items():
                            if k in cls._roles and isinstance(data, dict):
                                cls._roles[k].update(data)
            except Exception as e:
                logger.error(f"Failed to load {CONFIG_FILE}: {e}")
        else:
            # 2. Seed from environment variables / .env
            cls._seed_from_env()
            cls.save()

        # Enforce clean 'qa{N}' naming convention for all QA roles & ensure persona populated
        for k, rdata in cls._roles.items():
            if k.startswith("qa"):
                m = re.search(r"qa\s*(\d+)", k.lower())
                rdata["name"] = f"qa{m.group(1)}" if m else k.lower()
            if "persona" not in rdata:
                rdata["persona"] = DEFAULT_ROLES.get(k, {}).get("persona", "general")

        # [🧭 엔진/모델 정합성 자동 교정]
        #   디스크에 저장된 잘못된 조합(예: opencode + gemini-3.8-flash-high)을
        #   해당 엔진의 기본 모델로 정상화하고 .env/런타임 config 까지 동기화한다.
        coherence_repaired = False
        for k, rdata in cls._roles.items():
            eng = rdata.get("engine", "agy")
            mdl = (rdata.get("model") or "").strip()
            if mdl and not cls.is_model_compatible_with_engine(eng, mdl):
                default_model = cls.default_model_for_engine(eng)
                if default_model:
                    logger.warning(
                        f"[ModelCoherence] Loaded role '{k}' has incompatible model '{mdl}' "
                        f"for engine '{eng}' → repaired to '{default_model}'."
                    )
                    rdata["model"] = default_model
                    coherence_repaired = True

        cls._initialized = True
        if coherence_repaired:
            cls.save()
        cls.sync_to_runtime_config()

    @classmethod
    def _seed_from_env(cls):
        """Seeds default configuration from environment variables."""
        import config
        # Coder
        if getattr(config, "CODER_MODEL", None):
            cls._roles["coder"]["model"] = config.CODER_MODEL
        # QA1
        if getattr(config, "QA_MODEL", None):
            cls._roles["qa1"]["model"] = config.QA_MODEL
        # Auditor
        if getattr(config, "AUDITOR_MODEL", None):
            cls._roles["auditor"]["model"] = config.AUDITOR_MODEL
        # Commander
        if getattr(config, "COMMANDER_MODEL", None):
            cls._roles["commander"]["model"] = config.COMMANDER_MODEL
        # Consultant
        if getattr(config, "CONSULTANT_MODEL", None):
            cls._roles["consultant"]["model"] = config.CONSULTANT_MODEL
        if getattr(config, "CONSULTANT_ENGINE", None):
            cls._roles["consultant"]["engine"] = config.CONSULTANT_ENGINE

        # Extended QAs (QA2..QA6)
        for i in range(2, 7):
            q_key = f"qa{i}"
            m_val = os.getenv(f"QA{i}_MODEL")
            e_val = os.getenv(f"QA{i}_ENGINE")
            if m_val and q_key in cls._roles:
                cls._roles[q_key]["model"] = m_val.strip()
            if e_val and q_key in cls._roles:
                cls._roles[q_key]["engine"] = e_val.strip()

    @classmethod
    def get_all_roles(cls) -> Dict[str, Dict[str, Any]]:
        if not cls._initialized:
            cls.initialize()
        return dict(cls._roles)

    @classmethod
    def get_role_config(cls, role: str) -> Dict[str, Any]:
        if not cls._initialized:
            cls.initialize()
        role = role.lower().strip()
        alias_roles = {"qa": "qa1", "개발": "coder", "감사": "auditor"}
        role = alias_roles.get(role, role)
        return dict(cls._roles.get(role, {}))

    @classmethod
    def get_role_model(cls, role: str) -> str:
        """Returns the configured model string for a given role."""
        return cls.get_role_config(role).get("model", "")

    @classmethod
    def get_role_engine(cls, role: str) -> str:
        """Returns the configured engine/router string for a given role."""
        return cls.get_role_config(role).get("engine", "")

    @classmethod
    def get_role_persona(cls, role: str) -> str:
        """Returns the persona ID assigned to the given role (default: 'general')."""
        if not cls._initialized:
            cls.initialize()
        cfg = cls._roles.get(role, {})
        return cfg.get("persona", DEFAULT_ROLES.get(role, {}).get("persona", "general"))

    @classmethod
    def get_role_persona_info(cls, role: str) -> Dict[str, str]:
        """Returns the full persona dictionary for the given role."""
        p_id = cls.get_role_persona(role)
        return QA_PERSONAS.get(p_id, QA_PERSONAS.get("general", {
            "id": "general",
            "name": "기본값 (종합 품질 검증관)",
            "icon": "🕵️",
            "short_desc": "요구사항, 기능 결함, 빌드/테스트 전반 종합 점검",
            "badge": "🕵️ 기본 종합",
            "focus_prompt": "1. 사용자 원본 요구사항의 온전한 반영 및 기능적 무결성 검증"
        }))

    @classmethod
    def set_role_persona(cls, role: str, persona_id: str) -> bool:
        """Sets the persona ID for the given role and persists to disk."""
        if not cls._initialized:
            cls.initialize()
        role = role.lower().strip()
        alias_roles = {
            "qa": "qa1", "solar": "qa2", "deepseek": "qa3", "qwen": "qa4",
            "llama": "qa5", "ling": "qa6", "개발": "coder", "감사": "auditor"
        }
        role = alias_roles.get(role, role)
        if role not in cls._roles:
            return False
        p_id_norm = (persona_id or "general").lower().strip()
        alias_map = {
            "디자이너": "designer", "디자인": "designer", "ui": "designer", "ux": "designer", "frontend": "designer",
            "보안": "security", "보안전문가": "security", "취약점": "security", "appsec": "security",
            "카오스": "chaos", "엣지": "chaos", "엣지케이스": "chaos", "edge": "chaos",
            "성능": "sre", "sre": "sre", "안정성": "sre", "perf": "sre", "performance": "sre",
            "아키텍트": "architect", "기획": "architect", "감사": "architect", "po": "architect", "설계": "architect",
            "기본": "general", "기본값": "general", "종합": "general", "default": "general"
        }
        resolved_p_id = alias_map.get(p_id_norm, p_id_norm)
        if resolved_p_id not in QA_PERSONAS:
            resolved_p_id = "general"
        cls._roles[role]["persona"] = resolved_p_id
        cls.save()
        return True

    @classmethod
    def is_qa_enabled(cls, qa_identifier: Union[int, str]) -> bool:
        """
        Checks if a specific QA step is active.
        If False, the QA step is completely skipped in the pipeline!
        """
        if not cls._initialized:
            cls.initialize()
        if isinstance(qa_identifier, int):
            role_key = f"qa{qa_identifier}"
        else:
            role_key = str(qa_identifier).lower().strip()
            if not role_key.startswith("qa"):
                role_key = f"qa{role_key}"

        cfg = cls._roles.get(role_key)
        if cfg is None:
            return True
        return bool(cfg.get("enabled", True))

    @classmethod
    def set_role_enabled(cls, role: str, enabled: bool) -> bool:
        """Enables or disables a role globally."""
        if not cls._initialized:
            cls.initialize()
        role = role.lower().strip()
        if role in cls._roles:
            cls._roles[role]["enabled"] = bool(enabled)
            cls.save()
            return True
        return False

    # =========================================================================
    # 🧭 엔진/모델 정합성 가드 (Engine-Model Coherence Guard)
    #   - opencode 엔진에는 Gemini/Antigravity 네이티브 모델이 존재하지 않습니다.
    #   - 엔진과 모델이 어긋난 조합(예: OPENCODE + gemini-3.8-flash-high)은
    #     런타임에서 모델이 조용히 무시되거나, 폴백 경로에서 엉뚱한 엔진/쿼터를
    #     소모할 수 있으므로 저장 시점/로드 시점 모두에서 자동 교정합니다.
    # =========================================================================

    @staticmethod
    def model_owner_engine(model: Optional[str]) -> Optional[str]:
        """모델 ID가 소속된 엔진을 판별한다 (정합성 자동 교정용)."""
        m = (model or "").strip().lower()
        if not m:
            return None
        if m.startswith(("opencode-go/", "opencode/")):
            return "opencode"
        if m.startswith(("openrouter/", "anthropic/", "upstage/", "openai/", "qwen/",
                         "deepseek/", "meta-llama/", "inclusionai/", "google/", "x-ai/", "moonshotai/")):
            return "hermes"
        if any(k in m for k in ("gemini", "gpt-oss", "claude-sonnet-4-6", "claude-opus-4-6", "claude-3", "claude-4")):
            return "agy"
        if "/" in m:
            return "hermes"
        return None

    @classmethod
    def is_model_compatible_with_engine(cls, engine: Optional[str], model: Optional[str]) -> bool:
        """주어진 엔진에서 해당 모델을 실제로 실행할 수 있는지 판정한다."""
        eng = (engine or "agy").lower().strip()
        if eng in ("openrouter", "nous"):
            eng = "hermes"
        m = (model or "").strip()
        if not m:
            return True  # 모델 미지정 → 엔진 기본값 사용
        m_low = m.lower()
        owner = cls.model_owner_engine(m)

        if eng == "opencode":
            # OpenCode CLI 는 'provider/model' 형식만 허용하며 Gemini 네이티브 ID는 지원하지 않는다.
            return ("/" in m) and (owner != "agy")
        if eng == "agy":
            # AGY CLI 는 네이티브 모델(Gemini/Claude/GPT-OSS)만 지원한다.
            return owner == "agy" or m_low in ("gpt-oss", "opus", "sonnet")
        if eng == "hermes":
            if m_low.startswith(("opencode-go/", "opencode/")):
                return False
            return "/" in m or owner in ("hermes",)
        # codex/copilot/claude 등은 각 어댑터가 자체 필터링하므로 강제하지 않는다.
        return True

    @classmethod
    def default_model_for_engine(cls, engine: Optional[str]) -> Optional[str]:
        """해당 엔진의 기본(첫 번째) 프리셋 모델을 반환한다."""
        eng = (engine or "agy").lower().strip()
        if eng in ("openrouter", "nous"):
            eng = "hermes"
        try:
            presets = get_engine_models(eng)
        except Exception:
            presets = []
        if not presets:
            presets = list(ENGINE_PRESETS.get(eng, {}).get("models", []))
        return presets[0][0] if presets else None

    @classmethod
    def set_role_engine(cls, role: str, engine: str) -> bool:
        """Sets the router/engine for a role globally.

        [🧭 정합성 가드] 새 엔진이 현재 모델을 지원하지 못하면(예: OpenCode 엔진 + Gemini 모델)
        해당 엔진의 기본 모델로 자동 교체하여 '엔진≠모델' 불일치 상태를 원천 차단합니다.
        """
        if not cls._initialized:
            cls.initialize()
        role = role.lower().strip()
        engine = engine.lower().strip()
        if engine in ("openrouter", "hermes", "nous", "hermes-agent", "hermes_agent"):
            engine = "hermes"
        if role in cls._roles:
            cls._roles[role]["engine"] = engine
            curr_model = cls._roles[role].get("model", "")
            if curr_model and not cls.is_model_compatible_with_engine(engine, curr_model):
                default_model = cls.default_model_for_engine(engine)
                if default_model:
                    logger.warning(
                        f"[ModelCoherence] Role '{role}': engine '{engine}' cannot run model "
                        f"'{curr_model}' → auto-replaced with engine default '{default_model}'."
                    )
                    cls._roles[role]["model"] = default_model
            cls.save()
            return True
        return False

    @classmethod
    def resolve_model_id(cls, engine: str, user_input: str) -> str:
        """
        Smart alias resolver for model strings (e.g. 'gpt oss 120b' -> 'gpt-oss-120b-medium',
        'gemini 3.8' -> 'gemini-3.8-flash-high', 'solar' -> 'upstage/solar-pro4').
        """
        if not user_input:
            return ""
        inp = str(user_input).strip()
        inp_lower = inp.lower().replace("_", "-").replace("  ", " ")
        eng = (engine or "agy").lower().strip()
        if eng in ("openrouter", "nous"):
            eng = "hermes"

        # 1. Exact preset lookup across all engines first
        for check_eng in ("agy", "hermes", "codex", "copilot", "claude", "opencode"):
            for mid, _ in get_engine_models(check_eng):
                if mid.lower() == inp_lower:
                    return mid
                if mid.lower().endswith("/" + inp_lower):
                    return mid

        # 2. Fuzzy alias mapping based on engine
        resolved = cls._resolve_alias_for_engine(eng, inp_lower)
        if resolved:
            return resolved

        # 3. [🧭 Cross-engine fallback] 현재 엔진에 규칙이 없어도 입력이 다른 엔진의 모델을
        #    가리키면 유효한 모델 ID로 해석한다. (예: OpenCode 엔진에서 'gemini 3.8' 입력 →
        #    'gemini-3.8-flash-high' 로 해석되고, 엔진은 정합성 가드가 AGY 로 자동 교정)
        for fallback_eng in ("agy", "hermes", "opencode"):
            if fallback_eng == eng:
                continue
            resolved = cls._resolve_alias_for_engine(fallback_eng, inp_lower)
            if resolved:
                return resolved

        return inp

    @classmethod
    def _resolve_alias_for_engine(cls, eng: str, inp_lower: str) -> Optional[str]:
        """엔진별 퍼지 별칭 해석 규칙 (순수 함수)."""
        if eng == "agy":
            if any(k in inp_lower for k in ("gpt-oss", "gpt oss", "oss-120b", "oss 120b", "gptoss")):
                return "gpt-oss-120b-medium"
            if "3.8" in inp_lower and "tiered" in inp_lower:
                return "gemini-3.8-flash-tiered"
            if "3.8" in inp_lower or "gemini-3.8" in inp_lower or "gemini 3.8" in inp_lower:
                return "gemini-3.8-flash-high"
            if "3.7" in inp_lower and "middle" in inp_lower:
                return "gemini-3.7-flash-medium"
            if "3.7" in inp_lower and "tiered" in inp_lower:
                return "gemini-3.7-flash-tiered"
            if "3.7" in inp_lower or "gemini-3.7" in inp_lower or "gemini 3.7" in inp_lower:
                return "gemini-3.7-flash-high"
            if "3.1" in inp_lower and "low" in inp_lower:
                return "gemini-3.1-pro-low"
            if "3.1" in inp_lower or "gemini-3.1" in inp_lower or "gemini 3.1" in inp_lower:
                return "gemini-3.1-pro-high"
            if "3-flash" in inp_lower or "3 flash" in inp_lower:
                return "gemini-3-flash"
            if "sonnet" in inp_lower:
                return "claude-sonnet-4-6"
            if "opus" in inp_lower:
                return "claude-opus-4-6-thinking"
        elif eng == "hermes":
            if any(k in inp_lower for k in ("gpt-oss", "gpt oss", "oss-120b", "oss 120b")):
                return "openai/gpt-oss-120b"
            if "solar" in inp_lower:
                return "upstage/solar-pro4"
            if "deepseek" in inp_lower and ("v4" in inp_lower or "flash" in inp_lower):
                return "deepseek/deepseek-v4-flash-0731"
            if "deepseek" in inp_lower and "r1" in inp_lower:
                return "deepseek/deepseek-r1"
            if "deepseek" in inp_lower:
                return "deepseek/deepseek-chat"
            if "qwen" in inp_lower and "3" in inp_lower:
                return "qwen/qwen3-235b-a22b-2507"
            if "qwen" in inp_lower:
                return "qwen/qwen-2.5-coder-32b-instruct"
            if "llama" in inp_lower:
                return "meta-llama/llama-3.3-70b-instruct"
            if "ling" in inp_lower:
                return "inclusionai/ling-3.0-flash-fin:free"
            if "gpt-6" in inp_lower or "astra" in inp_lower:
                return "openai/gpt-6-astra"
            if "claude" in inp_lower and "3.7" in inp_lower:
                return "anthropic/claude-3.7-sonnet"
            if "claude" in inp_lower:
                return "anthropic/claude-3.5-sonnet"
        elif eng == "opencode":
            # 1. Qwen 계열 (Qwen 3.8 Flash, Qwen 3.8 Max, Qwen 3.7 등)
            if "qwen" in inp_lower and "3.8" in inp_lower and "max" in inp_lower:
                return "opencode-go/qwen3.8-max"
            if "qwen" in inp_lower and ("3.8" in inp_lower or "flash" in inp_lower):
                return "opencode-go/qwen3.8-flash"
            if "qwen" in inp_lower and "3.7" in inp_lower and "max" in inp_lower:
                return "opencode-go/qwen3.7-max"
            if "qwen" in inp_lower and "3.7" in inp_lower:
                return "opencode-go/qwen3.7-plus"
            if "qwen" in inp_lower and "3.6" in inp_lower:
                return "opencode-go/qwen3.6-plus"
            if "qwen" in inp_lower:
                return "opencode-go/qwen3.8-flash"

            # 2. GLM 계열 (GLM 5.3 Flash, GLM 5.3, GLM 5.2, GLM 5.1)
            if "glm" in inp_lower and "flash" in inp_lower:
                return "opencode-go/glm-5.3-flash"
            if "glm" in inp_lower and "5.2" in inp_lower:
                return "opencode-go/glm-5.2"
            if "glm" in inp_lower and "5.1" in inp_lower:
                return "opencode-go/glm-5.1"
            if "glm" in inp_lower and "5.3" in inp_lower:
                return "opencode-go/glm-5.3"
            if "glm" in inp_lower:
                return "opencode-go/glm-5.3-flash"

            # 3. MiMo 계열 (MiMo V2.6 Flash, MiMo V2.6 Pro, MiMo V2.5)
            if "mimo" in inp_lower and "pro" in inp_lower:
                return "opencode-go/mimo-v2.6-pro"
            if "mimo" in inp_lower and "2.5" in inp_lower:
                return "opencode-go/mimo-v2.5"
            if "mimo" in inp_lower:
                return "opencode-go/mimo-v2.6-flash"

            # 4. GPT Luna 계열 (GPT-6 Luna, GPT-5.6 Luna)
            if ("gpt-6" in inp_lower or "gpt6" in inp_lower) and "luna" in inp_lower:
                return "opencode-go/gpt-6-luna"
            if "gpt-5.6" in inp_lower or ("gpt5" in inp_lower and "luna" in inp_lower):
                return "opencode-go/gpt-5.6-luna"
            if "luna" in inp_lower:
                return "opencode-go/gpt-6-luna"

            # 5. DeepSeek 계열 (DeepSeek V4.1 Flash, DeepSeek V4 Pro, Vision Exp 등)
            if "deepseek" in inp_lower and ("pro" in inp_lower or "r1" in inp_lower):
                return "opencode-go/deepseek-v4-pro"
            if "deepseek" in inp_lower and "vision" in inp_lower:
                return "opencode-go/deepseek-v4-flash-vision-exp"
            if "deepseek" in inp_lower:
                return "opencode-go/deepseek-v4.1-flash"

            # 6. Kimi 계열 (Kimi K3, Kimi K2.7 Code, Kimi K2.6)
            if "kimi" in inp_lower and "code" in inp_lower:
                return "opencode-go/kimi-k2.7-code"
            if "kimi" in inp_lower and "2.6" in inp_lower:
                return "opencode-go/kimi-k2.6"
            if "kimi" in inp_lower:
                return "opencode-go/kimi-k3"

            # 7. Grok 계열 (Grok 4.7, Grok 4.6)
            if "grok" in inp_lower and "4.6" in inp_lower:
                return "opencode-go/grok-4.6"
            if "grok" in inp_lower:
                return "opencode-go/grok-4.7"

            # 8. MiniMax 계열 (MiniMax M3, M2.7, M2.5)
            if "minimax" in inp_lower and "2.7" in inp_lower:
                return "opencode-go/minimax-m2.7"
            if "minimax" in inp_lower and "2.5" in inp_lower:
                return "opencode-go/minimax-m2.5"
            if "minimax" in inp_lower:
                return "opencode-go/minimax-m3"

            # 9. 초장문 / 특화 / 무과금 모델
            if "longcat" in inp_lower:
                return "opencode-go/longcat-2.0"
            if "bunny" in inp_lower or "space" in inp_lower:
                return "opencode-go/space-bunny-free"
            if "hy4" in inp_lower or "hunyuan" in inp_lower:
                return "opencode-go/hy4-preview"
            if "hy3" in inp_lower:
                return "opencode-go/hy3"
            if "muse" in inp_lower:
                return "opencode-go/muse-spark-1.3-contributor"
            if "omen" in inp_lower:
                return "opencode-go/omen-alpha"
        return None

    @classmethod
    def set_role_model(cls, role: str, model: str) -> bool:
        """Sets the model identifier for a role globally with smart normalization and engine auto-harmonization."""
        if not cls._initialized:
            cls.initialize()
        role = role.lower().strip()
        model = model.strip()
        if role in cls._roles and model:
            curr_engine = cls._roles[role].get("engine", "agy")
            resolved_model = cls.resolve_model_id(curr_engine, model)
            cls._roles[role]["model"] = resolved_model

            # [🧭 Auto-harmonize engine based on model type]
            #  • opencode-go/…  → OpenCode 엔진으로 귀속
            #  • Gemini/Claude/GPT-OSS 네이티브 → AGY 엔진으로 귀속 (OpenCode 엔진이어도 교정)
            #  • 그 외 provider/model → Hermes(OpenRouter)
            # 즉, 사용자가 명시적으로 고른 '모델'의 소속 엔진으로 엔진을 정합화하여
            # 'OPENCODE + Gemini' 같은 실행 불가능한 조합이 저장되지 않게 한다.
            owner = cls.model_owner_engine(resolved_model)
            curr_eng = cls._roles[role].get("engine", "agy")
            if owner and owner != curr_eng and not cls.is_model_compatible_with_engine(curr_eng, resolved_model):
                cls._roles[role]["engine"] = owner
                logger.info(
                    f"[ModelCoherence] Role '{role}': model '{resolved_model}' cannot run on engine "
                    f"'{curr_eng}' → engine auto-harmonized to '{owner}'."
                )

            cls.save()
            return True
        return False

    @classmethod
    def set_all_qa_enabled(cls, enabled: bool) -> None:
        """Enables or disables all QA stages (QA1..QA6) at once."""
        if not cls._initialized:
            cls.initialize()
        for k in cls._roles:
            if k.startswith("qa"):
                cls._roles[k]["enabled"] = enabled
        cls.save()

    @classmethod
    def set_only_primary_qa(cls) -> None:
        """Enables QA1 only, disabling QA2..QA6 for high-speed runs."""
        if not cls._initialized:
            cls.initialize()
        cls._roles["qa1"]["enabled"] = True
        for k in cls._roles:
            if k.startswith("qa") and k != "qa1":
                cls._roles[k]["enabled"] = False
        cls.save()

    @classmethod
    def get_active_extended_qas(cls) -> List[Dict[str, Any]]:
        """
        Returns active extended QA configurations (QA2..QA10) where enabled=True.
        Directly consumed by trio_agent_runner and config.get_extended_qa_configs().
        """
        if not cls._initialized:
            cls.initialize()
        active_list: List[Dict[str, Any]] = []
        for i in range(2, 11):
            q_key = f"qa{i}"
            cfg = cls._roles.get(q_key)
            if not cfg or not cfg.get("enabled", True):
                continue
            model = cfg.get("model", "").strip()
            if not model:
                continue
            raw_eng = cfg.get("engine", "hermes").strip()
            engine = "hermes" if raw_eng in ("openrouter", "hermes") else raw_eng
            token = os.getenv(f"DISCORD_AGY_QA{i}_BOT_TOKEN") or os.getenv(f"DISCORD_QA{i}_BOT_TOKEN") or ""
            p_id = cfg.get("persona", DEFAULT_ROLES.get(q_key, {}).get("persona", "general"))
            p_info = QA_PERSONAS.get(p_id, QA_PERSONAS["general"])
            active_list.append({
                "index": i,
                "role": q_key,
                "name": f"qa{i}",
                "model": model,
                "engine": engine,
                "token": token,
                "enabled": True,
                "persona": p_id,
                "persona_name": p_info.get("name", "종합 검증관"),
                "persona_icon": p_info.get("icon", "🕵️")
            })
        return active_list

    @classmethod
    def save(cls):
        """Saves current configuration to JSON state file and synchronizes .env & config.py."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cls._roles, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to write {CONFIG_FILE}: {e}")

        # Synchronize .env file
        cls._sync_to_env_file()
        # Synchronize runtime in-memory config
        cls.sync_to_runtime_config()
        # Trigger real-time dynamic sync callbacks (e.g. Discord bot presences)
        cls.notify_change()

    @classmethod
    def sync_to_runtime_config(cls):
        """Updates in-memory config.py attributes in real time across the application."""
        try:
            import config
            # Coder
            coder_cfg = cls._roles.get("coder", {})
            config.CODER_MODEL = coder_cfg.get("model", getattr(config, "CODER_MODEL", "gemini-3.8-flash-high"))
            if coder_cfg.get("engine"):
                config.DEFAULT_AI_ENGINE = coder_cfg.get("engine")
                config.DEFAULT_ENGINE = coder_cfg.get("engine")

            # QA1
            qa1_cfg = cls._roles.get("qa1", {})
            config.QA_MODEL = qa1_cfg.get("model", getattr(config, "QA_MODEL", "gemini-3.1-pro-high"))

            # Auditor
            auditor_cfg = cls._roles.get("auditor", {})
            config.AUDITOR_MODEL = auditor_cfg.get("model", getattr(config, "AUDITOR_MODEL", "gemini-3.8-flash-high"))

            # Commander
            cmd_cfg = cls._roles.get("commander", {})
            config.COMMANDER_MODEL = cmd_cfg.get("model", getattr(config, "COMMANDER_MODEL", "gemini-3.7-flash-medium"))

            # Consultant
            cons_cfg = cls._roles.get("consultant", {})
            config.CONSULTANT_MODEL = cons_cfg.get("model", getattr(config, "CONSULTANT_MODEL", "openai/gpt-6-astra"))
            cons_eng = cons_cfg.get("engine", "hermes")
            config.CONSULTANT_ENGINE = "hermes" if cons_eng in ("openrouter", "hermes") else cons_eng

            # Extended QAs
            qa2_cfg = cls._roles.get("qa2", {})
            config.QA2_MODEL = qa2_cfg.get("model", getattr(config, "QA2_MODEL", "upstage/solar-pro4"))
            qa2_eng = qa2_cfg.get("engine", "hermes")
            config.QA2_ENGINE = "hermes" if qa2_eng in ("openrouter", "hermes") else qa2_eng
            config.EXTENDED_QA_CONFIGS = cls.get_active_extended_qas()

        except Exception as e:
            logger.error(f"Error syncing to runtime config: {e}")

    @classmethod
    def _sync_to_env_file(cls):
        """Safely updates keys in .env file without destroying other variables or formatting."""
        if not ENV_FILE.exists():
            return
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                content = f.read()

            for role_key, r_cfg in cls._roles.items():
                m_key = r_cfg.get("env_model_key")
                e_key = r_cfg.get("env_engine_key")
                model_val = r_cfg.get("model", "")
                engine_val = r_cfg.get("engine", "")

                if m_key and model_val:
                    pattern = rf"^{re.escape(m_key)}=.*$"
                    if re.search(pattern, content, flags=re.MULTILINE):
                        content = re.sub(pattern, f"{m_key}={model_val}", content, flags=re.MULTILINE)
                    else:
                        content += f"\n{m_key}={model_val}"
                    import os
                    os.environ[m_key] = model_val

                if e_key and engine_val:
                    pattern = rf"^{re.escape(e_key)}=.*$"
                    if re.search(pattern, content, flags=re.MULTILINE):
                        content = re.sub(pattern, f"{e_key}={engine_val}", content, flags=re.MULTILINE)
                    else:
                        content += f"\n{e_key}={engine_val}"
                    import os
                    os.environ[e_key] = engine_val

            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Failed to sync .env file: {e}")

    @classmethod
    def build_dashboard_embed(cls, selected_role: str = "qa2") -> discord.Embed:
        """Builds a rich, visual overview Embed of all roles and active model statuses."""
        if not cls._initialized:
            cls.initialize()

        def _short_model(m: str) -> str:
            """Shorten long model identifiers for display."""
            if "/" in m:
                return m.split("/")[-1][:28]
            return m[:28]

        embed = discord.Embed(
            title="🎛️ 전역 AI 모델 관제 센터",
            description="버튼과 드롭다운으로 모델을 즉시 교체합니다. 변경은 재부팅 없이 실시간 반영됩니다.",
            color=0x4285F4
        )

        # 1. Core Agents - compact single-line format
        core_lines = []
        for rk in ("coder", "auditor", "consultant", "commander"):
            cfg = cls._roles.get(rk, {})
            icon = cfg.get("icon", "🤖")
            name = cfg.get("name", rk)
            st = "🟢" if cfg.get("enabled", True) else "🔴"
            mod = _short_model(cfg.get("model", "unknown"))
            sel_tag = " ← **선택됨**" if rk == selected_role else ""
            core_lines.append(f"{st} {icon} **{name}** · `{mod}`{sel_tag}")

        embed.add_field(
            name="👥 핵심 에이전트",
            value="\n".join(core_lines),
            inline=False
        )

        # 2. QA Squad - show active ones prominently, collapse inactive
        active_lines = []
        inactive_names = []
        for i in range(1, 7):
            rk = f"qa{i}"
            cfg = cls._roles.get(rk, {})
            is_en = cfg.get("enabled", True)
            mod = _short_model(cfg.get("model", "unknown"))
            p_info = cls.get_role_persona_info(rk)
            p_icon = p_info.get("icon", "🕵️")
            sel_tag = " ← **선택됨**" if rk == selected_role else ""

            if is_en:
                active_lines.append(f"🟢 {p_icon} **qa{i}** · `{mod}`{sel_tag}")
            else:
                inactive_names.append(f"qa{i}")

        qa_summary = "\n".join(active_lines)
        if inactive_names:
            qa_summary += f"\n🔴 비활성: {', '.join(inactive_names)} *(자동 건너뜀)*"

        embed.add_field(
            name=f"🕵️ QA 검증 부대 ({len(active_lines)}개 가동)",
            value=qa_summary or "전체 비활성",
            inline=False
        )

        # 3. Selected Role Detail Box
        curr = cls._roles.get(selected_role, {})
        curr_icon = curr.get("icon", "⚙️")
        curr_name = curr.get("name", selected_role)
        curr_status = "🟢 활성" if curr.get("enabled", True) else "🔴 비활성"
        raw_curr_eng = curr.get("engine", "agy").lower()
        curr_eng = "OpenRouter" if raw_curr_eng in ("hermes", "openrouter") else raw_curr_eng.upper()
        curr_mod = curr.get("model", "unknown")
        curr_p_info = cls.get_role_persona_info(selected_role)

        embed.add_field(
            name=f"🎯 선택: {curr_icon} {curr_name}",
            value=(
                f"**상태** {curr_status} · **엔진** `{curr_eng}` · **모델** `{curr_mod}`\n"
                f"**역할** {curr_p_info.get('icon', '💼')} {curr_p_info.get('name', '기본값')}\n\n"
                f"💡 아래 드롭다운과 버튼으로 변경하세요."
            ),
            inline=False
        )

        embed.set_footer(text="Global Model Manager · 변경 즉시 반영")
        return embed


# -----------------------------------------------------------------------------
# Discord UI Components: Select Menus, Buttons & Modal
# -----------------------------------------------------------------------------

async def _safe_edit_view(interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View):
    """
    Safely edits a discord interaction message regardless of whether the interaction
    was already deferred or not, with a reliable fallback to interaction.message.edit.
    """
    try:
        is_done = False
        if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
            res = interaction.response.is_done()
            is_done = (res is True)
        if is_done and hasattr(interaction, "edit_original_response"):
            await interaction.edit_original_response(embed=embed, view=view)
        elif hasattr(interaction, "response") and hasattr(interaction.response, "edit_message"):
            await interaction.response.edit_message(embed=embed, view=view)
    except Exception as e:
        logger.warning(f"Error editing interaction response: {e}")
        try:
            if hasattr(interaction, "message") and interaction.message:
                await interaction.message.edit(embed=embed, view=view)
            elif hasattr(view, "message") and view.message:
                await view.message.edit(embed=embed, view=view)
        except Exception as e2:
            logger.debug(f"Fallback message edit failed: {e2}")


class CustomModelModal(Modal):
    """Modal popup allowing direct text input for any model identifier (e.g. OpenRouter slug)."""
    def __init__(self, selected_role: str, current_model: str, parent_view: 'ModelDashboardView'):
        super().__init__(title="✏️ AI 모델 식별자 직접 지정")
        self.selected_role = selected_role
        self.parent_view = parent_view

        role_cfg = GlobalModelManager.get_role_config(selected_role)
        role_name = role_cfg.get("name", selected_role)

        self.model_input = TextInput(
            label=f"[{role_name}] 모델 식별자",
            placeholder="예: upstage/solar-pro4, deepseek/deepseek-chat",
            default=current_model,
            required=True,
            max_length=120
        )
        self.add_item(self.model_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_model = self.model_input.value.strip()
        if new_model:
            GlobalModelManager.set_role_model(self.selected_role, new_model)
            embed = GlobalModelManager.build_dashboard_embed(self.selected_role)
            self.parent_view.update_components()
            await _safe_edit_view(interaction, embed, self.parent_view)
            try:
                await interaction.followup.send(
                    f"✅ **[{self.selected_role.upper()}]** 모델이 `{new_model}`(으)로 전역 변경되었습니다!",
                    ephemeral=True
                )
            except Exception:
                pass
        else:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("⚠️ 올바른 모델 식별자를 입력해주세요.", ephemeral=True)
                else:
                    await interaction.followup.send("⚠️ 올바른 모델 식별자를 입력해주세요.", ephemeral=True)
            except Exception:
                pass


class RoleSelectDropdown(Select):
    """Dropdown for choosing which agent/role to configure."""
    def __init__(self, selected_role: str):
        roles = GlobalModelManager.get_all_roles()
        options = []
        for rk, cfg in roles.items():
            st_icon = "🟢" if cfg.get("enabled", True) else "🔴"
            raw_e = cfg.get('engine', 'agy').lower()
            eng_label = "HERMES" if raw_e in ("hermes", "openrouter") else raw_e.upper()
            p_info = GlobalModelManager.get_role_persona_info(rk)
            p_badge = f"{p_info.get('icon', '💼')} {p_info.get('name', '기본')[:6]}"
            mod_str = cfg.get('model', '')[:16]
            options.append(
                discord.SelectOption(
                    label=f"{cfg.get('name', rk)[:25]}",
                    value=rk,
                    description=f"{st_icon} {eng_label} | {p_badge} | {mod_str}",
                    emoji=cfg.get("icon", "🤖"),
                    default=(rk == selected_role)
                )
            )
        super().__init__(
            placeholder="🎯 설정할 에이전트(Coder, qa1~qa6, Auditor 등)를 선택하세요...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        view: ModelDashboardView = self.view
        view.selected_role = self.values[0]
        view.update_components()
        embed = GlobalModelManager.build_dashboard_embed(view.selected_role)
        await _safe_edit_view(interaction, embed, view)


class EngineSelectDropdown(Select):
    """Dropdown for choosing the router/engine for the selected role."""
    def __init__(self, selected_role: str):
        role_cfg = GlobalModelManager.get_role_config(selected_role)
        curr_engine = role_cfg.get("engine", "agy").lower()
        if curr_engine in ("openrouter", "hermes"):
            curr_engine = "hermes"

        options = []
        canonical_engines = [
            ("agy", ENGINE_PRESETS["agy"]),
            ("hermes", ENGINE_PRESETS["hermes"]),
            ("codex", ENGINE_PRESETS["codex"]),
            ("copilot", ENGINE_PRESETS["copilot"]),
            ("claude", ENGINE_PRESETS["claude"]),
            ("opencode", ENGINE_PRESETS["opencode"]),
        ]
        for eng_id, info in canonical_engines:
            desc = "OpenRouter API = Hermes Agent (통합 라우터)" if eng_id == "hermes" else f"라우터: {eng_id.upper()}"
            options.append(
                discord.SelectOption(
                    label=f"{info['name']}",
                    value=eng_id,
                    description=desc[:50],
                    emoji=info["icon"],
                    default=(eng_id == curr_engine)
                )
            )
        super().__init__(
            placeholder="🌐 라우터(Antigravity, Hermes/OpenRouter, Codex 등)를 선택하세요...",
            min_values=1,
            max_values=1,
            options=options,
            row=2
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        view: ModelDashboardView = self.view
        new_engine = self.values[0]
        if new_engine in ("openrouter", "hermes"):
            new_engine = "hermes"
        
        # Get current model before switching engine
        role_cfg = GlobalModelManager.get_role_config(view.selected_role)
        curr_model = role_cfg.get("model", "")
        
        GlobalModelManager.set_role_engine(view.selected_role, new_engine)

        # Set default model of new engine if current model doesn't belong to it
        presets = get_engine_models(new_engine)
        if presets:
            preset_values = [p[0] for p in presets]
            if curr_model not in preset_values:
                first_model = presets[0][0]
                GlobalModelManager.set_role_model(view.selected_role, first_model)

        view.update_components()
        embed = GlobalModelManager.build_dashboard_embed(view.selected_role)
        await _safe_edit_view(interaction, embed, view)


class ModelPresetDropdown(Select):
    """Dropdown for choosing preset models for the selected engine."""
    def __init__(self, selected_role: str):
        role_cfg = GlobalModelManager.get_role_config(selected_role)
        curr_engine = role_cfg.get("engine", "agy").lower()
        if curr_engine in ("openrouter", "hermes"):
            curr_engine = "hermes"
        curr_model = role_cfg.get("model", "")

        preset_data = get_engine_models(curr_engine)
        options = []
        found_current = False
        for mid, label in preset_data[:24]:
            is_def = (mid == curr_model)
            if is_def:
                found_current = True
            options.append(
                discord.SelectOption(
                    label=f"{mid[:100]}",
                    value=mid,
                    description=label[:100],
                    default=is_def
                )
            )

        # If current model is custom and not in presets, add it at the top
        if curr_model and not found_current:
            options.insert(0, discord.SelectOption(
                label=f"{curr_model[:75]} (현재 사용자 지정)",
                value=curr_model,
                description="직접 입력된 커스텀 모델",
                default=True
            ))

        disp_eng = "HERMES (OpenRouter)" if curr_engine == "hermes" else curr_engine.upper()
        super().__init__(
            placeholder=f"📦 [{disp_eng}] 모델 프리셋 선택 (클릭)...",
            min_values=1,
            max_values=1,
            options=options[:25],
            row=3
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        view: ModelDashboardView = self.view
        new_model = self.values[0]
        GlobalModelManager.set_role_model(view.selected_role, new_model)
        view.update_components()
        embed = GlobalModelManager.build_dashboard_embed(view.selected_role)
        await _safe_edit_view(interaction, embed, view)


class PersonaSelectDropdown(Select):
    """Dropdown for choosing the job/persona (직업/전문 역할) of the selected agent."""
    def __init__(self, selected_role: str):
        curr_persona = GlobalModelManager.get_role_persona(selected_role)
        options = []
        for p_id, p_info in QA_PERSONAS.items():
            options.append(
                discord.SelectOption(
                    label=p_info["name"][:25],
                    value=p_id,
                    description=p_info["short_desc"][:50],
                    emoji=p_info.get("icon", "💼"),
                    default=(p_id == curr_persona)
                )
            )
        super().__init__(
            placeholder="💼 직업/전문 역할 선택 (기본값, 디자이너, 보안, 카오스 등)...",
            min_values=1,
            max_values=1,
            options=options,
            row=4
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        view: ModelDashboardView = self.view
        chosen_persona = self.values[0]
        GlobalModelManager.set_role_persona(view.selected_role, chosen_persona)
        view.update_components()
        embed = GlobalModelManager.build_dashboard_embed(view.selected_role)
        await _safe_edit_view(interaction, embed, view)


class ModelDashboardView(View):
    """
    Main Interactive View for the !model command.
    """
    def __init__(self, selected_role: str = "qa2", user_id: Optional[int] = None):
        super().__init__(timeout=600)
        self.selected_role = selected_role
        self.user_id = user_id
        self.message: Optional[discord.Message] = None
        self.update_components()

    def update_components(self):
        self.clear_items()
        role_cfg = GlobalModelManager.get_role_config(self.selected_role)
        is_enabled = role_cfg.get("enabled", True)
        curr_model = role_cfg.get("model", "")

        # Row 0: Role Select Dropdown (에이전트 선택)
        self.add_item(RoleSelectDropdown(self.selected_role))

        # Row 1: Action Buttons (최대 5개 버튼)
        # Button 1: Toggle ON/OFF
        btn_toggle = Button(
            label="🔴 비활성화" if is_enabled else "🟢 활성화",
            style=discord.ButtonStyle.danger if is_enabled else discord.ButtonStyle.success,
            row=1,
            custom_id="btn_model_toggle"
        )
        btn_toggle.callback = self._toggle_callback
        self.add_item(btn_toggle)

        # Button 2: Direct Model Input Modal
        btn_modal = Button(
            label="✏️ 모델 직접 입력",
            style=discord.ButtonStyle.primary,
            row=1,
            custom_id="btn_model_modal"
        )
        btn_modal.callback = self._modal_callback
        self.add_item(btn_modal)

        # Button 3: QA Mode Toggle (⚡ qa 전체 ON <-> 🛡️ qa1만 ON)
        all_qa_active = all(GlobalModelManager.is_qa_enabled(i) for i in range(1, 7))
        btn_qa_mode = Button(
            label="🛡️ qa1만 ON" if all_qa_active else "⚡ qa 전체 ON",
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="btn_model_all_qa"
        )
        btn_qa_mode.callback = self._fast_qa_callback if all_qa_active else self._all_qa_callback
        self.add_item(btn_qa_mode)

        # Button 4: Refresh Button
        btn_refresh = Button(
            label="🔄 새로고침",
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="btn_model_refresh"
        )
        btn_refresh.callback = self._refresh_callback
        self.add_item(btn_refresh)

        # Button 5: Close Button
        btn_close = Button(
            label="❌ 닫기",
            style=discord.ButtonStyle.secondary,
            row=1,
            custom_id="btn_model_close"
        )
        btn_close.callback = self._close_callback
        self.add_item(btn_close)

        # Row 2: Engine Select Dropdown (엔진/라우터 선택)
        self.add_item(EngineSelectDropdown(self.selected_role))

        # Row 3: Model Preset Dropdown (모델 프리셋 선택)
        self.add_item(ModelPresetDropdown(self.selected_role))

        # Row 4: Persona Select Dropdown (직업/전문 역할 선택)
        self.add_item(PersonaSelectDropdown(self.selected_role))

    async def _refresh_callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        GlobalModelManager.sync_to_runtime_config()
        self.update_components()
        embed = GlobalModelManager.build_dashboard_embed(self.selected_role)
        await _safe_edit_view(interaction, embed, self)
        try:
            await interaction.followup.send("🔄 **최신 모델 및 환경 설정이 새로고침되었습니다.**", ephemeral=True)
        except Exception:
            pass

    async def _env_status_callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        roles = GlobalModelManager.get_all_roles()
        lines = [
            f"**CODER_MODEL**=`{roles.get('coder', {}).get('model')}` (Engine: `{roles.get('coder', {}).get('engine')}`)",
            f"**QA_MODEL**=`{roles.get('qa1', {}).get('model')}` (Enabled: `{roles.get('qa1', {}).get('enabled')}`)",
            f"**AUDITOR_MODEL**=`{roles.get('auditor', {}).get('model')}`",
            f"**COMMANDER_MODEL**=`{roles.get('commander', {}).get('model')}`",
            f"**CONSULTANT_MODEL**=`{roles.get('consultant', {}).get('model')}` (Engine: `{roles.get('consultant', {}).get('engine')}`)"
        ]
        for i in range(2, 7):
            q_cfg = roles.get(f"qa{i}", {})
            lines.append(f"**qa{i}**: `{q_cfg.get('model')}` [{q_cfg.get('engine').upper()}] - {'🟢 ON' if q_cfg.get('enabled') else '🔴 OFF (Skip)'}")
        
        env_embed = discord.Embed(
            title="📋 전역 환경변수 및 모델 동기화 현황",
            description="\n".join(lines),
            color=0x34A853
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=env_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=env_embed, ephemeral=True)
        except Exception as e:
            logger.debug(f"Failed to send env status embed: {e}")

    async def _toggle_callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        role_cfg = GlobalModelManager.get_role_config(self.selected_role)
        new_state = not role_cfg.get("enabled", True)
        GlobalModelManager.set_role_enabled(self.selected_role, new_state)
        self.update_components()
        embed = GlobalModelManager.build_dashboard_embed(self.selected_role)
        await _safe_edit_view(interaction, embed, self)

    async def _modal_callback(self, interaction: discord.Interaction):
        role_cfg = GlobalModelManager.get_role_config(self.selected_role)
        curr_model = role_cfg.get("model", "")
        modal = CustomModelModal(self.selected_role, curr_model, self)
        await interaction.response.send_modal(modal)

    async def _all_qa_callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        GlobalModelManager.set_all_qa_enabled(True)
        self.update_components()
        embed = GlobalModelManager.build_dashboard_embed(self.selected_role)
        await _safe_edit_view(interaction, embed, self)
        try:
            await interaction.followup.send("✅ **qa1 ~ qa6 전원 활성화 완료!** 6중 교차 검증 파이프라인이 가동됩니다.", ephemeral=True)
        except Exception:
            pass

    async def _fast_qa_callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        GlobalModelManager.set_only_primary_qa()
        self.update_components()
        embed = GlobalModelManager.build_dashboard_embed(self.selected_role)
        await _safe_edit_view(interaction, embed, self)
        try:
            await interaction.followup.send("⚡ **qa1만 가동 모드 적용 완료!** (qa2~qa6 단계 자동 패스로 토큰/시간 절약)", ephemeral=True)
        except Exception:
            pass

    async def _close_callback(self, interaction: discord.Interaction):
        try:
            if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                if not interaction.response.is_done():
                    await interaction.response.defer()
        except Exception:
            pass
        try:
            if interaction.message:
                await interaction.message.delete()
            elif hasattr(self, "message") and self.message:
                await self.message.delete()
        except Exception:
            try:
                if hasattr(interaction, "response") and interaction.response.is_done():
                    await interaction.edit_original_response(content="👋 모델 관제 창이 닫혔습니다.", embed=None, view=None)
                else:
                    await interaction.response.edit_message(content="👋 모델 관제 창이 닫혔습니다.", embed=None, view=None)
            except Exception:
                pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# Initialize singleton immediately upon import
GlobalModelManager.initialize()
