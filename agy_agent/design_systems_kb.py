#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
design_systems_kb.py

AGY — 외부 디자인 표준 그라운딩 KB (Public Corporate Design Systems Grounding).

문제 정의 (사용자 지적, 2026-10-01):
- 지금까지 시스템은 '사용자가 준 하우스 룰/레퍼런스'가 있어야만 디테일을 채웠다.
- 그러나 구글/애플/마이크로소프트/IBM 등은 디자인 철학·수치 토큰·접근성 규격을
  이미 공개 문서로 남겨두었다. 사용자 취향 입력이 없어도 공개 표준을 기본값으로
  강제하면, 사용자가 제시하지 않은 부분(형태 스케일·엘리베이션·상태·접근성)의
  디테일까지 결정적으로 메워진다.

해결:
- 플랫폼/도메인 → 공개 디자인 시스템 1개를 결정적으로 매핑한다.
- 각 시스템의 공개 수치 토큰(그리드/형태/타이포/모션/상태/접근성)을 계약에 상속한다.
- 사용자 입력(house_rules/reference/design_system)은 '오버라이드'로만 위에 얹는다.
  즉 기본값 = 공개 표준, 사용자 입력 = 선택적 덮어쓰기.

정직성 원칙:
- 아래 수치는 각 벤더가 공개한 공식 문서에서 발췌한 대표값이다. 정밀 수치는
  각 항목의 `docs` URL을 단일 진실 공급원으로 삼는다.
- 네트워크/LLM 호출 없음 (순수 결정적 데이터 + 포매터).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("DesignSystemsKB")

DESIGN_SYSTEM_KB_VERSION = 1

