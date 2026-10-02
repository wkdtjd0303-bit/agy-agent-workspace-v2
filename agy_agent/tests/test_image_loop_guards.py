"""
tests/test_image_loop_guards.py

🎨 이미지 생성 루프 '불필요 실행/토큰 낭비' 방지 회귀 테스트.

검증 항목:
  1. [인용 오탐 차단] 프롬프트에 적힌 '호출법' 리터럴 예시 태그
     (`[IMAGE_REQUEST: {"prompt": "원하는 이미지 설명", ...}]`)를 그대로 인용만 해도
     실제 이미지 생성이 트리거되지 않아야 한다.
  2. [의도 게이트] 사용자 원본 요청에 이미지 의도가 없으면(예: 이미지 봇 초대/설정 질문)
     process_agent_output 이 생성 루프를 아예 돌리지 않아야 한다.
  3. [이름/경로 정합성] 이미 리소스 폴더에 있는 이미지를 재사용할 때 실제 파일명을
     보존하여 계산 경로와 배치 경로가 어긋나지 않아야 한다.
  4. [OpenRouter 회로 차단기] 크레딧 소진(402)이 확인되면 세션 내 재시도를 중단한다.
"""

import os
import sys
import asyncio
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from PIL import Image

from image_creator_hub import ImageCreatorHub
import image_generation_backend as _backend_mod

ImageGenerationBackend = _backend_mod.ImageGenerationBackend


def _png_bytes(color=(200, 100, 50)):
    import io
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, "PNG")
    return buf.getvalue()


class TestQuotedMarkerFalsePositive(unittest.TestCase):
    """1. 호출법 예시 태그 인용은 요청으로 오인하지 않는다."""

    def test_instruction_block_quotation_is_ignored(self):
        # trio_agent_runner / agent_runner 의 '호출법' 설명 블록에서 실제로 쓰인 문구
        block = (
            "🎨 **Image generator 도구 호출법**: 이미지 생성/수정이 필요하면 본문에 "
            '[IMAGE_REQUEST: {"prompt": "원하는 이미지 설명", "asset_name": "app_logo", "aspect_ratio": "1:1"}] '
            "한 줄을 남기십시오. 이미 배치된 에셋을 고치는 경우에는 "
            '[IMAGE_EDIT: {"prompt": "수정 지시", "source_image": "리소스 경로 또는 파일명"}] 를 사용하십시오.'
        )
        self.assertEqual(ImageCreatorHub.parse_image_requests(block), [])

    def test_ellipsis_and_empty_json_are_ignored(self):
        self.assertEqual(
            ImageCreatorHub.parse_image_requests('[IMAGE_REQUEST: {"prompt": "...", "asset_name": "app_logo"}]'),
            [],
        )
        self.assertEqual(ImageCreatorHub.parse_image_requests('[IMAGE_REQUEST: {"prompt": "..."}]'), [])

    def test_real_requests_still_parse(self):
        reqs = ImageCreatorHub.parse_image_requests(
            '[IMAGE_REQUEST: {"prompt": "미니멀 브랜드 히어로 배너", "asset_name": "banner", "aspect_ratio": "16:9"}]'
        )
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["asset_name"], "banner")

        pipe = ImageCreatorHub.parse_image_requests("[IMAGE_REQUEST: app_logo | 1:1 | 미니멀 로고]")
        self.assertEqual(len(pipe), 1)

        free = ImageCreatorHub.parse_image_requests("[IMAGE_REQUEST: 귀여운 고양이]")
        self.assertEqual(len(free), 1)

    def test_mixed_quotation_and_real_request(self):
        body = (
            '[IMAGE_REQUEST: {"prompt": "원하는 이미지 설명", "asset_name": "app_logo"}]\n'
            '[IMAGE_REQUEST: {"prompt": "실제 요청된 배너", "asset_name": "banner", "aspect_ratio": "16:9"}]'
        )
        reqs = ImageCreatorHub.parse_image_requests(body)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["prompt"], "실제 요청된 배너")


class TestImageIntentGate(unittest.TestCase):
    """2. 사용자 요청에 이미지 의도가 없으면 생성 루프를 돌리지 않는다."""

    def test_meta_question_is_not_image_intent(self):
        self.assertFalse(
            ImageCreatorHub.is_image_intent("이미지 생성 봇(1547168863387516938)은 디스코드에 초대 안해도 괜찮은거야?")
        )
        self.assertFalse(ImageCreatorHub.is_image_intent("이미지 생성기는 언제 동작해?"))
        self.assertFalse(ImageCreatorHub.is_image_intent("openrouter 크레딧 부족 문구가 계속 나와"))

    def test_real_image_requests_are_intent(self):
        self.assertTrue(ImageCreatorHub.is_image_intent("우리 프로그램 로고를 새로 만들어줘"))
        self.assertTrue(ImageCreatorHub.is_image_intent("브랜드 히어로 배너 이미지 생성해줘"))
        self.assertTrue(ImageCreatorHub.is_image_intent("앱 아이콘 그려줘"))
        self.assertTrue(ImageCreatorHub.is_image_intent("generate image for the splash screen"))

    def test_process_agent_output_skips_without_intent(self):
        workspace = tempfile.mkdtemp(prefix="agy_img_gate_")
        called = {"n": 0}

        async def _fake_generate(*a, **kw):
            called["n"] += 1
            raise AssertionError("이미지 의도가 없는데 generate() 가 호출됨")

        original = ImageCreatorHub.generate
        ImageCreatorHub.generate = classmethod(lambda cls, *a, **kw: _fake_generate(*a, **kw))
        try:
            out = asyncio.run(
                ImageCreatorHub.process_agent_output(
                    '[IMAGE_REQUEST: {"prompt": "원하는 이미지 설명", "asset_name": "app_logo"}]',
                    workspace=workspace,
                    user_prompt="이미지 생성 봇은 디스코드에 초대 안해도 괜찮은거야?",
                )
            )
            self.assertEqual(out, [])
            self.assertEqual(called["n"], 0)
        finally:
            ImageCreatorHub.generate = original


