#!/usr/bin/env python3
"""
test_vision_attachment_integration.py

Unit and Integration tests for VisionAttachmentAnalyzer, PDF extraction,
and bot attachment handling pipeline.
"""

import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from PIL import Image, ImageDraw

from vision_attachment_analyzer import VisionAttachmentAnalyzer


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
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".png") as tf:
                    img = Image.new("RGB", (100, 100), color="white")
                    img.save(tf.name)

                    res = await VisionAttachmentAnalyzer.analyze_image_file(tf.name, user_prompt="Test")
                    assert res.get("success") is True
                    assert res.get("engine") == "openrouter-gemini-3.8-flash"
                    assert "BUTTON_TEST" in res.get("analysis", "")

    asyncio.run(_runner())


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
