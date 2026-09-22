#!/usr/bin/env python3
"""
📊 Dynamic Test Scenario Graph & Checkpoint Engine (AGY 3.0 Standard)
Provides autonomous test plan generation based on static & dynamic app capability inspection
(AndroidManifest.xml, permissions, PRD.md, USER_FLOW.md, hardware modules).
Replaces rigid static test lists with adaptive 0%~100% Verification Graphs and targeted checkpoint execution.
"""

import os
import re
import glob
import logging
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("ScenarioGraphEngine")


@dataclass
class ScenarioCheckpoint:
    """Represents a milestone state in the 0%~100% verification graph."""
    percent: int
    tc_id: str
    title: str
    phase: str
    preconditions: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    assertions: List[str] = field(default_factory=list)
    status: str = "PENDING"
    details: str = ""
    duration_ms: int = 0
    execution_env: str = "EMULATOR_KVM (Simulated)"
    discovered_targets: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "percent": self.percent,
            "tc_id": self.tc_id,
            "title": self.title,
            "phase": self.phase,
            "preconditions": self.preconditions,
            "actions": self.actions,
            "assertions": self.assertions,
            "status": self.status,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "execution_env": self.execution_env,
            "discovered_targets": self.discovered_targets
        }


@dataclass
class ScenarioGraph:
    """Represents the complete dynamic verification graph for a specific application."""
    app_category: str
    app_name: str
    detected_permissions: List[str] = field(default_factory=list)
    detected_hardware: List[str] = field(default_factory=list)
    checkpoints: List[ScenarioCheckpoint] = field(default_factory=list)

    def get_checkpoints_from(self, start_percent: int = 0) -> List[ScenarioCheckpoint]:
        """Returns checkpoints filtered from a specific starting percent (e.g. 50% or 70%)."""
        return [cp for cp in self.checkpoints if cp.percent >= start_percent]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_name": self.app_name,
            "app_category": self.app_category,
            "detected_permissions": self.detected_permissions,
            "detected_hardware": self.detected_hardware,
            "checkpoints": [cp.to_dict() for cp in self.checkpoints]
        }


@dataclass
class TelemetryProfile:
    """Domain-specific expected performance, power, and frame rate profile."""
    profile_id: str
    baseline_desc: str
    expected_cpu_range_ms: Tuple[int, int]
    max_wakelock_allowed_ms: int
    target_fps: int
    max_memory_pss_kb: int
    max_battery_drain_pct_hr: float

    def evaluate(
        self,
        measured_cpu_ms: int,
        measured_wakelock_ms: int,
        measured_fps: float = 30.0,
        measured_pss_kb: int = 50000
    ) -> Dict[str, Any]:
        """
        Evaluates system telemetry against domain-specific profile baseline:
        Produces Baseline, Expected profile, Measured, Threshold, Deviation, Verdict.
        """
        cpu_min, cpu_max = self.expected_cpu_range_ms
        cpu_ok = measured_cpu_ms <= cpu_max
        wake_ok = measured_wakelock_ms <= self.max_wakelock_allowed_ms
        pss_ok = measured_pss_kb <= self.max_memory_pss_kb
        fps_ok = measured_fps >= (self.target_fps * 0.8)

        cpu_dev_pct = round(((measured_cpu_ms - cpu_max) / cpu_max) * 100.0, 1) if cpu_max else 0.0
        wake_dev_pct = round(((measured_wakelock_ms - self.max_wakelock_allowed_ms) / self.max_wakelock_allowed_ms) * 100.0, 1) if self.max_wakelock_allowed_ms else 0.0

        if cpu_ok and wake_ok and pss_ok and fps_ok:
            verdict = "PASS"
        elif not cpu_ok and measured_cpu_ms < cpu_max * 1.3 and wake_ok:
            verdict = "WARN"
        else:
            verdict = "FAIL"

        return {
            "profile_id": self.profile_id,
            "baseline": self.baseline_desc,
            "expected_profile": {
                "cpu_range_ms": [cpu_min, cpu_max],
                "max_wakelock_ms": self.max_wakelock_allowed_ms,
                "target_fps": self.target_fps,
                "max_memory_pss_kb": self.max_memory_pss_kb,
                "max_drain_rate_pct_hr": self.max_battery_drain_pct_hr
            },
            "measured": {
                "cpu_ms": measured_cpu_ms,
                "wakelock_ms": measured_wakelock_ms,
                "fps": measured_fps,
                "memory_pss_kb": measured_pss_kb
            },
            "thresholds": {
                "cpu_ceiling_ms": cpu_max,
                "wakelock_ceiling_ms": self.max_wakelock_allowed_ms,
                "pss_ceiling_kb": self.max_memory_pss_kb,
                "min_fps": int(self.target_fps * 0.8)
            },
            "deviations": {
                "cpu_deviation_pct": cpu_dev_pct,
                "wakelock_deviation_pct": wake_dev_pct
            },
            "verdict": verdict
        }


