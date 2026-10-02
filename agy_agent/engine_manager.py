import os
import re
import shutil
import subprocess
import asyncio
import logging
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable, Any
import discord
from discord.ui import View, Select, Button

import config
from session_manager import ChannelSession, kill_process_tree
try:
    from model_registry import AntigravityModelRegistry
except ImportError:
    from agy_agent.model_registry import AntigravityModelRegistry

logger = logging.getLogger(__name__)

# [🚫 무질문 자율 진행 헌법] 비대면(Discord) 환경에서 에이전트가 사용자에게 되묻거나 승인을
# 요청하여 파이프라인이 멈추는 것을 막기 위해, 모든 비-AGY 엔진 프롬프트 최상단에 강제 주입합니다.
AUTONOMY_NO_QUESTION_DIRECTIVE = (
    "🚫 **무질문 자율 진행 (최우선 규칙)**: 사용자에게 되묻거나 확인/승인/추가 정보를 요청하지 마십시오. "
    "(예: '두 가지만 여쭙겠습니다', '~해도 될까요?', '어떤 방식을 원하세요?') "
    "요청이 모호하면 가장 합리적인 가정을 스스로 세워 짧게 명시하고 즉시 끝까지 실행하십시오. "
    "'먼저 파악하겠습니다' 같은 예고로 턴을 끝내지 말고 같은 턴에서 실제 산출물까지 완성하십시오. "
    "이 환경은 비대면(Discord)이므로 사용자 답변을 받을 수 없습니다. `ask_question`/`question` 도구를 사용하지 마십시오."
)

# [🧭 AGY 코딩 행동규범 주입] AGY_AGENT.md 의 런타임 요약을 무질문 지시문 뒤에 결합한다.
# 이 지시문은 모든 엔진(AGY 네이티브/OpenCode/Hermes 등) 프롬프트 최상단에 함께 전달된다.
# 파일이 없어도 무중단(빈 문자열 폴백)으로 동작한다.
try:
    from agent_guidelines import AGY_AGENT_GUIDELINES as _AGY_GUIDELINES
except Exception:  # 무중단 폴백
    _AGY_GUIDELINES = ""
if _AGY_GUIDELINES:
    AUTONOMY_NO_QUESTION_DIRECTIVE = f"{AUTONOMY_NO_QUESTION_DIRECTIVE}\n\n{_AGY_GUIDELINES}"


async def safe_tool_notify(on_tool_update_fn: Optional[Callable[[str], Any]], msg: str):
    if not on_tool_update_fn:
        return
    try:
        await asyncio.wait_for(on_tool_update_fn(msg), timeout=4.0)
    except Exception:
        pass


# [🛡️ 3603초 프리징 원천 차단] 엔진 CLI 가 "실행 제한 시간"을 초과했는데도
# 프로세스를 죽이지 않고 곧바로 `await proc.wait()` 를 호출하면, 네트워크/라우터
# 오류(HTTP 400 등)로 멈춘(hung) 자식 프로세스가 영원히 reap 되지 않아
# 어댑터 코루틴이 무한 대기 → 디스코드 상태 타이머가 특정 초(예: 3603s)에
# 고정된 채 채널 전체가 프리징된다. 아래 헬퍼가 그 고리를 끊는다.
async def _terminate_process_tree(proc, *, bounded_wait: float = 10.0) -> None:
    """프로세스 그룹을 강제 회수하고, 반드시 유한 시간 안에 자원을 반납시킨다."""
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    if pid:
        # [🛡️ 자살 방지] 프로세스가 우리와 같은 PGID 면 그룹 kill 을 하면
        # 봇/테스트 자신까지 회수될 수 있다 → 개별 kill(proc.kill)로만 처리한다.
        try:
            same_group = os.getpgid(pid) == os.getpgrp()
        except Exception:
            same_group = True
        if not same_group:
            try:
                kill_process_tree(int(pid))
            except Exception:
                pass
    for closer in ("kill", "terminate"):
        try:
            getattr(proc, closer)()
            break
        except Exception:
            continue
    try:
        await asyncio.wait_for(proc.wait(), timeout=max(0.5, float(bounded_wait)))
    except Exception:
        # SIGKILL 조차 먹지 않는 D-state 라도 여기서 절대 블록하지 않는다.
        pass


# [🛡️ 400 라우터 오류 Fast-Fail] opencode-go / OpenRouter 라우터가 잘못된 모델 ID를
# 받으면 HTTP 400 을 뱉고도 프로세스가 살아남아 무한 대기하는 경우가 있다.
# 이런 "회복 불가" 신호는 즉시 감지해 프로세스를 회수하고 에러를 반환한다.
#
# ⚠️ 오탐 방지: 'HTTP 400' 같은 문구는 에이전트가 작성한 코드/설명에도 등장할 수 있으므로
#   ① 모델 ID 자체가 잘못됐다는 강한 신호(어디서든 즉시 치명)와
#   ② 400 계열이나, 이는 구조화된 에러 이벤트/ stderr 에서만 치명으로 판정한다.
_STRONG_FATAL_ENGINE_MARKERS = (
    "not a valid model id",
    "not a valid model",
    "invalid model id",
    "invalid model",
    "model not found",
    "model_not_found",
    "no such model",
    "unsupported model",
    "invalid_request_error",
)
_HTTP400_ERROR_MARKERS = (
    "http 400",
    "http: 400",
    "status code 400",
    "status 400",
    "400 bad request",
    "400: bad request",
    "err_400",
    # [🛡️ provider 측 '잘못된 요청' 계열] 400 코드가 메시지에 없어도 즉시 회수 대상.
    # (실측: `This Go model trains on request data ... Privacy settings` → AI.Error.InvalidRequest)
    "trains on request data",
    "invalidrequest",
)

# [🛡️ 세션 누적 컨텍스트 400 Self-Healing] 아래 신호는 "저장된 세션의 누적 컨텍스트"가
# 원인일 수 있어, 같은 세션을 계속 재사용하면 100% 재발한다(실측: 동일 ses_* 에서 반복 400).
# 이런 경우 새 세션으로 1회 재시도하면 대부분 복구된다.
_RECOVERABLE_SESSION_400_MARKERS = (
    "provider request failed",
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "input is too long",
    "request entity too large",
    "string too long",
    "prompt is too long",
)

# [🔒 OpenCode Zen 프라이버시] 학습 허용(privacy) 미동의 모델은 재시도해도 회복되지 않는다
# → 세션 초기화가 아니라 '설정 변경 또는 모델 교체'가 정답이므로 별도 안내한다.
_PRIVACY_TRAINING_MARKERS = (
    "trains on request data",
    "privacy settings",
    "train on request data",
)


def _is_fatal_engine_error(text: str) -> bool:
    """모델 ID 자체가 잘못됐다는 강한 신호 — 에이전트 서술문에서도 오탐이 거의 없다."""
    low = str(text or "").lower()
    return any(marker in low for marker in _STRONG_FATAL_ENGINE_MARKERS)


def _is_http_400_error(text: str) -> bool:
    """구조화된 에러 이벤트/stderr 전용 판정 — 일반 stdout 서술문에는 적용하지 않는다."""
    low = str(text or "").lower()
    return any(marker in low for marker in _HTTP400_ERROR_MARKERS)


def _is_privacy_training_error(text: str) -> bool:
    """OpenCode Zen '요청 데이터 학습' 미허용으로 인한 InvalidRequest 여부."""
    low = str(text or "").lower()
    return any(marker in low for marker in _PRIVACY_TRAINING_MARKERS)


def _is_recoverable_session_error(text: str) -> bool:
    """저장된 세션 누적 컨텍스트가 원인일 수 있는(새 세션으로 회복 가능한) 400 여부."""
    low = str(text or "").lower()
    return any(marker in low for marker in _RECOVERABLE_SESSION_400_MARKERS)


class BaseEngineAdapter:
    """Base class for all pluggable AI CLI engine adapters."""
    engine_id: str = "base"
    name: str = "Base Engine"
    icon: str = "🤖"
    description: str = "기본 AI 코딩 엔진"
    env_var: str = "AI_ENGINE_PATH"
    default_paths: List[str] = []
    install_command: str = "설치 가이드 없음"
    auth_info: str = "별도 인증 없음"
    supported_models: List[str] = []
    # [🚫 무질문 자율 진행] 어댑터가 build_command 단계에서 AUTONOMY_NO_QUESTION_DIRECTIVE 를
    # 이미 주입하는지 여부. False 인 엔진은 AgentRunner 디스패치 계층에서 자동 주입된다.
    injects_autonomy_directive: bool = False

    def get_binary_path(self) -> Optional[str]:
        """Resolves binary executable path using env var, shutil.which, or default search paths."""
        custom_path = getattr(config, self.env_var, None) or os.getenv(self.env_var)
        if custom_path:
            expanded = os.path.abspath(os.path.expanduser(custom_path))
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded

        # Look in current system PATH
        which_path = shutil.which(self.engine_id)
        if which_path:
            return which_path

        # Check default fallback locations
        for p in self.default_paths:
            exp = os.path.abspath(os.path.expanduser(p))
            if os.path.isfile(exp) and os.access(exp, os.X_OK):
                return exp

        return None

    def is_installed(self) -> bool:
        return self.get_binary_path() is not None

    def get_version(self) -> str:
        bin_path = self.get_binary_path()
        if not bin_path:
            return "미설치"
        try:
            out = subprocess.check_output([bin_path, "--version"], text=True, stderr=subprocess.STDOUT, timeout=2.5)
            lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
            # Find the first line that looks like a version string (contains semantic version numbers like \d+\.\d+)
            # and ignore warning/notice/deprecation/path alias lines.
            for line in lines:
                lower = line.lower()
                if any(lower.startswith(w) for w in ["warning", "notice", "info", "error", "deprecat", "could not"]):
                    continue
                if re.search(r'\b\d+\.\d+(?:\.\d+)?', line):
                    return line
            # Fallback to the first non-warning line or the first line
            for line in lines:
                if not any(line.lower().startswith(w) for w in ["warning", "notice", "info", "error", "could not"]):
                    return line
            return lines[0] if lines else "버전 확인 불가"
        except Exception:
            return "설치됨 (버전 미확인)"

    def check_auth_status(self) -> Tuple[bool, str]:
        """Diagnoses authentication or credentials state on local host."""
        return True, "인증 상태 정상"

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        raise NotImplementedError

    async def execute(
        self,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        timeout: float = 3600.0,
        **kwargs
    ) -> Tuple[int, str, List[str]]:
        """
        Runs Hermes Agent binary (via subagent pipeline).
        Enforces strict timeout and unresponsiveness protection, and returns (returncode, accumulated_output, error_lines).
        """
        bin_path = self.get_binary_path()
        if not bin_path:
            return -1, "", [f"[{self.name}] 바이너리를 찾을 수 없습니다. ({self.install_command})"]

        session_role = kwargs.pop("role", None) or getattr(session, "role", "")
        session_mode = kwargs.pop("mode", None) or getattr(session, "mode", "")
        read_only = kwargs.pop("read_only", None)
        # [🧠 세션 지속성] session 객체를 build_command에 전달 (execute의 session 매개변수와 충돌 방지)
        cmd = self.build_command(prompt, session.workspace, model=model, role=session_role, read_only=read_only, mode=session_mode, session=session, **kwargs)
        logger.info(f"Executing {self.name} command: {' '.join(cmd[:6])}...")

        await safe_tool_notify(on_tool_update, f"{self.icon} **[{self.name}] 작업 시작 중...**")

        # [🛡️ LS Quota Leak Neutralization via CloudCode Caching Proxy]
        # AGY's internal LS goroutine polls daily-cloudcode-pa.googleapis.com every 6 min,
        # consuming ~0.3% Gemini quota per cycle. We redirect all CloudCode traffic through
        # a local caching proxy that caches control-plane responses (loadCodeAssist,
        # fetchAvailableModels, etc.) while transparently passing LLM inference through.
        agy_env = os.environ.copy()
        try:
            from cloudcode_cache_proxy import start_proxy, get_proxy_url
            proxy_url = get_proxy_url()
            if not proxy_url:
                port = start_proxy()
                proxy_url = f"http://127.0.0.1:{port}"
            agy_env["CLOUD_CODE_URL"] = proxy_url
            logger.info(f"[LS Shield] CloudCode proxy active: {proxy_url}")
        except Exception as e:
            logger.warning(f"[LS Shield] Proxy unavailable, using direct connection: {e}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=session.workspace,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=agy_env,
            limit=30 * 1024 * 1024,
            start_new_session=True
        )
        session.current_process = proc

        accumulated_output: List[str] = []
        stderr_lines: List[str] = []
        fatal_from_stderr = False

        async def read_stderr():
            nonlocal fatal_from_stderr
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    if len(stderr_lines) > 40:
                        stderr_lines.pop(0)
                    if _is_fatal_engine_error(decoded) or _is_http_400_error(decoded):
                        fatal_from_stderr = True

        stderr_task = asyncio.create_task(read_stderr())
        start_time = time.time()
        last_activity_time = time.time()
        last_update_time = time.time()

        # [🛡️ 적응형 무응답 타임아웃] config.WATCHDOG_SILENCE_TIMEOUT 과 통합하여
        # Thinking 모델(Claude Opus, DeepSeek R1 등)의 긴 추론 시간을 존중합니다.
        # 비-AGY 엔진은 최소 900초(15분), 최대 WATCHDOG_SILENCE_TIMEOUT(기본 1800초=30분).
        # 단, QA 역할은 EXTENDED_QA_TIMEOUT(기본 4000s)까지 조용히 추론해도 회수하지 않는다.
        engine_idle_limit = max(900.0, float(getattr(config, "WATCHDOG_SILENCE_TIMEOUT", 1800)))
        if str(session_role or "").lower().strip().startswith("qa") or str(session_role or "").lower().strip() in ("reviewer", "qa_bot", "auditor"):
            engine_idle_limit = max(engine_idle_limit, float(getattr(config, "EXTENDED_QA_TIMEOUT", 4000.0)))

        try:
            while True:
                # Enforce unresponsiveness deadline and total execution deadline
                now = time.time()
                if now - start_time > timeout:
                    logger.warning(f"Engine {self.name} exceeded overall timeout ({timeout}s). Terminating...")
                    stderr_lines.append(f"⚠️ 실행 제한 시간({int(timeout)}초) 초과로 안전 종료되었습니다.")
                    # [🛡️ 프리징 차단] 프로세스를 죽이지 않고 break 하면 아래 proc.wait() 가 무한 대기한다.
                    await _terminate_process_tree(proc)
                    break

                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                except asyncio.TimeoutError:
                    idle_sec = now - last_activity_time
                    if fatal_from_stderr:
                        stderr_lines.append("⛔ stderr 모델/라우터 오류 감지 → 무응답 대기 대신 즉시 회수")
                        await _terminate_process_tree(proc)
                        break
                    if idle_sec > engine_idle_limit:
                        logger.warning(f"Engine {self.name} unresponsive for {idle_sec:.0f}s (> {engine_idle_limit:.0f}s). Terminating unresponsive process...")
                        stderr_lines.append(f"⚠️ 무응답 상태 지속({int(idle_sec)}초)으로 안전 종료되었습니다.")
                        await _terminate_process_tree(proc)
                        break
                    if proc.returncode is not None:
                        break
                    continue

                if not line:
                    break

                last_activity_time = time.time()
                # [🛡️ StallGuard] 엔진이 실제 stdout 을 뱉었다 = 진짜 진행 → 채널 하트비트 갱신.
                # (비-AGY 엔진 경로에는 이 touch 가 없어서 900초 강제회수가 동작하지 않았다.)
                try:
                    session.touch("engine_stdout")
                except Exception:
                    pass
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
                accumulated_output.append(decoded)
                # [🛡️ 400 Fast-Fail] 라우터 모델 오류는 기다려도 회복되지 않는다.
                if _is_fatal_engine_error(decoded):
                    stderr_lines.append(f"⛔ 엔진 치명적 오류(모델/라우터 400) 감지 → 즉시 회수: {decoded[:200]}")
                    logger.error(f"Engine {self.name} fatal model/router error detected: {decoded[:200]}")
                    await _terminate_process_tree(proc)
                    break

                if now - last_update_time >= 4.0:
                    last_update_time = now
                    elapsed = int(now - start_time)
                    preview = decoded[:60].replace("`", "")
                    await safe_tool_notify(
                        on_tool_update,
                        f"{self.icon} **[{self.name} 실행 중 ({elapsed}s)]**\n`{preview}`"
                    )

            await asyncio.wait_for(proc.wait(), timeout=15.0)
            await asyncio.wait_for(stderr_task, timeout=5.0)
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            raise
        except asyncio.TimeoutError:
            # [🛡️ 프리징 차단] proc.wait()/stderr_task 가 bounded wait 를 넘긴 경우에도 무한 대기 금지.
            await _terminate_process_tree(proc)
        finally:
            await _terminate_process_tree(proc)
            # [🛡️ Quota Leak Prevention] Kill orphaned AGY Language Servers that
            # survive after the main CLI process exits. These servers poll Google
            # Cloud APIs (loadCodeAssist) every 6 minutes, silently burning quota.
            try:
                from session_manager import kill_agy_language_servers
                kill_agy_language_servers()
            except Exception:
                pass

        full_output = "\n".join(accumulated_output).strip()
        return proc.returncode, full_output, stderr_lines


