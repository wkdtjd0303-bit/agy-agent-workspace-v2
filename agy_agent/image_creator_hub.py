#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_creator_hub.py

🎨 Image generator Hub — 실제 AI(Nano Banana 계열) 이미지 생성·수정 전용 도구.

역할:
  - Coder / QA 에이전트가 작업 중 이미지 생성·수정이 필요할 때 호출하는 전용 도구.
  - **실제 이미지 생성 백엔드**를 사용한다 (`image_generation_backend.py`):
      · OpenRouter 이미지 모델 (기본 `google/gemini-2.5-flash-image` = Nano Banana 계열)
      · (옵션) OpenAI 호환 `/images/generations`
  - 생성된 이미지를 프로젝트 네이티브 리소스 폴더로 배치하고, Coder 가 소스코드에
    바인딩할 수 있도록 경로·스니펫을 반환한다.

[🚨 정직성 원칙]
  - 실제 AI 생성이 실패했다면 조용히 도형 렌더로 대체하지 않는다.
    반드시 `fallback_used=True` + `message`(실패 사유)를 남기고, 호출자가
    Discord 에 "⚠️ [실제 AI 이미지 생성 실패] …" 를 표기하도록 한다.
  - 절차적 에셋(PIL 도형)은 사용자가 명시적으로 요청(`allow_procedural=True`)했거나
    실패를 명시적으로 고지한 뒤의 '임시 대체'로만 사용한다.

호출 프로토콜 (Coder / QA 가 본문에 지시문을 남기면 오케스트레이터가 감지):
  [IMAGE_REQUEST: {"prompt": "우주 테마 앱 로고", "asset_name": "app_logo", "aspect_ratio": "1:1"}]
  [IMAGE_REQUEST: app_logo | 1:1 | 우주 테마 앱 로고]
  [IMAGE_REQUEST: 우주 테마 앱 로고]
  [IMAGE_EDIT: {"prompt": "배경을 더 어둡게", "source_image": "app/src/main/res/drawable/hero.png"}]

동작:
  1. 요청 파싱 (JSON / 파이프 / 자유 텍스트 지원)
  2. 실제 AI 이미지 백엔드로 생성 (실측 결과 바이트를 디스크에 저장)
  3. 저장 파일 검증(존재/크기/PIL) 후 리소스 폴더 배치 및 코드 스니펫 생성
  4. 결과를 Discord 채널 + #nano-banana-image 스튜디오에 아카이빙
