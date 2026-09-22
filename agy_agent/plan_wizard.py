import os
import re
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple
import discord

from session_manager import SessionManager
from trio_agent_runner import TrioAgentRunner
from discord_utils import safe_send_discord_message, ThrottledStatusUpdater

logger = logging.getLogger("PlanWizard")

# High-resolution visual presets & thematic gallery for rich Discord UI embeds
PLATFORM_GALLERY: Dict[str, Dict[str, Any]] = {
    "web": {
        "label": "🌐 웹 & SaaS (React + TypeScript + Vite + TailwindCSS)",
        "emoji": "🌐",
        "preset_key": "web",
        "thumb": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=1000&q=80",
        "color": 0x3B82F6,
        "badge": "🌐 Fullstack Web / SaaS"
    },
    "game": {
        "label": "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)",
        "emoji": "🎮",
        "preset_key": "game",
        "thumb": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?auto=format&fit=crop&w=1000&q=80",
        "color": 0xF43F5E,
        "badge": "🎮 60fps WebGL / Canvas Game"
    },
    "retrogame": {
        "label": "🕹️ 레트로 8-bit / 16-bit 픽셀 게임 (도트 아케이드 & 칩튠 SFX)",
        "emoji": "🕹️",
        "preset_key": "game",
        "thumb": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1579373903781-fd5c0c30c4cd?auto=format&fit=crop&w=1000&q=80",
        "color": 0x8B5CF6,
        "badge": "🕹️ 8-Bit / 16-Bit Chiptune Game"
    },
    "win": {
        "label": "🪟 윈도우 네이티브 데스크톱 앱 (C# WPF / WinUI 3 or PyQt6)",
        "emoji": "🪟",
        "preset_key": "win",
        "thumb": "https://images.unsplash.com/photo-1587620962725-abab7fe55159?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=1000&q=80",
        "color": 0x0078D4,
        "badge": "🪟 Windows Native (WinUI 3/PyQt6)"
    },
    "app": {
        "label": "📱 안드로이드 네이티브 앱 (Kotlin + Jetpack Compose)",
        "emoji": "📱",
        "preset_key": "app",
        "thumb": "https://images.unsplash.com/photo-1607604276583-eef5d076aa5f?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1526406915894-7bcd65f60845?auto=format&fit=crop&w=1000&q=80",
        "color": 0x3DDC84,
        "badge": "📱 Android Native (Compose)"
    },
    "mobilegame": {
        "label": "📲 모바일 터치 캐주얼 게임 (Android / 모바일 가상 조이스틱)",
        "emoji": "📲",
        "preset_key": "app",
        "thumb": "https://images.unsplash.com/photo-1511512578047-dfb367046420?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=1000&q=80",
        "color": 0x10B981,
        "badge": "📲 Touch Action Mobile Game"
    },
    "apple": {
        "label": "🍎 애플 iOS / macOS 네이티브 앱 (Swift + SwiftUI)",
        "emoji": "🍎",
        "preset_key": "apple",
        "thumb": "https://images.unsplash.com/photo-1512941937669-90a1b58e7e9c?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=1000&q=80",
        "color": 0x0071E3,
        "badge": "🍎 Apple Native (SwiftUI)"
    },
    "desktop": {
        "label": "💻 크로스플랫폼 데스크톱 앱 (Electron / Tauri / Rust)",
        "emoji": "💻",
        "preset_key": "desktop",
        "thumb": "https://images.unsplash.com/photo-1517694712202-14dd9538aa97?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1542751371-adc38448a05e?auto=format&fit=crop&w=1000&q=80",
        "color": 0x6366F1,
        "badge": "💻 Desktop (Tauri / Electron)"
    },
    "bot": {
        "label": "🤖 백그라운드 자동화 / 크롤러 & 봇 (Python / Node.js)",
        "emoji": "🤖",
        "preset_key": "bot",
        "thumb": "https://images.unsplash.com/photo-1485827404703-89b55fcc595e?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1000&q=80",
        "color": 0xF59E0B,
        "badge": "🤖 Automation Daemon / Bot"
    },
    "extension": {
        "label": "🧩 크롬 브라우저 확장 프로그램 (Manifest V3 + Vite)",
        "emoji": "🧩",
        "preset_key": "extension",
        "thumb": "https://images.unsplash.com/photo-1522542550221-31fd19575a2d?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1507238691740-187a5b1d37b8?auto=format&fit=crop&w=1000&q=80",
        "color": 0xEC4899,
        "badge": "🧩 Chrome Extension V3"
    },
    "cli": {
        "label": "⚡ CLI / 고성능 터미널 유틸리티 (Python / Rich)",
        "emoji": "⚡",
        "preset_key": "cli",
        "thumb": "https://images.unsplash.com/photo-1629654297299-c8506221ca97?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=1000&q=80",
        "color": 0x10B981,
        "badge": "⚡ CLI / Terminal Utility"
    },
    "api": {
        "label": "☁️ 고성능 백엔드 API & MSA (FastAPI / NestJS + DB)",
        "emoji": "☁️",
        "preset_key": "api",
        "thumb": "https://images.unsplash.com/photo-1558494949-ef010cbdcc31?auto=format&fit=crop&w=256&q=80",
        "banner": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=1000&q=80",
        "color": 0x8B5CF6,
        "badge": "☁️ High-Perf Backend & MSA"
    }
}

THEME_COLOR_MAP: Dict[str, int] = {
    "파스텔": 0xFF70A6,
    "다크": 0x1E1E2E,
    "윈도우": 0x0078D4,
    "애플": 0x0071E3,
    "노션": 0x2F3437,
    "브루탈": 0xFFDE59,
    "픽셀": 0x8A2BE2,
    "터미널": 0x00FF66,
    "오로라": 0x7928CA,
    "네온": 0x00F0FF,
    "카툰": 0xFF6B6B
}


