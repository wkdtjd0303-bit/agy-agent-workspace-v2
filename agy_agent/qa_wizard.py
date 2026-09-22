import os
import json
import time
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple
import discord

from session_manager import SessionManager
from discord_utils import safe_send_discord_message

logger = logging.getLogger("QAWizard")

# Visual Metadata for QA Focus Profiles
QA_FOCUS_GALLERY: Dict[str, Dict[str, Any]] = {
    "full_comprehensive": {
        "label": "👑 올인원 종합 정밀 검증 (기능 + UI + 성능 + 헌법 전수)",
        "emoji": "👑",
        "description": "전체 페이지 인터랙션, 4대 디바이스 반응형 시각, 콘솔 에러, PRD 기획서 헌법 일치 전수 검사",
        "thumb": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=1000&q=80",
        "color": 0x6366F1,
        "badge": "👑 Full Comprehensive QA"
    },
    "core_flow": {
        "label": "🎯 핵심 비즈니스 로직 & 사용자 시나리오 (CRUD, 폼, 인터랙션)",
        "emoji": "🎯",
        "description": "버튼 클릭, 입력 폼 유효성, 데이터 저장/불러오기, 결제/인증 등 실사용자 핵심 여정 검증",
        "thumb": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=1000&q=80",
        "color": 0x3B82F6,
        "badge": "🎯 Functional & Core Flow"
    },
    "visual_ui": {
        "label": "🎨 UI/UX 디자인 감성 & 4대 디바이스 반응형 레이아웃",
        "emoji": "🎨",
        "description": "모바일(390px)~데스크톱(1440px) 뷰포트, 48dp 터치 타겟, 여백/정렬, 폰트 가독성, 화면 겹침 방어",
        "thumb": "https://images.unsplash.com/photo-1507238691740-187a5b1d37b8?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1522542550221-31fd19575a2d?auto=format&fit=crop&w=1000&q=80",
        "color": 0xEC4899,
        "badge": "🎨 Visual & Responsive UI/UX"
    },
    "prd_constitution": {
        "label": "🏛️ PRD.md 기획 요구사항 & 7대 개발헌법 정합성 (PO 감사)",
        "emoji": "🏛️",
        "description": "PRD.md 명세(REQ-001~) 달성률, 미구현 Mock 색출, PROJECT_CONSTITUTION 7대 Gate 통과 검증",
        "thumb": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1517694712202-14dd9538aa97?auto=format&fit=crop&w=1000&q=80",
        "color": 0xF59E0B,
        "badge": "🏛️ PRD & Constitution PO Audit"
    },
    "perf_stability": {
        "label": "⚡ 성능 & 브라우저 무결성 (콘솔 에러, 로딩 속도, 크래시 방어)",
        "emoji": "⚡",
        "description": "브라우저 콘솔 에러 0건, 네트워크 요청 실패 방어, 메모리 누수 감지, 스피너/스켈레톤 완비",
        "thumb": "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=1000&q=80",
        "color": 0x10B981,
        "badge": "⚡ Performance & Stability"
    },
    "security_edge": {
        "label": "🔒 엣지 케이스 & 비정상 입력 방어 (XSS, 공백, 경계값 무결성)",
        "emoji": "🔒",
        "description": "극단적 긴 문자열 입력, 특수문자/스크립트 주입 방어, 네트워크 단절 시 오프라인 처리, 에러 토스트",
        "thumb": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=1000&q=80",
        "color": 0xEF4444,
        "badge": "🔒 Security & Edge Cases"
    }
}

QA_DEPTH_MAP: Dict[str, Dict[str, str]] = {
    "deep_exploratory": {
        "label": "🧭 심층 탐색 (Deep Exploratory)",
        "desc": "전체 페이지 인터랙션, 멀티 뷰포트 및 엣지 케이스 전수 탐색",
        "badge": "🧭 Deep Exploratory"
    },
    "chaos_stress": {
        "label": "💥 카오스 스트레스 (Chaos Monkey)",
        "desc": "고속 연타, 악의적 입력, 네트워크 지연 및 극한 부하 테스트",
        "badge": "💥 Chaos Monkey"
    },
    "standard": {
        "label": "⚖️ 표준 검증 (Standard Flow)",
        "desc": "핵심 사용자 여정 및 주요 인터랙션 표준 검증",
        "badge": "⚖️ Standard Flow"
    },
    "quick_smoke": {
        "label": "⚡ 신속 스모크 (Smoke Test)",
        "desc": "기본 렌더링 및 런타임 크래시 여부 즉시 확인",
        "badge": "⚡ Quick Smoke"
    }
}