class AgyAdapter(BaseEngineAdapter):
    engine_id = "agy"
    name = "Google Antigravity CLI"
    icon = "🚀"
    description = "구글 공식 자율 코딩 에이전트 CLI (멀티에이전트 협업 & 자가 치유 내장 엔진)"
    env_var = "AGY_PATH"
    default_paths = [
        "/home/jangsung/.local/bin/agy",
        "/home/jangsung/.gemini/antigravity-cli/bin/agy",
        "/usr/local/bin/agy",
    ]
    install_command = "Antigravity CLI 배포 바이너리 설치"
    auth_info = "Google 계정 로그인 (AGY CLI 세션 내장)"

    @property
    def supported_models(self) -> List[str]:
        dynamic = AntigravityModelRegistry.get_selectable_model_ids()
        if dynamic:
            return dynamic
        return [
            "gemini-3.8-flash-high",
            "gemini-3.8-flash-tiered",
            "gemini-3.7-flash-high",
            "gemini-3.7-flash-tiered",
            "gemini-3.7-flash-medium",
            "gemini-3.1-pro-high",
            "gemini-3.1-pro-low",
            "gemini-3.6-flash-high",
            "gemini-3.6-flash-tiered",
            "gemini-3.6-flash-medium",
            "gemini-3.5-flash-agent",
            "gemini-3-flash",
            "gpt-oss-120b-medium",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
        ]

    def check_auth_status(self) -> Tuple[bool, str]:
        cli_dir = os.path.expanduser("~/.gemini/antigravity-cli")
        if os.path.isdir(cli_dir):
            return True, "✅ Google Antigravity 세션 인증 활성"
        return False, "⚠️ Antigravity 인증 디렉토리 미확인 (`agy auth` 필요)"

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or config.AGY_PATH
        cmd = [
            bin_path,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--print-timeout", config.AGY_PRINT_TIMEOUT,
        ]
        raw_model = model or getattr(config, "QA_MODEL", "gemini-3.1-pro-high") or "gemini-3.1-pro-high"
        # Dynamically resolve using Antigravity /model active catalog (auto-upgrades deprecated 2.5 etc.)
        effective_model = AntigravityModelRegistry.resolve_model(role="coder", preferred=raw_model)

        # Strict Antigravity /model compatibility check:
        # Native Antigravity models (all Gemini 3.x, gpt-oss-120b-medium, claude-sonnet-4-6, claude-opus-4-6-thinking)
        # pass directly to AGY CLI.
        # OpenRouter third-party models (openrouter/*, upstage/*, deepseek/*, qwen/*, etc.)
        # safely fallback to Gemini to prevent CLI execution crashes.
        if AntigravityModelRegistry.is_antigravity_model(effective_model):
            pass
        elif (
            effective_model.lower().startswith("openrouter/")
            or "/" in effective_model
            or any(marker in effective_model.lower() for marker in ("qwen", "deepseek", "llama", "solar", "ling", "minimax"))
        ):
            # [🛡️ Gemini Quota Protection] 비네이티브 모델을 조용히 Gemini로 폴백하지 않는다.
            # Gemini quota를 소모하지 않는 Antigravity 네이티브 모델(gpt-oss)로 대체한다.
            # (정상 경로에서는 상위 AgentRunner/_execute_single_run 이 Hermes/OpenCode로 라우팅한다.)
            logger.warning(
                f"AgyAdapter: External model '{effective_model}' is not an Antigravity native model. "
                f"Falling back to non-Gemini native 'gpt-oss-120b-medium' to protect Gemini quota."
            )
            effective_model = "gpt-oss-120b-medium"
        cmd.extend(["--model", effective_model])
        # [⚙️ 모델별 옵션] `--effort` 는 Gemini 계열 모델만 지원합니다.
        # gpt-oss / claude-* 등에 전달하면 'invalid model selection (--effort is not supported)' 오류가 발생합니다.
        if "gemini" in effective_model.lower():
            cmd.extend(["--effort", getattr(config, "AGY_EFFORT", "high")])
        if workspace:
            cmd.extend(["--add-dir", workspace])
        conv_id = kwargs.get("conversation_id")
        if conv_id:
            cmd.extend(["--conversation", conv_id])
        cmd.extend(["--print", prompt])
        return cmd

    async def execute(
        self,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        timeout: float = 3600.0,
        **kwargs
    ) -> Tuple[int, str, List[str]]:
        """[🛡️ Quota Firewall] 무인/자율 컨텍스트의 Gemini 자동 호출을 차단한 뒤 실행합니다."""
        try:
            from quota_firewall import check_model as _qfw_check
            decision = _qfw_check(model, source="AgyAdapter.execute")
            if not decision.allowed:
                logger.warning(f"[QuotaFirewall] {decision.reason}")
                return -3, "", [decision.reason]
        except Exception as e:
            logger.debug(f"QuotaFirewall check skipped: {e}")
        return await super().execute(
            session=session,
            prompt=prompt,
            on_tool_update=on_tool_update,
            model=model,
            timeout=timeout,
            **kwargs
        )


