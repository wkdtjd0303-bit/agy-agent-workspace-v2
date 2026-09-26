#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visual_craft_loop.py

AGY 4.x — !mode 3 (무인 자율 순환) 전용 시각 완성도 폐루프 (Visual Craftsmanship Loop).

문제 정의:
- Coder/QA/Auditor 파이프라인은 '기능이 동작하는가'는 기계적으로 증명하지만,
  '스마트폰을 손에 쥐었을 때 상용 앱처럼 보이는가'는 아무도 검증하지 않았다.
- 그 결과 화면 절반을 마케팅 히어로 카드가 점유하고, 액션 동선이 중복되며,
  카드가 3~4중 중첩되고, 48dp 미만 터치 타깃이 방치되는 'AI 슬롭' UI가 통과된다.

해결: !mode 1/2(사용자 개입)는 그대로 두고, !mode 3/3+(무인 자율)에만
'코드 작성 ➔ 렌더링/프레임버퍼 ➔ 시각 비평 ➔ 코드 수정' 사이클을 강제로 삽입한다.

4단계 결정적(Deterministic) 감사 + Vision-in-the-Loop:
  1. DESIGN CONTRACT (수치적 규격화)
     - 8pt(4pt 하프스텝) 그리드, 타이포 3단계, Primary 1종 + Neutral 계조,
       최소 터치 타깃, 썸존, 카드 중첩 한도, 첫 뷰포트 콘텐츠 비중을
       `reports/gate2/design_contract.json` 토큰으로 고정한다.
  2. STATIC AUDIT (소스 실측)
     - 마케팅 히어로/랜딩 구조, 그리드 이탈 여백, 타이포/팔레트 드리프트,
       터치 타깃 미달, 카드 중첩을 파일/라인 단위로 적발한다.
  3. RUNTIME GEOMETRY AUDIT (계층 실측)
     - Android UI Hierarchy XML(bounds/clickable)에서 실제 dp 크기와
       썸존(엄지 도달 영역) 위반을 계산한다.
  4. VISION CRITIQUE (스크린샷 실측)
     - 실 프레임버퍼를 멀티모달 Vision 모델에 전달하고, 계약 위반을
       [VISUAL_VERDICT]/[VISUAL_DEFECTS] 구조화 JSON 라인으로 환류한다.

산출물(물리적 증거):
- `reports/gate2/design_contract.json`          : 디자인 토큰 계약 (Coder/QA 프롬프트 주입용)
- `reports/gate5/visual_craft_review.json`      : 라운드별 시각 감사 증거 (Gate 5가 FAIL을 물리 검증)
- `reports/gate5/visual_craft_state.json`       : 라운드 이력 (동일 결함 반복 = stalled 감지)
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("VisualCraftLoop")

VISUAL_CRAFT_VERSION = 1
CONTRACT_REL_PATH = os.path.join("reports", "gate2", "design_contract.json")
EVIDENCE_REL_PATH = os.path.join("reports", "gate5", "visual_craft_review.json")
STATE_REL_PATH = os.path.join("reports", "gate5", "visual_craft_state.json")

# 시각 완성도 감사가 의미 있는 도메인 (EvidenceValidator.get_project_domain / 5W1H domain 표기 모두 허용)
VISUAL_DOMAINS = {
    "mobile_app", "android", "ios", "flutter", "react_native",
    "saas_web", "web", "frontend", "game",
}

BLOCKING_SEVERITIES = {"BLOCKER", "CRITICAL", "MAJOR"}

# ---------------------------------------------------------------------------
# 환경 설정 (기본 ON, 운영 중 즉시 비활성화 가능)
# ---------------------------------------------------------------------------
def _env_flag(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or default).strip().lower() not in ("0", "false", "off", "no")


def craft_loop_enabled() -> bool:
    return _env_flag("VISUAL_CRAFT_LOOP_ENABLED", "1")


def craft_vision_enabled() -> bool:
    return _env_flag("VISUAL_CRAFT_VISION_ENABLED", "1")


def craft_score_threshold() -> int:
    try:
        return int(os.getenv("VISUAL_CRAFT_SCORE_THRESHOLD", "80"))
    except Exception:
        return 80


def craft_max_rounds() -> int:
    try:
        return max(1, int(os.getenv("VISUAL_CRAFT_MAX_ROUNDS", "2")))
    except Exception:
        return 2


def craft_vision_timeout() -> float:
    try:
        return float(os.getenv("VISUAL_CRAFT_TIMEOUT", "150"))
    except Exception:
        return 150.0


# ---------------------------------------------------------------------------
# 디자인 계약 (Design Contract) — 수치적 규격화
# ---------------------------------------------------------------------------
SPACING_GRID_UNIT = 4          # 4pt 하프스텝까지 허용 (Material 8pt 그리드의 실무 표준)
TOUCH_TARGET_MIN_DP = 48
THUMB_ZONE_TOP_RATIO = 0.33    # 상단 33% = 보기 영역 (주요 조작 금지 권고)
FIRST_VIEWPORT_CONTENT_RATIO = 0.55
MAX_NESTED_CARD_DEPTH = 2
MAX_TYPOGRAPHY_STEPS = 6
MAX_PALETTE_COLORS = 12

MARKETING_PHRASE_PATTERNS = [
    r"지금\s*(?:바로\s*)?시작", r"시작해\s*보세요", r"환영합니다", r"최고의\s",
    r"완벽한\s", r"손쉽게\s", r"더\s*이상\s*고민", r"한\s*번에\s*해결", r"당신의\s",
    r"새로운\s*경험", r"놓치지\s*마세요", r"바로\s*시작하세요", r"Get\s+Started",
    r"Welcome\s+to", r"Sign\s+up\s+now", r"Try\s+it\s+free",
]

HERO_MARKER_PATTERNS = [
    r"\bhero\b", r"jumbotron", r"\blanding\b", r"\bbanner\b", r"\bpromo\b",
    r"intro[_\-]?section", r"온보딩\s*(?:배너|카드)",
]


