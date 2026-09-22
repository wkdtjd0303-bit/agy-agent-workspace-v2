import asyncio
import os
import re
import glob
import time
import shutil
import logging
import hashlib
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any, Callable
import discord

def clean_discord_links(text: str) -> str:
    """
    Cleans markdown links where the label is a raw URL, which causes Discord's
    auto-linkifier to corrupt the URL (e.g. [https://foo.com](https://foo.com) -> https://foo.com](https://foo.com)).
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ""

    def _replace_url_label(match):
        label = match.group(1).strip()
        url = match.group(2).strip()
        # If label is itself a URL, replace with pure URL
        if label.startswith("http://") or label.startswith("https://"):
            return url
        return match.group(0)

    # Match markdown link syntax: [label](url)
    return re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', _replace_url_label, text)

def cleanse_agent_output(text: str) -> str:
    """
    Cleanses intermediate agent execution monologues, background task notifications,
    and tool status lines in English and Korean
    (e.g. 'An asynchronous background task is executing...', 'Wait for task-60...',
    'The background test execution is running...', 'Logs available at:...')
    and extracts only the clean final deliverable report (simulating /copy behavior).
    """
    if not text or not isinstance(text, str):
        return ""

    # 1. Final Deliverable Slicing (/copy behavior):
    # If official report markers exist in the response, discard all preceding monologues/status noise!
    report_markers = [
        "[STATUS: APPROVED",
        "[STATUS: AUDIT_PASSED",
        "[STATUS: REJECTED",
        "[STATUS: AUDIT_FAILED",
        "[STATUS: AUDIT_STOP",
        "### 💬 사용자 질문 및 요청에 대한 직접 답변",
        "### 📁 생성/수정된 핵심 산출물 및 파일",
        "💬 🎯 [Commander 사령관 5W1H 개발 지침",
        "📦 Android APK 빌드 및 Google Drive 업로드 완료",
        "📦 **Android APK 빌드 및 Google Drive 업로드 완료**"
    ]
    earliest_idx = -1
    for marker in report_markers:
        idx = text.find(marker)
        if idx != -1:
            if earliest_idx == -1 or idx < earliest_idx:
                earliest_idx = idx

    cleaned = text[earliest_idx:] if earliest_idx != -1 else text

    # 2. Comprehensive pattern-based cleansing for any residual intermediate noise
    intermediate_patterns = [
        r"(?:^|\n)\s*(?:An\s+)?asynchronous background task is executing[^\n]*",
        r"(?:^|\n)\s*Wait for task[^\n]*",
        r"(?:^|\n)\s*Waiting for (?:task|\./|completion|background)[^\n]*",
        r"(?:^|\n)\s*The background (?:test|build|task|execution)[^\n]*",
        r"(?:^|\n)\s*Once the result is received[^\n]*",
        r"(?:^|\n)\s*I have (?:initiated|launched|triggered|started|resumed)[^\n]*",
        r"(?:^|\n)\s*I (?:am waiting|will wait|will now wait|am awaiting|will proceed|will now inspect|am verifying)[^\n]*",
        r"(?:^|\n)\s*I am still waiting for[^\n]*",
        r"(?:^|\n)\s*The (?:release build|tests|test execution|build)[^\n]*",
        r"(?:^|\n)\s*Test execution is currently running[^\n]*",
        r"(?:^|\n)\s*Awaiting (?:notification|completion)[^\n]*",
        r"(?:^|\n)\s*I will (?:process|review|inspect|monitor|analyze) the (?:output|results|test results)[^\n]*",
        r"(?:^|\n)\s*잠시 후 테스트 실행 결과가 반환되면[^\n]*",
        r"(?:^|\n)\s*테스트 실행 중입니다[^\n]*",
        r"(?:^|\n)\s*\[task-\d+\][^\n]*",
        r"(?:^|\n)\s*Task (?:id\s+)?[\w\-/]+ finished with result:[^\n]*",
        r"(?:^|\n)\s*The command exited with code \d+\.?",
        r"(?:^|\n)\s*Task completed with exit code:[^\n]*",
        r"(?:^|\n)\s*Task [\w\-/]+ completed with exit code \d+\.?[^\n]*",
        r"(?:^|\n)\s*Logs available at:[^\n]*",
        r"(?:^|\n)\s*Process returned exit code \d+\.?",
        r"(?:^|\n)\s*Starting (?:background task|process):[^\n]*",
        r"(?:^|\n)\s*Target working directory is ready:[^\n]*",
        r"(?:^|\n)\s*Wait for task-\d+ to (?:finish|complete)\.?",
        r"(?:^|\n)\s*Wait for test runner\.?",
    ]

    for pat in intermediate_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

def sanitize_attachment_filename(filename: Optional[str], max_bytes: int = 150) -> str:
    """
    Sanitizes attachment filename to prevent 'File name too long' (Errno 36 / NAME_MAX overflow),
    path traversal attacks, and filesystem incompatibilities while strictly preserving the file extension.
    Ensures UTF-8 byte length does not exceed max_bytes (default 150, safely below Linux's 255 byte limit).
    """
    if not filename or not isinstance(filename, str):
        fallback_hash = hashlib.sha256(str(time.time()).encode("utf-8")).hexdigest()[:8]
        return f"att_{int(time.time())}_{fallback_hash}.bin"

    # 1. Strip directory traversal components
    clean_name = os.path.basename(filename.replace("\\", "/")).strip()
    if not clean_name:
        clean_name = f"att_{int(time.time())}.bin"

    # 2. Sanitize prohibited/dangerous control characters (keep alnum, dots, underscores, dashes, spaces, Unicode)
    clean_name = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', '_', clean_name).strip()
    clean_name = re.sub(r'^\.+', '', clean_name).strip()
    if not clean_name:
        clean_name = f"att_{int(time.time())}.bin"

    # 3. Split stem and ext
    stem, ext = os.path.splitext(clean_name)
    # Ensure extension is reasonably sized (at most 32 bytes)
    ext_bytes = ext.encode("utf-8", errors="ignore")
    if len(ext_bytes) > 32:
        ext = ext_bytes[:32].decode("utf-8", errors="ignore")
        stem = clean_name

    # 4. Check total byte length
    encoded_total = (stem + ext).encode("utf-8", errors="ignore")
    if len(encoded_total) <= max_bytes:
        return stem + ext

    # 5. Deterministic truncation: compute short SHA-256 hash from original filename to ensure uniqueness
    file_hash = hashlib.sha256(filename.encode("utf-8", errors="ignore")).hexdigest()[:8]
    hash_suffix = f"_{file_hash}"
    allowed_stem_bytes = max_bytes - len(ext.encode("utf-8", errors="ignore")) - len(hash_suffix.encode("utf-8", errors="ignore"))
    if allowed_stem_bytes < 1:
        return f"att_{file_hash}{ext}"

    # Safely truncate stem on UTF-8 byte boundary
    stem_bytes = stem.encode("utf-8", errors="ignore")
    truncated_stem_bytes = stem_bytes[:allowed_stem_bytes]
    truncated_stem = truncated_stem_bytes.decode("utf-8", errors="ignore").rstrip(". _-")
    if not truncated_stem:
        truncated_stem = "att"

    return f"{truncated_stem}{hash_suffix}{ext}"

def shorten_path(path: str, workspace: Optional[str] = None) -> str:
    """
    Shortens overly long absolute filesystem paths for human readability and token economy.
    Converts absolute paths into concise relative paths (e.g. './src/main.py' or './reports/...').
    """
    if not path or not isinstance(path, str):
        return path or ""
    path = path.strip()
    if not path:
        return ""

    # 1. If explicit workspace is provided, compute relative path
    if workspace and os.path.isabs(workspace):
        norm_ws = os.path.normpath(workspace)
        norm_p = os.path.normpath(path)
        if norm_p.startswith(norm_ws):
            rel = os.path.relpath(norm_p, norm_ws)
            return "./" if rel == "." else (f"./{rel}" if not rel.startswith(".") else rel)

    # 2. General workspace detection (autocoding project pattern)
    m = re.match(r'^(?:/home/[^/\s`\'"<>]+/Desktop/autocoding/[^/\s`\'"<>]+)/(.*)$', path)
    if m:
        sub = m.group(1)
        return f"./{sub}" if sub else "./"

    # 3. Deep Linux home paths: collapse leading 4+ segments
    if path.startswith("/home/"):
        parts = path.split("/")
        if len(parts) > 4:
            return "./" + "/".join(parts[4:])

    return path


def shorten_paths_for_readability(text: str, workspace: Optional[str] = None) -> str:
    """
    Replaces verbose absolute filesystem paths in text with compact relative paths (./...)
    to prevent context bloat and improve reading clarity for users.
    """
    if not text or not isinstance(text, str):
        return text or ""

    # Workspace-specific absolute prefix replacement
    if workspace:
        clean_ws = workspace.rstrip("/")
        text = text.replace(clean_ws + "/", "./")
        text = text.replace(clean_ws, "./")

    # Autocoding projects absolute path pattern replacement
    text = re.sub(r'/home/[^/\s`\'"<>]+/Desktop/autocoding/[^/\s`\'"<>]+/', './', text)
    # Generic deep home directory paths (/home/.../Desktop/...)
    text = re.sub(r'/home/[^/\s`\'"<>]+/Desktop/[^/\s`\'"<>]+/', './', text)
    return text


def optimize_readability(text: str, workspace: Optional[str] = None) -> str:
    """
    Improves readability of Discord messages:
    - Replaces long absolute paths with compact relative paths (./...).
    - Normalizes excessive consecutive blank lines (3+ newlines -> 2 newlines).
    - Normalizes overly long delimiter lines (e.g. '════════════════════' -> '────────────────────────').
    - Solves the '너무 문자가 길어서 가독성도 떨어짐' problem by cleaning up cluttered typography.
    """
    if not text or not isinstance(text, str):
        return text or ""
    cleaned = shorten_paths_for_readability(text, workspace=workspace)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r'([═─\-_=~*]){30,}', r'\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1\1', cleaned)
    return cleaned


def split_message(text: str, limit: int = 1900, workspace: Optional[str] = None) -> List[str]:
    """
    Split a long message into chunks <= limit, safely handling code fence blocks (```),
    normalizing Discord links, and shortening long absolute paths for readability.
    """
    if text is None:
        return []
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return []
    
    try:
        limit = max(100, int(limit))
    except (TypeError, ValueError):
        limit = 1900
    text = clean_discord_links(text)
    text = optimize_readability(text, workspace=workspace)

    if len(text) <= limit:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_length = 0
    in_code_block = False
    code_block_lang = ""

    for line in lines:
        code_fence_match = re.match(r"^```(\w*)", line)
        
        # If line itself is massive, break it down
        line_parts = [line[i:i + (limit - 50)] for i in range(0, len(line), limit - 50)] or [""]

        for part in line_parts:
            # Closing fence overhead is 4 chars ("\n```")
            closing_fence_overhead = 4 if in_code_block else 0
            
            if current_length + len(part) + 1 + closing_fence_overhead > limit and current_chunk:
                if in_code_block:
                    current_chunk.append("```")
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0
                if in_code_block:
                    reopen_header = f"```{code_block_lang}"
                    current_chunk.append(reopen_header)
                    current_length = len(reopen_header)

            current_chunk.append(part)
            current_length += len(part) + 1

        if code_fence_match:
            if not in_code_block:
                in_code_block = True
                code_block_lang = code_fence_match.group(1)
            else:
                in_code_block = False
                code_block_lang = ""

    if current_chunk:
        if in_code_block and not current_chunk[-1].endswith("```"):
            current_chunk.append("```")
        chunks.append("\n".join(current_chunk))

    return chunks

def format_tool_status(tool_name: str, tool_info: dict, workspace: Optional[str] = None) -> str:
    """
    Returns a human-readable, concise summary of a tool call with shortened paths
    to improve user readability and prevent token/screen clutter.
    """
    params = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}
    
    if tool_name == "write_to_file":
        target = shorten_path(params.get("TargetFile", ""), workspace)
        return f"📝 **파일 생성/덮어쓰기**: `{target}`"
    elif tool_name in ("replace_file_content", "multi_replace_file_content", "edit_file"):
        target = shorten_path(params.get("TargetFile", ""), workspace)
        return f"✏️ **파일 수정**: `{target}`"
    elif tool_name == "run_command":
        cmd = shorten_paths_for_readability(params.get("CommandLine", ""), workspace)
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"💻 **명령어 실행**: `{cmd}`"
    elif tool_name == "view_file":
        target = shorten_path(params.get("AbsolutePath", ""), workspace)
        return f"🔍 **파일 조회**: `{target}`"
    elif tool_name == "list_dir":
        target = shorten_path(params.get("DirectoryPath", ""), workspace)
        return f"📁 **디렉토리 확인**: `{target}`"
    elif tool_name == "grep_search":
        query = params.get("Query", "")
        return f"🔎 **코드 검색**: `{query}`"
    elif tool_name == "search_web":
        query = params.get("query", "")
        return f"🌐 **웹 검색**: `{query}`"
    elif tool_name == "invoke_subagent":
        return "🤖 **서브에이전트 호출**"
    elif tool_name == "generate_image":
        return "🎨 **이미지 생성**"
    else:
        return f"⚙️ **도구 실행**: `{tool_name}`"

logger = logging.getLogger(__name__)


class ThrottledStatusUpdater:
    """
    Discord Status Message Rate-Limit Defense & Throttler.

    Solves Discord API 429 Too Many Requests when Coder/Agents execute rapid tool calls:
    1. Enforces a strict minimum interval (default: 2.0s) between message.edit() calls.
    2. Deduplicates identical status content (no-op if text has not changed).
    3. Trailing-edge async debouncing: rapidly arriving updates are coalesced and the latest
       state is flushed automatically once the cooldown expires.
    4. Automatically intercepts HTTP 429 RateLimit responses and applies dynamic backoff.
    5. Clean lifecycle management: cancel pending tasks on delete() or stop().
    """
    def __init__(self, message: discord.Message, min_interval: float = 2.0):
        self.message = message
        self.min_interval = max(0.1, float(min_interval))
        self._last_edit_time = 0.0
        self._last_content = getattr(message, "content", "") or ""
        self._pending_content: Optional[str] = None
        self._pending_embed: Optional[discord.Embed] = None
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._is_closed = False
        self._backoff_until = 0.0

    async def update(self, content: str = "", embed: Optional[discord.Embed] = None, prefix: str = ""):
        """Queues a status text/embed update with rate-limit throttling and deduplication."""
        if self._is_closed:
            return

        full_text = f"{prefix}\n{content}".strip() if prefix else content.strip()
        # Deduplication: if content and embed are identical, skip completely
        if embed is None and full_text == self._last_content:
            return

        async with self._lock:
            self._pending_content = full_text
            self._pending_embed = embed
            now = time.time()
            time_since_last = now - self._last_edit_time
            is_backed_off = now < self._backoff_until

            # Can edit immediately if cooldown passed, not in 429 backoff, and no flush scheduled
            if time_since_last >= self.min_interval and not is_backed_off and (self._flush_task is None or self._flush_task.done()):
                await self._do_edit_now()
            else:
                # Schedule trailing edge flush if not already active
                if self._flush_task is None or self._flush_task.done():
                    delay = max(0.1, self.min_interval - time_since_last)
                    if is_backed_off:
                        delay = max(delay, self._backoff_until - now)
                    self._flush_task = asyncio.create_task(self._delayed_flush(delay))

    async def _do_edit_now(self):
        """Internal helper to execute message.edit. Must be called while holding self._lock."""
        if self._is_closed:
            return
        if self._pending_content is None and self._pending_embed is None:
            return

        target_content = self._pending_content if self._pending_content is not None else self._last_content
        target_embed = self._pending_embed

        # If unchanged, discard pending
        if target_embed is None and target_content == self._last_content:
            self._pending_content = None
            return

        try:
            edit_kwargs: Dict[str, Any] = {}
            if target_content is not None:
                edit_kwargs["content"] = target_content
            if target_embed is not None:
                edit_kwargs["embed"] = target_embed

            await self.message.edit(**edit_kwargs)
            self._last_content = target_content
            self._last_edit_time = time.time()
            self._pending_content = None
            self._pending_embed = None
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, "retry_after", 3.0) or 3.0
                logger.warning(
                    f"⚠️ [ThrottledStatusUpdater] Discord 429 Rate Limit encountered. "
                    f"Backing off for {retry_after:.1f}s."
                )
                self._backoff_until = time.time() + retry_after + 0.5
                if self._flush_task is None or self._flush_task.done():
                    self._flush_task = asyncio.create_task(self._delayed_flush(retry_after + 0.5))
            else:
                logger.debug(f"[ThrottledStatusUpdater] Edit failed: {e}")
        except Exception as e:
            logger.debug(f"[ThrottledStatusUpdater] Unexpected edit error: {e}")

    async def _delayed_flush(self, delay: float):
        """Waits for cooldown to elapse, then applies the latest accumulated state."""
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                await self._do_edit_now()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"[ThrottledStatusUpdater] Delayed flush error: {e}")

    async def flush(self):
        """Forces any pending update to be sent, observing minimal cooldown."""
        if self._is_closed:
            return
        if self._flush_task and not self._flush_task.done():
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        async with self._lock:
            if self._pending_content is not None or self._pending_embed is not None:
                now = time.time()
                wait_time = max(0.0, (self._last_edit_time + self.min_interval) - now)
                if now < self._backoff_until:
                    wait_time = max(wait_time, self._backoff_until - now)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                await self._do_edit_now()

    async def delete(self):
        """Cancels any pending delayed update and deletes the status message cleanly."""
        self._is_closed = True
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.message.delete()
        except Exception:
            pass

    async def stop(self):
        """Stops the updater and cancels pending delayed updates without deleting the message."""
        self._is_closed = True
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass

    def get_callback(self, prefix: str = "") -> Callable[[str], Any]:
        """Returns an async callback function suitable for on_status_update / on_tool_update."""
        async def _callback(status_text: str):
            await self.update(content=status_text, prefix=prefix)
        return _callback

    def get_dynamic_callback(self, prefix_fn: Callable[[], str]) -> Callable[[str], Any]:
        """Returns an async callback function that dynamically resolves prefix at call time."""
        async def _callback(status_text: str):
            pref = prefix_fn() if callable(prefix_fn) else ""
            await self.update(content=status_text, prefix=pref)
        return _callback