class TestReuseNamingConsistency(unittest.TestCase):
    """3. 리소스 폴더에 이미 있는 이미지를 재사용할 때 실제 파일명을 보존한다."""

    def test_place_existing_image_preserves_filename_in_resource_dir(self):
        workspace = tempfile.mkdtemp(prefix="agy_img_name_")
        res_dir = os.path.join(workspace, "assets")
        os.makedirs(res_dir, exist_ok=True)

        src = os.path.join(res_dir, "pigmenta_banner.png")
        with open(src, "wb") as f:
            f.write(_png_bytes())

        placement = ImageCreatorHub._place_existing_image(
            src, workspace, asset_name="banner", aspect_ratio="16:9"
        )
        self.assertIsNotNone(placement)
        # 원본 파일명이 보존되어야 한다 (banner.png 로 덮어쓰지 않음)
        self.assertEqual(os.path.basename(placement.absolute_path), "pigmenta_banner.png")
        self.assertTrue(os.path.isfile(placement.absolute_path))

    def test_reuse_finds_substring_match_and_reports_real_name(self):
        workspace = tempfile.mkdtemp(prefix="agy_img_reuse_")
        res_dir = os.path.join(workspace, "assets")
        os.makedirs(res_dir, exist_ok=True)
        existing = os.path.join(res_dir, "pigmenta_banner.png")
        with open(existing, "wb") as f:
            f.write(_png_bytes())

        expected = os.path.join(res_dir, "banner.png")  # 존재하지 않는 계산 경로
        reused = ImageCreatorHub._reuse_existing_asset(expected, workspace, "banner", "16:9")
        self.assertIsNotNone(reused)
        self.assertEqual(os.path.basename(reused.absolute_path), "pigmenta_banner.png")

    def test_reuse_does_not_scan_workspace_root(self):
        """프로젝트 소스 밖(워크스페이스 루트)의 잔여 이미지를 끌어오지 않는다."""
        workspace = tempfile.mkdtemp(prefix="agy_img_scope_")
        stray = os.path.join(workspace, "ai_image_53394.png")
        with open(stray, "wb") as f:
            f.write(_png_bytes())

        # 리소스 폴더가 없고 expected 도 없으면 재사용 없음
        self.assertIsNone(
            ImageCreatorHub._reuse_existing_asset("", workspace, "banner", "1:1")
        )


class TestOpenRouterCircuitBreaker(unittest.TestCase):
    """4. OpenRouter 크레딧 소진(402)은 세션 내 재시도를 중단한다."""

    def setUp(self):
        self._orig_reason = ImageGenerationBackend._OR_EXHAUSTED_REASON
        ImageGenerationBackend.reset_openrouter_breaker()

    def tearDown(self):
        ImageGenerationBackend._OR_EXHAUSTED_REASON = self._orig_reason

    def test_credit_exhaustion_detection(self):
        humanized = ImageGenerationBackend._humanize_http_error(
            Exception("HTTP Error 402: Payment Required")
        )
        # 사람이 읽는 문구가 '크레딧'이어도 회로 차단 판정이 되어야 한다 (과거 미탐지 버그)
        self.assertTrue(ImageGenerationBackend.is_credit_exhausted(humanized))
        self.assertTrue(ImageGenerationBackend.is_credit_exhausted("insufficient credits"))

    def test_breaker_disables_openrouter(self):
        original_key = ImageGenerationBackend._openrouter_key
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "sk-or-v1-" + "a" * 40)
        try:
            self.assertTrue(ImageGenerationBackend.openrouter_available())
            ImageGenerationBackend.mark_openrouter_exhausted("OpenRouter 크레딧 부족(402)")
            self.assertFalse(ImageGenerationBackend.openrouter_available())
            ImageGenerationBackend.reset_openrouter_breaker()
            self.assertTrue(ImageGenerationBackend.openrouter_available())
        finally:
            ImageGenerationBackend._openrouter_key = original_key

    def test_explicit_disable_flag(self):
        original_key = ImageGenerationBackend._openrouter_key
        ImageGenerationBackend._openrouter_key = classmethod(lambda cls: "sk-or-v1-" + "a" * 40)
        original_env = os.environ.get("IMAGE_OPENROUTER_ENABLED")
        try:
            os.environ["IMAGE_OPENROUTER_ENABLED"] = "0"
            self.assertFalse(ImageGenerationBackend.openrouter_enabled())
            self.assertFalse(ImageGenerationBackend.openrouter_available())
            os.environ["IMAGE_OPENROUTER_ENABLED"] = "1"
            self.assertTrue(ImageGenerationBackend.openrouter_available())
        finally:
            if original_env is None:
                os.environ.pop("IMAGE_OPENROUTER_ENABLED", None)
            else:
                os.environ["IMAGE_OPENROUTER_ENABLED"] = original_env
            ImageGenerationBackend._openrouter_key = original_key


if __name__ == "__main__":
    unittest.main()
