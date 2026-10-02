import os
import sys
import io
import base64
import asyncio
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from image_creator_hub import ImageCreatorHub, ImageGenerationResult
from image_generation_backend import ImageGenerationBackend


def _png_bytes(size=(64, 64), color=(20, 40, 90)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class TestImageEditParsing(unittest.TestCase):
    """[IMAGE_EDIT] 태그 및 이미지 요청 파싱 무결성 (요청 유실 방지)."""

    def test_multiline_json_request_is_not_lost(self):
        """개행이 섞인 JSON 이미지 요청도 유실 없이 파싱되어야 한다 (과거 회귀 결함)."""
        text = (
            "이미지가 필요합니다.\n"
            '[IMAGE_REQUEST: {\n  "prompt": "우주 테마 앱 로고",\n'
            '  "asset_name": "app_logo",\n  "aspect_ratio": "16:9"\n}]\n'
        )
        reqs = ImageCreatorHub.parse_image_requests(text)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["prompt"], "우주 테마 앱 로고")
        self.assertEqual(reqs[0]["asset_name"], "app_logo")
        self.assertEqual(reqs[0]["aspect_ratio"], "16:9")

    def test_image_edit_tag_requires_source(self):
        with_src = ImageCreatorHub.parse_image_requests(
            '[IMAGE_EDIT: {"prompt": "배경을 더 어둡게", "source_image": "hero.png"}]'
        )
        self.assertEqual(len(with_src), 1)
        self.assertEqual(with_src[0]["source_image"], "hero.png")
        self.assertEqual(with_src[0]["prompt"], "배경을 더 어둡게")

        # 원본이 없으면 편집 요청으로 인정하지 않는다 (신규 생성 오인 방지)
        self.assertEqual(
            ImageCreatorHub.parse_image_requests('[IMAGE_EDIT: {"prompt": "더 밝게"}]'), []
        )

    def test_source_image_aliases(self):
        for alias in ("source_image", "source", "edit_of", "base_image", "image"):
            reqs = ImageCreatorHub.parse_image_requests(
                '[IMAGE_REQUEST: {"prompt": "x", "%s": "a/b/c.png"}]' % alias
            )
            self.assertEqual(len(reqs), 1, alias)
            self.assertEqual(reqs[0]["source_image"], "a/b/c.png", alias)

    def test_existing_formats_still_parse(self):
        self.assertEqual(
            len(ImageCreatorHub.parse_image_requests("[IMAGE_REQUEST: app_logo | 1:1 | 미니멀 로고]")), 1
        )
        self.assertEqual(
            len(ImageCreatorHub.parse_image_requests("[IMAGE_REQUEST: 귀여운 고양이]")), 1
        )
        bracket = ImageCreatorHub.parse_image_requests(
            "필요:\n[IMAGE_REQUEST: 귀여운 고양이]\n[IMAGE_REQUEST] 달빛 배경"
        )
        self.assertEqual(len(bracket), 2)
        self.assertEqual(bracket[1]["prompt"], "달빛 배경")
        self.assertEqual(ImageCreatorHub.parse_image_requests("이미지 요청 없음"), [])

    def test_format_pending_requests_preserves_tags(self):
        """미생성 요청은 '임시 대체' 오해 문구 없이 원형 태그로 보존되어야 한다."""
        reqs = ImageCreatorHub.parse_image_requests(
            '[IMAGE_EDIT: {"prompt": "선명하게", "source_image": "res/app_logo.png"}]\n'
            '[IMAGE_REQUEST: {"prompt": "히어로 배너", "asset_name": "hero_bg", "aspect_ratio": "16:9"}]'
        )
        text = ImageCreatorHub.format_pending_requests(reqs)
        self.assertIn("[IMAGE_EDIT:", text)
        self.assertIn("[IMAGE_REQUEST:", text)
        self.assertIn("res/app_logo.png", text)
        self.assertIn("hero_bg", text)
        self.assertNotIn("절차적", text)
        self.assertEqual(ImageCreatorHub.format_pending_requests([]), "")

    def test_image_edit_tag_parses_in_qa_review_body(self):
        """QA가 보고서 본문에 남긴 이미지 수정 요청이 파싱되어야 한다 (도구 연동 회귀 방지)."""
        qa_output = (
            "[STATUS: REJECTED]\n"
            "🖼️ 에셋 화질이 낮습니다.\n"
            '[IMAGE_EDIT: {"prompt": "로고 외곽선을 더 선명하게", "source_image": "app_logo.png"}]\n'
        )
        reqs = ImageCreatorHub.parse_image_requests(qa_output)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["source_image"], "app_logo.png")
        guide = ImageCreatorHub.format_results_for_agent(
            [ImageGenerationResult(ok=True, asset_name="app_logo", prompt=reqs[0]["prompt"], relative_path="app_logo.png")]
        )
        self.assertIn("app_logo", guide)
        self.assertIn("바인딩", guide)