class PlanWizardState:
    def __init__(self, channel_id: int, user_id: int, initial_idea: str = ""):
        self.channel_id = channel_id
        self.user_id = user_id
        self.initial_idea = initial_idea.strip() or "반응형 풀스택 프로덕션 서비스"
        
        # Default baseline
        self.platform = "🌐 웹 & SaaS (React + TypeScript + Vite + TailwindCSS)"
        self.target_persona = "📋 생산성 / 할일 & 스마트 메모 (투두리스트, 캘린더, 칸반 보드, 노트)"
        self.killer_feature = "📝 직관적인 일일 기록 & 통계 대시보드 (캘린더, 그래프, 월간 리포트)"
        self.design_mood = "🍬 파스텔 멀티 믹스 (핀터레스트 벤토 그리드 & 밀키 글래스)"
        self.auth_policy = "0초 무로그인 (가입 없이 즉시 사용, LocalStorage/IndexedDB 영구저장)"
        self.monetization = "기본 2회 무료 체험 ➔ 프리미엄 기능 잠금 결제모달 (토스/카카오페이)"
        self.viral_loop = "카카오톡/인스타그램 공유 시 무료 1회 추가 리워드 바이럴"
        self.custom_notes = ""

        # 🎮 Deep Game & Specification fields (Addresses user feedback on references & quality)
        self.benchmark_reference = ""
        self.quality_tier = ""
        self.control_scheme = ""
        self.game_loop = ""

        # Smart automatic inference based on initial_idea
        self.apply_smart_preset(self.initial_idea)

    def is_game_domain(self) -> bool:
        """Determines whether current project specification corresponds to a game."""
        combined = f"{self.platform} {self.target_persona} {self.killer_feature} {self.initial_idea} {self.benchmark_reference}".lower()
        return any(k in combined for k in [
            "게임", "game", "아케이드", "arcade", "canvas", "3d", "rpg", "퍼즐", "puzzle",
            "슈팅", "플랫포머", "로그라이크", "roguelike", "서바이벌", "수박", "테트리스", "쿠키런", "마리오"
        ])

    def apply_smart_preset(self, text: str):
        """Intelligently detects domain keywords to automatically configure ideal category & feature settings."""
        t = text.lower()

        # -------------------------------------------------------------
        # 🎮 1. Game Domains (High-Fidelity Presets & Benchmark Mapping)
        # -------------------------------------------------------------
        # 1-1. ⚔️ 탑다운 액션 & 서바이벌 로그라이크 (뱀파이어 서바이벌 계열)
        if any(k in t for k in ["뱀서", "뱀파이어", "서바이벌", "생존", "로그라이크", "roguelike", "브롤"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "⚔️ 탑다운 액션 & 서바이벌 로그라이크 (뱀파이어 서바이벌, 브롤스타즈)"
            self.killer_feature = "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작"
            self.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
            self.benchmark_reference = "뱀파이어 서바이벌 (Vampire Survivors) - 360도 자유 이동, 자동 발사, 경험치 젬 파밍 & 레벨업 3지선다 무기/스킬 강화"
            self.quality_tier = "Tier 2: 60fps 고반응성 Canvas 렌더링, 적 웨이브 스폰, 피격 파티클 폭발, 화면 흔들림(Screen Shake) 타격감"
            self.control_scheme = "모바일 터치 가상 조이스틱 + 데스크톱 WASD/방향키 듀얼 완비"
            self.game_loop = "생존 시작 ➔ 적 처치 & 젬 수집 ➔ 레벨업 3지선다 스킬 강화 ➔ 보스전 ➔ 사망 시 점수 정산 및 1초 즉시 재도전"
            self.auth_policy = "게스트 닉네임 즉시 플레이 (LocalStorage 최고기록 및 골드 영구저장)"
            self.monetization = "인게임 스킨/캐릭터 해금 & 명예의 전당 랭킹 등록"
            self.viral_loop = "생존 시간 및 처치 수 하이라이트 스크린샷 1클릭 SNS 공유"

        # 1-2. 👾 2D 아케이드 & 하이퍼캐주얼 (수박게임, 플래피버드, 2048)
        elif any(k in t for k in ["수박", "수박게임", "suika", "2048", "플래피", "벽돌", "아케이드", "하이퍼캐주얼"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "👾 2D 아케이드 & 하이퍼캐주얼 (수박게임, 2048, 플래피 버드, 벽돌깨기)"
            self.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.design_mood = "🍬 큐트 파스텔 카툰 (동물의 숲 & 캐주얼 팝 아트)"
            self.benchmark_reference = "수박게임 (Suika Game) - 상단 과일 투하, 2D 탄성 물리 충돌, 동일 크기 접촉 시 상위 단계 합성 및 팝 이펙트"
            self.quality_tier = "부드러운 60fps 2D 원형 물리 충돌 합성, 합성 시 과즙 팝 파티클 및 바운스 탄성 애니메이션"
            self.control_scheme = "모바일 터치 앤 드래그 투하 + 데스크톱 마우스/화살표 좌우 조준"
            self.game_loop = "오브젝트 투하 ➔ 물리 충돌 합성 ➔ 콤보 점수 획득 ➔ 데드라인 도달 시 게임오버 ➔ 1초 퀵 재도전"
            self.auth_policy = "0초 무로그인 (브라우저 LocalStorage 최고 점수 자동 보존)"
            self.monetization = "100% 무료 플레이 (소셜 스코어 카드 공유 바이럴)"
            self.viral_loop = "달성 스코어 및 최종 합성 과일 1클릭 인스타그램/카카오톡 공유"

        # 1-3. 🏃 횡스크롤 런 & 플랫포머 (슈퍼마리오, 쿠키런, 지오메트리 대시)
        elif any(k in t for k in ["마리오", "쿠키런", "점프", "플랫포머", "달리기", "러너", "런"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "🏃 횡스크롤 런 & 플랫포머 (슈퍼마리오, 쿠키런, 지오메트리 대시)"
            self.killer_feature = "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작"
            self.design_mood = "👾 16비트 슈퍼패미컴 클래식 (도트 스프라이트 & 따뜻한 레트로)"
            self.benchmark_reference = "쿠키런 / 슈퍼마리오 - 횡스크롤 전진, 점프/슬라이드 2버튼 조작, 젤리/코인 파밍, 장애물 피격 피드백"
            self.quality_tier = "60fps 중력 가속도 점프 곡선, 달리기 먼지 파티클, 피격 시 반짝임 무적 판정"
            self.control_scheme = "모바일 2버튼 (점프/슬라이드) + PC 스페이스/아래화살표"
            self.game_loop = "스테이지 스타트 ➔ 달리기 & 코인 수집 ➔ 장애물 점프 회피 ➔ 골인 지점 도달 ➔ 스코어 정산"
            self.auth_policy = "게스트 즉시 플레이 (누적 코인 및 클리어 스테이지 로컬 저장)"
            self.monetization = "스테이지 부활 하트 충전 & 특별 캐릭터 스킨 해금"
            self.viral_loop = "달린 거리 & 코인 획득량 1클릭 자랑하기 링크 전송"

        # 1-4. 🧩 두뇌 퍼즐 & 블록 매칭 (테트리스, 매치3, 스도쿠)
        elif any(k in t for k in ["테트리스", "tetris", "퍼즐", "블록", "스도쿠", "오목", "체스", "매치3"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "🧩 두뇌 퍼즐 & 블록 매칭 (테트리스, 매치3, 스도쿠, 오목)"
            self.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
            self.benchmark_reference = "테트리스 (Tetris) - 7종 블록 낙하, 회전 및 하드드롭, 줄 완성 시 라인 클리어 플래시 및 속도 가속"
            self.quality_tier = "그리드 기반 완벽한 충돌 감지, 블록 회전 킥(Wall Kick), 라인 삭제 시 반짝임 이펙트"
            self.control_scheme = "모바일 화면 가상 방향키/회전 버튼 + PC 화살표키 & 스페이스바 하드드롭"
            self.game_loop = "블록 낙하 ➔ 위치 조작 및 줄 맞추기 ➔ 라인 제거 및 콤보 ➔ 탑 도달 시 게임오버"
            self.auth_policy = "0초 무로그인 (최고 기록 및 라인 수 로컬 영구 저장)"
            self.monetization = "100% 무료 개방 (명예의 전당 랭킹 리더보드)"
            self.viral_loop = "내 클리어 라인 수 & 퍼즐 점수 카드 1클릭 공유"

        # 1-5. 🚀 슈팅 & 디펜스 (1942, 갤러그, 타워 디펜스)
        elif any(k in t for k in ["슈팅", "비행기", "갤러그", "1942", "탄막", "디펜스", "타워"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "🚀 탄막 슈팅 & 디펜스 (1942, 갤러그, 뱀파이어 닷지, 타워 디펜스)"
            self.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
            self.benchmark_reference = "1942 / 갤러그 - 종스크롤 탄막 슈팅, 파워업 캡슐 아이템, 보스 패턴 회피"
            self.quality_tier = "60fps 다중 탄막 렌더링, 폭발 파티클 및 폭탄 투하 화면 플래시, 칩튠 사운드"
            self.control_scheme = "모바일 터치 드래그 이동 + PC 마우스/화살표 & 스페이스바 연사"
            self.game_loop = "출격 ➔ 적 편대 격추 ➔ 파워업 아이템 습득 ➔ 스테이지 보스전 ➔ 클리어"
            self.auth_policy = "게스트 즉시 플레이 (최고 스테이지 및 스코어 로컬 보존)"
            self.monetization = "부활 코인 충전 & 특수 기체 스킨 결제"
            self.viral_loop = "보스 처치 인증 스크린샷 1클릭 소셜 공유"

        # 1-6. 🛡️ 덱빌딩 & 턴제 전술 RPG (슬레이 더 스파이어, 인투 더 브리치)
        elif any(k in t for k in ["덱빌딩", "턴제", "슬더스", "슬레이", "전술", "rpg"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "🛡️ 덱빌딩 & 턴제 전술 RPG (슬레이 더 스파이어, 인투 더 브리치)"
            self.killer_feature = "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작"
            self.design_mood = "👾 16비트 슈퍼패미컴 클래식 (도트 스프라이트 & 따뜻한 레트로)"
            self.benchmark_reference = "슬레이 더 스파이어 (Slay the Spire) - 턴제 전투, 무작위 카드 드로우, 시너지 덱 빌딩, 엘리트/보스 조우"
            self.quality_tier = "카드의 부드러운 호버 및 드래그 앤 드롭 애니메이션, 피격/타격 이펙트, 턴 종료 연출"
            self.control_scheme = "모바일 터치 드래그 앤 드롭 + PC 마우스 클릭 조작"
            self.game_loop = "전투 돌입 ➔ 카드 선택 및 사용 ➔ 적 턴 방어 ➔ 보상 획득 및 덱 강화"
            self.auth_policy = "0초 무로그인 (진행 상황 로컬 영구저장)"
            self.monetization = "100% 무료 개방"
            self.viral_loop = "내 최종 덱 시너지 및 클리어 인증 SNS 공유"

        # 1-7. 일반 게임 키워드 기본값
        elif any(k in t for k in ["게임", "game", "canvas", "webgl", "3d", "미니게임"]):
            self.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.target_persona = "👾 2D 아케이드 & 하이퍼캐주얼 (수박게임, 2048, 플래피 버드, 벽돌깨기)"
            self.killer_feature = "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작"
            self.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
            self.benchmark_reference = "하이퍼캐주얼 아케이드 - 직관적인 원터치 플레이, 즉각적인 물리 피드백, 1초 재도전 루프"
            self.quality_tier = "Tier 2: 부드러운 60fps Canvas 렌더링, 파티클 이펙트, 화면 흔들림 타격감 완비"
            self.control_scheme = "모바일 터치 가상패드 + 데스크톱 키보드/마우스 듀얼 지원"
            self.game_loop = "게임 스타트 ➔ 직관적 인터랙션 ➔ 스코어 획득 ➔ 게임오버 ➔ 즉시 재도전"
            self.auth_policy = "0초 무로그인 (로컬 최고기록 영구저장)"
            self.monetization = "100% 무료 플레이 & 소셜 공유 바이럴"
            self.viral_loop = "게임 스코어 하이라이트 1클릭 SNS 공유"

        # -------------------------------------------------------------
        # 💼 2. General SaaS / Mobile / Utility Domains
        # -------------------------------------------------------------
        # 2-1. 🏋️ 건강 / 운동 & 피트니스
        elif any(k in t for k in ["운동", "헬스", "피트니스", "식단", "건강", "diet", "fitness", "workout", "칼로리", "체중", "루틴", "pt"]):
            self.target_persona = "🏋️ 건강 / 운동 & 피트니스 (루틴 기록, 식단/칼로리, 헬스 트래커)"
            self.killer_feature = "📝 직관적인 일일 기록 & 통계 대시보드 (캘린더, 그래프, 월간 리포트)"
            if any(k in t for k in ["안드로이드", "android", "apk", "앱", "모바일"]):
                self.platform = "📱 안드로이드 네이티브 앱 (Kotlin + Jetpack Compose)"
            self.benchmark_reference = "밀리의 서재 / 번핏 (Burnfit) - 1세트 완료 체크, 중량/반복 기록, 운동 볼륨 그래프"
            self.quality_tier = "최소 48dp 터치 타깃 최적화, 운동 중 원터치 완료 체크, 오프라인 로컬 캐싱 완비"
            self.auth_policy = "0초 무로그인 (가입 없이 즉시 사용, 로컬 SQLite/IndexedDB 영구저장)"
            self.monetization = "기본 기록 무료 ➔ 주간/월간 AI 정밀 피트니스 리포트 구독"
            self.viral_loop = "오운완(오늘 운동 완료) 인증 카드 1클릭 인스타 스토리 공유"

        # 2-2. 💰 금융 / 가계부 & 자산관리
        elif any(k in t for k in ["금융", "주식", "코인", "가계부", "투자", "시세", "비트코인", "자산", "crypto", "stock", "지출", "머니", "돈"]):
            self.target_persona = "💰 금융 / 가계부 & 주식·코인 (자산 관리, 지출 분석, 실시간 시세 차트)"
            self.killer_feature = "📊 실시간 주가/데이터 스트리밍 & 알림 (웹소켓, 차트 캔들)"
            self.design_mood = "🖤 다크 모던 테크 (네온 액센트 & 사이버/핀테크 대시보드)"
            self.benchmark_reference = "토스 (Toss) - 큼직한 타이포, 카드형 지출 피드, 시인성 높은 실시간 차트"
            self.quality_tier = "실시간 데이터 무깜빡임 갱신, 캔들스틱 차트 반응형 터치 인터랙션, 숫자 카운트업 애니메이션"
            self.auth_policy = "소셜 간편 로그인 (카카오/구글 & 클라우드 데이터 암호화 동기화)"
            self.monetization = "이용량 기반(Pay-as-you-go) 단건 결제 & VIP 실시간 알림 구독"
            self.viral_loop = "월간 지출 분석 / 포트폴리오 수익률 스크린샷 1클릭 공유"

        # 2-3. 🎓 교육 / 어학 & 학습
        elif any(k in t for k in ["교육", "학습", "단어", "영어", "퀴즈", "공부", "edu", "quiz", "voca", "토익", "시험", "플래시카드"]):
            self.target_persona = "🎓 교육 / 어학 & 학습 도구 (단어장, 퀴즈, 플래시카드, AI 튜터)"
            self.killer_feature = "🏆 랭킹 리더보드 & 레벨업/보상 챌린지 (뱃지, 퀘스트, 포인트)"
            self.benchmark_reference = "듀오링고 (Duolingo) - 마일스톤 게이미피케이션, 정답 사운드 피드백, 일일 연속 학습 스트릭"
            self.quality_tier = "카드 뒤집기 3D 플립 애니메이션, 퀴즈 정답 시 꽃가루(Confetti) 축하 이펙트"
            self.auth_policy = "0초 무로그인 (학습 진도 및 북마크 로컬 영구저장)"
            self.monetization = "기본 5개 챕터 무료 ➔ 전체 프리미엄 문제집 1회성 결제"
            self.viral_loop = "퀴즈 만점 성적표 & 챌린지 친구 초대 리워드 링크"

        # 2-4. 🌤️ 날씨 / 환경 & 위젯
        elif any(k in t for k in ["날씨", "미세먼지", "기상", "weather", "환경", "비", "온도", "외출", "우산"]):
            self.target_persona = "🌤️ 날씨 / 생활환경 & 위젯 (실시간 기상, 미세먼지, 외출 추천 알림)"
            self.killer_feature = "📝 직관적인 일일 기록 & 통계 대시보드 (캘린더, 그래프, 월간 리포트)"
            self.design_mood = "🍎 애플 클래식 iOS/macOS (SF Pro, 반투명 블러 & 심플)"
            self.benchmark_reference = "Apple Weather - 날씨 상태별 동적 배경(비/눈/햇살) 애니메이션 및 시간대별 슬라이더"
            self.quality_tier = "CSS/Canvas 비/눈 날씨 파티클 애니메이션, 블러 글래스모피즘 레이아웃"
            self.auth_policy = "0초 무로그인 (내 위치 GPS 자동 감지 & 로컬 즐겨찾기 저장)"
            self.monetization = "100% 무료 개방 (소셜 날씨 카드 공유 바이럴)"
            self.viral_loop = "오늘의 옷차림 & 우산 챙기기 알림 1클릭 카카오톡 전송"

        # 2-5. 📰 뉴스 / 미디어 & RSS
        elif any(k in t for k in ["뉴스", "속보", "아티클", "기사", "news", "rss", "미디어", "피드", "블로그"]):
            self.target_persona = "📰 뉴스 / 미디어 & 아티클 (속보 피드, 뉴스 요약, 맞춤 큐레이션)"
            self.killer_feature = "🔍 스마트 키워드 검색 & 맞춤 필터링 (다양한 정렬/태그 분류)"
            self.design_mood = "📄 미니멀 노션 / 클린 테크 (깔끔한 타이포 & 고생산성)"
            self.benchmark_reference = "Medium / 뉴닉 - 읽기 편한 고품질 타이포그래피 및 3줄 핵심 요약 카드"
            self.quality_tier = "반응형 리더 뷰, 다크모드 무깜빡임 토글, 쾌속 무한 스크롤"
            self.auth_policy = "0초 무로그인 (읽은 기사 북마크 및 키워드 알림 로컬 저장)"
            self.monetization = "기본 무료 ➔ 광고 제거 및 전문 언론사 심층 리포트 구독"
            self.viral_loop = "핵심 3줄 요약 카드 1클릭 SNS 링크 공유"

        # 2-6. 💖 데이팅 / 소셜 & 커뮤니티
        elif any(k in t for k in ["데이트", "소개팅", "만남", "매칭", "dating", "소셜", "친목", "커뮤니티", "친구", "동네"]):
            self.target_persona = "💖 데이팅 / 소셜 & 커뮤니티 (프로필 매칭, 소모임 피드, 실시간 톡)"
            self.killer_feature = "💬 실시간 1:1 / 그룹 채팅 & 소셜 피드 (댓글, 좋아요, 푸시 알림)"
            self.benchmark_reference = "틴더 / 위피 - 스와이프 카드 프로필 넘기기 및 실시간 채팅"
            self.quality_tier = "부드러운 카드 스와이프 제스처 인터랙션 및 타이핑 인디케이터"
            self.auth_policy = "소셜 간편 로그인 (카카오/구글 간편 프로필 생성)"
            self.monetization = "일일 3회 무료 매칭 ➔ 프리미엄 매칭 하트/크레딧 결제"
            self.viral_loop = "친구 초대 시 무료 매칭 하트 5개 증정 바이럴"

        # 2-7. 🛍️ 쇼핑 / 마켓 & 공동구매
        elif any(k in t for k in ["쇼핑", "스토어", "마켓", "공구", "구매", "판매", "shop", "store", "commerce", "폰케이스", "명함"]):
            self.target_persona = "🛍️ 쇼핑 / 마켓 & 공동구매 (장바구니, 결제/주문, 가격 비교, 핫딜)"
            self.killer_feature = "💳 원클릭 간편결제 & 장바구니/주문 시스템 (토스/카카오페이)"
            self.benchmark_reference = "쿠팡 / 오늘의집 - 1초 장바구니 담기, 플로팅 구매 바, 리뷰 갤러리"
            self.quality_tier = "모바일 하단 플로팅 구매 바, 옵션 선택 바텀시트, 결제 완료 축하 모달"
            self.auth_policy = "0초 무로그인 장바구니 ➔ 주문 시 안심 휴대폰 간편 인증"
            self.monetization = "상품 단건 결제 & 무료배송 멤버십 정기구독"
            self.viral_loop = "공동구매 링크 공유 시 양쪽 모두 2,000원 즉시 할인"

        # 2-8. 🤖 AI 챗봇 / 비서 & 상담
        elif any(k in t for k in ["챗봇", "상담", "비서", "ai", "대화", "chatbot", "프롬프트", "번역"]):
            self.target_persona = "🤖 AI 챗봇 / 비서 & 상담 (맞춤형 페르소나 AI 챗, 음성 대화, 번역)"
            self.killer_feature = "🤖 대화형 AI 비서 상담 & 자동 질의응답 (페르소나 챗봇)"
            self.benchmark_reference = "ChatGPT / 뤼튼 - 마크다운 코드블록 렌더링, 실시간 타이핑 스트리밍, 복사 버튼"
            self.quality_tier = "부드러운 스트리밍 말풍선 렌더링, 자동 스크롤 다운, 코드 복사 피드백"
            self.auth_policy = "0초 무로그인 (대화 세션 및 메모리 로컬 영구저장)"
            self.monetization = "일일 10턴 무료 ➔ 무제한 고성능 LLM 스트리밍 구독"
            self.viral_loop = "AI가 작성한 재치있는 답변 스크린샷 1클릭 공유"

        # Platform keyword inference
        if any(k in t for k in ["윈도우", "windows", "win", "wpf", "winui", "exe", "pyqt"]):
            self.platform = "🪟 윈도우 네이티브 데스크톱 앱 (C# WPF / WinUI 3 or PyQt6)"
            self.design_mood = "🪟 윈도우 11 플루언트 (Fluent Design Mica & 아크릴 블러)"
        elif any(k in t for k in ["맥", "mac", "macos", "ios", "아이폰", "swift", "swiftui", "애플"]):
            self.platform = "🍎 애플 iOS / macOS 네이티브 앱 (Swift + SwiftUI)"
            self.design_mood = "🍎 애플 클래식 iOS/macOS (SF Pro, 반투명 블러 & 심플)"
        elif any(k in t for k in ["크롤", "봇", "스크랩", "알림", "디스코드", "텔레그램", "bot", "crawler", "스케줄러"]):
            self.platform = "🤖 백그라운드 자동화 / 크롤러 & 봇 (Python / Node.js)"
            self.killer_feature = "📅 드래그앤드롭 플래너 & 일정/루틴 알람 (스케줄러, 타이머)"
            self.design_mood = "🖥️ 레트로 터미널 / 해커 CLI (모노스페이스 & 그린/엠버)"
        elif any(k in t for k in ["cli", "터미널", "명령어", "terminal", "command", "스크립트"]):
            self.platform = "⚡ CLI / 고성능 터미널 유틸리티 (Python / Rich)"
            self.design_mood = "🖥️ 레트로 터미널 / 해커 CLI (모노스페이스 & 그린/엠버)"

        # Design Theme keyword inference
        if any(k in t for k in ["다크", "네온", "사이버", "다크모드"]):
            self.design_mood = "🖤 다크 모던 테크 (네온 액센트 & 사이버/핀테크 대시보드)"
        elif any(k in t for k in ["노션", "미니멀", "clean", "심플"]):
            self.design_mood = "📄 미니멀 노션 / 클린 테크 (깔끔한 타이포 & 고생산성)"
        elif any(k in t for k in ["브루탈", "브루탈리즘", "레트로팝", "힙"]):
            self.design_mood = "⚡ 네오 브루탈리즘 (볼드 보더 & 레트로 팝)"
        elif any(k in t for k in ["픽셀", "도트", "8비트", "아케이드", "레트로"]):
            self.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
        elif any(k in t for k in ["터미널", "해커", "콘솔", "cmd"]):
            self.design_mood = "🖥️ 레트로 터미널 / 해커 CLI (모노스페이스 & 그린/엠버)"
        elif any(k in t for k in ["오로라", "글로우", "우주", "그라디언트"]):
            self.design_mood = "🌌 딥 스페이스 오로라 (그라디언트 글로우 & 미래지향적)"

    def get_preset_key(self) -> str:
        p_lower = self.platform.lower()
        if "안드로이드" in p_lower or "android" in p_lower or "compose" in p_lower:
            return "app"
        elif "모바일 터치 캐주얼" in p_lower:
            return "mobilegame"
        elif "레트로 8-bit" in p_lower or "16-bit" in p_lower:
            return "retrogame"
        elif "게임" in p_lower or "game" in p_lower or "canvas" in p_lower:
            return "game"
        elif "윈도우" in p_lower or "windows" in p_lower or "win" in p_lower or "wpf" in p_lower:
            return "win"
        elif "애플" in p_lower or "apple" in p_lower or "ios" in p_lower or "mac" in p_lower or "swift" in p_lower:
            return "apple"
        elif "api" in p_lower or "백엔드" in p_lower or "server" in p_lower or "msa" in p_lower:
            return "api"
        elif "크로스플랫폼 데스크톱" in p_lower or "electron" in p_lower or "tauri" in p_lower:
            return "desktop"
        elif "bot" in p_lower or "봇" in p_lower or "crawler" in p_lower or "크롤러" in p_lower:
            return "bot"
        elif "cli" in p_lower or "터미널" in p_lower:
            return "cli"
        elif "extension" in p_lower or "익스텐션" in p_lower or "크롬" in p_lower:
            return "extension"
        return "web"

    def get_visual_meta(self) -> Dict[str, Any]:
        key = self.get_preset_key()
        meta = PLATFORM_GALLERY.get(key, PLATFORM_GALLERY["web"])
        
        # Color override by design mood
        for theme_k, color_v in THEME_COLOR_MAP.items():
            if theme_k in self.design_mood:
                meta = dict(meta)
                meta["color"] = color_v
                break
        return meta

    def generate_prd_content(self) -> str:
        """
        Generates high-precision PRD.md content with clear REQ-IDs,
        specifically adapting deep game-design or SaaS requirements based on domain.
        """
        if self.is_game_domain():
            ref_str = self.benchmark_reference or "수박게임 / 뱀파이어 서바이벌 / 2D 아케이드 명작"
            quality_str = self.quality_tier or "Tier 2: 60fps 무지연 Canvas 렌더링, 파티클 이펙트, 화면 흔들림(Screen Shake) 타격감 완비"
            control_str = self.control_scheme or "모바일 터치 가상 조이스틱/원터치 조작 + PC 키보드 WASD/화살표 듀얼 지원"
            loop_str = self.game_loop or "게임 스타트 ➔ 60fps 코어 인터랙션 ➔ 스코어/아이템 파밍 ➔ 난이도 점진 상승 ➔ 게임오버 ➔ 1초 퀵 재도전"

            return f"""# 📋 PROJECT REQUIREMENTS DOCUMENT (PRD) - GAME SPECIFICATION
## 게임 타이틀: **{self.initial_idea}**

---

### 1. 플랫폼 및 게임 엔진 아키텍처 (Platform & Engine)
* **목표 플랫폼**: `{self.platform}`
* **게임 장르 (Genre & Domain)**: {self.target_persona}
* **엔진 및 렌더링 스택**: HTML5 Canvas (60fps requestAnimationFrame 무지연 루프) / WebAudio API 절차적 사운드
* **반응형 뷰포트**: 모바일(390px 스마트폰 터치) ~ 태블릿(768px) ~ 데스크톱(1280px+ 와이드) 가로 스크롤 0px 완벽 방어

---

### 2. 벤치마크 레퍼런스 및 퀄리티 목표 (Benchmark & Quality Tier)
* **벤치마크 참고 게임 (Benchmark References)**: {ref_str}
* **완성도 및 그래픽 품질 등급 (Quality Tier)**: {quality_str}
* **시각 연출 및 타격감 (Visual Juice & Feedback)**:
  - 오브젝트 파괴/피격 시 절차적 파티클(Particles) 폭발 효과
  - 크리티컬 피격 시 화면 흔들림(Screen Shake: 3~6px 0.15초) 타격감 구현
  - 콤보/점수 획득 시 팝업 플로팅 텍스트(Floating Combo Text)
  - 부드러운 승리/게임오버 오버레이 애니메이션

---

### 3. 핵심 게임플레이 루프 및 조작 체계 (Core Loop & Controls)
* **핵심 기능 (REQ-001)**: {self.killer_feature}
* **핵심 게임 루프 (Golden Loop - REQ-002)**: 
  {loop_str}
* **조작 체계 (Controls Matrix - REQ-003)**: 
  {control_str}
  - 📱 모바일: 온스크린 가상 아날로그 조이스틱(Virtual Joystick) 또는 원터치 제스처 터치패드
  - 💻 데스크톱: 키보드 WASD / 방향키 + 스페이스바(액션) + 마우스 클릭 듀얼 지원

---

### 4. 사운드 및 오디오 아키텍처 (Sound FX & BGM - REQ-004)
* **WebAudio 절차적 합성 사운드 엔진**: 외부 무거운 오디오 파일 의존성 없이 순수 WebAudio API 오실레이터(Oscillator) 합성
* **사운드 구성**:
  - 8비트 레트로 칩튠 BGM 루프 (음악 토글 Mute 버튼 기본 제공)
  - 점프/이동/발사/파밍 효과음 (Square wave / Sine wave 피치 변조)
  - 폭발/피격/충돌 타격 효과음 (Noise wave 감쇄 사운드)

---

### 5. 스코어 시스템 및 데이터 영속성 (Score & Persistence - REQ-005)
* **인증 및 저장 정책**: {self.auth_policy}
* **데이터 보관**: 브라우저 `localStorage`를 이용한 최고 기록(High Score), 플레이 타임, 업적 영구 보존
* **수익화 모델**: {self.monetization}
* **바이럴 그로스 엔진**: {self.viral_loop} (최종 스코어 및 하이라이트 결과 카드 1클릭 SNS 공유)

---

### 6. 비주얼 아트 테마 & 특수 지시사항 (Visual Theme & Notes - REQ-006)
* **비주얼 디자인 테마**: {self.design_mood}
* **기획자 특수 지시사항**: {self.custom_notes if self.custom_notes else "결함 0건, 가로 스크롤 0px, 60fps 무지연 Canvas 게임 루프 완비"}
"""

        # General SaaS / App PRD
        ref_str = self.benchmark_reference or "토스 / 노션 / 당근마켓 등 업계 표준 서비스"
        quality_str = self.quality_tier or "최소 48dp 터치 타깃 최적화, 로딩 스켈레톤, 빈 화면(Empty State) 가이드, 반응형 390px~1280px 완비"

        return f"""# 📋 PROJECT REQUIREMENTS DOCUMENT (PRD)
## 프로젝트 명: **{self.initial_idea}**

---

### 1. 플랫폼 및 아키텍처 스펙 (Platform & Architecture)
* **목표 플랫폼**: `{self.platform}`
* **서비스 카테고리 (Category & Domain)**: {self.target_persona}
* **핵심 가치 제안**: 즉각적인 반응성, 제로 딜레이 인터랙션 및 최고 수준의 사용자 경험 제공
* **벤치마크 참고 서비스 (Benchmark Reference)**: {ref_str}

---

### 2. 핵심 기능 및 사용자 인터랙션 (Core Features & UX)
* **핵심 기능 (REQ-001)**: {self.killer_feature}
* **완성도 및 품질 목표 (Quality Tier - REQ-002)**: {quality_str}
* **데이터 플로우**: 직관적인 사용자 입력 ➔ 실시간 가공 및 시각화 ➔ 즉각적인 결과 피드백
* **사용자 핵심 여정 (Golden Flow)**: 
  1. 진입 (0초 온보딩) ➔ 2. 주요 액션/탐색 ➔ 3. 인터랙티브 결과 렌더링 ➔ 4. 로컬 저장 및 1클릭 공유

---

### 3. 온보딩 및 데이터 보관 정책 (Auth & Persistence)
* **인증 및 저장 방식 (REQ-003)**: {self.auth_policy}
* **무중단 내구성 가이드**: 오프라인 로컬 저장소 우선 및 네트워크 불안정 시 자동 복구 지원

---

### 4. 수익화 모델 및 바이럴 성장 루프 (Monetization & Growth)
* **비즈니스 모델 (REQ-004)**: {self.monetization}
* **바이럴 그로스 엔진 (REQ-005)**: {self.viral_loop}
* **소셜 메타데이터**: 카카오톡/인스타그램 공유 시 맞춤형 썸네일과 미리보기가 노출되는 메타태그 완비

---

### 5. UI/UX 디자인 시스템 헌법 (Visual Standard - REQ-006)
* **비주얼 테마**: {self.design_mood}
* **인체공학 설계**: 모바일 최소 48dp 터치 타겟, 가로 스크롤(Horizontal Overflow) 0px 방어
* **반응형 매트릭스**: 모바일(390px) ~ 태블릿(768px) ~ 데스크톱(1280px+) 전수 최적화

---

### 6. 기획자 특수 지시사항 (Custom Requirements)
* **추가 요구사항**: {self.custom_notes if self.custom_notes else "결함 0건, 가로 스크롤 0px, 반응형 모바일(390px)~데스크톱(1280px) 완비"}
"""

    def generate_full_prompt(self) -> str:
        domain_tag = "🎮 게임 스펙" if self.is_game_domain() else "📱 제품 스펙"
        return f"""[PROJECT SPECIFICATION: {self.initial_idea}]
반드시 다음 PRD 기획서와 플랫폼 헌법에 따라 완벽히 동작하는 프로덕션 코드를 구현하고 배포해주세요.

1. 📱 플랫폼 & 폼팩터: {self.platform}
2. 🎯 서비스 카테고리: {self.target_persona}
3. 🕹️ 벤치마크 레퍼런스: {self.benchmark_reference or '자동 최적화'}
4. 💎 품질 및 완성도 목표: {self.quality_tier or 'Tier 2 60fps 완성도'}
5. ⚡ 핵심 킬러 기능: {self.killer_feature}
6. 🎨 비주얼 디자인 테마: {self.design_mood}
7. 🔐 온보딩 & 데이터 보관: {self.auth_policy}
8. 💰 수익화 & 바이럴: {self.monetization} (바이럴: {self.viral_loop})
9. 📝 추가 요구사항: {self.custom_notes if self.custom_notes else "결함 0건, 가로 스크롤 0px, 반응형 모바일(390px)~데스크톱(1280px) 완비"}

배포 후 E2E 검증에서 결함과 콘솔 에러가 0건이 될 때까지 스스로 핫픽스하여 완성해주세요."""


# In-memory wizard session store
_WIZARD_SESSIONS: Dict[int, PlanWizardState] = {}


class AdvancedSettingsModal(discord.ui.Modal, title="⚙️ 레퍼런스/품질/BM 세부 설정"):
    benchmark_input = discord.ui.TextInput(
        label="참고할 벤치마크 레퍼런스 (게임/서비스)",
        style=discord.TextStyle.short,
        placeholder="예: '뱀파이어 서바이벌 3지선다 파밍', '수박게임 물리 합성', '토스 미니멀 카드'",
        required=False,
        max_length=200
    )
    quality_input = discord.ui.TextInput(
        label="그래픽 퀄리티 & 완성도 목표",
        style=discord.TextStyle.short,
        placeholder="예: '60fps Canvas, 피격 화면흔들림, WebAudio 8bit SFX', '최소 48dp 터치 타깃'",
        required=False,
        max_length=200
    )
    auth_input = discord.ui.TextInput(
        label="인증 및 데이터 보관 방식",
        style=discord.TextStyle.short,
        placeholder="예: 무로그인 LocalStorage 최고기록 저장, 카카오/구글 소셜 로그인, JWT 이메일",
        required=False,
        max_length=200
    )
    monetization_input = discord.ui.TextInput(
        label="수익화(BM) & 바이럴 전략",
        style=discord.TextStyle.short,
        placeholder="예: 100% 무료 플레이+SNS 결과 카드 공유, 인게임 아이템 결제, 토스페이",
        required=False,
        max_length=200
    )
    custom_notes_input = discord.ui.TextInput(
        label="기획자 특수 지시사항 (커스텀 프롬프트)",
        style=discord.TextStyle.paragraph,
        placeholder="예: '가상 조이스틱 필수', '다크모드 토글', 'WebAudio 사운드 ON/OFF 버튼'",
        required=False,
        max_length=1000
    )

    def __init__(self, state: PlanWizardState, on_complete):
        super().__init__()
        self.state = state
        self.on_complete = on_complete

        if self.state.benchmark_reference:
            self.benchmark_input.default = self.state.benchmark_reference[:180]
        if self.state.quality_tier:
            self.quality_input.default = self.state.quality_tier[:180]
        self.auth_input.default = self.state.auth_policy[:180]
        self.monetization_input.default = self.state.monetization[:180]
        if self.state.custom_notes:
            self.custom_notes_input.default = self.state.custom_notes[:900]

    async def on_submit(self, interaction: discord.Interaction):
        if self.benchmark_input.value.strip():
            self.state.benchmark_reference = self.benchmark_input.value.strip()
        if self.quality_input.value.strip():
            self.state.quality_tier = self.quality_input.value.strip()
        if self.auth_input.value.strip():
            self.state.auth_policy = self.auth_input.value.strip()
        if self.monetization_input.value.strip():
            self.state.monetization = self.monetization_input.value.strip()
        self.state.custom_notes = self.custom_notes_input.value.strip()
        await self.on_complete(interaction)


WIZARD_OPTIONS_MAP = {
    # Platforms (Row 0)
    "opt_0": "🌐 웹 & SaaS (React + TypeScript + Vite + TailwindCSS)",
    "opt_1": "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)",
    "opt_2": "🕹️ 레트로 8-bit / 16-bit 픽셀 게임 (도트 아케이드 & 칩튠 SFX)",
    "opt_3": "📱 안드로이드 네이티브 앱 (Kotlin + Jetpack Compose)",
    "opt_4": "📲 모바일 터치 캐주얼 게임 (Android / 모바일 가상 조이스틱)",
    "opt_5": "🪟 윈도우 네이티브 데스크톱 앱 (C# WPF / WinUI 3 or PyQt6)",
    "opt_6": "🍎 애플 iOS / macOS 네이티브 앱 (Swift + SwiftUI)",
    "opt_7": "💻 크로스플랫폼 데스크톱 앱 (Electron / Tauri / Rust)",
    "opt_8": "🤖 백그라운드 자동화 / 크롤러 & 봇 (Python / Node.js)",
    "opt_9": "🧩 크롬 브라우저 확장 프로그램 (Manifest V3 + Vite)",
    "opt_10": "⚡ CLI / 고성능 터미널 유틸리티 (Python / Rich)",
    "opt_11": "☁️ 고성능 백엔드 API & MSA (FastAPI / NestJS + DB)",

    # Categories & Genres (Row 1)
    "opt_12": "👾 2D 아케이드 & 하이퍼캐주얼 (수박게임, 2048, 플래피 버드, 벽돌깨기)",
    "opt_13": "⚔️ 탑다운 액션 & 서바이벌 로그라이크 (뱀파이어 서바이벌, 브롤스타즈)",
    "opt_14": "🏃 횡스크롤 런 & 플랫포머 (슈퍼마리오, 쿠키런, 지오메트리 대시)",
    "opt_15": "🧩 두뇌 퍼즐 & 블록 매칭 (테트리스, 매치3, 스도쿠, 오목)",
    "opt_16": "🛡️ 덱빌딩 & 턴제 전술 RPG (슬레이 더 스파이어, 인투 더 브리치)",
    "opt_17": "🚀 탄막 슈팅 & 디펜스 (1942, 갤러그, 뱀파이어 닷지, 타워 디펜스)",
    "opt_18": "📋 생산성 / 할일 & 스마트 메모 (투두리스트, 캘린더, 칸반 보드, 노트)",
    "opt_19": "💰 금융 / 가계부 & 주식·코인 (자산 관리, 지출 분석, 실시간 시세 차트)",
    "opt_20": "🏋️ 건강 / 운동 & 피트니스 (루틴 기록, 식단/칼로리, 헬스 트래커)",
    "opt_21": "🎓 교육 / 어학 & 학습 도구 (단어장, 퀴즈, 플래시카드, AI 튜터)",
    "opt_22": "🌤️ 날씨 / 생활환경 & 위젯 (실시간 기상, 미세먼지, 외출 추천 알림)",
    "opt_23": "📰 뉴스 / 미디어 & 아티클 (속보 피드, 뉴스 요약, 맞춤 큐레이션)",
    "opt_24": "💖 데이팅 / 소셜 & 커뮤니티 (프로필 매칭, 소모임 피드, 실시간 톡)",
    "opt_25": "🛍️ 쇼핑 / 이커머스 & 핫딜 (장바구니, 결제/주문, 가격 비교)",
    "opt_26": "🤖 AI 챗봇 / 비서 & 상담 (맞춤형 페르소나 AI 챗, 음성 대화, 번역)",
    "opt_27": "🏢 업무용 B2B / SaaS 관리자 (직원/프로젝트 관리, 통계 대시보드)",

    # Features & Core Loop (Row 2)
    "opt_28": "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작",
    "opt_29": "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감",
    "opt_30": "🎵 WebAudio 8-bit 절차적 레트로 BGM & 칩튠 점프/타격 효과음",
    "opt_31": "⚡ 아이템 파밍 & 캐릭터 스탯 성장/스킬 트리 업그레이드",
    "opt_32": "🏆 글로벌 랭킹 리더보드 & 최고기록(High Score) 로컬 저장",
    "opt_33": "📝 직관적인 일일 기록 & 통계 대시보드 (캘린더, 그래프, 월간 리포트)",
    "opt_34": "💬 실시간 1:1 / 그룹 채팅 & 소셜 피드 (댓글, 좋아요, 푸시 알림)",
    "opt_35": "🔍 스마트 키워드 검색 & 맞춤 필터링 (다양한 정렬/태그 분류)",
    "opt_36": "💳 원클릭 간편결제 & 장바구니/주문 시스템 (토스/카카오페이)",
    "opt_37": "📄 1클릭 PDF/Excel 리포트 다운로드 & SNS 공유 카드",
    "opt_38": "🤖 대화형 AI 비서 상담 & 자동 질의응답 (페르소나 챗봇)",

    # Aesthetics & Themes (Row 3)
    "opt_39": "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)",
    "opt_40": "👾 16비트 슈퍼패미컴 클래식 (도트 스프라이트 & 따뜻한 레트로)",
    "opt_41": "🍬 큐트 파스텔 카툰 (동물의 숲 & 캐주얼 팝 아트)",
    "opt_42": "🖤 다크 모던 테크 (네온 액센트 & 사이버/핀테크 대시보드)",
    "opt_43": "🪟 윈도우 11 플루언트 (Fluent Design Mica & 아크릴 블러)",
    "opt_44": "🍎 애플 클래식 iOS/macOS (SF Pro, 반투명 블러 & 심플)",
    "opt_45": "📄 미니멀 노션 / 클린 테크 (깔끔한 타이포 & 고생산성)",
    "opt_46": "⚡ 네오 브루탈리즘 (볼드 보더 & 레트로 팝)",
    "opt_47": "🖥️ 레트로 터미널 / 해커 CLI (모노스페이스 & 그린/엠버)",
    "opt_48": "🌌 딥 스페이스 오로라 (그라디언트 글로우 & 미래지향적)",
}


class PlanWizardView(discord.ui.View):
    def __init__(
        self,
        state: PlanWizardState,
        session_manager: SessionManager,
        bot_client: discord.Client,
        qa_bot_client: discord.Client,
        auditor_bot_client: discord.Client
    ):
        super().__init__(timeout=600)
        self.state = state
        self.session_manager = session_manager
        self.bot_client = bot_client
        self.qa_bot_client = qa_bot_client
        self.auditor_bot_client = auditor_bot_client

        # Inherit existing platform from PRD.md to respect workspace grounding
        try:
            session = self.session_manager.get_session(self.state.channel_id)
            if session and session.workspace and os.path.isdir(session.workspace):
                prd_path = os.path.join(session.workspace, "PRD.md")
                if os.path.exists(prd_path):
                    with open(prd_path, "r", encoding="utf-8") as f:
                        prd_content = f.read()
                    match_plat = re.search(r"\*\*\s*목표 플랫폼\s*\*\*\s*:\s*`([^`]+)`", prd_content)
                    if match_plat:
                        self.state.platform = match_plat.group(1).strip()
                    match_cat = re.search(r"\*\*\s*서비스 카테고리[^*]*\*\*\s*:\s*(.+)", prd_content)
                    if match_cat:
                        self.state.target_persona = match_cat.group(1).strip()
        except Exception:
            pass

        # Dynamically synchronize select UI options with state
        self._sync_select_defaults()

    def _sync_select_defaults(self):
        """Ensures all Select options match the exact values in self.state."""
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                if child.custom_id == "select_platform":
                    for opt in child.options:
                        opt.default = (WIZARD_OPTIONS_MAP.get(opt.value, opt.value) == self.state.platform)
                elif child.custom_id == "select_category":
                    for opt in child.options:
                        opt.default = (WIZARD_OPTIONS_MAP.get(opt.value, opt.value) == self.state.target_persona)
                elif child.custom_id == "select_feature":
                    for opt in child.options:
                        opt.default = (WIZARD_OPTIONS_MAP.get(opt.value, opt.value) == self.state.killer_feature)
                elif child.custom_id == "select_design":
                    for opt in child.options:
                        opt.default = (WIZARD_OPTIONS_MAP.get(opt.value, opt.value) == self.state.design_mood)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state.user_id:
            await interaction.response.send_message("⚠️ 이 기획 위저드를 시작한 사용자만 조작할 수 있습니다.", ephemeral=True)
            return False
        return True

    def build_dashboard_embed(self) -> discord.Embed:
        meta = self.state.get_visual_meta()
        
        safe_idea = self.state.initial_idea
        if len(safe_idea) > 150:
            safe_idea = safe_idea[:147] + "..."
            
        is_game = self.state.is_game_domain()
        badge_text = "🎮 Game Studio Pro" if is_game else meta.get('badge', '✨ Vibe Coding')

        embed = discord.Embed(
            title=f"📋 [AGY 다차원 기획 스튜디오: {safe_idea}]",
            description=(
                f"**{badge_text}**\n"
                "원하는 **플랫폼, 게임 장르/카테고리, 벤치마크 레퍼런스, 퀄리티 목표**를 선택하면 기획서(`PRD.md`)와 개발 사다리가 완벽하게 구성됩니다."
            ),
            color=meta.get("color", 0x3B82F6)
        )

        thumb_url = meta.get("thumb")
        if thumb_url:
            embed.set_thumbnail(url=thumb_url)
            
        banner_url = meta.get("banner")
        if banner_url:
            embed.set_image(url=banner_url)

        embed.add_field(
            name="1️⃣ 📱 플랫폼 & 폼팩터",
            value=f"`{self.state.platform}`",
            inline=False
        )
        embed.add_field(
            name="2️⃣ 🎯 서비스 주제 & 게임 장르",
            value=f"`{self.state.target_persona}`",
            inline=False
        )
        
        # Benchmark & Quality Target section (directly solves user need)
        ref_display = self.state.benchmark_reference or ("뱀파이어 서바이벌 / 수박게임 / 2D 아케이드" if is_game else "토스 / 노션 / 당근마켓 등 표준")
        quality_display = self.state.quality_tier or ("Tier 2: 60fps 무지연 Canvas, 파티클 폭발, 화면 흔들림 타격감" if is_game else "최소 48dp 터치 타깃, 로딩 스켈레톤, 반응형 390px~1280px")
        
        embed.add_field(
            name="🕹️ 🎮 벤치마크 레퍼런스 & 퀄리티 목표",
            value=(
                f"• **참고 레퍼런스**: `{ref_display}`\n"
                f"• **그래픽/완성도 목표**: `{quality_display}`"
            ),
            inline=False
        )

        embed.add_field(
            name="3️⃣ ⚡ 핵심 기능 & UX 스타일",
            value=f"`{self.state.killer_feature}`",
            inline=False
        )
        embed.add_field(
            name="4️⃣ 🎨 비주얼 디자인 테마",
            value=f"`{self.state.design_mood}`",
            inline=False
        )
        embed.add_field(
            name="5️⃣ 🔐 온보딩 & BM 전략",
            value=(
                f"• **인증/보관**: `{self.state.auth_policy}`\n"
                f"• **수익화/성장**: `{self.state.monetization}`"
            ),
            inline=False
        )
        if self.state.custom_notes:
            embed.add_field(
                name="6️⃣ 📝 기획자 특수 지시사항",
                value=f"```text\n{self.state.custom_notes}\n```",
                inline=False
            )

        embed.set_footer(text="설정 후 [⚙️ 레퍼런스/세부설정]에서 튜닝하거나, [🚀 개발 시작]을 누르면 3중 협업 코딩이 가동됩니다.")
        return embed

    # --- Row 0: Platform & Form Factor ---
    @discord.ui.select(
        placeholder="1️⃣ 📱 플랫폼 및 폼팩터 선택 (웹 게임, 레트로 8-bit, 네이티브 앱 등)",
        options=[
            discord.SelectOption(label="반응형 웹 & SaaS", value="opt_0"),
            discord.SelectOption(label="인터랙티브 웹 게임 (Canvas/WebGL)", value="opt_1"),
            discord.SelectOption(label="레트로 8-bit / 16-bit 픽셀 게임", value="opt_2"),
            discord.SelectOption(label="안드로이드 네이티브 앱 (Compose)", value="opt_3"),
            discord.SelectOption(label="모바일 터치 캐주얼 게임 (Android)", value="opt_4"),
            discord.SelectOption(label="윈도우 네이티브 데스크톱 앱", value="opt_5"),
            discord.SelectOption(label="애플 iOS / macOS 네이티브 앱", value="opt_6"),
            discord.SelectOption(label="크로스플랫폼 데스크톱 앱 (Tauri)", value="opt_7"),
            discord.SelectOption(label="백그라운드 자동화 & 크롤러 봇", value="opt_8"),
            discord.SelectOption(label="크롬 브라우저 확장 프로그램", value="opt_9"),
            discord.SelectOption(label="고성능 CLI 터미널 도구", value="opt_10"),
            discord.SelectOption(label="고성능 백엔드 API & MSA", value="opt_11"),
        ],
        custom_id="select_platform",
        row=0
    )
    async def select_platform(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.platform = WIZARD_OPTIONS_MAP.get(select.values[0], select.values[0])
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)

    # --- Row 1: Service Category & Game Genre ---
    @discord.ui.select(
        placeholder="2️⃣ 🎯 서비스 주제 및 게임 장르 선택 (로그라이크, 아케이드, 퍼즐, 금융 등)",
        options=[
            # Games First
            discord.SelectOption(label="2D 아케이드 & 하이퍼캐주얼 (수박게임)", value="opt_12"),
            discord.SelectOption(label="탑다운 액션 & 로그라이크 (뱀파이어 서바이벌)", value="opt_13"),
            discord.SelectOption(label="횡스크롤 런 & 플랫포머 (슈퍼마리오/쿠키런)", value="opt_14"),
            discord.SelectOption(label="두뇌 퍼즐 & 블록 매칭 (테트리스/2048)", value="opt_15"),
            discord.SelectOption(label="덱빌딩 & 턴제 전술 RPG (슬더스 스타일)", value="opt_16"),
            discord.SelectOption(label="탄막 슈팅 & 디펜스 (1942/갤러그)", value="opt_17"),
            # General SaaS
            discord.SelectOption(label="생산성 / 할일 & 스마트 메모", value="opt_18"),
            discord.SelectOption(label="금융 / 가계부 & 주식·코인", value="opt_19"),
            discord.SelectOption(label="건강 / 운동 & 피트니스", value="opt_20"),
            discord.SelectOption(label="교육 / 어학 & 학습 도구", value="opt_21"),
            discord.SelectOption(label="날씨 / 생활환경 & 위젯", value="opt_22"),
            discord.SelectOption(label="뉴스 / 미디어 & 아티클", value="opt_23"),
            discord.SelectOption(label="데이팅 / 소셜 & 커뮤니티", value="opt_24"),
            discord.SelectOption(label="쇼핑 / 이커머스 & 핫딜", value="opt_25"),
            discord.SelectOption(label="AI 챗봇 / 비서 & 상담", value="opt_26"),
            discord.SelectOption(label="업무용 B2B / SaaS 관리자", value="opt_27"),
        ],
        custom_id="select_category",
        row=1
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        selected_val = WIZARD_OPTIONS_MAP.get(select.values[0], select.values[0])
        self.state.target_persona = selected_val

        # Smart automatic adaptation when a game genre is selected
        if "수박게임" in selected_val or "아케이드" in selected_val:
            self.state.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.state.benchmark_reference = "수박게임 (Suika Game) - 상단 과일 투하, 2D 탄성 물리 충돌, 동일 크기 접촉 시 상위 단계 합성 및 팝 이펙트"
            self.state.quality_tier = "부드러운 60fps 2D 원형 물리 충돌 합성, 합성 시 과즙 팝 파티클 및 바운스 탄성 애니메이션"
            self.state.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.state.design_mood = "🍬 큐트 파스텔 카툰 (동물의 숲 & 캐주얼 팝 아트)"
        elif "뱀파이어" in selected_val or "로그라이크" in selected_val:
            self.state.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.state.benchmark_reference = "뱀파이어 서바이벌 (Vampire Survivors) - 360도 이동, 자동 발사, 경험치 젬 파밍 & 3지선다 무기 레벨업"
            self.state.quality_tier = "Tier 2: 60fps Canvas 렌더링, 적 웨이브 스폰, 피격 파티클 폭발, 화면 흔들림 타격감"
            self.state.killer_feature = "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작"
            self.state.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
        elif "슈퍼마리오" in selected_val or "쿠키런" in selected_val:
            self.state.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.state.benchmark_reference = "쿠키런 / 슈퍼마리오 - 횡스크롤 전진, 점프/슬라이드 조작, 젤리/코인 획득 및 장애물 피격 피드백"
            self.state.quality_tier = "60fps 중력 가속도 점프 곡선, 달리기 먼지 파티클, 피격 시 반짝임 무적 판정"
            self.state.killer_feature = "🕹️ 60fps 게임 루프 & 모바일 가상 조이스틱/키보드 듀얼 조작"
            self.state.design_mood = "👾 16비트 슈퍼패미컴 클래식 (도트 스프라이트 & 따뜻한 레트로)"
        elif "테트리스" in selected_val:
            self.state.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.state.benchmark_reference = "테트리스 (Tetris) - 7종 블록 낙하, 회전 및 하드드롭, 줄 완성 시 라인 클리어 플래시 및 속도 가속"
            self.state.quality_tier = "그리드 기반 완벽한 충돌 감지, 블록 회전 킥(Wall Kick), 라인 삭제 시 반짝임 이펙트"
            self.state.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.state.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"
        elif "덱빌딩" in selected_val or "턴제" in selected_val:
            self.state.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.state.benchmark_reference = "슬레이 더 스파이어 (Slay the Spire) - 턴제 전투, 무작위 카드 드로우, 시너지 덱 빌딩, 엘리트/보스 조우"
            self.state.quality_tier = "카드의 부드러운 호버 및 드래그 앤 드롭 애니메이션, 피격/타격 이펙트, 턴 종료 연출"
            self.state.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.state.design_mood = "👾 16비트 슈퍼패미컴 클래식 (도트 스프라이트 & 따뜻한 레트로)"
        elif "탄막" in selected_val or "슈팅" in selected_val:
            self.state.platform = "🎮 인터랙티브 웹 게임 (HTML5 Canvas + WebAudio / Three.js)"
            self.state.benchmark_reference = "1942 / 갤러그 - 종스크롤 탄막 슈팅, 파워업 캡슐 아이템, 보스 패턴 회피"
            self.state.quality_tier = "60fps 다중 탄막 렌더링, 폭발 파티클 및 폭탄 투하 화면 플래시, 칩튠 사운드"
            self.state.killer_feature = "💥 절차적 파티클 이펙트 & 화면 흔들림(Screen Shake) 타격감"
            self.state.design_mood = "🎮 레트로 8비트 네온 픽셀 (아케이드 사이버 감성 & 도트 UI)"

        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)

    # --- Row 2: Features & UX ---
    @discord.ui.select(
        placeholder="3️⃣ ⚡ 핵심 기능 및 사용자 경험(UX) 스타일 선택 (게임 루프, 파티클, BGM 등)",
        options=[
            discord.SelectOption(label="60fps 게임 루프 & 가상 조이스틱/키보드 듀얼", value="opt_28"),
            discord.SelectOption(label="절차적 파티클 & 화면 흔들림 타격감", value="opt_29"),
            discord.SelectOption(label="WebAudio 8-bit 절차적 BGM & 칩튠 SFX", value="opt_30"),
            discord.SelectOption(label="아이템 파밍 & 캐릭터 스탯 성장 루프", value="opt_31"),
            discord.SelectOption(label="글로벌 랭킹 리더보드 & 최고기록 로컬 저장", value="opt_32"),
            discord.SelectOption(label="일일 기록 & 통계 분석 대시보드", value="opt_33"),
            discord.SelectOption(label="실시간 채팅 & 소셜 피드", value="opt_34"),
            discord.SelectOption(label="스마트 키워드 검색 & 맞춤 필터링", value="opt_35"),
            discord.SelectOption(label="원클릭 간편결제 & 주문 시스템", value="opt_36"),
            discord.SelectOption(label="1클릭 PDF/Excel 리포트 & SNS 공유 카드", value="opt_37"),
            discord.SelectOption(label="대화형 AI 질의응답 & 맞춤 추천 비서", value="opt_38"),
        ],
        custom_id="select_feature",
        row=2
    )
    async def select_feature(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.killer_feature = WIZARD_OPTIONS_MAP.get(select.values[0], select.values[0])
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)

    # --- Row 3: Design Theme & Visual Aesthetics ---
    @discord.ui.select(
        placeholder="4️⃣ 🎨 비주얼 UI 및 그래픽 아트 테마 선택 (도트 픽셀, 16비트, 파스텔 등)",
        options=[
            discord.SelectOption(label="레트로 8비트 네온 픽셀 (아케이드 사이버)", value="opt_39"),
            discord.SelectOption(label="16비트 슈퍼패미컴 클래식 (도트 스프라이트)", value="opt_40"),
            discord.SelectOption(label="큐트 파스텔 카툰 (동물의 숲 감성)", value="opt_41"),
            discord.SelectOption(label="다크 모던 테크 (네온 액센트)", value="opt_42"),
            discord.SelectOption(label="윈도우 11 플루언트 (Fluent Mica)", value="opt_43"),
            discord.SelectOption(label="애플 클래식 iOS/macOS (SF Pro)", value="opt_44"),
            discord.SelectOption(label="미니멀 노션 / 클린 테크", value="opt_45"),
            discord.SelectOption(label="네오 브루탈리즘 (볼드 보더)", value="opt_46"),
            discord.SelectOption(label="레트로 터미널 / 해커 CLI", value="opt_47"),
            discord.SelectOption(label="딥 스페이스 오로라 (미래지향 글로우)", value="opt_48"),
        ],
        custom_id="select_design",
        row=3
    )
    async def select_design(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.state.design_mood = WIZARD_OPTIONS_MAP.get(select.values[0], select.values[0])
        self._sync_select_defaults()
        await interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)

    # --- Row 4: Controls & Launch ---
    @discord.ui.button(label="⚙️ 레퍼런스/품질/세부설정", style=discord.ButtonStyle.secondary, row=4)
    async def btn_advanced_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_modal_submit(modal_interaction: discord.Interaction):
            self._sync_select_defaults()
            await modal_interaction.response.edit_message(embed=self.build_dashboard_embed(), view=self)

        modal = AdvancedSettingsModal(self.state, on_complete=on_modal_submit)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📋 PRD 명세서 보기", style=discord.ButtonStyle.secondary, row=4)
    async def btn_preview_prd(self, interaction: discord.Interaction, button: discord.ui.Button):
        prd_text = self.state.generate_prd_content()
        meta = self.state.get_visual_meta()
        
        safe_idea = self.state.initial_idea
        if len(safe_idea) > 150:
            safe_idea = safe_idea[:147] + "..."
            
        preview_embed = discord.Embed(
            title=f"📋 [PRD.md 실시간 기획 명세서: {safe_idea}]",
            description=f"```markdown\n{prd_text[:3800]}\n```",
            color=meta.get("color", 0x3B82F6)
        )
        preview_embed.set_footer(text="스펙 확정 후 [🚀 개발 시작] 버튼을 누르면 3중 협업 코딩이 시작됩니다.")
        await interaction.response.send_message(embed=preview_embed, ephemeral=True)

    @discord.ui.button(label="🚀 개발 시작", style=discord.ButtonStyle.success, row=4)
    async def btn_start_dev(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session_manager.get_session(self.state.channel_id)
        
        # 0. Set mode to Pair
        session.set_mode("pair")

        # 1. Write Constitution and PRD.md to workspace matching selected platform
        if session.workspace and os.path.isdir(session.workspace):
            from setup_manager import create_project_constitution
            preset_key = self.state.get_preset_key()
            create_project_constitution(session.workspace, preset_key)
            
            prd_path = os.path.join(session.workspace, "PRD.md")
            try:
                with open(prd_path, "w", encoding="utf-8") as f:
                    f.write(self.state.generate_prd_content())
            except Exception as e:
                logger.debug(f"Failed to write PRD.md: {e}")

        # Disable all UI elements to show launch status
        for item in self.children:
            item.disabled = True

        meta = self.state.get_visual_meta()
        
        safe_idea = self.state.initial_idea
        if len(safe_idea) > 150:
            safe_idea = safe_idea[:147] + "..."
            
        is_game = self.state.is_game_domain()
        ref_text = self.state.benchmark_reference or ("뱀파이어 서바이벌 / 수박게임" if is_game else "표준 서비스")
        quality_text = self.state.quality_tier or ("Tier 2 60fps 물리/파티클" if is_game else "표준 UI/UX 최적화")

        launch_embed = discord.Embed(
            title=f"🚀 [자율 협업 개발 가동: {safe_idea}]",
            description=(
                f"**{meta.get('badge', '✨ Vibe Coding')}**\n\n"
                f"• 📱 **플랫폼**: `{self.state.platform}`\n"
                f"• 🎯 **카테고리/장르**: `{self.state.target_persona}`\n"
                f"• 🕹️ **벤치마크 레퍼런스**: `{ref_text}`\n"
                f"• 💎 **퀄리티 목표**: `{quality_text}`\n"
                f"• ⚡ **핵심 기능**: `{self.state.killer_feature}`\n"
                f"• 🎨 **디자인 테마**: `{self.state.design_mood}`\n"
                f"• 📋 **기획서**: 워크스페이스 디스크에 `PRD.md` 및 `PROJECT_CONSTITUTION.md` 동기화 완료!\n\n"
                f"👥 **Coder (초고속 개발) ➔ QA (시니어 검증) ➔ Auditor (전수 감사)** 3중 협업 파이프라인을 출격시킵니다."
            ),
            color=meta.get("color", 0x3B82F6)
        )
        if meta.get("thumb"):
            launch_embed.set_thumbnail(url=meta["thumb"])

        await interaction.response.edit_message(embed=launch_embed, view=self)

        # 2. Trigger Trio Pipeline Kickoff
        prompt = self.state.generate_full_prompt()
        asyncio.create_task(self._execute_trio_kickoff(interaction.channel, prompt, session))

    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, row=4)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        close_embed = discord.Embed(
            title="🛑 기획 스튜디오가 종료되었습니다.",
            description="언제든 `!plan <새로운 아이디어>` 명령어로 기획을 다시 시작할 수 있습니다. ✨",
            color=discord.Color.dark_gray()
        )
        await interaction.response.edit_message(embed=close_embed, view=None)

    async def _execute_trio_kickoff(self, channel: discord.abc.Messageable, prompt: str, session: Any):
        try:
            from config import PAIR_MAX_ROUNDS
            status_msg = await channel.send(
                f"🚀 👨‍💻 **[Coder 라운드 1 개발 중...]** `{self.state.platform}` 기반으로 비즈니스 로직과 UI 컴포넌트를 구축하고 있습니다..."
            )
            status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
            on_status_update = status_updater.get_callback()

            async with channel.typing():
                final_response = await TrioAgentRunner.run_pipeline(
                    session=session,
                    prompt=prompt,
                    main_channel=channel,
                    qa_client=self.qa_bot_client,
                    auditor_client=self.auditor_bot_client,
                    on_status_update=on_status_update,
                    max_rounds=PAIR_MAX_ROUNDS
                )

            await status_updater.delete()

            if TrioAgentRunner.is_system_error(final_response):
                await safe_send_discord_message(channel, final_response)


        except Exception as e:
            logger.exception(f"Trio kickoff failed: {e}")
            await channel.send(f"⚠️ 3중 에이전트 실행 중 오류가 발생했습니다: {e}")


async def handle_plan_command(
    ctx,
    initial_idea: str,
    session_manager: SessionManager,
    bot_client: discord.Client,
    qa_bot_client: discord.Client,
    auditor_bot_client: discord.Client
):
    async with ctx.channel.typing():
        state = PlanWizardState(ctx.channel.id, ctx.author.id, initial_idea)
        _WIZARD_SESSIONS[ctx.channel.id] = state

        view = PlanWizardView(
            state=state,
            session_manager=session_manager,
            bot_client=bot_client,
            qa_bot_client=qa_bot_client,
            auditor_bot_client=auditor_bot_client
        )

        embed = view.build_dashboard_embed()
        try:
            await ctx.channel.send(embed=embed, view=view)
        except discord.errors.DiscordServerError as e:
            logger.error(f"Discord API 500 Error on sending PlanWizardView: {e}")
            fallback_embed = discord.Embed(
                title="⚠️ 디스코드 API 일시적 오류 (500)",
                description=(
                    "디스코드 서버 측 문제로 인해 다중 선택 UI를 그리지 못했습니다.\n"
                    "대신 기본 설정(웹 & SaaS)으로 **즉시 개발을 시작**하려면 아래 버튼을 클릭하세요."
                ),
                color=0xFF0000
            )
            
            class FallbackView(discord.ui.View):
                def __init__(self, state, session_manager, bot, qa, auditor):
                    super().__init__(timeout=600)
                    self.state = state
                    self.session_manager = session_manager
                    self.qa_bot_client = qa
                    self.auditor_bot_client = auditor
                    self.bot_client = bot
                
                @discord.ui.button(label="🚀 기본 설정으로 강제 개발 시작", style=discord.ButtonStyle.success, row=0)
                async def btn_force_start(self, interaction: discord.Interaction, button: discord.ui.Button):
                    for item in self.children:
                        item.disabled = True
                    await interaction.response.edit_message(content="강제 개발을 시작합니다...", embed=None, view=self)
                    
                    session = self.session_manager.get_session(self.state.channel_id)
                    session.set_mode("pair")
                    
                    prompt = self.state.generate_full_prompt()
                    async def run():
                        try:
                            from config import PAIR_MAX_ROUNDS
                            from trio_agent_runner import TrioAgentRunner
                            status_msg = await interaction.channel.send("🚀 👨‍💻 **[Coder 라운드 1 개발 중...]**")
                            status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
                            on_status_update = status_updater.get_callback()

                            async with interaction.channel.typing():
                                final_response = await TrioAgentRunner.run_pipeline(
                                    session=session, prompt=prompt, main_channel=interaction.channel,
                                    qa_client=self.qa_bot_client, auditor_client=self.auditor_bot_client,
                                    on_status_update=on_status_update, max_rounds=PAIR_MAX_ROUNDS
                                )
                            await status_updater.delete()
                            
                            from discord_utils import safe_send_discord_message
                            if TrioAgentRunner.is_system_error(final_response):
                                await safe_send_discord_message(interaction.channel, final_response)
                        except Exception as ex:
                            await interaction.channel.send(f"⚠️ 오류: {ex}")
                    asyncio.create_task(run())

            f_view = FallbackView(state, session_manager, bot_client, qa_bot_client, auditor_bot_client)
            await ctx.channel.send(embed=fallback_embed, view=f_view)
