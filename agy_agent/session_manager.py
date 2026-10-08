import os
import time
import json
import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any, Tuple
from config import DEFAULT_WORKSPACE, DEFAULT_CHANNEL_WORKSPACES, STATE_FILE_PATH
try:
    import mode_registry
except Exception:  # pragma: no cover - 무중단 폴백
    mode_registry = None

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


_INTERPRETER_OR_TOOL = {
    "bash", "sh", "dash", "zsh", "fish", "grep", "pgrep", "ps", "head", "tail",
    "timeout", "python", "python3", "node", "npm", "npx", "opencode", "hermes",
    "cmdc", "commandcode",
    "systemctl", "journalctl", "ssh", "vim", "nano", "code", "cat", "sed", "awk",
}


def _is_agy_language_server(pid: int) -> Tuple[bool, str]:
    """Antigravity Language Server 프로세스 여부를 실행 파일(exe)/argv0 기반으로 판별한다.

    셸/그리프/파이썬 등 도구 프로세스의 명령줄에 우연히 'language_server' 문자열이
    포함된 경우(예: pgrep 검색 명령)를 오탐으로 죽이지 않도록,
    실제 실행 바이너리 이름/경로에 언어서버 마커가 있어야만 True 를 반환한다.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return False, ""
    argv = [a for a in raw.split("\x00") if a]
    if not argv:
        return False, ""
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except Exception:
        exe = ""
    argv0 = argv[0]
    for token in (exe, argv0):
        base = os.path.basename(token or "").lower()
        if base in _INTERPRETER_OR_TOOL:
            return False, ""
    blob = f"{exe} {' '.join(argv)}".lower()
    ls_markers = ("exa.language_server", "language_server", "language-server", "jetski")
    if any(m in blob for m in ls_markers):
        return True, blob
    if "cloudcode" in blob and "server" in blob:
        return True, blob
    if "antigravity" in blob and ("server" in blob or "lsp" in blob):
        return True, blob
    return False, ""


def kill_agy_language_servers():
    """Finds and kills orphaned AGY Language Server processes that linger after
    CLI sessions end, continuously polling Google Cloud APIs (loadCodeAssist,
    fetchAvailableModels) every 6 minutes and silently consuming quota.

    Active CLI sessions (--print / --dangerously-skip-permissions) are preserved.
    """
    import signal
    killed = []
    try:
        for pid_entry in os.listdir("/proc"):
            if not pid_entry.isdigit():
                continue
            pid = int(pid_entry)
            if pid == os.getpid():
                continue
            try:
                is_ls, cmdline = _is_agy_language_server(pid)
                if not is_ls:
                    continue
                if "--print" in cmdline or "--dangerously-skip-permissions" in cmdline:
                    continue  # active CLI session
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
                logging.getLogger(__name__).info(
                    f"[QuotaGuard] Terminated orphaned AGY Language Server pid={pid} cmd={cmdline[:120]}"
                )
            except (ProcessLookupError, PermissionError, OSError, ValueError):
                continue
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
    # [🖼️ 첨부 이미지 비전 분석] on_message 인라인에서 돌던 취소 불가능한 분석을
    # !stop/!reset 이 즉시 취소할 수 있도록 추적하는 태스크.
    vision_task: Any = None
    # [🛡️ StallGuard 하트비트] 실제 진행 시각과 busy 시작 시각 (정지 감시 기준)
    busy_since: float = 0.0
    last_progress_at: float = 0.0
    _stall_warned: bool = False
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
        engine_conversations: Optional[Dict[str, str]] = None,
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
        # [🔌 엔진별 대화 저장소] 엔진(agy/opencode/hermes...)마다 독립적인 대화/세션 ID를
        # 분리 보관하여, 라우터(엔진) 전환 시 서로의 conversation_id를 오염시키지 않습니다.
        # key 형식: "<engine>:<conversation_id_attr>" (예: "opencode:conversation_id")
        self.engine_conversations: Dict[str, str] = dict(engine_conversations) if isinstance(engine_conversations, dict) else {}
        init_mode = (mode or "auto").lower().strip()
        # 레거시 컨설턴트 모드(single_plus)는 제거되어 단일 에이전트 모드로 통합되었습니다.
        if init_mode == "single_plus":
            init_mode = "single"
        if init_mode not in ("auto", "read", "pair_quad", "single_qa2", "pair", "single", "mode3", "mode3_plus"):
            init_mode = "auto"
        self.mode: str = init_mode  # "auto", "pair", "pair_quad", "single", "single_qa2", "read", "mode3", or "mode3_plus"
        self.auto_enabled: bool = auto_enabled
        self.auto_mode: str = auto_mode
        self.auto_idle_seconds: int = auto_idle_seconds
        self.auto_max_cycles: int = auto_max_cycles
        self.last_url: Optional[str] = last_url
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.current_task: Optional[asyncio.Task] = None
        self.vision_task: Optional[asyncio.Task] = None
        self.is_busy: bool = is_busy
        # [🛡️ StallGuard] busy 시작 시각 / 실제 진행(하트비트) 시각 / 경보 발송 여부
        self.busy_since: float = time.time() if is_busy else 0.0
        self.last_progress_at: float = time.time()
        self._stall_warned: bool = False
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
        # [🚫 이미지 재활용 원천 차단] 이미 디스코드로 전송(전달)한 이미지의 지문(sha256/dHash).
        # 한 번 전달된 이미지는 동일 채널 세션에서 다시 첨부·전송될 수 없다(재사용 불가).
        self.delivered_image_hashes: List[str] = []
        self.delivered_image_dhashes: List[str] = []
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

    # [🚫 이미지 재활용 원천 차단] 디스코드로 실제 전송된 이미지의 sha256/dHash 를 누적 등록한다.
    # 이후 동일 이미지(바이트 동일 또는 지각적 유사 dHash<=3)는 전달 후보에서 제외된다.
    def register_delivered_images(self, paths: Optional[List[str]]) -> None:
        """전송한 이미지 파일 경로들의 지문을 누적 등록한다(비이미지는 무시)."""
        if not paths:
            return
        if not hasattr(self, "delivered_image_hashes"):
            self.delivered_image_hashes = []
            self.delivered_image_dhashes = []
        try:
            from evidence_validator import EvidenceValidator
        except Exception:
            EvidenceValidator = None  # type: ignore
        image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
        for p in paths:
            try:
                sp = str(p)
                if not sp.lower().endswith(image_exts):
                    continue
                if not os.path.isfile(sp):
                    continue
                h = hashlib.sha256()
                with open(sp, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                sha = h.hexdigest()
                if sha and sha not in self.delivered_image_hashes:
                    self.delivered_image_hashes.append(sha)
                if EvidenceValidator is not None:
                    dh = EvidenceValidator.compute_image_dhash(sp)
                    if dh and dh not in self.delivered_image_dhashes:
                        self.delivered_image_dhashes.append(dh)
            except Exception:
                continue

    @property
    def user_reference_hashes_set(self) -> set:
        """전달 제외 대상 SHA-256 집합 = 사용자 레퍼런스 이미지 ∪ 이미 전달한 이미지(재활용 차단)."""
        hashes = set(getattr(self, "delivered_image_hashes", []) or [])
        for item in getattr(self, "user_reference_images", []) or []:
            if item.get("sha256"):
                hashes.add(item["sha256"])
        return hashes

    @property
    def user_reference_dhashes_set(self) -> set:
        """전달 제외 대상 dHash 집합 = 사용자 레퍼런스 ∪ 이미 전달한 이미지(지각적 재활용 차단)."""
        dhashes = set(getattr(self, "delivered_image_dhashes", []) or [])
        for item in getattr(self, "user_reference_images", []) or []:
            if item.get("dhash"):
                dhashes.add(item["dhash"])
        return dhashes


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

        if self.vision_task and not self.vision_task.done():
            self.vision_task.cancel()
            stopped = True
            self.vision_task = None

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
        # [🔌 엔진별 대화 저장소] 엔진 전환/리셋 시 모든 엔진의 세션 ID도 함께 초기화합니다.
        self.engine_conversations = {}
        self.clear_user_reference_images()
        if self._on_change:
            self._on_change()

    def reset_for_hard_reset(self):
        """[!reset 전용 하드 리셋] 대화 ID뿐 아니라 목표/보류/대기 컨텍스트까지 완전히 비운다.

        `reset_conversation()` 은 마일스톤 사이클 전환(`reset_cycle_workers`)에서도 호출되므로
        root_user_goal / commander_macro_history 같은 지속 목표는 의도적으로 보존한다.
        반면 사용자의 `!reset` / `!reset all` 은 "깨끗한 새 세션"을 약속하므로, 잔존한
        목표·보류 프롬프트·그린 대기가 다음 실행에 되살아나는 것을 막기 위해 이 메서드를 쓴다.
        """
        self.reset_conversation()
        self.root_user_goal = None
        self.user_directive = None
        self.mode3_pending_prompt = None
        self.wait_for_green = False
        self.stop_after_current_cycle = False
        self.commander_macro_history = []

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
        """
        동작 모드를 설정한다.

        모든 별칭 해석은 `mode_registry.normalize_mode()` 단일 진실 공급원에
        위임한다. (`!mode 2` vs `!mode 2+`, `!mode 3` vs `!mode 3+` 의 구분이
        어떤 입력 경로로 들어와도 동일하게 해석되도록 보장)
        """
        try:
            if mode_registry is None:
                raise RuntimeError("mode_registry unavailable")
            resolved = mode_registry.normalize_mode(mode)
            if resolved not in mode_registry.ALL_CANONICAL_MODES:
                resolved = "auto"
        except Exception:
            # 레지스트리 로드 실패 시에도 안전하게 auto 로 수렴 (무중단)
            resolved = "auto"
        self.mode = resolved
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

    def touch(self, reason: str = "") -> None:
        """[🛡️ StallGuard] 실제 진행 발생 시 호출되는 하트비트.

        CLI stdout 출력, 도구 완료, 상태 전이 등 **진짜 일한 순간**에만 호출해야 한다.
        디스코드 8초 주기 '진행 중...' 임베드 같은 가짜 하트비트를 여기에 연결하면
        정지 감시가 무력화되므로 절대 연결하지 않는다.
        """
        self.last_progress_at = time.time()
        self._stall_warned = False

    def set_busy(self, is_busy: bool, desc: str = "작업 중"):
        self.is_busy = is_busy
        if is_busy:
            self.is_stopped = False
            self.busy_since = time.time()
        else:
            self.busy_since = 0.0
        self.last_progress_at = time.time()
        self._stall_warned = False
        self.status = "busy" if is_busy else "idle"
        self.status_desc = desc
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self._on_change:
            self._on_change()

    def set_status(self, status: str, desc: str = ""):
        self.status = status
        self.is_busy = (status == "busy")
        if self.is_busy and self.busy_since <= 0.0:
            self.busy_since = time.time()
        elif not self.is_busy:
            self.busy_since = 0.0
        self.last_progress_at = time.time()
        self._stall_warned = False
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
            "engine_conversations": getattr(self, "engine_conversations", {}),
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

def get_existing_session(channel_id: int) -> Optional["ChannelSession"]:
    """[🚫 이미지 재활용 차단] 이미 생성된 세션만 반환한다(없으면 None). 새 세션을 만들지 않는다."""
    try:
        mgr = getattr(SessionManager, "_primary", None)
        if mgr is None:
            return None
        return getattr(mgr, "_sessions", {}).get(channel_id)
    except Exception:
        return None


class SessionManager:
    _primary: Optional["SessionManager"] = None

    def __init__(self, state_file: str = STATE_FILE_PATH, clear_on_startup: bool = True):
        self._state_file = state_file
        self._sessions: Dict[int, ChannelSession] = {}
        self._last_mtime: float = 0.0
        self._load_state(clear_on_startup)
        SessionManager._primary = self

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
                engine_conv_ids = data.get("engine_conversations") or {}
                auditor_conv_id = data.get("auditor_conversation_id")
                mode = data.get("mode", "auto")
                if mode == "single_plus":
                    mode = "single"  # 레거시 컨설턴트 모드 자동 마이그레이션
                if mode not in ("auto", "read", "pair_quad", "single_qa2", "pair", "single", "mode3", "mode3_plus"):
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
                    engine_conversations=engine_conv_ids,
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
                        if "engine_conversations" in data:
                            sess.engine_conversations = data.get("engine_conversations") or {}
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
                            engine_conversations=data.get("engine_conversations") or {},
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
            sess.reset_for_hard_reset()
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
            session.reset_for_hard_reset()
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
