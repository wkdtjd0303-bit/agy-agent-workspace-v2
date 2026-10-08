import discord
from typing import Dict, Any, Optional, List
import config
from model_manager import GlobalModelManager

# 브랜드 배너 바인더 (생성 에셋 → Discord UI 연결). 미설치/오류 시 조용히 비활성.
try:
    import brand_ui as _brand_ui
except Exception:  # pragma: no cover - 방어적 폴백
    _brand_ui = None


def get_brand_files() -> List[Any]:
    """`!help` 발송 시 첨부할 브랜드 배너 파일 목록 (없으면 빈 리스트)."""
    if _brand_ui is None:
        return []
    try:
        return _brand_ui.banner_files()
    except Exception:
        return []


def _bind_brand_banner(embed: discord.Embed) -> bool:
    """overview 임베드에 브랜드 배너를 실제로 바인딩한다 (에셋 없으면 no-op)."""
    if _brand_ui is None:
        return False
    try:
        return _brand_ui.apply_brand_banner(embed, mode="image")
    except Exception:
        return False


def _image_backend_desc() -> str:
    """현재 구성된 '실제' 이미지 생성 백엔드 설명 (안내 문구를 실제 동작과 일치시킴)."""
    try:
        from image_generation_backend import ImageGenerationBackend
        return ImageGenerationBackend.describe_backends() or "미구성 (OPENROUTER_API_KEY 또는 GEMINI_API_KEY 필요)"
    except Exception:
        return "미구성 (OPENROUTER_API_KEY 또는 GEMINI_API_KEY 필요)"


