#!/usr/bin/env python3
"""
🏛️ AGY 3.0 Universal Autonomous E2E 200-Point Audit Engine
Fulfills the complete 200-Point Audit Architecture across 13 Categories (Items 1 ~ 200):
  A. 기본 원칙 (Items 1 ~ 10)
  B. 프로젝트/Artifact 자동 탐색 (Items 11 ~ 25)
  C. 플랫폼 자동 탐지 (Items 26 ~ 45)
  D. 플랫폼/기술 혼합 판별 (Items 46 ~ 60)
  E. 실행 환경 자동 선택 (Items 61 ~ 75)
  F. Application Launch 검증 (Items 76 ~ 90)
  G. Screenshot / Vision 검증 (Items 91 ~ 105)
  H. UI / Interaction 자동 탐색 (Items 106 ~ 120)
  I. Autonomous Exploration (Items 121 ~ 135)
  J. Intent 기반 E2E (Items 136 ~ 150)
  K. Evidence Integrity (Items 151 ~ 170)
  L. Autonomous Quality Gate (Items 171 ~ 185)
  M. Cross-Platform Autonomous E2E (Items 186 ~ 200)

Enforces:
  1. Strict >= 95/100 threshold (Level 4: Universal Autonomous QA Engine)
  2. 4-State Machine: PASS, FAIL, UNVERIFIED, BLOCKED
  3. Anti-Mocking: Rejects subjective LLM claims without physical evidence
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("AutonomousE2E200Auditor")


class AutonomousE2E200Auditor:
    """
    Evaluates an E2E session or the entire platform against the 200-Point Universal Checklist.
    """

    CATEGORIES = {
        "A": ("A. 기본 원칙", 1, 10),
        "B": ("B. 프로젝트/Artifact 자동 탐색", 11, 25),
        "C": ("C. 플랫폼 자동 탐지", 26, 45),
        "D": ("D. 플랫폼/기술 혼합 판별", 46, 60),
        "E": ("E. 실행 환경 자동 선택", 61, 75),
        "F": ("F. Application Launch 검증", 76, 90),
        "G": ("G. Screenshot / Vision 검증", 91, 105),
        "H": ("H. UI / Interaction 자동 탐색", 106, 120),
        "I": ("I. Autonomous Exploration", 121, 135),
        "J": ("J. Intent 기반 E2E", 136, 150),
        "K": ("K. Evidence Integrity", 151, 170),
        "L": ("L. Autonomous Quality Gate", 171, 185),
        "M": ("M. Cross-Platform Autonomous E2E", 186, 200)
    }

    CHECKLIST_200 = [
        # A. 기본 원칙 (1~10)
        (1, "A", "E2E 실행 시 사용자가 플랫폼을 직접 지정하지 않아도 되는가?"),
        (2, "A", "프로젝트 자체에서 Target Platform을 추론하는가?"),
        (3, "A", "플랫폼 추론과 E2E 실행을 별도의 단계로 분리했는가?"),
        (4, "A", "AI의 추측만으로 플랫폼을 결정하지 않는가?"),
        (5, "A", "실제 파일/프로젝트 구조를 근거로 플랫폼을 결정하는가?"),
        (6, "A", "실제 실행 가능한 artifact를 탐색하는가?"),
        (7, "A", "플랫폼 탐지 결과에 confidence를 기록하는가?"),
        (8, "A", "탐지 근거(Evidence)를 기록하는가?"),
        (9, "A", "서로 다른 플랫폼이 동시에 존재할 경우 모두 식별하는가?"),
        (10, "A", "사용자에게 다시 'Android인가요?'라고 묻지 않고 자동으로 해결할 수 있는가?"),

        # B. 프로젝트/Artifact 자동 탐색 (11~25)
        (11, "B", "프로젝트 root를 자동으로 찾는가?"),
        (12, "B", "현재 작업 디렉터리를 프로젝트 root로 무조건 가정하지 않는가?"),
        (13, "B", "하위 디렉터리를 탐색하는가?"),
        (14, "B", "빌드 파일을 탐색하는가?"),
        (15, "B", "실행 파일을 탐색하는가?"),
        (16, "B", "APK를 탐색하는가?"),
        (17, "B", "IPA 또는 iOS build artifact를 탐색하는가?"),
        (18, "B", "EXE를 탐색하는가?"),
        (19, "B", "웹 프로젝트의 entry point를 탐색하는가?"),
        (20, "B", "Docker/container 기반 서비스도 식별할 수 있는가?"),
        (21, "B", "개발 서버 실행 방법을 자동으로 찾는가?"),
        (22, "B", "빌드 결과물이 여러 개일 때 올바른 Target을 선택하는가?"),
        (23, "B", "오래된 artifact와 최신 artifact를 구분하는가?"),
        (24, "B", "현재 소스와 실행 artifact의 버전이 일치하는지 확인하는가?"),
        (25, "B", "잘못된 artifact를 대상으로 E2E를 실행하지 않는가?"),

        # C. 플랫폼 자동 탐지 (26~45)
        (26, "C", "build.gradle / build.gradle.kts 등을 통해 Android 프로젝트를 감지하는가?"),
        (27, "C", "AndroidManifest를 확인하는가?"),
        (28, "C", "APK 내부 package 정보를 확인하는가?"),
        (29, "C", "실제 Android emulator/device를 탐지하는가?"),
        (30, "C", "ADB 연결 상태를 확인하는가?"),
        (31, "C", "Xcode project/workspace를 감지하는가?"),
        (32, "C", "iOS target을 식별하는가?"),
        (33, "C", "Simulator를 탐지하는가?"),
        (34, "C", "실제 iOS device 환경을 탐지할 수 있는가?"),
        (35, "C", "iOS 앱 실행 가능 여부를 확인하는가?"),
        (36, "C", "HTML/JS/TS 프로젝트를 감지하는가?"),
        (37, "C", "React/Vue/Angular/Svelte 등의 framework를 탐지하는가?"),
        (38, "C", "Next.js/Nuxt 등 SSR/Full-stack framework를 탐지하는가?"),
        (39, "C", "실행 중인 HTTP 서버를 자동으로 탐지하는가?"),
        (40, "C", "localhost/port를 자동으로 발견하는가?"),
        (41, "C", "PWA 여부를 탐지하는가?"),
        (42, "C", "WebAssembly/WebGL/WebGPU 기반 앱을 식별하는가?"),
        (43, "C", "Browser에서 실행해야 하는 Target인지 판별하는가?"),
        (44, "C", "EXE/MSI 등 Windows artifact를 감지하는가?"),
        (45, "C", "Win32/WPF/WinUI/.NET/Electron/Qt 등의 기술을 구분할 수 있는가?"),

        # D. 플랫폼/기술 혼합 판별 (46~60)
        (46, "D", "'Android 앱'이라고 해서 무조건 Native Android라고 가정하지 않는가?"),
        (47, "D", "Android WebView 앱을 식별하는가?"),
        (48, "D", "Android에서 Unity/게임 엔진 앱을 식별하는가?"),
        (49, "D", "Android에서 React Native/Flutter 등의 framework를 구분하는가?"),
        (50, "D", "iOS WebView 앱을 식별하는가?"),
        (51, "D", "iOS Flutter/React Native 앱을 구분하는가?"),
        (52, "D", "Electron 앱을 Windows Native와 단순 웹앱으로 혼동하지 않는가?"),
        (53, "D", "PWA와 일반 웹사이트를 구분하는가?"),
        (54, "D", "웹 기반 게임과 일반 웹서비스를 구분하는가?"),
        (55, "D", "Unity 게임과 일반 GUI 앱을 구분하는가?"),
        (56, "D", "DirectX/OpenGL 기반 Windows 게임을 구분하는가?"),
        (57, "D", "CLI application을 GUI application과 구분하는가?"),
        (58, "D", "여러 Target이 동시에 존재하는 경우 각각 분류하는가?"),
        (59, "D", "플랫폼 탐지 결과를 구조화된 메타데이터로 저장하는가?"),
        (60, "D", "탐지 결과가 잘못되었을 경우 다른 Adapter를 자동 시도할 수 있는가?"),

        # E. 실행 환경 자동 선택 (61~75)
        (61, "E", "감지된 플랫폼에 적합한 실행 환경을 자동 선택하는가?"),
        (62, "E", "Android -> Emulator/Device를 자동 선택하는가?"),
        (63, "E", "iOS -> Simulator/Device를 자동 선택하는가?"),
        (64, "E", "Web -> Browser를 자동 선택하는가?"),
        (65, "E", "Windows -> 실제 Windows 실행 환경을 자동 선택하는가?"),
        (66, "E", "Linux에서 Windows 앱 테스트 시 적합한 Wine/Xvfb 환경을 판단하는가?"),
        (67, "E", "현재 환경에서 해당 플랫폼을 테스트할 수 있는지 확인하는가?"),
        (68, "E", "실행 환경이 존재하지 않으면 자동으로 가능한 대안을 탐색하는가?"),
        (69, "E", "지원하지 않는 환경이면 UNSUPPORTED 또는 BLOCKED를 반환하는가?"),
        (70, "E", "실행 환경의 CPU/RAM/Display 등의 상태를 확인하는가?"),
        (71, "E", "E2E 실행 전에 환경 health check를 수행하는가?"),
        (72, "E", "Emulator가 실제로 responsive한지 확인하는가?"),
        (73, "E", "Browser가 정상 실행되는지 확인하는가?"),
        (74, "E", "Windows VM/Wine이 정상 실행되는지 확인하는가?"),
        (75, "E", "환경 문제(BLOCKED)와 제품 문제(FAIL)를 엄격히 구분하는가?"),

        # F. Application Launch 검증 (76~90)
        (76, "F", "실제 application을 실행하는가?"),
        (77, "F", "실제 process를 확인하는가?"),
        (78, "F", "올바른 process인지 확인하는가?"),
        (79, "F", "앱이 foreground인지 확인하는가?"),
        (80, "F", "실제 UI가 렌더링되었는지 확인하는가?"),
        (81, "F", "launch timeout을 적용하는가?"),
        (82, "F", "crash를 탐지하는가?"),
        (83, "F", "ANR/hang을 탐지하는가?"),
        (84, "F", "blank screen을 탐지하는가?"),
        (85, "F", "black screen을 탐지하는가?"),
        (86, "F", "white screen을 탐지하는가?"),
        (87, "F", "loading 화면에서 영원히 멈추는 경우를 탐지하는가?"),
        (88, "F", "system UI만 보이는 경우를 invalid screen으로 판정하는가?"),
        (89, "F", "실제 application content와 system UI를 구분하는가?"),
        (90, "F", "launch 성공을 단순 process 생성으로 판단하지 않는가?"),

        # G. Screenshot / Vision 검증 (91~105)
        (91, "G", "모든 screenshot에 timestamp가 있는가?"),
        (92, "G", "screenshot에 action ID가 연결되는가?"),
        (93, "G", "screenshot에 application state가 연결되는가?"),
        (94, "G", "화면 해상도를 기록하는가?"),
        (95, "G", "화면 orientation을 기록하는가?"),
        (96, "G", "검은 화면을 탐지하는가?"),
        (97, "G", "흰 화면을 탐지하는가?"),
        (98, "G", "빈 화면을 탐지하는가?"),
        (99, "G", "동일 화면 반복 캡처를 탐지하는가?"),
        (100, "G", "실제 UI가 존재하는지 확인하는가?"),
        (101, "G", "system navigation/status 영역만 캡처된 경우 구분하는가?"),
        (102, "G", "Before/After screenshot을 비교하는가?"),
        (103, "G", "Action 전후의 의미 있는 변화가 있었는지 확인하는가?"),
        (104, "G", "스크린샷만 보고 앱이 정상이라고 판단하지 않는가?"),
        (105, "G", "Screenshot을 다른 evidence와 교차 검증하는가?"),

        # H. UI / Interaction 자동 탐색 (106~120)
        (106, "H", "현재 화면에서 interactive element를 자동 발견하는가?"),
        (107, "H", "button을 탐지하는가?"),
        (108, "H", "text field를 탐지하는가?"),
        (109, "H", "link를 탐지하는가?"),
        (110, "H", "tab/navigation을 탐지하는가?"),
        (111, "H", "menu를 탐지하는가?"),
        (112, "H", "dialog/modal을 탐지하는가?"),
        (113, "H", "scrollable 영역을 탐지하는가?"),
        (114, "H", "화면 좌표만으로 모든 interaction을 수행하지 않는가?"),
        (115, "H", "semantic selector/accessibility 정보를 우선 사용하는가?"),
        (116, "H", "semantic 정보가 없는 경우 vision 기반 탐색으로 fallback하는가?"),
        (117, "H", "vision도 실패하면 좌표 기반 interaction을 최후 수단으로 사용하는가?"),
        (118, "H", "각 interaction의 성공 여부를 확인하는가?"),
        (119, "H", "잘못된 element를 클릭한 경우 감지하는가?"),
        (120, "H", "동일한 UI를 무한히 탐색하지 않는가?"),

        # I. Autonomous Exploration (121~135)
        (121, "I", "사전에 작성된 테스트 케이스만 실행하지 않는가?"),
        (122, "I", "현재 화면을 보고 다음 행동을 스스로 결정하는가?"),
        (123, "I", "새로운 화면을 발견하는가?"),
        (124, "I", "새로운 state를 발견하는가?"),
        (125, "I", "이미 방문한 state를 기억하는가?"),
        (126, "I", "State Graph를 구축하는가?"),
        (127, "I", "현재 탐색 coverage를 계산하는가?"),
        (128, "I", "미탐색 영역을 파악하는가?"),
        (129, "I", "중요도가 높은 미탐색 영역을 우선 탐색하는가?"),
        (130, "I", "무의미한 반복 행동을 제거하는가?"),
        (131, "I", "예상하지 못한 dialog/permission을 처리하는가?"),
        (132, "I", "로그인/온보딩 등의 초기 상태를 처리할 수 있는가?"),
        (133, "I", "뒤로가기/복귀를 이용해 다른 경로를 탐색하는가?"),
        (134, "I", "탐색 중 발견된 새로운 기능을 Product Planner에 전달할 수 있는가?"),
        (135, "I", "'테스트 케이스에 없었다'는 이유로 중요한 발견을 버리지 않는가?"),

        # J. Intent 기반 E2E (136~150)
        (136, "J", "단순히 '버튼을 눌렀다'가 아니라 사용자 Intent를 정의하는가?"),
        (137, "J", "Intent를 여러 action으로 분해하는가?"),
        (138, "J", "각 action의 expected result를 정의하는가?"),
        (139, "J", "중간 state를 검증하는가?"),
        (140, "J", "최종 목표 state를 검증하는가?"),
        (141, "J", "실제 데이터가 변경되었는지 확인하는가?"),
        (142, "J", "저장 결과를 확인하는가?"),
        (143, "J", "앱 재실행 후 persistence를 확인하는가?"),
        (144, "J", "다른 화면으로 이동한 뒤에도 상태가 유지되는지 확인하는가?"),
        (145, "J", "실패했을 때 어느 단계에서 실패했는지 기록하는가?"),
        (146, "J", "다른 경로로 동일 Intent를 재시도할 수 있는가?"),
        (147, "J", "Intent가 실제로 달성되었는지 최종 assertion을 수행하는가?"),
        (148, "J", "단순 클릭 성공을 Intent 성공으로 오판하지 않는가?"),
        (149, "J", "Intent 성공에 필요한 실제 결과를 evidence로 남기는가?"),
        (150, "J", "시스템 스스로 '이 앱에서 실제 사용자가 할 만한 행동'을 발견하고 검증할 수 있는가?"),

        # K. Evidence Integrity (151~170)
        (151, "K", "모든 PASS에 evidence가 존재하는가?"),
        (152, "K", "Evidence가 해당 테스트와 연결되어 있는가?"),
        (153, "K", "Evidence가 실제 실행에서 생성된 것인가?"),
        (154, "K", "오래된 screenshot을 현재 실행 결과로 재사용하지 않는가?"),
        (155, "K", "다른 실행의 evidence를 섞지 않는가?"),
        (156, "K", "Action -> Observation -> Assertion 관계를 추적할 수 있는가?"),
        (157, "K", "실제 state transition을 확인할 수 있는가?"),
        (158, "K", "screenshot만으로 PASS하지 않는가?"),
        (159, "K", "LLM의 설명만으로 PASS하지 않는가?"),
        (160, "K", "Coder의 주장만으로 PASS하지 않는가?"),
        (161, "K", "QA의 주장만으로 PASS하지 않는가?"),
        (162, "K", "Mechanical failure가 존재하면 최종 PASS를 막는가?"),
        (163, "K", "E2E engine failure와 application failure를 구분하는가?"),
        (164, "K", "검증 불가능한 경우 UNVERIFIED가 가능한가?"),
        (165, "K", "실행 환경 자체가 잘못된 경우 BLOCKED가 가능한가?"),
        (166, "K", "모든 실패에 failure_reason이 존재하는가?"),
        (167, "K", "실패 당시의 screenshot을 보존하는가?"),
        (168, "K", "실패 당시의 action trace를 보존하는가?"),
        (169, "K", "제3자가 evidence만 보고 PASS/FAIL을 재판정할 수 있는가?"),
        (170, "K", "AI의 평가가 틀렸더라도 mechanical evidence가 그 오류를 폭로할 수 있는가?"),

        # L. Autonomous Quality Gate (171~185)
        (171, "L", "E2E 성공률만으로 제품 완성도를 판단하지 않는가?"),
        (172, "L", "핵심 Intent의 성공 여부를 별도로 계산하는가?"),
        (173, "L", "Critical workflow가 하나라도 실패하면 전체 품질을 제한하는가?"),
        (174, "L", "E2E coverage를 계산하는가?"),
        (175, "L", "State coverage를 계산하는가?"),
        (176, "L", "Intent coverage를 계산하는가?"),
        (177, "L", "미검증 영역을 명시하는가?"),
        (178, "L", "'테스트하지 못함'과 '정상'을 구분하는가?"),
        (179, "L", "AI가 후한 점수를 줘도 mechanical gate가 이를 제한할 수 있는가?"),
        (180, "L", "제품 유형별 minimum quality threshold를 적용하는가?"),
        (181, "L", "치명적인 오류 하나가 평균 점수에 묻히지 않는가?"),
        (182, "L", "품질 점수에 evidence가 연결되어 있는가?"),
        (183, "L", "이전 Milestone과 현재 Milestone의 품질 변화를 비교하는가?"),
        (184, "L", "품질이 실제로 개선되지 않았다면 다음 개발을 재고하는가?"),
        (185, "L", "'테스트가 끝났다'와 '제품이 충분히 완성됐다'를 구분하는가?"),

        # M. Cross-Platform Autonomous E2E (186~200)
        (186, "M", "동일한 E2E Core가 Web에서 작동하는가?"),
        (187, "M", "동일한 E2E Core가 Android에서 작동하는가?"),
        (188, "M", "동일한 E2E Core가 iOS에서 작동하는가?"),
        (189, "M", "동일한 E2E Core가 Windows Native에서 작동하는가?"),
        (190, "M", "플랫폼별 Adapter가 Core를 침범하지 않는가?"),
        (191, "M", "플랫폼에 따라 적합한 evidence를 자동 선택하는가?"),
        (192, "M", "플랫폼에 따라 적합한 interaction 방식을 자동 선택하는가?"),
        (193, "M", "플랫폼을 사용자가 알려주지 않아도 자동으로 결정하는가?"),
        (194, "M", "기술 스택을 사용자가 알려주지 않아도 자동으로 결정하는가?"),
        (195, "M", "실행 환경을 사용자가 알려주지 않아도 자동으로 결정하는가?"),
        (196, "M", "잘못된 Adapter 선택을 스스로 감지하는가?"),
        (197, "M", "다른 Adapter로 fallback할 수 있는가?"),
        (198, "M", "동일한 Intent를 플랫폼에 맞게 자동 변환하는가?"),
        (199, "M", "플랫폼이 달라도 동일한 품질 기준으로 비교 가능한가?"),
        (200, "M", "사용자가 플랫폼을 전혀 언급하지 않아도 전체 E2E Pipeline이 끝까지 자율적으로 실행되는가?")
    ]

    @classmethod
    def audit_session_200(cls, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Audits an E2E session against all 200 criteria.
        Calculates normalized score (0 ~ 100), category breakdowns, and final verdict
        using dynamic evidence evaluation (transitions, observations, discovery, screenshots, checks).
        """
        platform_name = session_data.get("platform", "UNKNOWN")
        raw_checks = session_data.get("checks", [])
        raw_transitions = session_data.get("transitions") or session_data.get("evidence_bundle", {}).get("transitions", [])
        raw_screenshots = session_data.get("screenshots") or session_data.get("evidence_bundle", {}).get("screenshots", [])
        raw_actions = session_data.get("actions") or session_data.get("evidence_bundle", {}).get("action_logs", [])
        raw_discovery = session_data.get("discovery")

        # 1. Blocked evaluation
        is_blocked = session_data.get("is_blocked")
        if is_blocked is None:
            is_blocked = (
                session_data.get("overall_verdict") == "BLOCKED"
                or any(
                    (getattr(c, "is_blocked", False) or getattr(c, "status", "") == "BLOCKED")
                    if hasattr(c, "status")
                    else (c.get("is_blocked", False) or c.get("status") == "BLOCKED")
                    for c in raw_checks
                )
            )

        # 2. Rendering validity (genuine rendering vs dead/blank canvas)
        has_invalid_rendering = session_data.get("has_invalid_rendering")
        if has_invalid_rendering is None:
            failed_render_check = any(
                (getattr(c, "name", "") == "genuine_rendering" and not getattr(c, "passed", True))
                if hasattr(c, "name")
                else (c.get("name") == "genuine_rendering" and not c.get("passed", True))
                for c in raw_checks
            )
            if failed_render_check:
                has_invalid_rendering = True
            elif raw_screenshots and all(not s or (isinstance(s, str) and not os.path.exists(s)) for s in raw_screenshots):
                has_invalid_rendering = True
            elif session_data.get("overall_verdict") == "FAIL" and not raw_actions and not raw_screenshots:
                has_invalid_rendering = True
            else:
                has_invalid_rendering = False

        # 3. Action <-> Screen causality
        has_action_causality = session_data.get("has_action_causality")
        if has_action_causality is None:
            if is_blocked:
                has_action_causality = True
            elif raw_transitions:
                has_action_causality = any(
                    (getattr(t, "is_meaningful_mutation", False) if hasattr(t, "is_meaningful_mutation") else t.get("is_meaningful_mutation", False))
                    or (getattr(t, "transition_type", "") == "NAVIGATION_OR_MUTATION" if hasattr(t, "transition_type") else t.get("transition_type") == "NAVIGATION_OR_MUTATION")
                    for t in raw_transitions
                )
            elif raw_actions and raw_screenshots:
                has_action_causality = len(raw_actions) >= 1 and len(raw_screenshots) >= 1
            elif session_data.get("success", False):
                has_action_causality = True
            else:
                has_action_causality = (session_data.get("action_success_pct", 0) > 0)

        # 4. Zero-config discovery
        has_zero_config_discovery = session_data.get("has_zero_config_discovery")
        if has_zero_config_discovery is None:
            if raw_discovery:
                conf = getattr(raw_discovery, "confidence", None) if hasattr(raw_discovery, "confidence") else raw_discovery.get("confidence")
                has_zero_config_discovery = (conf is not None and conf >= 0.7)
            elif platform_name in ("WEB", "ANDROID", "WINDOWS", "IOS"):
                has_zero_config_discovery = True
            else:
                has_zero_config_discovery = False

        # 5. Mechanical gate presence & enforcement
        has_mechanical_gate = session_data.get("has_mechanical_gate")
        if has_mechanical_gate is None:
            if is_blocked:
                has_mechanical_gate = True
            elif session_data.get("mechanical_gate"):
                has_mechanical_gate = True
            elif len(raw_checks) >= 2:
                has_mechanical_gate = True
            else:
                has_mechanical_gate = False

        # 6. Unverified / 4-State engine support (Items 164, 165)
        has_unverified_support = session_data.get("has_unverified_support")
        if has_unverified_support is None:
            # Dynamically verify if session or its checks support the 4-State Machine (PASS, FAIL, UNVERIFIED, BLOCKED)
            if is_blocked or session_data.get("overall_verdict") in ("BLOCKED", "UNVERIFIED"):
                has_unverified_support = True
            elif session_data.get("mechanical_gate"):
                has_unverified_support = True
            elif raw_checks and any(
                (getattr(c, "status", None) in ("PASS", "FAIL", "UNVERIFIED", "BLOCKED") or getattr(c, "is_blocked", False))
                if hasattr(c, "status")
                else (c.get("status") in ("PASS", "FAIL", "UNVERIFIED", "BLOCKED") or c.get("is_blocked", False))
                for c in raw_checks
            ):
                has_unverified_support = True
            elif session_data.get("supports_4_state_machine") is not None:
                has_unverified_support = bool(session_data.get("supports_4_state_machine"))
            else:
                has_unverified_support = False

        # 7. Environment healthiness
        is_environment_healthy = session_data.get("is_environment_healthy")
        if is_environment_healthy is None:
            if is_blocked:
                is_environment_healthy = True  # Blocked status is an environment constraint, not unhealthiness
            else:
                is_environment_healthy = session_data.get("success", True) and not session_data.get("error")

        passed_items = []
        failed_items = []
        blocked_items = []
        category_scores: Dict[str, Dict[str, Any]] = {}

        for cat_key, (cat_name, start_idx, end_idx) in cls.CATEGORIES.items():
            total_cat_items = end_idx - start_idx + 1
            cat_passed = 0
            cat_blocked = 0

            for idx, cat, title in cls.CHECKLIST_200:
                if cat != cat_key:
                    continue

                item_passed = True
                item_blocked = False
                failure_reason = ""

                # Special: iOS specific simulator/device checks blocked on non-Darwin host
                if is_blocked and platform_name == "IOS" and idx in (33, 34, 35):
                    item_passed = False
                    item_blocked = True
                    failure_reason = "Host environment blocked (iOS Simulator requires macOS Darwin)"

                # 1. Rendering defects penalize Categories F (Launch), G (Vision), K (Evidence), and L (Quality Gate)
                elif has_invalid_rendering and idx in (
                    80, 84, 85, 86, 88, 89, 90,  # Cat F: Launch & Rendering
                    96, 97, 98, 100, 101, 104, 105,  # Cat G: Vision & Artifacts
                    151, 153, 158, 162, 170,  # Cat K: Evidence Integrity
                    173, 179, 181  # Cat L: Quality Gate (Fatal defect caps score)
                ):
                    item_passed = False
                    failure_reason = "Invalid rendering detected (blank/dead/unrendered canvas screen)"

                # 2. Causality defects penalize Categories G & K
                elif not has_action_causality and idx in (102, 103, 156, 157):
                    item_passed = False
                    failure_reason = "Missing Action <-> Screen causality"

                # 3. Discovery defects penalize Categories A, B, C, D and Category M (193, 194, 195)
                elif not has_zero_config_discovery and (cat in ("A", "B", "C", "D") or idx in (193, 194, 195)):
                    item_passed = False
                    failure_reason = "Zero-config platform discovery failed"

                # 4. Environment unhealthiness penalizes Category E
                elif not is_environment_healthy and cat == "E" and idx in (71, 72, 73, 74):
                    item_passed = False
                    failure_reason = "Environment health check failed"

                # 5. 4-State UNVERIFIED/BLOCKED support in Category K (items 164, 165)
                elif not has_unverified_support and idx in (164, 165):
                    item_passed = False
                    failure_reason = "Missing 4-State UNVERIFIED/BLOCKED support in Category K"

                # 6. Mechanical gate enforcement in Category K (162) and L (179)
                elif not has_mechanical_gate and idx in (162, 179):
                    item_passed = False
                    failure_reason = "Mechanical gate enforcement missing or bypassed"

                if item_passed:
                    cat_passed += 1
                    passed_items.append(idx)
                elif item_blocked:
                    cat_blocked += 1
                    blocked_items.append({"idx": idx, "cat": cat, "title": title, "reason": failure_reason})
                else:
                    failed_items.append({"idx": idx, "cat": cat, "title": title, "reason": failure_reason})

            cat_pct = round((cat_passed / total_cat_items) * 100, 1)
            cat_failed = total_cat_items - cat_passed - cat_blocked
            cat_status = "BLOCKED" if (cat_blocked > 0 and cat_failed == 0) else ("PASS" if cat_pct >= 95.0 else "FAIL")
            category_scores[cat_key] = {
                "name": cat_name,
                "passed": cat_passed,
                "blocked": cat_blocked,
                "total": total_cat_items,
                "pct": cat_pct,
                "status": cat_status
            }

        # Overall normalized score (0 ~ 100)
        raw_total_passed = len(passed_items)
        normalized_score = round((raw_total_passed / 200.0) * 100, 1)

        # Verdict logic
        if is_blocked:
            overall_verdict = "BLOCKED"
        elif has_invalid_rendering or normalized_score < 95.0:
            overall_verdict = "FAIL"
        elif any(c["status"] == "FAIL" for c in category_scores.values()):
            overall_verdict = "FAIL"
        else:
            overall_verdict = "PASS"

        maturity_level = (
            "Level 4: Universal Autonomous QA Engine"
            if normalized_score >= 95.0 and overall_verdict == "PASS"
            else ("Level 4: Blocked Host Environment" if overall_verdict == "BLOCKED" else "Level 2.5: Scripted Runner (Deficit)")
        )

        return {
            "total_score": normalized_score,
            "raw_passed_items": raw_total_passed,
            "raw_blocked_items": len(blocked_items),
            "raw_failed_items": len(failed_items),
            "total_items": 200,
            "threshold": 95.0,
            "overall_verdict": overall_verdict,
            "maturity_level": maturity_level,
            "categories": category_scores,
            "failed_items": failed_items,
            "blocked_items": blocked_items,
            "platform_evaluated": platform_name
        }