def build_design_contract(domain: str = "mobile_app", app_name: str = "") -> Dict[str, Any]:
    """도메인별 디자인 토큰 계약을 수치로 고정한다 (LLM의 즉흥 여백/색상 남용 방지)."""
    is_mobile = domain in ("mobile_app", "android", "ios", "flutter", "react_native")
    return {
        "version": VISUAL_CRAFT_VERSION,
        "domain": domain,
        "app_name": app_name or "",
        "spacing": {
            "grid_unit_dp": SPACING_GRID_UNIT,
            "scale": [0, 4, 8, 12, 16, 24, 32, 40, 48, 64],
            "off_grid_tolerance": 4,  # 파일당 이 횟수 이상 이탈 시 결함
        },
        "typography": {
            "max_distinct_sizes": MAX_TYPOGRAPHY_STEPS,
            "scale": {
                "display": "1.5~2rem / 24~32sp, weight 700",
                "body": "0.95~1.05rem / 15~17sp, weight 400",
                "caption": "0.72~0.8rem / 11~13sp, weight 400~500",
            },
        },
        "color": {
            "max_distinct_colors": MAX_PALETTE_COLORS,
            "rule": "Primary 1종 + Neutral(gray-50~900) 계조. 임의의 파스텔/형광색 증식 금지.",
        },
        "touch_target": {
            "min_dp": TOUCH_TARGET_MIN_DP,
            "note": "아이콘 시각 크기가 작아도 터치 영역은 최소 규격을 확보한다.",
        },
        "ergonomics": {
            "thumb_zone_bottom_ratio": 1.0 - THUMB_ZONE_TOP_RATIO,
            "viewing_zone_top_ratio": THUMB_ZONE_TOP_RATIO,
            "rule": "주요 액션(추가/열기/저장)은 화면 하단 1/3(Thumb Zone) 또는 하단바/FAB에 배치.",
        },
        "layout": {
            "max_nested_card_depth": MAX_NESTED_CARD_DEPTH,
            "first_viewport_content_ratio_min": FIRST_VIEWPORT_CONTENT_RATIO,
            "rule": (
                "첫 화면은 마케팅 랜딩페이지가 아니라 작업 도구다. "
                "히어로/홍보 배너를 1차 콘텐츠 위에 두지 않는다."
            ),
        },
        "motion": {
            "duration_ms": "160~320ms",
            "easing": "cubic-bezier(0.16, 1, 0.3, 1)",
        },
    }


def _format_contract_block(contract: Dict[str, Any]) -> str:
    """Coder/QA 프롬프트용 고밀도 계약 요약 (토큰 다이어트: ~1,000자 이내)."""
    spacing = contract.get("spacing", {})
    typo = contract.get("typography", {})
    color = contract.get("color", {})
    touch = contract.get("touch_target", {})
    layout = contract.get("layout", {})
    return (
        "🎨 **[시각 완성도 계약 (Design Contract — GUI 프로젝트 필수)]**\n"
        f"- 여백/패딩: {spacing.get('grid_unit_dp', 4)}pt 그리드 {spacing.get('scale', [])} 만 사용 (임의값 금지)\n"
        f"- 타이포: 최대 {typo.get('max_distinct_sizes', 6)}단계 (Display/Body/Caption) — 크기 증식 금지\n"
        f"- 색상: Primary 1종 + Neutral 계조, 고유 색상 {color.get('max_distinct_colors', 12)}종 이하\n"
        f"- 터치: 최소 {touch.get('min_dp', 48)}dp 터치 타깃, 주요 액션은 화면 하단 Thumb Zone/FAB\n"
        f"- 레이아웃: 첫 화면은 '작업 도구' — 상단 마케팅 히어로/랜딩 배너 금지, "
        f"1차 콘텐츠(목록/작업 공간)가 첫 뷰포트의 {int(layout.get('first_viewport_content_ratio_min', 0.55) * 100)}% 이상\n"
        f"- 카드 중첩 {layout.get('max_nested_card_depth', 2)}중 초과 금지 — 컨테이너 대신 여백/구분선으로 구획\n"
        f"- 검증: `{EVIDENCE_REL_PATH}` (verdict=PASS) 가 Gate 5 통과 조건\n"
    )


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
@dataclass
class CraftFinding:
    rule_id: str
    severity: str            # BLOCKER | MAJOR | MINOR | INFO
    title: str
    evidence: str
    fix: str
    file: str = ""
    line: int = 0
    source: str = "static_source"   # static_source | ui_geometry | vision

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VisualCraftReport:
    workspace: str = ""
    domain: str = ""
    round_index: int = 1
    verdict: str = "SKIPPED"          # PASS | WARN | FAIL | SKIPPED
    score: Optional[int] = None
    findings: List[CraftFinding] = field(default_factory=list)
    vision_summary: str = ""
    measured: Dict[str, Any] = field(default_factory=dict)
    screenshots: List[str] = field(default_factory=list)
    stalled: bool = False
    engine: str = ""
    generated_at: float = field(default_factory=time.time)

    @property
    def blocking_findings(self) -> List[CraftFinding]:
        return [f for f in self.findings if f.severity in BLOCKING_SEVERITIES]

    @property
    def failing_rule_ids(self) -> List[str]:
        return sorted({f.rule_id for f in self.blocking_findings})

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["blocking_count"] = len(self.blocking_findings)
        payload["failing_rule_ids"] = self.failing_rule_ids
        return payload


# ---------------------------------------------------------------------------
# 파일 탐색 유틸
# ---------------------------------------------------------------------------
UI_FILE_EXTENSIONS = {
    ".kt", ".java", ".xml", ".css", ".scss", ".sass", ".less", ".html", ".htm",
    ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".dart",
}

SKIP_PATH_TOKENS = (
    os.sep + ".git" + os.sep, "node_modules", os.sep + ".venv" + os.sep,
    os.sep + "build" + os.sep, os.sep + "dist" + os.sep, "__pycache__",
    os.sep + "reports" + os.sep, os.sep + "tests" + os.sep, os.sep + "test" + os.sep,
    os.sep + ".gradle" + os.sep, os.sep + ".idea" + os.sep, os.sep + "Pods" + os.sep,
)

UI_CONTENT_MARKERS = (
    "@Composable", "androidx.compose", "<html", "<div", "<!doctype", "<template",
    "react", "className=", "styled.", ":root", "flutter/material.dart",
    "Widget build", "InkWell", "Modifier.", ":root {", "@media",
)


def _is_ui_source(rel_path: str, content: str) -> bool:
    lower = rel_path.lower()
    if not any(lower.endswith(ext) for ext in UI_FILE_EXTENSIONS):
        return False
    # .ts/.js 는 백엔드/스크립트일 수 있으므로 UI 마커가 있을 때만 스캔
    if lower.endswith((".ts", ".js")):
        return any(marker in content for marker in ("className=", "React", "react", "<div", "<span"))
    if lower.endswith((".kt", ".java", ".dart")):
        return any(marker in content for marker in ("@Composable", "Modifier.", "Widget build", "setContentView", "findViewById"))
    return any(marker in content for marker in UI_CONTENT_MARKERS) or lower.endswith((".css", ".scss", ".less", ".html", ".htm", ".vue", ".svelte", ".jsx", ".tsx", ".xml"))


def iter_ui_sources(workspace: str, max_files: int = 400, max_bytes: int = 400_000):
    """워크스페이스의 UI 소스 파일을 (rel_path, abs_path, content) 로 순회한다."""
    count = 0
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "build", "dist", "__pycache__", ".gradle", ".idea", "Pods", "reports")]
        for fname in sorted(files):
            if count >= max_files:
                return
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, workspace)
            if any(tok in os.sep + rel for tok in SKIP_PATH_TOKENS):
                continue
            try:
                if os.path.getsize(fpath) > max_bytes:
                    continue
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            if _is_ui_source(rel, content):
                count += 1
                yield rel, fpath, content