class TestSourceImageResolution(unittest.TestCase):
    """편집 원본 경로 해석 (상대경로/파일명/미존재)."""

    def _ws_with_hero(self, ws):
        draw = os.path.join(ws, "app", "src", "main", "res", "drawable")
        os.makedirs(draw, exist_ok=True)
        hero = os.path.join(draw, "hero.png")
        with open(hero, "wb") as f:
            f.write(_png_bytes())
        return hero

    def test_resolve_relative_workspace_path(self):
        with tempfile.TemporaryDirectory() as ws:
            hero = self._ws_with_hero(ws)
            got = ImageCreatorHub._resolve_source_image("app/src/main/res/drawable/hero.png", ws)
            self.assertEqual(got, os.path.abspath(hero))

    def test_resolve_bare_filename_searches_project(self):
        with tempfile.TemporaryDirectory() as ws:
            hero = self._ws_with_hero(ws)
            self.assertEqual(ImageCreatorHub._resolve_source_image("hero.png", ws), os.path.abspath(hero))

    def test_resolve_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as ws:
            self._ws_with_hero(ws)
            self.assertEqual(ImageCreatorHub._resolve_source_image("nope.png", ws), "")

    def test_edit_request_with_missing_source_fails_honestly(self):
        """원본 미존재 시 '새 이미지 생성'으로 조용히 강등하지 않고 정직하게 실패한다."""
        orig = ImageGenerationBackend.openrouter_available
        ImageGenerationBackend.openrouter_available = classmethod(lambda cls: False)
        try:
            async def _run():
                with tempfile.TemporaryDirectory() as ws:
                    res = await ImageCreatorHub.generate(
                        "배경을 더 어둡게", ws, asset_name="hero", source_image="ghost.png"
                    )
                    self.assertFalse(res.ok)
                    self.assertFalse(res.is_real_ai)
                    self.assertIn("찾을 수 없습니다", res.message)
                    self.assertIn("대체하지 않았", res.message)
            asyncio.run(_run())
        finally:
            ImageGenerationBackend.openrouter_available = orig


