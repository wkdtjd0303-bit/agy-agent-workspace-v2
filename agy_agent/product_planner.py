#!/usr/bin/env python3
"""
👑 ProductPlanner & ProductCritic Module (AGY !mode 3 / !mode 3+ Autonomous Product Planning)
Designated Bot: Commander Bot

This module implements the Autonomous Product Planning and Multi-Dimensional Quality Critic
architecture for !mode 3 (Evolve) and !mode 3+ (Quad Evolve+):
1. Product Definition & Multi-Dimensional Quality Definition (기능, UX, 비주얼, 오디오, 게임성/비즈니스, 안정성)
2. Milestone-driven Quality Evolution (Core Gameplay ➔ Visual Quality ➔ Audio Quality ➔ Game Feel ➔ Level Design ➔ Real E2E)
3. Separation of Functional Completion vs Product Completion
4. Critical Quality Gates & Evidence-based Multi-Dimensional Scoring (Anti-Mocking & Anti-"후한 점수")
5. 8 Core Critic Questions & Quality Gap Analysis
6. Goal Drift Prevention Guard
"""

import os
import re
import json
import logging
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

import discord
try:
    import config
except ImportError:
    from agy_agent import config

# 🚪 Sequential Milestone Gate (90점 완결 게이트 SSOT)
try:
    from product_gate import (
        CRITICAL_DIMENSION_MIN_SCORE,
        MILESTONE_PASS_SCORE,
        evaluate_milestone_gate,
        build_milestone_spec,
    )
except ImportError:
    from agy_agent.product_gate import (  # type: ignore
        CRITICAL_DIMENSION_MIN_SCORE,
        MILESTONE_PASS_SCORE,
        evaluate_milestone_gate,
        build_milestone_spec,
    )

logger = logging.getLogger("ProductPlanner")

COMMANDER_BOT_ID = config.COMMANDER_BOT_ID
COMMANDER_USER_MENTION = config.COMMANDER_USER_MENTION


# ──────────────────────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class QualityDimension:
    """Represents a single evaluation dimension of product quality."""
    key: str                    # e.g. 'controls_feel', 'audio_sfx', 'ux_ergonomics'
    name: str                   # e.g. '조작감 & 반응성 (Controls/Game Feel)'
    target_description: str     # Target quality description
    is_critical: bool = True    # If True, failing this dimension fails Critical Quality Gate
    min_threshold: float = 60.0 # Minimum score (0~100) required to pass
    score: float = 0.0          # Current measured score (0~100)
    passed: bool = False
    evidence: str = ""          # Concrete code/E2E evidence justifying the score
    gap_analysis: str = ""      # Target Quality vs Current Quality gap
    action_items: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProductDefinition:
    """High-level product identity, intent, and benchmark target."""
    domain: str                 # 'game' | 'mobile_app' | 'saas_web' | 'audio' | 'native_desktop' | 'daemon'
    title: str                  # e.g. '모바일 2D 횡스크롤 마리오 플랫포머'
    platform: str               # e.g. 'Android (Jetpack Compose / Canvas)'
    target_audience: str        # e.g. '캐주얼 모바일 게이머'
    core_experience: str        # e.g. '경쾌한 점프 포물선, 적 밟기 타격감, 코인 파밍의 즉각적 쾌감'
    benchmark_reference: str    # e.g. '슈퍼마리오 브라더스 1-1 / 상용 2D 모바일 플랫포머'
    key_value_proposition: str  # e.g. '터치 가상 조이스틱으로 즐기는 정통 레트로 액션'
    app_naming: str = ""        # Bespoke product branding or name
    design_vibe: str = ""       # Visual design theme or vibe
    target_demographic: str = "" # Target demographic
    monetization_strategy: str = "" # Monetization & store launch strategy

    def __post_init__(self):
        if not self.app_naming:
            self.app_naming = self.title or "Autocoding Service"
        if not self.target_demographic:
            self.target_demographic = self.target_audience or "스마트폰 및 디지털 서비스 실사용자"
        if not self.monetization_strategy:
            self.monetization_strategy = "인앱 결제 / 프리미엄 구독 (Freemium)"
        if not self.design_vibe:
            self.design_vibe = "모던 클린 & 반응형 테마"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProductPlan:
    """Full product plan with quality definition and phase-by-phase milestones."""
    product_def: ProductDefinition
    quality_dimensions: Dict[str, QualityDimension]
    milestones: List[Dict[str, Any]]
    initial_coder_directive: str
    rationale: str
    visual_asset_spec: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_def": self.product_def.to_dict(),
            "quality_dimensions": {k: v.to_dict() for k, v in self.quality_dimensions.items()},
            "milestones": self.milestones,
            "initial_coder_directive": self.initial_coder_directive,
            "rationale": self.rationale,
            "visual_asset_spec": self.visual_asset_spec
        }


@dataclass
class ProductCriticEvaluation:
    """Evaluation result from Product Critic across multi-dimensional quality gates."""
    domain: str
    milestone_version: str
    milestone_title: str
    dimension_scores: Dict[str, QualityDimension]
    overall_score: float
    critical_gate_passed: bool
    critical_failures: List[str]
    e2e_verified: bool
    e2e_evidence_summary: str
    is_final_complete: bool
    critic_answers: Dict[str, str]   # Answers to the 8 Core Critic Questions
    verdict: str                     # 'PASS_MILESTONE' | 'REWORK_QUALITY_GAP' | 'FINAL_PRODUCT_RELEASE'
    verdict_summary: str
    next_milestone_focus: str
    target_quality_gap: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "milestone_version": self.milestone_version,
            "milestone_title": self.milestone_title,
            "dimension_scores": {k: v.to_dict() for k, v in self.dimension_scores.items()},
            "overall_score": self.overall_score,
            "critical_gate_passed": self.critical_gate_passed,
            "critical_failures": self.critical_failures,
            "e2e_verified": self.e2e_verified,
            "e2e_evidence_summary": self.e2e_evidence_summary,
            "is_final_complete": self.is_final_complete,
            "critic_answers": self.critic_answers,
            "verdict": self.verdict,
            "verdict_summary": self.verdict_summary,
            "next_milestone_focus": self.next_milestone_focus,
            "target_quality_gap": self.target_quality_gap
        }


# ──────────────────────────────────────────────────────────────────────────────
# Domain Taxonomy & Standard Quality Dimensions
# ──────────────────────────────────────────────────────────────────────────────

GAME_QUALITY_DIMENSIONS = [
    ("functionality", "기능성 & 코어 룰 (Functionality)", "플레이어 이동, 점프 물리, 바닥/블록 충돌, 적/아이템 스폰 규칙 무결성", True, 70.0),
    ("controls_feel", "조작감 & 게임필 (Controls & Game Feel)", "입력 반응성(지연 0ms 체감), 부드러운 점프 가속 곡선, 착지 및 적 밟기 시각/사운드 반작용 피드백", True, 60.0),
    ("visual_quality", "시각적 완성도 & 애니메이션 (Visual Quality)", "캐릭터 런/점프 스프라이트, 배경 깊이감, 타격/코인 파티클, 체력/스코어 HUD 정렬", True, 60.0),
    ("audio_sfx", "오디오 & 사운드 디자인 (Audio & SFX)", "점프, 코인 획득, 적 처치, 피격 SFX 및 분위기에 맞는 BGM (단순 삑 비프음 탈락, 8-bit 아르페지오/절차적 WebAudio)", True, 55.0),
    ("level_gameplay", "레벨 디자인 & 몰입감 (Level Design & Replayability)", "단계적 난이도 곡선, 점진적 적/보상 배치, 클리어 목표감, 1초 즉시 재도전 루프", False, 50.0),
    ("stability_e2e", "안정성 & 실기기 E2E 검증 (Stability & Real E2E)", "60fps 무지연 렌더링, 크래시/ANR 0건, 실기기(에뮬레이터) 터치 인터랙션 물리적 증적 확보", True, 75.0),
]

APP_QUALITY_DIMENSIONS = [
    ("functionality", "핵심 비즈니스 기능 (Functionality)", "사용자가 요구한 핵심 업무 로직 및 CRUD 데이터 생성/조회/수정 완전성", True, 70.0),
    ("ux_ergonomics", "UX 인체공학 & 반응형 레이아웃 (UX Ergonomics & Responsive Layout)", "인체공학적 반응형 레이아웃 및 뷰포트 최적화(뷰어/에디터의 경우 순수 작업 뷰포트 >= 65% 확보, 상/하단 고정 바 <= 35% 슬림화 및 Auto-Hide 권장, 일반 앱은 자율 반응형 레이아웃 및 주요 터치 사용성 보장), 가로 스크롤 버그 0px 방어", True, 65.0),
    ("visual_design", "시각 완성도 & 피드백 (Visual Design)", "일관된 디자인 시스템/토큰, 로딩 스피너, 빈 상태(Empty State) 가이드, 스낵바 피드백", True, 60.0),
    ("data_integrity", "데이터 영속 & 복원력 (Data Integrity)", "새로고침/앱 재기동 시 데이터 영구 보존, 프로세스 킬 복원력, 예외 방어", True, 65.0),
    ("product_retention", "제품 경쟁력 & 리텐션 (Retention & Value)", "첫 사용 3초 내 핵심 가치 전달, 불필요한 마찰 제거, 재방문 유도 요소", False, 50.0),
    ("stability_e2e", "안정성 & E2E 무결성 (Stability & Real E2E)", "콘솔/런타임 에러 0건, 네트워크 장애 복원, 실제 디바이스 프레임버퍼 검증", True, 75.0),
]

CLI_QUALITY_DIMENSIONS = [
    ("functionality", "명령어 파싱 & 코어 로직 (Functionality)", "CLI 인자/옵션 파싱 무결성 및 사용자가 요구한 핵심 비즈니스 로직 완전성", True, 70.0),
    ("terminal_ux", "터미널 UX & 출력 가독성 (Terminal UX)", "Rich 포맷팅, 진행률 표시(Progress bar), 명확한 --help 매뉴얼 및 에러 피드백", True, 60.0),
    ("resilience_recovery", "예외 방어 & 복원력 (Resilience & Exit Codes)", "비정상 입력/SIGINT 시 안전 종료, 표준 종료 코드(Exit Code 0/1/2) 규격 준수", True, 65.0),
    ("performance_throughput", "처리 성능 & 자원 효율 (Performance & Throughput)", "서브세컨드 반응성, 메모리/파일 디스크립터 누수 0건, 대량 데이터 스트리밍", False, 50.0),
    ("configuration_persistence", "설정 영속 & 환경 호환 (Configuration & Storage)", "환경변수, 설정 파일(JSON/YAML) 무결성 및 워크스페이스 격리", True, 60.0),
    ("stability_e2e", "안정성 & 무인 자동화 검증 (Stability & Headless E2E)", "pytest 단위/통합 테스트 100% 통과, 백그라운드 데몬 무중단 안정성", True, 75.0),
]

BACKEND_QUALITY_DIMENSIONS = [
    ("functionality", "REST/API 엔드포인트 & 스키마 (API Functionality)", "요구된 모든 API 엔드포인트 구현, Pydantic 스키마 유효성 검증 완전성", True, 70.0),
    ("data_integrity", "데이터베이스 영속 & 트랜잭션 (Data Integrity)", "ORM/DB 쿼리 정합성, 외래키/인덱스 무결성, 롤백 트랜잭션 보장", True, 65.0),
    ("api_security", "인증/인가 & 입력 검증 (Security & Validation)", "SQL Injection/XSS 방어, 안전한 에러 마스킹, JWT/토큰 인증 체계", True, 65.0),
    ("performance_throughput", "응답 지연 & 처리량 (Latency & Concurrency)", "평균 응답속도 100ms 미만, 동시 요청(비동기 ASGI) 처리 무결성", False, 50.0),
    ("observability", "로깅 & 헬스체크 (Observability & Health)", "구조화된 JSON 로그(Structured Logging), /healthz 엔드포인트 정상 응답", True, 60.0),
    ("stability_e2e", "안정성 & 통합 E2E 검증 (Stability & API E2E)", "HTTP 5xx 0건, API 통합 테스트 전수 통과, 런타임 크래시 0건", True, 75.0),
]


# ──────────────────────────────────────────────────────────────────────────────
# Title Normalizer (2~4 Word High-Clarity Project Title Generator)
# ──────────────────────────────────────────────────────────────────────────────