QA_TIMING_MAP: Dict[str, Dict[str, str]] = {
    "manual": {
        "label": "🖐️ 온디맨드 수동 실행 (사용자가 !qa 요청 시에만 실행)",
        "desc": "명시적으로 !qa 명령어를 입력했을 때만 검증 파이프라인 가동"
    },
    "post_task": {
        "label": "🔄 작업 완료 시 자동 검증 (Coder 코딩 완료 직후 자동 트리거)",
        "desc": "개발 에이전트의 턴이 완료되면 자동으로 QA 에이전트가 검증 턴 시작"
    },
    "idle_auto": {
        "label": "⏳ 유휴 시간 자율 감사 (10분 무응답 시 자동 PO/QA 출동)",
        "desc": "사용자가 자리를 비웠을 때 기획 헌법 대조 및 E2E 자율 수행"
    },
    "pre_release": {
        "label": "📦 릴리즈/배포 전 최종 검증 (빌드 산출물 생성 시 Gate 7 검증)",
        "desc": "npm run build 또는 APK 빌드 완료 시 릴리즈 승인 전수 검증"
    }
}

QA_STRICTNESS_MAP: Dict[str, Dict[str, str]] = {
    "strict": {
        "label": "🛡️ Strict 헌법 모드 (Minor 결함도 전수 핫픽스 필수, 무결점 합격)",
        "desc": "사소한 1px 여백, 경미한 문구 결함까지 100% 수정 완료 전까지 반려"
    },
    "balanced": {
        "label": "⚖️ Balanced 표준 실용 모드 (Critical/Major 즉시 차단, Minor 백로그)",
        "desc": "핵심 동작 및 주요 화면 결함은 즉시 차단, 경미한 사항은 백로그 이관 후 합격"
    },
    "fast": {
        "label": "🚀 FastTrack 쾌속 모드 (치명적 크래시 및 핵심 동작 우선 패스)",
        "desc": "앱 크래시와 블로커 결함만 빠르게 점검하여 초고속 개발 순환"
    }
}

QA_MODEL_MAP: Dict[str, Dict[str, str]] = {
    "gpt_oss": {
        "label": "🤖 GPT-OSS 120B (메인) + Qwen 2.5 Coder 32B (백업)",
        "desc": "오픈소스 최고 성능 GPT-OSS 120B를 메인으로 검수하고 소진 시 Qwen 2.5 Coder 32B로 자동 전환"
    },
    "qwen": {
        "label": "🌐 Qwen 2.5 Coder 32B (OpenRouter ZDR 백업/전환)",
        "desc": "알리바바 코딩 특화 모델 (OpenRouter 유료 ZDR 보장, Claude 대비 93% 절감, 깐깐한 문법/엣지케이스 검증)"
    },
    "hybrid": {
        "label": "⚡ 하이브리드 최적화 (일반: Gemini Flash / 오토: Gemini 3.1 Pro)",
        "desc": "평소 대화는 무제한 Gemini Flash, 유휴 오토파일럿/정밀 검수 시 Gemini 3.1 Pro 집중 투입"
    },
    "gemini_pro": {
        "label": "🧠 Gemini 3.1 Pro High 고정 (수석 PO 기획 정합성 & 심층 감사)",
        "desc": "모든 QA 검수를 Gemini 3.1 Pro로 진행하여 최고 수준의 기획 정합성 보장"
    },
    "gemini_flash": {
        "label": "⚡ Gemini 3.7 Flash High 고정 (초고속 코드 리뷰 & 쿼터 절약)",
        "desc": "모든 QA 검수를 Gemini 3.7 Flash로 진행하여 빠른 피드백과 쿼터 극대화"
    },
    "claude_sonnet": {
        "label": "🧠 Gemini 3.1 Pro High 고정 (수석 PO 기획 정합성 & 심층 감사)",
        "desc": "모든 QA 검수를 Gemini 3.1 Pro로 진행하여 최고 수준의 기획 정합성 보장"
    }
}


