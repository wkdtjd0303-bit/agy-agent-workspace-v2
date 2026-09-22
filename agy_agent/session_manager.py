import os
import time
import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any
from config import DEFAULT_WORKSPACE, DEFAULT_CHANNEL_WORKSPACES, STATE_FILE_PATH

logger = logging.getLogger(__name__)

def kill_child_processes(parent_pid: int):
    """Recursively kills all child and descendant processes without killing the parent process."""
    try:
        import subprocess
        out = subprocess.check_output(["pgrep", "-P", str(parent_pid)], text=True, timeout=2.0)
        children = [int(p) for p in out.strip().split() if p.isdigit()]
        for c in children:
            kill_process_tree(c)
    except Exception:
        pass

def kill_process_tree(pid: int):
    """Kills a process and all its child/grandchild processes, including the
    entire process session group (handles start_new_session=True subprocesses
    like AGY CLI Language Servers that spawn in a new PGID)."""
    import signal

    # 1. Kill the entire process group (PGID) — catches Language Server children
    try:
        pgid = os.getpgid(pid)
        if pgid > 1:
            os.killpg(pgid, signal.SIGTERM)
            # Brief grace period, then force-kill
            import threading
            def _force_kill_pg():
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            t = threading.Timer(0.5, _force_kill_pg)
            t.daemon = True
            t.start()
    except (ProcessLookupError, PermissionError, OSError):
        pass

    # 2. Recursive child kill via pgrep as fallback (for processes that escaped PGID)
    try:
        out = subprocess.check_output(["pgrep", "-P", str(pid)], text=True, timeout=2.0)
        children = [int(p) for p in out.strip().split() if p.isdigit()]
        for c in children:
            kill_process_tree(c)
    except Exception:
        pass

    # 3. Kill the process itself
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def kill_agy_language_servers():
    """Finds and kills orphaned AGY Language Server processes that linger after
    CLI sessions end, continuously polling Google Cloud APIs (loadCodeAssist,
    fetchAvailableModels) every 6 minutes and silently consuming quota.

    These servers are identified by their command line containing both
    'antigravity' and 'server' keywords, or by the language server port pattern.
    """
    import signal
    killed = []
    try:
        # Find AGY-related server processes (Language Server spawned by AGY CLI)
        result = subprocess.run(
            ["pgrep", "-f", "antigravity.*server|cloudcode.*server"],
            stdout=subprocess.PIPE, text=True, timeout=3.0
        )
        if result.returncode == 0 and result.stdout.strip():
            for pid_str in result.stdout.strip().split():
                if pid_str.isdigit():
                    pid = int(pid_str)
                    # Verify it's actually a language server, not our main process
                    try:
                        cmdline_path = f"/proc/{pid}/cmdline"
                        if os.path.exists(cmdline_path):
                            with open(cmdline_path, 'rb') as f:
                                cmdline = f.read().decode('utf-8', errors='replace')
                            # Skip if it's the main agy CLI with --print (active session)
                            if '--print' in cmdline or '--dangerously-skip-permissions' in cmdline:
                                continue
                        os.kill(pid, signal.SIGTERM)
                        killed.append(pid)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
    except Exception:
        pass

    if killed:
        logging.getLogger(__name__).info(
            f"[QuotaGuard] Killed {len(killed)} orphaned AGY Language Server(s): {killed}"
        )
    return killed


@dataclass
class QueuedItem:
    message: Any
    prompt: str
    created_at: float = field(default_factory=time.time)


def should_route_away_from_agy(model_name: Optional[str]) -> bool:
    """
    Ensures third-party OpenRouter models (Qwen, DeepSeek, Solar, etc.)
    route to Hermes/OpenRouter, while allowing native Antigravity models
    (Gemini, Claude Sonnet, Claude Opus, GPT-OSS).
    """
    if not model_name:
        return False
    m_low = model_name.lower().strip()
    if m_low.startswith("openrouter/"):
        return True
    # Native AGY models are explicitly permitted on agy engine
    if any(k in m_low for k in ("gpt-oss", "oss", "opus", "sonnet")):
        return False
    if any(k in m_low for k in ("qwen", "deepseek", "solar", "llama", "ling", "minimax")):
        return True
    return False


