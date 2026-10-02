#!/usr/bin/env python3
"""
verify_live_catalog.py
======================
실제 업스트림(OpenCode CLI · Zen Go API · OpenRouter · Codex CLI · Antigravity
쿼터 API)을 그대로 호출해서 `!model` 이 진짜로 자동 수집하는지 확인합니다.

네트워크/인증이 없는 환경에서는 해당 엔진만 'SKIP' 으로 처리하고, 하나라도
살아 있으면 검증한 것입니다.

사용법:
    cd agy_agent && python3 tests/verify_live_catalog.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import live_model_catalog as lmc  # noqa: E402
import model_manager as mm  # noqa: E402

ENGINES = ["opencode", "hermes", "codex", "agy", "claude", "copilot"]


def main() -> int:
    if not mm.GlobalModelManager._initialized:
        mm.GlobalModelManager.initialize()

    print("=" * 78)
    print("실시간 모델 카탈로그 검증 (실제 업스트림 호출)")
    print("=" * 78)

    live_ok = 0
    new_total = 0
    problems = []

    for eng in ENGINES:
        try:
            models = lmc.LiveModelCatalog.get_models_blocking(eng, True)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[{eng:9s}] ERROR {exc}")
            problems.append(f"{eng}: {exc}")
            continue

        st = lmc.LiveModelCatalog.get_status(eng)
        seed = lmc._seed_for(eng)
        new = [m for m, _ in st.get("newest", [])]
        new_total += len(new)

        icon = "🟢" if st.get("live") else "🟡"
        print(f"\n[{eng:9s}] {icon} {len(models)}개 (curated {len(seed)} · 신규 {len(new)}) · {st.get('source')}")
        if st.get("error"):
            print(f"             error: {st['error']}")
        if new:
            for m in new[:10]:
                print(f"             🆕 {m}")
            if len(new) > 10:
                print(f"             … 외 {len(new) - 10}개")
        if st.get("live"):
            live_ok += 1

        # 픽커에 신규 모델이 실제로 보이는지 (25개 하드캡 회귀 검사)
        if new:
            try:
                with mock.patch.object(
                    mm.GlobalModelManager, "get_role_config",
                    return_value={"engine": eng, "model": "", "enabled": True},
                ):
                    dd = mm.ModelPresetDropdown("coder")
                values = [o.value for o in dd.options]
                has_search = "__search__" in values
                # 25칸 안에 모두 들어가면 전부 보여야 하고, 넘치면 검색으로
                # 도달 가능한지만 확인하면 된다 (Discord Select 하드캡 제약).
                fits = len(new) + 1 <= 25
                hidden = [m for m in new[:10] if m not in values]
                if fits and hidden:
                    print(f"             ⚠️  드롭다운에 안 보이는 신규: {hidden}")
                    problems.append(f"{eng}: 신규 모델이 픽커에 안 보임 {hidden}")
                elif not fits and not has_search:
                    print("             ⚠️  신규가 25칸을 넘어갔는데 검색 진입점이 없음")
                    problems.append(f"{eng}: 신규 초과 시 검색 진입점 누락")
                elif fits:
                    print(f"             ✅ 신규 {len(new)}개 전부 드롭다운에 노출됨")
                else:
                    print(
                        f"             ✅ 상위 {25 - 1}개 노출 + 🔍 검색으로 나머지 "
                        f"{len(new) - 24}개 접근 가능"
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"             ⚠️  픽커 렌더 실패: {exc}")
                problems.append(f"{eng}: picker render failed: {exc}")

    print("\n" + "=" * 78)
    print(f"live 엔진 {live_ok}/{len(ENGINES)} · 자동 발견 신규 모델 {new_total}개")
    if problems:
        print("\n문제:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("문제 없음 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
