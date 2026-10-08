#!/usr/bin/env python3
"""
📍 RoadmapManager — Roadmap Ladder System (AGY 3.0)

사용자가 명시적 목표를 정하지 않아도, Commander가 프로젝트의 PRD/소스를 분석하여
ROADMAP.md 단계 목표 사다리를 자동 생성하고, QA PASS 시마다 다음 단계로 진화합니다.

핵심 설계:
  - QA PASS  → 현재 마일스톤 체크 완료 → 다음 마일스톤 목표로 Coder 지시
  - QA FAIL  → HOTFIX_MAX_ROUNDS 이내 핫픽스 수렴 (무한루프 불가)
  - ROADMAP 소진 → Commander가 새 마일스톤 자동 기획하여 ROADMAP.md 확장
"""

import os
import sys
import re
import json
import time
import asyncio
import logging
import config
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

# 🔒 순차 완결 잠금 SSOT (product_gate). 부재 시에도 import 실패 금지.
try:
    from product_gate import CLEARED_STATUSES, PASS_STATUSES, can_start_milestone, first_startable_index
except ImportError:  # pragma: no cover
    from agy_agent.product_gate import (  # type: ignore
        CLEARED_STATUSES, PASS_STATUSES, can_start_milestone, first_startable_index,
    )

logger = logging.getLogger("RoadmapManager")


# ──────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────

@dataclass
class RoadmapTask:
    """ROADMAP.md 내 개별 체크박스 항목"""
    text: str
    done: bool = False

    def to_md(self) -> str:
        mark = "x" if self.done else " "
        return f"- [{mark}] {self.text}"


@dataclass
class RoadmapMilestone:
    """ROADMAP.md 의 하나의 버전/단계 (## v1.2 - 검색 기능)"""
    version: str        # e.g. "v1.2"
    title: str          # e.g. "검색 기능"
    tasks: List[RoadmapTask] = field(default_factory=list)
    status: str = "PENDING"   # PENDING | IN_PROGRESS | DONE | SKIPPED
    id: str = ""                                    # e.g. "MS-1" (순차 잠금 식별자)
    exit_criteria: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    @property
    def is_done(self) -> bool:
        return self.status == "DONE" or (self.tasks and all(t.done for t in self.tasks))

    @property
    def is_pending(self) -> bool:
        return not self.is_done and self.status != "SKIPPED"

    @property
    def is_locked(self) -> bool:
        """PASS(DONE)된 마일스톤은 LOCK되어 임의 변경이 금지된다."""
        return str(self.status).upper() in PASS_STATUSES

    def mark_done(self):
        for t in self.tasks:
            t.done = True
        self.status = "DONE"

    def to_md(self) -> str:
        badge = {"DONE": " ✅", "IN_PROGRESS": " 🔄", "SKIPPED": " ⏭️"}.get(self.status, "")
        lines = [f"## {self.version} — {self.title}{badge}"]
        for task in self.tasks:
            lines.append(task.to_md())
        return "\n".join(lines)


# ──────────────────────────────────────────────
# Parser / Serializer
# ──────────────────────────────────────────────

def _parse_roadmap_md(content: str) -> List[RoadmapMilestone]:
    milestones: List[RoadmapMilestone] = []
    current: Optional[RoadmapMilestone] = None

    milestone_re = re.compile(
        r"^#{1,3}\s+(v[\d.]+(?:-[\w.]+)?)\s*[—\-–:]\s*(.+?)(?:\s*(?:✅|🔄|⏭️|\[DONE\]|\[SKIPPED\]|\[IN_PROGRESS\]))?$",
        re.IGNORECASE
    )
    task_re = re.compile(r"^\s*-\s*\[([xX\s])\]\s*(.+)$")
    exit_re = re.compile(r"^\s*-\s*✅\s*(.+)$")

    for line in content.splitlines():
        m = milestone_re.match(line.strip())
        if m:
            if current:
                milestones.append(current)
            version = m.group(1).strip()
            title = m.group(2).strip()
            status = "PENDING"
            if "✅" in line or "[DONE]" in line.upper():
                status = "DONE"
            elif "🔄" in line or "[IN_PROGRESS]" in line.upper():
                status = "IN_PROGRESS"
            elif "⏭️" in line or "[SKIPPED]" in line.upper():
                status = "SKIPPED"
            current = RoadmapMilestone(version=version, title=title, status=status)
            continue

        t = task_re.match(line)
        if t and current:
            done = t.group(1).strip().lower() == "x"
            text = t.group(2).strip()
            current.tasks.append(RoadmapTask(text=text, done=done))
            if current.status == "PENDING" and done:
                current.status = "IN_PROGRESS"
            continue

        e = exit_re.match(line)
        if e and current:
            current.exit_criteria.append(e.group(1).strip())

    if current:
        milestones.append(current)

    # 태스크 기반 DONE 재검증
    for ms in milestones:
        if ms.tasks and all(t.done for t in ms.tasks) and ms.status != "SKIPPED":
            ms.status = "DONE"

    # 순차 잠금 식별자 부여 (없으면 순번 기반)
    for idx, ms in enumerate(milestones):
        if not ms.id:
            ms.id = f"MS-{idx + 1}"

    return milestones


def _serialize_roadmap(milestones: List[RoadmapMilestone], preamble: str = "") -> str:
    parts = []
    if preamble:
        parts.append(preamble.rstrip())
        parts.append("")
    for ms in milestones:
        parts.append(ms.to_md())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ──────────────────────────────────────────────
# Capability Fingerprint (중복 기획 차단)
# ──────────────────────────────────────────────

# 마일스톤 제목/태스크에서 의미 없는 수식어. 역량 비교 시 제거한다.
_CAPABILITY_STOPWORDS = re.compile(
    r"(사용자|피드백|긴급|반영|차기|고도화|완성|완결|구현|검증|"
    r"최적화|개선|강화|보강|확보|정비|정합화|엔진|시스템|기능|처리|대응|지원|"
    r"phase|v\d+(?:\.\d+)?|req-\d+)",
    re.IGNORECASE,
)


def canonical_capability_tokens(text: str) -> set:
    """Normalizes a milestone title/task into a comparable capability token set.

    Removes version tags, [REQ-xxx] markers, and generic filler words so that
    '브랜드 리뉴얼(로고·앱명·컬러), 버튼 가독성' and
    '브랜드 리뉴얼(로고·앱명·컬러)·버튼 가독성' collapse to the same fingerprint.
    """
    s = str(text or "").lower()
    s = re.sub(r"\[[^\]]*\]", " ", s)              # strip [REQ-001] style tags
    s = _CAPABILITY_STOPWORDS.sub(" ", s)
    s = re.sub(r"[^0-9a-z가-힣]+", " ", s)          # keep alnum + hangul only
    return {t for t in s.split() if len(t) >= 2}


def capability_similarity(a: str, b: str, min_overlap: int = 3) -> float:
    """Jaccard similarity between two capability fingerprints.

    Returns 0.0 when fewer than `min_overlap` distinctive tokens are shared, which
    prevents short generic titles from being treated as duplicates.
    """
    ta, tb = canonical_capability_tokens(a), canonical_capability_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter < min_overlap:
        return 0.0
    return inter / float(len(ta | tb))