DISCORD_MAX_FILE_BYTES = int(8.0 * 1024 * 1024)  # 8.0MB safe limit for Discord standard file uploads

ALLOWED_ATTACHMENT_EXTENSIONS = (
    # Archives & Compressed files
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    # Data & Spreadsheets (generated deliverables)
    ".xlsx", ".xls", ".csv", ".tsv",
    # Documents (generated reports/deliverables)
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    # Images & Graphics
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico",
    # Design & Media
    ".psd", ".ai", ".eps", ".mp3", ".mp4", ".wav",
    # Android & Application Packages
    ".apk", ".aab",
)

EXCLUDED_DIR_NAMES = {
    ".git", ".venv", "node_modules", "__pycache__",
    ".cache", ".local", ".npm", ".config", ".references",
    "tests", "test"
}


DELIVERABLE_PATTERNS = [
    "output/**/*.*", "build/**/*.*", "dist/**/*.*", "*.apk", "*.aab", "*.zip", "*.xlsx", "*.pdf",
    "reports/gate5/screenshots/*.png", "assets/**/*.*", "res/drawable*/**/*.*", "images/**/*.*"
]

_GDRIVE_UPLOAD_CACHE: Dict[str, Tuple[float, float, Dict[str, Any]]] = {}

def extract_candidates(
    text: str,
    workspace_root: Optional[str] = None,
    auto_scan_deliverables: bool = False,
    session_start_time: Optional[float] = None
) -> List[str]:
    """
    Extracts all local file candidate paths mentioned in text or within workspace_root.
    Includes newly generated image assets, artifact media, and deliverables.
    Enforces strict temporal session freshness when session_start_time is supplied.
    """
    if not text:
        return []

    candidates = set()

    # Artifact media path extractor (e.g. [ARTIFACT: ...] Path: file:///home/...)
    artifact_paths = re.findall(r'(?:Path:\s*|file://)(/[^\s`"\'\(\)\[\]<>]+\.(?:png|jpg|jpeg|webp|gif|bmp))', text, re.IGNORECASE)
    for ap in artifact_paths:
        if os.path.isfile(ap):
            candidates.add(ap)

    markdown_links = re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text)
    for target in markdown_links:
        cleaned = target.strip().strip("'\"`")
        if cleaned.startswith("file://"):
            cleaned = cleaned[7:]
        if "#" in cleaned:
            cleaned = cleaned.split("#")[0]
        if any(cleaned.lower().endswith(ext) for ext in ALLOWED_ATTACHMENT_EXTENSIONS):
            candidates.add(cleaned)

    raw_paths = re.findall(
        r'[`"\'\(]?((?:/(?:home|tmp|var)/|(?:output|data|scripts|templates|build|dist|scratch|orders|assets|images|reports|res)/)[^\s`"\'\(\)\[\]<>]+\.(?:[a-zA-Z0-9_]+))[`"\'\)]?',
        text,
        re.IGNORECASE
    )
    for p in raw_paths:
        clean_p = p.strip()
        if any(clean_p.lower().endswith(ext) for ext in ALLOWED_ATTACHMENT_EXTENSIONS):
            if clean_p.startswith("/"):
                candidates.add(clean_p)
            elif workspace_root:
                candidates.add(os.path.join(workspace_root, clean_p))
            else:
                candidates.add(os.path.abspath(clean_p))

    # Regex patterns for markdown links, codeblocks, or explicit paths
    path_patterns = [
        r'\[.*?\]\((file://)?([^\s\)]+)\)',
        r'`([a-zA-Z0-9_\-\.\/~\\]+)`',
        r'(?:^|\s)((?:/|[a-zA-Z]:\\)[^\s\*\?\"\'<>|:]+)',
        r'(?:^|\s)(\./[^\s\*\?\"\'<>|:]+)',
    ]

    for pat in path_patterns:
        for match in re.finditer(pat, text):
            groups = match.groups()
            path_str = groups[-1] if groups else match.group(0)
            if path_str and not path_str.startswith(("http://", "https://", "ws://", "wss://")):
                clean_path = path_str.strip().strip("'\"`")
                if any(clean_path.lower().endswith(ext) for ext in ALLOWED_ATTACHMENT_EXTENSIONS):
                    if clean_path.startswith("/"):
                        candidates.add(clean_path)
                    elif workspace_root:
                        candidates.add(os.path.join(workspace_root, clean_path))
                    else:
                        candidates.add(os.path.abspath(clean_path))

    # Check workspace root for latest deliverables ONLY if freshly generated (<60s) or explicitly in text
    if auto_scan_deliverables and workspace_root and os.path.exists(workspace_root):
        now = time.time()
        for pattern in DELIVERABLE_PATTERNS:
            try:
                for match_path in glob.glob(os.path.join(workspace_root, pattern), recursive=True):
                    try:
                        mtime = os.path.getmtime(match_path)
                        fn = os.path.basename(match_path).lower()
                        # Strict Session Boundary: enforce temporal freshness
                        if session_start_time is not None and mtime < (session_start_time - 1.0):
                            continue
                        # Auto-include if freshly generated in this turn (< 60s) OR explicitly in text
                        if (now - mtime <= 60.0) or (fn in text.lower()):
                            candidates.add(match_path)
                    except Exception:
                        pass
            except Exception:
                pass

    return list(candidates)

