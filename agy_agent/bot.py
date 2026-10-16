import os
import sys
import re
import time
import asyncio
import signal
import atexit
import importlib
import tempfile
import hashlib
import logging
import traceback
from typing import Any, Dict, Optional, Tuple
import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, Select
import config

from config import (
    DISCORD_BOT_TOKEN,
    DISCORD_COMMANDER_BOT_TOKEN,
    DISCORD_QA_BOT_TOKEN,
    DISCORD_AGY_QA2_BOT_TOKEN,
    DISCORD_AUDITOR_BOT_TOKEN,
    DISCORD_AGY_IMAGE_BOT_TOKEN,
    DISCORD_SERVER_MANAGER_BOT_TOKEN,
    DEFAULT_WORKSPACE,
    COMMAND_PREFIX,
    COMMAND_PREFIXES,
    CODER_MODEL,
    QA_MODEL,
    QA2_MODEL,
    AUDITOR_MODEL,
    IMAGE_MODEL,
    PAIR_MAX_ROUNDS,
    QA_MAX_ROUNDS,
    QA_HUB_CHANNEL_ID,
    COMMANDER_MODEL,
    FINISH_PROJECTS_CATEGORY_ID,
    QA_AUTO_MODEL,
)
from session_manager import SessionManager
from mode_classifier import classify_mode, get_mode_description, classify_task_type
from commander import (
    Commander,
    FEEDBACK_DEFECT_KEYWORDS,
    CRASH_STABILITY_KEYWORDS,
    EXPLICIT_NEW_PROJECT_KEYWORDS,
    GENERAL_CREATION_VERBS,
)
from feedback_classifier import FeedbackIntentClassifier
from agent_runner import AgentRunner
from trio_agent_runner import TrioAgentRunner, QuotaManager
import mode_registry
from research_runner import ResearchRunner
from consulting_runner import ConsultingRunner
import discord_utils
if not hasattr(discord_utils, "sanitize_attachment_filename"):
    import importlib
    discord_utils = importlib.reload(discord_utils)

from discord_utils import (
    async_prepare_message_payload,
    safe_send_discord_message,
    create_workspace_zip,
    export_channel_history,
    ThrottledStatusUpdater,
    sanitize_attachment_filename,
    safe_edit_view,
    _safe_edit_view
)


from evidence_validator import EvidenceValidator
try:
    from model_registry import AntigravityModelRegistry
except ImportError:
    from agy_agent.model_registry import AntigravityModelRegistry

# Track gateway boot time
BOT_BOOT_TIME = time.time()

def format_uptime(seconds: float) -> str:
    secs = int(seconds)
    days = secs // 86400
    hours = (secs % 86400) // 3600
    minutes = (secs % 3600) // 60
    rem_secs = secs % 60
    parts = []
    if days > 0:
        parts.append(f"{days}일")
    if hours > 0:
        parts.append(f"{hours}시간")
    if minutes > 0:
        parts.append(f"{minutes}분")
    parts.append(f"{rem_secs}초")
    return " ".join(parts)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("AgyDiscordBot")

# Intents configuration
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

# 0. Commander Bot (AI Intent Routing Dispatcher / Gemini 3.7 Flash Low)
commander_bot = discord.Client(intents=intents)
# 1. Main Bot (Coder / Primary)
bot = commands.Bot(command_prefix=COMMAND_PREFIXES, intents=intents, help_command=None, case_insensitive=True)
# 2. QA Bot (Code Reviewer / Test)
qa_bot = discord.Client(intents=intents)
# 2.5. QA2 Bot (OpenRouter / Upstage Solar Pro 4 / Cross-Verification)
qa2_bot = discord.Client(intents=intents)
# 3. Auditor Bot (Client Auditor / Acceptance)
auditor_bot = discord.Client(intents=intents)
# 4. Server Manager Bot (Infrastructure & Telemetry)
server_manager_intents = discord.Intents.default()
server_manager_bot = discord.Client(intents=server_manager_intents)
# 5. Image Generator Bot (실제 AI 이미지 생성 도구 봇 — Nano Banana 계열 Gemini image 백엔드)
# - 메시지 수신/명령 처리는 메인 봇이 담당하므로 최소 권한(guilds)만 사용하여
#   privileged message_content intent 요구로 인한 로그인 실패를 방지한다.
image_intents = discord.Intents.default()
image_bot = discord.Client(intents=image_intents)

# Dynamic Extended QA Bots Registry (QA2..QA10 plug-and-play)
extended_qa_bots: Dict[int, discord.Client] = {2: qa2_bot}

def init_extended_qa_bots():
    """Dynamically initializes discord clients for QA3..QA10 if bot tokens are configured, supporting runtime hot-plugging."""
    try:
        ext_qa = globals().get("extended_qa_bots")
        if not isinstance(ext_qa, dict):
            qa2_client = globals().get("qa2_bot")
            ext_qa = {2: qa2_client} if qa2_client else {}
            globals()["extended_qa_bots"] = ext_qa

        for cfg in config.get_extended_qa_configs():
            idx = cfg["index"]
            if idx == 2:
                continue
            token = cfg.get("token")
            if token:
                existing_client = ext_qa.get(idx)
                if existing_client is None or existing_client.is_closed():
                    qa_intents = discord.Intents.default()
                    qa_intents.guilds = True
                    client = discord.Client(intents=qa_intents)
                    ext_qa[idx] = client

                    def _bind_on_ready(c: discord.Client, c_name: str, c_model: str):
                        async def _on_ready():
                            logger.info(f"{c_name} Bot connected as: {c.user.name} ({c.user.id})")
                            try:
                                activity = discord.Activity(
                                    type=discord.ActivityType.watching,
                                    name=f"{c_name} 교차검증"
                                )
                                await c.change_presence(activity=activity)
                            except Exception:
                                pass
                            print(f"🕵️‍♂️ [{c_name}] AGY {c_name} Bot ({c_model}) 준비 완료: {c.user.name}")
                        return _on_ready

                    client.on_ready = _bind_on_ready(client, cfg["name"], cfg["model"])

                    # Hot-start client if runtime event loop is already active and token looks real (do not hot-start inside unit test runner)
                    is_test = "unittest" in sys.modules or bool(os.environ.get("UNITTEST_RUNNING"))
                    if not is_test and not token.startswith("mock") and not token.startswith("dummy") and len(token) > 20:
                        try:
                            loop = asyncio.get_running_loop()
                            if loop and loop.is_running():
                                logger.info(f"Hot-starting newly configured {cfg['name']} Bot client ({cfg['model']})...")
                                async def _safe_start(c: discord.Client, tok: str, name: str):
                                    try:
                                        await c.start(tok)
                                    except Exception as exc:
                                        logger.warning(f"[{name}] Bot connection notice: {exc}")
                                asyncio.create_task(_safe_start(client, token, cfg["name"]))
                        except RuntimeError:
                            pass
            elif not token and idx in ext_qa:
                old_client = ext_qa.pop(idx, None)
                if old_client and not old_client.is_closed():
                    try:
                        loop = asyncio.get_running_loop()
                        if loop and loop.is_running():
                            logger.info(f"Closing {cfg['name']} Bot client as token was removed from .env...")
                            asyncio.create_task(old_client.close())
                    except RuntimeError:
                        pass
    except Exception as e:
        logger.warning(f"Failed to dynamically initialize extended QA bot clients: {e}")

init_extended_qa_bots()

from qa_hub import QAHubManager
from thermal_guard import ThermalGuard


session_manager = SessionManager()
qa_hub_manager = QAHubManager(session_manager, qa_bot=qa_bot, auditor_bot=auditor_bot)

# Compatibility stubs for unit tests and legacy patches (decommissioned)
class _DormantStub:
    enabled = False
    admin_channel_id = 0
    def __getattr__(self, name): return lambda *a, **kw: None
    async def handle_error(self, *a, **kw): return False
    def get_state(self, *a, **kw): return self
    def cancel_timer(self, *a, **kw): pass
    def sync_to_session(self, *a, **kw): pass
    def on_task_completed(self, *a, **kw): pass

auto_pilot_manager = _DormantStub()
self_healing_manager = _DormantStub()

commander_bot_task = None

def ensure_commander_bot_started():
    global commander_bot_task
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
        return
    try:
        from config import DISCORD_COMMANDER_BOT_TOKEN
        if DISCORD_COMMANDER_BOT_TOKEN and not commander_bot.is_ready() and (commander_bot_task is None or commander_bot_task.done()):
            logger.info("Auto-starting Commander Bot client in background...")
            commander_bot_task = asyncio.create_task(commander_bot.start(DISCORD_COMMANDER_BOT_TOKEN))
    except Exception as e:
        logger.debug(f"ensure_commander_bot_started notice: {e}")
    # [🛡️ Quota Protection] Telemetry loop is started exclusively in on_ready/on_server_manager_ready.
    # Calling it here caused duplicate restarts on every !reset. Removed.

image_bot_task = None

def ensure_image_bot_started():
    global image_bot_task, image_bot
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
        return
    try:
        tok = getattr(config, "DISCORD_AGY_IMAGE_BOT_TOKEN", "")
        if tok and image_bot:
            if image_bot.is_closed():
                logger.info("Re-instantiating closed Image generator Bot client...")
                image_bot = discord.Client(intents=image_intents)
                image_bot.on_ready = on_image_ready
            if not image_bot.is_ready() and (image_bot_task is None or image_bot_task.done()):
                logger.info("Auto-starting Image generator Bot client in background...")
                image_bot_task = asyncio.create_task(image_bot.start(tok))
    except Exception as e:
        logger.debug(f"ensure_image_bot_started notice: {e}")

qa2_bot_task = None

def ensure_qa2_bot_started():
    global qa2_bot_task, qa2_bot
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
        return
    try:
        tok = getattr(config, "DISCORD_AGY_QA2_BOT_TOKEN", "")
        if tok and qa2_bot:
            if qa2_bot.is_closed():
                logger.info("Re-instantiating closed QA2 Bot client...")
                qa2_bot = discord.Client(intents=intents)
                qa2_bot.on_ready = on_qa2_ready
                if isinstance(globals().get("extended_qa_bots"), dict):
                    extended_qa_bots[2] = qa2_bot
            if not qa2_bot.is_ready() and (qa2_bot_task is None or qa2_bot_task.done()):
                logger.info("Auto-starting QA2 Bot client in background...")
                qa2_bot_task = asyncio.create_task(qa2_bot.start(tok))
    except Exception as e:
        logger.debug(f"ensure_qa2_bot_started notice: {e}")

auditor_bot_task = None

def ensure_auditor_bot_started():
    global auditor_bot_task, auditor_bot
    if os.environ.get("UNITTEST_RUNNING") == "1" or "pytest" in sys.modules:
        return
    try:
        tok = getattr(config, "DISCORD_AUDITOR_BOT_TOKEN", "")
        if tok and auditor_bot:
            if auditor_bot.is_closed():
                logger.info("Re-instantiating closed Auditor Bot client...")
                auditor_bot = discord.Client(intents=intents)
                auditor_bot.on_ready = on_auditor_ready
            if not auditor_bot.is_ready() and (auditor_bot_task is None or auditor_bot_task.done()):
                logger.info("Auto-starting Auditor Bot client in background...")
                auditor_bot_task = asyncio.create_task(auditor_bot.start(tok))
    except Exception as e:
        logger.debug(f"ensure_auditor_bot_started notice: {e}")

_in_reload_helper_modules = False

def reload_helper_modules():
    """Dynamically reloads helper modules so code changes in auto_pilot, trio_agent_runner, research_runner, qa_hub, etc. take effect without restarting the bot."""
    global _in_reload_helper_modules
    if _in_reload_helper_modules:
        return 0
    _in_reload_helper_modules = True
    try:
        return _reload_helper_modules_impl()
    finally:
        _in_reload_helper_modules = False

def _order_modules_for_reload(modules, current_dir):
    """[🔒 Hot-Reload Ordering] 로컬 모듈을 '의존성 우선(leaf-first)' 순서로 정렬한다.

    `from helper import X` 로 가져온 심볼은 import 시점의 객체에 바인딩되므로,
    importer 를 dependency 보다 먼저 리로드하면 옛 클래스/함수 객체가 그대로 남아
    `!reset` 후에도 변경 사항이 반영되지 않는다. AST 로 로컬 import 그래프를 만들어
    dependency 를 먼저 리로드하도록(위상 정렬) 보장한다.
    """
    import ast

    local = set(modules)
    deps = {m: set() for m in modules}

    def _collect_imports(statements):
        for stmt in statements:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    base = alias.name.split(".")[0]
                    if base in local and base != m:
                        deps[m].add(base)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.level == 0 and stmt.module:
                    base = stmt.module.split(".")[0]
                    if base in local and base != m:
                        deps[m].add(base)
            elif isinstance(stmt, (ast.If, ast.Try)):
                _collect_imports(stmt.body)
                _collect_imports(getattr(stmt, "orelse", []))
                for handler in getattr(stmt, "handlers", []):
                    _collect_imports(handler.body)
            # 함수/클래스 내부의 지연 import 는 호출 시점에 해석되므로 의존성에서 제외한다.

    for m in modules:
        path = os.path.join(current_dir, f"{m}.py")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            _collect_imports(tree.body)
        except Exception:
            continue

    ordered = []
    pending = list(modules)
    while pending:
        ready = [m for m in pending if deps[m].issubset(ordered)]
        if not ready:
            ordered.extend(pending)  # 순환 import 는 원래 순서대로 폴백
            break
        ordered.extend(ready)
        pending = [m for m in pending if m not in ready]
    return ordered


def _reload_helper_modules_impl():
    global qa_hub_manager, extended_qa_bots
    global TrioAgentRunner, QuotaManager, ResearchRunner, ConsultingRunner, AgentRunner
    global classify_mode, get_mode_description, classify_task_type
    global ThermalGuard, Commander
    global CODER_MODEL, QA_MODEL, AUDITOR_MODEL, COMMANDER_MODEL
    global EvidenceValidator

    # [⚡ Hot-Reload Cache Invalidation] 파이썬 바이트코드/파인더 캐시 무효화로 최신 파일 즉시 인식 보장
    try:
        importlib.invalidate_caches()
    except Exception as _inv_err:
        logger.debug(f"invalidate_caches notice: {_inv_err}")

    # Ensure extended_qa_bots registry exists in globals
    if not isinstance(globals().get("extended_qa_bots"), dict):
        qa2_client = globals().get("qa2_bot")
        extended_qa_bots = {2: qa2_client} if qa2_client else {}
        globals()["extended_qa_bots"] = extended_qa_bots
        logger.info("[Hot-Reload Fix] Initialized extended_qa_bots in globals().")

    if "init_extended_qa_bots" in globals() and callable(globals()["init_extended_qa_bots"]):
        try:
            init_extended_qa_bots()
        except Exception as _init_err:
            logger.debug(f"init_extended_qa_bots notice in reload: {_init_err}")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    priority_modules = [
        "config", "model_registry", "model_manager", "auto_scheduler", "session_manager", "mode_classifier", "hot_reload_bridge",
        "commander", "discord_utils", "gdrive_uploader", "android_build_manager",
        "audio_transcriber", "android_vision_inspector", "agent_guidelines", "agent_runner",
        "trio_agent_runner", "research_runner", "consulting_runner",
        "qa_hub", "thermal_guard", "setup_manager", "evidence_validator",
        "help_manager", "plan_wizard", "qa_wizard", "android_emulator_manager",
        "windows_vm_manager", "roadmap_manager", "telemetry_status_manager",
        "openrouter_usage_monitor", "autonomy_guard", "git_manager", "engine_manager",
        "product_planner", "safe_trash", "directory_manager"
    ]
    all_local_py = [
        os.path.splitext(f)[0] for f in os.listdir(current_dir)
        if f.endswith(".py") and f != "bot.py"
    ]
    modules = []
    for m in priority_modules + sorted(all_local_py):
        if m not in modules:
            modules.append(m)

    # [🔒 Hot-Reload Ordering] importer 를 dependency 보다 먼저 리로드하면
    # `from x import Y` 바인딩이 옛 객체로 남는다. 의존성 우선 순서로 재정렬한다.
    # (실행 중 프로세스가 구(舊) bot.py 본문일 수 있으므로 존재 여부를 방어적으로 확인)
    _ordering_fn = globals().get("_order_modules_for_reload")
    if callable(_ordering_fn):
        try:
            modules = _ordering_fn(modules, current_dir)
        except Exception as _order_err:
            logger.debug(f"[Hot-Reload Ordering] 모듈 정렬 건너뜀: {_order_err}")

    reloaded_modules = []
    for mod in modules:
        try:
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
                reloaded_modules.append(mod)
            else:
                __import__(mod)
                reloaded_modules.append(mod)
        except Exception as e:
            logger.debug(f"Could not dynamic-reload {mod}: {e}")
    if "config" in sys.modules:
        CODER_MODEL = sys.modules["config"].CODER_MODEL
        QA_MODEL = sys.modules["config"].QA_MODEL
        AUDITOR_MODEL = sys.modules["config"].AUDITOR_MODEL
        COMMANDER_MODEL = sys.modules["config"].COMMANDER_MODEL
    if "commander" in sys.modules:
        Commander = sys.modules["commander"].Commander
    if "evidence_validator" in sys.modules:
        EvidenceValidator = sys.modules["evidence_validator"].EvidenceValidator
        globals()["EvidenceValidator"] = EvidenceValidator
    if "session_manager" in sys.modules and hasattr(sys.modules["session_manager"], "SessionManager"):
        SM_cls = sys.modules["session_manager"].SessionManager
        if "session_manager" in globals() and hasattr(session_manager, "__class__"):
            if session_manager.__class__ is not SM_cls:
                session_manager.__class__ = SM_cls
    ensure_commander_bot_started()
    ensure_image_bot_started()
    if "trio_agent_runner" in sys.modules:
        TrioAgentRunner = sys.modules["trio_agent_runner"].TrioAgentRunner
        QuotaManager = sys.modules["trio_agent_runner"].QuotaManager
    if "model_manager" in sys.modules:
        try:
            mm = sys.modules["model_manager"]
            if hasattr(mm, "GlobalModelManager"):
                mm.GlobalModelManager.sync_to_runtime_config()
                _sync_fn = globals().get("sync_all_bot_presences")
                if callable(_sync_fn):
                    mm.GlobalModelManager.register_change_callback(_sync_fn)
            if hasattr(mm, "ModelDashboardView") and hasattr(mm.ModelDashboardView, "_active_dashboards"):
                for _dash in list(mm.ModelDashboardView._active_dashboards):
                    if _dash.__class__ is not mm.ModelDashboardView:
                        _dash.__class__ = mm.ModelDashboardView
        except Exception as _mm_err:
            logger.debug(f"model_manager post-reload notice: {_mm_err}")
    if "auto_scheduler" in sys.modules:
        try:
            _as = sys.modules["auto_scheduler"]
            if hasattr(_as, "ChannelAutoScheduler"):
                _fire_fn = globals().get("_fire_auto_channel_wakeup")
                if callable(_fire_fn):
                    _as.ChannelAutoScheduler.set_fire_handler(_fire_fn)
        except Exception as _as_err:
            logger.debug(f"auto_scheduler post-reload notice: {_as_err}")
    if "AutoDashboardView" in globals() and hasattr(AutoDashboardView, "_active_dashboards"):
        try:
            for _adash in list(AutoDashboardView._active_dashboards):
                if _adash.__class__ is not AutoDashboardView:
                    _adash.__class__ = AutoDashboardView
        except Exception as _ad_err:
            logger.debug(f"AutoDashboardView class upgrade notice: {_ad_err}")
    if "research_runner" in sys.modules:
        ResearchRunner = sys.modules["research_runner"].ResearchRunner
    if "consulting_runner" in sys.modules:
        ConsultingRunner = sys.modules["consulting_runner"].ConsultingRunner
    if "agent_runner" in sys.modules:
        AgentRunner = sys.modules["agent_runner"].AgentRunner
    if "mode_classifier" in sys.modules:
        classify_mode = sys.modules["mode_classifier"].classify_mode
        classify_task_type = sys.modules["mode_classifier"].classify_task_type
        get_mode_description = sys.modules["mode_classifier"].get_mode_description
    if "thermal_guard" in sys.modules:
        ThermalGuard = sys.modules["thermal_guard"].ThermalGuard
    # Self-Healing feature removed — no self_healing reload block
    if "qa_hub" in sys.modules:
        qa_hub_manager = sys.modules["qa_hub"].QAHubManager(session_manager, qa_bot=qa_bot, auditor_bot=auditor_bot)
        qa_hub_manager.set_on_complete_callback(dispatch_next_in_queue)
    # AutoPilot feature removed — no auto_pilot reload block
    # [🛡️ Quota Protection & Continuity] Ensure telemetry status loop is active (singleton prevents duplicate tasks)
    if "telemetry_status_manager" in sys.modules:
        try:
            tsm = sys.modules["telemetry_status_manager"]
            active_client = server_manager_bot if (DISCORD_SERVER_MANAGER_BOT_TOKEN and server_manager_bot.is_ready()) else bot
            tsm.ensure_telemetry_status_loop_running(active_client, session_manager)
            if hasattr(bot, "add_view"):
                tsm.ensure_telemetry_status_loop_running(bot, session_manager)
        except Exception as _tsm_err:
            logger.debug(f"telemetry loop check on reload notice: {_tsm_err}")

    if "init_extended_qa_bots" in globals() and callable(globals()["init_extended_qa_bots"]):
        try:
            init_extended_qa_bots()
        except Exception as _init_err:
            logger.debug(f"init_extended_qa_bots post-reload notice: {_init_err}")

    # ──────────────────────────────────────────────────────────────────
    # [🔒 Hot-Reload Fix] bot.py는 __main__ 스크립트이므로 importlib.reload()
    # 대상이 될 수 없습니다. 따라서 bot.py에 추가된 새 전역 변수나 루프 로직
    # (예: Patch 3 서킷 브레이커)은 !reset으로는 자동 반영되지 않습니다.
    # 아래 코드가 이 문제를 해결합니다:
    # 1. 새 전역 변수를 명시적으로 선언/초기화하여 항상 존재하게 보장
    # 2. 실행 중인 Watchdog 태스크를 강제 취소 후 재시작하여
    #    새 _evolve_watchdog_loop 클로저(서킷 브레이커 포함)가 즉시 적용되도록 함
    # ──────────────────────────────────────────────────────────────────
    global _evolve_watchdog_task, _watchdog_failure_counts, WATCHDOG_CIRCUIT_BREAKER_THRESHOLD

    # 새 전역 변수 초기화 (처음 !reset 시 또는 봇 기동 직후)
    if not isinstance(globals().get("_watchdog_failure_counts"), dict):
        _watchdog_failure_counts = {}
        logger.info("[Hot-Reload Fix] _watchdog_failure_counts 초기화 완료 (서킷 브레이커 카운터).")
    if not isinstance(globals().get("WATCHDOG_CIRCUIT_BREAKER_THRESHOLD"), int):
        WATCHDOG_CIRCUIT_BREAKER_THRESHOLD = 3
        logger.info("[Hot-Reload Fix] WATCHDOG_CIRCUIT_BREAKER_THRESHOLD = 3 으로 설정.")

    # [🛡️ Hot-Reload Fix] 대기열 락 전역의 유실 방지.
    # bot.py 는 __main__ 이라 importlib.reload 대상이 아니어서, 새로 추가된 전역이
    # 핫리로드 후 사라지면 `dispatch_next_in_queue` 가 NameError 로 죽어 대기열이
    # 영구 정지(=요청 무응답/상태 타이머 고정 프리징)했다. 여기서 항상 보장한다.
    try:
        if not isinstance(globals().get("_channel_queue_locks"), dict):
            # [QA Direct Patch] 함수 내 `_channel_queue_locks = {}` 는 global 선언이 없으면
            # '지역 변수'로만 바인딩되어 전역이 복원되지 않는다(복원 의도 무효). 전역에 직접 쓴다.
            globals()["_channel_queue_locks"] = {}
            logger.info("[Hot-Reload Fix] _channel_queue_locks 초기화 완료 (대기열 락 레지스트리).")
        if not isinstance(globals().get("QUEUE_LOCK_TIMEOUT_SECONDS"), float):
            globals()["QUEUE_LOCK_TIMEOUT_SECONDS"] = float(
                os.environ.get("QUEUE_LOCK_TIMEOUT_SECONDS", "20") or 20
            )
            logger.info("[Hot-Reload Fix] QUEUE_LOCK_TIMEOUT_SECONDS 복원 완료.")
        if not isinstance(globals().get("THERMAL_LOCK_GRACE_SECONDS"), float):
            globals()["THERMAL_LOCK_GRACE_SECONDS"] = float(
                os.environ.get("QUEUE_LOCK_THERMAL_GRACE_SECONDS", "320") or 320
            )
            logger.info("[Hot-Reload Fix] THERMAL_LOCK_GRACE_SECONDS 복원 완료.")
    except Exception as _ql_err:
        logger.debug(f"[Hot-Reload Fix] 대기열 락 전역 복원 알림: {_ql_err}")

    # 최신 bot.py 코드가 반영된 새 Watchdog 태스크 기동 보장
    try:
        ensure_evolve_channel_watchdog_running(bot, session_manager, qa_hub_manager)
    except Exception as e:
        logger.debug(f"Evolve watchdog 기동 알림: {e}")

    try:
        if "hot_reload_bridge" in sys.modules:
            sys.modules["hot_reload_bridge"].sync_in_memory_state()
    except Exception as _hrb_err:
        logger.debug(f"hot_reload_bridge sync notice: {_hrb_err}")

    # Dynamically rebind commands in bot.all_commands to ensure hot-reloading works without restart
    from discord.ext import commands as ext_commands

    def _safe_rebind_command(cmd_var_name, aliases):
        target = globals().get(cmd_var_name)
        if not target:
            return

        # Store pristine raw function in globals() if valid
        raw_cand = getattr(target, "callback", target)
        while hasattr(raw_cand, "callback"):
            if raw_cand is target or getattr(raw_cand, "callback", None) is raw_cand:
                raw_cand = None
                break
            raw_cand = getattr(raw_cand, "callback", None)
        if callable(raw_cand) and not hasattr(raw_cand, "invoke"):
            globals()[f"_raw_{cmd_var_name}"] = raw_cand

        # 1. If target is already a Command object
        if hasattr(target, "invoke") and hasattr(target, "callback"):
            # Ensure callback is unwrapped and not self/Command
            cb = target.callback
            visited = {id(target)}
            while hasattr(cb, "callback"):
                if id(cb) in visited or getattr(cb, "callback", None) is cb:
                    cb = None
                    break
                visited.add(id(cb))
                cb = cb.callback
            if cb is not None and callable(cb) and not hasattr(cb, "invoke"):
                target.callback = cb

            # Ensure aliases are attached to target.aliases
            existing_aliases = set(getattr(target, "aliases", []))
            for a in aliases[1:]:
                if a not in existing_aliases:
                    target.aliases.append(a)

            for alias in aliases:
                bot.all_commands[alias] = target
            return

        # 2. If target is a bare callable/function
        if callable(target):
            while hasattr(target, "callback"):
                target = target.callback
            if hasattr(target, "invoke") or not callable(target):
                return

            existing_cmd = None
            for alias in aliases:
                candidate = bot.all_commands.get(alias)
                if candidate is not None and hasattr(candidate, "invoke") and hasattr(candidate, "callback"):
                    existing_cmd = candidate
                    break

            if existing_cmd is not None:
                if target is not existing_cmd and not hasattr(target, "invoke"):
                    existing_cmd.callback = target
                existing_aliases = set(getattr(existing_cmd, "aliases", []))
                for a in aliases[1:]:
                    if a not in existing_aliases:
                        existing_cmd.aliases.append(a)
                for alias in aliases:
                    bot.all_commands[alias] = existing_cmd
                globals()[cmd_var_name] = existing_cmd
            else:
                new_cmd = ext_commands.Command(target, name=aliases[0], aliases=aliases[1:])
                try:
                    bot.add_command(new_cmd)
                except Exception:
                    pass
                for alias in aliases:
                    bot.all_commands[alias] = new_cmd
                globals()[cmd_var_name] = new_cmd

    _safe_rebind_command("help_command", ["help", "hlep", "도움", "도움말", "명령어", "commands", "guide", "man", "메뉴", "menu"])
    _safe_rebind_command("auto_command", ["auto", "자율", "오토", "autopilot", "autocode"])
    _safe_rebind_command("commander_command", ["commander", "cmd", "사령관", "라우터", "router"])
    _safe_rebind_command("export_command", ["export", "대화백업", "backup", "chatexport", "chatlog", "대화기록", "내보내기", "채팅저장"])
    _safe_rebind_command("read_command", ["read", "기획", "자문", "방향성", "consult", "readonly", "아키텍처"])
    _safe_rebind_command("single_command", ["single", "solo", "단일", "싱글"])
    _safe_rebind_command("image_command", ["image", "img", "이미지", "이미지생성", "그려줘", "draw"])
    _safe_rebind_command("pair_plus_command", ["pair+", "pair_plus", "2+", "2plus", "2플러스", "pair_quad", "quad_pair"])
    _safe_rebind_command("mode3_command", ["mode3", "3", "자율3", "무인3", "3자율", "3차자율", "auto_trio"])
    _safe_rebind_command("mode3_plus_command", ["mode3+", "mode3_plus", "3+", "3plus", "3플러스", "자율3+", "무인3+", "auto_multi"])
    _safe_rebind_command("single_qa2_command", ["single_qa2", "qa2_solo", "solo_qa2", "qa2"])
    _safe_rebind_command("pair_command", ["pair", "trio", "협업", "페어", "3중", "2"])
    _safe_rebind_command("quota_command", ["quota", "usage", "limit", "credits", "쿼터", "사용량"])
    _safe_rebind_command("pwd_command", ["pwd", "경로", "위치", "cwd", "dir"])
    _safe_rebind_command("cd_command", ["cd", "이동", "폴더이동", "디렉토리"])
    _safe_rebind_command("mkdir_command", ["mkdir", "폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"])
    _safe_rebind_command("stop_command", ["stop", "sto", "halt", "cancel", "kill", "abort", "중지", "정지", "취소", "그만", "멈춰", "중단"])
    _safe_rebind_command("done_command", ["done", "완료", "finish", "마무리", "끝", "stop_after_cycle"])
    _safe_rebind_command("zip_command", ["zip", "archive", "download", "zipfile"])
    _safe_rebind_command("apk_command", ["apk", "app", "android", "앱", "apk다운로드", "빌드"])
    _safe_rebind_command("drive_command", ["drive", "gdrive", "upload", "드라이브", "구글드라이브"])
    _safe_rebind_command("colab_command", ["colab", "android_runner", "headed", "코랩"])
    _safe_rebind_command("sim_command", ["sim", "simulator", "mobile", "viewer", "시뮬레이터"])
    _safe_rebind_command("status_command", ["status", "uptime", "info", "상태", "업타임", "정보", "sysinfo"])
    _safe_rebind_command("stall_command", ["stall", "스톨", "정지감시", "정지감시기", "stallguard", "교착"])
    _safe_rebind_command("reset_command", ["reset", "reload", "restart", "hotreload", "리셋", "재시작", "초기화", "핫리셋"])
    _safe_rebind_command("new_session", ["new", "clear", "clean", "cls", "대화초기화", "세션초기화", "새세션", "새대화", "비우기"])
    _safe_rebind_command("setup_command", ["setup", "셋업", "설정", "init", "preset", "프리셋", "초기설정", "plan", "기획"])
    _safe_rebind_command("mode_command", ["mode", "모드", "동작모드"])
    _safe_rebind_command("qa_command", ["qa", "qastatus", "qa_status", "다중qa", "qa목록"])
    _safe_rebind_command("queue_command", ["queue", "q", "대기열"])
    _safe_rebind_command("clear_queue_command", ["clear-queue", "cq", "대기열삭제", "대기열초기화", "대기열비우기"])
    _safe_rebind_command("temp_command", ["temp", "thermal", "온도", "발열"])
    _safe_rebind_command("model_command", ["model", "모델", "modelselect"])
    _safe_rebind_command("engine_command", ["engine", "ai", "cli", "엔진", "인공지능", "모델엔진", "engines"])
    _safe_rebind_command("role_command", ["role", "역할", "botrole", "채널역할", "roles"])
    _safe_rebind_command("identity_command", ["identity", "whoami", "나", "본체", "id", "정체성", "agent", "who"])
    _safe_rebind_command("e2e_universal_command", ["e2e", "cross_test", "universal_test", "자율테스트", "e2e테스트", "통합테스트", "검증", "test"])
    _safe_rebind_command("rounds_command", ["rounds", "라운드", "maxrounds", "round_limit", "roundcap"])
    _safe_rebind_command("watchdog_command", ["watchdog", "safety", "guards", "감시견", "안전가드"])

    # Global integrity guarantee: sanitize all entries in bot.all_commands
    for _k, _v in list(bot.all_commands.items()):
        if callable(_v) and not hasattr(_v, "invoke"):
            logger.warning(f"[all_commands sanitize] Auto-healing bare callable '{_k}' into Command")
            bot.all_commands[_k] = ext_commands.Command(_v, name=_k)
        elif hasattr(_v, "callback"):
            if _v.callback is _v or hasattr(_v.callback, "invoke"):
                logger.warning(f"[all_commands sanitize] Detected circular callback in '{_k}', repairing...")
                cand = (
                    globals().get(f"_raw_{_k}_command")
                    or globals().get(f"_raw_{_k}")
                    or globals().get(f"{_k}_command")
                    or globals().get(_k)
                )
                while hasattr(cand, "callback"):
                    if cand is _v or getattr(cand, "callback", None) is cand:
                        cand = None
                        break
                    cand = getattr(cand, "callback", None)
                if callable(cand) and not hasattr(cand, "invoke"):
                    _v._callback = cand
                    try:
                        _v.callback = cand
                    except Exception:
                        pass
                elif _k in ["mkdir", "폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"]:
                    cand_m = globals().get("_raw_mkdir_command") or globals().get("mkdir_command")
                    while hasattr(cand_m, "callback"):
                        if cand_m is _v or getattr(cand_m, "callback", None) is cand_m:
                            cand_m = None
                            break
                        cand_m = getattr(cand_m, "callback", None)
                    if callable(cand_m) and not hasattr(cand_m, "invoke"):
                        _v._callback = cand_m
                        try:
                            _v.callback = cand_m
                        except Exception:
                            pass

    global perform_master_reset
    if "execute_all_in_one_hot_reset" in globals():
        perform_master_reset = globals()["execute_all_in_one_hot_reset"]
        globals()["perform_master_reset"] = perform_master_reset

    return len(reloaded_modules)

def classify_user_intent(
    prompt: str,
    is_explicit_m3: bool = False,
    project_initialized: bool = True
) -> Tuple[bool, bool]:
    """
    Pure intent classifier for Mode 3 autonomous product planning vs feedback handling.
    Delegates to FeedbackIntentClassifier Single Source of Truth (SoT).
    Returns (is_new_goal, has_feedback_signal).
    """
    verdict = FeedbackIntentClassifier.classify(
        prompt=prompt,
        project_initialized=project_initialized,
        is_explicit_m3=is_explicit_m3
    )
    return verdict.to_bot_tuple()

async def check_and_apply_finish_project_freeze(channel, notify: bool = True) -> bool:
    """
    Checks if a channel belongs to the 'finish projects' category (FINISH_PROJECTS_CATEGORY_ID).
    If so, automatically freezes the channel into !auto off and !mode read.
    """
    if not channel:
        return False
    cat_id = getattr(channel, "category_id", None)
    if not cat_id and hasattr(channel, "category") and channel.category:
        cat_id = channel.category.id

    if cat_id == FINISH_PROJECTS_CATEGORY_ID:
        session = session_manager.get_session(channel.id)

        was_not_read = (session.mode != "read")
        was_auto_on = getattr(session, "auto_enabled", False)

        if was_not_read or was_auto_on:
            # Set mode to read
            session.set_mode("read")
            session.auto_enabled = False
            session_manager.save_to_disk()

            if notify:
                ch_name = getattr(channel, "name", f"channel_{channel.id}")
                embed = discord.Embed(
                    title="📦 🔒 [프로젝트 완료 아카이브 동결 안내]",
                    description=(
                        f"**#{ch_name}** 채널이 **finish projects** 완료 카테고리로 이동되었습니다.\n\n"
                        f"• 🛑 **자율 E2E 오토파일럿**: `🔴 비활성화됨 (OFF)`\n"
                        f"• 🔍 **동작 모드**: `🔍 3중 읽기/기획 전용 모드 (Read)`로 자동 고정동결되었습니다.\n\n"
                        f"*(※ 이 채널에서는 실제 소스코드가 수정되지 않으며, 질문 시 읽기 전용으로 아키텍처 및 방향성만 안전하게 자문합니다.)*"
                    ),
                    color=discord.Color.from_rgb(114, 137, 218)
                )
                embed.set_footer(text="AGY Discord Vibe Coding · Auto Archive Freeze")
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    logger.debug(f"Could not send freeze embed to {channel.id}: {e}")
            return True
    return False

@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    """
    Detects when a channel is moved into finish projects category (1538712322711752825).
    Automatically freezes the channel into !auto off and !mode read.
    """
    try:
        before_cat = getattr(before, "category_id", None) or (before.category.id if hasattr(before, "category") and before.category else None)
        after_cat = getattr(after, "category_id", None) or (after.category.id if hasattr(after, "category") and after.category else None)

        if after_cat == FINISH_PROJECTS_CATEGORY_ID and before_cat != FINISH_PROJECTS_CATEGORY_ID:
            logger.info(f"Channel #{after.name} ({after.id}) moved into finish projects category ({FINISH_PROJECTS_CATEGORY_ID}). Applying auto-freeze...")
            await check_and_apply_finish_project_freeze(after, notify=True)
    except Exception as e:
        logger.debug(f"on_guild_channel_update exception: {e}")

async def _sync_bot_nickname(client: Any, base_name: str, model_disp: str):
    """Dynamically syncs bot nickname in all mutual guilds to reflect active role/model."""
    if not client or not getattr(client, "is_ready", lambda: False)():
        return
    b_lower = base_name.lower().strip()
    if b_lower.startswith("qa"):
        m = re.search(r"qa\s*(\d+)", b_lower)
        nick = f"qa{m.group(1)}" if m else (b_lower if b_lower != "qa" else "qa1")
    else:
        nick = f"{base_name} ({model_disp})"
        if len(nick) > 32:
            nick = nick[:31] + "…"
    guilds = getattr(client, "guilds", [])
    for guild in guilds:
        try:
            me = getattr(guild, "me", None)
            if me and getattr(me, "guild_permissions", None) and me.guild_permissions.change_nickname:
                if me.nick != nick:
                    await me.edit(nick=nick)
        except Exception as e:
            logger.debug(f"Could not update nickname for {base_name} in {getattr(guild, 'name', 'guild')}: {e}")

async def sync_all_bot_presences():
    """
    Synchronizes presence activities and server nicknames for ALL bot clients in real-time.
    Called whenever GlobalModelManager updates roles/models.
    """
    try:
        from model_manager import GlobalModelManager
    except Exception:
        GlobalModelManager = None

    # 1. Commander
    if "commander_bot" in globals() and commander_bot and commander_bot.is_ready():
        try:
            m_disp = GlobalModelManager.get_role_model_display("commander") if GlobalModelManager else "Gemini 3.7 Flash"
            await commander_bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=f"Commander (AI 의도 분석 사령관) | {m_disp}")
            )
            await _sync_bot_nickname(commander_bot, "Commander", m_disp)
        except Exception as e:
            logger.debug(f"sync_all_bot_presences commander error: {e}")

    # 2. Coder (bot)
    if "bot" in globals() and bot and bot.is_ready():
        try:
            m_disp = GlobalModelManager.get_role_model_display("coder") if GlobalModelManager else getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
            prefix = globals().get("COMMAND_PREFIX", "!")
            await bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.listening, name=f"{prefix}help | Coder (초고속 개발) | {m_disp}")
            )
            await _sync_bot_nickname(bot, "Coder", m_disp)
        except Exception as e:
            logger.debug(f"sync_all_bot_presences coder error: {e}")

    # 3. QA1 (qa_bot)
    if "qa_bot" in globals() and qa_bot and qa_bot.is_ready():
        try:
            m_disp = GlobalModelManager.get_role_model_display("qa1") if GlobalModelManager else getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
            p_info = GlobalModelManager.get_role_persona_info("qa1") if GlobalModelManager else {}
            p_badge = p_info.get("name", "시니어 코드 리뷰")[:10]
            await qa_bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=f"qa1 ({p_badge}) | {m_disp}")
            )
            await _sync_bot_nickname(qa_bot, "qa1", m_disp)
        except Exception as e:
            logger.debug(f"sync_all_bot_presences qa error: {e}")

    # 4. QA2 (qa2_bot)
    if "qa2_bot" in globals() and qa2_bot and qa2_bot.is_ready():
        try:
            m_disp = GlobalModelManager.get_role_model_display("qa2") if GlobalModelManager else globals().get("QA2_MODEL", "Solar Pro 4")
            p_info = GlobalModelManager.get_role_persona_info("qa2") if GlobalModelManager else {}
            p_badge = p_info.get("name", "교차 검증")[:10]
            await qa2_bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=f"qa2 ({p_badge}) | {m_disp}")
            )
            await _sync_bot_nickname(qa2_bot, "qa2", m_disp)
        except Exception as e:
            logger.debug(f"sync_all_bot_presences qa2 error: {e}")

    # 5. Auditor (auditor_bot)
    if "auditor_bot" in globals() and auditor_bot and auditor_bot.is_ready():
        try:
            m_disp = GlobalModelManager.get_role_model_display("auditor") if GlobalModelManager else "Gemini 3.1 Pro"
            await auditor_bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=f"Auditor (최종 수락 감사) | {m_disp}")
            )
            await _sync_bot_nickname(auditor_bot, "Auditor", m_disp)
        except Exception as e:
            logger.debug(f"sync_all_bot_presences auditor error: {e}")

    # 6. Extended QAs (QA3..QA6)
    ext_qa_dict = globals().get("extended_qa_bots", {})
    for idx, ext_cli in ext_qa_dict.items():
        if idx == 2:
            continue
        if ext_cli and ext_cli.is_ready():
            try:
                role_key = f"qa{idx}"
                m_disp = GlobalModelManager.get_role_model_display(role_key) if GlobalModelManager else None
                c_name = f"qa{idx}"
                if not m_disp or m_disp == "N/A":
                    cfg = config.QA_EXTERNAL_BOTS.get(idx, {}) if hasattr(config, "QA_EXTERNAL_BOTS") else {}
                    c_model = cfg.get("model", "unknown")
                    m_disp = GlobalModelManager.format_model_name(c_model) if GlobalModelManager else c_model
                await ext_cli.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.watching,
                        name=f"{c_name} (교차 검증) | {m_disp}"
                    )
                )
                await _sync_bot_nickname(ext_cli, c_name, m_disp)
            except Exception as e:
                logger.debug(f"sync_all_bot_presences ext_qa error: {e}")

    # 7. Image generator (image_bot) — 실제 AI 이미지 생성 도구 봇 (Nano Banana 계열)
    if "image_bot" in globals() and image_bot and image_bot.is_ready():
        try:
            m_disp = GlobalModelManager.get_role_model_display("image") if GlobalModelManager else getattr(config, "IMAGE_MODEL", "Gemini 3.8 Flash")
            await image_bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=f"Image generator (AI) | {m_disp}")
            )
            await _sync_bot_nickname(image_bot, "Image generator", m_disp)
        except Exception as e:
            logger.debug(f"sync_all_bot_presences image error: {e}")

# Register presence sync callback to GlobalModelManager
try:
    from model_manager import GlobalModelManager
    GlobalModelManager.register_change_callback(sync_all_bot_presences)
except Exception:
    pass


def _engine_display_label(engine_id: str) -> str:
    """[FIX] 엔진 식별자를 실제 엔진 이름으로 정확히 표기한다.

    기존에는 `agy` 가 아니면 무조건 "Hermes" 로 출력해서, OpenCode 엔진으로
    동작 중인데도 기동 로그가 "Hermes Coder Bot" 으로 찍혀 사용자가 다른 봇이
    동작한 것으로 오인했다. (사용자 보고: "오픈코드로 세팅했는데 오픈라우터 봇이 동작")
    """
    eng = (engine_id or "").lower().strip()
    labels = {
        "agy": "AGY (Antigravity)",
        "opencode": "OpenCode",
        "hermes": "Hermes (OpenRouter)",
        "openrouter": "Hermes (OpenRouter)",
        "codex": "Codex",
        "claude": "Claude",
        "copilot": "Copilot",
        "nous": "Nous",
    }
    return labels.get(eng, eng.upper() or "AGY (Antigravity)")


def sync_sessions_with_global_model() -> int:
    """[FIX] `!model`(GlobalModelManager) 의 역할 엔진을 모든 채널 세션에 반영한다.

    과거 `!engine`/기본값으로 채워진 세션 필드(qa_engine=agy, qa2_engine=openrouter,
    auditor_engine=agy)가 `!model` 에서 고른 OpenCode 를 덮어써, 표시(`!model`)와
    실제 실행 엔진이 달라지는 "설정한 봇 ≠ 실제 동작 봇" 문제의 근원을 제거한다.

    반환: 실제로 갱신된 세션 수.
    """
    try:
        from model_manager import GlobalModelManager as _G
    except Exception:
        return 0
    mgr = globals().get("session_manager")
    if mgr is None or not hasattr(mgr, "get_all_sessions"):
        return 0

    # 세션 필드 → GlobalModelManager 역할 키
    role_map = {
        "engine": "coder",
        "qa_engine": "qa1",
        "qa2_engine": "qa2",
        "auditor_engine": "auditor",
    }
    # [📺 채널별 모델] 엔진과 함께 모델도 동기화할 세션 필드 → 역할 키
    model_map = {
        "engine_model": "coder",
        "qa_model": "qa1",
        "qa2_model": "qa2",
    }
    changed = 0
    try:
        sessions = mgr.get_all_sessions().values()
    except Exception:
        return 0
    for sess in sessions:
        dirty = False
        # [📺] 각 세션은 '자기 채널의 오버라이드' 기준으로 해석한다.
        #   (채널 오버레이가 있으면 그 값이, 없으면 전역 값이 적용된다)
        try:
            _cid = int(getattr(sess, "channel_id", 0) or 0)
        except Exception:
            _cid = 0
        for attr, role_key in role_map.items():
            want = (_G.get_role_engine(role_key, channel_id=_cid) or "").lower().strip()
            if not want:
                continue
            cur = (getattr(sess, attr, "") or "").lower().strip()
            # OpenRouter/Hermes 는 동일 별칭으로 취급
            if {cur, want} <= {"openrouter", "hermes"}:
                continue
            if cur != want:
                try:
                    setattr(sess, attr, want)
                    dirty = True
                except Exception:
                    pass
        # 모델 동기화는 '이 채널에 !model 오버라이드가 있을 때'만 수행한다.
        # (`!engine qa model X` 로 채널에서 직접 고른 값을 보존하기 위함)
        try:
            has_override = bool(_G.get_channel_overrides(_cid)) if _cid else False
        except Exception:
            has_override = False
        if has_override:
            for attr, role_key in model_map.items():
                want_model = (_G.get_role_model(role_key, channel_id=_cid) or "").strip()
                if not want_model:
                    continue
                cur_model = (getattr(sess, attr, "") or "").strip()
                if cur_model != want_model:
                    try:
                        setattr(sess, attr, want_model)
                        dirty = True
                    except Exception:
                        pass
        if dirty:
            changed += 1
    if changed and os.environ.get("UNITTEST_RUNNING") != "1":
        try:
            mgr.save_state()
        except Exception:
            pass
    return changed


# [FIX] `!model` 변경 시 채널 세션의 stale 엔진 값(qa_engine=agy, qa2_engine=openrouter …)도
#        즉시 정합화하여 "설정한 봇 ≠ 실제 동작 봇" 문제를 근원에서 제거한다.
try:
    from model_manager import GlobalModelManager as _GMM
    _GMM.register_change_callback(sync_sessions_with_global_model)
except Exception:
    pass

async def on_commander_ready():
    logger.info(f"Commander Bot connected as: {commander_bot.user.name} ({commander_bot.user.id})")
    await sync_all_bot_presences()
    try:
        from model_manager import GlobalModelManager
        cmd_disp = GlobalModelManager.get_role_model_display("commander")
    except Exception:
        cmd_disp = "Gemini 3.7 Flash"
    print("=" * 50)
    print(f"👑 [0/3] AGY Commander Bot ({cmd_disp}) 준비 완료: {commander_bot.user.name}")
    print("=" * 50)

commander_bot.on_ready = on_commander_ready

_evolve_watchdog_task: Optional[asyncio.Task] = None

# [🔒 Patch 3: Circuit Breaker] Track consecutive QA failure count per channel.
# When a channel hits WATCHDOG_CIRCUIT_BREAKER_THRESHOLD consecutive failures,
# the watchdog halts auto-dispatch for that channel and alerts the admin.
_watchdog_failure_counts: dict = {}  # {channel_id: int}
WATCHDOG_CIRCUIT_BREAKER_THRESHOLD = 3  # halt after N consecutive QA failures

def ensure_evolve_channel_watchdog_running(bot_client: discord.Client, session_mgr: Any, qa_hub_mgr: Any):
    pass  # AutoPilot feature removed



@bot.event
async def on_ready():
    logger.info(f"Main Bot connected as: {bot.user.name} ({bot.user.id})")
    logger.info(f"Default Workspace: {DEFAULT_WORKSPACE}")
    await sync_all_bot_presences()
    # [FIX] 기동 시 기존 채널 세션의 stale 엔진 값을 `!model` SSOT 로 1회 정합화
    try:
        _healed = sync_sessions_with_global_model()
        if _healed:
            logger.info(f"[ModelRouting] Healed stale engine fields in {_healed} channel session(s) from !model SSOT.")
    except Exception as _heal_err:
        logger.debug(f"sync_sessions_with_global_model on_ready notice: {_heal_err}")
    try:
        from model_manager import GlobalModelManager
        coder_disp = GlobalModelManager.get_role_model_display("coder")
    except Exception:
        coder_disp = getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
    print("=" * 50)
    active_engine = getattr(config, "DEFAULT_ENGINE", "agy").lower()
    engine_label = _engine_display_label(active_engine)
    print(f"🚀 [1/3] {engine_label} Coder Bot ({coder_disp}) 준비 완료: {bot.user.name} (에이전트 정체성: {active_engine})")
    print(f"기본 작업 폴더: {DEFAULT_WORKSPACE}")
    print("=" * 50)

    # Automatically scan all channels under finish projects and ensure they are frozen
    try:
        for guild in bot.guilds:
            for ch in guild.channels:
                cat_id = getattr(ch, "category_id", None) or (ch.category.id if hasattr(ch, "category") and ch.category else None)
                if cat_id == FINISH_PROJECTS_CATEGORY_ID:
                    await check_and_apply_finish_project_freeze(ch, notify=False)
    except Exception as e:
        logger.debug(f"on_ready finish projects check exception: {e}")

    # Recover any QA missions interrupted by previous crash/reboot
    try:
        if qa_hub_manager:
            await qa_hub_manager.recover_interrupted_missions(bot)
    except Exception as e:
        logger.debug(f"recover_interrupted_missions exception: {e}")

    # [🛡️ Quota Protection - Solar Pro 4 Protocol]: Background telemetry loop permanently deactivated
    if not DISCORD_SERVER_MANAGER_BOT_TOKEN:
        try:
            import telemetry_status_manager
            telemetry_status_manager.ensure_telemetry_status_loop_running(bot, session_manager)
        except Exception as e:
            logger.debug(f"on_ready fallback telemetry loop startup notice: {e}")

    # Start Roadmap Ladder Evolve Watchdog to guarantee 24/7 continuous operation
    try:
        ensure_evolve_channel_watchdog_running(bot, session_manager, qa_hub_manager)
    except Exception as e:
        logger.debug(f"on_ready evolve watchdog startup notice: {e}")

    # [⏰ !auto] 채널별 자동 알람 / 스케줄 웨이크업 엔진 기동
    try:
        await ensure_auto_scheduler_started()
    except Exception as e:
        logger.debug(f"on_ready auto scheduler startup notice: {e}")

async def on_qa_ready():
    logger.info(f"QA Bot connected as: {qa_bot.user.name} ({qa_bot.user.id})")
    await sync_all_bot_presences()
    try:
        from model_manager import GlobalModelManager
        qa_disp = GlobalModelManager.get_role_model_display("qa1")
    except Exception:
        qa_disp = config.QA_MODEL
    print("=" * 50)
    print(f"🕵️ [2/3] AGY qa1 Bot ({qa_disp}) 준비 완료: {qa_bot.user.name}")
    print("=" * 50)

qa_bot.on_ready = on_qa_ready

async def on_qa2_ready():
    logger.info(f"QA2 Bot connected as: {qa2_bot.user.name} ({qa2_bot.user.id})")
    await sync_all_bot_presences()
    try:
        from model_manager import GlobalModelManager
        qa2_disp = GlobalModelManager.get_role_model_display("qa2")
    except Exception:
        qa2_disp = QA2_MODEL
    print("=" * 50)
    print(f"🕵️‍♂️ [2/3] {_engine_display_label(getattr(config, 'QA2_ENGINE', 'agy'))} qa2 Bot ({qa2_disp}) 준비 완료: {qa2_bot.user.name}")
    print("=" * 50)

qa2_bot.on_ready = on_qa2_ready

async def on_auditor_ready():
    logger.info(f"Auditor Bot connected as: {auditor_bot.user.name} ({auditor_bot.user.id})")
    await sync_all_bot_presences()
    try:
        from model_manager import GlobalModelManager
        auditor_disp = GlobalModelManager.get_role_model_display("auditor")
    except Exception:
        auditor_disp = "Gemini 3.1 Pro"
    print("=" * 50)
    print(f"⚖️ [3/3] AGY Client Auditor Bot ({auditor_disp}) 준비 완료: {auditor_bot.user.name}")
    print("=" * 50)

auditor_bot.on_ready = on_auditor_ready

async def on_server_manager_ready():
    logger.info(f"Server Manager Bot connected as: {server_manager_bot.user.name} ({server_manager_bot.user.id})")
    activity = discord.Activity(type=discord.ActivityType.watching, name="Server Manager (인프라 & 텔레메트리 관제)")
    await server_manager_bot.change_presence(activity=activity)
    print("=" * 50)
    print(f"🖥️ [Server Manager] 서버 매니저 봇 준비 완료: {server_manager_bot.user.name}")
    print("=" * 50)
    # [🛡️ Quota Protection - Solar Pro 4 Protocol]: Background telemetry loop permanently deactivated
    try:
        import telemetry_status_manager
        telemetry_status_manager.ensure_telemetry_status_loop_running(server_manager_bot, session_manager)
    except Exception as e:
        logger.error(f"Failed to start telemetry status loop on server_manager_bot: {e}")

server_manager_bot.on_ready = on_server_manager_ready

async def on_server_manager_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id")
        if custom_id == "telemetry_status_refresh" and not interaction.response.is_done():
            # Silent ACK: no popup or ephemeral message
            try:
                await interaction.response.defer()
            except Exception:
                pass
            try:
                import telemetry_status_manager as tsm
                ok = await tsm.update_status_message(interaction.client, session_manager, force=True)
                if not ok:
                    await asyncio.to_thread(tsm.update_status_via_rest_api, session_manager, True)
            except Exception as e:
                logger.error(f"server_manager_bot on_interaction error: {e}")

server_manager_bot.on_interaction = on_server_manager_interaction

async def on_image_ready():
    logger.info(f"Image generator Bot connected as: {image_bot.user.name} ({image_bot.user.id})")
    await sync_all_bot_presences()
    try:
        from model_manager import GlobalModelManager
        c_disp = GlobalModelManager.get_role_model_display("image")
    except Exception:
        c_disp = getattr(config, "IMAGE_MODEL", "gemini-3.8-flash-high")
    print("=" * 50)
    print(f"🎨 [Image generator / 실제 AI 이미지 백엔드] Image Bot ({c_disp}) 준비 완료: {image_bot.user.name}")
    print("=" * 50)

image_bot.on_ready = on_image_ready

@bot.command(name="help", aliases=["hlep", "도움말", "도움", "명령어", "commands", "guide", "man", "메뉴", "menu"])
async def help_command(ctx, *, topic: str = ""):
    """
    AGY 4-Agent Collaborative System Interactive Help Menu with buttons & dropdown.
    """
    reload_helper_modules()
    import help_manager
    importlib.reload(help_manager)
    view = help_manager.HelpInteractiveView(initial_page=topic.strip(), user_id=ctx.author.id)
    embed = view.get_current_embed()
    msg = await ctx.send(embed=embed, view=view, files=view.get_files())
    view.message = msg

@bot.command(name="golden5", aliases=["핵심", "5대명령어", "essential", "필수", "치트시트", "골든5"], hidden=True)
async def golden5_command(ctx):
    """
    Displays the Daily Core Essential Commands cheat-sheet.
    """
    reload_helper_modules()
    import help_manager
    importlib.reload(help_manager)
    embed = help_manager.HelpManager.get_core_embed()
    view = help_manager.HelpInteractiveView(initial_page="core", user_id=ctx.author.id)
    msg = await ctx.send(embed=embed, view=view, files=view.get_files())
    view.message = msg


@bot.command(name="brand", aliases=["브랜드", "배너", "banner", "로고"], hidden=True)
async def brand_command(ctx):
    """
    프로젝트 브랜드 배너(Image generator 생성 에셋) 쇼케이스.
    """
    reload_helper_modules()
    import brand_ui
    importlib.reload(brand_ui)
    embed = brand_ui.build_brand_embed()
    files = brand_ui.banner_files()
    if files:
        await ctx.send(embed=embed, files=files)
    else:
        await ctx.send(embed=embed)

@bot.command(name="auto", aliases=["자율", "오토", "autopilot", "autocode"])
async def auto_command(ctx, *args):
    """
    ⏰ 채널 전용 AI 자동 알람 / 스케줄 웨이크업 (`!auto`)

    정해진 스케줄마다 이 채널의 Coder 봇을 자동으로 깨워,
    지정된 메시지를 마치 사용자가 입력한 것처럼 파이프라인에 투입한다.
    (마케팅 프로젝트의 '매일 아침 자동 브리핑' 등에 사용)

    • `!auto`                  : 현재 채널 설정 현황 + 인터랙티브 제어판
    • `!auto on` / `!auto off` : 이 채널 자동 알람 활성화 / 비활성화 (기본 = OFF)
    • `!auto message <내용>`   : 발동 시 투입할 메시지 지정
    • `!auto schedule <스케줄>`: every 30m · daily 09:00 · weekly mon 09:00 · 평일 09:00
    • `!auto now`              : 즉시 1회 테스트 발동
    • `!auto reset`            : 이 채널 설정 초기화

    ※ 모든 설정은 **채널별로 독립**이다.
    """
    reload_helper_modules()
    import auto_scheduler
    importlib.reload(auto_scheduler)
    from auto_scheduler import (
        ChannelAutoScheduler, ScheduleParseError, humanize_schedule,
    )

    channel_id = ctx.channel.id
    joined = " ".join(a for a in args if a).strip()
    low = joined.lower()

    async def _send_status(extra: str = ""):
        view = AutoDashboardView(channel_id=channel_id, user_id=ctx.author.id)
        msg = await ctx.send(extra if extra else None, embed=_auto_status_embed(channel_id), view=view)
        view.message = msg
        return msg

    # 1) 인자 없음 → 상태 + 제어판
    if not joined:
        await _send_status()
        return

    # 2) ON / OFF
    if low in ("on", "켜", "켜기", "활성", "활성화", "start", "enable", "가동", "시작"):
        cfg = ChannelAutoScheduler.set_enabled(channel_id, True)
        await _send_status(
            f"🟢 **이 채널의 자동 알람이 활성화되었습니다!**\n"
            f"스케줄: `{humanize_schedule(cfg)}` · 다음 발동: "
            f"{ChannelAutoScheduler.summarize(channel_id)['next_run_text']}"
        )
        return
    if low in ("off", "꺼", "끄기", "비활성", "비활성화", "stop", "disable", "중지", "정지"):
        ChannelAutoScheduler.set_enabled(channel_id, False)
        await _send_status("🔴 **이 채널의 자동 알람이 비활성화되었습니다.** (예약된 발동이 모두 취소됩니다)")
        return

    # 3) 메시지 지정
    for kw in ("message", "msg", "메시지", "내용", "문구"):
        if low == kw or low.startswith(kw + " "):
            text = joined[len(kw):].strip()
            if not text:
                await ctx.reply("⚠️ 발동 메시지를 함께 입력해주세요. 예: `!auto message 마케팅 주간 성과 점검해줘`")
                return
            ChannelAutoScheduler.set_message(channel_id, text)
            await _send_status("✅ **발동 메시지가 저장되었습니다!**")
            return

    # 4) 스케줄 지정
    for kw in ("schedule", "sched", "스케줄", "주기", "시간", "예약"):
        if low == kw or low.startswith(kw + " "):
            spec = joined[len(kw):].strip()
            try:
                _, desc = ChannelAutoScheduler.set_schedule(channel_id, spec)
            except ScheduleParseError as e:
                await ctx.reply(f"⚠️ {e}")
                return
            s = ChannelAutoScheduler.summarize(channel_id)
            extra = (
                f"🕒 **스케줄이 `{desc}`(으)로 설정되었습니다!**\n"
                + (f"다음 발동 예정: {s['next_run_text']}" if s["enabled"] else "현재 비활성 상태 — `!auto on` 으로 켜주세요.")
            )
            await _send_status(extra)
            return

    # 5) 즉시 발동(테스트)
    if low in ("now", "test", "지금", "즉시", "실행", "테스트", "돌려"):
        result = await ChannelAutoScheduler.trigger_now(channel_id)
        if result.get("fired"):
            await ctx.reply("▶️ **즉시 발동했습니다!** 설정된 메시지가 이 채널의 Coder 파이프라인에 투입됩니다.")
        else:
            await ctx.reply(
                "⚠️ 즉시 발동에 실패했습니다(채널이 작업 중이거나 봇을 찾을 수 없음).\n"
                "잠시 후 다시 시도하거나 `!status` 로 상태를 확인해주세요."
            )
        return

    # 6) 초기화
    if low in ("reset", "초기화", "삭제", "기본", "default", "clear"):
        ChannelAutoScheduler.reset_channel(channel_id)
        await ctx.reply("♻️ **이 채널의 자동 알람 설정이 초기화되었습니다.** (기본값: 비활성 / 매 60분)")
        return

    # 7) `!auto on every 30m` 형태 (on/off + 스케줄 동시 지정)
    tokens = low.split()
    if tokens and tokens[0] in ("on", "켜", "활성", "start", "enable", "가동"):
        schedule_part = " ".join(tokens[1:])
        if schedule_part:
            try:
                _, desc = ChannelAutoScheduler.set_schedule(channel_id, schedule_part)
                ChannelAutoScheduler.set_enabled(channel_id, True)
                s = ChannelAutoScheduler.summarize(channel_id)
                await _send_status(f"🟢 **자동 알람 활성화 + 스케줄 `{desc}` 설정 완료!**\n다음 발동: {s['next_run_text']}")
                return
            except ScheduleParseError as e:
                await ctx.reply(f"⚠️ {e}")
                return

    # 8) 스케줄 표현만 단독 입력 (`!auto every 30m`, `!auto daily 09:00`)
    try:
        _, desc = ChannelAutoScheduler.set_schedule(channel_id, joined)
        s = ChannelAutoScheduler.summarize(channel_id)
        await _send_status(
            f"🕒 **스케줄이 `{desc}`(으)로 설정되었습니다!**\n"
            + (f"다음 발동 예정: {s['next_run_text']}" if s["enabled"] else "현재 비활성 상태 — `!auto on` 으로 켜주세요.")
        )
        return
    except ScheduleParseError:
        pass

    # 9) 그 외 → 도움말
    await ctx.reply(
        "⚠️ 알 수 없는 `!auto` 명령입니다.\n"
        f"사용법: `!auto` · `!auto on` · `!auto off` · `!auto message <내용>` · "
        f"`!auto schedule every 30m` · `!auto now` · `!auto reset`"
    )


def _auto_status_embed(channel_id: int) -> discord.Embed:
    """`!auto` 상태 임베드(명령어/뷰 공통 SSOT)."""
    from auto_scheduler import ChannelAutoScheduler
    s = ChannelAutoScheduler.summarize(channel_id)
    embed = discord.Embed(
        title="⏰ 채널 자동 알람 (AI 스케줄 웨이크업)",
        description=(
            f"**상태**: {'🟢 활성화 (가동 중)' if s['enabled'] else '🔴 비활성화 (기본값)'}\n"
            f"**스케줄**: `{s['schedule_desc']}`\n"
            f"**다음 발동**: {s['next_run_text']}\n"
            f"**지난 발동**: {s['last_run_text']} (누적 {s['run_count']}회)"
        ),
        color=0x2ECC71 if s["enabled"] else 0x95A5A6
    )
    embed.add_field(
        name="💬 발동 메시지",
        value=f"```\n{s['message'][:700]}\n```",
        inline=False
    )
    embed.add_field(
        name="💡 사용법",
        value=(
            f"`{COMMAND_PREFIX}auto on` · `{COMMAND_PREFIX}auto off` — 활성/비활성\n"
            f"`{COMMAND_PREFIX}auto message <내용>` — 발동 메시지 변경\n"
            f"`{COMMAND_PREFIX}auto schedule <스케줄>` — `every 30m` / `daily 09:00` / `weekly mon 09:00` / `평일 09:00`\n"
            f"`{COMMAND_PREFIX}auto now` — 즉시 1회 테스트 발동\n"
            f"`{COMMAND_PREFIX}auto reset` — 이 채널 설정 초기화"
        ),
        inline=False
    )
    embed.set_footer(text=f"채널 ID: {channel_id} · 모든 설정은 채널별로 독립 저장됩니다")
    return embed

@bot.command(name="commander", aliases=["cmd", "사령관", "라우터", "router"], hidden=True)
async def commander_command(ctx, action: str = "status", *, test_prompt: str = ""):
    reload_helper_modules()
    action_clean = action.lower().strip()

    cmd_disp = GlobalModelManager.get_role_model_display("commander") if "GlobalModelManager" in globals() else "Gemini 3.7 Flash"
    if action_clean in ("test", "분석", "판정", "check") and test_prompt:
        status_msg = await ctx.send(f"👑 **[Commander 사령관 의도 분석 중]** 🧠 `{test_prompt[:50]}`...")
        decision = await Commander.analyze_intent(test_prompt, session_mode="auto", workspace=session_manager.get_session(ctx.channel.id).workspace)

        embed = discord.Embed(
            title="👑 Commander 실시간 의도 분석 리포트",
            description=f"**테스트 프롬프트**: `{test_prompt}`",
            color=discord.Color.gold() if decision.mode == "pair" else discord.Color.teal()
        )
        mode_icon = "👥 3중 협업 (Pair)" if decision.mode == "pair" else "👤 단일 에이전트 (Single)"
        embed.add_field(name="🎯 판정된 실행 모드", value=f"**{mode_icon}**", inline=True)
        embed.add_field(name="🏷️ 작업 도메인", value=f"`{decision.task_type}`", inline=True)
        embed.add_field(name="⚡ 분석 소요 시간", value=f"`{decision.latency_ms:.1f}ms` ({decision.source.upper()})", inline=True)
        embed.add_field(name="💡 사령관 판정 근거", value=decision.reason, inline=False)
        embed.add_field(name="📢 사령관 출동 브리핑", value=f"*\"{decision.briefing}\"*", inline=False)
        embed.set_footer(text=f"엔진: {COMMANDER_MODEL} ({cmd_disp}) | 신뢰도: {int(decision.confidence * 100)}%")
        await status_msg.edit(content="", embed=embed)
        return

    stats = Commander.get_stats()
    embed = discord.Embed(
        title="👑 Commander (AI 의도 분석 및 스마트 배차 사령관)",
        description=(
            "**`!mode auto` 환경에서만 동작**하는 최고 수석 관제 사령관입니다.\n"
            "사용자의 메시지가 단순 질문인지, 실제 개발/디버깅/리서치 과제인지를 딥러닝 분석하여\n"
            "**👤 단일 초고속 에이전트** 또는 **👥 3중 협업부대(Coder ↔ QA ↔ Auditor)**로 최적 배차합니다."
        ),
        color=discord.Color.gold()
    )
    embed.add_field(name="🤖 탑재 모델", value=f"`{stats['commander_model']}` *({cmd_disp})*", inline=True)
    embed.add_field(name="📊 총 분석 건수", value=f"`{stats['total_analyzed']}회`", inline=True)
    embed.add_field(name="⚡ 평균 지연 시간", value=f"`{stats['avg_latency_ms']}ms`", inline=True)
    embed.add_field(name="👤 Single 판정", value=f"`{stats['single_count']}회`", inline=True)
    embed.add_field(name="👥 Pair(3중) 판정", value=f"`{stats['pair_count']}회`", inline=True)
    embed.add_field(name="⚙️ AI vs 고속룰", value=f"AI: `{stats['ai_count']}` / Fast: `{stats['heuristic_count']}`", inline=True)

    last = stats.get("last_decision")
    if last:
        embed.add_field(
            name="🕒 최근 판정 이력",
            value=f"• 모드: `[{last['mode'].upper()}]` ({last['reason']})\n• 브리핑: *\"{last['briefing']}\"*",
            inline=False
        )

    embed.add_field(
        name="💡 명령어 안내",
        value=(
            f"`{COMMAND_PREFIX}commander` : 사령관 현황 및 통계 확인\n"
            f"`{COMMAND_PREFIX}commander test <메시지>` : 특정 프롬프트의 Single/Pair 판정 모의 테스트"
        ),
        inline=False
    )
    await ctx.send(embed=embed)


class _ScheduledWakeMessage:
    """
    [⏰ !auto] 스케줄 발동용 경량 메시지 객체.

    실제 디스코드 메시지는 아니지만, `execute_channel_prompt()` 가 요구하는
    최소 인터페이스(channel/content/author/reply)를 제공한다.
    이 객체를 통해 예약된 메시지가 '사용자가 방금 입력한 것'처럼 동일 파이프라인을 탄다.
    """

    def __init__(self, channel: Any, content: str):
        self.channel = channel
        self.content = content
        self.author = getattr(bot, "user", None)
        self.attachments: list = []
        self.embeds: list = []
        self.id = 0

    async def reply(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)


# [⏰ !auto] 자동 알람 발동 핸들러 — ChannelAutoScheduler 가 예약시간 도래 시 호출한다.
async def _fire_auto_channel_wakeup(channel_id: int, message: str, cfg: Dict[str, Any]) -> bool:
    """
    예약된 채널을 깨운다. 발동 성공 시 True, 실패/연기 시 False.

    실패 케이스: 봇이 채널을 찾을 수 없음 / 해당 채널이 현재 작업 중(busy)
    → scheduler 가 next_run 을 뒤로 밀어 다음 tick 에 재시도한다.
    """
    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning(f"[AutoScheduler] Channel {channel_id} not found (bot may have been removed from guild).")
        return False

    try:
        session = session_manager.get_session(channel_id)
        if getattr(session, "is_busy", False):
            logger.info(f"[AutoScheduler] Channel {channel_id} is busy — deferring scheduled wakeup.")
            return False
    except Exception:
        pass

    try:
        banner = discord.Embed(
            title="⏰ [자동 알람 발동]",
            description=(
                f"예약된 스케줄(채널 `{channel_id}`)이 도래하여 "
                "지정된 메시지를 Coder 파이프라인에 자동 투입합니다."
            ),
            color=0xF1C40F
        )
        await channel.send(embed=banner)
    except Exception as e:
        logger.warning(f"[AutoScheduler] Failed to send wakeup banner to {channel_id}: {e}")
        return False

    # 📊 [!auto PDCA 지표 컨텍스트 주입]
    pdca_block = ""
    try:
        from auto_scheduler import AutoMetricsHistory
        pdca_block = AutoMetricsHistory.format_pdca_context(channel_id)
    except Exception as _pdca_err:
        logger.debug(f"[AutoScheduler] PDCA context creation skipped: {_pdca_err}")

    full_prompt = f"{message}{pdca_block}" if pdca_block else message
    wake_msg = _ScheduledWakeMessage(channel, full_prompt)

    async def _run_and_record_auto_cycle():
        try:
            await execute_channel_prompt(wake_msg, full_prompt)
        except Exception as _wake_err:
            logger.error(f"[AutoScheduler] Error during scheduled wakeup execution: {_wake_err}")
        finally:
            try:
                from auto_scheduler import AutoMetricsHistory
                sess = session_manager.get_session(channel_id)
                last_resp = getattr(sess, "_last_turn_response", "") or ""
                AutoMetricsHistory.record_turn_output(
                    channel_id=channel_id,
                    prompt=message,
                    response=last_resp,
                    workspace=getattr(sess, "workspace", None)
                )
                logger.info(f"[AutoScheduler] Successfully ensured PDCA cycle record for channel {channel_id}.")
            except Exception as _rec_err:
                logger.debug(f"[AutoScheduler] Post-wakeup PDCA record notice: {_rec_err}")

    asyncio.create_task(_run_and_record_auto_cycle())
    return True



async def ensure_auto_scheduler_started() -> bool:
    """채널 자동 알람 스케줄러를 기동한다(중복 기동 방지)."""
    try:
        from auto_scheduler import ChannelAutoScheduler
        ChannelAutoScheduler.set_fire_handler(_fire_auto_channel_wakeup)
        started = await ChannelAutoScheduler.start()
        if started:
            enabled = [cid for cid, c in ChannelAutoScheduler.list_configs().items() if c.get("enabled")]
            logger.info(f"[AutoScheduler] Channel auto-wakeup active channels: {enabled or 'none'}")
        return started
    except Exception as e:
        logger.debug(f"ensure_auto_scheduler_started notice: {e}")
        return False


# [⏰ !auto] 모듈 import 시점에 발동 핸들러를 등록해 둔다.
#   (`!auto now` 는 스케줄 루프(on_ready)가 아직 기동되지 않아도 즉시 발동 가능해야 한다)
try:
    from auto_scheduler import ChannelAutoScheduler as _AutoSchedCls
    _AutoSchedCls.set_fire_handler(_fire_auto_channel_wakeup)
except Exception as _e:
    logger.debug(f"auto scheduler handler pre-registration skipped: {_e}")


class AutoMessageModal(Modal):
    """자동 알람 발동 시 투입할 메시지를 입력하는 모달."""
    def __init__(self, channel_id: int, current_message: str, parent_view: 'AutoDashboardView'):
        super().__init__(title="✏️ 자동 알람 메시지 설정")
        self.channel_id = channel_id
        self.parent_view = parent_view
        self.message_input = TextInput(
            label="발동 시 Coder에게 전달할 메시지",
            style=discord.TextStyle.paragraph,
            placeholder="예: 마케팅 주간 성과를 점검하고 이번 주 개선안 3가지를 정리해줘",
            default=(current_message or "")[:1000],
            required=True,
            max_length=1000
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        from auto_scheduler import ChannelAutoScheduler
        ChannelAutoScheduler.set_message(self.channel_id, self.message_input.value)
        if self.parent_view:
            self.parent_view._render()
            embed = self.parent_view.build_embed()
            await _safe_edit_view(interaction, embed, self.parent_view)
        else:
            embed = _auto_status_embed(self.channel_id)
            await _safe_edit_view(interaction, embed, None)
        try:
            await interaction.followup.send("✅ **발동 메시지가 저장되었습니다!**", ephemeral=True)
        except Exception:
            pass


class AutoScheduleModal(Modal):
    """자동 알람 스케줄을 입력하는 모달."""
    def __init__(self, channel_id: int, parent_view: 'AutoDashboardView'):
        super().__init__(title="🕒 자동 알람 스케줄 설정")
        self.channel_id = channel_id
        self.parent_view = parent_view
        self.schedule_input = TextInput(
            label="스케줄 표현",
            placeholder="every 30m · every 2h · daily 09:00 · weekly mon 09:00 · 평일 09:00",
            default="every 60m",
            required=True,
            max_length=100
        )
        self.add_item(self.schedule_input)

    async def on_submit(self, interaction: discord.Interaction):
        from auto_scheduler import ChannelAutoScheduler, ScheduleParseError
        try:
            _, desc = ChannelAutoScheduler.set_schedule(self.channel_id, self.schedule_input.value)
        except ScheduleParseError as e:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            except Exception:
                pass
            return
        if self.parent_view:
            self.parent_view._render()
            embed = self.parent_view.build_embed()
            await _safe_edit_view(interaction, embed, self.parent_view)
        else:
            embed = _auto_status_embed(self.channel_id)
            await _safe_edit_view(interaction, embed, None)
        try:
            await interaction.followup.send(f"🕒 **스케줄이 `{desc}`(으)로 설정되었습니다!**", ephemeral=True)
        except Exception:
            pass


class AutoDashboardView(View):
    """`!auto` 인터랙티브 제어판 (채널별)."""
    _active_dashboards: List['AutoDashboardView'] = []

    def __init__(self, channel_id: int, user_id: Optional[int] = None):
        super().__init__(timeout=600)
        self.channel_id = channel_id
        self.user_id = user_id
        self.message: Optional[discord.Message] = None
        self._render()
        AutoDashboardView._active_dashboards.append(self)
        if len(AutoDashboardView._active_dashboards) > 10:
            AutoDashboardView._active_dashboards = AutoDashboardView._active_dashboards[-10:]

    def _render(self):
        from auto_scheduler import ChannelAutoScheduler
        self.clear_items()
        enabled = ChannelAutoScheduler.is_enabled(self.channel_id)

        btn_toggle = Button(
            label="🔴 비활성화" if enabled else "🟢 활성화",
            style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
            row=0,
            custom_id="btn_auto_toggle"
        )
        btn_toggle.callback = self._toggle_callback
        self.add_item(btn_toggle)

        btn_msg = Button(label="✏️ 메시지 설정", style=discord.ButtonStyle.primary, row=0, custom_id="btn_auto_message")
        btn_msg.callback = self._message_callback
        self.add_item(btn_msg)

        btn_sched = Button(label="🕒 스케줄 설정", style=discord.ButtonStyle.primary, row=0, custom_id="btn_auto_schedule")
        btn_sched.callback = self._schedule_callback
        self.add_item(btn_sched)

        btn_now = Button(label="▶️ 지금 실행", style=discord.ButtonStyle.secondary, row=0, custom_id="btn_auto_now")
        btn_now.callback = self._now_callback
        self.add_item(btn_now)

        btn_reset = Button(label="♻️ 초기화", style=discord.ButtonStyle.secondary, row=0, custom_id="btn_auto_reset")
        btn_reset.callback = self._reset_callback
        self.add_item(btn_reset)

    def build_embed(self) -> discord.Embed:
        return _auto_status_embed(self.channel_id)

    async def refresh_view(self, interaction: discord.Interaction):
        self._render()
        await _safe_edit_view(interaction, self.build_embed(), self)

    async def _toggle_callback(self, interaction: discord.Interaction):
        from auto_scheduler import ChannelAutoScheduler
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass
        new_state = not ChannelAutoScheduler.is_enabled(self.channel_id)
        ChannelAutoScheduler.set_enabled(self.channel_id, new_state)
        await self.refresh_view(interaction)
        try:
            await interaction.followup.send(
                "🟢 **자동 알람을 활성화했습니다!**" if new_state else "🔴 **자동 알람을 비활성화했습니다.**",
                ephemeral=True
            )
        except Exception:
            pass

    async def _message_callback(self, interaction: discord.Interaction):
        from auto_scheduler import ChannelAutoScheduler
        try:
            await interaction.response.send_modal(
                AutoMessageModal(self.channel_id, ChannelAutoScheduler.get_config(self.channel_id).get("message", ""), self)
            )
        except Exception as e:
            logger.debug(f"Auto message modal failed: {e}")

    async def _schedule_callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_modal(AutoScheduleModal(self.channel_id, self))
        except Exception as e:
            logger.debug(f"Auto schedule modal failed: {e}")

    async def _now_callback(self, interaction: discord.Interaction):
        from auto_scheduler import ChannelAutoScheduler
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass
        result = await ChannelAutoScheduler.trigger_now(self.channel_id)
        try:
            if result.get("fired"):
                await interaction.followup.send("▶️ **즉시 발동했습니다!**", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ 발동 실패(채널 작업 중이거나 봇 미연결).", ephemeral=True)
        except Exception:
            pass

    async def _reset_callback(self, interaction: discord.Interaction):
        from auto_scheduler import ChannelAutoScheduler
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass
        ChannelAutoScheduler.reset_channel(self.channel_id)
        await self.refresh_view(interaction)
        try:
            await interaction.followup.send("♻️ **이 채널의 자동 알람 설정을 초기화했습니다.**", ephemeral=True)
        except Exception:
            pass


@bot.command(name="export", aliases=["대화백업", "backup", "chatexport", "chatlog", "대화기록", "내보내기", "채팅저장", "백업"], hidden=True)
async def export_command(ctx, *args):
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)

    # Parse arguments: limit (int) and format (md/txt)
    limit = None
    format_type = "md"

    for a in args:
        a_clean = a.lower().strip()
        if a_clean.isdigit():
            limit = int(a_clean)
        elif a_clean in ("txt", "text", "텍스트"):
            format_type = "txt"
        elif a_clean in ("md", "markdown", "마크다운"):
            format_type = "md"

    limit_desc = f"최근 {limit:,}개" if limit else "전체"
    status_msg = await ctx.send(f"📥 **[채널 대화 기록 추출 중 · {limit_desc}]** 메시지 내역을 수집하고 있습니다...")

    # Build bot role mapping for clean display
    bot_roles = {}
    if bot.user:
        bot_roles[bot.user.id] = f"👨‍💻 Coder ({CODER_MODEL})"
    if commander_bot.user:
        bot_roles[commander_bot.user.id] = f"👑 Commander ({COMMANDER_MODEL})"
    if qa_bot.user:
        bot_roles[qa_bot.user.id] = "qa1"
    if qa2_bot.user:
        bot_roles[qa2_bot.user.id] = "qa2"
    ext_qa = globals().get("extended_qa_bots", {})
    for ext_cfg in config.get_extended_qa_configs():
        ext_idx = ext_cfg["index"]
        if ext_idx in ext_qa and ext_qa[ext_idx].user:
            bot_roles[ext_qa[ext_idx].user.id] = f"qa{ext_idx}"
    if auditor_bot.user:
        bot_roles[auditor_bot.user.id] = f"⚖️ Client Auditor ({AUDITOR_MODEL})"
    if "image_bot" in globals() and image_bot.user:
        i_mod = getattr(config, "IMAGE_MODEL", "gemini-3.8-flash-high")
        bot_roles[image_bot.user.id] = f"🎨 Image generator ({i_mod})"

    async def on_progress(count: int):
        try:
            await status_msg.edit(content=f"📥 **[채널 대화 기록 추출 중 · {limit_desc}]** `{count:,}개` 메시지 수집 및 문서화 진행 중...")
        except Exception:
            pass

    out_path = None
    try:
        out_path, filename, stats = await export_channel_history(
            channel=ctx.channel,
            limit=limit,
            format_type=format_type,
            workspace=session.workspace,
            bot_roles=bot_roles,
            on_progress=on_progress
        )

        sz_kb = stats["file_size_bytes"] / 1024
        sz_mb = sz_kb / 1024
        size_str = f"{sz_mb:.2f} MB" if sz_mb >= 1.0 else f"{sz_kb:.1f} KB"

        # Auto-upload to Google Drive / Cloud
        try:
            await status_msg.edit(content="📥 **[대화 기록 추출 완료]** ☁️ Google Drive 클라우드에 업로드하고 있습니다...")
        except Exception:
            pass

        from gdrive_uploader import GoogleDriveUploader
        drive_res = await GoogleDriveUploader.upload_file(out_path, display_name=filename)
        drive_url = ""
        provider_name = "Google Drive"
        if drive_res.get("success"):
            provider_name = drive_res.get("provider", "Google Drive")
            drive_url = drive_res.get("download_url") or drive_res.get("view_url") or ""

        embed = discord.Embed(
            title="📜 [프로젝트 대화 기록 백업 & 클라우드 보관]",
            description=f"**#{ctx.channel.name}** 채널의 대화 내역이 성공적으로 추출 및 정리되었습니다.",
            color=discord.Color.green()
        )
        embed.add_field(name="📄 생성된 백업 파일", value=f"`{filename}`", inline=False)
        embed.add_field(
            name="💬 총 메시지 수",
            value=f"`{stats['total_messages']:,}개`\n(👤 {stats['user_messages']:,} / 🤖 {stats['bot_messages']:,})",
            inline=True
        )
        embed.add_field(name="📎 첨부 파일 수", value=f"`{stats['attachments']:,}개`", inline=True)
        embed.add_field(name="💾 파일 크기 / 포맷", value=f"`{size_str}` (`.{stats['format']}`)", inline=True)
        embed.add_field(
            name="⏱️ 수집 기간 (KST)",
            value=f"• 시작: `{stats['first_time']}`\n• 종료: `{stats['last_time']}`",
            inline=False
        )
        if drive_url:
            embed.add_field(
                name=f"☁️ {provider_name} 링크 (영구 보관)",
                value=f"• [클라우드에서 열기/다운로드]({drive_url})\n• URL: {drive_url}",
                inline=False
            )
        if session.workspace:
            embed.add_field(name="📂 연결된 프로젝트 작업 공간", value=f"`{session.workspace}`", inline=False)
        embed.set_footer(text="AGY Discord Vibe Coding Platform · 1-Click Export & Cloud Sync")

        await status_msg.edit(content="", embed=embed)

        # Try sending direct Discord attachment if under 10MB
        if stats["file_size_bytes"] < 10 * 1024 * 1024:
            try:
                await ctx.send(file=discord.File(out_path, filename=filename))
            except Exception as fe:
                logger.debug(f"Direct Discord file upload skipped or failed: {fe}")

    except Exception as e:
        logger.exception(f"Export command failed: {e}")
        await status_msg.edit(content=f"⚠️ 대화 기록 추출 중 오류가 발생했습니다: {e}")
    finally:
        try:
            if out_path and os.path.exists(out_path):
                p_dir = os.path.dirname(out_path)
                os.remove(out_path)
                if os.path.exists(p_dir) and "discord_export_" in p_dir:
                    os.rmdir(p_dir)
        except Exception:
            pass

@bot.command(name="zip", aliases=["archive", "download", "zipfile", "압축", "다운로드"], hidden=True)
async def zip_command(ctx, subpath: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    if not session.workspace or not os.path.exists(session.workspace):
        await ctx.send("⚠️ 설정된 작업 디렉토리가 없습니다.")
        return

    status_msg = await ctx.send("📦 **워크스페이스 압축 파일을 생성하고 있습니다...**")
    zip_file = None
    try:
        zip_file = create_workspace_zip(session.workspace, subpath=subpath or None)
        if not zip_file:
            await status_msg.edit(content="⚠️ 압축 파일 생성에 실패했거나 파일 크기가 디스코드 전송 한도(10MB)를 초과했습니다.")
            return

        sz_mb = os.path.getsize(zip_file) / (1024 * 1024)
        target_name = os.path.basename(zip_file)
        await status_msg.edit(content=f"📦 **[워크스페이스 압축 다운로드]** (`{sz_mb:.2f} MB`)\n• 경로: `{session.workspace}`")
        await safe_send_discord_message(ctx, "", files=[discord.File(zip_file, filename=target_name)])
    except Exception as e:
        await status_msg.edit(content=f"⚠️ 압축 파일 전송 중 오류가 발생했습니다: {e}")
    finally:
        try:
            if zip_file and os.path.exists(zip_file):
                os.remove(zip_file)
        except Exception:
            pass

@bot.command(name="apk", aliases=["app", "android", "앱", "apk다운로드", "빌드"], hidden=True)
async def apk_command(ctx, *, args: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    ws = session.workspace
    if not ws or not os.path.isdir(ws):
        await ctx.send("⚠️ 설정된 작업 디렉토리가 없습니다.")
        return

    from android_build_manager import AndroidBuildManager
    from gdrive_uploader import GoogleDriveUploader
    from datetime import datetime

    args_clean = (args or "").strip().lower()
    skip_build = any(k in args_clean for k in ["기존", "skip", "existing", "old"])
    forced_debug = "debug" in args_clean
    forced_release = "release" in args_clean
    build_type = "debug" if forced_debug else ("release" if forced_release else "auto")

    project_dir = AndroidBuildManager.find_android_project(ws)
    target_apk = None
    target_name = None
    target_sz = 0
    build_summary = ""

    if project_dir and not skip_build:
        app_name = AndroidBuildManager.extract_app_name(project_dir)
        status_msg = await ctx.send(
            f"🔨 **[최신 코드 실시간 APK 빌드 시작]**\n"
            f"• 프로젝트: `{app_name}` (`{project_dir}`)\n"
            f"• 현 시점 최신 코드를 즉시 컴파일하여 신규 APK를 생성 중입니다... ⏳"
        )
        build_res = await AndroidBuildManager.build_apk(ws, build_type=build_type, timeout_sec=300.0)
        if build_res.get("success"):
            target_apk = build_res["apk_path"]
            target_name = build_res["apk_name"]
            target_sz = build_res["apk_size"]
            duration = build_res.get("duration_sec", 0.0)
            b_type = build_res.get("build_type", "release").upper()
            build_summary = (
                f"• 빌드 상태: 🚀 **현 시점 최신 코드 실시간 빌드 성공!**\n"
                f"• 빌드 모드: `{b_type}` (컴파일 소요 시간: `{duration:.1f}초`)\n"
                f"• 산출물: `{target_name}` (`{target_sz / (1024*1024):.1f}MB`)\n"
            )
            await status_msg.edit(
                content=(
                    f"📦 **[최신 APK 빌드 성공]** `{target_name}` (`{target_sz / (1024*1024):.1f}MB`)\n"
                    f"☁️ **Google Drive(30TB)에 자동 업로드**를 진행합니다..."
                )
            )
        else:
            err_msg = build_res.get("error", "알 수 없는 빌드 오류")
            await status_msg.edit(
                content=(
                    f"❌ **[최신 APK 실시간 빌드 실패]**\n"
                    f"• 사유: 최신 소스코드 컴파일 도중 오류가 발생했습니다.\n"
                    f"{err_msg}\n\n"
                    f"💡 기존에 빌드되어 있던 이전 APK를 전송하려면 `!apk 기존` 명령어를 사용하세요."
                )
            )
            return
    else:
        # project_dir이 없거나 skip_build인 경우: 기존 APK 탐색
        apk_files = []
        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in [".git", ".venv", "node_modules", "__pycache__", ".system_generated"]]
            for f in files:
                if f.lower().endswith((".apk", ".aab")):
                    full_p = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(full_p)
                        apk_files.append((full_p, sz, f))
                    except Exception:
                        pass

        if not apk_files:
            await ctx.send(
                "📱 **[안드로이드 프로젝트 및 APK 미발견]**\n"
                f"• 워크스페이스(`{ws}`) 내에 안드로이드 빌드 스크립트(`gradlew`) 및 기존 `.apk` 파일이 없습니다.\n"
                "• 봇에게 `안드로이드 앱 개발해줘`라고 요청하시면 프로젝트 생성 및 자동 빌드를 진행합니다."
            )
            return

        def apk_sort_key(item):
            p, sz, fname = item
            fname_lower = fname.lower()
            is_debug = 1 if "debug" in fname_lower else 0
            is_oversized = 1 if sz > 9.5 * 1024 * 1024 else 0
            is_root = 0 if os.path.dirname(p) == ws else 1
            return (is_oversized, is_debug, is_root, -os.path.getmtime(p), sz)

        sorted_apks = sorted(apk_files, key=apk_sort_key)
        target_apk, target_sz, target_name = sorted_apks[0]
        status_msg = await ctx.send(
            f"📦 **[기존 안드로이드 APK 확인]** `{target_name}` (`{target_sz / (1024*1024):.1f}MB`)\n"
            f"☁️ **Google Drive(30TB)에 자동 업로드**를 시작합니다..."
        )
        build_summary = f"• 빌드 상태: ℹ️ 기존 보관된 APK 파일 전송 (빌드 스킵)\n• 산출물: `{target_name}`\n"

    target_sz_mb = target_sz / (1024 * 1024)

    # Google Drive 업로드 수행
    gdrive_success = False
    download_url = None
    provider = "Google Drive"
    try:
        res = await GoogleDriveUploader.upload_file(target_apk, display_name=target_name)
        if res.get("success"):
            gdrive_success = True
            download_url = res.get("download_url") or res.get("view_url")
            provider = res.get("provider", "Google Drive")
    except Exception as e:
        logger.warning(f"Google Drive upload exception: {e}")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if gdrive_success and download_url:
        main_content = (
            f"📱 ☁️ **[{provider} 최신 APK 배포 완료]**\n"
            f"{build_summary}"
            f"• 파일 크기: `{target_sz_mb:.2f}MB` (타임스탬프 산출물 보존 완료)\n"
            f"• 빌드/업로드 일시: `{now_str}`\n"
            f"• 🔗 다운로드 링크: {download_url}\n"
            f"• 로컬 파일 경로: `{target_apk}`\n\n"
            f"💡 **설치 안내**: 스마트폰에서 위 링크를 터치하여 APK를 다운로드한 후 즉시 설치할 수 있습니다."
        )
    else:
        err_msg = res.get("error", "알 수 없는 전송 지연") if isinstance(res, dict) else "전송 오류"
        main_content = (
            f"📦 **[최신 APK 준비 완료]**\n"
            f"{build_summary}"
            f"• 로컬 저장 위치: `{target_apk}`\n"
            f"• 파일 크기: `{target_sz_mb:.2f}MB`\n"
            f"• 일시: `{now_str}`\n"
            f"⚠️ Google Drive 업로드 실패: {err_msg} (로컬 파일은 정상 보존됨)\n"
        )

    # 9.5MB 이하인 경우 디스코드 직접 파일 첨부도 병행 (모바일 원클릭 편의 제공)
    if target_sz <= 9.5 * 1024 * 1024:
        try:
            await safe_send_discord_message(
                ctx,
                content=main_content,
                files=[discord.File(target_apk, filename=target_name)]
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
            return
        except Exception as e:
            logger.debug(f"Direct file send failed, falling back to message edit: {e}")

    await status_msg.edit(content=main_content)

@bot.command(name="drive", aliases=["gdrive", "upload", "드라이브", "구글드라이브"], hidden=True)
async def drive_command(ctx, target_path: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    ws = session.workspace
    if not ws or not os.path.isdir(ws):
        await ctx.send("⚠️ 설정된 작업 디렉토리가 없습니다.")
        return

    from gdrive_uploader import GoogleDriveUploader

    target_file = None
    if target_path:
        full_p = os.path.abspath(os.path.join(ws, target_path)) if not os.path.isabs(target_path) else target_path
        if os.path.isfile(full_p):
            target_file = full_p
        elif os.path.isdir(full_p):
            status_msg = await ctx.send(f"📦 **[구글 드라이브 업로드 준비]** 폴더를 압축하고 있습니다: `{os.path.basename(full_p)}`...")
            target_file = create_workspace_zip(ws, subpath=target_path, max_size_mb=500)
    else:
        # Default: look for APK or create workspace zip
        apks = []
        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in [".git", ".venv", "node_modules", "__pycache__", ".system_generated"]]
            for f in files:
                if f.lower().endswith((".apk", ".aab")):
                    apks.append(os.path.join(root, f))
        if apks:
            target_file = apks[0]
        else:
            status_msg = await ctx.send("📦 **[구글 드라이브 업로드 준비]** 워크스페이스 압축 중...")
            target_file = create_workspace_zip(ws, max_size_mb=500)

    if not target_file or not os.path.isfile(target_file):
        await ctx.send("⚠️ 업로드할 대상 파일을 찾을 수 없습니다.")
        return

    sz_mb = os.path.getsize(target_file) / (1024 * 1024)
    fname = os.path.basename(target_file)
    status_msg = await ctx.send(f"☁️ **[클라우드 / Google Drive 업로드 중...]** `{fname}` (`{sz_mb:.1f}MB`)")

    try:
        res = await GoogleDriveUploader.upload_file(target_file, display_name=fname)
        if res.get("success"):
            provider = res.get("provider", "Google Drive")
            download_url = res.get("download_url") or res.get("view_url")
            await status_msg.edit(
                content=(
                    f"☁️ 📥 **[{provider} 대용량 파일 다운로드]**\n"
                    f"• 파일명: `{fname}` (`{sz_mb:.1f}MB`)\n"
                    f"• 다운로드 링크: [클릭하여 다운로드]({download_url})\n"
                    f"• URL: {download_url}"
                )
            )
        else:
            await status_msg.edit(content=f"⚠️ 업로드 실패: {res.get('error')}")
    except Exception as e:
        await status_msg.edit(content=f"⚠️ 업로드 중 오류가 발생했습니다: {e}")

@bot.command(name="colab", aliases=["android_runner", "headed"], hidden=True)
async def colab_command(ctx):
    nb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "colab_android_runner.ipynb")
    if not os.path.isfile(nb_path):
        await ctx.send("⚠️ Colab 러너 노트북(`colab_android_runner.ipynb`)을 찾을 수 없습니다.")
        return

    from gdrive_uploader import GoogleDriveUploader
    status_msg = await ctx.send("☁️ **[Google Colab Headed 안드로이드 런너 준비 중...]**")

    try:
        res = await GoogleDriveUploader.upload_file(nb_path, display_name="colab_android_runner.ipynb")
        download_url = res.get("download_url") or res.get("view_url", "")
        file_id = res.get("file_id")

        colab_direct_link = f"https://colab.research.google.com/drive/{file_id}" if file_id else download_url

        embed = discord.Embed(
            title="📱 🖥️ Google Colab Headed 안드로이드 클라우드 런너",
            description="구글 Colab의 고성능 클라우드 환경에서 **안드로이드 OS를 실제로 구동하고 60fps 웹 브라우저 화면(noVNC)으로 조작 & 테스트**하는 원클릭 런너입니다.",
            color=discord.Color.green()
        )
        embed.add_field(
            name="🚀 1클릭 Colab 실행 링크",
            value=f"[Google Colab에서 노트북 열기]({colab_direct_link})\n• URL: {colab_direct_link}",
            inline=False
        )
        embed.add_field(
            name="📋 사용 방법 (초간단 2단계)",
            value=(
                "1. 위 링크로 Colab을 열고 **[런타임] ➔ [모두 실행 (Ctrl+F9)]**을 누릅니다.\n"
                "2. 3단계 셀에 출력되는 `https://xxxx.trycloudflare.com` 링크를 클릭하면 **크롬 브라우저 안에 안드로이드 폰 화면이 팝업**됩니다!\n"
                "3. 마우스로 앱을 터치/스크롤하며 테스트하고 스크린샷을 찍을 수 있습니다."
            ),
            inline=False
        )
        embed.add_field(
            name="💡 요금제 혜택",
            value="• 서피스 부하 0% / 추가 비용 0원 (Google Ultra 30TB 드라이브와 100% 자동 연동)",
            inline=False
        )

        await safe_send_discord_message(
            ctx,
            "",
            files=[discord.File(nb_path, filename="colab_android_runner.ipynb")]
        )
        await ctx.send(embed=embed)
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        await status_msg.edit(content=f"⚠️ Colab 러너 발급 중 오류 발생: {e}")

@bot.command(name="sim", aliases=["simulator", "mobile", "viewer", "시뮬", "시뮬레이터"], hidden=True)
async def simulator_command(ctx):
    """
    Launches or returns the 60fps Real-time Mobile Interactive Studio & Gemini Vision E2E Inspector URL.
    """
    try:
        import httpx
        import subprocess

        # Check if server is running on port 3000
        is_running = False
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                r = await client.get("http://127.0.0.1:3000/api/status")
                if r.status_code == 200:
                    is_running = True
            except Exception:
                is_running = False

        session = session_manager.get_session(ctx.channel.id)
        sim_workspace = session.workspace or DEFAULT_WORKSPACE

        if not is_running:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            sim_env = os.environ.copy()
            sim_env["MOBILE_SIM_WORKSPACE"] = sim_workspace
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mobile_simulator_server:app", "--host", "0.0.0.0", "--port", "3000"],
                cwd=app_dir,
                env=sim_env,
                stdin=subprocess.DEVNULL,
                stdout=open("/tmp/mobile_simulator.log", "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            await asyncio.sleep(1.0)

        tailscale_ip = "100.121.32.66"
        embed = discord.Embed(
            title="📱 60fps 실시간 모바일 인터랙티브 스튜디오 (공식 E2E 뷰어)",
            description=(
                "현재 워크스페이스의 안드로이드/모바일 웹앱을 **스마트폰 디바이스 프레임 내에서 60fps 무지연으로 실시간 테스트**할 수 있는 공식 E2E 스튜디오입니다."
            ),
            color=discord.Color.brand_green()
        )
        embed.add_field(
            name="🌐 실시간 접속 URL",
            value=(
                f"• **노트북/원격 (Tailscale)**: `http://{tailscale_ip}:3000`\n"
                "• **로컬 브라우저**: `http://localhost:3000`"
            ),
            inline=False
        )
        embed.add_field(
            name="✨ 주요 기능",
            value=(
                "• **📱 기기 프리셋**: iPhone 16 Pro (393x852), Galaxy/Pixel 8 (412x860), Fold Tablet + 화면 회전\n"
                "• **👆 실시간 터치 & 폼**: 마우스 클릭/스크롤/입력 0초 딜레이 조작\n"
                "• **🧠 Gemini Ultra Vision**: 실시간 시각 품질 4대 기준 전수 감사 (100점 만점 + CSS 코드 제안)\n"
                "• **🤖 Headless Playwright E2E**: 주차 등록 / 출차 알림 시나리오 1클릭 자율 실행\n"
                "• **📦 APK 원클릭 다운로드**: 상단 버튼으로 최신 빌드 APK 즉시 다운로드"
            ),
            inline=False
        )
        embed.set_footer(text="Google Antigravity 3-Agent Collaborative E2E Workbench")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"⚠️ 모바일 스튜디오 호출 중 오류 발생: {e}")

@bot.command(name="android_test", aliases=["androidtest", "apk_test", "apktest", "avd_test"], hidden=True)
async def android_test_command(ctx, *, apk_arg: str = ""):
    """
    Executes genuine Android Full-Test E2E run via ADB / Emulator.
    Captures genuine framebuffers and generates Gate 4/5 physical evidence.
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    ws = session.workspace or DEFAULT_WORKSPACE

    apk_path = None
    if apk_arg.strip():
        cand = os.path.expanduser(apk_arg.strip())
        if os.path.isfile(cand):
            apk_path = cand
        elif os.path.isfile(os.path.join(ws, cand)):
            apk_path = os.path.join(ws, cand)

    if not apk_path:
        for candidate in ["app-release.apk", "ParkMemo.apk", "PdfFlow.apk", "app/build/outputs/apk/release/app-release.apk", "app/build/outputs/apk/debug/app-debug.apk"]:
            p = os.path.join(ws, candidate)
            if os.path.isfile(p):
                apk_path = p
                break

    await ctx.send("📱 **[Android Full-Test]** 안드로이드 풀 테스트 에뮬레이터/ADB E2E 검증을 시작합니다...")
    
    from android_emulator_manager import AndroidEmulatorManager
    manager = AndroidEmulatorManager(workspace=ws)
    
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, manager.run_full_test_suite, apk_path)

    embed = discord.Embed(
        title="📱 Android Full-Test E2E 검증 리포트",
        color=0x2ecc71 if report["status"] == "PASS" else 0xf39c12 if report["status"] == "WARN" else 0xe74c3c,
        description=f"**상태**: `{report['status']}` | **소요 시간**: `{report['total_duration_ms']}ms`"
    )

    tc_lines = []
    for tc in report.get("test_cases", [])[:6]:
        icon = "✅" if tc["status"] == "PASS" else "⚠️" if tc["status"] == "WARN" else "❌"
        tc_lines.append(f"{icon} **{tc['tc_id']}** - {tc['title']}\n└ `{tc['details'][:100]}`")
    
    embed.add_field(name="🧪 테스트 케이스", value="\n".join(tc_lines) or "테스트 결과 없음", inline=False)
    
    files_to_send = []
    for s_path in report.get("screenshots", [])[:3]:
        if s_path and os.path.isfile(s_path):
            try:
                files_to_send.append(discord.File(s_path, filename=os.path.basename(s_path)))
            except Exception as e:
                logger.warning(f"Android test screenshot file attach failed ({s_path}): {e}")

    embed.set_footer(text="AGY 3.0 Android Full-Test Emulator Engine • Gate 4/5 Verified")
    await ctx.send(embed=embed, files=files_to_send if files_to_send else None)

@bot.command(name="win_test", aliases=["wintest", "windows_test", "exe_test", "exetest"], hidden=True)
async def win_test_command(ctx, *, exe_arg: str = ""):
    """
    Executes Windows Native Full-Test E2E run via Headless Wine/Xvfb & KVM.
    Captures genuine X11 framebuffers and verifies DPI / Fluent UI integrity.
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    ws = session.workspace or DEFAULT_WORKSPACE

    exe_path = None
    if exe_arg.strip():
        cand = os.path.expanduser(exe_arg.strip())
        if os.path.isfile(cand):
            exe_path = cand
        elif os.path.isfile(os.path.join(ws, cand)):
            exe_path = os.path.join(ws, cand)

    if not exe_path:
        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in [".git", ".venv", "node_modules", "__pycache__", ".system_generated"]]
            for f in files:
                if f.lower().endswith(".exe"):
                    exe_path = os.path.join(root, f)
                    break
            if exe_path:
                break

    await ctx.send("🪟 **[Windows Full-Test]** 윈도우 네이티브 가상머신 & Wine GUI 렌더링 E2E 검증을 시작합니다...")

    from windows_vm_manager import WindowsVMManager
    manager = WindowsVMManager(workspace=ws)

    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, manager.run_full_test_suite, exe_path)

    embed = discord.Embed(
        title="🪟 Windows Native Full-Test E2E 검증 리포트",
        color=0x2ecc71 if report["status"] == "PASS" else 0xe74c3c,
        description=f"**상태**: `{report['status']}` | **소요 시간**: `{report['total_duration_ms']}ms`"
    )

    tc_lines = []
    for tc in report.get("test_cases", [])[:6]:
        icon = "✅" if tc["status"] == "PASS" else "❌"
        tc_lines.append(f"{icon} **{tc['tc_id']}** - {tc['title']}\n└ `{tc['details'][:100]}`")

    embed.add_field(name="🧪 테스트 케이스", value="\n".join(tc_lines) or "테스트 결과 없음", inline=False)

    files_to_send = []
    for s_path in report.get("screenshots", [])[:3]:
        if s_path and os.path.isfile(s_path):
            try:
                files_to_send.append(discord.File(s_path, filename=os.path.basename(s_path)))
            except Exception as e:
                logger.warning(f"Windows test screenshot file attach failed ({s_path}): {e}")

    embed.set_footer(text="AGY 3.0 Windows Native Full-Test Engine • Gate 4/5 Verified")
    await ctx.send(embed=embed, files=files_to_send if files_to_send else None)

@bot.command(name="e2e", aliases=["cross_test", "universal_test", "자율테스트", "e2e테스트", "통합테스트", "검증", "test"], hidden=True)
async def e2e_universal_command(ctx, *, target_arg: str = ""):
    """
    Executes Universal Zero-Configuration Cross-Platform Autonomous E2E testing (REQ-007).
    Autonomously discovers platform (Web, Android, Windows, iOS), framework, and targets.
    Enforces 200-Point Audit Matrix and MechanicalGate (>= 95 score required).
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    ws = session.workspace or DEFAULT_WORKSPACE

    try:
        from cross_platform_e2e_runner import CrossPlatformE2ERunner
    except ImportError:
        from agy_agent.cross_platform_e2e_runner import CrossPlatformE2ERunner

    target_clean = target_arg.strip() if target_arg.strip() else None

    # Step 1: Zero-Config Platform & Stack Discovery
    discovery = CrossPlatformE2ERunner.discover_platform_and_target(workspace=ws, explicit_target=target_clean)

    disc_embed = discord.Embed(
        title="🔍 [Universal E2E] 제로 컨피그 플랫폼 & 타깃 자율 탐색 완료",
        color=0x3498db,
        description="시스템이 프로젝트 구조와 런타임 환경을 자율 분석하여 적합한 E2E 어댑터를 선택했습니다."
    )
    disc_embed.add_field(name="📱 감지 플랫폼", value=f"`{discovery.platform}` ({discovery.framework})", inline=True)
    disc_embed.add_field(name="⚙️ 실행 런타임", value=f"`{discovery.runtime}`", inline=True)
    disc_embed.add_field(name="🔌 E2E 어댑터", value=f"`{discovery.e2e_adapter}`", inline=True)
    disc_embed.add_field(name="🎯 실행 대상 (Target)", value=f"`{discovery.artifact or discovery.execution_target or 'Auto'}`", inline=False)
    disc_embed.add_field(name="📈 탐지 신뢰도", value=f"`{int(discovery.confidence * 100)}%`", inline=True)
    disc_embed.add_field(name="🖥️ 호스트 실행 가능 여부", value="`실행 가능`" if discovery.is_runnable_on_host else f"`BLOCKED: {discovery.unsupported_reason}`", inline=True)

    if discovery.evidence:
        ev_summary = "\n".join(f"• {e}" for e in discovery.evidence[:3])
        disc_embed.add_field(name="📝 탐지 물리 근거 (Evidence)", value=ev_summary, inline=False)

    disc_embed.set_footer(text="AGY 3.0 Universal Autonomous E2E Engine • REQ-007")
    progress_msg = await ctx.send(embed=disc_embed)

    # Step 2: Autonomous E2E Execution & 200-Point Audit
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(
        None,
        CrossPlatformE2ERunner.run_unified_cross_platform_e2e,
        target_clean,
        None,
        ws
    )

    verdict = report.get("overall_verdict") or report.get("mechanical_gate", {}).get("verdict", "FAIL")
    v_clean = verdict.strip().upper()

    color_map = {
        "PASS": 0x2ecc71,
        "BLOCKED": 0x3498db,
        "UNVERIFIED": 0xf39c12,
        "FAIL": 0xe74c3c
    }
    card_color = color_map.get(v_clean, 0xe74c3c)

    audit_200 = report.get("e2e_200_audit", {})
    score_200 = audit_200.get("total_score", 0.0)
    maturity = audit_200.get("maturity_level", "Unknown")
    mech_gate = report.get("mechanical_gate", {})

    embed = discord.Embed(
        title="🌐 Universal Cross-Platform Autonomous E2E 검증 리포트",
        color=card_color,
        description=f"**최종 판정**: `{verdict}` | **200대 감사 점수**: `{score_200} / 100점` (기준치 >= 95.0 필수)"
    )

    embed.add_field(
        name="🎯 대상 및 어댑터",
        value=f"• 플랫폼: `{report.get('platform', discovery.platform)}`\n• 기술스택: `{discovery.framework}`\n• 어댑터: `{discovery.e2e_adapter}`",
        inline=True
    )
    embed.add_field(
        name="🛡️ 기계적 검증 게이트 (Mechanical Gate)",
        value=f"• 통과 상태: `{mech_gate.get('verdict', verdict)}`\n• 검증 항목: `{mech_gate.get('passed_checks', 0)} / {mech_gate.get('total_checks', 0)} 통과`\n• AI 판정 번복: `{'예 (AI OVERRULED)' if mech_gate.get('ai_overruled') else '아니오'}`",
        inline=True
    )
    audit_160 = report.get("e2e_160_audit", {})
    score_160 = audit_160.get("total_score", 0.0)

    embed.add_field(
        name="🏛️ 200대 유니버설 감사 성숙도",
        value=f"• 성숙도 등급: `{maturity}`\n• 통과 문항 수: `{audit_200.get('raw_passed_items', 0)} / 200 문항`\n• 게이트 상태: `{'🟢 PASS (>=95.0점 달성)' if score_200 >= 95.0 else '🚨 FAIL (95점 미만)'}`",
        inline=False
    )
    if audit_160:
        embed.add_field(
            name="📋 160-Point 무결성 & 자율 자동화 감사",
            value=f"• 감사 점수: `{score_160} / 100점` ({audit_160.get('passed_items', 0)}/{audit_160.get('total_items', 160)} 문항)\n• 최종 승인 여부: `{'🟢 APPROVED (PASS)' if audit_160.get('is_approved') else '🚨 REJECTED'}`",
            inline=False
        )

    # Add Category breakdown
    cats = audit_200.get("categories", {})
    if cats:
        cat_lines = []
        for ck, cinfo in list(cats.items())[:7]:
            icon = "✅" if cinfo["pct"] >= 95.0 else "⚠️" if cinfo["pct"] >= 80.0 else "❌"
            cat_lines.append(f"{icon} **{cinfo['name']}**: `{cinfo['passed']}/{cinfo['total']}` ({cinfo['pct']}%)")
        embed.add_field(name="📊 핵심 감사 카테고리 현황", value="\n".join(cat_lines), inline=False)

    # Quantitative Benchmarks Table (Task 4)
    q_bench = report.get("quantitative_benchmarks", {})
    if q_bench and "metrics" in q_bench:
        m_dict = q_bench["metrics"]
        def _get_badge(m_info):
            p = m_info.get("passed")
            if p is True:
                return "🟢"
            elif p is False:
                return "🚨"
            return "⚪"

        bench_lines = [
            f"• **렌더링 FPS**: `{m_dict.get('rendering_fps', {}).get('formatted_measured', 'N/A (미계측)')}` ({_get_badge(m_dict.get('rendering_fps', {}))})",
            f"• **뷰포트 가시 비율 %**: `{m_dict.get('viewport_visible_ratio', {}).get('formatted_measured', 'N/A (미계측)')}` ({_get_badge(m_dict.get('viewport_visible_ratio', {}))})",
            f"• **메모리 PSS 변화량**: `{m_dict.get('memory_pss_delta', {}).get('formatted_measured', 'N/A (미계측)')}` ({_get_badge(m_dict.get('memory_pss_delta', {}))})",
            f"• **48dp 준수율**: `{m_dict.get('touch_target_48dp', {}).get('formatted_measured', 'N/A (미계측)')}` ({_get_badge(m_dict.get('touch_target_48dp', {}))})"
        ]
        embed.add_field(name="📐 정량적 계측 벤치마크 (실측 데이터)", value="\n".join(bench_lines), inline=False)

    # Attach screenshots if available
    files_to_send = []
    shots_dir = os.path.join(ws, "reports", "gate5", "screenshots")
    if os.path.isdir(shots_dir):
        pngs = sorted(
            [os.path.join(shots_dir, f) for f in os.listdir(shots_dir) if f.endswith(".png")],
            key=os.path.getmtime,
            reverse=True
        )
        for s_path in pngs[:3]:
            if s_path and os.path.isfile(s_path):
                try:
                    files_to_send.append(discord.File(s_path, filename=os.path.basename(s_path)))
                except Exception as e:
                    logger.warning(f"E2E screenshot attach failed ({s_path}): {e}")

    embed.set_footer(text="AGY 3.0 Universal Autonomous E2E Engine • 200-Point Verified")
    await ctx.send(embed=embed, files=files_to_send if files_to_send else None)

@bot.command(name="status_refresh", aliases=["telemetry_refresh", "텔레메트리갱신", "상태갱신", "status_sync"], hidden=True)
async def status_refresh_command(ctx):
    """
    Manually triggers an immediate refresh of the Status Channel telemetry message.
    """
    reload_helper_modules()
    import telemetry_status_manager
    active_client = server_manager_bot if (DISCORD_SERVER_MANAGER_BOT_TOKEN and server_manager_bot.is_ready()) else bot
    # [🛡️ Quota Protection]: Strictly 1-shot manual update, zero background loop
    ok = await telemetry_status_manager.update_status_message(active_client, session_manager)
    if ok:
        await ctx.send(f"✅ **[Control Tower]** <#{telemetry_status_manager.STATUS_CHANNEL_ID}> 채널의 실시간 상태 메시지가 성공적으로 갱신되었습니다!")
    else:
        await ctx.send("⚠️ **[Control Tower]** 상태 메시지 갱신 중 문제가 발생했습니다. 로그를 확인하세요.")

@bot.command(name="status", aliases=["uptime", "info", "상태", "업타임", "정보", "sysinfo"], hidden=True)
async def status_command(ctx):
    """
    Displays the real-time gateway status, boot timestamp, uptime, process PID, and channel sessions.
    """
    uptime_sec = time.time() - BOT_BOOT_TIME
    uptime_str = format_uptime(uptime_sec)
    boot_datetime = time.strftime("%Y-%m-%d %H:%M:%S KST", time.localtime(BOT_BOOT_TIME))
    current_pid = os.getpid()

    # Memory / CPU
    mem_str = "조회 불가"
    cpu_str = "조회 불가"
    try:
        import psutil
        proc = psutil.Process(current_pid)
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        mem_str = f"{mem_mb:.1f} MB"
        cpu_str = f"{proc.cpu_percent(interval=None):.1f}%"
    except Exception:
        pass

    session = session_manager.get_session(ctx.channel.id)
    busy_channels = session_manager.get_busy_channels()

    embed = discord.Embed(
        title="🖥️ [AGY 게이트웨이 시스템 실시간 상태]",
        description="봇 게이트웨이 및 세션의 실시간 가동 상태 정보입니다.",
        color=discord.Color.green() if not busy_channels else discord.Color.gold()
    )

    embed.add_field(
        name="⏱️ 게이트웨이 가동 정보 (Uptime)",
        value=(
            f"• **가동 시간**: `{uptime_str}`\n"
            f"• **부팅 시점**: `{boot_datetime}`\n"
            f"• **메인 프로세스 PID**: `{current_pid}`"
        ),
        inline=False
    )

    coder_id_str = f"`{session.conversation_id[:12]}...`" if session.conversation_id else "없음 (대화 초기화 상태)"
    qa_id_str = f"`{session.qa_conversation_id[:12]}...`" if session.qa_conversation_id else "없음"
    auditor_id_str = f"`{session.auditor_conversation_id[:12]}...`" if session.auditor_conversation_id else "없음"

    role_str = "🕵️ QA Reviewer (검증 전담)" if getattr(session, "role", "coder") == "qa" else "👨‍💻 Coder (개발 전담)"
    try:
        from model_manager import GlobalModelManager
        coder_m_disp = GlobalModelManager.get_role_model_display("coder")
        qa_m_disp = GlobalModelManager.get_role_model_display("qa1")
        auditor_m_disp = GlobalModelManager.get_role_model_display("auditor")
    except Exception:
        coder_m_disp = getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
        qa_m_disp = getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
        auditor_m_disp = "Gemini 3.1 Pro"

    embed.add_field(
        name="📍 현재 채널 세션 상태",
        value=(
            f"• **작업 폴더**: `{session.workspace}`\n"
            f"• **채널 역할**: {role_str}\n"
            f"• **배정 엔진**: Coder: `{session.engine}` | QA: `{session.qa_engine}` | Auditor: `{session.auditor_engine}`\n"
            f"• **배정 모델**: Coder: `{coder_m_disp}` | QA: `{qa_m_disp}` | Auditor: `{auditor_m_disp}`\n"
            f"• **동작 모드**: {get_mode_description(session.mode)}\n"
            f"• **진행 상태**: `{'🔴 작업 중' if session.is_busy else '🟢 정상 대기'}` ({session.status_desc})\n"
            f"• **Coder 세션**: {coder_id_str} | **QA**: {qa_id_str} | **Auditor**: {auditor_id_str}\n"
            f"• **대기열 큐**: `{len(session.peek_queue())}건 대기 중`"
        ),
        inline=False
    )

    total_sessions = len(session_manager._sessions)
    embed.add_field(
        name="🌐 전체 채널 요약",
        value=f"• 활성(작업 중): `{len(busy_channels)}개` / 등록 채널: `{total_sessions}개`",
        inline=True
    )

    embed.add_field(
        name="💻 리소스 점유",
        value=f"• 메모리: `{mem_str}` / CPU: `{cpu_str}`",
        inline=True
    )

    try:
        import telemetry_status_manager as tsm
        quota_summary_lines = []
        cc_usage = tsm.fetch_command_code_usage()
        if cc_usage:
            fh = cc_usage.get("five_hour", {})
            wk = cc_usage.get("weekly", {})
            m_cred = cc_usage.get("monthly_credits", 0)
            m_str = f"${float(m_cred):.2f}" if isinstance(m_cred, (int, float)) else str(m_cred)
            quota_summary_lines.append(f"• **Command Code (cmdc)**: 5h `{(fh.get('remaining_pct', 100.0)):.0f}%` · 주간 `{(wk.get('remaining_pct', 100.0)):.0f}%` · 잔여 {m_str} ({cc_usage.get('plan_name', 'Pro')})")
        oc_usage = tsm.fetch_opencode_go_usage()
        if oc_usage:
            rolling = oc_usage.get("rolling", {}).get("percent", 0.0)
            weekly = oc_usage.get("weekly", {}).get("percent", 0.0)
            quota_summary_lines.append(f"• **OpenCode Go**: 5h `{max(0.0, 100.0 - float(rolling)):.0f}%` · 주간 `{max(0.0, 100.0 - float(weekly)):.0f}%`")
        if quota_summary_lines:
            embed.add_field(name="📊 AI 라우터 쿼터 요약", value="\n".join(quota_summary_lines), inline=False)
    except Exception:
        pass

    embed.set_footer(text=f"명령어: {COMMAND_PREFIX}reset (게이트웨이 완전 재시작) · {COMMAND_PREFIX}reset all (세션 ID 전체 초기화) · {COMMAND_PREFIX}new (현재 채널 대화 리셋)")
    await ctx.send(embed=embed)

@bot.command(name="stall", aliases=["스톨", "정지감시", "정지감시기", "stallguard", "교착"], hidden=True)
async def stall_command(ctx):
    """[🛡️ StallGuard] 채널 정지(교착) 감시 현황 · 최근 교착 이력 · 임계값 조회."""
    try:
        import json
        import stall_guard as _sg
    except Exception as imp_err:
        await ctx.send(f"⚠️ StallGuard 모듈 로드 실패: {imp_err}")
        return

    now = time.time()
    active_lines = []
    busy_total = 0
    for cid, sess in sorted(session_manager.get_all_sessions().items()):
        if not getattr(sess, "is_busy", False):
            continue
        busy_total += 1
        silence = max(0.0, now - float(getattr(sess, "last_progress_at", 0.0) or 0.0))
        busy_sec = max(0.0, now - float(getattr(sess, "busy_since", 0.0) or 0.0))
        if silence >= _sg.DEEP_SECONDS:
            icon = "🔴"
        elif silence >= _sg.HARD_SECONDS:
            icon = "🟠"
        elif silence >= _sg.WARN_SECONDS:
            icon = "🟡"
        else:
            icon = "🟢"
        desc = str(getattr(sess, "status_desc", ""))[:70]
        active_lines.append(
            f"{icon} <#{cid}> · 진행 끊김 `{int(silence)}s` · 작업 `{int(busy_sec)}s` · {desc}"
        )
        if len(active_lines) >= 8:
            break
    if not active_lines:
        active_lines = ["🟢 작업 중인 채널 없음 (전 채널 정상 대기)"]

    recent_lines = []
    try:
        with open(_sg.DEFAULT_EVIDENCE_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
        for ev in payload.get("events", [])[-5:][::-1]:
            recent_lines.append(
                f"• `{ev.get('detected_at', '-')}` · 채널 `{ev.get('channel_id')}` · "
                f"**{ev.get('action')}** · 무응답 `{int(ev.get('silence_seconds', 0))}s`"
            )
    except FileNotFoundError:
        recent_lines = ["교착 이력 없음 (아직 감지된 정지 없음)"]
    except Exception as ev_err:
        recent_lines = [f"이력 파일 조회 실패: {ev_err}"]

    embed = discord.Embed(
        title="🛡️ [StallGuard 채널 정지 감시 현황]",
        description=(
            "작업 도중 정지(무응답/대기열 교착)를 10초 주기로 감시하고 경보합니다 (강제 회수 없음)."
        ),
        color=discord.Color.green()
        if busy_total and all(not l.startswith("🔴") for l in active_lines)
        else (discord.Color.gold() if busy_total else discord.Color.green())
    )
    embed.add_field(
        name=f"⚙️ 임계값 (경보 {int(_sg.WARN_SECONDS)}s / 심층관찰 {int(_sg.HARD_SECONDS)}s / 심층상한 {int(_sg.DEEP_SECONDS)}s / 대기열 {int(_sg.QUEUE_WAIT_SECONDS)}s)",
        value="\n".join(active_lines),
        inline=False
    )
    embed.add_field(name="📜 최근 교착 이력 (최신 5건)", value="\n".join(recent_lines), inline=False)
    # [Anti-AI-Slop] 사용자 UI 에 내부 절대경로 노출 금지 → 작업 공간 기준 상대경로로 표기
    try:
        _evidence_display = os.path.relpath(_sg.DEFAULT_EVIDENCE_PATH, os.getcwd())
    except Exception:
        _evidence_display = "reports/watchdog/stalls.json"
    if _evidence_display.startswith(".."):
        _evidence_display = os.path.join("agy_agent", "reports", "watchdog", "stalls.json")
    embed.add_field(
        name="📁 물리적 증거",
        value=f"`{_evidence_display}` (워크스페이스 기준)",
        inline=False
    )
    embed.set_footer(text="하트비트는 '실제 출력'에서만 갱신됨 (디스코드 임베드는 갱신 안 함)")
    await ctx.send(embed=embed)

@bot.command(name="git", aliases=["github", "repo", "깃", "저장소", "깃허브"], hidden=True)
async def git_command(ctx, *args):
    """
    Displays real-time Git status, branch, remote repo URL, and commit history with interactive UI buttons.
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    ws = session.workspace or DEFAULT_WORKSPACE

    from git_manager import GitRepoManager, GitStatusInteractiveView

    arg_text = " ".join(args).lower().strip()
    if any(k in arg_text for k in ("log", "commit", "commits", "이력", "커밋")):
        embed = GitRepoManager.build_commits_embed(ws, limit=5)
    else:
        embed = GitRepoManager.build_status_embed(ws)

    info = GitRepoManager.get_repo_info(ws)
    view = GitStatusInteractiveView(ws, repo_url=info.get("remote_url", ""))
    await ctx.send(embed=embed, view=view)

@bot.command(name="role", aliases=["역할", "botrole", "채널역할", "roles"], hidden=True)
async def role_command(ctx, *args):
    """Sets or switches the channel's bot persona between Coder and QA Reviewer."""
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    from engine_manager import EngineManager, EngineSelectView

    if args:
        target_role = args[0].lower().strip()
        if target_role in ("qa", "review", "reviewer", "test", "tester", "검증", "리뷰", "테스트"):
            session.set_role("qa")
            session_manager.save_state()
            qa_adapter = EngineManager.get_qa_adapter(session)
            embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
            view = EngineSelectView(session_manager, ctx.channel.id)
            await ctx.reply(
                f"🕵️ **[역할 전환: QA Reviewer & Inspector 모드 활성화]**\n"
                f"현재 채널(`<#{ctx.channel.id}>`)의 봇 역할이 **QA(코드 리뷰 및 품질 검증관)**으로 설정되었습니다!\n"
                f"• 전담 QA 엔진: {qa_adapter.icon} `{qa_adapter.name}` (`{session.qa_engine}`)\n"
                f"• 이제 이 채널에 코드를 올리거나 검증을 의뢰하면, 코드를 새로 짜지 않고 결함/버그/7대 품질 게이트/엣지 케이스를 철저히 검사합니다.\n"
                f"• Coder 모드로 복귀: `!role coder`",
                embed=embed,
                view=view
            )
            return
        elif target_role in ("coder", "code", "dev", "developer", "개발", "코더"):
            session.set_role("coder")
            session_manager.save_state()
            coder_adapter = EngineManager.get_coder_adapter(session)
            embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
            view = EngineSelectView(session_manager, ctx.channel.id)
            await ctx.reply(
                f"👨‍💻 **[역할 전환: Coder 모드 활성화]**\n"
                f"현재 채널(`<#{ctx.channel.id}>`)의 봇 역할이 **Coder(초고속 개발 및 기능 구현)**으로 설정되었습니다!\n"
                f"• 전담 Coder 엔진: {coder_adapter.icon} `{coder_adapter.name}` (`{session.engine}`)\n"
                f"• QA 모드로 전환: `!role qa`",
                embed=embed,
                view=view
            )
            return
        else:
            await ctx.reply("⚠️ 지원되는 역할: `!role qa` (품질 검증 전담) 또는 `!role coder` (개발 전담)")
            return

    embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
    view = EngineSelectView(session_manager, ctx.channel.id)
    await ctx.reply(embed=embed, view=view)


@bot.command(name="identity", aliases=["whoami", "나", "본체", "id", "정체성", "agent", "who"], hidden=True)
async def identity_command(ctx):
    """
    Returns the agent's identity (agy / hermes) based on current channel engine configuration.
    Allows users and automated QA harnesses to verify that each bot is running properly.
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    engine = getattr(session, "engine", "agy").lower().strip()
    try:
        from model_manager import GlobalModelManager
        coder_model = getattr(session, "engine_model", None) or GlobalModelManager.get_role_model("coder") or getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
        coder_display = GlobalModelManager.format_model_name(coder_model)
    except Exception:
        coder_model = getattr(session, "engine_model", None) or getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
        coder_display = coder_model
    direct_badge = "agy" if engine == "agy" else "hermes"

    embed = discord.Embed(
        title=f"🤖 **[에이전트 정체성 확인]** `{direct_badge}`",
        description=f"현재 채널 세션은 **`{direct_badge}`** 에이전트로 정상 구동 중입니다.",
        color=0x34A853 if engine == "agy" else 0x9B59B6
    )
    embed.add_field(name="에이전트 (Identity)", value=f"**`{direct_badge}`**", inline=True)
    embed.add_field(name="Coder 엔진", value=f"`{engine.upper()}`", inline=True)
    embed.add_field(name="배정 모델", value=f"`{coder_display}` ({coder_model})", inline=True)
    bot_user_name = bot.user.name if bot.user else "AGY Coder Bot"
    embed.add_field(name="봇 사용자명", value=f"`{bot_user_name}`", inline=True)
    embed.add_field(name="세션 상태", value="🟢 정상 구동 (명령 수신 대기 중)", inline=True)
    embed.add_field(name="채널 역할", value=f"`{getattr(session, 'role', 'coder').upper()}`", inline=True)
    embed.set_footer(text=f"엔진 전환: {COMMAND_PREFIX}engine coder [agy|hermes] | 정체성 확인: {COMMAND_PREFIX}identity")

    # Send exact keyword first for automated test assertions, with embed
    await ctx.send(f"{direct_badge}", embed=embed)


@bot.command(name="engine", aliases=["ai", "cli", "엔진", "인공지능", "모델엔진", "engines"], hidden=True)
async def engine_command(ctx, *args):
    """
    Displays currently active AI CLI engine (Agy, Hermes, Codex, Claude, OpenCode, Copilot)
    and allows interactive switching, QA engine assignment, and health diagnostics.
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    from engine_manager import EngineManager, EngineSelectView

    if args:
        first_arg = args[0].lower().strip()
        if first_arg in ("doctor", "diag", "diagnose", "진단", "점검", "test"):
            embed = EngineManager.build_doctor_embed(ctx.channel.id, session.workspace)
            await ctx.reply(embed=embed)
            return

        if first_arg in ("role", "역할") and len(args) > 1:
            sub_role = args[1].lower().strip()
            if sub_role in ("qa", "review", "test", "검증"):
                session.set_role("qa")
            else:
                session.set_role("coder")
            session_manager.save_state()
            embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
            view = EngineSelectView(session_manager, ctx.channel.id)
            await ctx.reply(f"✅ 채널 역할이 `{session.role.upper()}` 모드로 변경되었습니다!", embed=embed, view=view)
            return

        # QA Engine / Model configuration: !engine qa [engine|model|backup] [value]
        if first_arg in ("qa", "reviewer", "review", "검증", "리뷰") and len(args) > 1:
            sub_opt = args[1].lower().strip()
            if sub_opt in ("model", "모델") and len(args) > 2:
                target_model = EngineManager.resolve_model_alias(args[2])
                session.set_qa_model(target_model)
                if target_model.startswith("openrouter/"):
                    session.set_qa_engine("openrouter")
                session_manager.save_state()
                embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
                view = EngineSelectView(session_manager, ctx.channel.id)
                await ctx.reply(
                    f"🕵️ **[QA 전담 모델 설정 완료]**\n"
                    f"현재 채널의 QA 검증 모델이 **`{target_model}`**로 지정되었습니다! (배정 엔진: `{session.qa_engine}`)",
                    embed=embed,
                    view=view
                )
                return

            if sub_opt in ("backup", "백업", "fallback") and len(args) > 2:
                target_backup = EngineManager.resolve_model_alias(args[2])
                session.set_qa_backup_model(target_backup)
                session_manager.save_state()
                embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
                view = EngineSelectView(session_manager, ctx.channel.id)
                await ctx.reply(
                    f"🛡️ **[QA 백업 모델 등록 완료]**\n"
                    f"• 활성 메인 모델: `{session.qa_model or config.QA_MODEL}`\n"
                    f"• 등록 백업 모델: `{session.qa_backup_model}` (즉시 전환 준비 완료)\n"
                    f"💡 백업 모델로 즉시 전환: `!engine qa {args[2]}`",
                    embed=embed,
                    view=view
                )
                return

            # Check if user specified a model alias directly (e.g. !engine qa qwen)
            if sub_opt in EngineManager.MODEL_ALIASES or any(sub_opt.startswith(a) for a in ("qwen", "deepseek", "gpt-oss")):
                resolved_model = EngineManager.resolve_model_alias(sub_opt)
                session.set_qa_model(resolved_model)
                if resolved_model.startswith("openrouter/"):
                    session.set_qa_engine("openrouter")
                session_manager.save_state()
                embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
                view = EngineSelectView(session_manager, ctx.channel.id)
                await ctx.reply(
                    f"🕵️ **[QA 모델 전환 완료]**\n"
                    f"현재 채널의 QA 검증 모델이 **`{resolved_model}`**로 설정되었습니다!\n"
                    f"• 배정 엔진: `{session.qa_engine}` (OpenRouter ZDR 보안 가드레일 활성화)",
                    embed=embed,
                    view=view
                )
                return

            target_qa = sub_opt
            qa_adapter = EngineManager.get_adapter(target_qa)
            if not qa_adapter:
                valid_list = ", ".join([f"`{e}`" for e in EngineManager.get_all_engine_ids()])
                await ctx.reply(f"⚠️ 알 수 없는 QA 엔진 또는 모델 식별자입니다: `{target_qa}`\n💡 지원 엔진: {valid_list}\n💡 지원 모델: `qwen`, `gpt-oss`, `deepseek`, `claude`")
                return
            session.set_qa_engine(target_qa)
            session_manager.save_state()
            embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
            view = EngineSelectView(session_manager, ctx.channel.id)
            installed_badge = "✅ 준비 완료" if qa_adapter.is_installed() else f"⚠️ 미설치 (`{qa_adapter.install_command}`)"
            await ctx.reply(
                f"🕵️ **[QA 전담 엔진 설정 완료]**\n"
                f"현재 채널의 QA 검증 엔진이 **{qa_adapter.icon} {qa_adapter.name}** (`{target_qa}`)로 지정되었습니다!\n"
                f"• 바이너리: {installed_badge}\n"
                f"• 인증 규격: `{qa_adapter.auth_info}`",
                embed=embed,
                view=view
            )
            return

        # Coder Engine configuration: !engine coder agy / !engine coder hermes
        if first_arg in ("coder", "code", "dev", "개발") and len(args) > 1:
            target_coder = args[1].lower().strip()
            coder_adapter = EngineManager.get_adapter(target_coder)
            if not coder_adapter:
                valid_list = ", ".join([f"`{e}`" for e in EngineManager.get_all_engine_ids()])
                await ctx.reply(f"⚠️ 알 수 없는 Coder 엔진 식별자입니다: `{target_coder}`\n💡 지원 엔진: {valid_list}")
                return
            session.set_engine(target_coder)
            session_manager.save_state()
            embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
            view = EngineSelectView(session_manager, ctx.channel.id)
            await ctx.reply(
                f"👨‍💻 **[Coder 전담 엔진 설정 완료]**\n"
                f"현재 채널의 Coder 개발 엔진이 **{coder_adapter.icon} {coder_adapter.name}** (`{target_coder}`)로 지정되었습니다!",
                embed=embed,
                view=view
            )
            return

        # Model configuration: !engine model [coder|qa|backup] <model>
        if first_arg in ("model", "모델") and len(args) > 1:
            sub_m = args[1].lower().strip()
            if sub_m in ("qa", "reviewer", "review", "검증", "리뷰") and len(args) > 2:
                target_model = EngineManager.resolve_model_alias(args[2])
                session.set_qa_model(target_model)
                if target_model.startswith("openrouter/"):
                    session.set_qa_engine("openrouter")
                session_manager.save_state()
                await ctx.reply(f"✅ QA 검증 모델이 `{target_model}`로 설정되었습니다! (배정 엔진: `{session.qa_engine}`)")
                return
            elif sub_m in ("backup", "백업", "fallback") and len(args) > 2:
                target_backup = EngineManager.resolve_model_alias(args[2])
                session.set_qa_backup_model(target_backup)
                session_manager.save_state()
                await ctx.reply(f"🛡️ QA 백업 모델이 `{target_backup}`로 등록되었습니다!")
                return
            elif sub_m in ("coder", "code", "dev", "개발") and len(args) > 2:
                target_model = EngineManager.resolve_model_alias(args[2])
                session.set_engine_model(target_model)
                session_manager.save_state()
                await ctx.reply(f"✅ Coder 개발 모델이 `{target_model}`로 설정되었습니다!")
                return
            else:
                target_model = EngineManager.resolve_model_alias(args[1])
                session.set_engine_model(target_model)
                session_manager.save_state()
                await ctx.reply(f"✅ 채널 AI 엔진 모델이 `{target_model}`로 설정되었습니다!")
                return

        # Backup configuration inspection/setting: !engine backup [model]
        if first_arg in ("backup", "백업", "fallback"):
            if len(args) > 1:
                target_backup = EngineManager.resolve_model_alias(args[1])
                session.set_qa_backup_model(target_backup)
                session_manager.save_state()
                embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
                view = EngineSelectView(session_manager, ctx.channel.id)
                await ctx.reply(
                    f"🛡️ **[QA 백업 모델 등록 완료]**\n"
                    f"• 활성 메인 모델: `{session.qa_model or config.QA_MODEL}`\n"
                    f"• 등록 백업 모델: `{session.qa_backup_model}` (즉시 전환 준비 완료)\n"
                    f"💡 백업 모델로 즉시 전환: `!engine qa {args[1]}`",
                    embed=embed,
                    view=view
                )
            else:
                cur_backup = session.qa_backup_model or config.QA_BACKUP_MODEL
                cur_main = session.qa_model or config.QA_MODEL
                embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
                view = EngineSelectView(session_manager, ctx.channel.id)
                await ctx.reply(
                    f"📋 **[현재 QA 백업 모델 등록 현황]**\n"
                    f"• 활성 메인 모델: `{cur_main}`\n"
                    f"• 등록 백업 모델: `{cur_backup}`\n"
                    f"💡 백업 모델 변경: `!engine backup <qwen|gemini|deepseek>`\n"
                    f"💡 백업 모델로 즉시 전환: `!engine qa qwen`",
                    embed=embed,
                    view=view
                )
            return

        # Direct engine switch (defaulting to Coder engine)
        target_engine = first_arg
        adapter = EngineManager.get_adapter(target_engine)
        if not adapter:
            valid_list = ", ".join([f"`{e}`" for e in EngineManager.get_all_engine_ids()])
            await ctx.reply(f"⚠️ 알 수 없는 AI 엔진 식별자입니다: `{target_engine}`\n💡 지원되는 엔진: {valid_list}\n진단 실행: `!engine doctor`")
            return

        session.set_engine(target_engine)
        if len(args) > 1:
            session.set_engine_model(args[1].strip())
        session_manager.save_state()

        embed = EngineManager.build_status_embed(session.engine, ctx.channel.id, session=session)
        view = EngineSelectView(session_manager, ctx.channel.id)

        installed_badge = "✅ 로컬 바이너리 준비 완료" if adapter.is_installed() else f"⚠️ 미설치 (`{adapter.install_command}`)"
        model_info = f"\n• 지정 모델: `{session.engine_model}`" if getattr(session, "engine_model", None) else ""
        await ctx.reply(
            f"✅ 현재 채널의 AI 엔진이 **{adapter.icon} {adapter.name}** (`{target_engine}`)로 설정되었습니다!\n"
            f"• 바이너리 상태: {installed_badge}{model_info}\n"
            f"• 인증 규격: `{adapter.auth_info}`",
            embed=embed,
            view=view
        )
    else:
        embed = EngineManager.build_status_embed(getattr(session, "engine", "agy"), ctx.channel.id, session=session)
        view = EngineSelectView(session_manager, ctx.channel.id)
        await ctx.reply(embed=embed, view=view)

@bot.command(name="model", aliases=["모델", "modelselect"], hidden=True)
async def model_command(ctx, *args):
    """
    Real-time AI Model & Multi-QA Pipeline Controller — **채널별 설정 지원**.

    • 인자 없음 / `!model status` : 이 채널 기준 관제 대시보드(버튼·드롭다운)
    • `!model <role> [model|on|off|persona ...]` : 해당 역할 설정 변경
    • `!model reset [role]` : 이 채널의 전용 설정을 지우고 전역 기본값으로 복귀

    [📺 채널 스코프] 이 채널에서 바꾼 설정은 이 채널에만 저장된다.
    다른 채널은 그대로 전역 설정을 사용하며, `!model reset` 으로 언제든 복귀 가능.
    """
    reload_helper_modules()
    import model_manager
    importlib.reload(model_manager)
    from model_manager import GlobalModelManager, ModelDashboardView

    # [📺 채널 스코프 진입] 이후 모든 getter/setter 는 이 채널 기준으로 동작한다.
    _ch_tok = GlobalModelManager.set_active_channel(ctx.channel.id)
    try:
        return await _model_command_impl(ctx, args, GlobalModelManager, ModelDashboardView)
    finally:
        if _ch_tok is not None:
            GlobalModelManager.reset_active_channel(_ch_tok)


async def _model_command_impl(ctx, args, GlobalModelManager, ModelDashboardView):
    channel_id = ctx.channel.id
    _ch_tok = GlobalModelManager.set_active_channel(channel_id)
    try:
        return await _model_command_body(ctx, args, GlobalModelManager, ModelDashboardView, channel_id)
    finally:
        if _ch_tok is not None:
            GlobalModelManager.reset_active_channel(_ch_tok)


async def _model_command_body(ctx, args, GlobalModelManager, ModelDashboardView, channel_id: int):
    # 모든 대시보드는 '이 채널' 기준으로 렌더링/조작한다.
    def _dash(role: str = "auditor"):
        embed = GlobalModelManager.build_dashboard_embed(selected_role=role, channel_id=channel_id)
        view = ModelDashboardView(selected_role=role, user_id=ctx.author.id, channel_id=channel_id)
        return embed, view

    async def _send_dash(prefix_text: str = "", role: str = "auditor"):
        embed, view = _dash(role)
        msg = await ctx.send(prefix_text if prefix_text else None, embed=embed, view=view)
        view.message = msg
        return msg

    if not args:
        await _send_dash()
        return

    sub_cmd = args[0].lower().strip()

    # [📺 채널 전용 설정 해제] `!model reset [role]`
    if sub_cmd in ("reset", "초기화", "삭제", "global", "전역", "기본", "default", "clear"):
        if len(args) > 1:
            t_role = args[1].lower().strip()
            alias_map = {
                "qa": "qa1", "solar": "qa2", "deepseek": "qa3", "qwen": "qa4",
                "llama": "qa5", "ling": "qa6", "개발": "coder", "감사": "auditor",
                "이미지": "image", "그림": "image", "관제": "commander"
            }
            t_role = alias_map.get(t_role, t_role)
            ok = GlobalModelManager.clear_channel_override(channel_id, t_role)
            if ok:
                sync_sessions_with_global_model()
                await _send_dash(
                    f"♻️ **[{t_role.upper()}]** 채널 전용 설정을 해제하고 전역 기본값으로 되돌렸습니다!"
                )
            else:
                await _send_dash(
                    f"ℹ️ **[{t_role.upper()}]** 에는 이 채널 전용 설정이 없습니다(이미 전역 설정 사용 중)."
                )
            return
        ok = GlobalModelManager.clear_channel_override(channel_id)
        if ok:
            sync_sessions_with_global_model()
            await _send_dash("♻️ **이 채널의 모든 모델 전용 설정을 해제**하고 전역 기본값으로 되돌렸습니다!")
        else:
            await _send_dash("ℹ️ 이 채널에는 전용 설정이 없습니다(전역 설정 사용 중).")
        return

    if sub_cmd in ("list", "status", "목록", "현황", "보기", "대시보드", "dashboard"):
        await _send_dash()
        return

    role_alias_map = {
        "qa": "qa1", "solar": "qa2", "deepseek": "qa3", "qwen": "qa4",
        "llama": "qa5", "ling": "qa6", "개발": "coder", "감사": "auditor",
        "이미지": "image", "그림": "image", "관제": "commander"
    }
    scope_word = "이 채널"  # 모든 변경은 채널 전용 오버라이드로 저장됨

    if sub_cmd in ("all", "전체"):
        if len(args) > 1 and args[1].lower() in ("off", "disable", "끄기", "중지"):
            GlobalModelManager.set_all_qa_enabled(False)
            action_desc = "🔴 **qa1 ~ qa6 전원 비활성화 완료!** (파이프라인이 Coder ➔ Auditor로 직행합니다)"
        else:
            GlobalModelManager.set_all_qa_enabled(True)
            action_desc = "🟢 **qa1 ~ qa6 전원 활성화 완료!** (6중 전수 교차 검증 가동)"
        await sync_all_bot_presences()
        sync_sessions_with_global_model()
        await _send_dash(action_desc)
        return

    if sub_cmd in ("qa1_only", "fast", "기본만", "단일qa"):
        GlobalModelManager.set_only_primary_qa()
        await sync_all_bot_presences()
        sync_sessions_with_global_model()
        await _send_dash(
            "⚡ **qa1만 가동 모드 적용 완료!** (qa2~qa6 자동 패스로 토큰/시간 절약)", role="qa1"
        )
        return

    # Direct Persona sub-command: !model persona <role> <persona> or !model 직업 <role> <persona>
    if sub_cmd in ("persona", "job", "직업", "역할", "specialty"):
        if len(args) >= 3:
            t_role = role_alias_map.get(args[1].lower().strip(), args[1].lower().strip())
            p_val = " ".join(args[2:]).strip()
            GlobalModelManager.set_role_persona(t_role, p_val)
            p_info = GlobalModelManager.get_role_persona_info(t_role, channel_id=channel_id)
            desc = f"💼 **[{t_role.upper()}]** 직업/역할이 **{p_info.get('icon')} {p_info.get('name')}**(으)로 설정되었습니다! ({scope_word} 전용)"
            await sync_all_bot_presences()
            await _send_dash(desc, role=t_role)
            return
        elif len(args) == 2:
            t_role = role_alias_map.get(args[1].lower().strip(), args[1].lower().strip())
            await _send_dash(f"💼 **[{t_role.upper()}]** 직업/역할 설정 창을 엽니다.", role=t_role)
            return
        elif len(args) == 2:
            t_role = role_alias_map.get(args[1].lower().strip(), args[1].lower().strip())
            await _send_dash(f"💼 **[{t_role.upper()}]** 직업/역할 설정 창을 엽니다.", role=t_role)
            return

    # Targeting specific role: !model <role> ...
    target_role = sub_cmd
    all_roles = GlobalModelManager.get_all_roles(channel_id=channel_id)
    if target_role not in all_roles:
        target_role = role_alias_map.get(target_role, target_role)

    if target_role in all_roles:
        if len(args) == 2:
            second = args[1].lower().strip()
            if second in ("on", "enable", "켜기", "활성", "1"):
                GlobalModelManager.set_role_enabled(target_role, True)
                desc = f"🟢 **[{target_role.upper()}]** 에이전트가 **활성화**되었습니다! (파이프라인 검증 수행)"
            elif second in ("off", "disable", "끄기", "비활성", "0"):
                GlobalModelManager.set_role_enabled(target_role, False)
                desc = f"🔴 **[{target_role.upper()}]** 에이전트가 **비활성화**되었습니다! (파이프라인에서 자동 건너뜀)"
            elif second in ("general", "designer", "security", "chaos", "sre", "architect",
                            "디자이너", "디자인", "보안", "보안전문가", "카오스", "엣지", "성능", "아키텍트", "기본", "기본값"):
                GlobalModelManager.set_role_persona(target_role, second)
                p_info = GlobalModelManager.get_role_persona_info(target_role, channel_id=channel_id)
                desc = f"💼 **[{target_role.upper()}]** 직업/역할이 **{p_info.get('icon')} {p_info.get('name')}**(으)로 설정되었습니다!"
            else:
                GlobalModelManager.set_role_model(target_role, args[1].strip())
                resolved_mod = GlobalModelManager.get_role_model(target_role, channel_id=channel_id)
                disp_mod = GlobalModelManager.get_role_model_display(target_role, channel_id=channel_id)
                desc = f"✅ **[{target_role.upper()}]** 모델이 `{disp_mod}` (`{resolved_mod}`)(으)로 변경되었습니다! ({scope_word} 전용)"
        elif len(args) >= 3:
            first_candidate = args[1].lower().strip()
            if first_candidate in ("persona", "job", "직업", "역할", "specialty"):
                p_val = " ".join(args[2:]).strip()
                GlobalModelManager.set_role_persona(target_role, p_val)
                p_info = GlobalModelManager.get_role_persona_info(target_role, channel_id=channel_id)
                desc = f"💼 **[{target_role.upper()}]** 직업/역할이 **{p_info.get('icon')} {p_info.get('name')}**(으)로 설정되었습니다! ({scope_word} 전용)"
            else:
                valid_engines = {"agy", "hermes", "openrouter", "codex", "copilot", "claude", "opencode", "nous"}
                if first_candidate in valid_engines:
                    eng = first_candidate
                    mod = " ".join(args[2:]).strip()
                    GlobalModelManager.set_role_engine(target_role, eng)
                    GlobalModelManager.set_role_model(target_role, mod)
                    resolved_mod = GlobalModelManager.get_role_model(target_role, channel_id=channel_id)
                    disp_mod = GlobalModelManager.get_role_model_display(target_role, channel_id=channel_id)
                    desc = f"✅ **[{target_role.upper()}]** 엔진이 `{eng.upper()}`, 모델이 `{disp_mod}` (`{resolved_mod}`)(으)로 변경되었습니다! ({scope_word} 전용)"
                else:
                    mod = " ".join(args[1:]).strip()
                    GlobalModelManager.set_role_model(target_role, mod)
                    resolved_mod = GlobalModelManager.get_role_model(target_role, channel_id=channel_id)
                    disp_mod = GlobalModelManager.get_role_model_display(target_role, channel_id=channel_id)
                    desc = f"✅ **[{target_role.upper()}]** 모델이 `{disp_mod}` (`{resolved_mod}`)(으)로 변경되었습니다! ({scope_word} 전용)"
        else:
            desc = f"ℹ️ **[{target_role.upper()}]** 설정 창을 엽니다."

        await sync_all_bot_presences()
        sync_sessions_with_global_model()
        await _send_dash(desc, role=target_role)
        return

    # If unrecognized role, show dashboard with guidance
    await _send_dash(
        f"⚠️ 알 수 없는 모델 명령 형식입니다. 아래 대시보드에서 직접 제어하실 수 있습니다.\n"
        f"*사용법: `{config.COMMAND_PREFIX}model` · `{config.COMMAND_PREFIX}model qa2 off` · "
        f"`{config.COMMAND_PREFIX}model reset` (채널 전용 설정 해제)*"
    )


@bot.command(name="bots", aliases=["소개", "봇소개", "agents", "에이전트", "botlist", "botinfo"], hidden=True)
async def bots_command(ctx):
    """
    Shows real-time status and introductions of all active collaborative AI bots,
    including current assigned models and dynamic roles.
    """
    reload_helper_modules()
    from model_manager import GlobalModelManager
    
    coder_disp = GlobalModelManager.get_role_model_display("coder")
    coder_model = GlobalModelManager.get_role_model("coder") or config.CODER_MODEL
    coder_eng = GlobalModelManager.get_role_engine("coder") or getattr(config, "DEFAULT_AI_ENGINE", "agy")
    
    qa1_disp = GlobalModelManager.get_role_model_display("qa1")
    qa1_model = GlobalModelManager.get_role_model("qa1") or config.QA_MODEL
    
    auditor_disp = GlobalModelManager.get_role_model_display("auditor")
    auditor_model = GlobalModelManager.get_role_model("auditor") or config.AUDITOR_MODEL
    
    cmd_disp = GlobalModelManager.get_role_model_display("commander")
    cmd_model = GlobalModelManager.get_role_model("commander") or config.COMMANDER_MODEL
    
    img_disp = GlobalModelManager.get_role_model_display("image")
    img_model = GlobalModelManager.get_role_model("image") or getattr(config, "IMAGE_MODEL", "gemini-3.8-flash-high")
    img_eng = GlobalModelManager.get_role_engine("image") or getattr(config, "IMAGE_ENGINE", "agy")

    vis_disp = GlobalModelManager.get_role_model_display("vision")
    vis_model = GlobalModelManager.get_role_model("vision") or "google/gemini-3.8-flash"
    vis_eng = GlobalModelManager.get_role_engine("vision") or "hermes"
    
    embed = discord.Embed(
        title="🤖 AGY 자율 협업 AI 봇 군단 & 실시간 모델 현황",
        description=(
            "사용자의 명령이나 `!model` 설정에 따라 **봇 닉네임, 상태 메시지, 실시간 작업 배너**가 실시간 동기화됩니다.\n"
            f"💡 모델 변경: `{config.COMMAND_PREFIX}model` (예: `{config.COMMAND_PREFIX}model coder gpt oss 120b`)"
        ),
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name=f"👨‍💻 Coder (초고속 개발 및 구현) — {coder_disp}",
        value=f"• **현재 배정 모델**: `{coder_model}` ({coder_eng.upper()})\n• **주요 역할**: 다중 파일 생성, 반응형 UI 조립, 컴파일/빌드/배포 및 기능 구현\n• **실시간 알림**: `👨‍💻 [Coder({coder_disp}) 작업 중]`",
        inline=False
    )
    
    embed.add_field(
        name=f"🕵️ qa1 — {qa1_disp}",
        value=f"• **현재 배정 모델**: `{qa1_model}`\n• **주요 역할**: Playwright 4대 뷰포트 E2E, 안드로이드 MCP 도구 물리 단언, 7대 품질 게이트 검증\n• **실시간 알림**: `🕵️ [qa1({qa1_disp}) 정밀 검증 중]`",
        inline=False
    )
    
    ext_qas = GlobalModelManager.get_active_extended_qas()
    if ext_qas:
        qa_lines = []
        for q in ext_qas:
            q_disp = GlobalModelManager.get_role_model_display(q["role"])
            qa_lines.append(f"• **{q['name']}**: `{q_disp}` (`{q['model']}`) — {q['engine'].upper()} 교차 검증")
        embed.add_field(
            name=f"🕵️‍♂️ 다중 교차 검증 QA 부대 ({len(ext_qas)}개 활성)",
            value="\n".join(qa_lines),
            inline=False
        )
    
    embed.add_field(
        name=f"⚖️ Client Auditor (최종 수락 감사) — {auditor_disp}",
        value=f"• **현재 배정 모델**: `{auditor_model}`\n• **주요 역할**: 의뢰인(사용자) 관점 PRD/헌법 100% 대조 전수 검사, 최종 승인 및 릴리즈",
        inline=False
    )
    
    embed.add_field(
        name=f"👑 Commander (의도 분석 및 사령관) — {cmd_disp}",
        value=f"• **현재 배정 모델**: `{cmd_model}`\n• **주요 역할**: 실시간 의도 분석, Single/Pair/Quad 스마트 배차 관제",
        inline=False
    )
    
    try:
        from image_generation_backend import ImageGenerationBackend
        _img_backend_desc = ImageGenerationBackend.describe_backends() or "미구성 (`.env` OPENROUTER_API_KEY 또는 GEMINI_API_KEY 필요)"
        _img_backend_ok = ImageGenerationBackend.real_backend_available()
    except Exception:
        _img_backend_desc = "미구성 (`.env` OPENROUTER_API_KEY 또는 GEMINI_API_KEY 필요)"
        _img_backend_ok = False

    # agy 라벨(예: gemini-3.8-flash-high) → 실제 생성에 쓰이는 Nano Banana 모델 ID 해석 표시
    try:
        from image_creator_hub import ImageCreatorHub
        _img_gen_model = ImageCreatorHub.resolve_backend_model_hint(img_eng, img_model) or img_model
    except Exception:
        _img_gen_model = img_model

    embed.add_field(
        name=f"🎨 Image generator (실제 AI 이미지 생성 도구) — {img_disp}",
        value=(
            f"• **현재 배정 모델**: `{img_model}` ({img_eng.upper()})\n"
            f"• **실제 생성 모델**: `{_img_gen_model}`\n"
            f"• **주요 역할**: 실제 AI 이미지 백엔드로 로고·아이콘·배너·일러스트 생성/수정\n"
            f"• **실제 백엔드**: {'✅ ' if _img_backend_ok else '⚠️ '}{_img_backend_desc}\n"
            f"• **호출 방식**: Coder/QA가 `[IMAGE_REQUEST: ...]` 지시 → 이미지 봇 생성 → Coder가 결과물 바인딩 (`{config.COMMAND_PREFIX}image` 수동 호출 가능)\n"
            f"• **참고**: AI 생성 실패 시 사유를 정직하게 고지하며, 절차적 임시 에셋은 명시 허용 시에만 사용합니다."
        ),
        inline=False
    )

    embed.add_field(
        name=f"🖼️ Vision Analyst (첨부 이미지 분석) — {vis_disp}",
        value=(
            f"• **현재 배정 모델**: `{vis_model}` ({vis_eng.upper()})\n"
            f"• **주요 역할**: 사용자가 첨부한 이미지·스크린샷의 OCR 텍스트, UI/UX 레이아웃, 스타일 토큰을 정밀 분석해 Coder/QA 프롬프트에 주입\n"
            f"• **변경 방법**: `{config.COMMAND_PREFIX}model vision <모델>` (예: `{config.COMMAND_PREFIX}model vision google/gemini-3.8-flash`)\n"
            f"• **참고**: 이미지 생성(🎨 Image generator)과는 별개이며, 분석 실패 시 사유를 정직하게 고지합니다."
        ),
        inline=False
    )

    embed.set_footer(text=f"명령어: {config.COMMAND_PREFIX}model 로 모델 즉시 교체 | {config.COMMAND_PREFIX}help 종합 제어판")
    await ctx.send(embed=embed)

async def execute_all_in_one_hot_reset(channel: discord.abc.Messageable, channel_id: int, arg_text: str = "", is_legacy_clear: bool = False, clear_queue: bool = True, is_auto_reset: bool = False):
    """
    Performs a 100% In-Memory Safe All-in-One Hot-Reset:
    1. Hot-reloads all python helper modules dynamically (reflecting newest code instantly)
    2. Completely resets conversation context (conversation_id, qa/auditor conv_ids, queues, timers)
    3. Cleans up memory (gc.collect) and syncs bot client presences
    4. Delivers unified confirmation embed
    """
    arg_lower = arg_text.lower().strip()
    reset_all_sessions = any(kw in arg_lower for kw in ["all", "session", "full", "전체", "세션", "완전"])
    current_pid = os.getpid()

    status_prefix = "🔄 **[문제 해결 감지 ➔ 자동 !reset 진행 중...]**" if is_auto_reset else "🔄 **[올인원 핫리셋(Hot-Reset) 진행 중...]**"
    status_msg = await channel.send(f"{status_prefix} 최신 코드 핫리로드 및 대화 컨텍스트 초기화를 수행합니다...")

    try:
        # 1. Dynamic module hot-reloading & bot.py function recompilation FIRST
        reloaded_count = reload_helper_modules()

        # 2. Reset conversation context, cancel active tasks, and clear queues
        if reset_all_sessions:
            session_manager.reset_all_conversations(clear_queues=True)
            context_status = "모든 채널의 대화 세션 ID(컨텍스트) 및 대기열이 100% 완전 초기화되었습니다."

            # 2-b. QA Hub 진행 중인 모든 미션 취소 (전체 리셋 시)
            try:
                if qa_hub_manager:
                    qa_hub_manager.stop_missions_for_channel(getattr(qa_hub_manager, "qa_hub_channel_id", 0))
            except Exception as _qe:
                logger.debug(f"qa_hub stop_missions on global reset: {_qe}")
        else:
            # Context initialization for the current channel ONLY (zero impact on other channels)
            if hasattr(session_manager, "reset_channel_session"):
                session_manager.reset_channel_session(channel_id, clear_queue=clear_queue)
            else:
                sess = session_manager.get_session(channel_id)
                if sess:
                    sess.stop()
                    sess.reset_conversation()
                    if clear_queue:
                        sess.clear_queue()
                    sess.status = "idle"
                    sess.status_desc = "정상 대기 중 (리셋 완료)"
                    sess.is_busy = False
                    session_manager.save_state()

            # If the command was executed in the QA Hub channel, stop missions for this channel only
            try:
                if qa_hub_manager and getattr(qa_hub_manager, "qa_hub_channel_id", 0) == channel_id:
                    qa_hub_manager.stop_missions_for_channel(channel_id)
            except Exception as _qe:
                logger.debug(f"qa_hub stop_missions for channel on reset: {_qe}")

            context_status = f"현재 채널(<#{channel_id}>)의 대화 세션 ID(컨텍스트)가 100% 깔끔하게 초기화되었습니다. (다음 대화 시 0토큰의 깨끗한 새 세션 시작)"

        # 3. Garbage Collection to free memory
        import gc
        gc.collect()

        # 4. Resync bot client presences dynamically
        await sync_all_bot_presences()
        if DISCORD_AGY_QA2_BOT_TOKEN and qa2_bot and not qa2_bot.is_ready():
            try:
                logger.info("Attempting to connect uninitialized QA2 Bot during reset...")
                asyncio.create_task(qa2_bot.start(DISCORD_AGY_QA2_BOT_TOKEN))
            except Exception as e_start:
                logger.warning(f"Failed to start QA2 Bot on reset: {e_start}")

        mem_str = "N/A"
        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
            mem_str = f"{mem_mb:.1f} MB"
        except Exception:
            pass

        alias_note = "\n\n*(💡 `!clear` 명령어는 `!reset`으로 완전 통합되었습니다. 앞으로 `!reset` 또는 `!리셋` 하나만 사용하시면 됩니다.)*" if is_legacy_clear else ""
        auto_note = "\n\n*(💡 문제 해결 및 코드 수정이 완료되어 최신 코드 반영과 세션 초기화가 자동 완료되었습니다. 이제 방금 고쳐진 최신 코드로 즉시 동작합니다!)*" if is_auto_reset else ""

        embed_title = "🔄 [문제 해결 감지 · 자동 올인원 핫리셋(!reset) 완료]" if is_auto_reset else "🔄 [게이트웨이 무중단 올인원 핫리셋(Hot-Reset) 완료]"
        embed_desc = (
            f"문제 해결 및 코드 패치가 완료되어 **최신 코드 핫리로드 + 대화 컨텍스트 초기화**가 100% 자동 적용되었습니다! 🚀\n\n"
            f"• ⚡ **최신 코드 반영**: `{reloaded_count}`개 파이썬 모듈 핫리로드 및 최신 로직 즉시 동기화 완료\n"
            f"• 🧹 **컨텍스트 초기화**: {context_status}\n"
            f"• 🧠 **메모리 최적화**: GC 완료 (현재 메모리 점유: `{mem_str}`)\n"
            f"• 💡 **즉시 동작**: 다음 작업부터 방금 수정된 최신 코드가 백지 상태(0토큰)의 깨끗한 세션에서 실행됩니다!{auto_note}"
            if is_auto_reset else
            f"봇 프로세스(`PID: {current_pid}`) 중단 없이 **최신 코드 반영 + 모든 대화 컨텍스트 초기화**가 완벽하게 처리되었습니다! 🚀\n\n"
            f"• ⚡ **최신 코드 반영**: `{reloaded_count}`개 파이썬 모듈 핫리로드 및 `bot.py` 최신 로직 즉시 동기화 완료\n"
            f"• 🧹 **컨텍스트 초기화**: {context_status}\n"
            f"• 🧠 **메모리 최적화**: GC 완료 (현재 메모리 점유: `{mem_str}`)\n"
            f"• 📁 **헌법 보존**: 워크스페이스의 `PRD.md` 및 `PROJECT_CONSTITUTION.md` 헌법은 안전하게 유지됩니다.{alias_note}"
        )

        embed = discord.Embed(
            title=embed_title,
            description=embed_desc,
            color=discord.Color.green()
        )
        embed.set_footer(text="AGY Discord Vibe Coding Gateway · Auto Hot-Reset on Resolve" if is_auto_reset else "AGY Discord Vibe Coding Gateway · All-in-One Safe Hot-Reset")

        await status_msg.edit(content="", embed=embed)

    except Exception as e:
        logger.exception(f"Error during safe in-memory hot reset: {e}")
        await status_msg.edit(content=f"⚠️ 올인원 핫리셋 중 오류가 발생했습니다: {e}")


# Alias for backward compatibility and QA report alignment
perform_master_reset = execute_all_in_one_hot_reset


@bot.command(name="reset", aliases=["reload", "restart", "hotreload", "리셋", "재시작", "초기화", "핫리셋"])
async def reset_command(ctx, *args):
    """최신 코드 반영 + 모든 대화 컨텍스트 초기화를 한 번에 수행하는 단일 올인원 리셋 명령어"""
    arg_text = " ".join(args)
    await execute_all_in_one_hot_reset(ctx.channel, ctx.channel.id, arg_text=arg_text, is_legacy_clear=False)


@bot.command(name="new", aliases=["clear", "clean", "cls", "대화초기화", "세션초기화", "새세션", "새대화", "비우기"], hidden=True)
async def new_session(ctx, *args):
    """레거시 clear/new 명령어도 !reset과 동일하게 최신 코드 반영 + 컨텍스트 초기화 일괄 수행"""
    arg_text = " ".join(args)
    await execute_all_in_one_hot_reset(ctx.channel, ctx.channel.id, arg_text=arg_text, is_legacy_clear=True)

def recommend_preset_for_channel(channel_name: str, workspace_path: str) -> tuple:
    """Analyzes channel name and workspace directory to recommend the best project preset."""
    ch_lower = channel_name.lower()

    # 0. Game (가장 구체적 → 우선 판정: 채널명에 game/windows가 있으면 app보다 우선)
    if any(k in ch_lower for k in ["game", "게임", "arcade", "아케이드", "puzzle", "퍼즐", "roguelike", "로그라이크", "unity", "godot"]):
        return "game", "🎮 게임 (Web Canvas/WebGL · Unity · Godot · Pygame)", "실 입력→렌더 루프 검증 + 연속 프레임 캡처 + 60fps 프레임 예산 벤치마크"

    # 0-1. Windows Native Desktop
    if any(k in ch_lower for k in ["windows", "윈도우", "win", "wpf", "winui", "desktop", "데스크톱", "exe"]):
        return "windows", "🪟 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6)", "단위 테스트 + Windows VM 실 창 프레임버퍼 캡처 (고DPI/리사이즈/다크모드) 무결성 검증"

    # 1. Mobile / Android native
    if any(k in ch_lower for k in ["park", "rider", "android", "apk", "mobile", "app", "gps", "bluetooth"]):
        return "app", "📱 모바일 & 안드로이드 네이티브 앱", "Android KVM 풀 에뮬레이터 OS 프레임버퍼 검증 + Gemini Vision 모바일 UI/UX (버튼 간격, 48dp 터치, 가독성, 심미성) 시각 검증, 가상 하드웨어(!sim), APK 자동 빌드 최적화"
    
    # 2. Commerce / Store / Landing
    if any(k in ch_lower for k in ["phonecase", "smartcard", "card", "store", "shop", "trade", "idus", "biz", "commerce", "landing"]):
        return "store", "🛍️ 커머스 / AI명함 / 스토어 랜딩", "구매/생성 여정 이탈 방지(CRO), 카피라이팅, SEO / SNS 공유 썸네일 진단"
    
    # 3. Planning / Research / Consult
    if any(k in ch_lower for k in ["question", "research", "consult", "plan", "기획", "자문", "리서치"]):
        return "read", "🔍 기획 / 시장 리서치 & 전략 자문", "100% 안전한 무수정 아키텍처 자문 및 심층 시장 조사"

    # 4. Check workspace files
    if workspace_path and os.path.isdir(workspace_path):
        if os.path.exists(os.path.join(workspace_path, "build.gradle")) or os.path.exists(os.path.join(workspace_path, "android")):
            return "app", "📱 모바일 & 안드로이드 네이티브 앱", "Android KVM 풀 에뮬레이터 OS 프레임버퍼 검증 + Gemini Vision 모바일 UI/UX (버튼 간격, 48dp 터치, 가독성, 심미성) 시각 검증, 가상 하드웨어(!sim), APK 자동 빌드 최적화"
        if os.path.exists(os.path.join(workspace_path, "project.godot")) or os.path.exists(os.path.join(workspace_path, "Assets")):
            return "game", "🎮 게임 (Web Canvas/WebGL · Unity · Godot · Pygame)", "실 입력→렌더 루프 검증 + 연속 프레임 캡처 + 60fps 프레임 예산 벤치마크"
        try:
            if any(f.endswith(".sln") or f.endswith(".csproj") for f in os.listdir(workspace_path)):
                return "windows", "🪟 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6)", "단위 테스트 + Windows VM 실 창 프레임버퍼 캡처 (고DPI/리사이즈/다크모드) 무결성 검증"
        except Exception:
            pass

    # Default
    return "web", "🌐 웹 & SaaS 풀스택 개발", "Playwright 반응형 4대 기기 E2E 검증 및 라이브 URL 배포 자동 패치"

def apply_project_preset(channel_id: int, preset_key: str) -> discord.Embed:
    session = session_manager.get_session(channel_id)

    key = preset_key.lower().strip()
    if key in ("web", "웹", "saas", "frontend", "풀스택", "1"):
        session.set_mode("auto")
        
        embed = discord.Embed(
            title="🌐 [웹 / SaaS 풀스택 프로젝트 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `웹 & SaaS 풀스택 개발 모드`\n\n"
                f"• 🤖 **채널 동작 모드**: `Auto` (질의는 Single, 기능개발은 3중 Pair 자동 선택)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 2: 자율 패치)` - 10분 유휴 시 기동\n"
                f"• 🧪 **QA 엔진**: `Playwright` 4대 디바이스 반응형 & 6-TC 인터랙션 검증\n"
                f"• 🚀 **배포 연동**: Firebase/Vercel 라이브 URL 자동 추출 및 E2E 실시간 순환\n\n"
                f"💡 *팁: 언제든 `{COMMAND_PREFIX}setup`을 입력해 다른 프리셋으로 즉시 전환할 수 있습니다.*"
            ),
            color=discord.Color.blue()
        )
    elif key in ("app", "앱", "mobile", "android", "모바일", "안드로이드", "2"):
        session.set_mode("pair")
        
        embed = discord.Embed(
            title="📱 [모바일 / 안드로이드 네이티브 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `모바일 & 안드로이드 네이티브 앱 모드`\n\n"
                f"• 👥 **채널 동작 모드**: `Pair` (Coder ↔ QA ↔ Auditor 상시 3중 협업)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 3: 자율 발전)` - 10분 유휴 시 기동\n"
                f"• ⚡ **QA 엔진**: `Android KVM 풀 에뮬레이터` OS 프레임버퍼 + `Gemini Vision` UI/UX (버튼 간격/48dp/디자인) 전수 검증\n"
                f"• 📱 **가상 스튜디오**: `{COMMAND_PREFIX}sim` 60fps 인터랙티브 하드웨어(GPS/블루투스) 시뮬레이션 지원\n"
                f"• 📦 **바이너리**: 워크스페이스 내 APK 자동 빌드 및 `{COMMAND_PREFIX}apk` 즉시 다운로드"
            ),
            color=discord.Color.green()
        )
    elif key in ("store", "스토어", "biz", "commerce", "커머스", "명함", "폰케이스", "landing", "랜딩", "3"):
        session.set_mode("auto")
        
        embed = discord.Embed(
            title="🛍️ [커머스 / AI명함 / 스토어 랜딩 프리셋 적용 완료]",
            description=(
                "**선택된 프리셋:** `비즈니스 전환율(CRO) & 성장 관찰 모드`\n\n"
                "• 🤖 **채널 동작 모드**: `Auto` (안전 지향 자율 운영)\n"
                "• 👑 **오토파일럿**: `ON (Phase 1: 무위험 시각 관찰 & PO 감사)` - 10분 유휴 시 기동\n"
                "• 🔍 **성과/품질 진단**: 구매/생성 여정 이탈 요인, 카피라이팅, 신뢰도(Social Proof) 평가\n"
                "• 🌐 **마케팅 최적화**: 모바일 터치 최적화 & SNS 공유 썸네일(OG Tag) 상시 모니터링"
            ),
            color=discord.Color.gold()
        )
    elif key in ("read", "기획", "자문", "research", "리서치", "consult", "readonly", "4"):
        session.set_mode("read")
        
        embed = discord.Embed(
            title="🔍 [기획 / 시장 리서치 & 전략 자문 프리셋 적용 완료]",
            description=(
                "**선택된 프리셋:** `3중 읽기/기획 전용 아키텍처 모드`\n\n"
                "• 🔍 **채널 동작 모드**: `Read (3중 읽기 전용)` (코드 수정 없이 3대 두뇌가 방향성 자문)\n"
                "• 🛑 **오토파일럿**: `OFF` (100% 안전한 질의응답 전용 보장)\n"
                "• 📑 **리서치 엔진**: 심층 시장 조사, 경쟁사 벤치마킹, 하드웨어/스펙 비교 리포트 특화"
            ),
            color=discord.Color.purple()
        )
    elif key in ("game", "게임", "mobilegame", "retrogame", "unity", "godot", "unreal",
                 "3d", "webgl", "canvas", "arcade", "아케이드", "퍼즐", "5"):
        session.set_mode("pair")

        embed = discord.Embed(
            title="🎮 [게임 프로젝트 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `게임 (Web Canvas/WebGL · Unity · Godot · Pygame) 모드`\n\n"
                f"• 👥 **채널 동작 모드**: `Pair` (Coder ↔ QA ↔ Auditor 3중 협업)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 3: 자율 발전)` - 10분 유휴 시 기동\n"
                f"• 🧪 **QA 엔진**: 실 입력→렌더 루프 검증 + 연속 프레임 캡처 + 60fps 프레임 예산 벤치마크\n"
                f"• 🕹️ **코어 루프**: 시작→플레이→스코어→난이도 상승→게임오버→즉시 재도전 완결 강제\n"
                f"• 🎯 **HUD 표준**: 엣지 5% 안전영역 · 거리 가독성 · 색맹 친화(형태+색) · 일시정지/설정 UI"
            ),
            color=discord.Color.magenta()
        )
    elif key in ("win", "windows", "윈도우", "wpf", "winui", "winforms", "pyqt", "pyside", "데스크톱", "6"):
        session.set_mode("pair")

        embed = discord.Embed(
            title="🪟 [윈도우 네이티브 데스크톱 프리셋 적용 완료]",
            description=(
                f"**선택된 프리셋:** `윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6) 모드`\n\n"
                f"• 👥 **채널 동작 모드**: `Pair` (Coder ↔ QA ↔ Auditor 3중 협업)\n"
                f"• 👑 **오토파일럿**: `ON (Phase 3: 자율 발전)` - 10분 유휴 시 기동\n"
                f"• 🧪 **QA 엔진**: 단위 테스트 + Windows VM 실 창 프레임버퍼 캡처 (고DPI/리사이즈/다크모드)\n"
                f"• 🪟 **Fluent Design**: 타이틀바/리사이즈 · 키보드 단축키 · 스냅 레이아웃 · 고DPI 200% 무결성\n"
                f"• 📦 **바이너리**: `dotnet publish`/`pyinstaller` → `.exe` 및 설치 패키지"
            ),
            color=discord.Color.dark_teal()
        )
    else:
        session.set_mode("auto")
        
        embed = discord.Embed(
            title="⚙️ [기본 표준 프리셋 적용 완료]",
            description="채널이 기본 표준 모드로 설정되었습니다.",
            color=discord.Color.dark_grey()
        )

    embed.set_footer(text=f"AGY Project Initializer · 언제든 {COMMAND_PREFIX}setup 명령어로 다시 변경할 수 있습니다.")
    return embed

class ProjectSetupView(discord.ui.View):
    def __init__(self, channel_id: int, user_id: int):
        super().__init__(timeout=180)
        self.channel_id = channel_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("⚠️ 이 설정 카드를 실행한 사용자만 버튼을 누를 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🌐 웹 / SaaS 풀스택", style=discord.ButtonStyle.primary, emoji="🌐", custom_id="btn_preset_web", row=0)
    async def btn_web(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "web")
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="📱 모바일 / 안드로이드 앱", style=discord.ButtonStyle.success, emoji="📱", custom_id="btn_preset_app", row=0)
    async def btn_app(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "app")
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🛍️ 커머스 / AI명함 / 스토어", style=discord.ButtonStyle.secondary, emoji="🛍️", custom_id="btn_preset_store", row=0)
    async def btn_store(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "store")
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🔍 기획 / 시장 리서치", style=discord.ButtonStyle.secondary, emoji="🔍", custom_id="btn_preset_read", row=1)
    async def btn_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "read")
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🎮 게임 (WebGL/Unity/Godot)", style=discord.ButtonStyle.danger, emoji="🎮", custom_id="btn_preset_game", row=1)
    async def btn_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "game")
        await interaction.response.edit_message(content="", embed=embed, view=None)

    @discord.ui.button(label="🪟 윈도우 네이티브 데스크톱", style=discord.ButtonStyle.primary, emoji="🪟", custom_id="btn_preset_windows", row=1)
    async def btn_windows(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = apply_project_preset(self.channel_id, "windows")
        await interaction.response.edit_message(content="", embed=embed, view=None)

@bot.command(name="setup", aliases=["셋업", "설정", "init", "preset", "프리셋", "초기설정"], hidden=True)
async def setup_command(ctx, preset_key: str = ""):
    """
    Project Initializer: Intelligently configures channel mode and QA engines
    tailored for specific project categories (Web, Mobile App, Store/Landing, Research).
    """
    reload_helper_modules()
    session = session_manager.get_session(ctx.channel.id)
    ch_name = getattr(ctx.channel, "name", f"channel_{ctx.channel.id}")

    if preset_key:
        embed = apply_project_preset(ctx.channel.id, preset_key)
        await ctx.send(embed=embed)
        return

    rec_key, rec_label, rec_desc = recommend_preset_for_channel(ch_name, session.workspace)

    embed = discord.Embed(
        title="🚀 [AGY 프로젝트 맞춤형 원클릭 셋업 (Project Initializer)]",
        description=(
            f"현재 채널(**`#{ch_name}`**)의 워크스페이스와 성격에 맞춰 **최적의 동작 모드, 오토파일럿 주기, QA 검증 엔진**을 1클릭으로 구성합니다.\n\n"
            f"🎯 **AI 스마트 추천:**\n"
            f"> **{rec_label}**\n"
            f"> *{rec_desc}*\n\n"
            f"아래 버튼 중 프로젝트 성격에 맞는 카테고리를 선택해주세요:"
        ),
        color=discord.Color.gold()
    )
    embed.add_field(
        name="1. 🌐 웹 / SaaS 풀스택",
        value="• 모드: `Auto` | 오토파일럿: `Phase 2 자율 패치`\n• QA: `Playwright 4대 기기 반응형 & 6-TC`\n• 용도: 웹앱, 대시보드, 풀스택 서비스",
        inline=False
    )
    embed.add_field(
        name="2. 📱 모바일 / 안드로이드 앱",
        value="• 모드: `Pair 3중 협업` | 오토파일럿: `Phase 3 자율 발전`\n• QA: `Android KVM 풀 에뮬레이터 + !sim + APK`\n• 용도: 안드로이드 네이티브 앱, 백그라운드 서비스",
        inline=False
    )
    embed.add_field(
        name="3. 🛍️ 커머스 / AI명함 / 스토어 랜딩",
        value="• 모드: `Auto` | 오토파일럿: `Phase 1 무위험 시각 관찰`\n• QA: `전환율(CRO) 퍼널 + 카피라이팅 + SEO 썸네일`\n• 용도: AI 스마트 명함, 아이디어스 폰케이스, 랜딩페이지",
        inline=False
    )
    embed.add_field(
        name="4. 🔍 기획 / 시장 리서치 & 자문",
        value="• 모드: `Read (읽기 전용)` | 오토파일럿: `OFF`\n• QA: `3중 아키텍트 교차 분석 & 논문/시장 리서치`\n• 용도: 기획서 작성, 방향성 자문, 시장 분석",
        inline=False
    )
    embed.add_field(
        name="5. 🎮 게임 (Web Canvas/WebGL · Unity · Godot · Pygame)",
        value="• 모드: `Pair 3중 협업` | 오토파일럿: `Phase 3 자율 발전`\n• QA: `실 입력→렌더 루프 + 연속 프레임 캡처 + 60fps 벤치마크`\n• 용도: 캐주얼/아케이드/로그라이크/퍼즐 게임",
        inline=False
    )
    embed.add_field(
        name="6. 🪟 윈도우 네이티브 데스크톱 (WPF/WinUI 3 · PyQt6)",
        value="• 모드: `Pair 3중 협업` | 오토파일럿: `Phase 3 자율 발전`\n• QA: `단위 테스트 + Windows VM 실 창 프레임버퍼 (고DPI/다크모드)`\n• 용도: 윈도우 데스크톱 유틸리티, 생산성 앱",
        inline=False
    )
    embed.set_footer(text=f"명령어로 직접 설정: {COMMAND_PREFIX}setup web | app | store | read | game | windows")

    view = ProjectSetupView(channel_id=ctx.channel.id, user_id=ctx.author.id)
    await ctx.send(embed=embed, view=view)

@bot.command(name="mode", aliases=["모드", "동작모드"])
async def mode_command(ctx, *, mode_str: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    all_ext_cfgs = config.get_extended_qa_configs()
    ext_cfgs = session.get_active_extended_qas(all_ext_cfgs) if hasattr(session, "get_active_extended_qas") else all_ext_cfgs
    if ext_cfgs:
        pair_chain_names = " ↔ ".join([f"{c['name'].lower()}" for c in ext_cfgs])
        pair_plus_chain_str = f"Coder ↔ qa1 ↔ {pair_chain_names} ↔ Auditor"
        mode_label = f"{len(ext_cfgs) + 3}중 다중" if len(ext_cfgs) > 1 else "4중 쿼드"
    else:
        pair_plus_chain_str = "Coder ↔ qa1 ↔ Auditor"
        mode_label = "3중"

    if not mode_str:
        cur_desc = get_mode_description(session.mode, session)
        qa2_model = getattr(config, "QA2_MODEL", "inclusionai/ling-3.0-flash-fin:free")
        qa_selection_info = ""
        if getattr(session, "selected_qa_indices", None):
            sel_names = ", ".join([f"qa{i}" for i in session.selected_qa_indices])
            qa_selection_info = f"\n🎯 **현재 채널 QA 지정**: `{sel_names}`만 활성화 (`!qa all`로 초기화 가능)"

        await ctx.send(
            f"⚙️ **현재 채널 동작 모드:** {cur_desc}{qa_selection_info}\n\n"
            f"**모드 설정 방법:**\n"
            f"• `{COMMAND_PREFIX}mode 1` (`{COMMAND_PREFIX}mode single`) : 👤 **단일 에이전트** (초고속 단일 답변)\n"
            f"• `{COMMAND_PREFIX}mode 2` (`{COMMAND_PREFIX}mode pair`) : 👥 **사용자 주도 3중 협업** (Coder ↔ QA ↔ Auditor 단일 턴 완결 후 사용자 대기)\n"
            f"• `{COMMAND_PREFIX}mode 2+` (`{COMMAND_PREFIX}mode pair+`) : 👥 **사용자 주도 {mode_label} 협업** ({pair_plus_chain_str} 단일 과제 완결 후 사용자 대기)\n"
            f"  - 💡 **특정 QA만 지정**: `{COMMAND_PREFIX}mode 2+ qa2`, `{COMMAND_PREFIX}mode 2+ 2,3`, `{COMMAND_PREFIX}mode 2+ all` (전체 복원)\n"
            f"• `{COMMAND_PREFIX}mode 3` (`{COMMAND_PREFIX}mode auto_trio`) : 🤖 **무인 자율 순환 3중 협업** (User ➔ Commander 총괄 기획 ➔ Coder ↔ QA ↔ Auditor ➔ Commander 차기 마일스톤 자동 순환)\n"
            f"• `{COMMAND_PREFIX}mode 3+` (`{COMMAND_PREFIX}mode auto_multi`) : 🤖 **무인 자율 순환 {mode_label} 협업** (User ➔ Commander 총괄 기획 ➔ {pair_plus_chain_str} ➔ Commander 차기 마일스톤 자동 순환)\n"
            f"  - 💡 **특정 QA만 지정**: `{COMMAND_PREFIX}mode 3+ qa2`, `{COMMAND_PREFIX}mode 3+ 2,3`, `{COMMAND_PREFIX}mode 3+ all` (전체 복원)\n"
            f"• `{COMMAND_PREFIX}mode single_qa2` (`{COMMAND_PREFIX}mode qa2`) : 🥷 **단일 qa2 에이전트** (1545711652987600937 전용 동작)\n"
            f"• `{COMMAND_PREFIX}mode auto` : 🤖 **스마트 자동 모드 (추천)** (질의/경량은 Single, 개발/수정은 협업 자동 선택)\n"
            f"• `{COMMAND_PREFIX}mode read` : 🔍 **읽기/기획 전용** (코드 수정 없이 아키텍처·방향성 자문)\n"
            f"• `{COMMAND_PREFIX}mode all [1|2|2+|3|3+|auto|read]` : 🌐 **모든 채널 일괄 변경**"
        )
        return

    clean_arg = mode_str.lower().strip()
    cat_id = getattr(ctx.channel, "category_id", None) or (ctx.channel.category.id if hasattr(ctx.channel, "category") and ctx.channel.category else None)
    if cat_id == FINISH_PROJECTS_CATEGORY_ID and clean_arg:
        if clean_arg not in ("read", "readonly", "read-only", "consult", "consulting", "plan", "기획", "자문", "읽기", "방향성"):
            await ctx.send("⚠️ 이 채널은 **finish projects** 완료 아카이브 카테고리에 속해 있어 `!mode read`로 고정동결되어 있습니다. (다른 모드로 변경하려면 채널을 다른 카테고리로 이동해주세요.)")
            return

    valid_mode_roots = (
        "auto", "automatic", "smart", "ai", "자율", "자동", "오토",
        "read", "readonly", "read-only", "consult", "consulting", "plan", "planning", "읽기", "기획", "자문", "방향성",
        "pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스", "3+", "3plus", "3플러스", "mode3_plus", "mode3+", "자율3+", "무인3+",
        "single_qa2", "qa2+", "qa2_plus", "qa2", "qa2_solo", "solo_qa2",
        "pair", "trio", "dual", "team", "collab", "3중", "페어", "협업", "2", "3", "mode3", "자율3", "무인3", "3자율", "3차자율",
        # 아래 1+ 계열은 '제거된 레거시 컨설턴트 모드'의 무중단 호환 별칭이다(기능 아님).
        # 모두 !mode 1(단일 에이전트)로 수렴한다.
        "single_plus", "single+", "1+", "1plus", "1플러스", "single_consultant", "consultant", "컨설턴트",
        "single", "solo", "단일", "싱글", "1",
    )
    first_tok = clean_arg.split()[0] if clean_arg else ""
    is_valid_mode = (
        clean_arg in valid_mode_roots
        or first_tok in valid_mode_roots
        or clean_arg.replace(" ", "") in valid_mode_roots
        # 🎛️ SSOT 추가 검증: 레지스트리가 인식하는 모든 별칭(예: auto_multi, trio_auto 등) 허용
        or mode_registry.is_known_mode(clean_arg)
        or mode_registry.is_known_mode(first_tok)
        or clean_arg.startswith(("all", "전체"))
    )
    if not is_valid_mode:
        await ctx.send(
            f"⚠️ 지원되지 않는 모드입니다: `{clean_arg}`\n"
            f"💡 **지원 모드**: `{COMMAND_PREFIX}mode 1` (단일), `{COMMAND_PREFIX}mode 2` (사용자 주도 3중), `{COMMAND_PREFIX}mode 2+` (사용자 주도 {mode_label}), `{COMMAND_PREFIX}mode 3` (무인 자율 3중), `{COMMAND_PREFIX}mode 3+` (무인 자율 {mode_label}), `{COMMAND_PREFIX}mode auto` (스마트 자동), `{COMMAND_PREFIX}mode read` (읽기/기획 전용)"
        )
        return

    if clean_arg.startswith("all ") or clean_arg.startswith("전체 ") or clean_arg == "all" or clean_arg == "전체":
        parts = clean_arg.split(maxsplit=1)
        target_mode = parts[1] if len(parts) > 1 else "auto"
        session_manager.set_all_modes(target_mode)
        target_desc = get_mode_description(target_mode)
        await ctx.send(f"🌐 **모든 채널({len(session_manager._sessions)}개)**의 동작 모드가 {target_desc}(으)로 일괄 변경되었습니다! 🚀")
        return

    # Check for QA targeted specification in mode command (e.g. "!mode 2+ qa2", "!mode 3+ qa2", "!mode 2+ all")
    qa_selected_notice = ""
    parts = clean_arg.split(maxsplit=1)
    if len(parts) == 2:
        candidate_mode = parts[0]
        qa_spec = parts[1]
        if candidate_mode in ("pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "2플러스", "3+", "3plus", "3플러스", "mode3_plus") or mode_registry.is_multi_qa_mode(candidate_mode):
            clean_arg = candidate_mode
            indices, sel_cfgs, summary_label = config.parse_qa_selection(qa_spec, all_ext_cfgs)
            session.set_selected_qa_indices(indices)
            if indices:
                qa_selected_notice = f"\n🎯 **확장 QA 필터 지정**: {summary_label} (총 {len(sel_cfgs)}대 참여)"
            else:
                qa_selected_notice = f"\n🔄 **확장 QA 초기화**: 등록된 전체 {len(sel_cfgs)}대 확장 QA가 모두 참여합니다."

    session.set_mode(clean_arg)
    if not qa_selected_notice and getattr(session, "selected_qa_indices", None) and session.mode == "pair_quad":
        sel_names = ", ".join([f"qa{i}" for i in session.selected_qa_indices])
        qa_selected_notice = f"\n🎯 **현재 채널 QA 필터 적용 중**: `{sel_names}`만 참여 (💡 전체 활성 QA 복원: `{COMMAND_PREFIX}mode 2+ all` 또는 `{COMMAND_PREFIX}qa all`)"
    new_desc = get_mode_description(session.mode, session)

    await ctx.send(f"✅ 채널 동작 모드가 {new_desc}(으)로 변경되었습니다!{qa_selected_notice}")

@bot.command(name="qa", aliases=["qastatus", "qa_status", "다중qa", "qa목록"], hidden=True)
async def qa_command(ctx, *, arg: str = ""):
    """
    Displays the current status of all configured QA Reviewers (qa1 and Extended qa2..qa10),
    or configures which Extended QAs are active for this channel.
    Usage:
      !qa                      -> 현황 임베드 조회
      !qa select qa2 / !qa 2   -> qa2만 단독 지정
      !qa select 2,3           -> qa2, qa3 복수 지정
      !qa reset / !qa all      -> 전체 확장 QA 복원
    """
    reload_helper_modules()
    if "init_extended_qa_bots" in globals() and callable(globals()["init_extended_qa_bots"]):
        init_extended_qa_bots()
    session = session_manager.get_session(ctx.channel.id)
    all_ext_cfgs = config.get_extended_qa_configs()

    clean = arg.strip().lower() if arg else ""

    # Check if this is an interactive QA selection/filtering command
    if clean:
        for prefix in ("select ", "use ", "set ", "filter ", "선택 ", "필터 ", "지정 "):
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
                break

        indices, selected_cfgs, summary_label = config.parse_qa_selection(clean, all_ext_cfgs)
        session.set_selected_qa_indices(indices)

        if indices:
            chain_str = " ➔ ".join([f"{c['name'].lower()}" for c in selected_cfgs])
            quad_label = f"{len(selected_cfgs) + 3}중 다중" if len(selected_cfgs) > 1 else ("4중 쿼드" if len(selected_cfgs) == 1 else "3중")
            await ctx.send(
                f"✅ **[QA 지정 완료]** 이 채널의 확장 QA가 **{summary_label}** (총 {len(selected_cfgs)}대)로 지정되었습니다!\n"
                f"🔗 **협업 체인 ({quad_label})**: Coder ➔ qa1 ➔ {chain_str} ➔ Auditor\n"
                f"💡 다시 전체 QA를 사용하려면 `{COMMAND_PREFIX}qa all` 또는 `{COMMAND_PREFIX}mode 2+ all`을 입력하세요."
            )
        else:
            await ctx.send(
                f"🔄 **[QA 초기화 완료]** 이 채널의 확장 QA가 **기본 전체 목록** ({len(selected_cfgs)}대 전수)으로 복원되었습니다!\n"
                f"🔗 **협업 체인**: Coder ➔ qa1 ➔ {' ➔ '.join([c['name'].lower() for c in selected_cfgs])} ➔ Auditor"
            )
        return

    # No argument: Show rich embed status
    ext_cfgs = all_ext_cfgs
    active_cfgs = session.get_active_extended_qas(all_ext_cfgs)
    is_valid_key, key_msg = config.validate_openrouter_setup()

    embed = discord.Embed(
        title="🛡️ [AGY QA 리뷰어 및 교차 검증 엔진 현황]",
        description="동적 다중 QA 레지스트리(Plug-and-Play Dynamic QA) 상태 및 채널 타깃 설정 내역입니다.",
        color=discord.Color.blue() if is_valid_key else discord.Color.orange()
    )

    # QA1 (Primary)
    qa1_model = getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
    qa1_bot_status = f"✅ 연동 완료 (`@{qa_bot.user.name}`)" if (qa_bot and qa_bot.is_ready() and qa_bot.user) else "🌐 메인 채널 대행"
    embed.add_field(
        name="1️⃣ qa1 (기본)",
        value=(
            f"• **모델**: `{qa1_model}`\n"
            f"• **엔진**: `agy CLI (Gemini)`\n"
            f"• **봇 클라이언트**: {qa1_bot_status}\n"
            f"• **참여 모드**: 모든 협업 모드 (`!mode 2`, `!mode 2+`, `!mode 3`, `!mode 3+`)"
        ),
        inline=False
    )

    # Extended QAs (QA2..QA10)
    if ext_cfgs:
        ext_lines = []
        for cfg in ext_cfgs:
            idx = cfg["index"]
            name = cfg["name"]
            model = cfg["model"]
            engine = cfg["engine"]
            bot_client = globals().get("extended_qa_bots", {}).get(idx)
            if bot_client and bot_client.is_ready() and bot_client.user:
                bot_stat = f"✅ 연동 완료 (`@{bot_client.user.name}`)"
            elif cfg.get("token"):
                bot_stat = "⚠️ 토큰 등록됨 (연결 준비 중)"
            else:
                bot_stat = "🌐 메인 채널 대행 (토큰 미설정)"

            is_sel = (idx in session.selected_qa_indices) if getattr(session, "selected_qa_indices", None) else True
            sel_badge = "🟢 활성" if is_sel else "⚪ 대기"
            ext_lines.append(
                f"• [{sel_badge}] **{name}**: `{model}` | 엔진: `{engine}` | 봇: {bot_stat}"
            )
        embed.add_field(
            name=f"🔢 확장 QA 리뷰어 ({len(ext_cfgs)}개 등록 / {len(active_cfgs)}개 현재 채널 활성)",
            value="\n".join(ext_lines) + "\n*참여 모드: `!mode 2+` / `!mode 3+` (pair+)*",
            inline=False
        )
    else:
        embed.add_field(
            name="🔢 확장 QA 리뷰어",
            value="⚠️ 현재 활성화된 확장 QA가 없습니다. (.env에 `QA2_MODEL` 등을 설정하세요)",
            inline=False
        )

    # OpenRouter API Key Status
    embed.add_field(
        name="🔑 OpenRouter 인증 상태",
        value=key_msg,
        inline=False
    )

    # Current Channel Status
    cur_mode_desc = get_mode_description(session.mode, session)
    is_multi_active = mode_registry.is_multi_qa_mode(session.mode)
    mode_status = f"🟢 활성 ({len(active_cfgs)}대 확장 QA 참여)" if is_multi_active else "⚪ 비활성 (qa1 단독 모드, 활성화: `!mode 2+` / `!mode 3+`)"

    sel_desc = "전체 참여 (기본)"
    if getattr(session, "selected_qa_indices", None):
        sel_desc = ", ".join([f"qa{i}" for i in session.selected_qa_indices]) + " (지정 활성)"

    embed.add_field(
        name="📍 현재 채널 적용 현황",
        value=(
            f"• **채널 모드**: {cur_mode_desc}\n"
            f"• **다중 교차 검증 상태**: {mode_status}\n"
            f"• **채널 활성 QA**: `{sel_desc}`\n"
            f"• **QA 지정 방법**: `{COMMAND_PREFIX}qa select qa2` (qa2만), `{COMMAND_PREFIX}qa 2,3` (복수 지정), `{COMMAND_PREFIX}qa reset` (전체 복원)"
        ),
        inline=False
    )

    embed.set_footer(text="💡 !mode 2+ qa2 또는 !mode 3+ qa2 또는 !qa select qa2 명령으로 원하는 QA 모델만 핀포인트 지정 가능합니다.")
    await ctx.send(embed=embed)

@bot.command(name="read", aliases=["기획", "자문", "방향성", "consult", "readonly", "아키텍처"], hidden=True)
async def read_command(ctx, *, prompt: str = ""):
    if not prompt:
        await ctx.send(f"⚠️ 방향성을 모색할 질문/요청을 입력해주세요. 예: `{COMMAND_PREFIX}read 앞으로 DB 구조를 어떻게 마이그레이션하면 좋을까?`")
        return

    session = session_manager.get_session(ctx.channel.id)
    if session.is_busy:
        await ctx.send("⏳ 현재 다른 작업을 처리 중입니다. 완료 후 요청해주시거나 `!stop`으로 중단할 수 있습니다.")
        return

    status_msg = await ctx.send("🔍 **[3중 읽기/기획 전용 가동] Architect ↔ Reviewer ↔ Strategist 방향성 자문을 시작합니다...**")
    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
    on_status_update = status_updater.get_callback(prefix="🔍 **[3중 읽기/기획 자문 진행 중]**")

    session.current_task = asyncio.current_task()

    try:
        reload_helper_modules()
        async with ctx.channel.typing():
            final_response = await ConsultingRunner.run_pipeline(
                session=session,
                prompt=prompt,
                main_channel=ctx.channel,
                qa_client=qa_bot,
                auditor_client=auditor_bot,
                on_status_update=on_status_update,
                max_rounds=2
            )

        await status_updater.delete()

        if ConsultingRunner.is_system_error(final_response):
            await safe_send_discord_message(ctx, final_response, is_reply=True)

    except asyncio.CancelledError:
        logger.info(f"Task for channel {ctx.channel.id} was cancelled.")
        await ctx.send("🛑 **사용자에 의해 작업이 강제 중단되었습니다.**")
    except Exception as e:
        logger.exception(f"Error during read consulting execution for channel {ctx.channel.id}: {e}")
        await ctx.send(f"⚠️ 작업 실행 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, "정상 대기 중")
        session.current_task = None

@bot.command(name="single", aliases=["solo", "단일", "싱글", "single+", "single_plus", "1+", "1plus", "1플러스"], hidden=True)
async def single_command(ctx, *, prompt: str = ""):
    if not prompt:
        await ctx.send(f"⚠️ 요청 내용을 입력해주세요. 예: `{COMMAND_PREFIX}single 간단한 파이썬 스크립트 짜줘`")
        return

    session = session_manager.get_session(ctx.channel.id)
    if session.is_busy:
        await ctx.send("⏳ 현재 다른 작업을 처리 중입니다. 완료 후 요청해주시거나 `!stop`으로 중단할 수 있습니다.")
        return

    try:
        from model_manager import GlobalModelManager
    except Exception:
        GlobalModelManager = None

    coder_disp = GlobalModelManager.get_role_model_display("coder") if GlobalModelManager else "Coder"
    coder_m = GlobalModelManager.get_role_model("coder") if GlobalModelManager else getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
    coder_eng = GlobalModelManager.get_role_engine("coder") if GlobalModelManager else getattr(config, "DEFAULT_AI_ENGINE", "agy")

    status_msg = await ctx.send(f"👨‍💻 **[단일 Coder({coder_disp}) 가동] 초고속 단일 작업을 시작합니다...**")
    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
    on_status_update = status_updater.get_callback(prefix=f"👨‍💻 **[Coder({coder_disp}) 작업 중]**")

    # Cancel any active idle timer on new user prompt

    session.current_task = asyncio.current_task()
    session.set_busy(True, f"👨‍💻 AGY 단일 Coder({coder_disp}) 작업 진행 중 (수동 고정)")

    try:
        reload_helper_modules()
        async with ctx.channel.typing():
            final_response = await AgentRunner.run_prompt(
                session=session,
                prompt=prompt,
                on_tool_update=on_status_update,
                model=coder_m,
                engine=coder_eng
            )
            session.set_status("idle", "🟢 작업 완료 (정상 대기)")

        await status_updater.delete()

        chunks, file_attachments = await async_prepare_message_payload(
            final_response,
            workspace_root=session.workspace,
            auto_scan_deliverables=False,
            excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
            excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
        )

        if len(chunks) > 3:
            summary_header = chunks[0]
            import tempfile
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tf:
                tf.write(final_response)
                temp_resp_file = tf.name

            try:
                files_payload = [discord.File(temp_resp_file, filename="response.md")] + file_attachments
                await safe_send_discord_message(
                    ctx.message,
                    f"{summary_header}\n\n📎 **[전체 결과가 길어 파일로 첨부합니다]**",
                    files=files_payload,
                    is_reply=True
                )
            finally:
                try:
                    os.remove(temp_resp_file)
                except Exception:
                    pass
        else:
            for i, chunk in enumerate(chunks):
                files_to_send = file_attachments if i == 0 else []
                if i == 0:
                    await safe_send_discord_message(ctx.message, chunk, files=files_to_send, is_reply=True)
                else:
                    await safe_send_discord_message(ctx.channel, chunk, files=files_to_send)


    except asyncio.CancelledError:
        try:
            await status_msg.delete()
        except discord.DiscordException:
            pass
        session.set_status("idle", "정상 대기 중 (작업 중단)")
        await ctx.send("🛑 **진행 중이던 작업이 즉시 강제 중단(Stop)되었습니다.**")
    except Exception as e:
        logger.exception("단일 에이전트 실행 중 오류 발생")
        err_trace = traceback.format_exc()
        session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
        await ctx.send(f"⚠️ 단일 에이전트 실행 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, "정상 대기 중")
        session.current_task = None

@bot.command(name="image", aliases=["img", "이미지", "이미지생성", "그려줘", "draw"], hidden=True)
async def image_command(ctx, *, prompt: str = ""):
    """
    🎨 Image generator — 실제 AI 이미지 생성 도구 (Nano Banana 계열 / OpenRouter Gemini image 등).
    사용법: !image 우주 테마 앱 로고 (선택: `asset_name=app_logo ratio=1:1`)
    """
    if not prompt:
        backend_desc = "미구성"
        try:
            from image_generation_backend import ImageGenerationBackend
            backend_desc = ImageGenerationBackend.describe_backends() or "미구성"
        except Exception:
            pass
        await ctx.send(
            f"⚠️ 생성할 이미지 설명을 입력해주세요. 예: `{COMMAND_PREFIX}image 우주 테마 앱 로고`\n"
            f"• 옵션: `{COMMAND_PREFIX}image asset_name=app_logo ratio=16:9 미니멀 로고`\n"
            f"• **수정(AI 편집)**: `{COMMAND_PREFIX}image edit app/src/main/res/drawable/hero.png 배경을 더 어둡게`\n"
            f"  └ 또는 `{COMMAND_PREFIX}image source_image=hero.png 배경을 더 어둡게`\n"
            f"• 실제 AI 이미지 백엔드: {backend_desc}"
        )
        return

    session = session_manager.get_session(ctx.channel.id)
    if session.is_busy:
        await ctx.send("⏳ 현재 다른 작업을 처리 중입니다. 완료 후 요청해주시거나 `!stop`으로 중단할 수 있습니다.")
        return

    # 옵션 파싱 (asset_name=..., ratio=..., source_image=...)
    asset_name = ""
    aspect_ratio = "1:1"
    source_image = ""
    cleaned_tokens = []
    _img_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
    tokens = prompt.split()
    # `!image edit <원본경로> <프롬프트>` 축약형 지원 (수정/편집/고쳐)
    if tokens and tokens[0].lower() in ("edit", "수정", "편집", "고쳐", "고쳐줘"):
        tokens = tokens[1:]
        if tokens and ("/" in tokens[0] or tokens[0].lower().endswith(_img_exts)):
            source_image = tokens.pop(0)
    for tok in tokens:
        low = tok.lower()
        if low.startswith(("asset_name=", "name=")):
            asset_name = tok.split("=", 1)[1].strip()
        elif low.startswith(("ratio=", "aspect=")):
            aspect_ratio = tok.split("=", 1)[1].strip() or "1:1"
        elif low.startswith(("source_image=", "edit_of=", "source=", "원본=")):
            source_image = tok.split("=", 1)[1].strip()
        else:
            cleaned_tokens.append(tok)
    clean_prompt = " ".join(cleaned_tokens).strip() or ("이미지를 자연스럽게 개선" if source_image else prompt)

    img_disp = "Gemini 3.8 Flash"
    try:
        from model_manager import GlobalModelManager
        img_disp = GlobalModelManager.get_role_model_display("image")
    except Exception:
        pass

    _img_action = "수정" if source_image else "생성"
    status_msg = await ctx.send(f"🎨 **[Image generator({img_disp}) 가동] `{clean_prompt[:60]}` 이미지 {_img_action}을 시작합니다...**")
    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.5)
    on_status_update = status_updater.get_callback(prefix="🎨 **[Image generator 진행 중]**")

    session.current_task = asyncio.current_task()
    session.set_busy(True, f"🎨 Image generator({img_disp}) 이미지 {_img_action} 중")

    try:
        reload_helper_modules()
        from image_creator_hub import ImageCreatorHub

        async with ctx.channel.typing():
            result = await ImageCreatorHub.generate(
                prompt=clean_prompt,
                workspace=session.workspace,
                asset_name=asset_name,
                aspect_ratio=aspect_ratio,
                session=session,
                on_status=on_status_update,
                source_image=source_image,
            )

        await status_updater.delete()

        if not result.ok or not result.absolute_path or not os.path.isfile(result.absolute_path):
            backend_desc = "미구성"
            try:
                from image_generation_backend import ImageGenerationBackend
                backend_desc = ImageGenerationBackend.describe_backends() or "미구성"
            except Exception:
                pass
            await ctx.send(
                f"⚠️ **실제 AI 이미지 생성 실패**\n• 사유: {result.message or '알 수 없는 오류'}\n"
                f"• 현재 이미지 백엔드: {backend_desc}\n"
                f"• `.env`의 `OPENROUTER_API_KEY` (또는 `GEMINI_API_KEY`, `IMAGE_API_BASE`/`IMAGE_API_KEY`/`IMAGE_API_MODEL`) 설정을 확인해주세요."
            )
            return

        # 생성 이미지 전송 (Image generator 봇 채널 우선)
        img_ch = image_bot.get_channel(ctx.channel.id) if ("image_bot" in globals() and image_bot and image_bot.is_ready()) else None
        target_sender = img_ch or ctx

        if result.is_real_ai:
            title = f"🎨 [Image generator] {result.asset_name}.png 실제 AI {_img_action} 완료"
            color = 0xF59E0B
        else:
            title = f"⚠️ [Image generator] {result.asset_name}.png (절차적 임시 에셋)"
            color = 0xEF4444
        embed = discord.Embed(
            title=title,
            description=(
                f"• **요청**: {result.prompt}\n"
                f"• **작업 유형**: {'✏️ 기존 이미지 수정(AI 편집)' if source_image else '🆕 신규 이미지 생성'}\n"
                f"• **해상도**: `{result.width}x{result.height}`\n"
                f"• **생성 방식**: {'✅ 실제 AI 이미지' if result.is_real_ai else '⚠️ 절차적 임시 에셋(AI 생성 실패 대체)'}\n"
                + (f"• **백엔드/모델**: `{result.backend}` / `{result.model}`\n" if result.backend else "")
                + (f"• **⚠️ 사유**: {result.message}\n" if result.fallback_used else "")
                + f"• **프로젝트 배치 경로**: `{result.relative_path}`"
            ),
            color=color,
        )
        embed.set_footer(text="AGY Image generator · 실제 AI 이미지 백엔드 (Coder/QA가 `[IMAGE_REQUEST: ...]`로 자동 호출)")
        try:
            await target_sender.send(embed=embed, file=discord.File(result.absolute_path, filename=f"{result.asset_name}.png"))
        except Exception:
            await safe_send_discord_message(
                ctx, f"🎨 **[Image generator] {result.asset_name}.png**",
                files=[discord.File(result.absolute_path, filename=f"{result.asset_name}.png")],
                is_reply=True,
            )

        # #nano-banana-image 스튜디오 아카이빙 (best-effort)
        try:
            await ImageCreatorHub.dispatch_to_studio(bot, result, origin_channel_id=ctx.channel.id)
        except Exception:
            pass

        # Coder 바인딩 가이드
        snippet = ""
        try:
            from hybrid_asset_pipeline import HybridAssetPipeline
            snippet = HybridAssetPipeline.generate_code_integration_snippets(
                platform=HybridAssetPipeline.detect_project_platform(session.workspace),
                asset_name=result.asset_name,
                relative_path=result.relative_path,
            ).get("compose") or ""
        except Exception:
            snippet = ""
        if snippet:
            await ctx.send(f"💡 **소스코드 바인딩 가이드:**\n```kotlin\n{snippet}\n```")

    except asyncio.CancelledError:
        try:
            await status_msg.delete()
        except discord.DiscordException:
            pass
        session.set_status("idle", "정상 대기 중 (작업 중단)")
        await ctx.send("🛑 **이미지 생성 작업이 즉시 강제 중단(Stop)되었습니다.**")
    except Exception as e:
        logger.exception("Image generator 실행 중 오류 발생")
        session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
        await ctx.send(f"⚠️ 이미지 생성 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, "정상 대기 중")
        session.current_task = None

@bot.command(name="mode3", aliases=["3", "자율3", "무인3", "3자율", "3차자율", "auto_trio"], hidden=True)
async def mode3_command(ctx, *, prompt: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    session.set_mode("mode3")
    if hasattr(session, "reset_cycle_workers"):
        session.reset_cycle_workers()
    else:
        session.reset_conversation()
    if not prompt:
        cur_desc = get_mode_description(session.mode, session)
        await ctx.send(f"✅ 채널 동작 모드가 {cur_desc}(으)로 설정되었습니다!\n🧹 Coder 및 QA의 컨텍스트를 청정 초기화(Clean State)했습니다.\n💡 목표를 입력하면 사령관 총괄 기획 후 무인 자율 폐루프 개발을 개시합니다. 예: `{COMMAND_PREFIX}3 React 가계부 앱 만들어줘`")
        return
    setattr(ctx.message, "_is_explicit_mode3_command", True)
    await execute_channel_prompt(ctx.message, prompt)

@bot.command(name="mode3+", aliases=["3+", "3plus", "3플러스", "mode3_plus", "자율3+", "무인3+", "auto_multi"], hidden=True)
async def mode3_plus_command(ctx, *, prompt: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    clean_p = prompt.strip()
    p_parts = clean_p.split(maxsplit=1)
    if len(p_parts) == 2:
        candidate_qa = p_parts[0].lower()
        if candidate_qa in ("qa2", "solar", "solar-pro", "solar-pro4", "qa3", "deepseek", "qa4", "qwen", "qa5", "llama", "qa6", "ling", "all", "전체") or candidate_qa.startswith("qa"):
            indices, sel_cfgs, label = config.parse_qa_selection(candidate_qa)
            if indices or candidate_qa in ("all", "전체"):
                session.set_selected_qa_indices(indices)
                prompt = p_parts[1]

    session.set_mode("mode3_plus")
    if hasattr(session, "reset_cycle_workers"):
        session.reset_cycle_workers()
    else:
        session.reset_conversation()
    if not prompt:
        cur_desc = get_mode_description(session.mode, session)
        await ctx.send(f"✅ 채널 동작 모드가 {cur_desc}(으)로 설정되었습니다!\n🧹 Coder 및 QA의 컨텍스트를 청정 초기화(Clean State)했습니다.\n💡 목표를 입력하면 사령관 총괄 기획 후 무인 자율 폐루프 다중 협업 개발을 개시합니다. 예: `{COMMAND_PREFIX}3+ React 가계부 앱 만들어줘`")
        return
    setattr(ctx.message, "_is_explicit_mode3_command", True)
    await execute_channel_prompt(ctx.message, prompt)

@bot.command(name="pair+", aliases=["pair_plus", "2+", "2plus", "2플러스", "pair_quad", "quad_pair"], hidden=True)
async def pair_plus_command(ctx, *, prompt: str = ""):
    if not prompt:
        await ctx.send(f"⚠️ 요청 내용을 입력해주세요. 예: `{COMMAND_PREFIX}2+ React 투두리스트 만들고 테스트해줘`\n💡 채널 모드 변경: `{COMMAND_PREFIX}mode 2+`")
        return

    session = session_manager.get_session(ctx.channel.id)
    if session.is_busy:
        await ctx.send("⏳ 현재 다른 작업을 처리 중입니다. 완료 후 요청해주시거나 `!stop`으로 중단할 수 있습니다.")
        return

    # Check if first word specifies a QA target (e.g. "!2+ qa2 투두리스트", "!2+ solar 기능 개발")
    clean_p = prompt.strip()
    p_parts = clean_p.split(maxsplit=1)
    if len(p_parts) == 2:
        candidate_qa = p_parts[0].lower()
        if candidate_qa in ("qa2", "solar", "solar-pro", "solar-pro4", "qa3", "deepseek", "qa4", "qwen", "qa5", "llama", "qa6", "ling", "all", "전체") or candidate_qa.startswith("qa"):
            indices, sel_cfgs, label = config.parse_qa_selection(candidate_qa)
            if indices or candidate_qa in ("all", "전체"):
                session.set_selected_qa_indices(indices)
                prompt = p_parts[1]

    all_ext_cfgs = config.get_extended_qa_configs()
    ext_cfgs = session.get_active_extended_qas(all_ext_cfgs) if hasattr(session, "get_active_extended_qas") else all_ext_cfgs
    multi_step_num = len(ext_cfgs) + 3 if ext_cfgs else 3
    multi_label = f"{multi_step_num}중 다중" if ext_cfgs and len(ext_cfgs) > 1 else ("4중 쿼드" if ext_cfgs else "3중")
    chain_names = " ↔ ".join([f"{c['name'].lower()}" for c in ext_cfgs]) if ext_cfgs else ""

    collab_chain_display = f"Coder ↔ qa1 ↔ {chain_names} ↔ Auditor" if chain_names else "Coder ↔ qa1 ↔ Auditor"
    status_msg = await ctx.send(f"👥 **[{multi_label} 에이전트 가동 (!mode 2+)] {collab_chain_display} 협업 개발을 시작합니다...**")
    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
    on_status_update = status_updater.get_callback(prefix=f"👥 **[{multi_label} 협업 진행 중 (!mode 2+)]**")

    session.current_task = asyncio.current_task()

    try:
        reload_helper_modules()
        async with ctx.channel.typing():
            orig_mode = session.mode
            if session.mode not in ("quad", "pair_quad"):
                session.mode = "pair_quad"
            try:
                final_response = await TrioAgentRunner.run_pipeline(
                    session=session,
                    prompt=prompt,
                    main_channel=ctx.channel,
                    qa_client=qa_bot,
                    qa2_client=qa2_bot,
                    extended_qa_clients=globals().get("extended_qa_bots", {}),
                    auditor_client=auditor_bot,
                    on_status_update=on_status_update,
                    max_rounds=PAIR_MAX_ROUNDS
                )
            finally:
                if orig_mode not in ("quad", "pair_quad"):
                    session.mode = orig_mode

        await status_updater.delete()

        chunks, file_attachments = await async_prepare_message_payload(
            final_response,
            workspace_root=session.workspace,
            auto_scan_deliverables=True,
            excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
            excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
        )

        for i, chunk in enumerate(chunks):
            files_to_send = file_attachments if i == 0 else []
            if i == 0:
                await safe_send_discord_message(ctx.message, chunk, files=files_to_send, is_reply=True)
            else:
                await safe_send_discord_message(ctx.channel, chunk, files=files_to_send)


    except asyncio.CancelledError:
        try:
            await status_msg.delete()
        except discord.DiscordException:
            pass
        session.set_status("idle", "정상 대기 중 (작업 중단)")
        await ctx.send(f"🛑 **진행 중이던 {multi_label} 협업(!mode 2+) 작업이 즉시 강제 중단(Stop)되었습니다.**")
    except Exception as e:
        logger.exception(f"{multi_label} 협업(!mode 2+) 실행 중 오류 발생")
        err_trace = traceback.format_exc()
        session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
        await ctx.send(f"⚠️ {multi_label} 협업(!mode 2+) 실행 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, "정상 대기 중")
        session.current_task = None

@bot.command(name="single_qa2", aliases=["qa2_solo", "solo_qa2", "qa2"], hidden=True)
async def single_qa2_command(ctx, *, prompt: str = ""):
    qa2_model = getattr(config, "QA2_MODEL", "upstage/solar-pro4")
    if not prompt:
        await ctx.send(f"⚠️ 요청 내용을 입력해주세요. 예: `{COMMAND_PREFIX}qa2 코드 검토해줘`")
        return

    session = session_manager.get_session(ctx.channel.id)
    if session.is_busy:
        await ctx.send("⏳ 현재 다른 작업을 처리 중입니다. 완료 후 요청해주시거나 `!stop`으로 중단할 수 있습니다.")
        return

    qa2_ch = qa2_bot.get_channel(ctx.channel.id) if (qa2_bot and qa2_bot.is_ready()) else None
    status_label = "🥷 **[단일 qa2 가동] 작업을 시작합니다...**"
    if qa2_ch:
        status_msg = await qa2_ch.send(status_label)
    else:
        status_msg = await ctx.send(status_label)

    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
    on_status_update = status_updater.get_callback(prefix="🥷 **[단일 qa2 진행 중]**")

    session.current_task = asyncio.current_task()
    session.set_busy(True, "🥷 AGY 단일 qa2 작업 진행 중 (수동 고정)")
    session.role = "qa2"

    try:
        reload_helper_modules()
        qa2_engine = getattr(config, "QA2_ENGINE", "openrouter")
        async with ctx.channel.typing():
            final_response = await AgentRunner.run_prompt(
                session=session,
                prompt=prompt,
                on_tool_update=on_status_update,
                model=qa2_model,
                engine=qa2_engine,
                role="qa2"
            )
            session.set_status("idle", "🟢 작업 완료 (정상 대기)")

        await status_updater.delete()

        chunks, file_attachments = await async_prepare_message_payload(
            final_response,
            workspace_root=session.workspace,
            auto_scan_deliverables=True,
            excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
            excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
        )

        target_sender = qa2_ch or ctx.message

        if len(chunks) > 3:
            summary_header = chunks[0]
            import tempfile
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tf:
                tf.write(final_response)
                temp_resp_file = tf.name

            try:
                files_payload = [discord.File(temp_resp_file, filename="qa2_response.md")] + file_attachments
                await safe_send_discord_message(
                    target_sender,
                    f"{summary_header}\n\n📎 **[전체 결과가 길어 파일로 첨부합니다]**",
                    files=files_payload,
                    is_reply=(target_sender == ctx.message)
                )
            finally:
                try:
                    os.remove(temp_resp_file)
                except Exception:
                    pass
        else:
            for i, chunk in enumerate(chunks):
                files_to_send = file_attachments if i == 0 else []
                if i == 0:
                    await safe_send_discord_message(target_sender, chunk, files=files_to_send, is_reply=(target_sender == ctx.message))
                else:
                    await safe_send_discord_message(target_sender if hasattr(target_sender, "send") else ctx.channel, chunk, files=files_to_send)


    except asyncio.CancelledError:
        try:
            await status_msg.delete()
        except discord.DiscordException:
            pass
        session.set_status("idle", "정상 대기 중 (작업 중단)")
        await (qa2_ch.send if qa2_ch else ctx.send)("🛑 **진행 중이던 qa2 작업이 즉시 강제 중단(Stop)되었습니다.**")
    except Exception as e:
        logger.exception("QA2 명령 처리 중 오류 발생")
        err_trace = traceback.format_exc()
        session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
        await (qa2_ch.send if qa2_ch else ctx.send)(f"⚠️ qa2 요청 처리 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, "정상 대기 중")
        session.current_task = None

@bot.command(name="pair", aliases=["trio", "협업", "페어", "3중", "2"], hidden=True)
async def pair_command(ctx, *, prompt: str = ""):
    if not prompt:
        await ctx.send(f"⚠️ 요청 내용을 입력해주세요. 예: `{COMMAND_PREFIX}pair React 투두리스트 만들고 테스트해줘`")
        return

    session = session_manager.get_session(ctx.channel.id)
    if session.is_busy:
        await ctx.send("⏳ 현재 다른 작업을 처리 중입니다. 완료 후 요청해주시거나 `!stop`으로 중단할 수 있습니다.")
        return

    ext_cfgs = session.get_active_extended_qas() if hasattr(session, "get_active_extended_qas") else config.get_extended_qa_configs()
    multi_step_num = len(ext_cfgs) + 3 if ext_cfgs else 3
    multi_label = f"{multi_step_num}중 다중" if ext_cfgs and len(ext_cfgs) > 1 else ("4중 쿼드" if ext_cfgs else "3중")
    chain_names = " ↔ ".join([f"{c['name'].lower()}" for c in ext_cfgs]) if ext_cfgs else ""

    if mode_registry.is_multi_qa_mode(session.mode):
        collab_chain_display = f"Coder ↔ qa1 ↔ {chain_names} ↔ Auditor" if chain_names else "Coder ↔ qa1 ↔ Auditor"
        status_msg = await ctx.send(f"👥 **[{multi_label} 에이전트 가동] {collab_chain_display} 협업 개발을 시작합니다...**")
    else:
        status_msg = await ctx.send("👥 **[3중 에이전트 가동] Coder ↔ QA ↔ Auditor 협업 개발을 시작합니다...**")

    prefix = f"👥 **[{multi_label} 협업 진행 중]**" if mode_registry.is_multi_qa_mode(session.mode) else "👥 **[3중 협업 진행 중]**"
    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)
    on_status_update = status_updater.get_callback(prefix=prefix)

    # Cancel any active idle timer on new user prompt

    session.current_task = asyncio.current_task()

    try:
        reload_helper_modules()
        async with ctx.channel.typing():
            final_response = await TrioAgentRunner.run_pipeline(
                session=session,
                prompt=prompt,
                main_channel=ctx.channel,
                qa_client=qa_bot,
                qa2_client=qa2_bot,
                extended_qa_clients=globals().get("extended_qa_bots", {}),
                auditor_client=auditor_bot,
                on_status_update=on_status_update,
                max_rounds=PAIR_MAX_ROUNDS
            )

        await status_updater.delete()

        if TrioAgentRunner.is_system_error(final_response):
            await safe_send_discord_message(ctx, final_response, is_reply=True)

        # Schedule idle auto-pilot inspection if live URL is detected

    except asyncio.CancelledError:
        await status_updater.delete()
        await ctx.send("🛑 **진행 중이던 작업이 즉시 강제 중단(Stop)되었습니다.**")
    except Exception as e:
        logger.exception("3중 에이전트 협업 중 오류 발생")
        err_trace = traceback.format_exc()
        await ctx.send(f"⚠️ 3중 에이전트 협업 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, session.status_desc or "🟢 작업 완료 (정상 대기)")
        session.current_task = None
        asyncio.create_task(dispatch_next_in_queue(ctx.channel.id))

@bot.command(name="quota", aliases=["usage", "limit", "credits", "쿼터", "사용량", "토큰", "잔여량"], hidden=True)
async def quota_command(ctx):
    status_msg = await ctx.send("📊 **Antigravity AI 모델 쿼터 정보를 조회하고 있습니다...**")
    try:
        quota_data = await QuotaManager.get_quota_info()
        if not quota_data or "models" not in quota_data:
            await status_msg.edit(content="⚠️ 쿼터 정보를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
            return

        embed = discord.Embed(
            title="📊 Antigravity AI 모델 잔여 쿼터 현황",
            description=f"👤 계정: `{quota_data.get('email', '알 수 없음')}` (방식: `{quota_data.get('method', 'google')}`)",
            color=discord.Color.blue()
        )

        lines = []
        for m in quota_data.get("models", []):
            if m.get("isAutocompleteOnly"):
                continue
            label = m.get("label", m.get("modelId", "Unknown"))
            is_exhausted = m.get("isExhausted", False)
            rem_pct = m.get("remainingPercentage")
            reset_ms = m.get("timeUntilResetMs", 0)
            reset_hrs = reset_ms // (1000 * 3600)
            reset_mins = (reset_ms % (1000 * 3600)) // (1000 * 60)
            reset_str = f"{reset_hrs}시간 {reset_mins}분 후 리셋" if reset_ms > 0 else "즉시 리셋"

            if is_exhausted or (rem_pct is not None and rem_pct <= 0):
                status_icon = "🔴 0%"
            elif rem_pct is not None:
                status_icon = f"🟢 {rem_pct * 100:.0f}%"
            else:
                status_icon = "🟢 가용"

            lines.append(f"• **{label}**: {status_icon} *({reset_str})*")

        embed.add_field(name="🤖 Antigravity 모델별 잔여 현황", value="\n".join(lines[:12]) if lines else "모델 정보 없음", inline=False)
        if len(lines) > 12:
            embed.add_field(name="기타 모델", value="\n".join(lines[12:20]), inline=False)

        try:
            import telemetry_status_manager as tsm
            cc_usage = tsm.fetch_command_code_usage()
            if cc_usage:
                fh = cc_usage.get("five_hour", {})
                wk = cc_usage.get("weekly", {})
                m_cred = cc_usage.get("monthly_credits", 0)
                m_str = f"${float(m_cred):.2f}" if isinstance(m_cred, (int, float)) else str(m_cred)
                cc_desc = (
                    f"• **플랜**: `{cc_usage.get('plan_name', 'Pro')}`\n"
                    f"• **5시간 한도 잔여**: `{(fh.get('remaining_pct', 100.0)):.1f}%` (Used: ${float(fh.get('used', 0.0)):.2f} / Cap: ${float(fh.get('cap', 14.0)):.2f})\n"
                    f"• **주간 한도 잔여**: `{(wk.get('remaining_pct', 100.0)):.1f}%` (Used: ${float(wk.get('used', 0.0)):.2f} / Cap: ${float(wk.get('cap', 35.0)):.2f})\n"
                    f"• **월간 잔여 크레딧**: `{m_str}` · CLI: `cmdc`"
                )
                embed.add_field(name="💻 Command Code (cmdc) 쿼터", value=cc_desc, inline=False)
        except Exception:
            pass

        await status_msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await status_msg.edit(content=f"⚠️ 쿼터 조회 중 오류 발생: {e}")

@bot.command(name="pwd", aliases=["경로", "위치", "cwd", "dir"], hidden=True)
async def pwd_command(ctx):
    session = session_manager.get_session(ctx.channel.id)

    role_label = "🕵️ QA Reviewer (품질 검증 전담 모드)" if getattr(session, "role", "coder") == "qa" else "👨‍💻 Coder (개발 및 구현 전담 모드)"
    embed = discord.Embed(title="📍 3-Agent 세션 상태", color=discord.Color.gold() if getattr(session, "role", "coder") == "qa" else discord.Color.green())
    embed.add_field(name="작업 디렉토리 (CWD)", value=f"`{session.workspace}`", inline=False)
    embed.add_field(name="🎭 채널 역할", value=f"**{role_label}**", inline=False)
    embed.add_field(
        name="동작 모드",
        value=get_mode_description(session.mode, session),
        inline=False
    )
    # 🎛️ 실제 파이프라인 투입 규모를 세션 기준(표시==실행)으로 명시한다.
    try:
        _minfo = mode_registry.resolve_mode(session.mode, session)
        _pipe_val = (
            f"• **협업 체인**: {_minfo['chain']}\n"
            f"• **협업 규모(활성 확장 QA 기준)**: `{mode_registry.describe_pipeline(session.mode, session)}` "
            f"(활성 확장 QA {_minfo['extended_count']}대 · 라운드마다 사령관 스쿼드가 전원/부분 편성됨)"
        )
        if _minfo.get("warning"):
            _pipe_val += f"\n{_minfo['warning']}"
        embed.add_field(name="🧩 실제 파이프라인", value=_pipe_val, inline=False)
    except Exception:
        pass
    # [⚙️ 실제 배정 엔진] 실행에 실제로 사용되는 전역 역할 설정을 우선 표시합니다.
    # (session.engine 은 채널별 레거시 값일 수 있어 실제 실행 엔진과 불일치할 수 있음)
    try:
        from model_manager import GlobalModelManager
        coder_eng_disp = GlobalModelManager.get_role_engine("coder") or session.engine
        qa_eng_disp = GlobalModelManager.get_role_engine("qa1") or session.qa_engine
        auditor_eng_disp = GlobalModelManager.get_role_engine("auditor") or session.auditor_engine
    except Exception:
        coder_eng_disp, qa_eng_disp, auditor_eng_disp = session.engine, session.qa_engine, session.auditor_engine

    embed.add_field(
        name="⚙️ 배정 AI 엔진",
        value=(
            f"• 👨‍💻 **Coder**: `{coder_eng_disp}` ({CODER_MODEL})\n"
            f"• 🕵️ **QA**: `{qa_eng_disp}` ({QA_MODEL})\n"
            f"• ⚖️ **Auditor**: `{auditor_eng_disp}` ({AUDITOR_MODEL})"
        ),
        inline=False
    )
    _coder_conv_id = session.conversation_id or (getattr(session, "engine_conversations", {}) or {}).get("opencode:conversation_id")
    embed.add_field(
        name="Coder 세션 ID",
        value=f"`{_coder_conv_id or '대기 중'}`",
        inline=True
    )
    embed.add_field(
        name="QA 세션 ID",
        value=f"`{session.qa_conversation_id or '대기 중'}`",
        inline=True
    )
    embed.add_field(
        name="Auditor 세션 ID",
        value=f"`{session.auditor_conversation_id or '대기 중'}`",
        inline=True
    )
    embed.add_field(name="작업 진행 여부", value="🔴 실행 중" if session.is_busy else "🟢 대기 중", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="cd", aliases=["이동", "폴더이동", "디렉토리"], hidden=True)
async def cd_command(ctx, *, path: str = ""):
    if not path:
        await ctx.send(f"⚠️ 이동할 경로를 입력해주세요. 예: `{COMMAND_PREFIX}cd /home/user/my-project`")
        return
    session = session_manager.get_session(ctx.channel.id)
    try:
        session.set_workspace(path)
        await ctx.send(f"📁 **작업 디렉토리가 변경되었습니다:**\n`{session.workspace}`")
    except Exception as e:
        await ctx.send(f"⚠️ 디렉토리 변경 실패: {str(e)}")

@bot.command(name="mkdir", aliases=["폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"], hidden=True)
async def mkdir_command(ctx, *, path: str = ""):
    session = session_manager.get_session(ctx.channel.id)
    current_ws = getattr(session, "workspace", config.DEFAULT_WORKSPACE)
    user_id = ctx.author.id if hasattr(ctx, "author") and hasattr(ctx.author, "id") else 0

    try:
        import directory_manager
        import safe_trash
    except ImportError as ie:
        await ctx.send(f"⚠️ 디렉토리 관리 모듈 로드 실패: {ie}")
        return

    path_clean = (path or "").strip()
    if not path_clean:
        embed = directory_manager.build_mkdir_control_embed(ctx.channel.id, current_ws)
        view = directory_manager.DirectoryControlView(
            channel_id=ctx.channel.id,
            user_id=user_id,
            current_workspace=current_ws
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
        return

    try:
        resolved = safe_trash.resolve_device_path(path_clean, base_dir=current_ws)
        success, abs_p, msg_text = safe_trash.create_device_folder(resolved)
        if not success:
            await ctx.send(f"❌ {msg_text}")
            return

        session.set_workspace(abs_p)
        embed = discord.Embed(
            title="📁 [새 디렉토리 생성 및 채널 연동 완료]",
            description=(
                f"새 작업 디렉토리가 생성되고 현재 채널에 성공적으로 연동되었습니다! 🚀\n\n"
                f"📍 **연동된 작업 디렉토리 (CWD):**\n`{abs_p}`\n"
                f"🛡️ **안전 보장**: 영구 삭제(`rm -rf`) 금지 · OS 휴지통 보존 적용"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text=f"디렉토리 제어판: {config.COMMAND_PREFIX}mkdir | CWD 확인: {config.COMMAND_PREFIX}pwd")
        view = directory_manager.DirectoryControlView(
            channel_id=ctx.channel.id,
            user_id=user_id,
            current_workspace=abs_p
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
    except Exception as e:
        logger.error(f"mkdir_command error: {e}", exc_info=True)
        await ctx.send(f"⚠️ 디렉토리 생성 중 오류가 발생했습니다: {str(e)}")

_raw_mkdir_command = getattr(mkdir_command, "callback", mkdir_command)


@bot.command(name="stop", aliases=["sto", "halt", "cancel", "kill", "abort", "중지", "정지", "취소", "그만", "멈춰", "중단"])
async def stop_command(ctx, *, arg: str = ""):
    arg_lower = arg.strip().lower()
    session = session_manager.get_session(ctx.channel.id)
    
    # If user specified 'all' or 'clear', cancel running tasks and clear queue completely
    if arg_lower in ("all", "clear", "전체", "모두", "취소"):
        stopped_sess = session_manager.stop_session(ctx.channel.id)
        stopped_qa = qa_hub_manager.stop_missions_for_channel(ctx.channel.id)
        session.is_stopped = True
        session.set_auto(enabled=False)
        cleared_count = session.clear_queue()
        queue_msg = f" (대기열 {cleared_count}개 삭제됨)" if cleared_count > 0 else ""
        if stopped_sess or stopped_qa or cleared_count > 0:
            await ctx.send(f"🛑 **진행 중이던 작업 및 대기열을 모두 중단/초기화했습니다.{queue_msg}**")
        else:
            await ctx.send("ℹ️ 현재 중단할 진행 중인 작업 또는 대기열이 없습니다.")
        return

    # Standard !stop: stops current active task and starts next queued item if available
    stopped_sess = session_manager.stop_session(ctx.channel.id)
    stopped_qa = qa_hub_manager.stop_missions_for_channel(ctx.channel.id)
    session.is_stopped = True
    session.set_auto(enabled=False)
    
    if stopped_sess or stopped_qa:
        if session.queue:
            remaining = len(session.queue)
            await ctx.send(
                f"🛑 **진행 중이던 작업을 즉시 중단했습니다.**\n"
                f"▶️ 대기열에 {remaining}개의 작업이 남아 있어 다음 작업을 즉시 시작합니다. (전체 취소: `{COMMAND_PREFIX}stop all`)"
            )
            asyncio.create_task(dispatch_next_in_queue(ctx.channel.id))
        else:
            await ctx.send("🛑 **진행 중이던 작업을 즉시 중단했습니다.**")
    else:
        if session.queue:
            cleared = session.clear_queue()
            await ctx.send(f"🗑️ **실행 중인 작업은 없었으나 대기열({cleared}개)을 삭제했습니다.**")
        else:
            recent_status = f" (최근 상태: {session.status_desc})" if session.status_desc else ""
            await ctx.send(f"ℹ️ 현재 중단할 진행 중인 작업이 없습니다.{recent_status}")


@bot.command(name="done", aliases=["완료", "finish", "마무리", "끝", "stop_after_cycle"])
async def done_command(ctx, *, arg: str = ""):
    """
    [Mode 3 / 3+ Graceful Finish Command]
    Instructs the autonomous multi-agent pipeline to safely complete the CURRENT milestone,
    and then stop (idle) rather than auto-chaining to the next milestone.
    """
    session = session_manager.get_session(ctx.channel.id)

    # 1. If intermission is currently active (waiting 30s before next milestone)
    if getattr(session, "intermission_event", None) and not session.intermission_event.is_set():
        session.mode3_pending_prompt = None
        session.stop_after_current_cycle = False
        session.intermission_paused = False
        session.intermission_event.set()
        session.set_status("idle", "🏁 Mode 3 자율 순환 종료 (!done)")
        await ctx.send(
            "🏁 **[Mode 3 자율 순환 정상 종료]**\n"
            "인터미션 대기를 안전하게 종료하고 시스템을 정상 대기(idle) 상태로 전환했습니다.\n"
            f"언제든지 새 명령어를 입력하거나 `{COMMAND_PREFIX}mode 3`으로 개발을 재개할 수 있습니다."
        )
        return

    # 2. If actively busy running a milestone
    if session.is_busy:
        if getattr(session, "stop_after_current_cycle", False):
            session.stop_after_current_cycle = False
            await ctx.send("🔄 **[!done 예약 해제]** 이번 회차 종료 예약이 해제되었습니다. 무인 자율 순환이 계속 유지됩니다.")
        else:
            session.stop_after_current_cycle = True
            await ctx.send(
                "🏁 **[Mode 3 이번 회차 종료 예약 완료 (!done)]**\n"
                "현재 진행 중인 마일스톤 작업이 모두 끝나고 Auditor 승인 및 검증이 완료되면, "
                "**차기 마일스톤으로 넘어가지 않고 안전하게 무인 자율 순환을 종료(정상 대기)**합니다.\n\n"
                f"• 즉시 강제 중단: `{COMMAND_PREFIX}stop`\n"
                f"• 예약 취소 (순환 계속 유지): 다시 `{COMMAND_PREFIX}done` 입력"
            )
        return

    # 3. If idle
    await ctx.send(
        "ℹ️ 현재 진행 중인 무인 자율 순환 작업이 없습니다.\n"
        f"자율 개발을 시작하려면 `{COMMAND_PREFIX}mode 3` 또는 개발 요청 명령어를 입력하세요."
    )


@bot.command(name="dismiss", aliases=["dismiss_all", "미션삭제"], hidden=True)
async def dismiss_command(ctx, *, arg: str = ""):
    """
    [🛡️ Idle Quota Protection] Clears interrupted QA missions from the state file.
    After the bot shows interrupted missions on startup (without auto-resuming),
    use this command to dismiss them instead of resuming.
    Usage: !dismiss all
    """
    arg_lower = arg.strip().lower()
    if arg_lower not in ("all", "전체", "모두", ""):
        await ctx.send(f"❓ 사용법: `!dismiss all` — 중단된 QA 미션 목록을 전체 삭제합니다.")
        return
    try:
        active_missions_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "state", "active_qa_missions.json"
        )
        if os.path.isfile(active_missions_file):
            with open(active_missions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data)
            with open(active_missions_file, "w", encoding="utf-8") as f:
                json.dump({}, f)
            await ctx.send(
                f"🗑️ **[중단된 미션 목록 삭제 완료]** 총 {count}개의 중단된 QA 미션 기록을 삭제했습니다.\n"
                f"ℹ️ Gemini 할당량 보호를 위해 자동 재개되지 않습니다. 필요하면 `!qa` 명령으로 새로 시작하세요."
            )
        else:
            await ctx.send("ℹ️ 삭제할 중단된 미션 기록이 없습니다.")
    except Exception as e:
        await ctx.send(f"⚠️ 미션 기록 삭제 중 오류: `{e}`")

@bot.command(name="queue", aliases=["q", "대기열"], hidden=True)
async def queue_command(ctx):
    session = session_manager.get_session(ctx.channel.id)
    queue = session.peek_queue()
    status_text = f"**현재 상태:** {session.status_desc} (`{session.status}`)"
    
    if not queue:
        embed = discord.Embed(
            title="📋 [작업 대기열 현황]",
            description=f"{status_text}\n\n현재 대기 중인 작업이 없습니다 (대기열 비어있음).",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        return

    items_text = []
    for idx, item in enumerate(queue, 1):
        prompt_snippet = item.prompt.replace("\n", " ")[:60]
        items_text.append(f"`{idx}.` {prompt_snippet}...")

    embed = discord.Embed(
        title=f"📋 [작업 대기열 현황 · 총 {len(queue)}개]",
        description=f"{status_text}\n\n" + "\n".join(items_text) + f"\n\n• 대기열 전체 취소: `{COMMAND_PREFIX}clear-queue` 또는 `{COMMAND_PREFIX}stop all`",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@bot.command(name="clear-queue", aliases=["cq", "대기열삭제", "대기열초기화", "대기열비우기"], hidden=True)
async def clear_queue_command(ctx):
    session = session_manager.get_session(ctx.channel.id)
    cleared = session.clear_queue()
    if cleared > 0:
        await ctx.send(f"🗑️ **대기열에 있던 {cleared}개의 작업을 모두 삭제했습니다.**")
    else:
        await ctx.send("ℹ️ 삭제할 대기 중인 작업이 없습니다.")

@bot.command(name="temp", aliases=["thermal", "온도", "발열"], hidden=True)
async def temp_command(ctx):
    tier, curr_temp = ThermalGuard.get_tier()
    tier_desc = {
        "NORMAL": "🟢 **정상** (100% 최고 속도 가동 중)",
        "MILD": "🟡 **주의** (2.5초 미세 분산 쿨다운 적용)",
        "EMERGENCY": "🔴 **고열 보호** (5분 딥 쿨다운 안전 대기 모드)"
    }.get(tier, "🟢 **정상**")
    
    embed = discord.Embed(
        title="🌡️ [서피스 프로 7 하드웨어 실시간 온도]",
        description=(
            f"**현재 CPU 온도:** `{curr_temp:.1f}°C`\n"
            f"**스마트 열 관리 상태:** {tier_desc}\n\n"
            f"• **1단계 (🟢 ~75°C 이하):** 100% 전속력 작업 수행\n"
            f"• **2단계 (🟡 78°C~84°C):** 라운드 전환 간 2.5초 미세 쿨다운\n"
            f"• **3단계 (🔴 85°C 이상):** 서피스 하드웨어/배터리 보호를 위해 5분(300초) 안전 냉각 대기 (실시간 온도 모니터링)"
        ),
        color=discord.Color.gold() if tier == "MILD" else (discord.Color.red() if tier == "EMERGENCY" else discord.Color.green())
    )
    await ctx.send(embed=embed)

@bot.command(name="rounds", aliases=["라운드", "maxrounds", "round_limit", "roundcap"], hidden=True)
async def rounds_command(ctx):
    """라운드 상한 및 상한 도달 시 계속 여부 확인 프롬프트 설정을 표시한다."""
    continue_state = "✅ 활성 (예/아니요 확인)" if getattr(config, "ROUND_CONTINUE_ENABLED", True) else "⛔ 비활성"
    timeout_sec = int(getattr(config, "ROUND_CONTINUE_TIMEOUT_SEC", 300))
    embed = discord.Embed(
        title="🔁 [최대 라운드(수렴 상한) 설정]",
        description=(
            f"• **3중/다중 협업** (`{COMMAND_PREFIX}pair` · `{COMMAND_PREFIX}mode 2` · `{COMMAND_PREFIX}mode 3`): "
            f"`{PAIR_MAX_ROUNDS}라운드`\n"
            f"• **QA 수렴 상한**: `{QA_MAX_ROUNDS}라운드`\n"
            f"• **3중 심층 리서치**: `{config.RESEARCH_MAX_ROUNDS}라운드`\n"
            f"• **상한 도달 시 계속 확인**: {continue_state} (무응답 자동 종료 `{timeout_sec}초`)\n\n"
            "상한에 도달하면 봇이 **\"N회가 끝났습니다. 이어서 진행하시겠습니까?\"** 라고 예/아니요 버튼으로 확인합니다.\n"
            f"• 값을 바꾸려면 `.env` 의 `PAIR_MAX_ROUNDS` / `QA_MAX_ROUNDS` / `RESEARCH_MAX_ROUNDS` 를 수정하세요."
        ),
        color=discord.Color.blurple()
    )
    await ctx.send(embed=embed)

@bot.command(name="watchdog", aliases=["safety", "guards", "감시견", "안전가드"], hidden=True)
async def watchdog_command(ctx, subcommand: str = ""):
    """Displays live status of the 10 Bounded Autonomy Guards or resets safety counters."""
    ch_id = ctx.channel.id
    from autonomy_guard import AutonomySafetyLayer
    st = AutonomySafetyLayer.get_state(ch_id)

    if subcommand.strip().lower() in ("reset", "clear", "초기화", "리셋"):
        global _watchdog_failure_counts
        if isinstance(_watchdog_failure_counts, dict):
            _watchdog_failure_counts[ch_id] = 0
        AutonomySafetyLayer.reset_watchdog_runs(ch_id)
        st["consecutive_failures"] = 0
        st["file_churn_counter"].clear()
        st["recent_tasks"].clear()
        st["recent_errors"].clear()
        st["milestone_start_time"] = time.time()
        st["is_escalated"] = False
        st["escalation_reason"] = ""

        await ctx.send(
            "✅ **[감시견 & 자율 안전 가드레일 초기화 완료]**\n"
            "• 서킷 브레이커 실패 카운터: `0회`\n"
            "• 무인 감시견 연속 가동 수: `0회`\n"
            "• 파일 폭주 및 에러 히스토리가 초기화되었습니다.\n"
            "• 이제 `!auto on evolve`로 정상 재개할 수 있습니다."
        )
        return

    session = session_manager.get_session(ch_id)
    ws = session.workspace or DEFAULT_WORKSPACE
    is_safe, action, msg = AutonomySafetyLayer.check_safety_guards(ch_id, ws)

    # Calculate metrics
    elapsed_m = (time.time() - st["milestone_start_time"]) / 60.0
    top_churn = max(st["file_churn_counter"].values()) if st["file_churn_counter"] else 0
    fail_cnt = st["consecutive_failures"]

    embed = discord.Embed(
        title="🛡️ [AGY 4.x Bounded Autonomy — 10대 자율 안전 가드레일 현황]",
        description=(
            f"**종합 안전 상태:** `{'🟢 SAFE (정상 가동)' if is_safe else '🔴 ' + action}`\n"
            f"**세부 진단:** {msg}\n\n"
            f"**📊 10대 안전 가드 실시간 텔레메트리:**\n"
            f"• **1. 태스크 루프 방어 (Guard 1):** 최근 태스크 {len(st['recent_tasks'])}개 감시 중 (최대 3회 반복 허용)\n"
            f"• **2. 파일 폭주 방어 (Guard 2):** 최대 수정 `{top_churn}/5회`\n"
            f"• **3. 마일스톤 타임박스 (Guard 3):** 경과 `{elapsed_m:.1f}/30.0분`\n"
            f"• **4. 일일 LLM 호출 쿼터 (Guard 4):** `{st['daily_call_count']}/500회`\n"
            f"• **5. 디스크 용량 가드 (Guard 5):** 기준 최소 2.0GB 유지\n"
            f"• **6. 무인 감시견 브레이크 (Guard 6):** 연속 `{st['watchdog_runs']}/6회` (도달 시 10분 쿨다운)\n"
            f"• **7. 동일 결함 반복 감지 (Guard 7):** 최근 에러 {len(st['recent_errors'])}건 추적\n"
            f"• **8. 로드맵 자동 확장 깊이 (Guard 8):** `{st['roadmap_expansions']}/5차` 상한\n"
            f"• **9. 연속 실패 서킷 브레이커 (Guard 9):** `{fail_cnt}/3회` 실패 누적\n"
            f"• **10. 목표 표류 감지 (Guard 10):** 사소한 문서/주석 쳇바퀴 감시 (Goal Drift 차단)\n"
            f"• **💡 4D 의사결정 매트릭스:** `Risk + Value + Confidence + Cost` 연동 완료\n\n"
            f"💡 **카운터 초기화:** `!watchdog reset` 또는 `!safety reset`"
        ),
        color=discord.Color.green() if is_safe else discord.Color.red()
    )
    embed.set_footer(text="AGY 4.x Adaptive Dynamic Orchestrator")
    await ctx.send(embed=embed)

_channel_queue_locks: Dict[int, asyncio.Lock] = {}

def get_channel_queue_lock(channel_id: int) -> asyncio.Lock:
    if channel_id not in _channel_queue_locks:
        _channel_queue_locks[channel_id] = asyncio.Lock()
    return _channel_queue_locks[channel_id]

QUEUE_LOCK_TIMEOUT_SECONDS = float(os.environ.get("QUEUE_LOCK_TIMEOUT_SECONDS", "20") or 20)
# 열 관리 비상 쿨다운(ThermalGuard 300초) 중의 락 점유는 '정상 대기'이므로 교체하지 않는다.
THERMAL_LOCK_GRACE_SECONDS = float(os.environ.get("QUEUE_LOCK_THERMAL_GRACE_SECONDS", "320") or 320)


def _queue_lock_timeout_seconds() -> float:
    """[🛡️ Hot-Reload Safe] bot.py 는 __main__ 이라 importlib.reload 로 전역이 재정의되지 않는다.

    과거 !reset 핫리로드 후 `QUEUE_LOCK_TIMEOUT_SECONDS` 전역이 유실되어
    `dispatch_next_in_queue` 가 매번 NameError 로 죽고 대기열이 영구 정지했다.
    이 리졸버는 전역이 없어도 환경변수/기본값으로 반드시 값을 반환한다.
    """
    try:
        return max(0.1, float(QUEUE_LOCK_TIMEOUT_SECONDS))  # type: ignore[name-defined]
    except Exception:
        return max(0.1, float(os.environ.get("QUEUE_LOCK_TIMEOUT_SECONDS", "20") or 20))


def _thermal_lock_grace_seconds() -> float:
    """[🛡️ Hot-Reload Safe] THERMAL_LOCK_GRACE_SECONDS 전역 유실 방어."""
    try:
        return max(0.1, float(THERMAL_LOCK_GRACE_SECONDS))  # type: ignore[name-defined]
    except Exception:
        return max(0.1, float(os.environ.get("QUEUE_LOCK_THERMAL_GRACE_SECONDS", "320") or 320))


def _thermal_cooldown_holds_lock() -> bool:
    """현재 락 점유가 열 관리 비상 쿨다운 때문인지 판정한다."""
    try:
        from thermal_guard import ThermalGuard
        tier = ThermalGuard.get_tier()[0]
        return tier == "EMERGENCY"
    except Exception:
        return False


async def _acquire_queue_lock_or_recover(channel_id: int):
    """[🛡️ StallGuard] 채널 대기열 락 취득 — 타임아웃 + 강제 교체 회수.

    기존에는 `async with lock:` 으로 **타임아웃 없이 무한 대기**해서,
    락 점유 태스크가 멈추는 순간 그 채널의 모든 후속 디스패치가 영구 블락
    (요청 → 답장 없음 / 상태 타이머 고정) 되었다.
    """
    lock = get_channel_queue_lock(channel_id)
    queue_lock_timeout = _queue_lock_timeout_seconds()
    try:
        await asyncio.wait_for(lock.acquire(), timeout=queue_lock_timeout)
        return lock
    except asyncio.TimeoutError:
        pass

    # 열 관리 비상 쿨다운(최대 300초) 중 점유는 정상 → 락을 지키고 넉넉히 한 번 더 기다린다.
    if _thermal_cooldown_holds_lock():
        thermal_grace = _thermal_lock_grace_seconds()
        logger.info(
            f"[StallGuard] 채널 {channel_id} 락은 ThermalGuard 비상 쿨다운 중 → "
            f"교체하지 않고 {int(thermal_grace)}초 추가 대기"
        )
        try:
            await asyncio.wait_for(lock.acquire(), timeout=thermal_grace)
            return lock
        except asyncio.TimeoutError:
            pass

    logger.error(
        f"[StallGuard] 채널 {channel_id} 대기열 락이 {queue_lock_timeout}초(+쿨다운 여유) 동안 "
        f"취득되지 않음 → 락 강제 교체 후 1회 재시도"
    )
    _channel_queue_locks[channel_id] = asyncio.Lock()
    lock = _channel_queue_locks[channel_id]
    try:
        await asyncio.wait_for(lock.acquire(), timeout=queue_lock_timeout)
        return lock
    except asyncio.TimeoutError:
        logger.error(
            f"[StallGuard] 채널 {channel_id} 락 재취득 실패 → 이번 디스패치 보류 "
            f"(StallGuard 주기 재호출로 회복)"
        )
        return None


async def dispatch_next_in_queue(channel_id: int):
    # [🛡️ StallGuard] 락을 쥔 채 5분 열 쿨다운을 기다리지 않도록 락 취득 전에 먼저 처리한다.
    try:
        _pre_session = session_manager.get_session(channel_id)
        if _pre_session.queue and not _pre_session.is_busy:
            await ThermalGuard.check_task_start_cooldown(None)
    except Exception:
        pass

    lock = await _acquire_queue_lock_or_recover(channel_id)
    if lock is None:
        return
    try:
        await asyncio.sleep(0.3)
        session = session_manager.get_session(channel_id)
        
        # Check if QA Hub is actively running for this channel
        if qa_hub_manager and qa_hub_manager.channel_tasks.get(channel_id):
            logger.info(f"Cannot dispatch queue for channel {channel_id}: QA Hub is still active.")
            return

        # If session is marked busy, verify if task is genuinely alive
        if session.is_busy:
            # If current_task is done or None, it's a zombie busy state -> auto recover!
            if session.current_task is None or session.current_task.done():
                logger.info(f"Auto-recovering zombie busy state on channel {channel_id}")
                session.set_busy(False, "🟢 정상 대기 중")
                session.set_status("idle", "🟢 정상 대기 중")
            else:
                # Wait briefly (up to 2.0s) in case previous task is in final teardown
                for _ in range(4):
                    await asyncio.sleep(0.5)
                    if not session.is_busy or session.current_task is None or session.current_task.done():
                        session.set_busy(False, "🟢 정상 대기 중")
                        break
                if session.is_busy:
                    return

        next_item = session.dequeue()
        if not next_item:
            return

        # Check ThermalGuard emergency cooldown before launching next queued task
        try:
            await ThermalGuard.check_task_start_cooldown(next_item.message.channel)
        except Exception:
            pass

        try:
            channel = next_item.message.channel
            remaining = len(session.queue)
            queue_note = f" (남은 대기열: {remaining}개)" if remaining > 0 else ""
            notice_embed = discord.Embed(
                title="▶️ [대기열 작업 자동 시작]",
                description=(
                    f"이전 작업이 완료되어 대기 중이던 요청을 즉시 시작합니다!{queue_note}\n\n"
                    f"**실행 요청:**\n`{next_item.prompt[:300]}`"
                ),
                color=discord.Color.green()
            )
            await channel.send(embed=notice_embed)
        except Exception as e:
            logger.debug(f"Failed to send queue notice: {e}")

        asyncio.create_task(execute_channel_prompt(next_item.message, next_item.prompt))
    finally:
        # [🛡️ StallGuard] 예외/조기 return 발생 시에도 반드시 락을 반환한다.
        try:
            lock.release()
        except Exception:
            pass

qa_hub_manager.set_on_complete_callback(dispatch_next_in_queue)

async def execute_channel_prompt(message: discord.Message, prompt: str):
    """
    [📺 채널 모델 스코프 진입점]

    이 채널의 파이프라인(Coder·QA·Auditor 전체 경로)은 아래 호출을
    '해당 채널 ID' 컨텍스트에서 실행한다. 덕분에 GlobalModelManager 의 모든
    getter 는 별도 수정 없이 **이 채널의 `!model` 오버라이드**를 자동 적용한다.
    """
    _tok = None
    try:
        from model_manager import GlobalModelManager
        _tok = GlobalModelManager.set_active_channel(message.channel.id)
    except Exception:
        _tok = None
    try:
        return await _execute_channel_prompt_impl(message, prompt)
    finally:
        try:
            if _tok is not None:
                from model_manager import GlobalModelManager
                GlobalModelManager.reset_active_channel(_tok)
        except Exception:
            pass


async def _execute_channel_prompt_impl(message: discord.Message, prompt: str):
    session = session_manager.get_session(message.channel.id)
    session.set_busy(True, "⏳ 작업 준비 중...")
    session.current_task = asyncio.current_task()

    # [Commander Epic Cycle] 사용자가 새로운 최상위 작업/요청을 시작할 때 사이클 카운트 초기화
    # (Mode 3 자율 순환 내부 호출이 아닌 일반 사용자 입력일 때 0으로 리셋)
    is_internal_auto_chain = (
        prompt.strip().startswith("🎯 [Commander 사령관 5W1H 개발 지침")
        or prompt.strip().startswith("🎯 [Commander")
    )
    if not is_internal_auto_chain:
        session._commander_cycle_count = 0
        session._last_milestone_diff_lines = None

    # Check ThermalGuard before starting execution
    await ThermalGuard.check_task_start_cooldown(message.channel)

    # Check if this is a natural language E2E dispatch request targeting #qa-tester
    raw_user_prompt = (message.content or "")
    if bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "")
    if qa_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{qa_bot.user.id}>", "").replace(f"<@!{qa_bot.user.id}>", "")
    for ext_bot in globals().get("extended_qa_bots", {}).values():
        if ext_bot and ext_bot.user:
            raw_user_prompt = raw_user_prompt.replace(f"<@{ext_bot.user.id}>", "").replace(f"<@!{ext_bot.user.id}>", "")
    if auditor_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{auditor_bot.user.id}>", "").replace(f"<@!{auditor_bot.user.id}>", "")
    if "image_bot" in globals() and image_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{image_bot.user.id}>", "").replace(f"<@!{image_bot.user.id}>", "")
    if qa2_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{qa2_bot.user.id}>", "").replace(f"<@!{qa2_bot.user.id}>", "")
    raw_user_prompt = raw_user_prompt.strip()

    raw_lower = raw_user_prompt.lower()
    is_explicit_code_task = any(kw in raw_user_prompt for kw in [
        "반영", "수정", "개발", "구현", "작성", "패치", "코드", "시스템", "만들어", "커밋", "푸시",
        "개선", "완성도", "빌드", "최적화", "리팩토링", "버그", "에러", "해결", "기능",
        "ui", "ux", "다듬", "추가", "고쳐", "배포", "앱", "apk", "검증", "디자인", "스타일", "만든"
    ])
    is_question_or_inquiry = any(q in raw_user_prompt for q in ["?", "어디", "어떻게", "왜 ", "할까", "궁금", "뭐야", "무엇", "어떤", "알려줘", "설명해줘"])
    is_chat_quote = ("— 오전" in raw_user_prompt or "— 오후" in raw_user_prompt or "agy_agents" in raw_user_prompt or raw_user_prompt.startswith(">") or (raw_user_prompt.startswith('"') and raw_user_prompt.endswith('"')))
    has_test_intent_verb = any(kw in raw_user_prompt for kw in ["요청", "보내", "날려", "돌려", "진행", "실행", "검사", "테스트", "위임"])

    is_qa_dispatch_request = (
        message.channel.id != QA_HUB_CHANNEL_ID and
        not is_explicit_code_task and
        not (is_question_or_inquiry and not any(kw in raw_user_prompt for kw in ["테스트 해줘", "테스트해줘", "e2e 해줘", "e2e해줘", "검증해줘", "검증 해줘"])) and
        not is_chat_quote and
        has_test_intent_verb and
        (
            str(QA_HUB_CHANNEL_ID) in raw_user_prompt or
            "qa-tester" in raw_lower or
            "qa tester" in raw_lower or
            "qa-hub" in raw_lower or
            "qa hub" in raw_lower or
            ("e2e" in raw_lower and any(kw in raw_user_prompt for kw in ["요청", "보내", "날려", "돌려", "진행", "실행", "해줘", "부탁", "검사", "테스트"]))
        )
    )

    if is_qa_dispatch_request:
        reload_helper_modules()
        # Extract URL directly from prompt (AutoPilot removed)
        url_match = re.search(r'https?://[^\s]+', prompt)
        target = url_match.group(0) if url_match else ""

        # If user mentioned apk or app in prompt, check for APK files in workspace
        is_apk_request = any(kw in prompt.lower() for kw in ["apk", "앱", "안드로이드", "android"])
        if is_apk_request and session.workspace and os.path.isdir(session.workspace):
            for root, _, files in os.walk(session.workspace):
                for f in sorted(files, key=lambda x: os.path.getmtime(os.path.join(root, x)), reverse=True):
                    if f.lower().endswith(".apk"):
                        target = os.path.join(root, f) if not target else target
                        break
                if target and target.lower().endswith(".apk"):
                    break

        if not target:
            match = re.search(r'([\w\-_\.]+\.apk)', prompt, re.IGNORECASE)
            if match:
                target = match.group(1)

        if not target:
            target = session.last_url or ""

        if not target and session.workspace and os.path.isdir(session.workspace):
            for root, _, files in os.walk(session.workspace):
                for f in files:
                    if f.lower().endswith(".apk"):
                        target = os.path.join(root, f)
                        break
                if target:
                    break

        if not target:
            target = "최신 빌드 및 웹 서비스"

        # [🛡️ AutoPilot removed] qa_mode is always 'patch' — evolve mode disabled
        qa_mode = "patch"

        await qa_hub_manager.dispatch_mission(
            source_channel=message.channel,
            target_url_or_apk=target,
            mission=prompt,
            bot_client=bot,
            mode=qa_mode
        )
        return

    # Check if channel is configured with QA Role (!role qa)
    if getattr(session, "role", "coder") == "qa":
        reload_helper_modules()
        from engine_manager import EngineManager
        qa_adapter = EngineManager.get_qa_adapter(session)
        qa_ch = qa_bot.get_channel(message.channel.id) if (qa_bot and qa_bot.is_ready()) else None
        target_sender = qa_ch or message.channel

        status_msg = await target_sender.send(
            f"🕵️ **[{qa_adapter.icon} {qa_adapter.name} QA 검증관 가동]**\n"
            f"🔍 워크스페이스 코드 변경 사항, 버그, 7대 품질 게이트 및 엣지 케이스 정밀 감사를 시작합니다..."
        )

        async def on_qa_tool_update(tool_status: str):
            try:
                await status_msg.edit(content=f"🕵️ **[{qa_adapter.icon} {qa_adapter.name} 검증 진행 중]**\n{tool_status}")
            except Exception:
                pass

        try:
            async with message.channel.typing():
                try:
                    ev_cls = globals().get("EvidenceValidator")
                    if ev_cls is None:
                        from evidence_validator import EvidenceValidator as ev_cls
                except Exception:
                    ev_cls = None

                if ev_cls:
                    diff_info = ev_cls.get_git_diff_summary(session.workspace)
                    gates_info = ev_cls.validate_all_gates(session.workspace)
                    is_anti_mock_clean, anti_mock_violations, _ = ev_cls.verify_anti_mocking_and_authenticity(session.workspace, prompt)
                else:
                    diff_info = ""
                    gates_info = {"status": "UNKNOWN", "gates": {}}
                    is_anti_mock_clean, anti_mock_violations = True, []

                p_id = GlobalModelManager.get_role_persona("qa1") if GlobalModelManager else "general"
                qa_prompt = EngineManager.build_qa_prompt(
                    user_prompt=prompt,
                    workspace=session.workspace,
                    diff_info=diff_info,
                    gates_info=gates_info,
                    anti_mock_violations=anti_mock_violations,
                    engine_name=qa_adapter.name,
                    persona_id=p_id
                )

                qa_proxy = ChannelSession(
                    channel_id=session.channel_id,
                    workspace=session.workspace,
                    engine=qa_adapter.engine_id,
                    role="qa",
                    mode="single"
                )

                final_response = await AgentRunner.run_prompt(
                    session=qa_proxy,
                    prompt=qa_prompt,
                    on_tool_update=on_qa_tool_update,
                    model=getattr(session, "engine_model", None)
                )
        finally:
            session.is_busy = False
            session.current_process = None
            try:
                await status_msg.delete()
            except Exception:
                pass

        chunks, files = await async_prepare_message_payload(
            final_response,
            workspace_root=session.workspace,
            auto_scan_deliverables=True,
            excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
            excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
        )
        for idx, chunk in enumerate(chunks):
            files_payload = files if idx == 0 else []
            await safe_send_discord_message(target_sender, chunk, files=files_payload)
        return

    # Ensure helper modules (commander, config, trio_agent_runner, etc.) are always reloaded fresh
    reload_helper_modules()

    # Determine effective mode (auto via Commander, or manual pair / single) and task type
    if session.mode == "auto":
        cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
        target_sender = cmd_ch or message.channel
        status_msg = await target_sender.send("👑 **[Commander 사령관 출동]** 🧠 의도 분석 및 최적 경로 판정 중...")
        decision = await Commander.analyze_intent(prompt, session.mode, session.workspace)
        effective_mode = mode_registry.normalize_mode(decision.mode)
        mode_reason = decision.reason
        task_type = decision.task_type
        # 🎛️ `+`/무인 자율 모드는 리서치성 프롬프트라도 확장 QA 누락 없이 다중 파이프라인 유지
        is_research = (
            task_type == "research"
            and not mode_registry.is_multi_qa_mode(effective_mode)
            and not mode_registry.is_autonomous_mode(effective_mode)
        )
        briefing = decision.briefing

        if effective_mode == "read":
            collab_desc = "Lead Architect ↔ Tech Reviewer ↔ Chief Strategist 읽기/기획 자문"
            status_label = (
                f"👑 **[Commander 사령관 브리핑]** 🔍 **3중 읽기/기획 전용 부대 출동** ({collab_desc})\n"
                f"• **판정 근거**: {mode_reason}\n"
                f"• **사령관 작전 브리핑**: *\"{briefing}\"*\n"
                f"🔍 코드 수정 없이 심층 아키텍처 자문을 시작합니다..."
            )
        elif effective_mode == "pair":
            collab_desc = "Analyst ↔ FactChecker ↔ ChiefAuditor 심층 리서치" if is_research else "Coder ↔ QA ↔ Auditor 협업 개발"
            status_label = (
                f"👑 **[Commander 사령관 브리핑]** 👥 **3중 협업부대 출동** ({collab_desc})\n"
                f"• **판정 근거**: {mode_reason}\n"
                f"• **사령관 작전 브리핑**: *\"{briefing}\"*\n"
                f"🚀 실시간 3중 협업을 시작합니다..."
            )
        else:
            status_label = (
                f"👑 **[Commander 사령관 브리핑]** 👤 **단일 에이전트 출동**\n"
                f"• **판정 근거**: {mode_reason}\n"
                f"• **사령관 작전 브리핑**: *\"{briefing}\"*\n"
                f"💬 AGY 단일 에이전트가 생각 중입니다..."
            )
        try:
            await status_msg.edit(content=status_label)
        except Exception:
            pass
    else:
        effective_mode, mode_reason = classify_mode(prompt, session)
        task_type = classify_task_type(prompt)
        # 🎛️ [모드 무결성] `+` 다중 QA 모드(pair_quad/mode3_plus)와 무인 자율 모드는
        #    리서치성 프롬프트라도 확장 QA가 없는 ResearchRunner 로 강등하지 않는다.
        is_research = (
            task_type == "research"
            and not mode_registry.is_multi_qa_mode(effective_mode)
            and not mode_registry.is_autonomous_mode(effective_mode)
        )
        # 🎛️ SSOT 정규화: 이후 모든 분기/표시가 단일 정규 모드 값을 사용하도록 통일
        effective_mode = mode_registry.normalize_mode(effective_mode)
        effective_pipeline = "pair" if effective_mode in ("pair_quad", "mode3", "mode3_plus") else effective_mode
        ext_cfgs = session.get_active_extended_qas() if hasattr(session, "get_active_extended_qas") else config.get_extended_qa_configs()
        _mode_info = mode_registry.resolve_mode(effective_mode, session)
        # 🚨 `+` 모드인데 활성 확장 QA가 0대이면 조용히 3중으로 붕괴하지 않고 명시 경고한다.
        if _mode_info.get("warning"):
            try:
                await safe_send_discord_message(message.channel, _mode_info["warning"])
            except Exception as _warn_err:
                logger.debug(f"Mode integrity warning delivery skipped: {_warn_err}")
        if ext_cfgs and effective_mode in ("pair_quad", "mode3_plus"):
            try:
                init_squad_plan = Commander.dispatch_qa_squad(
                    prompt=prompt,
                    diff_info=None,
                    available_extended_qas=ext_cfgs,
                    mode=effective_mode
                )
                dispatched_qas = init_squad_plan.get("selected_qas", [])
            except Exception as e:
                logger.error(f"Commander.dispatch_qa_squad error: {e}", exc_info=True)
                dispatched_qas = ext_cfgs
            multi_step_num = len(dispatched_qas) + 3 if dispatched_qas else (len(ext_cfgs) + 3 if ext_cfgs else 3)
            chain_names = " ↔ ".join([f"{c['name'].lower()}" for c in (dispatched_qas or ext_cfgs)])
            multi_label = f"{multi_step_num}중 다중" if multi_step_num > 4 else ("4중 쿼드" if multi_step_num == 4 else "3중")
        else:
            multi_step_num = 3
            multi_label = "3중"
            chain_names = "qa1"

        if effective_mode == "read":
            status_label = "🔍 **[3중 읽기/기획 전용 가동 (수동 고정)] Lead Architect ↔ Tech Reviewer ↔ Chief Strategist 방향성 자문 진행 중...**"
        elif effective_mode == "mode3_plus":
            collab_desc = f"Commander ➔ Coder ➔ qa1 ➔ {chain_names} ➔ Auditor ➔ Commander" if chain_names and chain_names != "qa1" else "Commander ➔ Coder ➔ qa1 ➔ Auditor ➔ Commander"
            status_label = f"🤖 **[무인 자율 순환 {multi_label} 협업 가동 (!mode 3+)] {collab_desc} 폐루프 자율 개발 진행 중입니다...**"
        elif effective_mode == "mode3":
            status_label = "🤖 **[무인 자율 순환 3중 협업 가동 (!mode 3)] Commander ➔ Coder ↔ QA ↔ Auditor ➔ Commander 폐루프 자율 개발 진행 중입니다...**"
        elif effective_mode == "pair_quad":
            collab_desc = "Lead Analyst ↔ Fact Checker ↔ Chief Auditor 심층 리서치" if is_research else (f"Coder ↔ qa1 ↔ {chain_names} ↔ Auditor 협업" if chain_names and chain_names != "qa1" else "Coder ↔ qa1 ↔ Auditor 협업")
            status_label = f"👥 **[{multi_label} 에이전트 가동 (사용자 주도 · !mode 2+)] {collab_desc} 진행 중입니다...**"
        elif effective_mode == "pair":
            collab_desc = "Lead Analyst ↔ Fact Checker ↔ Chief Auditor 심층 리서치" if is_research else "Coder ↔ QA ↔ Auditor 협업"
            status_label = f"👥 **[3중 에이전트 가동 (사용자 주도 · !mode 2)] {collab_desc} 진행 중입니다...**"
        elif effective_mode in ("pair_plus", "single_qa2"):
            status_label = "🥷 **[단일 qa2 가동 (수동 고정)] 작업 진행 중입니다...**"
        else:
            try:
                from model_manager import GlobalModelManager
            except Exception:
                GlobalModelManager = None
            coder_disp = GlobalModelManager.get_role_model_display("coder") if GlobalModelManager else "Coder"
            status_label = f"👨‍💻 **[단일 Coder({coder_disp}) 가동]** 작업을 시작합니다..."

        qa2_ch = qa2_bot.get_channel(message.channel.id) if ("qa2_bot" in globals() and qa2_bot and qa2_bot.is_ready()) else None
        cmd_ch = commander_bot.get_channel(message.channel.id) if ("commander_bot" in globals() and commander_bot and commander_bot.is_ready()) else None
        if effective_mode in ("pair_plus", "single_qa2") and qa2_ch:
            status_msg = await qa2_ch.send(status_label)
        elif effective_mode in ("mode3", "mode3_plus") and cmd_ch:
            status_msg = await cmd_ch.send(status_label)
        else:
            try:
                status_msg = await message.reply(status_label)
            except Exception:
                status_msg = await message.channel.send(status_label)

    status_updater = ThrottledStatusUpdater(status_msg, min_interval=2.0)

    def get_current_prefix() -> str:
        if effective_mode == "read":
            return f"👑 **[Commander: 🔍 3중 읽기/자문 ({mode_reason})]**" if session.mode == "auto" else "🔍 **[3중 읽기/자문 진행 중]**"
        elif effective_mode in ("pair", "pair_quad", "mode3", "mode3_plus"):
            if effective_mode == "mode3_plus":
                return f"🤖 **[무인 자율 {multi_label} 순환 진행 중 (!mode 3+)]**"
            elif effective_mode == "mode3":
                return "🤖 **[무인 자율 3중 순환 진행 중 (!mode 3)]**"
            elif effective_mode == "pair_quad":
                return f"👑 **[Commander: 👥 {multi_label} 협업 ({mode_reason})]**" if session.mode == "auto" else f"👥 **[{multi_label} 협업 진행 중]**"
            else:
                return f"👑 **[Commander: 👥 3중 협업 ({mode_reason})]**" if session.mode == "auto" else "👥 **[3중 협업 진행 중]**"
        elif effective_mode in ("pair_plus", "single_qa2"):
            return "🥷 **[단일 qa2 진행 중]**"
        else:
            try:
                from model_manager import GlobalModelManager
            except Exception:
                GlobalModelManager = None
            coder_disp = GlobalModelManager.get_role_model_display("coder") if GlobalModelManager else "Coder"
            return f"👑 **[Commander: 👨‍💻 Coder({coder_disp}) ({mode_reason})]**" if session.mode == "auto" else f"👨‍💻 **[Coder({coder_disp}) 작업 진행 중...]**"

    on_tool_update = status_updater.get_dynamic_callback(get_current_prefix)

    # Cancel any active idle timer on new user message

    session.current_task = asyncio.current_task()

    try:
        reload_helper_modules()
        async with message.channel.typing():
            if effective_mode == "read":
                final_response = await ConsultingRunner.run_pipeline(
                    session=session,
                    prompt=prompt,
                    main_channel=message.channel,
                    qa_client=qa_bot,
                    auditor_client=auditor_bot,
                    on_status_update=on_tool_update,
                    max_rounds=2
                )
            elif effective_mode in ("pair", "pair_quad", "mode3", "mode3_plus"):  # pair/pair_quad/mode3/mode3_plus multi-agent pipeline
                if is_research:
                    final_response = await ResearchRunner.run_pipeline(
                        session=session,
                        prompt=prompt,
                        main_channel=message.channel,
                        qa_client=qa_bot,
                        auditor_client=auditor_bot,
                        on_status_update=on_tool_update,
                        max_rounds=config.RESEARCH_MAX_ROUNDS
                    )
                else:
                    pipeline_prompt = prompt
                    if effective_mode in ("mode3", "mode3_plus"):
                        roadmap_file = os.path.join(session.workspace, "ROADMAP.md") if session.workspace else ""
                        prd_file = os.path.join(session.workspace, "PRD.md") if session.workspace else ""
                        is_initial_launch = not os.path.isfile(roadmap_file) or not os.path.isfile(prd_file)

                        # Detect if prompt is an explicit command, initial launch, or a new product creation intent
                        is_explicit_m3 = getattr(message, "_is_explicit_mode3_command", False)
                        is_auto_evolve = prompt.strip().startswith("🎯 [AGY 무인 자율 순환 마일스톤 진화") or prompt.strip().startswith("🎯 [Commander 사령관")
                        is_new_goal = False
                        has_feedback_signal = False
                        if not is_auto_evolve:
                            is_new_goal, has_feedback_signal = classify_user_intent(
                                prompt=prompt,
                                is_explicit_m3=is_explicit_m3,
                                project_initialized=(not is_initial_launch)
                            )

                        should_plan = is_initial_launch or is_new_goal
                        if is_initial_launch or is_new_goal or is_explicit_m3 or not is_auto_evolve:
                            session.set_root_user_goal(prompt)
                        cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
                        target_sender = cmd_ch or message.channel
                        mode_label = "3+" if effective_mode == "mode3_plus" else "3"
                        try:
                            from model_manager import GlobalModelManager
                        except:
                            GlobalModelManager = None
                        cmd_model_disp = GlobalModelManager.get_role_model_display("commander") if GlobalModelManager else getattr(config, "COMMANDER_MODEL", "gemini-3.7-flash-medium")

                        if should_plan:
                            try:
                                plan_result = await Commander.create_autonomous_product_plan_async(prompt, session.workspace)
                            except Exception as plan_err:
                                logger.warning(f"Async Commander planning fallback: {plan_err}")
                                plan_result = Commander.create_autonomous_product_plan(prompt, session.workspace)

                            p_def = plan_result["product_def"]
                            milestones = plan_result.get("milestones", [])

                            ladder_lines = []
                            for ms in milestones[:3]:
                                ver = ms.get("version", "")
                                tit = ms.get("title", "")
                                tasks = ms.get("tasks", [])
                                if tasks:
                                    task_preview = ", ".join(tasks[:2])
                                    ladder_lines.append(f"• **{ver}**: {tit} ({task_preview})")
                                else:
                                    ladder_lines.append(f"• **{ver}**: {tit}")
                            ladder_text = "\n".join(ladder_lines) if ladder_lines else (
                                f"• **v1.0 (1차)**: {p_def.title} 코어 아키텍처 & MVP 구현\n"
                                f"• **v2.0 (2차)**: {p_def.title} 심층 인터랙션 & 데이터 영속화\n"
                                f"• **v3.0 (3차)**: {p_def.title} 상용 품질 안정화 & E2E 무결점 검증"
                            )

                            product_msg_parts = [
                                f"👑 **[Commander 사령관 총괄 제품 기획 브리핑 — !mode {mode_label}]**\n",
                                f"🚀 **\"{p_def.title}\"** 제품의 무인 자율 순환 개발을 개시합니다.",
                                "사령관이 직접 수립한 맞춤형 아키텍처 및 3단계 로드맵입니다.\n",
                                f"📱 **앱 네이밍 & 아이덴티티**: **{p_def.app_naming or p_def.title}** (`{p_def.benchmark_reference}`)",
                                f"🎨 **디자인 분위기 / 룩앤필**: {p_def.design_vibe}",
                                f"👥 **타깃 고객 & 주 사용자층**: {p_def.target_demographic}",
                                f"💰 **수익성 & 배포/출시 전략**: {p_def.monetization_strategy}\n"
                            ]

                            vis_spec = plan_result.get("visual_asset_spec")
                            if vis_spec and p_def.domain not in ("cli_agent", "daemon", "backend_api"):
                                product_msg_parts.append(f"🎨 **시각 에셋 정책 (Rule 8)**: {vis_spec[:300]}\n")

                            product_msg_parts.append(f"🪜 **3단계 순차 마일스톤 사다리**\n{ladder_text}\n")
                            product_msg_parts.append(f"*(지휘 엔진: {cmd_model_disp} | 🛑 언제든지 `!stop`으로 무인 자율 순환을 즉시 중단할 수 있습니다)*")

                            try:
                                await safe_send_discord_message(target_sender, "\n".join(product_msg_parts))
                            except Exception as em_err:
                                logger.error(f"Failed to send Commander plan message: {em_err}")

                            # 5W1H Strategic Master Plan for Milestone 1 (v1.0)
                            m1 = milestones[0] if milestones else {}
                            m1_title = m1.get("title", f"{p_def.title} Core Architecture & MVP")
                            m1_version = m1.get("version", "v1.0")
                            m1_tasks = m1.get("tasks", [])
                            try:
                                plan_1_5w1h = await Commander.plan_milestone_5w1h_async(
                                    milestone_title=m1_title,
                                    milestone_version=m1_version,
                                    tasks=m1_tasks,
                                    domain=p_def.domain,
                                    workspace=session.workspace,
                                    user_prompt=prompt,
                                    project_initialized=False
                                )
                            except Exception as p1_err:
                                logger.warning(f"5W1H m1 async error: {p1_err}")
                                plan_1_5w1h = Commander.plan_milestone_5w1h(
                                    milestone_title=m1_title,
                                    milestone_version=m1_version,
                                    tasks=m1_tasks,
                                    domain=p_def.domain,
                                    workspace=session.workspace,
                                    user_prompt=prompt,
                                    project_initialized=False
                                )

                            try:
                                m1_text = plan_1_5w1h.to_discord_message(mode_label=mode_label, engine_disp=cmd_model_disp)
                                await safe_send_discord_message(target_sender, m1_text)
                            except Exception as em1_err:
                                logger.error(f"Failed to send 5W1H m1 message: {em1_err}")

                            # [🎨 Visual Craft Loop 연동] 5W1H 플랜(도메인/시각 에셋 승인/QC 계약)을 파이프라인에 바인딩
                            session._current_5w1h_plan = plan_1_5w1h
                            pipeline_prompt = plan_1_5w1h.directive_for_coder
                        elif not prompt.strip().startswith("🎯 [Commander 사령관"):
                            # User-directed incremental task or feedback: Commander prioritizes user intent over static roadmap
                            clean_user_p = prompt.strip()
                            is_direct_user_input = bool(clean_user_p and not clean_user_p.startswith("🎯 ["))

                            if is_direct_user_input:
                                # [Non-Actionable Input Guard (Chit-chat & Status Inquiry)]
                                # If prompt has no feedback signal and is not a new goal, prevent triggering the heavy autonomous pipeline.
                                if not has_feedback_signal and not is_new_goal:
                                    intent_res = FeedbackIntentClassifier.classify(clean_user_p, project_initialized=True)
                                    if intent_res.intent_type in ("chit_chat", "inquiry") or not intent_res.is_actionable:
                                        logger.info(f"[Bot] Non-actionable input detected ('{clean_user_p}'), returning guidance response without running pipeline.")
                                        if intent_res.intent_type == "inquiry" or any(k in clean_user_p.lower() for k in ["상태", "진행", "status", "어디까지"]):
                                            ms_info = "진행 중인 마일스톤 정보 없음"
                                            try:
                                                from roadmap_manager import RoadmapManager
                                                rm = RoadmapManager(session.workspace)
                                                curr = rm.get_current_milestone(auto_expand=False)
                                                if curr:
                                                    ms_info = f"현재 마일스톤: **{curr.version}** — {curr.title} ({curr.status})"
                                            except Exception:
                                                pass
                                            resp = (
                                                f"ℹ️ **[AGY 프로젝트 현황 안내]**\n"
                                                f"{ms_info}\n\n"
                                                f"구체적인 개선 요청이나 기능 추가 사항을 말씀해주시면 Commander가 새 마일스톤을 기획하여 개발을 진행합니다."
                                            )
                                        else:
                                            resp = (
                                                f"👋 **[Commander 사령관]** 안녕하세요! AGY 자율 순환 개발 봇입니다.\n"
                                                f"프로젝트에 반영할 구체적인 **개선 사항(버그 수정, 디자인 리뉴얼, 기능 추가 등)**을 말씀해주시면 즉시 반영 기획을 수립합니다.\n"
                                                f"*(예: `앱 메인 페이지에 열기, 합치기 버튼 추가해줘`, `PDF 열 때 튕김 현상 수정해줘`)*"
                                            )
                                        await safe_send_discord_message(target_sender, resp)
                                        return

                                # [🔥 SUPREME RULE: Direct user input ALWAYS overrides previous roadmap milestones]
                                session.set_root_user_goal(prompt)
                                clean_prompt_no_cmd = re.sub(r'^[!/]?mode\s+\S+\s*', '', clean_user_p, flags=re.IGNORECASE).strip() or clean_user_p

                                # Determine target version from Roadmap
                                ms_ver = "v1.0"
                                try:
                                    from roadmap_manager import RoadmapManager
                                    rm_ver = RoadmapManager(session.workspace)
                                    curr_ms = rm_ver.get_current_milestone(auto_expand=False)
                                    if curr_ms and not curr_ms.is_done:
                                        ms_ver = curr_ms.version
                                    else:
                                        completed = rm_ver.get_completed_milestones()
                                        if completed:
                                            ms_ver = rm_ver.get_next_milestone_version()
                                except Exception as rm_ver_err:
                                    logger.warning(f"Error determining next milestone version from RoadmapManager: {rm_ver_err}")
                                    ms_ver = "v1.0"

                                is_legacy_feedback_goal = ("글자를 넣어도 크기가 조절되지 않는" in clean_prompt_no_cmd or "첨부파일 컬러 팔레트" in clean_prompt_no_cmd)
                                if is_legacy_feedback_goal and Commander._feedback_all_delivered(workspace=session.workspace):
                                    ms_title = "요구사항 실체 구현 및 차기 역량 고도화"
                                    ms_tasks = ["사용자 요구사항 실체 구현", "빌드 및 무결점 테스트 검증"]
                                    try:
                                        from roadmap_manager import RoadmapManager
                                        rm_done = RoadmapManager(session.workspace)
                                        next_ms = rm_done.get_current_milestone(auto_expand=True, active_goal=clean_prompt_no_cmd)
                                        if next_ms:
                                            ms_title = next_ms.title
                                            ms_ver = next_ms.version
                                            ms_tasks = [t.text for t in next_ms.tasks] if next_ms.tasks else ms_tasks
                                    except Exception as rm_done_err:
                                        logger.debug(f"Roadmap next lookup notice: {rm_done_err}")
                                else:
                                    fb_decomp = Commander.decompose_user_feedback(clean_prompt_no_cmd, workspace=session.workspace)
                                    ms_title = fb_decomp["title"]
                                    ms_tasks = fb_decomp["tasks"]

                                # Sync this priority feedback milestone into ROADMAP.md immediately
                                try:
                                    from roadmap_manager import RoadmapManager
                                    rm = RoadmapManager(session.workspace)
                                    rm.append_or_update_adaptive_milestone(
                                        version=ms_ver,
                                        title=ms_title,
                                        tasks=ms_tasks,
                                        status="IN_PROGRESS"
                                    )
                                except Exception as rm_err:
                                    logger.debug(f"Roadmap feedback sync notice: {rm_err}")
                            else:
                                ms_title = "요구사항 실체 구현 및 품질 고도화"
                                ms_ver = "vNext"
                                ms_tasks = ["사용자 지시사항 실체 구현", "빌드 및 무결점 테스트 검증"]
                                try:
                                    from roadmap_manager import RoadmapManager
                                    rm = RoadmapManager(session.workspace)
                                    clean_auto_goal = prompt if (prompt and not any(prompt.strip().startswith(prefix) for prefix in ["🎯 [", "사용자 원본 목표:", "[무인 자율", "[🎯", "[📍", "[🔧", "👑 ["])) else ""
                                    curr_ms = rm.get_current_milestone(auto_expand=True, active_goal=clean_auto_goal)
                                    if curr_ms:
                                        ms_title = curr_ms.title
                                        ms_ver = curr_ms.version
                                        ms_tasks = [t.text for t in curr_ms.tasks] if curr_ms.tasks else ms_tasks
                                except Exception as rm_err:
                                    logger.debug(f"Roadmap lookup notice: {rm_err}")

                            macro_mem = session.format_commander_macro_memory() if hasattr(session, "format_commander_macro_memory") else ""
                            try:
                                plan_5w1h = await Commander.plan_milestone_5w1h_async(
                                    milestone_title=ms_title,
                                    milestone_version=ms_ver,
                                    tasks=ms_tasks,
                                    workspace=session.workspace,
                                    user_prompt=prompt,
                                    macro_memory_text=macro_mem,
                                    preserve_caller_milestone=True
                                )
                            except Exception as p_err:
                                logger.warning(f"5W1H ongoing async error: {p_err}")
                                plan_5w1h = Commander.plan_milestone_5w1h(
                                    milestone_title=ms_title,
                                    milestone_version=ms_ver,
                                    tasks=ms_tasks,
                                    workspace=session.workspace,
                                    user_prompt=prompt,
                                    macro_memory_text=macro_mem,
                                    preserve_caller_milestone=True
                                )

                            try:
                                m_text = plan_5w1h.to_discord_message(mode_label=mode_label, engine_disp=cmd_model_disp)
                                await safe_send_discord_message(target_sender, m_text)
                            except Exception as em_err:
                                logger.error(f"Failed to send 5W1H ongoing message: {em_err}")

                            # [🎨 Visual Craft Loop 연동] 진행 마일스톤 5W1H 플랜 바인딩
                            session._current_5w1h_plan = plan_5w1h
                            pipeline_prompt = plan_5w1h.directive_for_coder

                    final_response = await TrioAgentRunner.run_pipeline(
                        session=session,
                        prompt=pipeline_prompt,
                        main_channel=message.channel,
                        qa_client=qa_bot,
                        qa2_client=qa2_bot,
                        extended_qa_clients=globals().get("extended_qa_bots", {}),
                        auditor_client=auditor_bot,
                        on_status_update=on_tool_update,
                        max_rounds=PAIR_MAX_ROUNDS
                    )
            elif effective_mode in ("pair_plus", "single_qa2"):
                qa2_model = getattr(config, "QA2_MODEL", "upstage/solar-pro4")
                qa2_engine = getattr(config, "QA2_ENGINE", "openrouter")
                session.set_busy(True, f"🥷 AGY 단일 qa2 작업 진행 중 ({mode_reason})")
                session.role = "qa2"
                final_response = await AgentRunner.run_prompt(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=qa2_model,
                    engine=qa2_engine,
                    role="qa2"
                )
                session.set_status("idle", "🟢 작업 완료 (정상 대기)")
            else:
                try:
                    from model_manager import GlobalModelManager
                except Exception:
                    GlobalModelManager = None
                coder_disp = GlobalModelManager.get_role_model_display("coder") if GlobalModelManager else "Coder"
                coder_m = GlobalModelManager.get_role_model("coder") if GlobalModelManager else getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
                coder_eng = GlobalModelManager.get_role_engine("coder") if GlobalModelManager else getattr(config, "DEFAULT_AI_ENGINE", "agy")
                session.set_busy(True, f"👨‍💻 AGY 단일 Coder({coder_disp}) 작업 진행 중 ({mode_reason})")
                final_response = await AgentRunner.run_prompt(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=coder_m,
                    engine=coder_eng
                )
                session.set_status("idle", "🟢 작업 완료 (정상 대기)")

                # 🎨 [Image generator Tool] 단일 Coder가 이미지 생성을 요청한 경우 도구 호출 후 바인딩 재작업
                try:
                    from image_creator_hub import ImageCreatorHub
                    img_results = await ImageCreatorHub.process_agent_output(
                        final_response,
                        workspace=session.workspace or "",
                        session=session,
                        on_status=on_tool_update,
                        user_prompt=prompt,
                    )
                    if img_results:
                        for _img in img_results:
                            if not (_img.ok and _img.absolute_path and os.path.isfile(_img.absolute_path)):
                                continue
                            try:
                                _tag = "🎨 **[Image generator] 실제 AI 이미지 생성 완료**" if _img.is_real_ai else "⚠️ **[Image generator] 절차적 임시 에셋(AI 생성 실패 대체)**"
                                await safe_send_discord_message(
                                    message.channel,
                                    f"{_tag} `{_img.asset_name}.png`\n"
                                    f"• 배치: `{_img.relative_path}` ({_img.width}x{_img.height})\n"
                                    + (f"• 백엔드: `{_img.backend}` / `{_img.model}`\n" if _img.backend else "")
                                    + (f"• 사유: {_img.message}" if _img.fallback_used else ""),
                                    files=[discord.File(_img.absolute_path, filename=f"{_img.asset_name}.png")],
                                )
                            except Exception as _isend:
                                logger.warning(f"Image send skipped: {_isend}")
                            try:
                                await ImageCreatorHub.dispatch_to_studio(bot, _img, origin_channel_id=message.channel.id)
                            except Exception:
                                pass
                        # [정직 고지] 실패 건이 있으면 사용자에게 즉시 실패 사유를 알린다.
                        _img_fail_summary = ImageCreatorHub.summarize_failures(img_results)
                        if _img_fail_summary:
                            try:
                                await safe_send_discord_message(message.channel, _img_fail_summary)
                            except Exception as _fsend:
                                logger.warning(f"Image failure notice skipped: {_fsend}")
                        binding_guide = ImageCreatorHub.format_results_for_agent(img_results)
                        if binding_guide:
                            session.set_busy(True, "🎨 이미지 에셋 바인딩 작업 진행 중")
                            async with message.channel.typing():
                                bind_resp = await AgentRunner.run_prompt(
                                    session=session,
                                    prompt=(
                                        "🎨 **[Image generator 도구 결과 바인딩 지시]**\n"
                                        f"{binding_guide}"
                                    ),
                                    on_tool_update=on_tool_update,
                                    model=coder_m,
                                    engine=coder_eng,
                                )
                            if bind_resp and str(bind_resp).strip():
                                final_response = f"{final_response}\n\n\n{bind_resp}"
                            session.set_status("idle", "🟢 작업 완료 (정상 대기)")
                except Exception as _img_err:
                    logger.warning(f"Single-agent image request handling skipped: {_img_err}")

        # Record last response on session for PDCA tracking & debugging
        session._last_turn_response = str(final_response or "")
        if isinstance(message, _ScheduledWakeMessage):
            try:
                from auto_scheduler import AutoMetricsHistory
                AutoMetricsHistory.record_turn_output(
                    channel_id=message.channel.id,
                    prompt=prompt,
                    response=str(final_response or ""),
                    workspace=session.workspace
                )
            except Exception as _ar_err:
                logger.debug(f"[AutoScheduler] Immediate PDCA recording note: {_ar_err}")

        # Update or delete status message
        await status_updater.delete()

        # Multi-agent pipelines (pair, read) stream every turn directly to Discord.
        # Ensure single-agent responses, system errors/stoppages, AND direct analytical answers
        # (which do not end with standard Auditor approval strings) are guaranteed delivery.
        is_approval = (
            isinstance(final_response, str)
            and (
                "최종 승인" in final_response
                or "에이전트 협업 개발 완료 - 최종 승인" in final_response
                or "심층 리서치 완료 - 최종 승인" in final_response
                or "아키텍처 자문 완료" in final_response
                or "최종 감사 승인 완료" in final_response
                or "[STATUS: AUDIT_PASSED]" in final_response
                or "[STATUS: APPROVED]" in final_response
            )
        )
        is_stoppage_or_error = (
            (not is_approval)
            and (
                TrioAgentRunner.is_system_error(final_response)
                or TrioAgentRunner.is_cancellation_or_stop(final_response)
                or (isinstance(final_response, str) and final_response.strip().startswith(("🛑", "🚨", "⚠️")))
            )
        )
        is_direct_answer = (
            isinstance(final_response, str)
            and bool(final_response.strip())
            and not final_response.strip().startswith("🎉")
            and not is_approval
        )
        if effective_mode in ("single", "pair_plus", "single_qa2") or is_stoppage_or_error or is_direct_answer:
            chunks, file_attachments = await async_prepare_message_payload(
                final_response, 
                workspace_root=session.workspace,
                auto_scan_deliverables=True,
                excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
            )

            qa2_ch = qa2_bot.get_channel(message.channel.id) if (effective_mode in ("pair_plus", "single_qa2") and "qa2_bot" in globals() and qa2_bot and qa2_bot.is_ready()) else None
            target_dest = qa2_ch or message

            if len(chunks) > 3:
                summary_header = chunks[0]
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tf:
                    tf.write(final_response)
                    temp_resp_file = tf.name

                try:
                    files_payload = [discord.File(temp_resp_file, filename="response.md")] + file_attachments
                    await safe_send_discord_message(
                        target_dest,
                        f"{summary_header}\n\n📎 **[전체 결과가 길어 파일로 첨부합니다]**",
                        files=files_payload,
                        is_reply=(target_dest == message)
                    )
                finally:
                    try:
                        os.remove(temp_resp_file)
                    except Exception:
                        pass
            else:
                for i, chunk in enumerate(chunks):
                    files_to_send = file_attachments if i == 0 else []
                    if i == 0:
                        await safe_send_discord_message(target_dest, chunk, files=files_to_send, is_reply=(target_dest == message))
                    else:
                        await safe_send_discord_message(target_dest if hasattr(target_dest, "send") else message.channel, chunk, files=files_to_send)

        # Schedule idle auto-pilot inspection if live URL is detected

        # --- [Mode 3 / Mode 3+ Fully Autonomous Closed-Loop Continuation] ---
        mode3_next_dispatched = False
        if (
            session.mode in ("mode3", "mode3_plus")
            and is_approval
            and not session.is_stopped
            and not is_stoppage_or_error
        ):
            try:
                from roadmap_manager import RoadmapManager
                rm = RoadmapManager(session.workspace)
                completed_ms = rm.mark_current_milestone_done()

                # 0.5. [Auto Git Checkpoint & Tagging for Milestone]
                if session.workspace and os.path.isdir(session.workspace) and completed_ms:
                    try:
                        # Prevent git bloat by ensuring binary artifacts (*.apk, build/, .gradle/) are ignored
                        gi_path = os.path.join(session.workspace, ".gitignore")
                        essential_gi = ["*.apk", "*.aab", "build/", "/build/", "app/build/", ".gradle/", "local.properties", "*.iml"]
                        existing_gi = set()
                        if os.path.isfile(gi_path):
                            try:
                                with open(gi_path, "r", encoding="utf-8", errors="ignore") as _gf:
                                    existing_gi = set(l.strip() for l in _gf if l.strip() and not l.startswith("#"))
                            except Exception:
                                pass
                        missing_gi = [p for p in essential_gi if p not in existing_gi]
                        if missing_gi:
                            try:
                                with open(gi_path, "a", encoding="utf-8") as _gf:
                                    _gf.write("\n# Auto-added by AGY to prevent git bloat\n" + "\n".join(missing_gi) + "\n")
                            except Exception:
                                pass

                        ver_tag = completed_ms.version.strip()
                        commit_msg = f"feat(milestone): complete {ver_tag} - {completed_ms.title}"
                        # Stage non-ignored source files
                        p_add = await asyncio.create_subprocess_shell("git add -A", cwd=session.workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        await asyncio.wait_for(p_add.communicate(), timeout=10.0)

                        # Check if anything changed
                        p_diff = await asyncio.create_subprocess_shell("git diff --cached --quiet", cwd=session.workspace)
                        diff_rc = await asyncio.wait_for(p_diff.wait(), timeout=5.0)
                        if diff_rc != 0:
                            p_commit = await asyncio.create_subprocess_shell(f'git commit -m "{commit_msg}"', cwd=session.workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                            await asyncio.wait_for(p_commit.communicate(), timeout=10.0)

                        # Tag current HEAD
                        p_tag = await asyncio.create_subprocess_shell(f'git tag -f "{ver_tag}"', cwd=session.workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        await asyncio.wait_for(p_tag.communicate(), timeout=5.0)
                        logger.info(f"[Mode 3 Git Checkpoint] Tagged {ver_tag} (committed={diff_rc != 0})")
                    except Exception as git_err:
                        logger.debug(f"[Mode 3 Git Checkpoint] Note: {git_err}")

                completed_title = f"{completed_ms.version} — {completed_ms.title}" if completed_ms else "이전 마일스톤"

                # 1. Extract clean concise conclusion from Auditor final response
                aud_lines = [
                    line.strip() for line in (final_response or "").splitlines()
                    if line.strip() and not line.strip().startswith(("#", "```", "---", "[STATUS:", "###"))
                ]
                auditor_summary = " ".join(aud_lines[:3])[:400] if aud_lines else "마일스톤 전수 검증 통과 및 최종 승인 완료"

                # 2. Record clean macro cycle memory (Commander directive + Auditor conclusion ONLY)
                cmd_directive_summary = pipeline_prompt[:350] if 'pipeline_prompt' in locals() else ""
                if hasattr(session, "record_commander_macro_cycle"):
                    session.record_commander_macro_cycle(
                        milestone=completed_title,
                        commander_directive=cmd_directive_summary,
                        auditor_conclusion=auditor_summary
                    )

                # 3. [CRITICAL USER MANDATE] Reset Coder and QA contexts so they start completely fresh for next milestone!
                if hasattr(session, "reset_cycle_workers"):
                    session.reset_cycle_workers()
                else:
                    session.reset_conversation()

                # [🏁 Graceful Finish Mandate] If user issued !done, stop gracefully right here!
                if getattr(session, "stop_after_current_cycle", False):
                    session.stop_after_current_cycle = False
                    session.set_status("idle", f"🏁 마일스톤({completed_title}) 완료 후 정상 종료 (!done)")
                    cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
                    target_sender = cmd_ch or message.channel
                    await safe_send_discord_message(
                        target_sender,
                        f"🏁 **[Mode 3 자율 순환 정상 종료 — !done 완료]**\n"
                        f"✅ 마일스톤 `{completed_title}` 구현 및 Auditor 최종 승인이 안전하게 완료되었습니다!\n"
                        f"사전 요청하신 `{COMMAND_PREFIX}done` 명령어에 따라 차기 마일스톤을 시작하지 않고 시스템을 정상 대기 상태로 전환합니다.\n\n"
                        f"💡 추가 개발이 필요하시면 언제든 새 명령어나 `{COMMAND_PREFIX}mode 3`을 입력해 주세요."
                    )
                    logger.info(f"[Mode 3 !done] Stopped after completing {completed_title} as requested by user.")
                    return

                # 🛑 [Commander Epic Loop Guard: 하드 리밋 & 수확 체감 감지]
                session._commander_cycle_count = int(getattr(session, "_commander_cycle_count", 0) or 0) + 1
                from round_control import COMMANDER_MAX_CYCLES, parse_commander_decision, detect_diminishing_returns
                cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
                target_sender = cmd_ch or message.channel

                # 1) 최대 에픽 사이클 상한 도달 시 정상 완결
                if session._commander_cycle_count >= COMMANDER_MAX_CYCLES:
                    session.set_status("idle", f"🏁 최대 마일스톤 에픽 상한({COMMANDER_MAX_CYCLES}회) 도달로 정상 완료")
                    await safe_send_discord_message(
                        target_sender,
                        f"🏁 **[Mode 3 자율 순환 최종 완결 — 최대 사이클({COMMANDER_MAX_CYCLES}회) 도달]**\n"
                        f"✅ 마일스톤 `{completed_title}` 승인 완료 및 전체 {session._commander_cycle_count}개 에픽 개발이 성공적으로 완료되었습니다!\n"
                        f"• 사유: 무한 루프 방지 상한({COMMANDER_MAX_CYCLES}회)에 도달하여 시스템을 안전한 대기(idle) 상태로 전환합니다.\n\n"
                        f"💡 추가 개발이 필요하시면 새 명령어나 `{COMMAND_PREFIX}mode 3`을 입력해 주세요."
                    )
                    logger.info(f"[Mode 3 Auto-Cycle] Reached COMMANDER_MAX_CYCLES ({COMMANDER_MAX_CYCLES}). Terminated gracefully.")
                    return

                # 2) 수확 체감(Diminishing Returns) 감지 시 정상 완결
                current_diff_info = EvidenceValidator.inspect_production_diff(session.workspace or "")
                current_diff_lines = current_diff_info.get("prod_diff_lines", 0)
                prev_diff_lines = getattr(session, "_last_milestone_diff_lines", None)
                session._last_milestone_diff_lines = current_diff_lines

                if prev_diff_lines is not None:
                    is_diminishing, dim_reason = detect_diminishing_returns(
                        prev_diff_lines, current_diff_lines, session._commander_cycle_count
                    )
                    if is_diminishing:
                        session.set_status("idle", f"🏁 수확 체감 감지({dim_reason})로 자율 개발 정상 완료")
                        await safe_send_discord_message(
                            target_sender,
                            f"🏁 **[Mode 3 자율 순환 수렴 완료 — 수확 체감 감지]**\n"
                            f"✅ 마일스톤 `{completed_title}`까지의 핵심 기능이 모두 수렴되었습니다.\n"
                            f"• 판정 사유: {dim_reason}\n"
                            f"• 추가 코드 변경량이 미미하여 불필요한 반복 핑퐁을 방지하고 시스템을 정상 대기(idle) 상태로 종결합니다."
                        )
                        logger.info(f"[Mode 3 Auto-Cycle] Diminishing returns detected: {dim_reason}. Terminated gracefully.")
                        return

                # [🛡️ Early Quota Protection] Check Gemini quota before calling Commander 5W1H planner
                cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
                target_sender = cmd_ch or message.channel
                try:
                    from telemetry_status_manager import fetch_native_antigravity_usage
                    _usage = fetch_native_antigravity_usage()
                    _gemini_rem = 1.0
                    for _grp in _usage.get("groups", []):
                        if "gemini" in _grp.get("name", "").lower():
                            for _bkt in _grp.get("buckets", []):
                                if "5" in _bkt.get("name", "").lower() or "five" in _bkt.get("name", "").lower():
                                    _gemini_rem = float(_bkt.get("remaining_fraction", 1.0))
                                    break
                    if _gemini_rem < 0.15:
                        logger.warning(f"[Mode 3 Plan Guard] Gemini quota low ({_gemini_rem*100:.0f}%) before 5W1H planning. Halting auto-continuation.")
                        await safe_send_discord_message(
                            target_sender,
                            f"⛔ **[Mode 3 자율 체인 보호 중단]** Gemini 5시간 할당량 잔여 `{_gemini_rem*100:.0f}%` — 15% 미만으로 차기 마일스톤 기획 및 진행을 안전하게 중단합니다.\n"
                            f"할당량 갱신 후 새 메시지를 입력하면 재개됩니다."
                        )
                        return
                except Exception as _qr_early:
                    logger.debug(f"Quota check before Mode 3 planning failed (non-critical): {_qr_early}")

                # 4. [Adaptive Codebase-Grounded Dynamic 5W1H Strategic Master Plan for Next Milestone]
                macro_mem = session.format_commander_macro_memory() if hasattr(session, "format_commander_macro_memory") else ""
                root_goal = session.get_root_user_goal() or prompt
                # [🛡️ Quota Firewall] 무인 자율 순환 구간 — Gemini 자동 호출 차단 컨텍스트로 실행
                try:
                    from quota_firewall import unattended as _qfw_unattended
                    _qfw_ctx = _qfw_unattended()
                except Exception:
                    from contextlib import nullcontext
                    _qfw_ctx = nullcontext()
                try:
                    with _qfw_ctx:
                        next_plan_5w1h = await Commander.plan_adaptive_next_milestone_async(
                            root_user_goal=root_goal,
                            workspace=session.workspace,
                            macro_memory_text=macro_mem,
                            current_completed_title=completed_title
                        )
                except Exception as np_err:
                    logger.warning(f"Async adaptive next milestone 5W1H plan fallback: {np_err}")
                    next_plan_5w1h = Commander.plan_adaptive_next_milestone(
                        root_user_goal=root_goal,
                        workspace=session.workspace,
                        macro_memory_text=macro_mem
                    )

                # [🎨 Visual Craft Loop 연동] 차기 마일스톤 5W1H 플랜(도메인/시각 에셋 승인/QC) 바인딩
                session._current_5w1h_plan = next_plan_5w1h
                next_title = f"{next_plan_5w1h.version} — {next_plan_5w1h.title}"

                # 3) Commander 기획의 명시적 완료 판정([COMMANDER_DECISION: COMPLETE]) 감지
                # plan.decision 과 directive_for_coder 파싱 결과 중 하나라도 COMPLETE 이면 우선 완결 처리
                plan_dec = str(getattr(next_plan_5w1h, "decision", "") or "").strip().upper()
                directive_dec = parse_commander_decision(next_plan_5w1h.directive_for_coder or "").upper()
                cmd_decision = "COMPLETE" if (plan_dec == "COMPLETE" or directive_dec == "COMPLETE") else (plan_dec or directive_dec)
                if cmd_decision == "COMPLETE":
                    session.set_status("idle", "🏁 Commander 최종 목표 달성 판단으로 자율 개발 완료")
                    await safe_send_discord_message(
                        target_sender,
                        f"🎉 **[Mode 3 자율 개발 최종 완결 — [COMMANDER_DECISION: COMPLETE]]**\n"
                        f"👑 **Commander 판정**: 프로젝트 목표 달성률 및 가치가 충분하여 자율 사이클을 완결합니다!\n"
                        f"• 직전 마일스톤: `{completed_title}` (Auditor 최종 승인 완료)\n"
                        f"• 사유: 핵심 기능과 비즈니스 로직이 프로덕션 표준으로 완결되었습니다.\n\n"
                        f"💡 추가 기능이 필요하시면 새로운 지시사항을 입력해 주세요."
                    )
                    logger.info("[Mode 3 Auto-Cycle] Commander decided COMPLETE. Terminated gracefully.")
                    return

                mode3_next_dispatched = True


                logger.info(
                    f"[Mode 3 Auto-Cycle] Milestone '{completed_title}' approved. "
                    f"Coder & QA contexts reset to clean slate (0 context baggage) for adaptive milestone '{next_title}'."
                )

                cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
                target_sender = cmd_ch or message.channel
                mode_label = "3+" if session.mode == "mode3_plus" else "3"
                try:
                    from model_manager import GlobalModelManager
                except Exception:
                    GlobalModelManager = None
                cmd_model_disp = GlobalModelManager.get_role_model_display("commander") if GlobalModelManager else getattr(config, "COMMANDER_MODEL", "gemini-3.7-flash-medium")

                next_header = (
                    f"👑 **[Commander 차기 마일스톤 5W1H 전략 기획 — {next_plan_5w1h.version}: {next_plan_5w1h.title}]**\n\n"
                    f"✅ 이전 단계 `{completed_title}` Auditor 최종 승인 완료!\n"
                    f"🧹 **[컨텍스트 초기화 완료]** Coder & QA 대화 기억을 완전히 초기화(Clean State)하여 사령관 차기 마스터플랜에 정렬했습니다.\n\n"
                )
                next_msg = next_header + next_plan_5w1h.to_discord_message(mode_label=mode_label, engine_disp=cmd_model_disp)
                try:
                    await safe_send_discord_message(target_sender, next_msg)
                except Exception as em_err:
                    logger.error(f"Failed to send next milestone 5W1H message: {em_err}")
                    await safe_send_discord_message(
                        target_sender,
                        f"👑 **[Commander 차기 마일스톤 5W1H 전략 수립 — !mode {mode_label}]**\n"
                        f"✅ `{completed_title}` 승인 완료 ➔ Coder/QA 컨텍스트 초기화 완료\n"
                        f"🚀 **차기 목표**: `{next_title}` 육하원칙 마스터플랜 가동\n"
                        f"*(비상 정지: `{COMMAND_PREFIX}stop`)*"
                    )

                evolve_prompt = next_plan_5w1h.directive_for_coder

                # If no manual message queued by user, auto-dispatch next milestone turn
                if not session.queue:
                    # [🛡️ RISK 5 Fix] Check Gemini quota before Mode 3 auto-chain dispatch
                    _ok_to_chain = True
                    try:
                        from telemetry_status_manager import fetch_native_antigravity_usage
                        _usage = fetch_native_antigravity_usage()
                        _gemini_rem = 1.0
                        for _grp in _usage.get("groups", []):
                            if "gemini" in _grp.get("name", "").lower():
                                for _bkt in _grp.get("buckets", []):
                                    if "5" in _bkt.get("name", "").lower() or "five" in _bkt.get("name", "").lower():
                                        _gemini_rem = float(_bkt.get("remaining_fraction", 1.0))
                                        break
                        if _gemini_rem < 0.15:
                            _ok_to_chain = False
                            logger.warning(f"[Mode 3 Auto-Chain] Gemini quota low ({_gemini_rem*100:.0f}%). Halting auto milestone continuation.")
                            await safe_send_discord_message(
                                target_sender,
                                f"⛔ **[Mode 3 자율 체인 보호 중단]** Gemini 5시간 할당량 잔여 `{_gemini_rem*100:.0f}%` — 15% 미만으로 차기 마일스톤 자동 진행을 중단합니다.\n"
                                f"할당량 갱신 후 다음 메시지를 입력하면 재개됩니다."
                            )
                    except Exception as _qr5:
                        logger.debug(f"Quota check before Mode 3 auto-chain failed (non-critical): {_qr5}")

                    if _ok_to_chain:
                        async def _delayed_next_milestone():
                            session.intermission_event = asyncio.Event()
                            session.intermission_feedback = None
                            session.intermission_paused = False
                            session.mode3_pending_prompt = evolve_prompt

                            int_msg_content = (
                                f"⏱️ **[Mode 3 인터미션 — 차기 마일스톤({next_plan_5w1h.version}) 진입 전 30초 검수 대기]**\n"
                                f"✅ 이전 단계 `{completed_title}` 개발 및 빌드가 완료되었습니다.\n\n"
                                f"💡 **지금 피드백을 입력하시면** 차기 마일스톤 기획에 최우선 반영됩니다.\n"
                                f"• 즉시 차기 마일스톤 시작: `{COMMAND_PREFIX}next`\n"
                                f"• 잠시 멈추고 직접 테스트: `{COMMAND_PREFIX}pause`\n"
                                f"• 이번 마일스톤 완료 후 안전 종료: `{COMMAND_PREFIX}done`\n"
                                f"• 무인 자율 순환 완전 중단: `{COMMAND_PREFIX}stop`\n\n"
                                f"*(30초 동안 추가 입력이 없으면 차기 마일스톤이 자동으로 시작됩니다)*"
                            )
                            int_msg = None
                            try:
                                int_msg = await target_sender.send(int_msg_content)
                            except Exception:
                                pass

                            try:
                                await asyncio.wait_for(session.intermission_event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                pass

                            if int_msg:
                                try:
                                    await int_msg.delete()
                                except Exception:
                                    pass

                            session.intermission_event = None
                            if session.is_stopped:
                                session.mode3_pending_prompt = None
                                return
                            if getattr(session, "stop_after_current_cycle", False):
                                session.stop_after_current_cycle = False
                                session.mode3_pending_prompt = None
                                session.set_status("idle", f"🏁 마일스톤({completed_title}) 완료 후 정상 종료 (!done)")
                                return
                            if getattr(session, "intermission_paused", False):
                                logger.info(f"[Mode 3 Intermission] Paused by user for channel {message.channel.id}")
                                session.set_status("idle", f"⏸️ Mode 3 일시 정지 ({next_plan_5w1h.version} 대기 중)")
                                return

                            current_prompt = evolve_prompt
                            if getattr(session, "intermission_feedback", None):
                                fb = session.intermission_feedback
                                session.intermission_feedback = None
                                session.set_root_user_goal(fb)
                                clean_fb = re.sub(r'^[!/]?mode\s+\S+\s*', '', fb, flags=re.IGNORECASE).strip() or fb

                                is_legacy_feedback_goal = ("글자를 넣어도 크기가 조절되지 않는" in clean_fb or "첨부파일 컬러 팔레트" in clean_fb)
                                if is_legacy_feedback_goal and Commander._feedback_all_delivered(workspace=session.workspace):
                                    _safe_fallback_ver = (next_plan_5w1h.version if next_plan_5w1h and next_plan_5w1h.version else "v1.0")
                                    _re_title, _re_ver = "사용자 피드백 긴급 반영", _safe_fallback_ver
                                    try:
                                        from roadmap_manager import RoadmapManager
                                        _rm_re = RoadmapManager(session.workspace)
                                        _nxt = _rm_re.get_current_milestone(auto_expand=True)
                                        if _nxt:
                                            _re_title, _re_ver = _nxt.title, _nxt.version
                                        else:
                                            _re_ver = _rm_re.get_next_milestone_version()
                                    except Exception as _re_guard_err:
                                        logger.debug(f"Intermission feedback guard notice: {_re_guard_err}")
                                    _re_tasks = None
                                else:
                                    fb_decomp = Commander.decompose_user_feedback(clean_fb, workspace=session.workspace)
                                    _re_title, _re_ver = fb_decomp["title"], next_plan_5w1h.version
                                    _re_tasks = fb_decomp["tasks"]

                                try:
                                    re_plan = await Commander.plan_milestone_5w1h_async(
                                        milestone_title=_re_title,
                                        milestone_version=_re_ver,
                                        tasks=_re_tasks,
                                        workspace=session.workspace,
                                        user_prompt=clean_fb,
                                        macro_memory_text=macro_mem
                                    )
                                    re_plan_msg = (
                                        f"💡 **[사용자 피드백 긴급 반영 차기 기획]**\n\n"
                                        f"{re_plan.to_discord_message(mode_label=mode_label, engine_disp=cmd_model_disp)}"
                                    )
                                    await safe_send_discord_message(target_sender, re_plan_msg)
                                    current_prompt = re_plan.directive_for_coder

                                    try:
                                        from roadmap_manager import RoadmapManager
                                        _rm_sync = RoadmapManager(session.workspace)
                                        _rm_sync.append_or_update_adaptive_milestone(
                                            version=_re_ver,
                                            title=_re_title,
                                            tasks=_re_tasks or re_plan.what,
                                            status="IN_PROGRESS"
                                        )
                                    except Exception as _rm_sync_err:
                                        logger.debug(f"Intermission roadmap sync notice: {_rm_sync_err}")
                                except Exception as _re_err:
                                    logger.warning(f"Re-planning with intermission feedback failed: {_re_err}")
                                    current_prompt = (
                                        f"**[🚨 사용자 현장 검수 피드백 최우선 반영 지침]**:\n"
                                        f"\"{fb}\"\n\n"
                                        f"{evolve_prompt}"
                                    )
                            session.mode3_pending_prompt = None
                            # [🛡️ Quota Firewall] 무인 자동 순환(Mode 3 Auto) 작업 실행 구간:
                            # Gemini 계열 모델의 자동 호출을 차단하여 야간/무인 상태에서
                            # Antigravity 한도가 저절로 소모되는 것을 방지합니다.
                            try:
                                from quota_firewall import unattended as _qfw_unattended
                                _qfw_exec_ctx = _qfw_unattended()
                            except Exception:
                                from contextlib import nullcontext
                                _qfw_exec_ctx = nullcontext()
                            with _qfw_exec_ctx:
                                await execute_channel_prompt(message, current_prompt)

                        asyncio.create_task(_delayed_next_milestone())
            except Exception as m3_err:
                logger.error(f"Mode 3 auto-continuation error: {m3_err}", exc_info=True)
        elif (
            session.mode in ("mode3", "mode3_plus")
            and not session.is_stopped
            and not is_approval
            and not is_stoppage_or_error
        ):
            # QA or Auditor rejected or exhausted hotfix rounds without approval
            try:
                from roadmap_manager import RoadmapManager
                rm = RoadmapManager(session.workspace)
                cur_ms = rm.get_current_milestone(auto_expand=False)
                ms_label = f"`{cur_ms.version} — {cur_ms.title}`" if cur_ms else "현재 마일스톤"
            except Exception:
                cur_ms = None
                ms_label = "현재 마일스톤"

            warn_msg = (
                f"⚠️ **[Mode 3 자율 순환 일시 정지 — 마일스톤 비승인]**\n\n"
                f"{ms_label} 작업이 QA/Auditor 품질 기준 또는 핫픽스 라운드 한도에 도달하여 자동 승인되지 못했습니다.\n\n"
                f"💡 **선택 가능한 제어 옵션**:\n"
                f"• **재시도 / 추가 피드백**: 보완할 지침을 메시지로 입력하시면 해당 마일스톤을 재작업합니다.\n"
                f"• **다음 단계로 강제 건너뛰기**: `{COMMAND_PREFIX}next` 를 입력하시면 해당 마일스톤을 스킵하고 차기를 진행합니다.\n"
                f"• **자율 모드 해제**: `{COMMAND_PREFIX}mode 2` 또는 `{COMMAND_PREFIX}stop`"
            )
            try:
                cmd_ch = commander_bot.get_channel(message.channel.id) if (commander_bot and commander_bot.is_ready()) else None
                target_sender = cmd_ch or message.channel
                await safe_send_discord_message(target_sender, warn_msg)
            except Exception as w_err:
                logger.debug(f"Failed to send Mode 3 rejection notice: {w_err}")

    except asyncio.CancelledError:
        await status_updater.delete()
        session.set_status("idle", "정상 대기 중 (작업 중단)")
        await message.channel.send("🛑 **진행 중이던 작업이 즉시 강제 중단(Stop)되었습니다.**")
    except Exception as e:
        logger.exception("대화 처리 중 오류 발생")
        err_trace = traceback.format_exc()
        session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
        await message.channel.send(f"⚠️ 요청 처리 중 오류가 발생했습니다: {str(e)}")
    finally:
        session.set_busy(False, "정상 대기 중")
        session.current_task = None
        session.current_process = None
        asyncio.create_task(dispatch_next_in_queue(message.channel.id))

@bot.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Ignore webhook messages and Discord system events to prevent event feedback loops
    if getattr(message, "webhook_id", None) is not None:
        return
    if hasattr(message, "is_system") and message.is_system():
        return

    # Ignore non-command messages in telemetry status channel (#status) to avoid accidental LLM dispatch
    try:
        from telemetry_status_manager import STATUS_CHANNEL_ID
        if message.channel.id == STATUS_CHANNEL_ID and not any(message.content.startswith(p) for p in COMMAND_PREFIXES):
            return
    except Exception:
        pass

    ensure_commander_bot_started()
    ensure_image_bot_started()
    ensure_qa2_bot_started()

    # Check for prefix command first (! or ~)
    matched_prefix = next((p for p in COMMAND_PREFIXES if message.content.startswith(p)), None)
    if matched_prefix:
        reload_helper_modules()
        raw_cmd = message.content[len(matched_prefix):].strip().split()
        if raw_cmd:
            cmd_name = raw_cmd[0].lower()
            cmd_args = " ".join(raw_cmd[1:])
            
            # Direct interception for hot-loaded commands
            if cmd_name in ("help", "hlep", "도움말", "도움", "명령어", "commands", "guide", "man", "메뉴", "menu"):
                import help_manager
                importlib.reload(help_manager)
                view = help_manager.HelpInteractiveView(initial_page=cmd_args, user_id=message.author.id)
                embed = view.get_current_embed()
                msg = await message.channel.send(embed=embed, view=view, files=view.get_files())
                view.message = msg
                return
            elif cmd_name in ("plan", "기획"):
                import plan_wizard
                importlib.reload(plan_wizard)
                async with message.channel.typing():
                    await plan_wizard.handle_plan_command(
                        ctx=message,
                        initial_idea=cmd_args,
                        session_manager=session_manager,
                        bot_client=bot,
                        qa_bot_client=qa_bot,
                        auditor_bot_client=auditor_bot
                    )
                return
            elif cmd_name in ("setup", "셋업", "설정", "init", "preset", "프리셋", "초기설정"):
                import setup_manager
                importlib.reload(setup_manager)
                await setup_manager.handle_setup_command(
                    message=message,
                    preset_key=cmd_args,
                    session_manager=session_manager,
                    command_prefix=COMMAND_PREFIX
                )
                return
            elif cmd_name in ("reset", "리셋", "reload", "restart", "hotreload", "초기화", "핫리셋"):
                await execute_all_in_one_hot_reset(message.channel, message.channel.id, arg_text=cmd_args, is_legacy_clear=False)
                return
            elif cmd_name in ("stop", "sto", "halt", "cancel", "kill", "abort", "중지", "정지", "취소", "그만", "멈춰", "중단"):
                ctx = await bot.get_context(message)
                await stop_command(ctx, arg=cmd_args)
                return
            elif cmd_name in ("done", "완료", "finish", "마무리", "끝", "stop_after_cycle"):
                ctx = await bot.get_context(message)
                await done_command(ctx, arg=cmd_args)
                return
            elif cmd_name in ("pause", "일시정지", "대기", "잠깐", "hold", "wait"):
                session = session_manager.get_session(message.channel.id)
                if getattr(session, "intermission_event", None) and not session.intermission_event.is_set():
                    session.intermission_paused = True
                    session.intermission_event.set()
                    await message.channel.send("⏸️ **[Mode 3 자율 순환 일시 정지]** 차기 마일스톤 자동 시작이 일시 정지되었습니다.\n언제든 `!next` 또는 `!resume`을 입력하시면 즉시 재개됩니다.")
                    return
                else:
                    await message.channel.send("ℹ️ 현재 대기 중인 인터미션이 없습니다. (진행 중인 작업을 중단하려면 `!stop`을 사용하세요.)")
                    return
            elif cmd_name in ("next", "다음", "resume", "재개", "go", "진행"):
                session = session_manager.get_session(message.channel.id)
                session.stop_after_current_cycle = False
                if getattr(session, "intermission_event", None) and not session.intermission_event.is_set():
                    session.intermission_paused = False
                    session.intermission_event.set()
                    await message.channel.send("🚀 **[Mode 3 인터미션 건너뛰기]** 차기 마일스톤 개발을 즉시 시작합니다!")
                    return
                elif getattr(session, "mode3_pending_prompt", None) and not session.is_busy:
                    p = session.mode3_pending_prompt
                    session.mode3_pending_prompt = None
                    await message.channel.send("🚀 **[Mode 3 일시 정지 해제]** 보류 중이던 차기 마일스톤 개발을 재개합니다!")
                    asyncio.create_task(execute_channel_prompt(message, p))
                    return
                elif session.mode in ("mode3", "mode3_plus") and not session.is_busy:
                    # If idle after non-approval or stoppage, skip current milestone and evolve
                    try:
                        from roadmap_manager import RoadmapManager
                        rm = RoadmapManager(session.workspace)
                        cur_ms = rm.get_current_milestone(auto_expand=False)
                        if cur_ms:
                            skipped = rm.skip_current_milestone(reason="사용자 !next 명령에 의한 수동 건너뛰기")
                            next_p = rm.build_skip_and_evolve_prompt(skipped, reason="사용자 !next 명령에 의한 수동 건너뛰기")
                            await message.channel.send(f"⏭️ **[Mode 3 마일스톤 건너뛰기]** `{cur_ms.version}`을 건너뛰고 차기 마일스톤 개발을 시작합니다!")
                            asyncio.create_task(execute_channel_prompt(message, next_p))
                            return
                    except Exception as sk_err:
                        logger.error(f"Failed to skip milestone via !next: {sk_err}")
                    await message.channel.send("ℹ️ 현재 대기 또는 보류 중인 차기 마일스톤이 없습니다.")
                    return
                else:
                    await message.channel.send("ℹ️ 현재 대기 또는 보류 중인 차기 마일스톤이 없습니다.")
                    return
            elif cmd_name in ("model", "모델", "modelselect"):
                ctx = await bot.get_context(message)
                await model_command(ctx, *cmd_args.split())
                return
            elif cmd_name in ("identity", "whoami", "id", "self", "정체성", "나", "본체", "agent", "who"):
                session = session_manager.get_session(message.channel.id)
                engine = getattr(session, "engine", "agy").lower().strip()
                direct_badge = "agy" if engine == "agy" else "hermes"
                coder_model = getattr(session, "engine_model", None) or getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
                
                embed = discord.Embed(
                    title=f"🤖 **[에이전트 정체성 확인]** `{direct_badge}`",
                    description=f"현재 채널 세션은 **`{direct_badge}`** 에이전트로 정상 구동 중입니다.",
                    color=0x34A853 if engine == "agy" else 0x9B59B6
                )
                embed.add_field(name="에이전트 (Identity)", value=f"**`{direct_badge}`**", inline=True)
                embed.add_field(name="Coder 엔진", value=f"`{engine.upper()}`", inline=True)
                embed.add_field(name="배정 모델", value=f"`{coder_model}`", inline=True)
                embed.add_field(name="봇 사용자명", value=f"`{bot.user.name}`", inline=True)
                embed.add_field(name="세션 상태", value="🟢 정상 구동 (명령 수신 대기 중)", inline=True)
                embed.add_field(name="채널 역할", value=f"`{getattr(session, 'role', 'coder').upper()}`", inline=True)
                embed.set_footer(text=f"엔진 전환: {COMMAND_PREFIX}engine coder [agy|hermes] | 정체성 확인: {COMMAND_PREFIX}identity")
                
                await message.channel.send(f"{direct_badge}", embed=embed)
                return
            elif cmd_name in ("bots", "소개", "봇소개", "agents", "에이전트", "botlist", "botinfo"):
                ctx = await bot.get_context(message)
                await bots_command(ctx)
                return
            elif cmd_name in ("clear", "clean", "cls", "new", "대화초기화", "세션초기화", "새세션", "새대화", "비우기"):
                await execute_all_in_one_hot_reset(message.channel, message.channel.id, arg_text=cmd_args, is_legacy_clear=True)
                return
            elif cmd_name in ("qasetup", "qa-setup", "qaplan") or (cmd_name in ("qa", "e2e", "test", "검증", "테스트") and any(w in cmd_args.lower().split() for w in ["setup", "config", "plan", "셋업", "설정", "위저드", "wizard"])):
                import qa_wizard
                importlib.reload(qa_wizard)
                await qa_wizard.handle_qa_setup_command(
                    ctx=message,
                    session_manager=session_manager,
                    bot_client=bot,
                    qa_bot_client=qa_bot,
                    auditor_bot_client=auditor_bot,
                    qa_hub_manager=qa_hub_manager,
                    target_arg=""
                )
                return
            elif cmd_name in ("mkdir", "폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"):
                ctx = await bot.get_context(message)
                await mkdir_command(ctx, path=cmd_args)
                return

        # Pre-check: Ensure any mistakenly bare callable in bot.all_commands is converted to a Command before processing,
        # and repair any circular callback references that would cause infinite recursion
        if raw_cmd:
            invoker = raw_cmd[0].lower()
            existing = bot.all_commands.get(invoker)
            if existing is not None and callable(existing) and not hasattr(existing, "invoke"):
                from discord.ext import commands as ext_commands
                logger.warning(f"Self-healing: fixing bare callable '{invoker}' in bot.all_commands")
                bot.all_commands[invoker] = ext_commands.Command(existing, name=invoker)
            elif existing is not None and hasattr(existing, "callback"):
                if existing.callback is existing or hasattr(existing.callback, "invoke"):
                    logger.warning(f"Self-healing: fixing circular callback on command '{invoker}' before execution")
                    cand = globals().get(f"{invoker}_command") or globals().get(invoker)
                    while hasattr(cand, "callback"):
                        if cand is existing:
                            cand = None
                            break
                        cand = getattr(cand, "callback", None)
                    if callable(cand) and not hasattr(cand, "invoke"):
                        existing.callback = cand
                    elif invoker in ["mkdir", "폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"]:
                        cand_m = globals().get("mkdir_command")
                        while hasattr(cand_m, "callback"):
                            if cand_m is existing:
                                cand_m = None
                                break
                            cand_m = getattr(cand_m, "callback", None)
                        if callable(cand_m) and not hasattr(cand_m, "invoke"):
                            existing.callback = cand_m

        try:
            await bot.process_commands(message)
        except AttributeError as ae:
            if "'function' object has no attribute 'invoke'" in str(ae):
                logger.warning(f"Self-healing: caught invoke error on command '{raw_cmd[0] if raw_cmd else ''}'. Healing all commands and retrying...")
                from discord.ext import commands as ext_commands
                for k, v in list(bot.all_commands.items()):
                    if callable(v) and not hasattr(v, "invoke"):
                        bot.all_commands[k] = ext_commands.Command(v, name=k)
                await bot.process_commands(message)
            else:
                raise
        return

    if hasattr(message.channel, "category_id") or hasattr(message.channel, "category"):
        await check_and_apply_finish_project_freeze(message.channel, notify=False)

    session = session_manager.get_session(message.channel.id)

    # Clean prompt text
    raw_user_prompt = message.content.replace(f"<@{bot.user.id}>", "").strip() if bot.user else message.content.strip()
    if commander_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{commander_bot.user.id}>", "").strip()
    if qa_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{qa_bot.user.id}>", "").strip()
    for ext_bot in globals().get("extended_qa_bots", {}).values():
        if ext_bot and ext_bot.user:
            raw_user_prompt = raw_user_prompt.replace(f"<@{ext_bot.user.id}>", "").replace(f"<@!{ext_bot.user.id}>", "").strip()
    if auditor_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{auditor_bot.user.id}>", "").strip()
    if "image_bot" in globals() and image_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{image_bot.user.id}>", "").replace(f"<@!{image_bot.user.id}>", "").strip()
    if qa2_bot.user:
        raw_user_prompt = raw_user_prompt.replace(f"<@{qa2_bot.user.id}>", "").replace(f"<@!{qa2_bot.user.id}>", "").strip()

    prompt = raw_user_prompt

    # Mode 3 Intermission Check: If intermission is active, capture user input as feedback and wake up loop immediately!
    if getattr(session, "intermission_event", None) and not session.intermission_event.is_set():
        if prompt:
            session.intermission_feedback = prompt
            session.intermission_event.set()
            try:
                await message.reply(f"💡 **[사용자 피드백 접수 완료]** \"{prompt}\"\n차기 마일스톤 기획에 최우선 반영하여 즉시 개발 순환을 재개합니다! 🚀")
            except Exception:
                await message.channel.send(f"💡 **[사용자 피드백 접수 완료]** \"{prompt}\"\n차기 마일스톤 기획에 최우선 반영하여 즉시 개발 순환을 재개합니다! 🚀")
            return

    # Handle attachments (text files read into prompt; voice/audio transcribed; media formatted safely)
    attachment_notes = []
    if message.attachments:
        ws = session.workspace if (session and session.workspace) else None
        if not ws:
            ws = getattr(config, "DEFAULT_WORKSPACE", None) or os.getcwd()

        # [📁 First-Class 사용자 첨부파일/참고자료 디렉토리 자동 구성]
        # 사용자가 번거롭게 워크스페이스에 직접 들어가 '참고자료' 폴더를 수동 생성할 필요 없이,
        # 디스코드로 전송된 모든 파일(코드, 데이터셋, 엑셀, 문서, 바이너리, 이미지 등)을
        # 워크스페이스 내 '참고자료' 폴더에 자동 영구 보존하고 상호 연동합니다.
        user_refs_dir = os.path.join(ws, "참고자료")
        dot_ref_dir = os.path.join(ws, ".references", "user_inputs")
        en_refs_dir = os.path.join(ws, "references")
        try:
            os.makedirs(user_refs_dir, exist_ok=True)
            os.makedirs(dot_ref_dir, mode=0o700, exist_ok=True)
            if not os.path.exists(en_refs_dir):
                try:
                    os.symlink("참고자료", en_refs_dir)
                except Exception:
                    pass
        except Exception as _dir_err:
            logger.warning(f"Failed to initialize user attachment dirs in workspace {ws}: {_dir_err}")

        temp_dir = tempfile.mkdtemp(prefix="agy_discord_att_")
        for att in message.attachments:
            orig_filename = att.filename or "attachment.bin"
            safe_filename = sanitize_attachment_filename(orig_filename)
            stem_d, ext_d = os.path.splitext(orig_filename)
            display_name = orig_filename if len(orig_filename) <= 60 else (stem_d[:40] + "..." + ext_d)

            temp_file_path = os.path.join(temp_dir, safe_filename)
            try:
                await att.save(temp_file_path)
            except Exception as save_err:
                logger.error(f"Failed to save attachment '{orig_filename}' as '{temp_file_path}': {save_err}")
                try:
                    await message.reply(f"⚠️ 첨부 파일(`{display_name}`)을 저장하는 중 오류가 발생하여 건너뜁니다: `{save_err}`")
                except Exception:
                    pass
                continue

            # [💾 워크스페이스 내 '참고자료' 실물 파일 영구 보존]
            dest_file_path = os.path.join(user_refs_dir, safe_filename)
            try:
                shutil.copy2(temp_file_path, dest_file_path)
            except Exception as copy_err:
                logger.warning(f"Failed to copy attachment to {dest_file_path}: {copy_err}")
                dest_file_path = temp_file_path

            # 에이전트가 바로 사용할 워크스페이스 기준 상대 경로 (forward slash 통일)
            if ws and dest_file_path.startswith(ws):
                rel_file_path = os.path.relpath(dest_file_path, ws).replace("\\", "/")
            else:
                rel_file_path = dest_file_path

            file_sz = os.path.getsize(dest_file_path) if os.path.isfile(dest_file_path) else 0
            if file_sz >= 1024 * 1024:
                file_sz_str = f"{file_sz / (1024 * 1024):.2f} MB"
            else:
                file_sz_str = f"{file_sz / 1024:.1f} KB"

            from audio_transcriber import AudioTranscriber

            # 1. Audio / Voice message files
            if AudioTranscriber.is_audio_file(safe_filename):
                transcription = await AudioTranscriber.transcribe_audio(dest_file_path)
                if transcription:
                    attachment_notes.append(
                        f"[🎙️ 음성 메시지 음성인식(STT) 내용: {display_name}]\n\"{transcription}\"\n"
                        f"- 워크스페이스 저장 위치: `{rel_file_path}` (절대경로: `{dest_file_path}`)"
                    )
                    try:
                        await message.reply(
                            f"🎙️ **[음성 메시지 인식 완료]**\n> \"{transcription}\"\n"
                            f"> 💾 음성 원본 저장: `{rel_file_path}`"
                        )
                    except Exception:
                        pass
                else:
                    if raw_user_prompt:
                        attachment_notes.append(
                            f"[첨부 오디오 파일: {display_name}] (저장 위치: `{rel_file_path}`, 절대경로: `{dest_file_path}`)"
                        )
                    else:
                        await message.reply(
                            f"🎙️ **[음성 메시지 수신]**\n"
                            f"음성 파일(`{display_name}`)을 수신했으나 음성을 명확히 인식하지 못했습니다.\n"
                            f"원하시는 작업 요청 내용을 텍스트로 채팅방에 남겨주시면 즉시 에이전트가 완벽히 처리해 드리겠습니다! 🚀"
                        )
                        return

            # 2. Text-based files (code, scripts, config, data, logs, etc.)
            elif safe_filename.lower().endswith((
                ".txt", ".md", ".py", ".js", ".ts", ".json", ".html", ".css", ".jsx", ".tsx",
                ".sh", ".yaml", ".yml", ".xml", ".csv", ".log", ".env", ".sql", ".ini", ".conf", ".toml"
            )):
                try:
                    file_size = os.path.getsize(dest_file_path)
                    max_read_bytes = 20000  # 20KB limit to protect memory and token budget
                    with open(dest_file_path, "r", encoding="utf-8", errors="replace") as f:
                        file_text_content = f.read(max_read_bytes)
                    truncation_notice = "\n... [파일 용량이 커서 앞부분 20KB만 프롬프트에 포함되었습니다]" if file_size > max_read_bytes else ""
                    attachment_notes.append(
                        f"\n[📄 첨부 파일: {display_name} ({file_sz_str})]\n"
                        f"- 워크스페이스 저장 위치: `{rel_file_path}` (절대경로: `{dest_file_path}`)\n"
                        f"- 지침: 사용자가 디스코드로 전달한 실제 파일이 워크스페이스 내 `{rel_file_path}`에 실물 파일로 온전히 저장되었습니다. "
                        f"STRICT WORKSPACE CONFINEMENT 규범에 부합하므로, 워크스페이스 내에서 직접 파일을 열람, 코드 검토, 스크립트 실행(예: python {rel_file_path}), 단위/호환성 테스트, 빌드 검증을 직접 완결하십시오.\n"
                        f"```\n{file_text_content}{truncation_notice}\n```"
                    )
                    try:
                        await message.reply(
                            f"📁 **[첨부 코드/문서 워크스페이스 저장 완료: `{display_name}`]**\n"
                            f"> 저장 위치: `{rel_file_path}` ({file_sz_str})\n"
                            f"> 에이전트가 워크스페이스 내 실물 파일에 직접 접근하여 호환성 검증 및 실행 테스트를 즉시 수행합니다! 🚀"
                        )
                    except Exception:
                        pass
                except Exception as _read_err:
                    attachment_notes.append(
                        f"[📄 첨부 파일: {display_name}] (저장 위치: `{rel_file_path}`, 절대경로: `{dest_file_path}`)\n"
                        f"- 지침: 워크스페이스 내 파일에 직접 접근하여 확인/실행하십시오."
                    )

            # 3. PDF Document files
            elif safe_filename.lower().endswith(".pdf"):
                from vision_attachment_analyzer import VisionAttachmentAnalyzer
                doc_res = VisionAttachmentAnalyzer.analyze_document_file(dest_file_path)
                if doc_res.get("success") and doc_res.get("content"):
                    p_cnt = doc_res.get("page_count", 1)
                    p_ext = doc_res.get("pages_extracted", 1)
                    attachment_notes.append(
                        f"\n[📑 첨부 PDF 문서 내용: {display_name} (총 {p_cnt}페이지 중 {p_ext}페이지 추출, {file_sz_str})]\n"
                        f"- 워크스페이스 저장 위치: `{rel_file_path}` (절대경로: `{dest_file_path}`)\n"
                        f"- 지침: 원본 PDF 파일이 워크스페이스 내 `{rel_file_path}`에 실물로 보존되어 있으므로 필요 시 직접 도구/라이브러리(pypdf, pdfplumber 등)로 열람 및 호환성 검증을 수행하십시오.\n"
                        f"{doc_res['content']}"
                    )
                    try:
                        await message.reply(
                            f"📑 **[PDF 문서 내용 분석 및 저장 완료: `{display_name}`]**\n"
                            f"> 저장 위치: `{rel_file_path}` ({file_sz_str})\n"
                            f"> 총 `{p_cnt}`페이지 중 `{p_ext}`페이지의 텍스트를 추출하여 에이전트에 전달했습니다! 🚀"
                        )
                    except Exception:
                        pass
                else:
                    attachment_notes.append(
                        f"[📑 첨부 PDF 파일: {display_name} ({file_sz_str})]\n"
                        f"- 워크스페이스 저장 위치: `{rel_file_path}` (절대경로: `{dest_file_path}`)\n"
                        f"- 지침: PDF 파일이 워크스페이스 내 `{rel_file_path}`에 보존되었습니다. 도구로 직접 열람 및 호환성을 검증하십시오."
                    )
                    try:
                        await message.reply(
                            f"📑 **[PDF 문서 워크스페이스 저장 완료: `{display_name}`]**\n"
                            f"> 저장 위치: `{rel_file_path}` ({file_sz_str})\n"
                            f"> 에이전트가 해당 경로에서 직접 파일을 열람하고 호환성 검증을 수행합니다! 🚀"
                        )
                    except Exception:
                        pass

            # 4. Image files (Multimodal Vision Intelligence & Reference Assets)
            elif safe_filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                ref_file_path = dest_file_path
                # 기존 .references/user_inputs 호환용 복사
                dot_file_path = os.path.join(dot_ref_dir, safe_filename)
                try:
                    shutil.copy2(dest_file_path, dot_file_path)
                except Exception:
                    pass

                sha256_hash = ""
                try:
                    with open(ref_file_path, "rb") as rf:
                        sha256_hash = hashlib.sha256(rf.read()).hexdigest()
                except Exception:
                    pass

                dhash = None
                try:
                    ev_cls = globals().get("EvidenceValidator")
                    if ev_cls is None:
                        from evidence_validator import EvidenceValidator as ev_cls
                    dhash = ev_cls.compute_image_dhash(ref_file_path)
                except Exception as _ev_err:
                    logger.warning(f"Failed to compute image dhash: {_ev_err}")
                    dhash = None

                if session:
                    session.register_user_reference_image(
                        filename=safe_filename,
                        file_path=ref_file_path,
                        sha256=sha256_hash,
                        dhash=dhash,
                        size_bytes=file_sz
                    )

                # High-speed Multimodal Vision Analysis for Attached Images
                vision_status_msg = None
                try:
                    vision_status_msg = await message.reply(
                        f"🖼️ **[첨부 이미지 시각 분석 진행 중...]**\n"
                        f"> 파일명: `{display_name}` · 저장 위치: `{rel_file_path}`\n"
                        f"> 화면 구성, OCR 텍스트, UI/UX 레이아웃을 정밀 분석하고 있습니다. ⏳"
                    )
                except Exception:
                    pass

                from vision_attachment_analyzer import VisionAttachmentAnalyzer
                # [🛑 취소 가능한 비전 분석] 인라인 await 는 !stop/!reset 이 취소할 수 없어
                # "이미지 시각 분석 진행 중" 상태가 계속 남았다. 태스크로 추적하여
                # session.stop() 이 즉시 취소하고, 이 메시지의 후속 실행도 함께 중단한다.
                vision_task = asyncio.ensure_future(
                    VisionAttachmentAnalyzer.analyze_image_file(
                        image_path=ref_file_path,
                        user_prompt=raw_user_prompt or "첨부된 이미지의 디자인과 레이아웃을 분석하여 구현해줘",
                        role="general"
                    )
                )
                if session:
                    session.vision_task = vision_task
                try:
                    vision_res = await vision_task
                except asyncio.CancelledError:
                    # 외부(봇 종료 등) 취소가 아니라 !stop/!reset 에 의한 취소일 때만 삼킨다.
                    if not vision_task.cancelled():
                        raise
                    if vision_status_msg:
                        try:
                            await vision_status_msg.edit(
                                content=f"🛑 **[첨부 이미지 시각 분석 중단됨: `{display_name}`]**"
                            )
                        except Exception:
                            pass
                    return
                finally:
                    if session and getattr(session, "vision_task", None) is vision_task:
                        session.vision_task = None

                vision_report_text = ""
                vision_verified = bool(vision_res.get("vision_verified"))
                if vision_res.get("success") and vision_res.get("analysis"):
                    vision_report_text = vision_res["analysis"]
                    if vision_status_msg:
                        try:
                            if vision_verified:
                                status_content = (
                                    f"🖼️ **[첨부 이미지 시각 분석 완료: `{display_name}`]**\n"
                                    f"> 📁 저장 위치: `{rel_file_path}`\n"
                                    f"> 🔍 **비전 분석 완료** (`{vision_res.get('engine') or 'vision'}` · {vision_res.get('width')}x{vision_res.get('height')} px)\n"
                                    f"> 화면 내 텍스트(OCR 전수 추출) 및 레이아웃/스타일 토큰이 에이전트(Coder/QA) 작업에 즉시 주입되었습니다! 🚀"
                                )
                            else:
                                status_content = (
                                    f"🖼️ **[첨부 이미지 처리 완료: `{display_name}`]**\n"
                                    f"> 📁 저장 위치: `{rel_file_path}`\n"
                                    f"> ⚠️ **비전 모델 미호출 — 메타데이터만 확보** ({vision_res.get('width')}x{vision_res.get('height')} px)\n"
                                    f"> 사유: {vision_res.get('degraded_reason') or '비전 API 미설정/호출 실패'}. "
                                    f"원본 파일 경로가 에이전트에 전달되어 직접 확인 모드로 진행합니다."
                                )
                            await vision_status_msg.edit(content=status_content)
                        except Exception:
                            pass
                else:
                    if vision_status_msg:
                        try:
                            await vision_status_msg.edit(
                                content=f"🖼️ **[첨부 이미지 수신 완료: `{display_name}`]** (저장 위치: `{rel_file_path}` · 에이전트 직접 검증 모드)"
                            )
                        except Exception:
                            pass

                if vision_report_text and vision_verified:
                    vision_block = f"\n\n**[🔍 정밀 시각 분석 리포트 (`{vision_res.get('engine') or 'vision'}`)]**:\n{vision_report_text}\n"
                elif vision_report_text:
                    vision_block = f"\n\n**[🖼️ 이미지 메타데이터 (정밀 비전 분석 미수행)]**:\n{vision_report_text}\n"
                else:
                    vision_block = ""

                is_evaluation_or_question = any(kw in (raw_user_prompt or "").lower() for kw in [
                    "몇점", "몇 점", "어때", "감점", "해결책", "원인", "의견", "어떻게 생각해",
                    "진단", "평가", "피드백", "궁금", "질문", "조언", "자문", "비교", "리뷰"
                ])

                anti_tracing_rule = (
                    f"⚠️ **[레퍼런스 이미지 외곽선 추출(Tracing) 및 모킹 절대 금지]**: 위 이미지는 사용자가 의뢰 시 제공한 원본 레퍼런스입니다.\n"
                    f"이 이미지를 그대로 복사하거나, 스크립트 코드(Image.open, cv2.imread, findContours, np.array 등)에서 열어 외곽선/마스크/픽셀을 추출하여 산출물로 위장하는 행위는 엄격히 금지됩니다. (오직 시각적 파악 및 비교 검증용)"
                )
                # ♻️ [중복 판독 방지] 비전 모델이 정밀 분석에 성공한 경우, Coder/QA 가 원본을
                #    다시 열어 같은 이미지를 두 번 읽는 낭비를 줄이도록 리포트를 우선 신뢰하게 한다.
                if vision_verified:
                    anti_tracing_rule += (
                        f"\n♻️ **[중복 판독 방지]**: 위 시각 분석 리포트가 이미 이미지의 텍스트(OCR)·레이아웃·스타일을 "
                        f"전수 추출했습니다. 별도의 시각 비교가 꼭 필요한 경우가 아니라면 동일한 원본 이미지를 다시 열어 "
                        f"중복 분석하지 말고 이 리포트를 신뢰하십시오."
                    )

                if not raw_user_prompt:
                    attachment_notes.append(
                        f"[🖼️ 사용자 제공 참고(레퍼런스) 이미지: {display_name}] (저장 위치: {rel_file_path}, 절대경로: {ref_file_path})"
                        f"{vision_block}\n"
                        f"{anti_tracing_rule}\n"
                        f"사용자 요청: 첨부된 이미지의 화면 구성, 텍스트, UI/UX 디자인 또는 에러/진단 내용을 정밀 분석하고, 사용자의 의도에 맞게 문제 해결/코드 개선/구현/답변을 최우선으로 제공해줘."
                    )
                elif is_evaluation_or_question:
                    attachment_notes.append(
                        f"[🖼️ 사용자 제공 참고(레퍼런스) 이미지: {display_name}] (저장 위치: {rel_file_path}, 절대경로: {ref_file_path})"
                        f"{vision_block}\n"
                        f"{anti_tracing_rule}\n"
                        f"🎯 **[사용자 질문 답변 최우선 지침]**: 사용자가 첨부 이미지에 대한 평가, 점수, 감점 요인, 해결책, 또는 자문을 요청했습니다. "
                        f"장황한 빌드 보고서만 앞세우지 말고, 사용자의 질문에 대한 구체적이고 전문적인 분석과 명쾌한 답변을 최우선으로 제공하십시오."
                    )
                else:
                    attachment_notes.append(
                        f"[🖼️ 사용자 제공 참고(레퍼런스) 이미지: {display_name}] (저장 위치: {rel_file_path}, 절대경로: {ref_file_path})"
                        f"{vision_block}\n"
                        f"{anti_tracing_rule}\n"
                        f"사용자 원본 요청을 충실히 반영하여 고유한 새 산출물을 생성하거나 필요한 코드/작업을 완결하십시오."
                    )

            # 5. Other binary / office / dataset / archive / executable files (호환성 및 실행 검증)
            else:
                attachment_notes.append(
                    f"[📦 첨부 파일 (호환성/실행 검증 대상): {display_name} ({file_sz_str})]\n"
                    f"- 워크스페이스 저장 위치: `{rel_file_path}` (절대경로: `{dest_file_path}`)\n"
                    f"- 핵심 지침: 사용자가 파일 열람, 호환성 테스트, 실행 검증 또는 데이터 분석을 위해 디스코드로 전달한 실제 파일입니다. "
                    f"파일이 워크스페이스 내 `{rel_file_path}`에 실물로 안전하게 저장되어 있으므로, STRICT WORKSPACE CONFINEMENT 규범을 준수하면서 "
                    f"해당 경로에서 직접 파일을 열람(python pandas, openpyxl, sqlite3, zipfile, subprocess 등 활용)하거나 "
                    f"실행 및 호환성/무결성 테스트를 즉시 완결하고 그 결과를 상세히 보고하십시오."
                )
                try:
                    await message.reply(
                        f"📦 **[첨부 파일 워크스페이스 저장 완료: `{display_name}`]**\n"
                        f"> 저장 위치: `{rel_file_path}` ({file_sz_str})\n"
                        f"> 사용자가 전달한 파일이 워크스페이스에 안전하게 배치되었습니다. 에이전트가 파일 열람 및 호환성/실행 검증을 즉시 진행합니다! 🚀"
                    )
                except Exception:
                    pass

    if attachment_notes:
        prompt = (prompt + "\n\n" if prompt else "") + "\n".join(attachment_notes)

    if not prompt or not prompt.strip():
        return

    # If session is currently busy or QA Hub is actively verifying this channel, enqueue the message!
    is_qa_busy = bool(qa_hub_manager and qa_hub_manager.channel_tasks.get(message.channel.id))
    if session.is_busy or is_qa_busy:
        if is_qa_busy and not session.is_busy:
            session.set_busy(True, "🧪 QA 허브(#qa-tester) E2E 검증 진행 중")
        pos = session.enqueue(message, prompt)
        qa_tag = " (중앙 QA 허브 E2E 검증 진행 중)" if (is_qa_busy or "QA 허브" in getattr(session, "status_desc", "")) else ""
        embed = discord.Embed(
            title=f"📥 [작업 대기열 등록 · {pos}번째]",
            description=(
                f"현재 채널에서 작업이 진행 중입니다{qa_tag}:\n"
                f"**진행 상태:** `{session.status_desc}`\n\n"
                f"진행 중인 QA/개발 작업이 완료되는 즉시 이 요청을 **자동으로 이어서 실행**합니다! 🚀\n\n"
                f"**등록된 요청 내용:**\n`{prompt[:250]}`\n\n"
                f"• 즉시 현재 작업을 중단하고 이 작업을 시작: `{COMMAND_PREFIX}stop`\n"
                f"• 대기열 조회: `{COMMAND_PREFIX}queue` · 대기열 전체 취소: `{COMMAND_PREFIX}stop all`"
            ),
            color=discord.Color.blue()
        )
        await message.reply(embed=embed)
        return

    # Session is idle: execute directly
    await execute_channel_prompt(message, prompt)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id")
        if custom_id == "telemetry_status_refresh" and not interaction.response.is_done():
            # Silent ACK: no popup or ephemeral message
            try:
                await interaction.response.defer()
            except Exception:
                pass
            try:
                import telemetry_status_manager as tsm
                ok = await tsm.update_status_message(interaction.client, session_manager, force=True)
                if not ok:
                    await asyncio.to_thread(tsm.update_status_via_rest_api, session_manager, True)
            except Exception as e:
                logger.error(f"bot on_interaction error: {e}")

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if hasattr(ctx.command, 'on_error'):
        return

    # 1. CommandNotFound: Unregistered command or user typo -> smart fuzzy suggestion
    if isinstance(error, commands.CommandNotFound):
        invoked = ctx.invoked_with or ""
        matched_prefix = next((p for p in COMMAND_PREFIXES if ctx.message.content.startswith(p)), None)
        if not invoked and matched_prefix:
            parts = ctx.message.content[len(matched_prefix):].strip().split()
            if parts:
                invoked = parts[0]

        all_cmds = set()
        for cmd in bot.commands:
            if not getattr(cmd, "hidden", False):
                all_cmds.add(cmd.name)
                all_cmds.update(cmd.aliases)
        all_cmds.update([
            "help", "plan", "setup", "clear", "new", "reset", "qa", "qasetup", "auto", "stop", "mode", "read",
            "single", "pair", "status", "quota", "export", "zip", "apk", "drive", "colab", "sim",
            "android_test", "win_test", "selfhealing", "temp", "queue", "clear-queue", "pwd", "cd",
            "engine", "model", "role", "identity", "git", "watchdog", "golden5", "essential",
            "기획", "셋업", "설정", "리셋", "초기화", "정지", "멈춰", "중단", "취소", "모드", "엔진",
            "도움말", "상태", "쿼터", "사용량", "토큰", "압축", "다운로드", "백업", "시뮬", "시뮬레이터",
            "핵심", "5대명령어", "필수", "골든5", "치트시트"
        ])

        import difflib
        matches = difflib.get_close_matches(invoked.lower(), list(all_cmds), n=3, cutoff=0.5)

        msg_text = f"❓ **알 수 없는 명령어입니다:** `{COMMAND_PREFIX}{invoked}`\n"
        if matches:
            suggestions = ", ".join(f"`{COMMAND_PREFIX}{m}`" for m in matches)
            msg_text += f"💡 혹시 이 명령어를 찾으셨나요? 👉 {suggestions}\n"
        msg_text += f"📖 전체 명령어 목록과 사용법은 `{COMMAND_PREFIX}help`를 입력하여 확인하실 수 있습니다."

        try:
            await safe_send_discord_message(ctx.channel, msg_text)
        except Exception:
            pass
        return

    # 2. MissingRequiredArgument: Parameter omitted
    if isinstance(error, commands.MissingRequiredArgument):
        cmd_name = ctx.command.name if ctx.command else "명령어"
        try:
            await safe_send_discord_message(
                ctx.channel,
                f"⚠️ **필수 인수가 누락되었습니다:** `{error.param.name}`\n"
                f"📖 자세한 사용법은 `{COMMAND_PREFIX}help {cmd_name}`을 참고해주세요."
            )
        except Exception:
            pass
        return

    # 3. UserInputError / BadArgument / TooManyArguments: Invalid format
    if isinstance(error, commands.UserInputError):
        cmd_name = ctx.command.name if ctx.command else "명령어"
        try:
            await safe_send_discord_message(
                ctx.channel,
                f"⚠️ **명령어 입력 형식이 올바르지 않습니다:** {str(error)}\n"
                f"📖 자세한 사용법은 `{COMMAND_PREFIX}help {cmd_name}`을 참고해주세요."
            )
        except Exception:
            pass
        return

    # 4. CheckFailure: Permission / requirement check failed
    if isinstance(error, commands.CheckFailure):
        try:
            await safe_send_discord_message(
                ctx.channel,
                "⛔ **이 명령어를 실행할 권한이 없거나 실행 조건이 충족되지 않았습니다.**"
            )
        except Exception:
            pass
        return

    # 5. DisabledCommand: Command is disabled
    if isinstance(error, commands.DisabledCommand):
        try:
            await safe_send_discord_message(
                ctx.channel,
                "⚠️ **현재 비활성화된 명령어입니다.**"
            )
        except Exception:
            pass
        return

    # 6. CommandOnCooldown: Rate limit cooldown
    if isinstance(error, commands.CommandOnCooldown):
        try:
            await safe_send_discord_message(
                ctx.channel,
                f"⏳ **명령어 쿨다운 중입니다.** {error.retry_after:.1f}초 후 다시 시도해주세요."
            )
        except Exception:
            pass
        return

    # 7. Cancellation
    if isinstance(error, commands.CommandInvokeError) and isinstance(error.original, (asyncio.CancelledError, KeyboardInterrupt)):
        return

    # 7.5 RecursionError guard - self-healing callback repair
    if isinstance(original_err, RecursionError):
        cmd_name = ctx.command.name if ctx.command else "Unknown"
        logger.error(f"[RecursionError Guard] Infinite recursion detected in command '{cmd_name}'. Repairing callback...")
        if ctx.command and hasattr(ctx.command, "callback"):
            cand = globals().get(f"{cmd_name}_command") or globals().get(cmd_name)
            while hasattr(cand, "callback"):
                if cand is ctx.command:
                    cand = None
                    break
                cand = getattr(cand, "callback", None)
            if callable(cand) and not hasattr(cand, "invoke"):
                ctx.command.callback = cand
                logger.info(f"[RecursionError Guard] Successfully repaired '{cmd_name}' callback.")
                try:
                    await ctx.reinvoke()
                    return
                except Exception as _reinv_err:
                    logger.error(f"Reinvoke after recursion repair failed: {_reinv_err}")

    # 8. Genuine unhandled runtime exception during command execution
    original_err = error.original if isinstance(error, commands.CommandInvokeError) else error
    logger.exception(f"Unhandled command error in {ctx.command}: {original_err}")
    err_trace = "".join(traceback.format_exception(type(original_err), original_err, original_err.__traceback__))

    try:
        await safe_send_discord_message(
            ctx.channel,
            f"⚠️ **명령어 `{ctx.command.name if ctx.command else 'Unknown'}` 처리 중 시스템 오류가 발생했습니다!**\n\n```python\n{str(original_err)[:1000]}\n```"
        )
    except Exception:
        pass

@bot.event
async def on_error(event_method, *args, **kwargs):
    logger.exception(f"Ignoring exception in {event_method}")
    err_trace = traceback.format_exc()
    
    channel = None
    for arg in args:
        if isinstance(arg, discord.Message):
            channel = arg.channel
            break
        elif hasattr(arg, 'channel') and isinstance(arg.channel, discord.abc.Messageable):
            channel = arg.channel
            break
            
    if channel:
        try:
            await safe_send_discord_message(
                channel,
                f"⚠️ **시스템 이벤트(`{event_method}`) 처리 중 오류가 발생했습니다!**\n\n```python\n{err_trace[-1000:]}\n```"
            )
        except Exception:
            pass

async def start_all_bots():
    tasks = [bot.start(DISCORD_BOT_TOKEN)]
    if DISCORD_COMMANDER_BOT_TOKEN:
        try:
            from model_manager import GlobalModelManager as _CmdGMM
            _cmd_disp = _CmdGMM.get_role_model_display("commander")
        except Exception:
            _cmd_disp = getattr(config, "COMMANDER_MODEL", "commander")
        logger.info(f"Starting Commander Bot client ({_cmd_disp})...")
        tasks.append(commander_bot.start(DISCORD_COMMANDER_BOT_TOKEN))
    if DISCORD_QA_BOT_TOKEN:
        logger.info("Starting QA Bot client...")
        tasks.append(qa_bot.start(DISCORD_QA_BOT_TOKEN))
    if DISCORD_AGY_QA2_BOT_TOKEN:
        try:
            from model_manager import GlobalModelManager as _Qa2GMM
            _qa2_disp = _Qa2GMM.get_role_model_display("qa2")
        except Exception:
            _qa2_disp = getattr(config, "QA2_MODEL", "qa2")
        logger.info(f"Starting QA2 Bot client ({_qa2_disp})...")
        tasks.append(qa2_bot.start(DISCORD_AGY_QA2_BOT_TOKEN))
    ext_qa = globals().get("extended_qa_bots", {})
    for ext_cfg in config.get_extended_qa_configs():
        ext_idx = ext_cfg["index"]
        if ext_idx == 2:
            continue
        ext_token = ext_cfg.get("token")
        if ext_token and ext_idx in ext_qa:
            logger.info(f"Starting {ext_cfg['name']} Bot client ({ext_cfg['model']})...")
            async def _safe_bot_run(cli=ext_qa[ext_idx], tok=ext_token, name=ext_cfg['name']):
                try:
                    await cli.start(tok)
                except Exception as err:
                    logger.warning(f"[{name}] Client start notice: {err}")
            tasks.append(_safe_bot_run())
    if DISCORD_AUDITOR_BOT_TOKEN:
        logger.info("Starting Auditor Bot client...")
        tasks.append(auditor_bot.start(DISCORD_AUDITOR_BOT_TOKEN))
    if DISCORD_AGY_IMAGE_BOT_TOKEN:
        try:
            from model_manager import GlobalModelManager as _ImgGMM
            _img_disp = _ImgGMM.get_role_model_display("image")
        except Exception:
            _img_disp = getattr(config, "IMAGE_MODEL", "gemini-3.8-flash-high")
        logger.info(f"Starting Image generator Bot client ({_img_disp})...")
        tasks.append(image_bot.start(DISCORD_AGY_IMAGE_BOT_TOKEN))
    if DISCORD_SERVER_MANAGER_BOT_TOKEN:
        logger.info("Starting Server Manager Bot client...")
        tasks.append(server_manager_bot.start(DISCORD_SERVER_MANAGER_BOT_TOKEN))

    # [🛡️ StallGuard] 채널 오케스트레이션 정지(무응답/대기열 교착) 감시 태스크 상시 가동
    # (관찰·경보 전용 — 강제 회수 없음)
    try:
        from stall_guard import build_guard, WARN_SECONDS, QUEUE_WAIT_SECONDS

        class _LiveSessions:
            """bot 모듈 글로벌을 매 호출마다 재해석 → !reset/핫리로드 후에도 최신 세션을 본다."""

            def get_all_sessions(self):
                return session_manager.get_all_sessions()

        async def _stall_notify(channel_id: int, text: str) -> None:
            try:
                ch = bot.get_channel(channel_id)
                if ch is None:
                    ch = await bot.fetch_channel(channel_id)
                if ch is not None:
                    await safe_send_discord_message(ch, text)
            except Exception as notify_err:
                logger.debug(f"StallGuard 채널 알림 실패(무시): {notify_err}")

        _stall_guard = build_guard(
            _LiveSessions(),
            notify_fn=_stall_notify,
            dispatch_fn=dispatch_next_in_queue,
        )
        tasks.append(_stall_guard.run())
        logger.info(
            f"[StallGuard] 기동(관찰 전용 · 강제 회수 없음): 무응답 경보 {int(WARN_SECONDS)}s / "
            f"대기열 정체 {int(QUEUE_WAIT_SECONDS)}s (증거: reports/watchdog/stalls.json)"
        )
    except Exception as guard_err:
        logger.warning(f"[StallGuard] 기동 실패(봇은 계속 가동): {guard_err}")

    await asyncio.gather(*tasks)

# [QC-P-01] Registered temporary scratch directories for cleanup on shutdown
_REGISTERED_TEMP_DIRS: set = set()

def register_temp_dir(path: str) -> None:
    """Register a temporary directory for automatic cleanup upon shutdown."""
    if path:
        _REGISTERED_TEMP_DIRS.add(os.path.abspath(path))

def unregister_temp_dir(path: str) -> None:
    """Unregister a temporary directory."""
    if path:
        _REGISTERED_TEMP_DIRS.discard(os.path.abspath(path))

def _graceful_shutdown(signum=None, frame=None):
    """[QC-P-01] Process-level graceful shutdown: terminates child processes and cleans up resources."""
    logger.info(f"[Graceful Shutdown] Received signal {signum}, cleaning up running processes...")
    my_pgid = None
    try:
        my_pgid = os.getpgrp()
    except Exception:
        pass

    try:
        # [QA Direct Patch] SessionManager._sessions is an INSTANCE attribute, so class-level
        # access raised AttributeError which was silently swallowed => shutdown was a no-op.
        # Use the live module-level instance (bot.session_manager) + its public accessor.
        _sm = globals().get("session_manager")
        if _sm is not None:
            for s in list(_sm.get_all_sessions().values()):
                proc = getattr(s, "current_process", None)
                if proc and getattr(proc, "pid", None):
                    try:
                        pgid = os.getpgid(proc.pid)
                        if my_pgid is not None and pgid == my_pgid:
                            proc.terminate()
                        else:
                            os.killpg(pgid, signal.SIGTERM)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
    except Exception:
        pass

    # [QC-P-01] Clean up registered temporary scratch directories
    try:
        import shutil
        for tdir in list(_REGISTERED_TEMP_DIRS):
            if os.path.exists(tdir) and os.path.isdir(tdir):
                try:
                    shutil.rmtree(tdir, ignore_errors=True)
                except Exception:
                    pass
        _REGISTERED_TEMP_DIRS.clear()
    except Exception:
        pass

    if signum is not None and signum in (signal.SIGINT, signal.SIGTERM):
        sys.exit(0)

def main():
    if not DISCORD_BOT_TOKEN:
        print("❌ [오류] DISCORD_BOT_TOKEN 이 설정되지 않았습니다.")
        print("'.env' 파일을 만들고 'DISCORD_BOT_TOKEN=your_token_here' 을 입력해주세요.")
        print("또는 '.env.example' 파일을 참고하세요.")
        sys.exit(1)

    # [QC-P-01] Process-level graceful shutdown: register SIGINT/SIGTERM handlers and atexit
    try:
        signal.signal(signal.SIGINT, _graceful_shutdown)
        signal.signal(signal.SIGTERM, _graceful_shutdown)
        atexit.register(_graceful_shutdown)
    except Exception as sig_err:
        logger.debug(f"Signal handler registration note: {sig_err}")

    try:
        asyncio.run(start_all_bots())
    except (KeyboardInterrupt, SystemExit):
        _graceful_shutdown()

if __name__ == "__main__":
    main()
