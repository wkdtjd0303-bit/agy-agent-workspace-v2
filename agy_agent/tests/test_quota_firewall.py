#!/usr/bin/env python3
"""
🛡️ Quota Firewall regression tests.

Antigravity(Gemini) 한도가 "저절로" 소모되는 것을 막는 방어막 검증:
1. 무인/자율 컨텍스트에서 Gemini 모델 호출이 차단된다.
2. 사용자 직접 요청(포그라운드)에서는 차단되지 않는다.
3. 무인 컨텍스트라도 비-Gemini 모델(gpt-oss, claude 등)은 허용된다.
4. AGY_UNATTENDED_GEMINI=allow 로 긴급 해제할 수 있다.
5. 모델 레지스트리가 자동으로 `agy models` CLI(언어서버 스폰)를 실행하지 않는다.
"""

import os
import sys
import unittest
from unittest.mock import patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)


class TestQuotaFirewall(unittest.TestCase):

    def setUp(self):
        # 다른 테스트의 환경변수 오염 방지
        os.environ.pop("AGY_UNATTENDED_GEMINI", None)

    def test_gemini_blocked_when_unattended(self):
        from quota_firewall import check_model, unattended
        with unattended():
            decision = check_model("gemini-3.8-flash-high", source="test")
        self.assertFalse(decision.allowed)
        self.assertIn("차단", decision.reason)

    def test_gemini_allowed_in_foreground(self):
        from quota_firewall import check_model
        decision = check_model("gemini-3.8-flash-high", source="test")
        self.assertTrue(decision.allowed)

    def test_non_gemini_allowed_when_unattended(self):
        from quota_firewall import check_model, unattended
        with unattended():
            decision = check_model("gpt-oss-120b-medium", source="test")
        self.assertTrue(decision.allowed)

    def test_env_override_unblocks(self):
        from quota_firewall import check_model, unattended
        with patch.dict(os.environ, {"AGY_UNATTENDED_GEMINI": "allow"}):
            with unattended():
                decision = check_model("gemini-3.1-pro-high", source="test")
        self.assertTrue(decision.allowed)

    def test_context_restores_after_exit(self):
        from quota_firewall import is_unattended, unattended
        self.assertFalse(is_unattended())
        with unattended():
            self.assertTrue(is_unattended())
        self.assertFalse(is_unattended())

    def test_audit_log_written(self):
        from quota_firewall import check_model, unattended
        import quota_firewall
        with patch.object(quota_firewall, "_AUDIT_PATH", os.path.join("/tmp/opencode", "quota_audit_test.jsonl")):
            with unattended():
                check_model("gemini-3.8-flash-high", source="test-audit")
            self.assertTrue(os.path.exists("/tmp/opencode/quota_audit_test.jsonl"))


class TestModelRegistryNoCliSpawn(unittest.TestCase):
    """모델 카탈로그 자동 갱신 경로에서 `agy models`(언어서버 스폰)가 실행되지 않아야 한다."""

    def test_fetch_models_skips_agy_cli_by_default(self):
        os.environ.pop("AGY_ALLOW_CLI_CATALOG", None)
        from model_registry import AntigravityModelRegistry
        AntigravityModelRegistry._cached_models = []
        AntigravityModelRegistry._last_fetch_time = 0.0
        with patch.object(AntigravityModelRegistry, "fetch_agy_cli_models") as mock_cli, \
             patch.object(AntigravityModelRegistry, "fetch_quota_models", return_value=[]):
            AntigravityModelRegistry.fetch_models(force_refresh=True)
            mock_cli.assert_not_called()

    def test_fetch_models_uses_cli_only_when_explicitly_allowed(self):
        from model_registry import AntigravityModelRegistry
        AntigravityModelRegistry._cached_models = []
        AntigravityModelRegistry._last_fetch_time = 0.0
        with patch.dict(os.environ, {"AGY_ALLOW_CLI_CATALOG": "1"}), \
             patch.object(AntigravityModelRegistry, "fetch_agy_cli_models", return_value=[]) as mock_cli, \
             patch.object(AntigravityModelRegistry, "fetch_quota_models", return_value=[]):
            AntigravityModelRegistry.fetch_models(force_refresh=True)
            mock_cli.assert_called()


if __name__ == "__main__":
    os.environ.setdefault("UNITTEST_RUNNING", "1")
    unittest.main()