def extract_local_attachments(
    text: str,
    workspace_root: Optional[str] = None,
    max_bytes: int = DISCORD_MAX_FILE_BYTES,
    auto_scan_deliverables: bool = False,
    excluded_hashes: Optional[Any] = None,
    excluded_dhashes: Optional[Any] = None,
    session_start_time: Optional[float] = None
) -> List[str]:
    """
    Extracts valid local deliverable files from text, ensuring they exist,
    are within max_bytes, and not in excluded directories or matching user reference hashes
    (both binary SHA-256 and perceptual dHash <= 3).
    Enforces strict temporal freshness when session_start_time is provided.
    """
    candidates = extract_candidates(text, workspace_root, auto_scan_deliverables, session_start_time=session_start_time)
    valid_files = []
    excluded_hash_set = set(excluded_hashes) if excluded_hashes else set()
    excluded_dhash_set = {dh for dh in excluded_dhashes if dh} if excluded_dhashes else set()
    for path_str in candidates:
        abs_p = os.path.abspath(os.path.expanduser(path_str))
        path_segments = set(os.path.normpath(abs_p).split(os.sep))
        if path_segments.intersection(EXCLUDED_DIR_NAMES) or path_segments.intersection({".references", "agy_user_references"}) or any(seg.startswith("agy_discord_att_") for seg in path_segments):
            continue
        if os.path.isfile(abs_p):
            if session_start_time is not None:
                try:
                    if os.path.getmtime(abs_p) < (session_start_time - 1.0):
                        logger.warning(f"discord_utils: Filtered stale deliverable '{abs_p}' (mtime < session_start_time)")
                        continue
                except OSError:
                    pass
            try:
                # Performance optimization: only compute hash for images against reference hashes
                if abs_p.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")):
                    if excluded_hash_set:
                        h = hashlib.sha256()
                        with open(abs_p, "rb") as f:
                            for chunk in iter(lambda: f.read(65536), b""):
                                h.update(chunk)
                        if h.hexdigest() in excluded_hash_set:
                            logger.warning(f"discord_utils: Filtered deliverable candidate '{abs_p}' (matches user reference SHA-256)")
                            continue

                    if excluded_dhash_set:
                        try:
                            from evidence_validator import EvidenceValidator
                            dh = EvidenceValidator.compute_image_dhash(abs_p)
                            if dh:
                                is_dhash_match = any(
                                    ref_dh and EvidenceValidator.hamming_distance(dh, ref_dh) <= 3
                                    for ref_dh in excluded_dhash_set
                                )
                                if is_dhash_match:
                                    logger.warning(f"discord_utils: Filtered deliverable candidate '{abs_p}' (matches user reference perceptual dHash <= 3)")
                                    continue
                        except Exception as d_err:
                            logger.debug(f"discord_utils: dHash check error on {abs_p}: {d_err}")

                sz = os.path.getsize(abs_p)
                if 0 < sz <= max_bytes:
                    valid_files.append(abs_p)
            except Exception:
                pass

    def priority_sort_key(fpath: str) -> Tuple[int, int, int]:
        fn_lower = os.path.basename(fpath).lower()
        is_apk = 0 if fn_lower.endswith((".apk", ".aab")) else 1
        is_image = 0 if fn_lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) else 1
        is_release = 0 if ("release" in fn_lower or "debug" not in fn_lower) else 1
        try:
            sz = os.path.getsize(fpath)
        except Exception:
            sz = 0
        return (is_apk, is_image, is_release, sz)

    return sorted(list(dict.fromkeys(valid_files)), key=priority_sort_key)

