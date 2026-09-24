#!/usr/bin/env python3
"""
🧭 엔진/모델 정합성 가드(Engine-Model Coherence Guard) 회귀 테스트

배경: `!model` UI/명령으로 'OPENCODE 엔진 + gemini-3.8-flash-high' 같은
실행 불가능한 조합이 저장되면, 표시가 잘못되고 폴백 경로에서 엉뚱한 엔진/쿼터를
건드릴 수 있다. 저장 시점(set_role_engine/set_role_model)과 로드 시점(initialize)
모두에서 자동 교정됨을 검증한다.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import model_manager
from model_manager import GlobalModelManager


class TestCoherenceHelpers(unittest.TestCase):

    def test_model_owner_engine_classification(self):
        self.assertEqual(GlobalModelManager.model_owner_engine("gemini-3.8-flash-high"), "agy")
        self.assertEqual(GlobalModelManager.model_owner_engine("claude-opus-4-6-thinking"), "agy")
        self.assertEqual(GlobalModelManager.model_owner_engine("gpt-oss-120b-medium"), "agy")
        self.assertEqual(GlobalModelManager.model_owner_engine("opencode-go/deepseek-v4.1-flash"), "opencode")
        self.assertEqual(GlobalModelManager.model_owner_engine("upstage/solar-pro4"), "hermes")
        self.assertIsNone(GlobalModelManager.model_owner_engine(""))

    def test_engine_model_compatibility(self):
        self.assertFalse(GlobalModelManager.is_model_compatible_with_engine("opencode", "gemini-3.8-flash-high"))
        self.assertTrue(GlobalModelManager.is_model_compatible_with_engine("opencode", "opencode-go/deepseek-v4.1-flash"))
        self.assertTrue(GlobalModelManager.is_model_compatible_with_engine("agy", "gemini-3.8-flash-high"))
        self.assertFalse(GlobalModelManager.is_model_compatible_with_engine("agy", "opencode-go/deepseek-v4.1-flash"))
        self.assertTrue(GlobalModelManager.is_model_compatible_with_engine("hermes", "upstage/solar-pro4"))
        self.assertFalse(GlobalModelManager.is_model_compatible_with_engine("hermes", "gemini-3.8-flash-high"))
        # 미지정 엔진(codex 등)은 강제하지 않음
        self.assertTrue(GlobalModelManager.is_model_compatible_with_engine("codex", "gemini-3.8-flash-high"))


class TestCrossEngineAliasResolution(unittest.TestCase):
    """엔진에 없는 모델 별칭을 입력해도 유효한 모델 ID로 해석되어야 한다."""

    def test_opencode_engine_resolves_gemini_alias(self):
        resolved = GlobalModelManager.resolve_model_id("opencode", "gemini 3.8")
        self.assertEqual(resolved, "gemini-3.8-flash-high")

    def test_agy_engine_resolves_hermes_alias(self):
        resolved = GlobalModelManager.resolve_model_id("agy", "deepseek")
        self.assertTrue("/" in resolved)

    def test_unknown_input_is_returned_as_is(self):
        self.assertEqual(GlobalModelManager.resolve_model_id("agy", "some-custom-model"), "some-custom-model")


class TestSetRoleEngineRepairsModel(unittest.TestCase):
    """엔진 변경 시 지원 불가 모델은 새 엔진 기본 모델로 자동 교체되어야 한다."""

    def setUp(self):
        self._orig_roles = GlobalModelManager._roles
        self._orig_init = GlobalModelManager._initialized
        GlobalModelManager._roles = {
            "coder": {"engine": "agy", "model": "gemini-3.8-flash-high", "name": "Coder"},
        }
        GlobalModelManager._initialized = True

    def tearDown(self):
        GlobalModelManager._roles = self._orig_roles
        GlobalModelManager._initialized = self._orig_init

    def test_switch_to_opencode_replaces_gemini_model(self):
        with patch.object(GlobalModelManager, "save", return_value=None):
            ok = GlobalModelManager.set_role_engine("coder", "opencode")
        self.assertTrue(ok)
        cfg = GlobalModelManager._roles["coder"]
        self.assertEqual(cfg["engine"], "opencode")
        self.assertNotIn("gemini", cfg["model"].lower())
        self.assertIn("/", cfg["model"])

    def test_compatible_model_is_kept(self):
        GlobalModelManager._roles["coder"]["model"] = "opencode-go/deepseek-v4.1-flash"
        GlobalModelManager._roles["coder"]["engine"] = "agy"
        with patch.object(GlobalModelManager, "save", return_value=None):
            GlobalModelManager.set_role_engine("coder", "opencode")
        self.assertEqual(GlobalModelManager._roles["coder"]["model"], "opencode-go/deepseek-v4.1-flash")


class TestSetRoleModelHarmonizesEngine(unittest.TestCase):
    """모델 선택 시 실행 불가능한 엔진이면 모델 소속 엔진으로 자동 정합화되어야 한다."""

    def setUp(self):
        self._orig_roles = GlobalModelManager._roles
        self._orig_init = GlobalModelManager._initialized
        GlobalModelManager._roles = {
            "coder": {"engine": "opencode", "model": "opencode-go/deepseek-v4.1-flash", "name": "Coder"},
        }
        GlobalModelManager._initialized = True

    def tearDown(self):
        GlobalModelManager._roles = self._orig_roles
        GlobalModelManager._initialized = self._orig_init

    def test_selecting_gemini_switches_engine_to_agy(self):
        with patch.object(GlobalModelManager, "save", return_value=None):
            ok = GlobalModelManager.set_role_model("coder", "gemini-3.8-flash-high")
        self.assertTrue(ok)
        cfg = GlobalModelManager._roles["coder"]
        self.assertEqual(cfg["engine"], "agy")
        self.assertEqual(cfg["model"], "gemini-3.8-flash-high")

    def test_selecting_opencode_model_keeps_opencode_engine(self):
        with patch.object(GlobalModelManager, "save", return_value=None):
            GlobalModelManager.set_role_model("coder", "opencode-go/deepseek-v4-pro")
        cfg = GlobalModelManager._roles["coder"]
        self.assertEqual(cfg["engine"], "opencode")
        self.assertEqual(cfg["model"], "opencode-go/deepseek-v4-pro")


class TestInitializeRepairsLegacyState(unittest.TestCase):
    """디스크에 저장된 비정합 상태(opencode + gemini)는 로드 시 자동 교정되어야 한다."""

    def test_initialize_repairs_incoherent_state(self):
        bad_state = {
            "coder": {
                "name": "Coder (개발)", "role_key": "coder", "icon": "👨‍💻", "enabled": True,
                "engine": "opencode", "model": "gemini-3.8-flash-high",
                "env_model_key": "CODER_MODEL", "env_engine_key": "DEFAULT_AI_ENGINE",
                "persona": "general", "description": "test",
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(bad_state, f)
            tmp_path = f.name
        try:
            orig_roles = GlobalModelManager._roles
            orig_init = GlobalModelManager._initialized
            GlobalModelManager._initialized = False
            try:
                with patch.object(model_manager, "CONFIG_FILE", __import__("pathlib").Path(tmp_path)), \
                     patch.object(GlobalModelManager, "save", return_value=None), \
                     patch.object(GlobalModelManager, "sync_to_runtime_config", return_value=None):
                    GlobalModelManager.initialize()
                cfg = GlobalModelManager._roles["coder"]
                self.assertEqual(cfg["engine"], "opencode")
                self.assertNotIn("gemini", cfg["model"].lower())
                self.assertIn("/", cfg["model"])
            finally:
                GlobalModelManager._roles = orig_roles
                GlobalModelManager._initialized = orig_init
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    os.environ.setdefault("UNITTEST_RUNNING", "1")
    unittest.main()