def latest_ui_source_mtime(workspace: str) -> float:
    """UI 소스코드의 최신 수정 시각 (프레임버퍼 신선도 판정용)."""
    newest = 0.0
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "build", "dist", "__pycache__", ".gradle", ".idea", "Pods", "reports")]
        for fname in files:
            if not any(fname.lower().endswith(ext) for ext in UI_FILE_EXTENSIONS):
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, fname)))
            except Exception:
                continue
    return newest


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _first_match_line(content: str, patterns: Sequence[str]) -> Tuple[str, int]:
    for pat in patterns:
        m = re.search(pat, content, re.IGNORECASE)
        if m:
            return m.group(0), _line_of(content, m.start())
    return "", 0


# ---------------------------------------------------------------------------
# 규칙 1~5: 소스 정적 감사
# ---------------------------------------------------------------------------
_ENTRY_FILE_RE = re.compile(r"(main|home|index|app|dashboard|landing|screen|welcome|onboard)", re.IGNORECASE)


def _rule_marketing_hero(rel: str, content: str, domain: str) -> List[CraftFinding]:
    """첫 화면을 마케팅 히어로/랜딩 배너가 점유하는 구조를 적발한다.

    오탐 방지를 위해 (a) hero/banner 마커 또는 (b) 엔트리/홈 화면 파일명 중 하나와,
    홍보성 카피 2건 이상이 동시에 성립할 때만 결함으로 판정한다.
    """
    hero_hit, hero_line = _first_match_line(content, HERO_MARKER_PATTERNS)
    is_entry_screen = bool(_ENTRY_FILE_RE.search(os.path.basename(rel)))
    if not hero_hit and not is_entry_screen:
        return []
    phrase_hits = sum(1 for pat in MARKETING_PHRASE_PATTERNS if re.search(pat, content, re.IGNORECASE))
    if phrase_hits < 2:
        return []
    marker = f"`{hero_hit}` (L{hero_line})" if hero_hit else f"엔트리 화면 `{os.path.basename(rel)}`"
    is_mobile = domain in ("mobile_app", "android", "ios", "flutter", "react_native")
    severity = "MAJOR" if is_mobile else "MINOR"
    return [CraftFinding(
        rule_id="MARKETING_HERO",
        severity=severity,
        title="첫 화면이 마케팅 히어로/랜딩 배너로 점유됨",
        evidence=f"`{rel}` {marker} + 홍보성 카피 {phrase_hits}건 감지",
        fix=("상단 히어로/홍보 배너를 제거하고, 1차 콘텐츠(최근 항목/작업 공간 목록)를 첫 뷰포트 상단에 배치하십시오. "
             "앱 기동 목적은 '도구 사용'이며 소개 페이지가 아닙니다. 신규 작업 유도는 FAB/상단바 1개 액션으로 단일화하십시오."),
        file=rel, line=hero_line, source="static_source",
    )]