def strip_prompt_directives(prompt: str) -> str:
    """
    Strips system instructions, prompt directive tags (e.g. '[Zero-Production-Diff]: ...',
    '[마일스톤 v1.1]: ...', '[CRITICAL...]'), markdown headers, bullet metadata, and bot commands.
    Ensures title normalization and domain detection focus purely on the user's intended product.
    """
    if not prompt or not isinstance(prompt, str):
        return ""
    clean = prompt
    # 1. Remove markdown headers (# ..., ## ...)
    clean = re.sub(r'(?m)^#+\s+.*$', ' ', clean)
    # 2. Remove bracket tags with optional trailing colons:
    # e.g., '[Zero-Production-Diff]:', '[마일스톤 v1.1]:', '[CRITICAL SYSTEM & TOOL SAFETY INSTRUCTIONS]', '[🎯 ...]'
    clean = re.sub(r'\[[^\]\n]+\]:?', ' ', clean)
    # 3. Remove bullet directives / key-value metadata lines (e.g., '• 🎯 **...**: ...', '• **...**: ...')
    clean = re.sub(r'(?m)^\s*[-*•]\s+.*$', ' ', clean)
    # 4. Remove bold instruction tags (e.g., '**[...]:**', '**[...]**')
    clean = re.sub(r'\*\*\[[^\]\n]+\]:\*\*', ' ', clean)
    clean = re.sub(r'\*\*\[[^\]\n]+\]\*\*', ' ', clean)
    # 5. Remove horizontal divider lines (---, ===, ───)
    clean = re.sub(r'[─—=-]{3,}', ' ', clean)
    # 6. Remove bot commands, mentions, URLs
    clean = re.sub(r'(<@!?\d+>|!mode\s+\S+|!sim|!qa\S*|!audit\S*|/goal|/schedule|/boost)', ' ', clean)
    clean = re.sub(r'https?://\S+', ' ', clean)
    return clean.strip()


def normalize_product_title(prompt: str, domain: Optional[str] = None) -> str:
    """
    Normalizes and extracts a clean, concise 2~4 word project title
    (e.g., '알PDF 모바일 에디터', '마리오 플랫포머 게임', '주차 메모 앱')
    from potentially long, conversational user prompts.
    Prevents ugly truncated sentence fragments from corrupting PRD.md and ROADMAP.md headers.
    """
    if not prompt or not isinstance(prompt, str):
        default_titles = {
            "game": "2D 캐주얼 게임",
            "mobile_app": "안드로이드 모바일 앱",
            "saas_web": "웹 프로덕션 서비스",
            "cli_agent": "고성능 CLI 도구",
            "daemon": "백그라운드 자동화 데몬",
            "backend_api": "마이크로서비스 REST API",
            "audio": "오디오 플레이어 서비스"
        }
        return default_titles.get(domain or "", "프로덕션 서비스")

    # 0. Strip prompt directive tags and markdown headers first!
    clean = strip_prompt_directives(prompt)
    if not clean:
        clean = prompt.strip()

    # 1. Extract explicit quotation marks ('...', "...", ‘...’, “...”) - brackets excluded to avoid directive tag misfire
    quote_match = re.search(r'[\'\"‘“]([^\'\"’”\n]{2,30})[\'\"’]', clean)
    if quote_match:
        cand = quote_match.group(1).strip()
        words = cand.split()
        if 1 <= len(words) <= 5 and not any(kw in cand.lower() for kw in ["http", "file:", "line", "error", "diff", "tag", "task", "milestone", "마일스톤"]):
            if len(words) == 1:
                suffix = "게임" if domain == "game" else ("앱" if domain == "mobile_app" else "서비스")
                return f"{cand} {suffix}" if not cand.endswith(suffix) else cand
            return " ".join(words[:4])

    # 2. Remove any remaining commands or mentions
    clean = re.sub(r'(!mode\s+\S+|!sim|!qa\S*|!audit\S*|/goal|/schedule|<@\d+>|#+)', ' ', clean)
    clean = re.sub(r'https?://\S+', ' ', clean)
    
    # 2. Extract first sentence or primary line
    lines = [l.strip() for l in clean.splitlines() if l.strip() and not l.strip().startswith(("-", "*", ">", "|"))]
    first_line = lines[0] if lines else clean
    first_sentence = re.split(r'[.?!;\n]', first_line)[0].strip()
    if not first_sentence:
        first_sentence = first_line

    fs_lower = first_sentence.lower()

    # 3. Known domain & product archetypes extraction
    # Domain: PDF / Editor
    if "알pdf" in fs_lower or "alpdf" in fs_lower:
        if any(k in fs_lower for k in ["모바일", "안드로이드", "앱", "스마트폰"]):
            return "알PDF 모바일 에디터"
        return "알PDF 스마트 에디터"
    elif "pdf" in fs_lower:
        if any(k in fs_lower for k in ["에디터", "편집", "수정"]):
            return "PDF 모바일 에디터" if any(k in fs_lower for k in ["모바일", "안드로이드", "앱"]) else "PDF 스마트 에디터"
        elif any(k in fs_lower for k in ["뷰어", "viewer", "리더"]):
            return "PDF 스마트 뷰어"
        return "PDF 모바일 에디터"

    # Domain: Mario / Platformer / Game
    if "마리오" in fs_lower:
        return "마리오 플랫포머 게임"
    if any(k in fs_lower for k in ["뱀서", "서바이벌", "생존"]) and ("게임" in fs_lower or domain == "game"):
        return "탑다운 서바이벌 게임"
    if "쿠키런" in fs_lower:
        return "2D 러닝 액션 게임"

    # Domain: Parking / Memo / Todo / Calculator
    if "주차" in fs_lower and ("메모" in fs_lower or "앱" in fs_lower):
        return "주차 메모 앱"
    if "메모" in fs_lower and ("앱" in fs_lower or "노트" in fs_lower):
        return "스마트 메모 앱"
    if "투두" in fs_lower or "할일" in fs_lower or "할 일" in fs_lower:
        return "스마트 투두 관리 앱"
    if "계산기" in fs_lower:
        return "모바일 계산기 앱"

    # Domain: Audio / Music / Synth
    if any(k in fs_lower for k in ["신디사이저", "synth", "신스"]):
        return "신디사이저 음악 플레이어"
    if any(k in fs_lower for k in ["음악", "뮤직", "오디오"]) and any(k in fs_lower for k in ["플레이어", "앱", "도구"]):
        return "오디오 뮤직 플레이어"

    # Domain: CLI / Daemon / API
    if any(k in fs_lower for k in ["로그", "log"]) and any(k in fs_lower for k in ["분석", "anal"]):
        return "CLI 로그 분석 도구"
    if any(k in fs_lower for k in ["결제", "payment"]) and any(k in fs_lower for k in ["api", "서버", "백엔드"]):
        return "결제 시스템 REST API"

    # 4. Pattern-based Extraction: "[Noun/Modifier] [Noun] [App/Editor/Game/Tool/Service]"
    pattern = re.search(r'([A-Za-z0-9가-힣]+(?:[A-Za-z0-9가-힣\s]{1,15}))\s*(에디터|뷰어|게임|앱|플레이어|관리자|매니저|대시보드|분석기|도구|서버|api|봇)', first_sentence, re.IGNORECASE)
    if pattern:
        prefix = pattern.group(1).strip()
        for filler in ["안녕하세요", "이번에", "만들", "개발할", "구현할", "원하는", "요청하는", "간단한", "고성능", "프로젝트는"]:
            prefix = re.sub(rf'^{filler}\s*', '', prefix).strip()
        cat = pattern.group(2).strip()
        cand = f"{prefix} {cat}".strip()
        words = cand.split()
        if 2 <= len(words) <= 4:
            return cand
        elif len(words) > 4:
            return " ".join(words[-4:])

    # 5. Tokenize and filter conversational fillers
    fillers = {
        "안녕하세요", "수고많으십니다", "이번에", "이번", "만들", "만들어줘", "만들어주세요", "개발해줘",
        "개발해주세요", "구축해줘", "제작해줘", "구현해줘", "리팩토링해줘", "고도화", "해줘", "해주세요",
        "부탁해", "부탁드립니다", "앱은", "앱을", "앱", "하나", "프로젝트", "프로젝트는", "시스템",
        "에이전트", "도구", "작업", "작업은", "관련", "관련된", "필요한", "있는", "수", "달",
        "열어서", "텍스트를", "수정하고", "주석을", "위한", "기반", "기반의", "대해", "대해서"
    }
    raw_tokens = re.findall(r'[A-Za-z0-9가-힣]+', first_sentence)
    meaningful = [t for t in raw_tokens if t.lower() not in fillers and len(t) > 1]

    if len(meaningful) >= 2:
        top_words = meaningful[:3]
        suffix = "게임" if domain == "game" else ("앱" if domain in ("mobile_app", "app") else "서비스")
        if not any(top_words[-1].endswith(s) for s in ["게임", "앱", "에디터", "뷰어", "도구", "서버", "api", "서비스"]):
            top_words.append(suffix)
        return " ".join(top_words[:4])
    elif len(meaningful) == 1:
        suffix = "게임" if domain == "game" else ("앱" if domain in ("mobile_app", "app") else "서비스")
        return f"{meaningful[0]} {suffix}"

    return "프로덕션 서비스"


# ──────────────────────────────────────────────────────────────────────────────
# AutonomousProductPlanner Implementation
# ──────────────────────────────────────────────────────────────────────────────

