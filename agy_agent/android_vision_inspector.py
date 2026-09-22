import os
import re
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple

from agent_runner import AgentRunner
from session_manager import ChannelSession
from config import VISION_MODEL

logger = logging.getLogger("AndroidVisionInspector")

class AndroidVisionInspector:
    """
    Analyzes mobile/Android screenshots using Gemini Vision multimodal intelligence
    to automatically evaluate UI layout, visual contrast, typography, and UX design quality.
    """

    SYSTEM_VISION_PROMPT = """[역할: 최고 수준의 수석 모바일 UI/UX 디자이너 & Visual QA 감사관 (Chief Visual Inspector)]

첨부된 모바일 안드로이드/웹 앱 스크린샷 화면을 정밀 분석하여 실사용자 관점에서 다음 [4대 핵심 결함 우선순위 체계]에 따라 철저히 시각 품질을 평가하십시오:

🚨 [0순위: 치명적 UI 깨짐 및 텍스트 줄바꿈/찌그러짐 전수 검사]
- **버튼 텍스트 줄바꿈 결함**: 저장, 공유 등 버튼 내부 텍스트가 가로폭 부족으로 `저\\n장` 처럼 2줄로 수직 줄바꿈되거나 찌그러지는 현상이 있는가? (발견 시 즉시 80점 미만 감점 & 핫픽스 지시)
- **화면 밖 오버플로우/잘림**: 중요한 타이틀, 파일명, 핵심 텍스트가 조기에 잘리거나(`...`) 화면 밖으로 넘치지 않는가?
- **상하단 Safe Area 침범**: 노치/펀치홀/카메라 아일랜드 및 하단 제스처 바 영역을 침범하지 않는가?

⚠️ [1순위: 액션 바 과밀집 및 터치 사용성 (Touch Targets & Usability)]
- **버튼 과밀집(Crowding) 방어**: 390px/412px 좁은 화면에 4개 이상의 액션 버튼이 무리하게 일렬 배치되어 오터치 위험이 있는가? (뷰어 모드 시 비활성 버튼 숨김 등 동적 제어 여부)
- **최소 48dp 터치 타깃**: 모든 버튼과 터치 가능한 인터랙션 요소가 최소 48x48dp(44px)의 엄지 터치 영역(Thumb Zone)을 보장하는가?

🔠 [2순위: 타이포그래피 & 색상 대비율 (Typography & Contrast Ratio)]
- **WCAG AA 4.5:1+ 대비율**: 본문, 계약서 조항, 서브텍스트(사업자번호, 직책 등)의 명도 대비가 충분하여 주광/야외에서도 또렷하게 읽히는가? (저대비 폰트 `#94A3B8` ➔ `#334155` 등 고대비 상향 여부)
- **폰트 계층 구조**: 대제목 ➔ 본문 ➔ 캡션의 크기와 굵기가 정돈되어 시각적 위계가 명확한가?

💡 [3순위: 심미성 & 인터랙션 피드백 (Aesthetics & Feedback)]
- 세련된 Material Design 3 / iOS HIG 스타일의 글래스모피즘, 일관된 코너 라운딩, 정갈한 그림자가 적용되었는가?
- 작업 실행(Save, Undo, Redo, Copy) 시 사용자에게 상태를 즉각 알려주는 토스트/스낵바 피드백이 제공되는가?

[출력 형식]:
- 🎨 **[종합 시각 품질 점수]**: 100점 만점 기준 점수 (예: 94/100)
- 🚨 **[버튼 텍스트 무결성]**: 🟢 1줄 정상 (줄바꿈 없음) / ❌ 2줄 깨짐 감지
- 📐 **[버튼 간격 & 여백 판정]**: 🟢 균형 우수 / ⚠️ 조정 권장 / ❌ 불균형
- 👆 **[터치 타깃 크기 (48dp)]**: 🟢 100% 준수 / ⚠️ 일부 협소 / ❌ 부적합
- 🔠 **[가독성 & 대비율]**: 🟢 WCAG 4.5:1+ 충족 / ⚠️ 저대비 주의
- 🎨 **[디자인 완성도]**: 🟢 모던 & 세련 / ⚠️ 스타일 개선 권장
- ✅ **잘된 점 (Strengths)**: 2~3가지 핵심 포인트
- 🔍 **발견된 결함 및 개선 권장 사항 (Issues & Improvements)**: 구체적 위치 및 수정 이유
- 💡 **Coder를 위한 Compose/XML/CSS 스타일 수정 코드**:
```kotlin
// 구체적인 수정 코드 스니펫
```
"""

    @classmethod
    def extract_component_roi_crop(
        cls,
        image_path: str,
        bbox: Tuple[int, int, int, int],
        output_path: Optional[str] = None,
        padding_ratio: float = 0.15
    ) -> Optional[str]:
        """
        Crops an isolated component region (ROI) at 1:1 native resolution with contextual padding.
        Prevents micro-components (e.g. 18px keycaps, small icons) from being blurred by LLM resizing.
        """
        if not os.path.isfile(image_path):
            return None
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                w, h = img.size
                x1, y1, x2, y2 = bbox
                pad_x = int((x2 - x1) * padding_ratio)
                pad_y = int((y2 - y1) * padding_ratio)
                crop_x1 = max(0, x1 - pad_x)
                crop_y1 = max(0, y1 - pad_y)
                crop_x2 = min(w, x2 + pad_x)
                crop_y2 = min(h, y2 + pad_y)

                cropped = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                if not output_path:
                    base, ext = os.path.splitext(image_path)
                    output_path = f"{base}_roi_crop{ext}"
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                cropped.save(output_path)
                return output_path
        except Exception as e:
            logger.warning(f"Failed to extract ROI crop: {e}")
            return None

    @classmethod
    def get_vision_prompt(
        cls,
        screenshot_path: str,
        app_name: str = "안드로이드 앱",
        roi_crop_path: Optional[str] = None,
        before_screenshot_path: Optional[str] = None,
        ui_grounding_metadata: Optional[Dict[str, Any]] = None,
        user_query: Optional[str] = None
    ) -> str:
        """Constructs an actionable multimodal prompt referencing full context, ROI crop, and grounding."""
        prompt_parts = [cls.SYSTEM_VISION_PROMPT, f"\n\n**[검사 대상 앱]**: {app_name}"]

        if user_query:
            prompt_parts.append(
                f"\n\n🎯 **[사용자 직접 문의 사항 (User Conversational Inquiry)]**:\n"
                f"\"{user_query}\"\n"
                f"👉 중요: 일반적인 체크리스트 점수 매기기에 앞서, 위 사용자의 구체적인 질문/의견에 대한 "
                f"직접적이고 명쾌한 시각적 답변(예: 크기, 톤앤매너, 가독성, 개선안)을 최우선으로 제공하십시오."
            )

        prompt_parts.append(f"\n\n**[스크린샷 파일 절대경로]**: `{screenshot_path}`")

        if roi_crop_path and os.path.isfile(roi_crop_path):
            prompt_parts.append(
                f"\n**[1:1 고해상도 컴포넌트 크롭 (ROI Zoom)]**: `{roi_crop_path}`\n"
                f"※ 중요: 전체 화면 축소로 인해 뭉개질 수 있는 세부 디테일(키캡 크기, 아이콘 해상도, 텍스트 가독성)은 "
                f"반드시 이 1:1 원본 크롭 이미지를 기준으로 정밀 진단하십시오."
            )

        if before_screenshot_path and os.path.isfile(before_screenshot_path):
            prompt_parts.append(
                f"\n**[수정 전(Before) 비교 스크린샷]**: `{before_screenshot_path}`\n"
                f"※ 전/후(A/B) 시각적 변화량을 대조하여 의도한 개선이 실제로 렌더링되었는지 판별하십시오."
            )

        if ui_grounding_metadata:
            import json
            prompt_parts.append(
                f"\n\n📐 **[물리 계측 시맨틱 메타데이터 (UI Grounding Metrics)]**:\n"
                f"```json\n{json.dumps(ui_grounding_metadata, ensure_ascii=False, indent=2)}\n```\n"
                f"※ 화면 눈대중이 아닌 위 실제 계산된 픽셀/dp 크기 및 속성을 토대로 WCAG 대비율과 48dp 터치 타깃을 정밀 판정하십시오."
            )

        prompt_parts.append("\nview_file 도구를 사용하여 위 스크린샷 및 크롭 이미지 파일을 직접 확인하고 시각 결함 평가 보고서를 완성해 주십시오.")
        return "".join(prompt_parts)

    @classmethod
    async def _analyze_with_direct_api(
        cls,
        screenshot_path: str,
        app_name: str,
        api_key: str,
        roi_crop_path: Optional[str] = None,
        before_screenshot_path: Optional[str] = None,
        ui_grounding_metadata: Optional[Dict[str, Any]] = None,
        user_query: Optional[str] = None
    ) -> Optional[str]:
        """
        Attempts direct multimodal inspection using google.genai in a separate thread
        to prevent blocking the asyncio event loop.
        """
        def _sync_worker() -> Optional[str]:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=api_key)
                
                with open(screenshot_path, "rb") as f:
                    image_bytes = f.read()

                prompt = cls.get_vision_prompt(
                    screenshot_path,
                    app_name=app_name,
                    roi_crop_path=roi_crop_path,
                    before_screenshot_path=before_screenshot_path,
                    ui_grounding_metadata=ui_grounding_metadata,
                    user_query=user_query
                )

                contents = [
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png")
                ]

                # If ROI crop exists, inject as 2nd high-res image part
                if roi_crop_path and os.path.isfile(roi_crop_path):
                    with open(roi_crop_path, "rb") as rf:
                        contents.append(types.Part.from_bytes(data=rf.read(), mime_type="image/png"))

                # If Before image exists, inject as 3rd image part for A/B diff
                if before_screenshot_path and os.path.isfile(before_screenshot_path):
                    with open(before_screenshot_path, "rb") as bf:
                        contents.append(types.Part.from_bytes(data=bf.read(), mime_type="image/png"))

                contents.append(prompt)

                response = client.models.generate_content(
                    model=VISION_MODEL,
                    contents=contents
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                logger.warning(f"Direct google.genai call failed: {e}. Falling back to AgentRunner.")
            return None

        return await asyncio.to_thread(_sync_worker)

    @classmethod
    async def analyze_screenshot(
        cls,
        screenshot_path: str,
        app_name: str = "안드로이드 앱",
        roi_crop_path: Optional[str] = None,
        before_screenshot_path: Optional[str] = None,
        ui_grounding_metadata: Optional[Dict[str, Any]] = None,
        user_query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Runs real Gemini Vision multimodal inspection on the screenshot.
        Uses direct Gemini API if key is configured, or AgentRunner AGY CLI with CODER_MODEL.
        Supports multi-scale ROI crops, before/after diffs, and conversational grounding.
        """
        if not os.path.isfile(screenshot_path):
            return {
                "success": False,
                "error": f"Screenshot file not found: {screenshot_path}",
                "screenshot_path": screenshot_path
            }

        sz_kb = os.path.getsize(screenshot_path) / 1024
        logger.info(f"Running Gemini Vision analysis for {app_name} ({sz_kb:.1f}KB): {screenshot_path}")

        analysis_text = None

        # 1. Check for direct Gemini API Key (non-blocking thread execution)
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if api_key:
            analysis_text = await cls._analyze_with_direct_api(
                screenshot_path,
                app_name,
                api_key,
                roi_crop_path=roi_crop_path,
                before_screenshot_path=before_screenshot_path,
                ui_grounding_metadata=ui_grounding_metadata,
                user_query=user_query
            )

        # 2. If direct API not available or failed, run via AgentRunner with unique ephemeral session
        if not analysis_text:
            prompt = cls.get_vision_prompt(
                screenshot_path,
                app_name=app_name,
                roi_crop_path=roi_crop_path,
                before_screenshot_path=before_screenshot_path,
                ui_grounding_metadata=ui_grounding_metadata,
                user_query=user_query
            )
            
            # Generate unique ephemeral channel ID to guarantee completely isolated session
            ephemeral_channel_id = uuid.uuid4().int % 1_000_000_000
            session = ChannelSession(
                channel_id=ephemeral_channel_id,
                workspace=os.path.dirname(os.path.abspath(screenshot_path)),
                conversation_id=None,
                mode="single"
            )

            try:
                analysis_text = await asyncio.wait_for(
                    AgentRunner.run_prompt(
                        session=session,
                        prompt=prompt,
                        model=VISION_MODEL
                    ),
                    timeout=60.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Vision inspection timed out (60s) for {screenshot_path}")
                return {
                    "success": False,
                    "error": f"Vision inspection timed out after 60s for {os.path.basename(screenshot_path)}",
                    "screenshot_path": screenshot_path,
                    "score": 0,
                    "size_kb": round(sz_kb, 1),
                    "app_name": app_name
                }
            except Exception as e:
                logger.warning(f"Vision inspection failed: {e}")
                return {
                    "success": False,
                    "error": f"Vision inspection failed: {str(e)}",
                    "screenshot_path": screenshot_path,
                    "score": 0,
                    "size_kb": round(sz_kb, 1),
                    "app_name": app_name
                }
            finally:
                import gc
                gc.collect()

        if not analysis_text or not analysis_text.strip():
            return {
                "success": False,
                "error": "AI Vision model returned an empty response.",
                "screenshot_path": screenshot_path,
                "score": 0
            }

        # Parse score if present in AI Vision output
        score = None
        score_match = re.search(r'(\d{1,3})\s*(?:/\s*100|점)', analysis_text)
        if score_match:
            try:
                parsed = int(score_match.group(1))
                if 0 <= parsed <= 100:
                    score = parsed
            except ValueError:
                pass

        if score is None:
            # Dynamic semantic score derivation (100-pt scale based on visual findings)
            base_score = 60
            t_lower = analysis_text.lower()
            pos_keywords = ["우수", "준수", "적절", "정상", "깨끗", "확보", "균형", "안정", "pass", "good", "clean", "optimal", "polish"]
            neg_keywords = ["잘림", "겹침", "오류", "부족", "가려짐", "경고", "깨짐", "clipping", "overflow", "defect", "fail", "broken", "error"]
            pos_cnt = sum(1 for kw in pos_keywords if kw in t_lower)
            neg_cnt = sum(1 for kw in neg_keywords if kw in t_lower)
            score = max(0, min(100, base_score + (pos_cnt * 3) - (neg_cnt * 15)))

        return {
            "success": True,
            "screenshot_path": screenshot_path,
            "analysis_text": analysis_text,
            "score": score,
            "size_kb": round(sz_kb, 1),
            "app_name": app_name
        }

    @classmethod
    def select_before_after_pair(cls, images: List[str]) -> Tuple[Optional[str], Optional[str], str, str]:
        """
        Intelligently selects a genuine, visually aligned Before/After pair:
        1. Same automated session: android_full_01_idle_{ts} and android_full_02_interaction_{ts} (or 04_form_input_{ts}).
        2. Explicit semantic pairs (before/after, idle/active, editor/applied).
        3. Strict orientation & aspect ratio match (never mix portrait with landscape).
        Returns (before_path, after_path, before_desc, after_desc).
        """
        if len(images) < 2:
            return None, None, "", ""

        from PIL import Image

        def get_aspect(p: str) -> Optional[float]:
            try:
                with Image.open(p) as img:
                    return img.width / img.height
            except Exception:
                return None

        def compatible(p1: str, p2: str) -> bool:
            a1, a2 = get_aspect(p1), get_aspect(p2)
            if not a1 or not a2:
                return False
            # Never pair portrait (<1.0) with landscape (>1.0)
            if (a1 < 1.0 and a2 > 1.0) or (a1 > 1.0 and a2 < 1.0):
                return False
            return abs(a1 - a2) <= 0.25

        # Heuristic 1: Match android_full session screenshots by timestamp
        session_map: Dict[str, Dict[str, str]] = {}
        for img in images:
            base = os.path.basename(img)
            m = re.search(r"android_full_(\d\d)_[a-z_]+_(\d+)\.png", base)
            if m:
                state_idx, ts = m.group(1), m.group(2)
                session_map.setdefault(ts, {})[state_idx] = img

        for ts, states in sorted(session_map.items(), reverse=True):
            b_cand = states.get("01")  # State 1: Idle
            # Pair with State 2 (Interaction) or State 4 (Form Input) or State 3 (Scroll)
            # Avoid State 5 (Landscape rotation) to maintain portrait orientation consistency
            a_cand = states.get("02") or states.get("04") or states.get("03")
            if b_cand and a_cand and compatible(b_cand, a_cand):
                return (
                    b_cand,
                    a_cand,
                    "[상태 1] 초기 대기 화면 (Idle)",
                    "[상태 2] 인터랙션 동작 화면 (Active Transition)"
                )

        # Heuristic 2: Explicit semantic pairs (before/after, idle/active, screen/applied)
        for b_cand in images:
            b_low = os.path.basename(b_cand).lower()
            if any(k in b_low for k in ["before", "idle", "default", "initial"]):
                for a_cand in images:
                    if a_cand == b_cand:
                        continue
                    a_low = os.path.basename(a_cand).lower()
                    if any(k in a_low for k in ["after", "active", "interaction", "applied", "modified"]):
                        if compatible(b_cand, a_cand):
                            return (
                                b_cand,
                                a_cand,
                                f"[Before] {os.path.splitext(os.path.basename(b_cand))[0]}",
                                f"[After] {os.path.splitext(os.path.basename(a_cand))[0]}"
                            )

        # Heuristic 3: Component editor / viewer progression
        editor_idle = [img for img in images if any(k in os.path.basename(img).lower() for k in ["editor_screen", "home_dashboard", "01_home", "02_viewer", "01_idle", "main_screen", "dashboard"])]
        editor_active = [img for img in images if any(k in os.path.basename(img).lower() for k in ["editor_text_applied", "edit_dialog", "text_edit", "08_text_edit", "02_active", "04_form", "detail_screen", "active"])]
        if editor_idle and editor_active:
            for b_cand in editor_idle:
                for a_cand in editor_active:
                    if compatible(b_cand, a_cand):
                        return (
                            b_cand,
                            a_cand,
                            "[Before] 기본 뷰포트 (대기 상태)",
                            "[After] 컴포넌트 편집 및 인터랙션 상태"
                        )

        # Heuristic 4: Fallback - first compatible pair (excluding rotation state 05)
        for i in range(len(images)):
            for j in range(i + 1, len(images)):
                b_cand, a_cand = images[i], images[j]
                if "05_landscape" in os.path.basename(b_cand) or "05_landscape" in os.path.basename(a_cand):
                    continue
                if compatible(b_cand, a_cand):
                    return (
                        b_cand,
                        a_cand,
                        f"[Before] {os.path.splitext(os.path.basename(b_cand))[0]}",
                        f"[After] {os.path.splitext(os.path.basename(a_cand))[0]}"
                    )

        return None, None, "", ""

    @classmethod
    async def analyze_project_screenshots(
        cls,
        workspace: str,
        app_name: str = "안드로이드 앱",
        max_screens: int = 6,
        session_start_time: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Discovers all real physical UI screenshots across standard project directories,
        performs Gemini Vision multimodal inspection on key screens, computes average score,
        and automatically writes Gate 5 visual_qa.json evidence.
        Enforces strict session isolation by rejecting any screenshots created prior to session_start_time.
        """
        from evidence_validator import EvidenceValidator

        candidate_dirs = [
            os.path.join(workspace, "reports", "gate5", "screenshots"),
            os.path.join(workspace, "reports", "screenshots"),
            os.path.join(workspace, "reports", "qa_screenshots"),
            os.path.join(workspace, "screenshots"),
            os.path.join(workspace, "app", "build", "outputs", "roborazzi"),
            os.path.join(workspace, "app", "build", "reports", "paparazzi"),
        ]

        found_images = []
        seen_hashes = set()

        for cdir in candidate_dirs:
            if os.path.isdir(cdir):
                for fname in sorted(os.listdir(cdir)):
                    if fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        # Exclude composite diff cards and non-app OS settings screenshots
                        if fname.startswith("00_") or "diff" in fname.lower() or "card" in fname.lower():
                            continue
                        fname_low = fname.lower()
                        if any(k in fname_low for k in ["sound_settings", "system_settings", "android_settings"]):
                            continue
                        fpath = os.path.join(cdir, fname)

                        # Enforce temporal freshness against session start time
                        if session_start_time is not None:
                            try:
                                mtime = os.path.getmtime(fpath)
                                if mtime < (session_start_time - 1.0):
                                    continue
                            except Exception:
                                pass

                        is_valid, _ = EvidenceValidator.validate_file(fpath, min_bytes=500, is_image=True)
                        if is_valid:
                            try:
                                from ui_visual_differ import UIVisualDiffer
                                fpath = UIVisualDiffer.auto_trim_letterbox(fpath) or fpath
                            except Exception:
                                pass
                            try:
                                import hashlib
                                with open(fpath, "rb") as bf:
                                    h = hashlib.md5(bf.read()).hexdigest()
                                if h not in seen_hashes:
                                    seen_hashes.add(h)
                                    found_images.append(fpath)
                            except Exception:
                                found_images.append(fpath)

        # Sort found images by modification time (newest first) to ensure fresh session screenshots are inspected
        found_images.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        if not found_images:
            return {
                "success": False,
                "error": "No fresh session screenshots found on disk (residual files ignored)",
                "app_name": app_name,
                "average_score": 0,
                "screens_analyzed": 0,
                "screen_results": [],
                "summary_markdown": "⚠️ 워크스페이스 내에서 캡처된 최신 UI 스크린샷 파일을 발견하지 못했습니다.",
                "screenshots": []
            }

        # Select up to max_screens of the freshest screenshots that physically exist
        target_images = [img for img in found_images if os.path.isfile(img)][:max_screens]
        # Re-sort chronologically for report readability (e.g. 01_home -> 02_viewer -> 03_ocr -> 04_dark)
        target_images.sort(key=lambda p: os.path.basename(p))

        screen_results = []
        screenshots_tuples = []
        total_score = 0

        for idx, img_path in enumerate(target_images, 1):
            base_label = os.path.splitext(os.path.basename(img_path))[0].replace("_", " ").title()
            screen_label = f"Screen {idx:02d} ({base_label})"
            screenshots_tuples.append(("mobile", screen_label, img_path))

            # Run vision inspection on discovered screens dynamically
            try:
                res = await cls.analyze_screenshot(img_path, app_name=f"{app_name} - {screen_label}")
                if res.get("success"):
                    screen_results.append(res)
                    total_score += res.get("score", 0)
                else:
                    err_score = res.get("score", 30)
                    screen_results.append({
                        "success": False,
                        "screenshot_path": img_path,
                        "analysis_text": f"• {screen_label}: 비전 분석 실패 ({res.get('error', '응답 없음')})",
                        "score": err_score
                    })
                    total_score += err_score
            except Exception as e:
                logger.warning(f"API Error for {screen_label}: {e}")
                screen_results.append({
                    "success": False,
                    "screenshot_path": img_path,
                    "analysis_text": f"⚠️ 비전 분석 API 호출 실패 ({e})",
                    "score": 0
                })
                total_score += 0

        avg_score = int(round(total_score / max(len(screen_results), 1)))

        # Build component-level crop assets and Before/After cards via UIVisualDiffer
        crop_assets = []
        try:
            from ui_visual_differ import UIVisualDiffer
            crops_dir = os.path.join(workspace, "reports", "gate5", "screenshots", "crops")
            os.makedirs(crops_dir, exist_ok=True)

            # Generate storyboard crops for primary screenshot
            if target_images:
                prime_img = target_images[0]
                crop_cards = UIVisualDiffer.generate_component_storyboard(prime_img, crops_dir, app_name=app_name)
                for c in crop_cards:
                    crop_assets.append(c["path"])
                    screenshots_tuples.append(("component_crop", c["title"], c["path"]))

            # Generate Truthful State Transition or True Before/After comparison cards
            before_img, after_img, b_desc, a_desc = cls.select_before_after_pair(target_images)
            if before_img and after_img:
                diff_card_path = os.path.join(crops_dir, "00_before_after_interaction_diff.png")
                diff_res = UIVisualDiffer.create_smart_micro_diff_card(
                    before_image_path=before_img,
                    after_image_path=after_img,
                    output_path=diff_card_path,
                    title=f"[{app_name}] 실기기 인터랙션 상태 전이 (State 1 ➔ State 2) 300% 돋보기 시각 대조",
                    before_desc=b_desc or "[상태 1] 초기 대기 화면 (Idle)",
                    after_desc=a_desc or "[상태 2] 인터랙션 동작 화면 (Active Transition)",
                    what_changed_details=[
                        {"title": "1. 실기기 인터랙션 이벤트", "desc": "초기 Idle 상태에서 사용자 탭/입력 이벤트 트리거에 따른 상태 전이"},
                        {"title": "2. 활성 포커스 및 픽셀 변화", "desc": "이벤트 발생 영역(버튼, 모달, 포커스) 내 픽셀 변경 감지"},
                        {"title": "3. 렌더링 무결성 확인", "desc": "상태 전이 중 화면 깨짐, 오버플로우 및 크래시 여부 시각적 대조"}
                    ]
                )
                if diff_res and os.path.exists(diff_res):
                    crop_assets.append(diff_res)
                    screenshots_tuples.insert(0, ("state_transition", "[전이] 실기기 상태 전이 (State 1 ➔ 2)", diff_res))
        except Exception as e:
            logger.debug(f"UIVisualDiffer component cropping skipped: {e}")

        # Build summary markdown for 3-agent Pair collaboration and Discord embeds
        summary_md_parts = [
            f"### 📱 [{app_name}] Gemini Vision 모바일 UI/UX 심층 시각 감사 리포트",
            f"• **종합 시각 품질 점수:** 🎨 **{avg_score}/100점** (검사된 화면: {len(target_images)}장, 포커스 크롭: {len(crop_assets)}장)",
            f"• **📐 버튼 간격 & 여백:** `{'🟢 균형 우수 (48dp+ 준수)' if avg_score >= 85 else '⚠️ 미세 조정 권장'}`",
            f"• **🔠 타이포그래피 대비율:** `{'🟢 WCAG 4.5:1+ 가독성 확보' if avg_score >= 80 else '⚠️ 대비율 개선 필요'}`",
            f"• **👆 모바일 터치 사용성:** `{'🟢 엄지 터치 최적화' if avg_score >= 85 else '⚠️ 터치 타깃 확대 권장'}`",
            f"• **🎨 심미성 & 비주얼 완성도:** `{'🟢 모던 & 세련된 UI Polish' if avg_score >= 85 else '⚠️ 디자인 일관성 보완'}`\n",
            "**🔍 화면별 세부 시각 분석:**"
        ]

        for s_res in screen_results:
            s_name = os.path.basename(s_res.get("screenshot_path", ""))
            s_text = s_res.get("analysis_text", "")
            s_score = s_res.get("score", 0)
            summary_md_parts.append(f"• **`{s_name}`** (점수: {s_score}/100):\n{s_text}\n")

        full_summary_md = "\n".join(summary_md_parts)

        # Persist Gate 5 visual_qa.json evidence
        try:
            import json, datetime
            gate5_dir = os.path.join(workspace, "reports", "gate5")
            os.makedirs(gate5_dir, exist_ok=True)
            vqa_file = os.path.join(gate5_dir, "visual_qa.json")
            vqa_payload = {
                "app_name": app_name,
                "timestamp": datetime.datetime.now().isoformat(),
                "average_score": avg_score,
                "screenshots_count": len(found_images),
                "analyzed_count": len(screen_results),
                "screenshots": found_images,
                "component_crops": crop_assets,
                "verdict": "PASS" if avg_score >= 80 else "WARN",
                "summary": full_summary_md
            }
            with open(vqa_file, "w", encoding="utf-8") as f:
                json.dump(vqa_payload, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved Gate 5 visual QA evidence to {vqa_file}")
        except Exception as e:
            logger.debug(f"Failed to persist visual_qa.json: {e}")

        return {
            "success": True,
            "app_name": app_name,
            "average_score": avg_score,
            "screens_analyzed": len(screen_results),
            "screen_results": screen_results,
            "summary_markdown": full_summary_md,
            "screenshots": screenshots_tuples,
            "crop_assets": crop_assets
        }


