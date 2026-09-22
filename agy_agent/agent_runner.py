import os
import re
import signal
import subprocess
import time
import asyncio
import json
import logging
from typing import Optional, Callable, Any, List, Tuple
from config import AGY_PATH, AGY_PRINT_TIMEOUT, WATCHDOG_TOOL_TIMEOUT, WATCHDOG_SILENCE_TIMEOUT
import config
from session_manager import ChannelSession
from discord_utils import format_tool_status, cleanse_agent_output

logger = logging.getLogger(__name__)


async def safe_tool_notify(on_tool_update_fn: Optional[Callable[[str], Any]], msg: str):
    if not on_tool_update_fn:
        return
    try:
        await asyncio.wait_for(on_tool_update_fn(msg), timeout=4.0)
    except Exception:
        pass

TOOL_SAFETY_HEADER = (
    "[CRITICAL SYSTEM & TOOL SAFETY INSTRUCTIONS]\n"
    "1. TOOL USAGE RULES (CRITICAL - AVOIDS SYSTEM CRASH):\n"
    "   - `find_by_name`: MANDATORY `Pattern` required (e.g. `Pattern: \"*\"`). Missing `Pattern` causes fatal crash (`missing property 'Pattern'`)!\n"
    "   - `write_to_file`: NEVER pass `ArtifactMetadata` on workspace/project files. Use only for internal brain/ files.\n"
    "   - Editing files: ALWAYS use `replace_file_content` or `write_to_file` with `Overwrite: true` (no ArtifactMetadata).\n"
    "2. SCRIPT & PROCESS EXECUTION SAFETY (AVOIDS DEADLOCKS/HANGS):\n"
    "   - Node/Python scripts (Playwright/tests): ALWAYS use try/finally with `browser.close()` & `process.exit(0)`.\n"
    "   - Strict timeouts: ALWAYS specify `timeout: 10000` on wait/navigation/network to prevent hanging.\n"
    "3. SERVICE SAFETY: NEVER run `systemctl restart` or restart bot service processes.\n"
    "4. LINK FORMATTING FOR DISCORD: Clean URLs directly or `[Label](url)`. NEVER raw markdown wrapper `[url](url)`.\n"
    "5. RUN_COMMAND & BUILD VERIFICATION (PHYSICAL INTEGRITY):\n"
    "   - 컴파일, 빌드, 단위 테스트는 적극적으로 실행하여 실제 물리적 무결성을 직접 확인하십시오. 단, 프로세스가 무한 대기(hang)하지 않도록 백그라운드 스크립트 실행 시 적절한 타임아웃을 지정하십시오.\n"
    "6. UNIVERSAL VALUE CREATION & ANTI-MOCKING MANDATE:\n"
    "   - [사용자 질문에 대한 직접 답변 최우선 원칙 / User Question Direct Answer First]: 질문/진단/평가 요청 시, 장황한 내부 회의록 배제하고 사용자 질문 답변을 최상단에 제공.\n"
    "   - [레퍼런스 이미지 외곽선 추출(Tracing) 및 소스코드 내 로드 원천 금지 / Anti-Tracing & Anti-Plagiarism]: 레퍼런스 이미지 외곽선 추출/픽셀 복사 엄격 금지. 순수 절차적 코드/3D/셰이더 생성.\n"
    "   - [가시적 실체 구현 헌법]: 단순 주석/테스트 눈속임(비가시적 눈속임) 금지. 실동작 UI/핵심 로직 구현 (비UI 과제는 전략/자동화 산출물 완결).\n"
    "7. DEFAULT TIMESTAMPED BUILD ARTIFACT MANDATE: 바이너리/APK 빌드 시 타임스탬프(`{AppName}-YYYYMMDD_HHMMSS.apk`) 및 `-latest.apk` 동시 생성.\n"
    "8. COMMANDER-APPROVED VISUAL ASSET TAXONOMY: `generate_image`는 사령관(<@1539448922643234876>) 승인 시만 호출. Type A(풀컷)/Type B(단일 객체/단색 배경) 2원화.\n"
    "9. STRICT WORKSPACE CONFINEMENT: 지정 워크스페이스(CWD) 외부 파일 접근/탐색/수정 절대 금지.\n"
    "10. COMPREHENSIVE REASONING & COMPACT CODE REPORT:\n"
    "    - 사용자 질문, 진단, 평가, 해결책에 대해 충분히 깊이 있고 논리적으로 완성도 높은 분석과 답변을 제공하십시오. 단, 불필요한 수백 줄의 소스코드 전체 원본 덤프나 감정적 미사여구는 배제하고 핵심 변경 사항(diff 및 요약)을 명확하게 제시하십시오.\n"
    "11. CLEAN MONOLOGUE-FREE USER-FACING REPORTING:\n"
    "    - 'I have initiated...', 'I am waiting for...', 'I will now wait for...' 등 도구 실행 중간의 영어 혼잣말/상태 문구를 본문에 절대 노출하지 마십시오. 사용자에게는 깔끔하게 완성된 한국어 본문과 결과물만 명쾌하게 제시하십시오.\n"
    "12. COMPLETE ASYNC EXECUTION & DELIVERABLE MANDATE:\n"
    "    - 빌드, APK 생성, 파일 전송/업로드, 서브에이전트 등의 작업을 실행할 때는 '백그라운드에서 진행 중입니다'라고 1차 메시지만 뱉고 조기 종료하지 마십시오. 반드시 작업이 완료될 때까지 기다려 최종 결과(성공 여부, 다운로드 링크, 파일 경로)를 확보한 뒤 최종 완결 보고서를 작성하십시오.\n"
    "13. ANTI-AI-SLOP & HUMAN-CRAFTED CRAFTSMANSHIP MANDATE (상용 서비스 품격 및 AI 냄새 완전 박멸):\n"
    "    - [Zero Prompt Bleed-through]: 지침서/PRD의 내부 개발 수치('50px+', '70px+', '16ms', '60fps')나 아키텍처 용어('3-Way 자동 병합', '온디바이스 평탄화 파이프라인')를 화면 UI 텍스트로 그대로 박는 행위 엄격 금지! 토스/애플 수준의 자연스러운 사용자 언어로 작성하십시오.\n"
    "    - [Zero PoC Clutter]: '단절 테스트', 'PoC 시뮬레이션 버튼' 등 기술 과시용 시연 버튼을 일반 사용자 화면에 노출하지 마십시오. 백그라운드 자동화로 처리하거나 설정 하위로 격리하십시오.\n"
    "    - [반응형 텍스트 쪼개짐 방어]: 버튼/탭/칩 텍스트가 공간 부족으로 '단절 / 테스 / 트'처럼 세로로 쪼개지지 않도록 maxLines = 1, overflow = TextOverflow.Ellipsis, minWidth와 패딩을 반드시 지정하십시오.\n"
    "    - [아이콘 대원칙]: 24~48dp 크기 버튼/탭/아이콘 영역에는 절대 생성형 AI 비트맵 썸네일을 축소해 욱여넣지 말고 안드로이드 공식 Material Icons/벡터 드로어블만 사용하십시오.\n"
    "    - [AI 클리셰 더미 데이터 금지]: '김민수 수석연구원', '1페이지 펜 필기 검토 중' 등 천편일률적 LLM 생성 더미 데이터를 배제하고 실제 제품 도메인에 부합하는 자연스러운 데이터를 사용하십시오.\n"
    "    - [모바일 360dp 뷰포트 적응형 툴바]: 상단 툴바에는 핵심 액션 최대 3개만 노출하고, 4개 이상은 반드시 '더보기(⋮)' 드롭다운 메뉴로 수납하여 화면 밖으로 메뉴가 삐져나가지 않도록 설계하십시오.\n\n"
)

