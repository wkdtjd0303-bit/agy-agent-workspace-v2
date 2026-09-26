import os
import discord
from typing import Tuple

def recommend_preset_for_channel(channel_name: str, workspace_path: str) -> Tuple[str, str, str]:
    """Analyzes channel name and workspace directory to recommend the best project preset."""
    ch_lower = channel_name.lower()
    
    # 0. Game (가장 구체적 → 우선 판정)
    if any(k in ch_lower for k in ["game", "게임", "arcade", "아케이드", "puzzle", "퍼즐", "roguelike", "로그라이크", "unity", "godot"]):
        return "game", "🎮 게임 (Web Canvas/WebGL · Unity · Godot · Pygame)", "실 입력→렌더 루프 검증 + 연속 프레임 캡처 + 60fps 프레임 예산 벤치마크"

    # 0-1. Windows Native Desktop
    if any(k in ch_lower for k in ["windows", "윈도우", "win", "wpf", "winui", "desktop", "데스크톱", "exe"]):
        return "windows", "🪟 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6)", "단위 테스트 + Windows VM 실 창 프레임버퍼 캡처 (고DPI/리사이즈/다크모드) 무결성 검증"

    # 1. Mobile / Android native
    if any(k in ch_lower for k in ["park", "rider", "android", "apk", "mobile", "app", "gps", "bluetooth"]):
        return "app", "📱 모바일 & 안드로이드 네이티브 앱", "Robolectric JVM OS 검증 + Gemini Vision 모바일 UI/UX (버튼 간격, 48dp 터치, 가독성, 심미성) 시각 검증, 가상 하드웨어(!sim), APK 자동 빌드 최적화"
    
    # 2. Commerce / Store / Landing
    if any(k in ch_lower for k in ["phonecase", "smartcard", "card", "store", "shop", "trade", "idus", "biz", "commerce", "landing"]):
        return "store", "🛍️ 커머스 / AI명함 / 스토어 랜딩", "구매/생성 여정 이탈 방지(CRO), 카피라이팅, SEO / SNS 공유 썸네일 진단"
    
    # 3. Planning / Research / Consult
    if any(k in ch_lower for k in ["question", "research", "consult", "plan", "기획", "자문", "리서치"]):
        return "read", "🔍 기획 / 시장 리서치 & 전략 자문", "100% 안전한 무수정 아키텍처 자문 및 심층 시장 조사"

    # 4. Check workspace files
    if workspace_path and os.path.isdir(workspace_path):
        if os.path.exists(os.path.join(workspace_path, "build.gradle")) or os.path.exists(os.path.join(workspace_path, "android")):
            return "app", "📱 모바일 & 안드로이드 네이티브 앱", "Robolectric JVM OS 검증 + Gemini Vision 모바일 UI/UX (버튼 간격, 48dp 터치, 가독성, 심미성) 시각 검증, 가상 하드웨어(!sim), APK 자동 빌드 최적화"
        if os.path.exists(os.path.join(workspace_path, "bot.py")) or os.path.exists(os.path.join(workspace_path, "agy_agent", "bot.py")):
            return "bot", "🤖 백그라운드 자동화 / 봇 / 크롤러", "비동기 데몬 헬스체크, 웹훅/알림 송수신 무결성 & 단위 테스트, 무중단 데몬 구동"
        if os.path.exists(os.path.join(workspace_path, "project.godot")) or os.path.exists(os.path.join(workspace_path, "Assets")):
            return "game", "🎮 게임 (Web Canvas/WebGL · Unity · Godot · Pygame)", "실 입력→렌더 루프 검증 + 연속 프레임 캡처 + 60fps 프레임 예산 벤치마크"
        try:
            if any(f.endswith(".sln") or f.endswith(".csproj") for f in os.listdir(workspace_path)):
                return "windows", "🪟 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6)", "단위 테스트 + Windows VM 실 창 프레임버퍼 캡처 (고DPI/리사이즈/다크모드) 무결성 검증"
        except Exception:
            pass

    # Default
    return "web", "🌐 웹 & SaaS 풀스택 개발", "Playwright 반응형 4대 기기 E2E 검증 및 라이브 URL 배포 자동 패치"

