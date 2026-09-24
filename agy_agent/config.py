import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env file from current directory
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_COMMANDER_BOT_TOKEN = os.getenv("DISCORD_COMMANDER_BOT_TOKEN", "").strip()
DISCORD_QA_BOT_TOKEN = os.getenv("DISCORD_QA_BOT_TOKEN", "").strip()
DISCORD_AGY_QA2_BOT_TOKEN = (
    os.getenv("DISCORD_QA2_BOT_TOKEN")
    or os.getenv("DISCORD_AGY_QA2_BOT_TOKEN")
    or ""
).strip()
DISCORD_AUDITOR_BOT_TOKEN = os.getenv("DISCORD_AUDITOR_BOT_TOKEN", "").strip()
DISCORD_AGY_CONSULTANT_BOT_TOKEN = (
    os.getenv("DISCORD_AGY_CONSULTANT_BOT_TOKEN")
    or os.getenv("DISCORD_CONSULTANT_BOT_TOKEN")
    or ""
).strip()
DISCORD_SERVER_MANAGER_BOT_TOKEN = (
    os.getenv("DISCORD_SERVER_MANAGER_BOT_TOKEN")
    or os.getenv("DISCORD_SERVER_MANAGER_BOT_Token")
    or ""
).strip()
CONSULTANT_MODEL = (
    os.getenv("Consultant_MODEL")
    or os.getenv("CONSULTANT_MODEL")
    or "opencode-go/glm-5.3"
).strip()
CONSULTANT_ENGINE = (
    os.getenv("Consultant_ENGINE")
    or os.getenv("CONSULTANT_ENGINE")
    or "opencode"
).strip()
DEFAULT_WORKSPACE = os.getenv("DEFAULT_WORKSPACE", str(Path.home() / "Desktop" / "autocoding"))
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")
COMMAND_PREFIXES = tuple(p.strip() for p in os.getenv("COMMAND_PREFIXES", f"{COMMAND_PREFIX},~").split(",") if p.strip())
AGY_PATH = os.getenv("AGY_PATH", "agy")
AGY_PRINT_TIMEOUT = os.getenv("AGY_PRINT_TIMEOUT", "30m")
AGY_EFFORT = os.getenv("AGY_EFFORT", "high").strip()
DEFAULT_AI_ENGINE = os.getenv("DEFAULT_AI_ENGINE", "opencode")
DEFAULT_ENGINE = os.getenv("DEFAULT_ENGINE", "opencode").lower().strip()
HERMES_PATH = os.getenv("HERMES_PATH", "/home/jangsung/.local/bin/hermes")
HERMES_MODEL = os.getenv("HERMES_MODEL", "openrouter/meta-llama/llama-3.3-70b-instruct").strip()
HERMES_ENGINE = os.getenv("HERMES_ENGINE", "hermes").strip()
AIDER_PATH = os.getenv("AIDER_PATH", HERMES_PATH)
CODEX_PATH = os.getenv("CODEX_PATH", "/home/jangsung/.npm-global/bin/codex")
COPILOT_PATH = os.getenv("COPILOT_PATH", "/home/jangsung/.npm-global/bin/copilot")
CLAUDE_PATH = os.getenv("CLAUDE_PATH", "claude")
OPENCODE_PATH = os.getenv("OPENCODE_PATH", "opencode")
# OpenCode Go 구독 사용량/한도 조회용 API 키 (미설정 시 로컬 opencode 인증 저장소에서 자동 조회)
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")