class ChannelSession:
    _on_change: Any = None
    queue: list = []
    is_stopped: bool = False
    is_busy: bool = False
    status: str = "idle"
    status_desc: str = "정상 대기 중"
    current_process: Any = None
    current_task: Any = None
    qa2_conversation_id: Optional[str] = None
    _extended_qa_conversation_ids: Dict[str, str] = {}
    selected_qa_indices: Optional[List[int]] = None

    @property
    def extended_qa_conversation_ids(self) -> Dict[str, str]:
        if not hasattr(self, "_extended_qa_conversation_ids") or not isinstance(self._extended_qa_conversation_ids, dict):
            self._extended_qa_conversation_ids = {}
        return self._extended_qa_conversation_ids

    @extended_qa_conversation_ids.setter
    def extended_qa_conversation_ids(self, val: Optional[Dict[str, str]]):
        self._extended_qa_conversation_ids = dict(val) if isinstance(val, dict) else {}

    def __init__(
        self,
        channel_id: int,
        workspace: str,
        conversation_id: Optional[str] = None,
        qa_conversation_id: Optional[str] = None,
        qa2_conversation_id: Optional[str] = None,
        auditor_conversation_id: Optional[str] = None,
        mode: str = "auto",
        auto_enabled: bool = False,
        auto_mode: str = "evolve",
        auto_idle_seconds: int = 600,
        auto_max_cycles: int = 20,
        last_url: Optional[str] = None,
        is_busy: bool = False,
        status: str = "idle",
        status_desc: str = "정상 대기 중",
        updated_at: Optional[str] = None,
        wait_for_green: bool = False,
        engine: str = "agy",
        engine_model: Optional[str] = None,
        role: str = "coder",
        qa_engine: str = "agy",
        qa_model: Optional[str] = None,
        qa_backup_model: Optional[str] = None,
        qa2_engine: str = "openrouter",
        qa2_model: Optional[str] = None,
        auditor_engine: str = "agy",
        extended_qa_conversation_ids: Optional[Dict[str, str]] = None,
        selected_qa_indices: Optional[List[int]] = None,
        user_directive: Optional[str] = None,
        root_user_goal: Optional[str] = None,
        commander_macro_history: Optional[List[Dict[str, Any]]] = None,
        on_change=None
    ):
        self.channel_id = channel_id
        self.workspace = workspace
        self.conversation_id: Optional[str] = conversation_id
        self.qa_conversation_id: Optional[str] = qa_conversation_id
        self.qa2_conversation_id: Optional[str] = qa2_conversation_id
        self.extended_qa_conversation_ids: Dict[str, str] = dict(extended_qa_conversation_ids) if isinstance(extended_qa_conversation_ids, dict) else {}
        self._extended_qa_conversation_ids = self.extended_qa_conversation_ids
        self.auditor_conversation_id: Optional[str] = auditor_conversation_id
        init_mode = (mode or "auto").lower().strip()
        if init_mode not in ("auto", "read", "pair_quad", "single_qa2", "pair", "single_plus", "single", "mode3", "mode3_plus"):
            init_mode = "auto"
        self.mode: str = init_mode  # "auto", "pair", "pair_quad", "single", "single_plus", "single_qa2", "read", "mode3", or "mode3_plus"
        self.auto_enabled: bool = auto_enabled
        self.auto_mode: str = auto_mode
        self.auto_idle_seconds: int = auto_idle_seconds
        self.auto_max_cycles: int = auto_max_cycles
        self.last_url: Optional[str] = last_url
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.current_task: Optional[asyncio.Task] = None
        self.is_busy: bool = is_busy
        self.status: str = status  # "idle", "busy", "error", "warning"
        self.status_desc: str = status_desc
        self.updated_at: str = updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.wait_for_green: bool = bool(wait_for_green)
        self.engine_model: Optional[str] = engine_model.strip() if engine_model else None
        init_engine = (engine or "agy").lower().strip()
        # [🛡️ Token Protection Mandate - Solar Pro 4 Architecture Protocol]
        # Auto-route any external/third-party models to hermes to prevent draining Antigravity quota
        if init_engine == "agy" and should_route_away_from_agy(self.engine_model):
            init_engine = "hermes"
        self.engine: str = init_engine
        self.role: str = (role or "coder").lower().strip()  # "coder" or "qa"
        self.qa_engine: str = (qa_engine or "agy").lower().strip()
        self.qa_model: Optional[str] = qa_model.strip() if qa_model else None
        self.qa_backup_model: Optional[str] = qa_backup_model.strip() if qa_backup_model else None
        self.qa2_engine: str = (qa2_engine or "openrouter").lower().strip()
        self.qa2_model: Optional[str] = qa2_model.strip() if qa2_model else None
        self.auditor_engine: str = (auditor_engine or "agy").lower().strip()
        self.selected_qa_indices: Optional[List[int]] = selected_qa_indices
        self.user_directive: Optional[str] = user_directive
        self.root_user_goal: Optional[str] = root_user_goal
        self.commander_macro_history: List[Dict[str, Any]] = list(commander_macro_history) if isinstance(commander_macro_history, list) else []
        self.is_stopped: bool = False
        self.stop_after_current_cycle: bool = False
        self.queue: List[QueuedItem] = []
        self.user_reference_images: List[Dict[str, Any]] = []
        self.round_deliverable_image_hashes: Dict[int, Dict[str, Any]] = {}
        self._on_change = on_change

    def get_user_directive(self) -> Optional[str]:
        """Returns the active user directive."""
        return getattr(self, "user_directive", None)

    def set_user_directive(self, directive: Optional[str]):
        """Sets or updates the active user directive."""
        self.user_directive = directive
        if self._on_change:
            self._on_change()

    def clear_user_directive(self):
        """Clears the active user directive."""
        self.user_directive = None
        if self._on_change:
            self._on_change()

    def get_root_user_goal(self) -> Optional[str]:
        """Returns the immutable root user goal for Mode 3 / 3+."""
        return getattr(self, "root_user_goal", None) or getattr(self, "user_directive", None)

    def set_root_user_goal(self, goal: Optional[str]):
        """Sets or updates the root user goal for Mode 3 / 3+."""
        self.root_user_goal = goal
        if self._on_change:
            self._on_change()

    def clear_root_user_goal(self):
        """Clears the root user goal."""
        self.root_user_goal = None
        if self._on_change:
            self._on_change()

    def register_user_reference_image(
        self,
        filename: str,
        file_path: str,
        sha256: str,
        dhash: Optional[str] = None,
        mtime: Optional[float] = None,
        size_bytes: int = 0
    ):
        """Registers an incoming user reference image attachment with cryptographic and perceptual fingerprints."""
        if not hasattr(self, "user_reference_images") or not isinstance(self.user_reference_images, list):
            self.user_reference_images = []
        for item in self.user_reference_images:
            if item.get("sha256") == sha256:
                return
        self.user_reference_images.append({
            "filename": filename,
            "path": file_path,
            "sha256": sha256,
            "dhash": dhash,
            "mtime": mtime or time.time(),
            "size_bytes": size_bytes
        })

    def clear_user_reference_images(self):
        """Clears reference images and round deliverable history for a clean task slate."""
        self.user_reference_images = []
        self.round_deliverable_image_hashes = {}

    @property
    def user_reference_hashes_set(self) -> set:
        """Returns a set of all SHA-256 hashes of user reference images."""
        if not hasattr(self, "user_reference_images") or not self.user_reference_images:
            return set()
        return {item["sha256"] for item in self.user_reference_images if "sha256" in item}

    @property
    def user_reference_dhashes_set(self) -> set:
        """Returns a set of all perceptual dHashes of user reference images."""
        if not hasattr(self, "user_reference_images") or not self.user_reference_images:
            return set()
        return {item["dhash"] for item in self.user_reference_images if item.get("dhash")}


    def enqueue(self, message: Any, prompt: str) -> int:
        self.queue.append(QueuedItem(message=message, prompt=prompt))
        if self._on_change:
            self._on_change()
        return len(self.queue)

    def dequeue(self) -> Optional[QueuedItem]:
        if self.queue:
            item = self.queue.pop(0)
            if self._on_change:
                self._on_change()
            return item
        return None

    def peek_queue(self) -> List[QueuedItem]:
        return list(self.queue)

    def clear_queue(self) -> int:
        count = len(self.queue)
        self.queue.clear()
        if count > 0 and self._on_change:
            self._on_change()
        return count

    def stop(self) -> bool:
        """Immediately stops the running process, kills all child processes, and cancels the orchestration task."""
        stopped = False
        self.is_stopped = True
        if self.current_process:
            try:
                if self.current_process.pid:
                    kill_process_tree(self.current_process.pid)
                self.current_process.kill()
                stopped = True
            except Exception:
                pass
            self.current_process = None

        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            stopped = True
            self.current_task = None

        if self.is_busy:
            self.is_busy = False
            stopped = True

        self.clear_user_directive()
        self.stop_after_current_cycle = False
        self.status = "idle"
        self.status_desc = "정상 대기 중 (작업 중단)"
        if self._on_change:
            self._on_change()

        return stopped

    def reset_conversation(self):
        self.conversation_id = None
        self.qa_conversation_id = None
        self.qa2_conversation_id = None
        self.extended_qa_conversation_ids = {}
        self.auditor_conversation_id = None
        self.clear_user_reference_images()
        if self._on_change:
            self._on_change()

    def reset_cycle_workers(self):
        """
        [Mode 3 Context Isolation & Anti-Tangling]
        Resets Coder, QA1, QA2, and all extended QAs' conversation IDs upon Auditor approval.
        Ensures Coder and QA enter the next milestone cycle completely fresh (0 context baggage),
        preventing memory tangling ('꼬임') and eliminating token explosion.
        Preserves Commander's macro memory.
        """
        self.reset_conversation()

    def record_commander_macro_cycle(self, milestone: str, commander_directive: str, auditor_conclusion: str):
        """
        [Mode 3 Macro Memory]
        Records a clean macro-level cycle entry containing ONLY Commander's directive
        and Auditor's final approval conclusion. Completely excludes intermediate
        Coder and QA tool logs to eliminate context bloat and memory tangling.
        """
        if not hasattr(self, "commander_macro_history") or not isinstance(self.commander_macro_history, list):
            self.commander_macro_history = []
        entry = {
            "cycle": len(self.commander_macro_history) + 1,
            "milestone": milestone,
            "commander_directive": (commander_directive or "").strip()[:500],
            "auditor_conclusion": (auditor_conclusion or "").strip()[:600],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        self.commander_macro_history.append(entry)
        if len(self.commander_macro_history) > 10:
            self.commander_macro_history.pop(0)
        if self._on_change:
            self._on_change()

    def get_commander_macro_history(self) -> List[Dict[str, Any]]:
        return list(getattr(self, "commander_macro_history", []))

    def format_commander_macro_memory(self) -> str:
        """
        Formats a clean macro memory summary for Commander containing only
        prior Commander directives and Auditor approvals.
        """
        hist = getattr(self, "commander_macro_history", [])
        if not hist:
            return ""
        lines = ["**[👑 Commander & ⚖️ Auditor 직전 회차 공인 확정 기록 (Macro Memory)]**:"]
        for rec in hist[-3:]:
            c_num = rec.get("cycle", 1)
            ms_title = rec.get("milestone", "이전 단계")
            c_dir = rec.get("commander_directive", "")[:180]
            a_con = rec.get("auditor_conclusion", "")[:220]
            lines.append(
                f"• (회차 {c_num}: {ms_title})\n"
                f"  - 👑 사령관 지침: \"{c_dir}\"\n"
                f"  - ⚖️ 감사관 승인 결론: \"{a_con}\""
            )
        return "\n".join(lines)

    def set_conversation_id(self, conv_id: str):
        if self.conversation_id != conv_id:
            self.conversation_id = conv_id
            if self._on_change:
                self._on_change()

    def set_qa_conversation_id(self, conv_id: str):
        if self.qa_conversation_id != conv_id:
            self.qa_conversation_id = conv_id
            if self._on_change:
                self._on_change()

    def set_qa2_conversation_id(self, conv_id: str):
        if getattr(self, "qa2_conversation_id", None) != conv_id:
            self.qa2_conversation_id = conv_id
            self.extended_qa_conversation_ids["qa2"] = conv_id
            if self._on_change:
                self._on_change()

    def get_extended_qa_conversation_id(self, role: str) -> Optional[str]:
        role_lower = (role or "").lower().strip()
        if role_lower == "qa2":
            return getattr(self, "qa2_conversation_id", None) or self.extended_qa_conversation_ids.get("qa2")
        return self.extended_qa_conversation_ids.get(role_lower)

    def set_extended_qa_conversation_id(self, role: str, conv_id: str):
        role_lower = (role or "").lower().strip()
        if role_lower == "qa2":
            self.qa2_conversation_id = conv_id
        if self.extended_qa_conversation_ids.get(role_lower) != conv_id:
            self.extended_qa_conversation_ids[role_lower] = conv_id
            if self._on_change:
                self._on_change()

    def set_auditor_conversation_id(self, conv_id: str):
        if self.auditor_conversation_id != conv_id:
            self.auditor_conversation_id = conv_id
            if self._on_change:
                self._on_change()

    def set_selected_qa_indices(self, indices: Optional[List[int]]):
        """Sets or resets the selected Extended QA indices for this session."""
        if indices is None:
            self.selected_qa_indices = None
        else:
            cleaned = []
            for x in indices:
                try:
                    ix = int(x)
                    if ix not in cleaned and 1 <= ix <= 20:
                        cleaned.append(ix)
                except (ValueError, TypeError):
                    pass
            self.selected_qa_indices = cleaned if cleaned else None
        if self._on_change:
            self._on_change()

    def get_active_extended_qas(self, all_configs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        Returns the subset of extended QA configurations active for this session.
        If selected_qa_indices is set, filters all_configs to only those indices.
        Otherwise returns all configured extended QAs.
        """
        if all_configs is None:
            try:
                import config
                all_configs = config.get_extended_qa_configs()
            except Exception:
                all_configs = []
        if not self.selected_qa_indices:
            return list(all_configs or [])
        return [cfg for cfg in (all_configs or []) if isinstance(cfg, dict) and cfg.get("index") in self.selected_qa_indices]

    def set_mode(self, mode: str):
        mode = mode.lower().strip()
        first_token = mode.split()[0] if mode else ""
        if mode in ("auto", "automatic", "smart", "ai", "자율", "자동", "오토") or first_token in ("auto", "automatic", "smart", "ai", "자율", "자동", "오토"):
            self.mode = "auto"
        elif mode in ("read", "readonly", "read-only", "consult", "consulting", "plan", "planning", "읽기", "기획", "자문", "방향성") or first_token in ("read", "readonly", "read-only", "consult", "consulting", "plan", "planning", "읽기", "기획", "자문", "방향성"):
            self.mode = "read"
        elif (
            mode in ("3+", "3plus", "3플러스", "mode3_plus", "mode3+", "자율3+", "무인3+")
            or mode.replace(" ", "") in ("3+", "3plus", "3플러스", "mode3_plus", "mode3+")
            or first_token in ("3+", "3plus", "3플러스", "mode3_plus", "mode3+")
        ):
            self.mode = "mode3_plus"  # 무인 자율 순환 다중 협업 모드 (!mode 3+: Commander ➔ Coder ➔ QA1 ➔ QAs ➔ Auditor ➔ Commander)
        elif (
            mode in ("3", "mode3", "자율3", "무인3", "3자율", "3차자율")
            or mode.replace(" ", "") in ("3", "mode3")
            or first_token in ("3", "mode3")
        ):
            self.mode = "mode3"  # 무인 자율 순환 3중 협업 모드 (!mode 3: Commander ➔ Coder ↔ QA ↔ Auditor ➔ Commander)
        elif (
            mode in ("pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스")
            or mode.replace(" ", "") in ("pair+", "pair_plus", "pair_quad", "quad_pair", "2+", "2plus", "2플러스")
            or first_token in ("pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스")
        ):
            self.mode = "pair_quad"  # 사용자 주도 다중 협업 모드 (!mode 2+: Coder -> QA1 -> QAs -> Auditor -> 사용자 대기)
        elif (
            mode in ("single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo", "solo_qa2")
            or mode.replace(" ", "") in ("single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo", "solo_qa2")
            or first_token in ("single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo", "solo_qa2")
        ):
            self.mode = "single_qa2"  # 단일 QA2 에이전트 모드 (1545711652987600937 봇 단독 동작)
        elif mode in ("pair", "trio", "dual", "team", "collab", "3중", "페어", "협업", "2") or first_token in ("pair", "trio", "dual", "team", "collab", "3중", "페어", "협업", "2"):
            self.mode = "pair"  # 사용자 주도 3중 협업 모드 (!mode 2: Coder ↔ QA ↔ Auditor -> 사용자 대기)
        elif (
            mode in ("single_plus", "single+", "1+", "1plus", "1플러스", "single_consultant", "consultant", "컨설턴트")
            or mode.replace(" ", "") in ("single+", "1+", "single_plus")
            or first_token in ("single_plus", "single+", "1+", "1plus", "1플러스", "single_consultant", "consultant", "컨설턴트")
        ):
            self.mode = "single_plus"  # 단일 컨설턴트 모드 (!mode 1+ / !mode single+: GPT Astra 등 고사양 모델 단일 자문)
        elif mode in ("single", "solo", "단일", "싱글", "1") or first_token in ("single", "solo", "단일", "싱글", "1"):
            self.mode = "single"
        else:
            self.mode = "auto"
        if self._on_change:
            self._on_change()

    def set_auto(self, enabled: Optional[bool] = None, mode: Optional[str] = None, idle_seconds: Optional[int] = None, last_url: Optional[str] = None, max_cycles: Optional[int] = None):
        if enabled is not None:
            self.auto_enabled = enabled
        if mode is not None:
            self.auto_mode = mode
        if idle_seconds is not None:
            self.auto_idle_seconds = idle_seconds
        if max_cycles is not None:
            self.auto_max_cycles = max_cycles
        if last_url is not None:
            self.last_url = last_url
        if self._on_change:
            self._on_change()

    def set_workspace(self, path: str):
        if not path:
            path = DEFAULT_WORKSPACE
        abs_path = os.path.abspath(os.path.expanduser(str(path)))
        try:
            os.makedirs(abs_path, exist_ok=True)
            if not os.path.isdir(abs_path):
                raise OSError(f"Target path is not a directory: {abs_path}")
        except Exception:
            abs_path = os.path.abspath(os.path.expanduser(str(DEFAULT_WORKSPACE)))
            try:
                os.makedirs(abs_path, exist_ok=True)
            except Exception:
                pass
        self.workspace = abs_path
        if self._on_change:
            self._on_change()

    def set_busy(self, is_busy: bool, desc: str = "작업 중"):
        self.is_busy = is_busy
        if is_busy:
            self.is_stopped = False
        self.status = "busy" if is_busy else "idle"
        self.status_desc = desc
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self._on_change:
            self._on_change()

    def set_status(self, status: str, desc: str = ""):
        self.status = status
        self.is_busy = (status == "busy")
        self.status_desc = desc
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self._on_change:
            self._on_change()

    def set_wait_for_green(self, enabled: bool):
        self.wait_for_green = enabled
        if self._on_change:
            self._on_change()

    def set_engine(self, engine: str):
        target = (engine or "agy").lower().strip()
        # [🛡️ Token Protection Mandate - Solar Pro 4 Architecture Protocol]
        if target == "agy" and should_route_away_from_agy(self.engine_model):
            target = "hermes"
        self.engine = target
        if self._on_change:
            self._on_change()

    def set_engine_model(self, model: Optional[str]):
        self.engine_model = model.strip() if model else None
        # [🛡️ Token Protection Mandate - Solar Pro 4 Architecture Protocol]
        if self.engine == "agy" and should_route_away_from_agy(self.engine_model):
            self.engine = "hermes"
        if self._on_change:
            self._on_change()

    def set_role(self, role: str):
        self.role = (role or "coder").lower().strip()
        if self._on_change:
            self._on_change()

    def set_qa_engine(self, engine: str):
        self.qa_engine = (engine or "agy").lower().strip()
        if self._on_change:
            self._on_change()

    def set_qa_model(self, model: Optional[str]):
        self.qa_model = model.strip() if model else None
        if self._on_change:
            self._on_change()

    def set_qa_backup_model(self, model: Optional[str]):
        self.qa_backup_model = model.strip() if model else None
        if self._on_change:
            self._on_change()

    def set_auditor_engine(self, engine: str):
        self.auditor_engine = (engine or "agy").lower().strip()
        if self._on_change:
            self._on_change()

    def to_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "conversation_id": self.conversation_id,
            "qa_conversation_id": self.qa_conversation_id,
            "qa2_conversation_id": getattr(self, "qa2_conversation_id", None),
            "extended_qa_conversation_ids": getattr(self, "extended_qa_conversation_ids", {}),
            "auditor_conversation_id": self.auditor_conversation_id,
            "mode": self.mode,
            "auto_enabled": self.auto_enabled,
            "auto_mode": self.auto_mode,
            "auto_idle_seconds": self.auto_idle_seconds,
            "auto_max_cycles": getattr(self, "auto_max_cycles", 20),
            "last_url": self.last_url,
            "is_busy": getattr(self, "is_busy", False),
            "status": getattr(self, "status", "idle"),
            "status_desc": getattr(self, "status_desc", "정상 대기 중"),
            "updated_at": getattr(self, "updated_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            "wait_for_green": getattr(self, "wait_for_green", False),
            "engine": getattr(self, "engine", "agy"),
            "engine_model": getattr(self, "engine_model", None),
            "role": getattr(self, "role", "coder"),
            "qa_engine": getattr(self, "qa_engine", "agy"),
            "qa_model": getattr(self, "qa_model", None),
            "qa_backup_model": getattr(self, "qa_backup_model", None),
            "qa2_engine": getattr(self, "qa2_engine", "openrouter"),
            "qa2_model": getattr(self, "qa2_model", None),
            "auditor_engine": getattr(self, "auditor_engine", "agy"),
            "selected_qa_indices": getattr(self, "selected_qa_indices", None),
            "user_directive": getattr(self, "user_directive", None),
            "root_user_goal": getattr(self, "root_user_goal", None),
            "commander_macro_history": getattr(self, "commander_macro_history", [])[-10:],
            "queue_count": len(getattr(self, "queue", [])),
            "queue": [
                {
                    "prompt": item.prompt[:200],
                    "created_at": item.created_at
                }
                for item in getattr(self, "queue", [])
            ]
        }

class SessionManager:
    def __init__(self, state_file: str = STATE_FILE_PATH, clear_on_startup: bool = True):
        self._state_file = state_file
        self._sessions: Dict[int, ChannelSession] = {}
        self._last_mtime: float = 0.0
        self._load_state(clear_on_startup)

    def _load_state(self, clear_on_startup: bool):
        saved_data = {}
        if os.path.exists(self._state_file):
            try:
                self._last_mtime = os.path.getmtime(self._state_file)
                with open(self._state_file, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read session state file: {e}")

        # Initialize from saved data
        for ch_str, data in saved_data.items():
            try:
                ch_id = int(ch_str)
                preset_ws = DEFAULT_CHANNEL_WORKSPACES.get(ch_id)
                workspace = data.get("workspace")
                # If channel has a dedicated preset workspace and saved workspace was the generic root, enforce preset
                if preset_ws and (not workspace or workspace == DEFAULT_WORKSPACE):
                    workspace = preset_ws

                if not workspace:
                    workspace = DEFAULT_WORKSPACE

                conv_id = data.get("conversation_id")
                qa_conv_id = data.get("qa_conversation_id")
                qa2_conv_id = data.get("qa2_conversation_id")
                extended_qa_conv_ids = data.get("extended_qa_conversation_ids") or {}
                auditor_conv_id = data.get("auditor_conversation_id")
                mode = data.get("mode", "auto")
                if mode not in ("auto", "read", "pair_quad", "single_qa2", "pair", "single_plus", "single", "mode3", "mode3_plus"):
                    mode = "auto"
                auto_enabled = data.get("auto_enabled", False)
                auto_mode = data.get("auto_mode", "evolve")
                auto_idle = data.get("auto_idle_seconds", 600)
                last_url = data.get("last_url")
                # Always start clean on startup (previous dead processes are cleared)
                if clear_on_startup:
                    is_busy = False
                    status = "idle"
                    status_desc = "정상 대기 중"
                else:
                    is_busy = data.get("is_busy", False)
                    status = data.get("status", "idle")
                    status_desc = data.get("status_desc", "정상 대기 중")
                updated_at = data.get("updated_at")
                wait_for_green = data.get("wait_for_green", False)
                engine = data.get("engine", "agy")
                engine_model = data.get("engine_model")
                role = data.get("role", "coder")
                qa_engine = data.get("qa_engine", "agy")
                qa_model = data.get("qa_model")
                qa_backup_model = data.get("qa_backup_model")
                qa2_engine = data.get("qa2_engine", "openrouter")
                qa2_model = data.get("qa2_model")
                auditor_engine = data.get("auditor_engine", "agy")
                selected_qa_indices = data.get("selected_qa_indices")
                user_directive = data.get("user_directive")
                root_user_goal = data.get("root_user_goal")
                cmd_macro_hist = data.get("commander_macro_history") or []

                auto_max_cycles = data.get("auto_max_cycles", 20)

                self._sessions[ch_id] = ChannelSession(
                    channel_id=ch_id,
                    workspace=workspace,
                    conversation_id=conv_id,
                    qa_conversation_id=qa_conv_id,
                    qa2_conversation_id=qa2_conv_id,
                    auditor_conversation_id=auditor_conv_id,
                    mode=mode,
                    auto_enabled=auto_enabled,
                    auto_mode=auto_mode,
                    auto_idle_seconds=auto_idle,
                    auto_max_cycles=auto_max_cycles,
                    last_url=last_url,
                    is_busy=is_busy,
                    status=status,
                    status_desc=status_desc,
                    updated_at=updated_at,
                    wait_for_green=wait_for_green,
                    engine=engine,
                    engine_model=engine_model,
                    role=role,
                    qa_engine=qa_engine,
                    qa_model=qa_model,
                    qa_backup_model=qa_backup_model,
                    qa2_engine=qa2_engine,
                    qa2_model=qa2_model,
                    auditor_engine=auditor_engine,
                    extended_qa_conversation_ids=extended_qa_conv_ids,
                    selected_qa_indices=selected_qa_indices,
                    user_directive=user_directive,
                    root_user_goal=root_user_goal,
                    commander_macro_history=cmd_macro_hist,
                    on_change=self.save_state
                )
            except Exception as e:
                logger.error(f"Error loading session for {ch_str}: {e}")

        # Ensure default preset channels exist
        for ch_id, default_ws in DEFAULT_CHANNEL_WORKSPACES.items():
            if ch_id not in self._sessions:
                self._sessions[ch_id] = ChannelSession(
                    channel_id=ch_id,
                    workspace=default_ws,
                    conversation_id=None,
                    qa_conversation_id=None,
                    auditor_conversation_id=None,
                    mode="auto",
                    auto_enabled=False,
                    auto_mode="evolve",
                    auto_idle_seconds=600,
                    auto_max_cycles=20,
                    last_url=None,
                    on_change=self.save_state
                )

        self.save_state()

    def sync_from_disk_if_modified(self):
        """Synchronizes session configurations from disk if channel_sessions.json was modified externally."""
        if not os.path.exists(self._state_file):
            return
        try:
            current_mtime = os.path.getmtime(self._state_file)
            if current_mtime <= getattr(self, "_last_mtime", 0.0):
                return
            self._last_mtime = current_mtime
            with open(self._state_file, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            for ch_str, data in saved_data.items():
                try:
                    ch_id = int(ch_str)
                    if ch_id in self._sessions:
                        sess = self._sessions[ch_id]
                        if "workspace" in data and data["workspace"]:
                            sess.workspace = data["workspace"]
                        if "status_desc" in data and data["status_desc"]:
                            sess.status_desc = data["status_desc"]
                        if "status" in data and data["status"]:
                            sess.status = data["status"]
                        sess.mode = data.get("mode", sess.mode)
                        sess.auto_enabled = data.get("auto_enabled", sess.auto_enabled)
                        sess.auto_mode = data.get("auto_mode", sess.auto_mode)
                        sess.auto_idle_seconds = data.get("auto_idle_seconds", sess.auto_idle_seconds)
                        sess.auto_max_cycles = data.get("auto_max_cycles", getattr(sess, "auto_max_cycles", 20))
                        sess.engine = data.get("engine", getattr(sess, "engine", "agy"))
                        sess.engine_model = data.get("engine_model", getattr(sess, "engine_model", None))
                        sess.role = data.get("role", getattr(sess, "role", "coder"))
                        sess.qa_engine = data.get("qa_engine", getattr(sess, "qa_engine", "agy"))
                        sess.qa_model = data.get("qa_model", getattr(sess, "qa_model", None))
                        sess.qa_backup_model = data.get("qa_backup_model", getattr(sess, "qa_backup_model", None))
                        sess.auditor_engine = data.get("auditor_engine", getattr(sess, "auditor_engine", "agy"))
                        if "selected_qa_indices" in data:
                            sess.selected_qa_indices = data.get("selected_qa_indices")
                        sess.qa2_engine = data.get("qa2_engine", getattr(sess, "qa2_engine", "openrouter"))
                        sess.qa2_model = data.get("qa2_model", getattr(sess, "qa2_model", None))
                        if "extended_qa_conversation_ids" in data:
                            sess.extended_qa_conversation_ids = data.get("extended_qa_conversation_ids") or {}
                        if "commander_macro_history" in data:
                            sess.commander_macro_history = list(data.get("commander_macro_history") or [])
                        if not sess.is_busy:
                            sess.last_url = data.get("last_url", sess.last_url)
                            if "conversation_id" in data:
                                sess.conversation_id = data.get("conversation_id")
                            if "qa_conversation_id" in data:
                                sess.qa_conversation_id = data.get("qa_conversation_id")
                            if "qa2_conversation_id" in data:
                                sess.qa2_conversation_id = data.get("qa2_conversation_id")
                            if "auditor_conversation_id" in data:
                                sess.auditor_conversation_id = data.get("auditor_conversation_id")
                    else:
                        preset_ws = DEFAULT_CHANNEL_WORKSPACES.get(ch_id)
                        workspace = data.get("workspace") or preset_ws or DEFAULT_WORKSPACE
                        self._sessions[ch_id] = ChannelSession(
                            channel_id=ch_id,
                            workspace=workspace,
                            conversation_id=data.get("conversation_id"),
                            qa_conversation_id=data.get("qa_conversation_id"),
                            auditor_conversation_id=data.get("auditor_conversation_id"),
                            mode=data.get("mode", "auto"),
                            auto_enabled=data.get("auto_enabled", False),
                            commander_macro_history=data.get("commander_macro_history"),
                            auto_mode=data.get("auto_mode", "evolve"),
                            auto_idle_seconds=data.get("auto_idle_seconds", 600),
                            auto_max_cycles=data.get("auto_max_cycles", 20),
                            last_url=data.get("last_url"),
                            is_busy=data.get("is_busy", False),
                            status=data.get("status", "idle"),
                            status_desc=data.get("status_desc", "정상 대기 중"),
                            updated_at=data.get("updated_at"),
                            wait_for_green=data.get("wait_for_green", False),
                            engine=data.get("engine", "agy"),
                            engine_model=data.get("engine_model"),
                            role=data.get("role", "coder"),
                            qa_engine=data.get("qa_engine", "agy"),
                            qa_model=data.get("qa_model"),
                            qa_backup_model=data.get("qa_backup_model"),
                            auditor_engine=data.get("auditor_engine", "agy"),
                            selected_qa_indices=data.get("selected_qa_indices"),
                            qa2_engine=data.get("qa2_engine", "openrouter"),
                            qa2_model=data.get("qa2_model"),
                            extended_qa_conversation_ids=data.get("extended_qa_conversation_ids") or {},
                            on_change=self.save_state
                        )
                except Exception as ex:
                    logger.debug(f"Error syncing channel {ch_str}: {ex}")
            logger.info("Successfully synchronized session states from disk.")
        except Exception as e:
            logger.error(f"Failed to sync state from disk: {e}")

    @property
    def sessions(self) -> Dict[int, ChannelSession]:
        self.sync_from_disk_if_modified()
        return self._sessions

    def save_state(self):
        try:
            self.sync_from_disk_if_modified()
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            import config
            for ch_id, default_ws in config.DEFAULT_CHANNEL_WORKSPACES.items():
                if ch_id in self._sessions:
                    sess = self._sessions[ch_id]
                    if default_ws and sess.workspace != default_ws and not sess.is_busy and (sess.workspace == config.DEFAULT_WORKSPACE or not os.path.exists(sess.workspace)):
                        sess.workspace = default_ws
                else:
                    self._sessions[ch_id] = ChannelSession(
                        channel_id=ch_id,
                        workspace=default_ws,
                        conversation_id=None,
                        qa_conversation_id=None,
                        auditor_conversation_id=None,
                        mode="auto",
                        auto_enabled=False,
                        auto_mode="evolve",
                        auto_idle_seconds=600,
                        auto_max_cycles=20,
                        last_url=default_ws,
                        status="idle",
                        status_desc="정상 대기 중 (초기화 완료)",
                        on_change=self.save_state
                    )
            data_to_save = {
                str(ch_id): sess.to_dict()
                for ch_id, sess in self._sessions.items()
            }
            temp_file = f"{self._state_file}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, self._state_file)
            self._last_mtime = os.path.getmtime(self._state_file)
        except Exception as e:
            logger.error(f"Failed to save session state file: {e}")

    def set_all_modes(self, mode: str):
        """Sets the execution mode for all channels simultaneously."""
        for sess in self._sessions.values():
            sess.set_mode(mode)
        self.save_state()

    def set_all_auto(self, enabled: Optional[bool] = None, mode: Optional[str] = None, max_cycles: Optional[int] = None):
        """Sets auto-pilot configuration for all channels simultaneously."""
        for sess in self._sessions.values():
            sess.set_auto(enabled=enabled, mode=mode, max_cycles=max_cycles)
        self.save_state()

    def get_session(self, channel_id: int) -> ChannelSession:
        self.sync_from_disk_if_modified()
        preset_ws = DEFAULT_CHANNEL_WORKSPACES.get(channel_id)
        if channel_id not in self._sessions:
            default_ws = preset_ws or DEFAULT_WORKSPACE
            self._sessions[channel_id] = ChannelSession(
                channel_id=channel_id,
                workspace=default_ws,
                conversation_id=None,
                qa_conversation_id=None,
                auditor_conversation_id=None,
                mode="auto",
                auto_enabled=False,
                auto_mode="evolve",
                auto_idle_seconds=600,
                auto_max_cycles=20,
                last_url=None,
                on_change=self.save_state
            )
        else:
            # If channel has a registered preset workspace and current session differs, synchronize immediately
            sess = self._sessions[channel_id]
            if preset_ws and sess.workspace != preset_ws:
                sess.workspace = preset_ws
                sess.reset_conversation()
                self.save_state()
            else:
                if not sess.is_busy and sess.updated_at:
                    try:
                        import datetime
                        updated_dt = datetime.datetime.strptime(sess.updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                        now_dt = datetime.datetime.now(datetime.timezone.utc)
                        if (now_dt - updated_dt).total_seconds() > 7200:  # 2시간(7200초) 이상 미사용 시 자동 초기화
                            sess.reset_conversation()
                            self.save_state()
                    except Exception:
                        pass
        target_sess = self._sessions[channel_id]
        if target_sess.__class__ is not ChannelSession:
            target_sess.__class__ = ChannelSession
        return target_sess

    def get_all_sessions(self) -> Dict[int, ChannelSession]:
        self.sync_from_disk_if_modified()
        for sess in self._sessions.values():
            if sess.__class__ is not ChannelSession:
                sess.__class__ = ChannelSession
        return self._sessions

    def stop_session(self, channel_id: int) -> bool:
        session = self._sessions.get(channel_id)
        if session:
            return session.stop()
        return False

    def stop_all_sessions(self) -> int:
        """Stops all running sessions, cancels tasks, and kills child processes."""
        stopped_count = 0
        for sess in self._sessions.values():
            if sess.stop():
                stopped_count += 1
            sess.clear_queue()
        # Also kill all child processes of current process
        kill_child_processes(os.getpid())
        self.save_state()
        return stopped_count

    def reset_all_conversations(self, clear_queues: bool = True) -> int:
        """Resets conversation IDs and stops all active sessions across all channels."""
        self.stop_all_sessions()
        for sess in self._sessions.values():
            sess.reset_conversation()
            sess.status = "idle"
            sess.status_desc = "정상 대기 중 (초기화 완료)"
            sess.is_busy = False
            if clear_queues:
                sess.clear_queue()
        self.save_state()
        return len(self._sessions)

    def reset_channel_session(self, channel_id: int, clear_queue: bool = True) -> Optional[ChannelSession]:
        """Resets conversation IDs, reference images, queues, and stops active tasks for a specific channel."""
        session = self._sessions.get(channel_id)
        if session:
            session.stop()
            session.reset_conversation()
            if clear_queue:
                session.clear_queue()
            session.status = "idle"
            session.status_desc = "정상 대기 중 (초기화 완료)"
            session.is_busy = False
            self.save_state()
            return session
        return None

    def get_busy_channels(self, exclude_channel_id: Optional[int] = None) -> list[int]:
        """Returns a list of channel IDs that are currently actively working (busy)."""
        busy_list = []
        for ch_id, sess in self._sessions.items():
            if exclude_channel_id is not None and ch_id == exclude_channel_id:
                continue
            if getattr(sess, "is_busy", False) or getattr(sess, "status", "idle") == "busy":
                busy_list.append(ch_id)
        return busy_list

    def get_queue(self, channel_id: int) -> List[QueuedItem]:
        session = self._sessions.get(channel_id)
        return session.peek_queue() if session else []

    def clear_queue(self, channel_id: int) -> int:
        session = self._sessions.get(channel_id)
        return session.clear_queue() if session else 0

    def dequeue(self, channel_id: int) -> Optional[QueuedItem]:
        session = self._sessions.get(channel_id)
        return session.dequeue() if session else None

    def clear_user_reference_images(self, channel_id: int):
        """Clears user reference images and round deliverable hashes for a specific channel session."""
        session = self._sessions.get(channel_id)
        if session:
            session.clear_user_reference_images()