def _extract_spacing_values(content: str) -> List[Tuple[int, int, str]]:
    """(line, value_px, raw) 목록 — 여백/패딩/간격 속성만 추출."""
    found: List[Tuple[int, int, str]] = []
    patterns = [
        r"(?:padding|margin|gap|row-gap|column-gap|padding-(?:top|bottom|left|right|horizontal|vertical)|margin-(?:top|bottom|left|right))\s*:\s*([^;{}\n]+)",
        r"(?:Modifier\.)?padding\(\s*([^)\n]*)",
        r"android:(?:layout_)?(?:padding|margin)[A-Za-z]*=\"([^\"]+)\"",
        r"EdgeInsets\.(?:all|symmetric|only|fromLTRB)\(([^)\n]*)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            fragment = m.group(1)
            for vm in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(px|dp|rem|rtl|pt)?", fragment):
                raw_num = float(vm.group(1))
                unit = (vm.group(2) or "").lower()
                if not unit and "android:" not in pat and "padding(" not in pat:
                    continue  # 단위 없는 값은 판정 보류 (오탐 방지)
                if unit in ("rem", "em"):
                    value = raw_num * 16.0
                else:
                    value = raw_num
                found.append((_line_of(content, m.start()), int(round(value)), vm.group(0).strip()))
    return found


def _rule_spacing_grid(rel: str, content: str, contract: Dict[str, Any]) -> List[CraftFinding]:
    tolerance = int(contract.get("spacing", {}).get("off_grid_tolerance", 4))
    values = _extract_spacing_values(content)
    off_grid = [(ln, v, raw) for ln, v, raw in values if v > 3 and v % SPACING_GRID_UNIT != 0]
    if len(off_grid) < tolerance:
        return []
    samples = ", ".join(f"{raw} (L{ln})" for ln, _, raw in off_grid[:3])
    return [CraftFinding(
        rule_id="SPACING_OFF_GRID", severity="MINOR",
        title=f"여백이 {SPACING_GRID_UNIT}pt 그리드를 이탈함 ({len(off_grid)}건)",
        evidence=f"`{rel}` — 예: {samples}",
        fix=f"여백/패딩/간격을 {contract.get('spacing', {}).get('scale', [])} 값으로만 정규화하십시오.",
        file=rel, line=off_grid[0][0],
    )]


def _rule_typography_drift(rel: str, content: str) -> List[CraftFinding]:
    sizes = set()
    for pat in [r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(px|rem)",
                r"fontSize\s*=\s*(\d+(?:\.\d+)?)\s*\.sp",
                r"android:textSize=\"(\d+(?:\.\d+)?)sp\"",
                r"fontSize\s*:\s*(\d+(?:\.\d+)?)"]:
        for m in re.finditer(pat, content, re.IGNORECASE):
            try:
                sizes.add(int(round(float(m.group(1)))))
            except Exception:
                continue
    if len(sizes) <= MAX_TYPOGRAPHY_STEPS:
        return []
    severity = "MAJOR" if len(sizes) > MAX_TYPOGRAPHY_STEPS + 3 else "MINOR"
    return [CraftFinding(
        rule_id="TYPOGRAPHY_DRIFT", severity=severity,
        title=f"폰트 크기 계층이 {len(sizes)}단계로 증식함",
        evidence=f"`{rel}` — 크기: {sorted(sizes)}",
        fix="Display/Body/Caption 3단계 스케일로 압축하고, 나머지는 동일 토큰으로 통합하십시오.",
        file=rel,
    )]


def _rule_palette_drift(rel: str, content: str) -> List[CraftFinding]:
    colors = set()
    for m in re.finditer(r"#([0-9a-fA-F]{6})\b", content):
        colors.add(m.group(1).lower())
    if len(colors) <= MAX_PALETTE_COLORS:
        return []
    severity = "MAJOR" if len(colors) > MAX_PALETTE_COLORS + 6 else "MINOR"
    return [CraftFinding(
        rule_id="PALETTE_DRIFT", severity=severity,
        title=f"하드코딩 색상이 {len(colors)}종으로 증식함",
        evidence=f"`{rel}` — 예: {['#' + c for c in sorted(colors)[:6]]}",
        fix="Primary 1종 + Neutral 계조 토큰(CSS 변수/ColorScheme/Theme)으로 통합하고 하드코딩 HEX를 제거하십시오.",
        file=rel,
    )]


_TOUCH_SMALL_RE = re.compile(r"(?:size|width|height|min-height|minHeight|layout_width|layout_height)\s*[=(:]?\s*(\d+(?:\.\d+)?)\s*(?:\.dp|dp|px)?", re.IGNORECASE)


def _rule_small_touch_target(rel: str, content: str) -> List[CraftFinding]:
    suspicious = []
    lines = content.splitlines()
    interactive_hint = re.compile(r"IconButton|clickable|Button\(|InkWell|GestureDetector|role=\"button\"|class=\"[^\"]*(?:btn|button|fab|action)", re.IGNORECASE)
    for idx, line in enumerate(lines):
        if not interactive_hint.search(line):
            continue
        window = "\n".join(lines[max(0, idx - 2): idx + 3])
        for m in _TOUCH_SMALL_RE.finditer(window):
            try:
                value = float(m.group(1))
            except Exception:
                continue
            if 16 <= value < 44:
                suspicious.append((idx + 1, int(value)))
                break
    if not suspicious:
        return []
    samples = ", ".join(f"{v}dp (L{ln})" for ln, v in suspicious[:3])
    return [CraftFinding(
        rule_id="SMALL_TOUCH_TARGET", severity="MAJOR",
        title=f"최소 터치 타깃({TOUCH_TARGET_MIN_DP}dp) 미달 위젯 {len(suspicious)}건",
        evidence=f"`{rel}` — {samples}",
        fix=f"아이콘 시각 크기는 유지하되, 터치 영역을 최소 {TOUCH_TARGET_MIN_DP}dp로 확장하십시오 (Modifier.size + padding / minWidth·minHeight).",
        file=rel, line=suspicious[0][0],
    )]


_CARD_PATTERNS = [
    r"<div[^>]*class=\"[^\"]*(?:card|panel|surface|tile|bento)[^\"]*\"",
    r"\bCard\s*\(",
    r"\bElevatedCard\s*\(",
    r"\bOutlinedCard\s*\(",
    r"\bSurface\s*\(",
    r"\bContainer\s*\(",
]


def _rule_nested_cards(rel: str, content: str, contract: Dict[str, Any]) -> List[CraftFinding]:
    max_depth = int(contract.get("layout", {}).get("max_nested_card_depth", MAX_NESTED_CARD_DEPTH))
    lines = content.splitlines()
    worst_line, worst_count = 0, 0
    for idx in range(len(lines)):
        window = "\n".join(lines[idx: idx + 25])
        count = sum(len(re.findall(pat, window)) for pat in _CARD_PATTERNS)
        if count > worst_count:
            worst_count, worst_line = count, idx + 1
    if worst_count <= max_depth:
        return []
    return [CraftFinding(
        rule_id="NESTED_CARDS", severity="MINOR",
        title=f"카드/컨테이너 중첩 과다 ({worst_count}개 컨테이너 근접)",
        evidence=f"`{rel}` L{worst_line} 부근 25줄 내 카드/박스 {worst_count}개",
        fix="큰 카드 안에 카드 타일을 다시 넣지 말고, 플랫 배경 + 타이포 위계 + 구분선(Divider) + 여백으로 구획하십시오.",
        file=rel, line=worst_line,
    )]


def audit_source_code(workspace: str, contract: Dict[str, Any], domain: str, max_findings: int = 12) -> List[CraftFinding]:
    """UI 소스코드 정적 감사 (결정적, 네트워크 불필요)."""
    findings: List[CraftFinding] = []
    for rel, _abs, content in iter_ui_sources(workspace):
        findings += _rule_marketing_hero(rel, content, domain)
        findings += _rule_small_touch_target(rel, content)
        findings += _rule_typography_drift(rel, content)
        findings += _rule_palette_drift(rel, content)
        findings += _rule_spacing_grid(rel, content, contract)
        findings += _rule_nested_cards(rel, content, contract)
        if len(findings) >= max_findings:
            break

    severity_rank = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 9), f.rule_id, f.file))
    # 동일 rule_id 중복은 대표 1건 + 카운트로 압축 (프롬프트 토큰 다이어트)
    compressed: List[CraftFinding] = []
    seen: Dict[str, int] = {}
    for f in findings:
        if f.rule_id in seen:
            seen[f.rule_id] += 1
            continue
        seen[f.rule_id] = 1
        compressed.append(f)
    for f in compressed:
        if seen.get(f.rule_id, 1) > 1:
            f.evidence += f" (외 {seen[f.rule_id] - 1}건)"
    return compressed[:max_findings]


# ---------------------------------------------------------------------------
# 규칙 6~7: 런타임 계층(UI Hierarchy) 기하 감사
# ---------------------------------------------------------------------------
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_android_hierarchy(xml_text: str) -> List[Dict[str, Any]]:
    """Android uiautomator XML에서 노드의 bounds/clickable/class/text를 추출한다."""
    nodes: List[Dict[str, Any]] = []
    for m in re.finditer(r"<node\b[^>]*/?>", xml_text):
        tag = m.group(0)
        bm = _BOUNDS_RE.search(tag)
        if not bm:
            continue
        x1, y1, x2, y2 = (int(v) for v in bm.groups())
        attrs = dict(re.findall(r'([\w:\-]+)="([^"]*)"', tag))
        nodes.append({
            "bounds": (x1, y1, x2, y2),
            "clickable": attrs.get("clickable", "false").lower() == "true",
            "class": attrs.get("class", ""),
            "text": attrs.get("text", ""),
            "resource_id": attrs.get("resource-id", ""),
        })
    return nodes