def _get_clean_openrouter_key() -> str:
    raw = os.getenv("OPENROUTER_API_KEY", "").strip()
    for prefix in ("(삭제해야정상동작)", '"(삭제해야정상동작)', "'(삭제해야정상동작)"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw.strip('"\'; ')

OPENROUTER_API_KEY = _get_clean_openrouter_key()
QA2_MODEL = os.getenv("QA2_MODEL", "gemini-3.8-flash-high").strip()
raw_qa2_engine = os.getenv("QA2_ENGINE", "hermes").strip()
QA2_ENGINE = "hermes" if raw_qa2_engine in ("openrouter", "hermes") else raw_qa2_engine

def validate_openrouter_setup() -> Tuple[bool, str]:
    """
    Validates whether OpenRouter is properly configured for extended QA reviewers.
    Returns (is_valid, message).
    """
    key = _get_clean_openrouter_key()
    if not key:
        return False, "⚠️ OPENROUTER_API_KEY가 설정되지 않았습니다 (.env에 OPENROUTER_API_KEY 등록 필요)."
    if len(key) < 10 or key.startswith("dummy") or key.startswith("your_"):
        return False, f"⚠️ OPENROUTER_API_KEY 형식이 유효하지 않습니다 (길이: {len(key)})."
    return True, "✅ OpenRouter API 키 설정 완료 (ZDR 강제 활성화)"

def get_extended_qa_configs() -> List[Dict[str, Any]]:
    """
    Scans environment variables or GlobalModelManager for QA{i} (i=2..10) configurations.
    Allows dynamic plug-and-play addition or removal of multiple QA reviewer models
    via OpenRouter (or other supported engines).
    """
    try:
        import sys
        if "model_manager" in sys.modules and not os.environ.get("UNITTEST_RUNNING") and "pytest" not in sys.modules:
            mm = sys.modules["model_manager"]
            return mm.GlobalModelManager.get_active_extended_qas()
    except Exception:
        pass

    configs: List[Dict[str, Any]] = []
    for i in range(2, 11):
        # Default for QA2 is free model, others default to empty
        default_model = "upstage/solar-pro4" if i == 2 else ""
        model = os.getenv(f"QA{i}_MODEL", default_model).strip()
        if not model:
            continue
        # [🛡️ Token Protection Mandate - Native Antigravity Models Protocol]
        # AGY engine is strictly used for Native Antigravity models (Gemini 3.x, Claude Sonnet/Opus 4.6, GPT-OSS).
        # Third-party OpenRouter models (Solar, DeepSeek, Qwen, Llama, Ling, etc.) default to openrouter
        model_lower = model.lower()
        is_agy_native = (
            ("gemini" in model_lower or "claude" in model_lower or "sonnet" in model_lower or "opus" in model_lower or "gpt-oss" in model_lower)
            and not model_lower.startswith("openrouter/")
            and not model_lower.startswith("anthropic/")
        )
        default_engine = "agy" if is_agy_native else "openrouter"
        engine = os.getenv(f"QA{i}_ENGINE", default_engine).strip() or default_engine
        token = (
            os.getenv(f"DISCORD_QA{i}_BOT_TOKEN")
            or os.getenv(f"DISCORD_AGY_QA{i}_BOT_TOKEN")
            or ""
        ).strip()
        raw_name = os.getenv(f"QA{i}_NAME", f"qa{i}").strip() or f"qa{i}"
        m_qa = re.search(r"qa\s*(\d+)", raw_name.lower())
        name = f"qa{m_qa.group(1)}" if m_qa else (f"qa{i}" if raw_name.lower().startswith("qa") else raw_name)
        configs.append({
            "index": i,
            "role": f"qa{i}",
            "name": name,
            "model": model,
            "engine": engine,
            "token": token,
        })
    return configs

EXTENDED_QA_CONFIGS = get_extended_qa_configs()

def parse_qa_selection(
    selection_str: str,
    available_configs: Optional[List[Dict[str, Any]]] = None
) -> Tuple[Optional[List[int]], List[Dict[str, Any]], str]:
    """
    Parses a user specification string to select a subset of configured Extended QAs (QA2..QA10).
    
    Supported inputs:
      - 'all', '전체', 'reset', '초기화', 'none', '기본', '원복', '*' -> (None, all_configs, "전체 확장 QA")
      - Single index/role: '2', 'qa2', 'qa 2', 'qa_2'
      - Model keywords/aliases: 'solar', 'solar-pro', 'deepseek', 'qwen', 'llama', 'ling', 'sonnet'
      - Multiple values (comma/space/slash): '2,3', 'qa2,qa4', '2 4', 'solar, qwen'
    
    Returns:
      (selected_indices, selected_configs, summary_label)
      - selected_indices: List of int (e.g. [2]) or None if all are active.
      - selected_configs: Filtered list of QA config dicts.
      - summary_label: Formatted description of active QA reviewers.
    """
    if available_configs is None:
        available_configs = get_extended_qa_configs()

    clean = (selection_str or "").strip().lower()
    if not clean or clean in ("all", "전체", "reset", "초기화", "none", "기본", "원복", "*"):
        return None, list(available_configs), "전체 확장 QA (기본)"

    tokens = [t.strip() for t in re.split(r"[,/+\s]+", clean) if t.strip()]
    if not tokens:
        return None, list(available_configs), "전체 확장 QA (기본)"

    matched_indices: List[int] = []
    
    for token in tokens:
        if token in ("all", "전체", "reset", "초기화"):
            return None, list(available_configs), "전체 확장 QA (기본)"

        idx = None
        if token.isdigit():
            idx = int(token)
        elif token.startswith("qa") and token[2:].isdigit():
            idx = int(token[2:])
        elif token.startswith("qa-") and token[3:].isdigit():
            idx = int(token[3:])
        elif token.startswith("qa_") and token[3:].isdigit():
            idx = int(token[3:])

        if idx is not None:
            if any(cfg.get("index") == idx for cfg in available_configs):
                if idx not in matched_indices:
                    matched_indices.append(idx)
            continue

        for cfg in available_configs:
            c_idx = cfg.get("index")
            c_model = cfg.get("model", "").lower()
            c_name = cfg.get("name", "").lower()
            if (token in c_model or token in c_name or 
                (token == "solar" and "solar" in c_model) or
                (token == "deepseek" and "deepseek" in c_model) or
                (token == "qwen" and "qwen" in c_model) or
                (token == "llama" and "llama" in c_model) or
                (token == "ling" and "ling" in c_model) or
                (token == "sonnet" and "sonnet" in c_model)):
                if c_idx not in matched_indices:
                    matched_indices.append(c_idx)

    matched_indices.sort()
    if not matched_indices:
        return None, list(available_configs), "전체 확장 QA (매칭 실패)"

    selected_configs = [cfg for cfg in available_configs if cfg.get("index") in matched_indices]
    summary_parts = []
    for cfg in selected_configs:
        c_name = cfg.get("name") or f"qa{cfg.get('index')}"
        m_short = cfg.get("model", "").split("/")[-1]
        summary_parts.append(f"{c_name}({m_short})")
    summary_label = ", ".join(summary_parts)
    return matched_indices, selected_configs, summary_label

# Claude Sonnet 4.6 Guard Rules (Mandate: Difficulty 7.5~10 & Quota >= 10%)
CLAUDE_SONNET_MIN_DIFFICULTY = float(os.getenv("CLAUDE_SONNET_MIN_DIFFICULTY", "7.5"))
CLAUDE_SONNET_MIN_QUOTA_PCT = float(os.getenv("CLAUDE_SONNET_MIN_QUOTA_PCT", "0.10"))

CODER_MODEL = os.getenv("CODER_MODEL", "opencode-go/deepseek-v4.1-flash")
CODER_FALLBACK_MODEL = os.getenv("CODER_FALLBACK_MODEL", "gemini-3.7-flash-high")
COMMANDER_MODEL = os.getenv("COMMANDER_MODEL", "opencode-go/mimo-v2.6-flash")
QA_MODEL = os.getenv("QA_MODEL", "opencode-go/deepseek-v4.1-flash")
QA_FALLBACK_MODEL = os.getenv("QA_FALLBACK_MODEL", "gemini-3.7-flash-high")
QA_BACKUP_MODEL = os.getenv("QA_BACKUP_MODEL", "openrouter/qwen/qwen-2.5-coder-32b-instruct")
QA_BACKUP_MODELS = [
    QA_BACKUP_MODEL,
    QA_FALLBACK_MODEL
]
QA_AUTO_MODEL = os.getenv("QA_AUTO_MODEL", "gemini-3.1-pro-high")
QA_AUTO_FALLBACK_MODEL = os.getenv("QA_AUTO_FALLBACK_MODEL", "gemini-3.7-flash-high")
AUDITOR_MODEL = os.getenv("AUDITOR_MODEL", "opencode-go/deepseek-v4.1-flash")
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.8-flash-high")
CLAUDE_QUOTA_THRESHOLD = float(os.getenv("CLAUDE_QUOTA_THRESHOLD", "0.05"))
PAIR_MAX_ROUNDS = int(os.getenv("PAIR_MAX_ROUNDS", "20"))
QA_MAX_ROUNDS = int(os.getenv("QA_MAX_ROUNDS", "20"))
HOTFIX_MAX_ROUNDS = int(os.getenv("HOTFIX_MAX_ROUNDS", "4"))  # 핫픽스 수렴 상한 (무한 패치 루프 방지)

# ==============================================================================
# [🔋 토큰 절약] 인플라이트 재시도/폴백 예산 (In-flight Retry & Fallback Budget)
# ------------------------------------------------------------------------------
# 기존 구조는 한 스텝이 실패하면 최대 8회까지 풀컨텍스트 재호출을 발생시켰습니다.
#   (503 백오프 3회 → Gemini 폴백 후보 4회 순회)
# 아래 값으로 상한을 두어 재시도 폭주를 원천 차단합니다.
# ==============================================================================
AGY_OVERLOAD_RETRY_DELAYS = [
    int(s) for s in os.getenv("AGY_OVERLOAD_RETRY_DELAYS", "30,90").split(",") if s.strip().isdigit()
] or [30, 90]  # 503/과부하 시 동일 모델 백오프 재시도 (기존 30,60,90 → 30,90)

AGY_FALLBACK_MAX_CANDIDATES = int(os.getenv("AGY_FALLBACK_MAX_CANDIDATES", "1"))  # 모델 폴백 후보 최대 개수 (기존 4)

AGY_RETRY_MAX_ATTEMPTS = int(os.getenv("AGY_RETRY_MAX_ATTEMPTS", "2"))  # 자가복구 재시도 최대 횟수 (세션유지 + 새세션)

# [🛡️ Gemini 한도 보호] 원본 모델이 Gemini 가 아닐 때(예: opencode-go/*, gpt-oss)
# 재시도/폴백 과정에서 Antigravity Gemini 로 넘어갈지 여부. 기본 0(금지).
AGY_ALLOW_GEMINI_FALLBACK = os.getenv("AGY_ALLOW_GEMINI_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")

# 비-Gemini 원본 모델이 소진되었을 때 시도할 대체 모델(쉼표 구분). Gemini 가 아닌 네이티브 모델만 권장.
AGY_NON_GEMINI_FALLBACK_CHAIN = [
    s.strip() for s in os.getenv("AGY_NON_GEMINI_FALLBACK_CHAIN", "gpt-oss-120b-medium").split(",") if s.strip()
]

AUTOPILOT_MAX_CYCLES = int(os.getenv("AUTOPILOT_MAX_CYCLES", "0"))  # Decommissioned legacy compatibility
ENABLE_SELF_HEALING = False  # Decommissioned legacy compatibility
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID", "1538529408703266902"))
NOTIFICATION_CHANNEL_ID = int(os.getenv("NOTIFICATION_CHANNEL_ID", "1538794871437918339"))
QA_HUB_CHANNEL_ID = int(os.getenv("QA_HUB_CHANNEL_ID", "1539993680788586496"))
NANO_BANANA_IMAGE_CHANNEL_ID = int(os.getenv("NANO_BANANA_IMAGE_CHANNEL_ID", "1549422783803691058"))
FINISH_PROJECTS_CATEGORY_ID = int(os.getenv("FINISH_PROJECTS_CATEGORY_ID", "1538712322711752825"))
AUDITOR_MENTION_USER_ID = os.getenv("AUDITOR_MENTION_USER_ID", "1257525029609017367").strip()
AGY_HEADED = os.getenv("AGY_HEADED", "0").lower() in ("1", "true", "yes")
WATCHDOG_TOOL_TIMEOUT = int(os.getenv("WATCHDOG_TOOL_TIMEOUT", "900"))       # 900s (15 min) for deep builds, subagents & thinking tool runs
WATCHDOG_SILENCE_TIMEOUT = int(os.getenv("WATCHDOG_SILENCE_TIMEOUT", "1800")) # 1800s (30 min) for deep thinking models
EXTENDED_QA_TIMEOUT = float(os.getenv("EXTENDED_QA_TIMEOUT", "600.0"))        # 600s (10 min) for extended QA reviewer models
STATE_FILE_PATH = os.getenv(
    "STATE_FILE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "channel_sessions.json")
)

# Preset default workspaces per channel
DEFAULT_CHANNEL_WORKSPACES = {
    1538529408703266902: str(Path.home() / "Desktop" / "autocoding" / "!10_ANY QUESTION"),
    1538695974023864400: str(Path.home() / "Desktop" / "autocoding" / "!10_ANY QUESTION"),
    1539483630089273435: str(Path.home() / "Desktop" / "autocoding" / "!5_agy_agent"),
    1538693952310485062: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "ministry"),
    1539527192663433308: str(Path.home() / "Desktop" / "autocoding" / "!8_idus_phonecase"),
    1539628752730984469: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "rider"),
    1539826011322060841: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "parking_lot"),
    1539993680788586496: str(Path.home() / "Desktop" / "autocoding" / "!9_qa_hub"),
    1540141654193999912: str(Path.home() / "Desktop" / "autocoding" / "!91_trade"),
    1540200001731567626: str(Path.home() / "Desktop" / "autocoding" / "!3_finish_projects" / "CLI_당근마켓 전국"),
    1540262967319334944: str(Path.home() / "Desktop" / "autocoding" / "!92_businesscard"),
    1540925575387025548: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "pdfediter_andriod"),
    1541757846696370206: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "minder-note"),
    1544169135544602704: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "pdfediter_andriod"),
    1544307915295424625: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "fluffy_post_cloud_odyssey_android"),
    1545343935096819752: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "survey_android"),
    1545385594186768404: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "test1234" / "test1"),
    1545385611144466442: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "test1234" / "test2"),
    1545385623118942309: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "test1234" / "test3"),
    1545385634615787601: str(Path.home() / "Desktop" / "autocoding" / "!1_vibecoding" / "test1234" / "test4"),
    1545740990181281865: str(Path.home() / "Desktop" / "autocoding" / "test1234" / "test1"),
    1545741028169097236: str(Path.home() / "Desktop" / "autocoding" / "test1234" / "test2"),
}

# Allow extra mappings via .env if defined
extra_mappings = os.getenv("CHANNEL_WORKSPACES_JSON")
if extra_mappings:
    try:
        parsed = json.loads(extra_mappings)
        for k, v in parsed.items():
            DEFAULT_CHANNEL_WORKSPACES[int(k)] = str(v)
    except Exception:
        pass

# CloudCode Caching Proxy configuration
CLOUD_CODE_PROXY_PORT = int(os.getenv("CLOUD_CODE_PROXY_PORT", "19876"))
CLOUD_CODE_PROXY_HOST = os.getenv("CLOUD_CODE_PROXY_HOST", "127.0.0.1")

def get_cloudcode_proxy_url(port: Optional[int] = None) -> str:
    """Returns formatted proxy URL using configured host and port without inline string formatting."""
    actual_port = port or CLOUD_CODE_PROXY_PORT
    return f"http://{CLOUD_CODE_PROXY_HOST}:{actual_port}"

# Ensure default workspace directories exist
os.makedirs(DEFAULT_WORKSPACE, exist_ok=True)
for path_str in DEFAULT_CHANNEL_WORKSPACES.values():
    try:
        os.makedirs(path_str, exist_ok=True)
    except Exception:
        pass
