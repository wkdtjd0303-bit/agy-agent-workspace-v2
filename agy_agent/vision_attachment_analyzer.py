#!/usr/bin/env python3
"""
vision_attachment_analyzer.py

Multimodal Vision & Document Attachment Analysis Engine for AGY 3.0 Platform.
Enables Coder, Senior QA, and Discord Bots to directly perceive, analyze,
and visually inspect user-attached images, screenshots, and PDF documents.

Key Capabilities:
1. High-speed Gemini Multimodal Vision inspection via OpenRouter (google/gemini-3.8-flash).
2. OCR & UI/UX layout breakdown (color tokens, bento grid, touch targets, buttons, typography).
3. Automated Visual QA defect scanning (button text line-breaks, overflow, WCAG contrast).
4. Rich PDF document text & metadata extraction with pypdf.
5. In-flight fallback to direct Google GenAI or local PIL heuristic analysis.
"""

import os
import io
import json
import base64
import asyncio
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

from PIL import Image

try:
    import pypdf
except ImportError:
    pypdf = None

logger = logging.getLogger("VisionAttachmentAnalyzer")

class VisionAttachmentAnalyzer:
    DEFAULT_OPENROUTER_VISION_MODEL = "google/gemini-3.8-flash"
    DEFAULT_AGY_VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.8-flash-high")
    DEFAULT_VISION_MODEL = DEFAULT_OPENROUTER_VISION_MODEL
    MAX_IMAGE_DIMENSION = 1600  # Max dimension for vision processing
    MAX_PDF_PAGES = 20
    MAX_PDF_TEXT_BYTES = 30000

    @classmethod
    def _get_openrouter_key(cls) -> str:
        raw = os.getenv("OPENROUTER_API_KEY", "").strip()
        for prefix in ("(삭제해야정상동작)", '"(삭제해야정상동작)', "'(삭제해야정상동작)"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        return raw.strip('"\'; ')

    @classmethod
    def _prepare_image_base64(cls, image_path: str) -> Tuple[str, str, int, int]:
        """
        Reads, normalizes, and base64-encodes an image file.
        Resizes high-res images to safe dimensions while preserving aspect ratio.
        Returns (base64_str, mime_type, width, height).
        """
        with Image.open(image_path) as img:
            width, height = img.size
            
            # Convert RGBA/P to RGB for JPEG compatibility if needed
            if img.mode in ("RGBA", "LA", "P"):
                # Check format: keep PNG if transparency exists
                mime_type = "image/png"
                out_format = "PNG"
            else:
                mime_type = "image/jpeg"
                out_format = "JPEG"

            # Resize if too large
            if max(width, height) > cls.MAX_IMAGE_DIMENSION:
                ratio = cls.MAX_IMAGE_DIMENSION / max(width, height)
                new_w = int(width * ratio)
                new_h = int(height * ratio)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                width, height = new_w, new_h

            buf = io.BytesIO()
            img.save(buf, format=out_format, quality=88, optimize=True)
            b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
            return b64_str, mime_type, width, height

    @classmethod
    async def analyze_image_file(
        cls,
        image_path: str,
        user_prompt: str = "",
        role: str = "general"
    ) -> Dict[str, Any]:
        """
        Analyzes an image attachment using Gemini Vision multimodal intelligence.
        Extracts OCR text, layout architecture, styling tokens, and actionable developer specs.
        """
        if not os.path.isfile(image_path):
            return {"success": False, "error": f"Image file not found: {image_path}"}

        try:
            b64_str, mime_type, orig_w, orig_h = await asyncio.to_thread(
                cls._prepare_image_base64, image_path
            )
        except Exception as e:
            logger.warning(f"Failed to prepare image {image_path}: {e}")
            return {"success": False, "error": f"Image preprocessing failed: {str(e)}"}

        filename = os.path.basename(image_path)

        if role == "design_critique":
            # !mode 3 무인 자율 개발 전용: 수치화된 디자인 계약 + PIL 실측치를 근거로
            # '[VISUAL_VERDICT]/[VISUAL_DEFECTS]' 구조화 라인만 출력하게 강제한다.
            vision_system_prompt = (
                "[역할: 최고 수준의 프로덕트 디자이너 & 시각 완성도 감사관 (Visual Craftsmanship Critic)]\n"
                "첨부된 실제 렌더링 스크린샷을, 스토어 출시된 상용 앱과 비교하는 냉정한 기준으로 감사하십시오.\n"
                "사용자의 요청(user_prompt)에 포함된 [디자인 계약] 수치와 [PIL 물리 계측치]를 절대 기준으로 사용하고, "
                "눈대중이 아닌 계약 위반 여부로 판정하십시오.\n\n"
                "🚨 [최우선 색출 대상 4대 반패턴]:\n"
                "1. WEB_LANDING_STRUCTURE: 앱인데 첫 화면이 마케팅 히어로/소개 배너 중심이고 1차 콘텐츠가 밀림/잘림\n"
                "2. DUPLICATE_ACTIONS: 동일 목적 버튼(열기/시작/추가)의 위계 중복으로 동선 혼선\n"
                "3. NESTED_CARDS_WHITESPACE: 카드 3중 이상 중첩, 여백 호흡 부족, 회색 박스 남용\n"
                "4. TOUCH_ERGONOMICS: 48dp 미만 터치 타깃, 상단 1/3 주요 조작 과밀집, 텍스트 줄바꿈/잘림/겹침\n\n"
                "[출력 형식 — 아래 라인만 정확히, 다른 설명 없이]:\n"
                "[VISUAL_VERDICT]: PASS 또는 FAIL\n"
                "[VISUAL_SCORE]: 0~100 정수\n"
                "[VISUAL_SUMMARY]: 30자 이내 한 줄 총평\n"
                "[VISUAL_DEFECTS]:\n"
                "- MAJOR | MARKETING_HERO | 상단 40% 히어로 카드 | 감성 카피가 첫 화면을 점유 | 히어로 제거 후 목록 상단 배치\n"
            )
        elif role == "qa_review":
            vision_system_prompt = (
                "[역할: 수석 Visual QA & 시각적 결함 감사관 (Chief Visual Inspector)]\n"
                "첨부된 UI 스크린샷 화면을 픽셀 단위로 정밀 검사하여 시각적 결함을 평가하십시오.\n\n"
                "🚨 [4대 집중 점검 기준]:\n"
                "1. 버튼 텍스트 줄바꿈 결함: 저장, 공유 등 버튼 내부 텍스트가 좁아서 2줄로 찌그러진 현상이 있는가?\n"
                "2. 레이아웃 오버플로우/잘림: 핵심 텍스트나 타이틀이 화면 밖으로 넘치거나 비정상적으로 잘리는가?\n"
                "3. 터치 타깃 & 버튼 과밀집: 모바일 기준 최소 48dp(44px) 터치 영역 준수 및 오터치 방지 여부\n"
                "4. 타이포그래피 & 명도 대비율: WCAG AA 4.5:1 이상 대비율 충족 여부\n\n"
                "출력 형식:\n"
                "- 🎨 **시각 품질 점수**: X/100점 (0~100)\n"
                "- 🔍 **시각적 결함 목록**: (없으면 '결함 없음 (무결)')\n"
                "- 📝 **화면 내 인식된 주요 텍스트(OCR)**:\n"
                "- 📐 **레이아웃 & 컴포넌트 평가**:\n"
                "- 💡 **Coder에게 전달할 시각 개선 지침**:"
            )
        else:
            vision_system_prompt = (
                "[역할: 최고 수준의 UI/UX 수석 디자이너 & Vision AI 분석관]\n"
                f"사용자가 작업 의뢰와 함께 참고 이미지(`{filename}`)를 첨부했습니다.\n"
                f"사용자 요청 문맥: \"{user_prompt or '첨부된 이미지의 디자인과 레이아웃을 분석하여 구현해줘'}\"\n\n"
                "첨부된 이미지를 정밀 분석하여 다음 정보를 상세하고 명확하게 제공하십시오:\n"
                "1. 🖼️ **이미지 성격 및 화면 개요**: (모바일 앱 UI, 웹 대시보드, 에러 스크린샷, 도식, 기획 시안 등)\n"
                "2. 📝 **화면 내 모든 텍스트(OCR 전수 추출)**: 화면에 표시된 모든 글자(버튼 레이블, 제목, 본문, 안내문, 코드 등)를 누락 없이 원문 그대로 추출\n"
                "3. 📐 **레이아웃 & 컴포넌트 구조**: 상단바, 내비게이션, 카드, 그리드, 버튼, 입력창, 하단 탭 등의 배치 및 위계 구조\n"
                "4. 🎨 **디자인 시스템 & 스타일 토큰**: 주요 색상(추정 HEX 코드), 배경색, 코너 라운딩(radius), 여백감, 다크/라이트 테마\n"
                "5. 🔍 **주목할 만한 시각 특징 & 사용자 의도**: 사용자가 구현 또는 수정을 원할 핵심 영역\n"
                "6. 💡 **프론트엔드/엔지니어링 구현 가이드**: Coder가 실제 소스코드로 구현할 때 반드시 준수해야 할 핵심 지침"
            )

        # 1. Primary engine: OpenRouter Google Gemini 3.8 Flash
        api_key = cls._get_openrouter_key()
        if api_key:
            analysis_text = await asyncio.to_thread(
                cls._call_openrouter_vision,
                b64_str,
                mime_type,
                vision_system_prompt,
                api_key
            )
            if analysis_text:
                return {
                    "success": True,
                    "engine": "openrouter-gemini-3.8-flash",
                    "filename": filename,
                    "width": orig_w,
                    "height": orig_h,
                    "analysis": analysis_text
                }

        # 2. Fallback: Direct Google GenAI SDK if GEMINI_API_KEY is available
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if gemini_key:
            analysis_text = await asyncio.to_thread(
                cls._call_direct_gemini_vision,
                image_path,
                vision_system_prompt,
                gemini_key
            )
            if analysis_text:
                return {
                    "success": True,
                    "engine": "direct-google-genai",
                    "filename": filename,
                    "width": orig_w,
                    "height": orig_h,
                    "analysis": analysis_text
                }

        # 3. Heuristic fallback
        return {
            "success": True,
            "engine": "local-heuristic",
            "filename": filename,
            "width": orig_w,
            "height": orig_h,
            "analysis": (
                f"🖼️ **[이미지 기본 메타데이터]**\n"
                f"- 파일명: `{filename}`\n"
                f"- 해상도: {orig_w}x{orig_h} px\n"
                f"- 안내: OpenRouter 또는 Gemini API 키 미설정으로 인해 정밀 비전 분석 대신 원본 파일 경로(`{image_path}`)가 에이전트에 전달되었습니다. 에이전트의 `view_file` 도구를 통해 직접 확인할 수 있습니다."
            )
        }

    @classmethod
    def _call_openrouter_vision(
        cls,
        b64_str: str,
        mime_type: str,
        prompt: str,
        api_key: str
    ) -> Optional[str]:
        """Synchronous worker for OpenRouter vision completion."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": cls.DEFAULT_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64_str}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 1500,
            "temperature": 0.2
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/google-deepmind/antigravity",
                "X-Title": "AGY 3.0 Vision Analyzer"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=25) as res:
                if res.status == 200:
                    data = json.loads(res.read().decode("utf-8"))
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.warning(f"OpenRouter vision call failed: {e}")
        return None

    @classmethod
    def _call_direct_gemini_vision(
        cls,
        image_path: str,
        prompt: str,
        api_key: str
    ) -> Optional[str]:
        """Synchronous worker for google.genai direct vision."""
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)
            with open(image_path, "rb") as f:
                img_bytes = f.read()

            res = client.models.generate_content(
                model=os.getenv("VISION_MODEL", "gemini-3.8-flash-high"),
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    prompt
                ]
            )
            if res and res.text:
                return res.text.strip()
        except Exception as e:
            logger.warning(f"Direct Google GenAI vision call failed: {e}")
        return None

    @classmethod
    def analyze_document_file(cls, file_path: str) -> Dict[str, Any]:
        """
        Reads and extracts text from PDF and document attachments.
        """
        if not os.path.isfile(file_path):
            return {"success": False, "error": f"Document not found: {file_path}"}

        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".pdf":
            if not pypdf:
                return {
                    "success": False,
                    "error": "pypdf package is not installed."
                }
            try:
                reader = pypdf.PdfReader(file_path)
                total_pages = len(reader.pages)
                pages_to_read = min(total_pages, cls.MAX_PDF_PAGES)

                extracted_text = []
                total_chars = 0
                for idx in range(pages_to_read):
                    page = reader.pages[idx]
                    txt = page.extract_text() or ""
                    if txt.strip():
                        extracted_text.append(f"--- [PDF 페이지 {idx + 1}/{total_pages}] ---\n{txt.strip()}")
                        total_chars += len(txt)
                        if total_chars >= cls.MAX_PDF_TEXT_BYTES:
                            extracted_text.append("\n... [문서 용량 초과로 이후 내용은 생략되었습니다] ...")
                            break

                full_text = "\n\n".join(extracted_text)
                return {
                    "success": True,
                    "doc_type": "pdf",
                    "filename": filename,
                    "page_count": total_pages,
                    "pages_extracted": pages_to_read,
                    "content": full_text
                }
            except Exception as e:
                logger.warning(f"PDF extraction error for {file_path}: {e}")
                return {"success": False, "error": f"PDF parsing failed: {str(e)}"}

        return {"success": False, "error": f"Unsupported document extension: {ext}"}

    @classmethod
    async def inspect_workspace_screenshots_for_qa(
        cls,
        workspace: str,
        user_reference_path: Optional[str] = None,
        min_mtime: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Finds the newest screenshots generated by Coder in the workspace,
        runs automated Visual QA inspection, and returns a structured report for QA / Auditor.

        ``min_mtime``(라운드 시작 시각)이 주어지면 그보다 오래된 스크린샷은 제외한다.
        REJECTED 후 재작업 라운드에서 "이전 라운드 화면"을 현재 증거로 재사용하는 것을 막는다.
        """
        candidate_dirs = [
            os.path.join(workspace, "reports", "gate5", "screenshots"),
            os.path.join(workspace, "reports", "screenshots"),
            os.path.join(workspace, "screenshots"),
            os.path.join(workspace, "app", "build", "outputs", "roborazzi"),
        ]

        found_images: List[Tuple[float, str]] = []
        for cdir in candidate_dirs:
            if os.path.isdir(cdir):
                for fname in os.listdir(cdir):
                    if fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        if fname.startswith("00_") or "diff" in fname.lower():
                            continue
                        fpath = os.path.join(cdir, fname)
                        if os.path.isfile(fpath) and os.path.getsize(fpath) > 1024:
                            mt = os.path.getmtime(fpath)
                            if min_mtime is not None and mt < (min_mtime - 1.0):
                                continue  # 이전 라운드 스크린샷 → 현재 증거로 재사용 금지
                            found_images.append((mt, fpath))

        if not found_images:
            return None

        # Sort by modification time descending (latest first)
        found_images.sort(key=lambda x: x[0], reverse=True)
        latest_screenshot = found_images[0][1]

        logger.info(f"Running automated Visual QA on latest screenshot: {latest_screenshot}")
        result = await cls.analyze_image_file(
            image_path=latest_screenshot,
            role="qa_review"
        )
        if result.get("success"):
            result["screenshot_path"] = latest_screenshot
            result["total_found"] = len(found_images)
            return result

        return None