def find_ui_hierarchy_dump(workspace: str) -> Optional[str]:
    """워크스페이스 내 최신 Android UI Hierarchy XML 덤프를 찾는다."""
    candidates: List[str] = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "build", "__pycache__")]
        for fname in files:
            if not fname.lower().endswith(".xml"):
                continue
            lower = fname.lower()
            if not any(k in lower for k in ("ui_dump", "window_dump", "hierarchy", "uiautomator")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(4000)
                if "<hierarchy" in head and "bounds=" in head:
                    candidates.append(fpath)
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def detect_android_density(workspace: str = "") -> float:
    env_val = os.getenv("ANDROID_DENSITY", "").strip()
    if env_val:
        try:
            return float(env_val)
        except Exception:
            pass
    if workspace:
        for rel in ("reports/gate5/density.txt", "reports/density.txt"):
            fpath = os.path.join(workspace, rel)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        m = re.search(r"(\d+(?:\.\d+)?)", f.read())
                    if m:
                        return float(m.group(1))
                except Exception:
                    pass
    return 3.0  # Android xxhdpi 기본값 (1080x2400 디바이스)


def audit_ui_hierarchy(nodes: List[Dict[str, Any]], contract: Dict[str, Any], density: float = 3.0) -> Tuple[List[CraftFinding], Dict[str, Any]]:
    """실제 계층 bounds 기반 터치 타깃/썸존 감사."""
    findings: List[CraftFinding] = []
    clickable = [n for n in nodes if n["clickable"]]
    min_dp = float(contract.get("touch_target", {}).get("min_dp", TOUCH_TARGET_MIN_DP))
    small: List[Tuple[int, int]] = []
    for n in clickable:
        x1, y1, x2, y2 = n["bounds"]
        w_dp = (x2 - x1) / density
        h_dp = (y2 - y1) / density
        if min(w_dp, h_dp) < min_dp - 4:
            small.append((int(min(w_dp, h_dp)), 1))

    if small:
        smallest = sorted(small)[:3]
        findings.append(CraftFinding(
            rule_id="SMALL_TOUCH_TARGET", severity="MAJOR",
            title=f"실기기 터치 타깃 미달 {len(small)}개 위젯",
            evidence="최소 변 기준 " + ", ".join(f"{v}dp" for v, _ in smallest),
            fix=f"해당 위젯의 터치 영역을 최소 {int(min_dp)}dp로 확장하고 오터치를 방지하십시오.",
            source="ui_geometry",
        ))

    if nodes:
        max_y = max(n["bounds"][3] for n in nodes)
        if max_y > 0:
            top_clickable = [n for n in clickable if (n["bounds"][1] + n["bounds"][3]) / 2 < max_y * THUMB_ZONE_TOP_RATIO]
            if len(clickable) >= 5 and len(top_clickable) >= max(3, int(len(clickable) * 0.5)):
                findings.append(CraftFinding(
                    rule_id="THUMB_ZONE_OVERLOAD", severity="MINOR",
                    title="주요 조작 요소가 상단 보기 영역에 과밀집",
                    evidence=f"클릭 가능 {len(clickable)}개 중 {len(top_clickable)}개가 상단 {int(THUMB_ZONE_TOP_RATIO * 100)}% 구역에 위치",
                    fix="주요 액션(추가/열기/저장)은 화면 하단 1/3(Thumb Zone) 또는 하단바/FAB로 이동하십시오.",
                    source="ui_geometry",
                ))

    metrics = {
        "node_count": len(nodes),
        "clickable_count": len(clickable),
        "min_touch_dp": round(min([(min((n["bounds"][2] - n["bounds"][0]), (n["bounds"][3] - n["bounds"][1])) / density) for n in clickable], default=0.0), 1),
        "density": density,
    }
    return findings, metrics


# ---------------------------------------------------------------------------
# 스크린샷 물리 계측 + Vision 비평
# ---------------------------------------------------------------------------
DEFAULT_SCREENSHOT_DIRS = [
    os.path.join("reports", "gate5", "screenshots"),
    os.path.join("reports", "screenshots"),
    os.path.join("reports", "qa_screenshots"),
    "screenshots",
]


def find_screenshots(workspace: str, session_start_time: Optional[float] = None, max_shots: int = 4) -> List[str]:
    """Gate 5 실 프레임버퍼 스크린샷을 최신순으로 수집한다 (빈/검은 프레임 제외)."""
    found: List[str] = []
    seen_hashes = set()
    for rel_dir in DEFAULT_SCREENSHOT_DIRS:
        cdir = os.path.join(workspace, rel_dir)
        if not os.path.isdir(cdir):
            continue
        for fname in sorted(os.listdir(cdir)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            lower = fname.lower()
            if lower.startswith("00_") or "diff" in lower or "card" in lower or "storyboard" in lower:
                continue
            fpath = os.path.join(cdir, fname)
            if session_start_time is not None:
                try:
                    if os.path.getmtime(fpath) < (session_start_time - 1.0):
                        continue
                except Exception:
                    pass
            try:
                import hashlib
                with open(fpath, "rb") as f:
                    h = hashlib.md5(f.read()).hexdigest()
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
            except Exception:
                pass
            found.append(fpath)

    def _is_blank(path: str) -> bool:
        try:
            from PIL import Image
            from ui_visual_differ import UIVisualDiffer
            with Image.open(path) as img:
                return UIVisualDiffer.is_blank_frame(img)
        except Exception:
            return False

    found = [p for p in found if not _is_blank(p)]
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return found[:max_shots]


def discover_web_url(workspace: str) -> str:
    """배포/프리뷰 URL을 탐색한다 (자동 캡처용). 없으면 빈 문자열."""
    env_url = (os.getenv("VISUAL_CRAFT_CAPTURE_URL", "") or "").strip()
    if env_url:
        return env_url
    for rel in (
        os.path.join("reports", "gate5", "deploy_url.txt"),
        os.path.join("reports", "deploy_url.txt"),
        ".agy_web_url",
    ):
        fpath = os.path.join(workspace, rel)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    url = f.read().strip().splitlines()[0].strip()
                if url.startswith(("http://", "https://")):
                    return url
            except Exception:
                continue
    return ""


async def capture_web_screenshot(url: str, output_path: str, timeout_ms: int = 25000) -> Optional[str]:
    """Playwright 헤드리스로 실 브라우저 프레임버퍼를 캡처한다 (실패 시 None, 파이프라인 무중단).

    ※ Canvas/PIL 합성 금지 원칙 준수: 오직 실제 브라우저 렌더링 결과만 저장한다.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules or "unittest" in sys.modules:
        return None
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        logger.debug(f"Playwright unavailable for auto-capture: {e}")
        return None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3)
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                await page.wait_for_timeout(800)
                await page.screenshot(path=output_path, full_page=False)
            finally:
                await browser.close()
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 500:
            return os.path.abspath(output_path)
    except Exception as e:
        logger.debug(f"Web auto-capture skipped ({url}): {e}")
    return None


def measure_screenshot_metrics(image_path: str, bands: int = 3) -> Dict[str, Any]:
    """PIL 기반 물리 계측: 화면을 3개 밴드로 나눠 '잉크(콘텐츠) 비율'을 계산한다.

    Vision 모델의 눈대중 판정을 보정하기 위한 객관 수치로만 사용하며,
    단독으로 승인/반려를 결정하지 않는다 (오탐 방지).
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            gray = img.convert("L")
            w, h = gray.size
            small = gray.resize((min(w, 360), min(h, 720)), Image.Resampling.BILINEAR)
            sw, sh = small.size
            pixels = list(small.getdata())
            hist = small.histogram()
            total = sw * sh
            cumulative = 0
            bg = 255
            for i, cnt in enumerate(hist):
                cumulative += cnt
                if cumulative >= total * 0.5:
                    bg = i
                    break
            ratios = []
            band_h = max(1, sh // bands)
            for b in range(bands):
                y1, y2 = b * band_h, (sh if b == bands - 1 else (b + 1) * band_h)
                band_pixels = pixels[y1 * sw: y2 * sw]
                if not band_pixels:
                    ratios.append(0.0)
                    continue
                ink = sum(1 for p in band_pixels if abs(p - bg) > 28)
                ratios.append(round(ink / len(band_pixels), 4))
            return {
                "width": w, "height": h, "background_luma": bg,
                **{f"ink_ratio_band{i}": r for i, r in enumerate(ratios)},
                "top_ink_ratio": ratios[0] if ratios else 0.0,
                "mid_ink_ratio": ratios[1] if len(ratios) > 1 else 0.0,
            }
    except Exception as e:
        logger.debug(f"measure_screenshot_metrics skipped: {e}")
        return {}


def parse_vision_verdict(text: str, threshold: Optional[int] = None) -> Dict[str, Any]:
    """Vision 응답에서 [VISUAL_VERDICT]/[VISUAL_SCORE]/[VISUAL_DEFECTS] 를 결정적으로 파싱한다."""
    threshold = threshold if threshold is not None else craft_score_threshold()
    result: Dict[str, Any] = {"verdict": None, "score": None, "defects": [], "summary": ""}
    if not text:
        return result

    m_verdict = re.search(r"\[VISUAL_VERDICT\]\s*:?\s*(PASS|FAIL)", text, re.IGNORECASE)
    if m_verdict:
        result["verdict"] = m_verdict.group(1).upper()

    m_score = re.search(r"\[VISUAL_SCORE\]\s*:?\s*(\d{1,3})", text, re.IGNORECASE)
    if not m_score:
        m_score = re.search(r"(\d{1,3})\s*(?:/\s*100|점)", text)
    if m_score:
        try:
            score = int(m_score.group(1))
            if 0 <= score <= 100:
                result["score"] = score
        except Exception:
            pass

    defect_re = re.compile(
        r"^\s*[-•*]\s*(BLOCKER|CRITICAL|MAJOR|MINOR|WARNING|INFO)\s*\|\s*([A-Za-z0-9_\-]+)\s*\|\s*([^|\n]*)\|?\s*([^|\n]*)\|?\s*([^\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in defect_re.finditer(text):
        sev = m.group(1).upper()
        if sev == "WARNING":
            sev = "MINOR"
        result["defects"].append({
            "severity": sev,
            "rule_id": m.group(2).upper(),
            "location": m.group(3).strip(),
            "issue": m.group(4).strip(),
            "fix": m.group(5).strip(),
        })

    summary_line = ""
    m_sum = re.search(r"\[VISUAL_SUMMARY\]\s*:?\s*([^\n]+)", text, re.IGNORECASE)
    if m_sum:
        summary_line = m_sum.group(1).strip()
    result["summary"] = summary_line or text.strip().splitlines()[0][:200] if text.strip() else ""

    blocking = [d for d in result["defects"] if d["severity"] in BLOCKING_SEVERITIES]
    if result["verdict"] is None:
        if result["score"] is not None:
            result["verdict"] = "FAIL" if (result["score"] < threshold or blocking) else "PASS"
        else:
            neg = len(re.findall(r"카드\s*중첩|랜딩\s*페이지|히어로|여백\s*부족|썸존|터치\s*타깃|과밀|중복|정렬\s*붕괴|잘림|겹침", text))
            result["verdict"] = "FAIL" if neg >= 3 else ("WARN" if neg >= 1 else "PASS")
    return result


def _build_vision_rubric(contract: Dict[str, Any], domain: str, measured: Dict[str, Any], app_name: str) -> str:
    measured_txt = json.dumps(measured, ensure_ascii=False, indent=2) if measured else "{}"
    return (
        f"[검사 대상]: {app_name or contract.get('app_name') or domain} ({domain})\n"
        f"[디자인 계약(절대 기준)]:\n{json.dumps(contract, ensure_ascii=False, indent=2)}\n\n"
        f"[PIL 물리 계측치 — 눈대중 보정용]:\n```json\n{measured_txt}\n```\n\n"
        "위 스크린샷을 실사용자(엄지 한 손 조작) 관점에서 냉정하게 비평하십시오. "
        "다음 4대 반패턴을 최우선으로 색출하십시오:\n"
        "1. WEB_LANDING_STRUCTURE: 앱인데 첫 화면이 마케팅 히어로/소개 배너 중심이고, "
        "정작 1차 콘텐츠(목록/작업 공간)가 하단으로 밀리거나 잘림.\n"
        "2. DUPLICATE_ACTIONS: 같은 목적의 버튼(열기/시작/추가)이 서로 다른 위계로 중복 배치되어 동선 혼선.\n"
        "3. NESTED_CARDS_WHITESPACE: 카드 안 카드 3중 이상 중첩, 여백 호흡 부족, 회색 박스 남용.\n"
        "4. TOUCH_ERGONOMICS: 48dp 미만 터치 타깃, 상단 1/3에 주요 조작 과밀집, 텍스트 줄바꿈/잘림/겹침.\n\n"
        "JSON/설명 없이 아래 형식만 정확히 출력하십시오 (항목은 최대 8개):\n"
        "[VISUAL_VERDICT]: PASS 또는 FAIL\n"
        "[VISUAL_SCORE]: 0~100 사이 정수\n"
        "[VISUAL_SUMMARY]: 한 줄 총평 (30자 이내)\n"
        "[VISUAL_DEFECTS]:\n"
        "- MAJOR | MARKETING_HERO | 상단 40% 히어로 카드 | 감성 카피가 첫 화면을 점유해 도구 기동성 저하 | 히어로 제거 후 최근 문서 목록 상단 배치\n"
        "- MINOR | SPACING | 하단 툴바 | 버튼 간격이 8pt 그리드 이탈 | 8/16dp로 정규화\n"
    )


async def critique_screenshots(
    screenshots: Sequence[str],
    contract: Dict[str, Any],
    domain: str,
    measured: Dict[str, Any],
    app_name: str = "",
    max_images: int = 3,
) -> Dict[str, Any]:
    """실 프레임버퍼를 멀티모달 Vision 모델에 전달해 구조화 결함 리포트를 받는다."""
    result: Dict[str, Any] = {"engine": "", "verdict": None, "score": None, "defects": [], "summary": "", "raw": ""}
    if not screenshots or not craft_vision_enabled():
        return result
    # 테스트 실행 중에는 외부 Vision API 호출을 원천 차단한다 (쿼터 보호·결정적 테스트)
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules or "unittest" in sys.modules:
        result["engine"] = "skipped-unittest"
        return result
    rubric = _build_vision_rubric(contract, domain, measured, app_name)
    try:
        from vision_attachment_analyzer import VisionAttachmentAnalyzer
    except Exception as e:
        logger.debug(f"Vision engine unavailable: {e}")
        return result

    merged_defects: List[Dict[str, Any]] = []
    scores: List[int] = []
    verdicts: List[str] = []
    summaries: List[str] = []
    engine = ""
    for shot in list(screenshots)[:max_images]:
        try:
            res = await VisionAttachmentAnalyzer.analyze_image_file(shot, user_prompt=rubric, role="design_critique")
        except Exception as e:
            logger.debug(f"design_critique failed for {shot}: {e}")
            continue
        if not res or not res.get("success"):
            continue
        text = res.get("analysis") or ""
        parsed = parse_vision_verdict(text)
        engine = res.get("engine", engine)
        if parsed.get("score") is not None:
            scores.append(parsed["score"])
        if parsed.get("verdict"):
            verdicts.append(parsed["verdict"])
        if parsed.get("summary"):
            summaries.append(f"{os.path.basename(shot)}: {parsed['summary']}")
        for d in parsed.get("defects", []):
            d["screenshot"] = os.path.basename(shot)
            merged_defects.append(d)
        if not result.get("raw"):
            result["raw"] = text[:2000]

    if scores:
        result["score"] = min(scores)  # 가장 나쁜 화면 기준
    if verdicts:
        result["verdict"] = "FAIL" if "FAIL" in verdicts else ("WARN" if "WARN" in verdicts else "PASS")
    elif result["score"] is not None:
        result["verdict"] = "FAIL" if result["score"] < craft_score_threshold() else "PASS"
    result["defects"] = merged_defects
    result["summary"] = " / ".join(summaries[:3])
    result["engine"] = engine or "vision"
    return result


# ---------------------------------------------------------------------------
# 메인 파이프라인 (라운드 실행)
# ---------------------------------------------------------------------------
class VisualCraftLoop:
    """!mode 3/3+ 무인 자율 개발 전용 시각 완성도 폐루프 오케스트레이터."""

    # ---------- 공개 헬퍼 ----------
    @staticmethod
    def enabled() -> bool:
        return craft_loop_enabled()

    @staticmethod
    def is_visual_domain(domain: str) -> bool:
        return (domain or "").strip().lower() in VISUAL_DOMAINS

    @staticmethod
    def detect_domain(workspace: str, hint: str = "") -> str:
        hint = (hint or "").strip().lower()
        if hint in VISUAL_DOMAINS:
            return hint
        if workspace and os.path.isdir(workspace):
            try:
                from evidence_validator import EvidenceValidator
                dom = EvidenceValidator.get_project_domain(workspace)
                if dom == "android":
                    return "mobile_app"
                if dom == "web":
                    return "saas_web"
                if dom:
                    return dom
            except Exception:
                pass
            if any(os.path.isfile(os.path.join(workspace, f)) for f in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
                return "mobile_app"
            if any(os.path.isfile(os.path.join(workspace, f)) for f in ("package.json", "index.html", "vite.config.ts", "next.config.js")):
                return "saas_web"
        return "cli_agent"

    @classmethod
    def ensure_design_contract(cls, workspace: str, domain: str = "", app_name: str = "", force: bool = False) -> Dict[str, Any]:
        """디자인 토큰 계약을 `reports/gate2/design_contract.json`에 물리적으로 고정한다."""
        dom = cls.detect_domain(workspace, domain)
        if not workspace or not os.path.isdir(workspace):
            return build_design_contract(dom, app_name)
        path = os.path.join(workspace, CONTRACT_REL_PATH)
        if not force and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("version") == VISUAL_CRAFT_VERSION and cached.get("domain") == dom:
                    return cached
            except Exception:
                pass
        contract = build_design_contract(dom, app_name)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(contract, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"design contract write skipped: {e}")
        return contract

    @classmethod
    def get_contract_prompt_block(cls, workspace: str, domain: str = "", app_name: str = "") -> str:
        """Coder 프롬프트 주입용 계약 요약. (예방 단계)"""
        if not cls.enabled():
            return ""
        dom = cls.detect_domain(workspace, domain)
        if not cls.is_visual_domain(dom):
            return ""
        contract = cls.ensure_design_contract(workspace, dom, app_name)
        return "\n" + _format_contract_block(contract) + "\n"

    # ---------- 상태 (stall 감지) ----------
    @staticmethod
    def _load_state(workspace: str) -> Dict[str, Any]:
        path = os.path.join(workspace, STATE_REL_PATH)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"history": []}

    @classmethod
    def _update_state(cls, workspace: str, report: "VisualCraftReport") -> bool:
        """동일 결함 세트가 2라운드 연속 반복되면 stalled=True (무한 핑퐁 방지)."""
        state = cls._load_state(workspace)
        history = state.get("history", [])
        signature = report.failing_rule_ids
        stalled = bool(signature) and any(entry.get("failing") == signature for entry in history[-2:])
        history.append({
            "round": report.round_index,
            "ts": time.time(),
            "verdict": report.verdict,
            "failing": signature,
            "score": report.score,
        })
        state["history"] = history[-5:]
        try:
            path = os.path.join(workspace, STATE_REL_PATH)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"visual craft state write skipped: {e}")
        return stalled

    # ---------- 라운드 실행 ----------
    @classmethod
    async def run_round(
        cls,
        workspace: str,
        domain: str = "",
        round_index: int = 1,
        screenshots: Optional[Sequence[str]] = None,
        session_start_time: Optional[float] = None,
        app_name: str = "",
        persist: bool = True,
        use_vision: bool = True,
    ) -> VisualCraftReport:
        """1개 라운드 시각 감사: 계약 → 정적 감사 → 계층 감사 → Vision 비평 → 증거 기록."""
        dom = cls.detect_domain(workspace, domain)
        report = VisualCraftReport(workspace=workspace, domain=dom, round_index=round_index)
        if not cls.enabled() or not workspace or not os.path.isdir(workspace):
            report.verdict = "SKIPPED"
            report.engine = "disabled"
            return report

        contract = cls.ensure_design_contract(workspace, dom, app_name)
        findings: List[CraftFinding] = []
        measured: Dict[str, Any] = {}
        engine_parts = ["static"]

        try:
            findings += audit_source_code(workspace, contract, dom)
        except Exception as e:
            logger.debug(f"static visual audit failed: {e}")

        try:
            xml_path = find_ui_hierarchy_dump(workspace)
            if xml_path:
                with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
                    nodes = parse_android_hierarchy(f.read())
                density = detect_android_density(workspace)
                geo_findings, geo_metrics = audit_ui_hierarchy(nodes, contract, density)
                findings += geo_findings
                measured["ui_hierarchy"] = geo_metrics
                measured["ui_hierarchy_file"] = os.path.relpath(xml_path, workspace)
                engine_parts.append("geometry")
        except Exception as e:
            logger.debug(f"ui geometry audit failed: {e}")

        shots = list(screenshots) if screenshots is not None else find_screenshots(workspace, session_start_time)
        shots = [s for s in shots if os.path.isfile(s)]

        # 실 프레임버퍼가 없고 배포/프리뷰 URL이 확인되면 실제 브라우저 캡처로 보완한다.
        if not shots and dom in ("saas_web", "web", "frontend"):
            _url = discover_web_url(workspace)
            if _url:
                _auto_shot = os.path.join(
                    workspace, "reports", "gate5", "screenshots",
                    f"visual_craft_auto_{int(time.time())}.png",
                )
                _captured = await capture_web_screenshot(_url, _auto_shot)
                if _captured:
                    shots = [_captured]
                    measured["auto_captured_url"] = _url
                    engine_parts.append("playwright_capture")

        if shots:
            measured["screenshots"] = {
                os.path.basename(s): measure_screenshot_metrics(s) for s in shots[:3]
            }
            engine_parts.append("screenshot_metrics")
            # 프레임버퍼 신선도: 코드가 캡처 이후에 수정되었다면 시각 검증이 무효일 수 있음을 경고
            try:
                newest_ui = latest_ui_source_mtime(workspace)
                newest_shot = max(os.path.getmtime(s) for s in shots)
                if newest_ui and newest_shot < (newest_ui - 1.0):
                    findings.append(CraftFinding(
                        rule_id="STALE_FRAMEBUFFER", severity="MINOR",
                        title="최신 프레임버퍼가 코드 수정보다 오래됨",
                        evidence=(
                            f"최신 UI 소스 mtime({time.strftime('%H:%M:%S', time.localtime(newest_ui))}) > "
                            f"최신 스크린샷 mtime({time.strftime('%H:%M:%S', time.localtime(newest_shot))})"
                        ),
                        fix="최신 코드를 빌드/렌더링한 뒤 스크린샷을 다시 캡처하여 시각 검증을 갱신하십시오.",
                        source="screenshot_metrics",
                    ))
            except Exception:
                pass

        vision_result: Dict[str, Any] = {}
        if shots and use_vision and craft_vision_enabled():
            try:
                vision_result = await critique_screenshots(shots, contract, dom, measured.get("screenshots", {}), app_name)
                engine_parts.append("vision")
            except Exception as e:
                logger.debug(f"vision critique failed: {e}")

        for d in vision_result.get("defects", []) or []:
            sev = str(d.get("severity", "MINOR")).upper()
            if sev == "WARNING":
                sev = "MINOR"
            findings.append(CraftFinding(
                rule_id=(d.get("rule_id") or "VISION_DEFECT").upper(),
                severity=sev if sev in ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO") else "MINOR",
                title=(d.get("issue") or "Vision 결함")[:120],
                evidence=f"[Vision] {d.get('screenshot', '')} {d.get('location', '')}".strip(),
                fix=(d.get("fix") or "화면을 디자인 계약에 맞게 재구성하십시오.")[:400],
                source="vision",
            ))

        report.findings = findings
        report.measured = measured
        report.screenshots = shots[:4]
        report.vision_summary = vision_result.get("summary", "") or ""
        report.score = vision_result.get("score")
        report.engine = "+".join(engine_parts)

        blocking = report.blocking_findings
        vision_fail = vision_result.get("verdict") == "FAIL"
        if blocking or vision_fail:
            report.verdict = "FAIL"
        elif findings:
            report.verdict = "WARN"
        else:
            report.verdict = "PASS"

        report.stalled = cls._update_state(workspace, report)
        if persist:
            cls.record_evidence(workspace, report, contract, vision_result)
        return report

    @classmethod
    def record_evidence(
        cls,
        workspace: str,
        report: VisualCraftReport,
        contract: Optional[Dict[str, Any]] = None,
        vision_result: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Gate 5가 물리 검증하는 시각 감사 증거 JSON을 기록한다."""
        if not workspace or not os.path.isdir(workspace):
            return None
        path = os.path.join(workspace, EVIDENCE_REL_PATH)
        payload = report.to_dict()
        payload["contract_path"] = CONTRACT_REL_PATH
        payload["contract"] = contract or cls.ensure_design_contract(workspace, report.domain)
        payload["vision"] = {
            "engine": (vision_result or {}).get("engine", ""),
            "verdict": (vision_result or {}).get("verdict"),
            "summary": (vision_result or {}).get("summary", ""),
        }
        payload["directive"] = cls.build_fix_directive(report)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return path
        except Exception as e:
            logger.debug(f"visual craft evidence write skipped: {e}")
            return None

    # ---------- 판정/프롬프트 ----------
    @staticmethod
    def should_block_approval(report: Optional[VisualCraftReport]) -> bool:
        return bool(report and report.verdict == "FAIL" and not report.stalled)

    @staticmethod
    def format_prompt_block(report: Optional[VisualCraftReport], max_items: int = 5) -> str:
        """QA 프롬프트에 주입할 고밀도 시각 감사 블록."""
        if not report or report.verdict in ("SKIPPED",):
            return ""
        if report.verdict == "PASS" and not report.findings:
            return (
                "\n🎨 **[Visual Craft Loop — 시각 완성도 자율 감사]**: PASS "
                f"(vision score: {report.score if report.score is not None else 'N/A'}) — 시각 상용 품질 기준 충족.\n\n"
            )
        lines = [
            "\n🎨 **[Visual Craft Loop — 시각 완성도 자율 감사 (Mode 3 전용)]**: "
            f"판정 `{report.verdict}`"
            + (f" (vision score: {report.score}/100)" if report.score is not None else "")
            + (f" | ⚠️ 동일 결함 반복(stalled) — 무한 핑퐁 방지를 위해 Auditor 에스컬레이션 권고" if report.stalled else "")
            + "\n검출된 시각/인체공학 결함:"
        ]
        for idx, f in enumerate(report.findings[:max_items], 1):
            lines.append(f"{idx}. [{f.rule_id}/{f.severity}] {f.title} — {f.evidence} → 조치: {f.fix[:140]}")
        if report.blocking_findings:
            lines.append(
                "⚠️ **[QA 필수 지침]**: 위 MAJOR 이상 시각 결함이 해소되지 않은 상태에서는 "
                "`[STATUS: APPROVED]`를 선언할 수 없습니다. Coder에게 구체적 수정을 지시하십시오."
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def build_fix_directive(report: Optional[VisualCraftReport], max_items: int = 6) -> str:
        """Coder 재작업용 [VISUAL-CRAFT-FIX] 지시문."""
        if not report or not report.findings:
            return ""
        lines = [
            f"[🎨 VISUAL-CRAFT-FIX — 시각 완성도 자율 보강 지시 (Round {report.round_index}, 판정 {report.verdict})]",
            f"디자인 토큰 계약: `{CONTRACT_REL_PATH}` (여백 4/8pt 그리드 · 타이포 3단계 · Primary 1종 + Neutral 계조)",
            "아래는 실기기/스크린샷 실측 기반 결함입니다. 마케팅 문구 추가가 아니라 '구조와 수치'를 고치십시오:",
        ]
        for idx, f in enumerate(report.findings[:max_items], 1):
            loc = f" (`{f.file}`{f' L{f.line}' if f.line else ''})" if f.file else ""
            lines.append(f"{idx}. [{f.rule_id}/{f.severity}] {f.title}{loc}\n   - 근거: {f.evidence}\n   - 지시: {f.fix}")
        if report.stalled:
            lines.append("※ 동일 결함이 반복되었습니다. 땜질식 조건문 추가 금지 — 레이아웃 구조 자체를 재설계하십시오.")
        return "\n".join(lines)


def run_round_sync(*args, **kwargs) -> VisualCraftReport:
    """CLI/테스트 편의용 동기 래퍼."""
    import asyncio
    return asyncio.run(VisualCraftLoop.run_round(*args, **kwargs))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    ws = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    rep = run_round_sync(ws, round_index=1)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
