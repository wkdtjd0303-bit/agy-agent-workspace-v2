#!/usr/bin/env python3
"""
🌐 Unified Autonomous E2E Core Data Models (AGY 3.0 Standard)
Abstracts platform-independent E2E verification entities:
  - Intent: User goal and expected state transitions
  - Action: Normalized interactive step (tap, click, input, scroll, key, navigate)
  - Observation: Multi-modal snapshot (screenshot, DOM/UI tree, network, logs, process)
  - StateTransition: Visual and semantic delta between screen states
  - Assertion: Ground-truth verifiable condition
  - Evidence: Tamper-proof physical records (hashes, images, logs, traces)
  - Recovery: Dynamic strategy adaptation on stagnation or loop detection
  - Result: 4-State Machine verdict (PASS, FAIL, UNVERIFIED, BLOCKED)
"""

import os
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Union


class E2EVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"
    BLOCKED = "BLOCKED"


class PlatformType(str, Enum):
    WEB = "WEB"
    ANDROID = "ANDROID"
    IOS = "IOS"
    WINDOWS = "WINDOWS"
    UNKNOWN = "UNKNOWN"


class TechStackType(str, Enum):
    WEB_SPA = "WEB_SPA"
    WEB_SSR = "WEB_SSR"
    WEB_GAME = "WEB_GAME"
    WEB_PWA = "WEB_PWA"
    ANDROID_COMPOSE = "ANDROID_COMPOSE"
    ANDROID_VIEWS = "ANDROID_VIEWS"
    ANDROID_WEBVIEW = "ANDROID_WEBVIEW"
    ANDROID_GAME = "ANDROID_GAME"
    IOS_SWIFTUI = "IOS_SWIFTUI"
    IOS_UIKIT = "IOS_UIKIT"
    IOS_WEBVIEW = "IOS_WEBVIEW"
    WINDOWS_WIN32 = "WINDOWS_WIN32"
    WINDOWS_WPF = "WINDOWS_WPF"
    WINDOWS_WINUI = "WINDOWS_WINUI"
    WINDOWS_ELECTRON = "WINDOWS_ELECTRON"
    WINDOWS_GAME = "WINDOWS_GAME"
    CROSS_FLUTTER = "CROSS_FLUTTER"
    CROSS_REACT_NATIVE = "CROSS_REACT_NATIVE"
    CROSS_UNITY = "CROSS_UNITY"
    GENERIC = "GENERIC"


@dataclass
class E2EIntent:
    """User intention to be validated (e.g. 'User can create and persist a signed contract')."""
    intent_id: str = field(default_factory=lambda: f"intent_{uuid.uuid4().hex[:8]}")
    title: str = ""
    description: str = ""
    preconditions: List[str] = field(default_factory=list)
    expected_outcomes: List[str] = field(default_factory=list)
    required_states: List[str] = field(default_factory=list)
    is_critical_workflow: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2EAction:
    """Normalized interaction primitive."""
    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")
    action_type: str = "tap"  # tap, click, input_text, scroll, navigate, keyevent, back
    target_selector: str = ""
    coordinates: Optional[Tuple[int, int]] = None
    payload: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2EObservation:
    """Multi-modal state observation captured immediately following an action or event."""
    observation_id: str = field(default_factory=lambda: f"obs_{uuid.uuid4().hex[:8]}")
    action_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    screenshot_path: Optional[str] = None
    is_valid_screen: bool = False
    ui_hierarchy_xml_or_dom: Optional[str] = None
    interactive_elements_count: int = 0
    active_process_pid: Optional[int] = None
    is_foreground: bool = False
    console_errors: List[str] = field(default_factory=list)
    network_errors: List[str] = field(default_factory=list)
    logcat_or_syslog_snippets: List[str] = field(default_factory=list)
    screen_signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StateTransition:
    """Semantic and visual delta between consecutive observations."""
    from_signature: str
    to_signature: str
    mutation_pct: float
    is_meaningful_mutation: bool
    transition_type: str  # NAVIGATION, DIALOG_POPUP, INPUT_REFLECTED, STAGNANT, CRASH
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2EAssertion:
    """Verifiable ground-truth statement with physical evidence."""
    assertion_id: str = field(default_factory=lambda: f"asrt_{uuid.uuid4().hex[:8]}")
    rule_name: str = ""
    intent_id: Optional[str] = None
    passed: bool = False
    is_unverified: bool = False
    is_blocked: bool = False
    details: str = ""
    evidence_path_or_value: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2ERecoveryAttempt:
    """Record of autonomous error recovery or backtracking."""
    recovery_id: str = field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}")
    trigger_reason: str = ""  # STAGNATION, BACK_LOOP, CRASH, MODAL_TRAP
    strategy_shift: str = ""
    attempt_count: int = 1
    recovered: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class E2EExecutionResult:
    """Comprehensive execution result from the Autonomous E2E Engine."""
    platform: str
    framework: str
    target_identifier: str
    verdict: E2EVerdict
    intent_score: float = 0.0
    coverage_pct: float = 0.0
    action_success_pct: float = 0.0
    actions_executed: int = 0
    assertions_count: int = 0
    passed_assertions: int = 0
    failed_assertions: int = 0
    unverified_assertions: int = 0
    blocked_assertions: int = 0
    actions_trace: List[Dict[str, Any]] = field(default_factory=list)
    state_transitions: List[Dict[str, Any]] = field(default_factory=list)
    evidence_bundle: Dict[str, Any] = field(default_factory=dict)
    failure_reasons: List[str] = field(default_factory=list)
    recovery_attempts: List[Dict[str, Any]] = field(default_factory=list)
    audit_200_score: float = 0.0
    audit_200_details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["verdict"] = self.verdict.value if isinstance(self.verdict, E2EVerdict) else self.verdict
        return res