TELEMETRY_PROFILES: Dict[str, TelemetryProfile] = {
    "HIGH_PERF_GAMING": TelemetryProfile(
        profile_id="HIGH_PERF_GAMING",
        baseline_desc="60fps dynamic 2D/3D interactive canvas with sustained GPU/CPU rendering",
        expected_cpu_range_ms=(500, 3500),
        max_wakelock_allowed_ms=5000,
        target_fps=60,
        max_memory_pss_kb=450000,
        max_battery_drain_pct_hr=15.0
    ),
    "MEDIA_CAMERA": TelemetryProfile(
        profile_id="MEDIA_CAMERA",
        baseline_desc="Camera sensor pipeline & hardware preview framebuffer with active viewfinder",
        expected_cpu_range_ms=(300, 1800),
        max_wakelock_allowed_ms=3000,
        target_fps=30,
        max_memory_pss_kb=280000,
        max_battery_drain_pct_hr=12.0
    ),
    "DOCUMENT_UTILITY": TelemetryProfile(
        profile_id="DOCUMENT_UTILITY",
        baseline_desc="Lightweight document editor / calculator / office utility with zero background drain",
        expected_cpu_range_ms=(50, 450),
        max_wakelock_allowed_ms=250,
        target_fps=30,
        max_memory_pss_kb=120000,
        max_battery_drain_pct_hr=2.5
    ),
    "IOT_CONNECTIVITY": TelemetryProfile(
        profile_id="IOT_CONNECTIVITY",
        baseline_desc="BLE GATT daemon / peripheral scanner with intermittent radio packets",
        expected_cpu_range_ms=(100, 800),
        max_wakelock_allowed_ms=1500,
        target_fps=20,
        max_memory_pss_kb=110000,
        max_battery_drain_pct_hr=4.0
    ),
    "DRAWING_CANVAS": TelemetryProfile(
        profile_id="DRAWING_CANVAS",
        baseline_desc="Interactive 2D vector/bitmap canvas with high-frequency touch sampling and low background drain",
        expected_cpu_range_ms=(80, 850),
        max_wakelock_allowed_ms=400,
        target_fps=60,
        max_memory_pss_kb=220000,
        max_battery_drain_pct_hr=5.0
    ),
    "GENERAL_APP": TelemetryProfile(
        profile_id="GENERAL_APP",
        baseline_desc="Standard multi-activity UI application with navigation & forms",
        expected_cpu_range_ms=(100, 900),
        max_wakelock_allowed_ms=800,
        target_fps=30,
        max_memory_pss_kb=160000,
        max_battery_drain_pct_hr=4.5
    )
}


