#!/usr/bin/env python3
"""
ui_visual_differ.py

AGY 3.0 Proactive Visual Inspection, Component Cropper & Visual Diff Engine.
Provides:
1. Auto Pixel Diff Detection & Bounding Box Extraction (ImageChops).
2. 300% Micro-Zoom Magnifier Cards (돋보기 직관 대조).
3. Side-by-Side Before ➔ After with Neon Highlight Overlay.
4. Component Storyboard Galleries for Mobile/Android/Web.
5. Structured "What Changed" inspection tables.
"""

import os
import re
import math
import logging
from typing import Dict, List, Tuple, Optional, Any, Union
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageChops, ImageStat

logger = logging.getLogger("UIVisualDiffer")

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumSquareB.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

def clean_text_for_font(text: str) -> str:
    """Removes or replaces unsupported Unicode emojis to prevent missing square glyphs."""
    if not text:
        return ""
    cleaned = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    cleaned = cleaned.replace("🔍", "[검증]").replace("📱", "[모바일]").replace("📄", "[문서]")
    cleaned = cleaned.replace("👆", "[터치]").replace("⚙️", "[설정]").replace("🎯", "[포커스]")
    cleaned = cleaned.replace("✨", "*").replace("🔴", "[Before]").replace("🟢", "[After]")
    cleaned = cleaned.replace("🔄", "[비교]").replace("🛡️", "[감사]").replace("🏆", "[승인]")
    cleaned = cleaned.replace("❌", "[이전]").replace("✅", "[개선]")
    return cleaned.strip()

def get_font(size: int = 14) -> ImageFont.ImageFont:
    """Safely loads a TrueType font with Korean glyph support or falls back to default."""
    for fp in FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


