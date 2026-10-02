#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
planner_visual_gap_analyzer.py

👑 Commander 전용 **스크린샷 실측 제품 결손(Product Gap) 분석기**.

문제 정의:
- E2E 스크린샷은 지금까지 CODER/QA/Gate5(시각 결함 감사)의 검증 재료로만 쓰였다.
- 정작 기획 단계의 COMMANDER는 디스크의 소스/PRD/ROADMAP만 보고 다음 마일스톤을 정했다.
- 그 결과 "전체 E2E 시나리오 테스트 100% 무결점 통과" 같은, 실제로는 아무것도 개선하지
  않는 자기참조적(generic) 과제가 차기 마일스톤으로 둔갑하는 문제가 있었다.

해결:
- 실제 렌더링된 **E2E 스크린샷**을 멀티모달 Vision + 로컬 시각 증거(visual_craft_review.json,
  visual_qa.json)로 분석하여 "이 제품에 아직 없는 기능/패널/UX 흐름"을 구조화된 결손으로 뽑는다.
- 그 결과를 Commander 기획 프롬프트에 물리 증거로 주입하고, 일반론적 과제(generic plan)가
  결손을 덮어쓰지 못하도록 강제한다.
- 결과는 `reports/planner/visual_gap_review.json` 에 물리 증거로 남긴다.

이 모듈은 **읽기 전용**이다. 제품 소스코드를 수정하지 않는다.

핵심 API:
    PlannerVisualGapAnalyzer.enabled() -> bool
    PlannerVisualGapAnalyzer.collect_screenshots(workspace, min_mtime=None, max_shots=3) -> List[str]
    await PlannerVisualGapAnalyzer.analyze_for_planning(workspace, root_user_goal, domain="", ...) -> Optional[PlannerVisualGapReview]
    PlannerVisualGapAnalyzer.load_review(workspace) -> Optional[dict]
    PlannerVisualGapAnalyzer.format_prompt_block(review, max_items=6) -> str
    PlannerVisualGapAnalyzer.format_digest(review, max_items=6) -> str
    PlannerVisualGapAnalyzer.select_top_gap(review) -> Optional[dict]
    PlannerVisualGapAnalyzer.build_gap_tasks(review, limit=4) -> List[str]
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("PlannerVisualGapAnalyzer")

# ---------------------------------------------------------------------------
# 물리 증거 경로
# ---------------------------------------------------------------------------
REVIEW_REL_PATH = os.path.join("reports", "planner", "visual_gap_review.json")
VISUAL_CRAFT_REVIEW_REL = os.path.join("reports", "gate5", "visual_craft_review.json")
VISUAL_QA_REL = os.path.join("reports", "gate5", "visual_qa.json")

# 스크린샷 후보 디렉터리 (E2E 렌더링 프레임버퍼가 실제로 쌓이는 곳)
SCREENSHOT_DIRS = [
    os.path.join("reports", "gate5", "screenshots"),
    os.path.join("reports", "screenshots"),
    os.path.join("reports", "qa_screenshots"),
    os.path.join("reports", "e2e"),
    "screenshots",
    os.path.join("app", "build", "outputs", "roborazzi"),
]

# ---------------------------------------------------------------------------
# 분류/가중치
# ---------------------------------------------------------------------------
SEVERITY_RANK = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}
SEVERITY_WEIGHT = {"BLOCKER": 1.0, "CRITICAL": 0.9, "MAJOR": 0.72, "MINOR": 0.5, "INFO": 0.3}

CATEGORY_ALIASES = {
    "CORE_FEATURE": "CORE_FEATURE",
    "CORE": "CORE_FEATURE",
    "FEATURE": "CORE_FEATURE",
    "MISSING_FEATURE": "CORE_FEATURE",
    "FUNCTIONALITY": "CORE_FEATURE",
    "UX_WORKFLOW": "UX_WORKFLOW",
    "UX": "UX_WORKFLOW",
    "WORKFLOW": "UX_WORKFLOW",
    "INTERACTION": "UX_WORKFLOW",
    "LAYOUT_PANEL": "LAYOUT_PANEL",
    "LAYOUT": "LAYOUT_PANEL",
    "PANEL": "LAYOUT_PANEL",
    "STRUCTURE": "LAYOUT_PANEL",
    "ACCESSIBILITY": "ACCESSIBILITY",
    "A11Y": "ACCESSIBILITY",
    "TOUCH": "ACCESSIBILITY",
    "PERFORMANCE": "PERFORMANCE",
    "PERF": "PERFORMANCE",
    "POLISH": "POLISH",
    "VISUAL": "POLISH",
    "STYLE": "POLISH",
    "CONTENT": "POLISH",
}
CATEGORY_WEIGHT = {
    "CORE_FEATURE": 1.0,
    "UX_WORKFLOW": 0.82,
    "ACCESSIBILITY": 0.7,
    "LAYOUT_PANEL": 0.62,
    "PERFORMANCE": 0.6,
    "POLISH": 0.4,
}
CATEGORY_ORDER = ["CORE_FEATURE", "UX_WORKFLOW", "LAYOUT_PANEL", "ACCESSIBILITY", "PERFORMANCE", "POLISH"]