TOOL_SAFETY_FOOTER = (
    "\n\n[MANDATORY TOOL SAFETY & REASONING REMINDER]\n"
    "- STRICT DIRECTORY BOUNDARY: NEVER access or modify files outside designated workspace.\n"
    "- NO VERBOSE ABSOLUTE PATHS: Use compact relative paths (./...) or filenames only.\n"
    "- THOROUGH REASONING & DIRECT ANSWER FIRST: Answer questions thoroughly, show key diffs/summaries without dumping whole source files.\n"
    "- `find_by_name`: ALWAYS supply `Pattern`.\n"
    "- `write_to_file`: OMIT `ArtifactMetadata` on workspace files.\n"
    "- Modifying files: Use `replace_file_content`.\n"
    "- PHYSICAL INTEGRITY: Execute builds and tests to verify code compilation and runtime stability.\n"
    "- Clean URLs only; Do NOT use `ask_question`, `wait`, `sleep`, or `schedule` tools. (Subagents via `invoke_subagent` are fully supported).\n"
)


async def safe_on_tool_update(on_tool_update_fn, msg: str):
    if not on_tool_update_fn:
        return
    try:
        await asyncio.wait_for(on_tool_update_fn(msg), timeout=5.0)
    except Exception:
        pass


def get_workspace_grounding_context(workspace: Optional[str], prompt: str = "") -> str:
    """
    Prompt Injection Diet:
    Injects only the 1 focused milestone task and up to 3 essential constraints from the active domain profile.
    Avoids dumping massive raw PRD/Constitution markdown to eliminate context pollution and hallucinated mobile/web rules.
    """
    if not workspace or not os.path.isdir(workspace):
        return ""

    # 1. Focused Milestone Task
    focused_task = None
    roadmap_path = os.path.join(workspace, "ROADMAP.md")
    if os.path.isfile(roadmap_path):
        try:
            with open(roadmap_path, "r", encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if line_s.startswith("- [ ]") or line_s.startswith("* [ ]"):
                        clean_task = re.sub(r"^[-*]\s*\[\s*\]\s*", "", line_s)
                        if clean_task:
                            focused_task = clean_task[:150]
                            break
        except Exception:
            pass

    if not focused_task:
        prd_path = os.path.join(workspace, "PRD.md")
        if os.path.isfile(prd_path):
            try:
                with open(prd_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line_s = line.strip()
                        if "REQ-" in line_s:
                            focused_task = line_s.lstrip("-*# ").strip()[:150]
                            break
            except Exception:
                pass

    # 2. Active Profile & Essential Constraints (<= 3 constraints)
    profile_path = os.path.join(workspace, "profiles", "active_profile.md")
    if not os.path.isfile(profile_path):
        profile_path = os.path.join(workspace, "PROJECT_CONSTITUTION.md")

    profile_name = "AGY Standard"
    constraints = []
    if os.path.isfile(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                content = f.read()
            m_title = re.search(r"^#+\s+(.+)$", content, re.MULTILINE)
            if m_title:
                profile_name = m_title.group(1).strip()

            # Constraint 1: Platform specification
            m_plat = re.search(r"\*\s*\*\*공식 플랫폼 규격\*\*:\s*`?([^`\n]+)`?", content)
            if m_plat:
                constraints.append(f"규격: {m_plat.group(1).strip()[:80]}")

            # Constraint 2: Forbidden invariant
            m_forbid = re.search(r"\*\s*\*\*엄격 금지[^*]*\*\*:\s*`?([^`\n]+)`?", content)
            if not m_forbid:
                m_forbid = re.search(r"-\s*(단순 보여주기식[^\n]+|불필요한[^\n]+|웹/HTML[^\n]+|가짜[^\n]+)", content)
            if m_forbid:
                constraints.append(f"금지: {m_forbid.group(1).strip()[:80]}")

            # Constraint 3: Verification engine
            m_qa = re.search(r"\*\s*\*\*(?:품질 검증 엔진|테스트 프레임워크)\*\*:\s*`?([^`\n]+)`?", content)
            if m_qa:
                constraints.append(f"검증: {m_qa.group(1).strip()[:80]}")
        except Exception:
            pass

    if not constraints:
        constraints = [
            "규격: 디스크 상의 실제 물리적 코드 구현",
            "금지: 가짜 목업 및 테스트 전용 패치 금지",
            "검증: 기계적 테스트 통과 (Exit code 0)"
        ]

    # 3. QA Configuration (Concise)
    qa_summary = ""
    for qa_fname in ["qa_config.json", os.path.join("reports", "qa_config.json")]:
        qa_path = os.path.join(workspace, qa_fname)
        if os.path.isfile(qa_path):
            try:
                with open(qa_path, "r", encoding="utf-8") as f:
                    qa_data = json.load(f)
                from qa_wizard import QA_FOCUS_GALLERY
                f_meta = QA_FOCUS_GALLERY.get(qa_data.get("focus", "full_comprehensive"), {})
                qa_summary = f"• 🧪 **QA 초점**: `{f_meta.get('label', qa_data.get('focus'))}`"
                break
            except Exception:
                pass

    # 4. Diagnostic query detection
    try:
        from mode_classifier import is_diagnostic_prompt
        is_diag = is_diagnostic_prompt(prompt)
    except Exception:
        is_diag = False

    diag_notice = ""
    if is_diag:
        diag_notice = (
            "⚠️ **[시스템 진단/질의응답 최우선 원칙]**: 사용자가 시스템 부하, 진단, 원인 분석, 상태 점검 등 "
            "시스템 자체에 대한 질의를 한 경우 신규 기능 개발을 시도하지 말고 질문에 대한 답변을 최우선 제공하십시오."
        )

    parts = ["[🎯 WORKSPACE FOCUS & CONSTRAINTS]"]
    if focused_task:
        parts.append(f"• 🎯 **집중 과제**: {focused_task}")
    parts.append(f"• 🏛️ **활성 프로파일**: {profile_name}")
    for idx, c in enumerate(constraints[:3], 1):
        parts.append(f"• 🔒 **핵심 제약 {idx}**: {c}")
    if qa_summary:
        parts.append(qa_summary)
    if diag_notice:
        parts.append(diag_notice)
    parts.append("────────────────────────────────────────\n")

    return "\n".join(parts) + "\n"


class AgentRunner:
    @staticmethod
    def get_descendant_pids(parent_pid: int) -> List[int]:
        """Recursively finds all child and grandchild PIDs of a given parent process."""
        try:
            out = subprocess.check_output(["pgrep", "-P", str(parent_pid)], text=True, timeout=2.0)
            children = [int(p) for p in out.strip().split() if p.isdigit()]
            all_pids = []
            for c in children:
                all_pids.append(c)
                all_pids.extend(AgentRunner.get_descendant_pids(c))
            return all_pids
        except Exception:
            return []

    @staticmethod
    async def _wait_for_descendants_grace(parent_pid: int, grace_seconds: float = 30.0):
        """Waits up to grace_seconds for child processes to finish naturally.
        Prevents premature SIGKILL when Coder or tooling subagents are still completing disk writes."""
        import asyncio
        check_interval = 1.0
        elapsed = 0.0
        while elapsed < grace_seconds:
            pids = AgentRunner.get_descendant_pids(parent_pid)
            if not pids:
                return  # All children finished naturally
            await asyncio.sleep(check_interval)
            elapsed += check_interval
        # If lingering child processes remain after grace period, clean them up to prevent resource leaks
        pids = AgentRunner.get_descendant_pids(parent_pid)
        if pids:
            logger.warning(f"AgentRunner: {len(pids)} lingering descendant processes (PIDs: {pids[:5]}) exceeded {grace_seconds}s grace period. Cleaning up...")
            AgentRunner.kill_descendants_immediate(parent_pid)

    @staticmethod
    def kill_descendants_immediate(parent_pid: int):
        """Immediately kills all descendant child processes (no grace period)."""
        pids = AgentRunner.get_descendant_pids(parent_pid)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    @staticmethod
    def kill_descendants(parent_pid: int):
        """Kills all descendant child processes (e.g. hanging node/chromium/python scripts)."""
        pids = AgentRunner.get_descendant_pids(parent_pid)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    @classmethod
    async def _execute_single_run(
        cls,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        conversation_id_override: Optional[str] = "KEEP",
        conversation_id_attr: Optional[str] = None
    ) -> Tuple[int, str, List[str], List[str]]:
        """
        Executes a single AGY CLI invocation in stream-json mode with Heartbeat Watchdog & Deadlock Auto-Recovery.
        Returns: (returncode, accumulated_response, last_error_messages, all_error_lines)
        """
        cmd = [
            AGY_PATH,
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--print-timeout", AGY_PRINT_TIMEOUT,
            "--effort", getattr(config, "AGY_EFFORT", "high"),
        ]

        if not model:
            if getattr(session, "role", "coder") == "qa":
                effective_model = getattr(session, "qa_model", None) or getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
            elif getattr(session, "role", "coder") == "auditor":
                effective_model = getattr(config, "AUDITOR_MODEL", "gemini-3.1-pro-high")
            else:
                effective_model = getattr(session, "engine_model", None) or getattr(config, "CODER_MODEL", "gemini-3.1-pro-high")
        else:
            effective_model = model

        # [Dynamic Model Resolution & Quota Guard]
        # Resolve common model aliases to canonical AGY / OpenRouter identifiers
        eff_lower = (effective_model or "").lower().strip()
        if eff_lower in ("gpt-oss", "gpt-oss-120b", "gpt_oss", "gpt_oss_120b", "oss-120b", "oss"):
            effective_model = "gpt-oss-120b-medium"
            eff_lower = effective_model.lower().strip()
        elif eff_lower in ("claude-opus", "claude-opus-4-6", "opus-4-6", "opus"):
            effective_model = "claude-opus-4-6-thinking"
            eff_lower = effective_model.lower().strip()
        elif eff_lower in ("claude-sonnet", "sonnet-4-6", "sonnet"):
            effective_model = "claude-sonnet-4-6"
            eff_lower = effective_model.lower().strip()

        session_role = (getattr(session, "role", None) or "coder").lower().strip()
        default_gemini = "gemini-3.1-pro-high"

        # [Quota Guard] Check if the requested model is exhausted.
        # If exhausted, redirect to a healthy first-party Gemini model before CLI dispatch.
        try:
            from trio_agent_runner import QuotaManager
            is_exhausted = QuotaManager.is_model_exhausted(effective_model)
        except Exception:
            is_exhausted = False

        if is_exhausted:
            redirect_reason = "할당량 소진(Quota Exhaustion)"
            fallback_gemini = default_gemini
            try:
                from trio_agent_runner import QuotaManager
                candidates = [default_gemini, "gemini-3.8-flash-high", "gemini-3.7-flash-high", "gemini-3.1-pro-high", "gemini-3-flash"]
                for candidate in candidates:
                    if not QuotaManager.is_model_exhausted(candidate):
                        fallback_gemini = candidate
                        break
            except Exception:
                pass
            logger.warning(
                f"[Quota Guard] "
                f"Model '{effective_model}' ({redirect_reason}) -> Redirecting to healthy '{fallback_gemini}'."
            )
            await safe_tool_notify(
                on_tool_update,
                f"🛡️ **[쿼터 가드]** `{effective_model}`({redirect_reason}) ➔ 대체 모델(`{fallback_gemini}`)로 안전 자동 재라우팅되었습니다."
            )
            effective_model = fallback_gemini
            eff_lower = effective_model.lower().strip()

        # AGY CLI Guard: Native Antigravity models (Gemini 3.x, claude-sonnet-4-6, claude-opus-4-6-thinking, gpt-oss-120b-medium) pass directly to AGY CLI.
        # Non-native OpenRouter/third-party models are dispatched directly to Hermes Agent (OpenRouter API)
        # without any forced fallback to Gemini.
        try:
            from model_registry import AntigravityModelRegistry
            is_native = AntigravityModelRegistry.is_antigravity_model(effective_model)
        except Exception:
            is_native = any(nm in effective_model.lower() for nm in ("gemini-3", "claude-sonnet", "claude-opus", "gpt-oss", "oss"))

        if is_native:
            pass
        elif (
            effective_model.lower().startswith("openrouter/")
            or ("/" in effective_model and not any(k in effective_model.lower() for k in ("claude", "gpt-oss", "gemini")))
            or any(marker in effective_model.lower() for marker in ("solar", "deepseek", "qwen", "llama", "ling", "minimax"))
        ):
            from engine_manager import EngineManager
            hermes_adapter = EngineManager.get_adapter("hermes")
            if hermes_adapter:
                logger.info(
                    f"Direct Dispatch: Non-Antigravity OpenRouter model '{effective_model}' dispatched directly to Hermes Agent (OpenRouter API) without Gemini fallback."
                )
                code, resp, errors = await hermes_adapter.execute(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=effective_model,
                    role=session_role
                )
                return code, resp, errors, []

        cmd.extend(["--model", effective_model])

        if session.workspace:
            cmd.extend(["--add-dir", session.workspace])

        if conversation_id_override == "KEEP":
            conv_id_to_use = getattr(session, conversation_id_attr, None) if conversation_id_attr else session.conversation_id
        else:
            conv_id_to_use = conversation_id_override

        if conv_id_to_use:
            cmd.extend(["--conversation", conv_id_to_use])

        grounding_ctx = get_workspace_grounding_context(session.workspace, prompt)
        final_prompt = TOOL_SAFETY_HEADER + grounding_ctx + prompt + TOOL_SAFETY_FOOTER
        cmd.extend(["--print", final_prompt])

        accumulated_response = ""
        last_error_messages: List[str] = []
        non_json_outputs: List[str] = []
        stderr_lines: List[str] = []

        # [🛡️ LS Quota Leak Neutralization via CloudCode Caching Proxy]
        agy_env = os.environ.copy()
        try:
            from cloudcode_cache_proxy import start_proxy, get_proxy_url
            proxy_url = get_proxy_url()
            if not proxy_url:
                port = start_proxy()
                proxy_url = getattr(config, "get_cloudcode_proxy_url", lambda p: get_proxy_url(p))(port)
            if proxy_url:
                agy_env["CLOUD_CODE_URL"] = proxy_url
        except Exception:
            pass  # Fallback: direct connection without proxy

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=session.workspace,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=agy_env,
            limit=50 * 1024 * 1024,  # 50MB stream buffer
            start_new_session=True
        )
        session.current_process = proc

        async def read_stderr():
            while True:
                err_line = await proc.stderr.readline()
                if not err_line:
                    break
                err_str = err_line.decode("utf-8", errors="replace").strip()
                if err_str:
                    stderr_lines.append(err_str)
                    if len(stderr_lines) > 50:
                        stderr_lines.pop(0)

        # Start reading stderr concurrently to avoid buffer blocking
        stderr_task = asyncio.create_task(read_stderr())

        # -----------------------------------------------------------------
        # Heartbeat & Deadlock Auto-Recovery Watchdog
        # -----------------------------------------------------------------
        turn_start_time = time.time()
        last_activity_time = time.time()
        last_heartbeat_time = time.time()
        last_discord_update_time = time.time()
        is_tool_running = False
        is_subagent_running = False
        has_subagents_active = False
        current_tool_desc = ""
        tool_start_time = 0.0
        step_count = 0
        inspected_items: List[str] = []
        child_swept = False

        async def watchdog():
            nonlocal child_swept, last_heartbeat_time, last_discord_update_time
            while proc.returncode is None:
                await asyncio.sleep(2.0)
                if getattr(session, "is_stopped", False):
                    cls.kill_descendants(proc.pid)
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break
                now = time.time()
                idle_sec = now - last_activity_time
                total_elapsed = int(now - turn_start_time)
                time_since_discord = now - last_discord_update_time

                # Active Discord Progress Heartbeat (every 8 seconds of active or thinking time)
                if time_since_discord >= 8.0:
                    last_discord_update_time = now
                    actions_str = ", ".join(inspected_items[-3:]) if inspected_items else "초기 정적 분석"
                    if is_tool_running:
                        tool_elapsed = int(now - tool_start_time) if tool_start_time > 0 else int(idle_sec)
                        desc_header = f"{current_tool_desc}\n" if current_tool_desc else ""
                        subagent_badge = " [서브에이전트 심층 작업 중 🤖]" if is_subagent_running else ""
                        await safe_on_tool_update(
                            on_tool_update,
                            f"{desc_header}⏳ **[도구 실행 중 ({tool_elapsed}s / 총 {total_elapsed}s | {step_count}단계){subagent_badge}]**\n"
                            f"📁 **검증 항목**: `{actions_str}`"
                        )
                    else:
                        await safe_on_tool_update(
                            on_tool_update,
                            f"🧠 **[심층 추론 및 정밀 검증 분석 중 ({total_elapsed}s | {step_count}단계)]**\n"
                            f"📁 **확인된 항목**: `{actions_str}`\n"
                            f"⚡ 논리 무결성 전수 대조 및 최종 판정 리포트 생성 중... 🚀"
                        )

                # Level 1: Tool execution timeout (sweeping hanging child processes after WATCHDOG_TOOL_TIMEOUT)
                # Subagent execution receives generous time (up to 2x) without premature sweeping
                effective_tool_timeout = (WATCHDOG_TOOL_TIMEOUT * 2) if is_subagent_running else WATCHDOG_TOOL_TIMEOUT
                stall_duration = idle_sec if is_tool_running else idle_sec
                if is_tool_running and stall_duration > effective_tool_timeout and not child_swept:
                    child_pids = cls.get_descendant_pids(proc.pid)
                    logger.warning(f"Watchdog: Tool stalled for {stall_duration:.1f}s (> {effective_tool_timeout}s). Sweeping child processes: {child_pids}")
                    child_swept = True
                    cls.kill_descendants(proc.pid)
                    await safe_on_tool_update(
                        on_tool_update,
                        f"⚠️ **[자동 교착 해제] 도구 실행 지연({effective_tool_timeout}초) 감지 ➔ 하위 프로세스 강제 회수 및 진행 재개 중...**"
                    )

                # Level 1.5: If tool remains stalled even after sweeping child processes for an extra 120s, abort process to trigger self-healing
                if is_tool_running and stall_duration > (effective_tool_timeout + 120.0):
                    logger.error(f"Watchdog: Tool completely deadlocked for {stall_duration:.1f}s. Terminating process for fast self-healing...")
                    cls.kill_descendants(proc.pid)
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        pass
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    last_error_messages.append(
                        f"⚠️ **[도구 실행 교착(Deadlock) 자동 회수]** 도구 실행이 {int(stall_duration)}초간 무응답 상태여서 작업을 안전하게 강제 회수하고 도구 안전 모드로 자가 치유합니다."
                    )
                    break

                # Level 2: Complete silence timeout (WATCHDOG_SILENCE_TIMEOUT default 15 minutes for thinking models)
                if idle_sec > WATCHDOG_SILENCE_TIMEOUT:
                    logger.error(f"Watchdog: Process completely frozen for {idle_sec:.1f}s (> {WATCHDOG_SILENCE_TIMEOUT}s). Terminating process tree...")
                    cls.kill_descendants(proc.pid)
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        pass
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    last_error_messages.append(
                        f"⚠️ **[무응답 교착(Deadlock) 자동 회수]** {WATCHDOG_SILENCE_TIMEOUT // 60}분간 새로운 응답이 없어 작업을 안전하게 강제 회수하고 자가 치유로 전환합니다."
                    )
                    break

        watchdog_task = asyncio.create_task(watchdog())

        try:
            while True:
                line = await proc.stdout.readline()
                if not line or getattr(session, "is_stopped", False):
                    if getattr(session, "is_stopped", False):
                        cls.kill_descendants(proc.pid)
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

                last_activity_time = time.time()
                child_swept = False  # Reset child sweep flag on new output

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    event_data = json.loads(line_str)
                    event_type = event_data.get("event")

                    if event_type == "init":
                        conv_id = event_data.get("conversation_id")
                        if conv_id:
                            if conversation_id_attr:
                                setattr(session, conversation_id_attr, conv_id)
                            else:
                                session.set_conversation_id(conv_id)

                    elif event_type == "step_update":
                        step = event_data.get("step_update", {})
                        step_type = step.get("step_type")
                        state = step.get("state")

                        # Track tool running state for watchdog
                        if step_type == "tool":
                            tool_name = step.get("tool_name", "")
                            tool_info = step.get("tool_info", {})
                            if state == "ACTIVE":
                                is_tool_running = True
                                is_subagent_running = bool(tool_name == "invoke_subagent" or "subagent" in (tool_name or "").lower())
                                if is_subagent_running:
                                    has_subagents_active = True
                                step_count += 1
                                tool_start_time = time.time()
                                current_tool_desc = format_tool_status(tool_name, tool_info)
                                params = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}
                                raw_target = params.get("TargetFile") or params.get("AbsolutePath") or params.get("Query") or params.get("CommandLine") or ""
                                if raw_target:
                                    clean_target = os.path.basename(raw_target) if ("/" in raw_target or "\\" in raw_target) else raw_target[:30]
                                    if clean_target and clean_target not in inspected_items:
                                        inspected_items.append(clean_target)
                                last_discord_update_time = time.time()
                                await safe_on_tool_update(on_tool_update, current_tool_desc)
                            elif state in ("DONE", "ERROR", "CANCELED"):
                                is_tool_running = False
                                is_subagent_running = False
                                tool_cost_sec = int(time.time() - tool_start_time) if tool_start_time > 0 else 0
                                tool_start_time = 0.0
                                last_discord_update_time = time.time()
                                if state == "DONE":
                                    if tool_cost_sec >= 1:
                                        actions_str = ", ".join(inspected_items[-2:]) if inspected_items else ""
                                        tag_info = f" ({actions_str})" if actions_str else ""
                                        await safe_on_tool_update(
                                            on_tool_update,
                                            f"🧠 **[도구 완료 ({tool_cost_sec}s){tag_info} ➔ 정밀 분석 & 추론 중...]**"
                                        )
                                else:
                                    await safe_on_tool_update(
                                        on_tool_update,
                                        f"⚠️ **[도구 {state}]** 다음 단계로 안전하게 전환하여 분석을 계속합니다..."
                                    )

                        # Check if text delta or content is provided
                        delta = step.get("text_delta") or step.get("delta")
                        if delta:
                            accumulated_response += delta

                        # Check if step has error
                        if step.get("error"):
                            last_error_messages.append(str(step.get("error")))

                    elif event_type == "result":
                        is_tool_running = False
                        result_obj = event_data.get("result", {})
                        if "response" in result_obj:
                            accumulated_response = result_obj["response"]
                        elif "content" in result_obj:
                            accumulated_response = result_obj["content"]
                        elif "text" in result_obj:
                            accumulated_response = result_obj["text"]

                        conv_id = result_obj.get("conversation_id")
                        if conv_id:
                            if conversation_id_attr:
                                setattr(session, conversation_id_attr, conv_id)
                            else:
                                session.set_conversation_id(conv_id)

                        if result_obj.get("error"):
                            last_error_messages.append(str(result_obj.get("error")))

                    elif event_type == "error":
                        is_tool_running = False
                        err_text = event_data.get("error") or event_data.get("message") or json.dumps(event_data, ensure_ascii=False)
                        last_error_messages.append(str(err_text))

                except json.JSONDecodeError:
                    # Collect non-JSON stdout lines (e.g. panic or warning messages)
                    non_json_outputs.append(line_str)
                    if len(non_json_outputs) > 30:
                        non_json_outputs.pop(0)

        finally:
            watchdog_task.cancel()
            # Give child/subagent processes up to 60s (or 15s standard) to finish naturally before force-killing
            grace = 60.0 if has_subagents_active else 15.0
            await cls._wait_for_descendants_grace(proc.pid, grace_seconds=grace)

        await proc.wait()
        await stderr_task

        all_error_lines = list(last_error_messages) + list(stderr_lines) + list(non_json_outputs)
        return proc.returncode, accumulated_response, last_error_messages, all_error_lines

    @classmethod
    async def run_prompt(
        cls,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None,
        conversation_id_attr: Optional[str] = None,
        engine: Optional[str] = None,
        role: Optional[str] = None
    ) -> str:
        """
        Executes AGY CLI in stream-json mode and returns the final text response.
        Calls on_tool_update whenever a tool execution starts or updates.
        Includes autonomous in-flight self-healing retry on tool argument/format crashes.
        """
        if getattr(session, "is_stopped", False):
            session.is_busy = False
            return "🛑 **작업이 중단(취소)되었습니다.**"
        session.is_busy = True
        raw_engine = engine or getattr(session, "engine", "agy") or "agy"
        from engine_manager import normalize_engine_id, EngineManager
        engine_id = normalize_engine_id(raw_engine)

        
        if engine_id != "agy":
            try:
                adapter = EngineManager.get_adapter(engine_id)
                if not adapter:
                    return f"⚠️ 알 수 없는 AI 엔진입니다: `{engine_id}`\n`!engine` 명령어로 사용 가능한 엔진을 확인하세요."
                session_role = (role or getattr(session, "role", None) or "coder").lower().strip()
                is_review_role = (
                    session_role.startswith("qa")
                    or session_role in ("reviewer", "qa_bot", "auditor", "single_qa2")
                    or getattr(session, "mode", "") in ("pair_plus", "single_qa2")
                    or (model and "solar" in model.lower())
                )
                if not adapter.is_installed():
                    # Fallback to AGY with Gemini fallback model to protect continuity
                    if is_review_role:
                        fallback_model = getattr(config, "QA_FALLBACK_MODEL", "gemini-3.7-flash-high")
                        if not fallback_model.startswith("gemini-"):
                            fallback_model = "gemini-3.7-flash-high"
                        logger.warning(f"Engine '{engine_id}' not installed for role '{session_role}'. Falling back to AGY ({fallback_model}).")
                        returncode, accumulated_response, last_errors, all_errors = await cls._execute_single_run(
                            session=session,
                            prompt=prompt,
                            on_tool_update=on_tool_update,
                            model=fallback_model,
                            conversation_id_attr=conversation_id_attr
                        )
                        fallback_prefix = (
                            f"⚠️ **[독립 QA 엔진 대체 검증 안내]** `{engine_id}` 엔진 연결 제한으로 인해 AGY(`{fallback_model}`) 모델로 대체 교차 검증을 수행했습니다.\n\n"
                        )
                        return (fallback_prefix + (accumulated_response or "")) if accumulated_response else f"{fallback_prefix}⚠️ 대체 검증 중 유효한 응답 본문이 생성되지 않았습니다."
                    return (
                        f"⚠️ **[{adapter.icon} {adapter.name}] CLI가 시스템에 설치되어 있지 않습니다.**\n\n"
                        f"• 검색된 경로: `{adapter.get_binary_path() or '없음'}`\n"
                        f"• 설치 명령어: `{adapter.install_command}`\n"
                        f"• 인증 규격: `{adapter.auth_info}`\n\n"
                        f"💡 `!engine agy` 명령어로 기본 Antigravity 엔진으로 전환할 수 있습니다."
                    )
                if session_role in ("qa2", "single_qa2") or getattr(session, "mode", "") in ("pair_plus", "single_qa2"):
                    effective_model = model or getattr(session, "qa2_model", None) or getattr(config, "QA2_MODEL", "upstage/solar-pro4")
                elif session_role == "qa":
                    effective_model = model or getattr(session, "qa_model", None) or getattr(session, "engine_model", None)
                elif session_role.startswith("qa"):
                    effective_model = model or getattr(session, "engine_model", None)
                elif session_role == "consultant" or getattr(session, "mode", "") == "single_plus":
                    effective_model = model or getattr(session, "consultant_model", None) or getattr(config, "CONSULTANT_MODEL", "openai/gpt-6-astra")
                else:
                    effective_model = model or getattr(session, "engine_model", None)
                code, resp, errors = await adapter.execute(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=effective_model,
                    role=session_role,
                    conversation_id_attr=conversation_id_attr,
                )
                combined_check_text = ((resp or "") + "\n" + "\n".join(errors or [])).lower()
                is_invalid_tool_error = bool(
                    combined_check_text and any(marker in combined_check_text for marker in (
                        "invalid tool call",
                        "unknown tool 'terminal'",
                        "unknown tool",
                        "model generated invalid tool call",
                        "invalid_tool_call",
                        "terminal tool is disabled",
                    ))
                )
                has_execution_failure = (code != 0 and not resp) or is_invalid_tool_error

                if has_execution_failure:
                    if is_review_role:
                        if model and not (model.startswith("openrouter/") or any(k in model.lower() for k in ("claude", "sonnet", "opus", "qwen", "deepseek", "llama", "solar", "ling", "minimax"))):
                            fallback_model = model
                        else:
                            fallback_model = getattr(config, "QA_FALLBACK_MODEL", "gemini-3.7-flash-high")

                        failure_desc = f"도구 호출 제한/오류('{resp.strip()[:60]}')" if is_invalid_tool_error else f"일시적 실행 오류(종료 코드 {code})"
                        logger.warning(
                            f"Engine '{engine_id}' execution failed ({failure_desc}) during {session_role} turn. "
                            f"Falling back gracefully to AGY ({fallback_model})."
                        )
                        await safe_tool_notify(
                            on_tool_update,
                            f"⚠️ **[{adapter.icon} {adapter.name}] {failure_desc} 발생 -> 🤖 [AGY {fallback_model}]로 즉시 안전 전환하여 검증을 지속합니다.**"
                        )
                        returncode, accumulated_response, last_errors, all_errors = await cls._execute_single_run(
                            session=session,
                            prompt=prompt,
                            on_tool_update=on_tool_update,
                            model=fallback_model,
                            conversation_id_attr=conversation_id_attr
                        )
                        return accumulated_response if (accumulated_response and accumulated_response.strip()) else "⚠️ 대체 검증 중 유효한 응답 본문이 생성되지 않았습니다."

                    err_summary = resp if is_invalid_tool_error else ("\n".join(errors[:10]) if errors else f"Exit code {code}")
                    return (
                        f"⚠️ **[{adapter.icon} {adapter.name}] 실행 중 오류 발생 (종료 코드 {code})**\n```\n{err_summary}\n```\n"
                        f"💡 인증 환경변수나 터미널 로그인 상태(`{adapter.auth_info}`)를 점검해주세요."
                    )
                return resp if (resp and resp.strip()) else "⚠️ **[비-AGY 엔진 실행 오류]** 유효한 응답 본문이 생성되지 않았습니다."
            except asyncio.CancelledError:
                if session.current_process:
                    try:
                        session.current_process.kill()
                    except Exception:
                        pass
                raise
            except Exception as e:
                logger.exception(f"Error executing non-AGY engine '{engine_id}'")
                return f"⚠️ **[{engine_id} 실행 오류]** {str(e)}"
            finally:
                session.is_busy = False
                session.current_process = None

        try:
            returncode, accumulated_response, last_errors, all_errors = await cls._execute_single_run(
                session=session,
                prompt=prompt,
                on_tool_update=on_tool_update,
                model=model,
                conversation_id_attr=conversation_id_attr
            )

            # Check if stopped by user during execution
            if getattr(session, "is_stopped", False):
                session.is_busy = False
                return "🛑 **작업이 중단(취소)되었습니다.**"

            # Check for fatal tool errors in logs even if returncode was 0
            has_fatal_tool_error = any(
                any(kw in str(err).lower() for kw in ("tool_error", "missing property", "invalid arguments", "invalid tool call"))
                for err in (last_errors + all_errors)
            )

            # Check for premature async exit (e.g. "I am uploading in background / will notify when finished")
            resp_lower = accumulated_response.lower() if accumulated_response else ""
            is_premature_async_exit = (
                any(kw in resp_lower for kw in [
                    "진행하고 있습니다", "진행 중입니다", "진행하고 있", "완료되면 공유 링크", "완료되면 안내",
                    "will notify you once", "awaiting notification", "awaiting completion",
                    "started uploading", "uploading to google drive", "in the background"
                ])
                and not any(link_kw in resp_lower for link_kw in ["drive.google.com", "http://", "https://", "다운로드 링크", "업로드 완료", "빌드 완료"])
            )

            # If agent gave an unfinished notice, auto-complete deliverable upload and attach link!
            if returncode == 0 and accumulated_response and is_premature_async_exit and session.workspace:
                try:
                    from gdrive_uploader import GoogleDriveUploader
                    apk_files = []
                    for root, _, files in os.walk(session.workspace):
                        for f in files:
                            if f.endswith((".apk", ".aab", ".zip")) and not f.startswith("."):
                                p = os.path.join(root, f)
                                apk_files.append((p, os.path.getmtime(p)))
                    if apk_files:
                        apk_files.sort(key=lambda x: x[1], reverse=True)
                        target_deliverable = apk_files[0][0]
                        upload_res = await asyncio.wait_for(
                            GoogleDriveUploader.upload_file(target_deliverable),
                            timeout=60.0
                        )
                        if upload_res.get("success") and upload_res.get("link"):
                            link = upload_res["link"]
                            fname = os.path.basename(target_deliverable)
                            accumulated_response += (
                                f"\n\n📦 **[Google Drive 업로드 완료]**\n"
                                f"• 파일명: `{fname}`\n"
                                f"• 공유 다운로드 링크: {link}"
                            )
                except Exception as g_err:
                    logger.debug(f"Premature async exit auto-resolution notice: {g_err}")

            if returncode == 0 and accumulated_response and accumulated_response.strip() and not has_fatal_tool_error:
                return cleanse_agent_output(accumulated_response)

            err_msg = "\n".join(all_errors).strip()

            # Check if canceled
            if "context canceled" in err_msg.lower() or getattr(session, "is_stopped", False):
                session.is_busy = False
                return "🛑 **작업이 중단(취소)되었습니다.**"

            # -------------------------------------------------------------
            # Autonomous In-Flight Self-Healing: Quota Exhaustion / ArtifactMetadata / Tool Schema Error / Empty Exit / Deadlock / Error Code 1
            # -------------------------------------------------------------
            is_quota_error = any(kw in err_msg.lower() for kw in [
                "individual quota reached",
                "quota reached",
                "quota exceeded",
                "resource exhausted",
                "rate limit exceeded",
                "model is exhausted"
            ])
            # [Self-Healing] Transient server overload / 503 capacity errors (NOT permanent quota exhaustion)
            is_server_overload = not is_quota_error and any(kw in err_msg.lower() for kw in [
                "high traffic",
                "no capacity available",
                "unavailable (code 503)",
                "code 503",
                "503 service unavailable",
                "503: no capacity",
                "try again in a minute",
                "server is overloaded",
                "temporarily unavailable",
            ])
            is_artifact_error = (
                "is not a valid artifact path" in err_msg
                or "ArtifactMetadata" in err_msg
                or "invalid tool call error" in err_msg
            )
            is_tool_schema_error = has_fatal_tool_error or any(kw in err_msg.lower() for kw in [
                "missing property 'pattern'",
                "missing property",
                "invalid arguments",
                "tool_error",
                "unknown tool",
            ])
            is_empty_premature_exit = (
                not getattr(session, "is_stopped", False)
                and (not accumulated_response or not accumulated_response.strip())
            )
            is_deadlock_or_crash = (
                not getattr(session, "is_stopped", False)
                and (
                    "무응답 교착" in err_msg
                    or "Deadlock" in err_msg
                    or returncode in (-9, 137)
                )
            )
            is_execution_error = returncode != 0 and not getattr(session, "is_stopped", False)

            if is_quota_error:
                current_model = (model or "default").strip()
                try:
                    from trio_agent_runner import QuotaManager
                    QuotaManager.mark_model_exhausted(current_model, duration_sec=3600.0)
                except Exception:
                    pass

                # Build healthy fallback candidates
                fallback_candidates = [
                    "gemini-3.8-flash-high",
                    "gemini-3.7-flash-high",
                    "gemini-3.1-pro-high",
                    "gemini-3-flash"
                ]
                try:
                    from trio_agent_runner import QuotaManager
                    fallback_candidates = [c for c in fallback_candidates if not QuotaManager.is_model_exhausted(c)]
                except Exception:
                    pass
                if not fallback_candidates:
                    fallback_candidates = ["gemini-3.8-flash-high", "gemini-3.7-flash-high", "gemini-3-flash"]

                for fb_idx, fallback_model in enumerate(fallback_candidates):
                    logger.warning(f"Detected quota exhaustion for '{current_model}'. Smart in-flight switching to fallback candidate {fb_idx+1}/{len(fallback_candidates)}: '{fallback_model}'...")
                    await safe_on_tool_update(
                        on_tool_update,
                        f"🔄 **[스마트 쿼터 자동 우회]** `{current_model}` 할당량 소진 감지 ➔ 백업 모델(`{fallback_model}`)로 즉시 자동 전환하여 작업을 지속합니다! 🚀"
                    )

                    if conversation_id_attr:
                        setattr(session, conversation_id_attr, None)
                    else:
                        session.conversation_id = None

                    fallback_code, fallback_resp, _, fallback_errors = await cls._execute_single_run(
                        session=session,
                        prompt=prompt,
                        on_tool_update=on_tool_update,
                        model=fallback_model,
                        conversation_id_override=None,
                        conversation_id_attr=conversation_id_attr
                    )

                    if fallback_code == 0 and fallback_resp:
                        logger.info(f"In-flight quota fallback succeeded with {fallback_model}!")
                        return fallback_resp

                    err_msg = "\n".join(fallback_errors or all_errors).strip()
                    returncode = fallback_code if fallback_code != 0 else returncode
                    # If this fallback also failed with quota error, mark it and try next candidate
                    if any(kw in err_msg.lower() for kw in ["quota", "exhausted", "limit"]):
                        try:
                            from trio_agent_runner import QuotaManager
                            QuotaManager.mark_model_exhausted(fallback_model)
                        except Exception:
                            pass
                        continue
                    else:
                        break

            elif is_server_overload and not getattr(session, "is_stopped", False):
                # [Self-Healing] Transient 503 / capacity overload: retry with exponential backoff, then model fallback
                current_model = (model or "default").strip()
                overload_retry_delays = [30, 60, 90]  # seconds
                overload_recovered = False

                for attempt_idx, delay_sec in enumerate(overload_retry_delays):
                    if getattr(session, "is_stopped", False):
                        session.is_busy = False
                        return "🛑 **작업이 중단(취소)되었습니다.**"

                    logger.warning(
                        f"[503 Overload Recovery] Model '{current_model}' server overload. "
                        f"Retry attempt {attempt_idx + 1}/{len(overload_retry_delays)} after {delay_sec}s backoff..."
                    )
                    await safe_on_tool_update(
                        on_tool_update,
                        f"⏳ **[서버 과부하 자동 복구]** `{current_model}` 서버 일시 과부하(503) 감지 ➔ "
                        f"{delay_sec}초 대기 후 재시도합니다 ({attempt_idx + 1}/{len(overload_retry_delays)})..."
                    )
                    await asyncio.sleep(delay_sec)

                    if getattr(session, "is_stopped", False):
                        session.is_busy = False
                        return "🛑 **작업이 중단(취소)되었습니다.**"

                    ol_code, ol_resp, _, ol_errors = await cls._execute_single_run(
                        session=session,
                        prompt=prompt,
                        on_tool_update=on_tool_update,
                        model=model,
                        conversation_id_attr=conversation_id_attr
                    )

                    if ol_code == 0 and ol_resp and ol_resp.strip():
                        logger.info(f"[503 Overload Recovery] Retry succeeded after {delay_sec}s backoff (attempt {attempt_idx + 1})!")
                        return ol_resp

                    ol_err = "\n".join(ol_errors).strip()
                    # If still 503/overload, continue to next backoff
                    still_overloaded = any(kw in ol_err.lower() for kw in [
                        "high traffic", "no capacity", "code 503", "unavailable", "try again"
                    ])
                    if not still_overloaded:
                        # Different error type now - break out and let other handlers process
                        break

                if not overload_recovered:
                    # All backoff retries exhausted or different error - fall through to Gemini model fallback
                    logger.warning(f"[503 Overload Recovery] All backoff retries exhausted for '{current_model}'. Switching to Gemini fallback model...")
                    fallback_candidates = [
                        "gemini-3.8-flash-high",
                        "gemini-3.7-flash-high",
                        "gemini-3.1-pro-high",
                        "gemini-3-flash"
                    ]
                    try:
                        from trio_agent_runner import QuotaManager
                        fallback_candidates = [c for c in fallback_candidates if not QuotaManager.is_model_exhausted(c)]
                    except Exception:
                        pass
                    if not fallback_candidates:
                        fallback_candidates = ["gemini-3.8-flash-high", "gemini-3-flash"]

                    for fb_model in fallback_candidates:
                        await safe_on_tool_update(
                            on_tool_update,
                            f"🔄 **[서버 과부하 모델 전환]** `{current_model}` 지속 과부하 ➔ 백업 모델(`{fb_model}`)로 자동 전환합니다."
                        )
                        if conversation_id_attr:
                            setattr(session, conversation_id_attr, None)
                        else:
                            session.conversation_id = None

                        fb_code, fb_resp, _, fb_errors = await cls._execute_single_run(
                            session=session,
                            prompt=prompt,
                            on_tool_update=on_tool_update,
                            model=fb_model,
                            conversation_id_override=None,
                            conversation_id_attr=conversation_id_attr
                        )

                        if fb_code == 0 and fb_resp and fb_resp.strip():
                            logger.info(f"[503 Overload Recovery] Fallback to '{fb_model}' succeeded!")
                            return fb_resp

            elif (is_artifact_error or is_tool_schema_error or is_empty_premature_exit or is_deadlock_or_crash or is_execution_error) and not getattr(session, "is_stopped", False):
                if is_artifact_error:
                    logger.warning("Detected tool call error (ArtifactMetadata / invalid artifact path). Performing in-flight self-recovery retry...")
                    await safe_on_tool_update(on_tool_update, "🛠️ **[자가 치유] 도구 호출 형식 오류 자동 감지 ➔ 도구 안전 모드로 즉시 재시도 중...**")

                    recovery_prompt = (
                        "[CRITICAL RECOVERY INSTRUCTION - PREVIOUS TOOL CALL CRASHED]\n"
                        "Your previous tool call failed with 'invalid tool call error (invalid_args): not a valid artifact path'.\n"
                        "Reason: `ArtifactMetadata` was passed for a workspace project file. In AGY, `ArtifactMetadata` is ONLY for internal brain/ artifacts.\n"
                        "MANDATORY FIX FOR THIS TURN:\n"
                        "1. When calling `write_to_file` on ANY workspace project file (e.g. *.html, *.css, *.js, *.py), DO NOT include the `ArtifactMetadata` parameter at all!\n"
                        "2. To modify existing files (e.g. `styles.css`), ALWAYS prefer `replace_file_content`.\n\n"
                        f"Now, please resume and execute the user's requested task cleanly:\n{prompt}"
                    )
                elif is_tool_schema_error or is_empty_premature_exit:
                    detected_reason = "도구 파라미터 스키마 오류(예: find_by_name의 Pattern 누락)" if is_tool_schema_error else "본문 산출물 없이 빈 문자열 조기 종료"
                    logger.warning(f"Detected {detected_reason} (err: {err_msg[:120]}). Performing in-flight self-recovery retry...")
                    await safe_on_tool_update(on_tool_update, f"🛠️ **[자가 치유] {detected_reason} 감지 ➔ 안전 도구 규격으로 즉시 재시도 중...**")

                    recovery_prompt = (
                        "[CRITICAL RECOVERY INSTRUCTION - PREVIOUS TOOL CALL OR TURN TERMINATED PREMATURELY]\n"
                        f"Your previous turn terminated prematurely with an empty response or tool schema error.\n"
                        f"Detected detail: {err_msg[:300] if err_msg else 'Agent process completed without writing a full solution/response.'}\n\n"
                        "MANDATORY RULES & FIXES FOR THIS TURN:\n"
                        "1. `find_by_name`: ALWAYS supply the `Pattern` parameter (e.g. `Pattern: \"*\"` or `Pattern: \"*.py\"`), even if `Extensions` is specified! Omitting `Pattern` crashes AGY schema validation with 'missing property Pattern'!\n"
                        "2. `write_to_file`: NEVER include `ArtifactMetadata` on workspace files.\n"
                        "3. `replace_file_content`: ALWAYS use this tool to modify existing files.\n"
                        "4. YOU MUST SUBSTANTIVELY EXECUTE AND ANSWER: Do NOT stop after just 1 tool call. Inspect files, perform the code changes or analysis, and produce a complete, thorough final report answering the user's request.\n\n"
                        f"User's task:\n{prompt}"
                    )
                elif is_deadlock_or_crash:
                    logger.warning("Detected deadlock/timeout. Performing in-flight self-recovery retry with modular partition mode...")
                    await safe_on_tool_update(on_tool_update, "🛠️ **[자가 치유] 작업 지연/교착 자동 감지 ➔ 단계별 모듈 분할 모드로 즉시 재시도 중...**")

                    recovery_prompt = (
                        "[CRITICAL RECOVERY INSTRUCTION - TIMEOUT & DEADLOCK PREVENTATIVE MODE]\n"
                        "Your previous turn timed out or stalled during tool execution.\n"
                        "MANDATORY INSTRUCTIONS FOR THIS TURN:\n"
                        "1. DO NOT run heavy background build commands (e.g. `./gradlew assembleDebug`, full compilation builds). Inspect files directly using `view_file` and `grep_search`.\n"
                        "2. Break down the task into smaller, modular, step-by-step actions. Do not attempt to output massive monolithic codebases in a single step.\n"
                        "3. When writing or modifying files, create the most essential core files first using `replace_file_content`.\n"
                        "4. When running tests or quick commands, ensure strict timeouts (e.g. timeout: 5000) and verify processes terminate quickly.\n"
                        "5. Do NOT include ArtifactMetadata parameter on write_to_file calls.\n\n"
                        f"Now, please resume and execute the user's requested task cleanly:\n{prompt}"
                    )
                else:
                    logger.warning(f"Detected process execution error (code {returncode}). Performing in-flight self-recovery retry...")
                    await safe_on_tool_update(on_tool_update, "🛠️ **[자가 치유] 프로세스 일시 오류 자동 감지 ➔ 안전 세션 모드로 즉시 재시도 중...**")

                    recovery_prompt = (
                        "[CRITICAL RECOVERY INSTRUCTION - CLEAN RETRY MODE]\n"
                        f"Your previous turn terminated with exit code {returncode} (error: {err_msg[:300]}).\n"
                        "MANDATORY INSTRUCTIONS FOR THIS TURN:\n"
                        "1. Resume and complete the requested task cleanly from where it stopped.\n"
                        "2. When creating workspace project files, OMIT ArtifactMetadata parameter on write_to_file.\n"
                        "3. When modifying existing files, use replace_file_content.\n\n"
                        f"User's task:\n{prompt}"
                    )

                # Ensure retry model is healthy and not exhausted
                safe_retry_model = model
                try:
                    from trio_agent_runner import QuotaManager
                    if not safe_retry_model or QuotaManager.is_model_exhausted(safe_retry_model):
                        safe_retry_model = "gemini-3.8-flash-high"
                        await safe_on_tool_update(
                            on_tool_update,
                            f"🔄 **[자동 복구 모델 전환]** 원본 모델(`{model or 'default'}`) 일시 소진/비응답 → 복구 재시도에 `{safe_retry_model}` 사용 중"
                        )
                except Exception:
                    if not safe_retry_model:
                        safe_retry_model = "gemini-3.8-flash-high"

                if getattr(session, "is_stopped", False):
                    session.is_busy = False
                    return "🛑 **작업이 중단(취소)되었습니다.**"

                # Attempt 1: Continue conversation with recovery prompt
                retry_code, retry_resp, _, retry_errors = await cls._execute_single_run(
                    session=session,
                    prompt=recovery_prompt,
                    on_tool_update=on_tool_update,
                    model=safe_retry_model,
                    conversation_id_attr=conversation_id_attr
                )

                if retry_code == 0 and retry_resp and retry_resp.strip():
                    logger.info("In-flight self-recovery retry succeeded with existing session!")
                    return retry_resp

                if getattr(session, "is_stopped", False):
                    session.is_busy = False
                    return "🛑 **작업이 중단(취소)되었습니다.**"

                # Attempt 2: If session context was corrupted, try with a fresh conversation session
                logger.warning("Retry 1 failed. Attempting fresh conversation session fallback...")
                await safe_on_tool_update(on_tool_update, "🔄 **[자가 치유] 세션 상태 복구 후 최종 재시도 중...**")

                if conversation_id_attr:
                    setattr(session, conversation_id_attr, None)
                else:
                    session.conversation_id = None

                fresh_code, fresh_resp, _, fresh_errors = await cls._execute_single_run(
                    session=session,
                    prompt=recovery_prompt,
                    on_tool_update=on_tool_update,
                    model=safe_retry_model,
                    conversation_id_override=None,
                    conversation_id_attr=conversation_id_attr
                )

                if fresh_code == 0 and fresh_resp and fresh_resp.strip():
                    logger.info("In-flight self-recovery retry succeeded with fresh session!")
                    return fresh_resp

                err_msg = "\n".join(fresh_errors or retry_errors or all_errors).strip()
                returncode = fresh_code if fresh_code != 0 else (retry_code if retry_code != 0 else returncode)

            if returncode != 0:
                if not err_msg:
                    err_msg = (
                        "AGY 프로세스가 비정상 종료되었습니다.\n"
                        "💡 이전 대화 컨텍스트가 너무 크거나 누적된 상태일 수 있습니다.\n"
                        "`!new` 명령어로 세션을 초기화한 후 다시 시도해보세요."
                    )
                elif "is not a valid artifact path" in err_msg:
                    err_msg += (
                        "\n\n💡 [진단] 일반 프로젝트 파일 생성 시 `ArtifactMetadata`가 포함되어 발생한 오류입니다.\n"
                        "`GEMINI.md`의 Tool Usage Safety 지침이 반영되어 다음 실행부터 방지됩니다."
                    )
                final_error_text = f"⚠️ 실행 중 오류가 발생했습니다 (코드 {returncode}):\n```\n{err_msg}\n```"
                return f"{accumulated_response}\n\n{final_error_text}" if accumulated_response else final_error_text

            if not accumulated_response or not accumulated_response.strip():
                err_detail = err_msg if err_msg else "에이전트가 도구 인자 오류 또는 조기 종료로 본문 텍스트 없이 프로세스를 마쳤습니다."
                return (
                    f"⚠️ **[에이전트 조기 종료 오류]** 요청하신 작업이 온전히 처리되지 못하고 도중 중단되었습니다.\n"
                    f"• 진단: `{err_detail[:250]}`\n"
                    f"💡 세션 컨텍스트 초기화(`!new` 또는 `!reset`) 후 다시 시도해 주십시오."
                )

            return cleanse_agent_output(accumulated_response)

        except asyncio.CancelledError:
            if session.current_process:
                try:
                    session.current_process.kill()
                except Exception:
                    pass
            raise
        except Exception as e:
            logger.exception("AgentRunner 실행 중 예외 발생")
            return f"⚠️ 오류 발생: {str(e)}"
        finally:
            if not getattr(session, "_in_multi_agent_pipeline", False):
                session.is_busy = False
            session.current_process = None