# ──────────────────────────────────────────────
# Default template (Commander 실패 시 폴백)
# ──────────────────────────────────────────────

_DEFAULT_ROADMAP_TEMPLATE = """\
# 🗺️ Project Roadmap (AGY Auto-Generated)
> 이 파일은 AGY Roadmap Ladder System이 자동 생성·관리합니다.
> QA 통과 시마다 자동으로 체크가 기록되고 다음 마일스톤으로 진화합니다.

## v1.0 — 핵심 기능 완성 및 안정화
- [ ] 주요 UI 화면 구현 완료
- [ ] 기본 CRUD 동작 검증
- [ ] 크래시 및 빌드 에러 0건

## v1.1 — UX 개선 및 피드백 반영
- [ ] 로딩 인디케이터 및 Empty State 적용
- [ ] 에러 메시지 및 예외 처리 강화

## v1.2 — 핵심 신기능 1차 확장
- [ ] 사용자 요청 신기능 #1 구현
- [ ] 기능 관련 E2E 테스트 통과

## v2.0 — 성능 최적화 및 고도화
- [ ] 렌더링 성능 프로파일링 및 병목 개선
- [ ] 메모리 누수 점검

## v2.1 — 추가 신기능 및 플랫폼 확장
- [ ] 사용자 요청 신기능 #2 구현
- [ ] 멀티 해상도 대응 검증

## v3.0 — 상용 배포 품질 달성
- [ ] 전 기능 회귀 테스트 PASS
- [ ] 릴리즈 빌드 및 스토어 제출 준비
"""


# ──────────────────────────────────────────────
# Domain Detection & Roadmap Alignment
# ──────────────────────────────────────────────

def _detect_domain(workspace: str) -> str:
    """Detects project domain from PRD.md, PROJECT_CONSTITUTION.md, or project files."""
    if not workspace or not os.path.isdir(workspace):
        return "saas"

    for doc in ["PROJECT_CONSTITUTION.md", "PRD.md", "README.md"]:
        doc_path = os.path.join(workspace, doc)
        if os.path.isfile(doc_path):
            try:
                with open(doc_path, encoding="utf-8", errors="ignore") as f:
                    txt = f.read().lower()
                    if any(k in txt for k in ["game", "mario", "arcade", "platformer", "pygame", "canvas game", "sprite"]):
                        return "game"
                    if any(k in txt for k in ["music", "audio", "sound", "synth", "webaudio", "tone.js", "작곡", "음악"]):
                        return "audio"
                    if any(k in txt for k in ["android", "apk", "compose", "gradle"]):
                        return "android"
                    if any(k in txt for k in ["bot", "daemon", "crawler", "scraper", "marketing", "discord"]):
                        return "daemon"
            except Exception:
                pass

    try:
        filenames = os.listdir(workspace)
        if any(f.endswith((".apk", ".gradle", ".gradle.kts")) or f in ["app", "gradlew"] for f in filenames):
            return "android"
        if any("bot" in f.lower() or "daemon" in f.lower() for f in filenames):
            return "daemon"
    except Exception:
        pass

    return "saas"


# ──────────────────────────────────────────────
# RoadmapManager
# ──────────────────────────────────────────────