# visual_craft_loop rule_id → 결손 카테고리 매핑 (로컬 폴백용)
_RULE_CATEGORY = {
    "MARKETING_HERO": "UX_WORKFLOW",
    "WEB_LANDING_STRUCTURE": "UX_WORKFLOW",
    "DUPLICATE_ACTIONS": "UX_WORKFLOW",
    "SMALL_TOUCH_TARGET": "ACCESSIBILITY",
    "THUMB_ZONE_OVERLOAD": "ACCESSIBILITY",
    "TOUCH_ERGONOMICS": "ACCESSIBILITY",
    "SPACING_OFF_GRID": "LAYOUT_PANEL",
    "TYPOGRAPHY_DRIFT": "LAYOUT_PANEL",
    "PALETTE_DRIFT": "LAYOUT_PANEL",
    "NESTED_CARDS": "LAYOUT_PANEL",
    "NESTED_CARDS_WHITESPACE": "LAYOUT_PANEL",
    "STALE_FRAMEBUFFER": "POLISH",
    "VISION_DEFECT": "POLISH",
}

BLANK_NAME_TOKENS = ("diff", "card", "storyboard", "crop", "collage")


# ---------------------------------------------------------------------------
# 환경 스위치
# ---------------------------------------------------------------------------
def _env_flag(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or default).strip().lower() not in ("0", "false", "off", "no")


def planner_visual_gap_enabled() -> bool:
    """Commander 기획 단계의 스크린샷 결손 분석 ON/OFF (기본 ON)."""
    return _env_flag("COMMANDER_VISUAL_GAP_ENABLED", "1")


def planner_visual_gap_max_images() -> int:
    try:
        return max(1, int(os.getenv("COMMANDER_VISUAL_GAP_MAX_IMAGES", "3")))
    except Exception:
        return 3


def _is_test_env() -> bool:
    """pytest/unittest 실행 중에는 외부 Vision API 호출을 원천 차단한다 (쿼터 보호·결정성)."""
    import sys
    return (
        os.environ.get("UNITTEST_RUNNING") == "1"
        or "pytest" in sys.modules
        or "unittest" in sys.modules
    )


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
def _norm_category(raw: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_]", "", (raw or "").strip().upper()) or "POLISH"
    return CATEGORY_ALIASES.get(key, "POLISH" if key not in CATEGORY_WEIGHT else key)


def _norm_severity(raw: str) -> str:
    key = (raw or "").strip().upper()
    if key == "WARNING":
        key = "MINOR"
    if key == "ERROR":
        key = "MAJOR"
    return key if key in SEVERITY_RANK else "MINOR"


@dataclass
class PlannerGap:
    category: str          # CORE_FEATURE | UX_WORKFLOW | LAYOUT_PANEL | ACCESSIBILITY | PERFORMANCE | POLISH
    severity: str          # BLOCKER | CRITICAL | MAJOR | MINOR | INFO
    feature: str           # 결손 기능/패널 이름 (예: "레이어 패널")
    missing: str           # 화면에서 관찰된 부재 증거
    plan_hint: str         # 다음에 기획/구현해야 할 조치
    screenshot: str = ""
    source: str = "vision"  # vision | visual_craft | visual_qa

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def score(self) -> float:
        return SEVERITY_WEIGHT.get(self.severity, 0.3) * CATEGORY_WEIGHT.get(self.category, 0.4)


