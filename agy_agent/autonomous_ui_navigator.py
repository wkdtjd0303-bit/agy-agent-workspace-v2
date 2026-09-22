#!/usr/bin/env python3
"""
🧭 Autonomous UI Navigator & UI Hierarchy Inspector (AGY 3.0 Standard)
Provides genuine semantic UI tree parsing (UiAutomator XML / Accessibility Nodes),
intelligent onboarding/permission bypass, dynamic widget discovery, and goal-oriented UI navigation.
Replaces brittle hardcoded tap coordinates with resilient semantic interaction decisions.
"""

import os
import re
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

try:
    from domain_adaptive_engine import DrawingCanvasDriver, GamingEngineDriver, DomainAdaptiveEngine
except ImportError:
    DrawingCanvasDriver = None
    GamingEngineDriver = None
    DomainAdaptiveEngine = None

logger = logging.getLogger("AutonomousUiNavigator")


class UiNode:
    """Represents an interactive accessibility node in Android UI Hierarchy."""
    def __init__(
        self,
        tag: str = "",
        resource_id: str = "",
        text: str = "",
        content_desc: str = "",
        class_name: str = "",
        package: str = "",
        clickable: bool = False,
        enabled: bool = True,
        focusable: bool = False,
        scrollable: bool = False,
        bounds_raw: str = "[0,0][0,0]",
        x1: int = 0,
        y1: int = 0,
        x2: int = 0,
        y2: int = 0,
        bounds: Optional[tuple] = None
    ):
        if bounds and len(bounds) == 4:
            x1, y1, x2, y2 = bounds
            bounds_raw = f"[{x1},{y1}][{x2},{y2}]"
        self.tag = tag
        self.resource_id = resource_id
        self.text = text
        self.content_desc = content_desc
        self.class_name = class_name
        self.package = package
        self.clickable = clickable
        self.enabled = enabled
        self.focusable = focusable
        self.scrollable = scrollable
        self.bounds_raw = bounds_raw
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.center_x = (x1 + x2) // 2
        self.center_y = (y1 + y2) // 2
        self.width = max(0, x2 - x1)
        self.height = max(0, y2 - y1)

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.class_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "text": self.text,
            "content_desc": self.content_desc,
            "class_name": self.class_name,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "bounds": [self.x1, self.y1, self.x2, self.y2],
            "center": [self.center_x, self.center_y],
            "label": self.label
        }


@dataclass
class ActionScreenRecord:
    """
    Tracks causal relationship between actions and screenshots (Items 20~26).
    Enforces 'Screenshot != Evidence': Action, State, Screenshot, Timing, Mutation linked together.
    """
    action_id: str
    timestamp: float
    action_type: str = "tap"
    target_label: str = ""
    before_screenshot: Optional[str] = None
    after_screenshot: Optional[str] = None
    mutation_pct: float = 0.0
    is_valid_app_screen: bool = True
    screenshot_captured: bool = True
    screen_state_before: str = "NORMAL"
    screen_state_after: str = "NORMAL"
    execution_success: bool = True
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "target_label": self.target_label,
            "before_screenshot": self.before_screenshot,
            "after_screenshot": self.after_screenshot,
            "mutation_pct": self.mutation_pct,
            "is_valid_app_screen": self.is_valid_app_screen,
            "screenshot_captured": self.screenshot_captured,
            "screen_state_before": self.screen_state_before,
            "screen_state_after": self.screen_state_after,
            "execution_success": self.execution_success,
            "details": self.details
        }


@dataclass
class BranchNavigationTracker:
    """
    Tracks multi-branch navigation state (6. 여러 브랜치 탐색 & Requirement 4: 여러 갈래 Navigation).
    Enables deep hierarchical exploration, multi-branch tracking, and systematic backtracking when a branch is exhausted.
    """
    branch_stack: List[str] = field(default_factory=list)
    visited_routes: Dict[str, set] = field(default_factory=dict)
    current_depth: int = 0
    max_branch_depth: int = 4
    discovered_branches: List[str] = field(default_factory=list)
    visited_branches: List[str] = field(default_factory=list)
    backtrack_history: List[str] = field(default_factory=list)
    branch_tree: Dict[str, List[str]] = field(default_factory=dict)
    breadcrumbs: List[str] = field(default_factory=lambda: ["Root"])

    BRANCH_KEYWORDS: List[str] = field(default_factory=lambda: [
        "탭", "tab", "메뉴", "menu", "카테고리", "category", "설정", "settings",
        "목록", "list", "브랜치", "branch", "서브", "more", "더보기", "전체보기",
        "상세", "detail", "view_more", "profile", "마이"
    ])

    def is_branch_candidate(self, node: 'UiNode') -> bool:
        """6. 여러 브랜치 탐색: 노드가 새로운 하위 브랜치 진입점인지 판별."""
        combined = f"{node.text} {node.content_desc} {node.resource_id}".lower()
        return any(bk in combined for bk in self.BRANCH_KEYWORDS)

    def register_branch(self, branch_name: str, parent_branch: Optional[str] = None) -> None:
        """6. 여러 브랜치 탐색: 새로운 하위 브랜치 진입점 등록."""
        clean_name = branch_name.strip()
        if clean_name and clean_name not in self.discovered_branches:
            self.discovered_branches.append(clean_name)
            parent = parent_branch or (self.branch_stack[-1] if self.branch_stack else "Root")
            if parent not in self.branch_tree:
                self.branch_tree[parent] = []
            if clean_name not in self.branch_tree[parent]:
                self.branch_tree[parent].append(clean_name)

    def record_step(self, screen_sig: str, node_sig: str, branch_hint: Optional[str] = None) -> None:
        """6. 여러 브랜치 탐색: 브랜치 탐색 스텝 기록 및 스택 전진."""
        if screen_sig not in self.visited_routes:
            self.visited_routes[screen_sig] = set()
        self.visited_routes[screen_sig].add(node_sig)
        self.branch_stack.append(screen_sig)
        self.current_depth = len(self.branch_stack)
        if branch_hint:
            self.register_branch(branch_hint)
            if branch_hint not in self.visited_branches:
                self.visited_branches.append(branch_hint)
            self.breadcrumbs.append(branch_hint)

    def should_backtrack(self, screen_sig: str, candidate_sigs: List[str]) -> bool:
        """6. 여러 브랜치 탐색: 현재 브랜치의 모든 후보 요소 탐색 완료 시 상위 브랜치로 역추적(Backtrack) 판단."""
        visited = self.visited_routes.get(screen_sig, set())
        unvisited = [s for s in candidate_sigs if s not in visited]
        return len(unvisited) == 0 and len(self.branch_stack) > 0

    def backtrack_pop(self) -> Optional[str]:
        """6. 여러 브랜치 탐색: 현재 브랜치를 팝(Pop)하고 상위 브랜치로 체계적 복귀."""
        if self.branch_stack:
            prev = self.branch_stack.pop()
            self.current_depth = len(self.branch_stack)
            self.backtrack_history.append(prev)
            if len(self.breadcrumbs) > 1:
                self.breadcrumbs.pop()
            return prev
        return None

    def to_dict(self) -> Dict[str, Any]:
        """6. 여러 브랜치 탐색: 상태 직렬화."""
        return {
            "step": "6. 여러 브랜치 탐색",
            "current_depth": self.current_depth,
            "discovered_branches": self.discovered_branches,
            "visited_branches": self.visited_branches,
            "backtracks_executed": len(self.backtrack_history),
            "branch_tree": self.branch_tree,
            "breadcrumbs": self.breadcrumbs
        }


