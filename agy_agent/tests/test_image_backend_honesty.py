"""
tests/test_image_backend_honesty.py

🎨 이미지 생성 "정직성" 및 안내 문구 일치 회귀 테스트.

검증 항목:
  1. 사용자 안내 문구(help/!bots/!image/에이전트 지침)가 실제 백엔드와 일치하는가?
     - 실제 백엔드(OpenRouter Nano Banana 계열)를 정확히 기술하는가?
     - 'Antigravity CLI 내장 Nano Banana' 같은 허위 단정 표현이 남아있지 않은가?
  2. 실제 AI 실패 시 조용한 도형 대체가 발생하지 않는가? (allow_procedural=False)
  3. 폴백 발생 시 is_real_ai=False / fallback_used=True 로 정직하게 고지되는가?
  4. 편집(source_image) 경로가 실제 백엔드로 라우팅되는가?
"""

import os
import io
import sys
import asyncio
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from PIL import Image

from image_generation_backend import ImageBackendResult
import image_creator_hub as _hub_mod
from image_creator_hub import ImageCreatorHub

# 실제 허브가 사용하는 백엔드 클래스 바인딩 (모듈 리로드로 인한 바인딩 분리 방어)
def _backend_cls():
    return _hub_mod.ImageGenerationBackend


class TestImageBackendHonesty(unittest.TestCase):

    def setUp(self):
        # 매 테스트 시작 시점의 '원본'을 캡처 (테스트 간 오염/모듈 리로드 방어)
        BC = _backend_cls()
        type(self)._orig_key = BC._openrouter_key
        type(self)._orig_avail = BC.openrouter_available
        type(self)._orig_gen = BC.generate_image
        type(self)._orig_edit = BC.edit_image

    def tearDown(self):
        # 어떤 테스트가 실패하더라도 원본을 반드시 복원
        self._restore_backend()

    def _patch_backend_unavailable(self):
        BC = _backend_cls()
        BC._openrouter_key = classmethod(lambda cls: "")
        BC.openrouter_available = classmethod(lambda cls: False)

    def _restore_backend(self):
        BC = _backend_cls()
        BC._openrouter_key = type(self)._orig_key
        BC.openrouter_available = type(self)._orig_avail
        BC.generate_image = type(self)._orig_gen
        BC.edit_image = type(self)._orig_edit

    # ------------------------------------------------------------------
    # 1. 안내 문구 일치
    # ------------------------------------------------------------------
    def test_help_text_matches_real_backend(self):
        import help_manager
        import config
        # help_manager 가 실제 백엔드 설명 헬퍼를 노출하고, 실제 모듈과 동일한 값을 반환해야 한다.
        self.assertTrue(hasattr(help_manager, "_image_backend_desc"))
        desc = help_manager._image_backend_desc()
        expected = _backend_cls().describe_backends() or "미구성 (OPENROUTER_API_KEY 필요)"
        self.assertEqual(desc, expected)

    def test_no_false_agy_builtin_claims_in_agent_directives(self):
        """에이전트 지침에 'AGY CLI 내장 나노바나나' 확정 표현이 남아있지 않아야 한다."""
        import agent_runner
        text = str(getattr(agent_runner, "AGENT_SYSTEM_PROMPT", "")) + str(getattr(agent_runner, "SYSTEM_PROMPT", ""))
        # 모듈에 상수가 없을 수도 있으므로 소스 파일 직접 스캔
        src_path = os.path.join(WORKSPACE_ROOT, "agent_runner.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        forbidden = [
            "Antigravity CLI 내장 이미지 생성기(Nano Banana)",
            "Antigravity Nano Banana · gemini-3.8-flash-high",
        ]
        for bad in forbidden:
            self.assertNotIn(bad, src, f"허위 단정 표현 잔존: {bad}")

    def test_hub_docstring_no_unconditional_builtin_claim(self):
        src_path = os.path.join(WORKSPACE_ROOT, "image_creator_hub.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        # 실제 백엔드 모듈을 사용해야 한다
        self.assertIn("image_generation_backend", src)
        self.assertIn("ImageGenerationBackend", src)

    # ------------------------------------------------------------------
    # 2~3. 정직성
    # ------------------------------------------------------------------
    def test_failure_does_not_silently_draw(self):
        self._patch_backend_unavailable()
        original_engine = getattr(__import__("config"), "IMAGE_ENGINE", "agy")
        import config
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    res = await ImageCreatorHub.generate("로고", ws, asset_name="x", allow_procedural=False)
                    self.assertFalse(res.ok)
                    self.assertFalse(res.is_real_ai)
                    self.assertFalse(res.fallback_used)
                    self.assertEqual(res.source, "error")
                    # 파일이 조용히 생성되어선 안 된다
                    self.assertFalse(
                        os.path.isfile(os.path.join(ws, "assets", "x.png"))
                    )
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            self._restore_backend()

    def test_procedural_fallback_is_labeled_honestly(self):
        self._patch_backend_unavailable()
        import config
        original_engine = getattr(config, "IMAGE_ENGINE", "agy")
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    res = await ImageCreatorHub.generate("로고", ws, asset_name="x", allow_procedural=True)
                    self.assertTrue(res.ok)
                    self.assertFalse(res.is_real_ai)
                    self.assertTrue(res.fallback_used)
                    self.assertEqual(res.source, "procedural_fallback")
                    self.assertIn("실제 AI 이미지 생성 실패", res.message)
                    guide = ImageCreatorHub.format_results_for_agent([res])
                    self.assertIn("절차적", guide)
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            self._restore_backend()

    # ------------------------------------------------------------------
    # 3-b. 실패 시 '기존 에셋 재사용' (실패 오보고 방지) + 402 안내
    # ------------------------------------------------------------------
    def test_failure_reuses_existing_valid_asset(self):
        """
        실제 AI 재생성은 실패해도, 동일 이름의 유효한 실이미지가 이미 디스크에 존재하면
        실패로 오보고하지 않고 그 에셋을 재사용해야 한다(정직 고지).
        """
        self._patch_backend_unavailable()
        import config
        original_engine = getattr(config, "IMAGE_ENGINE", "agy")
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    target_dir = os.path.join(ws, "assets")
                    os.makedirs(target_dir, exist_ok=True)
                    Image.new("RGB", (256, 256), (30, 90, 200)).save(os.path.join(target_dir, "x.png"))

                    res = await ImageCreatorHub.generate("로고", ws, asset_name="x", allow_procedural=False)
                    self.assertTrue(res.ok, res.message)
                    self.assertTrue(res.is_real_ai)
                    self.assertTrue(res.fallback_used)
                    self.assertEqual(res.source, "existing_local_asset")
                    self.assertIn("재사용", res.message)
                    self.assertEqual(os.path.basename(res.relative_path), "x.png")

                    # Coder 바인딩 지시문에 성공 에셋이 포함되고 실패 라인은 없어야 한다.
                    guide = ImageCreatorHub.format_results_for_agent([res])
                    self.assertIn("x.png", guide)
                    self.assertNotIn("생성 실패 보고", guide)
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            self._restore_backend()

    def test_reused_asset_is_normalized_to_real_png(self):
        """
        `.png` 이름이지만 실제로는 JPEG 바이트인 기존 에셋을 재사용할 때,
        확장자-내용 불일치를 정규화하여 진짜 PNG 로 만들어야 한다.
        """
        self._patch_backend_unavailable()
        import config
        original_engine = getattr(config, "IMAGE_ENGINE", "agy")
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    target_dir = os.path.join(ws, "assets")
                    os.makedirs(target_dir, exist_ok=True)
                    bad = os.path.join(target_dir, "y.png")
                    Image.new("RGB", (200, 200), (180, 60, 20)).save(bad, "JPEG")
                    with open(bad, "rb") as f:
                        self.assertTrue(f.read(3) == b"\xff\xd8\xff", "사전조건: JPEG 바이트")

                    res = await ImageCreatorHub.generate("아이콘", ws, asset_name="y", allow_procedural=False)
                    self.assertTrue(res.ok, res.message)
                    with open(bad, "rb") as f:
                        magic = f.read(8)
                    self.assertEqual(magic, b"\x89PNG\r\n\x1a\n", "재사용 시 진짜 PNG 로 정규화되어야 함")
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            self._restore_backend()

    def test_no_reuse_when_expected_asset_absent(self):

        """디스크에 기존 에셋이 없으면 재사용하지 않고 정직 실패해야 한다."""
        self._patch_backend_unavailable()
        import config
        original_engine = getattr(config, "IMAGE_ENGINE", "agy")
        config.IMAGE_ENGINE = "disabled"
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    res = await ImageCreatorHub.generate("로고", ws, asset_name="absent", allow_procedural=False)
                    self.assertFalse(res.ok)
                    self.assertEqual(res.source, "error")
                    self.assertFalse(res.fallback_used)
            asyncio.run(_run())
        finally:
            config.IMAGE_ENGINE = original_engine
            self._restore_backend()

    def test_openrouter_402_guidance_mentions_backend_env(self):
        """402(크레딧 부족) 안내는 실제 백엔드 환경변수 설정 방법을 명시해야 한다."""
        err = _backend_cls()._humanize_http_error(Exception("HTTP 402 payment required"))
        self.assertIn("OPENROUTER_API_KEY", err)
        self.assertIn("GEMINI_API_KEY", err)

    # ------------------------------------------------------------------
    # 4. 편집 경로 라우팅
    # ------------------------------------------------------------------
    def test_edit_routes_to_edit_backend(self):
        called = {}

        def fake_edit(prompt, source_image_path, output_path, aspect_ratio, model_hint):
            called["prompt"] = prompt
            called["source"] = source_image_path
            buf = io.BytesIO()
            Image.new("RGB", (256, 256), (10, 200, 10)).save(buf, "PNG")
            _backend_cls()._save_bytes(buf.getvalue(), output_path, "image/png")
            return ImageBackendResult(ok=True, provider="openrouter", model="google/gemini-2.5-flash-image",
                                      output_path=output_path, image_bytes=len(buf.getvalue()))

        BC = _backend_cls()
        BC.generate_image = staticmethod(lambda *a, **k: ImageBackendResult(ok=False, error="x"))
        BC.edit_image = staticmethod(fake_edit)
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    src = os.path.join(ws, "orig.png")
                    Image.new("RGB", (256, 256), (200, 0, 0)).save(src)
                    res = await ImageCreatorHub.generate("더 밝게", ws, asset_name="edited", source_image=src)
                    self.assertTrue(res.ok, res.message)
                    self.assertTrue(res.is_real_ai)
                    self.assertIn("원본 편집", res.message)
                    self.assertEqual(called.get("source"), src)
            asyncio.run(_run())
        finally:
            self._restore_backend()


if __name__ == "__main__":
    unittest.main()
