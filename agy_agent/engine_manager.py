import os
import re
import shutil
import subprocess
import asyncio
import logging
import json
import time
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


async def safe_tool_notify(on_tool_update_fn: Optional[Callable[[str], Any]], msg: str):
    if not on_tool_update_fn:
        return
    try:
        await asyncio.wait_for(on_tool_update_fn(msg), timeout=4.0)
    except Exception:
        pass


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
            clean = out.strip().split("\n")[0].strip()
            return clean if clean else "버전 확인 불가"
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
        last_activity_time = time.time()
        last_update_time = time.time()

        try:
            while True:
                # Enforce unresponsiveness deadline (600s idle for all roles, including QA/read-only thinking models) and total execution deadline
                now = time.time()
                if now - start_time > timeout:
                    logger.warning(f"Engine {self.name} exceeded overall timeout ({timeout}s). Terminating...")
                    stderr_lines.append(f"⚠️ 실행 제한 시간({int(timeout)}초) 초과로 안전 종료되었습니다.")
                    break

                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                except asyncio.TimeoutError:
                    idle_limit = 600.0  # 600s for all roles — deep thinking models (Claude Opus/Thinking) need 10+ min of silent internal reasoning
                    if now - last_activity_time > idle_limit:
                        logger.warning(f"Engine {self.name} unresponsive for {idle_limit}s. Terminating unresponsive process...")
                        stderr_lines.append(f"⚠️ 무응답 상태 지속({int(idle_limit)}초)으로 안전 종료되었습니다.")
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break
                    if proc.returncode is not None:
                        break
                    continue

                if not line:
                    break

                last_activity_time = time.time()
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
                accumulated_output.append(decoded)

                if now - last_update_time >= 4.0:
                    last_update_time = now
                    elapsed = int(now - start_time)
                    preview = decoded[:60].replace("`", "")
                    await safe_tool_notify(
                        on_tool_update,
                        f"{self.icon} **[{self.name} 실행 중 ({elapsed}s)]**\n`{preview}`"
                    )

            await proc.wait()
            await stderr_task
        except asyncio.CancelledError:
            if proc.pid:
                kill_process_tree(proc.pid)
            try:
                proc.kill()
            except Exception:
                pass
            raise
        finally:
            if proc.pid:
                kill_process_tree(proc.pid)
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
            "gemini-3.7-flash-middle",
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
            "--effort", getattr(config, "AGY_EFFORT", "high"),
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
            logger.warning(
                f"AgyAdapter: External model '{effective_model}' is not an Antigravity native model. "
                f"Falling back to 'gemini-3.1-pro-high' for AGY execution."
            )
            effective_model = "gemini-3.1-pro-high"
        cmd.extend(["--model", effective_model])
        if workspace:
            cmd.extend(["--add-dir", workspace])
        conv_id = kwargs.get("conversation_id")
        if conv_id:
            cmd.extend(["--conversation", conv_id])
        cmd.extend(["--print", prompt])
        return cmd


