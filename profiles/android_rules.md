# 📱 Profile: Android Native Application (android_rules)

> **적용 분야**: 100% Android Native (Kotlin, Jetpack Compose, Android SDK, Gradle)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Android Native (Kotlin + Jetpack Compose + Android Architecture Components)
* **🚫 엄격 금지 (Physical Block)**:
  - 웹 브라우저 래핑(WebView 기반 껍데기), PWA, Cordova/Capacitor 우회 구현 금지
  - 가짜 UI 드로잉(Mocking): Kotlin `Canvas(bitmap).drawRect` 또는 Python PIL로 가짜 스크린샷 조작 시 즉시 반려
* **UI/UX 네이티브 표준**:
  - **70% 뷰포트 확보 헌법 & 상단 크롬 과밀집 방어 (기존 65%에서 70% 이상으로 강화)**: 모바일 뷰어/에디터/캔버스 화면에서 상단 앱바, 알림바, 탭바, 툴바 및 하단 네비게이션 고정 바의 총 높이가 화면 전체의 30% 이하(기존 35% 한도 슬림화)로 엄격히 제한되어야 하며, 메인 콘텐츠 순수 작업 뷰포트를 최소 70% 이상(기본 65% 이상 상향) 무조건 확보해야 합니다. 상단 고정 바가 3단을 초과하거나 높이 합산이 150dp를 초과하는 과밀집을 엄격히 금지하며, 배너/가이드 텍스트 등 보조 툴바는 접기(Collapsible) 또는 스크롤 시 자동 숨김(Auto-Hide / NestedScrollConnection)을 기본 원칙으로 규정합니다.
  - **터치 타깃 규격**: 모든 터치 가능한 인터랙션 요소는 최소 48dp 터치 슬롭 확보 (`Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp)`)
  - **텍스트 말줄임 방어**: 상단 툴바 및 타이틀 텍스트 오버플로우 방어를 위해 `basicMarquee` 또는 적응형 축소 적용
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