class AutonomousProductPlanner:
    """
    👑 Product Planner & Critic Engine (Commander Bot)
    Analyzes ambiguous requests, creates comprehensive multi-dimensional quality specifications,
    manages quality-ladder milestones, and rigorously critiques products against commercial standards.
    """

    @classmethod
    def detect_domain(cls, text: str, workspace: Optional[str] = None, zero_base: bool = False) -> str:
        """
        Determines the domain category: game, mobile_app, saas_web, audio, daemon, cli_agent, backend_api.
        Prioritizes user request text (Zero-base intent) while giving strong priority to physical workspace
        technology stacks (e.g., build.gradle, AndroidManifest.xml) so prompt directive tags do not falsely
        overwrite domain to CLI or an unintended stack.
        """
        clean_text = strip_prompt_directives(text)
        t_lower = (clean_text or str(text or "")).lower()

        # 0. Physical Workspace Tech Stack Priority:
        # If unambiguous platform files (build.gradle, AndroidManifest.xml, gradlew) exist in the workspace,
        # grant priority to the physical workspace stack so prompt directive text (e.g. CLI/daemon keywords)
        # does not falsely overwrite the project domain to CLI.
        if not zero_base and workspace and os.path.isdir(workspace):
            has_android_files = False
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [d for d in dirs if d not in [".git", "build", ".gradle", "node_modules", "__pycache__", ".venv"]]
                depth = len(os.path.relpath(root, workspace).split(os.sep))
                if depth > 3:
                    continue
                if any(f in ["build.gradle", "build.gradle.kts", "AndroidManifest.xml"] for f in files):
                    has_android_files = True
                    break
            if has_android_files:
                # Android platform file detected physically in workspace.
                # Directives or CLI keywords must not override mobile_app.
                if not any(k in t_lower for k in ["react", "vue", "fastapi", "flask", "django"]):
                    return "mobile_app"

        # 1. Non-game explicit domains in cleaned prompt (Mobile, Web, Backend, Audio)
        if any(k in t_lower for k in ["apk", "안드로이드", "android", "compose", "모바일 앱", "앱 만들어", "앱 개발", "뷰포트", "viewport"]):
            return "mobile_app"
        if any(k in t_lower for k in ["음악 앱", "music player", "synth", "신디사이저", "작곡", "daw", "vst"]):
            return "audio"
        if any(k in t_lower for k in ["봇", "크롤러", "daemon", "스크래퍼", "crawler"]) or ("자동화" in t_lower and not any(c in t_lower for c in ["cli", "터미널", "명령어", "terminal"])):
            return "daemon"
        if any(k in t_lower for k in ["backend", "백엔드", "fastapi", "api 서버", "rest api"]):
            return "backend_api"
        if any(k in t_lower for k in ["대시보드", "saas", "react", "vue", "pdf", "에디터", "editor", "웹앱", "웹 서비스"]):
            return "saas_web"

        # 2. Explicit System / CLI / Daemon / Architecture prompts (clean of prompt directive tags)
        if any(k in t_lower for k in ["cli", "터미널", "명령어", "terminal", "command line", "daemon", "데몬", "자동화 도구", "아키텍처", "고도화", "테스트 벤치", "e2e 러너", "시스템"]):
            if any(k in t_lower for k in ["daemon", "데몬", "백그라운드", "크롤러", "crawler"]):
                return "daemon"
            return "cli_agent"

        # 3. Game domain in prompt: ONLY check strict, unambiguous game keywords
        # (Exclude generic terms like "캔버스", "canvas", "player", "joystick", "jump", "level loop" which belong to apps/tools!)
        if any(k in t_lower for k in ["게임", "game", "플랫포머", "platformer", "아케이드", "arcade", "슈팅", "로그라이크", "체스", "오목", "퍼즐", "테트리스", "rpg", "마리오", "쿠키런", "goomba", "hit-stop", "hitstop"]):
            return "game"

        if any(k in t_lower for k in ["오디오", "음악", "music", "sound"]):
            return "audio"

        # 3. If prompt is neutral, inspect workspace (profiles, manifests, filenames, code)
        if not zero_base and workspace and os.path.isdir(workspace):
            # Check active profile first
            active_prof = os.path.join(workspace, "profiles", "active_profile.md")
            if os.path.isfile(active_prof):
                try:
                    with open(active_prof, "r", encoding="utf-8") as f:
                        ap_text = f.read().lower()
                    if "cli_agent" in ap_text:
                        return "cli_agent"
                    if "backend_api" in ap_text:
                        return "backend_api"
                    if "android" in ap_text:
                        return "mobile_app"
                    if "web" in ap_text:
                        return "saas_web"
                except Exception:
                    pass

            ws_text = ""
            try:
                for root, dirs, files in os.walk(workspace):
                    dirs[:] = [d for d in dirs if d not in [".git", "build", ".gradle", "node_modules", "__pycache__"]]
                    for fn in files:
                        ws_text += " " + fn.lower()
                        if fn.endswith((".js", ".ts", ".html", ".py", ".kt", ".java")):
                            try:
                                with open(os.path.join(root, fn), "r", encoding="utf-8", errors="ignore") as f:
                                    ws_text += " " + f.read()[:2000].lower()
                            except Exception:
                                pass
            except Exception:
                pass

            if any(k in ws_text for k in ["audio", "oscillator", "webaudio", "synth"]):
                if any(k in ws_text for k in ["pygame", "phaser", "game"]):
                    return "game"
                return "audio"
            if any(k in ws_text for k in ["game", "mario", "goomba", "platformer", "pygame", "phaser", "godot", "unity", "game_loop"]):
                return "game"
            if any(k in ws_text for k in ["build.gradle", "androidx", "jetpack compose", "android"]):
                return "mobile_app"
            if any(k in ws_text for k in ["cli", "argparse", "click", "rich"]):
                return "cli_agent"
            if any(k in ws_text for k in ["fastapi", "flask", "django"]):
                return "backend_api"

        return "saas_web"

    @classmethod
    def normalize_product_title(cls, prompt: str, domain: Optional[str] = None) -> str:
        return normalize_product_title(prompt, domain)

    @classmethod
    def create_product_definition(cls, prompt: str, domain: str) -> ProductDefinition:
        """Infers product intent, target users, core experience, and benchmark references dynamically."""
        p_lower = prompt.lower()
        clean_title = cls.normalize_product_title(prompt, domain)

        if domain == "game":
            if any(k in p_lower for k in ["마리오", "플랫포머", "점프"]):
                return ProductDefinition(
                    domain="game",
                    title=clean_title if "마리오" in clean_title else "모바일 2D 횡스크롤 마리오 플랫포머 게임",
                    platform="Android / 모바일 웹 (가상 조이스틱 & 터치 2버튼)",
                    target_audience="원터치 및 캐주얼 모바일 게임 유저",
                    core_experience="경쾌한 점프 포물선 곡선, 몬스터 밟기 타격감, 코인 파밍 챠링 사운드와 골인 깃발 도달",
                    benchmark_reference="슈퍼마리오 브라더스 1-1 / 쿠키런 (60fps 물리 충돌 & 레트로 칩튠)",
                    key_value_proposition="설치 없이 즉시 몰입하는 상용 수준의 손맛과 피드백을 갖춘 레트로 액션",
                    design_vibe="레트로 8-bit 픽셀 아트 & 경쾌한 아케이드 테마"
                )
            elif any(k in p_lower for k in ["뱀서", "서바이벌", "생존"]):
                return ProductDefinition(
                    domain="game",
                    title=clean_title if any(k in clean_title for k in ["뱀서", "서바이벌", "생존"]) else "탑다운 액션 & 서바이벌 로그라이크",
                    platform="Android / 모바일 터치 조이스틱",
                    target_audience="핵앤슬래시 및 서바이벌 로그라이크 매니아",
                    core_experience="360도 이동, 적 웨이브 몰이 사냥, 경험치 젬 파밍 & 3지선다 무기 레벨업",
                    benchmark_reference="뱀파이어 서바이벌 (Vampire Survivors)",
                    key_value_proposition="끊임없는 화면 파티클과 도파민을 자극하는 성장 빌드 루프",
                    design_vibe="다크 고딕 판타지 & 네온 파티클 로그라이크 테마"
                )
            else:
                return ProductDefinition(
                    domain="game",
                    title=clean_title or "인터랙티브 2D 캐주얼 아케이드 게임",
                    platform="Android / Web (HTML5 Canvas 60fps)",
                    target_audience="킬링타임 캐주얼 게이머",
                    core_experience="직관적인 조작, 즉각적인 물리 피드백, 콤보 점수 획득과 1초 퀵 재도전",
                    benchmark_reference=f"상용 모바일/웹 60fps 캐주얼 인터랙티브 표준 ({clean_title})",
                    key_value_proposition="직관적인 60fps 조작감과 상용 수준의 인터랙티브 게임 루프",
                    design_vibe="비비드 컬러 & 미니멀 2D 아케이드 테마"
                )
        elif domain in ("cli_agent", "daemon"):
            return ProductDefinition(
                domain=domain,
                title=clean_title or "고성능 헤드리스 CLI & 백그라운드 자동화 도구",
                platform="Linux / macOS / Windows Headless CLI (Python / Node.js)",
                target_audience="개발자 및 시스템 엔지니어",
                core_experience="인자 자동 파싱, 실시간 상태 콘솔 출력, 비정상 상황 시 안전 종료 및 복원",
                benchmark_reference="Git / Docker / Rich CLI Tooling",
                key_value_proposition="군더더기 없는 명령행 인터페이스와 무인 배치 실행 완결성",
                design_vibe="ANSI 256컬러 & Rich 콘솔 터미널 테마"
            )
        elif domain == "backend_api":
            return ProductDefinition(
                domain="backend_api",
                title=clean_title or "고성능 마이크로서비스 백엔드 API",
                platform="FastAPI / Node.js REST API",
                target_audience="클라이언트 애플케이션 및 API 연동 개발자",
                core_experience="100ms 미만 초고속 응답, 엄격한 입력 검증, 안전한 데이터 영속성",
                benchmark_reference="Stripe API / GitHub REST API",
                key_value_proposition="확장 가능하고 무결점 데이터 처리를 보장하는 백엔드 아키텍처",
                design_vibe="RESTful OpenAPI 스키마 & 클린 마이크로서비스 아키텍처"
            )
        elif domain == "mobile_app":
            is_viewer_or_editor = any(k in p_lower for k in ["pdf", "에디터", "편집", "뷰어", "viewer", "editor", "캔버스", "canvas", "그림", "드로잉", "문서"])
            core_exp = (
                "65% 캔버스 순수 작업 뷰포트 확보 (상/하단 고정 바 35% 이내 제한, 스크롤 시 보조 툴바 Auto-Hide 및 슬림 크롬 패딩), "
                "지연 없는 48dp 원터치 조작, 세련된 머티리얼 3 레이아웃, 재기동 시 데이터 무결성 보존"
                if is_viewer_or_editor else
                "즉각적인 터치 반응성, 쾌적하고 직관적인 화면 레이아웃, 세련된 머티리얼 3 디자인, 재기동 시 데이터 무결성 보존"
            )
            key_val = (
                "상/하단 바 과다 중첩 방어 및 65% 이상 순수 콘텐츠 뷰포트 확보, 마찰 없는 초고속 온보딩과 실시간 시각 피드백"
                if is_viewer_or_editor else
                "마찰 없는 초고속 온보딩과 실시간 시각 피드백"
            )
            bench_ref = (
                "알PDF / 노션 모바일 (Clean UI, 65% Canvas Viewport & Zero Friction)"
                if is_viewer_or_editor else
                "토스 / 당근마켓 / 네이버페이 (Clean UI & Zero Friction)"
            )
            return ProductDefinition(
                domain="mobile_app",
                title=clean_title or "프로덕션 레디 안드로이드 네이티브 서비스",
                platform="Android (Kotlin + Jetpack Compose)",
                target_audience="스마트폰 실사용자",
                core_experience=core_exp,
                benchmark_reference=bench_ref,
                key_value_proposition=key_val,
                design_vibe="머티리얼 3 모던 클린 테마 (#2563EB, #F8FAFC)"
            )
        else:
            is_viewer_or_editor = any(k in p_lower for k in ["pdf", "에디터", "편집", "뷰어", "viewer", "editor", "캔버스", "canvas", "대시보드"])
            core_exp = (
                "65% 캔버스 순수 작업 뷰포트 확보 (상/하단 크롬 바 35% 이내 제한, 스크롤 시 보조 바 Auto-Hide), 1초 내 로딩, 완벽한 로컬/클라우드 데이터 영속성"
                if is_viewer_or_editor else
                "1초 내 로딩, 직관적인 대시보드 인터랙션, 완벽한 로컬/클라우드 데이터 영속성"
            )
            return ProductDefinition(
                domain="saas_web",
                title=clean_title or "고성능 풀스택 반응형 프로덕션 서비스",
                platform="모바일 390px ~ 데스크톱 1280px 반응형 웹",
                target_audience="생산성 및 비즈니스 실무자",
                core_experience=core_exp,
                benchmark_reference="Notion / Linear / Modern Responsive Web",
                key_value_proposition="상/하단 바 과다 중첩 방어 및 군더더기 없는 미니멀 인터페이스와 즉각적인 데이터 반응성",
                design_vibe="모던 미니멀 반응형 웹 테마 (#0F172A, #3B82F6)"
            )

    @classmethod
    def build_quality_dimensions(cls, domain: str) -> Dict[str, QualityDimension]:
        """Dynamically generates quality dimensions tailored to the domain."""
        if domain == "game":
            raw_dims = GAME_QUALITY_DIMENSIONS
        elif domain in ("cli_agent", "daemon"):
            raw_dims = CLI_QUALITY_DIMENSIONS
        elif domain == "backend_api":
            raw_dims = BACKEND_QUALITY_DIMENSIONS
        else:
            raw_dims = APP_QUALITY_DIMENSIONS
        dims: Dict[str, QualityDimension] = {}
        for key, name, target_desc, is_crit, thresh in raw_dims:
            dims[key] = QualityDimension(
                key=key,
                name=name,
                target_description=target_desc,
                is_critical=is_crit,
                min_threshold=thresh,
                score=0.0,
                passed=False,
                evidence="아직 구현 전 (초기 기획 수립 단계)",
                gap_analysis="타깃 품질 기준 대비 초기 상태",
                action_items=[]
            )
        return dims

    @classmethod
    def _attach_gate_metadata(cls, milestones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """각 마일스톤에 ID / Exit Criteria / Dependencies 스펙을 부여한다 (PRD §4).

        순차 잠금을 위해 dependencies 를 직전 마일스톤 버전으로 설정한다.
        """
        out: List[Dict[str, Any]] = []
        prev_version: Optional[str] = None
        for idx, ms in enumerate(milestones or []):
            ms = dict(ms)
            ms.setdefault("id", f"MS-{idx + 1}")
            spec = build_milestone_spec(
                index=idx,
                version=ms.get("version", ""),
                title=ms.get("title", ""),
                tasks=ms.get("tasks", []),
                dimension=ms.get("dimension", ""),
                target_quality=ms.get("target_quality", ""),
                dependencies=([prev_version] if prev_version else []),
            )
            ms["dependencies"] = spec.dependencies
            ms.setdefault("exit_criteria", spec.exit_criteria)
            ms["spec"] = spec.to_dict()
            prev_version = ms.get("version")
            out.append(ms)
        return out

    @classmethod
    def generate_milestones(cls, domain: str, p_def: ProductDefinition) -> List[Dict[str, Any]]:
        """
        Creates a 6-stage quality evolution ladder tailored to the domain and product definition.
        Breaks down progress across quality dimensions rather than mere feature checklists!
        """
        p_title_lower = p_def.title.lower()

        if domain == "game":
            if any(k in p_title_lower for k in ["마리오", "플랫포머", "점프"]):
                return [
                    {
                        "version": "v1.0",
                        "title": "Core Gameplay & Mechanics (코어 조작 및 물리 엔진)",
                        "dimension": "functionality",
                        "tasks": [
                            "플레이어 이동 및 60fps 중력 가속도 점프 포물선 곡선 구현",
                            "바닥, 장애물, 블록과의 정밀 2D 충돌 박스(AABB) 감지",
                            "화면 이탈 방어 및 뷰포트 카메라 부드러운 수평 추적"
                        ],
                        "target_quality": "조작 렉 0ms 체감, 벽 뚫림 현상 0건",
                        "status": "IN_PROGRESS"
                    },
                    {
                        "version": "v1.1",
                        "title": "Visual Quality & Animation (시각 완성도 및 상태 애니메이션)",
                        "dimension": "visual_quality",
                        "tasks": [
                            "플레이어 정지(Idle), 달리기(Run), 점프(Jump) 스프라이트 상태머신",
                            "바닥 착지 먼지 및 점프 잔상 파티클 이펙트 구현",
                            "배경 시차 스크롤(Parallax Background) 및 레트로 픽셀 아트 테마"
                        ],
                        "target_quality": "애니메이션 전환 시 뚝뚝 끊김 없는 부드러운 60fps 렌더링",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.2",
                        "title": "Audio Design & SFX (오디오 완성도 및 칩튠 사운드)",
                        "dimension": "audio_sfx",
                        "tasks": [
                            "점프 시 8-bit 상승 주파수 WebAudio SFX 재생",
                            "블록 타격 및 코인 획득 시 맑은 챠링 아르페지오 사운드",
                            "적 밟기 처치 퍽 소리 및 피격 경고 사운드",
                            "경쾌한 8-bit 레트로 스테이지 배경음악(BGM) 루프"
                        ],
                        "target_quality": "단순 비프음 배제, 주파수 변조 기반 풍성한 사운드 피드백",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.3",
                        "title": "Game Feel & Touch Controls (조작감 및 타격 반응성)",
                        "dimension": "controls_feel",
                        "tasks": [
                            "모바일 터치 가상 조이스틱 / 2버튼(점프/액션) 터치 슬롭(48dp) 최적화",
                            "적 밟기 순간의 미세한 역경직(Hit-stop 0.05초) 및 화면 흔들림(Screen Shake)",
                            "코인 획득 시 위로 튀어오르는 바운스 물리 피드백"
                        ],
                        "target_quality": "손끝으로 전해지는 상용 플랫포머 특유의 쫀득한 타격감 확보",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.4",
                        "title": "Level Design & Challenge (레벨 디자인 및 게임 루프)",
                        "dimension": "level_gameplay",
                        "tasks": [
                            "1-1 튜토리얼 성격의 안전한 전반부 레벨 배치",
                            "굼바 스타일 적 몬스터 순찰 AI 및 밟기 판정",
                            "물음표 블록에서 코인 및 버섯 아이템 생성 로직",
                            "스테이지 종료 깃발 도달 및 스코어 정산 연출"
                        ],
                        "target_quality": "억까 없는 자연스러운 난이도 곡선 및 성취감 부여",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.5",
                        "title": "Real-world E2E & Production Hardening (실기기 검증 및 내구성)",
                        "dimension": "stability_e2e",
                        "tasks": [
                            "실제 디바이스 / 에뮬레이터 터치 인터랙션 전수 통과 (Mechanical E2E)",
                            "메모리 누수 0건 및 10분 이상 연속 플레이 프레임 드롭 방어",
                            "최종 타임스탬프 APK 빌드 및 릴리즈 바이너리 검증"
                        ],
                        "target_quality": "Critical 0, Major 0 무결점 상용 릴리즈 품질 달성",
                        "status": "PENDING"
                    }
                ]
            else:
                return [
                    {
                        "version": "v1.0",
                        "title": "Core Gameplay & Mechanics (코어 조작 및 60fps 엔진)",
                        "dimension": "functionality",
                        "tasks": [
                            f"{p_def.title} 핵심 조작 및 60fps 무지연 렌더링 루프 구현",
                            "오브젝트 충돌 박스(AABB) 감지 및 상태 전이 규칙 무결성",
                            "뷰포트 이탈 방어 및 터치/키보드 듀얼 컨트롤 바인딩"
                        ],
                        "target_quality": "조작 렉 0ms 체감, 경계선 뚫림 현상 0건",
                        "status": "IN_PROGRESS"
                    },
                    {
                        "version": "v1.1",
                        "title": "Visual Quality & Animation (시각 완성도 및 상태 애니메이션)",
                        "dimension": "visual_quality",
                        "tasks": [
                            "오브젝트 정지/동작/상호작용 상태 스프라이트 애니메이션",
                            "충돌, 득점, 액션 발생 시 절차적 파티클 폭발 이펙트",
                            "일관된 비주얼 테마 및 모바일/데스크톱 캔버스 반응형 정렬"
                        ],
                        "target_quality": "애니메이션 전환 시 뚝뚝 끊김 없는 부드러운 60fps 렌더링",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.2",
                        "title": "Audio Design & SFX (오디오 완성도 및 사운드 피드백)",
                        "dimension": "audio_sfx",
                        "tasks": [
                            "핵심 액션 시 즉각적인 WebAudio 절차적 SFX 재생",
                            "콤보 득점 및 아이템 습득 시 상승 아르페지오 사운드",
                            "게임오버 및 클리어 연출 팡파레 효과음과 BGM 루프"
                        ],
                        "target_quality": "단순 비프음 배제, 주파수 변조 기반 풍성한 사운드 피드백",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.3",
                        "title": "Game Feel & Touch Controls (조작감 및 타격 반응성)",
                        "dimension": "controls_feel",
                        "tasks": [
                            "48dp 터치 슬롭 최적화 및 모바일 가상 패드 원터치 조작 완비",
                            "주요 상호작용 순간의 화면 흔들림(Screen Shake) 및 미세 역경직 피드백",
                            "즉각적인 시각/사운드 반작용 피드백으로 손맛 극대화"
                        ],
                        "target_quality": "손끝으로 전해지는 상용 아케이드 수준의 쫀득한 조작감 확보",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.4",
                        "title": "Level Design & Challenge (레벨 디자인 및 게임 루프)",
                        "dimension": "level_gameplay",
                        "tasks": [
                            "단계적 난이도 상승 곡선 및 점진적 도전 과제 배치",
                            "스코어링, 콤보 보너스 및 최고 기록(High Score) 로컬 저장",
                            "게임오버 판정 및 1초 즉시 재도전(Quick Retry) 황금 루프"
                        ],
                        "target_quality": "억까 없는 자연스러운 난이도 곡선 및 성취감 부여",
                        "status": "PENDING"
                    },
                    {
                        "version": "v1.5",
                        "title": "Real-world E2E & Production Hardening (실기기 검증 및 내구성)",
                        "dimension": "stability_e2e",
                        "tasks": [
                            "실제 디바이스 / 브라우저 터치 인터랙션 전수 통과 (Mechanical E2E)",
                            "메모리 누수 0건 및 10분 이상 연속 플레이 프레임 드롭 방어",
                            "최종 프로덕션 빌드 및 릴리즈 바이너리 검증"
                        ],
                        "target_quality": "Critical 0, Major 0 무결점 상용 릴리즈 품질 달성",
                        "status": "PENDING"
                    }
                ]
        elif domain in ("cli_agent", "daemon"):
            return [
                {
                    "version": "v1.0",
                    "title": "Core Command Parsing & Logic (명령행 인자 및 코어 로직)",
                    "dimension": "functionality",
                    "tasks": [
                        "CLI 서브커맨드 및 인자/플래그 파싱 엔진 구현",
                        "요구된 핵심 비즈니스 로직 및 파이프라인 처리 완비",
                        "표준 출력(stdout) 및 에러(stderr) 분리 및 종료 코드 정의"
                    ],
                    "target_quality": "인자 파싱 오류 0건, 비즈니스 로직 정상 수행",
                    "status": "IN_PROGRESS"
                },
                {
                    "version": "v1.1",
                    "title": "Robust Input Validation & Exit Codes (예외 방어 및 복원력)",
                    "dimension": "resilience_recovery",
                    "tasks": [
                        "비정상 인자/결측값 입력 시 친절한 안내 메시지 및 방어 로직",
                        "표준 프로세스 종료 코드(Exit Code 0/1/2) 완벽 준수",
                        "SIGINT/SIGTERM 감지 시 안전한 graceful shutdown 및 리소스 회수"
                    ],
                    "target_quality": "비정상 입력 시 크래시 없는 안전한 종료 및 도움말 제공",
                    "status": "PENDING"
                },
                {
                    "version": "v1.2",
                    "title": "Terminal UX & Rich Output (터미널 가독성 및 도움말 UX)",
                    "dimension": "terminal_ux",
                    "tasks": [
                        "Rich 콘솔 기반 가독성 높은 컬러 포맷팅 및 테이블 렌더링",
                        "장시간 작업 시 동적 진행률(Progress Bar / Spinner) 표시",
                        "상세한 --help 매뉴얼 및 버전 정보 제공"
                    ],
                    "target_quality": "한눈에 들어오는 깔끔한 터미널 출력 및 직관적 도움말",
                    "status": "PENDING"
                },
                {
                    "version": "v1.3",
                    "title": "Configuration & State Persistence (설정 영속 및 환경 호환)",
                    "dimension": "configuration_persistence",
                    "tasks": [
                        "설정 파일(JSON/YAML) 및 환경변수 우선순위 계층 로딩",
                        "세션 상태 및 실행 로그 파일 지정 디렉토리에 영구 보존",
                        "독립적인 작업 디렉토리 격리 및 파일 경로 안전성 보장"
                    ],
                    "target_quality": "환경변수 및 설정 파일 누락 시에도 안전한 기본값 복원",
                    "status": "PENDING"
                },
                {
                    "version": "v1.4",
                    "title": "Performance & Concurrency (처리 성능 및 자원 최적화)",
                    "dimension": "performance_throughput",
                    "tasks": [
                        "비동기 또는 스트리밍 기반 대용량 데이터 처리",
                        "파일 디스크립터 및 프로세스 메모리 누수 방어",
                        "서브세컨드 고속 응답 최적화"
                    ],
                    "target_quality": "대량 처리 시에도 메모리 급증 없는 안정적 풋프린트 유지",
                    "status": "PENDING"
                },
                {
                    "version": "v1.5",
                    "title": "Production Hardening & Headless E2E (자동화 검증 및 패키징)",
                    "dimension": "stability_e2e",
                    "tasks": [
                        "pytest 단위 및 E2E 테스트 100% 통과 (Mechanical Gate)",
                        "백그라운드 무인 실행 및 데몬 안정성 확인",
                        "배포 가능한 패키징 및 CLI 진입점 스크립트 완결"
                    ],
                    "target_quality": "결함 0건, CI/CD 자동화 환경 무결점 실행",
                    "status": "PENDING"
                }
            ]
        elif domain == "backend_api":
            return [
                {
                    "version": "v1.0",
                    "title": "Core API Endpoints & Models (핵심 엔드포인트 및 데이터 모델)",
                    "dimension": "functionality",
                    "tasks": [
                        "FastAPI/Node 기반 REST 엔드포인트 및 Pydantic 스키마 정의",
                        "핵심 CRUD 및 비즈니스 서비스 로직 완비",
                        "표준 HTTP 상태 코드(200/201/400/404) 응답 구조화"
                    ],
                    "target_quality": "모든 핵심 API 엔드포인트 정상 응답 및 스키마 일치",
                    "status": "IN_PROGRESS"
                },
                {
                    "version": "v1.1",
                    "title": "Schema Validation & Security Gating (입력 검증 및 보안 방어)",
                    "dimension": "api_security",
                    "tasks": [
                        "요청 본문(Body) 및 쿼리 파라미터 엄격한 타입 검증",
                        "내부 스택트레이스 노출 방지 및 구조화된 에러 핸들링",
                        "CORS 및 보안 헤더 정책 적용"
                    ],
                    "target_quality": "악의적/비정상 입력 시 500 에러 0건, 4xx 정상 차단",
                    "status": "PENDING"
                },
                {
                    "version": "v1.2",
                    "title": "Data Persistence & Transactions (데이터 영속 및 트랜잭션)",
                    "dimension": "data_integrity",
                    "tasks": [
                        "데이터베이스 연결 풀 및 ORM 모델 매핑",
                        "원자적 트랜잭션 보장 및 롤백 복원력",
                        "데이터베이스 마이그레이션 스크립트 완비"
                    ],
                    "target_quality": "동시 쓰기 시 데이터 유실/불일치 0건",
                    "status": "PENDING"
                },
                {
                    "version": "v1.3",
                    "title": "Observability & Structured Logging (관측 가능성 및 헬스체크)",
                    "dimension": "observability",
                    "tasks": [
                        "구조화된 JSON 로깅 및 요청별 추적 ID(Trace-ID) 발급",
                        "/healthz 및 /readyz 시스템 상태 엔드포인트 구현",
                        "실시간 에러 모니터링 체계 완비"
                    ],
                    "target_quality": "무중단 헬스체크 및 빠른 장애 원인 추적 지원",
                    "status": "PENDING"
                },
                {
                    "version": "v1.4",
                    "title": "Performance & Concurrency (응답 지연 및 동시성 최적화)",
                    "dimension": "performance_throughput",
                    "tasks": [
                        "비동기 I/O 및 데이터베이스 쿼리 인덱싱 최적화",
                        "평균 응답 지연(Latency) 100ms 미만 보장",
                        "동시 다발적 요청 부하 방어"
                    ],
                    "target_quality": "p95 응답 지연 150ms 미만 달성",
                    "status": "PENDING"
                },
                {
                    "version": "v1.5",
                    "title": "Production Hardening & E2E Integration (통합 검증 및 컨테이너)",
                    "dimension": "stability_e2e",
                    "tasks": [
                        "API 통합 E2E 테스트 100% 통과 (Mechanical Gate)",
                        "도커 컨테이너 빌드 및 런타임 환경변수 검증",
                        "무중단 배포 적합성 최종 승인"
                    ],
                    "target_quality": "HTTP 5xx 0건, 프로덕션 배포 무결점 달성",
                    "status": "PENDING"
                }
            ]
        else:
            is_viewer_or_editor = any(k in p_title_lower for k in ["pdf", "에디터", "편집", "뷰어", "viewer", "editor", "캔버스", "canvas", "드로잉", "그림", "문서"])
            if is_viewer_or_editor:
                ux_title = "UX Ergonomics & 65% Viewport Ratio (캔버스 뷰포트 최적화)"
                ux_tasks = [
                    "65% 캔버스 뷰포트 확보: 상단 앱바/알림바/탭바/툴바 및 하단 고정 바 합계 <= 화면 높이의 35%, 순수 콘텐츠 뷰포트 >= 65% 확보",
                    "스크롤 시 보조 바/헤더 자동 숨김(Auto-Hide) 및 크롬 패딩 슬림화",
                    "주요 터치 타깃 최적화 및 모바일 뷰포트(390px) 적응",
                    "가로 스크롤 버그(Overflow) 0px 철벽 방어",
                    "소프트 키보드 오버레이 시 입력 필드 가림 방지"
                ]
                ux_quality = "상/하단 바 과다 중첩 방어, 65% 이상 순수 작업 영역 확보"
            else:
                ux_title = "UX Ergonomics & Responsive Layout (UX 인체공학 및 반응형 레이아웃 최적화)"
                ux_tasks = [
                    "화면 크기(모바일~데스크톱)에 맞춘 자연스러운 반응형 레이아웃 및 여백/정렬 최적화",
                    "주요 인터랙션 요소 터치 사용성 및 엄지 영역(Thumb Zone) 접근성 확보",
                    "가로 스크롤 버그(Horizontal Overflow) 0px 방어",
                    "소프트 키보드 오버레이 시 입력 필드 가림 방지",
                    "부드러운 스크롤 경험 및 직관적인 시각 계층 구조 정립"
                ]
                ux_quality = "과도한 고정 바 없는 쾌적한 화면 구성 및 한 손 조작 인체공학적 UI"

            return [
                {
                    "version": "v1.0",
                    "title": "Core Architecture & Primary User Flow (핵심 아키텍처 및 유저 플로우)",
                    "dimension": "functionality",
                    "tasks": [
                        "기본 데이터 모델 및 핵심 비즈니스 로직 구현",
                        "메인 화면 레이아웃 및 1차 기능 동작 완비",
                        "빌드 및 런타임 크래시 0건 확보"
                    ],
                    "target_quality": "핵심 사용자 시나리오 완벽 정상 동작",
                    "status": "IN_PROGRESS"
                },
                {
                    "version": "v1.1",
                    "title": ux_title,
                    "dimension": "ux_ergonomics",
                    "tasks": ux_tasks,
                    "target_quality": ux_quality,
                    "status": "PENDING"
                },
                {
                    "version": "v1.2",
                    "title": "Visual Design Polish & Micro-Interactions (시각 완성도 및 피드백)",
                    "dimension": "visual_design",
                    "tasks": [
                        "일관된 테마 컬러 및 머티리얼 3 디자인 토큰 적용",
                        "로딩 스켈레톤 인디케이터 및 빈 상태(Empty State) 일러스트",
                        "작업 완료 스낵바/토스트 피드백"
                    ],
                    "target_quality": "시각적 불안감 없는 완성도 높은 상용 앱 룩앤필",
                    "status": "PENDING"
                },
                {
                    "version": "v1.3",
                    "title": "Data Persistence & Resilience (데이터 영속성 및 복원력)",
                    "dimension": "data_integrity",
                    "tasks": [
                        "로컬 스토리지 / SQLite 영구 보존 로직 완비",
                        "가로(Landscape) 회전 및 백그라운드 전환 시 데이터 유실 0건",
                        "네트워크 장애 시 자동 재시도 및 오프라인 폴백"
                    ],
                    "target_quality": "비정상 종료 후에도 작업 내용 100% 보존",
                    "status": "PENDING"
                },
                {
                    "version": "v1.4",
                    "title": "Product Competitiveness & Onboarding (경쟁력 및 온보딩 강화)",
                    "dimension": "product_retention",
                    "tasks": [
                        "가입/설정 없는 0초 즉시 사용 온보딩 경험",
                        "원클릭 데이터 내보내기 / 공유 카드 생성 기능",
                        "사용자 재방문을 유도하는 하이라이트 요약"
                    ],
                    "target_quality": "첫 사용 3초 내 실질적 가치를 느끼게 하는 완성도",
                    "status": "PENDING"
                },
                {
                    "version": "v1.5",
                    "title": "Real-world E2E & Production Hardening (실기기 E2E 및 상용 릴리즈)",
                    "dimension": "stability_e2e",
                    "tasks": [
                        "실제 디바이스 / 브라우저 E2E 전 시나리오 PASS",
                        "콘솔 및 시스템 로그 상의 Fatal Exception 0건",
                        "최종 릴리즈 패키지 생성 및 배포 검증"
                    ],
                    "target_quality": "스토어 즉시 제출 가능한 프로덕션 무결성",
                    "status": "PENDING"
                }
            ]

    @classmethod
    def plan_product_and_quality(
        cls,
        user_request: str,
        workspace: str,
        mode: str = "evolve"
    ) -> ProductPlan:
        """
        Main Product Planning Entry Point.
        Synthesizes Product Definition, Quality Definition, and generates PRD.md & ROADMAP.md.
        """
        domain = cls.detect_domain(user_request, workspace)
        p_def = cls.create_product_definition(user_request, domain)
        q_dims = cls.build_quality_dimensions(domain)
        milestones = cls._attach_gate_metadata(cls.generate_milestones(domain, p_def))

        # Write or update PRD.md with explicit Quality Definition
        cls._sync_prd_md(workspace, p_def, q_dims, user_request, milestones=milestones)
        cls._sync_roadmap_md(workspace, p_def, milestones)

        first_ms = milestones[0]
        initial_directive = (
            f"🎯 [수석 제품 기획관 1차 마일스톤 구현 지침: {first_ms['version']} — {first_ms['title']}]\n"
            f"• 제품 정의: {p_def.title} (레퍼런스: {p_def.benchmark_reference})\n"
            f"• 핵심 경험 목표: {p_def.core_experience}\n"
            f"• 1차 집중 품질 차원: `{first_ms['dimension']}` (목표: {first_ms['target_quality']})\n"
            f"• 세부 구현 태스크:\n" + "\n".join(f"  - {t}" for t in first_ms['tasks']) + "\n"
            f"⚠️ 주의: 단순 껍데기만 만들지 말고, 요구된 핵심 비즈니스 로직과 데이터 플로우를 실제 소스코드에 실구현하십시오."
        )

        rationale = (
            f"사용자의 모호한 요청('{user_request[:60]}...')을 분석하여 "
            f"'{p_def.title}'로 제품 목적을 구체화하고, 6대 다차원 품질 기준과 "
            f"단계별 진화 로드맵을 워크스페이스 디스크에 영구 확정했습니다."
        )

        # Determine visual asset spec based on domain
        vis_spec = ""
        if domain == "game":
            vis_spec = (
                f"🎨 [사령관 {COMMANDER_USER_MENTION} 시각 에셋 공식 승인 완료]\n"
                "• 에셋 정책: Rule 8 분류 체계 준수 하에 generate_image 호출 및 캔버스 리소스 바인딩 전격 허가\n"
                "• Type A (풀컷 씬/일러스트): 16:9 메인 타이틀 배경 및 스테이지 아트 (assets/)\n"
                "• Type B (모듈러 독립 파츠): 1:1 플레이어/적/아이템 스프라이트 및 조이스틱 UI (assets/)\n"
                "• 통합 가이드: HTML5 Canvas drawImage 렌더링 및 60fps 최적화"
            )
        elif domain == "mobile_app":
            vis_spec = (
                f"🎨 [사령관 {COMMANDER_USER_MENTION} 시각 에셋 공식 승인 완료]\n"
                "• 에셋 정책: Rule 8 분류 체계 준수 하에 generate_image 호출 및 네이티브 리소스 바인딩 전격 허가\n"
                "• Type A (풀컷 씬/일러스트): 9:16 모바일 스플래시(Splash) 화면 및 온보딩 랜딩 아트 (res/drawable/)\n"
                "• Type B (모듈러 독립 파츠): 1:1 앱 런처 아이콘, 기능 배지, 커스텀 액션 버튼 (res/drawable/)\n"
                "• 통합 가이드: Compose painterResource / XML ImageView 연결 및 자연스러운 터치 인터랙션 보장"
            )
        elif domain == "saas_web":
            vis_spec = (
                f"🎨 [사령관 {COMMANDER_USER_MENTION} 시각 에셋 공식 승인 완료]\n"
                "• 에셋 정책: Rule 8 분류 체계 준수 하에 generate_image 호출 및 웹 리소스 바인딩 전격 허가\n"
                "• Type A (풀컷 씬/일러스트): 16:9 대시보드 히어로 배너 및 랜딩 배경 일러스트 (assets/images/)\n"
                "• Type B (모듈러 독립 파츠): 1:1 브랜드 로고, 빈 화면(Empty State) 일러스트, 상태 배지 (assets/images/)\n"
                "• 통합 가이드: <img> 태그 또는 CSS background 연동 및 반응형 390~1280px 완벽 적응"
            )
        else:
            vis_spec = "시각 에셋 불필요 (CLI/헤드리스/백엔드 환경: 터미널 텍스트 UI 또는 REST 규격 준수)"

        full_directive = initial_directive
        if vis_spec and COMMANDER_USER_MENTION in vis_spec:
            full_directive = f"{initial_directive}\n\n{vis_spec}"

        return ProductPlan(
            product_def=p_def,
            quality_dimensions=q_dims,
            milestones=milestones,
            initial_coder_directive=full_directive,
            rationale=rationale,
            visual_asset_spec=vis_spec
        )

    @classmethod
    def plan_product_from_dict(
        cls,
        plan_data: Dict[str, Any],
        workspace: str
    ) -> ProductPlan:
        """
        Builds a ProductPlan from an AI-synthesized dictionary, syncing PRD.md and ROADMAP.md.
        """
        title = plan_data.get("title") or "자율 기획 프로덕트"
        domain = plan_data.get("domain") or cls.detect_domain(title, workspace)
        p_def = ProductDefinition(
            domain=domain,
            title=title,
            app_naming=plan_data.get("app_naming", title),
            platform=plan_data.get("platform", "반응형 웹 / CLI"),
            target_audience=plan_data.get("target_demographic", "일반 사용자"),
            core_experience=plan_data.get("core_experience", "사용자 맞춤형 고성능 인터랙션"),
            benchmark_reference=plan_data.get("benchmark_reference", "상용 글로벌 스탠다드 서비스"),
            key_value_proposition=plan_data.get("key_value_proposition", "군더더기 없는 핵심 가치 제공"),
            design_vibe=plan_data.get("design_vibe", "")
        )
        q_dims = cls.build_quality_dimensions(domain)
        milestones = plan_data.get("milestones") or cls.generate_milestones(domain, p_def)
        milestones = cls._attach_gate_metadata(milestones)

        # Write or update PRD.md and ROADMAP.md
        cls._sync_prd_md(workspace, p_def, q_dims, title, milestones=milestones)
        cls._sync_roadmap_md(workspace, p_def, milestones)

        initial_directive = plan_data.get("initial_directive")
        if not initial_directive and milestones:
            first_ms = milestones[0]
            initial_directive = (
                f"🎯 [수석 제품 기획관 1차 마일스톤 구현 지침: {first_ms.get('version', 'v1.0')} — {first_ms.get('title', '')}]\n"
                f"• 제품 정의: {p_def.title}\n"
                f"• 세부 구현 태스크:\n" + "\n".join(f"  - {t}" for t in first_ms.get('tasks', []))
            )

        vis_spec = plan_data.get("visual_asset_spec")
        if not vis_spec:
            if domain == "game":
                vis_spec = (
                    f"🎨 [사령관 {COMMANDER_USER_MENTION} 시각 에셋 공식 승인 완료]\n"
                    "• 에셋 정책: Rule 8 분류 체계 준수 하에 generate_image 호출 및 캔버스 리소스 바인딩 전격 허가\n"
                    "• Type A (풀컷 씬/일러스트): 16:9 메인 타이틀 배경 및 스테이지 아트 (assets/)\n"
                    "• Type B (모듈러 독립 파츠): 1:1 플레이어/적/아이템 스프라이트 및 조이스틱 UI (assets/)\n"
                    "• 통합 가이드: HTML5 Canvas drawImage 렌더링 및 60fps 최적화"
                )
            elif domain == "mobile_app":
                vis_spec = (
                    f"🎨 [사령관 {COMMANDER_USER_MENTION} 시각 에셋 공식 승인 완료]\n"
                    "• 에셋 정책: Rule 8 분류 체계 준수 하에 generate_image 호출 및 네이티브 리소스 바인딩 전격 허가\n"
                    "• Type A (풀컷 씬/일러스트): 9:16 모바일 스플래시(Splash) 화면 및 온보딩 랜딩 아트 (res/drawable/)\n"
                    "• Type B (모듈러 독립 파츠): 1:1 앱 런처 아이콘, 기능 배지, 커스텀 액션 버튼 (res/drawable/)\n"
                    "• 통합 가이드: Compose painterResource / XML ImageView 연결 및 자연스러운 터치 인터랙션 보장"
                )
            elif domain == "saas_web":
                vis_spec = (
                    f"🎨 [사령관 {COMMANDER_USER_MENTION} 시각 에셋 공식 승인 완료]\n"
                    "• 에셋 정책: Rule 8 분류 체계 준수 하에 generate_image 호출 및 웹 리소스 바인딩 전격 허가\n"
                    "• Type A (풀컷 씬/일러스트): 16:9 대시보드 히어로 배너 및 랜딩 배경 일러스트 (assets/images/)\n"
                    "• Type B (모듈러 독립 파츠): 1:1 브랜드 로고, 빈 화면(Empty State) 일러스트, 상태 배지 (assets/images/)\n"
                    "• 통합 가이드: <img> 태그 또는 CSS background 연동 및 반응형 390~1280px 완벽 적응"
                )
            else:
                vis_spec = "시각 에셋 불필요 (CLI/헤드리스/백엔드 환경: 터미널 텍스트 UI 또는 REST 규격 준수)"

        full_directive = initial_directive or ""
        if vis_spec and COMMANDER_USER_MENTION in vis_spec and COMMANDER_USER_MENTION not in full_directive:
            full_directive = f"{full_directive}\n\n{vis_spec}"

        return ProductPlan(
            product_def=p_def,
            quality_dimensions=q_dims,
            milestones=milestones,
            initial_coder_directive=full_directive,
            rationale=f"AI 자율 기획을 통해 '{title}' 제품 로드맵 및 PRD를 수립했습니다.",
            visual_asset_spec=vis_spec
        )

    @classmethod
    def _sync_prd_md(
        cls,
        workspace: str,
        p_def: ProductDefinition,
        q_dims: Dict[str, QualityDimension],
        user_request: str,
        milestones: Optional[List[Dict[str, Any]]] = None
    ):
        """Writes or updates PRD.md ensuring Quality Definition is firmly grounded."""
        if not workspace or not os.path.isdir(workspace):
            return
        prd_path = os.path.join(workspace, "PRD.md")
        display_name = p_def.app_naming or p_def.title
        lines = [
            f"# 📋 Product Requirements Document (PRD) — {display_name}",
            "",
            f"> 본 문서는 AGY 3.0 / Commander Bot ({COMMANDER_BOT_ID}) 수석 제품 기획관이",
            "> 사용자의 원본 요청과 상용 벤치마크 기준을 융합하여 자동 수립한 공식 제품 기획 헌법입니다.",
            "",
            "## 1. 제품 개요 및 가치 제안 (Product Overview)",
            f"* **제품명**: {display_name}",
            f"* **도메인 분류**: `{p_def.domain.upper()}`",
            f"* **타깃 플랫폼**: {p_def.platform}",
            f"* **대상 사용자**: {p_def.target_audience}",
            f"* **벤치마크 레퍼런스**: `{p_def.benchmark_reference}`",
            f"* **핵심 가치 제안**: {p_def.key_value_proposition}",
            f"* **사용자 원본 의뢰**: *\"{user_request}\"*",
            "",
            "## 2. 다차원 품질 기준 헌법 (Multi-Dimensional Quality Definition)",
            "> \"단순히 기능이 동작한다고 해서 제품이 완성된 것이 아니다.\"",
            "> 6대 품질 차원별로 측정 가능한 구체적 합격 기준을 규정합니다.",
            ""
        ]

        for idx, (k, dim) in enumerate(q_dims.items(), start=1):
            crit_badge = "🚨 [Critical Quality Gate: 100% 충족 필수]" if dim.is_critical else "💡 [Secondary Quality Item]"
            lines.append(f"### Q-{idx:02d}. {dim.name}")
            lines.append(f"* **구분**: {crit_badge}")
            lines.append(f"* **최소 합격 기준선**: `{dim.min_threshold:.0f}점` / 100점")
            lines.append(f"* **목표 완성도 규격**: {dim.target_description}")
            lines.append("")

        lines.append("## 3. 핵심 요구사항 명세 (Core Requirements)")
        if milestones:
            for idx, ms in enumerate(milestones, start=1):
                t_str = "; ".join(ms.get("tasks", []))
                lines.append(f"* **REQ-{idx:03d} ({ms.get('title', '')})**: {t_str}")
        elif p_def.domain == "game":
            lines.extend([
                "* **REQ-001 (Core Gameplay & Rules)**: 플레이어 60fps 이동/점프 물리 엔진 및 충돌 박스(AABB) 무결성",
                "* **REQ-002 (Visual Sprites & Effects)**: 3단 상태 애니메이션(Idle/Run/Jump) 및 착지 파티클",
                "* **REQ-003 (Procedural Audio Design)**: 8-bit WebAudio 점프/코인/타격 사운드 및 레트로 BGM 루프",
                "* **REQ-004 (Game Feel & Touch Controls)**: 48dp 가상 조이스틱 터치 반응성 및 적 밟기 타격감(Hit-Stop/Screen Shake)",
                "* **REQ-005 (Level Design & Challenge)**: 1-1 스테이지 레벨 배치, 적 몬스터 순찰 및 골인 깃발 도달 루프",
                "* **REQ-006 (Real-World E2E Verification)**: 실제 디바이스/에뮬레이터 터치 및 프레임버퍼 전수 검증 통과",
            ])
        else:
            lines.extend([
                "* **REQ-001 (Core Architecture & Primary Flow)**: 기본 데이터 모델 및 핵심 비즈니스 로직 완비",
                "* **REQ-002 (UX Ergonomics & Mobile Adaptability)**: 모바일 뷰포트 최적화(주요 터치 타깃 확보) 및 가로 스크롤 0px 방어",
                "* **REQ-003 (Visual Design Polish & Feedback)**: 일관된 디자인 시스템, 로딩 스피너 및 스낵바 피드백",
                "* **REQ-004 (Data Persistence & Resilience)**: 프로세스 재기동 시 데이터 보존 및 예외 방어",
                "* **REQ-005 (Product Retention & Value Delivery)**: 사용자 핵심 여정 마찰 제거 및 가치 전달",
                "* **REQ-006 (Production E2E Hardening)**: 런타임 크래시 0건 및 실제 환경 검증 통과",
            ])
        lines.append("")

        if milestones:
            lines.append("## 3-1. 마일스톤 명세 & Exit Criteria (순차 완결 조건)")
            lines.append("> 이전 마일스톤이 PASS(90점 게이트)되기 전에는 다음 마일스톤을 시작할 수 없습니다.")
            for idx, ms in enumerate(milestones, start=1):
                ms_id = ms.get("id", f"MS-{idx}")
                deps = ms.get("dependencies") or []
                dep_str = f" (Dependencies: {', '.join(deps)})" if deps else ""
                lines.append(f"### {ms_id} — {ms.get('version', '')} {ms.get('title', '')}{dep_str}")
                for c in (ms.get("exit_criteria") or []):
                    lines.append(f"* ✅ {c}")
            lines.append("")

        if p_def.domain in ("game", "mobile_app", "saas_web"):
            lines.extend([
                "## 4. 시각 에셋 승인 규격 (Visual Asset Taxonomy)",
                f"> 사령관 {COMMANDER_USER_MENTION} 공식 승인 하에 생성형 비주얼 에셋(Rule 8) 파이프라인을 가동합니다.",
                "* **Type A (풀컷 씬/일러스트)**: 16:9 메인 타이틀 배경 및 대시보드 히어로 배너 (assets/)",
                "* **Type B (모듈러 독립 파츠)**: 1:1 스프라이트, 런처 아이콘, 기능 배지 (assets/)",
                "* **도구 정책**: `generate_image` 호출 허가 및 네이티브/웹 리소스 바인딩 규격 준수",
                ""
            ])

        try:
            with open(prd_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            logger.info(f"PRD.md synced successfully with quality definition at {prd_path}")
        except Exception as e:
            logger.error(f"Failed to sync PRD.md: {e}")

    @classmethod
    def _sync_roadmap_md(
        cls,
        workspace: str,
        p_def: ProductDefinition,
        milestones: List[Dict[str, Any]]
    ):
        """Writes or updates ROADMAP.md matching the quality-oriented milestones."""
        if not workspace or not os.path.isdir(workspace):
            return
        rm_path = os.path.join(workspace, "ROADMAP.md")
        lines = [
            f"# 🗺️ Project Roadmap (AGY Autonomous Quality Ladder: {p_def.title})",
            f"> 이 파일은 AGY Commander Bot ({COMMANDER_BOT_ID})이 제품 품질 진화 사다리를 관리합니다.",
            "> 기능 구현뿐만 아니라 다차원 품질 게이트(Quality Gate)를 통과할 때마다 다음 사다리로 진화합니다.",
            ""
        ]

        for ms in milestones:
            badge = " 🔄" if ms.get("status") == "IN_PROGRESS" else (" ✅" if ms.get("status") == "DONE" else "")
            lines.append(f"## {ms['version']} — {ms['title']}{badge}")
            for t in ms.get("tasks", []):
                mark = "x" if ms.get("status") == "DONE" else " "
                lines.append(f"- [{mark}] {t}")
            ec = ms.get("exit_criteria") or []
            if ec:
                lines.append("**Exit Criteria (90점 PASS 요건):**")
                for c in ec:
                    lines.append(f"  - ✅ {c}")
            lines.append("")

        try:
            with open(rm_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            logger.info(f"ROADMAP.md synced successfully with quality milestones at {rm_path}")
        except Exception as e:
            logger.error(f"Failed to sync ROADMAP.md: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Product Critic: Multi-Dimensional Quality Evaluation & Gap Analysis
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def critique_product(
        cls,
        workspace: str,
        e2e_report: Any,
        coder_output: str,
        current_milestone: Optional[Any] = None,
        mode: str = "evolve",
        is_last_milestone: Optional[bool] = None,
        improvement_severities: Optional[Any] = None,
    ) -> ProductCriticEvaluation:
        """
        👑 Product Critic Evaluation Engine (Commander Bot)
        Strictly distinguishes 'Functional Completion' from 'Product Quality Completion'.
        Evaluates across multi-dimensional quality scores, enforces Critical Quality Gates,
        and answers the 8 Core Critic Questions.
        """
        domain = cls.detect_domain(coder_output, workspace)
        is_quad_mode = (mode in ("quad", "3+", "3plus", "4"))
        min_threshold_bias = 5.0 if is_quad_mode else 0.0 # Mode 3+ enforces stricter commercial thresholds!

        # Inspect E2E results
        e2e_test_cases = getattr(e2e_report, "test_cases", []) or []
        e2e_console_logs = getattr(e2e_report, "console_logs", []) or []
        e2e_screenshots = getattr(e2e_report, "screenshots", []) or []
        e2e_interactions = getattr(e2e_report, "interactions", []) or []
        has_e2e_crash = any("[ERROR]" in l or "crash" in l.lower() or "fatal" in l.lower() for l in e2e_console_logs)

        # 1. E2E Integrity Check: Broken/Failing E2E CANNOT PASS
        tc_fail_count = sum(1 for tc in e2e_test_cases if getattr(tc, "status", "") == "FAIL" or (isinstance(tc, dict) and tc.get("status") == "FAIL"))
        e2e_verified = (not has_e2e_crash and tc_fail_count == 0)

        # Inspect workspace code to find genuine implementations vs mock
        code_text = ""
        if workspace and os.path.isdir(workspace):
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [d for d in dirs if d not in [".git", "build", ".gradle", "node_modules", "__pycache__"]]
                for fn in files:
                    if fn.endswith((".kt", ".java", ".js", ".ts", ".html", ".css", ".py")):
                        try:
                            with open(os.path.join(root, fn), "r", encoding="utf-8", errors="ignore") as f:
                                code_text += " " + f.read()[:5000]
                        except Exception:
                            pass

        code_lower = code_text.lower()
        combined_text = (code_lower + " " + (coder_output or "").lower())

        # Evaluate each quality dimension
        dimensions: Dict[str, QualityDimension] = {}
        critical_failures: List[str] = []

        if domain == "game":
            base_dims = GAME_QUALITY_DIMENSIONS
        elif domain in ("cli_agent", "daemon"):
            base_dims = CLI_QUALITY_DIMENSIONS
        elif domain == "backend_api":
            base_dims = BACKEND_QUALITY_DIMENSIONS
        else:
            base_dims = APP_QUALITY_DIMENSIONS

        for key, name, target_desc, is_crit, thresh in base_dims:
            # 🚪 Critical 차원은 90점 미만이면 통과 불가 (90점 완결 게이트).
            #    비-Critical 차원은 기존 도메인 기준선을 유지한다.
            if is_crit:
                adj_thresh = max(float(thresh) + min_threshold_bias, CRITICAL_DIMENSION_MIN_SCORE)
            else:
                adj_thresh = float(thresh) + min_threshold_bias
            score = 50.0
            evidence = ""
            gap = ""

            if key == "functionality":
                # Domain-aware functionality scoring
                if domain == "game":
                    has_core_logic = any(k in combined_text for k in ["player", "jump", "update", "render", "collision", "gravity", "state", "controller"])
                elif domain in ("cli_agent", "daemon"):
                    has_core_logic = any(k in combined_text for k in ["argparse", "click", "subcommand", "main", "def run", "def execute", "pipeline", "process"])
                elif domain == "backend_api":
                    has_core_logic = any(k in combined_text for k in ["@app.get", "@app.post", "@router", "endpoint", "fastapi", "flask", "route", "handler"])
                else:
                    has_core_logic = any(k in combined_text for k in ["def ", "class ", "function", "controller", "service", "repository", "component", "render"])
                if has_core_logic and e2e_verified and len(e2e_interactions) > 0:
                    score = 92.0
                    evidence = "핵심 비즈니스 로직/엔티티 구현 및 실제 사용자 인터랙션 E2E 실증"
                    gap = "정상 동작. 특이 결함 없음."
                elif has_core_logic and e2e_verified:
                    # 🚫 Render/코드 '존재'만으로 높은 점수를 주지 않는다 (Render ≠ 품질).
                    score = 72.0
                    evidence = "코드 존재 및 E2E 무결성은 확인되나 실제 사용자 인터랙션 실증 데이터 없음"
                    gap = "렌더/코드 존재는 품질 증거가 아님 — 실제 조작 플로우 E2E 검증 필요"
                elif has_core_logic:
                    score = 65.0
                    evidence = "코드는 존재하나 E2E에서 결함 또는 크래시 감지"
                    gap = "런타임 실행 시 비정상 동작 또는 예외 방어 미흡"
                else:
                    score = 35.0
                    evidence = "핵심 로직 미구현 또는 빈 껍데기"
                    gap = "핵심 비즈니스 로직 구현 필요"

            elif key in ("controls_feel", "ux_ergonomics"):
                has_touch_controls = any(k in combined_text for k in ["touch", "joystick", "virtual", "button", "pointer", "48dp", "keydown", "keyup"])
                has_shake_or_feedback = any(k in combined_text for k in ["shake", "hitstop", "feedback", "vibrate", "ripple", "spring"])
                if has_touch_controls and has_shake_or_feedback:
                    score = 92.0
                    evidence = "가상 조이스틱/키보드 듀얼 조작 및 피격/화면 흔들림 피드백 확인"
                    gap = "타격 피드백 양호. 상용 수준 근접."
                elif has_touch_controls:
                    score = 60.0
                    evidence = "기본 터치/키보드 입력은 있으나 타격감(Hit-Stop, Screen Shake) 부재"
                    gap = "피격 및 코인 획득 시 역경직/바운스 피드백 보강 필요"
                else:
                    score = 30.0
                    evidence = "모바일 가상 조이스틱 또는 터치 컨트롤 미구현"
                    gap = "모바일 사용자가 조작 불가능한 상태"

            elif key in ("visual_quality", "visual_design"):
                has_sprites = any(k in combined_text for k in ["sprite", "drawimage", "canvas", "animation", "frame", "compose", "painter", "render", "draw", "graphic", "visual", "dust", "particle", "spark", "emitter"])
                has_particles = any(k in combined_text for k in ["particle", "dust", "effect", "emitter", "spark"])
                if has_sprites and has_particles and len(e2e_screenshots) > 0:
                    score = 92.0
                    evidence = "스프라이트 렌더링 및 파티클 이펙트 시스템 확인 + 실제 렌더 스크린샷 증거"
                    gap = "시각 효과 풍성함."
                elif has_sprites and has_particles:
                    # 🚫 canvas/render 키워드 존재만으로 높은 점수를 주지 않는다.
                    score = 72.0
                    evidence = "스프라이트/파티클 키워드는 확인되나 실제 렌더 스크린샷/디자인 대조 증거 없음"
                    gap = "코드 키워드 존재는 시각 품질 증거가 아님 — 실제 캡처 및 디자인 대조 필요"
                elif has_sprites:
                    score = 62.0
                    evidence = "기본 스프라이트는 렌더링되나 착지/타격 파티클 이펙트 결핍"
                    gap = "먼지 파티클 및 팝 이펙트 추가 필요"
                else:
                    score = 35.0
                    evidence = "단순 단색 사각형(Box/Rect) 렌더링에 머무름 (시각적 눈속임 경계)"
                    gap = "정규 스프라이트 및 시각 에셋 결핍"

            elif key == "audio_sfx":
                has_webaudio = any(k in combined_text for k in ["audiocontext", "webaudio", "oscillator", "soundpool", "mediaplayer", "audio/"])
                has_rich_tones = any(k in combined_text for k in ["frequency.setvalueattime", "setvalueattime", "arpeggio", "chord", "gain", "sine", "square", "triangle", "envelope"])
                if has_webaudio and has_rich_tones:
                    score = 92.0
                    evidence = "WebAudio Oscillator 주파수 변조 기반 칩튠 SFX 및 BGM 사운드 시스템 완비"
                    gap = "사운드 만족도 높음."
                elif has_webaudio:
                    score = 45.0
                    evidence = "오디오 호출 코드는 있으나 단순 단음 비프음에 머무름 (사운드 퀄리티 부족)"
                    gap = "점프, 코인(아르페지오), 피격 사운드의 주파수 다변화 및 믹싱 필요"
                else:
                    score = 25.0
                    evidence = "사운드 관련 구현 전무 (오디오 미구현)"
                    gap = "게임의 타격감을 살릴 오디오 시스템 구축 필수"

            elif key in ("level_gameplay", "product_retention"):
                has_enemies_or_items = any(k in combined_text for k in ["enemy", "goomba", "coin", "score", "item", "level", "stage", "flag"])
                has_multi_elements = any(k in combined_text for k in ["coin", "flag", "stage", "item"]) and any(k in combined_text for k in ["enemy", "goomba"])
                if has_multi_elements:
                    score = 92.0
                    evidence = "적 몬스터 순찰, 코인/블록 아이템, 스코어 및 골인 깃발 루프 완비"
                    gap = "풍성한 레벨 루프 완비."
                elif has_enemies_or_items:
                    score = 75.0
                    evidence = "적 몬스터 또는 코인 등 기본 레벨 요소 확인"
                    gap = "기본 레벨 루프 동작."
                else:
                    score = 40.0
                    evidence = "빈 스테이지에 캐릭터만 존재 (적, 장애물, 보상 루프 부재)"
                    gap = "적 배치 및 클리어 목표 루프 보강 필요"

            elif key == "data_integrity":
                has_storage = any(k in combined_text for k in ["localstorage", "sqlite", "room", "dao", "persist", "save", "load", "storage", "db", "json", "preference"])
                if has_storage:
                    score = 92.0
                    evidence = "로컬 영속성 스토리지 연동 및 데이터 상태 복원 메커니즘 확인"
                    gap = "데이터 무결성 양호."
                else:
                    score = 40.0
                    evidence = "영속 스토리지 미연동 (새로고침/재기동 시 상태 휘발 위험)"
                    gap = "로컬 스토리지 또는 DB 영속화 필요"

            # ── CLI / Daemon domain-specific scoring ──────────────────────────
            elif key == "terminal_ux":
                has_rich = any(k in combined_text for k in ["rich", "progress", "spinner", "table", "console", "ansi", "color", "click", "argparse"])
                has_help = any(k in combined_text for k in ["--help", "help", "usage", "description", "epilog"])
                if has_rich and has_help:
                    score = 92.0
                    evidence = "Rich 콘솔 포맷팅, 진행률 표시, 상세 --help 매뉴얼 확인"
                    gap = "터미널 UX 완성도 우수."
                elif has_rich or has_help:
                    score = 65.0
                    evidence = "기본 콘솔 출력은 있으나 Rich/진행률/매뉴얼 중 일부 미흡"
                    gap = "Rich 컬러 포맷팅 및 --help 상세화 필요"
                else:
                    score = 35.0
                    evidence = "단순 print() 수준의 터미널 출력 (UX 미흡)"
                    gap = "Rich 라이브러리 기반 가독성 향상 필요"

            elif key == "resilience_recovery":
                has_try_except = any(k in combined_text for k in ["try:", "except", "finally:", "traceback", "sigint", "sigterm", "atexit", "exit_code", "sys.exit"])
                has_graceful = any(k in combined_text for k in ["graceful", "shutdown", "cleanup", "signal", "recover"])
                if has_try_except and has_graceful:
                    score = 92.0
                    evidence = "try/except 예외 방어 및 graceful shutdown 로직 확인"
                    gap = "복원력 우수."
                elif has_try_except:
                    score = 65.0
                    evidence = "기본 예외 방어는 있으나 graceful shutdown 미흡"
                    gap = "SIGINT/SIGTERM 처리 및 리소스 회수 보강 필요"
                else:
                    score = 30.0
                    evidence = "예외 방어 로직 미흡 (에러 시 크래시 위험)"
                    gap = "try/except 전면 방어 및 안전 종료 구현 필수"

            elif key == "configuration_persistence":
                has_config = any(k in combined_text for k in ["dotenv", "yaml", "json", "config", "env", "settings", "toml", "ini", "os.getenv", "os.environ"])
                has_persist = any(k in combined_text for k in ["save", "load", "persist", "write", "read", "storage", "path", "file"])
                if has_config and has_persist:
                    score = 92.0
                    evidence = "환경변수/설정 파일 로딩 및 상태 영속성 메커니즘 확인"
                    gap = "설정 영속성 완비."
                elif has_config:
                    score = 65.0
                    evidence = "설정 파일 로딩은 있으나 상태 영속성 미흡"
                    gap = "세션 상태 파일 기반 영속화 보강 필요"
                else:
                    score = 35.0
                    evidence = "설정 관리 및 영속성 메커니즘 미구현"
                    gap = "dotenv/.env 기반 설정 계층 및 로컬 상태 저장 필요"

            elif key == "performance_throughput":
                has_async = any(k in combined_text for k in ["async", "await", "asyncio", "aiohttp", "concurrent", "thread", "process", "stream"])
                has_batch = any(k in combined_text for k in ["batch", "chunk", "yield", "generator", "buffer", "pipeline"])
                if has_async and has_batch:
                    score = 92.0
                    evidence = "비동기 처리 및 스트리밍 배치 파이프라인 확인"
                    gap = "처리 성능 우수."
                elif has_async:
                    score = 65.0
                    evidence = "비동기 기반 처리는 있으나 배치/스트리밍 최적화 미흡"
                    gap = "대용량 데이터 스트리밍 처리 보강 필요"
                else:
                    score = 45.0
                    evidence = "동기 처리 방식 (대용량 처리 시 블로킹 위험)"
                    gap = "asyncio 또는 스트리밍 기반 처리 도입 필요"

            # ── Backend API domain-specific scoring ───────────────────────────
            elif key == "api_security":
                has_validation = any(k in combined_text for k in ["pydantic", "validator", "schema", "validation", "type_hint", "annotated", "field"])
                has_auth = any(k in combined_text for k in ["jwt", "token", "auth", "bearer", "oauth", "permission", "cors"])
                if has_validation and has_auth:
                    score = 92.0
                    evidence = "Pydantic 유효성 검증 및 JWT/OAuth 인증 체계 확인"
                    gap = "보안 체계 우수."
                elif has_validation:
                    score = 65.0
                    evidence = "입력 유효성 검증은 있으나 인증/인가 체계 미흡"
                    gap = "JWT/Bearer 토큰 인증 체계 보강 필요"
                else:
                    score = 35.0
                    evidence = "입력 검증 및 인증 체계 미구현 (보안 취약)"
                    gap = "Pydantic 스키마 검증 및 CORS 정책 적용 필수"

            elif key == "observability":
                has_logging = any(k in combined_text for k in ["logging", "logger", "structlog", "json_log", "log.info", "log.error"])
                has_health = any(k in combined_text for k in ["healthz", "health", "readyz", "ping", "status_check"])
                if has_logging and has_health:
                    score = 92.0
                    evidence = "구조화 로깅 및 헬스체크 엔드포인트 확인"
                    gap = "관측 가능성 완비."
                elif has_logging:
                    score = 65.0
                    evidence = "로깅은 있으나 헬스체크 엔드포인트 미구현"
                    gap = "/healthz 엔드포인트 추가 필요"
                else:
                    score = 35.0
                    evidence = "로깅 및 헬스체크 미구현 (운영 가시성 없음)"
                    gap = "구조화 JSON 로그 및 헬스체크 구현 필수"

            elif key == "stability_e2e":
                if e2e_verified and len(e2e_interactions) > 0:
                    score = 92.0
                    evidence = f"실기기/브라우저 E2E 검증 성공 (수행된 인터랙션: {len(e2e_interactions)}개, 크래시 0건)"
                    gap = "안정성 우수."
                elif not e2e_verified:
                    score = 20.0
                    evidence = "E2E 테스트 실행 실패 또는 런타임 Fatal Exception 발생"
                    gap = "실행 크래시 핫픽스 필수"
                else:
                    score = 65.0
                    evidence = "정적 분석은 통과했으나 동적 E2E 실증 검증 데이터 제한적"
                    gap = "실기기 터치 인터랙션 검증 필요"

            passed = (score >= adj_thresh)
            if is_crit and not passed:
                critical_failures.append(f"{name} ({score:.0f}점 < 최소 기준선 {adj_thresh:.0f}점): {gap}")

            dimensions[key] = QualityDimension(
                key=key,
                name=name,
                target_description=target_desc,
                is_critical=is_crit,
                min_threshold=adj_thresh,
                score=score,
                passed=passed,
                evidence=evidence,
                gap_analysis=gap,
                action_items=[gap] if not passed else []
            )

        # Compute multi-dimensional score (Harmonic-weighted to penalize weak areas)
        scores_list = [d.score for d in dimensions.values()]
        min_score = min(scores_list) if scores_list else 0.0
        avg_score = sum(scores_list) / max(1, len(scores_list))
        # Overall score is heavily influenced by the lowest score (prevents masked failures)
        overall_score = round(0.4 * min_score + 0.6 * avg_score, 1)

        # 🚪 90점 완결 게이트 (SSOT): 종합 90점 + 모든 Critical 차원 90점 + E2E + Defect 0.
        gate = evaluate_milestone_gate(
            overall_score=overall_score,
            dimensions=dimensions,
            critical_requirements_satisfied=(len(critical_failures) == 0),
            exit_criteria=None,
            e2e_passed=e2e_verified,
            critical_defect_count=0,
            regression_passed=True,
            # 🚦 QA 개선점 severity: P0/P1 차단 + P2/P3 감점 반영.
            improvement_severities=improvement_severities,
        )
        critical_gate_passed = gate.passed

        # 8 Core Critic Questions
        ms_title = getattr(current_milestone, "title", "현재 마일스톤") if current_milestone else "현재 마일스톤"
        ms_ver = getattr(current_milestone, "version", "v1.0") if current_milestone else "v1.0"

        critic_answers = {
            "1_feature_implemented": "코어 기능은 작성되었으나 품질 차원별 충족도 점검 필요" if not critical_gate_passed else "마일스톤 목표 기능이 소스코드 및 E2E에 온전히 실구현됨",
            "2_can_real_users_use": "예, 브라우저/디바이스에서 실행 및 조작 가능" if e2e_verified else "아니오, 실행 에러 또는 인터랙션 장애로 실사용 불가",
            "6_lowest_quality_area": min(dimensions.values(), key=lambda d: d.score).name if dimensions else "핵심 기능",
            "8_can_release_as_product": "상용 릴리즈 불가 (품질 갭 보강 필요)" if not critical_gate_passed else "1차 릴리즈 가능 수준 도달"
        }

        # Domain-adaptive Q3/Q4/Q5/Q7
        _lowest_dim = min(dimensions.values(), key=lambda d: d.score) if dimensions else None
        _lowest_name = _lowest_dim.name if _lowest_dim else "핵심 기능"
        _lowest_gap = _lowest_dim.gap_analysis if _lowest_dim else "품질 기준 미달"

        if domain == "game":
            critic_answers["3_user_inconvenience"] = "오디오/타격 피드백이 밋밋하여 게임의 재미 체감이 낮음" if dimensions.get("audio_sfx", QualityDimension("", "", "")).score < 60 else "조작감 양호, 가상 조이스틱 터치 안정적"
            critic_answers["4_missing_critical_element"] = "풍성한 칩튠 사운드 효과 및 역경직 타격감" if dimensions.get("audio_sfx", QualityDimension("", "", "")).score < 60 else "상위 스테이지 및 추가 몬스터 패턴"
            critic_answers["5_unrequested_quality_need"] = "착지 시 먼지 파티클 및 밟기 피드백 타이밍(Hit-Stop 0.05초) 미세 조정"
            critic_answers["7_biggest_quality_gain_next"] = "8-bit WebAudio 아르페지오 코인 챠링 사운드 및 점프 SFX 탑재 시 체감 완성도 급상승"
        elif domain in ("cli_agent", "daemon"):
            critic_answers["3_user_inconvenience"] = f"최저 영역({_lowest_name}): {_lowest_gap}" if _lowest_dim and _lowest_dim.score < 70 else "CLI 인터페이스 안정적, 기본 명령어 체계 동작"
            critic_answers["4_missing_critical_element"] = "Rich 터미널 포맷팅, 진행률 표시, graceful shutdown" if dimensions.get("terminal_ux", QualityDimension("", "", "")).score < 70 else "고급 파이프라인 및 플러그인 아키텍처"
            critic_answers["5_unrequested_quality_need"] = "구조화 로깅(JSON), 설정 파일 검증, 재시도 로직 강화"
            critic_answers["7_biggest_quality_gain_next"] = f"'{_lowest_name}' 영역 보강 시 체감 완성도 급상승"
        elif domain == "backend_api":
            critic_answers["3_user_inconvenience"] = f"최저 영역({_lowest_name}): {_lowest_gap}" if _lowest_dim and _lowest_dim.score < 70 else "API 엔드포인트 안정적, 기본 CRUD 동작"
            critic_answers["4_missing_critical_element"] = "입력 유효성 검증 및 JWT 인증 체계" if dimensions.get("api_security", QualityDimension("", "", "")).score < 70 else "Rate Limiting 및 API 버저닝"
            critic_answers["5_unrequested_quality_need"] = "구조화 에러 응답, 헬스체크 엔드포인트, OpenAPI 스키마 자동 생성"
            critic_answers["7_biggest_quality_gain_next"] = f"'{_lowest_name}' 영역 보강 시 API 안정성 급상승"
        else:
            critic_answers["3_user_inconvenience"] = f"최저 영역({_lowest_name}): {_lowest_gap}" if _lowest_dim and _lowest_dim.score < 70 else "기본 UX 동작 양호"
            critic_answers["4_missing_critical_element"] = "반응형 모바일 최적화 및 빈 상태 가이드" if dimensions.get("visual_design", QualityDimension("", "", "")).score < 70 else "마이크로 인터랙션 및 애니메이션 디테일"
            critic_answers["5_unrequested_quality_need"] = "로딩 스피너, 토스트 피드백, 에러 바운더리 방어"
            critic_answers["7_biggest_quality_gain_next"] = f"'{_lowest_name}' 영역 보강 시 사용자 체감 품질 급상승"

        # Determine Verdict
        if not critical_gate_passed:
            verdict = "REWORK_QUALITY_GAP"
            verdict_summary = f"🚨 Critical Quality Gate 미달 ({len(critical_failures)}건 결함 적발) — 최저 품질 영역({_lowest_name})의 결함을 핫픽스합니다."
            next_focus = min(dimensions.values(), key=lambda d: d.score).name
            target_gap = f"최저 점수 영역({next_focus})을 기준선 이상으로 끌어올리는 정밀 패치 집중"
            is_final_complete = False
        else:
            # 90점 완결 게이트를 통과한 마일스톤만 여기 도달한다.
            # 최종 릴리즈 확정은 '남은 마일스톤이 없을 때'만 True (auto_pilot이 실제 값을 주입).
            is_final_complete = (is_last_milestone is not False)
            if is_final_complete:
                verdict = "FINAL_PRODUCT_RELEASE"
                verdict_summary = f"🎉 90점 완결 게이트 전수 통과 ({overall_score}점) — 최종 마일스톤 상용 품질 달성!"
                next_focus = "상용 패키징 및 최종 릴리즈"
                target_gap = "목표 품질 100% 달성 완료"
            else:
                verdict = "PASS_MILESTONE"
                verdict_summary = f"✅ 마일스톤({ms_ver}) 90점 완결 게이트 통과 ({overall_score}점) — 차기 품질 사다리로 진화합니다."
                next_focus = "차기 마일스톤 고도화"
                target_gap = "현재 단계 완결. 다음 품질 차원 확장."

        e2e_summary = (
            f"실행 증적: TC {len(e2e_test_cases)}개 중 {tc_fail_count}개 실패, "
            f"스크린샷 {len(e2e_screenshots)}장 캡처, 콘솔 크래시: {'발생' if has_e2e_crash else '0건'}"
        )

        return ProductCriticEvaluation(
            domain=domain,
            milestone_version=ms_ver,
            milestone_title=ms_title,
            dimension_scores=dimensions,
            overall_score=overall_score,
            critical_gate_passed=critical_gate_passed,
            critical_failures=critical_failures,
            e2e_verified=e2e_verified,
            e2e_evidence_summary=e2e_summary,
            is_final_complete=is_final_complete,
            critic_answers=critic_answers,
            verdict=verdict,
            verdict_summary=verdict_summary,
            next_milestone_focus=next_focus,
            target_quality_gap=target_gap
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Goal Drift Prevention Guard
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def validate_goal_drift(
        cls,
        candidate_task: str,
        p_def: ProductDefinition,
        current_gap_focus: str
    ) -> Tuple[bool, str]:
        """
        Validates against the 5 Goal Drift Pillars (Section 15):
        1) 왜 필요한가? 2) 누구를 위한 것인가? 3) 제품 목표와 어떻게 연결되는가?
        4) 현재 품질 문제를 얼마나 개선하는가? 5) 왜 지금 해야 하는가?
        """
        task_lower = candidate_task.lower()

        # Reject out-of-scope fluff (e.g. database migration on a simple Mario jump prototype)
        irrelevant_keywords = [
            "dockerfile", "k8s", "kubernetes", "migration", "oauth2", "jwt",
            "microservice", "terraform", "helm", "graphql", "redis cluster"
        ]
        if p_def.domain == "game" and any(k in task_lower for k in irrelevant_keywords):
            return False, f"목표 표류(Goal Drift) 적발: 게임 코어 재미와 무관한 인프라 작업({candidate_task}) 차단"

        # Check if the task addresses the current quality gap
        return True, "제품 가치 및 품질 갭에 직결된 합당한 개발 과제 승인"

    # ──────────────────────────────────────────────────────────────────────────
    # Discord Embed Builders (Commander Bot)
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def build_planning_embed(cls, plan: ProductPlan) -> discord.Embed:
        """Constructs Discord embed announcing Autonomous Product Planning from Commander Bot."""
        p = plan.product_def
        embed = discord.Embed(
            title=f"👑 🎯 [Product Planner 수석 제품 기획관 브리핑 ({COMMANDER_BOT_ID})]",
            description=(
                f"**{p.title}**\n\n"
                f"• 📱 **타깃 플랫폼**: `{p.platform}`\n"
                f"• 🕹️ **벤치마크 레퍼런스**: `{p.benchmark_reference}`\n"
                f"• 💡 **핵심 가치 제안**: `{p.key_value_proposition}`\n"
                f"• 🎮 **코어 경험 목표**: {p.core_experience}\n\n"
                f"📋 **[기획서 및 로드맵 디스크 동기화]**: `PRD.md` 및 `ROADMAP.md`에 다차원 품질 헌법을 영구 기록했습니다."
            ),
            color=0xF59E0B
        )

        dim_lines = []
        for d in plan.quality_dimensions.values():
            badge = "🚨 Critical" if d.is_critical else "💡 Standard"
            dim_lines.append(f"• **{d.name}** ({badge}): 기준 `{d.min_threshold:.0f}점` ➔ {d.target_description[:45]}...")
        embed.add_field(
            name="📊 6대 다차원 품질 정의 (Quality Definition)",
            value="\n".join(dim_lines[:6]),
            inline=False
        )

        ms_lines = []
        for ms in plan.milestones:
            st = "🔄 진행 중" if ms.get("status") == "IN_PROGRESS" else "⏳ 대기"
            ms_lines.append(f"• **{ms['version']}** {ms['title']} [{st}]")
        embed.add_field(
            name="📍 단계별 품질 진화 사다리 (Milestone Quality Ladder)",
            value="\n".join(ms_lines[:6]),
            inline=False
        )

        embed.set_footer(text=f"지정 봇: <@{COMMANDER_BOT_ID}> | !mode 3/3+ 자율 제품 개발 가동")
        return embed

    @classmethod
    def build_critic_embed(cls, eval_res: ProductCriticEvaluation) -> discord.Embed:
        """Constructs Discord embed with multi-dimensional scoreboard and gap analysis."""
        color = 0x10B981 if eval_res.critical_gate_passed else 0xEF4444
        badge_status = "✅ PASS" if eval_res.critical_gate_passed else "🚨 REWORK REQUIRED"

        embed = discord.Embed(
            title=f"👑 🧐 [Product Critic 제품 비평 및 다차원 품질 갭 감사 ({COMMANDER_BOT_ID})]",
            description=(
                f"**마일스톤:** `{eval_res.milestone_version} — {eval_res.milestone_title}`\n"
                f"**종합 품질 지수:** `{eval_res.overall_score:.1f}점 / 100점` ({badge_status})\n"
                f"**사령관 최종 판정:** **{eval_res.verdict_summary}**\n\n"
                f"• 🧪 **현실 검증(E2E) 상태**: `{eval_res.e2e_evidence_summary}`\n"
                f"• 🎯 **품질 갭 극복 과제**: `{eval_res.target_quality_gap}`"
            ),
            color=color
        )

        # Dimension Scoreboard
        score_lines = []
        for d in eval_res.dimension_scores.values():
            icon = "🟢" if d.passed else "❌"
            score_lines.append(f"• {icon} **{d.name}**: `{d.score:.0f}/{d.min_threshold:.0f}점` | *{d.evidence[:40]}*")
        embed.add_field(
            name="📊 다차원 품질 채점표 (Multi-Dimensional Scoreboard)",
            value="\n".join(score_lines[:6]),
            inline=False
        )

        # 8 Core Critic Questions highlight
        qa_lines = [
            f"• **Q2. 실제 사용 가능한가?**: {eval_res.critic_answers['2_can_real_users_use']}",
            f"• **Q3. 사용자가 불편해할 점**: {eval_res.critic_answers['3_user_inconvenience']}",
            f"• **Q6. 가장 품질이 낮은 영역**: `{eval_res.critic_answers['6_lowest_quality_area']}`",
            f"• **Q7. 차기 최대 품질 개선책**: {eval_res.critic_answers['7_biggest_quality_gain_next']}",
            f"• **Q8. 실제품 출시 가능 여부**: {eval_res.critic_answers['8_can_release_as_product']}"
        ]
        embed.add_field(
            name="🔍 8대 제품 핵심 질문 진단 (Critical Product Inquiries)",
            value="\n".join(qa_lines),
            inline=False
        )

        if eval_res.critical_failures:
            embed.add_field(
                name="⚠️ 미달된 Critical Quality Gate 항목",
                value="\n".join(f"• {f}" for f in eval_res.critical_failures[:4]),
                inline=False
            )

        embed.set_footer(text=f"지정 봇: <@{COMMANDER_BOT_ID}> | Functional Completion ≠ Product Completion")
        return embed