# ---------------------------------------------------------------------------
# 공개 디자인 시스템 카탈로그 (공식 문서 발췌 대표값)
# ---------------------------------------------------------------------------
DESIGN_SYSTEMS: Dict[str, Dict[str, Any]] = {
    "material3": {
        "id": "material3",
        "name": "Material Design 3",
        "vendor": "Google",
        "docs": "https://m3.material.io/",
        "platform": "Android · 모바일 · Flutter/React Native · Material 웹",
        "grid": {
            "base_dp": 4,
            "margin_dp": 16,
            "gutter_dp": 16,
            "columns": {"compact": 4, "medium": 8, "expanded": 12},
        },
        "shape_radius_dp": {"none": 0, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 28, "full": 9999},
        "type_ramp_summary": (
            "Display 57/45/36 · Headline 32/28/24 · Title 22/16/14 · "
            "Body 16/14/12 · Label 14/12/11(w500)"
        ),
        "touch_target_dp": 48,
        "elevation_dp": [0, 1, 3, 6, 8, 12],
        "motion": {
            "short_ms": [50, 100, 150, 200],
            "medium_ms": [250, 300, 350, 400],
            "long_ms": [450, 500, 550, 600],
            "emphasized_easing": "cubic-bezier(0.2, 0, 0, 1)",
        },
        "states": {
            "hover_opacity": 0.08,
            "focus_opacity": 0.12,
            "pressed_opacity": 0.12,
            "dragged_opacity": 0.16,
            "disabled_content_opacity": 0.38,
            "disabled_container_opacity": 0.12,
        },
        "a11y": {
            "contrast_normal": 4.5,
            "contrast_large": 3.0,
            "focus_visible": True,
            "reduce_motion": True,
            "not_color_only": True,
        },
        "required_states": ["loading", "empty", "error", "disabled"],
    },
    "apple_hig": {
        "id": "apple_hig",
        "name": "Apple Human Interface Guidelines (iOS)",
        "vendor": "Apple",
        "docs": "https://developer.apple.com/design/human-interface-guidelines/",
        "platform": "iOS · iPadOS · macOS · tvOS · watchOS",
        "grid": {
            "base_pt": 8,
            "margin_compact_pt": 16,
            "margin_regular_pt": 20,
            "safe_area_required": True,
        },
        "shape_radius_pt": {"standard": 10, "note": "연속(continuous) 곡률 사용"},
        "type_ramp_summary": (
            "LargeTitle 34 · Title1 28 · Title2 22 · Title3 20 · Headline 17(semibold) · "
            "Body 17 · Callout 16 · Subhead 15 · Footnote 13 · Caption1 12 · Caption2 11"
        ),
        "touch_target_pt": 44,
        "elevation": ["material(Thin/Regular/Thick)", "floating layer shadow"],
        "motion": {
            "curve": "spring (감쇠 진동)",
            "note": "Reduce Motion 설정 시 크로스페이드로 대체",
        },
        "states": {
            "pressed": "highlight tint",
            "disabled": "감소된 불투명도(색상만으로 구분 금지)",
        },
        "a11y": {
            "contrast_normal": 4.5,
            "contrast_large": 3.0,
            "focus_visible": True,
            "dynamic_type": True,
            "voiceover_labels": True,
        },
        "required_states": ["loading", "empty", "error", "disabled"],
    },
    "fluent2": {
        "id": "fluent2",
        "name": "Microsoft Fluent 2",
        "vendor": "Microsoft",
        "docs": "https://fluent2.microsoft.design/",
        "platform": "Windows · 데스크톱(WPF/WinUI) · 웹",
        "grid": {"base_px": 4, "margin_px": 24, "gutter_px": 16},
        "shape_radius_px": {"small": 2, "medium": 4, "large": 8, "xlarge": 12},
        "type_ramp_summary": (
            "Display 68/92 · LargeTitle 40/52 · Title 28/36 · Subtitle 20/26 · "
            "BodyLarge 16/22 · Body 14/20 · Caption 12/16"
        ),
        "touch_target_px": 40,
        "elevation": ["shadow2", "shadow4", "shadow8", "shadow16", "shadow28", "shadow64"],
        "motion": {
            "duration_fast_ms": 100,
            "duration_normal_ms": 200,
            "duration_slow_ms": 300,
            "curves": ["decelerate", "accelerate", "standard"],
        },
        "states": {
            "rest": "opacity 1.0",
            "hover": "subtle tint",
            "pressed": "reduced tint",
            "disabled": "감소된 불투명도",
        },
        "a11y": {
            "contrast_normal": 4.5,
            "contrast_large": 3.0,
            "focus_visible": True,
            "high_contrast_mode": True,
        },
        "required_states": ["loading", "empty", "error", "disabled"],
    },
    "carbon": {
        "id": "carbon",
        "name": "IBM Carbon Design System",
        "vendor": "IBM",
        "docs": "https://carbondesignsystem.com/",
        "platform": "엔터프라이즈 웹 SaaS · 대시보드 · 데이터 제품",
        "grid": {"base_px": 8, "columns": 16, "margin_px": 16, "gutter_px": 32},
        "shape_radius_px": {"default": 0, "note": "Carbon 은 각진(squared) 형태를 표준으로 한다"},
        "spacing_scale_px": [2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96, 160],
        "type_ramp_summary": (
            "Display 54/76 · Heading 42/28/24/20/16/14 · Body 16/14 · "
            "Label 12 · Helper 12 · Caption 12"
        ),
        "touch_target_px": 48,
        "color_primary": "#0f62fe (Blue 60)",
        "elevation": ["box-shadow low/mid/high"],
        "motion": {
            "duration_ms": {"fast-01": 70, "fast-02": 110, "moderate-01": 150,
                            "moderate-02": 240, "slow-01": 400, "slow-02": 700},
            "easing": "cubic-bezier(0.2, 0, 0.38, 0.9)",
        },
        "states": {
            "hover": "layer tint",
            "focus": "2px focus ring",
            "active": "pressed tint",
            "disabled": "감소된 대비",
        },
        "a11y": {
            "contrast_normal": 4.5,
            "contrast_large": 3.0,
            "focus_visible": True,
            "keyboard_navigable": True,
        },
        "required_states": ["loading", "empty", "error", "disabled"],
    },
}