def extract_oversized_attachments(
    text: str,
    workspace_root: Optional[str] = None,
    threshold_bytes: int = DISCORD_MAX_FILE_BYTES,
    auto_scan_deliverables: bool = False,
    excluded_hashes: Optional[Any] = None,
    excluded_dhashes: Optional[Any] = None
) -> List[Tuple[str, float]]:
    """
    Finds local deliverable files referenced in text whose size exceeds threshold_bytes (> 8.0MB).
    Only includes files explicitly mentioned in text or freshly created in the last 60s.
    Returns list of (file_path, size_in_mb).
    """
    candidates = extract_candidates(text, workspace_root, auto_scan_deliverables)
    oversized = []
    now = time.time()
    excluded_hash_set = set(excluded_hashes) if excluded_hashes else set()
    excluded_dhash_set = {dh for dh in excluded_dhashes if dh} if excluded_dhashes else set()
    for path_str in candidates:
        abs_p = os.path.abspath(os.path.expanduser(path_str))
        path_segments = set(os.path.normpath(abs_p).split(os.sep))
        if path_segments.intersection(EXCLUDED_DIR_NAMES) or path_segments.intersection({".references", "agy_user_references"}) or any(seg.startswith("agy_discord_att_") for seg in path_segments):
            continue
        if os.path.isfile(abs_p):
            try:
                # Performance optimization: only compute hash for images against reference hashes
                if abs_p.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")):
                    if excluded_hash_set:
                        h = hashlib.sha256()
                        with open(abs_p, "rb") as f:
                            for chunk in iter(lambda: f.read(65536), b""):
                                h.update(chunk)
                        if h.hexdigest() in excluded_hash_set:
                            continue
                    if excluded_dhash_set:
                        try:
                            from evidence_validator import EvidenceValidator
                            dh = EvidenceValidator.compute_image_dhash(abs_p)
                            if dh and any(ref_dh and EvidenceValidator.hamming_distance(dh, ref_dh) <= 3 for ref_dh in excluded_dhash_set):
                                continue
                        except Exception:
                            pass
                sz = os.path.getsize(abs_p)
                mtime = os.path.getmtime(abs_p)
                fn = os.path.basename(abs_p).lower()
                # Condition: File > 8MB AND (explicitly mentioned in text OR freshly generated < 3600s or auto_scan_deliverables)
                max_fresh_age = 3600.0 if auto_scan_deliverables else 600.0
                if sz > threshold_bytes and ((fn in text.lower()) or (now - mtime <= max_fresh_age) or (auto_scan_deliverables and any(ext in fn for ext in [".apk", ".aab", ".zip"]))):
                    oversized.append((abs_p, sz / (1024 * 1024)))
            except Exception:
                pass
    # Deduplicate by file path
    seen = set()
    deduped = []
    for p, sz in oversized:
        if p not in seen:
            seen.add(p)
            deduped.append((p, sz))
    return deduped


def resolve_app_name(file_path: str, workspace_root: Optional[str] = None) -> str:
    """Extracts a clean, canonical app/project name for deliverable naming."""
    fname = os.path.basename(file_path)
    base_name, _ = os.path.splitext(fname)
    if fname.lower().endswith(".tar.gz"):
        base_name = fname[:-7]
    
    # Strip common suffixes like -latest, -debug, -release, -20260901_123456
    clean_base = re.sub(r'-(?:latest|debug|release|unsigned|signed|\d{8}_\d{6}|\d{8})$', '', base_name, flags=re.IGNORECASE)
    
    if clean_base.lower() in ("app", "output", "build", "dist", "release", "debug", ""):
        ws = workspace_root or os.path.dirname(os.path.abspath(file_path))
        ws_name = os.path.basename(os.path.abspath(ws))
        clean_ws = re.sub(r'^![0-9]+_', '', ws_name)
        clean_ws = clean_ws.replace("_", " ").replace("-", " ").title().replace(" ", "")
        if clean_ws:
            return clean_ws
        return "App"
    return clean_base