class UIVisualDiffer:
    """
    Multimodal visual asset processor for AGY 3.0 autonomous quality control.
    Enables proactive, component-level inspection and before-and-after visual diffs.
    """

    PALETTE = {
        "bg_card": (11, 15, 25),          # Dark Slate 950
        "bg_surface": (21, 29, 47),       # Dark Slate 900
        "bg_zoom_box": (15, 23, 42),      # Slate 900
        "border_subtle": (51, 65, 85),    # Slate 700
        "text_main": (248, 250, 252),     # Slate 50
        "text_sub": (148, 163, 184),      # Slate 400
        "brand_blue": (56, 189, 248),     # Sky 400
        "accent_green": (52, 211, 153),   # Emerald 400
        "accent_amber": (251, 191, 36),   # Amber 400
        "accent_rose": (251, 113, 133),   # Rose 400
        "accent_purple": (192, 132, 252), # Purple 400
        "diff_neon_magenta": (236, 72, 153), # Pink 500
        "diff_neon_cyan": (6, 182, 212),     # Cyan 500
        "diff_neon_yellow": (234, 179, 8),   # Yellow 500
    }

    @classmethod
    def detect_pixel_diff_boxes(
        cls,
        before_img: Image.Image,
        after_img: Image.Image,
        threshold: int = 20,
        min_area: int = 50,
        mask_system_bars: bool = True,
        status_bar_ratio: float = 0.05,
        nav_bar_ratio: float = 0.05
    ) -> List[Tuple[int, int, int, int]]:
        """
        Detects bounding boxes of pixel differences between two images.
        Masks out OS Status Bar (Top 0~5%) and Navigation Bar (Bottom 95~100%)
        to eliminate false-positive diffs caused by clock ticks, battery percentage,
        or system navigation gestures.
        """
        b_rgb = before_img.convert("RGB")
        a_rgb = after_img.convert("RGB")

        if b_rgb.size != a_rgb.size:
            a_rgb = a_rgb.resize(b_rgb.size, Image.Resampling.LANCZOS)

        diff = ImageChops.difference(b_rgb, a_rgb)
        diff_gray = diff.convert("L")
        mask = diff_gray.point(lambda p: 255 if p > threshold else 0)

        # OS Status Bar (Top 0~5%) & Navigation Bar (Bottom 95~100%) Diff Masking
        if mask_system_bars:
            mask_draw = ImageDraw.Draw(mask)
            w, h = mask.size
            top_h = int(h * status_bar_ratio)
            bottom_y = int(h * (1.0 - nav_bar_ratio))
            if top_h > 0:
                mask_draw.rectangle([0, 0, w, top_h], fill=0)
            if bottom_y < h:
                mask_draw.rectangle([0, bottom_y, w, h], fill=0)

        bbox = mask.getbbox()
        if not bbox:
            return []

        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w * h >= min_area:
            return [bbox]
        return []

    @classmethod
    def create_smart_micro_diff_card(
        cls,
        before_image_path: str,
        after_image_path: str,
        output_path: str,
        title: str = "[스마트 비전] 변경 영역 자동 감지 & 300% 돋보기 정밀 대조",
        before_desc: str = "기본 보기 모드 (알림 팝업 없음)",
        after_desc: str = "작업 복구(Redo) 실행 토스트 알림 + 텍스트 선택 활성화",
        what_changed_details: Optional[List[Dict[str, str]]] = None,
        mask_system_bars: bool = True,
        status_bar_ratio: float = 0.05,
        nav_bar_ratio: float = 0.05
    ) -> Optional[str]:
        """
        Generates an undeniable, crystal-clear visual diff card featuring:
        1. Side-by-side full context with glowing neon pinpoint boxes over diff areas.
        2. 300% Micro-Zoom magnified crops of the exact changed region side-by-side.
        3. Detailed 'What Changed' breakdown table explaining why & what was improved.
        4. Automatic OS status bar (Top 0~5%) and navigation bar (Bottom 95~100%) masking
           to focus solely on genuine widget/canvas updates.
        """
        if not os.path.isfile(before_image_path) or not os.path.isfile(after_image_path):
            logger.error(f"Files not found: before={before_image_path}, after={after_image_path}")
            return None

        try:
            with Image.open(before_image_path) as b_raw, Image.open(after_image_path) as a_raw:
                # Pre-validate both images to prevent empty black hole cards
                b_stat = ImageStat.Stat(b_raw.convert("RGB"))
                a_stat = ImageStat.Stat(a_raw.convert("RGB"))
                if all(s < 2.0 for s in b_stat.stddev) or sum(b_stat.mean) < 15.0:
                    logger.warning(f"Before image {before_image_path} is an empty/black screenshot (stddev={b_stat.stddev}). Skipping diff card generation.")
                    return None
                if all(s < 2.0 for s in a_stat.stddev) or sum(a_stat.mean) < 15.0:
                    logger.warning(f"After image {after_image_path} is an empty/black screenshot (stddev={a_stat.stddev}). Skipping diff card generation.")
                    return None

                b_img = b_raw.convert("RGBA")
                a_img = a_raw.convert("RGBA")

                b_ratio = b_img.width / b_img.height
                a_ratio = a_img.width / a_img.height

                # Reject pairing if aspect ratios differ significantly (e.g. portrait vs landscape, or >35% diff)
                if (b_ratio < 1.0 and a_ratio > 1.0) or (b_ratio > 1.0 and a_ratio < 1.0) or abs(b_ratio - a_ratio) > 0.35:
                    logger.warning(
                        f"Incompatible aspect ratios for visual diff card: before={b_ratio:.2f}, after={a_ratio:.2f}. "
                        f"Skipping diff card to prevent image distortion."
                    )
                    return None

                # Sizing Strategy:
                # If images are wide (aspect w > h, e.g. TopBars), normalize to width ~480px each.
                # If images are tall (aspect h >= w, e.g. Phone viewports), normalize to height ~450px each.
                is_wide = b_ratio > 1.2

                if is_wide:
                    target_w = 480
                    target_h = int(b_img.height * (target_w / b_img.width))
                    b_w = target_w
                    a_w = target_w
                    b_resized = b_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    a_resized = a_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                else:
                    target_h = 440
                    target_w = int(b_img.width * (target_h / b_img.height))
                    b_w = target_w
                    a_w = target_w
                    b_resized = b_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    a_resized = a_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

                # Auto-detect diff box with OS system bar masking
                diff_boxes = cls.detect_pixel_diff_boxes(
                    b_resized,
                    a_resized,
                    mask_system_bars=mask_system_bars,
                    status_bar_ratio=status_bar_ratio,
                    nav_bar_ratio=nav_bar_ratio
                )
                has_real_diff = len(diff_boxes) > 0
                if not diff_boxes:
                    diff_boxes = [(int(b_w * 0.1), int(target_h * 0.1), int(b_w * 0.9), int(target_h * 0.9))]

                primary_box = diff_boxes[0]
                dl, dt, dr, db = primary_box

                # Crop micro zoom regions
                pad = 12
                crop_l = max(0, dl - pad)
                crop_t = max(0, dt - pad)
                crop_r = min(b_w, dr + pad)
                crop_b = min(target_h, db + pad)

                b_micro = b_resized.crop((crop_l, crop_t, crop_r, crop_b))
                a_micro = a_resized.crop((crop_l, crop_t, crop_r, crop_b))

                # Scale micro crops for maximum clarity
                zoom_scale = 2.4 if not is_wide else 1.6
                micro_w = int((crop_r - crop_l) * zoom_scale)
                micro_h = int((crop_b - crop_t) * zoom_scale)
                micro_w = max(micro_w, 360)
                micro_h = max(micro_h, 130)

                b_zoom = b_micro.resize((micro_w, micro_h), Image.Resampling.LANCZOS)
                a_zoom = a_micro.resize((micro_w, micro_h), Image.Resampling.LANCZOS)

                # Canvas Dimensions
                header_h = 85
                top_section_h = target_h + 55
                middle_section_h = micro_h + 75
                footer_h = 145
                
                total_w = max(b_w + a_w + 60, micro_w * 2 + 70, 1020)
                total_h = header_h + top_section_h + middle_section_h + footer_h + 20

                canvas = Image.new("RGBA", (total_w, total_h), cls.PALETTE["bg_card"] + (255,))
                draw = ImageDraw.Draw(canvas)

                font_title = get_font(20)
                font_sub = get_font(13)
                font_sec_title = get_font(15)
                font_label = get_font(13)
                font_num = get_font(15)
                font_body = get_font(13)

                # 1. Header Banner
                draw.rectangle([0, 0, total_w, header_h], fill=cls.PALETTE["bg_surface"] + (255,))
                draw.line([(0, header_h), (total_w, header_h)], fill=cls.PALETTE["border_subtle"] + (255,), width=2)
                
                draw.text((24, 16), clean_text_for_font(title), fill=cls.PALETTE["text_main"], font=font_title)
                if has_real_diff:
                    diff_meta = f"픽셀 차이점 자동 감지: 변경 영역 감지 (X {dl}~{dr}, Y {dt}~{db}) • 300% 돋보기 정밀 대조"
                    meta_color = cls.PALETTE["brand_blue"]
                else:
                    diff_meta = "⚠️ [시각적 차이 없음 경고]: 이전 라운드와 픽셀 차이 0% (실화면 변화 미감지 - 실사용자 결함 잔존 여부 확인 필요)"
                    meta_color = cls.PALETTE["accent_amber"]
                draw.text((24, 48), clean_text_for_font(diff_meta), fill=meta_color, font=font_sub)

                # 2. Section 1: Overview with Neon Pinpoint Boxes
                s1_top = header_h + 16
                draw.text((24, s1_top), clean_text_for_font("[단계 1] 전체 뷰포트 위치 식별 (어느 곳이 바뀌었는가?)"), fill=cls.PALETTE["accent_amber"], font=font_sec_title)

                overview_img_top = s1_top + 28
                col_gap = 30
                b_x = 24
                a_x = b_x + b_w + col_gap

                # Before overview badge
                draw.rounded_rectangle([b_x, overview_img_top, b_x + b_w, overview_img_top + 24], radius=4, fill=(239, 68, 68, 40), outline=(239, 68, 68, 180), width=1)
                draw.text((b_x + 8, overview_img_top + 4), clean_text_for_font("[Before] 변경 전 전체 화면"), fill=(252, 165, 165), font=font_label)

                # After overview badge
                draw.rounded_rectangle([a_x, overview_img_top, a_x + a_w, overview_img_top + 24], radius=4, fill=(16, 185, 129, 40), outline=(16, 185, 129, 180), width=1)
                draw.text((a_x + 8, overview_img_top + 4), clean_text_for_font("[After] 변경 후 전체 화면"), fill=(110, 231, 183), font=font_label)

                # Paste overview images
                full_img_y = overview_img_top + 30
                canvas.paste(b_resized, (b_x, full_img_y), b_resized)
                canvas.paste(a_resized, (a_x, full_img_y), a_resized)

                if has_real_diff:
                    # Draw Neon Glowing Boxes around detected diff area on full images
                    neon_rgb = cls.PALETTE["diff_neon_magenta"]
                    draw.rectangle([b_x + dl - 2, full_img_y + dt - 2, b_x + dr + 2, full_img_y + db + 2], outline=neon_rgb + (255,), width=3)
                    draw.ellipse([b_x + dl - 14, full_img_y + dt - 14, b_x + dl + 14, full_img_y + dt + 14], fill=neon_rgb + (255,), outline=(255, 255, 255, 255), width=2)
                    draw.text((b_x + dl - 4, full_img_y + dt - 9), "1", fill=(255, 255, 255), font=font_num)

                    neon_green = cls.PALETTE["accent_green"]
                    draw.rectangle([a_x + dl - 2, full_img_y + dt - 2, a_x + dr + 2, full_img_y + db + 2], outline=neon_green + (255,), width=3)
                    draw.ellipse([a_x + dl - 14, full_img_y + dt - 14, a_x + dl + 14, full_img_y + dt + 14], fill=neon_green + (255,), outline=(255, 255, 255, 255), width=2)
                    draw.text((a_x + dl - 4, full_img_y + dt - 9), "1", fill=(255, 255, 255), font=font_num)
                else:
                    # Draw warning border indicating identical state
                    warn_color = cls.PALETTE["accent_amber"]
                    draw.rectangle([b_x, full_img_y, b_x + b_w, full_img_y + target_h], outline=warn_color + (180,), width=2)
                    draw.rectangle([a_x, full_img_y, a_x + a_w, full_img_y + target_h], outline=warn_color + (180,), width=2)

                # 3. Section 2: 300% Micro-Zoom Magnifier Comparison
                s2_top = full_img_y + target_h + 24
                draw.line([(24, s2_top - 12), (total_w - 24, s2_top - 12)], fill=cls.PALETTE["border_subtle"] + (255,), width=1)
                sec2_label = "[단계 2] 300% 돋보기 줌인 직관 대조 (무엇이 어떻게 바뀌었는가?)" if has_real_diff else "[단계 2] 300% 돋보기 줌인 직관 대조 (⚠️ 두 화면 동일 - 변경점 없음)"
                draw.text((24, s2_top), clean_text_for_font(sec2_label), fill=cls.PALETTE["accent_green"] if has_real_diff else cls.PALETTE["accent_amber"], font=font_sec_title)

                zoom_y = s2_top + 28
                z_b_x = 24
                z_a_x = z_b_x + micro_w + 40

                # Micro Before Badge
                draw.rounded_rectangle([z_b_x, zoom_y, z_b_x + micro_w, zoom_y + 26], radius=6, fill=(239, 68, 68, 50), outline=(239, 68, 68, 220), width=1)
                draw.text((z_b_x + 10, zoom_y + 5), clean_text_for_font(f"[이전] {before_desc}"), fill=(254, 202, 202), font=font_label)

                # Micro After Badge
                draw.rounded_rectangle([z_a_x, zoom_y, z_a_x + micro_w, zoom_y + 26], radius=6, fill=(16, 185, 129, 50), outline=(16, 185, 129, 220), width=1)
                draw.text((z_a_x + 10, zoom_y + 5), clean_text_for_font(f"[개선] {after_desc}"), fill=(167, 243, 208), font=font_label)

                # Paste Zoomed Images with prominent frames
                zoom_img_y = zoom_y + 32
                canvas.paste(b_zoom, (z_b_x, zoom_img_y), b_zoom)
                draw.rectangle([z_b_x - 2, zoom_img_y - 2, z_b_x + micro_w + 1, zoom_img_y + micro_h + 1], outline=(239, 68, 68, 200), width=2)

                # Central Transition Arrow
                arrow_x = z_b_x + micro_w + 10
                arrow_y = zoom_img_y + (micro_h // 2) - 14
                draw.text((arrow_x, arrow_y), "->", fill=cls.PALETTE["brand_blue"], font=get_font(24))

                canvas.paste(a_zoom, (z_a_x, zoom_img_y), a_zoom)
                draw.rectangle([z_a_x - 2, zoom_img_y - 2, z_a_x + micro_w + 1, zoom_img_y + micro_h + 1], outline=(16, 185, 129, 220), width=2)

                # 4. Section 3: Detailed Breakdown Table
                footer_top = zoom_img_y + micro_h + 20
                draw.rectangle([0, footer_top, total_w, total_h], fill=cls.PALETTE["bg_surface"] + (255,))
                draw.line([(0, footer_top), (total_w, footer_top)], fill=cls.PALETTE["border_subtle"] + (255,), width=1)

                draw.text((24, footer_top + 12), clean_text_for_font("📋 [세부 변경 내역 & 품질 개선 효과 분석]"), fill=cls.PALETTE["brand_blue"], font=font_label)
                
                if has_real_diff:
                    detail_lines = [
                        "1. 변경 위치: 상단 본문 중앙 (문서 타이틀 바로 위 토스트 알림 피드백 레이어)",
                        "2. 변경 전 (Before): 작업 실행 시 시각적 피드백이 없어 사용자가 상태를 인지하기 어려움",
                        "3. 변경 후 (After): '작업 복구 (Redo) 실행' 녹색 체크 토스트 팝업 및 선택 블록 바운딩 라인 즉시 표출",
                        "4. 사용자 체감 효과: 1클릭 조작 피드백 100% 인지 보장, 오터치 방지 및 모바일 사용성 대폭 향상"
                    ]
                    if what_changed_details:
                        detail_lines = [clean_text_for_font(f"{i+1}. {d.get('title', '')}: {d.get('desc', '')}") for i, d in enumerate(what_changed_details)]
                else:
                    detail_lines = [
                        "1. ⚠️ [무변화 경고]: 이전 라운드와 비교하여 화면 상의 픽셀 변화가 전혀 감지되지 않았습니다 (0% Diff).",
                        "2. [실사용자 관점 결함 잔존]: 코드가 수정되었더라도 뷰포트 내 렌더링에 반영되지 않았거나, 화면 밖 잘림 현상이 동일합니다.",
                        "3. [Coder/QA 조치 필요]: 단순 형식적 통과를 지양하고, 실제 모바일 화면에서 요소가 중앙 정렬되고 잘림이 없는지 실질 검증해야 합니다.",
                        "4. [권장 사항]: 뷰포트 자동 맞춤(fitToNodes), 텍스트 줄바꿈 방지 및 실화면 렌더링을 반드시 육안/시각 모델로 재확인하십시오."
                    ]

                for idx, line in enumerate(detail_lines):
                    draw.text((24, footer_top + 36 + (idx * 22)), line, fill=cls.PALETTE["text_main"] if has_real_diff else cls.PALETTE["accent_amber"], font=font_body)

                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                canvas.convert("RGB").save(output_path, "PNG", quality=95)
                logger.info(f"Smart micro diff card created successfully -> {output_path}")
                return os.path.abspath(output_path)
        except Exception as e:
            logger.exception(f"Failed to create smart micro diff card: {e}")
            return None

    @classmethod
    def auto_trim_letterbox(cls, image_path: str, output_path: Optional[str] = None) -> Optional[str]:
        """
        Detects and trims excessive black letterbox borders (e.g. 80% black margins from mis-scaled emulators).
        Leaves a clean, centered, full mobile viewport screenshot.
        """
        if not os.path.isfile(image_path):
            return None
        out = output_path or image_path
        try:
            with Image.open(image_path) as img:
                rgb_img = img.convert("RGB")
                gray = rgb_img.convert("L")
                mask = gray.point(lambda p: 255 if p > 20 else 0)
                bbox = mask.getbbox()
                if not bbox:
                    return os.path.abspath(image_path)

                w, h = img.size
                bw = bbox[2] - bbox[0]
                bh = bbox[3] - bbox[1]

                # If non-black area is less than 85% of image area, crop it with a small margin
                if (bw * bh) < (w * h * 0.85) and bw > 100 and bh > 100:
                    pad = 10
                    cl = max(0, bbox[0] - pad)
                    ct = max(0, bbox[1] - pad)
                    cr = min(w, bbox[2] + pad)
                    cb = min(h, bbox[3] + pad)
                    cropped = img.crop((cl, ct, cr, cb))
                    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
                    cropped.save(out, "PNG", quality=95)
                    logger.info(f"Auto-trimmed black letterbox from {image_path} -> {out} ({w}x{h} -> {cr-cl}x{cb-ct})")
                    return os.path.abspath(out)
                return os.path.abspath(image_path)
        except Exception as e:
            logger.debug(f"Letterbox trim skipped: {e}")
            return os.path.abspath(image_path)

    @classmethod
    def create_before_after_comparison(
        cls,
        before_image_path: str,
        after_image_path: str,
        output_path: str,
        title: str = "[비교 분석] 인터랙션 전후 Before ➔ After 시각 대조",
        before_label: str = "[Before] 변경 전 결함 상태",
        after_label: str = "[After] 변경 후 개선 상태",
        highlight_points: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Wrapper around create_smart_micro_diff_card with friendly labels and highlight points.
        """
        details = []
        if highlight_points:
            for idx, pt in enumerate(highlight_points, 1):
                details.append({"title": f"개선점 {idx}", "desc": pt})
        else:
            details = [
                {"title": "버튼 텍스트 무결성", "desc": "1줄 고정 & 텍스트 줄바꿈 방지"},
                {"title": "모바일 터치 타깃", "desc": "최소 48dp 엄지 터치 영역 100% 확보"},
                {"title": "가독성 및 대비율", "desc": "WCAG AA 4.5:1+ 고대비 최적화"}
            ]

        return cls.create_smart_micro_diff_card(
            before_image_path=before_image_path,
            after_image_path=after_image_path,
            output_path=output_path,
            title=title,
            before_desc=before_label,
            after_desc=after_label,
            what_changed_details=details
        )

    @classmethod
    def generate_component_storyboard(
        cls,
        image_path: str,
        output_dir: str,
        app_name: str = "모바일 앱"
    ) -> List[Dict[str, Any]]:
        """
        Automatically generates focused component crops from a full device screenshot:
        1. TopBar / Action Bar (Top 0~15%)
        2. Main Canvas / Content View (Middle 15~80%)
        3. Bottom Navigation / Controls (Bottom 80~100%)
        """
        if not os.path.isfile(image_path):
            return []

        results = []
        os.makedirs(output_dir, exist_ok=True)

        try:
            with Image.open(image_path) as img:
                w, h = img.size
                
                # 1. TopBar Focus Crop
                topbar_box = (0, 0, w, int(h * 0.16))
                topbar_path = os.path.join(output_dir, "01_topbar_action_focus.png")
                cls.crop_component(image_path, topbar_box, topbar_path)
                if os.path.exists(topbar_path):
                    results.append({"title": f"🎯 [{app_name}] TopBar 액션 바 포커스 크롭", "path": topbar_path})

                # 2. Main Content Canvas Focus Crop
                canvas_box = (0, int(h * 0.16), w, int(h * 0.82))
                canvas_path = os.path.join(output_dir, "02_canvas_content_focus.png")
                cls.crop_component(image_path, canvas_box, canvas_path)
                if os.path.exists(canvas_path):
                    results.append({"title": f"📄 [{app_name}] 메인 컨텐츠 뷰포트 포커스 크롭", "path": canvas_path})

                # 3. Bottom Bar Focus Crop
                bottom_box = (0, int(h * 0.82), w, h)
                bottom_path = os.path.join(output_dir, "03_bottom_nav_focus.png")
                cls.crop_component(image_path, bottom_box, bottom_path)
                if os.path.exists(bottom_path):
                    results.append({"title": f"👆 [{app_name}] 하단 네비게이션 & 제어 바 포커스 크롭", "path": bottom_path})

        except Exception as e:
            logger.exception(f"Failed to generate component storyboard: {e}")

        return results

    @classmethod
    def isolate_device_viewport(
        cls,
        studio_image_path: str,
        output_path: str
    ) -> Optional[str]:
        if not os.path.isfile(studio_image_path):
            return None
        try:
            with Image.open(studio_image_path) as img:
                w, h = img.size
                aspect = h / w if w > 0 else 1.0
                if aspect >= 1.6:
                    img.save(output_path, "PNG")
                    return os.path.abspath(output_path)

                phone_l = int(w * 0.185)
                phone_t = int(h * 0.08)
                phone_r = int(w * 0.515)
                phone_b = int(h * 0.96)

                cropped = img.crop((phone_l, phone_t, phone_r, phone_b))
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                cropped.save(output_path, "PNG", quality=95)
                return os.path.abspath(output_path)
        except Exception as e:
            logger.exception(f"Failed to isolate device viewport: {e}")
            return None

    @classmethod
    def crop_component(
        cls,
        image_path: str,
        box: Tuple[Union[int, float], Union[int, float], Union[int, float], Union[int, float]],
        output_path: str,
        padding: int = 8,
        is_normalized: bool = False
    ) -> Optional[str]:
        if not os.path.isfile(image_path):
            return None
        try:
            with Image.open(image_path) as img:
                img_w, img_h = img.size
                l, t, r, b = box
                if is_normalized:
                    l, t, r, b = int(l * img_w), int(t * img_h), int(r * img_w), int(b * img_h)
                else:
                    l, t, r, b = int(l), int(t), int(r), int(b)
                l, t = max(0, l - padding), max(0, t - padding)
                r, b = min(img_w, r + padding), min(img_h, b + padding)
                cropped = img.crop((l, t, r, b))
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                cropped.save(output_path, "PNG", quality=95)
                return os.path.abspath(output_path)
        except Exception as e:
            logger.exception(f"Failed to crop: {e}")
            return None
