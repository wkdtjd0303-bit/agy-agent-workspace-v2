#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_generation_backend.py

🎨 실제 AI 이미지 생성 백엔드 (Real Image Generation Backends) — Nano Banana 계열.

배경 / Why this module exists
--------------------------------------------------------------------
이전 구현은 "Antigravity CLI(agy)에 이미지를 생성하라"는 **산문 지시문**을 던지고,
산출물이 없으면 PIL 도형 렌더(절차적 에셋)로 조용히 대체했다. 그러나 AGY CLI 에는
실제 이미지 생성 서브커맨드/플러그인/MCP 가 존재하지 않는다. 즉 사용자가 기대한
"나노바나나(진짜 AI 이미지)" 가 아니라 색깔 원/사각형이 나오는 구조였다.

본 모듈은 그 문제를 근본적으로 해결한다. **실제 이미지 바이트를 반환하는 API** 만 사용한다:

  1) OpenRouter 이미지 모델 (기본: google/gemini-2.5-flash-image = Nano Banana 계열)
     POST https://openrouter.ai/api/v1/chat/completions
       { "modalities": ["image","text"], ... }
     → choices[0].message.images[0].image_url.url ("data:image/png;base64,....")

  1-b) Google Gemini 네이티브 (Nano Banana 직접 호출) — GEMINI_API_KEY/GOOGLE_API_KEY 설정 시 자동 활성.
       OPENROUTER 크레딧과 독립적으로 동작하는 2차 실제 이미지 백엔드.
     POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=...
       { "generationConfig": { "responseModalities": ["TEXT","IMAGE"] }, ... }
     → candidates[0].content.parts[].inlineData.data (base64)

  2) OpenRouter 이미지 편집 (원본 이미지 + 프롬프트 → 수정본)
     messages[0].content = [
        {"type":"text","text": prompt},
        {"type":"image_url","image_url":{"url":"data:<mime>;base64,<...>"}}
     ]

  3) (옵션) OpenAI 호환 /images/generations 엔드포인트 — gpt-image 계열 등
     IMAGE_API_BASE + IMAGE_API_KEY 설정 시 자동 활성.