"""

from __future__ import annotations

import os
import re
import json
import time
import shutil
import logging
import asyncio
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from hybrid_asset_pipeline import HybridAssetPipeline, AssetPlacementResult
from image_generation_backend import ImageGenerationBackend

logger = logging.getLogger("ImageCreatorHub")

IMAGE_REQUEST_OPEN = "[IMAGE_REQUEST"
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
# 이미지로 인정할 최소 바이트. 작은 실제 이미지(저해상도/단색)를 크기만으로
# 버리지 않도록 매직바이트 판정과 함께 사용한다.
_MIN_IMAGE_BYTES = int(os.getenv("IMAGE_MIN_BYTES", "64"))
_DEFAULT_TIMEOUT = float(os.getenv("IMAGE_GENERATION_TIMEOUT", "420.0"))

# 출처(Source) 식별자
SOURCE_AI = "ai_backend"            # 실제 AI 이미지 생성 성공
SOURCE_AGY = "agy_artifact"         # AGY 가 실제 이미지 산출물을 남긴 경우(옵션)
SOURCE_EXISTING = "existing_local_asset"  # 재생성 실패 → 디스크에 이미 존재하는 실이미지 재사용(정직 고지)
SOURCE_PROCEDURAL = "procedural_fallback"  # 절차적 에셋(임시 대체, 정직 고지)
SOURCE_ERROR = "error"

# --- 엔진(agy) '모델 라벨' → 실제 이미지 백엔드 모델 ID 매핑 ---------------------
#   agy CLI 의 `gemini-3.8-flash-high` 같은 라벨은 OpenRouter/Gemini HTTP 이미지
#   백엔드의 실제 모델 ID 가 아니다. 그대로 넘기면 백엔드가 무시하고 기본 체인으로
#   떨어져, 사용자가 `!model` 에서 고른 이미지 모델/엔진이 **실제 생성에 반영되지
#   않는** 문제가 있었다. 여기서 실제 Nano Banana 계열 ID 로 해석한다.
#   `IMAGE_MODEL_ALIASES` 환경변수(JSON 또는 `k=v,k=v`)로 확장/덮어쓰기 가능.
_DEFAULT_IMAGE_MODEL_ALIASES: Dict[str, str] = {
    "gemini-3.8-flash-high": "google/gemini-2.5-flash-image",
    "gemini-3.1-flash-image": "google/gemini-3.1-flash-image",
    "gemini-3-pro-image": "google/gemini-3-pro-image",
    "gemini-2.5-flash-image": "google/gemini-2.5-flash-image",
    "nano-banana": "google/gemini-2.5-flash-image",
    "nano_banana": "google/gemini-2.5-flash-image",
}


@dataclass
class ImageGenerationResult:
    """이미지 생성 결과 1건."""
    ok: bool
    asset_name: str
    prompt: str
    aspect_ratio: str = "1:1"
    absolute_path: Optional[str] = None
    relative_path: str = ""
    width: int = 0
    height: int = 0
    source: str = SOURCE_ERROR  # ai_backend | agy_artifact | procedural_fallback | error
    backend: str = ""           # "openrouter" | "openai_compat" | "" 등
    model: str = ""             # 실제 사용된 백엔드 모델 ID
    engine: str = ""            # 사용자가 고른 엔진 (agy/opencode/hermes/...) — 표시용
    is_real_ai: bool = False    # 실제 AI 생성 여부 (절차적 도형이면 False)
    fallback_used: bool = False  # 실제 AI 실패 → 임시 대체 발생 여부
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImageCreatorHub:
    """실제 AI 이미지 생성(Nano Banana 계열) 기반 이미지 도구 허브."""

    # ------------------------------------------------------------------
    # 0. 백엔드 가용성
    # ------------------------------------------------------------------
    @classmethod
    def backend_status(cls) -> str:
        """현재 구성된 실제 이미지 백엔드 설명 문자열."""
        return ImageGenerationBackend.describe_backends()

    @classmethod
    def real_backend_available(cls) -> bool:
        return ImageGenerationBackend.real_backend_available()

    # ------------------------------------------------------------------
    # 0-b. image 역할의 엔진/모델 해석 (사용자 선택 실반영)
    # ------------------------------------------------------------------
    @classmethod
    def image_model_aliases(cls) -> Dict[str, str]:
        """agy 등 '모델 라벨' → 실제 이미지 백엔드 모델 ID 매핑 (환경변수로 확장 가능)."""
        aliases = {k.lower(): v for k, v in _DEFAULT_IMAGE_MODEL_ALIASES.items()}
        raw = (os.getenv("IMAGE_MODEL_ALIASES", "") or "").strip()
        if not raw:
            return aliases
        try:
            if raw.startswith("{"):
                data = json.loads(raw)
                if isinstance(data, dict):
                    aliases.update({str(k).lower(): str(v) for k, v in data.items()})
                    return aliases
            for pair in raw.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k.strip() and v.strip():
                        aliases[k.strip().lower()] = v.strip()
        except Exception as e:
            logger.debug(f"[ImageCreatorHub] IMAGE_MODEL_ALIASES 파싱 실패: {e}")
        return aliases

    @classmethod
    def resolve_role_engine_model(cls) -> Tuple[str, str]:
        """
        image 역할의 실제 사용 엔진/모델을 해석한다.

        런타임 config(.env + `!model` 동기화 결과)를 우선 사용하고, 값이 비어 있으면
        GlobalModelManager 에서 직접 조회한다. (`!model` 에서 Image generator 의
        엔진/모델을 바꾸면 즉시 이 값에 반영된다.)
        """
        engine = (getattr(config, "IMAGE_ENGINE", "") or "").strip()
        model = (getattr(config, "IMAGE_MODEL", "") or "").strip()
        if not engine or not model:
            try:
                from model_manager import GlobalModelManager
                if not engine:
                    engine = (GlobalModelManager.get_role_engine("image") or "").strip()
                if not model:
                    model = (GlobalModelManager.get_role_model("image") or "").strip()
            except Exception:
                pass
        engine = (engine or "agy").lower()
        model = model or "gemini-3.8-flash-high"
        return engine, model

    @classmethod
    def resolve_backend_model_hint(cls, engine: str, model: str) -> str:
        """
        image 역할 모델을 실제 이미지 백엔드 모델 ID 로 해석한다.

          · `provider/model` (OpenRouter 등) → 그대로 사용
          · 알려진 agy 라벨(`gemini-3.8-flash-high` 등) → 실제 Nano Banana 모델 ID
          · 알 수 없는 라벨 → "" (백엔드 기본 체인 사용 — 잘못된 ID 로 호출해
            불필요하게 실패하는 것을 방지)
        """
        m = (model or "").strip()
        if not m:
            return ""
        if "/" in m:
            return m
        return cls.image_model_aliases().get(m.lower(), "")

    # ------------------------------------------------------------------
    # 1. 요청 파싱
    # ------------------------------------------------------------------
    @classmethod
    def parse_image_requests(cls, text: str) -> List[Dict[str, str]]:
        """
        에이전트 본문에서 [IMAGE_REQUEST: ...] 지시문을 파싱한다.
        JSON, 파이프 구분, 자유 텍스트 포맷을 모두 지원한다.

        [오탐(false-positive) 원천 차단]
        프롬프트에는 '호출법'을 설명하는 **리터럴 예시 태그**
        (`[IMAGE_REQUEST: {"prompt": "원하는 이미지 설명", ...}]`)가 포함된다.
        에이전트가 이 예시를 인용만 해도 파서가 '실제 요청'으로 오인하여,
        이미지가 필요 없는 턴에도 백엔드 호출/바인딩 재작업 루프가 돌았다.
        따라서 JSON 내부에 예시 플레이스홀더 값이 그대로 남아 있는 태그는
        인용(quotation)으로 간주해 제거한다.

        규칙:
          · `...` / `…` 등 말줄임 포함        → 인용
          · `prompt` 가 예시 문구 그대로       → 인용
          · `{"prompt": "..."}` 처럼 빈 값     → 인용
        """
        requests: List[Dict[str, str]] = []
        if not text:
            return requests

        # JSON/파이프 등 본문이 길어 개행이 섞일 수 있으므로 태그 '바깥 대괄호'까지
        # 넉넉히(최대 800자) 캡처한다. 기존 `[^\]\n]*` 는 줄바꿈이 들어간 JSON 을
        # 놓쳐 '조용한 이미지 요청 유실'을 일으켰다.
        body_pat = r"([^\[\]]{1,800}?)"

        def _collect(tag: str, require_source: bool) -> List[Dict[str, str]]:
            out: List[Dict[str, str]] = []
            # (a) 대괄호로 닫힌 정형 태그
            for m in re.finditer(r"\[" + tag + r"\s*:?\s*" + body_pat + r"\]", text, re.DOTALL):
                raw = " ".join((m.group(1) or "").split()).strip()
                if cls.is_quoted_marker(raw):
                    continue
                parsed = cls._parse_single(raw)
                if parsed and parsed.get("prompt"):
                    if require_source and not parsed.get("source_image"):
                        continue
                    out.append(parsed)
            # (b) 대괄호를 닫지 않고 줄 끝까지만 남긴 관용 표기
            for m in re.finditer(r"\[" + tag + r"\]\s*([^\n\]]+)", text):
                raw = " ".join((m.group(1) or "").split()).strip()
                if cls.is_quoted_marker(raw):
                    continue
                parsed = cls._parse_single(raw)
                if parsed and parsed.get("prompt"):
                    if require_source and not parsed.get("source_image"):
                        continue
                    out.append(parsed)
            return out

        requests.extend(_collect("IMAGE_REQUEST", require_source=False))
        # 이미지 '수정(편집)' 전용 태그 — 반드시 원본(source_image)이 있어야 유효하다.
        requests.extend(_collect("IMAGE_EDIT", require_source=True))

        return requests

    # 프롬프트 '호출법' 예시에 등장하는 플레이스홀더 값 (인용 오탐 판정용)
    _EXAMPLE_PLACEHOLDERS = (
        "원하는 이미지 설명",
        "이미지 설명",
        "수정 지시",
        "이미지 프롬프트",
        "리소스 경로 또는 파일명",
        "경로 또는 파일명",
        "image description",
        "describe the image",
        "edit instruction",
    )

    @classmethod
    def is_quoted_marker(cls, raw: str) -> bool:
        """
        파싱된 태그 본문이 '실제 요청'이 아니라 프롬프트에 적힌 **예시/호출법 인용**인지 판정한다.

        에이전트가 지침서의 리터럴 예시(`[IMAGE_REQUEST: {"prompt": "원하는 이미지 설명", ...}]`)를
        본문에 그대로 옮겨 적는 것만으로 이미지 생성 루프가 돌던 토큰 낭비를 차단한다.
        """
        body = (raw or "").strip()
        if not body:
            return True

        # 1) 말줄임표(... / …) 포함 → 미완성 예시 인용
        if "..." in body or "…" in body:
            return True

        # 2) JSON 본문이면 필드 단위로 정밀 검사
        stripped = body.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except Exception:
                return False
            if isinstance(data, dict):
                prompt = str(data.get("prompt") or data.get("description") or "").strip()
                # 3) 빈 프롬프트 (`{"prompt": ""}` / `{"prompt": "..."}`)
                if not prompt:
                    return True
                low = prompt.lower()
                if any(ph.lower() in low for ph in cls._EXAMPLE_PLACEHOLDERS):
                    return True
                src = str(
                    data.get("source_image")
                    or data.get("edit_of")
                    or data.get("base_image")
                    or data.get("source")
                    or data.get("image")
                    or ""
                ).strip()
                # 4) 편집 예시의 원본 자리표시자
                if src and any(ph.lower() in src.lower() for ph in cls._EXAMPLE_PLACEHOLDERS):
                    return True
            return False

        # 5) 자유 텍스트/파이프: 플레이스홀더 문구 또는 빈 값이면 인용
        low = body.lower()
        if any(ph.lower() in low for ph in cls._EXAMPLE_PLACEHOLDERS):
            return True
        return False

    @classmethod
    def is_image_intent(cls, user_prompt: str) -> bool:
        """
        [불필요한 이미지 루프 차단] 사용자의 원본 요청이 실제로 '이미지/시각 에셋'과
        관련이 있는지 판정한다.

        배경: 에이전트(특히 AGY)가 '이미지 생성 도구 호출법' 지침 문구를 본문에
        인용하기만 해도 이미지 루프가 돌아 불필요한 토큰/백엔드 호출이 발생했다.
        (예: "이미지 생성 봇 초대 안 해도 되나?" 같은 이미지 생성과 무관한 질문)

        여기서는 **사용자 원문**에 이미지 대상(로고/아이콘/배너/브랜드/에셋/그려줘 등)
        또는 이미지 생성/편집 요청 의도가 실제로 있는 경우에만 True 를 반환한다.
        """
        p = (user_prompt or "").lower()
        if not p:
            return False

        # 0) 이미지 생성 '도구/봇'에 대한 메타 질문(운영/설정/동작 문의)은 이미지 작업이 아니다.
        #    (예: "이미지 생성 봇 초대 안 해도 되나?", "이미지 생성기는 언제 동작해?")
        if any(k in p for k in ("image generator", "이미지 생성기", "이미지 생성 봇", "이미지 봇", "이미지 생성 봇")):
            meta_markers = (
                "초대", "설정", "동작", "작동", "토큰", "쿼터", "왜", "안 되", "안돼",
                "괜찮", "맞아", "질문", "언제", "어떻게", "뭐야", "무엇", "인가", "인지",
            )
            if any(m in p for m in meta_markers):
                return False
            # 도구를 '호출/사용'하라는 지시로 보이면 이미지 작업으로 인정
            return any(k in p for k in ("사용", "호출", "돌려", "실행", "generate_image", "생성해", "만들어"))

        # 1) 명시적 생성/편집 지시 (시각 대상 포함)
        if any(k in p for k in (
            "그려", "그려줘", "그림", "이미지 생성", "이미지 만들어", "이미지 수정",
            "로고", "아이콘", "배너", "에셋", "일러스트", "썸네일", "스프라이트",
            "draw", "generate image", "image generation", "logo", "icon", "banner",
            "asset", "illustration", "thumbnail", "sprite",
        )):
            return True
        return False

    @classmethod
    def _parse_single(cls, raw: str) -> Optional[Dict[str, str]]:
        raw = (raw or "").strip()
        if not raw:
            return None

        # 1) JSON 형태
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    prompt = str(data.get("prompt") or data.get("description") or "").strip()
                    if not prompt:
                        return None
                    req: Dict[str, str] = {
                        "prompt": prompt,
                        "asset_name": str(data.get("asset_name") or data.get("name") or "").strip(),
                        "aspect_ratio": str(data.get("aspect_ratio") or data.get("ratio") or "1:1").strip() or "1:1",
                    }
                    # 이미지 편집(수정) 지원: source_image / edit_of / source / image 등 별칭 허용
                    src = str(
                        data.get("source_image")
                        or data.get("edit_of")
                        or data.get("base_image")
                        or data.get("source")
                        or data.get("image")
                        or ""
                    ).strip()
                    if src:
                        req["source_image"] = src
                    return req
            except Exception:
                raw = raw.strip("{}").strip()

        # 2) 파이프 구분: name | ratio | prompt  (필드 순서 유연)
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            asset_name, ratio, prompt = "", "", ""
            for p in parts:
                if re.fullmatch(r"\d{1,2}\s*:\s*\d{1,2}", p):
                    ratio = p.replace(" ", "")
                elif not asset_name and re.fullmatch(r"[A-Za-z0-9_\-]+", p) and len(p) <= 60:
                    asset_name = p
                else:
                    prompt = (prompt + " " + p).strip()
            if not prompt:
                prompt = " ".join(parts).strip()
            return {"prompt": prompt, "asset_name": asset_name, "aspect_ratio": ratio or "1:1"}

        # 3) 자유 텍스트
        return {"prompt": raw, "asset_name": "", "aspect_ratio": "1:1"}

    # ------------------------------------------------------------------
    # 2. 이미지 생성
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_source_image(cls, source_image: str, workspace: str) -> str:
        """
        이미지 편집 원본을 '실제 존재하는 절대경로'로 해석한다.

        허용 입력:
          · 절대경로 그대로
          · 워크스페이스 기준 상대경로 (예: `app/src/main/res/drawable/hero.png`)
          · 리소스 디렉터리 기준 파일명 (예: `hero.png`)
          · 프로젝트 어디에 있든 동일 파일명 (drawable 하위 폴더 포함)
          · http(s) 원격 URL (임시 파일로 다운로드)

        실패 시 "" 를 반환한다. 호출부(`generate`)는 이 경우 **새 이미지 생성으로
        무단 강등하지 않고** 정직한 실패를 반환한다(수정 요청이 조용히 다른 그림이
        되는 것을 원천 차단).
        """
        src = (source_image or "").strip().strip("`\"'")
        if not src:
            return ""

        # 1) 원격 URL → 임시 파일로 내려받아 편집 입력으로 사용
        if re.match(r"^https?://", src, re.IGNORECASE):
            try:
                import urllib.request as _urlreq
                import tempfile
                with _urlreq.urlopen(src, timeout=30) as _res:  # noqa: S310 (사용자 지정 편집 원본)
                    data = _res.read()
                if data and len(data) >= _MIN_IMAGE_BYTES:
                    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="img_edit_src_")
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    return tmp
            except Exception as e:
                logger.warning(f"[ImageCreatorHub] 원격 편집 원본 다운로드 실패: {e}")
            return ""

        candidates: List[str] = []
        if os.path.isabs(src):
            candidates.append(src)
        else:
            candidates.append(os.path.join(workspace, src))
            try:
                res_dir = HybridAssetPipeline.resolve_resource_directory(workspace)
                candidates.append(os.path.join(res_dir, src))
                candidates.append(os.path.join(res_dir, os.path.basename(src)))
            except Exception:
                pass
            # 프로젝트 내 동일 파일명 탐색 (drawable 등 하위 폴더 대응)
            try:
                base = os.path.basename(src)
                for root, _dirs, files in os.walk(workspace):
                    if base in files:
                        candidates.append(os.path.join(root, base))
                        break
            except Exception:
                pass
            candidates.append(os.path.abspath(src))

        for c in candidates:
            try:
                if c and os.path.isfile(c):
                    return os.path.abspath(c)
            except Exception:
                continue
        return ""

    @classmethod
    async def generate(
        cls,
        prompt: str,
        workspace: str,
        asset_name: str = "",
        aspect_ratio: str = "1:1",
        session: Optional[Any] = None,
        on_status: Optional[Any] = None,
        source_image: str = "",
        allow_procedural: bool = False,
    ) -> ImageGenerationResult:
        """
        이미지 1건을 실제 AI 백엔드로 생성하여 프로젝트 리소스 폴더에 배치한다.

        Args:
            source_image: 지정 시 해당 이미지를 입력으로 AI 편집(수정) 수행.
            allow_procedural: True 일 때만 실제 AI 실패 시 절차적 에셋으로 '임시 대체'
                              (이 경우에도 fallback_used=True 로 정직 고지). 기본 False.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return ImageGenerationResult(ok=False, asset_name="", prompt="", message="이미지 프롬프트가 비어 있습니다.", source=SOURCE_ERROR)

        workspace = os.path.abspath(os.path.expanduser(workspace or config.DEFAULT_WORKSPACE))
        os.makedirs(workspace, exist_ok=True)

        clean_name = HybridAssetPipeline.sanitize_asset_name(asset_name) if asset_name else cls._name_from_prompt(prompt)
        if not clean_name:
            clean_name = f"ai_image_{int(time.time())}"

        # image 역할에 배정된 엔진/모델(`!model` 선택 또는 .env 기본값)을 그대로 사용한다.
        engine, model = cls.resolve_role_engine_model()
        # agy 라벨(예: gemini-3.8-flash-high)은 실제 백엔드 모델 ID 로 해석해 전달한다.
        backend_model_hint = cls.resolve_backend_model_hint(engine, model)
        backend_desc = cls.backend_status()
        if not backend_desc:
            backend_desc = "실제 이미지 백엔드 미구성"

        # 이미지 '수정' 요청이면 원본을 실제 존재하는 경로로 해석한다.
        # 해석 실패 시 새 이미지 생성으로 조용히 강등하지 않고 정직하게 실패한다.
        edit_requested = bool((source_image or "").strip())
        resolved_source = cls._resolve_source_image(source_image, workspace) if edit_requested else ""
        if edit_requested and not resolved_source:
            await cls._notify(
                on_status,
                f"⚠️ **[Image generator]** 편집 원본을 찾을 수 없어 수정을 중단했습니다: `{source_image}`",
            )
            return ImageGenerationResult(
                ok=False,
                asset_name=clean_name,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                source=SOURCE_ERROR,
                message=(
                    f"이미지 편집(수정) 원본을 찾을 수 없습니다: `{source_image}`. "
                    "새 이미지 생성으로 대체하지 않았습니다. 워크스페이스 기준 경로를 확인하세요."
                ),
            )
        editing = bool(resolved_source)
        verb = "수정" if editing else "생성"
        role_model_desc = backend_model_hint or model
        await cls._notify(
            on_status,
            f"🎨 **[Image generator]** `{clean_name}` 이미지 {verb} 시작 — "
            f"엔진: `{engine}` · 모델: `{role_model_desc}` · 백엔드: {backend_desc}",
        )

        start_ts = time.time()
        target_dir = HybridAssetPipeline.resolve_resource_directory(workspace)
        os.makedirs(target_dir, exist_ok=True)
        expected = os.path.join(target_dir, f"{clean_name}.png")

        # --- 1순위: 실제 AI 이미지 백엔드 (동기 HTTP → thread) ---
        backend_res = None
        try:
            if editing:
                backend_res = await asyncio.to_thread(
                    ImageGenerationBackend.edit_image,
                    prompt, resolved_source, expected, aspect_ratio, backend_model_hint,
                )
            else:
                backend_res = await asyncio.to_thread(
                    ImageGenerationBackend.generate_image,
                    prompt, expected, aspect_ratio, backend_model_hint,
                )
        except Exception as e:
            logger.exception(f"[ImageCreatorHub] 실제 이미지 백엔드 호출 예외: {e}")
            backend_res = None

        if backend_res and backend_res.ok and os.path.isfile(expected) and os.path.getsize(expected) >= _MIN_IMAGE_BYTES:
            placement = cls._place_existing_image(expected, workspace, clean_name, aspect_ratio)
            if placement:
                await cls._notify(on_status, f"✅ **[Image generator]** `{clean_name}.png` 실제 AI 이미지 생성 완료 ({backend_res.provider}/{backend_res.model})")
                return ImageGenerationResult(
                    ok=True,
                    asset_name=clean_name,
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    absolute_path=placement.absolute_path,
                    relative_path=placement.relative_path,
                    width=placement.width,
                    height=placement.height,
                    source=SOURCE_AI,
                    backend=backend_res.provider,
                    model=backend_res.model,
                    engine=engine,
                    is_real_ai=True,
                    fallback_used=False,
                    message=f"실제 AI 이미지 생성 완료 ({backend_res.provider} · {backend_res.model})"
                            + (" · 원본 편집" if editing else ""),
                )

        # --- (옵션) AGY 가 실제 이미지 산출물을 남긴 경우: 명시 보고(SAVED_IMAGE:)만 인정 ---
        agy_produced = None
        if engine == "agy":
            try:
                agy_produced = await cls._try_agy_explicit_artifact(
                    prompt=prompt, workspace=workspace, asset_name=clean_name,
                    aspect_ratio=aspect_ratio, model=model, session=session, on_status=on_status,
                )
            except Exception as e:
                logger.debug(f"[ImageCreatorHub] AGY explicit artifact path skipped: {e}")
        if agy_produced and os.path.isfile(agy_produced) and os.path.getsize(agy_produced) >= _MIN_IMAGE_BYTES:
            placement = cls._place_existing_image(agy_produced, workspace, clean_name, aspect_ratio)
            if placement:
                await cls._notify(on_status, f"✅ **[Image generator]** `{clean_name}.png` AGY 산출물 배치 완료")
                return ImageGenerationResult(
                    ok=True, asset_name=clean_name, prompt=prompt, aspect_ratio=aspect_ratio,
                    absolute_path=placement.absolute_path, relative_path=placement.relative_path,
                    width=placement.width, height=placement.height,
                    source=SOURCE_AGY, backend="agy", model=model, engine=engine, is_real_ai=True,
                    fallback_used=False,
                    message="AGY CLI 가 실제 이미지 산출물(SAVED_IMAGE)을 보고하여 배치했습니다.",
                )

        backend_error = (backend_res.error if backend_res else "이미지 백엔드 호출 실패")

        # --- 1.5순위: [기존 에셋 재사용 — 실패 오보고 방지] -------------------------
        #   실제 AI 재생성은 실패했더라도, 동일 이름의 **유효한 실이미지**가 이미
        #   프로젝트 리소스 폴더(expected)에 존재하면 실패로 오보고하지 않고 그
        #   검증된 에셋을 재사용한다. (예: 후보 체인/이전 시도의 성공 산출물이 남아
        #   있는 경우) 이는 사령관 지시의 "실패 경로 참조 금지 + 기존 에셋으로 대체"를
        #   기계적으로 보장하며, 멀쩡한 에셋을 버리고 CSS 플레이스홀더로 대체하는
        #   손실을 막는다. (is_real_ai=True · fallback_used=True 로 정직 고지)
        reused = cls._reuse_existing_asset(expected, workspace, clean_name, aspect_ratio)
        if reused:
            await cls._notify(
                on_status,
                f"♻️ **[Image generator]** `{clean_name}.png` — 실제 AI 재생성 실패"
                f"(사유: {backend_error})했으나, 디스크에 이미 존재하는 유효 에셋을 재사용합니다.",
            )
            return ImageGenerationResult(
                ok=True, asset_name=clean_name, prompt=prompt, aspect_ratio=aspect_ratio,
                absolute_path=reused.absolute_path, relative_path=reused.relative_path,
                width=reused.width, height=reused.height,
                source=SOURCE_EXISTING,
                backend=(backend_res.provider if backend_res else ""),
                model=(backend_res.model if backend_res else ""),
                engine=engine, is_real_ai=True, fallback_used=True,
                message=(
                    f"실제 AI 이미지 재생성 실패(사유: {backend_error}) — "
                    f"디스크에 이미 존재하던 실이미지 에셋을 재사용했습니다."
                ),
            )

        # --- 2순위(명시 허용 시에만): 절차적 에셋으로 '임시 대체' + 정직 고지 ---
        if allow_procedural:
            logger.warning(f"[ImageCreatorHub] 실제 AI 실패 → 명시 허용에 따라 임시 절차적 대체: {backend_error}")
            await cls._notify(
                on_status,
                f"⚠️ **[실제 AI 이미지 생성 실패]** 임시 절차적 에셋으로 대체합니다 (사유: {backend_error})",
            )
            try:
                placement = await asyncio.to_thread(
                    HybridAssetPipeline.generate_and_place_asset,
                    workspace=workspace,
                    asset_name=clean_name,
                    category="illustration",
                    description=prompt,
                    aspect_ratio=aspect_ratio,
                )
                if placement and placement.absolute_path and os.path.isfile(placement.absolute_path):
                    return ImageGenerationResult(
                        ok=True, asset_name=clean_name, prompt=prompt, aspect_ratio=aspect_ratio,
                        absolute_path=placement.absolute_path, relative_path=placement.relative_path,
                        width=placement.width, height=placement.height,
                        source=SOURCE_PROCEDURAL, backend="procedural", model="", engine=engine,
                        is_real_ai=False, fallback_used=True,
                        message=f"⚠️ 실제 AI 이미지 생성 실패로 '임시 절차적 에셋'으로 대체했습니다 (사유: {backend_error})",
                    )
            except Exception as e:
                logger.exception(f"[ImageCreatorHub] 절차적 대체 생성 실패: {e}")

        # --- 실패 (조용한 도형 렌더 금지) ---
        return ImageGenerationResult(
            ok=False,
            asset_name=clean_name,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            source=SOURCE_ERROR,
            backend=(backend_res.provider if backend_res else ""),
            model=(backend_res.model if backend_res else ""),
            engine=engine,
            is_real_ai=False,
            fallback_used=False,
            message=f"실제 AI 이미지 생성에 실패했습니다. 사유: {backend_error}",
        )

    # ------------------------------------------------------------------
    # 3. (옵션) AGY 실제 이미지 산출물 — '명시 보고(SAVED_IMAGE:)'만 인정
    # ------------------------------------------------------------------
    @classmethod
    async def _try_agy_explicit_artifact(
        cls,
        prompt: str,
        workspace: str,
        asset_name: str,
        aspect_ratio: str,
        model: str,
        session: Optional[Any],
        on_status: Optional[Any],
    ) -> Optional[str]:
        """
        AGY CLI 가 '실제 이미지 파일'을 만들고 `SAVED_IMAGE: <절대경로>` 로 **명시 보고**한
        경우에만 그 경로를 인정한다. 워크스페이스 전체 신규 이미지 스캔(오인 위험)은 하지 않는다.
        """
        from engine_manager import EngineManager

        adapter = EngineManager.get_adapter("agy")
        if not adapter or not adapter.is_installed():
            return None

        target_dir = HybridAssetPipeline.resolve_resource_directory(workspace)
        target_path = os.path.join(target_dir, f"{asset_name}.png")

        directive = (
            "🎨 [IMAGE GENERATOR TOOL]\n"
            "당신에게 실제 이미지 생성 도구(내장 이미지 생성기)가 있다면 아래 요청을 이미지로 만드십시오.\n"
            f"- 이미지 요청: {prompt}\n"
            f"- 종횡비(Aspect Ratio): {aspect_ratio}\n"
            f"- 저장 희망 경로: {target_path}\n"
            "- 만약 실제 이미지 생성 도구가 없다면 아무 이미지도 만들지 말고 정확히 `NO_IMAGE_TOOL` 한 줄만 출력하십시오.\n"
            "- 실제 이미지를 만들었다면 다음 형식의 한 줄을 반드시 남기십시오: `SAVED_IMAGE: <저장한 파일의 절대경로>`\n"
        )

        if session is None:
            session = cls._get_light_session(workspace)

        from quota_firewall import explicit_tool

        async def _dummy_notify(_msg: str):
            return None

        notify = on_status or _dummy_notify
        with explicit_tool():
            code, output, errors = await adapter.execute(
                session=session, prompt=directive, on_tool_update=notify,
                model=model, timeout=_DEFAULT_TIMEOUT, role="image", mode="image",
            )
        out = output or ""
        logger.info(f"[ImageCreatorHub] AGY image attempt (code={code}, output={len(out)} chars)")
        if errors:
            logger.debug(f"[ImageCreatorHub] AGY errors: {errors[:3]}")

        # 명시 보고만 인정 (정확 경로 우선)
        reported = cls._extract_saved_image_path(out)
        if reported and os.path.isfile(reported):
            return reported
        return None

    @staticmethod
    def _extract_saved_image_path(text: str) -> Optional[str]:
        """에이전트 출력에서 `SAVED_IMAGE: <path>` 명시 보고 경로만 추출한다."""
        if not text:
            return None
        m = re.search(r"SAVED_IMAGE\s*[:=]\s*([^\n\r]+)", text)
        if not m:
            return None
        raw = m.group(1).strip().strip("`\"'")
        raw = raw.split(" ")[0].strip()  # 뒤에 설명이 붙은 경우 대비
        if not raw:
            return None
        # 상대경로면 절대경로로 보정 시도는 하지 않음(오인 방지) — 존재하는 경로만 인정
        return raw if os.path.isabs(raw) else None

    @classmethod
    def _get_light_session(cls, workspace: str):
        """세션 매니저에서 임시 워크스페이스 세션을 조달하거나 최소 세션 객체를 만든다."""
        try:
            from session_manager import SessionManager
            mgr = SessionManager()
            sess = mgr.get_session(0)
            if sess:
                sess.workspace = workspace
                return sess
        except Exception as e:
            logger.debug(f"[ImageCreatorHub] light session via manager failed: {e}")

        class _LightSession:
            def __init__(self, ws: str):
                self.workspace = ws
                self.channel_id = 0
                self.role = "image"
                self.mode = "image"
                self.current_process = None
                self.conversation_id = None
            def touch(self, *_a, **_k):
                return None

        return _LightSession(workspace)

    # ------------------------------------------------------------------
    # 4. 산출물 배치/검증
    # ------------------------------------------------------------------
    @classmethod
    def _reuse_existing_asset(
        cls,
        expected_path: str,
        workspace: str,
        asset_name: str,
        aspect_ratio: str,
    ) -> Optional[AssetPlacementResult]:
        """
        [기존 에셋 재사용] 프로젝트 리소스 폴더에 이미 유효한 실이미지가 있으면
        배치/검증 결과로 반환한다. 존재하지 않거나 이미지가 아니면 None 을 반환하여
        정상 실패 경로를 그대로 유지한다(가짜 성공 금지).

        탐색 순서:
          1) expected 경로 (호출측이 계산한 정확한 파일명)
          2) 동일 `asset_name` + 이미지 확장자 (png/jpg/jpeg/webp)
          3) `asset_name` 접두/포함 관계의 이미지 (예: `banner` → `pigmenta_banner.png`)
          4) 리소스 폴더의 `ai_image_*` 잔여 실이미지 (자동 채번 이름 재사용)
        `workspace` 루트(프로젝트 소스 밖)는 절대 탐색하지 않는다 → 타 프로젝트/외래
        산출물 오배치 차단.
        """
        try:
            candidates: List[str] = []
            if expected_path:
                candidates.append(expected_path)
            try:
                res_dir = HybridAssetPipeline.resolve_resource_directory(workspace)
            except Exception:
                res_dir = ""
            if res_dir and os.path.isdir(res_dir):
                base = (asset_name or "").strip()
                try:
                    entries = sorted(os.listdir(res_dir))
                except Exception:
                    entries = []
                stems: List[str] = []
                for entry in entries:
                    stem, ext = os.path.splitext(entry)
                    if ext.lower() not in _IMAGE_EXTS:
                        continue
                    if base and (base in stem or stem in base):
                        stems.append(entry)
                candidates.extend(os.path.join(res_dir, e) for e in stems)
                if not base:
                    candidates.extend(
                        os.path.join(res_dir, e)
                        for e in entries
                        if e.lower().startswith("ai_image") and os.path.splitext(e)[1].lower() in _IMAGE_EXTS
                    )

            seen: set = set()
            for path in candidates:
                try:
                    ap = os.path.abspath(path)
                except Exception:
                    continue
                if ap in seen:
                    continue
                seen.add(ap)
                if not os.path.isfile(ap) or os.path.getsize(ap) < _MIN_IMAGE_BYTES:
                    continue
                with open(ap, "rb") as f:
                    head = f.read(16)
                if not ImageGenerationBackend._looks_like_image(head):
                    continue
                cls._ensure_png_content(ap, head)
                # 재사용한 실제 파일명을 asset_name 으로 채택해 경로·이름 정합성을 맞춘다.
                reused_name = os.path.splitext(os.path.basename(ap))[0]
                return cls._place_existing_image(ap, workspace, reused_name, aspect_ratio)
            return None
        except Exception as e:
            logger.debug(f"[ImageCreatorHub] existing-asset reuse check failed: {e}")
            return None

    @classmethod
    def _ensure_png_content(cls, path: str, head: Optional[bytes] = None) -> bool:
        """
        `.png` 이름인데 실제 바이트가 JPEG/WEBP 등인 파일을 진짜 PNG 로 정규화한다.
        (Android drawable 등 확장자-내용 불일치에 민감한 소비자에서 바인딩 실패 방지)
        반환값: 정규화를 수행했으면 True.
        """
        try:
            if head is None:
                with open(path, "rb") as f:
                    head = f.read(16)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return False
            from PIL import Image
            with Image.open(path) as im:
                im.load()
                out = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
                out.save(path, "PNG")
            logger.info(f"[ImageCreatorHub] 확장자-내용 불일치 정규화 완료(→PNG): {path}")
            return True
        except Exception as e:
            logger.debug(f"[ImageCreatorHub] PNG 정규화 건너뜀: {e}")
            return False

    @classmethod
    def _place_existing_image(
        cls,
        source_path: str,
        workspace: str,
        asset_name: str,
        aspect_ratio: str,
    ) -> Optional[AssetPlacementResult]:
        """생성된 이미지를 프로젝트 리소스 폴더로 이동하고 검증/스니펫을 만든다."""
        try:
            target_dir = HybridAssetPipeline.resolve_resource_directory(workspace)
            os.makedirs(target_dir, exist_ok=True)

            # [이름 일관성] 소스 파일이 이미 프로젝트 리소스 폴더 안에 있으면(정상 생성
            # 산출물) 그 파일명을 그대로 보존한다. 예전에는 무조건 `{asset_name}.png` 로
            # 덮어써서, 호출측이 계산한 경로와 실제 배치 경로가 어긋나 재사용/재바인딩이
            # 매번 빗나갔다(→ 매번 다른 파일명으로 중복 생성).
            src_dir = os.path.abspath(os.path.dirname(source_path) or ".")
            if src_dir == os.path.abspath(target_dir):
                target_path = os.path.abspath(source_path)
            else:
                target_path = os.path.join(target_dir, f"{asset_name}.png")

            if os.path.abspath(source_path) != os.path.abspath(target_path):
                try:
                    shutil.copy2(source_path, target_path)
                except Exception:
                    if os.path.abspath(os.path.dirname(source_path)) != os.path.abspath(target_dir):
                        shutil.copyfile(source_path, target_path)

            if not os.path.isfile(target_path) or os.path.getsize(target_path) < _MIN_IMAGE_BYTES:
                logger.warning(
                    f"[ImageCreatorHub] 배치 실패 — 파일 없음 또는 너무 작음: {target_path} "
                    f"({os.path.getsize(target_path) if os.path.isfile(target_path) else 0} bytes)"
                )
                return None

            width, height = cls._read_dimensions(target_path, aspect_ratio)
            try:
                rel_path = os.path.relpath(target_path, workspace)
            except ValueError:
                rel_path = os.path.basename(target_path)

            platform = HybridAssetPipeline.detect_project_platform(workspace)
            snippets = HybridAssetPipeline.generate_code_integration_snippets(
                platform=platform,
                asset_name=asset_name,
                relative_path=rel_path,
            )
            return AssetPlacementResult(
                asset_name=asset_name,
                category="illustration",
                granularity="FULL_SCENE",
                format="png",
                target_dir=target_dir,
                relative_path=rel_path,
                absolute_path=os.path.abspath(target_path),
                code_snippets=snippets,
                is_verified_on_disk=True,
                width=width,
                height=height,
            )
        except Exception as e:
            logger.exception(f"[ImageCreatorHub] place existing image failed: {e}")
            return None

    @classmethod
    def _read_dimensions(cls, path: str, aspect_ratio: str) -> Tuple[int, int]:
        try:
            from PIL import Image
            with Image.open(path) as im:
                return int(im.width), int(im.height)
        except Exception:
            w, h = 512, 512
            if aspect_ratio == "9:16":
                w, h = 450, 800
            elif aspect_ratio == "16:9":
                w, h = 800, 450
            elif aspect_ratio == "4:3":
                w, h = 640, 480
            return w, h

    # ------------------------------------------------------------------
    # 5. 오케스트레이터 연동 (지시문 감지 → 생성 → 보고 텍스트)
    # ------------------------------------------------------------------
    @classmethod
    async def process_agent_output(
        cls,
        text: str,
        workspace: str,
        session: Optional[Any] = None,
        on_status: Optional[Any] = None,
        max_requests: int = 3,
        user_prompt: str = "",
    ) -> List[ImageGenerationResult]:
        """
        에이전트 본문에서 이미지 요청을 추출하여 실제 생성까지 수행한다.

        user_prompt 가 주어지면 **사용자 원문에 이미지 의도가 있을 때만** 실행한다.
        (이미지와 무관한 질문에서 에이전트가 지침 문구를 인용한 경우 불필요한
         생성/바인딩 루프와 토큰 낭비를 원천 차단)
        """
        if user_prompt and not cls.is_image_intent(user_prompt):
            logger.info(
                "[ImageCreatorHub] 사용자 요청에 이미지 의도가 없어 이미지 생성 루프를 건너뜁니다."
            )
            return []
        requests = cls.parse_image_requests(text)
        if not requests:
            return []
        results: List[ImageGenerationResult] = []
        for req in requests[:max_requests]:
            res = await cls.generate(
                prompt=req.get("prompt", ""),
                workspace=workspace,
                asset_name=req.get("asset_name", ""),
                aspect_ratio=req.get("aspect_ratio", "1:1"),
                session=session,
                on_status=on_status,
                source_image=req.get("source_image", ""),
            )
            results.append(res)
        return results

    @classmethod
    def format_results_for_agent(
        cls,
        results: List[ImageGenerationResult],
        include_failures: bool = True,
    ) -> str:
        """
        생성 결과를 Coder 가 소스코드에 바인딩할 수 있는 지시문으로 변환한다.

        [정직성 원칙] include_failures=True(기본) 이면 **실패 건도 함께 보고**하여
        Coder 가 존재하지 않는 이미지 경로를 소스코드에 참조하는 '유령 참조'를 원천 차단한다.
        (기존에는 ok 결과만 반환하여, 전량 실패 시 Coder 가 아무 피드백도 받지 못했다.)
        """
        ok_results = [r for r in results if r.ok]
        failed_results = [r for r in results if not r.ok]
        if not ok_results and not (include_failures and failed_results):
            return ""

        lines: List[str] = []
        if ok_results:
            lines.append(
                "🎨 **[Image generator 결과 반환 — 아래 생성 이미지를 반드시 소스코드에 바인딩하십시오]**"
            )
            for r in ok_results:
                quality = "실제 AI 생성" if r.is_real_ai else "⚠️ 절차적 임시 에셋(AI 생성 실패 대체)"
                lines.append(
                    f"- `{r.asset_name}.png` → `{r.relative_path}` "
                    f"({r.width}x{r.height}, {quality}, "
                    f"엔진: {r.engine or 'n/a'} · 백엔드: {r.backend or 'n/a'})"
                )
                if r.prompt:
                    lines.append(f"  · 요청: {r.prompt}")
                if r.fallback_used:
                    lines.append(f"  · ⚠️ 주의: {r.message}")
            lines.append(
                "👉 위 이미지를 프로젝트 UI(로고/아이콘/배너/배경 등)에 실제로 연결하고, "
                "생성된 에셋을 사용한 코드 변경(Diff)과 검증 결과를 보고하십시오."
            )

        if include_failures and failed_results:
            lines.append("❌ **[Image generator 생성 실패 보고 — 정직 고지]**")
            for r in failed_results:
                name = r.asset_name or "(이름 미지정)"
                lines.append(f"- `{name}` 생성 실패 · 사유: {r.message or '알 수 없는 오류'}")
            lines.append(
                "👉 생성에 실패한 이미지 경로를 소스코드에 **절대 참조하지 마십시오**. "
                "기존 에셋/CSS 플레이스홀더로 대체하고, 이미지가 꼭 필요하면 사용자에게 백엔드 설정"
                "(`OPENROUTER_API_KEY`)을 안내하십시오."
            )
        return "\n".join(lines)

    @classmethod
    def format_pending_requests(cls, requests: List[Dict[str, str]]) -> str:
        """
        아직 '생성 전'인 이미지 요청을, 태그를 잃지 않고 Coder 가 다음 턴에 그대로
        재방출할 수 있도록 지시문으로 변환한다.

        (format_results_for_agent 는 *생성 완료 결과*용이라 미생성 요청에 쓰면
         '절차적 임시 에셋' 같은 오해 문구가 섞인다. 여기서는 태그 원형을 보존한다.)
        """
        if not requests:
            return ""
        lines: List[str] = []
        for r in requests:
            prompt = (r.get("prompt") or "").strip()
            src = (r.get("source_image") or "").strip()
            tag = "IMAGE_EDIT" if src else "IMAGE_REQUEST"
            payload: List[str] = [f'"prompt": "{prompt}"']
            if src:
                payload.append(f'"source_image": "{src}"')
            else:
                name = (r.get("asset_name") or "").strip()
                ratio = (r.get("aspect_ratio") or "1:1").strip()
                if name:
                    payload.append(f'"asset_name": "{name}"')
                payload.append(f'"aspect_ratio": "{ratio}"')
            lines.append(f"- `[{tag}: {{{', '.join(payload)}}}]`")
        return "\n".join(lines)

    @classmethod
    def summarize_failures(cls, results: List[ImageGenerationResult]) -> str:
        """Discord 사용자에게 즉시 보여줄 실패 요약 한 줄 (실패 건이 없으면 빈 문자열)."""
        failed = [r for r in results if not r.ok]
        if not failed:
            return ""
        reasons = "; ".join(
            f"{r.asset_name or 'image'}: {(r.message or '알 수 없는 오류')[:120]}" for r in failed[:3]
        )
        extra = f" (외 {len(failed) - 3}건)" if len(failed) > 3 else ""
        return f"⚠️ **[Image generator 실패]** 실제 AI 이미지 생성에 실패했습니다. 사유: {reasons}{extra}"

    @classmethod
    async def dispatch_to_studio(
        cls,
        bot_client: Optional[Any],
        result: ImageGenerationResult,
        origin_channel_id: Optional[int] = None,
    ) -> bool:
        """생성 이미지를 #nano-banana-image 스튜디오 채널에 아카이빙한다 (best-effort)."""
        if not bot_client or not result.absolute_path or not os.path.isfile(result.absolute_path):
            return False
        try:
            import discord
            channel_id = getattr(config, "NANO_BANANA_IMAGE_CHANNEL_ID", 0)
            target = bot_client.get_channel(channel_id) if channel_id else None
            if target is None and channel_id:
                try:
                    target = await bot_client.fetch_channel(channel_id)
                except Exception:
                    target = None
            if target is None:
                return False
            origin = "실제 AI 생성" if result.is_real_ai else "⚠️ 절차적 임시 에셋(AI 생성 실패)"
            embed = discord.Embed(
                title=f"🍌 [Image generator] {result.asset_name}.png",
                description=(
                    (f"**요청 출처**: <#{origin_channel_id}>\n" if origin_channel_id else "")
                    + f"**프롬프트**: {result.prompt}\n"
                    + f"**해상도**: `{result.width}x{result.height}` · **출처**: `{origin}`\n"
                    + (f"**백엔드/모델**: `{result.backend}` / `{result.model}`\n" if result.backend else "")
                    + (f"**⚠️ 사유**: {result.message}\n" if result.fallback_used else "")
                    + f"**배치 경로**: `{result.relative_path}`"
                ),
                color=0xF59E0B if result.is_real_ai else 0xEF4444,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="AGY Image generator · 실제 AI 이미지 백엔드 (Nano Banana 계열)")
            await target.send(embed=embed, file=discord.File(result.absolute_path, filename=f"{result.asset_name}.png"))
            return True
        except Exception as e:
            logger.debug(f"[ImageCreatorHub] studio dispatch skipped: {e}")
            return False

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------
    @classmethod
    def _name_from_prompt(cls, prompt: str) -> str:
        words = re.findall(r"[A-Za-z0-9가-힣]+", prompt or "")
        stem = "_".join(words[:4])[:40] if words else ""
        stem = re.sub(r"[^A-Za-z0-9_\-]", "_", stem).strip("_")
        return f"ai_{stem}_{int(time.time()) % 100000}" if stem else f"ai_image_{int(time.time()) % 100000}"

    @classmethod
    async def _notify(cls, on_status: Optional[Any], message: str) -> None:
        if not on_status:
            return
        try:
            res = on_status(message)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass
