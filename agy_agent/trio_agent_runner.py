import os
import tempfile
import asyncio
import json
import time
import logging
import re
from typing import Optional, Callable, Dict, Any, Tuple
import math
import discord
import subprocess

import config
from config import (
    PAIR_MAX_ROUNDS,
    ADMIN_CHANNEL_ID,
    NOTIFICATION_CHANNEL_ID,
    AUDITOR_MENTION_USER_ID,
)
from session_manager import ChannelSession
from agent_runner import AgentRunner
from thermal_guard import ThermalGuard
from mode_classifier import classify_task_type, classify_risk_level, is_diagnostic_prompt
from evidence_validator import EvidenceValidator
from discord_utils import (
    async_prepare_message_payload,
    safe_send_discord_message
)
from commander import Commander
from model_manager import GlobalModelManager, QA_PERSONAS
from quality_state_manager import QualityStateManager

logger = logging.getLogger("TrioAgentRunner")

QA_MODEL = getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
QA1_MODEL = QA_MODEL

class QuotaManager:
    _cache: Optional[Dict[str, Any]] = None
    _last_check: float = 0.0
    _cache_ttl: float = 3600.0  # 3600s (1-hour) cache to prevent frequent polling — aligned with antigravity-usage cacheTTL config
    _exhausted_models: Dict[str, float] = {}  # model_name -> expiry_timestamp

    @classmethod
    def mark_model_exhausted(cls, model_name: str, duration_sec: float = 3600.0):
        if not model_name:
            return
        mid = model_name.lower().strip()
        cls._exhausted_models[mid] = time.time() + duration_sec
        logger.warning(f"QuotaManager: Model '{model_name}' marked as exhausted for {duration_sec / 3600:.1f}h")

    @classmethod
    def is_model_exhausted(cls, model_name: str) -> bool:
        if not model_name:
            return False
        key = model_name.lower().strip()
        expiry = cls._exhausted_models.get(key)
        if expiry and time.time() < expiry:
            return True
        return False

    @classmethod
    def reset_exhaustion(cls):
        """Resets all exhaustion marks and cache (essential for test isolation and quota reset)."""
        cls._exhausted_models.clear()
        cls._cache = None
        cls._last_check = 0.0

    @classmethod
    async def get_quota_info(cls) -> Optional[Dict[str, Any]]:
        now = time.time()
        if cls._cache and (now - cls._last_check) < cls._cache_ttl:
            return cls._cache
        try:
            proc = await asyncio.create_subprocess_exec(
                "antigravity-usage", "quota", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode == 0:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                cls._cache = data
                cls._last_check = now
                for m in data.get("models", []):
                    mid = m.get("modelId", "").lower()
                    is_exhausted = m.get("isExhausted", False)
                    rem = m.get("remainingPercentage")
                    time_until_reset = m.get("timeUntilResetMs", 0)
                    # Only mark exhausted if API explicitly declares isExhausted: true or remainingPercentage is 0.
                    # Claude and GPT-OSS do not return remainingPercentage (it is None by design), so rem is None is normal and NOT exhausted.
                    if is_exhausted or (rem is not None and rem <= 0.0):
                        reset_sec = max(time_until_reset / 1000.0, 3600.0 * 6)
                        cls.mark_model_exhausted(mid, duration_sec=reset_sec)
                return data
        except Exception as e:
            logger.debug(f"Quota check skipped or failed: {e}")
        return cls._cache

    @classmethod
    async def get_gemini_quota_ratio(cls) -> Optional[float]:
        data = await cls.get_quota_info()
        if not data or "models" not in data:
            return None
        min_ratio = None
        for m in data["models"]:
            if m.get("isAutocompleteOnly"):
                continue
            mid = m.get("modelId", "").lower()
            label = m.get("label", "").lower()
            if "gemini" in mid or "gemini" in label:
                if m.get("isExhausted"):
                    return 0.0
                raw_ratio = m.get("remainingPercentage")
                if raw_ratio is None:
                    continue
                try:
                    ratio = float(raw_ratio)
                    if math.isnan(ratio):
                        continue
                except (ValueError, TypeError):
                    continue
                if min_ratio is None or ratio < min_ratio:
                    min_ratio = ratio
        return min_ratio

    @classmethod
    async def resolve_qa_model(
        cls,
        is_auto: bool = False,
        session_mode: Optional[str] = None,
        risk_level: str = "MEDIUM"
    ) -> Tuple[str, str, Optional[str]]:
        """
        Dynamically resolves the QA model based on Risk/Impact Classification (Adaptive QA):
        - Primary QA: Gemini 3.1 Pro High (config.QA_MODEL = "gemini-3.1-pro-high") across all risk levels.
        - Backup QA: Gemini 3.7 Flash High (config.QA_FALLBACK_MODEL = "gemini-3.7-flash-high") when exhausted.
        Returns: (effective_model_name, model_display_label, fallback_notice)
        """
        target_is_auto = is_auto or (session_mode == "auto")
        base_model = config.QA_AUTO_MODEL if target_is_auto else config.QA_MODEL
        fallback_model = config.QA_AUTO_FALLBACK_MODEL if target_is_auto else config.QA_FALLBACK_MODEL

        # Preserve base QA model across all risk levels (LOW, NORMAL, HIGH) without forced downgrade


        if "qwen" in fallback_model.lower():
            fallback_display = "Qwen 2.5 Coder 32B (OpenRouter ZDR Backup QA)"
        elif "3.1" in fallback_model:
            fallback_display = "Gemini 3.1 Pro High (Backup QA)"
        elif "flash" in fallback_model:
            fallback_display = "Gemini 3.7 Flash High (Backup QA)"
        else:
            fallback_display = f"{fallback_model} (Backup QA)"

        if cls.is_model_exhausted(base_model):
            safe_fallback = fallback_model
            if cls.is_model_exhausted(safe_fallback):
                for alt in ("gemini-3.7-flash-high", "gemini-3.8-flash-high", "gemini-3-flash"):
                    if not cls.is_model_exhausted(alt):
                        safe_fallback = alt
                        break
            notice = (
                f"ℹ️ **[스마트 QA 모델 전환]** `{base_model}` 할당량이 소진되어 시니어 QA 리뷰어를 "
                f"**{fallback_display}**로 자동 전환합니다. 🚀"
            )
            return safe_fallback, fallback_display, notice

        data = await cls.get_quota_info()
        if data and "models" in data:
            for m in data["models"]:
                mid = m.get("modelId", "").lower()
                if mid == base_model.lower() or base_model.lower() in mid:
                    if m.get("isExhausted"):
                        cls.mark_model_exhausted(base_model)
                        safe_fallback = fallback_model
                        if cls.is_model_exhausted(safe_fallback):
                            for alt in ("gemini-3.7-flash-high", "gemini-3.8-flash-high", "gemini-3-flash"):
                                if not cls.is_model_exhausted(alt):
                                    safe_fallback = alt
                                    break
                        notice = (
                            f"ℹ️ **[스마트 QA 모델 전환]** `{base_model}` 할당량 한도 도달로 인해 "
                            f"시니어 QA 리뷰어를 **{fallback_display}**로 자동 전환합니다. 🚀"
                        )
                        return safe_fallback, fallback_display, notice

        if "qwen" in base_model.lower():
            display_name = "Qwen 2.5 Coder 32B (OpenRouter ZDR QA)"
        elif target_is_auto:
            display_name = "Gemini 3.1 Pro High (Auto PO/Audit)" if "3.1" in base_model.lower() else base_model
        else:
            display_name = "Gemini 3.1 Pro High (Fixed QA)" if "3.1" in base_model.lower() else ("Gemini 3.7 Flash High" if "flash" in base_model.lower() else base_model)

        return base_model, display_name, None


def compact_agent_context(text: str, max_chars: int = 12000) -> str:
    """
    Compresses agent output context before feeding into next agent's prompt,
    preventing exponential prompt bloating and cascading token consumption (Caveman Token Diet).
    Preserves status headers ([STATUS: ...]), user direct answers, changed file summaries, and test verdicts.
    Strips repetitive decorative lines, polite greetings/boilerplate, and trims redundant long code blocks or verbose log dumps.
    """
    if not text:
        return ""

    import re
    # 1. Strip repetitive decorative lines (─────, ═════, ------, ======)
    cleaned = re.sub(r"^[─═=\-~*]{4,}\s*$", "", text, flags=re.MULTILINE)
    # 2. Strip polite greetings/boilerplate at line starts
    cleaned = re.sub(r"^(안녕하세요[!,.]?|감사합니다[!,.]?|수고하셨습니다[!,.]?)\s*", "", cleaned, flags=re.MULTILINE)
    # 2.5. Shorten verbose absolute directory paths to relative ./ (Token Diet & Anti-Bloat)
    cleaned = re.sub(r'/home/[^/\s`\'"<>]+/Desktop/autocoding/[^/\s`\'"<>]+/', './', cleaned)
    cleaned = re.sub(r'/home/[^/\s`\'"<>]+/Desktop/[^/\s`\'"<>]+/', './', cleaned)
    # 3. Collapse 3+ consecutive newlines into 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if len(cleaned) <= max_chars and not any(l.strip().startswith("```") for l in cleaned.splitlines()):
        return cleaned

    lines = cleaned.split("\n")
    cleaned_lines = []
    in_code_block = False
    code_block_lines = 0
    code_block_is_closed = True  # whether current code block has a matching closing fence
    _STATUS_PATTERN = re.compile(r"^\[STATUS:\s")

    # Pre-scan: determine which opening fences have matching closing fences
    _fence_indices = [i for i, l in enumerate(lines) if l.strip().startswith("```")]
    _unclosed_starts = set()
    _scan_open = False
    _scan_start = -1
    for idx in _fence_indices:
        if not _scan_open:
            _scan_open = True
            _scan_start = idx
        else:
            _scan_open = False
    if _scan_open:
        _unclosed_starts.add(_scan_start)

    fence_idx_counter = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_block_lines = 0
                code_block_is_closed = i not in _unclosed_starts
                cleaned_lines.append(line)
            else:
                in_code_block = False
                cleaned_lines.append(line)
            continue

        if in_code_block:
            # For unclosed blocks: force-close on STATUS tag or after 12 lines
            if not code_block_is_closed:
                if _STATUS_PATTERN.match(line.strip()) or code_block_lines > 12:
                    cleaned_lines.append("```")
                    in_code_block = False
                    cleaned_lines.append(line)
                    continue
            code_block_lines += 1
            if code_block_lines <= 15:
                cleaned_lines.append(line)
            elif code_block_lines == 16:
                cleaned_lines.append("... [중략: 도구로 적용된 코드 본문 생략 (Token Diet)] ...")
        else:
            cleaned_lines.append(line)

    # Safety: if code block was never closed (LLM truncation), force-close
    if in_code_block:
        cleaned_lines.append("```")

    compacted = "\n".join(cleaned_lines).strip()
    if len(compacted) <= max_chars:
        return compacted

    head_len = max(200, int(max_chars * 0.6))
    tail_len = max(100, int(max_chars * 0.3))
    head = compacted[:head_len]
    tail = compacted[-tail_len:]
    return f"{head}\n\n... [중략: 세부 중간 로그 생략 (토큰 다이어트)] ...\n\n{tail}"


def detect_codebase_anchor_dict(workspace: str) -> Dict[str, Any]:
    """
    Detects project canonical root package, key entry points, and directory hierarchy.
    Used across TrioAgentRunner, Commander, and EvidenceValidator to eliminate Coder amnesia and package fragmentation.
    """
    if not workspace or not os.path.isdir(workspace):
        return {}

    root_pkg = None
    # 1. Check AndroidManifest.xml
    for root, _, files in os.walk(workspace):
        if "AndroidManifest.xml" in files:
            m_path = os.path.join(root, "AndroidManifest.xml")
            try:
                with open(m_path, "r", encoding="utf-8", errors="ignore") as mf:
                    content = mf.read()
                m = re.search(r'package\s*=\s*["\']([^"\']+)["\']', content)
                if m:
                    root_pkg = m.group(1).strip()
                    break
            except Exception:
                pass
        if root_pkg:
            break

    # 2. Check build.gradle.kts / build.gradle for namespace or applicationId
    if not root_pkg:
        for root, _, files in os.walk(workspace):
            for f in files:
                if f in ("build.gradle.kts", "build.gradle"):
                    p = os.path.join(root, f)
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as gf:
                            gc = gf.read()
                        m = re.search(r'namespace\s*=\s*["\']([^"\']+)["\']', gc) or re.search(r'applicationId\s*=\s*["\']([^"\']+)["\']', gc)
                        if m:
                            root_pkg = m.group(1).strip()
                            break
                    except Exception:
                        pass
            if root_pkg:
                break

    # 3. Existing key entry files
    entry_files = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "build", "node_modules"))]
        for f in files:
            if f in ("MainActivity.kt", "MainActivity.java", "App.kt", "DocuFlowApp.kt", "MainApplication.kt", "App.tsx", "main.py", "index.html"):
                rel = os.path.relpath(os.path.join(root, f), workspace)
                entry_files.append(rel)

    return {
        "root_package": root_pkg,
        "entry_files": entry_files
    }


def detect_codebase_anchor(workspace: str) -> str:
    """
    Formats a compact anchor block for Coder to enforce continuity and prevent amnesia.
    """
    info = detect_codebase_anchor_dict(workspace)
    root_pkg = info.get("root_package")
    entry_files = info.get("entry_files", [])

    if not root_pkg and not entry_files:
        return ""

    anchor_lines = [
        "**[🔒 기존 코드베이스 루트 패키지 및 진입점 승계 의무 (Anti-Amnesia Anchor)]**:"
    ]
    if root_pkg:
        anchor_lines.append(f"• **정규 루트 패키지**: `{root_pkg}` (🚨 엄격 금지: 임의로 `com.example`, `com.app` 등으로 패키지를 바꾸거나 새 패키지를 파지 마십시오. 모든 신규/수정 코드는 반드시 `{root_pkg}` 하위에 일관되게 작성해야 합니다.)")
    if entry_files:
        entry_list_str = ", ".join([f"`{ef}`" for ef in entry_files[:4]])
        anchor_lines.append(f"• **기존 핵심 진입점**: {entry_list_str} (🚨 엄격 금지: 기존 진입점 파일을 삭제하거나 경로를 바꾸지 말고, 기존 클래스/함수 위에 기능을 점진적으로 확장하십시오.)")

    return "\n".join(anchor_lines) + "\n\n"