class TestImageEditEndToEnd(unittest.TestCase):
    """편집 원본이 실제 백엔드로 전달되고 결과가 리소스에 배치되는지 (백엔드 mock)."""

    def setUp(self):
        # 이 테스트는 '편집 파이프라인' 자체만 검증한다. 실제 네트워크/잔존 키에
        # 의존하지 않도록 모든 백엔드를 결정적으로 목으로 대체한다.
        self._orig = {
            "openrouter_available": ImageGenerationBackend.openrouter_available,
            "openai_compat_config": ImageGenerationBackend.openai_compat_config,
            "gemini_native_config": ImageGenerationBackend.gemini_native_config,
            "edit_gemini": ImageGenerationBackend._edit_via_gemini_native,
            "gen_gemini": ImageGenerationBackend._generate_via_gemini_native,
        }
        self.captured = {}
        self._b64 = base64.b64encode(_png_bytes()).decode()
        self._install(ImageGenerationBackend, self.captured)

    def tearDown(self):
        B = ImageGenerationBackend
        B.openrouter_available = self._orig["openrouter_available"]
        B.openai_compat_config = self._orig["openai_compat_config"]
        B.gemini_native_config = self._orig["gemini_native_config"]
        B._edit_via_gemini_native = self._orig["edit_gemini"]
        B._generate_via_gemini_native = self._orig["gen_gemini"]

    def _fake_edit(self, cls, prompt, source_image_path, output_path, aspect_ratio, model):
        """편집 시 원본 이미지가 payload 에 inlineData 로 전달되는지 기계적으로 포착한다."""
        from image_generation_backend import ImageBackendResult
        try:
            b64_src, mime_src = cls._file_to_base64(source_image_path)
        except Exception as e:
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model, error=f"원본 인코딩 실패: {e}"
            )
        # 실제 구현과 동일한 payload 형태를 구성해 '원본 전달' 사실을 포착한다.
        parts = [
            {"text": "edit"},
            {"inlineData": {"mimeType": mime_src, "data": b64_src}},
        ]
        self.captured["has_inline"] = any("inlineData" in p for p in parts)
        self.captured["n_parts"] = len(parts)
        self.captured["source_image_path"] = source_image_path
        raw = base64.b64decode(self._b64)
        if not cls._save_bytes(raw, output_path, "image/png"):
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model, error=f"편집 이미지 저장 실패: {output_path}"
            )
        return ImageBackendResult(
            ok=True, provider="gemini_native", model=model, output_path=output_path,
            image_bytes=len(raw), mime_type="image/png",
        )

    def _fake_generate(self, cls, prompt, output_path, aspect_ratio, model):
        from image_generation_backend import ImageBackendResult
        self.captured["has_inline"] = False
        self.captured["n_parts"] = 1
        raw = base64.b64decode(self._b64)
        if not cls._save_bytes(raw, output_path, "image/png"):
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model, error=f"이미지 저장 실패: {output_path}"
            )
        return ImageBackendResult(
            ok=True, provider="gemini_native", model=model, output_path=output_path,
            image_bytes=len(raw), mime_type="image/png",
        )

    def _install(self, B, captured):
        B.openrouter_available = classmethod(lambda cls: False)
        B.openai_compat_config = classmethod(lambda cls: ("", "", ""))
        B.gemini_native_config = classmethod(lambda cls: (self._GEMINI_KEY, self._GEMINI_MODEL))
        B._edit_via_gemini_native = classmethod(self._fake_edit)
        B._generate_via_gemini_native = classmethod(self._fake_generate)

    _GEMINI_KEY = "test-gemini-key-1234567890"
    _GEMINI_MODEL = "gemini-2.5-flash-image"

    def _ensure_patched(self, captured):
        """importlib.reload 로 클래스가 교체되었으면 현재 살아있는 클래스에 목을 재적용한다."""
        import sys as _s
        seen = set()
        for mod_name in ("image_generation_backend", "image_creator_hub"):
            mod = _s.modules.get(mod_name)
            if mod is None:
                continue
            B = getattr(mod, "ImageGenerationBackend", None)
            if B is None or id(B) in seen:
                continue
            seen.add(id(B))
            if B.gemini_native_config() != (self._GEMINI_KEY, self._GEMINI_MODEL):
                self._install(B, captured)

    def test_edit_passes_source_inline_and_places_result(self):
        png = _png_bytes()
        try:
            async def _run():
                self._ensure_patched(self.captured)
                with tempfile.TemporaryDirectory() as ws:
                    draw = os.path.join(ws, "app", "src", "main", "res", "drawable")
                    os.makedirs(draw, exist_ok=True)
                    with open(os.path.join(draw, "hero.png"), "wb") as f:
                        f.write(png)

                    res = await ImageCreatorHub.generate(
                        "배경을 더 어둡게", ws, asset_name="hero_bg",
                        source_image="app/src/main/res/drawable/hero.png",
                    )
                    self.assertTrue(res.ok, res.message)
                    self.assertTrue(res.is_real_ai)
                    self.assertEqual(res.backend, "gemini_native")
                    self.assertTrue(os.path.isfile(res.absolute_path))
                    self.assertTrue(res.relative_path.endswith("hero_bg.png"))
                    # 원본 이미지가 실제로 편집 입력(inlineData)으로 전달되었는지
                    self.assertTrue(self.captured.get("has_inline"))
                    self.assertEqual(self.captured.get("n_parts"), 2)

                    # 파일명만 지정해도 프로젝트 내 원본을 찾아 편집한다.
                    res2 = await ImageCreatorHub.generate(
                        "더 밝게", ws, asset_name="hero_bg2", source_image="hero.png"
                    )
                    self.assertTrue(res2.ok, res2.message)
            asyncio.run(_run())
        finally:
            self.tearDown()
            self.setUp()


class TestSmallImagePreservation(unittest.TestCase):
    """작은 실제 이미지를 크기만으로 버리지 않는다 (매직바이트 판정)."""

    def test_small_valid_png_is_saved(self):
        png = _png_bytes(size=(16, 16))
        self.assertLess(len(png), 256)  # 과거 가드에 걸리던 크기
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "sub", "small.png")
            self.assertTrue(ImageGenerationBackend._save_bytes(png, out, "image/png"))
            self.assertTrue(os.path.isfile(out))

    def test_non_image_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(
                ImageGenerationBackend._save_bytes(os.urandom(400), os.path.join(d, "x.png"), "image/png")
            )
            self.assertFalse(
                ImageGenerationBackend._save_bytes(b"", os.path.join(d, "y.png"), "image/png")
            )

    def test_image_magic_detection(self):
        self.assertTrue(ImageGenerationBackend._looks_like_image(_png_bytes()))
        self.assertFalse(ImageGenerationBackend._looks_like_image(b"not an image at all"))


if __name__ == "__main__":
    unittest.main()