def ensure_timestamped_deliverable(
    file_path: str,
    workspace_root: Optional[str] = None
) -> Tuple[str, str, str, Optional[str]]:
    """
    Ensures deliverables (.apk, .zip, etc.) automatically have a timestamped copy and -latest copy.
    Returns: (timestamped_name, latest_name, timestamped_local_path, latest_local_path)
    """
    if not os.path.isfile(file_path):
        fname = os.path.basename(file_path)
        return (fname, fname, file_path, None)
    
    fname = os.path.basename(file_path)
    ext = ""
    for candidate_ext in (".tar.gz", ".apk", ".aab", ".zip", ".tgz", ".jar"):
        if fname.lower().endswith(candidate_ext):
            ext = candidate_ext
            break
    
    if not ext:
        return (fname, fname, file_path, None)
    
    mtime = os.path.getmtime(file_path)
    dt = datetime.fromtimestamp(mtime)
    time_str = dt.strftime("%Y%m%d_%H%M%S")
    
    has_timestamp = bool(re.search(r'\d{8}_\d{6}', fname))
    app_name = resolve_app_name(file_path, workspace_root)
    
    timestamped_name = fname if has_timestamp else f"{app_name}-{time_str}{ext}"
    latest_name = f"{app_name}-latest{ext}"
    
    target_dir = os.path.dirname(os.path.abspath(file_path))
    if workspace_root and os.path.isdir(workspace_root):
        abs_file = os.path.abspath(file_path)
        abs_ws = os.path.abspath(workspace_root)
        # Only place into workspace_root if the deliverable is physically inside that workspace tree.
        # This prevents deliverables from external project workspaces from spilling into the agent engine workspace.
        if abs_file.startswith(abs_ws):
            target_dir = abs_ws
        
    ts_path = os.path.join(target_dir, timestamped_name)
    latest_path = os.path.join(target_dir, latest_name)
    
    try:
        if not os.path.exists(ts_path) or os.path.abspath(file_path) != os.path.abspath(ts_path):
            shutil.copy2(file_path, ts_path)
        if not os.path.exists(latest_path) or os.path.abspath(file_path) != os.path.abspath(latest_path):
            shutil.copy2(file_path, latest_path)
    except Exception as e:
        logger.debug(f"Could not create timestamped copy: {e}")
        ts_path = file_path
        latest_path = None
        
    return (timestamped_name, latest_name, ts_path, latest_path)

def prepare_message_payload(
    final_response: str,
    workspace_root: Optional[str] = None,
    max_total_bytes: int = DISCORD_MAX_FILE_BYTES,
    auto_scan_deliverables: bool = False,
    excluded_hashes: Optional[Any] = None,
    excluded_dhashes: Optional[Any] = None
) -> Tuple[List[str], List[discord.File]]:
    """
    Prepares chunks and discord.File attachments safely.
    If oversized deliverables (>8.0MB) are referenced, automatically appends a notice.
    Caps total attached files size to max_total_bytes.
    Excludes files matching excluded_hashes (SHA-256) or excluded_dhashes (perceptual dHash <= 3).
    """
    augmented_response = cleanse_agent_output(final_response or "")
    oversized = extract_oversized_attachments(
        augmented_response,
        workspace_root=workspace_root,
        auto_scan_deliverables=auto_scan_deliverables,
        excluded_hashes=excluded_hashes,
        excluded_dhashes=excluded_dhashes
    )
    if oversized:
        lines = []
        seen_names = set()
        for p, sz in oversized:
            ts_name, latest_name, ts_path, latest_path = ensure_timestamped_deliverable(p, workspace_root=workspace_root)
            actual_loc = ts_path or p
            if ts_name not in seen_names:
                seen_names.add(ts_name)
                lines.append(f"• `{ts_name}` ({sz:.1f}MB) - 로컬 저장 위치: `{actual_loc}`")
            if latest_path and os.path.isfile(latest_path) and latest_name not in seen_names:
                seen_names.add(latest_name)
                lines.append(f"• `{latest_name}` ({sz:.1f}MB) - 로컬 저장 위치: `{latest_path}`")
        notice = (
            "\n\n📎 **[대용량 파일 안내]**\n"
            "디스코드 업로드 제한(8MB)을 초과한 산출물은 로컬에 보존되어 있습니다:\n"
            + "\n".join(lines)
        )
        augmented_response += notice

    chunks = split_message(augmented_response)
    valid_paths = extract_local_attachments(
        augmented_response,
        workspace_root=workspace_root,
        auto_scan_deliverables=auto_scan_deliverables,
        excluded_hashes=excluded_hashes,
        excluded_dhashes=excluded_dhashes
    )

    # Build safe file attachments capped by max_total_bytes
    file_attachments: List[discord.File] = []
    accumulated_bytes = 0
    for p in valid_paths[:10]:
        try:
            sz = os.path.getsize(p)
            if accumulated_bytes + sz <= max_total_bytes:
                file_attachments.append(discord.File(fp=p, filename=os.path.basename(p)))
                accumulated_bytes += sz
        except Exception:
            pass

    return chunks, file_attachments