# 도메인 → 기본 공개 표준 (사용자 입력이 없을 때의 결정적 기본값)
_DOMAIN_DEFAULT_SYSTEM: Dict[str, str] = {
    "mobile_app": "material3",
    "android": "material3",
    "flutter": "material3",
    "react_native": "material3",
    "ios": "apple_hig",
    "saas_web": "carbon",
    "web": "carbon",
    "frontend": "carbon",
    "native_desktop": "fluent2",
    "windows": "fluent2",
}

# 사용자/레퍼런스가 쓸 수 있는 별칭 → 정식 ID
_SYSTEM_ALIASES: Dict[str, str] = {
    "material": "material3",
    "material3": "material3",
    "material_3": "material3",
    "m3": "material3",
    "apple": "apple_hig",
    "apple_hig": "apple_hig",
    "hig": "apple_hig",
    "ios": "apple_hig",
    "fluent": "fluent2",
    "fluent2": "fluent2",
    "fluent_2": "fluent2",
    "windows": "fluent2",
    "carbon": "carbon",
    "ibm": "carbon",
}


def list_design_systems() -> List[str]:
    """사용 가능한 공개 디자인 시스템 ID 목록."""
    return sorted(DESIGN_SYSTEMS.keys())


def resolve_design_system(domain: str, style: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """도메인/스타일 입력에서 공개 디자인 시스템을 결정적으로 해석한다.

    우선순위:
      1) style["design_system"] (사용자/레퍼런스 명시 오버라이드)
      2) 도메인 기본 매핑
      3) 없음 → None (그라운딩 생략)
    """
    if isinstance(style, dict):
        raw = str(style.get("design_system") or "").strip()
        if raw:
            key = raw.lower().replace(" ", "").replace("-", "_")
            sys_id = _SYSTEM_ALIASES.get(key, key)
            if sys_id in DESIGN_SYSTEMS:
                return DESIGN_SYSTEMS[sys_id]
            logger.debug(f"unknown design_system override ignored: {raw}")
    sys_id = _DOMAIN_DEFAULT_SYSTEM.get((domain or "").strip().lower())
    return DESIGN_SYSTEMS.get(sys_id) if sys_id else None


def apply_design_system(contract: Dict[str, Any], style: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """계약에 공개 표준 토큰을 `design_system` 섹션으로 상속한다 (in-place).

    기존 계약 필드(spacing/typography/...)는 그대로 두고, 명명된 표준의 상세 토큰을
    추가한다. 사용자 style 이 명시적으로 표준을 지정하면 그것을 우선한다.
    """
    if not isinstance(contract, dict):
        return None
    system = resolve_design_system(str(contract.get("domain", "")), style)
    if not system:
        return None
    contract["design_system"] = system
    return system


# ---------------------------------------------------------------------------
# 프롬프트 블록 (토큰 다이어트: ~900자 이내)
# ---------------------------------------------------------------------------
def _join_scale(mapping: Dict[str, Any]) -> str:
    return " / ".join(f"{k} {v}" for k, v in mapping.items() if not isinstance(v, str))


def _join_named(mapping: Dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in mapping.items())


def format_design_system_block(contract: Dict[str, Any]) -> str:
    """Coder/QA 프롬프트용 '외부 디자인 표준' 요약 블록. 표준이 없으면 빈 문자열."""
    system = contract.get("design_system") if isinstance(contract, dict) else None
    if not isinstance(system, dict) or not system.get("name"):
        return ""

    lines = [
        "🎨 **[외부 디자인 표준 그라운딩 (Public Design System — 사용자 취향 입력 없이도 디테일 기준)]**:",
        f"- 기준 표준: {system['name']} ({system.get('vendor', '')}) — {system.get('docs', '')}",
    ]

    grid = system.get("grid") if isinstance(system.get("grid"), dict) else {}
    if grid:
        parts: List[str] = []
        if grid.get("base_dp"):
            parts.append(f"{grid['base_dp']}dp 기준")
        if grid.get("base_pt"):
            parts.append(f"{grid['base_pt']}pt 기준")
        if grid.get("base_px"):
            parts.append(f"{grid['base_px']}px 기준")
        cols = grid.get("columns")
        if isinstance(cols, dict):
            parts.append("컬럼 " + "/".join(str(v) for v in cols.values()))
        elif cols:
            parts.append(f"컬럼 {cols}")
        for label, key in (("마진", "margin_dp"), ("거터", "gutter_dp"),
                           ("마진", "margin_px"), ("거터", "gutter_px"),
                           ("마진(compact)", "margin_compact_pt"), ("마진(regular)", "margin_regular_pt")):
            if grid.get(key):
                parts.append(f"{label} {grid[key]}")
        if parts:
            lines.append("- 그리드: " + " · ".join(parts))

    shapes = (system.get("shape_radius_dp") or system.get("shape_radius_px")
              or system.get("shape_radius_pt"))
    if isinstance(shapes, dict):
        lines.append("- 형태(radius): " + _join_named(shapes) + " (임의값 금지)")
    if system.get("spacing_scale_px"):
        lines.append("- 간격 스케일: " + "/".join(str(v) for v in system["spacing_scale_px"]) + "px 만 사용")
    if system.get("type_ramp_summary"):
        lines.append("- 타이포 램프: " + str(system["type_ramp_summary"]))
    if system.get("color_primary"):
        lines.append("- Primary: " + str(system["color_primary"]))

    elevation = system.get("elevation_dp") or system.get("elevation")
    if elevation:
        if isinstance(elevation, list) and elevation and isinstance(elevation[0], int):
            lines.append("- 엘리베이션(dp): " + "/".join(str(v) for v in elevation))
        else:
            lines.append("- 엘리베이션: " + ", ".join(str(e) for e in elevation))

    motion = system.get("motion") if isinstance(system.get("motion"), dict) else {}
    if motion:
        bits: List[str] = []
        if motion.get("emphasized_easing"):
            bits.append(f"easing {motion['emphasized_easing']}")
        if motion.get("easing"):
            bits.append(f"easing {motion['easing']}")
        if motion.get("curve"):
            bits.append(str(motion["curve"]))
        for label, key in (("short", "short_ms"), ("medium", "medium_ms"), ("long", "long_ms")):
            if motion.get(key):
                bits.append(f"{label} {motion[key][0]}~{motion[key][-1]}ms")
        if motion.get("duration_fast_ms"):
            bits.append(f"fast {motion['duration_fast_ms']} / normal {motion.get('duration_normal_ms')} / slow {motion.get('duration_slow_ms')}ms")
        if motion.get("duration_ms"):
            bits.append("/".join(f"{k} {v}" for k, v in motion["duration_ms"].items()) + "ms")
        if bits:
            lines.append("- 모션: " + " · ".join(bits))

    states = system.get("states") if isinstance(system.get("states"), dict) else {}
    if states:
        lines.append("- 인터랙션 상태: " + _join_named(states))

    a11y = system.get("a11y") if isinstance(system.get("a11y"), dict) else {}
    if a11y:
        bits = []
        if a11y.get("contrast_normal"):
            bits.append(f"명암비 {a11y['contrast_normal']}:1(본문)/{a11y.get('contrast_large')}:1(대형)")
        if a11y.get("focus_visible"):
            bits.append("포커스 시인")
        if a11y.get("not_color_only"):
            bits.append("색상만으로 상태 구분 금지")
        if a11y.get("dynamic_type"):
            bits.append("Dynamic Type 지원")
        if a11y.get("reduce_motion"):
            bits.append("모션 감소 존중")
        if a11y.get("high_contrast_mode"):
            bits.append("고대비 모드 지원")
        if a11y.get("keyboard_navigable"):
            bits.append("키보드 내비게이션")
        if bits:
            lines.append("- 접근성: " + " · ".join(bits))

    if system.get("required_states"):
        lines.append("- 상태 커버리지 필수: " + " / ".join(str(s) for s in system["required_states"]))

    return "\n".join(lines) + "\n"
