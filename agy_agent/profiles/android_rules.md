# 📱 Profile: Android Native Application (android_rules)

> **적용 분야**: 100% Android Native (Kotlin, Jetpack Compose, Android SDK, Gradle)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Android Native (Kotlin + Jetpack Compose + Android Architecture Components)
* **🚫 엄격 금지 (Physical Block)**:
  - 웹 브라우저 래핑(WebView 기반 껍데기), PWA, Cordova/Capacitor 우회 구현 금지
  - 가짜 UI 드로잉(Mocking): Kotlin `Canvas(bitmap).drawRect` 또는 Python PIL로 가짜 스크린샷 조작 시 즉시 반려
* **UI/UX 네이티브 표준**:
  - **뷰포트 및 상단 크롬 최적화 (캔버스/뷰어 앱 전용 권장, 일반 앱은 자율 레이아웃)**: 모바일 뷰어/에디터/캔버스 화면에 한하여 상단 앱바, 알림바, 탭바, 툴바 및 하단 네비게이션 고정 바의 총 높이를 화면 전체의 35% 이내로 슬림화하고 순수 작업 뷰포트 65%(최대 70%) 이상 확보 및 Auto-Hide를 권장합니다. 상단 고정 바가 3단을 초과하거나 높이 합산이 150dp를 초과하는 과밀집을 엄격히 금지하며, NestedScrollConnection으로 스크롤 연동을 적용합니다. 일반 모바일 앱은 서비스 성격에 맞게 유연하고 인체공학적인 레이아웃을 자율 구성하며, 고정된 뷰포트 수치 제약에 구속되지 않습니다.
  - **터치 사용성 규격**: 주요 액션 버튼(FAB, 메인 CTA, 주요 탭)은 원활한 터치 반응성을 위해 48dp 수준의 터치 영역 확보를 권장하며, 세밀한 정보 밀도가 필요한 인라인 칩/배지/캘린더 셀 등은 디자인 특성에 맞게 자율 조절합니다.
  - **텍스트 말줄임 방어**: 상단 툴바 및 타이틀 텍스트 오버플로우 방어를 위해 `basicMarquee` 또는 적응형 축소 적용
  - **Safe Area & Insets**: Status Bar, Navigation Bar, 카메라 노치 침범 0px
  - **다크/라이트 모드 지원** 및 시스템 폰트 스케일링(`sp`) 대응 (WCAG 2.1 AA 명도 대비 4.5:1)
  - **다중 인터랙션 상태(Multi-State)**: Idle, Selected, Modal 상태 물리적 렌더링 무결성 증명
  - **🎨 상용 서비스 크래프트맨십 & Anti-AI-Slop 표준 (AI 냄새 완전 박멸)**:
    - **Zero Prompt Bleed-through**: 지침서/PRD의 내부 개발 수치(`50px+`, `70px+`, `16ms`)나 아키텍처 명칭(`3-Way 자동 병합`, `온디바이스 평탄화 파이프라인`, `Room DB`)을 UI 텍스트/라벨로 노출하는 행위 엄격 금지 (토스/애플 수준의 세련된 한국어 UX 카피 강제)
    - **Zero PoC Clutter**: `단절 테스트`, `PoC 시연용 버튼` 등 기술 과시형 시뮬레이션 버튼을 일반 사용자 화면에 노출 금지 (백그라운드 자동화 또는 설정 > 개발자 옵션 하위로 격리)
    - **Zero Word Splitting (텍스트 세로 쪼개짐 방어)**: 좁은 화면에서 단어가 `단절 / 테스 / 트`처럼 세로로 쪼개지지 않도록 모든 버튼/탭/칩에 `maxLines = 1`, `softWrap = false`, `overflow = TextOverflow.Ellipsis`, 최소 너비(`minWidth`)와 패딩 보장
    - **적응형 상단 툴바 (3-Action Rule)**: 모바일 360dp 뷰포트 기준 상단바에는 핵심 액션 최대 3개만 노출하고, 4개 이상은 반드시 `DropdownMenu`(더보기 ⋮)로 수납하여 화면 밖으로 메뉴가 삐져나가는 참사 방어
    - **아이콘 대원칙**: 24~48dp 크기 버튼/탭에는 AI 생성 썸네일 축소 삽입 절대 금지, Android 공식 `Material Icons` 또는 벡터 드로어블만 사용
    - **현실적 도메인 데이터**: `김민수 수석연구원`, `1페이지 펜 필기 검토 중` 등 천편일률적 AI 클리셰 더미 데이터를 배제하고 실제 제품 도메인에 부합하는 자연스러운 데이터셋 구성

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: Robolectric JVM Headless OS 검증 + Compose `captureToImage()` + ADB 실기기/에뮬레이터 E2E
* **검증 대상**:
  - `./gradlew testDebugUnitTest` 로직 단위 테스트
  - Compose UI 렌더링 프레임버퍼 캡처 및 비전 결함 검사
  - 액티비티 생명주기 및 프로세스 재생성 시 상태 보존
* **최종 산출물**: `./gradlew assembleRelease` -> `app-release.apk` 및 AAB 번들, E2E 검증 리포트
