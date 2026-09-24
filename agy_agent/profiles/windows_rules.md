# 🪟 Profile: Windows Native Desktop Application (windows_rules)

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