class HermesAdapter(BaseEngineAdapter):
    engine_id = "hermes"
    name = "Hermes Agent (OpenRouter API)"
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

        # [🧠 세션 지속성] --oneshot 대신 --continue로 대화 맥락을 유지합니다.
        conv_name = None
        if session:
            conv_name = f"agy-channel-{session.channel_id}"
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
                "--message", prompt,
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
        is_consultant = session_role == "consultant" or session_mode == "single_plus"
        has_cli = bool(self.get_binary_path())

        # MANDATE: All OpenRouter bots execute via Hermes Agent CLI when binary is available!
        # When role is QA reviewer, Hermes runs in strict READONLY mode (-t readonly) to guarantee ZERO workspace writes.
        if has_cli:
            mode_desc = "READONLY mode (strictly zero workspace writes)" if is_qa_review else "full autonomous mode"
            logger.info(f"OpenRouter executing via Hermes Agent CLI ({mode_desc}, role: {session_role}, mode: {session_mode}).")
            if is_qa_review:
                await safe_tool_notify(on_tool_update, f"🪽 **[Hermes QA] {model or getattr(config, 'QA2_MODEL', 'upstage/solar-pro4')} 읽기 전용 정밀 검증 중...**")
            qa_timeout = min(timeout, 900.0) if is_qa_review else timeout
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
            d_code, d_resp, d_errors = await self.execute_direct_api(prompt, model=model, timeout=min(timeout, 600.0))
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
                    b_code, b_resp, b_errors = await self.execute_direct_api(prompt, model=backup_model, timeout=min(timeout, 600.0))
                    if b_code == 0 and b_resp:
                        return b_code, b_resp, []

            return d_code, d_resp, d_errors or errors

        # Environments without Hermes CLI binary fallback to direct API
        await safe_tool_notify(on_tool_update, f"🌐 **[OpenRouter] {model or 'openai/gpt-6-astra'} API 직접 분석 호출 중...**")
        code, resp, errors = await self.execute_direct_api(prompt, model=model, timeout=min(timeout, 900.0 if is_consultant else 600.0))
        if code == 0 and resp:
            return code, resp, errors

        if is_consultant:
            return 1, "", [f"OpenRouter Consultant direct API call failed: {'; '.join(errors)}"]

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
            b_code, b_resp, b_errors = await self.execute_direct_api(prompt, model=backup_model, timeout=min(timeout, 600.0))
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

        async def read_stderr():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    if len(stderr_lines) > 50:
                        stderr_lines.pop(0)

        stderr_task = asyncio.create_task(read_stderr())
        start_time = time.time()

        try:
            while True:
                if time.time() - start_time > timeout:
                    stderr_lines.append(f"⚠️ 실행 제한 시간({int(timeout)}초) 초과")
                    # Kill process tree to prevent deadlock on subsequent proc.wait()
                    if proc.pid:
                        kill_process_tree(proc.pid)
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break

                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    continue

                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded:
                    continue
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

            await proc.wait()
            await stderr_task
        except asyncio.CancelledError:
            if proc.pid:
                kill_process_tree(proc.pid)
            try:
                proc.kill()
            except Exception:
                pass
            raise
        finally:
            if proc.pid:
                kill_process_tree(proc.pid)
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


class OpenCodeAdapter(BaseEngineAdapter):
    engine_id = "opencode"
    name = "OpenCode Interpreter"
    icon = "⚡"
    description = "오픈소스 로컬/클라우드 자율 코드 실행 인터프리터 CLI"
    env_var = "OPENCODE_PATH"
    default_paths = [
        "/home/jangsung/.local/bin/opencode",
        "/home/jangsung/.local/bin/interpreter",
        "/usr/local/bin/opencode",
        "/usr/bin/opencode",
    ]
    install_command = "pip install opencode-ai 또는 pip install open-interpreter"
    auth_info = "로컬 LLM (Ollama, LM Studio) 또는 OPENAI_API_KEY"
    supported_models = [
        "ollama/qwen2.5-coder",
        "ollama/deepseek-coder",
        "gpt-4o",
    ]

    def check_auth_status(self) -> Tuple[bool, str]:
        return True, "✅ 오픈소스 로컬 인터프리터 모드 지원"

    def build_command(self, prompt: str, workspace: str, model: Optional[str] = None, **kwargs) -> List[str]:
        bin_path = self.get_binary_path() or "opencode"
        return [bin_path, "run", prompt, "-y"]


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
            canon_id = "hermes" if adapter.engine_id in ("hermes", "openrouter") else adapter.engine_id
            if canon_id in seen_engines:
                continue
            seen_engines.add(canon_id)
            installed = adapter.is_installed()
            status_str = "설치됨" if installed else "미설치"
            is_def = (adapter.engine_id == current_qa_id) or (canon_id == "hermes" and current_qa_id in ("hermes", "openrouter"))
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
            canon_id = "hermes" if adapter.engine_id in ("hermes", "openrouter") else adapter.engine_id
            if canon_id in seen_engines:
                continue
            seen_engines.add(canon_id)
            installed = adapter.is_installed()
            status_str = "설치됨" if installed else "미설치"
            is_def = (adapter.engine_id == current_id) or (canon_id == "hermes" and current_id in ("hermes", "openrouter"))
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