class HermesAdapter(BaseEngineAdapter):
    engine_id = "hermes"
    name = "Hermes Agent (OpenRouter API)"
    injects_autonomy_directive = True
    icon = "🪽"
    description = "OpenRouter API 연동 Hermes 자율 에이전트 (오픈라우터 = Hermes Agent)"
    env_var = "HERMES_PATH"
    default_paths = [
        "/home/jangsung/.local/bin/hermes",
        "/usr/local/bin/hermes",
        "/home/jangsung/.hermes/bin/hermes",
    ]
    install_command = "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    auth_info = "OPENROUTER_API_KEY 또는 ~/.hermes/config.yaml 연동"
    supported_models = [
        "upstage/solar-pro4",
        "openrouter/upstage/solar-pro4",
        "openrouter/anthropic/claude-3.7-sonnet",
        "openrouter/deepseek/deepseek-chat",
        "openrouter/deepseek/deepseek-r1",
        "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
        "openrouter/openai/gpt-4o",
        "openrouter/openai/o3-mini",
        "anthropic/claude-sonnet-4",
    ]

    def check_auth_status(self) -> Tuple[bool, str]:
        config_path = os.path.expanduser("~/.hermes/config.yaml")
        if os.path.exists(config_path):
            return True, "✅ Hermes Agent 설정 및 인증 정상 (~/.hermes/config.yaml)"
        key = getattr(config, "OPENROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        if key:
            return True, "✅ OPENROUTER_API_KEY 인증 연동 완료"
        return False, "⚠️ Hermes 설정 파일(~/.hermes/config.yaml) 또는 OPENROUTER_API_KEY 미설정"

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or "hermes"
        role = str(kwargs.get("role", "")).lower().strip()
        read_only = bool(kwargs.get("read_only", False))
        mode = str(kwargs.get("mode", "")).lower().strip()
        session = kwargs.get("session")  # ChannelSession for conversation persistence
        is_qa_review = (
            read_only
            or role.startswith("qa")
            or role in ("reviewer", "qa_bot", "auditor", "single_qa2")
            or mode in ("pair_plus", "single_qa2")
            or (model and "solar" in model.lower())
        )

        final_prompt = prompt
        if is_qa_review:
            final_prompt = f"[CRITICAL MANDATE - READONLY QA ENVIRONMENT]\n{prompt}"
        # [🚫 무질문 자율 진행] 비대면 사용자에게 되묻지 않고 자율 완결하도록 최상단에 강제 주입합니다.
        final_prompt = f"{AUTONOMY_NO_QUESTION_DIRECTIVE}\n\n{final_prompt}"

        cmd = [
            bin_path,
            "chat",
            "-q", final_prompt,
            "-Q",
            "--yolo",
            "--in", workspace,
        ]

        # [🔄 CWD 정확 표시] workspace 경로를 프롬프트 앞에 주입하여
        # Hermes가 내부 CWD를 절대 경로로 알 수 있도록 합니다.
        cmd[3] = f"📁 **작업 디렉토리:** `{workspace}`\n{final_prompt}"

        # [🧠 세션 지속성 및 역할별 격리] --oneshot 대신 --continue로 대화 맥락을 유지하되,
        # Coder와 QA의 대화 스레드가 뒤섞이지 않도록 role별로 세션 이름을 엄격히 격리합니다.
        conv_name = None
        if session:
            role_tag = f"-{role}" if role else "-coder"
            conv_name = f"agy-channel-{session.channel_id}{role_tag}"
            if session.conversation_id:
                conv_name = f"{conv_name}-{session.conversation_id[:8]}"

        if conv_name:
            cmd.extend(["--continue", conv_name, "--create-if-missing"])
        else:
            cmd.append("--oneshot")

        if is_qa_review:
            cmd.extend(["-t", "readonly"])
        if model:
            target_model = model
            if "llama-4" in target_model.lower():
                target_model = "meta-llama/llama-3.3-70b-instruct"
            # Hermes Agent CLI requires raw model ID (e.g. upstage/solar-pro4), NOT prefixed with 'openrouter/'
            clean_hermes_model = target_model
            while clean_hermes_model.startswith("openrouter/"):
                clean_hermes_model = clean_hermes_model[len("openrouter/"):]
            cmd.extend(["-m", clean_hermes_model])
        return cmd


class AiderAdapter(HermesAdapter):
    """Legacy alias redirecting all Aider requests permanently to Hermes Autonomous Agent."""
    engine_id = "aider"
    name = "Hermes Autonomous Agent (formerly Aider)"
    icon = "🪽"


class OpenRouterAdapter(HermesAdapter):
    engine_id = "openrouter"
    name = "Hermes Agent (OpenRouter API)"
    icon = "🪽"
    description = "OpenRouter API 연동 Hermes 자율 에이전트 (오픈라우터 = Hermes Agent)"
    env_var = "OPENROUTER_PATH"
    default_paths = [
        "/home/jangsung/.local/bin/hermes",
        "/usr/local/bin/hermes",
        "/home/jangsung/.hermes/bin/hermes",
    ]
    install_command = "export OPENROUTER_API_KEY=... 및 Hermes Agent 연동"
    auth_info = "OPENROUTER_API_KEY 환경변수 인증 (ZDR 강제 적용)"
    supported_models = [
        "upstage/solar-pro4",
        "openrouter/upstage/solar-pro4",
        "upstage/solar-pro-3",
        "openrouter/upstage/solar-pro-3",
        "solar-pro-4",
        "solar",
        "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen",
        "qwen-coder",
        "qwen-2.5-coder-32b",
        "openrouter/deepseek/deepseek-chat",
        "deepseek/deepseek-chat",
        "openrouter/deepseek/deepseek-r1",
        "deepseek/deepseek-r1",
        "deepseek-coder",
        "openrouter/deepseek/deepseek-coder",
        "openrouter/anthropic/claude-3.7-sonnet",
        "anthropic/claude-3.7-sonnet",
        "openrouter/openai/o3-mini",
        "openrouter/openai/gpt-4o",
        "openrouter/meta-llama/llama-3.3-70b-instruct",
        "meta-llama/llama-3.3-70b-instruct",
        "openrouter/mistralai/mistral-large-2411",
        "gemini-3.1-pro-high",
    ]

    def _get_clean_key(self) -> str:
        key = getattr(config, "OPENROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        key = str(key).strip()
        for prefix in ("(삭제해야정상동작)", '"(삭제해야정상동작)', "'(삭제해야정상동작)"):
            if key.startswith(prefix):
                key = key[len(prefix):]
        return key.strip('"\'; ')

    def is_installed(self) -> bool:
        # OpenRouter can operate either via Hermes CLI binary OR via Direct API if API key is present
        return self.get_binary_path() is not None or bool(self._get_clean_key())

    def check_auth_status(self) -> Tuple[bool, str]:
        key = self._get_clean_key()
        if key and len(key) >= 10:
            return True, "✅ OpenRouter API 키 인증 완료 (ZDR 강제 활성화)"
        return False, "⚠️ OPENROUTER_API_KEY 미설정 (.env 에 등록 필요)"

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or "hermes"
        raw_target = model or getattr(config, "QA2_MODEL", "upstage/solar-pro4") or getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
        resolved_target = EngineManager.resolve_model_alias(raw_target) if "EngineManager" in globals() else raw_target
        resolved_target = resolved_target or raw_target
        if not resolved_target.startswith("openrouter/"):
            formatted_model = f"openrouter/{resolved_target}"
        else:
            formatted_model = resolved_target

        if "llama-4" in formatted_model.lower():
            formatted_model = "openrouter/meta-llama/llama-3.3-70b-instruct"

        # OpenRouter / Hermes CLI requires raw model ID (e.g. upstage/solar-pro4), NOT prefixed with 'openrouter/'
        clean_model = formatted_model
        while clean_model.startswith("openrouter/"):
            clean_model = clean_model[len("openrouter/"):]

        if "hermes" in bin_path:
            role = str(kwargs.get("role", "")).lower().strip()
            read_only = bool(kwargs.get("read_only", False))
            mode = str(kwargs.get("mode", "")).lower().strip()
            is_qa_review = (
                read_only
                or role.startswith("qa")
                or role in ("reviewer", "qa_bot", "auditor", "single_qa2")
                or mode in ("pair_plus", "single_qa2")
                or (clean_model and "solar" in clean_model.lower())
            )
            final_prompt = prompt
            if is_qa_review:
                final_prompt = f"[CRITICAL MANDATE - READONLY QA ENVIRONMENT]\n{prompt}"
            # [🚫 무질문 자율 진행] 최상단에 강제 주입
            final_prompt = f"{AUTONOMY_NO_QUESTION_DIRECTIVE}\n\n{final_prompt}"
            cmd = [
                bin_path,
                "chat",
                "-q", final_prompt,
                "--oneshot",
                "-Q",
                "--yolo",
                "--in", workspace,
                "-m", clean_model,
            ]
            if is_qa_review:
                cmd.extend(["-t", "readonly"])
        else:
            cmd = [
                bin_path,
                "--message", f"{AUTONOMY_NO_QUESTION_DIRECTIVE}\n\n{prompt}",
                "--yes-always",
                "--no-auto-commits",
                "--no-analytics",
                "--analytics-disable",
                "--no-add-gitignore-files",
                "--model", clean_model,
                "--openai-api-base", "https://openrouter.ai/api/v1",
            ]

        # OpenRouter Zero Data Retention (ZDR) & Privacy Routing Guard
        clean_key = self._get_clean_key()
        if clean_key:
            os.environ["OPENROUTER_API_KEY"] = clean_key
            os.environ["OPENAI_API_KEY"] = clean_key
        os.environ["OPENROUTER_PROVIDER_DATA_COLLECTION"] = "deny"
        os.environ["OR_DATA_COLLECTION"] = "deny"
        os.environ["OPENROUTER_PREFERENCES"] = json.dumps({"data_collection": "deny", "require_parameters": True})

        return cmd

    async def execute_direct_api(self, prompt: str, model: Optional[str] = None, timeout: float = 600.0) -> Tuple[int, str, List[str]]:
        """Direct fallback execution via OpenRouter Chat Completions API."""
        clean_key = self._get_clean_key()
        if not clean_key:
            logger.warning("OpenRouter API key missing or invalid in execute_direct_api")
            return 1, "", ["OPENROUTER_API_KEY가 설정되지 않았습니다."]

        raw_target = model or getattr(config, "QA2_MODEL", "upstage/solar-pro4")
        resolved_target = EngineManager.resolve_model_alias(raw_target) if "EngineManager" in globals() else raw_target
        resolved_target = resolved_target or raw_target
        while resolved_target.startswith("openrouter/"):
            resolved_target = resolved_target[len("openrouter/"):]

        # OpenRouter endpoint safety: Llama 4 Scout is not available on OpenRouter (HTTP 404). Auto-fallback to Llama 3.3 70B Instruct.
        if "llama-4" in resolved_target.lower():
            resolved_target = "meta-llama/llama-3.3-70b-instruct"
        if "solar" in resolved_target.lower() and not ("solar-pro4" in resolved_target.lower() or "solar-pro-3" in resolved_target.lower()):
            resolved_target = "upstage/solar-pro4"

        headers = {
            "Authorization": f"Bearer {clean_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/google/antigravity",
            "X-Title": "AGY Multi-Agent Platform",
        }
        payload = {
            "model": resolved_target,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "provider": {
                "data_collection": "deny"
            }
        }

        def _call_api():
            import urllib.request
            import json
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                data=json.dumps(payload).encode("utf-8"),
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, _call_api)
            choices = data.get("choices", [])
            if choices and "message" in choices[0]:
                content = choices[0]["message"].get("content", "").strip()
                return 0, content, []
            return 1, "", ["응답 본문에서 choices를 찾을 수 없습니다."]
        except Exception as e:
            err_msg = str(e)
            try:
                import urllib.error
                if isinstance(e, urllib.error.HTTPError):
                    raw_body = e.read().decode("utf-8", errors="replace")
                    err_msg = f"HTTP {e.code}: {raw_body[:200]}"
                    # Auto-heal 400 invalid model ID errors (e.g. openrouter/ prefix leakage or invalid model ID)
                    if e.code == 400 and ("not a valid model id" in raw_body.lower() or "invalid model" in raw_body.lower()):
                        curr_model = payload.get("model", "")
                        clean_fb = curr_model
                        while clean_fb.startswith("openrouter/"):
                            clean_fb = clean_fb[len("openrouter/"):]
                        
                        target_models = []
                        if clean_fb != curr_model:
                            target_models.append(clean_fb)
                        if "solar" in curr_model.lower():
                            target_models.append("upstage/solar-pro4")
                            target_models.append("upstage/solar-pro-3")
                        target_models.append("meta-llama/llama-3.3-70b-instruct")

                        for fb_model in target_models:
                            if fb_model != curr_model:
                                logger.warning(f"OpenRouter 400 invalid model ID '{curr_model}'. Auto-retrying with robust model '{fb_model}'...")
                                payload["model"] = fb_model
                                try:
                                    data = await loop.run_in_executor(None, _call_api)
                                    choices = data.get("choices", [])
                                    if choices and "message" in choices[0]:
                                        content = choices[0]["message"].get("content", "").strip()
                                        return 0, content, []
                                except Exception as fb_err:
                                    logger.error(f"Auto-fallback to {fb_model} failed: {fb_err}")

                    # Auto-heal 404 endpoint not found errors by retrying with verified robust model
                    if e.code == 404 and "No endpoints found" in raw_body and payload.get("model") != "meta-llama/llama-3.3-70b-instruct":
                        logger.warning(f"OpenRouter 404 for {payload.get('model')}. Auto-retrying with meta-llama/llama-3.3-70b-instruct...")
                        payload["model"] = "meta-llama/llama-3.3-70b-instruct"
                        try:
                            data = await loop.run_in_executor(None, _call_api)
                            choices = data.get("choices", [])
                            if choices and "message" in choices[0]:
                                content = choices[0]["message"].get("content", "").strip()
                                return 0, content, []
                        except Exception as fb_err:
                            logger.error(f"Auto-fallback failed after 404: {fb_err}")

                    # Auto-heal 503/429 transient server overload with exponential backoff retry
                    if e.code in (503, 429) or "high traffic" in raw_body.lower() or "no capacity" in raw_body.lower():
                        overload_delays = [15, 30, 60]
                        for ol_attempt, ol_delay in enumerate(overload_delays):
                            logger.warning(
                                f"OpenRouter {e.code} transient overload for '{payload.get('model')}'. "
                                f"Backoff retry {ol_attempt + 1}/{len(overload_delays)} after {ol_delay}s..."
                            )
                            await asyncio.sleep(ol_delay)
                            try:
                                data = await loop.run_in_executor(None, _call_api)
                                choices = data.get("choices", [])
                                if choices and "message" in choices[0]:
                                    content = choices[0]["message"].get("content", "").strip()
                                    logger.info(f"OpenRouter overload recovery succeeded after {ol_delay}s backoff (attempt {ol_attempt + 1}).")
                                    return 0, content, []
                            except Exception as ol_err:
                                ol_err_str = str(ol_err)
                                still_overloaded = any(kw in ol_err_str.lower() for kw in ["503", "429", "high traffic", "no capacity", "overloaded"])
                                if not still_overloaded:
                                    err_msg = ol_err_str
                                    break
                                logger.warning(f"OpenRouter overload retry {ol_attempt + 1} still failing: {ol_err_str[:120]}")
            except Exception:
                pass
            logger.error(f"OpenRouter direct API call failed: {err_msg}")
            return 1, "", [err_msg]

    async def execute(
        self,
        session: Any,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        timeout: float = 3600.0,
        **kwargs
    ) -> Tuple[int, str, List[str]]:
        clean_key = self._get_clean_key()
        if not clean_key:
            return 1, "", ["⚠️ OPENROUTER_API_KEY가 설정되지 않았거나 유효하지 않습니다 (.env 등록 필요)."]

        os.environ["OPENROUTER_API_KEY"] = clean_key
        os.environ["OPENAI_API_KEY"] = clean_key

        # If role is any QA reviewer (qa, qa1..qa10, reviewer), or hermes binary missing, try direct API
        session_role = kwargs.pop("role", None) or getattr(session, "role", "") or ""
        session_mode = kwargs.pop("mode", None) or getattr(session, "mode", "") or ""
        is_qa_solo = session_mode in ("pair_plus", "single_qa2")
        is_qa_review = (
            session_role.startswith("qa")
            or session_role in ("reviewer", "qa_bot", "auditor", "single_qa2")
            or is_qa_solo
            or (model and "solar" in model.lower())
        )
        has_cli = bool(self.get_binary_path())

        # MANDATE: All OpenRouter bots execute via Hermes Agent CLI when binary is available!
        # When role is QA reviewer, Hermes runs in strict READONLY mode (-t readonly) to guarantee ZERO workspace writes.
        if has_cli:
            mode_desc = "READONLY mode (strictly zero workspace writes)" if is_qa_review else "full autonomous mode"
            logger.info(f"OpenRouter executing via Hermes Agent CLI ({mode_desc}, role: {session_role}, mode: {session_mode}).")
            if is_qa_review:
                await safe_tool_notify(on_tool_update, f"🪽 **[Hermes QA] {model or getattr(config, 'QA2_MODEL', 'upstage/solar-pro4')} 읽기 전용 정밀 검증 중...**")
            qa_timeout = min(timeout, float(getattr(config, "EXTENDED_QA_TIMEOUT", 4000.0))) if is_qa_review else timeout
            code, resp, errors = await super().execute(
                session=session,
                prompt=prompt,
                on_tool_update=on_tool_update,
                model=model,
                timeout=qa_timeout,
                role=session_role,
                read_only=is_qa_review,
                mode=session_mode,
                **kwargs
            )
            # If Hermes CLI succeeds or produced output, return immediately
            if code == 0 or resp:
                return code, resp, errors

            # If Hermes CLI failed or timed out, fallback gracefully to Direct API
            logger.warning(f"Hermes Agent CLI execution failed ({errors}). Falling back gracefully to Direct API...")
            await safe_tool_notify(on_tool_update, f"⚠️ **[OpenRouter] Hermes Agent CLI 응답 지연 -> Direct API로 안전 전환 중...**")
            d_code, d_resp, d_errors = await self.execute_direct_api(prompt, model=model, timeout=min(timeout, (float(getattr(config, "EXTENDED_QA_TIMEOUT", 4000.0)) if is_qa_review else 600.0)))
            if d_code == 0 and d_resp:
                return d_code, d_resp, []

            # Backup model fallback within CLI path if primary model failed
            if is_qa_review:
                backup_model = getattr(config, "QA_BACKUP_MODEL", "openrouter/qwen/qwen-2.5-coder-32b-instruct")
                raw_target = model or getattr(config, "QA2_MODEL", "upstage/solar-pro4")
                resolved_raw = (raw_target or "").replace("openrouter/", "")
                clean_backup = (backup_model or "").replace("openrouter/", "")
                if clean_backup and resolved_raw != clean_backup:
                    await safe_tool_notify(
                        on_tool_update,
                        f"⚠️ **[OpenRouter] {raw_target} 응답 지연/실패 -> 고속 백업 모델({backup_model})로 즉시 재시도 중...**"
                    )
                    b_code, b_resp, b_errors = await self.execute_direct_api(prompt, model=backup_model, timeout=min(timeout, (float(getattr(config, "EXTENDED_QA_TIMEOUT", 4000.0)) if is_qa_review else 600.0)))
                    if b_code == 0 and b_resp:
                        return b_code, b_resp, []

            return d_code, d_resp, d_errors or errors

        # Environments without Hermes CLI binary fallback to direct API
        await safe_tool_notify(on_tool_update, f"🌐 **[OpenRouter] {model or 'openai/gpt-6-astra'} API 직접 분석 호출 중...**")
        code, resp, errors = await self.execute_direct_api(prompt, model=model, timeout=min(timeout, (float(getattr(config, "EXTENDED_QA_TIMEOUT", 4000.0)) if is_qa_review else 600.0)))
        if code == 0 and resp:
            return code, resp, errors

        # If primary model failed on QA review, attempt fast backup model on OpenRouter before failing
        backup_model = getattr(config, "QA_BACKUP_MODEL", "openrouter/qwen/qwen-2.5-coder-32b-instruct")
        raw_target = model or getattr(config, "QA2_MODEL", "upstage/solar-pro4")
        resolved_raw = (raw_target or "").replace("openrouter/", "")
        clean_backup = (backup_model or "").replace("openrouter/", "")
        if clean_backup and resolved_raw != clean_backup:
            await safe_tool_notify(
                on_tool_update,
                f"⚠️ **[OpenRouter] {raw_target} 응답 지연/실패 -> 고속 백업 모델({backup_model})로 즉시 재시도 중...**"
            )
            logger.warning(f"OpenRouter primary model {raw_target} failed: {errors}. Retrying with backup model {backup_model}...")
            b_code, b_resp, b_errors = await self.execute_direct_api(prompt, model=backup_model, timeout=min(timeout, (float(getattr(config, "EXTENDED_QA_TIMEOUT", 4000.0)) if is_qa_review else 600.0)))
            if b_code == 0 and b_resp:
                return b_code, b_resp, []

        return 1, "", [f"OpenRouter direct API call failed: {'; '.join(errors)}"]


class CodexAdapter(BaseEngineAdapter):
    engine_id = "codex"
    name = "OpenAI Codex CLI"
    icon = "🧠"
    description = "OpenAI 공식 Codex 코딩 어시스턴트 CLI (JSONL 이벤트 스트림 지원)"
    env_var = "CODEX_PATH"
    default_paths = [
        "/home/jangsung/.npm-global/bin/codex",
        "/usr/local/bin/codex",
        "/usr/bin/codex",
    ]
    install_command = "npm install -g @openai/codex"
    auth_info = "codex login 또는 OPENAI_API_KEY 환경변수"
    supported_models = [
        "o3-mini",
        "o1",
        "gpt-4o",
    ]

    def check_auth_status(self) -> Tuple[bool, str]:
        codex_cfg = os.path.expanduser("~/.codex")
        if os.path.exists(codex_cfg) or os.getenv("OPENAI_API_KEY"):
            return True, "✅ Codex 설정 / OPENAI_API_KEY 감지"
        return False, "⚠️ OpenAI Codex 인증 미확인 (`codex login` 또는 OPENAI_API_KEY 필요)"

    def _resolve_model(self, model: Optional[str]) -> Optional[str]:
        if not model:
            return None
        m_lower = model.lower()
        # Incompatible engine models should not be passed to Codex CLI
        if m_lower.startswith(("gemini", "gpt-oss", "claude", "deepseek", "anthropic")):
            return None
        return model

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or "codex"
        cmd = [
            bin_path,
            "exec",
            "--sandbox", "workspace-write",
            "--skip-git-repo-check",
            "-C", workspace,
        ]
        target_model = self._resolve_model(model)
        if target_model:
            cmd.extend(["-m", target_model])
        cmd.append(prompt)
        return cmd

    async def execute(
        self,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        timeout: float = 3600.0,
        **kwargs
    ) -> Tuple[int, str, List[str]]:
        """Specialized executor parsing Codex CLI JSONL output with active watchdog."""
        bin_path = self.get_binary_path()
        if not bin_path:
            return -1, "", [f"[{self.name}] 바이너리를 찾을 수 없습니다. ({self.install_command})"]

        cmd = [
            bin_path,
            "exec",
            "--sandbox", "workspace-write",
            "--skip-git-repo-check",
            "--json",
            "-C", session.workspace,
        ]
        target_model = self._resolve_model(model)
        if target_model:
            cmd.extend(["-m", target_model])
        cmd.append(prompt)

        await safe_tool_notify(on_tool_update, f"{self.icon} **[OpenAI Codex] 세션 초기화 및 비대화형 실행 중...**")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=session.workspace,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=30 * 1024 * 1024,
            start_new_session=True
        )
        session.current_process = proc

        accumulated_text: List[str] = []
        stderr_lines: List[str] = []
        stderr_fatal = False

        async def read_stderr():
            nonlocal stderr_fatal
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    if len(stderr_lines) > 50:
                        stderr_lines.pop(0)
                    if _is_fatal_engine_error(decoded) or _is_http_400_error(decoded):
                        stderr_fatal = True

        stderr_task = asyncio.create_task(read_stderr())
        start_time = time.time()

        try:
            while True:
                if time.time() - start_time > timeout:
                    stderr_lines.append(f"⚠️ 실행 제한 시간({int(timeout)}초) 초과")
                    # Kill process tree to prevent deadlock on subsequent proc.wait()
                    await _terminate_process_tree(proc)
                    break

                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                except asyncio.TimeoutError:
                    if stderr_fatal:
                        stderr_lines.append("⛔ stderr 모델/라우터 오류 감지 → 즉시 회수")
                        await _terminate_process_tree(proc)
                        break
                    if proc.returncode is not None:
                        break
                    continue

                if not line:
                    break
                # [🛡️ StallGuard] 실제 stdout = 진짜 진행 → 채널 하트비트 갱신
                try:
                    session.touch("engine_stdout")
                except Exception:
                    pass
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
                # [🛡️ 400 Fast-Fail] 회복 불가한 모델/라우터 오류는 즉시 회수.
                if _is_fatal_engine_error(decoded):
                    stderr_lines.append(f"⛔ Codex 치명적 모델/라우터 오류 감지 → 즉시 회수: {decoded[:200]}")
                    logger.error(f"Codex fatal model/router error detected: {decoded[:200]}")
                    await _terminate_process_tree(proc)
                    break
                try:
                    event = json.loads(decoded)
                    etype = event.get("type") or event.get("event")
                    if etype == "item.completed":
                        item = event.get("item") or {}
                        itype = item.get("type")
                        if itype in ("agent_message", "message"):
                            text = item.get("text") or item.get("content") or ""
                            if text:
                                accumulated_text.append(str(text))
                        elif itype == "command_execution":
                            cmd_str = item.get("command", "")
                            if cmd_str:
                                await safe_tool_notify(on_tool_update, f"🧠 **[Codex 명령 실행 완료]** `{cmd_str[:50]}`")
                    elif etype in ("item.created", "item.updated"):
                        item = event.get("item") or {}
                        if item.get("type") == "command_execution":
                            cmd_str = item.get("command", "")
                            if cmd_str:
                                await safe_tool_notify(on_tool_update, f"🧠 **[Codex 명령 실행 중]** `{cmd_str[:50]}`")
                    elif etype in ("message", "content", "response"):
                        text_val = event.get("content") or event.get("text") or ""
                        if text_val:
                            accumulated_text.append(str(text_val))
                    elif etype == "tool_call":
                        tname = event.get("name", "Tool")
                        await safe_tool_notify(on_tool_update, f"🧠 **[Codex 도구 실행]** `{tname}`")
                    elif etype == "error":
                        err_obj = event.get("error")
                        err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(event.get("message") or err_obj or "")
                        if err_msg:
                            stderr_lines.append(f"Codex Error: {err_msg}")
                except json.JSONDecodeError:
                    if not decoded.startswith("Reading additional input from stdin"):
                        accumulated_text.append(decoded)

            await asyncio.wait_for(proc.wait(), timeout=15.0)
            await asyncio.wait_for(stderr_task, timeout=5.0)
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            raise
        except asyncio.TimeoutError:
            await _terminate_process_tree(proc)
        finally:
            await _terminate_process_tree(proc)
            # [🛡️ Quota Leak Prevention] Kill orphaned AGY Language Servers
            try:
                from session_manager import kill_agy_language_servers
                kill_agy_language_servers()
            except Exception:
                pass

        # Filter out harmless stdin notification if other messages exist
        clean_errors = [l for l in stderr_lines if not l.startswith("Reading additional input from stdin")]
        return proc.returncode, "\n".join(accumulated_text).strip(), (clean_errors if clean_errors else stderr_lines)