PROFILE_CLI_AGENT = """# ⚙️ Profile: CLI, Daemon & Automation Agent (cli_agent_rules)

> **적용 분야**: 100% Background Automation, CLI 도구, 데몬, 봇, 데이터 파이프라인, 파이썬/노드 자동화 스크립트

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Headless CLI / Daemon / Bot / Automation Tool (Python, Node.js, Shell)
* **🚫 엄격 금지 (Physical Block)**:
  - 단순 보여주기식 정적 웹 UI / HTML / CSS / 프론트엔드 목업 생성 엄격 금지
  - 주객전도 UI 작업 금지 (CLI/백엔드 프로젝트에 `.html`, `.css`, `frontend/` 디렉터리 생성 시 물리적 Gate 반려)
  - 불필요한 무거운 Electron/Web 브라우저 래핑 금지
* **I/O & 프로세스 표준**:
  - `argparse` / `click` / `typer` 표준 인자 파싱 및 `--help` 매뉴얼 완비
  - 명확한 표준 스트림 분리 (`stdout`: 유의미한 결과, `stderr`: 에러 및 경고)
  - 명확한 프로세스 종료 코드 준수 (`0`: 성공, `non-zero`: 실패)
  - 비동기 이벤트 루프 및 자식 프로세스 타임아웃, 예외 격리(try/finally), 시그널(`SIGINT`/`SIGTERM`) 정상 핸들링

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: `pytest` / `unittest` 단위 및 통합 테스트 전수 통과
* **검증 대상**:
  - 각 서브커맨드의 exit code, stdout/stderr 출력 포맷
  - 파일 I/O 및 데이터 영속화(SQLite/JSON/ENV) 무결성
  - 비동기 데몬 헬스체크 및 프로세스 지속성
* **최종 산출물**: 실행 가능한 CLI 바이너리/스크립트, 무중단 데몬 서비스, 100% 통과 단위 테스트 리포트
"""

PROFILE_ANDROID = """# 📱 Profile: Android Native Application (android_rules)

> **적용 분야**: 100% Android Native (Kotlin, Jetpack Compose, Android SDK, Gradle)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Android Native (Kotlin + Jetpack Compose + Android Architecture Components)
* **🚫 엄격 금지 (Physical Block)**:
  - 웹 브라우저 래핑(WebView 기반 껍데기), PWA, Cordova/Capacitor 우회 구현 금지
  - 가짜 UI 드로잉(Mocking): Kotlin `Canvas(bitmap).drawRect` 또는 Python PIL로 가짜 스크린샷 조작 시 즉시 반려
* **UI/UX 네이티브 표준**:
  - **터치 타깃 규격**: 모든 터치 가능한 인터랙션 요소는 최소 48dp 터치 타깃(Touch Target) 확보 (`Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp)` 또는 `IconButton`, 최소 48dp 보장)
  - **Safe Area & Insets**: Status Bar, Navigation Bar, 카메라 노치 침범 0px
  - **다크/라이트 모드 지원** 및 시스템 폰트 스케일링(`sp`) 대응 (WCAG 2.1 AA 명도 대비 4.5:1)
  - **다중 인터랙션 상태(Multi-State)**: Idle, Selected, Modal 상태 물리적 렌더링 무결성 증명

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: Robolectric JVM Headless OS 검증 + Compose `captureToImage()` + ADB 실기기/에뮬레이터 E2E
* **검증 대상**:
  - `./gradlew testDebugUnitTest` 로직 단위 테스트
  - Compose UI 렌더링 프레임버퍼 캡처 및 비전 결함 검사
  - 액티비티 생명주기 및 프로세스 재생성 시 상태 보존
* **최종 산출물**: `./gradlew assembleRelease` -> `app-release.apk` 및 AAB 번들, E2E 검증 리포트
"""

PROFILE_WEB = """# 🌐 Profile: Web & SaaS Fullstack (web_rules)

> **적용 분야**: Modern Web, SaaS, SPA/SSR (React, TypeScript, Vite, Next.js, TailwindCSS)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Modern Web Application (React/TypeScript + TailwindCSS + Vite/Next.js)
* **🚫 엄격 금지 (Physical Block)**:
  - 가짜 정적 이미지 목업을 브라우저 렌더링으로 위장하는 행위 금지
  - 결함 은폐용 `overflow-x: hidden` 또는 인라인 강제 클램핑 남발 금지
* **UI/UX 웹 표준**:
  - **반응형 4대 뷰포트**: 모바일(390px), 대화면 모바일(412px), 태블릿(768px), 데스크톱(1280px) 전 구간 레이아웃 정합성
  - **터치 & 클릭 타깃**: 모바일 뷰포트에서 최소 44px (또는 48dp) 터치 타깃(Touch Target) 보장
  - **가로 스크롤(Horizontal Overflow)** 버그 0px 보장
  - 시맨틱 HTML5, 로딩 스피너/스켈레톤, 에러 바운더리 완비

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: Playwright Headless/Headed E2E + Vitest/Jest 단위 테스트
* **검증 대상**:
  - 브라우저 콘솔 에러(Uncaught Exception) 0건
  - 4대 디바이스 뷰포트 실제 브라우저 렌더링 프레임버퍼 캡처
  - 폼 검증, 비동기 API 통신 및 데이터 렌더링 파이프라인
* **최종 산출물**: `npm run build` -> `dist/` 프로덕션 빌드 번들, Playwright E2E 리포트
"""

