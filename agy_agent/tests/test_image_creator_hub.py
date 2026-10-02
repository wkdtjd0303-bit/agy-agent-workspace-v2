import os
import sys
import asyncio
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from image_creator_hub import ImageCreatorHub, ImageGenerationResult


class TestImageCreatorHub(unittest.TestCase):
    """Image generator Hub (Nano Banana 도구) 파싱/생성/배치 검증."""

    def test_parse_json_format(self):
        reqs = ImageCreatorHub.parse_image_requests(
            '[IMAGE_REQUEST: {"prompt": "우주 테마 로고", "asset_name": "app_logo", "aspect_ratio": "16:9"}]'
        )
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["prompt"], "우주 테마 로고")
        self.assertEqual(reqs[0]["asset_name"], "app_logo")
        self.assertEqual(reqs[0]["aspect_ratio"], "16:9")

    def test_parse_pipe_format(self):
        reqs = ImageCreatorHub.parse_image_requests("[IMAGE_REQUEST: app_logo | 1:1 | 미니멀 로고]")
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["asset_name"], "app_logo")
        self.assertEqual(reqs[0]["aspect_ratio"], "1:1")
        self.assertEqual(reqs[0]["prompt"], "미니멀 로고")

    def test_parse_free_and_bracket_format(self):
        reqs = ImageCreatorHub.parse_image_requests(
            "필요한 이미지:\n[IMAGE_REQUEST: 귀여운 고양이]\n[IMAGE_REQUEST] 달빛 배경"
        )
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0]["prompt"], "귀여운 고양이")
        self.assertEqual(reqs[1]["prompt"], "달빛 배경")

    def test_parse_empty(self):
        self.assertEqual(ImageCreatorHub.parse_image_requests("이미지 요청 없음"), [])
        self.assertEqual(ImageCreatorHub.parse_image_requests(""), [])

    def test_generate_fallback_places_file(self):
        """실제 AI 백엔드가 불가할 때, '명시 허용(allow_procedural)' 시에만 절차적 임시 에셋을 사용하고 정직히 고지한다."""
        original_engine = getattr(config, "IMAGE_ENGINE", "agy")

        # 실제 AI 백엔드가 사용 불가하도록 키를 비운다.
        from image_generation_backend import ImageGenerationBackend
        orig_key_fn = ImageGenerationBackend._openrouter_key
        orig_or_avail = ImageGenerationBackend.openrouter_available
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "")
        ImageGenerationBackend.openrouter_available = classmethod(lambda cls: False)
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    res = await ImageCreatorHub.generate(
                        "우주 테마 앱 로고", ws, asset_name="app_logo",
                        aspect_ratio="16:9", allow_procedural=True,
                    )
                    self.assertTrue(res.ok, res.message)
                    self.assertTrue(os.path.isfile(res.absolute_path))
                    self.assertEqual(os.path.basename(res.absolute_path), "app_logo.png")
                    self.assertEqual((res.width, res.height), (800, 450))
                    # 정직성: 절차적 대체임을 명확히 표기
                    self.assertFalse(res.is_real_ai)
                    self.assertTrue(res.fallback_used)
                    self.assertEqual(res.source, "procedural_fallback")
                    guide = ImageCreatorHub.format_results_for_agent([res])
                    self.assertIn("app_logo.png", guide)
                    self.assertIn("바인딩", guide)
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            ImageGenerationBackend._openrouter_key = orig_key_fn
            ImageGenerationBackend.openrouter_available = orig_or_avail

    def test_generate_without_allow_procedural_never_silently_draws(self):
        """실제 AI 실패 + allow_procedural=False 이면 조용한 도형 렌더 없이 정직하게 실패한다."""
        from image_generation_backend import ImageGenerationBackend
        orig_key_fn = ImageGenerationBackend._openrouter_key
        orig_or_avail = ImageGenerationBackend.openrouter_available
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "")
        ImageGenerationBackend.openrouter_available = classmethod(lambda cls: False)
        original_engine = getattr(config, "IMAGE_ENGINE", "agy")
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    res = await ImageCreatorHub.generate("우주 로고", ws, asset_name="app_logo")
                    self.assertFalse(res.ok)
                    self.assertFalse(res.is_real_ai)
                    self.assertIn("실패", res.message)
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            ImageGenerationBackend._openrouter_key = orig_key_fn
            ImageGenerationBackend.openrouter_available = orig_or_avail

    def test_generate_empty_prompt_fails_gracefully(self):
        async def _run():
            with tempfile.TemporaryDirectory() as ws:
                res = await ImageCreatorHub.generate("", ws)
                self.assertFalse(res.ok)
        asyncio.run(_run())

    def test_format_results_reports_failures_honestly(self):
        """전량 실패 시에도 Coder 에게 실패를 정직하게 보고하여 '유령 참조'를 차단한다."""
        failed = ImageGenerationResult(
            ok=False,
            asset_name="hero_bg",
            prompt="영웅 배경 일러스트",
            source="error",
            message="실제 AI 이미지 생성에 실패했습니다. 사유: OpenRouter 크레딧 부족(402)",
        )
        guide = ImageCreatorHub.format_results_for_agent([failed])
        self.assertIn("hero_bg", guide)
        self.assertIn("실패", guide)
        self.assertIn("절대 참조하지 마", guide)

        summary = ImageCreatorHub.summarize_failures([failed])
        self.assertIn("hero_bg", summary)
        self.assertIn("실패", summary)

        # include_failures=False 로 명시하면 기존 동작(빈 문자열)을 유지한다.
        self.assertEqual(
            ImageCreatorHub.format_results_for_agent([failed], include_failures=False), ""
        )
        # 성공/실패가 섞여 있으면 둘 다 안내한다.
        ok = ImageGenerationResult(
            ok=True, asset_name="app_logo", prompt="우주 로고",
            relative_path="assets/app_logo.png",
            width=512, height=512, source="ai_backend", backend="openrouter",
            model="google/gemini-2.5-flash-image", is_real_ai=True,
        )
        mixed = ImageCreatorHub.format_results_for_agent([ok, failed])
        self.assertIn("app_logo.png", mixed)
        self.assertIn("hero_bg", mixed)
        self.assertIn("실패", mixed)

    def test_summarize_failures_empty_when_all_ok(self):
        ok = ImageGenerationResult(
            ok=True, asset_name="a", prompt="a",
            relative_path="assets/a.png", width=1, height=1,
        )
        self.assertEqual(ImageCreatorHub.summarize_failures([ok]), "")
        self.assertEqual(ImageCreatorHub.summarize_failures([]), "")


if __name__ == "__main__":
    unittest.main()