class ClaudeAdapter(BaseEngineAdapter):
    engine_id = "claude"
    name = "Claude Code CLI"
    icon = "🟣"
    description = "Anthropic 공식 Claude Code CLI (고성능 추론 및 터미널 자율 리팩토링)"
    env_var = "CLAUDE_PATH"
    default_paths = [
        "/home/jangsung/.npm-global/bin/claude",
        "/home/jangsung/.local/bin/claude",
        "/usr/local/bin/claude",
        "/usr/bin/claude",
    ]
    install_command = "npm install -g @anthropic-ai/claude-code"
    auth_info = "claude login 또는 ANTHROPIC_API_KEY"
    supported_models = [
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ]

    def check_auth_status(self) -> Tuple[bool, str]:
        if os.getenv("ANTHROPIC_API_KEY") or os.path.exists(os.path.expanduser("~/.claude.json")):
            return True, "✅ Claude 인증 정보 감지"
        return False, "⚠️ `claude login` 또는 ANTHROPIC_API_KEY 필요"

    def _resolve_model(self, model: Optional[str]) -> Optional[str]:
        if not model:
            return None
        m_lower = model.lower()
        if not m_lower.startswith("claude"):
            return None
        return model

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or "claude"
        cmd = [
            bin_path,
            "-p", prompt,
            "--dangerously-skip-permissions",
        ]
        target_model = self._resolve_model(model)
        if target_model:
            cmd.extend(["--model", target_model])
        return cmd


def _get_engine_conversation_store(session) -> Dict[str, str]:
    """
    [🔌 엔진별 대화 저장소 접근자]
    ChannelSession.engine_conversations(dict)를 반환하며, 없으면 즉석에서 생성합니다.
    key 형식: "<engine>:<conversation_id_attr>" (예: "opencode:conversation_id")
    """
    store = getattr(session, "engine_conversations", None)
    if not isinstance(store, dict):
        store = {}
        try:
            session.engine_conversations = store
        except Exception:
            pass
    return store


def _opencode_event_hint(etype: Optional[str], part: dict) -> str:
    """
    [🖥️ 사람이 읽는 진행 표기] OpenCode JSON 이벤트를 채널에 보여줄 문구로 변환한다.

    QA 처럼 모델이 아직 `text` 파트를 내지 않고 도구만 연속 실행하는 구간에서는
    원시 JSON(`{"type":"step_start"...`)이 그대로 노출되어 "동작하는지 알 수 없다"는
    혼동을 준다. Coder/QA 가 같은 OpenCode 어댑터를 쓰므로 여기서 단계/도구 설명을
    반환하면 두 봇의 진행 표기가 동일해진다. (설명 불가한 이벤트는 빈 문자열)
    """
    if etype == "tool_use":
        state = part.get("state") or {}
        tool_name = part.get("tool") or state.get("title") or "tool"
        title = state.get("title")
        if state.get("status") == "completed":
            return f"✅ **도구 완료**: `{str(title or tool_name)[:60]}`"
        hint = f"🔧 **도구 실행 중**: `{str(tool_name)[:60]}`"
        if title and str(title) != str(tool_name):
            hint += f" — {str(title)[:80]}"
        return hint
    if etype == "step_start":
        return "🧠 **다음 단계 추론 시작 — 모델 사고 중...**"
    if etype == "step_finish":
        return "✅ **단계 완료 — 다음 단계로 진행 중...**"
    if etype == "error":
        return "⛔ **오류 감지 — 복구/보고 처리 중...**"
    return ""