PROFILE_BACKEND_API = """# 🚀 Profile: Backend API & Microservices (backend_api_rules)

> **적용 분야**: 100% High-Performance Backend API, Microservices (FastAPI, NestJS, Go, Express)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Headless REST / GraphQL / gRPC Backend API (FastAPI, NestJS, Node.js, Go)
* **🚫 엄격 금지 (Physical Block)**:
  - 단순 정적 프론트엔드 HTML/CSS 목업 생성 엄격 금지 (API 전용 프로젝트에 불필요한 UI 파일 생성 금지)
  - 입력값 검증 없는 날것의 요청 처리 금지 (Pydantic / DTO 필수)
  - 동기 블로킹 I/O로 인한 이벤트 루프 정체 방치 금지
* **API & 데이터베이스 표준**:
  - OpenAPI 3.0 / Swagger 문서와 코드 100% 동기화
  - 표준 HTTP 상태 코드 준수 (200, 201, 400, 401, 403, 404, 422, 500) 및 표준 에러 응답 포맷
  - 비동기 DB 커넥션 풀링 관리 및 커넥션 누수 0건

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: `pytest` + `httpx` (Python) / `Jest` + `supertest` (Node.js)
* **검증 대상**:
  - 모든 엔드포인트의 입력 유효성 검사 및 엣지 케이스 테스트
  - DB 마이그레이션(Alembic, Prisma 등) 적용 및 롤백 무결성
  - 동시 요청 부하 시의 안정성 및 응답 시간
* **최종 산출물**: 실행 가능한 서버 스크립트, DB 마이그레이션 파일, Dockerfile, API 테스트 전수 통과 리포트
"""

PROFILE_WINDOWS = """# 🪟 Profile: Windows Native Desktop Application (windows_rules)

> **적용 분야**: 100% Windows Native Desktop (C# WPF / WinUI 3 / .NET, Python PyQt6 데스크톱 유틸리티)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Windows Native Desktop App (WPF/WinUI 3 + .NET, 또는 PyQt6/PySide6)
* **🚫 엄격 금지 (Physical Block)**:
  - 웹 브라우저 래핑(WebView2 기반 껍데기), Electron 껍데기로 네이티브 위장 금지
  - 가짜 UI 드로잉(Mocking): PIL/GDI+ 캔버스로 가짜 창 스크린샷 합성 시 즉시 반려
  - 콘솔 창(`Console`)만 띄우고 GUI 미구현 상태를 네이티브 앱으로 보고하는 행위 금지
* **UI/UX 네이티브 표준 (Fluent Design)**:
  - **타이틀바 & 리사이즈**: 표준 캡션 버튼(최소화/최대화/닫기) 및 자유 리사이즈 정상 동작, DWM Mica/Acrylic 배경 적용
  - **고DPI 대응**: 100~200% 배율에서 레이아웃 깨짐 0px (per-monitor DPI awareness v2)
  - **키보드 중심 UX**: 주요 액션 단축키(`Ctrl+N/S/Z` 등) 및 Tab 포커스 순환, `AccessKey` 제공
  - **스냅 레이아웃/멀티윈도우**: 최소 창 크기 보장 및 스냅 시 컨텐츠 오버플로 0px
  - **테마**: 라이트/다크 모드 대응 (WCAG 2.1 AA 명도 대비 4.5:1)

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: `xUnit`/`NUnit`(.NET) 또는 `pytest-qt`(PyQt6) 단위 테스트 + Windows VM 실 프레임버퍼 캡처
* **검증 대상**:
  - 핵심 로직 단위 테스트 및 뷰모델 상태 전이 무결성
  - 실제 창 렌더링 프레임버퍼 캡처 (고DPI/리사이즈/다크모드 다중 상태)
  - 프로세스 재시작 후 설정/상태 영속화 무결성
* **최종 산출물**: `dotnet publish`/`pyinstaller` -> 실행 가능한 `.exe` 및 설치 패키지(MSIX/Inno Setup), E2E 검증 리포트
"""

