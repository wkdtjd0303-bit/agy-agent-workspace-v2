#!/usr/bin/env python3
"""
🔎 GapSignalScanner (AGY Mode 3 — Code/Signal-Driven Gap Discovery)

Commander가 '문서에 적힌 계획(PRD/ROADMAP)'이 아니라 '그때그때 디스크의 코드와
실제 신호'를 읽고 다음 개선점을 찾도록 만드는 신호 스캐너.

수집 신호(Signal):
  1. test   — 단위/E2E 테스트 실패(주니트 XML) 및 테스트 부재
  2. lint   — 정적 분석(Lint) Error
  3. crash  — 런타임 크래시/예외 로그
  4. stub   — TODO/FIXME/미구현 스텁 잔재

각 신호는 Value x Risk x Cost 로 점수화되어 정렬되며, Commander는 이 중
'단 하나의 최우선 결손'만 골라 다음 마일스톤으로 삼는다(스코프 가드).
"""

import os
import re
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Iterable

logger = logging.getLogger("GapSignalScanner")


_SEVERITY_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}

_IGNORED_DIRS = {
    ".git", "build", ".gradle", "node_modules", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", "dist", ".cache", "out", ".scannerwork", "coverage",
    ".gemini", ".pytest_cache",
}

_SOURCE_EXTS = (
    ".kt", ".java", ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css",
    ".vue", ".svelte", ".rs", ".go", ".c", ".cpp",
)

_STUB_MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
_STUB_BODY_RE = re.compile(
    r"(NotImplementedError|UnsupportedOperationException|"
    r"throw\s+new\s+NotImplemented|return\s+null\s*;?\s*//\s*(stub|mock|todo))",
    re.IGNORECASE,
)

_CRASH_PATTERNS = (
    "FATAL EXCEPTION",
    "E/AndroidRuntime",
    "Unhandled exception",
    "Traceback (most recent call last)",
    "Segmentation fault",
    "panic:",
)


@dataclass
class GapSignal:
    """A single actionable improvement candidate discovered from code/signals."""
    area: str            # "test" | "lint" | "crash" | "stub"
    title: str           # short capability title for the milestone
    detail: str          # what is wrong / what to do
    evidence: str        # concrete disk evidence
    severity: str = "medium"   # high | medium | low
    value: float = 0.5         # 0..1 user-perceived value
    risk: float = 0.3          # 0..1 implementation risk
    cost: float = 0.3          # 0..1 implementation cost
    actions: List[str] = field(default_factory=list)

    @property
    def priority(self) -> float:
        base = self.value * _SEVERITY_WEIGHT.get(self.severity, 0.3)
        return round(base / max(0.1, self.risk * self.cost), 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "area": self.area,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "severity": self.severity,
            "value": self.value,
            "risk": self.risk,
            "cost": self.cost,
            "priority": self.priority,
            "actions": list(self.actions),
        }


