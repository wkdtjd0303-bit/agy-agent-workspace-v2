#!/usr/bin/env python3
"""
👑 CodebaseRealityInspector (AGY Mode 3 / Mode 3+ Adaptive Codebase Grounding Engine)
Designated Bot: 1540273467453345812 (Commander Bot)

Physically scans and inspects the project workspace on disk to ground Commander's planning
in authentic reality rather than hypothetical waterfall assumptions:
1. Extracts existing production source files, tests, build scripts, and manifests.
2. Identifies already implemented components, UI screens, viewmodels, and engines.
3. Examines Git commit history and current tags.
4. Reads living ROADMAP.md to track completed vs pending milestones.
5. Formulates a concise reality briefing for Commander to compute the exact missing delta:
   Δ = Root User Goal - Current Codebase Reality.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set


@dataclass
class CodebaseReality:
    """Represents the authentic physical state of the workspace on disk."""
    workspace: str
    is_empty: bool
    tech_stack: str
    build_files: List[str] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    key_directories: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    test_files: List[str] = field(default_factory=list)
    implemented_components: List[str] = field(default_factory=list)
    completed_milestones: List[str] = field(default_factory=list)
    current_milestone: Optional[str] = None
    recent_git_commits: List[str] = field(default_factory=list)
    summary_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "is_empty": self.is_empty,
            "tech_stack": self.tech_stack,
            "build_files": self.build_files,
            "entrypoints": self.entrypoints,
            "key_directories": self.key_directories,
            "source_files_count": len(self.source_files),
            "test_files_count": len(self.test_files),
            "implemented_components": self.implemented_components,
            "completed_milestones": self.completed_milestones,
            "current_milestone": self.current_milestone,
            "recent_git_commits": self.recent_git_commits,
            "summary_text": self.summary_text
        }


class CodebaseRealityInspector:
    """
    Physical disk inspector for Commander.
    Extracts high-fidelity project evidence to prevent duplicate milestone hallucinations.
    """

    IGNORED_DIRS: Set[str] = {
        ".git", "build", ".gradle", "node_modules", "__pycache__",
        ".venv", "venv", ".idea", ".vscode", "dist", ".cache",
        "reports", "out", ".scannerwork", "coverage"
    }

    SOURCE_EXTENSIONS: Set[str] = {
        ".kt", ".java", ".py", ".ts", ".tsx", ".js", ".jsx",
        ".html", ".css", ".vue", ".svelte", ".rs", ".go", ".c", ".cpp"
    }

    @classmethod
    def inspect(cls, workspace: Optional[str]) -> CodebaseReality:
        """
        Scans workspace directory on disk and returns structured CodebaseReality.
        Safe against non-existent or empty directories.
        """
        if not workspace or not os.path.isdir(workspace):
            return CodebaseReality(
                workspace=workspace or "",
                is_empty=True,
                tech_stack="Uninitialized (Empty Workspace)",
                summary_text="[워크스페이스 상태]: 디렉토리가 비어있거나 초기화되지 않았습니다. 기본 프로젝트 뼈대 및 코어 MVP 구축이 필요합니다."
            )

        build_files: List[str] = []
        entrypoints: List[str] = []
        key_directories_set: Set[str] = set()
        source_files: List[str] = []
        test_files: List[str] = []
        implemented_components_set: Set[str] = set()

        for root, dirs, files in os.walk(workspace):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in cls.IGNORED_DIRS and not d.startswith(".")]

            rel_root = os.path.relpath(root, workspace)
            if rel_root != ".":
                depth = len(rel_root.split(os.sep))
                if depth <= 4:
                    key_directories_set.add(rel_root.replace("\\", "/"))

            for fname in files:
                rel_path = os.path.join(rel_root, fname).replace("\\", "/")
                if rel_root == ".":
                    rel_path = fname

                # Check build and manifest files
                lower_fname = fname.lower()
                if lower_fname in (
                    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
                    "androidmanifest.xml", "package.json", "tsconfig.json", "pyproject.toml",
                    "setup.py", "requirements.txt", "cargo.toml", "go.mod", "pom.xml"
                ):
                    build_files.append(rel_path)

                # Check entrypoints
                if lower_fname in (
                    "mainactivity.kt", "mainactivity.java", "main.py", "app.py",
                    "index.html", "main.ts", "main.js", "app.tsx", "app.jsx", "index.ts", "index.js"
                ):
                    entrypoints.append(rel_path)

                # Check source vs test
                ext = os.path.splitext(fname)[1].lower()
                if ext in cls.SOURCE_EXTENSIONS:
                    is_test = (
                        "test" in rel_path.lower()
                        or fname.endswith(("Test.kt", "Test.java", "Test.py", "_test.py", ".test.ts", ".spec.ts", ".test.js"))
                    )
                    if is_test:
                        test_files.append(rel_path)
                    else:
                        source_files.append(rel_path)
                        # Extract component name from file
                        stem = os.path.splitext(fname)[0]
                        if stem and (stem[0].isupper() or stem.lower() not in ("__init__", "index", "test")):
                            implemented_components_set.add(stem)

        is_empty = (len(source_files) == 0 and len(build_files) == 0)

        # Detect tech stack
        tech_stack = cls._detect_tech_stack(workspace, build_files, source_files)

        # Read completed milestones from ROADMAP.md
        completed_milestones, current_milestone = cls._read_roadmap_status(workspace)

        # Read recent git commits
        recent_commits = cls._read_recent_commits(workspace)

        implemented_components = sorted(list(implemented_components_set))

        # Build human/LLM summary
        summary_text = cls._build_summary(
            is_empty=is_empty,
            tech_stack=tech_stack,
            source_count=len(source_files),
            test_count=len(test_files),
            components=implemented_components,
            completed_milestones=completed_milestones,
            current_milestone=current_milestone,
            recent_commits=recent_commits
        )

        return CodebaseReality(
            workspace=workspace,
            is_empty=is_empty,
            tech_stack=tech_stack,
            build_files=build_files,
            entrypoints=entrypoints,
            key_directories=sorted(list(key_directories_set)),
            source_files=source_files,
            test_files=test_files,
            implemented_components=implemented_components,
            completed_milestones=completed_milestones,
            current_milestone=current_milestone,
            recent_git_commits=recent_commits,
            summary_text=summary_text
        )

    @classmethod
    def _detect_tech_stack(cls, workspace: str, build_files: List[str], source_files: List[str]) -> str:
        bf_str = " ".join(build_files).lower()
        sf_str = " ".join(source_files).lower()

        if "androidmanifest.xml" in bf_str or "build.gradle" in bf_str:
            if any(f.endswith(".kt") for f in source_files):
                return "Android (Kotlin / Jetpack Compose / Gradle)"
            return "Android (Java / Gradle)"
        elif "package.json" in bf_str:
            if any(f.endswith((".tsx", ".ts")) for f in source_files):
                return "Web Frontend (React / TypeScript / Node)"
            return "Web (JavaScript / HTML5 Canvas)"
        elif "pyproject.toml" in bf_str or "setup.py" in bf_str or "requirements.txt" in bf_str or any(f.endswith(".py") for f in source_files):
            return "Python (CLI / Backend / Automation)"
        elif any(f.endswith(".html") for f in source_files):
            return "HTML5 / Web Canvas / JavaScript"
        return "Generic Multi-Platform Project"

    @classmethod
    def _read_roadmap_status(cls, workspace: str) -> tuple[List[str], Optional[str]]:
        completed: List[str] = []
        current: Optional[str] = None
        rm_path = os.path.join(workspace, "ROADMAP.md")
        if not os.path.isfile(rm_path):
            return completed, current

        try:
            with open(rm_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            for line in lines:
                line_str = line.strip()
                if line_str.startswith("## v"):
                    header = line_str.lstrip("#").strip()
                    if "✅" in header or "[DONE]" in header.upper():
                        clean_hdr = header.replace("✅", "").replace("[DONE]", "").strip()
                        completed.append(clean_hdr)
                    elif "🔄" in header or "[IN_PROGRESS]" in header.upper():
                        clean_hdr = header.replace("🔄", "").replace("[IN_PROGRESS]", "").strip()
                        current = clean_hdr
                    elif not current and not completed:
                        current = header
        except Exception:
            pass

        return completed, current

    @classmethod
    def _read_recent_commits(cls, workspace: str) -> List[str]:
        if not os.path.isdir(os.path.join(workspace, ".git")):
            return []
        try:
            res = subprocess.run(
                ["git", "log", "-n", "3", "--oneline"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=3.0
            )
            if res.returncode == 0 and res.stdout:
                return [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            pass
        return []

    @classmethod
    def _build_summary(
        cls,
        is_empty: bool,
        tech_stack: str,
        source_count: int,
        test_count: int,
        components: List[str],
        completed_milestones: List[str],
        current_milestone: Optional[str],
        recent_commits: List[str]
    ) -> str:
        if is_empty:
            return (
                "📂 [디스크 코드베이스 실사 결과 — 초기 상태]\n"
                "• 상태: 워크스페이스에 기존 구현된 소스 파일이 없습니다.\n"
                "• 권장 차기 조치: 제품 코어 아키텍처 뼈대(프로젝트 구조, 빌드 스크립트, 진입점 메인 액티비티/엔트리포인트)를 구축하십시오."
            )

        comp_preview = ", ".join(components[:15]) if components else "확인된 대형 컴포넌트 없음"
        if len(components) > 15:
            comp_preview += f" 외 {len(components) - 15}개"

        done_preview = " 없음"
        if completed_milestones:
            done_preview = "\n  - " + "\n  - ".join(completed_milestones[-4:])

        commits_preview = ""
        if recent_commits:
            commits_preview = f"\n• 최근 Git 커밋: {' | '.join(recent_commits[:2])}"

        return (
            f"📂 [디스크 코드베이스 실사 결과 — 실체 기반 현황]\n"
            f"• 기술 스택: {tech_stack} (소스 {source_count}개, 테스트 {test_count}개)\n"
            f"• 디스크에 실구현된 핵심 컴포넌트: {comp_preview}\n"
            f"• 기완료 확정 마일스톤:{done_preview}\n"
            f"• 현재 진행 중인 마일스톤: {current_milestone or '없음'}"
            f"{commits_preview}\n"
            f"⚠️ [사령관 엄명]: 위 목록에 이미 구현된 파일/컴포넌트는 중복 구현하지 말고, "
            f"사용자 원본 목표 중 아직 결손된 단 하나의 핵심 기능에만 집중하십시오."
        )