PROFILE_GAME = """# 🎮 Profile: Game Application (game_rules)

> **적용 분야**: 100% 게임 (Web Canvas/WebGL/Three.js, Unity, Godot, Pygame — 캐주얼/아케이드/로그라이크/퍼즐)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: 실제 플레이 가능한 게임 런타임 (Web: Canvas/WebGL/Three.js, Native: Unity/Godot/Pygame)
* **🚫 엄격 금지 (Physical Block)**:
  - 정적 이미지/목업을 게임 화면으로 위장하는 행위 금지 (실제 입력→렌더 루프 필수)
  - 클릭조차 동작하지 않는 껍데기 스크린(Stub) 금지
  - 가짜 FPS/스코어 리포트 작성 금지 (실 프레임 타이밍 계측만 인정)
* **게임 UX/렌더링 표준**:
  - **코어 루프**: 시작 → 플레이 → 스코어/아이템 → 난이도 상승 → 게임오버 → 즉시 재도전 1사이클 완결
  - **60fps 프레임 예산**: 입력 지연(latency) 최소화, 프레임 드랍 구간 0건 (16.6ms 예산)
  - **HUD 안전영역**: 화면 엣지 5% 이내 핵심 HUD 배치, 거리 가독성(큰 폰트/고대비) 확보
  - **색맹 친화**: 색상 단독 의존 금지 (형태/아이콘 병행), 일시정지/설정 UI 완비
  - **입력 프롬프트**: 키보드(WASD/화살표)/터치(가상 조이스틱)/마우스 입력 안내 표시

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: Playwright(Web 게임) 또는 엔진별 자동화 + 실 프레임버퍼 캡처 + FPS 벤치마크
* **검증 대상**:
  - 실제 입력 시퀀스 → 상태 변화(스코어/체력/레벨) 물리적 검증
  - 연속 프레임 캡처로 애니메이션/파티클 실제 렌더링 무결성 증명
  - 평균/최소 FPS 및 프레임 타임 분포 리포트
* **최종 산출물**: 실행 가능한 게임 빌드(웹 번들 또는 네이티브 바이너리), FPS 벤치마크 리포트
"""