async def async_prepare_message_payload(
    final_response: str,
    workspace_root: Optional[str] = None,
    max_total_bytes: int = DISCORD_MAX_FILE_BYTES,
    auto_scan_deliverables: bool = False,
    excluded_hashes: Optional[Any] = None,
    excluded_dhashes: Optional[Any] = None
) -> Tuple[List[str], List[discord.File]]:
    """
    Asynchronously prepares chunks and discord.File attachments safely.
    For oversized deliverables (>8.0MB) explicitly referenced or freshly generated,
    uploads them to Google Drive (with caching to prevent repeated latency)
    and embeds direct download links into the response.
    Automatically applies timestamped names ({AppName}-YYYYMMDD_HHMMSS.apk) and {AppName}-latest.apk by default.
    Excludes files matching excluded_hashes (SHA-256) or excluded_dhashes (perceptual dHash <= 3).
    """
    augmented_response = cleanse_agent_output(final_response or "")
    oversized = extract_oversized_attachments(
        augmented_response,
        workspace_root=workspace_root,
        auto_scan_deliverables=auto_scan_deliverables,
        excluded_hashes=excluded_hashes,
        excluded_dhashes=excluded_dhashes
    )

    if oversized:
        try:
            from gdrive_uploader import GoogleDriveUploader
            cloud_lines = []
            seen_cloud_names = set()
            for p, sz in oversized[:3]:
                ts_name, latest_name, ts_path, latest_path = ensure_timestamped_deliverable(p, workspace_root=workspace_root)
                upload_target_path = ts_path if (ts_path and os.path.isfile(ts_path)) else p
                
                try:
                    mtime = os.path.getmtime(upload_target_path)
                    raw_sz = os.path.getsize(upload_target_path)
                    # Check in-memory cache first to eliminate repeated network upload delay!
                    cached_entry = _GDRIVE_UPLOAD_CACHE.get(upload_target_path)
                    if cached_entry and cached_entry[0] == mtime and cached_entry[1] == raw_sz:
                        res = cached_entry[2]
                    else:
                        res = await asyncio.wait_for(
                            GoogleDriveUploader.upload_file(upload_target_path, display_name=ts_name),
                            timeout=35.0
                        )
                        if res.get("success"):
                            _GDRIVE_UPLOAD_CACHE[upload_target_path] = (mtime, raw_sz, res)

                    if res.get("success"):
                        provider = res.get("provider", "Google Drive")
                        url = res.get("download_url") or res.get("view_url")
                        if ts_name not in seen_cloud_names:
                            seen_cloud_names.add(ts_name)
                            cloud_lines.append(f"• 📥 **[{provider}]** [{ts_name} ({sz:.1f}MB)]({url})")
                        
                        # Also upload latest version if available
                        if latest_path and os.path.isfile(latest_path) and latest_name not in seen_cloud_names:
                            try:
                                lat_mtime = os.path.getmtime(latest_path)
                                lat_sz = os.path.getsize(latest_path)
                                lat_cached = _GDRIVE_UPLOAD_CACHE.get(latest_path)
                                if lat_cached and lat_cached[0] == lat_mtime and lat_cached[1] == lat_sz:
                                    lat_res = lat_cached[2]
                                else:
                                    lat_res = await asyncio.wait_for(
                                        GoogleDriveUploader.upload_file(latest_path, display_name=latest_name),
                                        timeout=35.0
                                    )
                                    if lat_res.get("success"):
                                        _GDRIVE_UPLOAD_CACHE[latest_path] = (lat_mtime, lat_sz, lat_res)
                                if lat_res.get("success"):
                                    lat_url = lat_res.get("download_url") or lat_res.get("view_url")
                                    seen_cloud_names.add(latest_name)
                                    cloud_lines.append(f"• 📥 **[{provider}]** [{latest_name} ({sz:.1f}MB)]({lat_url})")
                            except Exception:
                                pass
                    else:
                        if ts_name not in seen_cloud_names:
                            seen_cloud_names.add(ts_name)
                            cloud_lines.append(f"• `{ts_name}` ({sz:.1f}MB) - 로컬 저장 위치: `{upload_target_path}`")
                        if latest_path and os.path.isfile(latest_path) and latest_name not in seen_cloud_names:
                            seen_cloud_names.add(latest_name)
                            cloud_lines.append(f"• `{latest_name}` ({sz:.1f}MB) - 로컬 저장 위치: `{latest_path}`")
                except Exception:
                    if ts_name not in seen_cloud_names:
                        seen_cloud_names.add(ts_name)
                        cloud_lines.append(f"• `{ts_name}` ({sz:.1f}MB) - 로컬 저장 위치: `{upload_target_path}`")
                    if latest_path and os.path.isfile(latest_path) and latest_name not in seen_cloud_names:
                        seen_cloud_names.add(latest_name)
                        cloud_lines.append(f"• `{latest_name}` ({sz:.1f}MB) - 로컬 저장 위치: `{latest_path}`")

            if cloud_lines:
                notice = (
                    "\n\n☁️ **[대용량 파일 Google Drive 다운로드 (8MB 초과)]**\n"
                    "디스코드 직접 첨부 한도(8MB)를 초과한 산출물을 공식 Google Drive(30TB)에 안전하게 업로드했습니다:\n"
                    + "\n".join(cloud_lines)
                )
                augmented_response += notice
        except Exception:
            pass

    chunks = split_message(augmented_response, workspace=workspace_root)
    valid_paths = extract_local_attachments(
        augmented_response,
        workspace_root=workspace_root,
        auto_scan_deliverables=auto_scan_deliverables,
        excluded_hashes=excluded_hashes,
        excluded_dhashes=excluded_dhashes
    )

    file_attachments: List[discord.File] = []
    accumulated_bytes = 0
    for p in valid_paths[:10]:
        try:
            sz = os.path.getsize(p)
            if accumulated_bytes + sz <= max_total_bytes:
                file_attachments.append(discord.File(fp=p, filename=os.path.basename(p)))
                accumulated_bytes += sz
        except Exception:
            pass

    return chunks, file_attachments

async def _raw_dispatch_send(
    target,
    kwargs: Dict[str, Any],
    is_reply: bool = False,
    reference: Optional[discord.Message] = None
) -> Optional[discord.Message]:
    """
    Universally dispatches a send/reply payload to any valid Discord target
    (Context, TextChannel, Thread, Message, Member, Webhook, Interaction).
    """
    if not kwargs:
        return None

    # 1. Reply to explicit reference message if requested
    if is_reply and reference and hasattr(reference, "reply"):
        try:
            return await reference.reply(**kwargs)
        except Exception as e:
            logger.debug(f"_raw_dispatch_send reference.reply failed: {e}")

    # 2. Reply to target if target is a message/context with reply
    if is_reply and hasattr(target, "reply"):
        try:
            return await target.reply(**kwargs)
        except Exception as e:
            logger.debug(f"_raw_dispatch_send target.reply failed: {e}")

    # 3. Direct send on target (TextChannel, Thread, Context, DMChannel)
    if hasattr(target, "send"):
        try:
            return await target.send(**kwargs)
        except Exception as e:
            logger.warning(f"_raw_dispatch_send target.send failed: {e}")

    # 4. If target is a discord.Message (which has .channel.send)
    if hasattr(target, "channel") and hasattr(target.channel, "send"):
        try:
            return await target.channel.send(**kwargs)
        except Exception as e:
            logger.warning(f"_raw_dispatch_send target.channel.send failed: {e}")

    # 5. If target is an Interaction (InteractionResponse / InteractionFollowup)
    if hasattr(target, "followup") and hasattr(target.followup, "send"):
        try:
            return await target.followup.send(**kwargs)
        except Exception as e:
            logger.warning(f"_raw_dispatch_send followup.send failed: {e}")
    elif hasattr(target, "response") and hasattr(target.response, "send_message"):
        try:
            return await target.response.send_message(**kwargs)
        except Exception as e:
            logger.warning(f"_raw_dispatch_send response.send_message failed: {e}")

    logger.error(f"_raw_dispatch_send: Failed to dispatch to target={type(target)} with kwargs={list(kwargs.keys())}")
    return None

def format_quote_reference_bar(text: str, author: Optional[str] = None, max_length: int = 120) -> str:
    """
    Builds a Discord Quote/Reference Bar with leading quote indicators (> ),
    author attribution, and truncated context for clear threaded visual reference.
    Sanitizes backticks and special formatting to prevent breaking subsequent message blocks.
    """
    if not text or not text.strip():
        return ""
    clean = text.strip().replace("`", "'")
    clean = " ".join(clean.split())
    clean = clean.lstrip(">").strip()
    if len(clean) > max_length:
        clean = clean[:max_length - 3] + "..."
    author_tag = f"**@{author}** " if author else ""
    return f"> 💬 {author_tag}{clean}\n\n"

DEFAULT_TOOLBAR_EMOJIS = ["👍", "👎", "🔄", "🛑"]

async def attach_reaction_toolbar(
    message: Optional[discord.Message],
    emojis: Optional[List[str]] = None
) -> None:
    """
    Attaches an interactive reaction & toolbar (emojis) to a sent Discord message.
    Enables one-click user feedback (❤️ / 👎), retry (🔄), or cancellation (🛑).
    """
    if not message or not hasattr(message, "add_reaction"):
        return
    toolbar = emojis or DEFAULT_TOOLBAR_EMOJIS
    for emoji in toolbar:
        try:
            await message.add_reaction(emoji)
        except Exception:
            break