class ScenarioGraphEngine:
    """
    Analyzes application capabilities from APK badging, AndroidManifest.xml, and PRD.md
    to synthesize a dynamic, domain-adapted 0%~100% verification graph.
    """

    @classmethod
    def get_telemetry_profile(cls, category: str) -> TelemetryProfile:
        """Returns the appropriate domain telemetry profile."""
        if category in ["game", "arcade"]:
            return TELEMETRY_PROFILES["HIGH_PERF_GAMING"]
        elif category in ["drawing", "canvas", "paint"]:
            return TELEMETRY_PROFILES["DRAWING_CANVAS"]
        elif category in ["camera", "media"]:
            return TELEMETRY_PROFILES["MEDIA_CAMERA"]
        elif category in ["editor", "calculator", "finance"]:
            return TELEMETRY_PROFILES["DOCUMENT_UTILITY"]
        elif category in ["bluetooth", "iot"]:
            return TELEMETRY_PROFILES["IOT_CONNECTIVITY"]
        return TELEMETRY_PROFILES["GENERAL_APP"]

    @classmethod
    def inspect_app_capabilities(
        cls,
        workspace: str,
        apk_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Discovers permissions, activities, hardware modules, and PRD goals."""
        permissions = set()
        activities = []
        app_name = os.path.basename(os.path.abspath(workspace)) if workspace else "App"

        # 1. Inspect APK badging via aapt if available
        if apk_path and os.path.isfile(apk_path):
            try:
                aapt_bin = shutil_which_aapt()
                if aapt_bin:
                    res = subprocess.run([aapt_bin, "dump", "badging", apk_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8.0)
                    if res.returncode == 0:
                        for line in res.stdout.splitlines():
                            if line.startswith("uses-permission:"):
                                m = re.search(r"name='([^']+)'", line)
                                if m:
                                    permissions.add(m.group(1))
                            elif line.startswith("launchable-activity:"):
                                m = re.search(r"name='([^']+)'", line)
                                if m:
                                    activities.append(m.group(1))
                            elif line.startswith("application-label:"):
                                m = re.search(r"label='([^']+)'", line)
                                if m:
                                    app_name = m.group(1)
            except Exception as e:
                logger.debug(f"APK badging inspection notice: {e}")

        # 2. Inspect workspace AndroidManifest.xml
        if workspace and os.path.isdir(workspace):
            manifest_candidates = [
                os.path.join(workspace, "app", "src", "main", "AndroidManifest.xml"),
                os.path.join(workspace, "src", "main", "AndroidManifest.xml"),
                os.path.join(workspace, "AndroidManifest.xml")
            ]
            for m_path in manifest_candidates:
                if os.path.isfile(m_path):
                    try:
                        tree = ET.parse(m_path)
                        root = tree.getroot()
                        for p_elem in root.findall(".//uses-permission"):
                            p_name = p_elem.get("{http://schemas.android.com/apk/res/android}name") or p_elem.get("name")
                            if p_name:
                                permissions.add(p_name)
                        for a_elem in root.findall(".//activity"):
                            a_name = a_elem.get("{http://schemas.android.com/apk/res/android}name") or a_elem.get("name")
                            if a_name:
                                activities.append(a_name)
                    except Exception as e:
                        logger.debug(f"Manifest parse notice: {e}")

        # 3. Inspect PRD / USER_FLOW context
        prd_text = ""
        for fname in ["PRD.md", "USER_FLOW.md", "README.md"]:
            p = os.path.join(workspace, fname)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        prd_text += " " + f.read()
                except Exception:
                    pass

        # 4. Map detected hardware & modules
        hardware = []
        perm_str = " ".join(permissions).upper()
        raw_combined = f"{perm_str} {prd_text}".lower()

        if any(k in raw_combined for k in ["camera", "카메라", "사진"]):
            hardware.append("CAMERA")
        if any(k in raw_combined for k in ["bluetooth", "ble", "블루투스", "비콘"]):
            hardware.append("BLUETOOTH")
        if any(k in raw_combined for k in ["gyro", "accelerometer", "자이로", "가속도", "센서", "sensor"]):
            hardware.append("SENSORS")
        if any(k in raw_combined for k in ["location", "gps", "위치", "지도", "주차"]):
            hardware.append("LOCATION")
        if any(k in raw_combined for k in ["storage", "read_external", "write_external", "pdf", "파일", "저장"]):
            hardware.append("STORAGE")
        if any(k in raw_combined for k in ["notification", "push", "알림", "fcm"]):
            hardware.append("NOTIFICATION")

        # 5. Classify Category
        category = "general"
        if "CAMERA" in hardware:
            category = "camera"
        elif any(k in raw_combined for k in ["paint", "draw", "canvas", "sketch", "그림판", "드로잉", "손글씨", "doodle", "서명"]):
            category = "drawing"
        elif any(k in raw_combined for k in ["pdf", "에디터", "문서", "editor"]):
            category = "editor"
        elif "BLUETOOTH" in hardware:
            category = "bluetooth"
        elif any(k in raw_combined for k in ["게임", "game", "unity", "sprite", "arcade"]):
            category = "game"
        elif any(k in raw_combined for k in ["주차", "parking", "mobility", "차량"]):
            category = "mobility"
        elif any(k in raw_combined for k in ["쇼핑", "store", "cart", "장바구니"]):
            category = "ecommerce"
        elif any(k in raw_combined for k in ["대시보드", "차트", "통계", "dashboard"]):
            category = "dashboard"

        return {
            "app_name": app_name,
            "category": category,
            "permissions": sorted(list(permissions)),
            "activities": activities,
            "hardware": hardware,
            "prd_text": prd_text[:1000]
        }

    @classmethod
    def build_verification_graph(
        cls,
        workspace: str,
        apk_path: Optional[str] = None,
        mission: str = "",
        discovered_nodes: Optional[List[Any]] = None
    ) -> ScenarioGraph:
        """
        Builds an app-tailored 0%~100% ScenarioGraph.
        Dynamically adapts actions and assertions to real discovered UI components
        rather than relying purely on static category templates.
        """
        info = cls.inspect_app_capabilities(workspace, apk_path)
        cat = info["category"]
        app_name = info["app_name"]
        hardware = info["hardware"]
        perms = info["permissions"]

        discovered_buttons = []
        discovered_inputs = []
        if discovered_nodes:
            for n in discovered_nodes:
                lbl = getattr(n, "label", "") or getattr(n, "text", "")
                if getattr(n, "clickable", False) and lbl and len(lbl) < 30:
                    discovered_buttons.append(lbl)
                if "edittext" in getattr(n, "class_name", "").lower():
                    discovered_inputs.append(lbl or "EditText")

        checkpoints = []

        # Checkpoint 0%: Cold Boot & Binary Launch
        checkpoints.append(ScenarioCheckpoint(
            percent=0,
            tc_id="TC-BOOT-00",
            title="0% 앱 콜드부트 & OS 바이너리 패키징 기동",
            phase="COLD_BOOT",
            preconditions=["APK 바이너리 무결성 확인", "ADB OS 서브시스템 온라인"],
            actions=["메인 액티비티 기동 (`am start`)", "메모리 PSS 베이스라인 측정"],
            assertions=["No Process Crash", "Activity Manager 'RUNNING' 상태", "초기 뷰포트 정상 렌더링"],
            execution_env="EMULATOR_KVM (OS Binary)"
        ))

        # Checkpoint 25%: Auth / Onboarding / Permissions
        onboard_assertions = ["온보딩/권한 오버레이 통과 완료", "메인 화면 상호작용 가능 상태 도달"]
        if "CAMERA" in hardware or "android.permission.CAMERA" in perms:
            onboard_assertions.append("카메라 런타임 권한 승인 완료")
        if "LOCATION" in hardware or any("LOCATION" in p for p in perms):
            onboard_assertions.append("위치 접근 런타임 권한 승인 완료")

        checkpoints.append(ScenarioCheckpoint(
            percent=25,
            tc_id="TC-ONBOARD-25",
            title="25% 런타임 권한 수락 & 온보딩 자율 통과",
            phase="AUTH_ONBOARDING",
            preconditions=["State 0% 통과"],
            actions=["UiAutomator XML 덤프", "권한/온보딩 모달 감지 시 자율 탭 Bypassing"],
            assertions=onboard_assertions,
            execution_env="EMULATOR_KVM (Accessibility Framework)"
        ))

        # Checkpoint 50%: Core Domain Navigation (Dynamically customized to discovered UI nodes)
        if cat == "drawing":
            core_actions = ["캔버스 뷰포트 감지 및 툴바 활성화", "기본 브러시 베지에 곡선 스트로크 드로잉", "팔레트 색상 칩 전환"]
            core_assertions = ["캔버스 픽셀 변이율(Pixel Delta) >= 0.1% 확인", "팔레트 선택 색상과 렌더링 획 색상 동기화 확인", "48dp 툴바 터치 타깃 준수"]
        elif cat == "editor":
            core_actions = ["기본 문서/캔버스 템플릿 로드", "문서 에디터 툴바 활성화"]
            core_assertions = ["문서 본문 텍스트/캔버스 렌더링 확인", "에디터 조작 컨트롤 48dp 터치 타깃 준수"]
        elif cat == "camera":
            core_actions = ["카메라 프리뷰 화면 전환", "가상 카메라 씬 바인딩"]
            core_assertions = ["카메라 SurfaceView/TextureView 렌더링", "셔터 버튼 활성화"]
        elif cat == "bluetooth":
            core_actions = ["BLE 장치 검색(Scan) 진입", "스캔 인디케이터 상태 확인"]
            core_assertions = ["BLE 스캔 서비스 정상 구동", "디바이스 리스트 컨테이너 노출"]
        elif cat == "game":
            core_actions = ["메인 게임 스테이지 진입", "가상 조이스틱 연속 스와이프", "콤보 액션 연타"]
            core_assertions = ["프레임버퍼 픽셀 델타 확인 (Visual Freeze 0건)", "터치 입력 후 엔티티 반응 단언", "Canvas 60fps 유지"]
        elif cat == "mobility":
            core_actions = ["주차/차량 위치 대시보드 로드", "구역 선택 뷰포트 진입"]
            core_assertions = ["층수/구역 인디케이터 정상 노출", "위치 마커 렌더링"]
        else:
            core_actions = ["메인 대시보드/홈 뷰포트 탐색", "주요 네비게이션 탭 전환"]
            core_assertions = ["핵심 데이터 컨테이너 정상 렌더링", "오버플로우 0건"]

        if discovered_buttons:
            core_actions.append(f"자율 발견된 타깃 컴포넌트 '{discovered_buttons[0]}' 포커스")

        checkpoints.append(ScenarioCheckpoint(
            percent=50,
            tc_id="TC-CORE-50",
            title=f"50% [{cat.upper()}] 핵심 기능 뷰 & 주요 도메인 진입",
            phase="CORE_NAVIGATION",
            preconditions=["온보딩 통과 완료", "메인 네비게이션 트리 식별"],
            actions=core_actions,
            assertions=core_assertions,
            execution_env="EMULATOR_KVM (Genuine Framebuffer)",
            discovered_targets=discovered_buttons[:3]
        ))

        # Checkpoint 60%: 6. 여러 브랜치 탐색 (Multi-Branch Deep Navigation & Backtracking)
        branch_actions = [
            "6. 여러 브랜치 탐색: 하위 브랜치 후보(탭/메뉴/설정/목록) 자동 감지 및 등록",
            "브랜치 스택 계층 진입 및 서브 뷰포트 상태 검증",
            "현재 브랜치 탐색 완료 후 상위 브랜치로 체계적 백트래킹(Backtrack `keyevent 4`) 복귀"
        ]
        branch_assertions = [
            "6. 여러 브랜치 탐색: 최소 1개 이상 하위 브랜치 진입 성공",
            "브랜치 스택 기반 체계적 백트래킹 복귀 확인",
            "뒤로가기 루프 0건 (BackKeyLoopDefender 방어 무결성)"
        ]
        checkpoints.append(ScenarioCheckpoint(
            percent=60,
            tc_id="TC-BRANCH-60",
            title=f"60% [{cat.upper()}] 6. 여러 브랜치 탐색 및 체계적 백트래킹",
            phase="MULTI_BRANCH_NAVIGATION",
            preconditions=["State 50% 통과", "하위 브랜치 진입점 식별"],
            actions=branch_actions,
            assertions=branch_assertions,
            execution_env="EMULATOR_KVM (Accessibility & Wire Protocol)",
            discovered_targets=discovered_buttons[:2]
        ))

        # Checkpoint 75%: Deep Action & Hardware / Data Interaction
        if cat == "drawing":
            feat_actions = ["지우개 모드 소거 제스처", "되돌리기(Undo)/다시실행(Redo) 상태 검증", "작품 파일 로컬 스토리지 저장(Save/Export)"]
            feat_assertions = ["지우개 픽셀 소거 무결성 (잔여 변이 정상화)", "저장된 이미지 0바이트 방어 (Non-Zero Byte)", "올블랙/올화이트 단색 캔버스 파손 방어 (Valid Bitmap)"]
            hw_env = "EMULATOR_KVM (Hardware Framebuffer)"
        elif cat == "editor":
            feat_actions = ["서명/주석 레이어 추가 제스처", "문서 변경사항 저장(Save) 버튼 탭"]
            feat_assertions = ["서명 스트로크 반영", "파일 시스템 수정본 생성 확인", "저장 완료 토스트/피드백"]
            hw_env = "EMULATOR_KVM (Hardware Framebuffer)"
        elif cat == "camera":
            feat_actions = ["가상 셔터 버튼 탭", "이미지 캡처 및 필터 효과 적용"]
            feat_assertions = ["새로운 사진 파일 DCIM 저장 확인", "썸네일 미리보기 갱신"]
            hw_env = "EMULATOR_VIRTUALSCENE (Mock Camera Hardware)"
        elif cat == "bluetooth":
            feat_actions = ["타깃 가상 BLE 장치 연결 시도", "GATT 특성값 R/W 시뮬레이션"]
            feat_assertions = ["연결 상태 'CONNECTED'", "GATT 패킷 전송 성공"]
            hw_env = "SIMULATED_BLE_STACK (Virtual GATT Daemon)"
        elif cat == "game":
            feat_actions = ["차징 롱프레스 터치", "카오스 경계선 터치 및 일시정지(Pause) 토글", "게임오버/리트라이 루프 복구 검증"]
            feat_assertions = ["Choreographer 스킵 프레임 방어 (Jank 0건)", "게임오버 소프트락 0건 (Softlock Defender)", "메모리 누수/크래시 0건"]
            hw_env = "EMULATOR_KVM (Hardware Framebuffer)"
        else:
            feat_actions = ["텍스트 인젝션 & 폼 입력", "핵심 액션 버튼 실행 및 상태 갱신"]
            feat_assertions = ["입력값 상태 반영", "데이터 갱신 완료", "네트워크/스토리지 I/O 정상"]
            hw_env = "EMULATOR_KVM (Hardware Framebuffer)"

        if discovered_inputs:
            feat_actions.append(f"자율 발견된 입력 필드 '{discovered_inputs[0]}' 테스트 데이터 인젝션")
        if len(discovered_buttons) > 1:
            feat_actions.append(f"자율 발견된 보조 액션 버튼 '{discovered_buttons[1]}' 트리거")
            feat_assertions.append(f"버튼 '{discovered_buttons[1]}' 인터랙션 무결성 단언")

        if "NOTIFICATION" in hardware:
            feat_assertions.append("NotificationManager 알림 발행 확인 (dumpsys notification)")

        checkpoints.append(ScenarioCheckpoint(
            percent=75,
            tc_id="TC-FEATURE-75",
            title=f"75% [{cat.upper()}] 하드웨어 모듈 & 심층 데이터 조작 검증",
            phase="FEATURE_INTERACTION",
            preconditions=["State 50% 통과"],
            actions=feat_actions,
            assertions=feat_assertions,
            execution_env=hw_env,
            discovered_targets=discovered_inputs[:2] + discovered_buttons[1:3]
        ))

        # Checkpoint 100%: Lifecycle, Rotation, Diagnostics & Persistence
        checkpoints.append(ScenarioCheckpoint(
            percent=100,
            tc_id="TC-LIFECYCLE-100",
            title="100% 생명주기(백그라운드/재시작) & 배터리/알림/크래시 종합 진단",
            phase="PERSISTENCE_LIFECYCLE",
            preconditions=["모든 인터랙션 완료"],
            actions=[
                "가로 화면 회전 (`user_rotation 1`) & 복원",
                "백그라운드➔포그라운드 생명주기 전이 (`input keyevent 3` & `am start`)",
                "앱 콜드 재기동 및 상태 복원 (`am force-stop` & `am start -W`)",
                "dumpsys batterystats 전력 계측",
                "dumpsys notification 알림 단언",
                "Logcat FATAL EXCEPTION 전수 스캔"
            ],
            assertions=[
                "회전 시 0px 오버플로우 & 액티비티 재시작 무결성",
                "백그라운드 전이 후 복귀 시 크래시 0건 & PSS 메모리 안정",
                "콜드 재시작 후 윈도우 포커스 및 영속성 데이터 정상 복구",
                "런타임 크래시 0건 (FATAL EXCEPTION 0건)",
                "ANR 0건",
                "정상 배터리 CPU 점유율 및 메모리 PSS 안정"
            ],
            execution_env="ADB_DUMPSYS & LOGCAT (Real OS Subsystem)"
        ))

        return ScenarioGraph(
            app_category=cat,
            app_name=app_name,
            detected_permissions=perms,
            detected_hardware=hardware,
            checkpoints=checkpoints
        )


def shutil_which_aapt() -> Optional[str]:
    """Helper to locate aapt binary."""
    import shutil
    cand = shutil.which("aapt") or shutil.which("aapt2")
    if cand:
        return cand
    sdk_roots = [
        os.environ.get("ANDROID_HOME", ""),
        os.path.expanduser("~/Android/Sdk"),
        "/opt/android-sdk"
    ]
    for root in sdk_roots:
        if root and os.path.isdir(root):
            tools = glob.glob(os.path.join(root, "build-tools", "*", "aapt"))
            if tools:
                tools.sort(reverse=True)
                return tools[0]
    return None