class HelpManager:
    """
    AGY 4-Agent Collaborative Platform Help & Documentation Engine
    Provides rich, interactive, category-based guides with buttons and select menu.
    Fully reflects all latest commands:
    - Daily Core / Golden 5 (!help, !plan, !mode, !reset, !stop, @Antigravity)
    - Orchestration & Planning (!plan, !setup, !mode 1/2/2+/auto/read/all, !single, !pair, !read, !image)
    - QA Squad & E2E Testing (!qa, !qa select, !qasetup, !e2e, !sim, !apk, !colab, !android_test, !win_test)
    - Commander & Cloud Backup (!commander, !export, !drive, !zip, !git)
    - System & Safety Guardrails (!status, !quota, !temp, !watchdog, !selfhealing, !model, !identity, !engine, !bots, !queue, !clear-queue, !pwd, !cd)
    """

    @staticmethod
    def get_overview_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="🤖 AGY 4-Agent 자율 협업 플랫폼 종합 도움말",
            description=(
                "Google Antigravity(AGY) 기반 **Commander ↔ Coder ↔ QA ↔ Auditor** 4대 AI 두뇌가 유기적으로 협업하는 차세대 바이브 코딩 시스템입니다.\n\n"
                "💡 **원하는 카테고리 버튼을 클릭**하거나 아래 드롭다운 메뉴를 선택하여 상세 도움말을 확인하세요!"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name="⭐ 외워야 할 핵심 필수 명령어 5선 (Golden 5)",
            value=(
                f"1️⃣ **`@Antigravity <자연어>`** : 만능 자율 배차 (기획/코딩/버그수정 95% 해결)\n"
                f"2️⃣ **`{p}plan`** (또는 `{p}기획`) : 원터치 프로젝트 대화형 기획 위저드 착수\n"
                f"3️⃣ **`{p}stop`** (또는 `{p}멈춰`) : 0초 지연 비상 즉시 정지 / 프로세스 킬 (`{p}stop all` 대기열 포함 전체 취소)\n"
                f"4️⃣ **`{p}new`** (또는 `{p}reset`) : 대화 세션 초기화 & 핫리로드 원스톱 리셋\n"
                f"5️⃣ **`{p}help`** (또는 `{p}도움말`) : 인터랙티브 버튼형 전체 제어판\n"
                f"👉 *수십 개 명령어를 외울 필요 없이 위 5개만으로 모든 개발/운영이 완결됩니다!*"
            ),
            inline=False
        )

        embed.add_field(
            name="🎯 3초 요약: 어떻게 쓰면 되나요? (초간단 사용법 3단계)",
            value=(
                f"1️⃣ **평소 기능 개발 / 버그 수정**: 편하게 채팅하거나 `{p}pair <요청>`\n"
                f"   • 예: `{p}pair 모바일 반응형 헤더와 다크모드 토글 스위치 구현해줘`\n"
                "   • Coder(개발) ➔ QA(E2E/실기기 테스트) ➔ Auditor(검토)가 알아서 완성!\n"
                f"2️⃣ **새 프로젝트 기획부터 시작**: `{p}plan <아이디어>` 또는 `{p}setup`\n"
                f"   • 예: `{p}plan 일정 관리 SaaS` ➔ 플랫폼/기능/스타일 고르고 1클릭 시작\n"
                f"3️⃣ **퇴근/외출 시 완전 무인 개발**: `{p}mode 3` (3중 협업 모드 고정)\n"
                f"   • 예: `{p}mode 3` 입력 후 *\"차기 로드맵의 핵심 기능들을 이어서 구현해줘\"*\n"
                "   • Coder ➔ QA ➔ Auditor 3중 협업 자율 순환 개발!"
            ),
            inline=False
        )

        embed.add_field(
            name="💡 상황별 빠른 명령어 (내가 필요한 순간)",
            value=(
                f"• ⚡ **빠른 단일 질의 / 스크립트**: `{p}single <질문>` (경량 코드 및 빠른 답변)\n"
                f"• 🔍 **코드 무수정 기획/설계 자문**: `{p}read <질문>` (3인의 시니어 아키텍트 토론)\n"
                f"• 🌐 **유니버설 E2E 자율 검증**: `{p}e2e` (웹/Android/Windows/iOS 200대 전수 감사)\n"
                f"• 📱 **실시간 모바일 화면 확인**: `{p}sim` (60fps 웹 폰 뷰어 & 실시간 터치 조작)\n"
                f"• 🔨 **안드로이드 APK 즉시 다운로드**: `{p}apk` (구글 드라이브 1클릭 다운로드 링크)\n"
                f"• 🎛️ **AI 모델 실시간 변경**: `{p}model` (인터랙티브 모델 제어판)\n"
                f"• 🛑 **작업 멈춤 / 세션 리셋**: `{p}stop` (즉시 중단), `{p}reset` (올인원 핫리셋)"
            ),
            inline=False
        )

        # Build comprehensive multi-agent role fields with explicit model identifiers
        ext_cfgs = config.get_extended_qa_configs()
        qa_count = 1 + len(ext_cfgs) if ext_cfgs else 1

        core_role_lines = [
            f"• 👑 **Commander (의도 분석 및 관제 사령관)** — 구동: `{GlobalModelManager.get_role_model_display('commander')}` (`{config.COMMANDER_MODEL}`)\n  └ 실시간 의도 분석 & Single/Pair/Quad 스마트 배차 관제",
            f"• 👨‍💻 **Coder (초고속 개발 및 기능 구현)** — 구동: `{GlobalModelManager.get_role_model_display('coder')}` (`{config.CODER_MODEL}`)\n  └ 다중 파일 생성, 반응형 UI 조립, 컴파일/빌드/배포 완결",
            f"• ⚖️ **Client Auditor (대리 기획자 최종 감사)** — 구동: `{GlobalModelManager.get_role_model_display('auditor')}` (`{config.AUDITOR_MODEL}`)\n  └ PO 관점 PRD/헌법 100% 전수 대조, 최종 수락 및 차기 로드맵 기획",
            f"• 🎨 **Image generator (실제 AI 이미지 생성 도구)** — 구동: `{GlobalModelManager.get_role_model_display('image')}` (`{getattr(config, 'IMAGE_MODEL', 'gemini-3.8-flash-high')}`)\n"
            f"  └ Coder/QA가 `[IMAGE_REQUEST: ...]`(생성) / `[IMAGE_EDIT: ...]`(수정) 또는 `!image` 로 호출하는 이미지 생성·수정 전용 도구 봇\n"
            f"  └ 실제 이미지 백엔드(OpenRouter Nano Banana 계열 Gemini image): {_image_backend_desc()}",
            f"• 🖼️ **Vision Analyst (첨부 이미지 분석)** — 구동: `{GlobalModelManager.get_role_model_display('vision')}`\n"
            f"  └ 사용자가 첨부한 이미지·스크린샷의 OCR/레이아웃/스타일을 정밀 분석해 Coder/QA 프롬프트에 주입 (`{config.COMMAND_PREFIX}model vision <모델>` 로 변경)",
        ]

        qa_role_lines = [
            f"• 🕵️‍♂️ **qa1 (기본 검증관)** — 구동: `{GlobalModelManager.get_role_model_display('qa1')}` (`{config.QA_MODEL}`)\n  └ Playwright 웹 4대 뷰포트 & Android MCP 12개 도구 물리 단언"
        ]
        for cfg in ext_cfgs:
            role_key = cfg.get("role_key", cfg["name"].lower())
            m_disp = GlobalModelManager.get_role_model_display(role_key)
            qa_role_lines.append(
                f"• 🕵️‍♂️ **{cfg['name']}** — 구동: `{m_disp}` (`{cfg['model']}`)\n  └ {cfg['engine'].upper()} 기반 교차 검증 및 심층 코드 품질 점검"
            )

        embed.add_field(
            name="👥 핵심 개발 & 관제 에이전트 (구동 모델 표기)",
            value="\n".join(core_role_lines),
            inline=False
        )

        embed.add_field(
            name=f"🕵️ QA 교차 검증 부대 (QA 1~{qa_count} 구동 모델 표기)",
            value="\n".join(qa_role_lines),
            inline=False
        )

        embed.set_footer(text="AGY Collaborative System · 도움말 메뉴 버튼을 눌러 상세 정보를 확인하세요.")
        # 생성된 브랜드 배너 에셋을 overview UI에 실제 바인딩 (에셋 없으면 no-op)
        _bind_brand_banner(embed)
        return embed

    @staticmethod
    def get_golden5_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="⭐ [AGY Golden 5] 외워야 할 핵심 필수 명령어 5선",
            description=(
                "수십 가지 복잡한 명령어를 일일이 기억하실 필요가 전혀 없습니다!\n"
                "아래 **5가지 핵심 필수 명령어(Golden 5)**만 사용하셔도 기획, 자율 코딩, 실시간 관제, 비상 정지, 세션 리셋까지 100% 완결됩니다."
            ),
            color=discord.Color.gold()
        )

        embed.add_field(
            name="1️⃣ @Antigravity <자연어 요청> (만능 자율 배차기 - 95% 작업 처리)",
            value=(
                "• **역할**: 일상적인 모든 개발, 기능 추가, UI 수정, 버그 해결을 자율 처리합니다.\n"
                "• **특징**: 특정 명령어를 몰라도 평소 말하듯이 대화하면 사령관(Commander)이 의도를 자동 분석하여 Coder ↔ QA ↔ Auditor 협업 체인을 즉시 가동합니다.\n"
                "• **예시**: `@Antigravity 반응형 웹 대시보드에 다크모드 스위치와 실시간 차트 추가해줘`"
            ),
            inline=False
        )

        embed.add_field(
            name=f"2️⃣ {p}plan 또는 {p}기획 (스마트 프로젝트 기획 위저드)",
            value=(
                "• **역할**: 새로운 웹/앱 프로젝트를 백지 상태에서 시작할 때 1분 만에 세팅을 끝냅니다.\n"
                "• **특징**: 대화형 모달과 버튼 UI를 통해 플랫폼, 벤치마크, 킬러 기능, 디자인 감성을 클릭으로 고르고 즉시 자율 착수합니다.\n"
                "• **예시**: `!plan 슬더스 스타일 덱빌딩 카드 게임`"
            ),
            inline=False
        )

        embed.add_field(
            name=f"3️⃣ {p}stop / {p}done (즉각 정지 vs 회차 완료 후 안전 종료)",
            value=(
                f"• **`{p}stop` (0초 비상 정지)**: 일반 큐를 우회하여 세션/QA/프로세스를 즉시 강제 종료합니다 (`{p}stop all` 대기열 포함 전체 취소).\n"
                f"• **`{p}done` (회차 완료 후 안전 종료)**: Mode 3/3+ 무인 순환 시 현재 마일스톤(Coder ➔ QA ➔ Auditor)을 100% 정상 완결 후 차기 순환 없이 정상 대기로 안전 전환합니다.\n"
                f"• **별칭**: `{p}완료`, `{p}finish`, `{p}마무리`, `{p}끝`"
            ),
            inline=False
        )

        embed.add_field(
            name=f"4️⃣ {p}new 또는 {p}reset (원스톱 핫리셋 & 새 세션)",
            value=(
                "• **역할**: 이전 대화 맥락을 깔끔하게 지우고 새 작업을 시작할 때 사용합니다.\n"
                "• **특징**: 세션 컨텍스트 초기화 + 메모리 정리(GC) + 봇 모듈 핫리로드(Hot-reload)를 원스톱으로 처리합니다.\n"
                "• **별칭**: `!초기화`, `!리셋`, `!clear`, `!reload`"
            ),
            inline=False
        )

        embed.add_field(
            name=f"5️⃣ {p}help 또는 {p}도움말 (인터랙티브 대시보드)",
            value=(
                "• **역할**: 모든 고급 제어 기능(시뮬레이터, 설정, 상태, APK 등)을 마우스 클릭으로 다룹니다.\n"
                "• **특징**: 텍스트 명령어를 일일이 타이핑할 필요 없이 버튼과 드롭다운으로 직관적 조작이 가능합니다."
            ),
            inline=False
        )

        embed.add_field(
            name="💡 보조 원클릭 파워툴 (필요할 때만 쏙 골라쓰기)",
            value=(
                f"• `{p}sim` (또는 `{p}시뮬`) : 60fps 실시간 모바일/웹 뷰어 및 Gemini Vision E2E 인스펙터 링크 발급\n"
                f"• `{p}apk` : 안드로이드 실기기 설치용 APK 1클릭 클라우드 빌드\n"
                f"• `{p}model` : 실시간 인터랙티브 모델 대시보드 (역할별 AI 모델 원터치 교체)\n"
                f"• `{p}mode 3` : 퇴근/외출 시 사람 없이 지속되는 3중 협업 무인 모드"
            ),
            inline=False
        )

        embed.set_footer(text=f"AGY Golden 5 System · 언제든 {p}golden5 또는 {p}핵심 으로 이 치트시트를 호출할 수 있습니다.")
        return embed

    @staticmethod
    def get_core_embed() -> discord.Embed:
        """Daily Core essential guide (alias/variant of Golden 5)."""
        return HelpManager.get_golden5_embed()

    @staticmethod
    def get_dev_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="🚀 [1. 코딩 & 기획 (Dev & Planning) 명령어 가이드]",
            description="3중/다중 협업, 단일 초고속 개발, 대화형 기획 위저드 및 동작 모드 설정 가이드입니다.",
            color=discord.Color.blue()
        )

        ext_cfgs = config.get_extended_qa_configs()
        qa_names = " ➔ ".join([c["name"].lower() for c in ext_cfgs]) if ext_cfgs else "qa1"
        quad_label = f"{len(ext_cfgs) + 3}중 다중" if len(ext_cfgs) > 1 else ("4중 쿼드" if len(ext_cfgs) == 1 else "3중")

        embed.add_field(
            name="👥 협업 & 단일 개발 직접 실행 명령어",
            value=(
                f"• `{p}pair <요청>` : Coder(`{config.CODER_MODEL}`) ↔ QA(`{config.QA_MODEL}`) ↔ Auditor(`{config.AUDITOR_MODEL}`) 3중 협업 개발\n"
                f"  *(최대 {config.PAIR_MAX_ROUNDS}라운드 반복 교차 검증으로 완벽한 프로덕션 코드 완성)*\n"
                f"  💡 **실전 예시**: `{p}pair 반응형 대시보드 화면에 일일 통계 차트와 다크모드 추가해줘`\n"
                f"• `{p}single <요청>` : 초고속 단일 Coder(`{config.CODER_MODEL}`) 모드로 즉시 코딩 및 답변\n"
                f"  💡 **실전 예시**: `{p}single 폴더 내 이미지들을 일괄 리사이징하는 파이썬 스크립트 작성해줘`\n"
                f"• `{p}image <요청>` : 🎨 **Image generator 실제 AI 이미지 생성** (Nano Banana 계열 · 백엔드: {_image_backend_desc()})\n"
                f"  💡 **실전 예시**: `{p}image 우주 테마 앱 로고 (asset_name=app_logo ratio=1:1)`\n"
                f"• `{p}image edit <원본경로> <수정요청>` : ✏️ **기존 이미지 AI 편집(수정)** — 원본 입력 후 부분 수정\n"
                f"  💡 **실전 예시**: `{p}image edit app/src/main/res/drawable/hero.png 배경을 더 어둡게`\n"
                f"• `{p}pair+ <요청>` : 👥 **{quad_label} 협업 개발** (Coder ↔ qa1 ↔ {qa_names} ↔ Auditor)\n"
                f"• `{p}single_qa2 <요청>` : 🥷 **단일 qa2 검증관 전용 실행**\n"
                f"• `{p}read <질문>` / `{p}기획 <질문>` : 100% 무수정 읽기/기획 전용 자문\n"
                f"  *(기존 코드를 건드리지 않고 아키텍트 3명이 최적의 설계 및 방향성 모색)*"
            ),
            inline=False
        )

        embed.add_field(
            name="🎯 1클릭 셋업 & 대화형 기획 위저드",
            value=(
                f"• `{p}plan [아이디어]` : 📋 **5단계 인터랙티브 기획 위저드**\n"
                f"  *(웹/SaaS, 2D/3D 게임, 레트로 픽셀 게임, 윈도우 네이티브, 안드로이드 모바일 등 템플릿·벤치마크·기능 1분 기획)*\n"
                f"  💡 **실전 예시**: `{p}plan 슬더스 스타일 덱빌딩 카드 게임`\n"
                f"• `{p}setup` : 🎯 **채널 프로젝트 맞춤형 1클릭 프리셋 UI**\n"
                f"• `{p}setup [web|app|windows|game|store|bot|read]` : 텍스트로 프리셋 즉시 적용\n"
                f"  - `web` : 웹 & SaaS 풀스택 (Playwright E2E)\n"
                f"  - `app` : 모바일 & 안드로이드 네이티브 (Android MCP 12개 도구 & !sim)\n"
                f"  - `windows` : 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6, Fluent Design)\n"
                f"  - `game` : 게임 (Web Canvas/WebGL · Unity · Godot · Pygame, 60fps)\n"
                f"  - `store` : 커머스/스토어 랜딩 (CRO 최적화 & OG 태그)\n"
                f"  - `bot` : 백그라운드 자동화/데몬/봇\n"
                f"  - `read` : 100% 무수정 기획 & 리서치 자문"
            ),
            inline=False
        )

        embed.add_field(
            name="⚙️ 채널 동작 모드 완벽 가이드 (!mode)",
            value=(
                f"• `{p}mode auto` : 🤖 **스마트 자동 모드 (기본 권장)** (Commander가 의도 분석 후 Single/Pair 자동 배차)\n"
                f"• `{p}mode 3+` : 👥 **무인 자율 순환 다중 협업 모드** (Commander ➔ Coder ➔ QA Squad ➔ Auditor ➔ 자율 순환)\n"
                f"• `{p}mode 3` : 👥 **무인 자율 순환 3중 협업 모드** (Commander 5W1H 적응형 기획 ➔ Coder ➔ QA ➔ Auditor ➔ 자율 순환)\n"
                f"• `{p}mode 2+` (`{p}mode pair+`) : 👥 **{quad_label} 협업 고정** (단일 과제 완결 후 대기)\n"
                f"  - 💡 **특정 QA만 지정**: `{p}mode 2+ qa2` (qa2만), `{p}mode 2+ 2,3` (복수 지정), `{p}mode 2+ all` (전체 복원)\n"
                f"• `{p}mode 2` (`{p}mode pair`) : 👥 **3중 협업 모드 고정** (Coder ↔ QA ↔ Auditor 완결 후 사용자 대기)\n"
                f"• `{p}mode single_qa2` (`{p}mode qa2`) : 🥷 **단일 qa2 전용 모드**\n"
                f"• `{p}mode 1` / `{p}mode single` : 👤 **단일 에이전트 모드 고정** (초고속 단일 답변)\n"
                f"• `{p}mode read` : 🔍 **3중 읽기/기획 전용 모드 고정** (코드 무수정 아키텍처 자문)\n"
                f"• `{p}mode all [auto|3+|3|2+|2|1|read]` : 🌐 **모든 채널 일괄 모드 변경**\n"
                f"• `{p}role qa` / `{p}role coder` : 🎭 **채널 역할 전환** (개발자 ↔ 시니어 QA 전담)"
            ),
            inline=False
        )

        embed.add_field(
            name="🎛️ 멀티 AI 엔진 & 외부 연동 가이드 (!engine)",
            value=(
                "Antigravity CLI 외에도 OpenRouter API, Hermes Agent, Codex, Claude 등을 직접 연동할 수 있습니다.\n"
                f"• 🩺 `{p}engine doctor` : 전체 7대 엔진 설치, 바이너리 경로, 버전 및 API 키 인증 정밀 점검\n"
                f"• 🪽 `{p}engine coder hermes` / `{p}engine qa hermes` : Nous Research 완전 자율형 Hermes 에이전트 연동\n"
                f"• 🧠 `{p}engine coder codex` / `{p}engine qa codex` : OpenAI Codex CLI(o3-mini/gpt-4o) 연동\n"
                f"• 🟣 `{p}engine coder claude` / `{p}engine qa claude` : Anthropic Claude Code 연동\n"
                f"• ⚡ `{p}engine coder opencode` / `{p}engine qa opencode` : OpenCode CLI(멀티 프로바이더·세션 지속·MCP) 연동\n"
                f"• 🌐 `{p}engine qa [qwen|deepseek|claude-3.7]` : OpenRouter 다양한 모델 검증관 배정\n"
                f"• 🔄 `{p}engine coder agy` & `{p}engine qa agy` : Antigravity 기본 엔진으로 원복\n"
                f"• 🤖 `{p}identity` (또는 `{p}whoami`) : 현재 채널 세션의 에이전트 정체성(AGY vs Hermes) 및 배정 모델 확인"
            ),
            inline=False
        )

        embed.set_footer(text="AGY Collaborative System · Category: Coding & Planning")
        return embed

    @staticmethod
    def get_auto_embed() -> discord.Embed:
        embed = discord.Embed(
            title="⚠️ [오토파일럿 기능 제거됨]",
            description="AutoPilot 자율 순환 기능은 토큰 보호를 위해 영구 제거되었습니다.\n대신 `!mode 3` (무인 자율 개발) 모드를 사용하세요.",
            color=0xFF6B6B
        )
        return embed

    @staticmethod
    def get_loop_embed() -> discord.Embed:
        """Alias to autonomous loop embed."""
        return HelpManager.get_auto_embed()

    @staticmethod
    def get_qa_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="🧪 [3. QA 허브 & E2E / 모바일 스튜디오 명령어]",
            description="Playwright 웹 4대 뷰포트, Android MCP 12개 물리 도구, Windows 가상 프레임버퍼 200대 전수 기계 검증 체계입니다.",
            color=discord.Color.green()
        )

        embed.add_field(
            name="🧪 QA 스쿼드 제어 및 E2E 검증 명령어",
            value=(
                f"• `{p}qa` : 📋 **전체 QA 스쿼드(qa1 ~ qa10) 가동 현황 및 채널 필터링 상태 조회**\n"
                f"• `{p}qa select qa2` (또는 `{p}qa 2`) : 특정 QA만 단독 지정\n"
                f"• `{p}qa select 2,3` : 복수 QA 선택 지정\n"
                f"• `{p}qa reset` (또는 `{p}qa all`) : 전체 확장 QA 전수 복원\n"
                f"• `{p}qasetup` (또는 `{p}qa-setup`) : 🎛️ **대화형 QA 위저드** (종합 정밀, 핵심 로직, UI/UX 디자인 등 포커스 설정)\n"
                f"• `{p}e2e [대상]` : 🌐 **유니버설 제로 컨피그 E2E 자율 검증 (REQ-007)**\n"
                f"  *(웹, 안드로이드, 윈도우 200대 감사 체크리스트 전수 기계 검증)*\n"
                f"• `{p}android_test` : 안드로이드 AVD 에뮬레이터 MCP 도구 기반 정밀 테스트\n"
                f"• `{p}win_test` : 윈도우 Wine Xvfb 가상 프레임버퍼 GUI/EXE 정밀 테스트\n"
                f"• `{p}colab` : Google Colab 클라우드 Headed 안드로이드 런너 링크 발급"
            ),
            inline=False
        )

        embed.add_field(
            name="📱 실시간 시뮬레이터 & APK 클라우드 빌드",
            value=(
                f"• `{p}sim` (또는 `{p}시뮬`) : 📱 **60fps 실시간 모바일 인터랙티브 스튜디오 & Vision AI 뷰어**\n"
                f"  *(iPhone 16 Pro, Galaxy S23 등 실시간 터치/스크롤 조작 & Gemini Vision 시각 검증)*\n"
                f"• `{p}apk` (또는 `{p}앱`, `{p}빌드`) : 🔨 **안드로이드 APK 실시간 빌드 & 구글 드라이브 자동 배포**\n"
                f"  *(사용자가 시간을 요청하지 않아도 현재 시각 타임스탬프 APK와 latest.apk 동시 생성)*"
            ),
            inline=False
        )

        embed.add_field(
            name="🤖 Android MCP E2E 12대 물리 도구",
            value=(
                "• **12대 표준 MCP 도구**: `launch_app`, `tap_element`, `type_text`, `press_key`, `swipe_screen`, `navigate_page`, `restart_app`, `inject_asset`, `verify_screen`, `verify_file`, `verify_app_state`, `inspect_screen_vision`\n"
                "• **원자적 Action ↔ Assertion (`StepContext`)**: 액션 전/후 Framebuffer 캡처 및 사후조건 물리 단언 영구 보관\n"
                "• **실시간 자율 복구**: 다이얼로그 팝업 선제 자동 해제, 스와이프 재탐색, 백트래킹 복구"
            ),
            inline=False
        )

        embed.set_footer(text="AGY Collaborative System · Category: QA Hub & Mobile Studio")
        return embed

    @staticmethod
    def get_commander_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="👑 [4. 관제 사령관 (Commander) & 클라우드 백업]",
            description="실시간 AI 의도 분석 사령관 현황과 채널 대화 백업 및 대용량 클라우드 연동 도구입니다.",
            color=discord.Color.purple()
        )

        embed.add_field(
            name="👑 Commander (AI 의도 분석 관제탑)",
            value=(
                f"• 탑재 모델: `{config.COMMANDER_MODEL}` *({GlobalModelManager.get_role_model_display('commander')})*\n"
                f"• `{p}commander` / `{p}cmd` : 사령관 현황, 분석 통계 및 최근 판정 이력 조회\n"
                f"• `{p}commander test <메시지>` : 특정 프롬프트의 Single vs Pair 배차 모의 판정 테스트"
            ),
            inline=False
        )

        embed.add_field(
            name="📜 채널 대화 백업 & 클라우드 영구 보관",
            value=(
                f"• `{p}export` / `{p}대화백업` : 채널 전체 대화 기록을 Markdown(.md) 문서로 정리 및 Google Drive 자동 업로드\n"
                f"• `{p}export 50 txt` : 최근 50개 메시지를 텍스트(.txt) 형식으로 추출"
            ),
            inline=False
        )

        embed.add_field(
            name="☁️ 대용량 파일 전송 & 압축",
            value=(
                f"• `{p}drive [경로]` / `{p}gdrive` : 워크스페이스/APK 파일을 Google Drive에 업로드하고 1클릭 다운로드 링크 생성\n"
                f"• `{p}zip [하위경로]` : 워크스페이스 전체 압축(.zip) 파일을 생성하여 다운로드 제공\n"
                f"• `{p}git` / `{p}github` : Git 브랜치 상태, 최신 커밋 이력 및 GitHub 링크 표출"
            ),
            inline=False
        )

        embed.set_footer(text="AGY Collaborative System · Category: Commander & Cloud Storage")
        return embed

    @staticmethod
    def get_system_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="⚙️ [5. 시스템 진단 / 대기열 / 안전망 (System & Admin)]",
            description="봇 운영 라이프사이클, 텔레메트리, AI 모델 제어판, 발열 감시 및 안전 가드레일 콘솔입니다.",
            color=discord.Color.dark_teal()
        )

        embed.add_field(
            name="🖥️ 시스템 텔레메트리 & 리소스",
            value=(
                f"• `{p}status` : 게이트웨이 가동 시간, 메모리/CPU 점유율, 채널별 세션 상태 확인\n"
                f"• `{p}status_refresh` : 텔레메트리 및 센서 상태 즉각 동기화\n"
                f"• `{p}quota` : Antigravity AI 모델 잔여 쿼터 및 리셋 잔여 시간 실시간 확인\n"
                f"• `{p}temp` / `{p}thermal` : 서피스 하드웨어 CPU 실시간 온도 및 3단계 서멀 가드 상태\n"
                f"• `{p}watchdog` : 10대 자율 안전 가드레일 상태 및 서킷 브레이커 리셋 (`{p}watchdog reset`)\n"
                f"• `{p}selfhealing [on|off]` : 고립 프로세스/세션 자가 치유 안전망"
            ),
            inline=False
        )

        embed.add_field(
            name="🎛️ AI 모델 제어판 & 에이전트 정체성",
            value=(
                f"• `{p}model` : 🎛️ **실시간 인터랙티브 모델 대시보드 UI** (역할별 AI 모델 원터치 교체)\n"
                f"• `{p}identity` (또는 `{p}whoami`) : 현재 채널의 에이전트 정체성(AGY vs Hermes) 및 배정 모델 확인\n"
                f"• `{p}bots` : 4대 AI 두뇌(Commander, Coder, QA, Auditor) 역할 및 상태 안내"
            ),
            inline=False
        )

        embed.add_field(
            name="📁 작업 경로 및 대기열 제어",
            value=(
                f"• `{p}pwd` : 현재 작업 폴더(CWD), 4대 모델, 세션 ID 및 상태 확인\n"
                f"• `{p}cd <경로>` : 프로젝트 작업 디렉토리 변경\n"
                f"• `{p}queue` : 예약된 대기열 목록 조회 | `{p}clear-queue` : 대기열 전체 삭제\n"
                f"• `{p}reset` (또는 `{p}new`) : 원스톱 핫리셋 (대화 세션 초기화 + 메모리 정리 + 모듈 핫리로드)\n"
                f"• `{p}done` (또는 `{p}완료`) : Mode 3 진행 중인 회차 완결 후 안전 종료 (정상 대기 전환)\n"
                f"• `{p}stop` (또는 `{p}멈춰`) : 0초 지연 비상 즉각 정지 (`{p}stop all` 대기열 포함 전체 취소)"
            ),
            inline=False
        )

        embed.set_footer(text="AGY Collaborative System · Category: System & Ops")
        return embed

    @staticmethod
    def get_admin_embed() -> discord.Embed:
        """Alias to system and admin embed."""
        return HelpManager.get_system_embed()

    @staticmethod
    def get_tips_embed() -> discord.Embed:
        p = config.COMMAND_PREFIX
        embed = discord.Embed(
            title="🎯 [6. 구체적 실전 사용 방법 & 모범 워크플로우]",
            description="실제 작업 상황에서 어떤 명령어를 어떤 순서로 입력하면 되는지 5대 실전 워크플로우와 밤샘 무인 공식입니다.",
            color=discord.Color.gold()
        )

        embed.add_field(
            name="📌 Case 1: 새 프로젝트 기획부터 시작할 때 (3단계)",
            value=(
                f"1️⃣ **환경 셋업**: `{p}setup web` (웹/SaaS) 또는 `{p}setup app` (모바일 안드로이드)\n"
                f"2️⃣ **기획 위저드**: `{p}plan <아이디어>` 입력 ➔ 대화형 UI에서 대상 사용자, 핵심 기능, 디자인 스타일 선택\n"
                f"3️⃣ **개발 착수**: `{p}pair 기획서 기반으로 첫 프로토타입 메인 화면과 핵심 기능 구현해줘`"
            ),
            inline=False
        )

        embed.add_field(
            name="📌 Case 2: 평소 기능 개발 / 버그 수정할 때 (가장 많이 쓰는 방법)",
            value=(
                f"1️⃣ **개발 요청**: 편하게 채팅하거나 `{p}pair <만들고 싶은 기능>`\n"
                f"   • 예: `{p}pair 장바구니 결제 기능 완성하고 반응형 UI로 다듬어줘`\n"
                "2️⃣ **3중 교차 검증**: Coder 코딩 ➔ QA가 실제 브라우저/Android MCP E2E 테스트 ➔ Auditor 검토 후 완료 보고\n"
                "3️⃣ **피드백 수정**: 마음에 안 드는 부분은 편하게 채팅 (예: *\"버튼 색상을 파란색으로 바꾸고 여백 늘려줘\"*)\n"
                f"4️⃣ **직접 확인**: `{p}sim` (웹 모바일 뷰어) 또는 `{p}apk` (안드로이드 APK 다운로드)"
            ),
            inline=False
        )

        embed.add_field(
            name="📌 Case 3: 빠른 질문 & 코드 안 건드리는 기획/설계 자문",
            value=(
                f"• ⚡ **가벼운 스크립트 / 단일 질의**: `{p}single <요청>`\n"
                f"  *(예: `{p}single 디렉토리 내 중복 이미지 찾는 파이썬 스크립트 짜줘`)*\n"
                f"• 🔍 **코드 무수정 기획/아키텍처 자문**: `{p}read <질문>` 또는 `{p}기획 <질문>`\n"
                f"  *(예: `{p}read Supabase와 Firebase 중 우리 서비스에 어떤 DB가 적합할까?`)*"
            ),
            inline=False
        )

        embed.add_field(
            name="📌 Case 4: 무인 자율 발전 (손 놓고 개발)",
            value=(
                f"1️⃣ **모드 켜기**: `{p}mode 3` 또는 `{p}mode 3+` 입력 (무인 자율 순환 가동)\n"
                "2️⃣ **명령 남겨두기**:\n"
                f"> *\"안드로이드에서 구동 가능한 소닉 게임을 만들어줘.\"*\n"
                "3️⃣ **결과**: Commander(5W1H 적응형 기획) ➔ Coder ➔ QA ➔ Auditor가 연속 자율 순환 개발!\n"
                f"4️⃣ **안전 제어**: `{p}done` (이번 회차 완료 후 안전 종료), `{p}pause` (인터미션 일시정지), `{p}next` (즉시 재개)\n"
                f"5️⃣ **복귀 후 확인**: 디스코드 알림, `{p}git log`, `{p}apk`로 산출물 확인"
            ),
            inline=False
        )

        embed.add_field(
            name="📌 Case 5: 실시간 확인, 빌드 다운로드 & 긴급 제어",
            value=(
                f"• 📱 **실시간 모바일 조작**: `{p}sim` (브라우저에서 폰 화면 직접 터치 & Gemini Vision 100점 감사)\n"
                f"• 🔨 **최신 APK 받기**: `{p}apk` (타임스탬프 APK 빌드 & 구글 드라이브 1클릭 다운로드)\n"
                f"• 🏁 **회차 완료 후 안전 종료**: `{p}done` (Mode 3 진행 중인 마일스톤 완결 후 정상 대기)\n"
                f"• 🛑 **작업 강제 즉시 중단**: `{p}stop` (진행 중인 AI 작업 즉시 강제 멈춤)\n"
                f"• 🔄 **올인원 핫리셋**: `{p}reset` 또는 `{p}리셋` (0초 다운타임 최신 코드 반영 + 세션 컨텍스트 완전 초기화)"
            ),
            inline=False
        )

        embed.set_footer(text="AGY Collaborative System · Category: Master Tips & Autonomous Workflow")
        return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self, current_page: str = "overview"):
        options = [
            discord.SelectOption(
                label="🏠 종합 홈 (Overview & 빠른 시작)",
                value="overview",
                description="4대 AI 두뇌 체계, 핵심 명령어 및 전체 개요",
                emoji="🏠",
                default=(current_page == "overview")
            ),
            discord.SelectOption(
                label="⭐ 핵심 필수 명령어 5선 (Golden 5)",
                value="golden5",
                description="복잡함 끝! 딱 5개만 알면 모든 작업이 가능한 골든 치트시트",
                emoji="⭐",
                default=(current_page in ("golden5", "core"))
            ),
            discord.SelectOption(
                label="🚀 1. 코딩 & 기획 (Dev & Planning)",
                value="dev",
                description="!plan, !setup, !mode, !pair, !single, !read",
                emoji="🚀",
                default=(current_page == "dev")
            ),
            discord.SelectOption(
                label="⚠️ 2. 오토파일럿 (제거됨)",
                value="auto",
                description="기능이 제거되었습니다. !mode 3을 사용하세요.",
                emoji="⚠️",
                default=(current_page in ("auto", "loop"))
            ),
            discord.SelectOption(
                label="🧪 3. QA 허브 & 모바일 스튜디오",
                value="qa",
                description="!qa, !qasetup, !e2e, !sim, !apk, !android_test",
                emoji="🧪",
                default=(current_page == "qa")
            ),
            discord.SelectOption(
                label="👑 4. 관제 사령관 & 클라우드 백업",
                value="commander",
                description="!commander, !export, !drive, !zip, !git",
                emoji="👑",
                default=(current_page == "commander")
            ),
            discord.SelectOption(
                label="⚙️ 5. 시스템 / 대기열 / 안전망",
                value="system",
                description="!status, !model, !quota, !temp, !watchdog, !reset",
                emoji="⚙️",
                default=(current_page in ("system", "admin"))
            ),
            discord.SelectOption(
                label="🎯 6. 실전 꿀팁 & 모범 워크플로우",
                value="tips",
                description="기획➔코딩➔검증➔배포 및 밤샘 무인 공식",
                emoji="🎯",
                default=(current_page == "tips")
            ),
        ]
        super().__init__(placeholder="📑 원하는 안내 카테고리를 선택하세요...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        selected_page = self.values[0]
        view: HelpInteractiveView = self.view
        view.current_page = HelpInteractiveView.normalize_page(selected_page)
        view.message = interaction.message
        view.update_components()
        embed = view.get_current_embed()
        await interaction.response.edit_message(embed=embed, view=view)


class HelpInteractiveView(discord.ui.View):
    """
    High-Performance Interactive View for the AGY Platform.
    Provides fast navigation buttons and dropdown for all 8 command categories.
    """
    @classmethod
    def normalize_page(cls, page_input: Optional[str]) -> str:
        if not page_input or not isinstance(page_input, str):
            return "overview"
        clean = page_input.lower().strip()
        if clean in ("golden5", "golden", "골든", "핵심", "5선", "필수", "치트시트", "cheat", "essential", "core", "daily", "3선"):
            return "golden5"
        elif clean in ("dev", "coding", "code", "개발", "코딩", "기획", "pair", "single", "read", "plan", "setup", "mode", "engine", "ai", "codex", "claude", "openrouter", "opencode", "api", "엔진", "인공지능", "외부", "1"):
            return "dev"
        elif clean in ("auto", "autopilot", "오토", "오토파일럿", "loop", "evolve", "순환", "자율", "루프", "2"):
            return "auto"
        elif clean in ("qa", "qahub", "e2e", "sim", "apk", "colab", "테스트", "검증", "모바일", "3"):
            return "qa"
        elif clean in ("commander", "cmd", "export", "drive", "zip", "사령관", "백업", "4"):
            return "commander"
        elif clean in ("system", "sys", "status", "reset", "temp", "thermal", "quota", "queue", "selfhealing", "admin", "diag", "diagnostics", "시스템", "상태", "리셋", "진단", "관리자", "디버그", "debug", "5"):
            return "system"
        elif clean in ("tips", "tip", "꿀팁", "팁", "가이드", "guide", "사용법", "방법", "howto", "use", "실전", "시간", "time", "round", "라운드", "6"):
            return "tips"
        elif clean in ("overview", "home", "main", "홈", "종합", "개요", "help", "hlep", "도움말", "0"):
            return "overview"
        else:
            return "overview"

    def __init__(self, initial_page: str = "overview", user_id: Optional[int] = None):
        super().__init__(timeout=600)  # Active for 10 minutes
        self.current_page = HelpInteractiveView.normalize_page(initial_page)
        self.user_id = user_id
        self.message: Optional[discord.Message] = None
        self.update_components()

    def get_files(self) -> List[Any]:
        """현재 뷰 발송 시 함께 첨부할 파일 목록 (브랜드 배너)."""
        return get_brand_files()

    def get_current_embed(self) -> discord.Embed:
        page = self.current_page
        if page in ("golden5", "core"):
            return HelpManager.get_golden5_embed()
        elif page == "dev":
            return HelpManager.get_dev_embed()
        elif page in ("auto", "loop"):
            return HelpManager.get_auto_embed()
        elif page == "qa":
            return HelpManager.get_qa_embed()
        elif page == "commander":
            return HelpManager.get_commander_embed()
        elif page in ("system", "admin"):
            return HelpManager.get_system_embed()
        elif page == "tips":
            return HelpManager.get_tips_embed()
        else:
            return HelpManager.get_overview_embed()

    def update_components(self):
        self.clear_items()

        # Row 0: Dropdown Select Menu
        self.add_item(HelpCategorySelect(self.current_page))

        # Row 1: Primary Navigation Buttons (5 items max)
        btn_home = discord.ui.Button(
            label="🏠 홈",
            style=discord.ButtonStyle.primary if self.current_page == "overview" else discord.ButtonStyle.secondary,
            row=1,
            custom_id="help_btn_overview"
        )
        btn_home.callback = self._make_page_callback("overview")
        self.add_item(btn_home)

        btn_golden5 = discord.ui.Button(
            label="⭐ 필수 5선",
            style=discord.ButtonStyle.success if self.current_page in ("golden5", "core") else discord.ButtonStyle.secondary,
            row=1,
            custom_id="help_btn_golden5"
        )
        btn_golden5.callback = self._make_page_callback("golden5")
        self.add_item(btn_golden5)

        btn_dev = discord.ui.Button(
            label="🚀 코딩/기획",
            style=discord.ButtonStyle.primary if self.current_page == "dev" else discord.ButtonStyle.secondary,
            row=1,
            custom_id="help_btn_dev"
        )
        btn_dev.callback = self._make_page_callback("dev")
        self.add_item(btn_dev)

        btn_auto = discord.ui.Button(
            label="⚠️ 오토파일럿",
            style=discord.ButtonStyle.primary if self.current_page in ("auto", "loop") else discord.ButtonStyle.secondary,
            row=1,
            custom_id="help_btn_auto"
        )
        btn_auto.callback = self._make_page_callback("auto")
        self.add_item(btn_auto)

        btn_qa = discord.ui.Button(
            label="🧪 QA/모바일",
            style=discord.ButtonStyle.primary if self.current_page == "qa" else discord.ButtonStyle.secondary,
            row=1,
            custom_id="help_btn_qa"
        )
        btn_qa.callback = self._make_page_callback("qa")
        self.add_item(btn_qa)

        # Row 2: Secondary Navigation & Action Buttons (5 items max)
        btn_cmd = discord.ui.Button(
            label="👑 사령관/백업",
            style=discord.ButtonStyle.primary if self.current_page == "commander" else discord.ButtonStyle.secondary,
            row=2,
            custom_id="help_btn_commander"
        )
        btn_cmd.callback = self._make_page_callback("commander")
        self.add_item(btn_cmd)

        btn_sys = discord.ui.Button(
            label="⚙️ 시스템/안전",
            style=discord.ButtonStyle.primary if self.current_page in ("system", "admin") else discord.ButtonStyle.secondary,
            row=2,
            custom_id="help_btn_system"
        )
        btn_sys.callback = self._make_page_callback("system")
        self.add_item(btn_sys)

        btn_tips = discord.ui.Button(
            label="🎯 실전 꿀팁",
            style=discord.ButtonStyle.primary if self.current_page == "tips" else discord.ButtonStyle.secondary,
            row=2,
            custom_id="help_btn_tips"
        )
        btn_tips.callback = self._make_page_callback("tips")
        self.add_item(btn_tips)

        btn_model = discord.ui.Button(
            label="🎛️ 모델 제어판",
            style=discord.ButtonStyle.success,
            row=2,
            custom_id="help_btn_model"
        )
        async def _open_model_dashboard(interaction: discord.Interaction):
            try:
                if hasattr(interaction, "response") and hasattr(interaction.response, "is_done"):
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True)
                from model_manager import GlobalModelManager, ModelDashboardView
                embed = GlobalModelManager.build_dashboard_embed(selected_role="coder")
                view = ModelDashboardView(selected_role="coder", user_id=interaction.user.id)
                msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                view.message = msg
            except Exception as e:
                try:
                    if hasattr(interaction, "response") and interaction.response.is_done():
                        await interaction.followup.send(f"⚠️ 모델 대시보드 로드 오류: {e}", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"⚠️ 모델 대시보드 로드 오류: {e}", ephemeral=True)
                except Exception:
                    pass
        btn_model.callback = _open_model_dashboard
        self.add_item(btn_model)

        btn_close = discord.ui.Button(
            label="❌ 닫기",
            style=discord.ButtonStyle.danger,
            row=2,
            custom_id="help_btn_close"
        )
        btn_close.callback = self._close_callback
        self.add_item(btn_close)

    def _make_page_callback(self, page_name: str):
        async def page_callback(interaction: discord.Interaction):
            self.current_page = HelpInteractiveView.normalize_page(page_name)
            self.message = interaction.message
            self.update_components()
            embed = self.get_current_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        return page_callback

    async def _close_callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            if interaction.message:
                await interaction.message.delete()
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.edit_message(content="👋 도움말 안내창이 닫혔습니다.", embed=None, view=None)
                else:
                    await interaction.delete_original_response()
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
