#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
craft_inputs.py

AGY — 사용자 제공 크래프트 입력(레퍼런스 / 하우스 룰 / 카피 톤 / 가치 브리프) SSOT.

문제 정의 (디테일 공백의 실체):
- 레퍼런스가 텍스트 문자열로만 존재해 Coder/QA 프롬프트에 '이미지/목표'로 닿지 않는다.
  (`plan_wizard.py` 의 benchmark_reference 는 문자열이고, `design_refs/*` 는 아무도 읽지 않는다.)
- 검증은 '우리 계약 위반'만 보고 '레퍼런스 대비 델타'를 보지 않는다.
- 우선순위는 하드코딩 카탈로그에서 나오고 사용자 가치함수(누가/단 하나의 Job/틀리면 비용)가
  입력될 슬롯이 없다.

해결 (단일 파일 SSOT):
- `<workspace>/reports/gate2/craft_inputs.json` 을 크래프트 입력의 단일 기준으로 둔다.
    · reference_images : 레퍼런스 이미지 경로. 비어 있으면 design_refs/* 자동 수집 폴백.
    · house_rules      : 3~5개 강한 규칙 (밀도/라운드/모션/카피 톤).
    · copy_tone        : 문구 톤 1줄 + 금지어/선호어.
    · value_brief      : {who, one_job, cost_of_wrong} — 우선순위 tie-break 입력.
- 결정적(Deterministic) 프롬프트 블록 생성 + 파서만 제공한다 (네트워크/LLM 호출 없음).

CLI (사용자 입력 채널):
    python craft_inputs.py --workspace ./myapp \
        --reference reports/gate2/refs/home.png \
        --house-rule "여백 넉넉, 화면당 Primary CTA 1개" \
        --copy-tone "동사구 라벨, 마케팅 문구 금지" \
        --who "1인 개발자" --one-job "30초 안에 기록 1건 저장" --cost "저장 실패 시 신뢰 붕괴"
"""

from __future__ import annotations

import os
import re
import json
from typing import Any, Dict, List, Optional, Sequence

CRAFT_INPUTS_REL = os.path.join("reports", "gate2", "craft_inputs.json")
DESIGN_TOKENS_REL = os.path.join("reports", "gate2", "design_tokens.json")
DESIGN_REFERENCE_REL = os.path.join("reports", "gate2", "design_reference.png")
INPUTS_VERSION = 1

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
_DEFAULT_INPUTS: Dict[str, Any] = {
    "version": INPUTS_VERSION,
    "reference_images": [],
    "house_rules": [],
    "copy_tone": "",
    "value_brief": {"who": "", "one_job": "", "cost_of_wrong": ""},
}


# ---------------------------------------------------------------------------
# 환경 플래그 (운영 중 즉시 비활성화 가능)
# ---------------------------------------------------------------------------
def _env_flag(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or default).strip().lower() not in ("0", "false", "off", "no")


def reference_target_enabled() -> bool:
    return _env_flag("CRAFT_REFERENCE_TARGET_ENABLED", "1")


def divergence_enabled() -> bool:
    return _env_flag("CRAFT_DIVERGENCE_ENABLED", "1")


def package_refs_enabled() -> bool:
    """에이전트 패키지 동봉 design_refs/* 를 전역 레퍼런스 폴백으로 쓸지 여부."""
    return _env_flag("CRAFT_PACKAGE_REFS_ENABLED", "1")


# ---------------------------------------------------------------------------
# 입출력
# ---------------------------------------------------------------------------
def _package_design_refs_dir() -> str:
    """에이전트 패키지에 동봉된 전역 레퍼런스 폴더(최후순위 폴백)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "design_refs")


def load_inputs(workspace: str) -> Dict[str, Any]:
    """craft_inputs.json 을 방어적으로 로드한다. 없거나 손상되면 기본값."""
    data: Dict[str, Any] = {
        "version": INPUTS_VERSION,
        "reference_images": list(_DEFAULT_INPUTS["reference_images"]),
        "house_rules": list(_DEFAULT_INPUTS["house_rules"]),
        "copy_tone": "",
        "value_brief": dict(_DEFAULT_INPUTS["value_brief"]),
    }
    if not workspace:
        return data
    path = os.path.join(workspace, CRAFT_INPUTS_REL)
    if not os.path.isfile(path):
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            return data
        refs = loaded.get("reference_images")
        if isinstance(refs, list):
            data["reference_images"] = [str(x) for x in refs if str(x).strip()]
        rules = loaded.get("house_rules")
        if isinstance(rules, list):
            data["house_rules"] = [str(x).strip() for x in rules if str(x).strip()]
        if isinstance(loaded.get("copy_tone"), str):
            data["copy_tone"] = loaded["copy_tone"].strip()
        vb = loaded.get("value_brief")
        if isinstance(vb, dict):
            for key in ("who", "one_job", "cost_of_wrong"):
                if isinstance(vb.get(key), str):
                    data["value_brief"][key] = vb[key].strip()
        if isinstance(loaded.get("version"), int):
            data["version"] = loaded["version"]
    except Exception:
        return data
    return data


def save_inputs(workspace: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """기존 입력에 patch 를 병합해 저장한다 (얕은 병합 + value_brief 는 키 단위 병합)."""
    data = load_inputs(workspace)
    if not isinstance(patch, dict):
        return data
    for key in ("reference_images", "house_rules"):
        if isinstance(patch.get(key), list):
            data[key] = [str(x).strip() for x in patch[key] if str(x).strip()]
    if isinstance(patch.get("copy_tone"), str):
        data["copy_tone"] = patch["copy_tone"].strip()
    vb_patch = patch.get("value_brief")
    if isinstance(vb_patch, dict):
        for key in ("who", "one_job", "cost_of_wrong"):
            if isinstance(vb_patch.get(key), str):
                data["value_brief"][key] = vb_patch[key].strip()
    if not workspace:
        return data
    try:
        path = os.path.join(workspace, CRAFT_INPUTS_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return data


# ---------------------------------------------------------------------------
# 가치 브리프 (P0-3: 우선순위 tie-break 외부 신호)
# ---------------------------------------------------------------------------
def load_value_brief(workspace: str) -> Dict[str, str]:
    vb = load_inputs(workspace).get("value_brief", {})
    return {k: str(vb.get(k, "") or "") for k in ("who", "one_job", "cost_of_wrong")}


def value_brief_text(workspace: str) -> str:
    vb = load_value_brief(workspace)
    parts = [vb.get("who", ""), vb.get("one_job", ""), vb.get("cost_of_wrong", "")]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# 스타일 토큰 SSOT (P0-2 / P2-7)
# ---------------------------------------------------------------------------
def load_style_tokens(workspace: str) -> Dict[str, Any]:
    """design_tokens.json(기존 SSOT) + craft_inputs.json 의 스타일 입력을 병합해 반환한다."""
    style: Dict[str, Any] = {}
    if workspace:
        tokens_path = os.path.join(workspace, DESIGN_TOKENS_REL)
        if os.path.isfile(tokens_path):
            try:
                with open(tokens_path, "r", encoding="utf-8") as f:
                    tokens = json.load(f)
                if isinstance(tokens, dict):
                    for key in ("style_archetype", "palette", "card_radius", "motion",
                                "primary_color", "accent_color", "background_color",
                                "design_system"):
                        if tokens.get(key):
                            style[key] = tokens[key]
            except Exception:
                pass

    inputs = load_inputs(workspace)
    if inputs.get("house_rules"):
        style["house_rules"] = list(inputs["house_rules"])
    if inputs.get("copy_tone"):
        style["copy_tone"] = inputs["copy_tone"]
    return style


# ---------------------------------------------------------------------------
# 레퍼런스 이미지 해석 (P0-1)
# ---------------------------------------------------------------------------
def _normalize_image_path(workspace: str, raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""
    path = raw if os.path.isabs(raw) else os.path.join(workspace or "", raw)
    return os.path.abspath(path)


def _iter_dir_images(directory: str) -> List[str]:
    if not directory or not os.path.isdir(directory):
        return []
    found: List[str] = []
    try:
        for name in sorted(os.listdir(directory)):
            if name.lower().endswith(_IMAGE_EXTS):
                found.append(os.path.join(directory, name))
    except Exception:
        return []
    return found


def resolve_reference_images(
    workspace: str,
    extra_images: Optional[Sequence[Any]] = None,
    max_images: int = 2,
    include_package_refs: bool = True,
) -> List[str]:
    """레퍼런스 이미지 경로를 우선순위대로 해석한다 (존재하는 파일만, 중복 제거, 상한 적용).

    우선순위:
      1) craft_inputs.json 의 reference_images (사용자 명시)
      2) extra_images (세션에 첨부된 사용자 레퍼런스 이미지)
      3) <workspace>/.references/user_inputs/* (세션 첨부 보존 위치)
      4) <workspace>/design_refs/*
      5) <workspace>/reports/gate2/design_reference.png (스크레이핑 레퍼런스)
      6) 패키지 동봉 design_refs/* (전역 폴백 · `include_package_refs=True` 일 때만)

    `include_package_refs=False` 는 델타 '검증'처럼 프로젝트 고유 레퍼런스만 공정한
    판정 기준이 되어야 하는 경우에 사용한다(전역 폴백은 프롬프트 가이드 전용).
    """
    if max_images <= 0:
        return []
    ordered: List[str] = []

    for raw in load_inputs(workspace).get("reference_images", []) or []:
        ordered.append(_normalize_image_path(workspace, raw))

    for item in extra_images or []:
        if isinstance(item, dict):
            ordered.append(_normalize_image_path(workspace, item.get("path") or item.get("file_path") or ""))
        elif isinstance(item, str):
            ordered.append(_normalize_image_path(workspace, item))

    ordered.extend(_iter_dir_images(os.path.join(workspace or "", ".references", "user_inputs")))
    ordered.extend(_iter_dir_images(os.path.join(workspace or "", "design_refs")))
    ordered.append(os.path.join(workspace or "", DESIGN_REFERENCE_REL))
    if include_package_refs and package_refs_enabled():
        ordered.extend(_iter_dir_images(_package_design_refs_dir()))

    resolved: List[str] = []
    seen = set()
    for path in ordered:
        if not path or not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        resolved.append(path)
        if len(resolved) >= max_images:
            break
    return resolved


# ---------------------------------------------------------------------------
# 프롬프트 블록 (P0-1 레퍼런스 타깃+델타 / P1-5 발산)
# ---------------------------------------------------------------------------
def build_reference_target_block(
    workspace: str,
    extra_images: Optional[Sequence[Any]] = None,
    max_images: int = 2,
) -> str:
    """Coder/QA 프롬프트용 '레퍼런스 스타일 타깃 + 델타' 블록. 레퍼런스 없으면 빈 문자열."""
    if not reference_target_enabled():
        return ""
    refs = resolve_reference_images(workspace, extra_images=extra_images, max_images=max_images)
    if not refs:
        return ""
    listing = " · ".join(f"`{os.path.basename(p)}`" for p in refs)
    return (
        "\n🖼️ **[레퍼런스 스타일 타깃 + 델타 지시 (Reference Target & Delta)]**:\n"
        f"- 타깃 이미지: {listing} (직접 열어 밀도/여백/타이포 위계/색 사용량을 관찰)\n"
        "- **델타 규칙**: 정체성(로고·문구·고유 색·일러스트)은 절대 복제하지 말고 새로 만들되, "
        "[여백 리듬 · 그리드 밀도 · 타이포 위계 · 주요 액션 배치]는 타깃과 일치시킨다.\n"
        "- 표절 금지: 픽셀/외곽선 추출(Image.open/cv2/findContours) 금지 — 기존 anti-tracing 규칙을 그대로 유지한다.\n"
    )


def build_divergence_block(max_alternatives: int = 3) -> str:
    """디테일 결함이 잡힌 화면에 한해 대안을 나란히 비교하게 하는 발산 지시 (P1-5).

    전면 도입이 아니라, 스페이싱/CTA/빈 상태 등 '디테일' 화면에만 국소 적용한다.
    """
    if not divergence_enabled():
        return ""
    return (
        "\n🔀 **[디테일 발산 지시 (Diverge-then-Converge, 국소 적용)]**:\n"
        f"- 스페이싱/CTA 배치/빈 상태(empty state)/에러 문구처럼 '디테일'에 해당하는 화면에 한해, "
        f"구현 전에 {max_alternatives}개 대안(여백/위계/문구가 서로 다른 안)을 먼저 나열하고 "
        "가장 적합한 1개를 근거와 함께 선택하십시오.\n"
        "- 모든 화면에 적용하지 마십시오(비용). 첫 안을 그대로 확정하지 마십시오.\n"
    )


# ---------------------------------------------------------------------------
# 레퍼런스 델타 판정 파서 (P0-4)
# ---------------------------------------------------------------------------
_REF_DEFECT_RE = re.compile(
    r"^\s*[-•*]\s*(BLOCKER|CRITICAL|MAJOR|MINOR|WARNING|INFO)\s*\|\s*([A-Za-z0-9_\-]+)\s*\|\s*([^|\n]*)\|?\s*([^|\n]*)\|?\s*([^\n]*)$",
    re.IGNORECASE | re.MULTILINE,
)


def build_reference_delta_rubric(contract: Dict[str, Any], reference_names: Sequence[str], app_name: str = "") -> str:
    """레퍼런스 vs 산출물의 '스타일 델타'만 보게 하는 결정적 루브릭."""
    names = ", ".join(os.path.basename(n) for n in reference_names) or "reference"
    return (
        f"[검사 대상]: {app_name or contract.get('app_name') or 'product'}\n"
        f"[레퍼런스 타깃]: {names} (합성 이미지의 왼쪽)\n"
        "[비교 대상]: 실제 산출물 스크린샷 (합성 이미지의 오른쪽)\n\n"
        "두 이미지의 **스타일 델타만** 냉정하게 비교하십시오. 정체성(로고/문구/고유 색)의 "
        "동일성은 평가하지 마십시오(표절과 혼동 금지). 아래 차원의 '차이'만 적발합니다:\n"
        "1. DENSITY_GAP: 여백 리듬/수직 호흡/정보 밀도가 타깃 대비 과밀 또는 과소.\n"
        "2. TYPO_RHYTHM_GAP: 제목/본문/캡션의 크기·굵기 위계 리듬이 타깃보다 평평하거나 어긋남.\n"
        "3. PALETTE_USAGE_GAP: 색 사용량/역할 분담이 타깃 대비 산만하거나 무채색으로 죽어 있음.\n"
        "4. ACTION_PLACEMENT_GAP: 주요 액션(CTA)의 위치/개수/위계가 타깃 동선과 어긋남.\n"
        "5. CARD_GROUPING_GAP: 카드/구획 리듬(중첩/구분선)이 타깃보다 과하거나 붕괴됨.\n\n"
        "JSON/설명 없이 아래 형식만 정확히 출력하십시오 (항목은 최대 6개, 타깃과 유사하면 결함 없음):\n"
        "[REFERENCE_DELTA_VERDICT]: PASS 또는 FAIL\n"
        "[REFERENCE_DELTA_SCORE]: 0~100 사이 정수 (타깃 스타일 근접도)\n"
        "[REFERENCE_DELTA_SUMMARY]: 한 줄 총평 (30자 이내)\n"
        "[REFERENCE_DELTA_DEFECTS]:\n"
        "- MAJOR | DENSITY_GAP | 상단 영역 | 타깃은 여백이 넉넉한데 산출물은 카드가 과밀 | 카드 간 24px 호흡 확보\n"
        "- MINOR | TYPO_RHYTHM_GAP | 목록 헤더 | 제목/본문 크기 차가 타깃보다 작음 | 제목 1.5rem, 본문 1rem로 위계 강화\n"
    )


def parse_reference_delta(text: str, threshold: Optional[int] = None) -> Dict[str, Any]:
    """Vision 응답에서 [REFERENCE_DELTA_*] 를 결정적으로 파싱한다."""
    result: Dict[str, Any] = {"verdict": None, "score": None, "defects": [], "summary": ""}
    if not text:
        return result

    m_verdict = re.search(r"\[REFERENCE_DELTA_VERDICT\]\s*:?\s*(PASS|FAIL)", text, re.IGNORECASE)
    if m_verdict:
        result["verdict"] = m_verdict.group(1).upper()

    m_score = re.search(r"\[REFERENCE_DELTA_SCORE\]\s*:?\s*(\d{1,3})", text, re.IGNORECASE)
    if m_score:
        try:
            score = int(m_score.group(1))
            if 0 <= score <= 100:
                result["score"] = score
        except Exception:
            pass

    for m in _REF_DEFECT_RE.finditer(text):
        sev = m.group(1).upper()
        if sev == "WARNING":
            sev = "MINOR"
        result["defects"].append({
            "severity": sev,
            "rule_id": m.group(2).upper(),
            "location": m.group(3).strip(),
            "issue": m.group(4).strip(),
            "fix": m.group(5).strip(),
        })

    m_sum = re.search(r"\[REFERENCE_DELTA_SUMMARY\]\s*:?\s*([^\n]+)", text, re.IGNORECASE)
    if m_sum:
        result["summary"] = m_sum.group(1).strip()

    blocking = [d for d in result["defects"] if d["severity"] in ("BLOCKER", "CRITICAL", "MAJOR")]
    if result["verdict"] is None:
        if result["score"] is not None:
            thr = threshold if threshold is not None else 70
            result["verdict"] = "FAIL" if (result["score"] < thr or blocking) else "PASS"
        else:
            result["verdict"] = "FAIL" if blocking else "PASS"
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser():
    import argparse
    parser = argparse.ArgumentParser(description="AGY 크래프트 입력(레퍼런스/하우스 룰/카피 톤/가치 브리프) 등록")
    parser.add_argument("--workspace", default=".", help="대상 프로젝트 워크스페이스")
    parser.add_argument("--reference", action="append", default=[], help="레퍼런스 이미지 경로(반복 가능)")
    parser.add_argument("--house-rule", action="append", default=[], help="하우스 룰(반복 가능)")
    parser.add_argument("--copy-tone", default="", help="카피 톤 1줄")
    parser.add_argument("--who", default="", help="사용자: 누가 쓰는가")
    parser.add_argument("--one-job", dest="one_job", default="", help="단 하나의 Job")
    parser.add_argument("--cost", dest="cost_of_wrong", default="", help="틀리면 비용")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    args = _build_arg_parser().parse_args(argv)
    patch: Dict[str, Any] = {}
    if args.reference:
        patch["reference_images"] = args.reference
    if args.house_rule:
        patch["house_rules"] = args.house_rule
    if args.copy_tone:
        patch["copy_tone"] = args.copy_tone
    if args.who or args.one_job or args.cost_of_wrong:
        patch["value_brief"] = {
            "who": args.who, "one_job": args.one_job, "cost_of_wrong": args.cost_of_wrong,
        }
    saved = save_inputs(args.workspace, patch)
    print(json.dumps(saved, ensure_ascii=False, indent=2))
    return saved


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