class OpenCodeAdapter(BaseEngineAdapter):
    engine_id = "opencode"
    name = "OpenCode CLI"
    icon = "⚡"
    description = "오픈소스 자율 코딩 에이전트 CLI (멀티 프로바이더 라우팅 / 세션 지속 / MCP 지원)"
    injects_autonomy_directive = True
    env_var = "OPENCODE_PATH"
    default_paths = [
        "/home/jangsung/.opencode/bin/opencode",
        "~/.opencode/bin/opencode",
        "/home/jangsung/.local/bin/opencode",
        "/usr/local/bin/opencode",
        "/usr/bin/opencode",
    ]
    install_command = "curl -fsSL https://opencode.ai/install | bash"
    auth_info = "opencode auth login (프로바이더 인증) 또는 OPENCODE_API_KEY"
    # 정적 폴백 카탈로그 (실시간 조회 실패 시 사용되는 최신 전체 모델 카탈로그)
    _STATIC_MODELS = [
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/deepseek-v4-pro",
        "opencode-go/glm-5.3-flash",
        "opencode-go/glm-5.3",
        "opencode-go/mimo-v2.6-flash",
        "opencode-go/mimo-v2.6-pro",
        "opencode-go/qwen3.8-flash",
        "opencode-go/qwen3.8-max",
        "opencode-go/gpt-6-luna",
        "opencode-go/gpt-5.6-luna",
        "opencode-go/kimi-k3",
        "opencode-go/kimi-k2.7-code",
        "opencode-go/grok-4.7",
        "opencode-go/minimax-m3",
        "opencode-go/qwen3.7-max",
        "opencode-go/qwen3.7-plus",
        "opencode-go/qwen3.6-plus",
        "opencode-go/longcat-2.0",
        "opencode-go/space-bunny-free",
        "opencode-go/hy4-preview",
        "opencode-go/muse-spark-1.3-contributor",
        "opencode-go/deepseek-v4-flash",
        "opencode-go/deepseek-v4-flash-vision-exp",
        "opencode-go/omen-alpha",
        "opencode-go/mimo-v2.5-pro",
        "opencode-go/mimo-v2.5",
        "opencode-go/minimax-m2.7",
        "opencode-go/minimax-m2.5",
        "opencode-go/kimi-k2.6",
        "opencode-go/glm-5.2",
        "opencode-go/glm-5.1",
        "opencode-go/grok-4.6",
        "opencode-go/hy3",
        "opencode-go/muse-spark-1.2-contributor",
        "opencode-go/deepseek-flash",
    ]
    _models_cache: Tuple[float, List[str]] = (0.0, [])
    _MODELS_CACHE_TTL = 600.0

    @property
    def supported_models(self) -> List[str]:
        """
        [🔄 자동화] OpenCode 실시간 모델 카탈로그를 반환합니다.

        `live_model_catalog` 가 'opencode models' CLI 와 Zen Go API 결과를
        **합집합으로 병합**하여 관리하므로(무료 `opencode/*` 티어 누락 방지),
        여기서는 그 단일 소스를 그대로 사용합니다.
        실패/미로그인 시에는 curated 정적 폴백을 반환합니다.
        """
        try:
            from live_model_catalog import LiveModelCatalog
            live = LiveModelCatalog.get_models("opencode")
            if live:
                return [mid for mid, _ in live]
        except Exception as e:
            logger.debug(f"OpenCodeAdapter: live catalog unavailable ({e})")
        return list(self._STATIC_MODELS)

    def _fetch_models_legacy(self) -> List[str]:
        """
        [Deprecated] 자동화 도입 이전의 단일 소스(Zen API 우선) 조회 로직.
        API 응답이 CLI 결과를 가려서 free 티어가 누락되던 원인이 남아 있어
        더 이상 `supported_models` 에서 사용하지 않습니다. 참고용으로 보존.
        """
        now = time.time()
        cached_at, cached = OpenCodeAdapter._models_cache
        if cached and (now - cached_at) < self._MODELS_CACHE_TTL:
            return list(cached)

        # 1. OpenCode Go API 실시간 모델 카탈로그 조회 (가장 정확한 최신 카탈로그)
        try:
            from telemetry_status_manager import _get_opencode_go_api_key
            key = _get_opencode_go_api_key()
            if key:
                import urllib.request
                req = urllib.request.Request(
                    "https://opencode.ai/zen/go/v1/models",
                    headers={"Authorization": f"Bearer {key}", "User-Agent": "opencode/2.0.12"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        payload = json.loads(resp.read().decode("utf-8"))
                        api_models = []
                        for item in payload.get("data", []):
                            mid = item.get("id")
                            if mid:
                                api_models.append(f"opencode-go/{mid}")
                        if api_models:
                            order_map = {m: idx for idx, m in enumerate(self._STATIC_MODELS)}
                            api_models.sort(key=lambda m: (order_map.get(m, 999), m))
                            OpenCodeAdapter._models_cache = (now, api_models)
                            return list(api_models)
        except Exception as e:
            logger.debug(f"OpenCodeAdapter: OpenCode Go API 실시간 조회 실패: {e}")

        # 2. CLI `opencode models` 조회 시도
        bin_path = self.get_binary_path()
        if bin_path:
            try:
                out = subprocess.check_output(
                    [bin_path, "models"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
                models = [ln.strip() for ln in out.splitlines() if ln.strip() and "/" in ln]
                if models:
                    OpenCodeAdapter._models_cache = (now, models)
                    return list(models)
            except Exception as e:
                logger.warning(f"OpenCodeAdapter: 'opencode models' 카탈로그 조회 실패: {e}")

        # 3. 정적 폴백 목록 반환
        return list(self._STATIC_MODELS)

    def check_auth_status(self) -> Tuple[bool, str]:
        data_dir = os.path.expanduser("~/.local/share/opencode")
        if os.path.isdir(data_dir) or os.getenv("OPENCODE_API_KEY"):
            return True, "✅ OpenCode 인증/세션 데이터 감지 (`opencode auth login`)"
        return False, "⚠️ OpenCode 인증 미확인 (`opencode auth login` 필요)"

    def _resolve_model(self, model: Optional[str]) -> Optional[str]:
        """OpenCode CLI 는 'provider/model' 형식만 허용하므로 타 엔진 모델은 무시합니다."""
        if not model:
            return None
        clean = str(model).strip()
        if "/" not in clean:
            return None
        return clean

    # [🔒 채널 격리 샌드박스] opencode의 permission.external_directory 를
    # OPENCODE_CONFIG_CONTENT(최고 우선순위 인라인 설정)로 주입합니다.
    # 이 설정은 opencode가 시작된 작업 디렉토리(cwd) 밖 경로의 접근을 기본 차단합니다.
    # (실제 정책은 _build_env() 가 "상위 deny / 하위 링크 대상 allow" 로 동적 구성합니다.
    #  아래 상수는 workspace 정보가 없을 때의 보수적 기본(전체 차단) 폴백입니다.)
    SANDBOX_CONFIG_CONTENT = json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "external_directory": "deny",
        },
    })

    # [🔒 채널 격리 경계 정책]
    #  • 상위/형제 디렉토리(워크스페이스 밖)  → 접근 금지(deny)
    #  • 워크스페이스 및 그 '하위'에 있는 심볼릭 링크가 가리키는 실제 대상 → 접근 허용(allow)
    #    즉, "상위폴더는 막고 하위폴더(링크 대상 포함)는 열어주는" 정책입니다.
    MAX_LINKED_ALLOWED_ROOTS = 64

    # 심볼릭 링크 탐색 시 통째로 건너뛸 대용량/무관 디렉토리 (탐색 비용 방지)
    _SCAN_SKIP_DIRS = {
        ".git", "node_modules", ".venv", "venv", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
        "dist", "build", ".next", "target",
    }

    @classmethod
    def _collect_linked_allowed_roots(cls, workspace: str) -> List[str]:
        """
        워크스페이스 '하위'에 위치한 심볼릭 링크가 가리키는 실제 경로를 수집합니다.

        정책: 상위폴더(조상)와 홈 루트는 절대 허용하지 않고, 워크스페이스 하위에서
        링크로 연결된 실제 대상(하위폴더/파일)만 허용 목록에 추가합니다.
        이미 워크스페이스 내부에 있는 대상은 별도 허용이 필요 없으므로 제외합니다.
        """
        ws = os.path.abspath(os.path.expanduser(workspace or ""))
        if not os.path.isdir(ws):
            return []
        try:
            ws_real = os.path.realpath(ws)
            home_real = os.path.realpath(os.path.expanduser("~"))
        except OSError:
            return []

        allowed: List[str] = []
        seen_dirs: set = set()
        stack: List[str] = [ws]
        budget = 8192
        while stack and budget > 0:
            current = stack.pop()
            try:
                current_real = os.path.realpath(current)
            except OSError:
                continue
            if current_real in seen_dirs:
                continue
            seen_dirs.add(current_real)
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                budget -= 1
                if budget <= 0:
                    break
                try:
                    is_link = entry.is_symlink()
                except OSError:
                    continue
                if is_link:
                    try:
                        target = os.path.realpath(entry.path)
                    except OSError:
                        continue
                    if not target or target == os.sep or target == home_real:
                        continue
                    # 대상 경로가 호스트에 실제로 존재해야 함 (깨진 심볼릭 링크 배제)
                    if not os.path.exists(target):
                        continue
                    # 상위(조상) 디렉토리 및 워크스페이스 자신은 절대 허용 금지
                    if target == ws_real or ws_real.startswith(target.rstrip(os.sep) + os.sep):
                        continue
                    # 이미 워크스페이스 내부면 별도 허용 불필요
                    if target.startswith(ws_real.rstrip(os.sep) + os.sep):
                        continue
                    if target not in allowed:
                        if len(allowed) >= cls.MAX_LINKED_ALLOWED_ROOTS:
                            continue
                        allowed.append(target)
                    if os.path.isdir(target):
                        stack.append(target)
                else:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if entry.name in cls._SCAN_SKIP_DIRS or entry.name.startswith(".git"):
                        continue
                    stack.append(entry.path)
        return allowed

    @staticmethod
    def _build_permission_patterns(allowed_roots: List[str]) -> Dict[str, str]:
        """
        external_directory 허용/차단 패턴을 구성합니다.
        OpenCode 권한 규칙은 '마지막으로 일치한 규칙'이 승리하므로,
        전체 차단(`*`)을 먼저 두고 허용 대상(하위 경로 포함)을 뒤에 둡니다.
        """
        patterns: Dict[str, str] = {"*": "deny"}
        for root in allowed_roots:
            clean_root = root.rstrip("/") or "/"
            if os.path.isdir(root):
                patterns[f"{clean_root}/*"] = "allow"
                patterns[clean_root] = "allow"
            else:
                patterns[clean_root] = "allow"
        return patterns

    def _build_env(self, workspace: str = "") -> Dict[str, str]:
        """opencode 서브프로세스용 환경변수(채널 격리 샌드박스 포함)를 구성합니다."""
        env = os.environ.copy()
        # 사용자가 OPENCODE_SANDBOX=0 으로 명시적으로 끄지 않는 한 격리를 강제합니다.
        if str(os.getenv("OPENCODE_SANDBOX", "1")).lower() in ("0", "false", "off", "no"):
            return env
        merged: Dict[str, Any] = {}
        existing = env.get("OPENCODE_CONFIG_CONTENT")
        if existing:
            try:
                parsed = json.loads(existing)
                if isinstance(parsed, dict):
                    merged = parsed
            except Exception:
                merged = {}
        perm = dict(merged.get("permission") or {})
        # [🔒 경계 재정의] 무조건 deny → "상위는 deny, 하위(링크 대상 포함)는 allow"
        allowed_roots = self._collect_linked_allowed_roots(workspace)
        perm["external_directory"] = self._build_permission_patterns(allowed_roots)
        merged["permission"] = perm
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(merged)
        return env

    def _sandbox_enabled(self) -> bool:
        """bwrap 기반 파일시스템 샌드박스 사용 여부 (OPENCODE_BWRAP=0 으로 비활성화)."""
        if str(os.getenv("OPENCODE_BWRAP", "1")).lower() in ("0", "false", "off", "no"):
            return False
        return shutil.which("bwrap") is not None

    def _wrap_sandbox(self, cmd: List[str], workspace: str) -> List[str]:
        """
        [🔒 채널 격리] bubblewrap(bwrap)로 파일시스템을 격리합니다.
        - 루트 전체(`/`)는 읽기 전용으로 바인딩 → 다른 프로젝트 디렉토리 수정 원천 차단
        - 채널 워크스페이스 + opencode 자체 상태 디렉토리만 쓰기 허용
        - 워크스페이스 '하위'의 심볼릭 링크가 가리키는 실제 대상도 쓰기 허용
          (상위폴더/형제 디렉토리는 계속 읽기 전용 = 상위 deny, 하위 allow)
        - --standalone(전용 서버)과 함께 써야 도구 실행(셸 포함)이 샌드박스 안에서 이뤄집니다.
        bwrap 미설치 시 원본 명령을 그대로 반환합니다(권한 설정 폴백).
        """
        if not self._sandbox_enabled():
            return cmd
        bwrap = shutil.which("bwrap")
        ws = os.path.abspath(os.path.expanduser(workspace or ""))
        if not bwrap or not os.path.isdir(ws):
            return cmd

        # opencode 자체가 쓰기를 필요로 하는 상태 디렉토리만 추가로 허용
        writable_dirs: List[str] = [ws]
        for d in (
            "~/.local/share/opencode",
            "~/.local/state/opencode",
            "~/.config/opencode",
            "~/.cache/opencode",
        ):
            p = os.path.abspath(os.path.expanduser(d))
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                continue
            if os.path.isdir(p) and p not in writable_dirs:
                writable_dirs.append(p)

        # [🔌 MCP 런타임 허용] stdio MCP(npx 등) 사용 시 필요한 경로를 OPENCODE_MCP_DIRS 로 추가 허용
        #   예: OPENCODE_MCP_DIRS=~/.npm:~/.cache/npm
        #   (http 원격 MCP 는 localhost 네트워크만 필요하므로 설정 불필요)
        for d in (os.getenv("OPENCODE_MCP_DIRS", "") or "").split(":"):
            d = d.strip()
            if not d:
                continue
            p = os.path.abspath(os.path.expanduser(d))
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                continue
            if os.path.isdir(p) and p not in writable_dirs:
                writable_dirs.append(p)

        # [🔗 하위 심볼릭 링크 허용] 워크스페이스 '하위'의 링크가 가리키는 실제 대상도 쓰기 허용
        #   (상위폴더/형제 디렉토리는 계속 읽기 전용으로 차단 → "상위 deny, 하위 allow")
        for d in self._collect_linked_allowed_roots(ws):
            if d not in writable_dirs:
                writable_dirs.append(d)

        wrapped: List[str] = [
            bwrap,
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-pid",
            "--die-with-parent",
        ]
        # [🚀 KVM / 가속 하드웨어 바인딩] 호스트의 KVM/DRI 장치가 있으면 샌드박스 내부로 노출
        # (기본 --dev /dev는 최소 가상 dev만 생성하므로 KVM 가속 에뮬레이터 구동을 위해 필수 바인딩)
        for dev_node in ("/dev/kvm", "/dev/dri"):
            if os.path.exists(dev_node):
                wrapped.extend(["--dev-bind", dev_node, dev_node])
        # /tmp 는 임시 tmpfs 로 격리 (워크스페이스가 /tmp 하위여도 별도 바인딩으로 보존)
        wrapped.extend(["--tmpfs", "/tmp"])
        for d in writable_dirs:
            if not os.path.exists(d):
                continue
            wrapped.extend(["--bind", d, d])
        wrapped.extend(["--chdir", ws, "--"])
        wrapped.extend(cmd)
        return wrapped

    def build_command(
        self,
        prompt: str,
        workspace: str,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> List[str]:
        bin_path = self.get_binary_path() or "opencode"
        # [📁 CWD 명시] 에이전트가 자신의 절대 작업 경로와 격리 경계를 정확히 인지하도록 주입합니다.
        # [🚫 무질문 자율 진행] 되묻기/승인 요청으로 턴이 끊겨 비대면(Discord) 사용자가 답할 수 없는
        #   문제를 원천 차단하기 위해, 모든 opencode 세션 프롬프트 최상단에 자율 진행 헌법을 강제 주입합니다.
        scoped_prompt = (
            f"{AUTONOMY_NO_QUESTION_DIRECTIVE}\n\n"
            f"📁 **작업 디렉토리(절대경로):** `{workspace}`\n"
            f"- 위 경로가 당신의 **유일한 작업 공간(워크스페이스)**이며, 이 디렉토리 밖의 경로는 접근이 차단됩니다.\n"
            f"- \"현재 디렉토리\"를 물으면 위 절대경로를 그대로 답하세요.\n\n{prompt}"
        )
        cmd = [
            bin_path,
            "run", scoped_prompt,
            "--format", "json",
            "--auto",
            "--standalone",
        ]
        target_model = self._resolve_model(model)
        if target_model:
            cmd.extend(["--model", target_model])
        if session_id:
            cmd.extend(["--session", session_id])
        return cmd

    async def execute(
        self,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        timeout: float = 3600.0,
        **kwargs
    ) -> Tuple[int, str, List[str]]:
        """
        [🧠 AGY 패턴 준수] OpenCode CLI 를 JSON 이벤트 스트림으로 실행하고,
        반환된 sessionID 를 엔진별 저장소(engine_conversations)에 보관하여
        다음 턴에서 `--session <id>` 로 대화를 이어갑니다.
        """
        bin_path = self.get_binary_path()
        if not bin_path:
            return -1, "", [f"[{self.name}] 바이너리를 찾을 수 없습니다. ({self.install_command})"]

        conversation_id_attr = kwargs.get("conversation_id_attr") or "conversation_id"
        store = _get_engine_conversation_store(session)
        store_key = f"opencode:{conversation_id_attr}"
        session_id = store.get(store_key)

        async def _run_once(use_session: bool) -> Tuple[int, str, List[str], Optional[str], bool]:
            cmd = self.build_command(
                prompt,
                session.workspace,
                model=model,
                session_id=session_id if use_session else None,
            )
            cmd = self._wrap_sandbox(cmd, session.workspace)
            logger.info(f"Executing {self.name} command: {' '.join(cmd[:6])}...")
            await safe_tool_notify(on_tool_update, f"{self.icon} **[{self.name}] 작업 시작 중...**")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=session.workspace,
                env=self._build_env(session.workspace),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=30 * 1024 * 1024,
                start_new_session=True,
            )
            session.current_process = proc

            text_parts: List[str] = []
            stderr_lines: List[str] = []
            captured_sid: Optional[str] = None
            session_not_found = False
            fatal_error: Optional[str] = None
            stderr_fatal = False

            async def read_stderr():
                nonlocal stderr_fatal
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        stderr_lines.append(decoded)
                        if len(stderr_lines) > 40:
                            stderr_lines.pop(0)
                        if _is_fatal_engine_error(decoded) or _is_http_400_error(decoded):
                            stderr_fatal = True

            stderr_task = asyncio.create_task(read_stderr())
            start_time = time.time()
            last_activity_time = time.time()
            last_update_time = time.time()
            # [🖥️ 사람이 읽는 진행 표기] 모델이 아직 text 를 내지 않은 구간(예: QA 가
            # 도구만 연속 실행)에서 원시 JSON(`{"type":"step_start"...`) 대신 단계/도구
            # 설명을 보여준다. Coder/QA 공용 OpenCode 어댑터라 두 봇 표기가 동일해진다.
            last_event_hint = ""

            # [🛡️ 적응형 무응답 타임아웃] config.WATCHDOG_SILENCE_TIMEOUT 과 통합
            opencode_idle_limit = max(900.0, float(getattr(config, "WATCHDOG_SILENCE_TIMEOUT", 1800)))

            try:
                while True:
                    now = time.time()
                    if now - start_time > timeout:
                        stderr_lines.append(f"⚠️ 실행 제한 시간({int(timeout)}초) 초과로 안전 종료되었습니다.")
                        # [🛡️ 3603초 프리징 차단] 죽이지 않고 break 하면 아래 proc.wait() 가 무한 대기한다.
                        await _terminate_process_tree(proc)
                        break
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                    except asyncio.TimeoutError:
                        idle_sec = now - last_activity_time
                        # [🛡️ 400 Fast-Fail] stderr 로 라우터/모델 오류가 확인되면
                        # 무응답 30분을 기다리지 않고 즉시 회수한다.
                        if stderr_fatal:
                            fatal_error = "stderr fatal model/router error"
                            stderr_lines.append("⛔ stderr 모델/라우터 오류 감지 → 즉시 회수")
                            await _terminate_process_tree(proc)
                            break
                        if idle_sec > opencode_idle_limit:
                            stderr_lines.append(f"⚠️ 무응답 상태 지속({int(idle_sec)}초)으로 안전 종료되었습니다.")
                            # [🛡️ 프리징 차단] 프로세스 트리 + 유한 wait 로 자원 회수.
                            await _terminate_process_tree(proc)
                            break
                        if proc.returncode is not None:
                            break
                        continue
                    if not line:
                        break

                    last_activity_time = time.time()
                    # [🛡️ StallGuard] 실제 stdout = 진짜 진행 → 채널 하트비트 갱신
                    # (비-AGY 엔진 경로에 이 touch 가 없어 900초 강제회수가 동작하지 않았다.)
                    try:
                        session.touch("engine_stdout")
                    except Exception:
                        pass
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if not decoded:
                        continue
                    try:
                        event = json.loads(decoded)
                    except json.JSONDecodeError:
                        text_parts.append(decoded)
                        if _is_fatal_engine_error(decoded):
                            fatal_error = decoded
                            await _terminate_process_tree(proc)
                            break
                        continue

                    etype = event.get("type")
                    part = event.get("part") or {}
                    sid = event.get("sessionID") or part.get("sessionID")
                    if sid:
                        captured_sid = sid

                    if etype == "text":
                        txt = part.get("text")
                        if txt:
                            text_parts.append(str(txt))
                            # 실제 발화 텍스트가 오면 그걸 우선 표기(힌트 해제).
                            last_event_hint = ""
                    else:
                        hint = _opencode_event_hint(etype, part)
                        if hint:
                            last_event_hint = hint
                        if etype == "tool_use":
                            state = part.get("state") or {}
                            if state.get("status") == "completed":
                                title = state.get("title") or part.get("tool") or "tool"
                                await safe_tool_notify(
                                    on_tool_update,
                                    f"{self.icon} **[OpenCode 도구 완료]** `{str(title)[:60]}`",
                                )
                        elif etype == "error":
                            err_obj = event.get("error") or {}
                            err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                            if err_msg:
                                stderr_lines.append(f"OpenCode Error: {err_msg}")
                                if "session not found" in str(err_msg).lower():
                                    session_not_found = True
                                # [🛡️ 400 라우터 오류 Fast-Fail] 잘못된 모델 ID/라우터 400 은
                                # 기다려도 회복되지 않고 프로세스만 멈춘다 → 즉시 회수 후 반환.
                                if _is_fatal_engine_error(err_msg) or _is_http_400_error(err_msg):
                                    fatal_error = str(err_msg)
                                    logger.error(
                                        f"OpenCode fatal model/router error detected → aborting immediately: {str(err_msg)[:200]}"
                                    )
                                    stderr_lines.append(
                                        f"⛔ OpenCode 치명적 모델/라우터 오류(HTTP 400 계열) 감지 → 즉시 회수: {str(err_msg)[:200]}"
                                    )
                                    await _terminate_process_tree(proc)
                                    break

                    if time.time() - last_update_time >= 4.0:
                        last_update_time = time.time()
                        elapsed = int(time.time() - start_time)
                        if last_event_hint:
                            body = last_event_hint
                        else:
                            body = "`" + (text_parts[-1][:60] if text_parts else decoded[:60]).replace("`", "") + "`"
                        await safe_tool_notify(
                            on_tool_update,
                            f"{self.icon} **[{self.name} 실행 중 ({elapsed}s)]**\n{body}",
                        )

                await asyncio.wait_for(proc.wait(), timeout=15.0)
                await asyncio.wait_for(stderr_task, timeout=5.0)
            except asyncio.CancelledError:
                await _terminate_process_tree(proc)
                raise
            except asyncio.TimeoutError:
                # [🛡️ 프리징 차단] proc.wait()/stderr_task bounded wait 초과 → 무한 대기 금지.
                await _terminate_process_tree(proc)
            finally:
                await _terminate_process_tree(proc)

            if fatal_error:
                # [🎯 원인별 정확한 처방] 모든 400 을 "모델 ID 오류"로 뭉뚱그리면 사용자가
                # 할 수 있는 조치가 없어진다. 실측된 두 원인을 구분해 안내한다.
                if _is_privacy_training_error(str(fatal_error)):
                    stderr_lines.append(
                        "💡 [OpenCode Zen 프라이버시 설정] 이 모델은 **요청 데이터로 학습하는 유료 엔드포인트**입니다. "
                        "opencode.ai 워크스페이스 → Privacy settings 에서 "
                        "'Allow paid endpoints that train on request data' 를 허용하거나, "
                        "학습을 사용하지 않는 모델(예: `opencode-go/deepseek-v4.1-flash`)로 교체하세요."
                    )
                elif _is_recoverable_session_error(str(fatal_error)):
                    stderr_lines.append(
                        "💡 누적 대화 컨텍스트가 모델 한도를 넘었을 수 있습니다. "
                        "`!clear` 로 세션을 초기화하면 즉시 복구됩니다. "
                        "(세션 ID: `opencode:` 저장소를 비우면 다음 턴에 새 세션으로 시작합니다)"
                    )
                else:
                    stderr_lines.append(
                        "💡 모델 ID/라우터 설정을 점검하세요: `!engine` 으로 엔진을, `opencode models` 로 유효 모델을 확인할 수 있습니다."
                    )
                return -2, "\n".join(text_parts).strip(), stderr_lines, captured_sid, session_not_found

            return proc.returncode, "\n".join(text_parts).strip(), stderr_lines, captured_sid, session_not_found

        code, text, errors, captured_sid, session_not_found = await _run_once(use_session=bool(session_id))

        # [🛡️ Self-Healing] 저장된 세션이 현재 프로젝트에 존재하지 않으면 새 세션으로 1회 재시도
        if session_id and session_not_found:
            logger.warning(f"OpenCode 세션 '{session_id}' 을 찾을 수 없어 새 세션으로 재시작합니다.")
            store.pop(store_key, None)
            code, text, errors, captured_sid, _ = await _run_once(use_session=False)

        # [🛡️ 400 Self-Healing] 저장된 세션의 누적 컨텍스트(과대/손상)로 인한 provider 400 은
        # 같은 세션을 계속 재사용하면 매 턴 100% 재발한다(실측: 동일 ses_* 에서 반복 400).
        # '학습 데이터 허용' 프라이버시 오류는 재시도로 회복되지 않으므로 제외하고,
        # 회복 가능한 신호일 때만 세션을 비우고 새 세션으로 딱 1회 재시도한다.
        if (
            session_id
            and code == -2
            and not session_not_found
            and _is_recoverable_session_error("\n".join(errors or []))
            and not _is_privacy_training_error("\n".join(errors or []))
        ):
            logger.warning(
                "OpenCode provider 400 on persisted session '%s' (stale/oversized context) "
                "→ retrying once with a fresh session.",
                session_id,
            )
            await safe_tool_notify(
                on_tool_update,
                f"{self.icon} **[OpenCode 세션 자가치유]** 누적 컨텍스트로 인한 라우터 오류 감지 → "
                "새 세션으로 1회 재시도합니다.",
            )
            store.pop(store_key, None)
            code, text, errors, captured_sid, _ = await _run_once(use_session=False)

        if captured_sid:
            store[store_key] = captured_sid

        return code, text, errors


class CopilotAdapter(BaseEngineAdapter):
    engine_id = "copilot"
    name = "GitHub Copilot CLI"
    icon = "🐙"
    description = "GitHub 공식 Copilot CLI (터미널 명령어 자동화 및 코드 보조)"
    env_var = "COPILOT_PATH"
    default_paths = [
        "/home/jangsung/.npm-global/bin/copilot",
        "/home/jangsung/.npm-global/bin/github-copilot-cli",
        "/usr/local/bin/copilot",
    ]
    install_command = "npm install -g @github/copilot"
    auth_info = "copilot auth 또는 gh auth login"
    supported_models = [
        "default",
        "gpt-4o",
    ]

    def check_auth_status(self) -> Tuple[bool, str]:
        gh_cfg = os.path.expanduser("~/.config/gh/hosts.yml")
        if os.path.exists(gh_cfg):
            return True, "✅ GitHub CLI 토큰 연동 감지"
        return True, "ℹ️ `gh auth login` 또는 copilot 인증 지원"

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or "copilot"
        return [bin_path, "--prompt", prompt]


class CommandCodeAdapter(BaseEngineAdapter):
    engine_id = "cmdc"
    name = "Command Code CLI"
    icon = "💻"
    description = "Command Code 자율 코딩 에이전트 CLI (cmdc)"
    injects_autonomy_directive = True
    env_var = "CMDC_PATH"
    default_paths = [
        "/home/jangsung/.npm-global/bin/cmdc",
        "~/.npm-global/bin/cmdc",
        "/home/jangsung/.npm-global/bin/commandcode",
        "/usr/local/bin/cmdc",
        "/usr/bin/cmdc",
        "cmdc",
    ]
    install_command = "npm install -g command-code"
    auth_info = "cmdc login (또는 ~/.commandcode/auth.json)"

    _STATIC_MODELS = [
        "deepseek/deepseek-v4.1-flash",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
        "moonshotai/kimi-k2.7-code",
        "zai-org/glm-5.3",
        "z-ai/glm-5.3-flash",
        "minimaxai/minimax-m3",
        "xiaomi/mimo-v2.6-pro",
        "xiaomi/mimo-v2.6-flash",
        "qwen/qwen3.8-max",
        "qwen/qwen3.8-flash",
        "claude-sonnet-5-5",
        "claude-sonnet-4-6",
        "claude-opus-5-5",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-luna",
        "google/gemini-3.8-flash",
        "google/gemini-3.7-flash",
        "xai/grok-4.7",
        "xai/grok-4.5",
        "meta/muse-spark-1.3",
        "poolside/laguna-s-2.1-free",
    ]

    @property
    def supported_models(self) -> List[str]:
        try:
            from live_model_catalog import LiveModelCatalog
            live = LiveModelCatalog.get_models("cmdc")
            if live:
                return [mid for mid, _ in live]
        except Exception as e:
            logger.debug(f"CommandCodeAdapter: live catalog unavailable ({e})")
        return list(self._STATIC_MODELS)

    def check_auth_status(self) -> Tuple[bool, str]:
        auth_file = Path(os.path.expanduser("~/.commandcode/auth.json"))
        if auth_file.is_file():
            try:
                data = json.loads(auth_file.read_text(encoding="utf-8"))
                user = data.get("userName") or data.get("userId")
                if data.get("apiKey"):
                    return True, f"✅ Command Code 계정 인증됨 ({user})"
            except Exception:
                pass
        env_key = os.getenv("COMMAND_CODE_API_KEY") or os.getenv("CMDC_API_KEY")
        if env_key:
            return True, "✅ Command Code API Key 환경변수 설정됨"
        return False, "⚠️ cmdc 인증 필요 (`cmdc login` 또는 `~/.commandcode/auth.json`)"

    def build_command(
        self,
        prompt: str,
        workspace: str,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs,
    ) -> List[str]:
        bin_path = self.get_binary_path() or "cmdc"
        scoped_prompt = (
            f"{AUTONOMY_NO_QUESTION_DIRECTIVE}\n\n"
            f"📁 **작업 디렉토리(절대경로):** `{workspace}`\n"
            f"- 위 경로가 당신의 **유일한 작업 공간(워크스페이스)**입니다.\n"
            f"- \"현재 디렉토리\"를 물으면 위 절대경로를 그대로 답하세요.\n\n{prompt}"
        )
        cmd = [
            bin_path,
            "-p", scoped_prompt,
            "-t",
            "--yolo",
            "--tools-all",
            "--skip-onboarding",
            "--output-format", "json",
        ]
        target_model = self._resolve_model(model)
        if target_model:
            cmd.extend(["-m", target_model])
        if session_id:
            cmd.extend(["--session", session_id])
        return cmd

    def _resolve_model(self, model: Optional[str]) -> Optional[str]:
        if not model:
            return None
        return str(model).strip()

    async def execute(
        self,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        timeout: float = 3600.0,
        **kwargs
    ) -> Tuple[int, str, List[str]]:
        bin_path = self.get_binary_path()
        if not bin_path:
            return -1, "", [f"[{self.name}] 바이너리를 찾을 수 없습니다. ({self.install_command})"]

        conversation_id_attr = kwargs.get("conversation_id_attr") or "conversation_id"
        store = _get_engine_conversation_store(session)
        store_key = f"cmdc:{conversation_id_attr}"
        session_id = store.get(store_key)

        async def _run_once(use_session: bool) -> Tuple[int, str, List[str], Optional[str], bool]:
            cmd = self.build_command(
                prompt,
                session.workspace,
                model=model,
                session_id=session_id if use_session else None,
            )
            logger.info(f"Executing {self.name} command: {' '.join(cmd[:6])}...")
            await safe_tool_notify(on_tool_update, f"{self.icon} **[{self.name}] 작업 시작 중...**")

            env = os.environ.copy()
            npm_bin = os.path.expanduser("~/.npm-global/bin")
            if npm_bin not in env.get("PATH", ""):
                env["PATH"] = f"{npm_bin}:{env.get('PATH', '')}"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=session.workspace,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=30 * 1024 * 1024,
                start_new_session=True,
            )
            session.current_process = proc

            text_parts: List[str] = []
            stderr_lines: List[str] = []
            captured_sid: Optional[str] = None
            session_not_found = False
            final_text: Optional[str] = None

            async def read_stderr():
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        stderr_lines.append(decoded)
                        if len(stderr_lines) > 40:
                            stderr_lines.pop(0)

            stderr_task = asyncio.create_task(read_stderr())
            start_time = time.time()
            last_update_time = time.time()

            try:
                while True:
                    if timeout and (time.time() - start_time) > timeout:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        return -1, "", [f"[{self.name}] 실행 제한 시간({timeout}s) 초과"], None, False

                    line = await proc.stdout.readline()
                    if not line:
                        break
                    raw_str = line.decode("utf-8", errors="replace").strip()
                    if not raw_str:
                        continue

                    try:
                        ev_data = json.loads(raw_str)
                    except json.JSONDecodeError:
                        text_parts.append(raw_str)
                        continue

                    ev_type = ev_data.get("type")
                    if ev_type == "result":
                        captured_sid = ev_data.get("sessionId") or captured_sid
                        ft = ev_data.get("finalText")
                        if ft:
                            final_text = ft
                    elif ev_type == "event":
                        inner_ev = ev_data.get("event", {})
                        inner_type = inner_ev.get("type")
                        if inner_type == "run_start":
                            captured_sid = inner_ev.get("sessionId") or captured_sid
                        elif inner_type == "text_delta":
                            delta = inner_ev.get("delta", "")
                            if delta:
                                text_parts.append(delta)
                        elif inner_type in ("tool_call", "tool_start"):
                            tool_name = inner_ev.get("toolName") or inner_ev.get("name") or "tool"
                            await safe_tool_notify(on_tool_update, f"🔧 **[Command Code]** `{tool_name}` 실행 중...")
                        elif inner_type == "message_update":
                            content = inner_ev.get("content", "")
                            if content and isinstance(content, str):
                                if time.time() - last_update_time >= 5.0:
                                    last_update_time = time.time()
                                    snippet = content.strip().splitlines()[-1][:60]
                                    if snippet:
                                        await safe_tool_notify(on_tool_update, f"⚙️ {snippet}...")
                    elif ev_type == "text":
                        t = ev_data.get("text", "")
                        if t:
                            text_parts.append(t)

                return_code = await proc.wait()
                await stderr_task

                output_text = final_text if final_text else "".join(text_parts)
                return return_code, output_text, stderr_lines, captured_sid, session_not_found

            except asyncio.CancelledError:
                try:
                    proc.terminate()
                except Exception:
                    pass
                raise
            finally:
                session.current_process = None

        rc, out_txt, errs, new_sid, not_found = await _run_once(use_session=bool(session_id))
        if rc != 0 and session_id and (not_found or any("session" in e.lower() for e in errs)):
            logger.warning(f"Command Code session {session_id} failed; retrying with fresh session.")
            rc, out_txt, errs, new_sid, _ = await _run_once(use_session=False)

        if new_sid:
            store[store_key] = new_sid

        return rc, out_txt, errs


