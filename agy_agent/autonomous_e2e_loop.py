#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔬 Autonomous E2E Verification Loop (AGY 4.x)
=============================================
멀티 에이전트 협업 파이프라인(!mode 2 / 2+ / 3 / 3+)의 Coder 턴 종료 직후에 삽입되어
"실제로 테스트를 돌리고 → 실 화면 스크린샷을 실측 기록하고 → 부족한 점을 스스로 규명하여
→ Coder/QA 피드백으로 환류"하는 자율 E2E 폐루프를 제공한다.

배경:
    기존 E2E(cross_platform_e2e_runner)는 수동 `!e2e` 명령에서만 동작했고, 협업 파이프라인
    내부에서는 시각 스크린샷 몇 장으로 끝났다. 본 모듈은 협업 모드에서
      (1) 프로젝트의 실제 테스트 명령을 자동 탐지·실행하고,
      (2) 표준 스크린샷 디렉토리의 신선한 캡처를 실측 기록하며,
      (3) 테스트 실패/런타임 에러/시각 증거 결손을 구조화된 결함으로 도출해
      (4) QA 프롬프트와 Coder 피드백에 기계적으로 주입한다.

물리 증거: reports/gate4/autonomous_e2e_report.json
스위치:
    AUTONOMOUS_E2E_LOOP_ENABLED(=0 비활성)
    AUTONOMOUS_E2E_MAX_ROUNDS(결함 자동 환류 상한, 기본 2)
    AUTONOMOUS_E2E_TEST_TIMEOUT(테스트 1건 실행 타임아웃 초, 기본 180)
    AUTONOMOUS_E2E_VISION_ENABLED(=0 시 스크린샷 전수 비전 분석 비활성, 기본 1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import subprocess

logger = logging.getLogger("AutonomousE2ELoop")

EVIDENCE_REL_PATH = os.path.join("reports", "gate4", "autonomous_e2e_report.json")

# 실 화면 스크린샷이 생성되는 대표 디렉터리 (discord_screenshot_gallery 와 정합)
_SCREENSHOT_DIRS: Tuple[str, ...] = (
    os.path.join("reports", "gate5", "screenshots"),
    os.path.join("reports", "screenshots"),
    os.path.join("reports", "qa_screenshots"),
    os.path.join("reports", "e2e"),
    os.path.join("reports", "evidence"),
    "reports",
    "screenshots",
    os.path.join("tests", "e2e", "screenshots"),
)
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# GUI 프로젝트 판정용 파일 지표 (Visual Asset Gate 와 동일 기준)
_GUI_INDICATORS: Tuple[str, ...] = (
    "index.html", "package.json", "build.gradle", "build.gradle.kts",
    "pubspec.yaml", "App.tsx", "App.jsx", "App.vue", "index.tsx", "index.jsx",
)

# UI 소스 변경 판정용 확장자 (스크린샷 결손을 참결함으로 승격할지 결정)
_UI_SOURCE_EXTS: Tuple[str, ...] = (
    ".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss",
    ".kt", ".dart", ".swift", ".xml",
)

_SEVERITY_BLOCKER = "BLOCKER"
_SEVERITY_MAJOR = "MAJOR"
_SEVERITY_MINOR = "MINOR"
_BLOCKING_SEVERITIES = (_SEVERITY_BLOCKER, _SEVERITY_MAJOR)

# 테스트 출력에서 치명 런타임 에러로 간주하는 시그니처
_RUNTIME_ERROR_SIGNATURES = (
    "traceback (most recent call last)",
    "segmentation fault",
    "fatal exception",
    "nullpointerexception",
    "stack overflow",
)

# 환경/의존성 미비로 인한 실패 시그니처 (차단 결함이 아닌 미검증 갭으로 강등)
_ENV_FAILURE_SIGNATURES = (
    "no tests ran",
    "cannot find module",
    "err_module_not_found",
    "modulenotfounderror",
    "no module named",
    "command not found",
    "no such file or directory",
)


# ---------------------------------------------------------------------------
# 환경 스위치
# ---------------------------------------------------------------------------
def autonomous_e2e_enabled() -> bool:
    raw = os.environ.get("AUTONOMOUS_E2E_LOOP_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def autonomous_e2e_max_rounds() -> int:
    """결함 자동 환류(반려 후 재작업) 상한. 이후에는 Auditor/인간 에스컬레이션."""
    return max(0, _env_int("AUTONOMOUS_E2E_MAX_ROUNDS", 2))


def autonomous_e2e_test_timeout() -> float:
    """테스트 1건 실행 타임아웃(초)."""
    return float(max(10, _env_int("AUTONOMOUS_E2E_TEST_TIMEOUT", 180)))


def autonomous_e2e_vision_enabled() -> bool:
    """수집된 실 화면 스크린샷을 전수(1장도 빠뜨리지 않고) 비전 모델로 분석할지 여부 (기본 ON)."""
    raw = os.environ.get("AUTONOMOUS_E2E_VISION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
@dataclass
class E2ETestRun:
    """실제 실행한 테스트 명령 1건의 물리 결과."""
    name: str
    command: str
    returncode: int
    passed: bool
    duration_sec: float
    output_tail: str = ""
    reason: str = ""  # 미실행/스킵 사유 (실행했으면 "")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2EDefect:
    """자율 E2E 가 스스로 적발한 결함/결손 항목."""
    severity: str
    category: str
    title: str
    evidence: str = ""
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2EScreenshotInspection:
    """실 화면 스크린샷 1장에 대한 전수 비전 검사 결과 (꼼꼼한 화면 분석 기록)."""
    screenshot: str
    engine: str = ""
    verdict: str = "SKIPPED"  # PASS | FAIL | WARN | SKIPPED
    score: Optional[int] = None
    summary: str = ""
    defects: List[Dict[str, Any]] = field(default_factory=list)
    degraded: bool = False  # 비전 API 미설정/실패로 정밀 분석 미수행

    @property
    def verified(self) -> bool:
        """정밀 비전 분석이 실제로 수행되었는가 (폴백 메타데이터는 미검증으로 본다)."""
        return not self.degraded and self.verdict != "SKIPPED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutonomousE2EReport:
    """자율 E2E 검증 1회의 물리 리포트 (reports/gate4/autonomous_e2e_report.json)."""
    workspace: str
    verdict: str  # PASS | FAIL | BLOCKED
    ran_tests: bool
    round_index: int = 1
    test_runs: List[E2ETestRun] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    vision_inspections: List[E2EScreenshotInspection] = field(default_factory=list)
    defects: List[E2EDefect] = field(default_factory=list)
    gaps: List[E2EDefect] = field(default_factory=list)
    blank_frame_count: int = 0
    duplicate_frame_count: int = 0
    summary: str = ""
    evidence_path: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def screenshot_count(self) -> int:
        return len(self.screenshots)

    def blocking_defects(self) -> List[E2EDefect]:
        return [d for d in self.defects if d.severity in _BLOCKING_SEVERITIES]

    def has_blocking_defects(self) -> bool:
        return len(self.blocking_defects()) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "verdict": self.verdict,
            "ran_tests": self.ran_tests,
            "round_index": self.round_index,
            "test_runs": [t.to_dict() for t in self.test_runs],
            "test_run_count": len(self.test_runs),
            "tests_passed": sum(1 for t in self.test_runs if t.passed),
            "tests_failed": sum(1 for t in self.test_runs if not t.passed and not t.reason),
            "screenshots": self.screenshots,
            "screenshot_count": self.screenshot_count,
            "vision_inspections": [v.to_dict() for v in self.vision_inspections],
            "vision_inspected_count": len(self.vision_inspections),
            "vision_failed_count": sum(1 for v in self.vision_inspections if v.verdict == "FAIL"),
            "blank_frame_count": self.blank_frame_count,
            "duplicate_frame_count": self.duplicate_frame_count,
            "defects": [d.to_dict() for d in self.defects],
            "blocking_defect_count": len(self.blocking_defects()),
            "gaps": [d.to_dict() for d in self.gaps],
            # 커버리지: 이번 라운드에 무엇을 얼마나 꼼꼼히 검사했는지 물리 기록
            "coverage": {
                "tests_executed": sum(1 for t in self.test_runs if not t.reason),
                "screenshots_captured": self.screenshot_count,
                "screenshots_vision_analyzed": sum(1 for v in self.vision_inspections if v.verified),
                "blocking_defects": len(self.blocking_defects()),
                "gaps": len(self.gaps),
            },
            "summary": self.summary,
            "evidence_path": self.evidence_path,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# 테스트 명령 자동 탐지 (제로 컨피그)
# ---------------------------------------------------------------------------
def detect_test_commands(workspace: str) -> List[Dict[str, Any]]:
    """워크스페이스 구조를 스캔해 실제 실행 가능한 테스트 명령을 도출한다.

    반환: [{"name": ..., "command": ..., "argv": [...]}, ...] — 실행 가능한 것만.
    셸 주입 방지를 위해 명령은 문자열이 아니라 argv 리스트로 다룬다.
    """
    ws = workspace or ""
    commands: List[Dict[str, Any]] = []

    def _has_file(name: str) -> bool:
        return os.path.isfile(os.path.join(ws, name))

    # 1. Python (pytest) — sys.executable 사용으로 항상 실행 가능
    py_markers = (
        _has_file("pytest.ini")
        or _has_file("tox.ini")
        or _has_file("conftest.py")
        or os.path.isdir(os.path.join(ws, "tests"))
        or any(f.startswith("test_") and f.endswith(".py") for f in _safe_listdir(ws))
    )
    if py_markers:
        commands.append({
            "name": "pytest",
            "argv": [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
            "command": "python -m pytest -q",
        })

    # 2. Node (npm test) — package.json 에 실제 test 스크립트가 있을 때만
    pkg_json = os.path.join(ws, "package.json")
    if os.path.isfile(pkg_json) and shutil.which("npm"):
        try:
            with open(pkg_json, "r", encoding="utf-8", errors="ignore") as f:
                pkg = json.load(f)
            test_script = str((pkg.get("scripts") or {}).get("test", "") or "").strip()
        except Exception:
            test_script = ""
        if test_script and "no test specified" not in test_script.lower():
            commands.append({
                "name": "npm-test",
                "argv": ["npm", "test", "--silent"],
                "command": "npm test --silent",
            })

    # 3. Gradle (JVM/Android)
    gradlew = os.path.join(ws, "gradlew")
    if os.path.isfile(gradlew):
        task = "testDebugUnitTest" if os.path.isfile(os.path.join(ws, "build.gradle")) or os.path.isfile(os.path.join(ws, "build.gradle.kts")) else "test"
        if os.access(gradlew, os.X_OK):
            argv = [gradlew, task, "--console=plain"]
        else:
            argv = ["sh", gradlew, task, "--console=plain"]
        commands.append({"name": f"gradle:{task}", "argv": argv, "command": f"./gradlew {task}"})

    # 4. Rust (cargo test)
    if _has_file("Cargo.toml") and shutil.which("cargo"):
        commands.append({"name": "cargo-test", "argv": ["cargo", "test", "--quiet"], "command": "cargo test --quiet"})

    # 5. Go (go test)
    if _has_file("go.mod") and shutil.which("go"):
        commands.append({"name": "go-test", "argv": ["go", "test", "./..."], "command": "go test ./..."})

    return commands


def _safe_listdir(path: str) -> List[str]:
    try:
        return os.listdir(path)
    except Exception:
        return []


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# 자율 E2E 루프
# ---------------------------------------------------------------------------
class AutonomousE2ELoop:
    """협업 파이프라인용 자율 E2E 검증 루프 (실 테스트 실행 + 스크린샷 실측 + 결함 환류)."""

    # ----- 테스트 실행 -----
    @classmethod
    def run_test_suites(cls, workspace: str, timeout: Optional[float] = None) -> List[E2ETestRun]:
        """탐지된 실제 테스트 명령을 실행하고 물리 결과를 반환한다."""
        if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
            # 자체 단위 테스트 중에는 대상 프로젝트 재귀 실행을 원천 차단한다.
            return []
        per_timeout = timeout if timeout is not None else autonomous_e2e_test_timeout()
        runs: List[E2ETestRun] = []
        for spec in detect_test_commands(workspace):
            runs.append(cls._execute_one(workspace, spec, per_timeout))
        return runs

    @classmethod
    def _execute_one(cls, workspace: str, spec: Dict[str, Any], timeout: float) -> E2ETestRun:
        name = str(spec.get("name", "test"))
        command = str(spec.get("command", ""))
        argv = list(spec.get("argv") or [])
        started = time.time()
        try:
            res = subprocess.run(
                argv,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            tail = (res.stdout or "")[-4000:]
            # pytest exit 5 = 테스트 0건 수집 → 실패가 아니라 '미검증'으로 정직하게 표기
            reason = "테스트 0건 수집(no tests ran)" if (res.returncode == 5 and "no tests ran" in tail.lower()) else ""
            return E2ETestRun(
                name=name,
                command=command,
                returncode=res.returncode,
                passed=(res.returncode == 0),
                duration_sec=round(time.time() - started, 2),
                output_tail=tail,
                reason=reason,
            )
        except subprocess.TimeoutExpired as e:
            tail = ""
            try:
                tail = str(getattr(e, "output", "") or "")[-2000:]
            except Exception:
                pass
            return E2ETestRun(
                name=name,
                command=command,
                returncode=-1,
                passed=False,
                duration_sec=round(time.time() - started, 2),
                output_tail=tail,
                reason=f"타임아웃({int(timeout)}s) 초과",
            )
        except FileNotFoundError:
            return E2ETestRun(
                name=name,
                command=command,
                returncode=-2,
                passed=False,
                duration_sec=round(time.time() - started, 2),
                reason="실행 파일을 찾을 수 없음(도구 미설치)",
            )
        except Exception as e:
            return E2ETestRun(
                name=name,
                command=command,
                returncode=-3,
                passed=False,
                duration_sec=round(time.time() - started, 2),
                reason=f"실행 오류: {e}",
            )

    # ----- 스크린샷 실측 -----
    @classmethod
    def collect_screenshots(
        cls, workspace: str, session_start_time: Optional[float] = None
    ) -> List[str]:
        """표준 디렉터리의 신선한 스크린샷을 최신순으로 수집한다."""
        ws = workspace or ""
        found: List[Tuple[float, str]] = []
        seen = set()
        for rel in _SCREENSHOT_DIRS:
            d = os.path.join(ws, rel) if rel else ws
            if not os.path.isdir(d):
                continue
            for fname in _safe_listdir(d):
                if not fname.lower().endswith(_IMAGE_EXTS):
                    continue
                fp = os.path.join(d, fname)
                if fp in seen:
                    continue
                seen.add(fp)
                mtime = _safe_mtime(fp)
                if session_start_time is not None and mtime < (session_start_time - 1.0):
                    continue
                try:
                    if os.path.getsize(fp) < 500:
                        continue
                except Exception:
                    continue
                found.append((mtime, fp))
        found.sort(key=lambda t: t[0], reverse=True)
        return [fp for _, fp in found]

    @classmethod
    def _count_blank_frames(cls, screenshots: List[str]) -> int:
        # 전수조사: 표본 8장이 아니라 수집된 스크린샷 전체를 검사한다.
        if not screenshots:
            return 0
        try:
            from PIL import Image, ImageStat
        except Exception:
            return 0
        blanks = 0
        for fp in screenshots:
            try:
                with Image.open(fp) as im:
                    stddevs = ImageStat.Stat(im.convert("RGB")).stddev
                    if all(s < 2.0 for s in stddevs):
                        blanks += 1
            except Exception:
                continue
        return blanks

    @staticmethod
    def _find_duplicate_frame_pairs(screenshots: List[str]) -> List[Tuple[str, str]]:
        """
        전수조사: 연속 스크린샷이 바이트 단위로 100% 동일한 쌍(UI Freeze/Hang 후보)을 검출한다.
        파일명이 아니라 실제 SHA-256 페이로드로만 판정한다.
        """
        import hashlib
        pairs: List[Tuple[str, str]] = []
        prev_hash: Optional[str] = None
        prev_path: Optional[str] = None
        for fp in screenshots:
            try:
                with open(fp, "rb") as f:
                    cur = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                prev_hash, prev_path = None, None
                continue
            if prev_hash is not None and cur == prev_hash:
                pairs.append((prev_path or "", fp))
            prev_hash, prev_path = cur, fp
        return pairs

    # ----- 스크린샷 전수 비전 분석 -----
    @classmethod
    def inspect_screenshots_vision(
        cls, screenshots: List[str]
    ) -> List[E2EScreenshotInspection]:
        """수집된 실 화면 스크린샷을 1장도 빠뜨리지 않고(전수) 비전 모델로 분석·기록한다.

        비전 모델이 각 스크린샷에서 시각 결함을 구조화([VISUAL_VERDICT]/[VISUAL_DEFECTS])해
        돌려주면 그대로 검사 결과로 남긴다. 비전 API 미설정/실패 시에는 정직하게
        degraded=True 로 표기해 '검증되지 않음'을 위장하지 않는다.
        """
        if not screenshots or not autonomous_e2e_vision_enabled():
            return []
        # 테스트 실행 중에는 외부 Vision API 호출을 원천 차단한다 (쿼터 보호·결정적 테스트)
        if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules or "unittest" in sys.modules:
            return []
        try:
            from vision_attachment_analyzer import VisionAttachmentAnalyzer
            from visual_craft_loop import parse_vision_verdict
        except Exception as e:
            logger.debug(f"Vision engine unavailable for E2E inspection: {e}")
            return []

        inspections: List[E2EScreenshotInspection] = []
        for shot in screenshots:
            try:
                res = asyncio.run(
                    VisionAttachmentAnalyzer.analyze_image_file(shot, role="design_critique")
                )
            except Exception as e:
                logger.debug(f"E2E vision inspection failed for {shot}: {e}")
                continue
            if not res or not res.get("success"):
                continue
            degraded = bool(res.get("degraded"))
            parsed = (
                {"verdict": "SKIPPED", "score": None, "defects": [], "summary": ""}
                if degraded
                else parse_vision_verdict(res.get("analysis") or "")
            )
            inspections.append(E2EScreenshotInspection(
                screenshot=os.path.basename(shot),
                engine=str(res.get("engine", "")),
                verdict=str(parsed.get("verdict") or ("SKIPPED" if degraded else "WARN")),
                score=parsed.get("score"),
                summary=str(parsed.get("summary") or "")[:200],
                defects=list(parsed.get("defects") or []),
                degraded=degraded,
            ))
        return inspections

    # ----- 결함 분석 -----
    @staticmethod
    def _is_gui_project(workspace: str) -> bool:
        return any(os.path.isfile(os.path.join(workspace or "", g)) for g in _GUI_INDICATORS)

    @staticmethod
    def _changed_ui_sources(changed_files: Optional[List[str]]) -> List[str]:
        out = []
        for f in (changed_files or []):
            if any(str(f).lower().endswith(ext) for ext in _UI_SOURCE_EXTS):
                out.append(str(f))
        return out

    @classmethod
    def analyze(
        cls,
        workspace: str,
        test_runs: List[E2ETestRun],
        screenshots: List[str],
        changed_files: Optional[List[str]] = None,
        inspections: Optional[List[E2EScreenshotInspection]] = None,
    ) -> Tuple[List[E2EDefect], List[E2EDefect], str]:
        """실행 결과로부터 (차단 결함, 개선 갭, 판정)을 도출한다."""
        defects: List[E2EDefect] = []
        gaps: List[E2EDefect] = []
        inspections = inspections or []

        # 1. 테스트 실패
        for run in test_runs:
            if run.reason:
                # 도구 미설치/타임아웃은 환경 문제 → 개선 갭(비차단)으로 정직하게 기록
                gaps.append(E2EDefect(
                    severity=_SEVERITY_MINOR,
                    category="TEST_UNVERIFIED",
                    title=f"테스트 미검증: {run.name}",
                    evidence=run.reason,
                    fix="테스트 실행 환경을 정비하고 실제 테스트를 통과시키십시오.",
                ))
                continue
            if run.passed:
                continue
            lowered = (run.output_tail or "").lower()
            # pytest exit 5(테스트 0건) / 의존성·도구 미비는 환경 문제 → 미검증 갭(비차단)
            if run.returncode == 5 or any(sig in lowered for sig in _ENV_FAILURE_SIGNATURES):
                gaps.append(E2EDefect(
                    severity=_SEVERITY_MINOR,
                    category="TEST_UNVERIFIED",
                    title=f"테스트 미검증: {run.name} (exit={run.returncode})",
                    evidence=(run.output_tail or run.command).strip()[-600:],
                    fix="테스트 의존성/실행 환경을 정비하고 실제 테스트를 통과시키십시오.",
                ))
                continue
            is_runtime = any(sig in lowered for sig in _RUNTIME_ERROR_SIGNATURES)
            defects.append(E2EDefect(
                severity=_SEVERITY_BLOCKER,
                category="RUNTIME_ERROR" if is_runtime else "TEST_FAILURE",
                title=f"테스트 실패: {run.name} (exit={run.returncode})",
                evidence=(run.output_tail or "").strip()[-1200:] or "출력 없음",
                fix="위 테스트 실패 로그의 실패 지점을 분석하여 프로덕션 소스코드를 수정하고, 테스트를 재실행해 통과시키십시오.",
            ))

        # 2. 시각 증거 결손 (GUI + 이번 라운드 UI 소스 변경 시에만 참결함 승격)
        if cls._is_gui_project(workspace):
            ui_changes = cls._changed_ui_sources(changed_files)
            if not screenshots:
                if ui_changes:
                    defects.append(E2EDefect(
                        severity=_SEVERITY_MAJOR,
                        category="VISUAL_EVIDENCE_MISSING",
                        title="실 화면 스크린샷 증거 0장 (UI 변경 감지)",
                        evidence=f"이번 라운드 UI 소스 변경: {', '.join(ui_changes[:6])}",
                        fix="개발 서버/에뮬레이터를 실행해 실제 렌더링 화면을 캡처하고 reports/gate5/screenshots 에 남기십시오. 목업/도형 대체는 금지됩니다.",
                    ))
                else:
                    gaps.append(E2EDefect(
                        severity=_SEVERITY_MINOR,
                        category="VISUAL_EVIDENCE_MISSING",
                        title="실 화면 스크린샷 증거 없음 (UI 변경 미감지)",
                        evidence="GUI 프로젝트이나 이번 라운드에서 UI 소스 변경이 감지되지 않았습니다.",
                        fix="UI 기능 구현 라운드에서는 실 렌더링 캡처를 시각 증거로 함께 남기십시오.",
                    ))
            else:
                blanks = cls._count_blank_frames(screenshots)
                if blanks and blanks == len(screenshots):
                    defects.append(E2EDefect(
                        severity=_SEVERITY_MAJOR,
                        category="VISUAL_BLANK",
                        title="스크린샷이 전부 빈 화면(blank)",
                        evidence=f"{blanks}장 전수 검사 결과 모두 단색/빈 프레임 (stddev<2.0)",
                        fix="앱이 실제로 렌더링되도록 런타임 크래시/미출력 원인을 수정하고, 유의미한 화면을 다시 캡처하십시오.",
                    ))
                # UI Freeze/Hang 전수 검출: 연속 프레임 바이트 완전 동일
                dup_pairs = cls._find_duplicate_frame_pairs(screenshots)
                if dup_pairs:
                    pair_desc = ", ".join(
                        f"{os.path.basename(a)}=={os.path.basename(b)}" for a, b in dup_pairs[:4]
                    )
                    defects.append(E2EDefect(
                        severity=_SEVERITY_MAJOR,
                        category="VISUAL_UI_FREEZE",
                        title=f"연속 스크린샷 바이트 완전 동일 {len(dup_pairs)}쌍 (UI Freeze/Hang 의심)",
                        evidence=pair_desc,
                        fix="화면 멈춤(UI Freeze) 여부를 확인하고, 상호작용에 따라 실제 상태가 변하는 화면을 다시 캡처하십시오.",
                    ))

        # 3. 스크린샷 전수 비전 분석 결과 (꼼꼼한 화면 검사 → 결함/갭 적발)
        #    실제 정밀 분석이 수행된(verified) 검사만 판정에 반영한다. 폴백 메타데이터는 배제.
        verified = [v for v in inspections if v.verified]
        for v in verified:
            blocking_defects = [
                d for d in v.defects
                if str(d.get("severity", "")).upper() in _BLOCKING_SEVERITIES
            ]
            if v.verdict == "FAIL" or blocking_defects:
                detail = " | ".join(
                    f"{d.get('severity')}:{d.get('rule_id')}@{d.get('location')} {d.get('issue')}"
                    for d in blocking_defects[:4]
                ) or v.summary
                defects.append(E2EDefect(
                    severity=_SEVERITY_MAJOR,
                    category="VISUAL_VISION_DEFECT",
                    title=f"실 화면 비전 검사 FAIL: {v.screenshot} (score={v.score})",
                    evidence=(detail or "비전 모델이 시각 결함을 보고했습니다.")[:600],
                    fix="스크린샷에서 적발된 시각 결함(줄바꿈/오버플로우/터치 타깃/레이아웃)을 수정하고 실제 화면을 다시 캡처하십시오.",
                ))
            elif v.verdict == "WARN":
                gaps.append(E2EDefect(
                    severity=_SEVERITY_MINOR,
                    category="VISUAL_VISION_WARN",
                    title=f"실 화면 비전 검사 경고: {v.screenshot}",
                    evidence=(v.summary or "")[:300],
                    fix="경고된 시각 항목을 점검하십시오.",
                ))
        if screenshots and not verified:
            gaps.append(E2EDefect(
                severity=_SEVERITY_MINOR,
                category="VISUAL_VISION_UNVERIFIED",
                title=f"실 화면 스크린샷 {len(screenshots)}장이 비전 검증되지 않음",
                evidence="비전 API 미설정/호출 실패로 스크린샷 전수 분석이 수행되지 않았습니다.",
                fix="OPENROUTER_API_KEY/GEMINI_API_KEY 를 설정해 스크린샷 전수 비전 검사를 활성화하십시오.",
            ))

        # 4. 테스트 스위트 자체 부재 → 개선 갭 (비차단: 무한 반려 방지)
        if not test_runs and not detect_test_commands(workspace):
            gaps.append(E2EDefect(
                severity=_SEVERITY_MINOR,
                category="NO_TESTS",
                title="자동 탐지된 테스트 스위트 없음",
                evidence="pytest/npm test/gradle test/cargo/go 테스트 명령을 찾지 못했습니다.",
                fix="핵심 워크플로에 대한 회귀 테스트를 추가해 자율 검증이 가능하도록 하십시오.",
            ))

        # 판정: 차단 결함 > 실제 테스트 통과 > (그 외) 미검증
        blocking = [d for d in defects if d.severity in _BLOCKING_SEVERITIES]
        if blocking:
            verdict = "FAIL"
        elif any(r.passed for r in test_runs):
            verdict = "PASS"
        else:
            verdict = "BLOCKED"
        return defects, gaps, verdict

    # ----- 오케스트레이션 -----
    @classmethod
    def run(
        cls,
        workspace: str,
        round_index: int = 1,
        session_start_time: Optional[float] = None,
        changed_files: Optional[List[str]] = None,
    ) -> AutonomousE2EReport:
        """자율 E2E 1회를 수행하고 물리 리포트를 디스크에 기록한다 (예외를 밖으로 던지지 않는다)."""
        ws = workspace or ""
        try:
            test_runs = cls.run_test_suites(ws)
            screenshots = cls.collect_screenshots(ws, session_start_time)
            inspections = cls.inspect_screenshots_vision(screenshots)
            defects, gaps, verdict = cls.analyze(
                ws, test_runs, screenshots, changed_files, inspections
            )
            report = AutonomousE2EReport(
                workspace=ws,
                verdict=verdict,
                ran_tests=bool(test_runs),
                round_index=round_index,
                test_runs=test_runs,
                screenshots=screenshots,
                vision_inspections=inspections,
                defects=defects,
                gaps=gaps,
                blank_frame_count=cls._count_blank_frames(screenshots),
                duplicate_frame_count=len(cls._find_duplicate_frame_pairs(screenshots)),
            )
            report.summary = cls._build_summary(report)
            cls.write_evidence(report, ws)
            return report
        except Exception as e:
            logger.debug(f"Autonomous E2E loop internal error: {e}")
            report = AutonomousE2EReport(
                workspace=ws,
                verdict="BLOCKED",
                ran_tests=False,
                round_index=round_index,
                gaps=[E2EDefect(
                    severity=_SEVERITY_MINOR,
                    category="LOOP_ERROR",
                    title="자율 E2E 루프 내부 오류",
                    evidence=str(e)[:500],
                )],
                summary=f"자율 E2E 루프 내부 오류로 검증을 건너뜁니다: {e}",
            )
            return report

    @staticmethod
    def _build_summary(report: AutonomousE2EReport) -> str:
        executed = [r for r in report.test_runs if not r.reason]
        passed = sum(1 for r in executed if r.passed)
        failed = len(executed) - passed
        if report.ran_tests:
            head = f"실 테스트 {len(executed)}건 실행 (통과 {passed} / 실패 {failed})"
        else:
            head = "실행 가능한 테스트 명령 없음"
        verified = sum(1 for v in report.vision_inspections if v.verified)
        return (
            f"[{report.verdict}] {head} · 스크린샷 {report.screenshot_count}장 "
            f"(전수 비전분석 {verified}장) · "
            f"차단 결함 {len(report.blocking_defects())}건 · 개선 갭 {len(report.gaps)}건"
        )

    @classmethod
    def write_evidence(cls, report: AutonomousE2EReport, workspace: str) -> str:
        """물리 증거 파일 기록. 실패해도 파이프라인을 막지 않는다."""
        path = os.path.join(workspace or "", EVIDENCE_REL_PATH)
        report.evidence_path = path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Autonomous E2E evidence write skipped: {e}")
        return path

    # ----- 프롬프트/피드백 직렬화 -----
    @classmethod
    def format_prompt_block(cls, report: Optional[AutonomousE2EReport]) -> str:
        """QA 프롬프트에 주입할 자율 E2E 리포트 블록."""
        if report is None:
            return ""
        executed = [r for r in report.test_runs if not r.reason]
        passed = sum(1 for r in executed if r.passed)
        lines: List[str] = []
        lines.append("\n**[🔬 자율 E2E 검증 리포트 (실 테스트 실행 + 스크린샷 실측)]**:")
        lines.append(f"- 판정: `{report.verdict}` — {report.summary}")
        if executed:
            for r in executed:
                icon = "✅" if r.passed else "❌"
                lines.append(f"  {icon} `{r.command}` → exit={r.returncode} ({r.duration_sec}s)")
        else:
            lines.append("  ⚠️ 실행된 테스트 명령이 없습니다 (자율 탐지 실패/도구 미설치).")
        lines.append(f"- 실 화면 스크린샷 실측: **{report.screenshot_count}장**")
        if report.vision_inspections:
            lines.append(
                f"- 스크린샷 전수 비전 분석: **{len(report.vision_inspections)}장** (1장도 빠뜨리지 않고 개별 검사)"
            )
            _marks = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIPPED": "▫️"}
            for v in report.vision_inspections:
                mark = _marks.get(v.verdict, "•")
                score = f", score={v.score}" if v.score is not None else ""
                lines.append(f"  {mark} `{v.screenshot}` → {v.verdict}{score}")
            lines.append(
                "  ▸ **[전수 검사 원칙]**: 위 개별 화면 검사 결과를 포함해 모든 기능/화면을 빠짐없이 확인하고, "
                "검증하지 못한 항목은 '미검증'으로 정직하게 기록하십시오."
            )
        for d in report.defects:
            lines.append(f"  ❌ [{d.severity}/{d.category}] {d.title}")
            if d.evidence:
                lines.append(f"     └ 근거: {d.evidence[:400]}")
        for g in report.gaps:
            lines.append(f"  🔧 [개선/{g.category}] {g.title} — {g.evidence[:200]}")
        if report.blocking_defects():
            lines.append(
                "\n⚠️ **[QA 필수 지침]**: 위 자율 E2E에서 BLOCKER/MAJOR 차단 결함이 적발되었습니다. "
                "실제 테스트 실패·런타임 에러·실 화면 증거 결손이 해소되지 않은 상태에서는 "
                "`[STATUS: APPROVED]`를 선언할 수 없습니다. Coder에게 구체적 수정 스펙과 함께 반려하십시오.\n"
            )
        else:
            lines.append(
                "\n✅ **[QA 참고]**: 자율 E2E 차단 결함은 없습니다. 남은 개선 갭이 사용자 체감 가치를 높인다면 "
                "[차기 백로그]로만 기록하고 승인 판정은 기능 충족성 기준으로 내리십시오.\n"
            )
        return "\n".join(lines) + "\n"

    @classmethod
    def build_fix_directive(cls, report: Optional[AutonomousE2EReport]) -> str:
        """Coder 피드백으로 환류할 자율 E2E 수정 지시문."""
        if report is None:
            return ""
        blocking = report.blocking_defects()
        lines = ["[🔬 AUTONOMOUS-E2E-FIX — 자율 E2E 검증에서 적발된 결함 수정 지시]"]
        for i, d in enumerate(blocking, 1):
            lines.append(f"{i}. [{d.severity}/{d.category}] {d.title}")
            if d.evidence:
                lines.append(f"   • 근거: {d.evidence[:600]}")
            if d.fix:
                lines.append(f"   • 조치: {d.fix}")
        if not blocking:
            lines.append("차단 결함 없음 — 아래 개선 갭을 여유가 되면 반영하십시오.")
        for g in report.gaps:
            lines.append(f"- [개선/{g.category}] {g.title}: {g.fix}")
        return "\n".join(lines)

    @classmethod
    def has_blocking_defects(cls, report: Optional[AutonomousE2EReport]) -> bool:
        return bool(report is not None and report.has_blocking_defects())