class QAWizardState:
    def __init__(
        self,
        channel_id: int,
        user_id: int,
        workspace: str,
        initial_target: str = ""
    ):
        self.channel_id = channel_id
        self.user_id = user_id
        self.workspace = workspace
        self.focus: str = "full_comprehensive"
        self.depth: str = "deep_exploratory"
        self.timing: str = "idle_auto"
        self.strictness: str = "balanced"
        self.model_pref: str = "hybrid"
        self.target_url_or_apk: str = initial_target.strip()
        self.custom_notes: str = ""
        self.last_saved_at: Optional[str] = None

        # Load existing config from workspace if available
        self.load_from_workspace()

    def load_from_workspace(self):
        """Loads saved QA configuration from workspace qa_config.json if present."""
        if not self.workspace or not os.path.isdir(self.workspace):
            return

        for fname in ["qa_config.json", os.path.join("reports", "qa_config.json")]:
            p = os.path.join(self.workspace, fname)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.focus = data.get("focus", self.focus)
                    self.depth = data.get("depth", self.depth)
                    self.timing = data.get("timing", self.timing)
                    self.strictness = data.get("strictness", self.strictness)
                    self.model_pref = data.get("model_pref", self.model_pref)
                    if not self.target_url_or_apk and data.get("target_url_or_apk"):
                        self.target_url_or_apk = data.get("target_url_or_apk")
                    self.custom_notes = data.get("custom_notes", self.custom_notes)
                    self.last_saved_at = data.get("updated_at")
                    logger.info(f"Loaded QA config from {p}")
                    break
                except Exception as e:
                    logger.debug(f"Could not load {p}: {e}")

    def save_to_workspace(self) -> str:
        """Saves QA configuration to workspace reports/qa_config.json and qa_config.json."""
        if not self.workspace or not os.path.isdir(self.workspace):
            return ""

        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.last_saved_at = now_str
        payload = {
            "channel_id": self.channel_id,
            "focus": self.focus,
            "depth": self.depth,
            "timing": self.timing,
            "strictness": self.strictness,
            "model_pref": self.model_pref,
            "target_url_or_apk": self.target_url_or_apk,
            "custom_notes": self.custom_notes,
            "updated_at": now_str
        }

        os.makedirs(os.path.join(self.workspace, "reports"), exist_ok=True)
        paths = [
            os.path.join(self.workspace, "qa_config.json"),
            os.path.join(self.workspace, "reports", "qa_config.json")
        ]

        saved_path = paths[0]
        for p in paths:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Failed to write QA config to {p}: {e}")

        return saved_path

    def get_focus_meta(self) -> Dict[str, Any]:
        return QA_FOCUS_GALLERY.get(self.focus, QA_FOCUS_GALLERY["full_comprehensive"])

    def to_prompt_instructions(self) -> str:
        """Generates detailed prompt instructions based on current QA setup."""
        focus_meta = self.get_focus_meta()
        depth_info = QA_DEPTH_MAP.get(self.depth, QA_DEPTH_MAP["deep_exploratory"])
        timing_info = QA_TIMING_MAP.get(self.timing, QA_TIMING_MAP["idle_auto"])
        strict_info = QA_STRICTNESS_MAP.get(self.strictness, QA_STRICTNESS_MAP["balanced"])

        lines = [
            f"🎯 **[워크스페이스 확정 QA 검증 전략 & 초점 프로필]**",
            f"• 🔍 **최우선 검증 초점**: `{focus_meta['label']}`",
            f"  - 상세 검증 범위: {focus_meta['description']}",
            f"• 🧭 **검증 심도 & 지속 시간**: `{depth_info['label']}` ({depth_info['badge']})",
            f"• ⏰ **실행 타이밍 정책**: `{timing_info['label']}`",
            f"• 🛡️ **품질 게이트 엄격도**: `{strict_info['label']}`",
        ]

        if self.focus == "core_flow":
            lines.extend([
                "• 📋 **집중 체크포인트 (기능 Core)**:",
                "  1. 실사용자 주요 시나리오(회원가입, 데이터 등록/수정/삭제, 핵심 인터랙션)가 100% 정상 작동하는가?",
                "  2. 버튼 클릭 시 피드백(로딩, 상태 변경, 토스트 알림)이 명확히 제공되는가?",
                "  3. 입력 폼의 유효성 검사 및 필수값 누락 처리가 올바르게 방어되는가?"
            ])
        elif self.focus == "visual_ui":
            lines.extend([
                "• 📋 **집중 체크포인트 (시각 UI/UX & 런타임 인터랙션)**:",
                "  1. [UI 프로젝트] 모바일 390px ~ 데스크톱 1440px 뷰포트에서 가로 스크롤이나 컴포넌트 겹침이 없는가?",
                "  2. [UI 프로젝트] 모바일 터치 타겟(48dp/44px) 및 Safe Area 인셋이 준수되었는가?",
                "  3. [CLI/백엔드] 터미널 ANSI 컬러/테이블 서식 또는 API 응답 JSON 구조가 명확하고 일관적인가?"
            ])
        elif self.focus == "prd_constitution":
            lines.extend([
                "• 📋 **집중 체크포인트 (PRD & 헌법 감사)**:",
                "  1. `PRD.md`에 명시된 핵심 요구사항(REQ-001~)이 누락이나 Mock 없이 실구현되었는가?",
                "  2. `PROJECT_CONSTITUTION.md` 및 `profiles/active_profile.md` 7대 Gate 물리적 Evidence 파일이 존재하는가?",
                "  3. 플랫폼 기술 스택 락 규격(CLI 프로젝트 내 프론트엔드 목업 생성 금지 등)을 100% 준수하고 있는가?"
            ])
        elif self.focus == "perf_stability":
            lines.extend([
                "• 📋 **집중 체크포인트 (성능 & 안정성)**:",
                "  1. 브라우저 콘솔 에러(Uncaught Error, Warning)가 0건인가?",
                "  2. 네트워크 요청 실패나 지연 시 스피너/에러 바운더리가 안전하게 동작하는가?",
                "  3. 메모리 누수나 불필요한 무한 렌더링 루프가 없는가?"
            ])
        elif self.focus == "security_edge":
            lines.extend([
                "• 📋 **집중 체크포인트 (보안 & 엣지케이스)**:",
                "  1. 극단적 길이의 텍스트, 특수문자, HTML 태그 입력 시 화면이 깨지지 않고 방어되는가?",
                "  2. 네트워크 오프라인 또는 서버 응답 에러 시 적절한 사용자 가이드가 출력되는가?",
                "  3. 비정상적 조작이나 연속 클릭(더블클릭 등) 시 데이터 중복 전송이 방어되는가?"
            ])
        else:
            lines.extend([
                "• 📋 **집중 체크포인트 (올인원 종합)**:",
                "  - 기능 Core 로직 ➔ 시각 4대 반응형 UI ➔ 콘솔 무에러 ➔ PRD 요구사항 달성률을 전수 점검하십시오."
            ])

        if self.custom_notes:
            lines.append(f"• 📝 **사용자 특별 지시사항**: {self.custom_notes}")

        return "\n".join(lines)


