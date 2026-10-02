import os
import sys
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from image_creator_hub import ImageCreatorHub
from image_generation_backend import ImageGenerationBackend


class TestImageRoleModelSelection(unittest.TestCase):
    """
    Image generator 역할에 배정된 엔진/모델(`!model` 선택 또는 .env)이
    실제 이미지 백엔드 호출에 **실반영**되는지 검증한다.

    배경(실버그): 기존에는 config.IMAGE_MODEL(기본 'gemini-3.8-flash-high')이
    백엔드에 model_hint 로 전달되어도 '/' 가 없어 무시되고, 항상 기본 체인으로
    떨어졌다. 즉 `!model` 에서 이미지 모델을 바꿔도 실제 생성에는 반영되지 않았다.
    """

    def test_agy_label_maps_to_real_backend_model(self):
        """agy 라벨(예: gemini-3.8-flash-high)은 실제 Nano Banana 모델 ID 로 해석된다."""
        hint = ImageCreatorHub.resolve_backend_model_hint("agy", "gemini-3.8-flash-high")
        self.assertTrue(hint, "agy 기본 라벨이 해석되지 않았습니다.")
        self.assertIn("/", hint)
        # 해석된 힌트가 백엔드 후보 체인의 최우선 순위로 실제 반영된다.
        chain = ImageGenerationBackend._candidate_models(hint)
        self.assertEqual(chain[0], hint)

    def test_openrouter_id_is_used_as_is(self):
        """provider/model 형식은 그대로 사용된다."""
        hint = ImageCreatorHub.resolve_backend_model_hint(
            "hermes", "google/gemini-3-pro-image"
        )
        self.assertEqual(hint, "google/gemini-3-pro-image")

    def test_unknown_label_falls_back_to_default_chain(self):
        """알 수 없는 라벨은 잘못된 ID 로 호출하지 않도록 빈 힌트(=기본 체인)로 강등한다."""
        self.assertEqual(ImageCreatorHub.resolve_backend_model_hint("agy", "알수없는모델"), "")
        self.assertEqual(ImageCreatorHub.resolve_backend_model_hint("agy", ""), "")

    def test_resolver_prefers_runtime_config(self):
        """`!model` 동기화가 반영된 config 값을 최우선으로 해석한다."""
        orig_model = getattr(config, "IMAGE_MODEL", None)
        orig_engine = getattr(config, "IMAGE_ENGINE", None)
        try:
            config.IMAGE_ENGINE = "hermes"
            config.IMAGE_MODEL = "google/gemini-3.1-flash-image"
            engine, model = ImageCreatorHub.resolve_role_engine_model()
            self.assertEqual(engine, "hermes")
            self.assertEqual(model, "google/gemini-3.1-flash-image")
        finally:
            if orig_model is not None:
                config.IMAGE_MODEL = orig_model
            if orig_engine is not None:
                config.IMAGE_ENGINE = orig_engine

    def test_image_preset_catalog_contains_real_image_models(self):
        """`!model` Image generator 드롭다운에 실제 이미지 모델 프리셋이 노출된다."""
        from model_manager import IMAGE_MODEL_PRESETS

        ids = [mid for mid, _label in IMAGE_MODEL_PRESETS]
        self.assertIn("google/gemini-2.5-flash-image", ids)
        self.assertIn("gemini-3.8-flash-high", ids)  # AGY CLI 기본값(별칭)
        # 모든 프리셋은 해석 가능해야 한다(빈 힌트로 무시되지 않음).
        for mid in ids:
            hint = ImageCreatorHub.resolve_backend_model_hint("agy", mid)
            self.assertTrue(hint, f"프리셋 '{mid}' 이 백엔드 모델로 해석되지 않습니다.")


if __name__ == "__main__":
    unittest.main()
