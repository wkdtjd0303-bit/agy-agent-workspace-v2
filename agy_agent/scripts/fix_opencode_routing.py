#!/usr/bin/env python3
"""
fix_opencode_routing.py
=======================
[BUG FIX] `agent_runner.py` 의 AGY CLI Guard 가 `opencode-go/...` / `opencode/...`
모델을 OpenRouter 모델로 오인하여 Hermes(OpenRouter) 어댑터로 잘못 전송하던
문제를 수정합니다.

증상 (QA 봇 동작 타이밍):
    HTTP 400: opencode-go/mimo-v2.6-flash is not a valid model ID
    (그 뒤에 agent_runner 의 범용 폴백 문구
     "이전 대화 컨텍스트가 너무 크거나 누적된 상태일 수 있습니다" 가 따라붙음)

원인:
    agent_runner.py 의 `_execute_single_run()` 안 AGY CLI Guard 분기에서
        ("/" in effective_model) and not any(k in model for k in ("claude","gpt-oss","gemini"))
    조건이 `opencode-go/*` 모델에도 매칭되어 Hermes(OpenRouter)로 전송됨.
    (OpenRouter 는 `opencode-go/` 접두사를 모르기 때문에 HTTP 400 반환.
     참고: `opencode-go/mimo-v2.6-flash` 는 OpenCode CLI 에는 존재하는 정상 모델임)

수정:
    `opencode-go/` 및 `opencode/` 접두사 모델은 AGY CLI / OpenRouter 가 아니라
    OpenCode 어댑터(EngineManager.get_adapter("opencode"))로 직접 전송합니다.

대상 파일 해석:
    이 디렉토리(!5_agy_agent)의 `agy_agent` 는 실제 코드(!05_agy_agent/agy_agent)
    로 향하는 심볼릭 링크입니다. 스크립트는 자신의 위치를 기준으로 대상을 찾으므로
    경로가 바뀌어도 동작합니다. `--target` 으로 명시 지정도 가능합니다.

사용법:
    python3 fix_opencode_routing.py --dry-run   # 변경 미리보기 (파일 수정 안 함)
    python3 fix_opencode_routing.py             # 실제 적용 (원본은 .bak_<ts> 로 백업)
    python3 fix_opencode_routing.py --check      # 대상 파일의 패치 상태만 확인
    python3 fix_opencode_routing.py --target /절대/경로/agent_runner.py
"""

import argparse
import datetime
import shutil
import sys
from pathlib import Path

# 스크립트 위치 기준 대상 후보 (심볼릭 링크를 관통하여 실제 파일을 가리킴)
_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TARGETS = [
    _SCRIPT_DIR / "agy_agent" / "agent_runner.py",
    Path("/home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/agent_runner.py"),
    Path("/home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent/agent_runner.py"),
]

# --- 수정 전 (정확히 이 블록을 찾아 교체) ---
OLD = """        if is_native:
            pass
        elif (
            effective_model.lower().startswith("openrouter/")
            or ("/" in effective_model and not any(k in effective_model.lower() for k in ("claude", "gpt-oss", "gemini")))
            or any(marker in effective_model.lower() for marker in ("solar", "deepseek", "qwen", "llama", "ling", "minimax"))
        ):
            from engine_manager import EngineManager
            hermes_adapter = EngineManager.get_adapter("hermes")
            if hermes_adapter:
                logger.info(
                    f"Direct Dispatch: Non-Antigravity OpenRouter model '{effective_model}' dispatched directly to Hermes Agent (OpenRouter API) without Gemini fallback."
                )
                code, resp, errors = await hermes_adapter.execute(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=effective_model,
                    role=session_role
                )
                return code, resp, errors, []
"""

