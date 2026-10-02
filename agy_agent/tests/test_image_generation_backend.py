import os
import sys
import base64
import io
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from image_generation_backend import ImageGenerationBackend
from image_creator_hub import ImageCreatorHub


def _tiny_png_b64() -> str:
    from PIL import Image
    import random
    random.seed(1)
    im = Image.new("RGB", (128, 128))
    im.putdata([(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(128 * 128)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class TestImageGenerationBackend(unittest.TestCase):
    """실제 AI 이미지 백엔드 파싱/저장/정직성 검증 (네트워크 비의존)."""

    def test_extract_image_from_openrouter_data_url(self):
        b64 = _tiny_png_b64()
        data = {
            "choices": [{
                "message": {
                    "content": "done",
                    "images": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}],
                }
            }]
        }
        img, mime, _ = ImageGenerationBackend._extract_image_from_openrouter(data)
        self.assertIsNotNone(img)
        self.assertEqual(img[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(mime, "image/png")

    def test_extract_image_from_openai_b64(self):
        b64 = _tiny_png_b64()
        data = {"data": [{"b64_json": b64}]}
        img, mime = ImageGenerationBackend._extract_image_from_openai(data)
        self.assertIsNotNone(img)
        self.assertEqual(mime, "image/png")

    def test_extract_returns_none_without_image(self):
        self.assertEqual(ImageGenerationBackend._extract_image_from_openrouter({"choices": [{"message": {"content": "hi"}}]}), (None, "", ""))
        self.assertEqual(ImageGenerationBackend._extract_image_from_openai({"data": []}), (None, ""))

    def test_save_bytes_normalizes_png(self):
        import tempfile
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (32, 48), (200, 30, 30)).save(buf, "JPEG")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "sub", "asset.png")
            ok = ImageGenerationBackend._save_bytes(buf.getvalue(), out, "image/jpeg")
            self.assertTrue(ok)
            self.assertTrue(os.path.isfile(out))
            with Image.open(out) as im:
                self.assertEqual(im.size, (32, 48))

    def test_humanize_error_credit(self):
        msg = ImageGenerationBackend._humanize_http_error(RuntimeError('HTTP 402: {"error":"insufficient credits"}'))
        self.assertIn("크레딧", msg)

    def test_openai_compat_unconfigured(self):
        os.environ.pop("IMAGE_API_BASE", None)
        os.environ.pop("IMAGE_API_KEY", None)
        os.environ.pop("IMAGE_API_MODEL", None)
        base, key, model = ImageGenerationBackend.openai_compat_config()
        self.assertEqual((base, key, model), ("", "", ""))

    def test_generate_no_backend_reports_honest_error(self):
        """실제 백엔드 미구성 시 도형으로 대체하지 않고 정직한 오류를 반환한다."""
        import tempfile
        orig_key_fn = ImageGenerationBackend._openrouter_key
        orig_or = ImageGenerationBackend.openrouter_available
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "")
        ImageGenerationBackend.openrouter_available = classmethod(lambda cls: False)
        try:
            with tempfile.TemporaryDirectory() as d:
                res = ImageGenerationBackend.generate_image("로고", os.path.join(d, "x.png"))
                self.assertFalse(res.ok)
                self.assertIn("백엔드", res.error)
        finally:
            ImageGenerationBackend._openrouter_key = orig_key_fn
            ImageGenerationBackend.openrouter_available = orig_or

    def test_hub_parse_source_image_edit_field(self):
        reqs = ImageCreatorHub.parse_image_requests(
            '[IMAGE_REQUEST: {"prompt": "더 밝게", "asset_name": "logo", "source_image": "/tmp/logo.png"}]'
        )
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["source_image"], "/tmp/logo.png")

    def test_extract_saved_image_path(self):
        self.assertEqual(
            ImageCreatorHub._extract_saved_image_path("작업 완료\nSAVED_IMAGE: /tmp/a.png"),
            "/tmp/a.png",
        )
        self.assertIsNone(ImageCreatorHub._extract_saved_image_path("이미지 없음"))
        # 상대경로는 오인 방지를 위해 인정하지 않는다
        self.assertIsNone(ImageCreatorHub._extract_saved_image_path("SAVED_IMAGE: rel/a.png"))

    # ------------------------------------------------------------------
    # Google Gemini 네이티브 백엔드 (2차 실제 이미지 백엔드)
    # ------------------------------------------------------------------
    def test_extract_image_from_gemini_native_inline_data(self):
        b64 = _tiny_png_b64()
        data = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "here you go"},
                        {"inlineData": {"mimeType": "image/png", "data": b64}},
                    ]
                }
            }]
        }
        img, mime, text = ImageGenerationBackend._extract_image_from_gemini_native(data)
        self.assertIsNotNone(img)
        self.assertEqual(img[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(mime, "image/png")
        self.assertIn("here", text)
        self.assertEqual(ImageGenerationBackend._extract_image_from_gemini_native({"candidates": []}), (None, "", ""))

    def test_gemini_url_strips_models_prefix(self):
        url = ImageGenerationBackend._gemini_url("models/gemini-2.5-flash-image")
        self.assertTrue(url.endswith("/models/gemini-2.5-flash-image:generateContent"))
        self.assertNotIn("models/models/", url)

    def test_generate_uses_gemini_native_when_openrouter_unavailable(self):
        """OpenRouter 크레딧이 없어도 GEMINI_API_KEY 가 있으면 Gemini 네이티브로 실제 이미지를 생성한다."""
        import tempfile
        b64 = _tiny_png_b64()
        gemini_payload = {
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": b64}}]}}]
        }
        orig_key_fn = ImageGenerationBackend._openrouter_key
        orig_or = ImageGenerationBackend.openrouter_available
        orig_post = ImageGenerationBackend._post_json_plain
        prev_gemini = os.environ.get("GEMINI_API_KEY")
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "")
        ImageGenerationBackend.openrouter_available = classmethod(lambda cls: False)
        ImageGenerationBackend._post_json_plain = classmethod(lambda cls, url, payload: gemini_payload)
        os.environ["GEMINI_API_KEY"] = "test-gemini-key-1234567890"
        try:
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "nano.png")
                res = ImageGenerationBackend.generate_image("우주 로고", out)
                self.assertTrue(res.ok, res.error)
                self.assertEqual(res.provider, "gemini_native")
                self.assertTrue(os.path.isfile(out))
                self.assertGreater(res.image_bytes, 100)
        finally:
            ImageGenerationBackend._openrouter_key = orig_key_fn
            ImageGenerationBackend.openrouter_available = orig_or
            ImageGenerationBackend._post_json_plain = orig_post
            if prev_gemini is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = prev_gemini

    def test_edit_uses_gemini_native_when_openrouter_unavailable(self):
        """이미지 '수정'도 OpenRouter 불가 시 Gemini 네이티브로 원본+프롬프트 편집을 수행한다."""
        import tempfile
        gemini_payload = {
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": _tiny_png_b64()}}]}}]
        }
        orig_key_fn = ImageGenerationBackend._openrouter_key
        orig_or = ImageGenerationBackend.openrouter_available
        orig_post = ImageGenerationBackend._post_json_plain
        prev_gemini = os.environ.get("GEMINI_API_KEY")
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "")
        ImageGenerationBackend.openrouter_available = classmethod(lambda cls: False)
        ImageGenerationBackend._post_json_plain = classmethod(lambda cls, url, payload: gemini_payload)
        os.environ["GEMINI_API_KEY"] = "test-gemini-key-1234567890"
        try:
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, "src.png")
                with open(src, "wb") as f:
                    f.write(base64.b64decode(_tiny_png_b64()))
                out = os.path.join(d, "edited.png")
                res = ImageGenerationBackend.edit_image("배경을 파란색으로", src, out)
                self.assertTrue(res.ok, res.error)
                self.assertEqual(res.provider, "gemini_native")
                self.assertTrue(os.path.isfile(out))
        finally:
            ImageGenerationBackend._openrouter_key = orig_key_fn
            ImageGenerationBackend.openrouter_available = orig_or
            ImageGenerationBackend._post_json_plain = orig_post
            if prev_gemini is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = prev_gemini

    def test_describe_backends_includes_gemini_native(self):
        prev_gemini = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-gemini-key-1234567890"
        try:
            g_key, g_model = ImageGenerationBackend.gemini_native_config()
            self.assertTrue(g_key)
            self.assertTrue(g_model)
            self.assertIn("Gemini", ImageGenerationBackend.describe_backends())
        finally:
            if prev_gemini is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = prev_gemini

    def test_humanize_error_gemini_quota_and_key(self):
        self.assertIn(
            "쿼터",
            ImageGenerationBackend._humanize_http_error(
                RuntimeError('HTTP 429: {"error":{"status":"RESOURCE_EXHAUSTED"}}')
            ),
        )
        self.assertIn(
            "키",
            ImageGenerationBackend._humanize_http_error(
                RuntimeError('HTTP 400: {"error":{"message":"API key not valid"}}')
            ),
        )


if __name__ == "__main__":
    unittest.main()
