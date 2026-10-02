#!/usr/bin/env python3
"""
diagnose_model_routing.py — `!model` SSOT ↔ 실제 런타임 엔진/모델 정합성 진단기
================================================================================

사용자 보고: "!model 로 OpenCode 봇으로 세팅했는데 실제로는 OpenRouter(Hermes) 봇이 돌았다."

이 스크립트는 전역 설정(GlobalModelManager), .env, 그리고 각 채널 세션에 남아 있는
엔진 값을 한 번에 읽어 다음을 판정한다.

  • 역할별 전역 엔진/모델이 실제 실행 가능한 조합인가 (engine ≠ model owner)
  • 채널 세션의 stale qa_engine / qa2_engine / auditor_engine 이 `!model` 을 덮어쓰는가
  • opencode-go/* 모델이 OpenRouter(Hermes) 로 새는 조합이 남아 있는가

사용법:
    cd agy_agent && ./.venv/bin/python scripts/diagnose_model_routing.py
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

os.environ.setdefault("UNITTEST_RUNNING", "1")  # 중복 초기화/디스코드 부작용 방지


def _load_sessions():
    path = os.path.join(PARENT, "state", "channel_sessions.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> int:
    import model_manager
    from model_manager import GlobalModelManager as G

    G.initialize()

    print("=" * 78)
    print(" 1) 전역 역할 설정 (GlobalModelManager = `!model` SSOT)")
    print("=" * 78)
    problems = []
    print(f"{'ROLE':<10} {'ENGINE':<10} {'MODEL':<38} {'OWNER':<9} COMPAT")
    for role, cfg in G.get_all_roles().items():
        eng = cfg.get("engine", "")
        mdl = (cfg.get("model") or "").strip()
        owner = G.model_owner_engine(mdl)
        compat = G.is_model_compatible_with_engine(eng, mdl)
        print(f"{role:<10} {str(eng):<10} {mdl:<38} {str(owner):<9} {compat}")
        if mdl and not compat:
            problems.append(f"역할 '{role}': 엔진={eng} + 모델={mdl} (소속={owner}) → 실행 불가 조합")

    print()
    print("=" * 78)
    print(" 2) 채널 세션의 stale 엔진 값 (런타임 덮어쓰기 위험)")
    print("=" * 78)
    sessions = _load_sessions()
    stale = {"qa_engine": [], "qa2_engine": [], "auditor_engine": [], "engine": []}
    for ch, s in sessions.items():
        for field, expected in (
            ("qa_engine", G.get_role_engine("qa1")),
            ("qa2_engine", G.get_role_engine("qa2")),
            ("auditor_engine", G.get_role_engine("auditor")),
            ("engine", G.get_role_engine("coder")),
        ):
            val = (s.get(field) or "").lower()
            if val and expected and val != expected.lower():
                stale[field].append((ch, val, expected))
    for field, rows in stale.items():
        print(f"  {field}: 불일치 {len(rows)}건")
        for ch, val, exp in rows[:5]:
            print(f"     - ch {ch}: 세션={val} ≠ !model={exp}")
    if not any(stale.values()):
        print("  ✅ 세션 엔진 값이 `!model` 과 일치합니다.")

    print()
    print("=" * 78)
    if problems:
        print(f" ❌ 정합성 문제 {len(problems)}건 발견:")
        for p in problems:
            print("   -", p)
        return 2
    print(" ✅ 역할 엔진/모델 정합성 100% — `!model` 선택대로 실행 가능한 상태입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