async def safe_send_discord_message(
    target,
    content: str = "",
    embed: Optional[discord.Embed] = None,
    embeds: Optional[List[discord.Embed]] = None,
    files: Optional[List[discord.File]] = None,
    is_reply: bool = False,
    reference: Optional[discord.Message] = None,
    quote_reference: Optional[str] = None,
    quote_author: Optional[str] = None,
    add_toolbar: bool = False,
    toolbar_emojis: Optional[List[str]] = None
) -> Optional[discord.Message]:
    """
    Defensively sends or replies to a Discord message or embed.
    Gracefully handles HTTPException 413, 429, 50035, and payload errors without crashing or dropping messages.
    Supports optional Quote/Reference Bar prepending and Reaction & Toolbar emoji attachment.
    """
    # Prepend Quote/Reference Bar if requested
    if quote_reference:
        quote_bar = format_quote_reference_bar(quote_reference, author=quote_author)
        content = f"{quote_bar}{content}"

    # 1. Pre-filter files to prevent oversized payloads
    safe_files: List[discord.File] = []
    accumulated_bytes = 0
    if files:
        for f in files:
            try:
                f_fp = getattr(f, "fp", None)
                f_size = 0
                if f_fp and hasattr(f_fp, "name") and os.path.exists(f_fp.name):
                    f_size = os.path.getsize(f_fp.name)
                elif isinstance(f_fp, (bytes, bytearray)):
                    f_size = len(f_fp)
                if f_size > 0 and (accumulated_bytes + f_size) <= DISCORD_MAX_FILE_BYTES:
                    safe_files.append(f)
                    accumulated_bytes += f_size
                elif f_size > DISCORD_MAX_FILE_BYTES:
                    logger.warning(f"File {f.filename} exceeds single file upload limit ({f_size/(1024*1024):.1f}MB > {DISCORD_MAX_FILE_BYTES/(1024*1024):.1f}MB). Omitting from direct attachment.")
            except Exception:
                pass

    # CRITICAL: If content exceeds 1900 chars (e.g. quote bar pushed it over limit or long text),
    # safely split into sequential chunks to guarantee NO 2000-char Discord 400 Bad Request
    if content and len(content) > 1900:
        logger.info(f"safe_send_discord_message: Content length ({len(content)}) exceeds 1900 chars. Splitting into safe chunks.")
        chunks = split_message(content, limit=1900)
        last_sent = None
        for i, ch in enumerate(chunks):
            sub_files = safe_files if i == 0 else None
            sub_ref = reference if i == 0 else None
            sub_reply = is_reply if i == 0 else False
            sub_toolbar = add_toolbar if i == len(chunks) - 1 else False
            last_sent = await safe_send_discord_message(
                target,
                content=ch,
                embed=embed if i == 0 else None,
                embeds=embeds if i == 0 else None,
                files=sub_files,
                is_reply=sub_reply,
                reference=sub_ref,
                add_toolbar=sub_toolbar,
                toolbar_emojis=toolbar_emojis
            )
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)
        return last_sent

    send_kwargs: Dict[str, Any] = {}
    if content:
        send_kwargs["content"] = content
    if embed:
        send_kwargs["embed"] = embed
    if embeds:
        send_kwargs["embeds"] = embeds
    if safe_files:
        send_kwargs["files"] = safe_files

    sent_msg: Optional[discord.Message] = None
    curr_is_reply = is_reply
    curr_reference = reference

    # Try up to 4 attempts with accurate rate-limit backoff and attachment degradation
    for attempt in range(4):
        try:
            if curr_is_reply and curr_reference and hasattr(curr_reference, "reply"):
                sent_msg = await curr_reference.reply(**send_kwargs)
            elif curr_is_reply and hasattr(target, "reply"):
                sent_msg = await target.reply(**send_kwargs)
            elif hasattr(target, "send"):
                sent_msg = await target.send(**send_kwargs)
            elif hasattr(target, "channel") and hasattr(target.channel, "send"):
                sent_msg = await target.channel.send(**send_kwargs)
            
            if sent_msg and add_toolbar:
                try:
                    asyncio.create_task(attach_reaction_toolbar(sent_msg, toolbar_emojis))
                except Exception:
                    pass
            return sent_msg
        except discord.errors.HTTPException as http_err:
            is_429 = (getattr(http_err, "status", None) == 429) or ("429" in str(http_err))
            if is_429:
                retry_sec = getattr(http_err, "retry_after", 2.0)
                # Respect Discord's retry_after fully (plus 0.5s safety padding), cap at 15.0s max
                wait_time = min(max(float(retry_sec) + 0.5, 1.5), 15.0)
                logger.warning(f"safe_send_discord_message: Discord rate limited (429). Backing off {wait_time:.1f}s (attempt {attempt+1}/4)...")
                await asyncio.sleep(wait_time)
                continue
            
            # If payload too large (413 / 40005) or general file upload failure, retry without files
            if "files" in send_kwargs and send_kwargs["files"]:
                logger.warning(f"safe_send_discord_message: Send with files failed ({http_err}). Dropping files and retrying pure text...")
                send_kwargs.pop("files", None)
                continue

            # If reply reference invalid/deleted (10008 / 50035), retry without reply
            if curr_is_reply:
                logger.warning(f"safe_send_discord_message: Reply failed ({http_err}). Retrying as direct message...")
                curr_is_reply = False
                curr_reference = None
                continue

            logger.warning(f"safe_send_discord_message: Primary send failed ({http_err}). Moving to emergency fallback.")
            break
        except Exception as err:
            logger.warning(f"safe_send_discord_message: Unexpected send error ({err}). Moving to fallback.")
            if "files" in send_kwargs:
                send_kwargs.pop("files", None)
            break

    # Emergency Fallback: Send plain text chunks without files and without reply
    logger.warning("safe_send_discord_message: Initiating emergency fallback send...")
    if content:
        fallback_chunks = split_message(content, limit=1800)
        last_msg = None
        for idx, c in enumerate(fallback_chunks):
            last_msg = await _raw_dispatch_send(target, {"content": c}, is_reply=False, reference=None)
            if idx < len(fallback_chunks) - 1:
                await asyncio.sleep(0.3)
        return last_msg
    elif embed or embeds:
        emb_kw = {"embed": embed} if embed else {"embeds": embeds}
        return await _raw_dispatch_send(target, emb_kw, is_reply=False, reference=None)
    
    return None

# Alias for backward compatibility
extract_local_image_files = extract_local_attachments

def create_workspace_zip(workspace_path: str, subpath: Optional[str] = None, max_size_mb: float = 9.5) -> Optional[str]:
    """
    Creates a clean zip archive of the workspace or subfolder,
    automatically excluding node_modules, .venv, .git, and __pycache__.
    Includes defensive checks against oversized files and disk-full conditions.
    Returns the path to the temporary zip file (< max_size_mb).
    """
    import zipfile
    import tempfile

    target_dir = os.path.abspath(os.path.join(workspace_path, subpath)) if subpath else os.path.abspath(workspace_path)
    if not os.path.isdir(target_dir):
        return None

    base_name = os.path.basename(target_dir.rstrip("/")) or "workspace"
    tf = tempfile.NamedTemporaryFile("wb", suffix=f"_{base_name}.zip", delete=False)
    zip_path = tf.name
    tf.close()

    max_bytes = int(max_size_mb * 1024 * 1024)
    max_individual_file_bytes = 8 * 1024 * 1024  # Skip individual files > 8MB
    max_total_uncompressed_bytes = 30 * 1024 * 1024  # Cap uncompressed scan to 30MB
    accumulated_bytes = 0

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(target_dir):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]
                for file in files:
                    full_path = os.path.join(root, file)
                    if os.path.isfile(full_path):
                        try:
                            f_size = os.path.getsize(full_path)
                            if f_size > max_individual_file_bytes:
                                continue
                            accumulated_bytes += f_size
                            if accumulated_bytes > max_total_uncompressed_bytes:
                                break
                            rel_path = os.path.relpath(full_path, target_dir)
                            zf.write(full_path, rel_path)
                        except (OSError, IOError):
                            continue
                if accumulated_bytes > max_total_uncompressed_bytes:
                    break

        if not os.path.exists(zip_path) or os.path.getsize(zip_path) > max_bytes:
            if os.path.exists(zip_path):
                os.remove(zip_path)
            return None
        return zip_path
    except Exception:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        return None


def format_kst(dt) -> str:
    """Converts a datetime to KST (UTC+9) string."""
    import datetime
    if dt is None:
        return "N/A"
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    kst_tz = datetime.timezone(datetime.timedelta(hours=9))
    kst_dt = dt.astimezone(kst_tz)
    return kst_dt.strftime("%Y-%m-%d %H:%M:%S KST")