class GapSignalScanner:
    """Bounded, read-only scanner over the real workspace."""

    MAX_FILE_BYTES = 200_000
    MAX_SCAN_FILES = 4000

    # ── Public API ──────────────────────────────────────────────────────────

    @classmethod
    def scan(
        cls,
        workspace: Optional[str],
        domain: str = "",
        source_files: Optional[Iterable[str]] = None,
        test_files: Optional[Iterable[str]] = None,
    ) -> List[GapSignal]:
        """Returns all discovered gap signals for the workspace (unranked)."""
        if not workspace or not os.path.isdir(workspace):
            return []

        signals: List[GapSignal] = []
        for probe in (
            cls._scan_test_signals,
            cls._scan_lint_signals,
            cls._scan_crash_signals,
            cls._scan_stub_signals,
        ):
            try:
                signals.extend(probe(workspace, source_files=source_files, test_files=test_files) or [])
            except Exception as e:  # never let one probe break planning
                logger.debug(f"[GapSignalScanner] {probe.__name__} notice: {e}")
        return signals

    @classmethod
    def rank(cls, signals: List[GapSignal]) -> List[GapSignal]:
        return sorted(signals, key=lambda s: s.priority, reverse=True)

    @classmethod
    def select_next(
        cls,
        workspace: Optional[str],
        domain: str = "",
        source_files: Optional[Iterable[str]] = None,
        test_files: Optional[Iterable[str]] = None,
    ) -> Optional[GapSignal]:
        """Returns the single highest-priority gap, or None when the codebase is clean."""
        ranked = cls.rank(cls.scan(workspace, domain, source_files, test_files))
        return ranked[0] if ranked else None

    # ── Probes ──────────────────────────────────────────────────────────────

    @classmethod
    def _iter_files(cls, workspace: str, exts: Optional[tuple] = None, name_filter=None, include_build: bool = False):
        count = 0
        for root, dirs, files in os.walk(workspace):
            # `build/` is normally pruned (source scanning), but report artifacts
            # (test-results, lint) physically live under it, so probes may opt in.
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and (d not in _IGNORED_DIRS or (include_build and d == "build"))
            ]
            for fname in files:
                if exts and not fname.lower().endswith(exts):
                    continue
                if name_filter and not name_filter(fname):
                    continue
                count += 1
                if count > cls.MAX_SCAN_FILES:
                    return
                yield os.path.join(root, fname)

    @classmethod
    def _read_bounded(cls, path: str) -> str:
        try:
            if os.path.getsize(path) > cls.MAX_FILE_BYTES:
                return ""
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    @classmethod
    def _scan_test_signals(cls, workspace: str, source_files=None, test_files=None) -> List[GapSignal]:
        signals: List[GapSignal] = []
        failures = 0
        errors = 0
        suites = 0

        for xml_path in cls._iter_files(workspace, exts=(".xml",), include_build=True):
            low = xml_path.lower()
            if "test-results" not in low and "testreport" not in low:
                continue
            content = cls._read_bounded(xml_path)
            if not content or "<testsuite" not in content:
                continue
            try:
                root = ET.fromstring(content)
            except Exception:
                continue
            for suite in root.iter("testsuite"):
                suites += 1
                failures += int(suite.get("failures", 0) or 0)
                errors += int(suite.get("errors", 0) or 0)

        if failures or errors:
            signals.append(GapSignal(
                area="test",
                title="테스트 실패/에러 0건 복구 및 회귀 방어",
                detail=f"주니트 리포트 {suites}개 스위트에서 실패 {failures}건 / 에러 {errors}건이 감지되었습니다.",
                evidence=f"test-results XML: failures={failures}, errors={errors}",
                severity="high",
                value=0.95, risk=0.2, cost=0.35,
                actions=[
                    "실패한 테스트를 재현하고 근본 원인을 수정",
                    "수정 후 전체 테스트 스위트 0 failures/0 errors 재검증",
                    "동일 결함 재발 방지 회귀 테스트 추가",
                ],
            ))
        else:
            test_count = len(list(test_files)) if test_files is not None else None
            if test_count is None:
                test_count = sum(1 for _ in cls._iter_files(
                    workspace, exts=_SOURCE_EXTS,
                    name_filter=lambda f: "test" in f.lower() or "spec" in f.lower(),
                ))
            has_prod = bool(source_files) if source_files is not None else bool(
                list(cls._iter_files(workspace, exts=_SOURCE_EXTS))
            )
            if has_prod and test_count == 0:
                signals.append(GapSignal(
                    area="test",
                    title="핵심 로직 자동 테스트 스위트 구축",
                    detail="프로덕션 코드는 존재하나 이를 검증하는 자동 테스트가 디스크에 없습니다.",
                    evidence="테스트 파일 0건 (프로덕션 소스 존재)",
                    severity="medium",
                    value=0.7, risk=0.2, cost=0.5,
                    actions=[
                        "핵심 비즈니스 로직 경계값 테스트 작성",
                        "실패 케이스/예외 경로 테스트 추가",
                        "CI에서 실행 가능한 테스트 러너 연결",
                    ],
                ))
        return signals

    @classmethod
    def _scan_lint_signals(cls, workspace: str, source_files=None, test_files=None) -> List[GapSignal]:
        signals: List[GapSignal] = []
        total_errors = 0
        for xml_path in cls._iter_files(workspace, exts=(".xml",), include_build=True):
            low = xml_path.lower()
            if "lint" not in low:
                continue
            content = cls._read_bounded(xml_path)
            if not content or "<issue" not in content:
                continue
            try:
                root = ET.fromstring(content)
            except Exception:
                continue
            for issue in root.iter("issue"):
                if (issue.get("severity") or "").lower() in ("error", "fatal"):
                    total_errors += 1

        if total_errors:
            signals.append(GapSignal(
                area="lint",
                title="정적 분석(Lint) Error 0건 달성",
                detail=f"Lint 리포트에서 Error/Fatal {total_errors}건이 감지되었습니다.",
                evidence=f"lint-results XML: errors={total_errors}",
                severity="high",
                value=0.85, risk=0.2, cost=0.3,
                actions=[
                    "Lint Error 항목별 원인 분석 및 수정",
                    "신규 경고 유입 없이 Error 0건 재검증",
                ],
            ))
        return signals

    @classmethod
    def _scan_crash_signals(cls, workspace: str, source_files=None, test_files=None) -> List[GapSignal]:
        hits = []
        for path in cls._iter_files(
            workspace,
            exts=(".log", ".txt", ".out", ".err"),
            name_filter=lambda f: any(k in f.lower() for k in ("log", "crash", "stderr", "error", "logcat")),
        ):
            content = cls._read_bounded(path)
            if not content:
                continue
            for pat in _CRASH_PATTERNS:
                if pat.lower() in content.lower():
                    hits.append((os.path.relpath(path, workspace), pat))
                    break
            if len(hits) >= 5:
                break

        if hits:
            sample = ", ".join(f"{p} ({pat})" for p, pat in hits[:3])
            return [GapSignal(
                area="crash",
                title="런타임 크래시/예외 로그 근본 원인 제거",
                detail=f"크래시/예외 시그니처가 담긴 로그 {len(hits)}건이 감지되었습니다.",
                evidence=sample,
                severity="high",
                value=0.98, risk=0.25, cost=0.4,
                actions=[
                    "크래시 스택트레이스 근본 원인 규명 및 방어 코드 추가",
                    "재현 시나리오 회귀 테스트 고정",
                    "동일 로그 시그니처 0건 재검증",
                ],
            )]
        return []

    @classmethod
    def _scan_stub_signals(cls, workspace: str, source_files=None, test_files=None) -> List[GapSignal]:
        markers = 0
        stub_bodies = 0
        samples: List[str] = []

        for path in cls._iter_files(workspace, exts=_SOURCE_EXTS):
            rel = os.path.relpath(path, workspace)
            # Only skip genuine test sources (relative path), never the workspace root name.
            if "test" in rel.lower() or "spec" in os.path.basename(rel).lower():
                continue
            content = cls._read_bounded(path)
            if not content:
                continue
            found = _STUB_MARKER_RE.findall(content)
            bodies = _STUB_BODY_RE.findall(content)
            if found:
                markers += len(found)
                if len(samples) < 3:
                    samples.append(rel)
            if bodies:
                stub_bodies += len(bodies)
                if len(samples) < 3 and rel not in samples:
                    samples.append(rel)

        if markers or stub_bodies:
            severity = "high" if stub_bodies >= 3 else "medium"
            return [GapSignal(
                area="stub",
                title="미구현 스텁/TODO 잔재 청산 및 실구현 완결",
                detail=f"TODO/FIXME 마커 {markers}건, 미구현 스텁 본문 {stub_bodies}건이 남아 있습니다.",
                evidence=", ".join(samples) if samples else "소스 내 마커",
                severity=severity,
                value=0.75, risk=0.25, cost=0.45,
                actions=[
                    "미구현 스텁 본문을 실제 로직으로 대체",
                    "TODO/FIXME 항목별 이슈화 또는 해소",
                    "스텁/더미 반환 0건 재검증",
                ],
            )]
        return []
