"""
[🛡️ Quota Leak Prevention Test Suite]
Verifies all layers of Gemini quota protection:
  1. antigravity-usage config.json cacheTTL >= 3600
  2. telemetry_status_manager cache TTLs >= 3600
  3. start_telemetry_status_loop interval_sec >= 600
  4. QuotaManager._cache_ttl >= 3600
  5. fetch_native_antigravity_usage subprocess NOT called on cache hit
  6. Diagnostic/inquiry prompts bypass QA/Auditor agents (no extra quota burn)
"""
import os
import sys
import json
import time
import inspect
import unittest
import asyncio

os.environ.setdefault("UNITTEST_RUNNING", "1")

_agy_agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _agy_agent_dir not in sys.path:
    sys.path.insert(0, _agy_agent_dir)

from unittest.mock import AsyncMock, patch, MagicMock, call


# ---------------------------------------------------------------------------
# Test 1: antigravity-usage config.json cacheTTL >= 3600
# ---------------------------------------------------------------------------
class TestAntigravityUsageConfigTTL(unittest.TestCase):
    """Ensures the on-disk antigravity-usage config enforces a 1-hour cache TTL."""

    def test_cache_ttl_is_at_least_3600(self):
        config_path = os.path.expanduser("~/.config/antigravity-usage/config.json")
        self.assertTrue(
            os.path.exists(config_path),
            f"~/.config/antigravity-usage/config.json must exist (got: {config_path})"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        ttl = cfg.get("preferences", {}).get("cacheTTL", 0)
        self.assertGreaterEqual(
            ttl, 3600,
            f"cacheTTL must be >= 3600 to prevent frequent Google API calls, got {ttl}"
        )


# ---------------------------------------------------------------------------
# Test 2: telemetry_status_manager cache TTLs >= 3600
# ---------------------------------------------------------------------------
class TestTelemetryCacheTTL(unittest.TestCase):
    """Verifies all in-memory cache TTLs in telemetry_status_manager are >= 3600s."""

    def _get_source_constant(self, source: str, constant: str) -> float:
        """Extracts a numeric constant from source code string."""
        import re
        pattern = rf"{re.escape(constant)}\s*<\s*([0-9.]+)"
        match = re.search(pattern, source)
        if match:
            return float(match.group(1))
        return -1.0

    def test_native_usage_cache_ttl(self):
        tsm_path = os.path.join(_agy_agent_dir, "telemetry_status_manager.py")
        with open(tsm_path, "r", encoding="utf-8") as f:
            src = f.read()
        import re
        # Look for pattern: _native_usage_cache.get("time", 0.0) < <N>
        matches = re.findall(r'_native_usage_cache.*?<\s*([0-9.]+)', src)
        for m in matches:
            ttl = float(m)
            self.assertGreaterEqual(
                ttl, 3600.0,
                f"_native_usage_cache TTL must be >= 3600, found {ttl}"
            )

    def test_quota_sections_cache_ttl(self):
        tsm_path = os.path.join(_agy_agent_dir, "telemetry_status_manager.py")
        with open(tsm_path, "r", encoding="utf-8") as f:
            src = f.read()
        import re
        matches = re.findall(r'_last_quota_fetch_time.*?<\s*([0-9.]+)', src)
        for m in matches:
            ttl = float(m)
            self.assertGreaterEqual(
                ttl, 3600.0,
                f"_last_quota_fetch_time TTL must be >= 3600, found {ttl}"
            )


# ---------------------------------------------------------------------------
# Test 3: start_telemetry_status_loop default interval_sec >= 600
# ---------------------------------------------------------------------------
class TestTelemetryPollInterval(unittest.TestCase):
    """Verifies that the background polling loop default interval is >= 600s (10 min)."""

    def test_default_interval_is_at_least_600(self):
        import telemetry_status_manager
        sig = inspect.signature(telemetry_status_manager.start_telemetry_status_loop)
        interval_param = sig.parameters.get("interval_sec")
        self.assertIsNotNone(interval_param, "interval_sec parameter must exist")
        default_val = interval_param.default
        self.assertGreaterEqual(
            default_val, 600,
            f"start_telemetry_status_loop interval_sec default must be >= 600, got {default_val}"
        )


# ---------------------------------------------------------------------------
# Test 4: QuotaManager._cache_ttl >= 3600
# ---------------------------------------------------------------------------
class TestQuotaManagerCacheTTL(unittest.TestCase):
    """Verifies QuotaManager in trio_agent_runner uses a 1-hour cache TTL."""

    def test_quota_manager_ttl_is_at_least_3600(self):
        from trio_agent_runner import QuotaManager
        self.assertGreaterEqual(
            QuotaManager._cache_ttl, 3600.0,
            f"QuotaManager._cache_ttl must be >= 3600, got {QuotaManager._cache_ttl}"
        )


# ---------------------------------------------------------------------------
# Test 5: fetch_native_antigravity_usage uses cache on second call (no subprocess)
# ---------------------------------------------------------------------------
class TestNativeUsageCacheHit(unittest.TestCase):
    """Verifies subprocess is NOT called on second invocation within TTL window."""

    def test_cache_prevents_subprocess_on_hit(self):
        import telemetry_status_manager as tsm

        fake_data = {
            "account": "test@gmail.com",
            "groups": [],
            "raw": {}
        }
        # Prime the cache manually
        tsm._native_usage_cache = {"time": time.time(), "data": fake_data}

        with patch("subprocess.run") as mock_run:
            result = tsm.fetch_native_antigravity_usage()
            mock_run.assert_not_called()
            self.assertEqual(result, fake_data)

    def test_cache_expires_after_ttl(self):
        import telemetry_status_manager as tsm

        # Simulate expired cache (time in the far past)
        tsm._native_usage_cache = {"time": time.time() - 7200.0, "data": {"groups": []}}

        mock_result = MagicMock()
        mock_result.returncode = 1  # Simulate failure → falls back to cache
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = tsm.fetch_native_antigravity_usage()
            # subprocess.run should have been called (cache expired)
            self.assertGreaterEqual(mock_run.call_count, 1)


# ---------------------------------------------------------------------------
# Test 6: Diagnostic/inquiry prompts bypass QA and Auditor (original test)
# ---------------------------------------------------------------------------
class TestMultiAgentUnconditionalTransitOnDiagnosis(unittest.TestCase):
    """Verifies that in multi-agent collaboration modes (!mode 2, 2+, 3, 3+),
    diagnostic and inquiry prompts do NOT terminate early after Coder, but
    unconditionally advance through QA and Auditor to reach final client approval.
    """

    def setUp(self):
        from session_manager import ChannelSession
        self.session = ChannelSession(
            channel_id=1539483630089273435,
            workspace="/home/jangsung/Desktop/autocoding/!5_agy_agent",
            mode="pair"
        )
        self.session.is_busy = True

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    def test_diagnostic_prompt_advances_to_qa_and_auditor(self, mock_run_prompt, mock_thermal, mock_send):
        mock_run_prompt.side_effect = [
            "Coder: 진단 결과 백그라운드 누수 분석 리포트입니다.",
            "[STATUS: APPROVED]\nqa1 진단 무결성 검증 완료.",
            (
                "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
                "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
                "진단 결과: 백그라운드 누수 분석 완료.\n\n"
                "### 📁 생성/수정된 핵심 산출물 및 파일\n"
                "• 진단 완료"
            )
        ]
        main_channel = MagicMock()
        main_channel.id = 1539483630089273435
        main_channel.send = AsyncMock()
        main_channel.typing = MagicMock()
        main_channel.typing.return_value.__aenter__ = AsyncMock()
        main_channel.typing.return_value.__aexit__ = AsyncMock()

        qa_client = MagicMock()
        qa_client.is_ready.return_value = False
        auditor_client = MagicMock()
        auditor_client.is_ready.return_value = False

        from trio_agent_runner import TrioAgentRunner
        res = asyncio.run(
            TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="할당량 줄어드는데 버그 있나봐 찾아줘",
                main_channel=main_channel,
                qa_client=qa_client,
                auditor_client=auditor_client,
                max_rounds=1
            )
        )
        self.assertIn("최종 승인", res)
        self.assertEqual(mock_run_prompt.call_count, 3)
        self.assertFalse(self.session.is_busy)

    @patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock)
    @patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock)
    @patch("trio_agent_runner.AgentRunner.run_prompt")
    def test_inquiry_without_code_changes_advances_to_qa_and_auditor(self, mock_run_prompt, mock_thermal, mock_send):
        mock_run_prompt.side_effect = [
            "Coder: 답변: 시스템 구조 분석 결과입니다.",
            "[STATUS: APPROVED]\nqa1 시스템 구조 분석 검증 완료.",
            (
                "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\n\n"
                "### 💬 사용자 질문 및 요청에 대한 직접 답변\n"
                "답변: 시스템 구조 분석 결과 브리핑 완료.\n\n"
                "### 📁 생성/수정된 핵심 산출물 및 파일\n"
                "• 아키텍처 분석 리포트"
            )
        ]
        main_channel = MagicMock()
        main_channel.id = 1539483630089273435
        main_channel.send = AsyncMock()
        main_channel.typing = MagicMock()
        main_channel.typing.return_value.__aenter__ = AsyncMock()
        main_channel.typing.return_value.__aexit__ = AsyncMock()

        qa_client = MagicMock()
        qa_client.is_ready.return_value = False
        auditor_client = MagicMock()
        auditor_client.is_ready.return_value = False

        from trio_agent_runner import TrioAgentRunner
        res = asyncio.run(
            TrioAgentRunner.run_pipeline(
                session=self.session,
                prompt="현재 아키텍처 상태 어때? 감점 요인과 원인 분석해줘",
                main_channel=main_channel,
                qa_client=qa_client,
                auditor_client=auditor_client,
                max_rounds=1
            )
        )
        self.assertIn("최종 승인", res)
        self.assertEqual(mock_run_prompt.call_count, 3)
        self.assertFalse(self.session.is_busy)


if __name__ == "__main__":
    unittest.main()