async def export_channel_history(
    channel,
    limit: Optional[int] = None,
    format_type: str = "md",
    workspace: Optional[str] = None,
    bot_roles: Optional[Dict[int, str]] = None,
    on_progress: Optional[Callable[[int], Any]] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Exports full or partial message history of a Discord channel into a formatted Markdown (.md) or Text (.txt) file
    using a stream-to-disk architecture to prevent high RAM/OOM crashes on large channels.
    Returns: (output_file_path, filename, stats_dict)
    """
    import datetime
    import tempfile
    import shutil

    bot_roles = bot_roles or {}
    ch_name = getattr(channel, "name", f"channel_{channel.id}")
    guild_name = getattr(getattr(channel, "guild", None), "name", "Direct Message")

    # Safe filename
    clean_name = re.sub(r'[^\w\-_\. ]', '_', ch_name).strip() or f"channel_{channel.id}"
    now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    time_str = now_kst.strftime("%Y%m%d_%H%M%S")
    ext = "txt" if format_type.lower() == "txt" else "md"
    filename = f"{clean_name}_대화기록_{time_str}.{ext}"

    temp_dir = tempfile.mkdtemp(prefix="discord_export_")
    try:
        out_path = os.path.join(temp_dir, filename)
        body_path = os.path.join(temp_dir, "body.tmp")

        total_count = 0
        user_count = 0
        bot_count = 0
        attachment_count = 0
        first_time = "N/A"
        last_time = "N/A"

        last_progress_time = time.time()

        # Step 1: Stream formatted message bodies directly to temporary disk file
        with open(body_path, "w", encoding="utf-8") as body_file:
            async for msg in channel.history(limit=limit, oldest_first=True):
                total_count += 1
                if total_count == 1:
                    first_time = format_kst(msg.created_at)
                last_time = format_kst(msg.created_at)

                if msg.author.bot:
                    bot_count += 1
                else:
                    user_count += 1
                if msg.attachments:
                    attachment_count += len(msg.attachments)

                msg_time = format_kst(msg.created_at)

                if ext == "md":
                    # Author role formatting
                    if msg.author.id in bot_roles:
                        author_label = f"**{bot_roles[msg.author.id]}**"
                    elif msg.author.bot:
                        author_label = f"🤖 **[Bot] {msg.author.display_name}**"
                    else:
                        author_label = f"👤 **[User] {msg.author.display_name}** (`@{msg.author.name}`)"

                    body_file.write(f"### `#{total_count:04d}` [{msg_time}] {author_label}\n\n")

                    if msg.content and msg.content.strip():
                        body_file.write(f"{msg.content.strip()}\n\n")

                    # Handle attachments
                    if msg.attachments:
                        body_file.write("**📎 첨부 파일:**\n")
                        for att in msg.attachments:
                            sz_kb = att.size / 1024
                            body_file.write(f"- [{att.filename}]({att.url}) (`{sz_kb:.1f} KB`)\n")
                        body_file.write("\n")

                    # Handle embeds
                    if msg.embeds:
                        for emb in msg.embeds:
                            body_file.write("> 📋 **[임베드 카드]**\n")
                            if emb.title:
                                body_file.write(f"> **제목**: {emb.title}\n")
                            if emb.description:
                                body_file.write(f"> **설명**: {emb.description}\n")
                            for f in emb.fields:
                                body_file.write(f"> • **{f.name}**: {f.value}\n")
                            body_file.write("\n")

                    # Handle reactions
                    if msg.reactions:
                        rxn_strs = [f"{r.emoji} ({r.count})" for r in msg.reactions]
                        body_file.write(f"*⭐ 반응: {', '.join(rxn_strs)}*\n\n")

                    body_file.write("---\n\n")

                else:
                    # Plain text format
                    if msg.author.id in bot_roles:
                        author_name = bot_roles[msg.author.id]
                    elif msg.author.bot:
                        author_name = f"[Bot] {msg.author.display_name}"
                    else:
                        author_name = f"[User] {msg.author.display_name} (@{msg.author.name})"

                    body_file.write(f"[{total_count:04d}] [{msg_time}] {author_name}\n")
                    if msg.content:
                        body_file.write(f"{msg.content}\n")
                    if msg.attachments:
                        for att in msg.attachments:
                            body_file.write(f"  [첨부파일] {att.filename} ({att.size / 1024:.1f} KB) -> {att.url}\n")
                    body_file.write("-" * 60 + "\n")

                # Notify progress every 100 messages or 2 seconds
                if on_progress and (total_count % 100 == 0 or (time.time() - last_progress_time) > 2.0):
                    last_progress_time = time.time()
                    try:
                        await on_progress(total_count)
                    except Exception:
                        pass

        # Step 2: Assemble Header + Body Stream + Footer into final out_path
        with open(out_path, "w", encoding="utf-8") as out_file:
            if ext == "md":
                # Markdown Header
                out_file.write(f"# 📜 [프로젝트 대화 기록 백업] #{ch_name}\n\n")
                out_file.write("> 이 문서는 Antigravity AI 시스템에 의해 자동 생성된 프로젝트 대화 전문 기록입니다.\n\n")
                out_file.write("### 📌 기본 메타 정보\n")
                out_file.write(f"- **채널명**: `#{ch_name}` (ID: `{channel.id}`)\n")
                out_file.write(f"- **디스코드 서버**: `{guild_name}`\n")
                if workspace:
                    out_file.write(f"- **프로젝트 작업 공간**: `{workspace}`\n")
                out_file.write(f"- **백업 일시**: `{now_kst.strftime('%Y-%m-%d %H:%M:%S KST')}`\n")
                out_file.write(f"- **수집 대화 기간**: `{first_time}` ~ `{last_time}`\n")
                out_file.write(f"- **총 수집 메시지**: **{total_count:,}개** (👤 사용자: {user_count:,}개 | 🤖 봇: {bot_count:,}개)\n")
                out_file.write(f"- **총 첨부 파일**: **{attachment_count:,}개**\n\n")
                out_file.write("---\n\n")
                out_file.write("## 💬 대화 타임라인 전문\n\n")

                # Stream body from temporary file
                if os.path.exists(body_path):
                    with open(body_path, "r", encoding="utf-8") as body_in:
                        shutil.copyfileobj(body_in, out_file)

                # Footer
                out_file.write("## 📊 대화 기록 요약 통계\n")
                out_file.write(f"- **총 메시지 수**: {total_count:,}건\n")
                out_file.write(f"- **사용자 발화**: {user_count:,}건 ({(user_count / max(1, total_count)) * 100:.1f}%)\n")
                out_file.write(f"- **봇 응답**: {bot_count:,}건 ({(bot_count / max(1, total_count)) * 100:.1f}%)\n")
                out_file.write(f"- **첨부파일 수**: {attachment_count:,}건\n")
                out_file.write("- **추출 생성기**: AGY Discord Bot Platform\n\n")

            else:
                # Plain text format header
                out_file.write("=" * 80 + "\n")
                out_file.write(f" [프로젝트 대화 기록 백업] #{ch_name}\n")
                out_file.write(f" 서버: {guild_name} | 채널 ID: {channel.id}\n")
                if workspace:
                    out_file.write(f" 작업 공간: {workspace}\n")
                out_file.write(f" 백업 일시: {now_kst.strftime('%Y-%m-%d %H:%M:%S KST')}\n")
                out_file.write(f" 총 메시지: {total_count:,}개 (사용자: {user_count:,}개, 봇: {bot_count:,}개, 첨부: {attachment_count:,}개)\n")
                out_file.write(f" 수집 기간: {first_time} ~ {last_time}\n")
                out_file.write("=" * 80 + "\n\n")

                # Stream body from temporary file
                if os.path.exists(body_path):
                    with open(body_path, "r", encoding="utf-8") as body_in:
                        shutil.copyfileobj(body_in, out_file)

        # Clean up temporary body file
        if os.path.exists(body_path):
            try:
                os.remove(body_path)
            except Exception:
                pass

        stats = {
            "filename": filename,
            "total_messages": total_count,
            "user_messages": user_count,
            "bot_messages": bot_count,
            "attachments": attachment_count,
            "first_time": first_time,
            "last_time": last_time,
            "file_size_bytes": os.path.getsize(out_path),
            "format": ext
        }

        return out_path, filename, stats
    except Exception:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