@dataclass
class PlannerVisualGapReview:
    workspace: str = ""
    domain: str = ""
    root_goal: str = ""
    verdict: str = "SKIPPED"       # GAPS_FOUND | NO_GAP | SKIPPED
    summary: str = ""
    next_milestone: str = ""
    gaps: List[PlannerGap] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    engine: str = ""
    generated_at: float = field(default_factory=time.time)

    @property
    def has_gaps(self) -> bool:
        return bool(self.gaps)

    def top_gap(self) -> Optional[PlannerGap]:
        if not self.gaps:
            return None
        return max(self.gaps, key=lambda g: (g.score, -SEVERITY_RANK.get(g.severity, 9)))

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["screenshot_names"] = [os.path.basename(s) for s in self.screenshots]
        payload["gap_count"] = len(self.gaps)
        return payload


# ---------------------------------------------------------------------------
# 스크린샷 수집
# ---------------------------------------------------------------------------
def _is_blank_frame(path: str) -> bool:
    try:
        from PIL import Image
        from ui_visual_differ import UIVisualDiffer
        with Image.open(path) as img:
            return UIVisualDiffer.is_blank_frame(img)
    except Exception:
        return False


def _looks_like_artifact(name: str) -> bool:
    lower = name.lower()
    if lower.startswith("00_"):
        return True
    return any(tok in lower for tok in BLANK_NAME_TOKENS)


def collect_screenshots(
    workspace: str,
    min_mtime: Optional[float] = None,
    max_shots: int = 3,
) -> List[str]:
    """실 렌더링 E2E 스크린샷을 최신순으로 수집한다 (빈 프레임/합성 카드/이전 라운드 제외)."""
    if not workspace or not os.path.isdir(workspace):
        return []

    found: List[str] = []
    seen_hashes = set()
    for rel_dir in SCREENSHOT_DIRS:
        cdir = os.path.join(workspace, rel_dir)
        if not os.path.isdir(cdir):
            continue
        for fname in sorted(os.listdir(cdir)):
            if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            if _looks_like_artifact(fname):
                continue
            fpath = os.path.join(cdir, fname)
            try:
                if os.path.getsize(fpath) < 1024:
                    continue
                if min_mtime is not None and os.path.getmtime(fpath) < (min_mtime - 1.0):
                    continue
                import hashlib
                with open(fpath, "rb") as f:
                    h = hashlib.md5(f.read()).hexdigest()
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
            except Exception:
                continue
            found.append(fpath)

    found = [p for p in found if not _is_blank_frame(p)]
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return found[:max(1, max_shots)]


# ---------------------------------------------------------------------------
# Vision 응답 파서
# ---------------------------------------------------------------------------
def parse_planner_gap_output(text: str) -> Dict[str, Any]:
    """Vision이 반환한 [PLAN_*] 구조화 라인을 결정적으로 파싱한다.

    출력 예시:
        [PLAN_GAP_SUMMARY]: 다크 테마 편집기는 갖췄지만 레이어/선택/색상 관리가 없다.
        [PLAN_NEXT_MILESTONE]: 레이어 패널 및 레이어 연산 기반
        [PLAN_GAPS]:
        - MAJOR | CORE_FEATURE | 레이어 패널 | 상단에 '1개 레이어' 텍스트만 존재 | 레이어 추가/삭제/숨김/투명도/블렌드 패널 기획
    """
    result: Dict[str, Any] = {"summary": "", "next_milestone": "", "gaps": []}
    if not text:
        return result

    m_sum = re.search(r"\[PLAN_GAP_SUMMARY\]\s*:?\s*([^\n]+)", text, re.IGNORECASE)
    if m_sum:
        result["summary"] = m_sum.group(1).strip()

    m_next = re.search(r"\[PLAN_NEXT_MILESTONE\]\s*:?\s*([^\n]+)", text, re.IGNORECASE)
    if m_next:
        result["next_milestone"] = m_next.group(1).strip()

    line_re = re.compile(r"^\s*[-•*]\s*", re.MULTILINE)
    valid_sev = {"BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO", "WARNING", "ERROR"}
    for raw_line in text.splitlines():
        if not line_re.match(raw_line):
            continue
        body = line_re.sub("", raw_line, count=1).strip()
        parts = [p.strip() for p in body.split("|", 4)]
        if len(parts) < 4:
            continue
        if parts[0].upper() not in valid_sev:
            continue
        severity = _norm_severity(parts[0])
        category = _norm_category(parts[1])
        feature = parts[2]
        missing = parts[3]
        hint = parts[4] if len(parts) > 4 else ""
        if not feature:
            continue
        result["gaps"].append(PlannerGap(
            category=category,
            severity=severity,
            feature=feature,
            missing=missing,
            plan_hint=hint or f"{feature} 구현 및 사용자 워크플로 연결",
            source="vision",
        ))

    # 구조화 라인이 없으면 산문에서 카테고리 키워드 기반으로 최소 1건 복원 (fail-soft)
    if not result["gaps"] and text.strip():
        result["summary"] = result["summary"] or text.strip().splitlines()[0][:200]
        if re.search(r"없|부재|부족|missing|absent|lacks", text, re.IGNORECASE):
            result["gaps"].append(PlannerGap(
                category="CORE_FEATURE",
                severity="MINOR",
                feature="미검증 결손",
                missing=text.strip()[:180],
                plan_hint="위 Vision 소견을 참고하여 단일 결손을 원자적으로 해소",
                source="vision",
            ))
    return result