@dataclass
class BackKeyLoopDefender:
    """
    Defends against back-key infinite loops and ping-pong state oscillation (Requirement 5: 뒤로가기 루프).
    Prevents repetitive back-button trapping, circular navigation loops, and state oscillations.
    """
    consecutive_backs: int = 0
    max_consecutive_backs: int = 3
    recent_screen_signatures: List[str] = field(default_factory=list)
    oscillation_window: int = 4
    loops_prevented: int = 0

    def record_action(self, action_type: str, screen_sig: str) -> None:
        """Records an action and resulting screen state for loop analysis."""
        if action_type in ["key_back", "back", "press_back"]:
            self.consecutive_backs += 1
        else:
            self.consecutive_backs = 0
        self.recent_screen_signatures.append(screen_sig)
        if len(self.recent_screen_signatures) > 10:
            self.recent_screen_signatures.pop(0)

    def is_in_back_loop(self) -> bool:
        """Detects whether navigation is trapped in a back-key or ping-pong loop."""
        if self.consecutive_backs >= self.max_consecutive_backs:
            return True
        if len(self.recent_screen_signatures) >= self.oscillation_window:
            recent = self.recent_screen_signatures[-self.oscillation_window:]
            if recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]:
                return True
        return False

    def break_loop(self) -> None:
        """Resets consecutive back counter and registers loop defense trigger."""
        self.consecutive_backs = 0
        self.loops_prevented += 1