def normalize_engine_id(engine: Optional[str]) -> str:
    """
    Global Canonical Engine Normalizer:
    Enforces 'hermes' as the single unified identifier for Hermes Agent / OpenRouter API.
    Guarantees zero conceptual or architectural division between OpenRouter and Hermes.
    """
    if not engine:
        return "agy"
    clean = engine.lower().strip()
    if clean in ("hermes", "openrouter", "aider", "nous", "hermes-agent", "hermes_agent"):
        return "hermes"
    if clean in ("cmdc", "commandcode", "command-code", "command_code", "command"):
        return "cmdc"
    return clean


class EngineManager:
    """Registry and Coordinator for all AI CLI engines."""

    @staticmethod
    def normalize_engine_id(engine: Optional[str]) -> str:
        return normalize_engine_id(engine)

    MODEL_ALIASES: Dict[str, str] = {
        # Upstage Solar
        "solar": "openrouter/upstage/solar-pro4",
        "solar-pro": "openrouter/upstage/solar-pro4",
        "solar-pro4": "openrouter/upstage/solar-pro4",
        "solar-pro-4": "openrouter/upstage/solar-pro4",
        "solar-4": "openrouter/upstage/solar-pro4",
        "solar4": "openrouter/upstage/solar-pro4",
        "solar pro 4": "openrouter/upstage/solar-pro4",
        "upstage/solar-pro4": "openrouter/upstage/solar-pro4",
        "solar-pro3": "openrouter/upstage/solar-pro-3",
        "solar-pro-3": "openrouter/upstage/solar-pro-3",
        "upstage/solar-pro-3": "openrouter/upstage/solar-pro-3",

        # Qwen
        "qwen": "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen-coder": "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen-2.5-coder": "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen-2.5-coder-32b": "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen-32b": "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen/qwen-2.5-coder-32b-instruct": "openrouter/qwen/qwen-2.5-coder-32b-instruct",
        "qwen-72b": "openrouter/qwen/qwen-2.5-72b-instruct",
        "qwen3": "openrouter/qwen/qwen3-235b-a22b-2507",
        "qwen-3": "openrouter/qwen/qwen3-235b-a22b-2507",
        "qwen 3": "openrouter/qwen/qwen3-235b-a22b-2507",
        "qwen3-235b": "openrouter/qwen/qwen3-235b-a22b-2507",
        "qwen3-235b-a22b-2507": "openrouter/qwen/qwen3-235b-a22b-2507",
        "qwen/qwen3-235b-a22b-2507": "openrouter/qwen/qwen3-235b-a22b-2507",

        # DeepSeek
        "deepseek": "openrouter/deepseek/deepseek-chat",
        "deepseek-chat": "openrouter/deepseek/deepseek-chat",
        "deepseek-v3": "openrouter/deepseek/deepseek-chat",
        "deepseek-r1": "openrouter/deepseek/deepseek-r1",
        "r1": "openrouter/deepseek/deepseek-r1",
        "deepseek-coder": "openrouter/deepseek/deepseek-coder",
        "deepseek/deepseek-chat": "openrouter/deepseek/deepseek-chat",
        "deepseek/deepseek-r1": "openrouter/deepseek/deepseek-r1",
        "deepseek/deepseek-coder": "openrouter/deepseek/deepseek-coder",
        "deepseek-v4": "openrouter/deepseek/deepseek-v4-flash-0731",
        "deepseek-v4-flash": "openrouter/deepseek/deepseek-v4-flash-0731",
        "deepseek-v4-flash-0731": "openrouter/deepseek/deepseek-v4-flash-0731",
        "deepseek v4 flash 0731": "openrouter/deepseek/deepseek-v4-flash-0731",
        "deepseek v4 flash": "openrouter/deepseek/deepseek-v4-flash-0731",
        "deepseek v4": "openrouter/deepseek/deepseek-v4-flash-0731",
        "deepseek/deepseek-v4-flash-0731": "openrouter/deepseek/deepseek-v4-flash-0731",

        # Meta Llama
        "llama-3.3": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "llama-3.3-70b": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "meta-llama/llama-3.3-70b-instruct": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "llama-4": "openrouter/meta-llama/llama-4-scout",
        "llama 4": "openrouter/meta-llama/llama-4-scout",
        "llama4": "openrouter/meta-llama/llama-4-scout",
        "llama-4-scout": "openrouter/meta-llama/llama-4-scout",
        "llama 4 scout": "openrouter/meta-llama/llama-4-scout",
        "meta-llama/llama-4-scout": "openrouter/meta-llama/llama-4-scout",

        # InclusionAI Ling
        "ling": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling-3": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling-3.0": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling 3.0": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling-flash": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling-3.0-flash": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling 3.0 flash": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling-3.0-flash-fin": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "ling-3.0-flash-fin:free": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "inclusionai/ling-3.0-flash-fin": "openrouter/inclusionai/ling-3.0-flash-fin:free",
        "inclusionai/ling-3.0-flash-fin:free": "openrouter/inclusionai/ling-3.0-flash-fin:free",

        # OpenAI / Astra
        "astra": "openrouter/openai/gpt-6-astra",
        "gpt-6-astra": "openrouter/openai/gpt-6-astra",
        "gpt6-astra": "openrouter/openai/gpt-6-astra",
        "openai/gpt-6-astra": "openrouter/openai/gpt-6-astra",

        # Anthropic / Mistral / Others
        "gemini-3.1-pro-high": "gemini-3.1-pro-high",
        "claude-sonnet": "claude-sonnet-4-6",
        "claude sonnet": "claude-sonnet-4-6",
        "claude sonnet 4.6": "claude-sonnet-4-6",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        "claude-opus": "claude-opus-4-6-thinking",
        "claude opus": "claude-opus-4-6-thinking",
        "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
        "opus": "claude-opus-4-6-thinking",
        "sonnet": "claude-sonnet-4-6",
        "sonnet-4-6": "claude-sonnet-4-6",
        "sonnet 4.6": "claude-sonnet-4-6",
        "claude-3.7": "openrouter/anthropic/claude-3.7-sonnet",
        "claude-3.7-sonnet": "openrouter/anthropic/claude-3.7-sonnet",
        "anthropic/claude-3.7-sonnet": "openrouter/anthropic/claude-3.7-sonnet",
        "mistral": "openrouter/mistralai/mistral-large-2411",
        "mistral-large": "openrouter/mistralai/mistral-large-2411",
        "gpt-oss": "gpt-oss-120b-medium",
        "gpt-oss-120b": "gpt-oss-120b-medium",
        "gpt-oss-120b-medium": "gpt-oss-120b-medium",
        "gpt oss": "gpt-oss-120b-medium",
    }

    @classmethod
    def resolve_model_alias(cls, model_str: Optional[str]) -> Optional[str]:
        if not model_str:
            return None
        cleaned = model_str.strip()
        # Clean up spaces around slashes: e.g. "inclusionai / ling" -> "inclusionai/ling"
        cleaned = re.sub(r'\s*/\s*', '/', cleaned)
        key = cleaned.lower()

        # 1. Direct match in dictionary
        if key in cls.MODEL_ALIASES:
            return cls.MODEL_ALIASES[key]

        # 2. Normalized hyphens for spaces/underscores (e.g. "DeepSeek V4 Flash 0731" -> "deepseek-v4-flash-0731")
        hyphenated = re.sub(r'[\s_]+', '-', key)
        if hyphenated in cls.MODEL_ALIASES:
            return cls.MODEL_ALIASES[hyphenated]

        # 3. Suffix or matching entry in aliases
        for alias, full in cls.MODEL_ALIASES.items():
            if key == alias or key.endswith("/" + alias) or key.endswith(alias) or hyphenated == alias:
                return full

        # 4. Heuristics for common family names
        if "astra" in key:
            return "openrouter/openai/gpt-6-astra"
        if "deepseek" in key and ("v4" in key or "flash" in key or "0731" in key):
            return "openrouter/deepseek/deepseek-v4-flash-0731"
        if "qwen" in key and ("3" in key or "235b" in key or "2507" in key):
            return "openrouter/qwen/qwen3-235b-a22b-2507"
        if "llama" in key and ("4" in key or "scout" in key):
            return "openrouter/meta-llama/llama-4-scout"
        if "ling" in key:
            return "openrouter/inclusionai/ling-3.0-flash-fin:free"
        if "solar" in key and ("4" in key or "pro4" in key or "pro-4" in key or "pro 4" in key):
            return "openrouter/upstage/solar-pro4"

        # 5. If already starts with openrouter/, return cleaned
        if key.startswith("openrouter/"):
            return cleaned

        # 6. Any slug containing a slash '/' (provider/model) is automatically recognized as OpenRouter
        if "/" in key and not key.startswith("openrouter/"):
            return f"openrouter/{cleaned}"

        # 7. If it matches a known provider slug prefix
        known_prefixes = ("upstage/", "deepseek/", "qwen/", "anthropic/", "meta-llama/", "mistralai/", "google/", "inclusionai/", "openai/")
        if any(key.startswith(p) for p in known_prefixes):
            return f"openrouter/{cleaned}"

        # 8. Antigravity native model resolution (ensures non-OpenRouter gemini models follow /model catalog)
        if "gemini" in key and not key.startswith("openrouter/"):
            return AntigravityModelRegistry.resolve_model(preferred=cleaned)

        return cleaned

    _adapters: Dict[str, BaseEngineAdapter] = {
        "agy": AgyAdapter(),
        "hermes": HermesAdapter(),
        "openrouter": OpenRouterAdapter(),
        "codex": CodexAdapter(),
        "claude": ClaudeAdapter(),
        "opencode": OpenCodeAdapter(),
        "copilot": CopilotAdapter(),
        "cmdc": CommandCodeAdapter(),
        "commandcode": CommandCodeAdapter(),
    }

    @classmethod
    def get_adapter(cls, engine_id: str) -> Optional[BaseEngineAdapter]:
        if not engine_id:
            return cls._adapters.get("agy")
        clean_id = engine_id.lower().strip()
        if clean_id in cls._adapters:
            return cls._adapters[clean_id]
        normalized = normalize_engine_id(clean_id)
        return cls._adapters.get(normalized)

    @classmethod
    def get_all_adapters(cls) -> List[BaseEngineAdapter]:
        return list(cls._adapters.values())

    @classmethod
    def get_all_engine_ids(cls) -> List[str]:
        return list(cls._adapters.keys())

    @classmethod
    def detect_installed_engines(cls) -> Dict[str, bool]:
        return {eid: adapter.is_installed() for eid, adapter in cls._adapters.items()}

    @classmethod
    def diagnose_all(cls, workspace: str = "") -> Dict[str, Any]:
        """Runs a comprehensive Doctor diagnostic across all registered AI engines."""
        results = {}
        for eid, adapter in cls._adapters.items():
            installed = adapter.is_installed()
            bin_path = adapter.get_binary_path()
            version = adapter.get_version() if installed else "미설치"
            auth_ok, auth_msg = adapter.check_auth_status()
            results[eid] = {
                "name": adapter.name,
                "icon": adapter.icon,
                "installed": installed,
                "path": bin_path,
                "version": version,
                "auth_ok": auth_ok,
                "auth_msg": auth_msg,
                "models": adapter.supported_models,
                "install_command": adapter.install_command,
            }
        return results

    @classmethod
    def get_qa_adapter(cls, session: Optional[ChannelSession] = None) -> BaseEngineAdapter:
        """Resolves the designated QA Reviewer AI adapter (AGY / OpenRouter / Codex / Hermes / Claude)."""
        qa_eid = getattr(session, "qa_engine", "agy") if session else "agy"
        adapter = cls.get_adapter(qa_eid)
        if adapter and adapter.is_installed():
            return adapter
        # Fallback preference order for QA: agy -> openrouter -> hermes -> codex -> claude
        for fallback_id in ["agy", "openrouter", "hermes", "codex", "claude"]:
            cand = cls.get_adapter(fallback_id)
            if cand and cand.is_installed():
                return cand
        return cls._adapters["agy"]

    @classmethod
    def get_coder_adapter(cls, session: Optional[ChannelSession] = None) -> BaseEngineAdapter:
        """Resolves the primary Coder AI adapter (default AGY / Antigravity)."""
        coder_eid = getattr(session, "engine", "agy") if session else "agy"
        adapter = cls.get_adapter(coder_eid)
        if adapter and adapter.is_installed():
            return adapter
        return cls._adapters["agy"]

    @classmethod
    def get_auditor_adapter(cls, session: Optional[ChannelSession] = None) -> BaseEngineAdapter:
        """Resolves the Client Auditor AI adapter (default AGY / Codex / Claude)."""
        auditor_eid = getattr(session, "auditor_engine", "agy") if session else "agy"
        adapter = cls.get_adapter(auditor_eid)
        if adapter and adapter.is_installed():
            return adapter
        for fallback_id in ["agy", "codex", "claude"]:
            cand = cls.get_adapter(fallback_id)
            if cand and cand.is_installed():
                return cand
        return cls._adapters["agy"]

    @classmethod
    def build_qa_prompt(
        cls,
        user_prompt: str,
        workspace: str,
        diff_info: Optional[Dict[str, Any]] = None,
        gates_info: Optional[Dict[str, Any]] = None,
        anti_mock_violations: Optional[List[str]] = None,
        engine_name: str = "QA Inspector",
        persona_id: Optional[str] = None
    ) -> str:
        """Builds a high-rigor Senior QA Engineer prompt for external AI engines."""
        diff_files = diff_info.get("changed_files", []) if diff_info else []
        diff_files_str = ", ".join(diff_files) if diff_files else "워크스페이스 전체 점검"
        ins = diff_info.get("insertions", 0) if diff_info else 0
        dels = diff_info.get("deletions", 0) if diff_info else 0

        anti_mock_text = ""
        if anti_mock_violations:
            anti_mock_text = (
                "\n🚨 **[CRITICAL: Anti-Mocking 위반 적발]**\n"
                + "\n".join(f"- {v}" for v in anti_mock_violations)
                + "\n👉 판정 가이드: 위반 사항이 완전히 해소될 때까지 즉시 [STATUS: REJECTED]를 선언하십시오.\n\n"
            )

        gates_str = "전체 통과 (PASS)" if (gates_info and gates_info.get("all_passed")) else "일부 게이트 점검 필요"

        persona_block = ""
        try:
            from model_manager import QA_PERSONAS
            p_info = QA_PERSONAS.get(persona_id or "general", QA_PERSONAS.get("general", {}))
            if p_info:
                p_name = p_info.get("name", "시니어 QA")
                p_icon = p_info.get("icon", "🕵️")
                p_focus = p_info.get("focus_prompt", "")
                persona_block = (
                    f"**[💼 전문 역할(전공): {p_icon} {p_name}]**\n"
                    f"{p_focus}\n\n"
                )
        except Exception:
            pass

        return (
            f"[역할: 수석 QA 엔지니어 & 코드 감사관 (Senior QA Inspector / {engine_name})]\n\n"
            f"당신은 단순한 결함 검사관이 아니라, 전문적 관점에서 Coder가 미처 생각하지 못한 부분까지 설계할 수 있도록 리드하는 시니어 QA 파트너입니다.\n\n"
            + persona_block
            + (
                f"**[🎯 수동적 승인봇(Rubber-Stamping) 전면 금지 및 주도적 설계 비평 헌법]**:\n"
                f"1. **'이것이 최선인가? 더 좋은 선택(Better Alternative)은 없는가?' 주도적 비평**:\n"
                f"2. **직업(Persona)별 맞춤형 심층 설계 견인 (모든 역할 공통 의무)**:\n"
                f"   - 🕵️ **기본값 (종합 품질 검증관 / General)**: 단순 동작 여부로 승인 금지. '이것이 최선의 선택인가?'를 엄격히 자문하고 에러 메시지, 로딩 피드백, 조작성, 예외 처리 맹점을 보완하여 프로덕션 완성도로 견인하십시오.\n"
                f"   - 🎨 **디자이너 봇 (UI/UX Designer)**: 이목이 집중될 디자인인가? 폰트/타이포 밸런스는 적절한가? 단순 박스 대신 **나노바나나(#nano-banana-image)** 이미지 에셋을 생성/배치하여 완성도를 극대화하도록 지시하십시오.\n"
                f"   - 🛡️ **보안 전문가 (Security & AppSec)**: '가장 안전한 최선의 방어 구조인가?' 공격자 시각에서 권한 상승, 인젝션, 경로 탈출(`../`), 토큰 노출을 심의하고 안전한 보안 아키텍처 대안을 제시하십시오.\n"
                f"   - 💥 **카오스 테스터 (Chaos & Resilience)**: '극한 상황에서도 우아하게 복구(Graceful Degradation)되는가?' 비동기 경합, 타임아웃, 기형 페이로드 등 엣지 케이스를 격파하고 자가 치유 설계를 강제하십시오.\n"
                f"   - ⚡ **성능 & SRE 엔지니어 (Performance & SRE)**: '장기 가동 시 리소스 고갈 없는 최선의 성능인가?' O(N^2) 루프, 블로킹 I/O를 배치/캐싱으로 전환하고 메모리/소켓 누수 방어책을 제시하십시오.\n"
                f"   - 🏛️ **수석 아키텍트 (Architect & PO Requirements)**: '향후 확장과 유지보수가 가장 우수한 최선의 아키텍처인가?' 단일 책임(SRP), 모듈 디커플링, 클린 인터페이스 대안을 제시하십시오.\n\n"
            )
            + f"사용자의 의뢰/검증 요청 내용:\n"
            f"────────────────────────────────────────\n"
            f"{user_prompt}\n"
            f"────────────────────────────────────────\n\n"
            f"**[타깃 워크스페이스]**: `{workspace}`\n"
            f"**[물리적 Git 코드 변경 내역]**:\n"
            f"• 변경 파일: {diff_files_str}\n"
            f"• 라인 변경: +{ins} / -{dels}\n"
            f"• 7대 플랫폼 Quality Gate: {gates_str}\n"
            f"{anti_mock_text}"
            f"**[수석 QA 검증 및 감사 헌법]**:\n"
            f"1. **역할 고정 원칙**: 당신은 코드를 새로 짜는 Coder가 아닙니다. 코드를 검증하고 결함, 엣지 케이스, 런타임 크래시를 색출하는 **시니어 QA 검증관**입니다.\n"
            f"2. **실체 구현 및 사용자 질문 충족성 대조**: 사용자의 요구사항과 질문(평가, 분석, 원인, 해결책 등)에 대한 답변이 실제 소스코드와 결과물에 100% 충족되었는지 검증하십시오. 레퍼런스 이미지 외곽선 추출(Tracing)이나 픽셀 복사 모킹이 없는지 철저히 점검하십시오.\n"
            f"3. **결함 색출 & 설계 비평**: 버그, 예외 처리 누락, 메모리 누수, 눈속임(Mock/Stub/Tracing) 및 설계/시각적 완성도 미비를 전수 점검하십시오.\n"
            f"4. **보고서 판정 규칙**:\n"
            f"   - 결함/미비점 또는 설계적 재보강이 필요한 경우: 반드시 첫 줄에 `[STATUS: REJECTED]` 표기 및 Coder가 미처 생각지 못한 구체적 개선 가이드(폰트, 나노바나나 에셋, 아키텍처 스펙) 작성\n"
            f"   - 경미한 1~5줄 오타/버그를 직접 수정했다면: 첫 줄에 `[STATUS: APPROVED (QA Direct Patch)]` 표기\n"
            f"   - 모든 기준을 무결하게 통과하고 최선의 설계가 입증된 경우: 첫 줄에 `[STATUS: APPROVED]` 표기 및 종합 코드 리뷰서(전문가 관점 심층 평가 & 차기 고도화 권고) 작성"
        )

    @classmethod
    def build_status_embed(cls, current_engine_id: str, channel_id: int, session: Optional[ChannelSession] = None) -> discord.Embed:
        current_id = (current_engine_id or "agy").lower().strip()
        active_adapter = cls.get_adapter(current_id) or cls._adapters["agy"]

        role = getattr(session, "role", "coder") if session else "coder"
        qa_eid = getattr(session, "qa_engine", "agy") if session else "agy"
        qa_adapter = cls.get_adapter(qa_eid) or cls._adapters["agy"]
        auditor_eid = getattr(session, "auditor_engine", "agy") if session else "agy"
        auditor_adapter = cls.get_adapter(auditor_eid) or cls._adapters["agy"]

        role_badge = "🕵️ **QA Reviewer (품질 검증 & 코드 리뷰 모드)**" if role == "qa" else "👨‍💻 **Coder (초고속 개발 및 기능 구현 모드)**"

        embed = discord.Embed(
            title="🎛️ 멀티 AI 코딩 & QA 엔진 관리자 (Multi-Engine Orchestrator)",
            description=(
                f"현재 채널(`<#{channel_id}>`)의 역할과 AI 엔진을 배정하고 관리합니다.\n"
                f"Antigravity, Aider, Codex, Claude 등 다양한 AI 도구를 역할별로 호환 구동합니다."
            ),
            color=0xF1C40F if role == "qa" else 0x3498DB
        )

        embed.add_field(
            name="🎭 현재 채널 역할 (Role)",
            value=f"{role_badge}\n• 모드 변경 명령어: `!role qa` (검증 전담) 또는 `!role coder` (개발 전담)",
            inline=False
        )

        coder_model_label = getattr(session, "engine_model", None) or getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
        qa_model_label = getattr(session, "qa_model", None) or getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
        qa_backup_label = getattr(session, "qa_backup_model", None) or getattr(config, "QA_BACKUP_MODEL", "openrouter/qwen/qwen-2.5-coder-32b-instruct")

        embed.add_field(
            name="⚙️ 역할별 배정 AI 엔진 & 모델 현황",
            value=(
                f"• 👨‍💻 **Coder 엔진**: {active_adapter.icon} `{active_adapter.name}` (`{active_adapter.engine_id}`)\n"
                f"  - 모델: `{coder_model_label}`\n"
                f"• 🕵️ **QA Reviewer 엔진**: {qa_adapter.icon} `{qa_adapter.name}` (`{qa_adapter.engine_id}`)\n"
                f"  - 활성 모델: `{qa_model_label}`\n"
                f"  - 등록 백업: `{qa_backup_label}` (즉시 전환 준비 완료)\n"
                f"• ⚖️ **Auditor 엔진**: {auditor_adapter.icon} `{auditor_adapter.name}` (`{auditor_adapter.engine_id}`)"
            ),
            inline=False
        )

        # Overview table of all supported engines
        engine_rows = []
        for adapter in cls.get_all_adapters():
            installed = adapter.is_installed()
            status_tag = f"✅ 설치됨 ({adapter.get_version()})" if installed else f"⚠️ 미설치 (`{adapter.install_command}`)"
            marker = "👉 " if adapter.engine_id in (current_id, qa_eid) else ""
            engine_rows.append(
                f"{marker}{adapter.icon} **{adapter.name}** (`{adapter.engine_id}`)\n"
                f"  - 상태: {status_tag}\n"
                f"  - 특징: {adapter.description}"
            )

        embed.add_field(
            name="📋 지원되는 전체 AI 엔진 목록 & 로컬 설치 상태",
            value="\n\n".join(engine_rows),
            inline=False
        )

        embed.set_footer(text="💡 '!role <qa|coder>' 또는 '!engine qa <엔진>' 명령어로 원하는 역할을 즉시 설정할 수 있습니다.")
        return embed

    @classmethod
    def build_doctor_embed(cls, channel_id: int, workspace: str = "") -> discord.Embed:
        """Constructs an in-depth Apple Bento diagnostic report embed for all engines."""
        diag = cls.diagnose_all(workspace)

        embed = discord.Embed(
            title="🩺 AI 코딩 엔진 시스템 정밀 진단 리포트 (Engine Doctor)",
            description=(
                f"채널 ID: `{channel_id}` | 호스트 머신에 구성된 6대 AI 코딩 엔진의 물리 바이너리, 버전, 인증 및 워크스페이스 호환성을 전수 점검했습니다."
            ),
            color=0x2ECC71
        )

        installed_count = sum(1 for d in diag.values() if d["installed"])
        total_count = len(diag)

        embed.add_field(
            name="📊 엔진 가용성 요약",
            value=f"• 전체 7대 엔진 중 **{installed_count}개 설치 및 가동 준비 완료** ({installed_count}/{total_count})\n"
                  f"• 기본 추천: `🚀 agy` (3중 협업 & 자가 치유) / `🪽 hermes` (자율 에이전트 & 실시간 코드 수정) / `🧠 codex` (JSONL 자동화)",
            inline=False
        )

        for eid, info in diag.items():
            badge = "✅ 준비 완료" if info["installed"] else "⚠️ 미설치"
            models_str = ", ".join(info["models"][:3]) if info["models"] else "기본"
            detail = (
                f"• **상태**: `{badge}` (`{info['version']}`)\n"
                f"• **바이너리**: `{info['path'] or '없음'}`\n"
                f"• **인증**: {info['auth_msg']}\n"
                f"• **추천 모델**: `{models_str}`"
            )
            if not info["installed"]:
                detail += f"\n• **설치**: `{info['install_command']}`"

            embed.add_field(
                name=f"{info['icon']} {info['name']} (`{eid}`)",
                value=detail,
                inline=True
            )

        # Workspace inspection
        ws_path = workspace or config.DEFAULT_WORKSPACE
        is_git = os.path.isdir(os.path.join(ws_path, ".git"))
        git_badge = "✅ Git 저장소 연동됨" if is_git else "ℹ️ 일반 디렉토리"
        is_writable = os.access(ws_path, os.W_OK) if os.path.exists(ws_path) else False
        write_badge = "✅ 쓰기 권한 정상" if is_writable else "⚠️ 쓰기 권한 필요"

        embed.add_field(
            name="📁 현재 작업 공간(Workspace) 진단",
            value=(
                f"• **경로**: `{ws_path}`\n"
                f"• **저장소 상태**: {git_badge}\n"
                f"• **파일 시스템**: {write_badge}"
            ),
            inline=False
        )

        embed.set_footer(text="💡 '!engine <이름>' 명령어로 원하는 AI 도구로 즉시 전환할 수 있습니다.")
        return embed