CORE_CONSTITUTION_CONTENT = """# 🏛️ PROJECT CONSTITUTION (프로젝트 개발 헌법)

> 본 프로젝트는 **AGY 자율 제품 생산 공장 체계**를 따르며, 모든 에이전트(Coder, QA Reviewer, Auditor)는 아래 5대 시스템 불변원칙을 강제 준수합니다.
> 상세 플랫폼 기술 규칙은 바인딩된 `profiles/active_profile.md`를 단독 로드하여 적용합니다.

---

## 1. ⚙️ 기계적 증거 최우선 원칙 (Mechanical Evidence > Semantic Claims)
* **Evidence 필수성**: "PASS"는 AI의 주관적 주장이 아니라 디스크 상의 검증 가능한 물리적 산출물(Exit code 0, 단위/E2E 테스트 성공 로그, 실 프레임버퍼)로만 인정됩니다.
* **Anti-Mocking & 위조 금지 (Anti-Tracing Law 포함)**: 
  - 프로덕션 코드 수정 없는 테스트 전용 패치(Test-only patch) 금지
  - 임의의 Canvas/PIL 가짜 이미지 합성, 레퍼런스 이미지 외곽선 모사/표절(Anti-Tracing Law), 또는 가짜 리포트(`{"status": "PASSED"}`) 작성 적발 시 즉시 Gate 반려

## 2. 🎯 도메인 프로파일 격리 원칙 (Domain Profile Binding)
* 워크스페이스는 단 1개의 활성 도메인 프로파일(`profiles/active_profile.md`)에 바인딩됩니다.
* 해당 프로파일의 가드레일(CLI/데몬 시 웹 UI 금지, 네이티브 앱 시 웹 래핑 금지 등)은 `evidence_validator.py`를 통해 물리적 코드로 강제 검증됩니다.

## 3. 🛡️ 7대 Quality Gate 체계
* **Gate 0 (초기화 & 헌법)**: 워크스페이스 및 프로파일 무결성 확인
* **Gate 1 (기획)**: `PRD.md` 명세 (REQ-001~)
* **Gate 2 (설계)**: `ARCHITECTURE.md` 또는 `DESIGN_SPEC.md`
* **Gate 3 (조립)**: 프로덕션 소스코드 물리적 구현 (도메인 금지 확장자 차단)
* **Gate 4 (기능 검증)**: `reports/gate4/e2e_test.json` 실제 테스트 통과
* **Gate 5 (시각/상태 검증)**: 실기기/브라우저 프레임버퍼 또는 CLI/데몬 상태 무결성
* **Gate 6 (회귀 방지)**: `reports/gate6/regression_test.json` (기존 REQ 회귀 잠금)
* **Gate 7 (릴리즈)**: 최종 산출물 해시 및 결함 0건 검증

## 4. 🔄 다단계 자율 복구 & 롤백 (Self-Healing & Rollback)
* 게이트 검증 실패 또는 과잉 수정 적발 시 마지막 정상 체크포인트로 즉시 롤백(`git checkout -- .`)합니다.
* 단계별 자율 복구(`Attempt 1`: 인라인 패치 ➔ `Attempt 2`: 구조 변경 ➔ `Attempt 3`: 대체 구현 ➔ `Attempt 4`: 롤백 후 재설계)를 수행합니다.

## 5. 💬 사용자 질문 최우선 답변 (User Question Direct Answer First)
* 사용자의 질의/진단/평가 요청 시, 내부 에이전트 로그나 회의록보다 **사용자 질문에 대한 명확하고 전문적인 답변을 최상단 1순위로 제공**합니다 (User Question Direct Answer First).

## 6. 🎨 시각 완성도 계약 (Visual Craft Contract · GUI 프로젝트 필수)
* **작업 도구 우선 (Tool-first)**: 앱/웹의 첫 화면은 마케팅 랜딩페이지가 아니라 작업 공간입니다. 상단 히어로/홍보 배너를 1차 콘텐츠 위에 배치하지 않습니다.
* **수치 규격화**: 여백은 4/8pt 그리드, 타이포는 3단계(Display/Body/Caption), 색상은 Primary 1종 + Neutral 계조로 제한합니다.
* **인체공학**: 주요 액션은 엄지 도달 영역(화면 하단 1/3·하단바·FAB)에 두고 최소 터치 타깃을 확보합니다.
* **중첩 금지**: 컨테이너(카드/박스) 3중 이상 중첩을 금지하고, 여백·구분선·타이포 위계로 구획합니다.
* **자동 검증**: `reports/gate2/design_contract.json` + `reports/gate5/visual_craft_review.json`(verdict=PASS)이 Gate 5 통과 조건입니다. (무인 자율 `!mode 3/3+` 강제)

## 7. 🎯 QC 수렴 & 정직성 계약 (Convergence & Honesty Contract · !mode 3/3+ 필수)
* **차단/비차단 분리**: Gate 반려(REJECTED)는 사용자 체감 차단 결함(빌드 실패, 런타임 크래시, 핵심 기능 미동작, 사용자 요청 미구현, Mock/Stub/표절)에만 한정합니다. 코드 스타일·문서/증거 파일 포맷·해시·타임스탬프 신선도 등 메타 인프라는 차기 백로그로만 기록하며 반려 사유가 될 수 없습니다.
* **정직성 게이트(상시 엄격)**: 위 비차단 면제는 거짓 주장에는 적용되지 않습니다. 실행하지 않은 테스트의 통과 기재, 상태를 표현하지 않는 캡처의 근거 제출, 재현 불가 수치의 재현 주장은 즉시 반려 대상입니다. 계측이 불가하면 `BLOCKED`로 정직하게 기록하고 `PASS` 위장을 금지합니다.
* **진척 기반 수렴**: QA는 반려 시 `[BLOCKING_DEFECTS: N]`을 표기하고, 시스템은 차단 결함 수가 2회 연속 감소하지 않으면 라운드 상한 전에 조기 에스컬레이션합니다. 잔존 항목이 비차단뿐이면 백로그로 이관하고 승인으로 수렴합니다.
"""