class AutonomousUiNavigator:
    """
    Intelligently analyzes UI hierarchy dumps, identifies permission/onboarding overlays,
    and executes semantic UI actions without relying on blind hardcoded tap coordinates.
    """

    # Multi-lingual Permission Keywords
    PERMISSION_KEYWORDS = [
        "허용", "앱 사용 중에만 허용", "이번만 허용", "동의", "계속", "승인", "권한 허용", "항상 허용",
        "allow", "while using the app", "while using", "only this time", "agree", "continue", "grant", "always allow"
    ]

    # Multi-lingual Onboarding & Dismiss Keywords
    ONBOARDING_KEYWORDS = [
        "건너뛰기", "skip", "닫기", "close", "시작하기", "시작", "get started", "start",
        "다음", "다음 단계", "next", "확인", "ok", "완료", "done", "다시 보지 않기",
        "don't show again", "둘러보기", "explore", "let's explore", "알겠습니다", "got it",
        "나중에", "later", "not now", "계속", "continue", "준비됐어요", "ready", "i'm ready",
        "이해했습니다", "understand", "건너뛰고 시작", "지금 시작"
    ]

    # Priority 1: Primary Call-to-Action (CTA) Keywords (+60 pts)
    PRIMARY_CTA_KEYWORDS = [
        "추가", "생성", "열기", "시작", "저장", "등록", "전송", "촬영", "스캔", "연결", "작성",
        "add", "create", "new", "start", "open", "save", "submit", "send", "scan", "connect", "capture", "write"
    ]

    # Priority 2: Secondary Exploration Keywords (+40 pts)
    SECONDARY_ACTION_KEYWORDS = [
        "수정", "편집", "선택", "설정", "검색", "서명", "그리기", "필터", "조회",
        "edit", "select", "settings", "search", "sign", "draw", "filter", "view"
    ]

    # Priority 3: Negative / Exit Keywords (-40 pts to avoid premature exit)
    DESTRUCTIVE_KEYWORDS = [
        "취소", "뒤로", "종료", "탈퇴", "삭제", "나가기",
        "cancel", "back", "exit", "close", "delete", "remove"
    ]

    # Priority 4: High-Risk / Hazardous Crash Traps (-120 pts to avoid minefield/fatal crash buttons)
    HAZARDOUS_KEYWORDS = [
        "강제 종료", "초기화", "데이터 삭제", "모두 지우기", "포맷", "치명적 오류", "크래시",
        "force stop", "format", "wipe", "factory reset", "crash", "destroy", "fatal"
    ]

    # Priority 5: Low-Information / Boilerplate Diverters (-30 pts to avoid diversion into legal/terms screens)
    BOILERPLATE_KEYWORDS = [
        "이용약관", "개인정보", "라이선스", "오픈소스", "버전 정보", "도움말", "faq",
        "terms", "privacy", "license", "about", "help", "legal"
    ]

    @classmethod
    def parse_bounds(cls, bounds_str: str) -> Tuple[int, int, int, int]:
        """Parses '[x1,y1][x2,y2]' format into (x1, y1, x2, y2)."""
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str.strip())
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return 0, 0, 0, 0

    @classmethod
    def parse_xml_hierarchy(cls, xml_text: str) -> List[UiNode]:
        """Parses UiAutomator XML dump into a flat list of interactive UiNode instances."""
        nodes: List[UiNode] = []
        if not xml_text or not xml_text.strip():
            return nodes

        try:
            root = ET.fromstring(xml_text.strip())
        except Exception as e:
            logger.debug(f"Failed to parse XML hierarchy: {e}")
            return nodes

        for elem in root.iter("node"):
            bounds_raw = elem.get("bounds", "[0,0][0,0]")
            x1, y1, x2, y2 = cls.parse_bounds(bounds_raw)
            # Filter out zero-area or invisible off-screen elements
            if x2 <= x1 or y2 <= y1:
                continue

            node = UiNode(
                tag=elem.tag,
                resource_id=elem.get("resource-id", ""),
                text=elem.get("text", "").strip(),
                content_desc=elem.get("content-desc", "").strip(),
                class_name=elem.get("class", ""),
                package=elem.get("package", ""),
                clickable=(elem.get("clickable", "false").lower() == "true"),
                enabled=(elem.get("enabled", "true").lower() == "true"),
                focusable=(elem.get("focusable", "false").lower() == "true"),
                scrollable=(elem.get("scrollable", "false").lower() == "true"),
                bounds_raw=bounds_raw,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2
            )
            nodes.append(node)

        return nodes

    @classmethod
    def classify_screen_state(cls, nodes: List[UiNode]) -> Dict[str, Any]:
        """
        Classifies current screen state into:
        - PERMISSION_DIALOG
        - ONBOARDING_MODAL
        - FORM_INPUT
        - SCROLLABLE_LIST
        - MAIN_CONTENT
        """
        if not nodes:
            return {"type": "EMPTY_OR_UNKNOWN", "confidence": 0.0, "target_node": None}

        # 1. Check for Permission Dialog
        for n in nodes:
            combined = f"{n.text} {n.content_desc} {n.resource_id}".lower()
            if any(pk in combined for pk in cls.PERMISSION_KEYWORDS):
                if any(k in combined for k in ["permission", "권한", "packageinstaller", "grant_permissions"]):
                    return {"type": "PERMISSION_DIALOG", "confidence": 0.95, "target_node": n}
                if n.clickable and any(pk == n.text.strip().lower() for pk in ["허용", "allow", "앱 사용 중에만 허용", "while using the app"]):
                    return {"type": "PERMISSION_DIALOG", "confidence": 0.90, "target_node": n}

        # 2. Check for Onboarding / Tutorial Modal
        clickable_nodes = [n for n in nodes if n.clickable]
        screen_text = " ".join([f"{n.text} {n.content_desc} {n.resource_id}" for n in nodes]).lower()
        has_onboarding_context = any(kw in screen_text for kw in [
            "가이드", "튜토리얼", "환영합니다", "다시 보지 않기", "건너뛰기",
            "guide", "tutorial", "welcome", "onboarding", "don't show again"
        ])
        is_sparse_overlay = len(clickable_nodes) <= 4

        if has_onboarding_context or is_sparse_overlay:
            for n in nodes:
                if not n.clickable:
                    continue
                combined = f"{n.text} {n.content_desc}".strip().lower()
                # Specific onboarding dismissal phrases
                if any(ok == combined for ok in ["건너뛰기", "skip", "다시 보지 않기", "don't show again", "둘러보기", "explore", "let's explore", "시작하기", "get started", "준비됐어요"]):
                    return {"type": "ONBOARDING_MODAL", "confidence": 0.90, "target_node": n}
                if has_onboarding_context and any(ok in combined for ok in ["닫기", "close", "다음", "next", "확인", "ok"]):
                    return {"type": "ONBOARDING_MODAL", "confidence": 0.85, "target_node": n}

        # 3. Check for Network Error / Offline Dialog (Requirement 10: 네트워크 오류)
        for n in nodes:
            combined = f"{n.text} {n.content_desc} {n.resource_id}".lower()
            if any(nk in combined for nk in ["네트워크", "network", "offline", "오프라인", "인터넷 연결", "connection lost", "http 503", "통신 오류"]):
                if any(ek in combined for ek in ["오류", "실패", "error", "failed", "끊김", "없음", "disconnected"]):
                    confirm_nodes = [
                        btn for btn in nodes
                        if btn.clickable and any(rb in f"{btn.text} {btn.content_desc}".lower() for rb in ["다시 시도", "재시도", "retry", "확인", "ok", "오프라인", "닫기"])
                    ]
                    target_btn = confirm_nodes[0] if confirm_nodes else n
                    return {"type": "NETWORK_ERROR_DIALOG", "confidence": 0.95, "target_node": target_btn}

        # 4. Check for Graceful Failure / Error Dialog (Requirement 15: 의도적으로 실패 가능한 기능)
        for n in nodes:
            combined = f"{n.text} {n.content_desc} {n.resource_id}".lower()
            if any(ek in combined for ek in ["오류", "실패", "에러", "error", "failed", "timed out", "다시 시도"]):
                confirm_nodes = [
                    btn for btn in nodes
                    if btn.clickable and any(rb in f"{btn.text} {btn.content_desc}".lower() for rb in ["확인", "다시 시도", "재시도", "ok", "retry", "닫기"])
                ]
                target_btn = confirm_nodes[0] if confirm_nodes else n
                return {"type": "ERROR_DIALOG", "confidence": 0.90, "target_node": target_btn}

        # 5. Check for Form / Input Screen
        edit_nodes = [n for n in nodes if "edittext" in n.class_name.lower() or (n.focusable and not n.clickable and "button" not in n.class_name.lower())]
        if edit_nodes:
            return {"type": "FORM_INPUT", "confidence": 0.75, "target_node": edit_nodes[0]}

        # 6. Check for Scrollable List
        scroll_nodes = [n for n in nodes if n.scrollable or "recyclerview" in n.class_name.lower() or "scrollview" in n.class_name.lower()]
        if scroll_nodes:
            return {"type": "SCROLLABLE_LIST", "confidence": 0.70, "target_node": scroll_nodes[0]}

        return {"type": "MAIN_CONTENT", "confidence": 0.60, "target_node": None}

    @classmethod
    def is_ambiguous_node(cls, node: UiNode) -> bool:
        """
        Identifies ambiguous or cryptic buttons (Requirement 3: 의미가 불분명한 버튼).
        Detects unlabeled icons, generic placeholders ('btn_1', 'iv_'), or opaque symbols.
        """
        label = node.label.strip()
        if not label:
            return True
        if len(label) <= 1 or re.match(r"^[\W_0-9]+$", label):
            return True
        generic_patterns = ["btn_", "view_", "iv_", "icon_", "mystery", "item_", "button_", "???", "..."]
        res_tail = node.resource_id.split("/")[-1].lower() if node.resource_id else ""
        if not node.text and not node.content_desc and any(gp in res_tail for gp in generic_patterns):
            return True
        return False

    @classmethod
    def get_node_signature(cls, node: UiNode) -> str:
        """Returns a stable signature for loop detection and exploration memory."""
        res = node.resource_id.split("/")[-1] if "/" in node.resource_id else node.resource_id
        text_clean = node.text[:20].strip()
        desc_clean = node.content_desc[:20].strip()
        pos_bucket = f"{node.center_x // 60}_{node.center_y // 60}"
        return f"{res}::{text_clean}::{desc_clean}::{pos_bucket}"

    @classmethod
    def get_screen_signature(cls, nodes: List[UiNode]) -> str:
        """Returns a stable composite signature for screen state tracking."""
        clickable_nodes = [n for n in nodes if n.clickable]
        cand_sigs = [cls.get_node_signature(n) for n in clickable_nodes]
        return "::".join(sorted(cand_sigs[:4])) if cand_sigs else "screen_empty"

    @classmethod
    def process_dense_button_environment(
        cls,
        nodes: List[UiNode],
        threshold: int = 20,
        visited_signatures: Optional[set] = None,
        goal_keywords: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Explicitly analyzes and clusters UI environments with 20+ buttons (Requirement 2: 버튼 20개 이상).
        Partitions dense button layouts into Primary CTAs, Safe Exploratory Targets, Ambiguous Buttons,
        Hazardous Crash Traps, and Boilerplate/Destructive buttons.
        """
        clickable_nodes = [n for n in nodes if n.clickable and n.enabled and n.width > 20 and n.height > 20]
        is_dense = len(clickable_nodes) >= threshold

        scored_candidates = []
        ctas = []
        hazards = []
        ambiguous = []
        boilerplate = []

        for n in clickable_nodes:
            sc = cls.score_interactive_node(n, visited_signatures=visited_signatures, goal_keywords=goal_keywords)
            scored_candidates.append((sc, n))

            comb = f"{n.text} {n.content_desc} {n.resource_id}".lower()
            if any(hk in comb for hk in cls.HAZARDOUS_KEYWORDS):
                hazards.append(n)
            elif cls.is_ambiguous_node(n):
                ambiguous.append(n)
            elif any(bk in comb for bk in cls.BOILERPLATE_KEYWORDS):
                boilerplate.append(n)
            elif any(pk in comb for pk in cls.PRIMARY_CTA_KEYWORDS):
                ctas.append(n)

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_node = scored_candidates[0] if scored_candidates else (0.0, None)

        return {
            "is_dense": is_dense,
            "total_buttons": len(clickable_nodes),
            "primary_ctas_count": len(ctas),
            "hazards_avoided_count": len(hazards),
            "ambiguous_count": len(ambiguous),
            "boilerplate_count": len(boilerplate),
            "ranked_candidates": [(s, n.label, n.resource_id) for s, n in scored_candidates],
            "best_candidate": best_node,
            "best_score": best_score
        }

    @classmethod
    def score_interactive_node(
        cls,
        node: UiNode,
        visited_signatures: Optional[set] = None,
        goal_keywords: Optional[List[str]] = None
    ) -> float:
        """
        Computes semantic information-gain score for an interactive node.
        Solves the '20 buttons dilemma' by prioritizing primary CTA, unvisited novelty,
        and high-value user interaction areas while penalizing repeated loops.
        """
        score = 50.0

        # 1. Unvisited Novelty Boost / Loop Prevention
        sig = cls.get_node_signature(node)
        if visited_signatures and sig in visited_signatures:
            score -= 100.0  # Already clicked in this session, strongly penalize loop
        else:
            score += 30.0  # Novel, unvisited node

        combined = f"{node.text} {node.content_desc} {node.resource_id}".lower()

        # 2. Goal Keyword Match (Highest priority)
        if goal_keywords:
            for gk in goal_keywords:
                if gk.lower() in combined:
                    score += 80.0

        # 3. Primary Call-to-Action (CTA) Boost
        if any(pk in combined for pk in cls.PRIMARY_CTA_KEYWORDS):
            score += 75.0

        # 4. Secondary Action Boost
        elif any(sk in combined for sk in cls.SECONDARY_ACTION_KEYWORDS):
            score += 25.0

        # 5. Destructive / Hazardous / Boilerplate / Ambiguous Deprioritization
        if any(hk in combined for hk in cls.HAZARDOUS_KEYWORDS):
            score -= 120.0  # High-risk crash trap / minefield button, actively avoid
        elif any(dk in combined for dk in cls.DESTRUCTIVE_KEYWORDS):
            score -= 40.0
        elif cls.is_ambiguous_node(node):
            score -= 35.0  # Requirement 3: Deprioritize ambiguous/cryptic buttons in favor of semantic CTAs
        elif any(bk in combined for bk in cls.BOILERPLATE_KEYWORDS):
            score -= 30.0  # Low-information legal/boilerplate diversion

        # 5.5. 6. 여러 브랜치 탐색 (Multi-Branch Deep Navigation Boost)
        BRANCH_KEYWORDS = [
            "탭", "tab", "메뉴", "menu", "카테고리", "category", "설정", "settings",
            "목록", "list", "브랜치", "branch", "서브", "more", "더보기", "전체보기",
            "상세", "detail", "view_more", "profile", "마이"
        ]
        if any(bk in combined for bk in BRANCH_KEYWORDS):
            score += 35.0  # 6. 여러 브랜치 탐색: 하위 브랜치 발견 및 계층 탐색 적극 유도

        # 6. Physical Position & Thumb Zone Boost
        # Bottom navigation or FAB area (lower half, right side)
        if node.center_y > 1100 and node.center_x > 400:
            score += 20.0
        # Central interaction area
        elif 400 < node.center_y < 1600:
            score += 10.0
        # Extreme top bar (often back arrow or time)
        elif node.center_y < 120:
            score -= 15.0

        # 7. Touch Target Size (48dp+ accessibility)
        if node.width >= 80 and node.height >= 80:
            score += 10.0

        return score

    @classmethod
    def decide_next_action(
        cls,
        nodes: List[UiNode],
        goal_keywords: Optional[List[str]] = None,
        prefer_action: Optional[str] = None,
        visited_signatures: Optional[set] = None,
        stagnant_attempts: int = 0,
        branch_tracker: Optional[BranchNavigationTracker] = None,
        back_loop_defender: Optional[BackKeyLoopDefender] = None,
        app_archetype: str = "AUTO"
    ) -> Dict[str, Any]:
        """
        Autonomously determines the next optimal action (tap coordinates, text injection, or swipe)
        based on real semantic UI elements and information-gain scoring rather than hardcoded coordinates.
        Includes recovery loop detection, back-key loop defense, and multi-branch navigation backtracking.
        Supports DomainAdaptiveEngine gestures for DrawingCanvas and GamingLoop.
        """
        # Priority 0: Back-Key Loop Defense (Requirement 5: 뒤로가기 루프 및 핑퐁 상태 진동 방어)
        if back_loop_defender and back_loop_defender.is_in_back_loop():
            back_loop_defender.break_loop()
            clickable_nodes = [n for n in nodes if n.clickable and n.enabled and n.width > 20 and n.height > 20]
            # Exclude back/cancel buttons to prevent re-entering back loop
            forward_candidates = [
                n for n in clickable_nodes
                if not any(bk in f"{n.text} {n.content_desc} {n.resource_id}".lower() for bk in ["back", "뒤로", "취소", "cancel", "navigate up"])
            ]
            candidates = forward_candidates if forward_candidates else clickable_nodes
            if candidates:
                scored = []
                for n in candidates:
                    sc = cls.score_interactive_node(n, visited_signatures=visited_signatures, goal_keywords=goal_keywords)
                    scored.append((sc, n))
                scored.sort(key=lambda x: x[0], reverse=True)
                chosen = scored[0][1]
                sig = cls.get_node_signature(chosen)
                return {
                    "action": "tap",
                    "x": chosen.center_x,
                    "y": chosen.center_y,
                    "reason": f"뒤로가기 루프 방어 발동 (BackKeyLoopDefender): 무한 루프/상태 진동 차단 후 전방 노드 ('{chosen.label}') 강제 전진",
                    "target_node": chosen.to_dict(),
                    "node_signature": sig,
                    "is_loop_defense": True,
                    "is_strategy_shift": True,
                    "strategy_shift_reason": f"BackKeyLoopDefender: 뒤로가기 루프/상태 진동 감지 후 전방 노드('{chosen.label}')로 탐색 전략 선회"
                }
            else:
                return {
                    "action": "swipe",
                    "x1": 360,
                    "y1": 900,
                    "x2": 360,
                    "y2": 400,
                    "reason": "뒤로가기 루프 방어 발동 (BackKeyLoopDefender): 클릭 대상 부재로 전방 스와이프 탐색",
                    "target_node": None,
                    "node_signature": "loop_defense::swipe_forward",
                    "is_loop_defense": True,
                    "is_strategy_shift": True,
                    "strategy_shift_reason": "BackKeyLoopDefender: 클릭 대상 부재로 전방 스와이프로 탐색 전략 선회"
                }

        # Priority 0.2: Autonomous Recovery Maneuver (Stagnant Screen / Deadlock / Modal Trap Escape)
        if stagnant_attempts >= 2:
            return {
                "action": "key_back",
                "x": 0,
                "y": 0,
                "reason": f"자율 회복 루프 (Autonomous Recovery): 동일 화면 정체({stagnant_attempts}회) 감지 후 뒤로가기(Back) 롤백 탈출",
                "target_node": None,
                "node_signature": "recovery::press_back",
                "is_recovery": True,
                "is_strategy_shift": True,
                "strategy_shift_reason": f"AutonomousRecovery: 동일 화면 정체({stagnant_attempts}회) 감지로 롤백 탈출 전략 선회"
            }

        # Priority 0.5: 6. 여러 브랜치 탐색 역추적 (Requirement 4 & 6: 여러 브랜치 탐색 - Multi-Branch Backtracking)
        if branch_tracker and nodes:
            clickable_nodes = [n for n in nodes if n.clickable]
            cand_sigs = [cls.get_node_signature(n) for n in clickable_nodes]
            screen_sig = cls.get_screen_signature(nodes)
            if branch_tracker.should_backtrack(screen_sig, cand_sigs):
                prev_branch = branch_tracker.backtrack_pop()
                return {
                    "action": "key_back",
                    "x": 0,
                    "y": 0,
                    "reason": f"6. 여러 브랜치 탐색 역추적(Backtrack): 현재 브랜치 탐색 완료 ➔ 상위 브랜치('{prev_branch}') 복귀",
                    "target_node": None,
                    "node_signature": f"branch::backtrack::{prev_branch}",
                    "is_branch_backtrack": True,
                    "is_recovery": True,
                    "is_strategy_shift": True,
                    "strategy_shift_reason": f"MultiBranchTracker: 하위 브랜치 탐색 완료로 상위 브랜치('{prev_branch}') 백트래킹 전략 선회"
                }

        is_game_canvas = False
        if not nodes:
            is_game_canvas = True
        elif len(nodes) <= 3 and any(any(sv in n.class_name.lower() for sv in ["surfaceview", "textureview", "glsurfaceview", "canvas"]) for n in nodes):
            is_game_canvas = True

        if is_game_canvas:
            if app_archetype in ["DRAWING_CANVAS", "drawing", "paint", "canvas"] and DrawingCanvasDriver:
                points = DrawingCanvasDriver.generate_bezier_points((200, 800), (540, 600), (880, 1100), num_steps=12)
                return {
                    "action": "stroke",
                    "points": points,
                    "x": 540,
                    "y": 800,
                    "reason": "드로잉 캔버스 전용 베지에 곡선 브러시 스트로크 드로잉 제스처 수행 (DrawingCanvasDriver)",
                    "target_node": None,
                    "node_signature": "vision::canvas_bezier_stroke",
                    "is_vision_anchor": True,
                    "perception_mode": "HYBRID_VISION",
                    "app_archetype": "DRAWING_CANVAS"
                }
            elif app_archetype in ["HIGH_PERF_GAMING", "game", "arcade"] and GamingEngineDriver:
                return {
                    "action": "joystick_drag",
                    "center": (250, 1800),
                    "target": (400, 1800),
                    "x": 400,
                    "y": 1800,
                    "reason": "게임 엔진 전용 가상 조이스틱 연속 스와이프 및 이동 제스처 수행 (GamingEngineDriver)",
                    "target_node": None,
                    "node_signature": "vision::game_joystick_drag",
                    "is_vision_anchor": True,
                    "perception_mode": "HYBRID_VISION",
                    "app_archetype": "HIGH_PERF_GAMING"
                }
            else:
                return {
                    "action": "tap",
                    "x": 540,
                    "y": 1200,
                    "reason": "하이브리드 비전 앵커 탐색 (Game & Canvas SurfaceView): 접근성 트리 부재 시 중앙 시작/플레이 액션 구역 자율 타겟팅",
                    "target_node": None,
                    "node_signature": "vision::canvas_action_anchor",
                    "is_vision_anchor": True,
                    "perception_mode": "HYBRID_VISION"
                }

        state_info = cls.classify_screen_state(nodes)
        state_type = state_info.get("type")
        target = state_info.get("target_node")

        # Priority 1: Dismiss Permission Dialogs
        if state_type == "PERMISSION_DIALOG" and target:
            sig = cls.get_node_signature(target)
            return {
                "action": "tap",
                "x": target.center_x,
                "y": target.center_y,
                "reason": f"권한 팝업 자동 승인 클릭 ('{target.label}')",
                "target_node": target.to_dict(),
                "node_signature": sig
            }

        # Priority 1.4: Network Error Dialog Recovery (Requirement 10: 네트워크 오류 복원)
        if state_type == "NETWORK_ERROR_DIALOG" and target:
            sig = cls.get_node_signature(target)
            return {
                "action": "tap",
                "x": target.center_x,
                "y": target.center_y,
                "reason": f"네트워크 오류/단절 팝업 재시도 및 복구 클릭 ('{target.label}')",
                "target_node": target.to_dict(),
                "node_signature": sig,
                "is_network_recovery": True
            }

        # Priority 1.5: Graceful Failure Recovery (Requirement 15: 의도적으로 실패 가능한 기능)
        if state_type == "ERROR_DIALOG" and target:
            sig = cls.get_node_signature(target)
            return {
                "action": "tap",
                "x": target.center_x,
                "y": target.center_y,
                "reason": f"의도적 오류/실패 다이얼로그 정상 복구 확인 ('{target.label}')",
                "target_node": target.to_dict(),
                "node_signature": sig,
                "is_graceful_recovery": True
            }

        # Priority 2: Bypass Onboarding / Tutorial Modal
        if state_type == "ONBOARDING_MODAL" and target:
            sig = cls.get_node_signature(target)
            return {
                "action": "tap",
                "x": target.center_x,
                "y": target.center_y,
                "reason": f"온보딩/모달 자동 통과 클릭 ('{target.label}')",
                "target_node": target.to_dict(),
                "node_signature": sig
            }

        # Priority 3: Target elements matching goal_keywords (e.g. ['서명', 'edit', 'camera'])
        if goal_keywords:
            for n in nodes:
                if not n.clickable and not n.focusable:
                    continue
                combined = f"{n.text} {n.content_desc} {n.resource_id}".lower()
                for gk in goal_keywords:
                    if gk.lower() in combined:
                        sig = cls.get_node_signature(n)
                        return {
                            "action": "tap",
                            "x": n.center_x,
                            "y": n.center_y,
                            "reason": f"목표 키워드 '{gk}' 일치 요소 클릭 ('{n.label}')",
                            "target_node": n.to_dict(),
                            "node_signature": sig
                        }

        # Priority 4: Action requested by test phase
        if prefer_action == "input":
            edit_nodes = [n for n in nodes if "edittext" in n.class_name.lower() or n.focusable]
            if edit_nodes:
                en = edit_nodes[0]
                sig = cls.get_node_signature(en)
                return {
                    "action": "input_text",
                    "x": en.center_x,
                    "y": en.center_y,
                    "text": "AGY_E2E_AUTO_TEST",
                    "reason": f"입력 폼 필드 발견 및 포커스 텍스트 인젝션 ('{en.label}')",
                    "target_node": en.to_dict(),
                    "node_signature": sig
                }

        if prefer_action == "scroll":
            scroll_nodes = [n for n in nodes if n.scrollable or "scrollview" in n.class_name.lower() or "recyclerview" in n.class_name.lower()]
            if scroll_nodes:
                sn = scroll_nodes[0]
                sig = cls.get_node_signature(sn)
                return {
                    "action": "swipe",
                    "x1": sn.center_x,
                    "y1": min(sn.y2 - 50, sn.center_y + 300),
                    "x2": sn.center_x,
                    "y2": max(sn.y1 + 50, sn.center_y - 300),
                    "reason": f"스크롤 가능 컨테이너 제스처 수행 ('{sn.label}')",
                    "target_node": sn.to_dict(),
                    "node_signature": sig
                }

        # Priority 5: Intelligent ActionScorer across ALL clickable candidates (Requirement 2: 버튼 20개 이상 밀집 UI 최적화)
        dense_analysis = cls.process_dense_button_environment(
            nodes,
            threshold=20,
            visited_signatures=visited_signatures,
            goal_keywords=goal_keywords
        )
        clickable_nodes = [n for n in nodes if n.clickable and n.enabled and n.width > 20 and n.height > 20]
        if clickable_nodes:
            scored_candidates = []
            for n in clickable_nodes:
                sc = cls.score_interactive_node(n, visited_signatures=visited_signatures, goal_keywords=goal_keywords)
                scored_candidates.append((sc, n))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, chosen = scored_candidates[0]
            sig = cls.get_node_signature(chosen)

            # 6. 여러 브랜치 탐색: 선택된 컴포넌트의 브랜치 진입 여부 판별 및 스택 등록
            is_branch_entry = False
            branch_label = ""
            combined_chosen = f"{chosen.text} {chosen.content_desc} {chosen.resource_id}".lower()
            BRANCH_KEYWORDS = [
                "탭", "tab", "메뉴", "menu", "카테고리", "category", "설정", "settings",
                "목록", "list", "브랜치", "branch", "서브", "more", "더보기", "전체보기",
                "상세", "detail", "view_more", "profile", "마이"
            ]
            if any(bk in combined_chosen for bk in BRANCH_KEYWORDS):
                is_branch_entry = True
                branch_label = chosen.label
                if branch_tracker:
                    branch_tracker.register_branch(branch_label)

            return {
                "action": "tap",
                "x": chosen.center_x,
                "y": chosen.center_y,
                "reason": f"지능형 ActionScorer 최고점 ({best_score:.1f}점) 자율 컴포넌트 탭 ('{chosen.label}') [밀집버튼: {dense_analysis['total_buttons']}개, dense={dense_analysis['is_dense']}]" + (f" [6. 여러 브랜치 탐색: '{branch_label}']" if is_branch_entry else ""),
                "target_node": chosen.to_dict(),
                "node_signature": sig,
                "score": best_score,
                "is_dense_button_mode": dense_analysis["is_dense"],
                "dense_analysis": dense_analysis,
                "is_branch_entry": is_branch_entry,
                "branch_name": branch_label
            }

        # Fallback: Hybrid Vision Anchor Selection
        return {
            "action": "tap",
            "x": 540,
            "y": 1200,
            "reason": "클릭 가능 노드 없음 - 하이브리드 비전 뷰포트 인터랙션 앵커 탭 수행",
            "target_node": None,
            "node_signature": "vision::fallback_anchor",
            "is_vision_anchor": True,
            "perception_mode": "HYBRID_VISION"
        }

    @classmethod
    def execute_autonomous_step(
        cls,
        run_adb_fn,
        package_name: str = "",
        goal_keywords: Optional[List[str]] = None,
        prefer_action: Optional[str] = None,
        visited_signatures: Optional[set] = None,
        branch_tracker: Optional[BranchNavigationTracker] = None,
        back_loop_defender: Optional[BackKeyLoopDefender] = None,
        stagnant_attempts: int = 0,
        app_archetype: str = "AUTO",
        capture_screen_fn=None,
        step_index: int = 1,
        artifact_dir: str = "/tmp/agy_e2e_evidence"
    ) -> Dict[str, Any]:
        """
        Executes a complete autonomous cycle via ADB:
        1. Dumps UI hierarchy to /data/local/tmp/uidump.xml
        2. Parses XML into semantic nodes
        3. Captures Before-Action screenshot if capture_screen_fn is provided
        4. Decides intelligent action using ActionScorer, Dense Button Classifier, & loop memory
        5. Executes action via ADB (supports tap, stroke, joystick_drag, input_text, swipe, key_back)
        6. Captures After-Action screenshot, calculates mutation delta, and validates genuine app screen
        7. Updates BackKeyLoopDefender state history
        """
        import time
        t_now = time.time()
        os.makedirs(artifact_dir, exist_ok=True)
        before_screenshot = os.path.join(artifact_dir, f"step_{step_index:02d}_before.png") if capture_screen_fn else None
        after_screenshot = os.path.join(artifact_dir, f"step_{step_index:02d}_after.png") if capture_screen_fn else None

        # Optional Before screenshot
        if capture_screen_fn and before_screenshot:
            try:
                capture_screen_fn(before_screenshot)
            except Exception as e:
                logger.debug(f"Before screenshot capture error: {e}")

        # Step 1: Dump hierarchy
        code, _, _ = run_adb_fn(["shell", "uiautomator", "dump", "/data/local/tmp/uidump.xml"], timeout=6.0)
        xml_content = ""
        if code == 0:
            c_code, out, _ = run_adb_fn(["shell", "cat", "/data/local/tmp/uidump.xml"], timeout=4.0)
            if c_code == 0 and out:
                xml_content = out

        # Step 2: Parse nodes
        nodes = cls.parse_xml_hierarchy(xml_content) if xml_content else []
        state_before = cls.classify_screen_state(nodes)

        # Step 3: Decide next action
        decision = cls.decide_next_action(
            nodes,
            goal_keywords=goal_keywords,
            prefer_action=prefer_action,
            visited_signatures=visited_signatures,
            stagnant_attempts=stagnant_attempts,
            branch_tracker=branch_tracker,
            back_loop_defender=back_loop_defender,
            app_archetype=app_archetype
        )

        # Step 4: Execute action
        act = decision.get("action")
        exec_ok = False
        if act == "stroke" and DrawingCanvasDriver:
            pts = decision.get("points", [])
            exec_ok = DrawingCanvasDriver.execute_stroke_adb(run_adb_fn, pts)
        elif act == "joystick_drag" and GamingEngineDriver:
            c = decision.get("center", (250, 1800))
            t = decision.get("target", (400, 1800))
            exec_ok = GamingEngineDriver.execute_joystick_adb(run_adb_fn, c, t)
        elif act == "tap":
            x, y = decision["x"], decision["y"]
            t_code, _, _ = run_adb_fn(["shell", "input", "tap", str(x), str(y)], timeout=4.0)
            exec_ok = (t_code == 0)
        elif act == "input_text":
            x, y = decision["x"], decision["y"]
            txt = decision.get("text", "AGY_TEST")
            run_adb_fn(["shell", "input", "tap", str(x), str(y)], timeout=3.0)
            t_code, _, _ = run_adb_fn(["shell", "input", "text", txt], timeout=4.0)
            exec_ok = (t_code == 0)
        elif act == "swipe":
            x1, y1 = decision.get("x1", 360), decision.get("y1", 1000)
            x2, y2 = decision.get("x2", 360), decision.get("y2", 300)
            t_code, _, _ = run_adb_fn(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "300"], timeout=4.0)
            exec_ok = (t_code == 0)
        elif act == "key_back":
            t_code, _, _ = run_adb_fn(["shell", "input", "keyevent", "4"], timeout=4.0)
            exec_ok = (t_code == 0)

        # Render stabilization delay after physical input to avoid capturing intermediate frame transitions
        if exec_ok and act in ("input_text", "tap", "swipe", "stroke", "joystick_drag", "key_back"):
            time.sleep(1.0)

        # Step 5: After screenshot & Mutation verification (Items 21~26)
        mutation_pct = 0.0
        is_valid_screen = True
        screenshot_captured = False
        if capture_screen_fn and after_screenshot:
            try:
                if capture_screen_fn(after_screenshot):
                    screenshot_captured = True
                    # Verify genuine app rendering
                    from mechanical_gate import MechanicalGate
                    render_chk = MechanicalGate.assert_genuine_rendering(after_screenshot)
                    is_valid_screen = render_chk.passed

                    # Verify Before/After mutation
                    if before_screenshot and os.path.exists(before_screenshot):
                        mut_chk = MechanicalGate.assert_action_screen_mutation(before_screenshot, after_screenshot, min_mutation_pct=0.1)
                        mutation_pct = mut_chk.metadata.get("mutation_pct", 0.0)
            except Exception as e:
                logger.debug(f"After screenshot capture/mutation check error: {e}")

        # Step 6: Record action in BackKeyLoopDefender if enabled
        screen_sig = cls.get_screen_signature(nodes) if nodes else "empty"
        if back_loop_defender and nodes:
            back_loop_defender.record_action(act or "", screen_sig)

        action_record = ActionScreenRecord(
            action_id=f"ACT_{step_index:03d}_{act}",
            timestamp=t_now,
            action_type=act or "tap",
            target_label=(
                getattr(decision.get("target_node"), "label", None)
                or (decision.get("target_node").get("label") if isinstance(decision.get("target_node"), dict) else None)
                or decision.get("node_signature", "")
            ),
            after_screenshot=after_screenshot if (after_screenshot and os.path.exists(after_screenshot)) else None,
            mutation_pct=mutation_pct,
            is_valid_app_screen=is_valid_screen,
            screenshot_captured=screenshot_captured,
            screen_state_before=state_before,
            screen_state_after=state_before,  # updated in next cycle
            execution_success=exec_ok,
            details=f"Mutation: {mutation_pct:.2f}%, ValidScreen: {is_valid_screen}"
        )

        decision["executed"] = exec_ok
        decision["nodes_found"] = len(nodes)
        decision["nodes"] = nodes
        decision["xml_content"] = xml_content
        decision["action_record"] = action_record.to_dict()
        decision["mutation_pct"] = mutation_pct
        decision["is_valid_app_screen"] = is_valid_screen
        return decision