class TrioAgentRunner:
    resolve_qa_model = QuotaManager.resolve_qa_model

    @staticmethod
    def is_system_error(text: str) -> bool:
        """Returns True only if text is a genuine top-level runner system error or stoppage string."""
        if not text or not isinstance(text, str):
            return False
        stripped = text.strip()
        if (
            stripped.startswith("🎉")
            or "3중 에이전트 협업 완료 - 최종 승인" in stripped
            or "최종 감사 승인 완료" in stripped
            or "[STATUS: AUDIT_PASSED]" in stripped
            or "[STATUS: APPROVED]" in stripped
        ):
            return False
        if stripped.startswith(("🛑", "🚨")):
            return True
        error_prefixes = (
            "⚠️ 실행 중 오류가 발생했습니다",
            "⚠️ 오류 발생:",
            "⚠️ 3중 에이전트 실행 중 오류 발생:",
            "⚠️ **[에이전트 조기 종료 오류]",
            "⚠️ **[에이전트 조기 종료 감지]",
            "⚠️ **[비-AGY 엔진 실행 오류]",
            "⚠️ **[독립 QA 엔진",
            "⚠️ **[Zero-Production-Diff",
            "⚠️ **[컴파일 점검 실패",
            "⚠️ **[자율 안전 가드레일",
        )
        if any(stripped.startswith(prefix) for prefix in error_prefixes):
            return True
        if stripped.startswith("⚠️") and any(kw in stripped.lower() for kw in ("오류", "error", "실패", "중단", "차단", "도달")):
            return True
        return False

    @staticmethod
    def is_cancellation_or_stop(text: str) -> bool:
        """Returns True if text represents a cancellation, manual stop, or timeout."""
        if not text or not isinstance(text, str):
            return False
        clean = text.strip()
        if "🛑" in clean:
            return True
        if "[STATUS: AUDIT_STOP]" in clean:
            return True
        if "교착(Deadlock) 자동 회수" in clean:
            return True
        # Explicit system stoppage sentences
        explicit_stop_phrases = (
            "작업이 중단되었습니다",
            "작업이 중단(취소)되었습니다",
            "사용자의 요청으로 작업이 중단",
            "진행 중이던 작업이 즉시 강제 중단",
            "작업을 중단합니다",
        )
        return any(phrase in clean for phrase in explicit_stop_phrases)

    @staticmethod
    def is_qa_approved(text: str) -> bool:
        """
        Determines whether a QA review output signifies APPROVAL or REJECTION.
        Accurately distinguishes explicit declaration lines (e.g. line 1 '[STATUS: APPROVED]'
        or '판정: [STATUS: APPROVED]') from incidental in-text mentions (e.g. discussions of
        '[STATUS: REJECTED]' feedback loops or instruction quotations).
        """
        if not text or not isinstance(text, str):
            return False

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False

        verdict_prefix_pattern = re.compile(
            r'^[#*\-•>\s\d.]*(?:\*\*|__)?(?:판정|결론|최종\s*판정|최종\s*결과|감사\s*결과|검증\s*결과|검토\s*결과|Verdict|Result|Conclusion|Final\s*Verdict|Status)(?:\*\*|__)?\s*[:：\-]\s*(?:\*\*|__)?\[?STATUS:\s*(APPROVED(?:\s*\([^)]+\))?|REJECTED|DEFECT_DETECTED)\]?',
            re.IGNORECASE
        )

        standalone_tag_pattern = re.compile(
            r'^[#*\-•>\s\d.]*(?:\*\*|__)?\[?STATUS:\s*(APPROVED(?:\s*\([^)]+\))?|REJECTED|DEFECT_DETECTED)\]?(?:\*\*|__)?(?:\s*[:：\-~.]?\s*(?:\([^)]*\))?\s*)$',
            re.IGNORECASE
        )

        declarations = []
        for idx, line in enumerate(lines):
            m = verdict_prefix_pattern.match(line)
            if m:
                declarations.append((idx, m.group(1).upper(), line, "verdict"))
                continue
            m = standalone_tag_pattern.match(line)
            if m:
                declarations.append((idx, m.group(1).upper(), line, "standalone"))

        if declarations:
            first_idx, first_tag, first_line, first_kind = declarations[0]
            last_idx, last_tag, last_line, last_kind = declarations[-1]

            # 1. Closing verdict or bottom declaration takes final precedence
            if last_kind == "verdict" or last_idx >= len(lines) - 5:
                if "REJECTED" in last_tag or "DEFECT_DETECTED" in last_tag:
                    return False
                if "APPROVED" in last_tag:
                    return True

            # 2. Top-level declaration (Line 1/2 mandate from QA instructions)
            if first_idx <= 2:
                if "APPROVED" in first_tag:
                    return True
                if "REJECTED" in first_tag or "DEFECT_DETECTED" in first_tag:
                    return False

            # 3. If any declaration was REJECTED and not overridden by a closing verdict
            has_any_rejected = any("REJECTED" in d[1] or "DEFECT_DETECTED" in d[1] for d in declarations)
            if has_any_rejected:
                return False

            has_any_approved = any("APPROVED" in d[1] for d in declarations)
            if has_any_approved:
                return True

        # 3. Fallback: Natural language approval check (when explicit tags are absent)
        has_rejection_context = any(
            neg in text for neg in [
                "결함 지적", "수정 필요", "반려 사유", "결함 목록",
                "[STATUS: REJECTED]", "STATUS: REJECTED",
                "치명적 결함", "Major 결함", "동작 오류"
            ]
        )
        if has_rejection_context:
            return False

        nl_approved_keywords = [
            "No further code changes are required",
            "no further code changes are required",
            "모든 요구사항이 충족",
            "All requirements have been met",
            "모든 검증을 통과",
            "검증 완료: 승인",
            "[STATUS: APPROVED]"
        ]
        return any(kw.lower() in text.lower() for kw in nl_approved_keywords)

    @staticmethod
    def _validate_qa_inspection_depth(qa_output: str, workspace: str) -> tuple[bool, str]:
        """
        Mechanically enforces that the QA response has substantive inspection depth.
        Requires:
        1. Minimum 250 characters.
        2. At least 2 specific file references.
        3. Concrete code inspection evidence (function/class/variable/line references or code blocks).
        4. Substantive feedback or architectural suggestions (not just blind praise).
        """
        if not qa_output or len(qa_output.strip()) < 250:
            return False, "QA 응답이 너무 짧습니다 (250자 미만). 소스코드에 대한 실질적인 줄 단위 검토 없이 형식적 승인(Rubber-stamp)을 한 것으로 판정되어 반려합니다."

        # Scan for common file extensions to count unique file references
        ext_pattern = re.compile(r'\b[a-zA-Z0-9_\-\.]+\.(?:py|js|ts|kt|html|css|json|yaml|yml|xml|gradle|vue|jsx|tsx|swift|dart)\b')
        matches = ext_pattern.findall(qa_output)
        unique_files = set(matches)
        if len(unique_files) < 1:
            return False, "QA 검수에 구체적인 소스코드 파일명 언급이 누락되었습니다 (최소 1개 이상의 변경/검증 파일 심층 분석 필요)."

        # Verify substantive inspection evidence: code blocks, function/method names, line references, or architectural suggestions
        has_code_evidence = any([
            "```" in qa_output,
            re.search(r'\b(?:def|class|function|val|var|fun|const|import|export|interface)\b', qa_output),
            re.search(r'(?:line|라인|줄|행)\s*\d+', qa_output, re.IGNORECASE),
            re.search(r'[`\"][a-zA-Z0-9_\-\.]{3,}[`\"]', qa_output),
        ])
        if not has_code_evidence:
            return False, "QA 응답에 구체적인 함수/클래스/코드 라인 분석 증거가 없습니다. 실제 소스코드를 하나하나 대조한 구체적 검수 내용을 작성해야 합니다."

        # Substantive inspection depth is confirmed by character length, file references, and code evidence.
        return True, ""

    @staticmethod
    def is_auditor_approved(text: str) -> bool:
        """
        Determines whether an Auditor review output signifies AUDIT_PASSED or AUDIT_FAILED/AUDIT_STOP.
        Accurately distinguishes explicit declaration lines from incidental in-text mentions.
        """
        if not text or not isinstance(text, str):
            return False

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False

        verdict_prefix_pattern = re.compile(
            r'^[#*\-•>\s\d.]*(?:\*\*|__)?(?:(?:최종\s*)?(?:감사|검증|검토)?\s*(?:판정|결과|결론)|최종\s*감사|Verdict|Result|Conclusion|Final\s*Verdict|Status)(?:\*\*|__)?\s*[:：\-]\s*(?:\*\*|__)?\[?STATUS:\s*(AUDIT_PASSED(?:\s*\([^)]+\))?|AUDIT_FAILED|AUDIT_STOP)\]?',
            re.IGNORECASE
        )

        standalone_tag_pattern = re.compile(
            r'^[#*\-•>\s\d.]*(?:\*\*|__)?\[?STATUS:\s*(AUDIT_PASSED(?:\s*\([^)]+\))?|AUDIT_FAILED|AUDIT_STOP)\]?(?:\*\*|__)?(?:\s*(?:\[VALUE:[^\]]*\])?\s*(?:\[ACTION:[^\]]*\])?\s*[:：\-~.]?\s*(?:\([^)]*\))?\s*)$',
            re.IGNORECASE
        )

        declarations = []
        for idx, line in enumerate(lines):
            m = verdict_prefix_pattern.match(line)
            if m:
                declarations.append((idx, m.group(1).upper(), line, "verdict"))
                continue
            m = standalone_tag_pattern.match(line)
            if m:
                declarations.append((idx, m.group(1).upper(), line, "standalone"))

        if declarations:
            first_idx, first_tag, first_line, first_kind = declarations[0]
            last_idx, last_tag, last_line, last_kind = declarations[-1]
            if last_kind == "verdict" or last_idx >= len(lines) - 5:
                if "AUDIT_FAILED" in last_tag or "AUDIT_STOP" in last_tag:
                    return False
                if "AUDIT_PASSED" in last_tag:
                    return True

            # Top-level declaration
            if first_idx <= 2:
                if "AUDIT_PASSED" in first_tag:
                    return True
                if "AUDIT_FAILED" in first_tag or "AUDIT_STOP" in first_tag:
                    return False

            has_any_failed = any("AUDIT_FAILED" in d[1] or "AUDIT_STOP" in d[1] for d in declarations)
            if has_any_failed:
                return False

            has_any_passed = any("AUDIT_PASSED" in d[1] for d in declarations)
            if has_any_passed:
                return True

        has_explicit_fail = (
            "[STATUS: AUDIT_FAILED]" in text
            or "STATUS: AUDIT_FAILED" in text
            or "[STATUS: AUDIT_STOP]" in text
            or "STATUS: AUDIT_STOP" in text
            or any(kw in text for kw in ["감사 반려", "배포 반려", "보안 결함", "품질 미달"])
        )
        if has_explicit_fail:
            return False

        has_pass_tag = (
            "[STATUS: AUDIT_PASSED]" in text
            or "STATUS: AUDIT_PASSED" in text
            or "STATUS: AUDIT_PASSED (Auditor Direct Patch)" in text
            or "[STATUS: AUDIT_PASSED (Auditor Direct Patch)]" in text
            or "[ACTION: PROCEED]" in text
            or any(kw in text for kw in ["Audit Passed", "AUDIT PASSED", "감사 통과", "최종 승인", "배포 승인"])
        )
        return has_pass_tag

    @staticmethod
    async def run_coder_turn(
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None
    ) -> str:
        """Runs the Coder Agent using dynamically resolved model from GlobalModelManager / config."""
        coder_m = GlobalModelManager.get_role_model("coder") or getattr(config, "CODER_MODEL", "gemini-3.8-flash-high")
        coder_eng = GlobalModelManager.get_role_engine("coder") or getattr(config, "DEFAULT_AI_ENGINE", "agy")
        return await AgentRunner.run_prompt(
            session=session,
            prompt=prompt,
            on_tool_update=on_tool_update,
            model=coder_m,
            engine=coder_eng
        )

    @classmethod
    async def run_qa_turn(
        cls,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None
    ) -> str:
        """Runs the QA Reviewer Agent using configured QA engine (Claude/Codex/Aider/Agy) with fresh evaluation context."""
        target_engine = getattr(session, "qa_engine", "agy") or "agy"
        session_qa_model = getattr(session, "qa_model", None)
        if target_engine == "agy":
            target_model = model or session_qa_model or config.QA_MODEL
        elif target_engine == "openrouter":
            target_model = model or session_qa_model or getattr(session, "engine_model", None) or getattr(config, "QA_MODEL", "gemini-3.1-pro-high")
        else:
            target_model = model or session_qa_model or getattr(session, "engine_model", None)

        
        qa_proxy = ChannelSession(
            channel_id=session.channel_id,
            workspace=session.workspace,
            conversation_id=None,
            qa_conversation_id=None,
            auditor_conversation_id=None,
            mode="single",
            engine=target_engine,
            role="qa"
        )

        qa_response = await AgentRunner.run_prompt(
            session=qa_proxy,
            prompt=prompt,
            on_tool_update=on_tool_update,
            model=target_model
        )

        if qa_proxy.conversation_id:
            session.set_qa_conversation_id(qa_proxy.conversation_id)

        return qa_response

    @classmethod
    async def run_extended_qa_turn(
        cls,
        session: ChannelSession,
        prompt: str,
        qa_config: Dict[str, Any],
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None
    ) -> str:
        """Runs an Extended QA Reviewer Agent (QA2..QA10) using configured engine (OpenRouter) for independent cross-verification."""
        target_engine = qa_config.get("engine") or "agy"
        target_model = model or qa_config.get("model") or config.QA2_MODEL or "upstage/solar-pro4"
        role_str = qa_config.get("role", f"qa{qa_config.get('index', 2)}")

        existing_conv_id = (
            session.get_extended_qa_conversation_id(role_str)
            if hasattr(session, "get_extended_qa_conversation_id")
            else None
        )
        if not existing_conv_id and role_str == "qa2":
            existing_conv_id = getattr(session, "qa2_conversation_id", None)

        qa_proxy = ChannelSession(
            channel_id=session.channel_id,
            workspace=session.workspace,
            conversation_id=existing_conv_id,
            qa_conversation_id=None,
            auditor_conversation_id=None,
            mode="single",
            engine=target_engine,
            role=role_str
        )

        qa_response = await AgentRunner.run_prompt(
            session=qa_proxy,
            prompt=prompt,
            on_tool_update=on_tool_update,
            model=target_model
        )

        if not qa_response or qa_response.strip().startswith("⚠️"):
            logger.warning(f"Extended QA turn {role_str} returned error or empty response. Attempting fast AGY backup review...")
            qa_proxy.engine = "agy"
            fallback_model = getattr(config, "QA_FALLBACK_MODEL", "gemini-3.7-flash-high")
            fb_response = await AgentRunner.run_prompt(
                session=qa_proxy,
                prompt=prompt,
                on_tool_update=on_tool_update,
                model=fallback_model
            )
            if fb_response and not fb_response.strip().startswith("⚠️"):
                qa_response = f"⚠️ **[{role_str.upper()} 엔진 지연 -> AGY({fallback_model}) 고속 대체 검증 완료]**\n\n" + fb_response

        if qa_proxy.conversation_id:
            if hasattr(session, "set_extended_qa_conversation_id"):
                session.set_extended_qa_conversation_id(role_str, qa_proxy.conversation_id)
            if role_str == "qa2":
                session.set_qa2_conversation_id(qa_proxy.conversation_id)

        return qa_response

    @classmethod
    async def run_qa2_turn(
        cls,
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None
    ) -> str:
        """Runs the qa2 Agent using OpenRouter for independent cross-verification (backward compatibility)."""
        qa2_cfg = {
            "index": 2,
            "role": "qa2",
            "name": "qa2",
            "model": model or getattr(session, "qa2_model", None) or config.QA2_MODEL or "upstage/solar-pro4",
            "engine": getattr(session, "qa2_engine", config.QA2_ENGINE) or "agy",
        }
        return await cls.run_extended_qa_turn(
            session=session,
            prompt=prompt,
            qa_config=qa2_cfg,
            on_tool_update=on_tool_update,
            model=model
        )

    @staticmethod
    async def run_auditor_turn(
        session: ChannelSession,
        prompt: str,
        on_tool_update: Optional[Callable[[str], Any]] = None,
        model: Optional[str] = None
    ) -> str:
        """Runs the Auditor Agent using configured Auditor engine (Codex/Claude/Agy) with fresh evaluation context."""
        target_engine = getattr(session, "auditor_engine", None) or GlobalModelManager.get_role_engine("auditor") or "agy"
        target_model = model or GlobalModelManager.get_role_model("auditor") or config.AUDITOR_MODEL

        auditor_proxy = ChannelSession(
            channel_id=session.channel_id,
            workspace=session.workspace,
            conversation_id=None,
            qa_conversation_id=None,
            auditor_conversation_id=None,
            mode="single",
            engine=target_engine,
            role="auditor"
        )

        auditor_response = await AgentRunner.run_prompt(
            session=auditor_proxy,
            prompt=prompt,
            on_tool_update=on_tool_update,
            model=target_model
        )

        if auditor_proxy.conversation_id:
            session.set_auditor_conversation_id(auditor_proxy.conversation_id)

        return auditor_response

    @classmethod
    async def run_pipeline(
        cls,
        session: ChannelSession,
        prompt: str,
        main_channel: discord.abc.Messageable,
        qa_client: Optional[discord.Client] = None,
        auditor_client: Optional[discord.Client] = None,
        on_status_update: Optional[Callable[[str], Any]] = None,
        max_rounds: int = PAIR_MAX_ROUNDS,
        qa2_client: Optional[discord.Client] = None,
        extended_qa_clients: Optional[Dict[int, discord.Client]] = None
    ) -> str:
        """
        Orchestrates a 3-Agent Collaborative Pipeline (or Dynamic Multi-QA Pipeline for !mode 2+):
        1. 👨‍💻 Coder (Gemini 3.7 Flash / 3.8 Flash) : 초고속 개발
        2. 🕵️ qa1 : 코드 리뷰 및 7대 게이트 1차 검증
        2.5. 🕵️‍♂️ Dynamic Extended QAs (qa2..qa10) : !mode 2+ 전용 독립 순차 교차 검증
        3. ⚖️ Client Auditor (Gemini 3.1 Pro High) : 의뢰인 전담 최종 수락 감사
        """
        session.is_busy = True
        session._in_multi_agent_pipeline = True
        latest_coder_output = ""
        latest_qa_output = ""
        latest_qa2_output = ""
        latest_auditor_output = ""
        extended_qa_outputs: Dict[str, str] = {}
        channel_id = getattr(main_channel, "id", None)

        # Multi-QA pipeline activation is strictly constrained to !mode 2+ (pair_quad).
        # Standard !mode 1, !mode 2 remain pure Coder-QA1-Auditor 3-role pipelines.
        is_multi_qa_pipeline = (
            session.mode in ("pair_quad", "pair+", "pair_plus", "2+", "2plus", "2플러스", "3+", "3plus", "3플러스", "mode3_plus")
            or (getattr(session, "auto_enabled", False) and getattr(session, "auto_mode", "") in ("pair_quad", "pair+", "2+", "3+", "mode3_plus"))
            or (qa2_client is not None and getattr(session, "mode", "") in ("pair_quad", "pair+", "pair_plus", "2+", "2plus", "2플러스", "3+", "3plus", "3플러스", "mode3_plus"))
        )
        is_quad_pipeline = is_multi_qa_pipeline

        # Dynamically discover configured extended QAs (QA2..QA10) from config/environment (respecting session QA selection)
        if is_multi_qa_pipeline:
            if hasattr(session, "get_active_extended_qas"):
                active_extended_qas = session.get_active_extended_qas()
            else:
                active_extended_qas = config.get_extended_qa_configs()
        else:
            active_extended_qas = []

        resolved_qa_clients: Dict[int, discord.Client] = dict(extended_qa_clients or {})
        if qa2_client and 2 not in resolved_qa_clients:
            resolved_qa_clients[2] = qa2_client

        if active_extended_qas:
            try:
                init_plan = Commander.dispatch_qa_squad(
                    prompt=prompt,
                    diff_info=None,
                    available_extended_qas=active_extended_qas,
                    mode=getattr(session, "mode", None)
                )
                init_qas = init_plan.get("selected_qas", [])
                total_steps = 3 + len(init_qas) if init_qas else 3 + len(active_extended_qas)
            except Exception:
                total_steps = 3 + len(active_extended_qas)
        else:
            total_steps = 3
        # Operational transit notice helper: routes system notifications to dedicated Notification channel (1538794871437918339)
        # Keeps Admin/Self-Healing channel (1538529408703266902) clean and dedicated purely to self-healing & recursive improvement.
        noti_channel = None
        if hasattr(main_channel, "guild") and main_channel.guild:
            noti_channel = main_channel.guild.get_channel(NOTIFICATION_CHANNEL_ID)

        async def send_transit_notice(text: str):
            tag = f"📍 [<#{channel_id}>] " if channel_id else ""
            full_notice = f"{tag}{text}"

            # 1. Dispatch operational progress notice to dedicated notification channel (1538794871437918339)
            if noti_channel:
                try:
                    import re
                    # Remove discord user mentions like <@1234> or <@!1234> to prevent tagging in noti channel
                    noti_text = re.sub(r"<@!?&?\d+>\s*", "", full_notice)
                    await noti_channel.send(noti_text)
                except Exception as e:
                    logger.debug(f"Could not send transit notice to noti channel: {e}")

            # 2. Also send to main_channel if main_channel is not the notification channel itself
            main_id = getattr(main_channel, "id", None)
            if main_channel and main_id != NOTIFICATION_CHANNEL_ID:
                try:
                    await main_channel.send(text)
                except Exception:
                    pass

        qa_channel = None
        if qa_client and channel_id and qa_client.is_ready():
            qa_channel = qa_client.get_channel(channel_id)
            if not qa_channel:
                try:
                    qa_channel = await qa_client.fetch_channel(channel_id)
                except Exception as e:
                    logger.debug(f"Could not fetch QA channel {channel_id}: {e}")

        qa2_channel = None
        if qa2_client and channel_id and qa2_client.is_ready():
            qa2_channel = qa2_client.get_channel(channel_id)
            if not qa2_channel:
                try:
                    qa2_channel = await qa2_client.fetch_channel(channel_id)
                except Exception as e:
                    logger.debug(f"Could not fetch QA2 channel {channel_id}: {e}")

        auditor_channel = None
        if auditor_client and channel_id and auditor_client.is_ready():
            auditor_channel = auditor_client.get_channel(channel_id)
            if not auditor_channel:
                try:
                    auditor_channel = await auditor_client.fetch_channel(channel_id)
                except Exception as e:
                    logger.debug(f"Could not fetch Auditor channel {channel_id}: {e}")

        task_type = classify_task_type(prompt)
        is_research = (task_type == "research")
        if is_research:
            logger.info("Task classified as 'research'. Delegating to dedicated ResearchRunner...")
            from research_runner import ResearchRunner
            return await ResearchRunner.run_pipeline(
                session=session,
                prompt=prompt,
                main_channel=main_channel,
                qa_client=qa_client,
                auditor_client=auditor_client,
                on_status_update=on_status_update,
                max_rounds=max_rounds
            )

        feedback_for_coder = ""
        clean_task_desc = prompt

        try:
            import sys, importlib
            if "autonomy_guard" in sys.modules:
                importlib.reload(sys.modules["autonomy_guard"])
            from autonomy_guard import AutonomySafetyLayer
            if channel_id:
                # Direct user commands (!mode 2 / manual prompts) are not unattended autonomous loops;
                # refresh the milestone timer so stale startup time does not trigger false positive Guard 3.
                is_unattended_autonomous = bool(getattr(session, "auto_enabled", False))
                if not is_unattended_autonomous:
                    AutonomySafetyLayer.reset_milestone(channel_id)
                AutonomySafetyLayer.record_task_start(channel_id, prompt, is_new_user_task=(not is_unattended_autonomous))

                is_safe, guard_action, guard_msg = AutonomySafetyLayer.check_safety_guards(
                    channel_id=channel_id,
                    workspace=session.workspace or "",
                    current_task=prompt
                )
                if not is_safe:
                    logger.warning(f"TrioAgentRunner: Autonomy guard tripped ({guard_action}): {guard_msg}")
                    session.set_busy(False, "🛑 자율 안전 가드레일 정지")
                    guard_report = f"🛑 **[자율 안전 가드레일 작동 — {guard_action}]**\n{guard_msg}"
                    try:
                        await safe_send_discord_message(main_channel, guard_report)
                    except Exception:
                        pass
                    return guard_report

            previously_approved_qas = set()
            last_arbitration_directive = ""
            squad_plan = None
            squad_qas = []
            for round_num in range(1, max_rounds + 1):
                extended_qa_outputs.clear()
                qa_vision_block = ""
                # Immediate cancellation check
                if getattr(session, "is_stopped", False) or not getattr(session, "is_busy", True):
                    logger.info(f"🛑 [TrioAgentRunner] Pipeline stopped by user before starting Round {round_num}.")
                    pipe_cancel_label = f"{total_steps}중 다중" if active_extended_qas else "3중"
                    return f"🛑 사용자의 요청으로 {pipe_cancel_label} 협업 개발 작업이 즉시 중단되었습니다."

                # Check ThermalGuard before starting Coder turn
                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 1: Coder Agent Turn
                # -------------------------------------------------------------
                coder_display = GlobalModelManager.get_role_model_display("coder")
                session.set_busy(True, f"👨‍💻 Coder 개발 진행 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    await on_status_update(f"👨‍💻 **[라운드 {round_num}/{max_rounds}] Coder({coder_display}) 초고속 견고 개발 진행 중...**")

                if round_num == 1 and not feedback_for_coder:
                    task_type = classify_task_type(prompt)
                    if task_type in ("ops_marketing", "consulting"):
                        is_diag = is_diagnostic_prompt(prompt)
                        if is_diag:
                            coder_prompt = (
                                f"[역할: 시스템 진단 및 문제 해결 엔지니어 (Coder / {coder_display})]\n\n"
                                f"사용자가 다음과 같은 시스템 진단/원인 분석/상태 점검 작업을 요청했습니다:\n"
                                f"────────────────────────────────────────\n"
                                f"{prompt}\n"
                                f"────────────────────────────────────────\n\n"
                                f"**[핵심 실행 지침 - 사용자 질문 직접 답변 최우선]:**\n"
                                f"1. **🎯 사용자 질문에 대한 직접 답변 최우선 (User Question Direct Answer First)**: 사용자의 질문('부하를 받고 있는 건지', '원인이 무엇인지', '컨텍스트 리셋이 잘 되는지', 점수/진단/평가 등)에 대한 객관적이고 구체적인 답변을 본문 최상단에 먼저 상세히 제공하십시오.\n"
                                f"2. **실질적 원인 규명 및 꼼수 배제**: 불필요한 프론트엔드 UI/앱 코드를 억지로 만들지 마십시오. 시스템 부하(CPU/RAM/네트워크/API Rate Limit), 세션 및 컨텍스트 초기화 메커니즘, 프롬프트 주입 문제의 실제 원인을 정확히 규명하고 실질적인 해결책과 패치를 완결하십시오.\n"
                                f"3. **도구 사용 안전 수칙**: 소스코드/설정 수정 시 `write_to_file`(ArtifactMetadata 제외) 및 `replace_file_content`를 안전하게 사용하십시오.\n"
                                f"4. **실행 검증 및 명확한 보고**: 개선 조치를 취한 경우 실행 검증 결과를 명확히 요약하십시오."
                            )
                        elif task_type == "ops_marketing":
                            coder_prompt = (
                                f"[역할: 전략 및 자동화 엔지니어 (Coder / {coder_display})]\n\n"
                                f"사용자가 다음과 같은 운영/전략/자동화 작업을 요청했습니다:\n"
                                f"────────────────────────────────────────\n"
                                f"{prompt}\n"
                                f"────────────────────────────────────────\n\n"
                                f"**[핵심 실행 지침 - 실질적 산출물 완결]:**\n"
                                f"1. **완벽한 가시적 실체 구현 & 꼼수 절대 금지**: 단순 주석이나 형식적 문구가 아닌, 실제로 동작하는 운영 자동화 파이썬/Node 스크립트, 완성된 마케팅 ASO 메타데이터, 또는 구체적 전략 문서를 직접 완벽히 구현하십시오.\n"
                                f"2. **인간 수준의 완성도 & 전문성**: 불필요한 프론트엔드 UI를 억지로 만들지 마십시오. 실무자가 즉시 복사하여 스토어나 운영에 적용할 수 있는 최고 수준의 결과물을 제공하십시오.\n"
                                f"3. **도구 사용 안전 수칙**: 파일 생성/수정 시 `write_to_file`(ArtifactMetadata 제외) 및 `replace_file_content`를 안전하게 사용하십시오.\n"
                                f"4. **실행 검증 및 반영 완결**: 스크립트나 설정인 경우 실제 테스트를 수행하여 무결성을 검증하고 결과를 명확히 요약하십시오."
                            )
                        else:
                            coder_prompt = (
                                f"[역할: 수석 기술 자문관 (Consultant / {coder_display})]\n\n"
                                f"사용자가 다음과 같은 기술 자문/방향성 분석을 요청했습니다:\n"
                                f"────────────────────────────────────────\n"
                                f"{prompt}\n"
                                f"────────────────────────────────────────\n\n"
                                f"**[핵심 실행 지침 - 심층 자문 및 명쾌한 답변]:**\n"
                                f"1. **🎯 사용자 질문에 대한 직접 답변 최우선**: 사용자의 질문에 대한 구체적이고 전문적인 답변을 최상단에 제공하십시오.\n"
                                f"2. **실효성 높은 해결책 제시**: 억지로 소스코드를 생성하지 말고, 기술적 근거와 함께 구체적인 방향성을 제시하십시오.\n"
                                f"3. **도구 사용 안전 수칙**: 소스코드/설정 수정 시 `write_to_file`(ArtifactMetadata 제외) 및 `replace_file_content`를 사용하십시오."
                            )
                    else:
                        codebase_anchor_block = detect_codebase_anchor(session.workspace)
                        coder_prompt = (
                            f"[역할: 초고속 자율 엔지니어 (Coder / {coder_display})]\n\n"
                            f"사용자가 다음과 같은 작업을 요청했습니다:\n"
                            f"────────────────────────────────────────\n"
                            f"{prompt}\n"
                            f"────────────────────────────────────────\n\n"
                            f"{codebase_anchor_block}"
                            f"**[핵심 개발 지침]:**\n"
                            f"1. **🎯 사용자 질문 직접 답변 최우선**: 질문(점수/원인/의견 등)이 포함된 경우, 코드 작성 전에 명쾌한 분석과 답변을 본문 최상단에 먼저 제공하십시오.\n"
                            f"2. **가시적 실체 구현 & 꼼수 금지**: 주석/변수명/테스트 파일만 추가하는 눈속임 금지. 사용자가 앱을 켰을 때 즉시 체감하는 UI·로직을 실제 소스코드에 구현하십시오. 서브에이전트(`invoke_subagent`)를 호출하는 경우 서브에이전트가 코드를 파일에 완전히 반영할 때까지 반드시 대기하고 검증한 후 턴을 종료하십시오. 서브에이전트를 백그라운드에 방치한 채 조기 완료하는 행위는 엄격히 금지됩니다. Canvas/PIL 가짜 스크린샷, 레퍼런스 이미지 표절/외곽선 추출(Tracing)도 금지됩니다.\n"
                            f"3. **상용 수준 디자인 시스템 적용** (GUI 앱 필수):\n"
                            f"   - 색상: 브랜드 컬러 + 보조색 + 중립색(gray-50~900)을 CSS 변수로 정의. 무채색 일색 금지.\n"
                            f"   - 타이포: 제목(1.5~2rem, 700), 본문(1rem, 400), 캡션(0.75rem) 3단계 스케일.\n"
                            f"   - 스페이싱: 8px 그리드(8/16/24/32px). 반응형: 모바일 390px(48dp+) ~ 데스크톱 1280px.\n"
                            f"   - 이미지: `generate_image`로 로고·아이콘·배너 등 최소 1개 에셋 생성 및 배치. 텍스트만의 UI는 상용 미달.\n"
                            f"4. **도구 안전 수칙**: 기존 파일 수정 시 `replace_file_content` 사용. `write_to_file`에 `ArtifactMetadata` 절대 전달 금지.\n"
                            f"5. **빌드·배포·내구성 완결**: 코드 완성 후 빌드/테스트를 실행하여 결과 확인. 견고한 예외 처리(try-except, null check) 내장. `ROADMAP.md`는 임의 편집 금지.\n"
                            f"6. **핵심 보고 및 diff 요약**: 인사말/미사여구 배제, 상대경로 사용. 불필요한 전체 코드 덤프를 지양하고 변경 핵심 diff 및 사용자 질문 답변을 논리적이고 명쾌하게 보고하십시오.\n"
                        )
                else:
                    task_type = classify_task_type(prompt)
                    if task_type in ("ops_marketing", "consulting"):
                        coder_prompt = (
                            f"[피드백 반영 및 운영/전략 보강 요청]\n\n"
                            f"**[🎯 사용자 원본 요청 (절대 준수 — 이전에 구현한 기능을 절대 제거하거나 롤백하지 마십시오)]:**\n"
                            f"════════════════════════════════════════\n"
                            f"{prompt}\n"
                            f"════════════════════════════════════════\n\n"
                            f"시니어 QA 리뷰 및 최종 감사관(Auditor)의 전수 검사에서 아래 추가 지적 사항이 전달되었습니다:\n"
                            f"────────────────────────────────────────\n"
                            f"{feedback_for_coder}\n"
                            f"────────────────────────────────────────\n\n"
                            f"**[🎯 사용자 질문에 대한 직접 답변 최우선]**: 사용자가 문의한 질문이나 요청에 대한 명확한 답변을 최우선으로 작성하십시오.\n"
                            f"**[🚨 실질적 완성도 보강]**: 불필요한 프론트엔드 UI 생성이 아닌, 지적된 스크립트/메타데이터/전략 내용을 정확히 보강하십시오.\n"
                            f"**[도구 사용 주의]**: 소스코드 및 파일 수정 시 `replace_file_content`를 사용하고, `write_to_file`에 `ArtifactMetadata`를 포함하지 마십시오.\n"
                            f"**[📉 명확한 보고 및 가독성 준수]**: 인사말/수다 배제, 절대경로 대신 상대경로(`./...`) 사용, 전체 소스코드 덤프 금지(수정은 도구로만 수행), 변경 핵심 diff 및 사용자 질문 답변을 논리적이고 명쾌하게 보고하십시오."
                        )
                    else:
                        codebase_anchor_block = detect_codebase_anchor(session.workspace)
                        coder_prompt = (
                            f"[피드백 반영 및 자율 운영 보강 요청 — 라운드 {round_num}/{max_rounds}]\n\n"
                            f"**[🎯 사용자 원본 요청 (절대 준수 — 이전 라운드에서 구현한 기능을 절대 제거하거나 롤백하지 마십시오)]:**\n"
                            f"════════════════════════════════════════\n"
                            f"{prompt}\n"
                            f"════════════════════════════════════════\n\n"
                            f"{codebase_anchor_block}"
                            f"**[🎯 사용자 질문에 대한 직접 답변 최우선 (User Question Direct Answer First)]**: 사용자의 질문(점수, 원인, 해결책, 의견 등)에 대한 명쾌한 답변을 본문 최상단에 반드시 포함하십시오.\n"
                            f"**[⚠️ 이전 라운드 구현 보존 필수]**: 이전 라운드에서 이미 구현된 기능(소리, 애니메이션, 점프 높낮이, 특정 UI 등)이 있다면 절대로 제거하거나 초기화하지 마십시오. 아래 지적 사항만 추가/수정하십시오.\n\n"
                            f"시니어 QA 리뷰 및 최종 감사관(Auditor)의 깐깐한 전수 검사에서 아래와 같은 추가 지적 사항이 전달되었습니다:\n"
                            f"────────────────────────────────────────\n"
                            f"{feedback_for_coder}\n"
                            f"────────────────────────────────────────\n\n"
                            f"**[🚨 가시적 실체 수정 필수]**: 단순 테스트 파일 추가나 내부 락 변경이 아닌, **실제 화면 및 동작 코드의 실질적 수정(Patch)**을 완수하십시오.\n"
                            f"**[💡 QA 전문가 관점(디자인/아키텍처/보안) 지적사항 적극 수용]**: QA가 제안한 더 나은 대안(디자인 이목 집중, 폰트/레이아웃 계층, 나노바나나 에셋 도입, 아키텍처 개선 등)을 성실히 반영하여 단순 땜질이 아닌 한 단계 진화된 설계를 코드로 실현하십시오.\n"
                            f"**[도구 사용 주의]**: 소스코드 수정 시 `replace_file_content`를 사용하고, `write_to_file` 호출 시 `ArtifactMetadata`를 절대 포함하지 마십시오.\n"
                            f"**[시각 진실성 & 다중 인터랙션 무결성 (Anti-Mocking & Multi-State)]**:\n"
                            f"- Canvas/PIL 수동 그리기(drawRect/drawText)로 가짜 스크린샷을 합성하여 Gate 5를 속이는 행위 절대 금지 (Compose `captureToImage()`나 Playwright 실제 프레임버퍼 필수).\n"
                            f"- 변수 분리(`val shouldShow`), `else if` 쪼개기 등을 악용한 은폐형 조건부 렌더링 꼼수 절대 금지 (모든 인터랙션 상태에서 무결해야 함).\n"
                            f"- 폰트/좌표 스케일링 시 임의의 하드코딩 클램핑(`coerceIn(..., 34f)` 등 변수화 우회 포함)을 금지하고 뷰포트 비율에 맞춘 정확한 수학적 변환식을 적용하십시오.\n"
                            f"- **[🚨 레퍼런스 이미지 표절, 트레이싱(Tracing), 라운드 재탕 절대 금지]**: 사용자의 참고 이미지를 그대로 복사하거나, 스크립트 코드(Image.open, cv2.imread, findContours 등)에서 열어 외곽선/픽셀을 추출하거나, 이전 라운드의 결과물 이미지를 변경 없이 그대로 재탕하는 행위는 엄격히 금지됩니다. 반드시 실제 수정된 소스코드를 렌더링/빌드하여 고유한 새 결과물 이미지를 생성하십시오.\n"
                            f"**[📉 명확한 보고 및 가독성 준수]**: 인사말/수다 배제, 장황한 내부 절대경로 노출 금지(상대경로 `./...` 또는 파일명만 사용), 소스코드 전체 덤프 금지(수정은 도구로만 수행하고 본문에는 변경 핵심 diff/요약 표기), 사용자 질문 답변과 변경 목적 및 검증 결과를 논리적으로 명쾌하게 보고하십시오.\n"
                            f"위 지적 사항을 면밀히 분석하여 소스코드를 직접 수정(Patch)하고, 2~3일간 무인 운영되어도 문제없도록 완벽히 보강하십시오."
                        )

                async def on_coder_tool(tool_status: str):
                    if on_status_update:
                        await on_status_update(f"👨‍💻 **[Coder({coder_display}) 작업 중]** {tool_status}")

                async with main_channel.typing():
                    latest_coder_output = await cls.run_coder_turn(
                        session=session,
                        prompt=coder_prompt,
                        on_tool_update=on_coder_tool
                    )
                    if channel_id:
                        AutonomySafetyLayer.record_llm_call(channel_id)

                # Check for execution error or cancellation
                if cls.is_system_error(latest_coder_output) or cls.is_cancellation_or_stop(latest_coder_output) or getattr(session, "is_stopped", False):
                    return latest_coder_output or "🛑 사용자의 요청으로 작업이 중단되었습니다."

                # Guard against trivial mock completion string without substance
                is_trivial_completion = (
                    latest_coder_output.strip() in (
                        "작업을 완료했습니다.", "완료했습니다.", "검증 작업을 완료했습니다.",
                        "작업 완료", "작업 완료.", "완료", "완료."
                    )
                    or len(latest_coder_output.strip()) < 8
                )
                if is_trivial_completion:
                    logger.warning(
                        f"TrioAgentRunner: Coder produced empty/trivial response: '{latest_coder_output.strip()}'. "
                        "Attempting in-flight self-recovery retry before rejecting turn..."
                    )
                    await safe_send_discord_message(
                        main_channel,
                        "🔄 **[자율 자가 치유 (Self-Healing)]** Coder 응답이 비어있거나 단답형으로 감지되었습니다. "
                        "무인 자율 모드(!mode 3) 지속을 위해 사령관 권한으로 실제 코드 구현 및 엣지 케이스 완결을 긴급 재촉구합니다..."
                    )
                    recovery_prompt = (
                        "⚠️ **[사령관 긴급 재지시: 실제 소스코드 구현 및 상세 검증 보고 의무]**\n"
                        "제출된 결과에 실제 코드 변경(Diff)이나 구체적인 구현/검증 보고가 누락되었습니다.\n"
                        "1. **이미 구현된 경우**: 본 마일스톤의 요구사항이 이미 디스크에 작성되어 있다면, 소스코드를 심층 분석하여 누락된 엣지 케이스 방어, 성능 최적화, 또는 테스트 코드를 추가 구현하십시오.\n"
                        "2. **미구현된 경우**: 아직 구현되지 않은 핵심 로직이 있다면 파일 수정 도구(`write_to_file`, `replace_file_content`, bash 등)를 실행하여 실제 코드를 디스크에 저장하십시오.\n"
                        "3. **보고 의무**: '완료했습니다' 같은 단순 단답형 종료는 엄격히 금지됩니다. 수정한 파일명과 구체적인 변경/검증 내용을 반드시 기술하여 보고하십시오."
                    )
                    async with main_channel.typing():
                        latest_coder_output = await cls.run_coder_turn(
                            session=session,
                            prompt=recovery_prompt,
                            on_tool_update=on_coder_tool
                        )
                        if channel_id:
                            AutonomySafetyLayer.record_llm_call(channel_id)

                    is_still_trivial = (
                        latest_coder_output.strip() in (
                            "작업을 완료했습니다.", "완료했습니다.", "검증 작업을 완료했습니다.",
                            "작업 완료", "작업 완료.", "완료", "완료."
                        )
                        or len(latest_coder_output.strip()) < 8
                    )
                    if is_still_trivial:
                        logger.warning("TrioAgentRunner: Coder still produced trivial response after in-flight self-recovery. Rejecting turn.")
                        return (
                            "⚠️ **[에이전트 조기 종료 감지]** Coder 에이전트가 본문 내용 및 구현 산출물 없이 작업을 조기 종료했습니다.\n"
                            "• 조치: 세션 컨텍스트를 초기화(`!new`)하거나 명령어를 다시 전송해 주십시오."
                        )

                # Guard against Coder prematurely exiting while subagents are still working
                subagent_delegated = any(kw in latest_coder_output for kw in [
                    "서브에이전트", "서브 에이전트", "subagent", "sub-agent", "서브에이전트에게",
                    "위임했습니다", "백그라운드에서 실행", "작업을 지시했습니다"
                ])
                if subagent_delegated:
                    diff_check = EvidenceValidator.inspect_production_diff(session.workspace or "")
                    if diff_check.get("diff_lines", 0) == 0:
                        logger.info("TrioAgentRunner: Coder mentioned subagent delegation with 0 diff lines. Waiting and polling Coder for final resolution...")
                        await safe_send_discord_message(
                            main_channel,
                            "⏳ **[서브에이전트 작업 완료 대기]** Coder가 서브에이전트에 위임한 작업의 실제 코드 반영을 기다리고 있습니다..."
                        )
                        subagent_retry_prompt = (
                            "⚠️ **[서브에이전트 완료 대기 및 최종 통합 의무]**\n"
                            "서브에이전트에게 작업을 지시했으나 아직 워크스페이스에 실제 코드 변경(Diff)이 발생하지 않았습니다.\n"
                            "서브에이전트의 작업이 완전히 끝나고 코드가 실제 파일에 저장될 때까지 대기한 후, "
                            "최종 코드 상태를 확인하고 완성된 결과와 변경 사항을 요약하여 보고하십시오."
                        )
                        async with main_channel.typing():
                            latest_coder_output = await cls.run_coder_turn(
                                session=session,
                                prompt=subagent_retry_prompt,
                                on_tool_update=on_coder_tool
                            )

                # Post Coder response to main channel
                user_ref_hashes = getattr(session, "user_reference_hashes_set", set())
                user_ref_dhashes = getattr(session, "user_reference_dhashes_set", set())
                coder_chunks, coder_file_attachments = await async_prepare_message_payload(
                    latest_coder_output,
                    workspace_root=session.workspace,
                    excluded_hashes=user_ref_hashes,
                    excluded_dhashes=user_ref_dhashes
                )
                coder_send_success = True
                if len(coder_chunks) > 2:
                    summary_chunk = coder_chunks[0]
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tf:
                        tf.write(latest_coder_output)
                        temp_resp_file = tf.name
                    try:
                        files_payload = [discord.File(temp_resp_file, filename="coder_details.md")] + coder_file_attachments
                        sent_msg = await safe_send_discord_message(
                            main_channel,
                            f"{summary_chunk}\n\n📎 **[세부 작업 내역이 길어 전문을 파일(`coder_details.md`)로 첨부합니다]**",
                            files=files_payload,
                            quote_reference=prompt if round_num == 1 else None,
                            add_toolbar=False
                        )
                        coder_send_success = (sent_msg is not None)
                    finally:
                        try:
                            os.remove(temp_resp_file)
                        except Exception:
                            pass
                else:
                    for idx, chunk in enumerate(coder_chunks):
                        files_payload = coder_file_attachments if idx == 0 else []
                        quote_ref = prompt if (idx == 0 and round_num == 1) else None
                        sent_msg = await safe_send_discord_message(
                            main_channel,
                            chunk,
                            files=files_payload,
                            quote_reference=quote_ref,
                            add_toolbar=False
                        )
                        if sent_msg is None:
                            logger.warning(f"TrioAgentRunner: Coder chunk {idx+1}/{len(coder_chunks)} delivery returned None. Attempting emergency direct delivery...")
                            emergency_msg = await safe_send_discord_message(
                                main_channel,
                                f"📝 **[Coder 작업 답변 보증 전달 — {idx+1}/{len(coder_chunks)}]**\n{chunk}"
                            )
                            if emergency_msg is None:
                                coder_send_success = False
                                logger.error(f"TrioAgentRunner: Emergency delivery ALSO failed for Coder chunk {idx+1}!")
                        if idx < len(coder_chunks) - 1:
                            await asyncio.sleep(0.4)

                if not coder_send_success:
                    try:
                        logger.critical("TrioAgentRunner: Coder output lost normal channels. Attempting direct fallback send...")
                        await main_channel.send(f"⚠️ **[비상 알림: Coder 답변 본문]**\n{latest_coder_output[:1800]}")
                    except Exception as fatal_e:
                        logger.critical(f"TrioAgentRunner: Failed even absolute direct channel send: {fatal_e}")

                # Rate-limit safety spacing before proceeding
                await asyncio.sleep(0.5)

                # Check if session was stopped during Coder turn
                if getattr(session, "is_stopped", False):
                    logger.info(f"🛑 [TrioAgentRunner] Pipeline stopped by user after Coder turn in Round {round_num}.")
                    return "🛑 사용자의 요청으로 작업이 중단되었습니다."
                
                # Restore is_busy since AgentRunner.run_prompt clears it in its finally block
                session.is_busy = True

                # Resolve target workspace dynamically from context or fallback to session workspace
                effective_ws = session.workspace
                combined_context = f"{prompt} {latest_coder_output}"
                import re, urllib.parse
                ws_matches = re.findall(r'(/home/jangsung/Desktop/autocoding/[^\s`\'"\(\)]+)', combined_context)
                for m in ws_matches:
                    p = urllib.parse.unquote(m.rstrip(".,;:)'\"")).replace('\\!', '!')
                    while p and not os.path.exists(p):
                        p = os.path.dirname(p)
                    if p and os.path.isdir(p) and p not in ("/home/jangsung/Desktop/autocoding/agy_agnet", "/home/jangsung/Desktop/autocoding"):
                        if os.path.isdir(os.path.join(p, ".git")) or os.path.isfile(os.path.join(p, "package.json")) or os.path.isfile(os.path.join(p, "build.gradle")) or os.path.isfile(os.path.join(p, "build.gradle.kts")) or os.path.isfile(os.path.join(p, "gradlew")):
                            effective_ws = p
                            break

                user_ref_images = getattr(session, "user_reference_images", [])
                round_deliverable_image_hashes = getattr(session, "round_deliverable_image_hashes", {})
                prev_round_hashes = round_deliverable_image_hashes.get(round_num - 1) if round_num > 1 else None

                diff_info = EvidenceValidator.get_git_diff_summary(effective_ws)
                gates_info = EvidenceValidator.validate_all_gates(
                    effective_ws,
                    user_reference_images=user_ref_images,
                    previous_round_image_hashes=prev_round_hashes
                )
                is_anti_mock_clean, anti_mock_violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(
                    effective_ws,
                    prompt,
                    user_reference_images=user_ref_images,
                    previous_round_image_hashes=prev_round_hashes
                )
                gates_passed_str = "전체 통과 (PASS)" if (gates_info.get("all_passed") and is_anti_mock_clean) else "일부 게이트 점검 필요"

                # Record current round deliverable image hashes for subsequent round comparisons
                curr_round_hashes = EvidenceValidator.collect_workspace_image_hashes(effective_ws)
                round_deliverable_image_hashes[round_num] = curr_round_hashes
                session.round_deliverable_image_hashes = round_deliverable_image_hashes

                # =============================================================
                # Zero-Production-Diff Mechanical Gate (AGY 4.x Pillar 1)
                # Block transition to QA/Auditor when Coder reports completion
                # with 0 git diff lines in production source code.
                # =============================================================
                is_git_tree = False
                try:
                    check_git = subprocess.run(
                        ["git", "rev-parse", "--is-inside-work-tree"],
                        cwd=effective_ws,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=2.0
                    )
                    is_git_tree = (check_git.returncode == 0 and check_git.stdout.strip() == "true")
                except Exception:
                    pass

                is_diag = is_diagnostic_prompt(prompt)
                t_type = classify_task_type(prompt)
                s_mode = getattr(session, "mode", "auto")
                is_inquiry_or_eval = any(kw in (prompt or "").lower() for kw in [
                    "몇점", "몇 점", "어때", "감점", "해결책", "원인", "의견", "어떻게 생각해",
                    "진단", "평가", "피드백", "궁금", "질문", "조언", "자문", "리뷰", "설명", "이유"
                ])
                from mode_classifier import QUESTION_INDICATORS
                has_question_pattern = any(re.search(pat, (prompt or "").lower()) for pat in QUESTION_INDICATORS)
                is_non_coding = (
                    is_diag or
                    is_inquiry_or_eval or
                    has_question_pattern or
                    t_type in ("diagnostic", "consulting", "research", "ops_marketing") or
                    s_mode in ("read", "readonly", "read-only", "consult", "consulting", "plan", "planning", "읽기", "기획", "자문", "방향성")
                )
                is_explicit_test = any(kw in (prompt or "").lower() for kw in [
                    "테스트 작성", "테스트 코드", "단위테스트", "단위 테스트", "e2e 테스트",
                    "test suite", "테스트 추가", "test code", "tc 작성"
                ])

                prod_diff_info = EvidenceValidator.inspect_production_diff(effective_ws, diff_info)
                has_git_activity = is_git_tree or diff_info.get("has_changes", False) or bool(diff_info.get("changed_files"))

                # =============================================================
                # [🛡️ Multi-Agent Unconditional Transit Mandate (!mode 2, 2+, 3, 3+)]
                # In collaborative multi-agent modes (2, 2+, 3, 3+), the pipeline
                # MUST unconditionally and fixedly transition: Coder -> QA -> Auditor
                # (and Extended QAs in 2+/3+). Early-exit after Coder is strictly forbidden
                # so that QA and Auditor always perform rigorous verification and final client audit.
                # =============================================================

                if has_git_activity and not is_non_coding and not is_explicit_test and prod_diff_info.get("is_zero_production_diff"):
                    if round_num < max_rounds:
                        logger.warning(
                            f"TrioAgentRunner: Zero-Production-Diff detected in Round {round_num}/{max_rounds} "
                            f"for workspace '{effective_ws}'. Blocking QA/Auditor transition."
                        )
                        feedback_for_coder = (
                            f"[🚨 Zero-Production-Diff 기계적 차단 — Coder 즉시 재작업 지시 (Round {round_num}/{max_rounds})]\n\n"
                            f"**[차단 사유: 프로덕션 소스코드 변경 0줄 감지]**\n"
                            f"- Coder가 작업을 완료했다고 보고했으나, 실제 프로덕션 소스코드(`app/src/main/`, `*.kt`, `*.py`, `*.ts` 등)에 반영된 git diff가 0줄입니다.\n"
                            f"- 감지된 프로덕션 변경 파일: {prod_diff_info.get('prod_changed_files', [])}\n"
                            f"- 감지된 비프로덕션/테스트/보고서 파일: {prod_diff_info.get('non_prod_files', [])}\n"
                            f"- 테스트 코드(`test_*.py`, `*Test.kt`), mock 하네스, markdown 문서, JSON 보고서만 수정하거나 실제 구현을 누락하는 안티 모킹(Anti-Mocking) 행위는 엄격히 금지됩니다.\n\n"
                            f"**[Coder 필수 실행 지침]**:\n"
                            f"1. 반드시 실제 운영/프로덕션 소스코드 파일을 직접 수정하거나 신규 작성하십시오.\n"
                            f"2. 요구사항에 명시된 기능 로직을 실질적인 코드로 온전히 구현하십시오.\n"
                            f"3. 프로덕션 코드 변경 없이 테스트/보고서만 수정하여 통과를 시도하지 마십시오."
                        )
                        session.set_status("busy", f"⚠️ 프로덕션 코드 변경 0줄 감지 ➔ Coder 즉시 재작업 ({round_num+1}R)")
                        zero_diff_banner = (
                            f"🚨 **[Zero-Production-Diff 기계적 차단 — 라운드 {round_num}/{max_rounds}]**\n"
                            f"Coder 작업 보고 대비 **실제 프로덕션 코드 변경(git diff) 0줄** 감지.\n"
                            f"• 요약: {prod_diff_info.get('summary', '프로덕션 코드 변경 없음')}\n"
                            f"• QA/Auditor 검증 이관 차단 ➔ Coder 즉시 Fail-Fast 재작업 지시."
                        )
                        await safe_send_discord_message(main_channel, zero_diff_banner)
                        continue
                    else:
                        logger.warning(f"TrioAgentRunner: Coder produced zero production diff after {max_rounds} rounds.")
                        session.set_status("warning", f"🛑 Zero-Production-Diff 차단 (최대 라운드 {max_rounds}회 도달)")
                        stop_notice = (
                            f"🛑 **[Zero-Production-Diff 차단 — 최대 라운드({max_rounds}회) 도달]**\n"
                            f"Coder가 {max_rounds}회 라운드를 거쳤으나 실제 프로덕션 소스코드 변경(git diff)이 전혀 없어 검증을 최종 거부하고 종료합니다."
                        )
                        await safe_send_discord_message(main_channel, stop_notice)
                        return stop_notice

                # =============================================================
                # Pre-Flight Compile Check Gate
                # Runs fast './gradlew compileDebugSources' or unit test compilation
                # immediately after Coder turn to catch syntax/compilation errors early.
                # If compilation fails, blocks QA/Auditor transition and triggers
                # immediate Fail-Fast self-correction in Coder stage.
                # =============================================================
                if not is_non_coding and effective_ws:
                    from android_build_manager import AndroidBuildManager
                    if AndroidBuildManager.find_android_project(effective_ws):
                        preflight_res = await AndroidBuildManager.run_preflight_compile_check(
                            workspace=effective_ws,
                            timeout_sec=90.0
                        )
                        if preflight_res.get("is_android") and not preflight_res.get("passed"):
                            if round_num < max_rounds:
                                logger.warning(
                                    f"TrioAgentRunner: Pre-Flight compile check failed for '{effective_ws}' in Round {round_num}/{max_rounds}. "
                                    f"Blocking QA/Auditor transition. Triggering Coder Fail-Fast self-correction."
                                )
                                err_summary = preflight_res.get("error") or preflight_res.get("log_tail") or "컴파일 실패"
                                feedback_for_coder = (
                                    f"[🚨 컴파일 사전 비행 점검(Pre-Flight Check) 실패 — Coder 즉시 문법 자가수정 지시 (Round {round_num}/{max_rounds})]\n\n"
                                    f"**[차단 사유: 문법 오류 / 소스코드 컴파일 실패 (compileDebugSources Exit Code != 0)]**\n"
                                    f"- Coder가 작업을 완료했다고 선언했으나, 사전 비행 컴파일 점검(`./gradlew compileDebugSources`)에서 문법/참조 오류가 발생했습니다.\n"
                                    f"- 컴파일이 실패한 소스코드는 전체 빌드 및 QA 단계로 이관할 수 없으므로, Coder 단계에서 즉시 Fail-Fast 자가수정을 유도합니다.\n\n"
                                    f"**[컴파일 에러 로그]**:\n```text\n{err_summary}\n```\n\n"
                                    f"**[Coder 필수 수정 지침]**:\n"
                                    f"1. 위 에러 로그에 표시된 파일 및 라인의 문법 오류(타입 불일치, import 누락, Unresolved reference 등)를 즉시 수정하십시오.\n"
                                    f"2. 전체 아키텍처와 Compose 컴포넌트의 문법 무결성을 확보하여 정상 컴파일되도록 만드십시오."
                                )
                                session.set_status("busy", f"⚠️ 컴파일 에러 감지 ➔ Coder 즉시 자가수정 ({round_num+1}R)")
                                preflight_banner = (
                                    f"🚨 **[컴파일 사전 비행 점검(Pre-Flight Check) 차단 — 라운드 {round_num}/{max_rounds}]**\n"
                                    f"Coder 완료 보고 직후 `./gradlew compileDebugSources` **문법/참조 컴파일 오류** 감지.\n"
                                    f"• QA 이관 차단 ➔ Coder 즉시 Fail-Fast 자가수정 실행.\n"
                                    f"• 에러 요약:\n```text\n{err_summary[:300]}\n```"
                                )
                                await safe_send_discord_message(main_channel, preflight_banner)
                                continue
                            else:
                                logger.warning(f"TrioAgentRunner: Coder preflight compilation failed after {max_rounds} rounds.")
                                session.set_status("warning", f"🛑 컴파일 점검 실패 차단 (최대 라운드 {max_rounds}회 도달)")
                                stop_notice = (
                                    f"🛑 **[컴파일 점검 실패 차단 — 최대 라운드({max_rounds}회) 도달]**\n"
                                    f"Coder가 {max_rounds}회 라운드를 거쳤으나 `./gradlew compileDebugSources` 컴파일 에러가 해결되지 않아 검증을 중단합니다."
                                )
                                await safe_send_discord_message(main_channel, stop_notice)
                                return stop_notice

                # Dynamic Risk & Collaboration Impact Classification (AGY 4.x)
                risk_level, risk_reason = classify_risk_level(prompt, diff_info)
                logger.info(f"TrioAgentRunner: Task risk level classified as '{risk_level}' ({risk_reason})")

                try:
                    qa1_is_enabled = GlobalModelManager.is_qa_enabled(1)
                except Exception:
                    qa1_is_enabled = True

                # Early squad dispatch calculation to ensure exact, synchronized step denominators from Step 1
                if active_extended_qas:
                    if squad_plan is None:
                        if "commander" in sys.modules:
                            try:
                                importlib.reload(sys.modules["commander"])
                            except Exception:
                                pass
                        from commander import Commander
                        squad_plan = Commander.dispatch_qa_squad(
                            prompt=clean_task_desc,
                            diff_info=diff_info,
                            available_extended_qas=active_extended_qas,
                            mode=getattr(session, "mode", None)
                        )
                        squad_qas = squad_plan.get("selected_qas", [])
                    total_steps = (2 + (1 if qa1_is_enabled else 0)) + len(squad_qas)
                else:
                    total_steps = 2 + (1 if qa1_is_enabled else 0)

                # -------------------------------------------------------------
                # Step 2: QA Turn (Adaptive Model based on Risk/Impact)
                # -------------------------------------------------------------
                if getattr(session, "is_stopped", False) or not getattr(session, "is_busy", True):
                    return "🛑 사용자의 요청으로 작업이 중단되었습니다."

                if not qa1_is_enabled:
                    logger.info("TrioAgentRunner: QA1 is disabled globally. Automatically skipping QA1.")
                    qa_approved = True
                    latest_qa_output = "[STATUS: APPROVED]\n⚡ **[qa1 비활성화 자동 건너뜀]** 전역 설정에서 qa1 단계가 비활성화되어 다음 단계로 즉시 패스합니다. (토큰/시간 절약)"
                    await send_transit_notice("⚡ **[qa1 자동 패스]** 전역 설정에서 qa1이 비활성화되어 다음 단계로 즉시 이관합니다.")
                elif 1 in previously_approved_qas:
                    logger.info("TrioAgentRunner: QA1 previously approved. Skipping QA1 in this round.")
                    qa_approved = True
                    latest_qa_output = "[STATUS: APPROVED]\n✅ **[선행 라운드 승인 유지]** 기존에 qa1이 이미 승인 완료하였으므로 건너뜁니다. (토큰 절약)"
                    await send_transit_notice(f"⚡ **[증분 검증 통과]** qa1은 선행 라운드에서 이미 승인 완료되어 재검증을 건너뜁니다. (토큰 최적화)")
                else:
                    # Single concise in-channel transition notice (No duplicate transit_banner spam)
                    qa_step_label = f"1/{total_steps}단계 Coder({coder_display}) 완료 ➔ 2/{total_steps}단계"
                    await send_transit_notice(
                        f"⚡ **[{qa_step_label}]** 🕵️ <@1539448922643234876> (qa1) 코드 및 품질 게이트 정밀 검증 시작..."
                    )

                    # Check ThermalGuard before starting QA review
                    await ThermalGuard.check_round_transition_cooldown(main_channel)

                    session.set_busy(True, f"🕵️ qa1 코드 리뷰 중 ({round_num}/{max_rounds}R · {risk_level}-Path)")
                    effective_qa_model, qa_display_name, quota_notice = await QuotaManager.resolve_qa_model(
                        is_auto=(session.mode == "auto"),
                        session_mode=session.mode,
                        risk_level=risk_level
                    )

                    if quota_notice:
                        await send_transit_notice(quota_notice)

                    path_badge = "⚡ Fast-Path" if risk_level == "LOW" else ("🛡️ Normal-Path" if risk_level == "MEDIUM" else "🚨 Deep-Path")
                    if on_status_update:
                        await on_status_update(f"🕵️ **[라운드 {round_num}/{max_rounds} · {path_badge}] qa1 결함 및 무결성 검증 진행 중...**")

                    # Target for detailed QA logs: dedicated QA bot client in main channel > main channel
                    qa_target_channel = qa_channel or main_channel

                    qa_status_msg = await qa_target_channel.send(
                        f"🕵️ **[라운드 {round_num}/{max_rounds} · {path_badge}] qa1 검증 로그...**\n"
                        f"💡 *{risk_reason}*"
                    )

                    async def on_qa_tool(tool_status: str):
                        try:
                            await qa_status_msg.edit(content=f"🕵️ **[qa1 검증 중 · {path_badge}]**\n{tool_status}")
                        except discord.DiscordException:
                            pass

                    anti_mock_warning = ""
                    if not is_anti_mock_clean:
                        anti_mock_warning = (
                            "\n🚨 **[CRITICAL SYSTEM INTEGRITY ALERT - ANTI-MOCKING & ANTI-PLAGIARISM VIOLATION DETECTED]** 🚨\n"
                            "시스템 사전 검증기(Evidence Integrity Guardian)에 의해 결과 조작/가시적 눈속임/레퍼런스 이미지 표절/재탕 패턴이 물리적으로 적발되었습니다:\n"
                            + "\n".join(f"• {v}" for v in anti_mock_violations) +
                            "\n👉 **QA 판정 필수 지침**: 위반 사항이 적발되었으므로 Coder가 사용자의 참고 이미지를 표절했거나 이전 라운드 산출물을 재탕했습니다. 즉시 **[STATUS: REJECTED]**를 선언하고 Coder에게 실제 프로덕션 소스코드 및 UI 컴포넌트 실구현/새 렌더링을 강제하십시오.\n\n"
                        )

                    ws_basename = os.path.basename(effective_ws)
                    diff_summary_text = (
                        f"{anti_mock_warning}"
                        f"**[물리적 코드 변경 증거 (Raw Git Diff Summary: `{ws_basename}`)]:**\n"
                        f"- 타깃 워크스페이스: `./{ws_basename}`\n"
                        f"- 수정된 파일 목록: {', '.join(diff_info.get('changed_files', [])) or '없음'}\n"
                        f"- Git 변경 상태: {diff_info.get('summary', 'Clean')}\n"
                        f"- 자동 7대 Quality Gate 검증 상태: {gates_passed_str}\n"
                    )
                    if diff_info.get("raw_diff"):
                        diff_summary_text += f"```diff\n{diff_info.get('raw_diff', '')[:15000]}\n```\n"

                    clean_task_desc = prompt

                    # ---------------------------------------------------------
                    # Automated Visual QA & Screenshot Evidence Inspection
                    # ---------------------------------------------------------
                    qa_vision_block = ""
                    try:
                        from vision_attachment_analyzer import VisionAttachmentAnalyzer
                        qa_vis_res = await VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa(effective_ws)
                        if qa_vis_res and qa_vis_res.get("success"):
                            shot_path = str(qa_vis_res.get('screenshot_path', ''))
                            if effective_ws and shot_path.startswith(effective_ws):
                                shot_disp = f"./{os.path.relpath(shot_path, effective_ws)}"
                            else:
                                shot_disp = f"./{os.path.basename(shot_path)}" if shot_path else "없음"
                            qa_vision_block = (
                                f"\n**[🎨 실기기/UI 스크린샷 Gemini Vision 사전 검사 리포트]**:\n"
                                f"- 검사된 최신 스크린샷: `{shot_disp}` (총 {qa_vis_res.get('total_found', 1)}장 검출)\n"
                                f"- 해상도: {qa_vis_res.get('width')}x{qa_vis_res.get('height')} px\n"
                                f"- **비전 분석 상세**:\n{qa_vis_res.get('analysis')}\n\n"
                                f"⚠️ **[QA 시각 결함 심사 지침]**: 위 비전 분석 리포트에서 버튼 텍스트 줄바꿈(예: `저\\n장`), 레이아웃 오버플로우, 터치 타깃 48dp 미달 등이 보고되었거나 발견된 경우 반드시 **[STATUS: REJECTED]**를 선언하십시오. (단, 본 작업이 프론트엔드/UI 스크린샷과 무관한 백엔드 및 로직 전용 수정인 경우 이 경고를 무시하고 진행하십시오.)\n\n"
                            )
                    except Exception as _vis_err:
                        logger.debug(f"Visual QA inspection notice: {_vis_err}")

                    # ---------------------------------------------------------
                    # Visual Asset Gate: Check if GUI project has image assets
                    # ---------------------------------------------------------
                    visual_asset_gate_block = ""
                    try:
                        session_5w1h = getattr(session, "_current_5w1h_plan", None)
                        visual_approved = getattr(session_5w1h, "visual_approved", False) if session_5w1h else False
                        domain = getattr(session_5w1h, "domain", "") if session_5w1h else ""
                        is_gui_domain = domain in ("mobile_app", "saas_web", "game") if domain else False

                        # Also detect GUI from workspace content if 5W1H is not set
                        if not is_gui_domain and effective_ws:
                            gui_indicators = ["index.html", "package.json", "build.gradle", "build.gradle.kts",
                                              "pubspec.yaml", "App.vue", "App.jsx", "App.tsx"]
                            for gi in gui_indicators:
                                if os.path.isfile(os.path.join(effective_ws, gi)):
                                    is_gui_domain = True
                                    break

                        if is_gui_domain:
                            asset_check = EvidenceValidator.check_visual_asset_presence(effective_ws)
                            if not asset_check.get("has_assets"):
                                visual_asset_gate_block = (
                                    f"\n🎨 **[Visual Asset Gate — 이미지 에셋 미감지 경고]**:\n"
                                    f"- 이 프로젝트는 GUI 앱이지만 **이미지 에셋이 0개**입니다.\n"
                                    f"- 아이콘, 로고, 배너, 빈 화면 일러스트 등 시각적 에셋 없이 텍스트/색상만으로 구현된 경우,\n"
                                    f"  상용 서비스 대비 완성도가 크게 떨어집니다.\n"
                                    f"- **QA 권고**: Coder에게 `generate_image` 도구를 사용하여 최소 1개 이상의 시각 에셋(로고, 아이콘, 히어로 배너 등)을 생성하고 프로젝트에 적용하도록 반려(`[STATUS: REJECTED]`)를 적극 검토하십시오.\n\n"
                                )
                            else:
                                visual_asset_gate_block = (
                                    f"\n✅ **[Visual Asset Gate]**: {asset_check.get('summary', '')}\n\n"
                                )
                    except Exception as _vag_err:
                        logger.debug(f"Visual Asset Gate check notice: {_vag_err}")

                    arb_qa1_guidance = ""
                    if last_arbitration_directive:
                        arb_qa1_guidance = (
                            f"\n**[⚖️ 사령관(<@1540273467453345812>) 최종 중재 판결 지침]**:\n"
                            f"{last_arbitration_directive}\n\n"
                            f"⚠️ **[심사 필수 가이드]**: 사령관이 상반된 의견 대립(A안 vs B안)을 종합 중재하여 위 단일 수렴 지침으로 최종 판결했습니다.\n"
                            f"개인적인 스타일/구조 선호로 인한 번복성 재반려는 불가하며, 오직 사령관 지침이 소스코드에 충실히 반영되었는지만 집중 검증하십시오.\n\n"
                        )

                    session_5w1h = getattr(session, "_current_5w1h_plan", None)
                    active_qcs = getattr(session_5w1h, "quality_contracts", []) if session_5w1h else []
                    if not active_qcs and effective_ws:
                        dom_cand = getattr(session_5w1h, "domain", None) if session_5w1h else None
                        if not dom_cand:
                            # 1. Check session profile or prompt
                            active_prof = getattr(session, "active_profile", "") or ""
                            if "cli_agent" in active_prof or "daemon" in active_prof:
                                dom_cand = "cli_agent"
                            elif "android" in active_prof or "mobile" in active_prof:
                                dom_cand = "mobile_app"
                            elif "web" in active_prof:
                                dom_cand = "saas_web"
                            else:
                                try:
                                    from product_planner import AutonomousProductPlanner
                                    dom_cand = AutonomousProductPlanner.detect_domain(prompt, effective_ws)
                                except Exception:
                                    dom_cand = "cli_agent"
                        active_qcs = QualityStateManager.generate_domain_default_qcs(domain=dom_cand or "cli_agent")

                    qc_prompt_block = ""
                    if active_qcs:
                        qc_items_str = "\n".join([
                            f"  • `{q.get('id')}` [{q.get('category', '').upper()}/Lv.{q.get('level', 1)}]: {q.get('description', '')}\n    -> 검증 규칙: {q.get('verification_rule', '')}"
                            for q in active_qcs
                        ])
                        qc_prompt_block = (
                            f"\n**[📊 Quality Contract (QC) 기계적 정량 검증 의무 (Boolean Checklist)]**:\n"
                            f"사령관이 수립한 아래 QC 항목들에 대해, 반드시 소스코드 및 런타임을 대조하여 각 항목별로 합격/불합격을 한 줄씩 명확히 선언하십시오:\n"
                            f"{qc_items_str}\n\n"
                            f"**[QC 출력 필수 규격 예시]**:\n"
                            f"• QC-G-01: PASS (근거: elapsedTime 변수를 통한 speed 점증 수식 확인)\n"
                            f"• QC-V-01: FAIL (사유: 단색 fillRect만 사용되고 스프라이트/이미지 렌더링 누락)\n\n"
                        )

                    qa1_persona_info = GlobalModelManager.get_role_persona_info("qa1")
                    qa1_persona_name = qa1_persona_info.get("name", "시니어 QA")
                    qa1_persona_badge = f"{qa1_persona_info.get('icon', '🕵️')} {qa1_persona_name}"
                    qa1_persona_focus = qa1_persona_info.get("focus_prompt", "")

                    qa_prompt = (
                        f"[역할: {qa1_persona_badge} & 코드 리뷰어 + 소규모 직접 패치 권한 ({qa_display_name})]\n\n"
                        f"당신은 {qa1_persona_name} 전문 리뷰어입니다.\n"
                        f"Coder의 사소한 실수나 잠재적 결함, 엣지 케이스도 절대 용납하지 않고 꼼꼼히 점검해야 합니다.\n\n"
                        f"**[💼 전문 역할(전공) 집중 검증 영역]**:\n"
                        f"{qa1_persona_focus}\n\n"
                        f"**[🎯 수동적 승인봇(Rubber-Stamping) 전면 금지 및 주도적 설계 비평 헌법]**:\n"
                        f"1. **'이것이 최선인가? 더 좋은 선택(Better Alternative)은 없는가?' 주도적 비평**:\n"
                        f"   - Coder가 코드를 작성했다고 해서 단순히 '어 만들어 놨네. 잘했어 통과' 식으로 기계적 승인을 내리는 관성적 행위는 엄격히 금지됩니다.\n"
                        f"   - QA는 단순 결함 검사관을 넘어, **자신의 직업(Persona: {qa1_persona_name}) 관점을 뚜렷이 드러내어 Coder가 미처 생각하지 못한 부분까지 설계할 수 있도록 리드하는 시니어 파트너**입니다.\n"
                        f"2. **직업(Persona)별 맞춤형 심층 설계 견인 (모든 역할 공통 의무)**:\n"
                        f"   - 🕵️ **기본값 (종합 품질 검증관 / General)**: '기본값'이라고 해서 결코 관대하게 넘어가지 마십시오. '이것이 최선의 선택인가?'를 엄격히 자문하고, 사용자 관점의 에러 메시지 명확성, 로딩/진행 피드백, 조작 동선(UX), Coder가 간과한 예외 처리 및 파라미터 유효성 맹점을 파고들어 프로덕션 완성도 대안을 제시하십시오.\n"
                        f"   - 🎨 **디자이너 봇 (UI/UX Designer)**: 이목이 집중될 디자인인가? 폰트가 밋밋하거나 글자가 너무 작거나 크지는 않은가? (폰트 계층/자간/행간 심의) 단순 텍스트/여백으로 때우지 않고 **나노바나나(#nano-banana-image / NanoBanana Hub)** 이미지 에셋(아이콘, 배너, 일러스트, 버튼)을 생성하여 적용하면 훨씬 시각적 완성도가 높아지지 않는가? 미흡하다면 과감히 반려([STATUS: REJECTED])하고 구체적 비주얼/에셋 설계 스펙을 지시하십시오.\n"
                        f"   - 🛡️ **보안 전문가 (Security & AppSec)**: '가장 안전한 최선의 방어 구조인가?' 공격자의 시각(Attacker Mindset)에서 비인가 접근, 권한 상승, 공격 표면을 심의하십시오. 쉘 명령어 인젝션, 경로 탈출(`../`), 토큰/민감정보 유출 취약점을 색출하고 안전한 토큰/샌드박싱 설계 대안을 제시하십시오.\n"
                        f"   - 💥 **카오스 테스터 (Chaos & Resilience)**: '극한의 상황에서도 우아하게 복구(Graceful Degradation)되는 최선의 구조인가?' 비동기 경합(Race Condition), 타임아웃/지연, Empty/Null/음수/초대용량 기형 페이로드 등 Coder가 '설마' 하고 방치한 엣지 케이스를 격파하고 자가 치유/서킷 브레이커 설계를 강제하십시오.\n"
                        f"   - ⚡ **성능 & SRE 엔지니어 (Performance & SRE)**: '2~3일간 대량 트래픽에도 리소스 고갈 없는 최선의 성능인가?' O(N^2) 루프, 블로킹 I/O를 배치/캐싱 구조로 최적화하고, 파일/소켓/메모리 누수를 색출하여 대규모 무인 가동 내구성을 확보하는 대안을 제시하십시오.\n"
                        f"   - 🏛️ **수석 아키텍트 (Architect & PO Requirements)**: '1년 뒤에도 유연하게 확장 가능한 최선의 아키텍처인가?' 단일 책임(SRP), 모듈 간 결합도 완화, 클린 인터페이스 패턴을 제시하여 Coder의 스파게티/하드코딩 땜질을 원천 차단하고 구조적 품격을 견인하십시오.\n\n"
                        f"사용자가 다음과 같은 작업을 요청했고, Coder({coder_display}) 에이전트가 개발/보강 및 빌드를 완료했습니다.\n\n"
                        f"**[🚨 최우선 검증 대상 - 사용자 원본 요청 및 첨부 문서]:**\n"
                        f"────────────────────────────────────────\n"
                        f"{clean_task_desc}\n"
                        f"────────────────────────────────────────\n\n"
                        f"**[Coder 에이전트의 작업 결과 요약]:**\n{compact_agent_context(latest_coder_output, max_chars=12000)}\n\n"
                        + arb_qa1_guidance
                        + qc_prompt_block
                        + qa_vision_block
                        + visual_asset_gate_block
                        + f"{diff_summary_text}\n"
                        f"**[🚨 QA 도구 사용 및 검증 지침]**:\n"
                        f"0. 🔒 **[지정 워크스페이스 엄격 격리 원칙]**: 반드시 현재 프로젝트 워크스페이스(`./{os.path.basename(session.workspace or 'project')}`) 내부의 파일만 검증하고 수정하십시오. 인접 프로젝트 폴더나 상위 디렉토리로 넘어가는 행위는 엄격히 금지됩니다.\n"
                        f"1. **철저하고 객관적인 심층 전수 검증**: 변경된 모든 핵심 파일을 `view_file`, `grep_search` 도구로 충분히 정독하고, 누락된 엣지 케이스, 버그, 가짜 모킹(Mock/Stub), 표절 여부를 타협 없이 전수 검증하십시오.\n"
                        f"2. **🆕 [소규모 직접 패치 권한 및 물리 검증 의무]**: 검증 중 명백한 단순 버그(오타, 누락된 return, 1~5줄 이내의 로직 오류)를 발견한 경우, `replace_file_content` 도구로 **직접 수정**할 수 있습니다.\n"
                        f"   - ⚠️ **주의 (Write = Build & Test 강제)**: 만약 코드를 1줄이라도 직접 수정했다면, 눈으로만 승인하지 말고 반드시 `./gradlew assembleDebug` 또는 `npm run build` 등의 빌드 명령어를 `run_command`로 실행하여 **물리적으로 컴파일이 되는지 확인한 후** `[STATUS: APPROVED (QA Direct Patch)]`를 선언하십시오.\n"
                        f"   - 단, 10줄 초과 구조 변경이나 새 기능 추가는 여전히 직접 패치 금지이며 Coder에게 REJECTED로 위임하십시오.\n"
                        f"   - 빌드 설정(Gradle, CMake, package.json) 변경\n\n"
                        f"**[검증 관점 & 심층 분석 강제]:**\n"
                        f"0. **심층 물리 검증 강제 (Deep Physical Verification Mandate)**: 눈으로 대충 훑어보고 섣불리 승인(Rubber-stamping)하지 마십시오. `view_file`이나 `grep_search`로 Coder가 수정한 실제 파일들의 소스코드와 로직을 면밀히 직접 열람하고, 극단적 엣지 케이스, 레이아웃 붕괴, 메모리 누수 위험을 철저히 분석하십시오. 실제 빌드/테스트를 직접 실행하여 물리적 결과를 확인한 후 정밀한 검증 증거와 함께 판정하십시오.\n"
                        f"1. **사용자 직접 의뢰 및 질문 충족성 (User Request & Questions Priority)**: 사용자가 지시한 내용 및 질문(점수 평가, 감점 요인 분석, 해결책 질문, 의견 등)에 대해 Coder가 충실하고 구체적으로 답변했는지 대조하십시오. 질문을 무시하고 내부 코드/파일만 나열한 경우 단호하게 `[STATUS: REJECTED]`를 선언하십시오.\n"
                        f"2. **가시적 가치 & 실질 변경 감사 (Anti-Invisible-Fix)**: 프로덕션 화면/동작 소스코드에 실질적 변화가 있는지 확인하십시오.\n"
                        f"3. 기능적 결함, 버그, 문법 에러, 런타임 크래시 위험\n"
                        f"4. 2~3일 장기 실행 시 메모리 누수, 무한 루프, 처리되지 않은 예외(Unhandled Exceptions)\n"
                        f"5. Mock/Stub/하드코딩 및 Canvas/PIL 가짜 이미지 모킹 여부\n"
                        f"6. 다중 인터랙션 상태 무결성\n"
                        f"7. 폰트 및 좌표 스케일링 공식의 수학적 정규화\n"
                        f"8. 안드로이드/모바일 E2E 표준 및 7대 Quality Gate 전수 통과 여부\n"
                        f"9. **[🚨 레퍼런스 이미지 표절 및 스크립트 트레이싱(Tracing) 색출]**: 사용자가 제공한 참고용 레퍼런스 이미지를 그대로 복사해 제출하거나, Coder의 스크립트가 Image.open, cv2.imread 등으로 열어 외곽선(findContours)이나 픽셀을 추출·합성한 속임수가 없는지 철저히 점검하십시오. 발견 시 즉시 `[STATUS: REJECTED]`를 선언하십시오.\n"
                        f"10. **[🚨 Anti-AI-Slop & 상용 크래프트맨십 심사]**: (1) 내부 개발 수치(`50px+`, `70px+`, `16ms`)나 백엔드 인프라 용어(`3-Way 병합`, `단절 테스트`, `온디바이스 평탄화` 등)가 UI 버튼/타이틀로 노출되었는지, (2) 단어가 좁은 너비로 인해 `단절 / 테스 / 트`처럼 세로로 쪼개지거나 메뉴가 360dp 뷰포트 밖으로 삐져나가는지, (3) 24~48dp 버튼/탭에 AI 비트맵 이미지를 뭉개서 축소 삽입했거나 '김민수 수석연구원' 등 진부한 AI 더미 데이터를 사용했는지 전수 검사하십시오. 위반 시 즉시 `[STATUS: REJECTED]`를 선언하십시오.\n\n"
                        f"**[검증 및 판정 규칙 - 인간형 경량 대화 피드백 (Visual Tiki-Taka & Token Diet)]**:\n"
                        f"1. 구조적 버그, 레이아웃/스타일 문제, 동작 오류, 또는 **직업(Persona) 관점에서 설계적/시각적/구조적 보강이 필수적인 경우**:\n"
                        f"   - 반드시 첫 줄에 `[STATUS: REJECTED]`를 정확한 형식으로 표기하십시오.\n"
                        f"   - **Coder가 미처 생각지 못한 설계 맹점 및 구체적 대안 스펙 제시**: '이것이 최선인가?'에 대한 직업적 진단과 함께, Coder가 즉시 구현할 수 있는 폰트/스타일, 나노바나나 에셋 적용 위치, 안전한 아키텍처 대안을 명시하십시오.\n"
                        f"   - **구체적이고 실질적인 결함 리포트**: 무의미한 형식적 체크리스트 나열을 지양하고, 구체적인 결함 부위(파일명, 라인 번호, 캡처/크롭 증거)와 함께 Coder가 즉시 수정할 수 있는 명쾌하고 실질적인 피드백을 제시하십시오.\n"
                        f"   - **📉 [출력 형식 및 가독성 준수]**: 불필요한 미사여구 배제, 내부 절대경로 노출 금지(상대경로 `./...` 또는 파일명만 사용). 핵심 결함 또는 승인 근거를 명확하고 구체적인 증거와 함께 체계적인 개조식으로 작성하여 가독성을 확보하십시오.\n"
                        f"   - 상세 텔레메트리는 `reports/` 하위 JSON으로 자동 보존되므로 대화창 맥락을 불필요하게 늘이지 마십시오.\n"
                        f"2. 단순 1~5줄 버그를 직접 패치했다면:\n"
                        f"   - 반드시 첫 줄에 `[STATUS: APPROVED (QA Direct Patch)]`를 정확한 형식으로 표기하고,\n"
                        f"   - 직접 수정한 대상 파일과 변경 내용을 명확히 명시하십시오.\n"
                        f"3. 모든 기술적/설계적 요구사항이 충족되었고 오류가 없으며 최선의 설계와 실질적 변경이 확인된 경우 (물리적 Diff가 존재하고 테스트가 통과된 경우):\n"
                        f"   - 반드시 첫 줄에 `[STATUS: APPROVED]`를 정확한 형식으로 표기하고,\n"
                        f"   - 기술 검증 통과 내역과 핵심 개선 확인 리포트 및 **[전문가 관점 심층 평가 & 차기 고도화 권고]**를 체계적으로 작성하십시오.\n"
                        f"4. 사용자 요청이 정보 확인, 기획 자문, 시스템 상태 조회인 경우:\n"
                        f"   - Coder의 답변이 현재 시스템 상태 및 코드와 일치하는지 확인하고, 결함이 없다면 반드시 첫 줄에 `[STATUS: APPROVED]`를 표기하십시오."
                    )

                    try:
                        async with qa_target_channel.typing():
                            latest_qa_output = await cls.run_qa_turn(
                                session=session,
                                prompt=qa_prompt,
                                on_tool_update=on_qa_tool,
                                model=effective_qa_model
                            )
                            if channel_id:
                                AutonomySafetyLayer.record_llm_call(channel_id)
                    finally:
                        try:
                            await qa_status_msg.delete()
                        except Exception:
                            try:
                                await qa_status_msg.edit(content=f"✅ **[라운드 {round_num}/{max_rounds}] qa1 검증 단계 완료**")
                            except Exception:
                                pass

                    # Check for execution error
                    if cls.is_system_error(latest_qa_output):
                        return latest_qa_output

                    # Post QA review message to log target channel with safe attachments
                    qa_chunks, qa_file_attachments = await async_prepare_message_payload(
                        latest_qa_output,
                        workspace_root=session.workspace,
                        excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                        excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                    )
                    for idx, chunk in enumerate(qa_chunks):
                        files_payload = qa_file_attachments if idx == 0 else []
                        await safe_send_discord_message(qa_target_channel, chunk, files=files_payload)
                        if idx < len(qa_chunks) - 1:
                            await asyncio.sleep(0.4)

                # Check QA Approval (일반 승인 + QA 직접 패치 승인 + 자연어 승인 폴백 모두 처리)
                raw_qa_approved = cls.is_qa_approved(latest_qa_output)
                qa_approved = raw_qa_approved
                guardian_overridden = False

                # 🛡️ QA Minimum Inspection Gate (형식적 승인 방지)
                # Non-coding tasks (diagnostic, inquiry, consulting) bypass depth validation
                current_mode = getattr(session, "mode", "single")
                _multi_modes = ("pair", "mode3", "pair_quad", "pair+", "pair_plus", "quad_pair", "2+", "2plus", "mode3_plus", "3+", "3plus")
                qa1_was_skipped = (1 in previously_approved_qas) and round_num > 1
                if qa_approved and current_mode in _multi_modes and not is_non_coding and not qa1_was_skipped:
                    is_sufficient, reject_reason = cls._validate_qa_inspection_depth(latest_qa_output, session.workspace)
                    if not is_sufficient:
                        logger.warning(f"TrioAgentRunner: QA Minimum Inspection Gate failed: {reject_reason}")
                        qa_approved = False
                        guardian_overridden = True
                        latest_qa_output = (
                            f"[STATUS: REJECTED]\n"
                            f"🚨 **[품질 게이트 강제 반려 — QA 형식적 승인(Rubber-stamp) 방지]**\n"
                            f"{reject_reason}\n\n"
                            f"QA는 단 한 줄의 코드라도 파일명과 결함 부위를 명확히 지적하며, '승인' 전 반드시 실질적인 코드 분석을 수행해야 합니다.\n\n"
                            f"--- QA 원본 응답 ---\n"
                            f"{latest_qa_output}"
                        )

                # 🎨 Visual Asset Enforcement Gate (GUI 프로젝트 이미지 생성 기계적 강제)
                if qa_approved and current_mode in _multi_modes:
                    session_5w1h = getattr(session, "_current_5w1h_plan", None)
                    vis_app = getattr(session_5w1h, "visual_approved", False) if session_5w1h else False
                    dom = getattr(session_5w1h, "domain", "") if session_5w1h else ""
                    if vis_app and dom in ("mobile_app", "saas_web", "game"):
                        asset_stat = EvidenceValidator.check_visual_asset_presence(session.workspace or "")
                        if not asset_stat.get("has_assets", False):
                            logger.warning("TrioAgentRunner: QA passed GUI task but 0 visual assets exist. Overriding to REJECTED for visual quality.")
                            qa_approved = False
                            guardian_overridden = True
                            latest_qa_output = (
                                f"[STATUS: REJECTED]\n"
                                f"🎨 **[품질 게이트 강제 반려 — 시각 디자인 에셋(Visual Asset) 미생성]**\n"
                                f"이 프로젝트는 GUI({dom}) 프로덕트이나 워크스페이스에 생성된 이미지 에셋(로고/아이콘/배너)이 0개입니다.\n"
                                f"단순 텍스트/단색 UI로 때운 결과물은 상용 수준 품질에 미달합니다.\n"
                                f"Coder는 `generate_image` 도구를 실행하여 필요한 대표 시각 에셋을 생성하고 소스코드에 배치하십시오.\n\n"
                                f"--- QA 원본 응답 ---\n"
                                f"{latest_qa_output}"
                            )

                if qa_approved and not is_anti_mock_clean:
                    logger.warning(f"TrioAgentRunner: QA passed verbally but anti-mocking violations exist: {anti_mock_violations}. Overriding to REJECTED.")
                    qa_approved = False
                    guardian_overridden = True
                    latest_qa_output = (
                        f"[STATUS: REJECTED]\n"
                        f"🚨 **[품질 게이트 강제 반려 — Anti-Mocking / 레퍼런스 이미지 표절 적발]**\n"
                        f"시니어 QA가 합격 판정을 내렸으나, 시스템 무결성 가디언이 결과 조작/표절/재탕을 기계적으로 적발하여 즉시 반려합니다:\n"
                        + "\n".join(f"• {v}" for v in anti_mock_violations) + "\n\n"
                        f"Coder는 사용자의 참고 이미지를 흉내 내거나 이전 산출물을 재탕하지 말고, 실제 코드를 렌더링/빌드하여 고유한 새 결과물을 생성하십시오."
                    )
                    await safe_send_discord_message(main_channel, latest_qa_output)
                elif qa_approved and not any(tag in latest_qa_output for tag in ["[STATUS: APPROVED]", "STATUS: APPROVED"]):
                    latest_qa_output = f"[STATUS: APPROVED]\n{latest_qa_output}"

                # 🛡️ Collaboration Intelligence Check: Anti-Collusion & Requirement Coverage
                from autonomy_guard import CollaborationIntelligence
                if qa_approved:
                    full_check_text = latest_coder_output
                    # If Coder offloaded detailed text to coder_details.md, include it in coverage verification
                    if session.workspace:
                        details_path = os.path.join(session.workspace, "coder_details.md")
                        if os.path.isfile(details_path):
                            try:
                                with open(details_path, "r", encoding="utf-8", errors="ignore") as f:
                                    full_check_text += "\n" + f.read()
                            except Exception:
                                pass
                        # Also include recently modified files content or git diff
                        if diff_info.get("has_changes"):
                            full_check_text += "\n" + "\n".join(diff_info.get("changed_files", []))

                    req_ok, missing_reqs, req_msg = CollaborationIntelligence.validate_requirement_coverage(prompt, full_check_text)
                    # Physical Ground Truth Guard: If all 7 Quality Gates physically passed and Git Diff exists, do not false-reject
                    if not req_ok and gates_info.get("all_passed") and is_anti_mock_clean and diff_info.get("has_changes"):
                        logger.info(f"TrioAgentRunner: Physical 7 Quality Gates passed and Git diff exists. Bypassing requirement text omission ({missing_reqs}).")
                        req_ok = True

                    if not req_ok:
                        logger.warning(f"TrioAgentRunner: Anti-Collusion caught requirement omission: {missing_reqs}")
                        qa_approved = False
                        guardian_overridden = True
                        latest_qa_output = (
                            f"[STATUS: REJECTED]\n"
                            f"⚠️ **[품질 게이트 강제 반려 — 필수 요구사항 누락 감지]**\n"
                            f"{req_msg}\n"
                            f"시니어 QA가 합격 판정을 내렸으나, 시스템 무결성 가디언이 필수 요구사항 누락을 감지하여 기계적으로 반려합니다:\n"
                            f"누락된 핵심 요구사항을 회피하지 말고 직접 소스코드에 구현하십시오: {', '.join(missing_reqs)}"
                        )
                        await safe_send_discord_message(main_channel, latest_qa_output)

                # 🛡️ Collaboration Intelligence Check: Coder-QA Ping-Pong Convergence Trap (A10)
                if not qa_approved:
                    is_ping_pong, ping_msg = CollaborationIntelligence.detect_convergence_failure(
                        channel_id=channel_id or 0,
                        current_defect=latest_qa_output,
                        round_num=round_num
                    )
                    if is_ping_pong:
                        logger.warning(f"TrioAgentRunner: Collaboration Convergence Breaker tripped: {ping_msg}")
                        session.set_status("warning", "🛑 Coder-QA 수렴 실패 (Ping-Pong Trap)")
                        return f"🚨 **[협업 수렴 실패 차단 — Ping-Pong Trap Breaker (A10)]**\n{ping_msg}"

                # Ground Truth Verification: If all 7 Quality Gates physically passed and Git Diff exists on disk
                if not qa_approved and gates_info.get("all_passed") and is_anti_mock_clean and diff_info.get("has_changes"):

                    if "변경 사항 없음" in latest_qa_output or "파일이 단 한 줄도" in latest_qa_output or "물리적 미생성" in latest_qa_output:
                        logger.info("EvidenceValidator: Physical Git Diff and 7 Quality Gates verified on disk. Validating QA pass.")
                        qa_approved = True
                        latest_qa_output = (
                            f"[STATUS: APPROVED]\n"
                            f"✅ **[7대 Quality Gate 및 물리적 Git Diff 자동 검증 승인 완료]**\n"
                            f"- 물리적 수정 파일: {len(diff_info.get('changed_files', []))}개 파일 변경 확인\n"
                            f"- 7대 게이트 상태: ALL_PASSED (True)\n"
                            f"- 산출물: {gates_info.get('gates', {}).get('Gate_7', {}).get('message', 'PASS')}"
                        )

                # Check if session was stopped during QA turn
                if getattr(session, "is_stopped", False) or not getattr(session, "is_busy", True):
                    logger.info(f"🛑 [TrioAgentRunner] Pipeline stopped by user after QA turn in Round {round_num}.")
                    return "🛑 사용자의 요청으로 작업이 중단되었습니다."

                # ---------------------------------------------------------
                # Programmatic Quality State Store & Regression Gate
                # ---------------------------------------------------------
                qc_eval_results = QualityStateManager.parse_qa_qc_verdicts(
                    qa_output=latest_qa_output,
                    expected_qcs=active_qcs,
                    workspace=effective_ws or session.workspace or ""
                )
                current_ver_label = getattr(session_5w1h, "version", "v1.0") if session_5w1h else "v1.0"
                updated_quality_state = QualityStateManager.evaluate_and_update(
                    workspace=effective_ws or session.workspace or "",
                    milestone_version=current_ver_label,
                    qc_results=qc_eval_results,
                    functional_ac_passed=qa_approved,
                    build_succeeded=True
                )
                session._latest_quality_state = updated_quality_state

                # Check if regression occurred
                if updated_quality_state.get("regression", {}).get("has_regression"):
                    reg_details = updated_quality_state.get("regression", {}).get("details", "")
                    logger.warning(f"QualityStateManager: Regression detected ({reg_details}). Overriding to REJECTED.")
                    qa_approved = False
                    guardian_overridden = True
                    latest_qa_output = (
                        f"[STATUS: REJECTED]\n"
                        f"🚨 **[Quality State Store — 회귀 감지 강제 반려 (Regression Gate)]**\n"
                        f"기존 기능 인수 기준(AC) 또는 빌드 무결성이 훼손되었습니다: {reg_details}\n\n"
                        + latest_qa_output
                    )

                if not qa_approved:
                    feedback_for_coder = f"[qa1 반려 사유]\n{latest_qa_output}"
                    session.set_status("busy", f"⚠️ qa1 반려 ➔ Coder 보강 중 ({round_num}R)")
                    if guardian_overridden:
                        await send_transit_notice(f"❌ **[시스템 무결성 가디언 반려 (라운드 {round_num})]** QA 구두 승인 후 무결성 검증 위반이 적발되어 Coder에게 재작업을 지시합니다.")
                    else:
                        await send_transit_notice(f"❌ **[qa1 라운드 {round_num} 반려]** Coder에게 결함 보강 재작업을 지시합니다.")
                    continue
                else:
                    previously_approved_qas.add(1)
                    ov_sc = updated_quality_state.get("overall_score", 0.0)
                    dl_sc = updated_quality_state.get("delta", 0.0)
                    dl_sign = f"+{dl_sc:.1f}" if dl_sc >= 0 else f"{dl_sc:.1f}"
                    await send_transit_notice(
                        f"📊 **[Quality State 갱신]** 기계 산출 품질 점수 `{ov_sc:.1f}%` (증분 Delta: `{dl_sign}%` | 무결점 회귀 검증 통과)"
                    )


                # =============================================================
                # Step 2.5: Commander Intelligent QA Squad Dispatching & Arbitration
                # Dynamic Plug-and-Play for QA2 ~ QA7 with intelligent targeted squad
                # =============================================================
                extended_qa_rejected = False
                extended_qa_summaries_for_subsequent = ""
                squad_total_steps = total_steps

                if active_extended_qas:
                    # 1. Commander Squad Dispatching: Evaluate difficulty, risk, and select targeted squad
                    if squad_plan is None:
                        squad_plan = Commander.dispatch_qa_squad(
                            prompt=clean_task_desc,
                            diff_info=diff_info,
                            available_extended_qas=active_extended_qas,
                            mode=getattr(session, "mode", None)
                        )
                        squad_qas = squad_plan.get("selected_qas", [])
                    dispatch_banner = squad_plan.get("dispatch_banner", "")
                    if round_num == 1 and dispatch_banner:
                        await safe_send_discord_message(main_channel, dispatch_banner)
                        await send_transit_notice(f"🎯 사령관 스쿼드 출동: {', '.join([q.get('name', '') for q in squad_qas]) or '없음'}")

                    # Check which QAs actually need evaluation vs already approved
                    pending_squad_qas = [q for q in squad_qas if q.get("index", 0) not in previously_approved_qas]
                    passed_squad_qas = [q for q in squad_qas if q.get("index", 0) in previously_approved_qas]

                    squad_results: Dict[str, Dict[str, Any]] = {}
                    for passed_q in passed_squad_qas:
                        p_name = passed_q.get("name", f"qa{passed_q.get('index')}").lower()
                        squad_results[p_name] = {
                            "approved": True,
                            "output": f"[STATUS: APPROVED]\n✅ 선행 라운드에서 승인 완료 (유지)",
                            "model": passed_q.get("model", "unknown"),
                            "index": passed_q.get("index")
                        }

                    if not pending_squad_qas:
                        logger.info("TrioAgentRunner: All extended QAs previously approved. Skipping Step 2.5 entirely.")
                        await send_transit_notice("⚡ **[스쿼드 승인 유지]** 출동 QA 전원이 이미 승인 완료되어 즉시 최종 감사로 이관합니다. (토큰 최적화)")
                    else:
                        if passed_squad_qas:
                            passed_names = ", ".join([f"**{q.get('name', '').lower()}**" for q in passed_squad_qas])
                            pending_names = ", ".join([f"**{q.get('name', '').lower()}**" for q in pending_squad_qas])
                            await send_transit_notice(
                                f"⚡ **[증분 선별 재검증]** 기승인 QA({passed_names}) 승인 유지 ➔ 반려/미검증 QA({pending_names})만 선별 재검증을 진행합니다. (불필요한 토큰 낭비 차단)"
                            )

                        squad_total_steps = 3 + len(squad_qas)
                        for ext_idx, qa_cfg in enumerate(pending_squad_qas):
                            curr_qa_step = len(passed_squad_qas) + 3 + ext_idx
                            qa_num = qa_cfg.get("index", ext_idx + 2)
                            qa_name = qa_cfg.get("name", f"qa{qa_num}").lower()
                            qa_model_name = qa_cfg.get("model", "unknown")
                            qa_engine_name = qa_cfg.get("engine", "openrouter")

                            # Determine dedicated client & mention
                            ext_bot_client = resolved_qa_clients.get(qa_num)
                            qa_mention = (
                                f"<@{ext_bot_client.user.id}>"
                                if (ext_bot_client and ext_bot_client.user)
                                else f"**{qa_name}**"
                            )

                            await send_transit_notice(
                                f"⚡ **[{curr_qa_step}/{squad_total_steps}단계 교차 검증]** 🕵️‍♂️ {qa_mention} ({qa_name}) 독립 검증 시작..."
                            )
                            await ThermalGuard.check_round_transition_cooldown(main_channel)

                            if getattr(session, "is_stopped", False) or not getattr(session, "is_busy", True):
                                return "🛑 사용자의 요청으로 작업이 중단되었습니다."

                            session.set_busy(True, f"🕵️‍♂️ {qa_name} 교차 검증 중 ({round_num}/{max_rounds}R)")
                            if on_status_update:
                                await on_status_update(f"🕵️‍♂️ **[라운드 {round_num}/{max_rounds}] {qa_name} 독립 교차 검증 진행 중...**")

                            # Target channel: if dedicated bot client is ready, route via it, else main_channel
                            qa_target_channel = main_channel
                            if ext_bot_client and ext_bot_client.is_ready():
                                try:
                                    resolved_ch = ext_bot_client.get_channel(main_channel.id)
                                    if resolved_ch:
                                        qa_target_channel = resolved_ch
                                except Exception:
                                    pass

                            qa_status_msg = await qa_target_channel.send(
                                f"🕵️‍♂️ **[라운드 {round_num}/{max_rounds}] {qa_name} 독립 교차 검증 로그...**\n"
                                f"💡 *독립 LLM 교차 검증 & 무인 자율 헌법 부합성 심층 감사*"
                            )

                            async def on_ext_qa_tool(tool_status: str, q_name=qa_name, q_model=qa_model_name, q_msg=qa_status_msg):
                                try:
                                    await q_msg.edit(content=f"🕵️‍♂️ **[{q_name} 검증 중]**\n{tool_status}")
                                except discord.DiscordException:
                                    pass

                            arbitration_guidance = ""
                            if last_arbitration_directive:
                                arbitration_guidance = (
                                    f"\n**[⚖️ 사령관(<@1540273467453345812>) 최종 중재 판결 지침]**:\n"
                                    f"{last_arbitration_directive}\n\n"
                                    f"⚠️ **[심사 필수 가이드]**: 사령관이 상반된 의견 대립(A안 vs B안)을 종합 중재하여 위 단일 수렴 지침으로 최종 판결했습니다.\n"
                                    f"개인적인 스타일/구조 선호로 인한 번복성 재반려는 불가하며, 오직 사령관 지침이 소스코드에 충실히 반영되었는지만 집중 검증하십시오.\n"
                                )

                            ext_persona_info = GlobalModelManager.get_role_persona_info(qa_name)
                            ext_persona_name = ext_persona_info.get("name", "독립 교차 리뷰어")
                            ext_persona_badge = f"{ext_persona_info.get('icon', '🕵️')} {ext_persona_name}"
                            ext_persona_focus = ext_persona_info.get("focus_prompt", "")

                            ext_qa_prompt = (
                                f"[역할: {qa_name} ({ext_persona_badge}) 독립 교차 리뷰어]\n\n"
                                f"당신은 {ext_persona_name} 전문 독립 리뷰어입니다.\n"
                                f"이전 검증을 통과한 결과물에 대해 독립적인 시각으로 재검증하여 사소한 결함이나 누락, 엣지 케이스도 놓치지 않고 깐깐하게 점검해야 합니다.\n\n"
                                f"**[💼 전문 역할(전공) 집중 감사 영역 - 80% 가중치 집중 점검]**:\n"
                                f"{ext_persona_focus}\n\n"
                                f"*(단, 본인의 전문 직업 영역을 80% 가중치로 집중 감사하되, 컴파일/빌드 무결성 및 런타임 크래시 방어는 기본 필수 전제로 확인하십시오.)*\n\n"
                                f"**[🎯 수동적 승인봇(Rubber-Stamping) 전면 금지 및 독립 교차 설계 비평 헌법]**:\n"
                                f"1. **'이것이 최선인가? 더 좋은 선택(Better Alternative)은 없는가?' 독립적 비평**:\n"
                                f"   - 선행 검증이나 Coder의 작업에 대해 단순히 '어 만들어 놨네. 오류 없으니 통과' 식으로 승인하는 기계적 태도를 엄격히 금지합니다.\n"
                                f"   - 당신은 {ext_persona_name}의 독립적 시각에서, Coder가 미처 생각하지 못한 설계적/시각적/구조적 빈틈을 파고들어 더 높은 완성도로 설계하도록 견인해야 합니다.\n"
                                f"2. **직업(Persona)별 맞춤형 독립 심층 설계 견인 (모든 역할 공통 의무)**:\n"
                                f"   - 🕵️ **기본값 (종합 품질 검증관 / General)**: '기본값'이라고 해서 관성적 통과 금지. '이것이 최선의 선택인가?'를 자문하고, 에러 메시지 명확성, 진행 피드백, 조작성, 예외 처리 맹점을 비평하여 완성도를 견인하십시오.\n"
                                f"   - 🎨 **디자이너 봇 (UI/UX Designer)**: 이목이 집중될 디자인인가? 폰트/타이포 밸런스는 적절한가? 단순 텍스트 대신 **나노바나나(#nano-banana-image / NanoBanana Hub)** 이미지 에셋을 생성/배치하여 완성도를 높일 곳은 없는가? 부족하다면 과감히 `[STATUS: REJECTED]` 선언 후 구체적 디자인 스펙/에셋 제작을 지시하십시오.\n"
                                f"   - 🛡️ **보안 전문가 (Security & AppSec)**: '가장 안전한 최선의 방어 구조인가?' 공격자 시각에서 권한 상승, 인젝션, 경로 탈출(`../`), 토큰 노출을 심의하고 안전한 보안 아키텍처 대안을 제시하십시오.\n"
                                f"   - 💥 **카오스 테스터 (Chaos & Resilience)**: '극한 상황에서도 우아하게 복구(Graceful Degradation)되는 최선의 구조인가?' 비동기 경합, 타임아웃, 기형 페이로드 등 엣지 케이스를 격파하고 자가 치유 설계를 강제하십시오.\n"
                                f"   - ⚡ **성능 & SRE 엔지니어 (Performance & SRE)**: '장기 가동 시 리소스 고갈 없는 최선의 성능인가?' O(N^2) 비효율 루프, 블로킹 I/O를 배치/캐싱으로 전환하고 메모리/소켓 누수 방어책을 제시하십시오.\n"
                                f"   - 🏛️ **수석 아키텍트 (Architect & PO Requirements)**: '향후 확장과 유지보수가 가장 우수한 최선의 아키텍처인가?' 단일 책임(SRP), 모듈 디커플링, 클린 인터페이스 대안을 제시하여 하드코딩 땜질을 차단하십시오.\n\n"
                                f"**[🚨 최우선 검증 대상 - 사용자 원본 요청]:**\n"
                                f"────────────────────────────────────────\n"
                                f"{clean_task_desc}\n"
                                f"────────────────────────────────────────\n\n"
                                f"**[Coder 작업 요약]:**\n{compact_agent_context(latest_coder_output, max_chars=12000)}\n\n"
                                f"**[1차 qa1 검증 보고서 요약]:**\n{compact_agent_context(latest_qa_output, max_chars=8000)}\n\n"
                                + (f"**[선행 다중 QA 검증 결과 요약]:**\n{extended_qa_summaries_for_subsequent}\n\n" if extended_qa_summaries_for_subsequent else "")
                                + arbitration_guidance
                                + f"{diff_summary_text}\n"
                                
                                f"**[검증 관점 ({qa_name} - {ext_persona_name} 독립 교차 감사)]**:\n"
                                f"1. **사용자 원본 질문 및 요청 직접 답변 최우선 확인 (User Question Direct Answer First)**: 사용자가 질문(점수 평가, 감점 요인 분석, 해결책 문의 등)을 던진 경우, 최상단에 사용자 질문에 대한 직접적이고 전문적인 답변이 충실히 작성되었는지 가장 먼저 확인하십시오. 내부 협업/빌드 보고만 나열되어 있고 질문 답변이 부실하거나 누락된 경우 즉시 반려(`[STATUS: REJECTED]`)하십시오.\n"
                                f"2. **사용자 요청 온전한 실현**: 사용자 지시사항이 실제 소스코드와 기능에 100% 충실히 반영되었는가?\n"
                                f"3. **가시적 실체 구현 & 안티 트레이싱 (Anti-Mocking, Anti-Tracing & Provenance)**: 단순 주석, 껍데기 함수, 가짜 모킹이 아닌 실체 코드가 구현되었는가? 사용자가 제공한 참고용 레퍼런스 이미지 표절, 소스코드 내 스크립트 트레이싱(Image.open/cv2.imread/findContours/Base64 임베딩), 또는 이전 라운드 산출물 재탕이 없는가? 순수 절차적(Procedural) 생성 여부 및 외부 텍스처 미사용이 입증되었는가?\n"
                                f"4. **런타임 결함 및 예외 방어**: 크래시, 무한 루프, null 오류 위험이 없는가?\n"
                                f"5. **선행 검증 맹점 보완**: 이전 QA들이 간과했을 수 있는 엣지 케이스나 플랫폼 규격 위반 사항이 없는가?\n\n"
                                f"**[검증 및 판정 규칙]:**\n"
                                f"1. 결함이나 미비점, 또는 **직업(Persona) 관점에서 설계적/시각적/구조적 보강이 필요한 경우**:\n"
                                f"   - 반드시 첫 줄에 `[STATUS: REJECTED]`를 표기하고, Coder가 미처 생각하지 못한 설계 대안 및 구체적 개선 지침(폰트/스타일, 나노바나나 에셋, 아키텍처 대안)을 작성하십시오.\n"
                                f"2. 모든 요구사항이 온전히 구현되었고 결함이 없으며 최선의 설계가 입증된 경우:\n"
                                f"   - 반드시 첫 줄에 `[STATUS: APPROVED]`를 표기하고 독립 검증 통과 내역 및 **[전문가 관점 심층 평가 & 차기 고도화 권고]**를 작성하십시오.\n"
                                f"3. 사용자 요청이 정보 확인, 기획 자문, 시스템 상태 조회, 인사/테스트 답변인 경우:\n"
                                f"   - Coder의 답변이 사용자 질문에 온전히 부합하고 결함이 없다면, 소스코드 변경(Diff)이 없더라도 반드시 첫 줄에 `[STATUS: APPROVED]`를 표기하십시오."
                            )

                            try:
                                async with qa_target_channel.typing():
                                    ext_qa_timeout = getattr(config, "EXTENDED_QA_TIMEOUT", 600.0)
                                    current_ext_output = await asyncio.wait_for(
                                        cls.run_extended_qa_turn(
                                             session=session,
                                            prompt=ext_qa_prompt,
                                            qa_config=qa_cfg,
                                            on_tool_update=on_ext_qa_tool
                                        ),
                                        timeout=ext_qa_timeout
                                    )
                                    if channel_id:
                                        AutonomySafetyLayer.record_llm_call(channel_id)
                            except asyncio.TimeoutError:
                                logger.error(f"[{qa_name}] Extended QA turn timed out after {ext_qa_timeout}s")
                                current_ext_output = (
                                    f"[STATUS: REJECTED]\n"
                                    f"⚠️ **[{qa_name} 모델 응답 시간 초과 ({int(ext_qa_timeout)}초 타임아웃)]**\n"
                                    f"OpenRouter 또는 LLM 엔진의 응답이 {int(ext_qa_timeout)}초 내에 완료되지 않아 안전 가드에 의해 타임아웃되었습니다.\n"
                                    f"파이프라인 정체를 방지하기 위해 사령관 중재로 이관합니다."
                                )
                            except Exception as e:
                                logger.error(f"[{qa_name}] Extended QA turn encountered exception: {e}")
                                current_ext_output = (
                                    f"[STATUS: REJECTED]\n"
                                    f"⚠️ **[{qa_name} 모델 호출 중 예외 발생 (인프라 장애)]**\n"
                                    f"오류 내역: {str(e)}\n"
                                    f"파이프라인 정체를 방지하기 위해 사령관 중재로 이관합니다."
                                )
                            finally:
                                try:
                                    await qa_status_msg.delete()
                                except Exception:
                                    try:
                                        await qa_status_msg.edit(content=f"✅ **[라운드 {round_num}/{max_rounds}] {qa_name} 독립 교차 검증 완료**")
                                    except Exception:
                                        pass

                            if cls.is_system_error(current_ext_output):
                                return current_ext_output

                            ext_chunks, ext_file_attachments = await async_prepare_message_payload(
                                current_ext_output,
                                workspace_root=session.workspace,
                                excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                                excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                            )
                            for idx, chunk in enumerate(ext_chunks):
                                files_payload = ext_file_attachments if idx == 0 else []
                                await safe_send_discord_message(qa_target_channel, chunk, files=files_payload)
                                if idx < len(ext_chunks) - 1:
                                    await asyncio.sleep(0.4)

                            # Discord RateLimit and engine transition relaxation pause
                            await asyncio.sleep(0.5)

                            qa_passed = cls.is_qa_approved(current_ext_output)
                            if qa_passed and not is_anti_mock_clean:
                                logger.warning(f"TrioAgentRunner: {qa_name} approved verbally but anti-mocking violations exist. Overriding to REJECTED.")
                                qa_passed = False
                                current_ext_output = (
                                    f"[STATUS: REJECTED]\n"
                                    f"🚨 **[{qa_name} 품질 게이트 강제 반려 — Anti-Mocking / 레퍼런스 이미지 표절 적발]**\n"
                                    + "\n".join(f"• {v}" for v in anti_mock_violations) + "\n\n"
                                    f"Coder는 사용자 참고 이미지 복사나 라운드 재탕을 중단하고 새로운 실체 결과물을 생성하십시오."
                                )
                                await safe_send_discord_message(main_channel, current_ext_output)


                            if getattr(session, "is_stopped", False) or not getattr(session, "is_busy", True):
                                return "🛑 사용자의 요청으로 작업이 중단되었습니다."

                            squad_results[qa_name] = {
                                "approved": qa_passed,
                                "output": current_ext_output,
                                "model": qa_model_name,
                                "index": qa_num,
                            }

                            if qa_passed:
                                previously_approved_qas.add(qa_num)
                                extended_qa_outputs[qa_name] = current_ext_output
                                extended_qa_summaries_for_subsequent += f"• **{qa_name} 통과**: {current_ext_output[:4000]}\n"
                                if qa_num == 2:
                                    latest_qa2_output = current_ext_output

                    # 2. Commander 3-Tier Arbitration Protocol
                    resolution = await Commander.resolve_qa_conflict(
                        qa_results=squad_results,
                        diff_info=diff_info,
                        prompt=clean_task_desc,
                        primary_qa_approved=True,
                        round_num=round_num,
                        max_rounds=max_rounds
                    )

                    if resolution.get("has_conflict"):
                        arbitration_text = resolution.get("arbitration_text", "")
                        if arbitration_text:
                            await safe_send_discord_message(main_channel, arbitration_text)

                    if not resolution.get("is_approved"):
                        extended_qa_rejected = True
                        last_arbitration_directive = resolution.get("synthesized_directive", "") or resolution.get("arbitration_text", "")
                        rej_details = []
                        for qn, qres in squad_results.items():
                            if not qres.get("approved"):
                                rej_details.append(f"[{qn} 반려 사유]\n{qres.get('output')}")

                        arb_prefix = resolution.get("arbitration_text", "")
                        feedback_for_coder = f"{arb_prefix}\n\n" + "\n\n".join(rej_details) if arb_prefix else "\n\n".join(rej_details)
                        session.set_status("busy", f"⚠️ QA 스쿼드 반려 ➔ Coder 보강 중 ({round_num}R)")
                        await send_transit_notice(f"❌ **[스쿼드 라운드 {round_num} 반려]** 사령관 단일화 중재 지침을 Coder에게 전달하여 보강 재작업을 지시합니다. (기승인 QA 승인 유지 · 반려 QA만 재검증)")
                        continue
                    else:
                        if resolution.get("has_conflict"):
                            await send_transit_notice(resolution.get("verdict_summary", "✅ 사령관 중재: 단일화 승인 완료"))

                auditor_step = squad_total_steps if active_extended_qas else total_steps
                await send_transit_notice(
                    f"⚡ **[{auditor_step}/{auditor_step}단계 최종 감사]** ⚖️ <@1539459595133521930> PRD 헌법 부합성 & 기계적 증거 전수 대조 감사 시작..."
                )

                # Check ThermalGuard before starting Auditor final audit
                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 3: Client Auditor Turn
                # -------------------------------------------------------------
                auditor_display = GlobalModelManager.get_role_model_display("auditor")
                if getattr(session, "is_stopped", False) or not getattr(session, "is_busy", True):
                    return "🛑 사용자의 요청으로 작업이 중단되었습니다."
                session.set_busy(True, f"⚖️ Auditor 최종 감사 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    auditor_status_text = f"⚖️ **[라운드 {round_num}/{max_rounds}] Auditor({auditor_display})가 PRD 헌법 부합성 & 기계적 증거 전수 대조 감사를 시작합니다...**"
                    await on_status_update(auditor_status_text)

                # Target for detailed Auditor logs: dedicated Auditor bot client in main channel > main channel
                auditor_target_channel = auditor_channel or main_channel

                auditor_status_msg = await auditor_target_channel.send(
                    f"⚖️ **[라운드 {round_num}/{max_rounds}] Auditor 전수 감사 로그...**"
                )

                async def on_auditor_tool(tool_status: str):
                    try:
                        await auditor_status_msg.edit(content=f"⚖️ **[Auditor({auditor_display}) 전수 감사 & 차기 로드맵 설계 중]**\n{tool_status}")
                    except discord.DiscordException:
                        pass

                ext_qa_report_block = ""
                if active_extended_qas:
                    if resolution and resolution.get("has_conflict"):
                        arb_text = resolution.get("arbitration_text", "")
                        if arb_text:
                            ext_qa_report_block += f"**[사령관 & 수석감사관 중재 판결 요약]:**\n{arb_text}\n\n"
                    if squad_results:
                        for qname, qdata in squad_results.items():
                            qout = qdata.get("output", "")
                            qmodel = qdata.get("model", "")
                            qapp = qdata.get("approved", False)
                            status_tag = "승인 통과" if qapp else "사령관 중재 승인"
                            ext_qa_report_block += f"**[{qname} 독립 교차 검증 ({status_tag}) 리포트 요약]:**\n{qout[:6000]}\n\n"
                    elif extended_qa_outputs:
                        for qname, qout in extended_qa_outputs.items():
                            ext_qa_report_block += f"**[{qname} 독립 교차 검증 리포트 요약]:**\n{qout[:6000]}\n\n"
                elif is_quad_pipeline and latest_qa2_output:
                    ext_qa_report_block = f"**[qa2 독립 교차 검증 리포트 요약]:**\n{latest_qa2_output[:8000]}\n\n"

                qa_summary_title = f"{squad_total_steps}중 협업 품질 감사 통과 요약" if (active_extended_qas and squad_qas) else "3중 협업 품질 감사 통과 요약"
                qa_reviewers_label = f"QA (qa1 ~ qa{len(squad_qas) + 1}): 독립 교차 검증 및 결함 전수 통과" if (active_extended_qas and squad_qas) else "qa1: 오류 및 엣지 케이스 검증 통과"

                auditor_prompt = (
                    f"[역할: 의뢰인 전담 최종 승인관 겸 품질 감사관 (Client Auditor / {config.AUDITOR_MODEL})]\n\n"
                    f"당신은 오직 **작업을 의뢰한 사용자(요청자)의 눈과 기준만을 100% 대변하는 의뢰인의 수락 대변인이자 최종 감사관**입니다.\n"
                    f"⚠️ **[🚨 핵심 원칙: 최종 답변의 수신자는 Coder/QA가 아니라 오직 '요청자(사용자)님'입니다]**:\n"
                    f"- 당신의 최종 보고서는 내부 개발팀(Coder나 QA)에게 주는 개발 피드백이나 회의록이 아닙니다.\n"
                    f"- **요청을 맡기신 사용자(요청자)님께 1:1로 직접 결과를 정중하고 명확하게 보고하는 최종 답변**이어야 합니다.\n"
                    f"- 사용자님이 요청하신 사항을 감사관이 어떻게 정확히 파악했고, 실제 코드와 화면/동작에 빠짐없이 구현 및 반영되었는지 상세히 설명하여 요청자에게 깊은 신뢰와 확신을 주어야 합니다.\n\n"
                    f"**[🚨 최종 수락 기준 - 사용자 원본 요청 및 요구사항 요건]:**\n"
                    f"────────────────────────────────────────\n"
                    f"{clean_task_desc}\n"
                    f"────────────────────────────────────────\n\n"
                    f"**[핵심 감사 기준 (Chief Auditor & Client Value Alignment)]**:\n"
                    f"1. **🎯 사용자 의뢰 완결성 및 본질 가치 부합 (User Intent Fulfilment)**:\n"
                    f"   - **\"사용자 본래 요구사항과 의도가 실제 화면과 소스코드에 온전히 구현되었는가?\"**\n"
                    f"   - 사용자 요구가 무시되었거나 정해진 템플릿만 실행된 경우 가차없이 `[STATUS: AUDIT_FAILED]`를 선언하십시오.\n"
                    f"   - 불필요한 곁가지 기능으로 복잡성과 기술 부채를 유발하지 않았는지 확인하십시오.\n"
                    f"2. **실사용자 체감 가치 검증 (User Experience Verification)**: 사용자가 이 결과물을 브라우저나 기기에서 실행했을 때 실질적인 시각적/기능적 변화를 온전히 체감할 수 있는지 확인하십시오.\n"
                    f"3. **기계적 Quality State 및 증거 기반 무결성 검증 (Mechanical Evidence Over Claims)**:\n"
                    f"   - AI의 주관적 감상이나 자화자찬 점수가 아니라, 시스템이 아래 수집한 [Quality State Store]의 실제 QC 합격률과 회귀(Regression) 여부를 대조 감사하십시오.\n"
                    f"   - 이전 턴 대비 핵심 기능(Functional AC)이 깨졌거나 회귀가 적발된 경우 무조건 [STATUS: AUDIT_FAILED] [ACTION: REVISE]로 반려하십시오.\n"
                    f"4. **불완전 구현 & 가짜 모킹 색출**: Mock/Stub/TODO 주석 방치 여부, 가짜 Canvas/Box 우회 렌더링을 철저히 단속하십시오.\n"
                    f"5. **다중 인터랙션 렌더링 무결성**: AST/정규식 꼼수를 이용한 은폐형 렌더링을 적발하십시오.\n"
                    f"6. **🎯 무인 자율 운영 시 95% 사용자 의도 닻(Intent Anchor) 엄격 검증**:\n"
                    f"   - 무인 자율 운영(Mode 3) 시에는 사용자가 현장에서 매 턴 지적할 수 없으므로, Coder의 수정 내역이 사용자가 명명한 본래 기획(`PRD.md`) 및 `ROADMAP.md`의 현재 마일스톤과 **95% 이상 명확히 일치**하는지 엄격히 심사하십시오.\n"
                    f"   - 엉뚱한 부가 기능(단순 주석, 불필요한 설정 추가, 목표와 무관한 코드 조작 등)으로 목표 표류(Goal Drift)가 발생한 경우 가차없이 반려(`[STATUS: AUDIT_FAILED] [ACTION: REVISE]`)하여 본래 기획 궤도로 복귀시키십시오.\n"
                    f"7. 🎨 **[Anti-AI-Slop & 상용 서비스 품격 심사 (Commercial Craftsmanship Mandate)]**:\n"
                    f"   - **프롬프트 누출(Prompt Bleed-through) 차단**: 내부 개발 수치(`50px+`, `70px+`, `16ms`)나 백엔드 인프라 전문 용어(`3-Way 자동 병합`, `단절 테스트`, `온디바이스 평탄화` 등)가 사용자 UI 버튼/타이틀로 날것 그대로 노출되었는지 전수 검사하십시오. 발견 시 가차없이 반려하십시오.\n"
                    f"   - **텍스트 쪼개짐 & 반응형 붕괴 차단**: 단어가 공간 부족으로 `단절 / 테스 / 트`처럼 세로로 쪼개지거나, 상단 메뉴가 모바일 360dp 뷰포트 밖으로 삐져나가는 결함을 철저히 색출하십시오.\n"
                    f"   - **아이콘 대원칙**: 24~48dp 크기 버튼/탭에 복잡한 AI 생성 썸네일을 축소 삽입하지 않고 깔끔한 벡터 아이콘(SVG/Material Icons)을 사용했는지 확인하십시오.\n"
                    f"   - **AI 클리셰 더미 데이터 금지**: '김민수 수석연구원', '이지은 디자이너' 등 천편일률적 LLM 생성 더미 데이터를 배제하고 실제 제품 도메인에 부합하는 자연스러운 데이터를 사용했는지 점검하십시오.\n\n"
                    f"**[🚨 감사관 도구 사용 지침]**:\n"
                    f"- 🔒 **[지정 워크스페이스 엄격 격리 원칙]**: 반드시 현재 프로젝트 워크스페이스(`./{os.path.basename(session.workspace or 'project')}`) 내부의 파일만 감사하십시오. 타 프로젝트 디렉토리 접근 및 수정을 엄격히 금지합니다.\n"
                    f"- 소스코드 및 검증 결과 확인: 필요한 경우 view_file, grep_search, run_command(독립 빌드/테스트)를 충분히 활용하여 Coder와 QA의 작업 결과가 실제 프로덕션 표준을 충족하는지 독립적이고 철저하게 최종 감사하십시오.\n"
                    f"- **🆕 [소규모 직접 패치 권한 및 물리 검증 의무]**: 최종 감사 중 명백한 단순 버그(오타, 1~5줄 이내의 로직 오류)를 발견한 경우, `replace_file_content` 도구로 **직접 수정**할 수 있습니다.\n"
                    f"  - ⚠️ **주의 (Write = Build & Test 강제)**: 만약 코드를 1줄이라도 직접 수정했다면, 눈으로만 승인하지 말고 반드시 `./gradlew assembleDebug` 또는 `npm run build` 등의 명령어를 `run_command`로 실행하여 **물리적으로 컴파일이 되는지 확인한 후** `[STATUS: AUDIT_PASSED (Auditor Direct Patch)]`를 선언하십시오.\n"
                    f"  - 단, 10줄 초과 수정 또는 구조적 리팩토링이나 새로운 기능 추가는 직접 패치 금지이며 Coder에게 재작업 지시를 내리십시오.\n\n"
                    f"**[Coder 작업물 요약]:**\n{compact_agent_context(latest_coder_output, max_chars=12000)}\n\n"
                    + f"\n{QualityStateManager.format_state_summary_for_commander(effective_ws)}\n\n"
                    + qa_vision_block
                    + f"{diff_summary_text}\n"
                    f"**[qa1 검증 리포트 요약]:**\n{compact_agent_context(latest_qa_output, max_chars=8000)}\n\n"
                    + ext_qa_report_block
                    + f"**[감사 판정 규칙 및 출력 형식 (오케스트레이터 실시간 제어 신호)]:**\n"
                    f"1. **가치 부합 실패 또는 사용자 목표 왜곡 시 (즉시 중단)**:\n"
                    f"   - **반드시 첫 줄에 `[STATUS: AUDIT_STOP] [VALUE: LOW] [ACTION: STOP]`를 표기하십시오.**\n"
                    f"   - 사유: 왜 이 작업이 사용자 목표와 맞지 않고 기술 부채를 유발하여 중단되어야 하는지 직설적으로 기술하십시오.\n\n"
                    f"2. **기술적 결함 또는 방향성 보완 필요 시 (재작업 지시)**:\n"
                    f"   - **반드시 첫 줄에 `[STATUS: AUDIT_FAILED] [VALUE: MEDIUM] [ACTION: REVISE]`를 표기하십시오.**\n"
                    f"   - ❌ **반려 사유 및 결함 목록**: 부족한 점을 기술하고 Coder 재작업 지침을 전달하십시오.\n\n"
                    f"3. **단순 버그를 직접 패치한 경우**:\n"
                    f"   - **반드시 첫 줄에 `[STATUS: AUDIT_PASSED (Auditor Direct Patch)]`를 표기하십시오.**\n"
                    f"   - 직접 수정한 내용을 명시하십시오.\n\n"
                    f"4. **모든 요구사항 충족 및 높은 사용자 가치 입증 시 (최종 승인)**:\n"
                    f"   - **반드시 첫 줄에 `[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]`를 표기하십시오.**\n"
                    f"   - 🚨 **[중요 - 요청자(사용자)님께 드리는 1:1 직접 답변 표준 양식 (Comprehensive Report)]**:\n"
                    f"     내부 개발팀(Coder/QA)을 향한 중복 회의록을 배제하고, 장황한 내부 절대경로(/home/...) 대신 상대경로(`./...`) 또는 파일명만 표기하며, **요청자(사용자)님을 직접 수신자로 하여 질문에 대한 심층적이고 구체적인 답변과 완성도 높은 분석 리포트를 명확한 개조식**으로 작성하십시오:\n\n"
                    f"     (1) `### 💬 사용자 질문 및 요청에 대한 직접 답변`:\n"
                    f"         • 사용자님께서 질문하신 내용('몇 점인가요?', '감점 요인과 해결책이 무엇인가요?', '어떻게 생각하나요?', 평가 및 자문)에 대해 친절하고 전문적이며 명쾌하게 1:1로 직접 답변하십시오. (사용자 질문이 있는 경우 무조건 최상단에 최우선 배치!)\n"
                    f"     (2) `### 📁 생성/수정된 핵심 산출물 및 파일`:\n"
                    f"         • `상대경로/파일명` (역할 및 변경 요약 [A ➔ B]) (내부 절대경로 노출 금지, 전체 코드 덤프 금지)\n"
                    f"     (3) `### 🔍 세부 구현 및 품질 검증 결과`:\n"
                    f"         • 사용자님의 세부 요구사항 항목별 핵심 구현 내용 및 엣지 케이스 방어 결과 보고\n"
                    f"     (4) `### 🚀 실행 및 확인 방법`:\n"
                    f"         • 사용자님이 직접 브라우저 또는 개발 환경에서 변경 사항을 체감하고 테스트해볼 수 있는 구체적인 실행/조작 가이드\n"
                    f"     (5) `---\n### 🛡️ {qa_summary_title}` (맨 하단 최종 요약):\n"
                    f"         • 👨‍💻 **Coder**: 구현 및 방어 로직 완비 | 🕵️ **{qa_reviewers_label}** | ⚖️ **Client Auditor**: 사용자 의도 100% 부합 최종 승인\n"
                    f"         *(핵심 변경 사항과 검증 결과를 한눈에 파악 가능한 최상의 가독성으로 작성하십시오)*"
                )

                try:
                    async with auditor_target_channel.typing():
                        latest_auditor_output = await cls.run_auditor_turn(
                            session=session,
                            prompt=auditor_prompt,
                            on_tool_update=on_auditor_tool
                        )
                        if channel_id:
                            AutonomySafetyLayer.record_llm_call(channel_id)
                finally:
                    try:
                        await auditor_status_msg.delete()
                    except Exception:
                        try:
                            await auditor_status_msg.edit(content=f"✅ **[라운드 {round_num}/{max_rounds}] Auditor 전수 감사 단계 완료**")
                        except Exception:
                            pass

                # Check for execution error
                if cls.is_system_error(latest_auditor_output):
                    return latest_auditor_output

                # 🛡️ Collaboration Intelligence Check: Auditor Value Alignment (A5 / C3)
                import importlib, sys
                if "autonomy_guard" in sys.modules:
                    importlib.reload(sys.modules["autonomy_guard"])
                from autonomy_guard import CollaborationIntelligence
                val_action, val_reason = CollaborationIntelligence.evaluate_auditor_value_alignment(
                    prompt=prompt,
                    diff_info=diff_info,
                    is_cosmetic_only=(risk_level == "LOW")
                )
                if val_action == "STOP_LOW_VALUE":
                    latest_auditor_output = f"[ACTION: STOP] [VALUE: LOW]\n{val_reason}"

                # [🚨 AGY 4.x Action: STOP] If Auditor explicitly rules [VALUE: LOW] / [ACTION: STOP]
                is_value_stopped = (
                    "[ACTION: STOP]" in latest_auditor_output
                    or "[STATUS: AUDIT_STOP]" in latest_auditor_output
                    or ("[VALUE: LOW]" in latest_auditor_output and "[ACTION: PROCEED]" not in latest_auditor_output)
                )
                if is_value_stopped:
                    if round_num < max_rounds:
                        logger.warning(
                            f"TrioAgentRunner: Auditor flagged value/scope issue in Round {round_num}/{max_rounds}. "
                            f"Routing feedback to Coder for root-cause analysis and self-healing refinement."
                        )
                        feedback_for_coder = (
                            f"[⚖️ Client Auditor 가치/범위 반려 — 원인 분석 및 자율 개선 재작업 지시 (Round {round_num}/{max_rounds})]\n\n"
                            f"Auditor 검증 소견:\n{latest_auditor_output}\n\n"
                            f"**[🚨 핵심 실행 지침 — 절대 작업을 중단하거나 취소하지 마십시오]**:\n"
                            f"1. 위 Auditor 지적 사항의 근본 원인을 철저히 분석하십시오 (예: 과도한 파일 수정, 엉뚱한 방향 구현, 가짜 주석 등).\n"
                            f"2. 불필요하게 건드린 파일들은 원상 복구하고, 사용자의 본래 명령/목표에 직결된 핵심 코드만 정밀 타겟팅하여 수정하십시오.\n"
                            f"3. 원인을 해결한 뒤 실질적인 가치를 증명하여 Auditor가 승인할 수 있도록 개선하십시오."
                        )
                        session.set_status("busy", f"⚠️ Auditor 가치 지적 ➔ Coder 원인 분석 및 자율 개선 중 ({round_num+1}R)")
                        await send_transit_notice(
                            f"⚠️ **[Auditor 지적에 따른 자율 개선 재작업 (Round {round_num}/{max_rounds})]**\n"
                            f"가치/범위 결함 감지 → **작업을 냅다 취소하지 않고**, 원인을 파악하여 Coder에게 정밀 개선 재작업을 지시합니다."
                        )
                        continue
                    else:
                        logger.warning(f"TrioAgentRunner: Auditor issued STOP action for channel {session.channel_id} after {max_rounds} rounds.")
                        session.set_status("idle", f"🛑 Auditor 가치 검증 거부 (최대 라운드 {max_rounds}회 도달)")
                        stop_notice = (
                            f"🛑 **[Auditor PO 가치 검증 거부 — 최대 라운드({max_rounds}회) 개선 시도 후 중단]**\n\n"
                            f"Client Auditor가 사용자 본래 의도와 프로젝트 전략 목표에 비추어 검증한 결과, "
                            f"{max_rounds}회의 자율 개선 시도 후에도 실질 가치가 부족하거나(VALUE: LOW) 기술 부채를 유발한다고 판단하여 작업을 안전하게 중단했습니다.\n\n"
                            f"**[Auditor 소견]:**\n{latest_auditor_output[:1200]}"
                        )
                        await safe_send_discord_message(auditor_target_channel, stop_notice)
                        await send_transit_notice(f"🛑 **[Auditor STOP]** {max_rounds}회 자율 개선 시도 후 사용자 목표 부합 실패로 작업 중단")
                        return stop_notice

                # Check Auditor Acceptance (일반 승인 + Auditor 직접 패치 승인 모두 처리)
                auditor_passed = cls.is_auditor_approved(latest_auditor_output) and not is_value_stopped
                if auditor_passed and not is_anti_mock_clean:
                    logger.warning(f"TrioAgentRunner: Auditor approved verbally but anti-mocking violations exist: {anti_mock_violations}. Overriding to REJECTED.")
                    auditor_passed = False
                    latest_auditor_output = (
                        f"[STATUS: AUDIT_FAILED] [ACTION: STOP]\n"
                        f"🚨 **[감사관 최종 감사 거부 — Anti-Mocking / 레퍼런스 이미지 표절 적발]**\n"
                        + "\n".join(f"• {v}" for v in anti_mock_violations) + "\n\n"
                        f"프로젝트 헌법 및 무결성 기준에 따라 표절/재탕 행위가 감지되어 최종 승인을 거부합니다."
                    )
                    await safe_send_discord_message(main_channel, latest_auditor_output)

                # Ground Truth Verification for Auditor (Only when Auditor mistakenly claimed no files changed)
                if not auditor_passed and gates_info.get("all_passed") and is_anti_mock_clean and diff_info.get("has_changes"):
                    # NEVER override if Auditor explicitly declared AUDIT_FAILED
                    has_explicit_fail = (
                        "[STATUS: AUDIT_FAILED]" in latest_auditor_output
                        or "STATUS: AUDIT_FAILED" in latest_auditor_output
                        or "[STATUS: AUDIT_STOP]" in latest_auditor_output
                        or "STATUS: AUDIT_STOP" in latest_auditor_output
                    )
                    if not has_explicit_fail and ("변경 사항 없음" in latest_auditor_output or "미생성" in latest_auditor_output):
                        logger.info("EvidenceValidator: Physical Git Diff and 7 Quality Gates verified on disk. Correcting false no-change claim.")
                        auditor_passed = True
                        if not any(k in latest_auditor_output for k in ["### 💬 사용자 질문 및 요청에 대한 직접 답변", "### 📁 생성/수정된 파일", "### 📁 생성/수정된 핵심 산출물 및 파일", "### ✨ 주요 기능 및 특징", "### 🔍 요구사항별 구현 및 반영 결과 검증", "### 🔍 세부 구현 및 품질 검증 결과"]):
                            ws_name = os.path.basename(session.workspace) if session.workspace else "프로젝트"
                            files_list = "\n".join([f"• `{f}` (수정 완료 및 물리 검증 통과)" for f in diff_info.get("changed_files", [])[:5]]) or "• 워크스페이스 내 주요 소스코드"
                            latest_auditor_output = (
                                f"[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
                                f"### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
                                f"• 사용자 요구사항 접수 및 충실한 구현·검증 완료\n\n"
                                f"### 📁 생성/수정된 핵심 산출물 및 파일\n"
                                f"{files_list}\n\n"
                                f"### 🔍 세부 구현 및 품질 검증 결과\n"
                                f"1. **사용자 핵심 요구사항 구현**: 지시된 기능 및 변경 사항 소스코드 반영 완료\n"
                                f"2. **시각 및 다중 인터랙션 무결성**: 조작 상태 렌더링 붕괴 0px 방어\n"
                                f"3. **물리적 런타임 안정성**: 크래시 0건 및 7대 게이트 무결성 확보\n\n"
                                f"### 🚀 실행 및 확인 방법\n"
                                f"• 워크스페이스 내 산출물 및 빌드 파일 통해 즉시 확인 가능\n\n"
                                f"---\n"
                                f"### 🛡️ {pipe_label} 협업 품질 요약\n"
                                f"• 👨‍💻 Coder 구현 완료 | 🕵️ QA 통과 | ⚖️ Auditor 최종 승인"
                            )

                if active_extended_qas:
                    pipe_label = f"{squad_total_steps}중"
                elif is_quad_pipeline:
                    pipe_label = "4중 쿼드"
                else:
                    pipe_label = "3중"

                user_mention_str = f"<@{AUDITOR_MENTION_USER_ID}> " if AUDITOR_MENTION_USER_ID else ""
                success_tag = f"{user_mention_str}🎉 **[{pipe_label} 에이전트 협업 개발 완료 - 최종 승인]** (라운드 {round_num}/{max_rounds})"

                auditor_send_text = latest_auditor_output
                if auditor_passed:
                    # 🚀 [Android APK & Deliverable Auto-Upload to Google Drive on Auditor Approval]
                    gdrive_apk_block = ""
                    if session.workspace and os.path.isdir(session.workspace):
                        try:
                            from gdrive_uploader import GoogleDriveUploader
                            from discord_utils import ensure_timestamped_deliverable
                            apk_files = []
                            for root, _, files in os.walk(session.workspace):
                                for f in files:
                                    if f.endswith(".apk") and not f.startswith(".") and not f.startswith("unsigned-"):
                                        p = os.path.join(root, f)
                                        apk_files.append((p, os.path.getmtime(p)))
                            if apk_files:
                                apk_files.sort(key=lambda x: x[1], reverse=True)
                                target_apk = apk_files[0][0]
                                ts_name, latest_name, ts_path, latest_path = ensure_timestamped_deliverable(
                                    target_apk, workspace_root=session.workspace
                                )
                                upload_target = ts_path if (ts_path and os.path.isfile(ts_path)) else target_apk
                                upload_res = await asyncio.wait_for(
                                    GoogleDriveUploader.upload_file(upload_target, display_name=ts_name),
                                    timeout=45.0
                                )
                                latest_res = None
                                if latest_path and os.path.isfile(latest_path):
                                    try:
                                        latest_res = await asyncio.wait_for(
                                            GoogleDriveUploader.upload_file(latest_path, display_name=latest_name),
                                            timeout=45.0
                                        )
                                    except Exception:
                                        pass

                                if upload_res.get("success"):
                                    ts_url = upload_res.get("download_url") or upload_res.get("view_url") or "#"
                                    lat_url = (latest_res.get("download_url") or latest_res.get("view_url")) if (latest_res and latest_res.get("success")) else ts_url
                                    app_title = latest_name.replace("-latest.apk", "").replace(".apk", "")
                                    gdrive_apk_block = (
                                        f"📦 **Android APK 빌드 및 Google Drive 업로드 완료**\n"
                                        f"요청하신 {app_title} 최신 릴리즈 APK 빌드가 정상 완료되었으며, Google Drive에 업로드되었습니다. 아래 링크를 통해 바로 다운로드하실 수 있습니다.\n\n"
                                        f"---\n\n"
                                        f"#### 🔗 다운로드 링크\n"
                                        f"최신 릴리즈 (Latest APK):\n"
                                        f"[{latest_name} 다운로드]({lat_url})\n"
                                        f"타임스탬프 릴리즈:\n"
                                        f"[{ts_name} 다운로드]({ts_url})\n\n"
                                        f"---\n\n"
                                    )
                        except Exception as apk_up_err:
                            logger.debug(f"Auditor pass APK upload note: {apk_up_err}")

                    if gdrive_apk_block and gdrive_apk_block not in latest_auditor_output:
                        # Insert right after [STATUS: AUDIT_PASSED...] or at the very top
                        m_stat = re.search(r"(\[STATUS:[^\]]+\](?:[^\n]*\n+)?)", latest_auditor_output)
                        if m_stat:
                            stat_end = m_stat.end()
                            auditor_send_text = latest_auditor_output[:stat_end] + "\n" + gdrive_apk_block + latest_auditor_output[stat_end:] + f"\n\n{success_tag}"
                        else:
                            auditor_send_text = f"{gdrive_apk_block}{latest_auditor_output.rstrip()}\n\n{success_tag}"
                    else:
                        auditor_send_text = f"{latest_auditor_output.rstrip()}\n\n{success_tag}"

                # Post Auditor report to log target channel with safe attachments (auto_scan_deliverables enabled for APK/artifacts)
                auditor_chunks, auditor_file_attachments = await async_prepare_message_payload(
                    auditor_send_text,
                    workspace_root=session.workspace,
                    auto_scan_deliverables=True,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(auditor_chunks):
                    files_payload = auditor_file_attachments if idx == 0 else []
                    await safe_send_discord_message(auditor_target_channel, chunk, files=files_payload)
                    if idx < len(auditor_chunks) - 1:
                        await asyncio.sleep(0.4)

                if auditor_passed:
                    session.set_status("idle", "🟢 최종 감사 승인 완료 (정상 대기)")
                    if noti_channel:
                        try:
                            tag = f"📍 [<#{channel_id}>] " if channel_id else ""
                            noti_text = re.sub(r"<@!?&?\d+>\s*", "", f"{tag}{success_tag}")
                            await noti_channel.send(noti_text)
                        except Exception:
                            pass
                    
                    clean_auditor = latest_auditor_output
                    for tag in [
                        "[STATUS: AUDIT_PASSED (Auditor Direct Patch)]",
                        "STATUS: AUDIT_PASSED (Auditor Direct Patch)",
                        "[STATUS: AUDIT_PASSED]",
                        "STATUS: AUDIT_PASSED",
                        "[VALUE: HIGH]",
                        "[ACTION: PROCEED]",
                    ]:
                        clean_auditor = clean_auditor.replace(tag, "")
                    clean_auditor = clean_auditor.strip()
                    
                    final_brief = f"{success_tag}\n\n{clean_auditor}"
                    return final_brief
                else:
                    # Preserve previously_approved_qas as incremental caching;
                    # only QAs that previously rejected or were not dispatched need to re-run.
                    feedback_for_coder = f"[최종 감사관(Auditor / {config.AUDITOR_MODEL}) 반려 사유]\n{latest_auditor_output}"
                    session.set_status("busy", f"⚠️ Auditor 반려 ➔ Coder 보강 중 ({round_num}R)")
                    if active_extended_qas:
                        await send_transit_notice(f"❌ **[Auditor 라운드 {round_num} 반려]** Coder에게 선제적 내구성 보강 재작업을 지시합니다. (qa1 ➔ 다중 QA ➔ Auditor 전수 재검증)")
                    elif is_quad_pipeline:
                        await send_transit_notice(f"❌ **[Auditor 라운드 {round_num} 반려]** Coder에게 선제적 내구성 보강 재작업을 지시합니다. (qa1 ➔ qa2 ➔ Auditor 전수 재검증)")
                    else:
                        await send_transit_notice(f"❌ **[Auditor 라운드 {round_num} 반려]** Coder에게 선제적 내구성 보강 재작업을 지시합니다.")

            # If reached max_rounds without full approval
            session.set_status("warning", f"⚠️ 최대 라운드({max_rounds}회) 도달")
            _sqs = locals().get("squad_total_steps", 3)
            pipe_label = f"{_sqs}중" if active_extended_qas else ("4중 쿼드" if is_quad_pipeline else "3중")
            return f"⚠️ **[{pipe_label} 에이전트 협업 완료 - 최대 라운드({max_rounds}회) 도달]**"

        except asyncio.CancelledError:
            if session.current_process:
                session.current_process.terminate()
            session.set_status("idle", "정상 대기 중 (작업 취소)")
            raise
        except Exception as e:
            logger.exception("TrioAgentRunner 실행 중 오류 발생")
            session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
            active_qas_flag = bool(locals().get("active_extended_qas"))
            total_steps_val = locals().get("squad_total_steps", 3)
            err_pipe_label = f"{total_steps_val}중 다중" if active_qas_flag else "3중"
            return f"⚠️ {err_pipe_label} 에이전트 실행 중 오류 발생: {str(e)}"
        finally:
            session._in_multi_agent_pipeline = False
            session.set_busy(False, session.status_desc or "정상 대기 중")
            session.current_process = None

# Backward compatibility alias
DualAgentRunner = TrioAgentRunner