def create_project_constitution(workspace_path: str, preset_key: str = "web") -> None:
    """Generates slim core PROJECT_CONSTITUTION.md, domain profiles, and reports directory tree."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return

    try:
        # Ensure reports directory structure exists
        for sub in ["gate0", "gate1", "gate2", "gate3", "gate4", "gate5/screenshots", "gate6", "gate7", "deferred"]:
            os.makedirs(os.path.join(workspace_path, "reports", sub), exist_ok=True)

        # Ensure profiles directory structure exists
        profiles_dir = os.path.join(workspace_path, "profiles")
        os.makedirs(profiles_dir, exist_ok=True)

        # Populate domain profile templates
        profile_files = {
            "cli_agent_rules.md": PROFILE_CLI_AGENT,
            "android_rules.md": PROFILE_ANDROID,
            "web_rules.md": PROFILE_WEB,
            "backend_api_rules.md": PROFILE_BACKEND_API,
            "windows_rules.md": PROFILE_WINDOWS,
            "game_rules.md": PROFILE_GAME,
        }
        for fname, content in profile_files.items():
            fpath = os.path.join(profiles_dir, fname)
            if not os.path.exists(fpath):
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)

        # Match preset to active profile
        key = preset_key.lower().strip()
        if key in ("app", "앱", "mobile", "android", "모바일", "안드로이드", "2"):
            active_content = PROFILE_ANDROID
        elif key in ("game", "게임", "mobilegame", "retrogame", "unity", "godot", "unreal",
                     "3d", "webgl", "canvas", "arcade", "아케이드", "퍼즐"):
            active_content = PROFILE_GAME
        elif key in ("win", "windows", "윈도우", "wpf", "winui", "winforms", "pyqt", "pyside",
                     "데스크톱", "desktop app"):
            active_content = PROFILE_WINDOWS
        elif key in ("web", "웹", "saas", "frontend", "풀스택", "1", "store", "스토어", "biz",
                     "commerce", "커머스", "landing", "랜딩", "3", "extension", "electron", "tauri", "desktop"):
            active_content = PROFILE_WEB
        elif key in ("api", "server", "backend", "백엔드", "서버", "msa", "fastapi", "nest"):
            active_content = PROFILE_BACKEND_API
        elif key in ("bot", "봇", "crawler", "크롤러", "automation", "자동화", "scraping", "daemon", "cli", "terminal", "터미널", "tool", "도구"):
            active_content = PROFILE_CLI_AGENT
        else:
            active_content = PROFILE_CLI_AGENT

        # Write active profile
        active_profile_path = os.path.join(profiles_dir, "active_profile.md")
        with open(active_profile_path, "w", encoding="utf-8") as f:
            f.write(active_content)

        # Write slim core constitution
        constitution_path = os.path.join(workspace_path, "PROJECT_CONSTITUTION.md")
        with open(constitution_path, "w", encoding="utf-8") as f:
            f.write(CORE_CONSTITUTION_CONTENT)
    except Exception as e:
        logger.warning(f"Failed to create project constitution: {e}")

def apply_project_preset(channel_id: int, preset_key: str, session_manager, command_prefix: str = "!") -> discord.Embed:
    session = session_manager.get_session(channel_id)

    # Auto-bootstrap Project Constitution in workspace
    if session.workspace:
        create_project_constitution(session.workspace, preset_key)

    key = preset_key.lower().strip()
    if key in ("web", "웹", "saas", "frontend", "풀스택", "1"):
        session.set_mode("auto")
        
        embed = discord.Embed(
            title="🌐 [웹 / SaaS 풀스택 프로젝트 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `웹 & SaaS 풀스택 개발 모드`\n\n"
                f"• 🤖 **채널 동작 모드**: `Auto` (질의는 Single, 기능개발은 3중 Pair 자동 선택)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 2: 자율 패치)` - 10분 유휴 시 기동\n"
                f"• 🧪 **QA 엔진**: `Playwright` 4대 디바이스 반응형 & 6-TC 인터랙션 검증\n"
                f"• 🚀 **배포 연동**: Firebase/Vercel 라이브 URL 자동 추출 및 E2E 실시간 순환\n\n"
                f"💡 *팁: 언제든 `{command_prefix}setup`을 입력해 다른 프리셋으로 즉시 전환할 수 있습니다.*"
            ),
            color=discord.Color.blue()
        )
    elif key in ("app", "앱", "mobile", "android", "모바일", "안드로이드", "2"):
        session.set_mode("pair")
        
        embed = discord.Embed(
            title="📱 [모바일 / 안드로이드 네이티브 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `모바일 & 안드로이드 네이티브 앱 모드`\n\n"
                f"• 👥 **채널 동작 모드**: `Pair` (Coder ↔ QA ↔ Auditor 상시 3중 협업)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 3: 자율 발전)` - 10분 유휴 시 기동\n"
                f"• ⚡ **QA 엔진**: `Robolectric JVM` OS 로직 + `Gemini Vision` UI/UX (버튼 간격/48dp/디자인) 전수 검증\n"
                f"• 📱 **가상 스튜디오**: `{command_prefix}sim` 60fps 인터랙티브 하드웨어(GPS/블루투스) 시뮬레이션 지원\n"
                f"• 📦 **바이너리**: 워크스페이스 내 APK 자동 빌드 및 `{command_prefix}apk` 즉시 다운로드"
            ),
            color=discord.Color.green()
        )
    elif key in ("store", "스토어", "biz", "commerce", "커머스", "명함", "폰케이스", "landing", "랜딩", "3"):
        session.set_mode("auto")
        
        embed = discord.Embed(
            title="🛍️ [커머스 / AI명함 / 스토어 랜딩 프리셋 적용 완료]",
            description=(
                "**선택된 프리셋:** `비즈니스 전환율(CRO) & 성장 관찰 모드`\n\n"
                "• 🤖 **채널 동작 모드**: `Auto` (안전 지향 자율 운영)\n"
                "• 👑 **오토파일럿**: `ON (Phase 1: 무위험 시각 관찰 & PO 감사)` - 10분 유휴 시 기동\n"
                "• 🔍 **성과/품질 진단**: 구매/생성 여정 이탈 요인, 카피라이팅, 신뢰도(Social Proof) 평가\n"
                "• 🌐 **마케팅 최적화**: 모바일 터치 최적화 & SNS 공유 썸네일(OG Tag) 상시 모니터링"
            ),
            color=discord.Color.gold()
        )
    elif key in ("read", "기획", "자문", "research", "리서치", "consult", "readonly", "4"):
        session.set_mode("read")
        
        embed = discord.Embed(
            title="🔍 [기획 / 시장 리서치 & 전략 자문 프리셋 적용 완료]",
            description=(
                "**선택된 프리셋:** `3중 자문 & 리서치 전용 아키텍처 모드`\n\n"
                "• 🔍 **채널 동작 모드**: `Read (지능형 라우팅)` (질문 의도에 따라 아래 두 엔진 중 자동 선택)\n"
                "• 🧠 **엔진 1 (코드 자문)**: `ConsultingRunner` (코드 수정 없이 기존 코드베이스 아키텍처 심층 분석)\n"
                "• 📑 **엔진 2 (시장 조사)**: `ResearchRunner` (웹 검색 기반 경쟁사 벤치마킹, 스펙 비교 리포트 작성)\n"
                "• 🛑 **오토파일럿**: `OFF` (100% 안전한 질의응답 전용 보장)"
            ),
            color=discord.Color.purple()
        )
    elif key in ("game", "게임", "mobilegame", "retrogame", "unity", "godot", "unreal",
                 "3d", "webgl", "canvas", "arcade", "아케이드", "퍼즐", "5"):
        session.set_mode("pair")

        embed = discord.Embed(
            title="🎮 [게임 프로젝트 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `게임 (Web Canvas/WebGL · Unity · Godot · Pygame) 모드`\n\n"
                f"• 👥 **채널 동작 모드**: `Pair` (Coder ↔ QA ↔ Auditor 3중 협업)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 3: 자율 발전)` - 10분 유휴 시 기동\n"
                f"• 🧪 **QA 엔진**: 실 입력→렌더 루프 검증 + 연속 프레임 캡처 + 60fps 프레임 예산 벤치마크\n"
                f"• 🕹️ **코어 루프**: 시작→플레이→스코어→난이도 상승→게임오버→즉시 재도전 완결 강제\n"
                f"• 🎯 **HUD 표준**: 엣지 5% 안전영역 · 거리 가독성 · 색맹 친화(형태+색) · 일시정지/설정 UI"
            ),
            color=discord.Color.magenta()
        )
    elif key in ("win", "windows", "윈도우", "wpf", "winui", "winforms", "pyqt", "pyside", "데스크톱", "6"):
        session.set_mode("pair")

        embed = discord.Embed(
            title="🪟 [윈도우 네이티브 데스크톱 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6) 모드`\n\n"
                f"• 👥 **채널 동작 모드**: `Pair` (Coder ↔ QA ↔ Auditor 3중 협업)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 3: 자율 발전)` - 10분 유휴 시 기동\n"
                f"• 🧪 **QA 엔진**: 단위 테스트 + Windows VM 실 창 프레임버퍼 캡처 (고DPI/리사이즈/다크모드)\n"
                f"• 🪟 **Fluent Design**: 타이틀바/리사이즈 · 키보드 단축키 · 스냅 레이아웃 · 고DPI 200% 무결성\n"
                f"• 📦 **바이너리**: `dotnet publish`/`pyinstaller` → `.exe` 및 설치 패키지"
            ),
            color=discord.Color.dark_teal()
        )
    else:
        session.set_mode("auto")
        
        embed = discord.Embed(
            title="⚙️ [기본 표준 프리셋 적용 완료]",
            description="채널이 기본 표준 모드(`Auto 모드`)로 설정되었습니다.",
            color=discord.Color.dark_grey()
        )

    embed.set_footer(text=f"AGY Project Initializer · 언제든 {command_prefix}setup 명령어로 다시 변경할 수 있습니다.")
    return embed

class ProjectSetupView(discord.ui.View):
    def __init__(self, channel_id: int, user_id: int, session_manager, command_prefix: str = "!"):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.user_id = user_id
        self.session_manager = session_manager
        self.command_prefix = command_prefix

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("⚠️ 이 설정 카드를 실행한 사용자만 버튼을 누를 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🌐 웹 / SaaS 풀스택", style=discord.ButtonStyle.primary, emoji="🌐", custom_id="btn_preset_web", row=0)
    async def btn_web(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "web", self.session_manager, self.command_prefix)
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="📱 모바일 / 안드로이드 앱", style=discord.ButtonStyle.success, emoji="📱", custom_id="btn_preset_app", row=0)
    async def btn_app(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "app", self.session_manager, self.command_prefix)
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🛍️ 커머스 / AI명함 / 스토어", style=discord.ButtonStyle.secondary, emoji="🛍️", custom_id="btn_preset_store", row=0)
    async def btn_store(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "store", self.session_manager, self.command_prefix)
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🔍 기획 / 시장 리서치", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="btn_preset_read", row=1)
    async def btn_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "read", self.session_manager, self.command_prefix)
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🎮 게임 (WebGL/Unity/Godot)", style=discord.ButtonStyle.danger, emoji="🎮", custom_id="btn_preset_game", row=1)
    async def btn_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "game", self.session_manager, self.command_prefix)
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🪟 윈도우 네이티브 데스크톱", style=discord.ButtonStyle.primary, emoji="🪟", custom_id="btn_preset_windows", row=1)
    async def btn_windows(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "windows", self.session_manager, self.command_prefix)
        await interaction.response.edit_message(content="", embed=embed, view=None)

async def handle_setup_command(message: discord.Message, preset_key: str, session_manager, command_prefix: str = "!"):
    session = session_manager.get_session(message.channel.id)
    ch_name = getattr(message.channel, "name", f"channel_{message.channel.id}")

    if preset_key:
        embed = apply_project_preset(message.channel.id, preset_key, session_manager, command_prefix)
        await message.channel.send(embed=embed)
        return

    rec_key, rec_label, rec_desc = recommend_preset_for_channel(ch_name, session.workspace)

    embed = discord.Embed(
        title="🚀 [AGY 프로젝트 맞춤형 원클릭 셋업 (Project Initializer)]",
        description=(
            f"현재 채널(**`#{ch_name}`**)의 워크스페이스와 성격에 맞춰 **최적의 동작 모드, 오토파일럿 주기, QA 검증 엔진**을 1클릭으로 구성합니다.\n\n"
            f"🎯 **AI 스마트 추천:**\n"
            f"> **{rec_label}**\n"
            f"> *{rec_desc}*\n\n"
            f"아래 버튼 중 프로젝트 성격에 맞는 카테고리를 선택해주세요:"
        ),
        color=discord.Color.gold()
    )
    embed.add_field(
        name="1. 🌐 웹 / SaaS 풀스택",
        value="• 모드: `Auto` | 오토파일럿: `Phase 2 자율 패치`\n• QA: `Playwright 4대 기기 반응형 & 6-TC`\n• 용도: 웹앱, 대시보드, 풀스택 서비스",
        inline=False
    )
    embed.add_field(
        name="2. 📱 모바일 / 안드로이드 앱",
        value="• 모드: `Pair 3중 협업` | 오토파일럿: `Phase 3 자율 발전`\n• QA: `Robolectric JVM OS 가상화 + !sim + APK`\n• 용도: 안드로이드 네이티브 앱, 백그라운드 서비스",
        inline=False
    )
    embed.add_field(
        name="3. 🛍️ 커머스 / AI명함 / 스토어 랜딩",
        value="• 모드: `Auto` | 오토파일럿: `Phase 1 무위험 시각 관찰`\n• QA: `전환율(CRO) 퍼널 + 카피라이팅 + SEO 썸네일`\n• 용도: AI 스마트 명함, 아이디어스 폰케이스, 랜딩페이지",
        inline=False
    )
    embed.add_field(
        name="4. 🔍 기획 / 시장 리서치 & 자문",
        value="• 모드: `Read (읽기 전용)` | 오토파일럿: `OFF`\n• QA: `3중 아키텍트 교차 분석 & 논문/시장 리서치`\n• 용도: 기획서 작성, 방향성 자문, 시장 분석",
        inline=False
    )
    embed.add_field(
        name="5. 🎮 게임 (Web Canvas/WebGL · Unity · Godot · Pygame)",
        value="• 모드: `Pair 3중 협업` | 오토파일럿: `Phase 3 자율 발전`\n• QA: `실 입력→렌더 루프 + 연속 프레임 캡처 + 60fps 벤치마크`\n• 용도: 캐주얼/아케이드/로그라이크/퍼즐 게임",
        inline=False
    )
    embed.add_field(
        name="6. 🪟 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6)",
        value="• 모드: `Pair 3중 협업` | 오토파일럿: `Phase 3 자율 발전`\n• QA: `단위 테스트 + Windows VM 실 창 프레임버퍼 (고DPI/다크모드)`\n• 용도: 윈도우 데스크톱 유틸리티, 생산성 앱",
        inline=False
    )
    embed.set_footer(text=f"명령어로 직접 설정: {command_prefix}setup web | app | store | read | game | windows")

    view = ProjectSetupView(channel_id=message.channel.id, user_id=message.author.id, session_manager=session_manager, command_prefix=command_prefix)
    await message.channel.send(embed=embed, view=view)
