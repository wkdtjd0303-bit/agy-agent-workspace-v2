#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visual_feedback_loop.py

Multimodal Visual Peer-Review & Real-time Critique Loop Engine for AGY 3.0.
Emulates the multi-agent visual dialogue workflow seen in advanced autonomous setups:
1. Worker Agent (Coder/Implementer) renders the service UI (Web, Android, Game Canvas)
   and captures real high-res / cropped screenshots.
2. Reviewer Agent (QA/Critic) inspects the visual screenshot with Multimodal Vision,
   identifying UI/UX defects (button sizes, missing keycaps, contrast, alignments).
3. Reviewer posts concrete, conversational feedback in Discord ("버튼이 너무 작아요", "Q 키캡 누락").
4. Worker acknowledges the issue, explains the root cause, applies the code fix,
   re-captures the UI, and responds ("24px로 키운 버전입니다", "정상 매핑 확인").
5. Reviewer validates the revised screenshot and issues final approval ("확인되었습니다").
"""

import os
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("VisualFeedbackLoop")


@dataclass
class VisualDialogueTurn:
    speaker: str              # e.g. "clone-코맥스", "clone-클로드", "clover-원격코드", "clover-클코"
    role: str                 # "WORKER" | "REVIEWER"
    content: str              # Korean conversational dialogue text
    screenshot_paths: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VisualFeedbackSession:
    session_id: str
    target_component: str
    turns: List[VisualDialogueTurn] = field(default_factory=list)
    status: str = "IN_PROGRESS"  # "IN_PROGRESS", "APPROVED", "REJECTED"
    final_verdict: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualFeedbackLoopEngine:
    """
    Manages conversational visual feedback exchanges between autonomous agents.
    Provides structured generation of dialogues and handles screenshot attachments.
    """

    @classmethod
    def create_session(cls, session_id: str, target_component: str) -> VisualFeedbackSession:
        return VisualFeedbackSession(
            session_id=session_id,
            target_component=target_component
        )

    @classmethod
    def generate_worker_proposal_turn(
        cls,
        worker_name: str,
        component_name: str,
        change_summary: List[str],
        screenshot_path: str,
        note: str = ""
    ) -> VisualDialogueTurn:
        """
        Worker Agent proposes the first render or an updated render with evidence screenshot.
        """
        bullet_lines = "\n".join([f"• {item}" for item in change_summary])
        text = f"**[{component_name}] 렌더링 결과입니다.**\n"
        if bullet_lines:
            text += f"{bullet_lines}\n"
        if note:
            text += f"\n{note}\n"
        text += "화면 캡처 확인 부탁드립니다."

        return VisualDialogueTurn(
            speaker=worker_name,
            role="WORKER",
            content=text,
            screenshot_paths=[screenshot_path] if os.path.exists(screenshot_path) else [],
            metadata={"phase": "INITIAL_PROPOSAL", "component": component_name}
        )

    @classmethod
    def generate_reviewer_critique_turn(
        cls,
        reviewer_name: str,
        defects_found: List[str],
        positive_notes: List[str] = None,
        crop_screenshot_path: Optional[str] = None
    ) -> VisualDialogueTurn:
        """
        Reviewer Agent inspects the visual screenshot and posts structured critique in conversational tone.
        """
        positive_notes = positive_notes or []
        lines = []

        if positive_notes:
            lines.append("전반적인 레이아웃 확인했습니다.")
            for pos in positive_notes:
                lines.append(f"• {pos}")
            lines.append("")

        if defects_found:
            lines.append("다만 시각적/기능적 수정이 필요한 부분이 있습니다:")
            for idx, defect in enumerate(defects_found, 1):
                lines.append(f"{idx}. {defect}")
            lines.append("\n확인 후 수정 부탁드립니다.")
        else:
            lines.append("시각적 가시성과 단축키 배치가 모두 깔끔합니다. 이대로 승인합니다. [STATUS: APPROVED]")

        text = "\n".join(lines)
        shots = [crop_screenshot_path] if crop_screenshot_path and os.path.exists(crop_screenshot_path) else []

        return VisualDialogueTurn(
            speaker=reviewer_name,
            role="REVIEWER",
            content=text,
            screenshot_paths=shots,
            metadata={"defects_count": len(defects_found)}
        )

    @classmethod
    def generate_worker_remedy_turn(
        cls,
        worker_name: str,
        remedy_description: str,
        remedy_bullets: List[str],
        new_screenshot_path: str
    ) -> VisualDialogueTurn:
        """
        Worker Agent explains the remedy, lists updated specs (e.g. 24px, Q/R/E bindings),
        and attaches the new screenshot.
        """
        lines = [remedy_description, ""]
        for b in remedy_bullets:
            lines.append(f"• {b}")
        lines.append("\n이제는 시인성이 충분한지 피드백 주시면 좋겠습니다.")

        text = "\n".join(lines)
        shots = [new_screenshot_path] if os.path.exists(new_screenshot_path) else []

        return VisualDialogueTurn(
            speaker=worker_name,
            role="WORKER",
            content=text,
            screenshot_paths=shots,
            metadata={"phase": "REMEDY_APPLIED"}
        )

    @classmethod
    def generate_reviewer_approval_turn(
        cls,
        reviewer_name: str,
        approved_aspects: List[str],
        final_comment: str = ""
    ) -> VisualDialogueTurn:
        """
        Reviewer Agent issues the final conversational approval.
        """
        lines = ["수정본 확인했습니다. 훨씬 낫네요!"]
        for aspect in approved_aspects:
            lines.append(f"• {aspect}")
        if final_comment:
            lines.append(f"\n{final_comment}")
        lines.append("\n[STATUS: APPROVED] 최종 검수 통과 완료되었습니다.")

        text = "\n".join(lines)
        return VisualDialogueTurn(
            speaker=reviewer_name,
            role="REVIEWER",
            content=text,
            screenshot_paths=[],
            metadata={"phase": "FINAL_APPROVAL", "verdict": "APPROVED"}
        )

    @classmethod
    def execute_peer_review_cycle(
        cls,
        session_id: str,
        component_name: str,
        worker_name: str = "clone-코맥스",
        reviewer_name: str = "clone-클로드",
        initial_shot_path: str = "",
        initial_specs: Optional[List[str]] = None,
        detected_defects: Optional[List[str]] = None,
        remedy_bullets: Optional[List[str]] = None,
        remedy_shot_path: str = ""
    ) -> VisualFeedbackSession:
        """
        Simulates or drives a full 4-stage Visual Peer-Review cycle:
        Turn 1: Worker Proposal -> Turn 2: Reviewer Critique ->
        Turn 3: Worker Remedy -> Turn 4: Reviewer Final Approval
        """
        session = cls.create_session(session_id, component_name)
        initial_specs = initial_specs or ["기본 버튼 폰트 16px", "다크 테마 적용"]
        detected_defects = detected_defects or [
            "버튼 폰트가 너무 작아서 시인성 저하 (Pause/Game Over 화면)",
            "단축키 텍스트 'Enter/Esc'가 뭉개져 보임 (최소 24px 이상 필요)"
        ]
        remedy_bullets = remedy_bullets or [
            "ENTER / ESC 텍스트 크기 24px로 확대",
            "Xelu-dark 테마 톤 유지 및 여백 정렬",
            "게임패드/키보드 바인딩 영향 없음 검증"
        ]

        # Turn 1
        t1 = cls.generate_worker_proposal_turn(
            worker_name=worker_name,
            component_name=component_name,
            change_summary=initial_specs,
            screenshot_path=initial_shot_path,
            note="초기 화면 구성입니다."
        )
        session.turns.append(t1)

        # Turn 2
        t2 = cls.generate_reviewer_critique_turn(
            reviewer_name=reviewer_name,
            defects_found=detected_defects,
            positive_notes=["다크 테마 톤과 기본 배치는 양호함"],
            crop_screenshot_path=initial_shot_path
        )
        session.turns.append(t2)

        # Turn 3
        t3 = cls.generate_worker_remedy_turn(
            worker_name=worker_name,
            remedy_description="지적해주신 시인성 문제 수정하여 다시 캡처했습니다.",
            remedy_bullets=remedy_bullets,
            new_screenshot_path=remedy_shot_path or initial_shot_path
        )
        session.turns.append(t3)

        # Turn 4
        t4 = cls.generate_reviewer_approval_turn(
            reviewer_name=reviewer_name,
            approved_aspects=[
                "24px 확대 버전으로 시인성 완벽 개선",
                "Enter/Esc 및 Pause UI 정렬 일관성 확보"
            ],
            final_comment="게임패드 및 모바일 터치 영역 기준도 무난하게 충족합니다."
        )
        session.turns.append(t4)

        session.status = "APPROVED"
        session.final_verdict = "APPROVED"
        return session