class QAWizardInteractiveView(discord.ui.View):
    def __init__(
        self,
        state: QAWizardState,
        session_manager: SessionManager,
        bot_client: discord.Client,
        qa_bot_client: Optional[discord.Client] = None,
        auditor_bot_client: Optional[discord.Client] = None,
        qa_hub_manager: Any = None
    ):
        super().__init__(timeout=600)
        self.state = state
        self.session_manager = session_manager
        self.bot_client = bot_client
        self.qa_bot_client = qa_bot_client
        self.auditor_bot_client = auditor_bot_client
        self.qa_hub_manager = qa_hub_manager
        self.message: Optional[discord.Message] = None

        self._sync_select_defaults()

    def _sync_select_defaults(self):
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                if child.custom_id == "select_qa_focus":
                    for opt in child.options:
                        opt.default = (opt.value == self.state.focus)
                elif child.custom_id == "select_qa_timing":
                    for opt in child.options:
                        opt.default = (opt.value == self.state.timing)
                elif child.custom_id == "select_qa_strictness":
                    for opt in child.options:
                        opt.default = (opt.value == self.state.strictness)
                elif child.custom_id == "select_qa_model":
                    for opt in child.options:
                        opt.default = (opt.value == self.state.model_pref)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state.user_id:
            await interaction.response.send_message("⚠️ 이 QA 전략 셋업을 시작한 사용자만 조작할 수 있습니다.", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        meta = self.state.get_focus_meta()
        depth_info = QA_DEPTH_MAP.get(self.state.depth, QA_DEPTH_MAP["deep_exploratory"])
        timing_info = QA_TIMING_MAP.get(self.state.timing, QA_TIMING_MAP["idle_auto"])
        strict_info = QA_STRICTNESS_MAP.get(self.state.strictness, QA_STRICTNESS_MAP["balanced"])
        model_info = QA_MODEL_MAP.get(self.state.model_pref, QA_MODEL_MAP["hybrid"])

        target_display = self.state.target_url_or_apk or "🔍 자동 감지 (최신 웹 배포 URL / APK)"
        saved_status = f"💾 최근 저장: `{self.state.last_saved_at}`" if self.state.last_saved_at else "⏳ 변경 사항 미저장"

        embed = discord.Embed(
            title="🧪 [AGY 3.0 지능형 QA 전략 & 검증 초점 셋업 스튜디오]",
            description=(
                f"**{meta.get('badge', '🧪 QA Focus Studio')}**\n"
                f"드롭다운을 선택하여 **무엇을 집중 검증할지(Focus)**, **얼마나 깊이 검증할지(Depth)**, **언제 실행할지(Timing)**를 설정하세요.\n"
                f"{saved_status}"
            ),
            color=meta.get("color", 0x6366F1)
        )

        thumb_url = meta.get("thumb")
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)

        embed.add_field(
            name="1️⃣ 🎯 최우선 검증 초점 (QA Focus)",
            value=f"**{meta['label']}**\n└ *{meta['description']}*",
            inline=False
        )

        embed.add_field(
            name="2️⃣ 🧭 검증 심도 & 지속 시간 (Depth & Duration)",
            value=f"**{depth_info['label']}**\n└ *{depth_info['desc']}*",
            inline=False
        )

        embed.add_field(
            name="3️⃣ ⏰ 검증 실행 타이밍 (Timing & Trigger)",
            value=f"**{timing_info['label']}**\n└ *{timing_info['desc']}*",
            inline=False
        )

        embed.add_field(
            name="4️⃣ 🛡️ 품질 게이트 엄격도 (Strictness & Pass Criteria)",
            value=f"**{strict_info['label']}**\n└ *{strict_info['desc']}*",
            inline=False
        )

        embed.add_field(
            name="5️⃣ 🤖 검수 담당 모델 정책 (Assigned Engine)",
            value=f"**{model_info['label']}**\n└ *{model_info['desc']}*",
            inline=False
        )

        embed.add_field(
            name="6️⃣ 📦 대상 타겟 (URL / Binary)",
            value=f"`{target_display}`",
            inline=False
        )

        embed.set_footer(text="설정 저장 후 [🚀 즉시 QA 실행]을 누르거나 !qa 명령 시 위 전략이 100% 자동 주입됩니다.")
        return embed

    # --- Row 0: Select QA Focus Profile ---
    @discord.ui.select(
        placeholder="1️⃣ 🎯 무엇을 집중 검증할지 초점(Focus)을 선택하세요",
        options=[
            discord.SelectOption(
                label="올인원 종합 정밀 검증",
                value="full_comprehensive",
                description="기능 + UI/UX + 콘솔에러 + PRD 헌법 일치 전수 검사",
                emoji="👑"
            ),
            discord.SelectOption(
                label="핵심 비즈니스 로직 & 시나리오",
                value="core_flow",
                description="CRUD, 입력 폼, 버튼 인터랙션, 실사용자 핵심 여정",
                emoji="🎯"
            ),
            discord.SelectOption(
                label="UI/UX 디자인 & 4대 디바이스 반응형",
                value="visual_ui",
                description="모바일 390px~1440px, 48dp 터치, 여백/정렬, 폰트 가독성",
                emoji="🎨"
            ),
            discord.SelectOption(
                label="PRD 기획서 요구사항 & 헌법 일치",
                value="prd_constitution",
                description="REQ-001~ 달성률, 미구현 Mock 색출, 7대 Gate 통과",
                emoji="🏛️"
            ),
            discord.SelectOption(
                label="성능 & 브라우저 무결성",
                value="perf_stability",
                description="콘솔 에러 0건, 로딩 속도, 메모리 누수 방어",
                emoji="⚡"
            ),
            discord.SelectOption(
                label="엣지 케이스 & 비정상 입력 방어",
                value="security_edge",
                description="극단적 긴 문자열, 특수문자, 오프라인 에러 토스트",
                emoji="🔒"
            ),
        ],
        custom_id="select_qa_focus",
        row=0
    )
    async def select_qa_focus(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.focus = select.values[0]
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    # --- Row 1: Select QA Execution Timing ---
    @discord.ui.select(
        placeholder="2️⃣ ⏰ 언제 QA 검증을 실행할지 타이밍(Timing)을 선택하세요",
        options=[
            discord.SelectOption(
                label="유휴 시간 자율 감사 (10분 무응답 시 자동)",
                value="idle_auto",
                description="자리를 비웠을 때 기획/E2E 자율 수행",
                emoji="⏳"
            ),
            discord.SelectOption(
                label="온디맨드 수동 실행 (!qa 명령 시에만)",
                value="manual",
                description="사용자가 직접 !qa 명령을 내렸을 때만 검증 파이프라인 가동",
                emoji="🖐️"
            ),
            discord.SelectOption(
                label="작업 완료 시 자동 검증 (코딩 직후 자동)",
                value="post_task",
                description="Coder 개발 완료 직후 자동으로 시니어 QA 검증 턴 가동",
                emoji="🔄"
            ),
            discord.SelectOption(
                label="릴리즈/배포 전 최종 검증 (빌드 생성 시)",
                value="pre_release",
                description="npm run build or APK 빌드 시 Gate 7 릴리즈 승인 전수 검증",
                emoji="📦"
            ),
        ],
        custom_id="select_qa_timing",
        row=1
    )
    async def select_qa_timing(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.timing = select.values[0]
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    # --- Row 2: Select QA Strictness Level ---
    @discord.ui.select(
        placeholder="3️⃣ 🛡️ 검증 엄격도 및 통과 기준을 선택하세요",
        options=[
            discord.SelectOption(
                label="Balanced 표준 실용 모드 (권장)",
                value="balanced",
                description="Critical/Major 즉시 차단, Minor는 백로그 이관 후 통과",
                emoji="⚖️"
            ),
            discord.SelectOption(
                label="Strict 헌법 모드 (무결점 전수 핫픽스)",
                value="strict",
                description="Minor 결함도 100% 수정 전까지 불합격 (완전 무결점)",
                emoji="🛡️"
            ),
            discord.SelectOption(
                label="FastTrack 쾌속 모드 (치명 결함 위주)",
                value="fast",
                description="앱 크래시 및 주요 블로커 위주로 초고속 패스",
                emoji="🚀"
            ),
        ],
        custom_id="select_qa_strictness",
        row=2
    )
    async def select_qa_strictness(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.strictness = select.values[0]
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    # --- Row 3: Select Assigned Model ---
    @discord.ui.select(
        placeholder="4️⃣ 🤖 검수 담당 모델 및 엔진을 선택하세요",
        options=[
            discord.SelectOption(
                label="GPT-OSS 120B (메인) + Qwen 2.5 Coder (백업)",
                value="gpt_oss",
                description="GPT-OSS 120B를 메인으로 검수하고 소진 시 Qwen으로 자동 전환",
                emoji="🤖"
            ),
            discord.SelectOption(
                label="Qwen 2.5 Coder 32B (OpenRouter ZDR 백업/전환)",
                value="qwen",
                description="알리바바 코딩 특화 (유료 ZDR 보안, Claude 대비 93% 절감)",
                emoji="🌐"
            ),
            discord.SelectOption(
                label="하이브리드 최적화 (일반: Flash / 오토: Pro)",
                value="hybrid",
                description="일반 대화는 초고속 Gemini Flash, 유휴 오토파일럿은 Gemini 3.1 Pro",
                emoji="⚡"
            ),
            discord.SelectOption(
                label="Gemini 3.1 Pro High 고정 (수석 PO 정밀 감사)",
                value="gemini_pro",
                description="모든 QA 검수를 Gemini 3.1 Pro High로 진행",
                emoji="🧠"
            ),
            discord.SelectOption(
                label="Gemini 3.7 Flash High 고정 (초고속 & 쿼터 절약)",
                value="gemini_flash",
                description="모든 QA 검수를 Gemini 3.7 Flash High로 진행",
                emoji="⚡"
            ),
        ],
        custom_id="select_qa_model",
        row=3
    )
    async def select_qa_model(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.model_pref = select.values[0]
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    # --- Row 4: Action Buttons ---
    @discord.ui.button(label="💾 설정 저장 & 영구 적용", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def btn_save(self, interaction: discord.Interaction, button: discord.ui.Button):
        saved_file = self.state.save_to_workspace()
        session = self.session_manager.get_session(self.state.channel_id)

        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        await interaction.followup.send(
            f"✅ **QA 전략 설정이 성공적으로 저장되었습니다!**\n"
            f"📁 저장 위치: `{saved_file}`\n"
            f"🎯 이제 `!qa` 실행 시 **[{self.state.get_focus_meta()['label']}]** 초점이 자동으로 최우선 적용됩니다! 🚀",
            ephemeral=True
        )

    @discord.ui.button(label="🚀 이 설정으로 즉시 QA 실행", style=discord.ButtonStyle.primary, emoji="🚀", row=4)
    async def btn_run_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.save_to_workspace()
        await interaction.response.send_message("🧪 🚀 **선택하신 QA 전략으로 즉시 정밀 검증을 시작합니다...**", ephemeral=False)

        session = self.session_manager.get_session(self.state.channel_id)
        target = self.state.target_url_or_apk or session.last_url

        if not target and session.workspace and os.path.isdir(session.workspace):
            for candidate in ["app-release.apk", "ParkMemo.apk"]:
                p = os.path.join(session.workspace, candidate)
                if os.path.isfile(p):
                    target = p
                    break

        mission = f"[{self.state.get_focus_meta()['badge']}] {self.state.get_focus_meta()['description']}"

        if self.qa_hub_manager:
            asyncio.create_task(
                self.qa_hub_manager.dispatch_mission(
                    source_channel=interaction.channel,
                    target_url_or_apk=target or "현재 배포 서비스 및 소스코드",
                    mission=mission,
                    bot_client=self.bot_client,
                    mode="patch"
                )
            )

    @discord.ui.button(label="🔄 기본값 리셋", style=discord.ButtonStyle.secondary, emoji="🔄", row=4)
    async def btn_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.focus = "full_comprehensive"
        self.state.timing = "idle_auto"
        self.state.strictness = "balanced"
        self.state.model_pref = "hybrid"
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


def get_workspace_qa_config(workspace_path: str) -> Optional[Dict[str, Any]]:
    """Helper to read qa_config.json from workspace if it exists."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return None

    for fname in ["qa_config.json", os.path.join("reports", "qa_config.json")]:
        p = os.path.join(workspace_path, fname)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return None


async def handle_qa_setup_command(
    ctx: Any,
    session_manager: SessionManager,
    bot_client: discord.Client,
    qa_bot_client: Optional[discord.Client] = None,
    auditor_bot_client: Optional[discord.Client] = None,
    qa_hub_manager: Any = None,
    target_arg: str = ""
):
    """Entrypoint to launch the interactive QA Strategy Setup Studio."""
    channel_id = ctx.channel.id
    user_id = ctx.author.id if hasattr(ctx, "author") else ctx.user.id
    session = session_manager.get_session(channel_id)
    ws = session.workspace or os.path.expanduser("~/Desktop/autocoding")

    state = QAWizardState(
        channel_id=channel_id,
        user_id=user_id,
        workspace=ws,
        initial_target=target_arg
    )

    view = QAWizardInteractiveView(
        state=state,
        session_manager=session_manager,
        bot_client=bot_client,
        qa_bot_client=qa_bot_client,
        auditor_bot_client=auditor_bot_client,
        qa_hub_manager=qa_hub_manager
    )

    embed = view.build_embed()
    msg = await ctx.channel.send(embed=embed, view=view)
    view.message = msg