# ---------------------------------------------------------------------------
# 로컬 시각 증거 → 결손 (Vision API 불가 시 결정적 폴백)
# ---------------------------------------------------------------------------
def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def gaps_from_visual_craft_review(workspace: str) -> Tuple[List[PlannerGap], str]:
    data = _read_json(os.path.join(workspace, VISUAL_CRAFT_REVIEW_REL))
    if not data:
        return [], ""
    gaps: List[PlannerGap] = []
    for finding in (data.get("findings") or []):
        if not isinstance(finding, dict):
            continue
        try:
            severity = _norm_severity(str(finding.get("severity", "MINOR")))
        except Exception:
            severity = "MINOR"
        rule_id = str(finding.get("rule_id", "VISION_DEFECT")).upper()
        category = _RULE_CATEGORY.get(rule_id, "POLISH")
        feature = str(finding.get("title") or rule_id).strip()[:80]
        fix = str(finding.get("fix") or "").strip()
        gaps.append(PlannerGap(
            category=category,
            severity=severity,
            feature=feature or rule_id,
            missing=str(finding.get("evidence") or "")[:200],
            plan_hint=fix or "디자인 계약에 맞게 구조를 재설계",
            source="visual_craft",
        ))
    return gaps, str(data.get("vision_summary") or data.get("verdict") or "")


def gaps_from_visual_qa(workspace: str) -> Tuple[List[PlannerGap], str]:
    data = _read_json(os.path.join(workspace, VISUAL_QA_REL))
    if not data:
        return [], ""
    verdict = str(data.get("verdict", "")).upper()
    avg = data.get("average_score")
    if verdict in ("PASS", "") and not (isinstance(avg, (int, float)) and avg < 80):
        return [], ""
    summary = str(data.get("summary", "")).strip()
    gaps: List[PlannerGap] = []
    if summary:
        gaps.append(PlannerGap(
            category="POLISH",
            severity="MAJOR" if verdict not in ("PASS", "WARN") else "MINOR",
            feature="화면 시각 완성도",
            missing=summary[:200],
            plan_hint="실기기 스크린샷에서 지적된 시각 결함을 구조적으로 해소",
            source="visual_qa",
        ))
    return gaps, summary