모든 백엔드는 `ImageBackendResult`(성공 여부 + 저장 경로 + 정직한 사유)를 반환하며,
실패 시 절대 조용히 도형으로 대체하지 않는다. 호출자(image_creator_hub)가 사용자에게
실패 사유를 정직하게 보고하도록 `error` 를 그대로 전달한다.
"""

from __future__ import annotations

import os
import io
import base64
import json
import time
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ImageGenerationBackend")

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# ---------------------------------------------------------------------------
# 프롬프트 → 실제 이미지 품질을 끌어올리는 공통 지침 (AI 이미지 모델용)
# ---------------------------------------------------------------------------
_QUALITY_SUFFIX = (
    " High quality, crisp, production-ready app asset. "
    "Clean composition, coherent lighting, no watermark, no text artifacts, "
    "no UI mockups unless explicitly requested."
)


@dataclass
class ImageBackendResult:
    """실제 이미지 생성 백엔드 호출 결과 1건."""
    ok: bool
    provider: str = ""              # "openrouter" | "openai_compat" | ...
    model: str = ""
    output_path: Optional[str] = None
    image_bytes: int = 0
    mime_type: str = ""
    revised_prompt: str = ""
    error: str = ""
    fallback_hint: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImageGenerationBackend:
    """실제 AI 이미지 생성/편집 백엔드 디스패처."""

    # 나노바나나 = Gemini 이미지 생성 모델 계열. OpenRouter 상에서 실제 이미지 바이트를
    # 반환하는 것이 실측 확인된 모델을 기본값으로 둔다.
    DEFAULT_OPENROUTER_IMAGE_MODEL = os.getenv(
        "IMAGE_OPENROUTER_MODEL", "google/gemini-2.5-flash-image"
    ).strip() or "google/gemini-2.5-flash-image"

    # 실제 이미지를 반환하는 것으로 알려진 후보 체인 (앞에서부터 시도)
    OPENROUTER_IMAGE_CANDIDATES: List[str] = [
        m.strip() for m in os.getenv(
            "IMAGE_OPENROUTER_MODEL_CHAIN",
            "google/gemini-2.5-flash-image,google/gemini-3.1-flash-image,google/gemini-3-pro-image",
        ).split(",") if m.strip()
    ]

    _OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    _OPENROUTER_TIMEOUT = float(os.getenv("IMAGE_BACKEND_TIMEOUT", "180.0"))
    _MAX_TOKENS = int(os.getenv("IMAGE_BACKEND_MAX_TOKENS", "2048"))

    # [사용자 선택] `IMAGE_OPENROUTER_ENABLED=0` 이면 OpenRouter 를 아예 시도하지 않는다.
    #   (예: 요금제를 쓰지 않고 AGY/Gemini 2차 백엔드만 사용할 때 402 스팸 원천 차단)
    @classmethod
    def openrouter_enabled(cls) -> bool:
        raw = (os.getenv("IMAGE_OPENROUTER_ENABLED", "1") or "1").strip().lower()
        return raw not in ("0", "false", "no", "off", "disable", "disabled")

    # ------------------------------------------------------------------
    # 0. 가용성 판정
    # ------------------------------------------------------------------
    # [🔌 크레딧 소진 회로 차단기] OpenRouter 402(크레딧 부족)는 키/크레딧을
    #   충전하기 전에는 영구 실패다. 매 턴 3개 모델을 다시 호출하며 402 로그/지연을
    #   반복하지 않도록, 소진이 확인된 세션에서는 OpenRouter 시도를 건너뛴다.
    #   (충전 후 재시도하려면 봇 재시작 또는 reset_openrouter_breaker())
    _OR_EXHAUSTED_REASON: str = ""

    @classmethod
    def is_credit_exhausted(cls, message: str = "") -> bool:
        """크레딧/결제 문제로 인한 영구 실패 메시지인지 판정한다(한/영 혼용 대응)."""
        low = (message or "").lower()
        return any(
            token in low or token in (message or "")
            for token in ("402", "credit", "insufficient", "크레딧", "payment required", "quota_exceeded")
        )

    @classmethod
    def mark_openrouter_exhausted(cls, reason: str) -> None:
        """OpenRouter 크레딧 소진을 기록해 세션 내 재시도를 중단한다(로그는 1회만)."""
        if not cls._OR_EXHAUSTED_REASON:
            cls._OR_EXHAUSTED_REASON = reason or "OpenRouter 크레딧 소진(402)"
            logger.warning(
                f"[ImageGenerationBackend] OpenRouter 회로 차단기 작동 — {cls._OR_EXHAUSTED_REASON} "
                "(재시도 중단, GEMINI_API_KEY 등 2차 백엔드로 전환)"
            )

    @classmethod
    def reset_openrouter_breaker(cls) -> None:
        """크레딧 충전 후 수동으로 회로 차단기를 해제한다."""
        cls._OR_EXHAUSTED_REASON = ""

    @classmethod
    def openrouter_exhausted_reason(cls) -> str:
        return cls._OR_EXHAUSTED_REASON

    @classmethod
    def _openrouter_key(cls) -> str:
        """config → 환경변수 순으로 OpenRouter 키를 정제해 가져온다."""
        raw = ""
        try:
            import config
            raw = getattr(config, "OPENROUTER_API_KEY", "") or ""
        except Exception:
            raw = ""
        if not raw:
            raw = os.getenv("OPENROUTER_API_KEY", "")
        raw = (raw or "").strip()
        for prefix in ("(삭제해야정상동작)", '"(삭제해야정상동작)', "'(삭제해야정상동작)"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        return raw.strip('"\'; ')

    @classmethod
    def openrouter_available(cls) -> bool:
        if not cls.openrouter_enabled():
            return False  # 사용자가 OpenRouter 이미지 백엔드를 명시적으로 비활성화함
        if cls._OR_EXHAUSTED_REASON:
            return False  # 크레딧 소진 확인됨 → 세션 내 재시도 중단
        key = cls._openrouter_key()
        return bool(key) and len(key) >= 10 and not key.startswith(("dummy", "your_"))

    @classmethod
    def openai_compat_config(cls) -> Tuple[str, str, str]:
        """
        OpenAI 호환 이미지 엔드포인트 설정 (선택).
        Returns: (base_url, api_key, model) — 미설정 시 ("", "", "")
        """
        base = (os.getenv("IMAGE_API_BASE", "") or "").strip().rstrip("/")
        key = (os.getenv("IMAGE_API_KEY", "") or os.getenv("OPENAI_API_KEY", "") or "").strip()
        model = (os.getenv("IMAGE_API_MODEL", "") or "").strip()
        if base and key and model:
            return base, key, model
        return "", "", ""

    # ------------------------------------------------------------------
    # 0-b. Google Gemini 네이티브 이미지 백엔드 (Nano Banana 직접 호출)
    # ------------------------------------------------------------------
    @classmethod
    def gemini_native_config(cls) -> Tuple[str, str]:
        """
        Google Generative Language API 직접 호출 설정 (선택).
        OPENROUTER 크레딧과 무관하게 동작하는 2차 실제 이미지 백엔드.
        Returns: (api_key, model) — 미설정 시 ("", "")
        """
        key = (
            os.getenv("GEMINI_API_KEY", "")
            or os.getenv("GOOGLE_API_KEY", "")
            or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY", "")
            or ""
        ).strip()
        model = (os.getenv("IMAGE_GEMINI_MODEL", "") or "").strip()
        if model:
            # 'models/' 접두사가 있으면 제거 (URL 조립 시 중복 방지)
            if model.startswith("models/"):
                model = model[len("models/"):]
        else:
            model = "gemini-2.5-flash-image"  # Nano Banana 계열 (Gemini 이미지 모델)
        if key:
            return key, model
        return "", ""

    @classmethod
    def real_backend_available(cls) -> bool:
        """실제 이미지 생성 백엔드가 최소 1개 이상 구성되어 있는지."""
        base, key, model = cls.openai_compat_config()
        g_key, _g_model = cls.gemini_native_config()
        return cls.openrouter_available() or bool(g_key) or bool(base and key and model)

    @classmethod
    def describe_backends(cls) -> str:
        """현재 구성된 실제 이미지 백엔드 설명 (help/!bots 안내 문구용)."""
        parts: List[str] = []
        if cls.openrouter_available():
            parts.append(f"OpenRouter `{cls.DEFAULT_OPENROUTER_IMAGE_MODEL}` (Nano Banana 계열)")
        g_key, g_model = cls.gemini_native_config()
        if g_key:
            parts.append(f"Google Gemini `{g_model}` (Native API)")
        base, key, model = cls.openai_compat_config()
        if base and key and model:
            parts.append(f"OpenAI호환 `{model}` @ `{base}`")
        return " / ".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # 1. 메인 진입점 — 실제 이미지 생성
    # ------------------------------------------------------------------
    @classmethod
    def generate_image(
        cls,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "1:1",
        model_hint: str = "",
    ) -> ImageBackendResult:
        """
        실제 AI 이미지 생성. 성공 시 output_path 에 실제 이미지 바이트를 저장한다.
        실패 시 절대 도형/PIL 로 대체하지 않고 정직한 error 를 반환한다.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return ImageBackendResult(ok=False, error="이미지 프롬프트가 비어 있습니다.")

        errors: List[str] = []

        # 1) OpenRouter 이미지 모델 (Nano Banana 계열) — 주 백엔드
        if cls.openrouter_available():
            res = cls._generate_via_openrouter(prompt, output_path, aspect_ratio, model_hint)
            if res.ok:
                return res
            errors.append(f"OpenRouter: {res.error}")

        # 1-b) Google Gemini 네이티브 (Nano Banana 직접 호출) — 2차 실제 백엔드
        g_key, g_model = cls.gemini_native_config()
        if g_key:
            res = cls._generate_via_gemini_native(prompt, output_path, aspect_ratio, g_model)
            if res.ok:
                return res
            errors.append(f"GeminiNative: {res.error}")

        # 2) OpenAI 호환 /images/generations — 옵션 백엔드
        base, key, model = cls.openai_compat_config()
        if base and key and model:
            res = cls._generate_via_openai_compat(prompt, output_path, aspect_ratio, model)
            if res.ok:
                return res
            errors.append(f"OpenAI호환: {res.error}")

        if not cls.real_backend_available():
            if cls.openrouter_enabled():
                hint = (
                    "실제 이미지 생성 백엔드가 구성되지 않았습니다. "
                    ".env 의 OPENROUTER_API_KEY(또는 GEMINI_API_KEY, IMAGE_API_BASE/IMAGE_API_KEY/IMAGE_API_MODEL)를 확인하십시오."
                )
            else:
                hint = (
                    "OpenRouter 이미지 백엔드가 비활성화(IMAGE_OPENROUTER_ENABLED=0)되어 있고 "
                    "대체 백엔드도 없습니다. .env 의 GEMINI_API_KEY(Google Nano Banana) 또는 "
                    "IMAGE_API_BASE/IMAGE_API_KEY/IMAGE_API_MODEL 을 설정하십시오."
                )
            errors.append(hint)

        return ImageBackendResult(
            ok=False,
            error=" | ".join(errors) or "알 수 없는 이미지 백엔드 오류",
            fallback_hint="절차적 에셋으로 대체됩니다.",
        )

    # ------------------------------------------------------------------
    # 2. 이미지 편집 (원본 + 프롬프트 → 수정본)
    # ------------------------------------------------------------------
    @classmethod
    def edit_image(
        cls,
        prompt: str,
        source_image_path: str,
        output_path: str,
        aspect_ratio: str = "1:1",
        model_hint: str = "",
    ) -> ImageBackendResult:
        """기존 이미지를 입력으로 받아 AI 편집(수정)한다."""
        prompt = (prompt or "").strip()
        if not prompt:
            return ImageBackendResult(ok=False, error="편집 프롬프트가 비어 있습니다.")
        if not os.path.isfile(source_image_path):
            return ImageBackendResult(ok=False, error=f"원본 이미지가 없습니다: {source_image_path}")

        errors: List[str] = []

        if cls.openrouter_available():
            res = cls._edit_via_openrouter(
                prompt, source_image_path, output_path, aspect_ratio, model_hint
            )
            if res.ok:
                return res
            or_error = res.error
            or_model = res.model or cls.DEFAULT_OPENROUTER_IMAGE_MODEL
            errors.append(f"OpenRouter: {or_error}")
        else:
            or_error = ""
            or_model = ""

        # 2차: Google Gemini 네이티브 편집
        #  [주의] generate_image 와 달리 기존 edit_image 는 gemini_native_config() 를
        #  직접 조회하고 Gemini 시도 결과를 무조건 반환했다. 그 결과
        #    · Gemini 키는 있으나 실패(예: 이미지 모델 미지원)하고
        #    · OpenAI 호환(/images/edits) 백엔드는 정상 구성된 경우
        #  정상 백엔드를 건너뛰고 실패했다. 이제 편집도 3단 폴백으로 통일한다.
        g_key, g_model = cls.gemini_native_config()
        if g_key:
            res = cls._edit_via_gemini_native(
                prompt, source_image_path, output_path, aspect_ratio, g_model
            )
            if res.ok:
                return res
            errors.append(f"GeminiNative: {res.error}")

        # 3차: OpenAI 호환 편집 (/images/edits — gpt-image 계열 등)
        base, key, model = cls.openai_compat_config()
        if base and key and model:
            res = cls._edit_via_openai_compat(
                prompt, source_image_path, output_path, aspect_ratio, model
            )
            if res.ok:
                return res
            errors.append(f"OpenAI호환: {res.error}")

        if not cls.real_backend_available():
            errors.append(
                "이미지 편집 백엔드가 구성되지 않았습니다. "
                ".env 의 OPENROUTER_API_KEY / GEMINI_API_KEY / IMAGE_API_BASE·IMAGE_API_KEY·IMAGE_API_MODEL 을 확인하십시오."
            )

        return ImageBackendResult(
            ok=False,
            provider="openrouter" if or_error else "",
            model=or_model,
            error=" | ".join(errors) or "이미지 편집 백엔드(OpenRouter/Gemini)가 구성되지 않았습니다.",
            fallback_hint="절차적 에셋으로 대체됩니다.",
        )

    # ------------------------------------------------------------------
    # 3. OpenRouter 구현
    # ------------------------------------------------------------------
    @classmethod
    def _candidate_models(cls, model_hint: str) -> List[str]:
        chain: List[str] = []
        hint = (model_hint or "").strip()
        # 사용자가 지정한 모델이 실제 이미지 모델이면 최우선
        if hint and hint not in ("agy", "auto") and "/" in hint:
            chain.append(hint)
        chain.append(cls.DEFAULT_OPENROUTER_IMAGE_MODEL)
        chain.extend(cls.OPENROUTER_IMAGE_CANDIDATES)
        # 중복 제거(순서 보존)
        seen, out = set(), []
        for m in chain:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    @classmethod
    def _generate_via_openrouter(
        cls,
        prompt: str,
        output_path: str,
        aspect_ratio: str,
        model_hint: str,
    ) -> ImageBackendResult:
        key = cls._openrouter_key()
        content = f"{prompt}.{_QUALITY_SUFFIX}"
        ar_hint = cls._aspect_ratio_hint(aspect_ratio)
        if ar_hint:
            content = f"{content} {ar_hint}"

        last_err = ""
        for model in cls._candidate_models(model_hint):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "modalities": ["image", "text"],
                "max_tokens": cls._MAX_TOKENS,
            }
            try:
                data = cls._post_json(cls._OPENROUTER_URL, payload, key)
            except Exception as e:
                last_err = cls._humanize_http_error(e)
                logger.warning(f"[ImageGenerationBackend] OpenRouter {model} 실패: {last_err}")
                # 크레딧/키 오류는 다음 모델도 동일하게 실패할 가능성이 높다.
                if cls.is_credit_exhausted(last_err) or "401" in last_err or "403" in last_err:
                    cls.mark_openrouter_exhausted(last_err)
                    return ImageBackendResult(
                        ok=False, provider="openrouter", model=model, error=last_err,
                    )
                continue

            img_bytes, mime, revised = cls._extract_image_from_openrouter(data)
            if img_bytes:
                saved = cls._save_bytes(img_bytes, output_path, mime)
                if saved:
                    return ImageBackendResult(
                        ok=True,
                        provider="openrouter",
                        model=model,
                        output_path=output_path,
                        image_bytes=len(img_bytes),
                        mime_type=mime or cls._guess_mime(output_path),
                        revised_prompt=revised,
                    )
                last_err = f"생성된 이미지 저장 실패: {output_path}"
            else:
                last_err = f"{model} 응답에 이미지 데이터가 없습니다."

        return ImageBackendResult(
            ok=False, provider="openrouter",
            model=cls.DEFAULT_OPENROUTER_IMAGE_MODEL,
            error=last_err or "OpenRouter 이미지 생성 실패",
        )

    @classmethod
    def _edit_via_openrouter(
        cls,
        prompt: str,
        source_image_path: str,
        output_path: str,
        aspect_ratio: str,
        model_hint: str,
    ) -> ImageBackendResult:
        key = cls._openrouter_key()
        try:
            b64_src, mime_src = cls._file_to_base64(source_image_path)
        except Exception as e:
            return ImageBackendResult(ok=False, provider="openrouter", error=f"원본 인코딩 실패: {e}")

        content = [
            {"type": "text", "text": f"{prompt}.{_QUALITY_SUFFIX} {cls._aspect_ratio_hint(aspect_ratio)}".strip()},
            {"type": "image_url", "image_url": {"url": f"data:{mime_src};base64,{b64_src}"}},
        ]

        last_err = ""
        for model in cls._candidate_models(model_hint):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "modalities": ["image", "text"],
                "max_tokens": cls._MAX_TOKENS,
            }
            try:
                data = cls._post_json(cls._OPENROUTER_URL, payload, key)
            except Exception as e:
                last_err = cls._humanize_http_error(e)
                if cls.is_credit_exhausted(last_err) or "401" in last_err or "403" in last_err:
                    cls.mark_openrouter_exhausted(last_err)
                    return ImageBackendResult(ok=False, provider="openrouter", model=model, error=last_err)
                continue

            img_bytes, mime, revised = cls._extract_image_from_openrouter(data)
            if img_bytes:
                saved = cls._save_bytes(img_bytes, output_path, mime)
                if saved:
                    return ImageBackendResult(
                        ok=True, provider="openrouter", model=model,
                        output_path=output_path, image_bytes=len(img_bytes),
                        mime_type=mime or cls._guess_mime(output_path), revised_prompt=revised,
                    )
                last_err = f"편집 이미지 저장 실패: {output_path}"
            else:
                last_err = f"{model} 편집 응답에 이미지 데이터가 없습니다."

        return ImageBackendResult(
            ok=False, provider="openrouter",
            model=cls.DEFAULT_OPENROUTER_IMAGE_MODEL,
            error=last_err or "OpenRouter 이미지 편집 실패",
        )

    # ------------------------------------------------------------------
    # 3-b. Google Gemini 네이티브 구현 (Nano Banana 직접 호출)
    # ------------------------------------------------------------------
    _GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

    @classmethod
    def _gemini_url(cls, model: str) -> str:
        model = (model or "gemini-2.5-flash-image").strip()
        if model.startswith("models/"):
            model = model[len("models/"):]
        return f"{cls._GEMINI_API_BASE}/models/{model}:generateContent"

    @classmethod
    def _generate_via_gemini_native(
        cls,
        prompt: str,
        output_path: str,
        aspect_ratio: str,
        model: str,
    ) -> ImageBackendResult:
        key, model = cls.gemini_native_config()
        if not key:
            return ImageBackendResult(ok=False, provider="gemini_native", error="Gemini API 키 미구성")
        content = f"{prompt}.{_QUALITY_SUFFIX} {cls._aspect_ratio_hint(aspect_ratio)}".strip()
        payload = {
            "contents": [{"parts": [{"text": content}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        url = f"{cls._gemini_url(model)}?key={key}"
        try:
            data = cls._post_json_plain(url, payload)
        except Exception as e:
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model,
                error=cls._humanize_http_error(e),
            )
        img_bytes, mime, revised = cls._extract_image_from_gemini_native(data)
        if not img_bytes:
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model,
                error="Gemini 응답에 이미지 데이터가 없습니다.",
            )
        if not cls._save_bytes(img_bytes, output_path, mime):
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model,
                error=f"이미지 저장 실패: {output_path}",
            )
        return ImageBackendResult(
            ok=True, provider="gemini_native", model=model,
            output_path=output_path, image_bytes=len(img_bytes),
            mime_type=mime or cls._guess_mime(output_path), revised_prompt=revised,
        )

    @classmethod
    def _edit_via_gemini_native(
        cls,
        prompt: str,
        source_image_path: str,
        output_path: str,
        aspect_ratio: str,
        model: str,
    ) -> ImageBackendResult:
        key, model = cls.gemini_native_config()
        if not key:
            return ImageBackendResult(ok=False, provider="gemini_native", error="Gemini API 키 미구성")
        try:
            b64_src, mime_src = cls._file_to_base64(source_image_path)
        except Exception as e:
            return ImageBackendResult(ok=False, provider="gemini_native", error=f"원본 인코딩 실패: {e}")

        content = f"{prompt}.{_QUALITY_SUFFIX} {cls._aspect_ratio_hint(aspect_ratio)}".strip()
        payload = {
            "contents": [{
                "parts": [
                    {"text": content},
                    {"inlineData": {"mimeType": mime_src, "data": b64_src}},
                ]
            }],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        url = f"{cls._gemini_url(model)}?key={key}"
        try:
            data = cls._post_json_plain(url, payload)
        except Exception as e:
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model,
                error=cls._humanize_http_error(e),
            )
        img_bytes, mime, revised = cls._extract_image_from_gemini_native(data)
        if not img_bytes:
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model,
                error="Gemini 편집 응답에 이미지 데이터가 없습니다.",
            )
        if not cls._save_bytes(img_bytes, output_path, mime):
            return ImageBackendResult(
                ok=False, provider="gemini_native", model=model,
                error=f"편집 이미지 저장 실패: {output_path}",
            )
        return ImageBackendResult(
            ok=True, provider="gemini_native", model=model,
            output_path=output_path, image_bytes=len(img_bytes),
            mime_type=mime or cls._guess_mime(output_path), revised_prompt=revised,
        )

    @classmethod
    def _extract_image_from_gemini_native(cls, data: Dict[str, Any]) -> Tuple[Optional[bytes], str, str]:
        """Gemini generateContent 응답에서 첫 inlineData 이미지 바이트를 추출한다."""
        try:
            for cand in (data.get("candidates") or []):
                parts = ((cand.get("content") or {}).get("parts")) or []
                text_acc = ""
                for part in parts:
                    inline = part.get("inlineData") or part.get("inline_data")
                    if isinstance(inline, dict) and inline.get("data"):
                        mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                        try:
                            raw = base64.b64decode(inline["data"])
                        except Exception:
                            raw = b""
                        if raw:
                            return raw, mime, text_acc[:300]
                    if part.get("text"):
                        text_acc += str(part.get("text"))
        except Exception as e:
            logger.debug(f"[ImageGenerationBackend] gemini native parse error: {e}")
        return None, "", ""

    @classmethod
    def _post_json_plain(cls, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """API 키가 URL 쿼리에 포함되는 엔드포인트용 JSON POST (Gemini 네이티브)."""
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=cls._OPENROUTER_TIMEOUT) as res:
                raw = res.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as he:
            detail = ""
            try:
                detail = he.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = ""
            raise RuntimeError(f"HTTP {he.code}: {detail}") from he

    # ------------------------------------------------------------------
    # 4. OpenAI 호환 /images/generations 구현 (옵션)
    # ------------------------------------------------------------------
    @classmethod
    def _generate_via_openai_compat(
        cls,
        prompt: str,
        output_path: str,
        aspect_ratio: str,
        model: str,
    ) -> ImageBackendResult:
        base, key, model = cls.openai_compat_config()
        if not (base and key and model):
            return ImageBackendResult(ok=False, provider="openai_compat", error="OpenAI호환 백엔드 미구성")

        size = cls._openai_size(aspect_ratio)
        url = f"{base}/images/generations"
        payload = {
            "model": model,
            "prompt": f"{prompt}.{_QUALITY_SUFFIX}",
            "n": 1,
            "size": size,
        }
        try:
            data = cls._post_json(url, payload, key)
        except Exception as e:
            return ImageBackendResult(
                ok=False, provider="openai_compat", model=model,
                error=cls._humanize_http_error(e),
            )

        img_bytes, mime = cls._extract_image_from_openai(data)
        if not img_bytes:
            return ImageBackendResult(
                ok=False, provider="openai_compat", model=model,
                error="OpenAI호환 응답에 이미지 데이터가 없습니다.",
            )
        saved = cls._save_bytes(img_bytes, output_path, mime)
        if not saved:
            return ImageBackendResult(
                ok=False, provider="openai_compat", model=model,
                error=f"이미지 저장 실패: {output_path}",
            )
        return ImageBackendResult(
            ok=True, provider="openai_compat", model=model,
            output_path=output_path, image_bytes=len(img_bytes),
            mime_type=mime or cls._guess_mime(output_path),
        )

    @classmethod
    def _edit_via_openai_compat(
        cls,
        prompt: str,
        source_image_path: str,
        output_path: str,
        aspect_ratio: str,
        model: str,
    ) -> ImageBackendResult:
        """
        OpenAI 호환 `/images/edits` (multipart) 로 기존 이미지를 편집한다.
        (gpt-image 계열 등 — OpenAI 는 편집을 반드시 multipart 로 요구한다.)
        """
        base, key, model = cls.openai_compat_config()
        if not (base and key and model):
            return ImageBackendResult(ok=False, provider="openai_compat", error="OpenAI호환 백엔드 미구성")
        try:
            import mimetypes
            import uuid

            with open(source_image_path, "rb") as f:
                raw = f.read()
            fname = os.path.basename(source_image_path)
            mime = mimetypes.guess_type(fname)[0] or "image/png"

            boundary = f"----AGYImageEdit{uuid.uuid4().hex}"
            size = cls._openai_size(aspect_ratio)

            def _field(name: str, value: str) -> bytes:
                return (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")

            body = b""
            body += _field("model", model)
            body += _field("prompt", f"{prompt}.{_QUALITY_SUFFIX}")
            body += _field("n", "1")
            body += _field("size", size)
            body += (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
            body += raw + b"\r\n"
            body += f"--{boundary}--\r\n".encode("utf-8")

            req = urllib.request.Request(
                f"{base}/images/edits",
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=cls._OPENROUTER_TIMEOUT) as res:
                text = res.read().decode("utf-8", errors="replace")
            data = json.loads(text) if text.strip() else {}
        except Exception as e:
            return ImageBackendResult(
                ok=False, provider="openai_compat", model=model,
                error=cls._humanize_http_error(e),
            )

        img_bytes, mime = cls._extract_image_from_openai(data)
        if not img_bytes:
            return ImageBackendResult(
                ok=False, provider="openai_compat", model=model,
                error="OpenAI호환 편집 응답에 이미지 데이터가 없습니다.",
            )
        if not cls._save_bytes(img_bytes, output_path, mime):
            return ImageBackendResult(
                ok=False, provider="openai_compat", model=model,
                error=f"편집 이미지 저장 실패: {output_path}",
            )
        return ImageBackendResult(
            ok=True, provider="openai_compat", model=model,
            output_path=output_path, image_bytes=len(img_bytes),
            mime_type=mime or cls._guess_mime(output_path),
        )

    # ------------------------------------------------------------------
    # 5. HTTP / 파싱 유틸
    # ------------------------------------------------------------------
    @classmethod
    def _post_json(cls, url: str, payload: Dict[str, Any], api_key: str) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/google-deepmind/antigravity",
                "X-Title": "AGY Image generation backend",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=cls._OPENROUTER_TIMEOUT) as res:
                raw = res.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as he:
            detail = ""
            try:
                detail = he.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = ""
            raise RuntimeError(f"HTTP {he.code}: {detail}") from he

    @classmethod
    def _extract_image_from_openrouter(cls, data: Dict[str, Any]) -> Tuple[Optional[bytes], str, str]:
        """OpenRouter chat.completions 응답에서 첫 이미지 바이트를 추출한다."""
        try:
            choices = data.get("choices") or []
            if not choices:
                return None, "", ""
            msg = choices[0].get("message") or {}
            images = msg.get("images") or []
            for im in images:
                url = ""
                if isinstance(im, dict):
                    url = (im.get("image_url") or {}).get("url") or im.get("url") or ""
                elif isinstance(im, str):
                    url = im
                if not url:
                    continue
                b = cls._decode_data_url(url)
                if b:
                    return b, cls._mime_from_data_url(url), str(msg.get("content") or "")[:300]
            # 일부 구현은 content 배열에 image_url 파트를 넣기도 한다.
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url") or ""
                        b = cls._decode_data_url(url)
                        if b:
                            return b, cls._mime_from_data_url(url), ""
        except Exception as e:
            logger.debug(f"[ImageGenerationBackend] openrouter parse error: {e}")
        return None, "", ""

    @classmethod
    def _extract_image_from_openai(cls, data: Dict[str, Any]) -> Tuple[Optional[bytes], str]:
        """OpenAI /images/generations 응답에서 첫 이미지 바이트를 추출한다."""
        try:
            items = data.get("data") or []
            if not items:
                return None, ""
            first = items[0]
            if isinstance(first, dict):
                b64 = first.get("b64_json")
                if b64:
                    return base64.b64decode(b64), "image/png"
                url = first.get("url")
                if url and url.startswith("data:"):
                    return cls._decode_data_url(url), cls._mime_from_data_url(url)
                if url and url.startswith("http"):
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=cls._OPENROUTER_TIMEOUT) as res:
                        return res.read(), res.headers.get_content_type() or "image/png"
        except Exception as e:
            logger.debug(f"[ImageGenerationBackend] openai parse error: {e}")
        return None, ""

    @classmethod
    def _decode_data_url(cls, url: str) -> Optional[bytes]:
        try:
            if url.startswith("data:") and "," in url:
                return base64.b64decode(url.split(",", 1)[1])
            if url.startswith("http"):
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=cls._OPENROUTER_TIMEOUT) as res:
                    return res.read()
        except Exception as e:
            logger.debug(f"[ImageGenerationBackend] data url decode failed: {e}")
        return None

    @staticmethod
    def _mime_from_data_url(url: str) -> str:
        if url.startswith("data:") and ";" in url:
            return url[5:url.index(";")]
        return ""

    # 실제 래스터 이미지의 최소 바이트 (GIF 매직 등). 이보다 작으면 이미지가 아니다.
    _MIN_IMAGE_BYTES = int(os.getenv("IMAGE_MIN_BYTES", "64"))
    # 이미지 매직바이트 (PNG / JPEG / GIF / WEBP / BMP)
    _IMAGE_MAGICS = (
        b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM",
    )

    @classmethod
    def _looks_like_image(cls, img_bytes: bytes) -> bool:
        """매직바이트 기반 이미지 판정 (작은 이미지를 크기만으로 버리지 않기 위함)."""
        if not img_bytes:
            return False
        if img_bytes.startswith(b"RIFF") and img_bytes[8:12] == b"WEBP":
            return True
        return any(img_bytes.startswith(m) for m in cls._IMAGE_MAGICS)

    @classmethod
    def _save_bytes(cls, img_bytes: bytes, output_path: str, mime: str) -> bool:
        """
        실제 이미지 바이트를 검증(PIL) 후 PNG 로 정규화 저장한다.

        [정직성/정확성] 과거에는 `len(img_bytes) < 256` 을 무조건 실패로 처리하여
        **정상적으로 작은 실제 이미지**(작은 해상도·단색 JPEG 등)까지 '저장 실패'로
        버렸다. 이제는 매직바이트로 이미지 여부를 판정하므로 작은 이미지도 보존한다.
        """
        if not img_bytes:
            return False
        if len(img_bytes) < cls._MIN_IMAGE_BYTES and not cls._looks_like_image(img_bytes):
            return False
        try:
            from PIL import Image
            with Image.open(io.BytesIO(img_bytes)) as im:
                im.load()
                width, height = im.size
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                out = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
                out.save(output_path, "PNG")
                logger.info(
                    f"[ImageGenerationBackend] 실제 AI 이미지 저장 완료: {output_path} "
                    f"({width}x{height}, {len(img_bytes)} bytes)"
                )
                return True
        except Exception as e:
            logger.warning(f"[ImageGenerationBackend] 이미지 검증/저장 실패: {e}")
            # PIL 파싱 불가라도 매직바이트가 이미지면 원본 저장
            if cls._looks_like_image(img_bytes):
                try:
                    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img_bytes)
                    return True
                except Exception:
                    return False
            return False

    @classmethod
    def _file_to_base64(cls, path: str) -> Tuple[str, str]:
        with open(path, "rb") as f:
            raw = f.read()
        ext = os.path.splitext(path)[1].lower()
        mime = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        }.get(ext, "image/png")
        return base64.b64encode(raw).decode("utf-8"), mime

    @staticmethod
    def _guess_mime(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}.get(ext, "image/png")

    @staticmethod
    def _aspect_ratio_hint(aspect_ratio: str) -> str:
        ar = (aspect_ratio or "1:1").replace(" ", "")
        return {
            "16:9": "Wide 16:9 landscape composition.",
            "9:16": "Tall 9:16 portrait composition.",
            "4:3": "4:3 composition.",
            "3:4": "3:4 portrait composition.",
            "1:1": "Square 1:1 composition.",
        }.get(ar, "")

    @staticmethod
    def _openai_size(aspect_ratio: str) -> str:
        ar = (aspect_ratio or "1:1").replace(" ", "")
        return {
            "16:9": "1536x1024",
            "9:16": "1024x1536",
            "4:3": "1024x1024",
            "3:4": "1024x1536",
            "1:1": "1024x1024",
        }.get(ar, "1024x1024")

    @staticmethod
    def _humanize_http_error(e: Exception) -> str:
        msg = str(e)
        low = msg.lower()
        if "402" in msg or "credit" in low or "insufficient" in low or "크레딧" in msg:
            return (
                "OpenRouter 크레딧 부족(402) — OPENROUTER_API_KEY 크레딧을 충전하거나 "
                "2차 백엔드(GEMINI_API_KEY=Google Nano Banana) / IMAGE_API_BASE·IMAGE_API_KEY·IMAGE_API_MODEL 를 설정하세요."
            )

        if "api key not valid" in low or "api_key_invalid" in low or "invalid api key" in low or "401" in msg:
            return "이미지 API 키 인증 실패(401/API_KEY_INVALID)"
        if "403" in msg:
            return "이미지 API 접근 거부(403)"
        if "resource_exhausted" in low or "quota" in low or "429" in msg or "rate limit" in low:
            return "이미지 생성 요청 한도/쿼터 초과(429/RESOURCE_EXHAUSTED)"
        if "timed out" in low or "timeout" in low:
            return "이미지 생성 타임아웃"
        return msg[:300] if msg else "알 수 없는 오류"