class QAEngineDropdown(Select):
    def __init__(self, session_manager, channel_id: int):
        self.session_manager = session_manager
        self.channel_id = channel_id
        session = session_manager.get_session(channel_id)
        current_qa_id = getattr(session, "qa_engine", "claude") or "claude"

        options = []
        seen_engines = set()
        for adapter in EngineManager.get_all_adapters():
            canon_id = normalize_engine_id(adapter.engine_id)
            if canon_id in seen_engines:
                continue
            seen_engines.add(canon_id)
            installed = adapter.is_installed()
            status_str = "설치됨" if installed else "미설치"
            is_def = (adapter.engine_id == current_qa_id) or (canon_id == normalize_engine_id(current_qa_id))
            options.append(
                discord.SelectOption(
                    label=f"QA: {adapter.name[:35]}",
                    value=canon_id,
                    description=f"[{status_str}] {adapter.description[:40]}",
                    emoji=adapter.icon,
                    default=is_def
                )
            )

        super().__init__(
            placeholder="🕵️ QA 검증 전담 AI 엔진을 선택하세요...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        session = self.session_manager.get_session(self.channel_id)
        session.set_qa_engine(selected_id)
        self.session_manager.save_state()

        adapter = EngineManager.get_adapter(selected_id)
        new_embed = EngineManager.build_status_embed(session.engine, self.channel_id, session=session)
        new_view = EngineSelectView(self.session_manager, self.channel_id)

        await interaction.response.edit_message(embed=new_embed, view=new_view)
        await interaction.followup.send(
            f"✅ QA 검증 엔진이 **{adapter.icon} {adapter.name}** (`{selected_id}`)로 지정되었습니다!",
            ephemeral=True
        )


class EngineDropdown(Select):
    def __init__(self, session_manager, channel_id: int):
        self.session_manager = session_manager
        self.channel_id = channel_id
        session = session_manager.get_session(channel_id)
        current_id = getattr(session, "engine", "agy") or "agy"

        options = []
        seen_engines = set()
        for adapter in EngineManager.get_all_adapters():
            canon_id = normalize_engine_id(adapter.engine_id)
            if canon_id in seen_engines:
                continue
            seen_engines.add(canon_id)
            installed = adapter.is_installed()
            status_str = "설치됨" if installed else "미설치"
            is_def = (adapter.engine_id == current_id) or (canon_id == normalize_engine_id(current_id))
            options.append(
                discord.SelectOption(
                    label=f"Coder: {adapter.name[:35]}",
                    value=canon_id,
                    description=f"[{status_str}] {adapter.description[:40]}",
                    emoji=adapter.icon,
                    default=is_def
                )
            )

        super().__init__(
            placeholder="👨‍💻 Coder 개발 전담 AI 엔진을 선택하세요...",
            min_values=1,
            max_values=1,
            options=options,
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        session = self.session_manager.get_session(self.channel_id)
        session.set_engine(selected_id)
        self.session_manager.save_state()

        adapter = EngineManager.get_adapter(selected_id)
        new_embed = EngineManager.build_status_embed(selected_id, self.channel_id, session=session)
        new_view = EngineSelectView(self.session_manager, self.channel_id)

        await interaction.response.edit_message(embed=new_embed, view=new_view)
        await interaction.followup.send(
            f"✅ Coder 개발 엔진이 **{adapter.icon} {adapter.name}** (`{selected_id}`)로 전환되었습니다!",
            ephemeral=True
        )


class EngineSelectView(View):
    def __init__(self, session_manager, channel_id: int):
        super().__init__(timeout=180)
        self.session_manager = session_manager
        self.channel_id = channel_id
        session = session_manager.get_session(channel_id)
        current_id = getattr(session, "engine", "agy") or "agy"
        current_role = getattr(session, "role", "coder")
        current_qa_id = getattr(session, "qa_engine", "claude") or "claude"

        # Row 0 & 1: Select Dropdowns
        self.add_item(QAEngineDropdown(session_manager, channel_id))
        self.add_item(EngineDropdown(session_manager, channel_id))

        # Row 2: Role Switch & Doctor & Refresh
        role_label = "🎭 역할: 🕵️ QA 모드 가동 중" if current_role == "qa" else "🎭 역할: 👨‍💻 Coder 모드 가동 중"
        role_btn = Button(
            label=role_label,
            style=discord.ButtonStyle.success if current_role == "qa" else discord.ButtonStyle.primary,
            custom_id="btn_role_toggle",
            row=2
        )
        role_btn.callback = self._role_toggle_callback
        self.add_item(role_btn)

        doctor_btn = Button(label="🩺 엔진 진단", style=discord.ButtonStyle.secondary, custom_id="btn_engine_doctor", row=2)
        doctor_btn.callback = self._doctor_callback
        self.add_item(doctor_btn)

        refresh_btn = Button(label="🔄 새로고침", style=discord.ButtonStyle.gray, custom_id="btn_engine_refresh", row=2)
        refresh_btn.callback = self._refresh_callback
        self.add_item(refresh_btn)

        # Row 3: Quick QA Engine Buttons (Hermes/OpenRouter, Agy, Codex, Claude)
        for qid in ["hermes", "agy", "codex", "claude"]:
            adapter = EngineManager.get_adapter(qid)
            if not adapter:
                continue
            is_active_qa = (qid == current_qa_id) or (qid == "hermes" and current_qa_id in ("openrouter", "hermes"))
            btn_label = "🕵️ QA: HERMES (OpenRouter)" if qid == "hermes" else f"🕵️ QA: {qid.upper()}"
            btn = Button(
                label=btn_label,
                style=discord.ButtonStyle.success if is_active_qa else discord.ButtonStyle.secondary,
                custom_id=f"btn_qa_engine_{qid}",
                row=3
            )
            btn.callback = self._create_qa_btn_callback(qid)
            self.add_item(btn)

    async def _role_toggle_callback(self, interaction: discord.Interaction):
        session = self.session_manager.get_session(self.channel_id)
        current_role = getattr(session, "role", "coder")
        new_role = "qa" if current_role == "coder" else "coder"
        session.set_role(new_role)
        self.session_manager.save_state()

        new_embed = EngineManager.build_status_embed(session.engine, self.channel_id, session=session)
        new_view = EngineSelectView(self.session_manager, self.channel_id)

        await interaction.response.edit_message(embed=new_embed, view=new_view)
        role_desc = "🕵️ **QA Reviewer (품질 검증 전담)**" if new_role == "qa" else "👨‍💻 **Coder (초고속 개발 전담)**"
        await interaction.followup.send(
            f"✅ 채널 역할이 {role_desc}으로 전환되었습니다!\n"
            f"• Coder 엔진: `{session.engine}` | QA 엔진: `{session.qa_engine}`",
            ephemeral=True
        )

    def _create_qa_btn_callback(self, engine_id: str):
        async def callback(interaction: discord.Interaction):
            session = self.session_manager.get_session(self.channel_id)
            session.set_qa_engine(engine_id)
            self.session_manager.save_state()

            adapter = EngineManager.get_adapter(engine_id)
            new_embed = EngineManager.build_status_embed(session.engine, self.channel_id, session=session)
            new_view = EngineSelectView(self.session_manager, self.channel_id)

            await interaction.response.edit_message(embed=new_embed, view=new_view)
            await interaction.followup.send(
                f"✅ QA 검증 엔진이 **{adapter.icon} {adapter.name}** (`{engine_id}`)로 지정되었습니다!",
                ephemeral=True
            )
        return callback

    def _create_btn_callback(self, engine_id: str):
        async def callback(interaction: discord.Interaction):
            session = self.session_manager.get_session(self.channel_id)
            session.set_engine(engine_id)
            self.session_manager.save_state()

            adapter = EngineManager.get_adapter(engine_id)
            new_embed = EngineManager.build_status_embed(engine_id, self.channel_id, session=session)
            new_view = EngineSelectView(self.session_manager, self.channel_id)

            await interaction.response.edit_message(embed=new_embed, view=new_view)
            await interaction.followup.send(
                f"✅ 채널 AI 엔진이 **{adapter.icon} {adapter.name}** (`{engine_id}`)로 즉시 전환되었습니다!",
                ephemeral=True
            )
        return callback

    async def _doctor_callback(self, interaction: discord.Interaction):
        session = self.session_manager.get_session(self.channel_id)
        doc_embed = EngineManager.build_doctor_embed(self.channel_id, session.workspace)
        await interaction.response.send_message(embed=doc_embed, ephemeral=True)

    async def _refresh_callback(self, interaction: discord.Interaction):
        session = self.session_manager.get_session(self.channel_id)
        current_id = getattr(session, "engine", "agy") or "agy"
        new_embed = EngineManager.build_status_embed(current_id, self.channel_id, session=session)
        new_view = EngineSelectView(self.session_manager, self.channel_id)
        await interaction.response.edit_message(embed=new_embed, view=new_view)
        await interaction.followup.send("🔄 AI 엔진 로컬 설치 상태를 새로고침했습니다.", ephemeral=True)
