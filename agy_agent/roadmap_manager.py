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
import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

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

    @property
    def is_done(self) -> bool:
        return self.status == "DONE" or (self.tasks and all(t.done for t in self.tasks))

    @property
    def is_pending(self) -> bool:
        return not self.is_done and self.status != "SKIPPED"

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

    if current:
        milestones.append(current)

    # 태스크 기반 DONE 재검증
    for ms in milestones:
        if ms.tasks and all(t.done for t in ms.tasks) and ms.status != "SKIPPED":
            ms.status = "DONE"

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

    def _generate_roadmap_from_prd(self, prd_path: str) -> Optional[str]:
        """Parses PRD.md requirements and synthesizes initial milestones directly aligned with user intent."""
        try:
            with open(prd_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            req_matches = re.findall(r'[\*\-\•]?\s*\*\*([A-Z0-9\-_]+(?:\s*\([^\)]+\))?)\*\*:\s*(.+)', content)
            if not req_matches:
                return None
            lines = [
                "# 🗺️ Project Roadmap (AGY Auto-Generated from PRD.md)",
                "> 이 파일은 AGY Roadmap Ladder System이 PRD 기획 헌법을 기반으로 자동 생성·관리합니다.",
                "> QA 통과 시마다 자동으로 체크가 기록되고 다음 마일스톤으로 진화합니다.",
                ""
            ]
            cur_maj = 1
            cur_min = 0
            for item in req_matches:
                req_raw = item[0].strip()
                req_desc = item[1].strip()
                m = re.match(r'(REQ-\d+)\s*(?:\((.*?)\))?', req_raw, re.IGNORECASE)
                if m:
                    req_id = m.group(1).upper()
                    req_title = m.group(2) or req_desc.split(".")[0]
                else:
                    req_id = req_raw
                    req_title = req_desc.split(".")[0]

                ver_str = f"v{cur_maj}.{cur_min}"
                lines.append(f"## {ver_str} — [{req_id}] {req_title}")
                subtasks = [s.strip() for s in re.split(r'[\.,;]\s+', req_desc) if len(s.strip()) > 3]
                if not subtasks:
                    subtasks = [req_desc]
                for st in subtasks[:3]:
                    lines.append(f"- [ ] {st}")
                lines.append("")
                cur_min += 1
                if cur_min >= 3:
                    cur_maj += 1
                    cur_min = 0

            lines.append(f"## v{cur_maj}.0 — 상용 배포 품질 및 회귀 무결성 검증")
            lines.append("- [ ] 전 기능 회귀 테스트 PASS 및 7대 게이트 무결점 통과")
            lines.append("- [ ] 릴리즈 빌드 및 무인 운영 내구성 확보")
            lines.append("")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"Failed to generate roadmap from PRD: {e}")
            return None

    def ensure_exists(self) -> bool:
        """ROADMAP.md 없으면 PRD.md 기반(또는 기본 템플릿)으로 생성. True=새로 생성됨."""
        if os.path.isfile(self.roadmap_path):
            return False
        try:
            initial_content = None
            prd_path = os.path.join(self.workspace, "PRD.md")
            if os.path.isfile(prd_path):
                initial_content = self._generate_roadmap_from_prd(prd_path)

            if not initial_content:
                initial_content = _DEFAULT_ROADMAP_TEMPLATE

            with open(self.roadmap_path, "w", encoding="utf-8") as f:
                f.write(initial_content)
            logger.info(f"ROADMAP.md 생성 완료 (PRD 기반: {bool(initial_content != _DEFAULT_ROADMAP_TEMPLATE)})")
            self.load()
            return True
        except Exception as e:
            logger.error(f"ROADMAP.md 생성 실패: {e}")
            return False

    # ── Query ──────────────────────────────────────

    def get_current_milestone(self, auto_expand: bool = True) -> Optional[RoadmapMilestone]:
        """IN_PROGRESS 또는 첫 번째 PENDING 마일스톤 반환. 소진 시 자동 확장 지원."""
        if not self._loaded:
            self.load()
        for ms in self._milestones:
            if ms.status == "IN_PROGRESS":
                return ms
        for ms in self._milestones:
            if ms.is_pending:
                return ms
        if auto_expand:
            new_ms_list = self.auto_expand_roadmap()
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

    # ── Mutation ──────────────────────────────────────

    def auto_expand_roadmap(self) -> List[RoadmapMilestone]:
        """
        ROADMAP.md의 모든 마일스톤이 완료/스킵되어 소진되었을 때,
        프로젝트 컨텍스트 및 상용 표준에 맞춘 차기 고도화 마일스톤 3개를 동적으로 자동 생성 및 영구 추가합니다.
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

        # 1. Check if PRD.md has any pending REQs not yet included in any existing milestones
        prd_path = os.path.join(self.workspace, "PRD.md")
        unmapped_prd_templates = []
        if os.path.isfile(prd_path):
            try:
                with open(prd_path, "r", encoding="utf-8", errors="ignore") as f:
                    prd_text = f.read()
                req_matches = re.findall(r'[\*\-\•]?\s*\*\*([A-Z0-9\-_]+(?:\s*\([^\)]+\))?)\*\*:\s*(.+)', prd_text)
                for item in req_matches:
                    req_raw = item[0].strip()
                    req_desc = item[1].strip()
                    m = re.match(r'(REQ-\d+)\s*(?:\((.*?)\))?', req_raw, re.IGNORECASE)
                    req_id = m.group(1).upper() if m else req_raw
                    req_title = m.group(2) if m and m.group(2) else req_desc.split(".")[0]
                    if not any(req_id in ms.title for ms in self._milestones):
                        subtasks = [s.strip() for s in re.split(r'[\.,;]\s+', req_desc) if len(s.strip()) > 3]
                        if not subtasks:
                            subtasks = [req_desc]
                        unmapped_prd_templates.append((f"[{req_id}] {req_title}", subtasks[:3]))
            except Exception as e:
                logger.debug(f"PRD unmapped check notice: {e}")

        existing_titles = [ms.title for ms in self._milestones]
        def is_title_duplicate(template_title: str, existing_title: str) -> bool:
            m_tpl = re.search(r'\[(REQ-\d+)\]', template_title)
            if m_tpl and m_tpl.group(1).lower() in existing_title.lower():
                return True
            # Strip [REQ-xxx] and check semantic title similarity
            clean_tpl = re.sub(r'\[REQ-\d+\]', '', template_title, flags=re.IGNORECASE)
            clean_ext = re.sub(r'\[REQ-\d+\]', '', existing_title, flags=re.IGNORECASE)
            c_tpl = re.sub(r'[\s\-_&]', '', clean_tpl.lower())
            c_ext = re.sub(r'[\s\-_&]', '', clean_ext.lower())
            if not c_tpl or not c_ext:
                return False
            prefix_len = min(6, len(c_tpl), len(c_ext))
            return (c_tpl[:prefix_len] in c_ext) or (c_ext[:prefix_len] in c_tpl)

        # If there are unmapped requirements in PRD.md, prioritize them!
        available_templates = [t for t in unmapped_prd_templates if not any(is_title_duplicate(t[0], et) for et in existing_titles)]

        # If no unmapped PRD requirements, dynamically synthesize project-aware milestones by inspecting actual codebase files
        if not available_templates:
            available_templates = self._synthesize_codebase_aware_milestones(existing_titles)

        new_milestones: List[RoadmapMilestone] = []
        cur_maj = max_major
        cur_min = max_minor

        for title, tasks in available_templates[:3]:
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

        # Domain-aware synthesis (Zero B2B SaaS Hallucination & Atomic Single-Capability)
        if is_pdf_or_doc:
            return [
                (
                    f"인체공학적 문서 탐색 & 썸네일 목차 그리드 (Phase {exp_idx}: 탐색 코어)",
                    [
                        "고해상도 페이지 썸네일 그리드 내비게이터 및 빠른 점프 슬라이더 구현",
                        "문서 개요(Outline/TOC) 계층 트리 뷰 및 양방향 페이지 동기화",
                        "북마크 추가/삭제/필터링 및 시각적 리본 인덱스 인터랙션 완비"
                    ]
                ),
                (
                    f"실시간 본문 텍스트 검색 & 뷰포트 하이라이트 (Phase {exp_idx+1}: 검색 코어)",
                    [
                        "실시간 본문 전문 검색 쿼리 및 매칭 단어 뷰포트 하이라이트/스크롤 연동",
                        "이전/다음 검색 결과 순환 이동 및 일치 단어 카운터 뱃지",
                        "대용량 문서 검색 비동기 인덱싱 및 특수문자/정규식 검색 예외 처리"
                    ]
                ),
                (
                    f"주석 레이어 렌더링 & 무손실 내보내기 (Phase {exp_idx+2}: 주석 코어)",
                    [
                        "주석(형광펜, 펜 필기, 텍스트) 레이어 분리 저장 및 캔버스 렌더링",
                        "터치 제스처 충돌 방지(스크롤 vs 드로잉) 및 Undo/Redo 제어 무결성",
                        "원본 문서 훼손 없는 무손실 주석 병합 및 표준 PDF 내보내기"
                    ]
                )
            ]
        elif is_game:
            return [
                (
                    f"고급 레벨 디자인 & 다이내믹 게임플레이 튜닝 (Phase {exp_idx})",
                    [
                        "다양한 지형 기믹 및 동적 장애물 인터랙션 구현",
                        "적 행동 패턴 다양화 및 보스 스테이지 페이즈 연출",
                        "콤보 점수 시스템 및 시각적 축하 파티클 이펙트 완성"
                    ]
                ),
                (
                    f"사운드 오디오 믹싱 & 60fps 프레임 내구성 방어 (Phase {exp_idx+1})",
                    [
                        "절차적 타격 효과음 및 동적 배경음악 크로스페이드",
                        "모바일 터치 조작감 튜닝 및 가상 패드 햅틱 반응성",
                        "장기 실행 시 메모리 누수 0건 및 60fps 유지 최적화"
                    ]
                ),
                (
                    f"플레이어 성장 밸런스 & 업적 챌린지 시스템 (Phase {exp_idx+2})",
                    [
                        "다채로운 파워업 아이템 효과 및 인게임 스킬 트리 구현",
                        "업적 챌린지 해금 모달 및 로컬 최고 기록 스코어보드 연동",
                        "결과 화면 애니메이션 연출 및 원클릭 즉시 재시작 UX"
                    ]
                )
            ]
        else:
            return [
                (
                    f"사용자 중심 핵심 워크플로우 & 인터랙션 완성도 강화 (Phase {exp_idx})",
                    [
                        "주요 기능 원클릭 접근성 및 인체공학적 직관적 제어 UX 구현",
                        "비정상 입력/네트워크 오류 시 친절한 사용자 가이드 및 자동 복원",
                        "작업 상태 영속화 및 재실행 시 이전 세션 100% 무손실 복구"
                    ]
                ),
                (
                    f"상용 서비스 품질 검증 & 크래시 0건 내구성 안정화 (Phase {exp_idx+1})",
                    [
                        "반응형 뷰포트 레이아웃 붕괴 0px 및 터치 타깃 최적화",
                        "백그라운드 메모리 누수 제로화 및 OOM 선제적 방어",
                        "전체 단위/통합 테스트 100% 통과 및 상용 릴리즈 패키징"
                    ]
                ),
                (
                    f"인체공학적 개인화 설정 & 데이터 백업/복원 완성 (Phase {exp_idx+2})",
                    [
                        "사용자 커스텀 테마/환경설정 로컬 영속화 및 즉시 적용",
                        "작업 데이터 원클릭 백업/복원 및 무손실 데이터 무결성 검증",
                        "전 플랫폼 엔드투엔드(E2E) 회귀 테스트 100% 무결점 통과"
                    ]
                )
            ]

    def sync_milestones_to_prd(self):
        """
        [PRD.md & ROADMAP.md Bidirectional Sync]
        Ensures PRD.md reflects completed and active milestones so that QA and Auditor
        never suffer from sync drift or false rejection.
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
        Persists to ROADMAP.md and syncs with PRD.md.
        """
        if not self._loaded:
            self.load()

        target_ms = None
        for ms in self._milestones:
            if ms.version.lower() == version.lower() or ms.title.lower() == title.lower():
                target_ms = ms
                break

        task_objs = [RoadmapTask(text=t, done=(status == "DONE")) for t in tasks]

        if target_ms:
            target_ms.title = title
            target_ms.tasks = task_objs
            target_ms.status = status
        else:
            # Mark prior in-progress milestones as DONE
            for prior_ms in self._milestones:
                if prior_ms.status == "IN_PROGRESS":
                    prior_ms.status = "DONE"
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
                    f"\n🎨 **[사령관 <@1539448922643234876> 공식 시각 에셋 생성 승인]**:\n"
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
                    f"\n🎨 **[사령관 <@1539448922643234876> 공식 시각 에셋 생성 승인]**:\n"
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