class RoadmapManager:
    """
    프로젝트 워크스페이스의 ROADMAP.md를 읽고, 현재 마일스톤을 판단하고,
    QA 결과에 따라 자동으로 업데이트합니다.
    """

    ROADMAP_FILENAME = "ROADMAP.md"

    def __init__(self, workspace: str):
        self.workspace = os.path.abspath(workspace)
        self.roadmap_path = os.path.join(self.workspace, self.ROADMAP_FILENAME)
        self._milestones: List[RoadmapMilestone] = []
        self._preamble: str = ""
        self._loaded = False
        self._expansion_count: int = 0

    # ── I/O ──────────────────────────────────────

    def load(self) -> bool:
        if not os.path.isfile(self.roadmap_path):
            return False
        try:
            with open(self.roadmap_path, encoding="utf-8") as f:
                content = f.read()
            self._milestones = _parse_roadmap_md(content)
            first_ms = re.search(r"^#{1,3}\s+v[\d.]", content, re.MULTILINE)
            self._preamble = content[:first_ms.start()].rstrip() if first_ms else ""
            self._loaded = True
            logger.info(f"ROADMAP.md 로드: {len(self._milestones)}개 마일스톤")
            return True
        except Exception as e:
            logger.error(f"ROADMAP.md 파싱 실패: {e}")
            return False

    def save(self):
        try:
            content = _serialize_roadmap(self._milestones, self._preamble)
            with open(self.roadmap_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"ROADMAP.md 저장 완료")
        except Exception as e:
            logger.error(f"ROADMAP.md 저장 실패: {e}")

    # NOTE: `_generate_roadmap_from_prd` was intentionally removed.
    # [PRD=일기장 원칙] PRD의 REQ 목록은 더 이상 우선순위 사다리로 변환되지 않는다.
    # 차기 마일스톤은 항상 `_generate_roadmap_from_codebase` /
    # `_synthesize_codebase_aware_milestones` (디스크 코드 실사)에서 도출된다.

    def _workspace_has_source(self) -> bool:
        """True when the workspace actually contains production source files."""
        source_exts = (".kt", ".java", ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".vue", ".rs", ".go", ".cpp")
        try:
            for root, dirs, files in os.walk(self.workspace):
                dirs[:] = [d for d in dirs if d not in (
                    "build", ".gradle", ".git", "node_modules", "__pycache__", "dist", ".venv", "venv"
                ) and not d.startswith(".")]
                for fname in files:
                    if fname.lower().endswith(source_exts):
                        return True
        except Exception:
            pass
        return False

    def _generate_roadmap_from_codebase(self) -> Optional[str]:
        """Synthesizes the initial roadmap ladder from the REAL codebase on disk.

        [PRD=일기장 원칙] PRD의 REQ 목록은 사다리로 변환하지 않는다. 코드 실사만 사용한다.
        """
        if not self._workspace_has_source():
            return None
        templates = self._synthesize_codebase_aware_milestones([])
        if not templates:
            return None
        lines = [
            "# 🗺️ Project Roadmap (AGY Auto-Generated from Codebase Reality)",
            "> 이 파일은 AGY Roadmap Ladder System이 디스크 코드 실사를 기반으로 자동 생성·관리합니다.",
            "> PRD.md는 '무엇을 했는지'를 기록하는 실행 일기이며, 우선순위 결정에는 사용되지 않습니다.",
            "",
        ]
        for idx, (title, tasks) in enumerate(templates, start=0):
            lines.append(f"## v1.{idx} — {title}")
            for t in tasks:
                lines.append(f"- [ ] {t}")
            lines.append("")
        return "\n".join(lines)

    def ensure_exists(self) -> bool:
        """ROADMAP.md 없으면 '디스크 코드 실사' 기반(또는 기본 템플릿)으로 생성. True=새로 생성됨."""
        if os.path.isfile(self.roadmap_path):
            return False
        try:
            initial_content = self._generate_roadmap_from_codebase()

            if not initial_content:
                initial_content = _DEFAULT_ROADMAP_TEMPLATE

            with open(self.roadmap_path, "w", encoding="utf-8") as f:
                f.write(initial_content)
            logger.info(f"ROADMAP.md 생성 완료 (코드 실사 기반: {bool(initial_content != _DEFAULT_ROADMAP_TEMPLATE)})")
            self.load()
            return True
        except Exception as e:
            logger.error(f"ROADMAP.md 생성 실패: {e}")
            return False

    # ── Query ──────────────────────────────────────

    def _is_unlocked(self, index: int) -> bool:
        """🔒 순차 잠금: 선행 마일스톤이 모두 CLEARED(DONE/PASS/SKIPPED)여야 해금된다."""
        try:
            return can_start_milestone(self._milestones, index)
        except Exception:
            for prior in self._milestones[:index]:
                if str(prior.status).upper() not in CLEARED_STATUSES:
                    return False
            return True

    def get_current_milestone(self, auto_expand: bool = True, active_goal: str = "") -> Optional[RoadmapMilestone]:
        """IN_PROGRESS 또는 첫 번째 PENDING 마일스톤 반환 (🔒 순차 잠금 적용). 소진 시 자동 확장 지원.

        선행 마일스톤이 PASS(90점 완결 게이트)되기 전에는 다음 단계가 '잠금'되어
        구현 대상으로 선택될 수 없다 (A PASS → B UNLOCK).
        """
        if not self._loaded:
            self.load()
        # 🔒 순차 잠금: 해금된 IN_PROGRESS 우선
        for idx, ms in enumerate(self._milestones):
            if ms.status == "IN_PROGRESS" and self._is_unlocked(idx):
                return ms
        # 해금된 첫 PENDING
        for idx, ms in enumerate(self._milestones):
            if ms.is_pending and self._is_unlocked(idx):
                return ms
        if auto_expand and self.is_roadmap_exhausted():
            new_ms_list = self.auto_expand_roadmap(active_goal=active_goal)
            if new_ms_list:
                return new_ms_list[0]
        return None

    def is_roadmap_exhausted(self) -> bool:
        if not self._loaded:
            self.load()
        return bool(self._milestones) and all(not ms.is_pending for ms in self._milestones)

    def get_progress_summary(self) -> str:
        if not self._loaded:
            self.load()
        total = len(self._milestones)
        done = sum(1 for ms in self._milestones if ms.status == "DONE")
        current = self.get_current_milestone(auto_expand=False)
        cur_str = f" → 현재: **{current.version} {current.title}**" if current else " → 🎉 전체 완료!"
        return f"📍 로드맵 진행률: `{done}/{total}` 마일스톤 완료{cur_str}"

    def get_completed_milestones(self) -> List[RoadmapMilestone]:
        """Returns all completed milestones in the roadmap."""
        if not self._loaded:
            self.load()
        return [ms for ms in self._milestones if ms.is_done]

    def get_next_milestone_version(self) -> str:
        """
        Computes the next logical semantic version (e.g. v2.1 -> v2.2 or v3.0)
        based on the highest existing milestone version.
        """
        if not self._loaded:
            self.load()
        max_major = 1
        max_minor = 0
        for ms in self._milestones:
            m = re.match(r"^v?(\d+)(?:\.(\d+))?", ms.version, re.IGNORECASE)
            if m:
                maj = int(m.group(1))
                min_ = int(m.group(2)) if m.group(2) is not None else 0
                if (maj, min_) > (max_major, max_minor):
                    max_major, max_minor = maj, min_

        if max_minor < 2:
            return f"v{max_major}.{max_minor + 1}"
        else:
            return f"v{max_major + 1}.0"

    # ── Mutation ──────────────────────────────────────

    def auto_expand_roadmap(self, active_goal: str = "") -> List[RoadmapMilestone]:
        """
        ROADMAP.md의 모든 마일스톤이 완료/스킵되어 소진되었을 때,
        사용자 활성 목표(active_goal), 실제 디스크 결손 신호(GapSignalScanner),
        및 제품 완성도 심도 결손(ProductRefinementGovernor)을 기반으로 실체 마일스톤을 동적 생성합니다.
        (AutonomySafetyLayer Guard 8: 최대 5차 확장까지만 허용하여 무한 사다리 폭주 방지)
        """
        try:
            from autonomy_guard import AutonomySafetyLayer
            max_depth = getattr(AutonomySafetyLayer, "MAX_ROADMAP_EXPAND_DEPTH", 5)
        except Exception:
            max_depth = 5

        if getattr(self, "_expansion_count", 0) >= max_depth:
            logger.warning(
                f"🛑 [RoadmapManager] 로드맵 자동 확장 한도({self._expansion_count}/{max_depth}회) 도달! "
                "무한 사다리 루프 방지(Bounded Autonomy)를 위해 자동 확장을 중단합니다."
            )
            return []

        if not self._loaded:
            self.load()

        self._expansion_count = getattr(self, "_expansion_count", 0) + 1

        # Find the highest version currently in self._milestones
        max_major = 3
        max_minor = 0
        for ms in self._milestones:
            m = re.match(r"^v?(\d+)(?:\.(\d+))?", ms.version, re.IGNORECASE)
            if m:
                maj = int(m.group(1))
                min_ = int(m.group(2)) if m.group(2) is not None else 0
                if (maj, min_) > (max_major, max_minor):
                    max_major, max_minor = maj, min_

        domain = _detect_domain(self.workspace)
        logger.info(f"[RoadmapManager] Detected domain '{domain}' for auto-expansion")

        existing_titles = [ms.title for ms in self._milestones]
        disk_candidates: List[Tuple[str, List[str]]] = []

        # 0. 최우선 순위: 사용자 활성 목표 / 피드백 지시문 (Goal-Aware Expansion)
        clean_goal = (active_goal or "").strip()
        # Internal directive prefix guard (prevent automated prompts from being treated as feedback)
        if any(clean_goal.startswith(p) for p in ["🎯 [", "사용자 원본 목표:", "[무인 자율", "[🎯", "[📍", "[🔧", "👑 ["]):
            clean_goal = ""

        if clean_goal:
            try:
                from feedback_classifier import FeedbackIntentClassifier
                verdict = FeedbackIntentClassifier.classify(clean_goal, project_initialized=True)
                if verdict.is_feedback_directive:
                    from commander import Commander
                    fb_decomp = Commander.decompose_user_feedback(clean_goal, workspace=self.workspace, domain=domain)
                    fb_title = fb_decomp.get("title", "")
                    fb_tasks = fb_decomp.get("tasks", [])
                    if fb_title and fb_tasks and not any(fb_title in et for et in existing_titles):
                        disk_candidates.append((fb_title, fb_tasks))
                        logger.info(f"[RoadmapManager] Auto-expand prioritized active user goal feedback: {fb_title}")
            except Exception as e:
                logger.debug(f"[RoadmapManager] Goal decomposition notice during auto-expansion: {e}")

        # 1. 디스크 실측 결손 신호 (테스트 실패, Lint, 크래시, 스텁)
        try:
            from gap_signal_scanner import GapSignalScanner
            top_signal = GapSignalScanner.select_next(self.workspace, domain=domain)
            if top_signal and top_signal.title:
                tasks = list(top_signal.actions) if top_signal.actions else [top_signal.detail]
                if not any(top_signal.title in et for et in existing_titles) and not any(top_signal.title == dc[0] for dc in disk_candidates):
                    disk_candidates.append((top_signal.title, tasks))
                    logger.info(f"[RoadmapManager] Auto-expand prioritized GapSignal: {top_signal.title}")
        except Exception as e:
            logger.debug(f"[RoadmapManager] GapSignalScanner scan notice: {e}")

        # 1.5. 📸 스크린샷 실측 제품 결손 (Commander Planner Visual Gap) — 실 렌더링 화면에
        #      아직 없는 기능 공백을 코드 신호보다 먼저 로드맵 후보로 승격한다.
        try:
            from planner_visual_gap_analyzer import PlannerVisualGapAnalyzer
            vg_review = PlannerVisualGapAnalyzer.load_review(self.workspace)
            vg_tasks = PlannerVisualGapAnalyzer.build_gap_tasks(vg_review, limit=4) if vg_review else []
            vg_top = PlannerVisualGapAnalyzer.select_top_gap(vg_review) if vg_review else None
            if vg_top and vg_tasks:
                vg_title = f"스크린샷 실측 결손 해소: {vg_top.get('feature')}"
                if not any(vg_title in et for et in existing_titles) and not any(vg_title == dc[0] for dc in disk_candidates):
                    disk_candidates.append((vg_title, vg_tasks))
                    logger.info(f"[RoadmapManager] Auto-expand prioritized PlannerVisualGap: {vg_title}")
        except Exception as e:
            logger.debug(f"[RoadmapManager] PlannerVisualGap scan notice: {e}")

        # 2. 제품 완성도 심도 결손 (Interaction, State, A11y, Maintenance)
        try:
            from product_refinement_governor import ProductRefinementGovernor
            verdict = ProductRefinementGovernor.evaluate(self.workspace, domain=domain)
            if verdict and verdict.requires_depth and verdict.gap:
                gap = verdict.gap
                tasks = list(gap.actions) if gap.actions else [gap.detail]
                if not any(gap.title in et for et in existing_titles) and not any(gap.title == dc[0] for dc in disk_candidates):
                    disk_candidates.append((gap.title, tasks))
                    logger.info(f"[RoadmapManager] Auto-expand prioritized RefinementGap: {gap.title}")
        except Exception as e:
            logger.debug(f"[RoadmapManager] ProductRefinementGovernor scan notice: {e}")

        # 3. 실측 신호나 목표가 전혀 없을 때: 품질 하드닝 템플릿을 찍어내기 전에
        #    '아직 없는 사용자 체감 신규 기능'(제품 가치 기획)을 먼저 로드맵 후보로 승격한다.
        if not disk_candidates:
            try:
                from product_value_planner import ProductValuePlanner
                value_plans = ProductValuePlanner.select_roadmap_plans(
                    domain=domain,
                    workspace=self.workspace,
                    existing_titles=existing_titles,
                    limit=1,
                )
                for title, tasks in value_plans:
                    if not any(title in et or et in title for et in existing_titles):
                        disk_candidates.append((title, tasks))
                        logger.info(f"[RoadmapManager] Auto-expand prioritized ProductValue: {title}")
            except Exception as e:
                logger.debug(f"[RoadmapManager] ProductValuePlanner scan notice: {e}")

        # 3.1 실측 신호/사용자 가치 기능이 전혀 없을 때만: 단 1개의 품질 안정화 템플릿만 fallback으로 제공
        if not disk_candidates:
            available_templates = self._synthesize_codebase_aware_milestones(existing_titles)
            for tpl in available_templates:
                if not any(tpl[0] in et for et in existing_titles):
                    disk_candidates.append(tpl)
                    break

        new_milestones: List[RoadmapMilestone] = []
        cur_maj = max_major
        cur_min = max_minor

        for title, tasks in disk_candidates:
            if cur_min < 2:
                cur_min += 1
            else:
                cur_maj += 1
                cur_min = 0
            ver_str = f"v{cur_maj}.{cur_min}"
            task_objs = [RoadmapTask(text=t, done=False) for t in tasks]
            new_ms = RoadmapMilestone(
                version=ver_str,
                title=title,
                tasks=task_objs,
                status="PENDING"
            )
            new_milestones.append(new_ms)

        if new_milestones:
            self.append_milestones(new_milestones)
            self.sync_milestones_to_prd()
            logger.info(f"ROADMAP.md 자동 무한 확장 완료: {[ms.version for ms in new_milestones]} 추가됨")

        return new_milestones

    def _synthesize_codebase_aware_milestones(self, existing_titles: List[str]) -> List[Tuple[str, List[str]]]:
        """
        Dynamically synthesizes genuine, project-tailored next milestones by analyzing
        the real filesystem codebase (source files, directory hierarchy, manifests).
        Strictly prohibits generic B2B SaaS hallucinations (Webhook, multi-cloud storage, AI classifiers)
        unless the application is specifically a cloud integration daemon.
        """
        workspace = self.workspace or os.getcwd()
        exp_idx = getattr(self, "_expansion_count", 1)

        # 1. Inspect real production source files & keywords in workspace
        codebase_keywords = set()
        sample_files = []
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules", "build", "dist", "reports"))]
            rel_dir = os.path.relpath(root, workspace).replace("\\", "/")
            if "test" in rel_dir.lower():
                continue
            for fname in files:
                if fname.endswith((".kt", ".java", ".tsx", ".jsx", ".py", ".vue", ".html", ".cpp", ".rs")):
                    sample_files.append(os.path.join(rel_dir, fname))
                    # Tokenize filename into domain hints
                    parts = re.split(r'[^a-zA-Z0-9]+', os.path.splitext(fname)[0].lower())
                    for p in parts:
                        if len(p) >= 3 and p not in ("screen", "view", "model", "test", "activity", "component", "util", "helper"):
                            codebase_keywords.add(p)

        # Check domain indicators
        is_pdf_or_doc = any(k in codebase_keywords or "pdf" in f.lower() or "doc" in f.lower() for k in ("pdf", "reader", "viewer", "canvas", "page", "annotation") for f in sample_files[:20])
        is_game = any(k in codebase_keywords for k in ("game", "player", "stage", "sprite", "physics", "enemy", "level"))
        is_media = any(k in codebase_keywords for k in ("audio", "video", "player", "synth", "music", "sound"))

        # Domain-aware synthesis (Zero B2B SaaS Hallucination, Zero Phantom Feature Ladders, Pure Quality Hardening)
        if is_pdf_or_doc:
            return [
                (
                    "문서 뷰어 렌더링 파이프라인 최적화 & 메모리 캐싱 방어",
                    [
                        "LruCache 메모리 풀 및 비트맵 재사용 최적화로 OOM 예방",
                        "터치 제스처 반응성 튜닝 및 뷰포트 스크롤 60fps 유지",
                        "대용량 문서 렌더링 시 백그라운드 코루틴 디스패처 안정화"
                    ]
                ),
                (
                    "문서 파일 I/O 안정화 & 대용량 스트림 비동기 처리",
                    [
                        "대용량 파일 스트림 읽기/쓰기 비동기 파이프라인 분리",
                        "파일 손상 감지 시 자동 복구 및 안전 세이프가드 구축",
                        "저장 공간 부족 시 선제적 경고 및 임시 버퍼 정리"
                    ]
                ),
                (
                    "화면 회전 및 구성 변경 시 뷰포트 상태 보존 및 복구",
                    [
                        "화면 회전 및 액티비티 재생성 시 현재 읽기 위치 100% 보존",
                        "다크 모드 / 라이트 모드 전환 시 UI 테마 지연 없는 핫스왑",
                        "백그라운드 복귀 시 렌더링 컨텍스트 즉시 재개"
                    ]
                ),
                (
                    "제스처 반응성 정밀 튜닝 & 터치 지연 제로화",
                    [
                        "핀치 투 줌 및 팬 제스처 인터랙션 60fps 스무딩",
                        "더블 탭 확대/축소 모션 곡선 정밀 이징 적용",
                        "터치 이벤트 디스패치 레이턴시 16ms 이내 보장"
                    ]
                ),
                (
                    "문서 처리 전역 예외 처리 & 장애 격리 안전망 구축",
                    [
                        "포맷 파싱 에러 발생 시 크래시 없는 에러 안내 뷰 표출",
                        "구조화된 진단 로깅 및 비정상 종료 리포트 생성 체계 완비",
                        "비정상 종료 없는 연속 런타임 내구성 최종 검증"
                    ]
                )
            ]
        elif is_game:
            return [
                (
                    "사운드 오디오 믹싱 & 60fps 프레임 내구성 방어",
                    [
                        "절차적 타격 효과음 및 오디오 믹싱 레이턴시 최적화",
                        "모바일 터치 조작감 튜닝 및 가상 패드 햅틱 반응성",
                        "장기 실행 시 메모리 누수 0건 및 60fps 유지 최적화"
                    ]
                ),
                (
                    "가상 패드 입력 응답성 튜닝 & 햅틱 촉각 피드백 정밀화",
                    [
                        "멀티터치 터치 감도 튜닝 및 입력 래그 제로화",
                        "햅틱 피드백 패턴 다양화 및 진동 엔진 최적화",
                        "터치 영역 시각 피드백 및 사용자 정의 배치 지원"
                    ]
                ),
                (
                    "스프라이트 렌더링 메모리 풀 최적화 & OOM 방어",
                    [
                        "텍스처 아틀라스 메모리 캐싱 및 비트맵 인스턴스 재사용",
                        "가비지 컬렉션 스파이크 최소화 및 프레임 드롭 방어",
                        "화면 외 객체 컬링(Culling) 및 드로우콜 감소"
                    ]
                ),
                (
                    "물리 충돌 감지 연산 최적화 & 프레임 내구성 방어",
                    [
                        "공간 분할(Spatial Partitioning) 기반 충돌 연산 경량화",
                        "복합 콜라이더 교차 판정 정밀도 및 터널링 현상 방어",
                        "장기 실행 시 물리 시뮬레이션 안정성 확보"
                    ]
                ),
                (
                    "게임 루프 전역 예외 처리 & 데이터 자동 세이브 무결성",
                    [
                        "비정상 종료 시 게임 진행 상태 원자적 자동 저장",
                        "렌더링 스레드 예외 분리 및 크래시 방어",
                        "세이브 데이터 체크섬 무결성 검증 및 롤백 체계"
                    ]
                )
            ]
        else:
            return [
                (
                    "상용 서비스 품질 검증 & 크래시 0건 내구성 안정화",
                    [
                        "반응형 뷰포트 레이아웃 붕괴 0px 및 터치 타깃 최적화",
                        "백그라운드 메모리 누수 제로화 및 OOM 선제적 방어",
                        "전체 단위/통합 테스트 100% 통과 및 상용 릴리즈 패키징"
                    ]
                ),
                (
                    "런타임 리소스 누수 방어 & 비정상 종료 예외 처리 강화",
                    [
                        "전역 예외 핸들러 및 비정상 종료 복구 파이프라인 수립",
                        "메모리/네트워크 커넥션 누수 차단 및 가비지 수거 점검",
                        "장시간 실행 시 성능 저하 없는 안정적 상태 유지"
                    ]
                ),
                (
                    "입출력 데이터 무결성 보증 & 트랜잭션 장애 격리",
                    [
                        "데이터 저장소 원자적 트랜잭션 및 롤백 안전망 구축",
                        "불완전 I/O 쓰기 방어 및 데이터 손상 감지 검증",
                        "스토리지 접근 예외 시 사용자 친화적 복구 UI"
                    ]
                ),
                (
                    "사용자 인터랙션 성능 튜닝 & 프레임 드롭 제로화",
                    [
                        "주요 화면 렌더링 60fps 고정 및 UI 점프/버벅임 제거",
                        "비동기 태스크 디스패칭 분리로 메인 스레드 블로킹 방어",
                        "컴포넌트 리렌더링 최소화 및 연산 캐싱"
                    ]
                ),
                (
                    "전역 시스템 보안 감사 & 배포 릴리즈 무결성 패키징",
                    [
                        "민감 정보 유출 방지 및 보안 취약점 점검",
                        "릴리즈 바이너리 무결성 해시 검증 및 프로덕션 사이닝",
                        "배포 아티팩트 규격 준수 및 운영 배포 준비 완료"
                    ]
                )
            ]

    PRD_JOURNAL_HEADER = "## 📓 Execution Journal (실행 일기 — append-only)"

    def append_prd_journal_entry(
        self,
        version: str,
        title: str,
        status: str = "DONE",
        evidence: str = "",
        timestamp: Optional[str] = None,
    ) -> bool:
        """Appends one immutable entry to PRD.md's execution journal.

        [PRD=일기장 원칙] PRD는 '이전에 이런 걸 했다'를 남기는 일기장이다.
        우선순위/다음 작업 결정에는 절대 사용되지 않으며, 과거 기록을 덮어쓰지 않는다.
        """
        prd_path = os.path.join(self.workspace, "PRD.md")
        try:
            content = ""
            if os.path.isfile(prd_path):
                with open(prd_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

            marker = f"`{version}`"
            # Idempotent: never journal the same version+title twice.
            if marker in content and title[:20] in content:
                return False

            ts = timestamp or time.strftime("%Y-%m-%d %H:%M")
            entry = f"- **{ts}** {marker} [{status}] — {title}"
            if evidence:
                entry += f"\n  ↳ 증거: {evidence[:160]}"

            header = self.PRD_JOURNAL_HEADER
            if header not in content:
                content = (
                    content.rstrip()
                    + f"\n\n{header}\n"
                    + "> PRD는 '무엇을 했는지'를 기록하는 실행 일기입니다. 다음 작업 우선순위 결정에는 사용되지 않습니다.\n\n"
                    + entry + "\n"
                )
            else:
                m = re.search(rf"{re.escape(header)}.*?(?=\n## |\Z)", content, re.DOTALL)
                if m:
                    block = m.group(0).rstrip()
                    content = content[:m.start()] + block + "\n" + entry + "\n" + content[m.end():]
                else:
                    content = content.rstrip() + "\n" + entry + "\n"

            with open(prd_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[PRD Journal] 기록: {version} — {title}")
            return True
        except Exception as e:
            logger.debug(f"[PRD Journal] 기록 실패: {e}")
            return False

    def sync_milestones_to_prd(self):
        """
        [PRD.md & ROADMAP.md Bidirectional Sync]
        Ensures PRD.md reflects completed and active milestones so that QA and Auditor
        never suffer from sync drift or false rejection.
        PRD는 결과를 기록하는 일기장(append-only)이며, 우선순위 소스로 읽히지 않는다.
        """
        prd_path = os.path.join(self.workspace, "PRD.md")
        if not os.path.isfile(prd_path):
            return
        try:
            with open(prd_path, "r", encoding="utf-8", errors="ignore") as f:
                prd_content = f.read()

            # Mark completed REQs if found in completed milestones
            done_milestones = [ms for ms in self._milestones if ms.status == "DONE"]
            for ms in done_milestones:
                req_match = re.search(r'\[?(REQ-\d+)\]?', ms.title, re.IGNORECASE)
                if req_match:
                    req_id = req_match.group(1).upper()
                    prd_content = re.sub(
                        rf'-\s*\[\s*\]\s*(\*\*{req_id}\*\*)',
                        r'- [x] \1',
                        prd_content,
                        flags=re.IGNORECASE
                    )

            tracker_header = "## 🗺️ Milestone Evolution Tracker"
            tracker_lines = [tracker_header]
            for ms in self._milestones:
                mark = "x" if ms.status == "DONE" else ("🔄" if ms.status == "IN_PROGRESS" else " ")
                tracker_lines.append(f"- [{mark}] **{ms.version}**: {ms.title}")
            tracker_block = "\n".join(tracker_lines)

            if tracker_header in prd_content:
                prd_content = re.sub(
                    rf'{re.escape(tracker_header)}.*?(?=\n## |\Z)',
                    tracker_block + "\n",
                    prd_content,
                    flags=re.DOTALL
                )
            else:
                prd_content = prd_content.rstrip() + "\n\n" + tracker_block + "\n"

            with open(prd_path, "w", encoding="utf-8") as f:
                f.write(prd_content)
            logger.info("PRD.md successfully synchronized with latest ROADMAP milestones.")

            # 실행 일기(append-only): 완료된 마일스톤을 PRD에 영구 기록한다.
            for ms in done_milestones:
                self.append_prd_journal_entry(ms.version, ms.title, status="DONE")
        except Exception as e:
            logger.debug(f"PRD sync notice: {e}")

    def mark_current_milestone_done(self) -> Optional[RoadmapMilestone]:
        if not self._loaded:
            self.load()
        current = self.get_current_milestone(auto_expand=False)
        if not current:
            return None
        current.mark_done()
        self.save()
        self.sync_milestones_to_prd()
        logger.info(f"마일스톤 완료: {current.version} — {current.title}")

        if self.is_roadmap_exhausted():
            self.auto_expand_roadmap()

        return current

    def skip_current_milestone(self, reason: str = "") -> Optional[RoadmapMilestone]:
        if not self._loaded:
            self.load()
        current = self.get_current_milestone(auto_expand=False)
        if not current:
            return None
        current.status = "SKIPPED"
        if reason:
            current.tasks.append(RoadmapTask(text=f"⏭️ 스킵: {reason}", done=False))
        self.save()
        logger.warning(f"마일스톤 스킵: {current.version} — {reason}")

        if self.is_roadmap_exhausted():
            self.auto_expand_roadmap()

        return current

    def set_current_in_progress(self):
        if not self._loaded:
            self.load()
        current = self.get_current_milestone(auto_expand=True)
        if current and current.status == "PENDING":
            current.status = "IN_PROGRESS"
            self.save()

    def append_milestones(self, new_milestones: List[RoadmapMilestone]):
        self._milestones.extend(new_milestones)
        self.save()
        logger.info(f"{len(new_milestones)}개 새 마일스톤 추가")

    def find_equivalent_milestone(
        self,
        title: str,
        tasks: Optional[List[str]] = None,
        min_similarity: float = 0.6
    ) -> Optional[RoadmapMilestone]:
        """Returns an existing milestone whose capability fingerprint matches `title`.

        Prevents the same capability being appended twice under a slightly different
        version/title (e.g. v2.0 vs v2.1 of the identical '브랜드 리뉴얼 + PDF 원본 편집' scope).
        """
        if not self._loaded:
            self.load()

        combined = f"{title} {' '.join(tasks)}" if tasks else title
        best_ms: Optional[RoadmapMilestone] = None
        best_score = 0.0
        for ms in self._milestones:
            ms_text = f"{ms.title} {' '.join(t.text for t in ms.tasks)}"
            score = max(
                capability_similarity(combined, ms_text),
                capability_similarity(title, ms.title),
            )
            if score >= min_similarity and score > best_score:
                best_score, best_ms = score, ms
        return best_ms

    def is_capability_completed(self, title: str, tasks: Optional[List[str]] = None) -> bool:
        """True when an equivalent milestone already exists AND is marked DONE."""
        equivalent = self.find_equivalent_milestone(title, tasks)
        return bool(equivalent and equivalent.is_done)

    def reconcile_delivered_capabilities(self, is_delivered) -> List[RoadmapMilestone]:
        """Marks any not-done milestone DONE when `is_delivered(title, tasks)` proves
        the capability already exists on disk.

        This keeps the ROADMAP ledger honest ("진행했으면 진행했다고 체크") even when a
        previous approval round failed to tick the box, so the same work is never
        re-planned. `is_delivered` is a predicate supplied by the caller (Commander),
        which owns the domain-specific delivery evidence.
        """
        if not self._loaded:
            self.load()

        marked: List[RoadmapMilestone] = []
        for ms in self._milestones:
            if ms.is_done or ms.status == "SKIPPED":
                continue
            try:
                if is_delivered(ms.title, [t.text for t in ms.tasks]):
                    ms.mark_done()
                    marked.append(ms)
            except Exception as e:
                logger.debug(f"[RoadmapManager] delivery predicate notice: {e}")

        if marked:
            self.save()
            self.sync_milestones_to_prd()
            logger.info(
                f"[RoadmapManager] 디스크 실구현 확인 → 완료 체크: {[m.version for m in marked]}"
            )
        return marked

    def append_or_update_adaptive_milestone(
        self,
        version: str,
        title: str,
        tasks: List[str],
        status: str = "IN_PROGRESS"
    ) -> RoadmapMilestone:
        """
        [Adaptive Codebase Grounding Mandate]
        Appends or updates an adaptive milestone dynamically planned by Commander.
        Ensures all prior completed milestones are marked DONE.
        Idempotent: an already-completed equivalent capability is never resurrected
        nor duplicated as a new row (중복 기획/재작업 원천 차단).
        Persists to ROADMAP.md and syncs with PRD.md.
        """
        if not self._loaded:
            self.load()

        target_ms = None
        for ms in self._milestones:
            if ms.version.lower() == version.lower() or ms.title.lower() == title.lower():
                target_ms = ms
                break

        # Semantic capability dedup: same capability under a different version/title.
        if target_ms is None:
            target_ms = self.find_equivalent_milestone(title, tasks)

        task_objs = [RoadmapTask(text=t, done=(status == "DONE")) for t in tasks]

        if target_ms:
            # Never downgrade a completed milestone back to IN_PROGRESS.
            if target_ms.is_done and status != "DONE":
                logger.warning(
                    f"[RoadmapManager] 중복 기획 차단: '{title}' 는 이미 완료된 "
                    f"{target_ms.version} '{target_ms.title}' 과 동일 역량 → 완료 상태 유지(재작업 방지)."
                )
                self.save()
                self.sync_milestones_to_prd()
                return target_ms
            target_ms.title = title
            target_ms.tasks = task_objs
            target_ms.status = status
        else:
            # 🔒 순차 잠금: 선행 마일스톤이 '실제 완결(모든 태스크 완료)'된 경우에만 DONE으로 승격한다.
            #    부분 완성(50~80%) 상태의 마일스톤을 DONE으로 강제 승격하지 않는다 (병렬 부분 완성 금지).
            for prior_ms in self._milestones:
                if prior_ms.status == "IN_PROGRESS" and prior_ms.is_done:
                    prior_ms.status = "DONE"
                elif prior_ms.status == "IN_PROGRESS":
                    logger.warning(
                        f"[RoadmapManager] 🔒 순차 잠금: 선행 마일스톤 {prior_ms.version} '{prior_ms.title}' 가 "
                        f"아직 완결되지 않아 DONE 승격을 보류합니다 (다음 마일스톤은 대기 상태로 편입)."
                    )
            target_ms = RoadmapMilestone(
                version=version,
                title=title,
                tasks=task_objs,
                status=status
            )
            self._milestones.append(target_ms)

        self.save()
        self.sync_milestones_to_prd()
        logger.info(f"[RoadmapManager] Adaptive milestone synchronized: {version} — {title} ({status})")
        return target_ms

    # ── Prompt Builders ──────────────────────────────────────

    def build_evolve_prompt(self, project_context: str = "") -> str:
        """
        QA PASS 후 사용:
        현재 마일스톤을 DONE 처리하고, 다음 마일스톤 개발 지시 프롬프트 반환.
        로드맵 소진 시 자동으로 확장하여 무한 순환 보장.
        """
        if not self._loaded:
            self.load()

        completed = self.mark_current_milestone_done()
        next_ms = self.get_current_milestone(auto_expand=True)

        if not next_ms:
            self.auto_expand_roadmap()
            next_ms = self.get_current_milestone(auto_expand=True)

        if next_ms:
            next_ms.status = "IN_PROGRESS"
            self.save()

        completed_str = f"`{completed.version} — {completed.title}`" if completed else "이전 단계"
        next_ver_title = f"`{next_ms.version} — {next_ms.title}`" if next_ms else "차기 마일스톤"
        task_list = "\n".join(f"  - {t.text}" for t in next_ms.tasks) if next_ms and next_ms.tasks else "  - 신규 상용 기능 구현 및 UI 고도화"
        progress = self.get_progress_summary()

        prompt = (
            f"[📍 Roadmap Ladder — 자율 진화 다음 단계 지시]\n\n"
            f"✅ **완료된 단계**: {completed_str}\n"
            f"🚀 **다음 개발 목표**: {next_ver_title}\n\n"
            f"**[구현 태스크]:**\n{task_list}\n\n"
            f"{progress}\n\n"
            f"**[💡 피드백 루프 & 가치/위험도 사전 점검 (Value Pre-Check)]:**\n"
            f"1. **목표 부합성 평가**: 맹목적으로 사다리 항목을 찍어내지 말고, 현재 앱의 실제 상태와 핵심 도메인 목표에 진정으로 부합하는지 먼저 확인하십시오.\n"
            f"2. **자율 고도화 권한**: 이미 구현되었거나 프로젝트 성격에 맞지 않는 겉핥기식 기능인 경우, 실사용자 체감 가치를 극대화할 수 있는 방향으로 태스크를 능동 조정하여 구현하십시오.\n"
            f"3. **가시적 실체 구현**: 단순 주석/테스트 파일 추가가 아닌 실제 UI 컴포넌트, 인터랙션, 또는 핵심 비즈니스 로직을 소스코드에 실구현하고 빌드/배포를 완료하십시오.\n"
        )
        if next_ms:
            t_combined = (" ".join(t.text for t in next_ms.tasks) + " " + next_ms.title).lower()
            is_visual_target = (
                any(k in t_combined for k in ["에셋", "아이콘", "스플래시", "스프라이트", "배경", "이미지", "generate_image", "ux", "visual", "asset", "icon", "splash", "polish"])
                or next_ms.version in ("v2.0", "v2.1", "v2")
            )
            if is_visual_target:
                prompt += (
                    f"\n🎨 **[사령관 {config.COMMANDER_USER_MENTION} 공식 시각 에셋 생성 승인]**:\n"
                    f"- 본 마일스톤의 UI/UX 완성도 및 브랜드 품질 극대화를 위해 `generate_image` 호출을 정식 승인합니다.\n"
                    f"- **Rule 8 에셋 2원화 분류 체계 필수 준수**:\n"
                    f"  ① **Type A (풀컷 씬/일러스트)**: Splash, Title Hero, Background (9:16 또는 16:9 전체 배경 포함 구도)\n"
                    f"  ② **Type B (모듈러 독립 파츠)**: Joystick, Sprite, Button, Badge, Seal, Icon (1:1, 단일 객체, 투명/단색 배경)\n"
                    f"- 생성된 이미지는 프로젝트 리소스 폴더(`res/drawable/`, `assets/` 등)에 배치하고 UI 코드와 즉시 바인딩하십시오.\n"
                )
        if project_context:
            prompt += f"\n**[프로젝트 컨텍스트]:**\n{project_context[:800]}"
        return prompt

    def build_skip_and_evolve_prompt(self, skipped: RoadmapMilestone, reason: str = "", project_context: str = "") -> str:
        """마일스톤 스킵 후 차기 마일스톤 개발 지시 프롬프트."""
        if not self._loaded:
            self.load()

        next_ms = self.get_current_milestone(auto_expand=True)
        if not next_ms:
            self.auto_expand_roadmap()
            next_ms = self.get_current_milestone(auto_expand=True)

        if next_ms:
            next_ms.status = "IN_PROGRESS"
            self.save()

        next_ver_title = f"`{next_ms.version} — {next_ms.title}`" if next_ms else "차기 마일스톤"
        task_list = "\n".join(f"  - {t.text}" for t in next_ms.tasks) if next_ms and next_ms.tasks else "  - 신규 상용 기능 구현 및 UI 고도화"
        progress = self.get_progress_summary()

        skip_desc = f"사유: {reason}" if reason else "핫픽스 한도 초과 또는 수동 스킵"
        prompt = (
            f"[📍 Roadmap Ladder — 마일스톤 스킵 후 차기 개발 지시]\n\n"
            f"⏭️ 이전 마일스톤 `{skipped.version} — {skipped.title}` 단계는 스킵되었습니다 ({skip_desc}).\n"
            f"🚀 **신규 개발 목표**: {next_ver_title}\n\n"
            f"**[구현 태스크]:**\n{task_list}\n\n"
            f"{progress}\n\n"
            f"**[🚨 필수 구현 지침]:**\n"
            f"1. 실사용자가 앱/화면에서 즉시 체감할 수 있는 핵심 기능을 직접 구현하십시오.\n"
            f"2. 단순 주석/테스트 추가는 무효이며, UI 컴포넌트와 비즈니스 로직을 소스코드에 실구현하십시오.\n"
            f"3. 구현 완료 후 빌드/배포를 마치고 차기 E2E 검증을 준비하십시오.\n"
        )
        if next_ms:
            t_combined = (" ".join(t.text for t in next_ms.tasks) + " " + next_ms.title).lower()
            is_visual_target = (
                any(k in t_combined for k in ["에셋", "아이콘", "스플래시", "스프라이트", "배경", "이미지", "generate_image", "ux", "visual", "asset", "icon", "splash", "polish"])
                or next_ms.version in ("v2.0", "v2.1", "v2")
            )
            if is_visual_target:
                prompt += (
                    f"\n🎨 **[사령관 {config.COMMANDER_USER_MENTION} 공식 시각 에셋 생성 승인]**:\n"
                    f"- 본 마일스톤의 UI/UX 완성도 및 브랜드 품질 극대화를 위해 `generate_image` 호출을 정식 승인합니다.\n"
                    f"- **Rule 8 에셋 2원화 분류 체계 필수 준수**:\n"
                    f"  ① **Type A (풀컷 씬/일러스트)**: Splash, Title Hero, Background (9:16 또는 16:9 전체 배경 포함 구도)\n"
                    f"  ② **Type B (모듈러 독립 파츠)**: Joystick, Sprite, Button, Badge, Seal, Icon (1:1, 단일 객체, 투명/단색 배경)\n"
                    f"- 생성된 이미지는 프로젝트 리소스 폴더(`res/drawable/`, `assets/` 등)에 배치하고 UI 코드와 즉시 바인딩하십시오.\n"
                )
        if project_context:
            prompt += f"\n**[프로젝트 컨텍스트]:**\n{project_context[:800]}"
        return prompt

    def _build_expansion_prompt(self, last: Optional[RoadmapMilestone], context: str) -> str:
        """로드맵 소진 시 Commander에게 새 마일스톤 기획 요청."""
        return (
            f"[📍 Roadmap Ladder — 로드맵 소진, 자동 확장 요청]\n\n"
            f"🎉 ROADMAP.md의 모든 마일스톤이 완료되었습니다.\n"
            f"마지막 완료: {last.version if last else '없음'}\n\n"
            f"**[Commander 지시: 새 마일스톤 기획 후 ROADMAP.md 확장]**\n"
            f"현재 프로젝트 소스를 분석하여 상용 가치를 높일 신규 마일스톤 3~5개를 "
            f"ROADMAP.md에 추가하고, 첫 번째 신규 마일스톤 구현을 즉시 시작하십시오.\n"
            f"형식:\n```markdown\n## vX.Y — 기능명\n- [ ] 태스크\n```\n"
        )

    def build_hotfix_prompt(self, qa_report: str, hotfix_round: int, hotfix_max: int) -> str:
        """QA FAIL 시 핫픽스 지시 프롬프트."""
        current = self.get_current_milestone(auto_expand=False)
        ms_str = f"`{current.version} — {current.title}`" if current else "현재 단계"
        progress = self.get_progress_summary()
        return (
            f"[🔧 Roadmap Ladder — 핫픽스 지시 ({hotfix_round}/{hotfix_max}회)]\n\n"
            f"❌ **검증 실패 단계**: {ms_str}\n"
            f"{progress}\n\n"
            f"**[QA 결함 리포트]:**\n```\n{qa_report[:1200]}\n```\n\n"
            f"**[수정 지침]:**\n"
            f"1. 위 결함만 정밀 수정하십시오. 다른 기능에 영향 금지.\n"
            f"2. 수정 후 빌드/배포 완료 후 E2E 재검증을 트리거하십시오.\n"
            f"3. {hotfix_max}회 한도 내 미해결 시 해당 단계는 자동 스킵됩니다.\n"
        )


# ──────────────────────────────────────────────
# Commander-powered generation (async)
# ──────────────────────────────────────────────

async def ensure_roadmap(workspace: str) -> RoadmapManager:
    """워크스페이스에 ROADMAP.md가 없으면 기본 템플릿으로 자동 생성 후 반환."""
    mgr = RoadmapManager(workspace)
    if not mgr.load():
        mgr.ensure_exists()
    return mgr


def detect_workspace_target(ws: str, session: Optional[Any] = None) -> Tuple[str, str]:
    """
    Intelligently determines verification target path/URL and clean display name
    based on the actual workspace type (Android, Web/SaaS, Python Service, or URL).
    Returns (target_path_or_url, display_name).
    """
    if not ws or not os.path.exists(ws):
        fallback = getattr(session, "last_url", None) or ws or "Default Workspace"
        return fallback, os.path.basename(fallback) if os.path.exists(fallback) else fallback

    # 1. Android Project (Must have gradlew / android project structure)
    try:
        from android_build_manager import AndroidBuildManager
        and_root = AndroidBuildManager.find_android_project(ws)
    except Exception:
        and_root = None

    if and_root and os.path.isdir(and_root):
        all_apks = []
        for root, _, files in os.walk(and_root):
            if any(ignored in root for ignored in [".gradle", ".git", "node_modules", ".idea", ".venv"]):
                continue
            for f in files:
                if f.lower().endswith(".apk") and not f.startswith("."):
                    p = os.path.join(root, f)
                    if os.path.isfile(p):
                        all_apks.append(p)
        if all_apks:
            def apk_sort_key(p):
                fname = os.path.basename(p)
                is_named_timestamp = bool(re.search(r"\d{8}_\d{6}", fname))
                is_latest = "latest" in fname.lower()
                is_generic = fname.lower() in ("app-release.apk", "app-debug.apk", "app-release-unsigned.apk")
                priority = 2 if is_named_timestamp else (1 if is_latest else (0 if not is_generic else -1))
                return (priority, os.path.getmtime(p))

            all_apks.sort(key=apk_sort_key, reverse=True)
            newest_apk = all_apks[0]
            return newest_apk, os.path.basename(newest_apk)
        else:
            app_name = os.path.basename(and_root.rstrip("/"))
            return and_root, f"{app_name} (Android App)"

    # 2. Web / Frontend Project (package.json, index.html, vite.config, etc.)
    pkg_json = os.path.join(ws, "package.json")
    if os.path.isfile(pkg_json) or os.path.isfile(os.path.join(ws, "index.html")):
        last_url = getattr(session, "last_url", None)
        if last_url and (last_url.startswith("http://") or last_url.startswith("https://")):
            return last_url, last_url
        pkg_name = ""
        try:
            import json
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
                pkg_name = pkg_data.get("name", "")
        except Exception:
            pass
        folder_name = os.path.basename(ws.rstrip("/"))
        name = pkg_name or folder_name
        return ws, f"{name} (Web App)"

    # 3. Python Service / Bot (bot.py, pyproject.toml, requirements.txt)
    if (
        os.path.isfile(os.path.join(ws, "bot.py"))
        or os.path.isfile(os.path.join(ws, "agy_agent", "bot.py"))
        or os.path.isfile(os.path.join(ws, "pyproject.toml"))
        or os.path.isfile(os.path.join(ws, "requirements.txt"))
    ):
        folder_name = os.path.basename(ws.rstrip("/"))
        return ws, f"{folder_name} (Python Service)"

    # 4. Session URL if present
    last_url = getattr(session, "last_url", None)
    if last_url:
        return last_url, (os.path.basename(last_url) if os.path.exists(last_url) else last_url)

    # 5. Default directory name
    folder_name = os.path.basename(ws.rstrip("/")) or ws
    return ws, folder_name
