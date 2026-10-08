#!/usr/bin/env python3
"""
test_vision_attachment_integration.py

Unit and Integration tests for VisionAttachmentAnalyzer, PDF extraction,
and bot attachment handling pipeline.
"""

import os
import io
import json
import urllib.error
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image, ImageDraw

from vision_attachment_analyzer import VisionAttachmentAnalyzer
from android_vision_inspector import AndroidVisionInspector


class _FakeHTTPResponse:
    """urllib.request.urlopen 이 돌려주는 컨텍스트 매니저 응답 스텁."""

    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def test_prepare_image_base64(tmp_path):
    # Create test image
    img_path = tmp_path / "test_img.png"
    img = Image.new("RGB", (200, 150), color="blue")
    img.save(str(img_path))

    b64_str, mime_type, w, h = VisionAttachmentAnalyzer._prepare_image_base64(str(img_path))
    assert len(b64_str) > 50
    assert mime_type in ("image/png", "image/jpeg")
    assert w == 200
    assert h == 150


def test_prepare_image_large_resize(tmp_path):
    # Test high-resolution image downscaling
    img_path = tmp_path / "large_img.jpg"
    img = Image.new("RGB", (3200, 1600), color="red")
    img.save(str(img_path))

    b64_str, mime_type, w, h = VisionAttachmentAnalyzer._prepare_image_base64(str(img_path))
    assert max(w, h) <= VisionAttachmentAnalyzer.MAX_IMAGE_DIMENSION
    assert w == 1600
    assert h == 800


def test_pdf_document_extraction(tmp_path):
    import pytest
    pypdf = pytest.importorskip("pypdf")

    pdf_path = tmp_path / "sample.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(str(pdf_path), "wb") as f:
        writer.write(f)

    res = VisionAttachmentAnalyzer.analyze_document_file(str(pdf_path))
    assert res.get("success") is True
    assert res.get("doc_type") == "pdf"
    assert res.get("page_count") == 1


def test_analyze_image_file_mocked():
    async def _runner():
        with patch.object(VisionAttachmentAnalyzer, "_call_openrouter_vision", return_value="[OCR]: BUTTON_TEST\nLayout: Clean"):
            with patch.object(VisionAttachmentAnalyzer, "_get_openrouter_key", return_value="dummy_key"):
                with patch.object(VisionAttachmentAnalyzer, "_get_configured_vision_model", return_value=""):
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".png") as tf:
                        img = Image.new("RGB", (100, 100), color="white")
                        img.save(tf.name)

                        res = await VisionAttachmentAnalyzer.analyze_image_file(tf.name, user_prompt="Test")
                        assert res.get("success") is True
                        assert res.get("engine") == f"openrouter-{VisionAttachmentAnalyzer.DEFAULT_OPENROUTER_VISION_MODEL}"
                        assert "BUTTON_TEST" in res.get("analysis", "")

    asyncio.run(_runner())


def test_vision_model_resolution_priority(monkeypatch):
    """비전 모델 우선순위: `!model vision` 역할 → OPENROUTER_VISION_MODEL → 내장 기본값."""
    monkeypatch.delenv("OPENROUTER_VISION_MODEL", raising=False)
    # 1) 역할 미지정 → 내장 기본값
    monkeypatch.setattr(
        VisionAttachmentAnalyzer, "_get_configured_vision_model",
        classmethod(lambda cls: ""),
    )
    assert (
        VisionAttachmentAnalyzer._get_openrouter_vision_model()
        == VisionAttachmentAnalyzer.DEFAULT_OPENROUTER_VISION_MODEL
    )
    # 2) 환경변수 override 적용
    monkeypatch.setenv("OPENROUTER_VISION_MODEL", "google/gemini-2.5-flash")
    assert VisionAttachmentAnalyzer._get_openrouter_vision_model() == "google/gemini-2.5-flash"
    # 3) `!model vision` 역할 모델이 환경변수보다 최우선
    monkeypatch.setattr(
        VisionAttachmentAnalyzer, "_get_configured_vision_model",
        classmethod(lambda cls: "qwen/qwen3.8-27b"),
    )
    assert VisionAttachmentAnalyzer._get_openrouter_vision_model() == "qwen/qwen3.8-27b"


