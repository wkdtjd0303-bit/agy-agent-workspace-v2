# 📋 Product Requirements Document (PRD) - AGY 3.0 Cross-Platform Autonomous E2E Engine

## 1. 개요 및 비전 (Overview)
* **프로젝트명**: AGY 3-Agent Collaborative Platform & Cross-Platform Autonomous E2E Engine
* **목표**: Coder(3.8 Flash / 3.1 Pro) ↔ QA(Gemini 3.1 Pro / 3.7 Flash) ↔ Auditor(Gemini 3.1 Pro) 3중 협업 자율 개발과 Web / Android / Windows 3대 크로스 플랫폼 무인 E2E 자율화 테스트, 자율 개발, 디자인 자율 고도화 파이프라인 통합 제공.
* **대상 사용자**: 1인 창업가, Vibe Coder, 자율 소프트웨어 에이전트 운영팀

---

## 2. 핵심 기능 요구사항 (Core Requirements)
* **REQ-001 (Cross-Platform E2E Autonomous Testing)**: Web(Playwright 헤드리스 브라우저), Android(ADB, UIAutomator, Pixel 8 Pro 뷰포트), Windows(Wine Xvfb 프레임버퍼 / KVM VM) 3대 플랫폼 대상 무인 E2E 자율 테스트 및 실시간 런타임 스크린샷·로그 수집.
* **REQ-002 (3-Agent Trio Pipeline)**: Coder의 초고속 개발, QA의 시니어 코드 리뷰, Auditor의 최종 수락 감사를 통한 무결점 자율 코드 생산 및 프로덕션 품질 유지.
* **REQ-003 (Self-Healing & Watchdog)**: 데몬 중단 감지 시 10초 내 자동 재기동(RestartSec=10) 및 상태 복구.
* **REQ-004 (Quality Gate Evidence & Anti-Mocking)**: Gate 0부터 Gate 7까지 전수 검증 가능한 물리적 아티팩트 디스크 저장 보장 (가짜 UI 목업/쇼케이스 웹페이지 생성 엄격 금지).
* **REQ-005 (14대 자율 탐색 & 브랜치 백트래킹)**: 14대 완전 독립 자율 탐색 파이프라인 탑재 및 하위 브랜치(탭/메뉴/설정) 계층 탐색, 스택 관리, 체계적 백트래킹(Backtrack `keyevent 4`) 및 뒤로가기 무한 루프 방어.
* **REQ-006 (Autonomous Design Evolution)**: 단순 껍데기가 아닌, 1:1 ROI 시각 크롭 분석 및 UIVisualDiffer를 통해 실제 대상 앱의 디자인(터치 타깃 48dp, 폰트/여백 가독성, 반응형 레이아웃)을 소스코드 레벨에서 자율적으로 개선.
* **REQ-007 (Universal Zero-Config Autonomous E2E & 200-Point Quality Gate)**: 사용자가 플랫폼을 명시하지 않아도 워크스페이스 프로젝트와 실행 환경을 자율 탐색(Web, Android, Windows Native, iOS)하여 적합한 E2E Adapter를 자동 구동하고, 13개 카테고리 200대 감사 체크리스트(>= 95점 임계치) 및 4-State Machine(PASS/FAIL/UNVERIFIED/BLOCKED)을 통해 무결점 자율 검증 완결.

---

## 3. 플랫폼 및 인터페이스 표준
* **운영 인터페이스**: Discord Bot 실시간 임베드 관제 & 헤드리스 E2E CLI 자동화
* **검증 플랫폼**: Web(Playwright 393~1280px), Android(ADB Real/Emulator), Windows Native(Wine/Xvfb/KVM), iOS(XCUITest/Simulator 및 호스트 불일치 시 BLOCKED 기계적 분리)
* **철회 사항**: 단순 보여주기식 정적 웹페이지(`dist/index.html` 등)는 전면 철회 및 폐기됨.
