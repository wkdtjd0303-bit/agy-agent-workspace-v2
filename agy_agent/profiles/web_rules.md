# 🌐 Profile: Web & SaaS Fullstack (web_rules)

> **적용 분야**: Modern Web, SaaS, SPA/SSR (React, TypeScript, Vite, Next.js, TailwindCSS)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Modern Web Application (React/TypeScript + TailwindCSS + Vite/Next.js)
* **🚫 엄격 금지 (Physical Block)**:
  - 가짜 정적 이미지 목업을 브라우저 렌더링으로 위장하는 행위 금지
  - 결함 은폐용 `overflow-x: hidden` 또는 인라인 강제 클램핑 남발 금지
* **UI/UX 웹 표준**:
  - **캔버스 뷰포트 최적화 (에디터/뷰어 전용 권장, 일반 웹앱은 자율 레이아웃)**: 웹 에디터/뷰어 화면에 한하여 상단 헤더, 알림바, 툴바 및 하단 패널의 총 높이가 화면의 35%를 넘지 않도록 크롬 패딩을 최소화하고, 순수 작업 영역(Canvas/Content Viewport) 65% 이상 확보 및 Auto-Hide를 권장합니다. 일반 웹/SaaS 앱은 컴포넌트 특성에 맞게 자율적이고 반응성 높은 레이아웃을 구성합니다.
  - **터치 & 클릭 사용성**: 모바일 뷰포트에서 주요 인터랙션 버튼(CTA, 네비게이션)의 44px (또는 48dp) 터치 타깃 확보를 권장하며, 데이터 테이블/인라인 링크 등 정보 밀도가 필요한 요소는 UI 특성에 맞춰 자연스러운 크기를 적용합니다.
  - **가로 스크롤(Horizontal Overflow)** 버그 0px 보장
  - 시맨틱 HTML5, 로딩 스피너/스켈레톤, 에러 바운더리 완비

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: Playwright Headless/Headed E2E + Vitest/Jest 단위 테스트
* **검증 대상**:
  - 브라우저 콘솔 에러(Uncaught Exception) 0건
  - 4대 디바이스 뷰포트 실제 브라우저 렌더링 프레임버퍼 캡처
  - 폼 검증, 비동기 API 통신 및 데이터 렌더링 파이프라인
* **최종 산출물**: `npm run build` -> `dist/` 프로덕션 빌드 번들, Playwright E2E 리포트