def _dedupe_gaps(gaps: Sequence[PlannerGap], limit: int = 12) -> List[PlannerGap]:
    seen = set()
    out: List[PlannerGap] = []
    for g in sorted(gaps, key=lambda x: (SEVERITY_RANK.get(x.severity, 9), x.feature)):
        key = (g.category, re.sub(r"\s+", "", g.feature)[:30].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(g)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Vision 루브릭
# ---------------------------------------------------------------------------
def build_planner_gap_rubric(root_goal: str, domain: str, app_name: str, screenshot_count: int) -> str:
    return (
        "[역할: 최고 제품 책임자 (Chief Product Officer)이자 수석 프로덕트 디자이너]\n"
        f"지금 보시는 것은 실제로 빌드/배포되어 렌더링된 '{app_name or domain}' 제품의 실기기/E2E "
        f"스크린샷입니다 (총 {screenshot_count}장 중 1장).\n"
        f"제품의 원래 목표(North Star): \"{root_goal or '사용자에게 실질적 가치를 주는 완성형 제품'}\"\n\n"
        "당신의 임무는 디자인 QA(깨짐/겹침 지적)가 아니라 **제품 기획 결손 진단**입니다. "
        "이 화면을 상용 최고 수준의 동종 앱(예: Photoshop/Figma/Notion 등)과 비교했을 때, "
        "'사용자가 당연히 기대하지만 아직 없는 기능·패널·단계'를 냉정하게 찾아내십시오.\n\n"
        "🚨 [집중 색출 대상 — 다음 기획에 바로 쓸 수 있어야 함]:\n"
        "1. CORE_FEATURE: 화면 텍스트/상태는 언급하지만 실제 UI/패널/도구가 없는 핵심 기능 (예: '1개 레이어' 텍스트만 있고 레이어 패널 부재)\n"
        "2. UX_WORKFLOW: 작업 흐름이 막히는 누락 단계 (예: 실행취소 아이콘만 있고 다단계 작업내역 목록 없음, 선택/크롭 도구 없음)\n"
        "3. LAYOUT_PANEL: 우측/좌측 패널 구조가 단일 기능만 담아 확장 불가 (예: 탭/아코디언 구조 부재, 속성 패널 없음)\n"
        "4. ACCESSIBILITY: 48dp 미만 터치 타깃, 상단 1/3 조작 과밀, 잘림/겹침\n"
        "5. PERFORMANCE / POLISH: 무거운 캔버스 내비게이터(미니맵) 부재, 빈 화면 상태(Empty State) 부재 등\n\n"
        "⚠️ 반드시 실제 스크린샷에 보이는 증거만 근거로 삼으십시오. 상상으로 없는 기능을 지어내지 마십시오.\n"
        "⚠️ 이미 화면에 존재하는 기능을 '없다'고 주장하지 마십시오.\n\n"
        "JSON/설명 없이 아래 형식만 정확히 출력하십시오 (결손은 가치 순 최대 6개):\n"
        "[PLAN_GAP_SUMMARY]: 한 줄 총평 (40자 이내)\n"
        "[PLAN_NEXT_MILESTONE]: 다음에 원자적으로 기획할 단 하나의 기능명 (명사형)\n"
        "[PLAN_GAPS]:\n"
        "- MAJOR | CORE_FEATURE | 레이어 패널 | 상단에 '1개 레이어' 텍스트만 있고 레이어 관리 UI 부재 | 레이어 추가/삭제/숨김/투명도/블렌드 모드 패널 기획\n"
        "- MINOR | LAYOUT_PANEL | 우측 패널 모듈화 | 우측 패널을 브러시 옵션이 단독 점유 | 탭/아코디언 구조로 재설계하여 레이어·색상·속성 수용\n"
    )


# ---------------------------------------------------------------------------
# 메인 분석기
# ---------------------------------------------------------------------------
class PlannerVisualGapAnalyzer:
    """Commander 기획 전용 스크린샷 실측 결손 분석기 (읽기 전용)."""

    @staticmethod
    def enabled() -> bool:
        return planner_visual_gap_enabled()

    # ---------- 영속화 ----------
    @staticmethod
    def review_path(workspace: str) -> str:
        return os.path.join(workspace, REVIEW_REL_PATH)

    @classmethod
    def load_review(cls, workspace: Optional[str]) -> Optional[Dict[str, Any]]:
        """디스크에 저장된 최신 결손 리뷰(JSON dict)를 읽는다. 없으면 None."""
        if not workspace:
            return None
        data = _read_json(cls.review_path(workspace))
        if not data:
            return None
        gaps = data.get("gaps")
        if not isinstance(gaps, list):
            data["gaps"] = []
        return data

    @classmethod
    def _persist(cls, review: PlannerVisualGapReview) -> Optional[str]:
        try:
            path = cls.review_path(review.workspace)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(review.to_dict(), f, ensure_ascii=False, indent=2)
            return path
        except Exception as e:
            logger.debug(f"planner gap review write skipped: {e}")
            return None

    # ---------- 신선도 ----------
    @classmethod
    def _cached_review_is_fresh(cls, cached: Dict[str, Any], workspace: str, screenshots: Sequence[str]) -> bool:
        try:
            generated_at = float(cached.get("generated_at") or 0.0)
        except Exception:
            return False
        if generated_at <= 0:
            return False
        # 리뷰 생성 이후 새 스크린샷이 생겼다면 무효 (이전 화면 재사용 금지)
        for shot in screenshots:
            try:
                if os.path.getmtime(shot) > generated_at + 1.0:
                    return False
            except Exception:
                continue
        return True

    # ---------- 분석 ----------
    @classmethod
    async def analyze_for_planning(
        cls,
        workspace: str,
        root_user_goal: str = "",
        domain: str = "",
        app_name: str = "",
        min_mtime: Optional[float] = None,
        max_images: Optional[int] = None,
        force: bool = False,
    ) -> Optional[PlannerVisualGapReview]:
        """
        실 렌더링 스크린샷을 분석하여 차기 마일스톤 후보가 될 제품 결손을 도출한다.

        - Vision API 사용 가능 + 실제 스크린샷 존재 → 멀티모달 비평
        - 그 외 → `visual_craft_review.json` / `visual_qa.json` 로컬 증거로 결정적 폴백
        - 스크린샷/증거가 전혀 없으면 None (초기 프로젝트에서 오탐 금지)
        """
        if not cls.enabled() or not workspace or not os.path.isdir(workspace):
            return None

        max_img = max_images or planner_visual_gap_max_images()
        screenshots = list(collect_screenshots(workspace, min_mtime=min_mtime, max_shots=max_img))

        cached = cls.load_review(workspace)
        if cached and not force and cls._cached_review_is_fresh(cached, workspace, screenshots):
            review = cls._from_dict(cached, workspace, domain, root_user_goal)
            logger.info(f"[PlannerVisualGap] 기존 스크린샷 결손 리뷰 재사용 (gaps={len(review.gaps)})")
            return review

        gaps: List[PlannerGap] = []
        engine_parts: List[str] = []
        summary = ""
        next_milestone = ""

        # 1) 로컬 시각 증거 (결정적, 네트워크 불필요)
        craft_gaps, craft_summary = gaps_from_visual_craft_review(workspace)
        if craft_gaps:
            gaps += craft_gaps
            engine_parts.append("visual_craft_review")
        qa_gaps, qa_summary = gaps_from_visual_qa(workspace)
        if qa_gaps:
            gaps += qa_gaps
            engine_parts.append("visual_qa")
        summary = craft_summary or qa_summary or ""

        # 2) 멀티모달 Vision (실 스크린샷이 있을 때만, 테스트 환경에서는 차단)
        if screenshots and not _is_test_env():
            try:
                from vision_attachment_analyzer import VisionAttachmentAnalyzer
                rubric = build_planner_gap_rubric(root_user_goal, domain, app_name, len(screenshots))
                vision_summaries: List[str] = []
                for shot in screenshots:
                    try:
                        res = await VisionAttachmentAnalyzer.analyze_image_file(
                            image_path=shot,
                            user_prompt=rubric,
                            role="planner_gap",
                        )
                    except Exception as e:
                        logger.debug(f"planner_gap vision failed for {shot}: {e}")
                        continue
                    if not res or not res.get("success"):
                        continue
                    parsed = parse_planner_gap_output(res.get("analysis") or "")
                    for g in parsed.get("gaps", []):
                        g.screenshot = os.path.basename(shot)
                        gaps.append(g)
                    if parsed.get("summary"):
                        vision_summaries.append(parsed["summary"])
                    if not next_milestone and parsed.get("next_milestone"):
                        next_milestone = parsed["next_milestone"]
                    if "vision" not in engine_parts:
                        engine_parts.append("vision")
                if vision_summaries:
                    summary = " / ".join(vision_summaries[:2])
            except Exception as e:
                logger.debug(f"planner_gap vision pipeline skipped: {e}")

        # 3) 아무 물리 증거도 없으면 None (스크린샷도, 시각 증거 파일도 없음)
        if not screenshots and not gaps:
            logger.debug("[PlannerVisualGap] 물리 시각 증거 없음 → 분석 생략")
            return None

        merged = _dedupe_gaps(gaps)
        top = merged[0] if merged else None
        if not next_milestone and top is not None:
            next_milestone = top.feature

        verdict = "GAPS_FOUND" if merged else "NO_GAP"
        if not summary:
            summary = "실 렌더링 화면에서 기획 결손이 발견되지 않았습니다." if not merged else (
                f"가장 시급한 결손은 '{top.feature}'입니다."
            )

        review = PlannerVisualGapReview(
            workspace=workspace,
            domain=domain,
            root_goal=root_user_goal,
            verdict=verdict,
            summary=summary,
            next_milestone=next_milestone,
            gaps=merged,
            screenshots=screenshots[:max_img],
            engine="+".join(engine_parts) if engine_parts else "screenshots",
        )
        # 테스트 환경에서는 NO_GAP 리뷰를 디스크에 남기지 않는다(테스트 격리). 결손이 실제로
        # 발견된 경우에는 회귀 테스트/재사용을 위해 항상 영속화한다.
        if merged or (screenshots and not _is_test_env()):
            cls._persist(review)
        logger.info(
            f"[PlannerVisualGap] 스크린샷 결손 분석 완료: verdict={verdict}, gaps={len(merged)}, "
            f"engine={review.engine}, top={top.feature if top else 'N/A'}"
        )
        return review

    @classmethod
    def _from_dict(cls, data: Dict[str, Any], workspace: str, domain: str, root_goal: str) -> PlannerVisualGapReview:
        gaps: List[PlannerGap] = []
        for item in (data.get("gaps") or []):
            if not isinstance(item, dict):
                continue
            gaps.append(PlannerGap(
                category=_norm_category(str(item.get("category", "POLISH"))),
                severity=_norm_severity(str(item.get("severity", "MINOR"))),
                feature=str(item.get("feature", "")).strip() or "미상 결손",
                missing=str(item.get("missing", "")),
                plan_hint=str(item.get("plan_hint", "")),
                screenshot=str(item.get("screenshot", "")),
                source=str(item.get("source", "vision")),
            ))
        return PlannerVisualGapReview(
            workspace=workspace,
            domain=domain or str(data.get("domain", "")),
            root_goal=root_goal or str(data.get("root_goal", "")),
            verdict=str(data.get("verdict", "GAPS_FOUND" if gaps else "NO_GAP")),
            summary=str(data.get("summary", "")),
            next_milestone=str(data.get("next_milestone", "")),
            gaps=gaps,
            screenshots=[str(s) for s in (data.get("screenshots") or [])],
            engine=str(data.get("engine", "cached")),
            generated_at=float(data.get("generated_at") or time.time()),
        )

    # ---------- 선택/변환 ----------
    @staticmethod
    def _as_gap(obj: Any) -> Optional[PlannerGap]:
        """dict / (구버전·reload된) dataclass 인스턴스 모두 PlannerGap 으로 정규화한다."""
        if obj is None:
            return None
        if isinstance(obj, PlannerGap):
            return obj
        if isinstance(obj, dict):
            return PlannerGap(
                category=_norm_category(str(obj.get("category", "POLISH"))),
                severity=_norm_severity(str(obj.get("severity", "MINOR"))),
                feature=str(obj.get("feature", "")).strip(),
                missing=str(obj.get("missing", "")),
                plan_hint=str(obj.get("plan_hint", "")),
                screenshot=str(obj.get("screenshot", "")),
                source=str(obj.get("source", "vision")),
            )
        # hot-reload 로 클래스 동일성이 깨진 dataclass 인스턴스도 duck-typing 으로 수용한다.
        if hasattr(obj, "feature"):
            return PlannerGap(
                category=_norm_category(str(getattr(obj, "category", "POLISH"))),
                severity=_norm_severity(str(getattr(obj, "severity", "MINOR"))),
                feature=str(getattr(obj, "feature", "")).strip(),
                missing=str(getattr(obj, "missing", "")),
                plan_hint=str(getattr(obj, "plan_hint", "")),
                screenshot=str(getattr(obj, "screenshot", "")),
                source=str(getattr(obj, "source", "vision")),
            )
        return None

    @classmethod
    def _extract_gaps(cls, review: Any) -> List[PlannerGap]:
        """dict / dataclass / 단일 결손 dict 어느 형태에서도 PlannerGap 목록을 뽑는다."""
        if not review:
            return []
        raw: Any = None
        if isinstance(review, dict):
            raw = review.get("gaps")
            if raw is None and review.get("feature"):
                raw = [review]
        else:
            raw = getattr(review, "gaps", None)
            if raw is None and hasattr(review, "feature"):
                raw = [review]
        if not isinstance(raw, (list, tuple)):
            return []
        return [g for g in (cls._as_gap(x) for x in raw) if g]

    @classmethod
    def select_top_gap(cls, review: Any) -> Optional[Dict[str, Any]]:
        """가장 가치 높은(심각도 x 카테고리 가중치) 단일 결손을 dict로 반환한다."""
        gaps = cls._extract_gaps(review)
        if not gaps:
            return None
        return max(gaps, key=lambda g: (g.score, -SEVERITY_RANK.get(g.severity, 9))).to_dict()

    @classmethod
    def build_gap_tasks(cls, review: Any, limit: int = 4) -> List[str]:
        """결손 리뷰 → 차기 마일스톤 태스크 목록 (원자적 단일 기능 + 방어/마감)."""
        gaps = cls._extract_gaps(review)
        if not gaps:
            return []

        gaps.sort(key=lambda g: (g.score, -SEVERITY_RANK.get(g.severity, 9)), reverse=True)
        tasks: List[str] = []
        for g in gaps:
            hint = (g.plan_hint or "").strip()
            entry = f"{g.feature}: {hint}" if hint else f"{g.feature} 구현 및 사용자 워크플로 연결"
            if entry and entry not in tasks:
                tasks.append(entry)
            if len(tasks) >= limit:
                break

        fallbacks = [
            "기존 검증 완료 기능의 회귀(Zero-Regression) 0건 방어",
            "엣지 케이스 및 예외 처리 방어 (비정상 입력/대용량/권한 오류 복원력)",
            "실사용자 워크플로 완결 (진입 → 조작 → 저장/내보내기까지 끊김 없이 동작)",
        ]
        for fb in fallbacks:
            if len(tasks) >= max(3, limit):
                break
            if fb not in tasks:
                tasks.append(fb)
        return tasks[:max(3, limit)]

    # ---------- 프롬프트/표시 ----------
    @classmethod
    def format_prompt_block(cls, review: Any, max_items: int = 6) -> str:
        """Commander 기획 프롬프트에 주입할 고밀도 결손 증거 블록. 결손 없으면 빈 문자열."""
        if not review:
            return ""
        gaps: List[PlannerGap] = cls._extract_gaps(review)
        if isinstance(review, dict):
            summary = str(review.get("summary", ""))
            next_ms = str(review.get("next_milestone", ""))
            engine = str(review.get("engine", ""))
            screenshots = [str(s) for s in (review.get("screenshots") or [])]
        else:
            summary = str(getattr(review, "summary", "") or "")
            next_ms = str(getattr(review, "next_milestone", "") or "")
            engine = str(getattr(review, "engine", "") or "")
            screenshots = [str(s) for s in (getattr(review, "screenshots", None) or [])]

        if not gaps:
            return ""

        gaps.sort(key=lambda g: (g.score, -SEVERITY_RANK.get(g.severity, 9)), reverse=True)
        names = ", ".join(os.path.basename(s) for s in screenshots[:4]) or "N/A"
        lines = [
            "🔬 **[실 렌더링 E2E 스크린샷 기반 제품 결손 분석 (Planner Visual Gap)]**",
            f"- 분석 대상 스크린샷: {names} (engine: {engine})",
            f"- 종합 진단: {summary or '실 화면에서 제품 공백이 검출됨'}",
            f"- 차기 원자적 마일스톤 후보: {next_ms or gaps[0].feature}",
            "검출된 제품 결손 (실사용자 체감 완성도 기준, 가치 순):",
        ]
        for idx, g in enumerate(gaps[:max_items], 1):
            lines.append(
                f"  {idx}. [{g.severity}/{g.category}] {g.feature} — {g.missing or '화면상 미구현'} "
                f"→ 조치: {g.plan_hint or '구현 기획'}"
            )
        lines.append(
            "※ 위 결손은 디자인 QA 지적이 아니라 '아직 기획/구현되지 않은 제품 기능 공백'입니다. "
            "이번 마일스톤은 가장 가치 높은 결손 1건을 원자적으로 해소해야 하며, "
            "'전체 E2E 통과/품질 하드닝' 같은 자기참조적 일반론으로 대체하는 것을 엄격히 금지합니다."
        )
        return "\n".join(lines)

    @classmethod
    def format_digest(cls, review: Any, max_items: int = 5) -> str:
        """Discord/5W1H 플랜 표시용 축약 다이제스트. 결손 없으면 빈 문자열."""
        block = cls.format_prompt_block(review, max_items=max_items)
        if not block:
            return ""
        # 프롬프트 지침 문장은 표시에서 제거하고 핵심만 남긴다.
        return block.split("※ 위 결손은")[0].strip()

    @classmethod
    def is_generic_validation_plan(cls, title: str, tasks: Optional[Sequence[str]] = None) -> bool:
        """'전체 E2E 시나리오 테스트 100% 통과'류의 자기참조적 일반론/하드닝 전용 과제인지 판정한다."""
        try:
            from product_value_planner import ProductValuePlanner
            return ProductValuePlanner.is_hardening_only_plan(title, tasks)
        except Exception:
            pass
        blob = f"{title or ''}\n" + "\n".join(tasks or [])
        markers = (
            "무결점 통과", "시나리오 테스트 100%", "회귀 검증", "품질 하드닝",
            "품질 안정화", "상용 배포 품질", "안정성 하드닝", "E2E 회귀",
            "하드닝", "경계 조건", "대용량 데이터", "크래시 방어",
            "응답 속도", "불필요한 연산",
        )
        return any(m in blob for m in markers)