def test_vision_model_follows_model_command_role(monkeypatch):
    """`!model vision <model>` 로 바꾼 모델을 분석기가 즉시 사용한다 (하드코딩 제거 검증)."""
    from model_manager import GlobalModelManager

    monkeypatch.delenv("OPENROUTER_VISION_MODEL", raising=False)
    GlobalModelManager.initialize()
    assert GlobalModelManager.set_role_model("vision", "google/gemini-3.5-flash") is True
    assert GlobalModelManager.get_role_model("vision") == "google/gemini-3.5-flash"
    # 실제 역할 조회 경로(패치 없음) 그대로 분석기가 반영해야 한다.
    assert VisionAttachmentAnalyzer._get_openrouter_vision_model() == "google/gemini-3.5-flash"


def test_inspect_workspace_screenshots_for_qa(tmp_path):
    async def _runner():
        # Setup gate5 screenshot dir
        sc_dir = tmp_path / "reports" / "gate5" / "screenshots"
        sc_dir.mkdir(parents=True, exist_ok=True)
        sc_path = sc_dir / "screen_01.png"

        img = Image.new("RGB", (390, 844), color="black")
        img.save(str(sc_path))

        with patch.object(VisionAttachmentAnalyzer, "analyze_image_file", new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = {
                "success": True,
                "engine": "openrouter-gemini-3.8-flash",
                "analysis": "🎨 시각 품질 점수: 95/100점\n결함 없음 (무결)",
                "width": 390,
                "height": 844
            }

            res = await VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa(str(tmp_path))
            assert res is not None
            assert res.get("success") is True
            assert res.get("width") == 390
            assert res.get("total_found") == 1

    asyncio.run(_runner())


# ---------------------------------------------------------------------------
# 정직성(Honesty): 비전 모델 미호출 폴백을 '분석 완료'로 위장하지 않는다.
# ---------------------------------------------------------------------------
def test_analyze_image_file_degraded_fallback_is_honest(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    img_path = tmp_path / "no_key.png"
    Image.new("RGB", (60, 60), color="white").save(str(img_path))

    res = asyncio.run(VisionAttachmentAnalyzer.analyze_image_file(str(img_path)))

    assert res.get("success") is True  # 하위 호환: 메타데이터는 전달됨
    assert res.get("engine") == "local-heuristic"
    assert res.get("vision_verified") is False
    assert res.get("degraded") is True
    assert res.get("degraded_reason")


def test_openrouter_vision_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}
    payload = json.dumps({"choices": [{"message": {"content": "OK_VISION"}}]}).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("temporary blip")
        return _FakeHTTPResponse(payload)

    monkeypatch.setattr("vision_attachment_analyzer.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("vision_attachment_analyzer.time.sleep", lambda *a, **k: None)

    out = VisionAttachmentAnalyzer._call_openrouter_vision("b64", "image/png", "prompt", "key")
    assert out == "OK_VISION"
    assert calls["n"] == 2


def test_openrouter_vision_gives_up_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def always_fail(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr("vision_attachment_analyzer.urllib.request.urlopen", always_fail)
    monkeypatch.setattr("vision_attachment_analyzer.time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(VisionAttachmentAnalyzer, "VISION_MAX_ATTEMPTS", 3)

    out = VisionAttachmentAnalyzer._call_openrouter_vision("b64", "image/png", "prompt", "key")
    assert out is None
    assert calls["n"] == 3


def test_openrouter_vision_handles_null_content(monkeypatch):
    """추론형 모델이 content=None 을 반환해도 크래시하지 않고 정직하게 실패 처리한다."""
    payload = json.dumps(
        {"choices": [{"message": {"content": None, "reasoning": "chain of thought"}}]}
    ).encode("utf-8")

    monkeypatch.setattr(
        "vision_attachment_analyzer.urllib.request.urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(payload),
    )
    monkeypatch.setattr("vision_attachment_analyzer.time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(VisionAttachmentAnalyzer, "VISION_MAX_ATTEMPTS", 2)

    sink = {}
    out = VisionAttachmentAnalyzer._call_openrouter_vision("b64", "image/png", "prompt", "key", sink)
    assert out is None
    assert "empty content" in sink["error"]


def test_openrouter_vision_rate_limit_uses_longer_backoff(monkeypatch):
    """무료 모델의 429 레이트리밋은 일반 blip보다 길게 백오프한다."""
    def always_429(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", {},
            io.BytesIO(b'{"error":{"message":"Provider returned error"}}'),
        )

    monkeypatch.setattr("vision_attachment_analyzer.urllib.request.urlopen", always_429)
    sleeps = []
    monkeypatch.setattr("vision_attachment_analyzer.time.sleep", lambda s, *a, **k: sleeps.append(s))
    monkeypatch.setattr(VisionAttachmentAnalyzer, "VISION_MAX_ATTEMPTS", 3)

    out = VisionAttachmentAnalyzer._call_openrouter_vision("b64", "image/png", "prompt", "key")
    assert out is None
    assert sleeps == [
        VisionAttachmentAnalyzer.VISION_RATE_LIMIT_BACKOFF_SEC * 1,
        VisionAttachmentAnalyzer.VISION_RATE_LIMIT_BACKOFF_SEC * 2,
    ]


def test_openrouter_vision_surfaces_http_error_body(monkeypatch):
    """실패 시 HTTP 상태 + API 메시지를 error_sink 로 노출한다(402 크레딧 부족 등)."""
    def fail_402(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 402, "Payment Required", {},
            io.BytesIO(b'{"error":{"message":"Insufficient credits. Add more using https://openrouter.ai/settings/credits"}}'),
        )

    monkeypatch.setattr("vision_attachment_analyzer.urllib.request.urlopen", fail_402)
    monkeypatch.setattr("vision_attachment_analyzer.time.sleep", lambda *a, **k: None)

    sink = {}
    out = VisionAttachmentAnalyzer._call_openrouter_vision("b64", "image/png", "prompt", "key", sink)
    assert out is None
    assert "402" in sink["error"]
    assert "Insufficient credits" in sink["error"]


def test_degraded_reason_surfaces_openrouter_error(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    img_path = tmp_path / "err.png"
    Image.new("RGB", (30, 30), color="white").save(str(img_path))

    def fake_call(b64, mime, prompt, key, error_sink=None):
        if error_sink is not None:
            error_sink["error"] = "HTTP 402: Insufficient credits"
        return None

    monkeypatch.setattr(VisionAttachmentAnalyzer, "_call_openrouter_vision", staticmethod(fake_call))
    res = asyncio.run(VisionAttachmentAnalyzer.analyze_image_file(str(img_path)))

    assert res.get("vision_verified") is False
    assert res.get("degraded") is True
    assert "402" in res.get("degraded_reason", "")


# ---------------------------------------------------------------------------
# 전수조사(Exhaustive): 연속 동일 프레임(UI Freeze)을 픽셀이 아니라 바이트로 검출.
# ---------------------------------------------------------------------------
def test_detect_duplicate_frames_flags_identical_consecutive(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    c = tmp_path / "c.png"
    img = Image.new("RGB", (40, 40), color=(10, 20, 30))
    ImageDraw.Draw(img).line((0, 0, 39, 39), fill=(255, 255, 255), width=3)
    img.save(str(a))
    img.save(str(b))  # a 와 바이트 동일
    Image.new("RGB", (40, 40), color=(200, 30, 30)).save(str(c))

    pairs = AndroidVisionInspector._detect_duplicate_frames([str(a), str(b), str(c)])
    assert pairs == [(str(a), str(b))]