# --- 수정 후 (OpenCode 우선 분기 추가) ---
NEW = """        if is_native:
            pass
        elif effective_model.lower().startswith(("opencode-go/", "opencode/")):
            # [FIX] OpenCode Go 모델은 AGY CLI/OpenRouter 가 아니라 OpenCode 어댑터로 직접 전송합니다.
            # (기존 버그: '/' 포함 조건에 걸려 Hermes(OpenRouter)로 오전송 -> HTTP 400 not a valid model ID)
            from engine_manager import EngineManager
            opencode_adapter = EngineManager.get_adapter("opencode")
            if opencode_adapter:
                logger.info(
                    f"Direct Dispatch: OpenCode model '{effective_model}' dispatched directly to OpenCode adapter (opencode CLI session)."
                )
                code, resp, errors = await opencode_adapter.execute(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=effective_model,
                    role=session_role
                )
                return code, resp, errors, []
        elif (
            effective_model.lower().startswith("openrouter/")
            or ("/" in effective_model and not any(k in effective_model.lower() for k in ("claude", "gpt-oss", "gemini")))
            or any(marker in effective_model.lower() for marker in ("solar", "deepseek", "qwen", "llama", "ling", "minimax"))
        ):
            from engine_manager import EngineManager
            hermes_adapter = EngineManager.get_adapter("hermes")
            if hermes_adapter:
                logger.info(
                    f"Direct Dispatch: Non-Antigravity OpenRouter model '{effective_model}' dispatched directly to Hermes Agent (OpenRouter API) without Gemini fallback."
                )
                code, resp, errors = await hermes_adapter.execute(
                    session=session,
                    prompt=prompt,
                    on_tool_update=on_tool_update,
                    model=effective_model,
                    role=session_role
                )
                return code, resp, errors, []
"""

PATCH_MARKER = "Direct Dispatch: OpenCode model '"


def _resolve_target(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for cand in DEFAULT_TARGETS:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _is_patched(src: str) -> bool:
    return PATCH_MARKER in src


def main() -> int:
    ap = argparse.ArgumentParser(description="opencode-go/* 모델의 Hermes 오라우팅 수정")
    ap.add_argument("--dry-run", action="store_true", help="변경 미리보기만 수행")
    ap.add_argument("--check", action="store_true", help="패치 상태만 확인")
    ap.add_argument("--target", default=None, help="agent_runner.py 절대 경로 (기본: 스크립트 위치 기준 자동 탐색)")
    args = ap.parse_args()

    target = _resolve_target(args.target)
    if target is None:
        print("❌ 대상 agent_runner.py 를 찾을 수 없습니다.")
        print("   --target /절대/경로/agent_runner.py 로 지정해 주세요.")
        return 1

    print(f"🎯 대상 파일: {target}")

    try:
        src = target.read_text(encoding="utf-8")
    except OSError as e:
        print(f"❌ 대상 파일을 읽을 수 없습니다: {e}")
        return 1

    if _is_patched(src):
        print("ℹ️  이미 패치가 적용되어 있습니다. (변경 없음)")
        return 0

    if args.check:
        print("⚠️  아직 패치되지 않았습니다. (--dry-run 으로 확인 후 실제 적용하세요)")
        return 0

    count = src.count(OLD)
    if count != 1:
        print(f"❌ 예상한 원본 블록을 정확히 1개 찾지 못했습니다 (발견: {count}개).")
        print("   파일이 이미 수정되었거나 포맷이 다릅니다. 수동 확인이 필요합니다.")
        return 2

    patched = src.replace(OLD, NEW, 1)

    # 문법 검증 (쓰기 전에 먼저)
    try:
        compile(patched, str(target), "exec")
    except SyntaxError as e:
        print(f"❌ 패치 결과에 문법 오류가 있어 적용을 취소했습니다: {e}")
        return 3

    if args.dry_run:
        print("=== DRY-RUN: 다음과 같이 변경됩니다 ===\n")
        start = patched.find("        if is_native:")
        print(patched[start:start + len(NEW)])
        print("(실제 파일은 변경되지 않았습니다. 적용하려면 --dry-run 없이 실행하세요.)")
        return 0

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_suffix(target.suffix + f".bak_{ts}")
    try:
        shutil.copy2(target, backup)
    except OSError as e:
        print(f"❌ 백업 생성 실패(대상이 읽기 전용일 수 있음): {e}")
        print("   실제 코드 디렉토리에 쓰기 권한이 있는 셸에서 다시 실행해 주세요.")
        return 4

    try:
        target.write_text(patched, encoding="utf-8")
    except OSError as e:
        print(f"❌ 파일 쓰기 실패: {e}")
        print(f"   백업은 생성되었습니다: {backup}")
        return 5

    # 적용 후 재검증
    verify = target.read_text(encoding="utf-8")
    if not _is_patched(verify) or verify.count(PATCH_MARKER) != 1:
        print("❌ 적용 후 검증에 실패했습니다. 백업으로 복구하세요:", backup)
        return 6

    print("✅ 패치 적용 완료!")
    print(f"   백업: {backup}")
    print("   → 봇을 재시작하면 QA(및 coder/auditor)의 `opencode-go/*` 모델이")
    print("     Hermes(OpenRouter) 대신 OpenCode CLI 어댑터로 정상 전송됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
