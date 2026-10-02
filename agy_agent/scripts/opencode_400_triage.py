#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opencode_400_triage.py — "OpenCode HTTP 400 이 왜 자주 뜨는가" 원인 진단 + 자동 처방
=====================================================================================

증상 (Discord 봇 / agy_agent 오케스트레이터):
    ⚠️ [⚡ OpenCode CLI] 실행 중 오류 발생 (종료 코드 -2)
        OpenCode Error: Provider request failed with HTTP 400
        ⛔ OpenCode 치명적 모델/라우터 오류(HTTP 400 계열) 감지 → 즉시 회수
        💡 인증 환경변수나 터미널 로그인 상태(...)를 점검해주세요.

실측된 진짜 원인 (이 스크립트가 자동으로 가려냅니다):
  A. [세션 컨텍스트 초과/손상]  ← 가장 흔함
     봇이 `--session <id>` 로 같은 세션에 거대한 QA/감사 리포트를 계속 누적한다.
     총 입력이 모델 컨텍스트 한도를 넘는 순간 provider 가 400 을 돌려주고,
     세션을 계속 재사용하는 한 **매 턴 100% 재발**한다.
     → 처방: 세션 초기화(새 세션) / 봇의 engine_conversations 비우기 (`!clear`)
  B. [OpenCode Zen 프라이버시 미동의]
     "This Go model trains on request data ... Allow paid endpoints that train on
      request data in your workspace's Privacy settings to use it."
     → 처방: opencode.ai 워크스페이스 Privacy settings 허용 또는 학습 미사용 모델로 교체
  C. [잘못된 모델 ID] "not a valid model ID"
     → 처방: `opencode models` 에 있는 ID만 사용 (이 스크립트가 검증)
  D. [백그라운드 서비스 정체] "Background service is unresponsive"
     → 처방: opencode 서비스/좀비 프로세스 정리

사용법:
    python3 opencode_400_triage.py                 # 진단 리포트 (읽기 전용)
    python3 opencode_400_triage.py --probe         # 설정된 모델을 실제 1줄 호출로 검증
    python3 opencode_400_triage.py --json out.json # 결과를 JSON 으로 저장
    python3 opencode_400_triage.py --report        # reports/opencode_400_diagnosis.md 생성

이 스크립트는 기본적으로 **아무것도 삭제/변경하지 않습니다**(읽기 전용 진단).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# agy_agent 는 심볼릭 링크일 수 있으므로 실제 디렉토리를 해석한다.
AGY_DIR = (HERE / "agy_agent")
if AGY_DIR.is_symlink():
    AGY_DIR = Path(os.path.realpath(AGY_DIR))
ENV_PATH = AGY_DIR / ".env"

OPENCODE_LOG = Path.home() / ".local" / "share" / "opencode" / "log" / "opencode.log"
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

# 봇이 실제로 쓰는 모델 환경변수 (엔진 포함)
MODEL_ENV_KEYS = [
    "CODER_MODEL", "QA_MODEL", "QA2_MODEL", "QA3_MODEL", "QA4_MODEL",
    "QA5_MODEL", "QA6_MODEL", "AUDITOR_MODEL", "COMMANDER_MODEL", "IMAGE_MODEL",
]

OPENAUTH_KEYS = ["OPENCODE_API_KEY", "OPENCODE_API_KEYS"]

# 400 로그에서 사람이 읽을 원인 요약
CAUSE_PATTERNS = [
    (re.compile(r"trains on request data", re.I),
     "B. OpenCode Zen 프라이버시: 요청 데이터 학습 엔드포인트 미허용"),
    (re.compile(r"Provider request failed with HTTP 400", re.I),
     "A. 누적 컨텍스트 초과/손상 등으로 인한 provider 400 (세션 재사용 시 재발)"),
    (re.compile(r"not a valid model id|invalid model|model_not_found", re.I),
     "C. 잘못된 모델 ID"),
    (re.compile(r"Background service is unresponsive", re.I),
     "D. opencode 백그라운드 서비스 정체"),
    (re.compile(r"Unable to connect|Transport", re.I),
     "E. 네트워크/전송 실패 (Upstream 연결 불가)"),
]


def _run(cmd, timeout=30, env=None):
    """CLI 실행 → (returncode, stdout+stderr 텍스트). 절대 예외를 던지지 않는다."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env or {**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"[not found] {' '.join(map(str, cmd))}"
    except subprocess.TimeoutExpired:
        return 124, f"[timeout {timeout}s] {' '.join(map(str, cmd))}"
    except Exception as exc:  # noqa: BLE001 - 진단 도구는 fail-open
        return 1, f"[error] {exc}"


def resolve_opencode() -> str | None:
    env = (os.getenv("OPENCODE_PATH") or "").strip()
    if env and os.access(os.path.expanduser(env), os.X_OK):
        return os.path.expanduser(env)
    which = shutil.which("opencode")
    if which:
        return which
    for cand in ("~/.opencode/bin/opencode", "~/.local/bin/opencode",
                 "/usr/local/bin/opencode", "/usr/bin/opencode"):
        p = os.path.expanduser(cand)
        if os.access(p, os.X_OK):
            return p
    return None


def read_env_models(path: Path) -> dict:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in MODEL_ENV_KEYS and v:
                out[k] = v
    except Exception:  # noqa: BLE001
        pass
    return out


def get_live_models(binary: str) -> list[str]:
    code, out = _run([binary, "models"], timeout=60)
    models = []
    for line in out.splitlines():
        mid = line.strip()
        if mid and "/" in mid and not mid.lower().startswith(("warning", "error", "timestamp")):
            models.append(mid)
    return models


def get_auth(binary: str) -> str:
    code, out = _run([binary, "auth", "list"], timeout=30)
    return out.strip()


def scan_log() -> dict:
    """opencode.log 에서 400/정체/전송 오류 패턴을 집계한다."""
    result = {
        "path": str(OPENCODE_LOG),
        "exists": OPENCODE_LOG.is_file(),
        "size_mb": round(OPENCODE_LOG.stat().st_size / 1e6, 1) if OPENCODE_LOG.is_file() else 0,
        "counts": {},
        "sessions_400": {},
        "hits": [],
    }
    if not OPENCODE_LOG.is_file():
        return result
    try:
        raw = OPENCODE_LOG.read_bytes().replace(b"\x00", b"")
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return result

    cause_hits = {}
    sessions_400 = {}
    hits = []
    for line in text.splitlines():
        for pat, label in CAUSE_PATTERNS:
            if pat.search(line):
                cause_hits[label] = cause_hits.get(label, 0) + 1
                if label.startswith("A."):
                    for sid in re.findall(r"ses_[A-Za-z0-9]+", line):
                        sessions_400[sid] = sessions_400.get(sid, 0) + 1
                if len(hits) < 25 and "spawning process" not in line:
                    ts = re.search(r"timestamp=(\S+)", line)
                    hits.append({
                        "ts": ts.group(1) if ts else "",
                        "cause": label,
                        "excerpt": line[:240],
                    })
    result["counts"] = cause_hits
    result["sessions_400"] = dict(sorted(sessions_400.items(), key=lambda x: -x[1]))
    result["hits"] = hits[-25:]
    return result


def probe_model(binary: str, model: str, timeout: int = 90) -> dict:
    """설정된 모델을 아주 작은 프롬프트로 실제 호출해 400 여부를 검증한다."""
    cmd = [binary, "run", "--model", model, "--format", "json", "--auto",
           "--standalone", "Reply with exactly: OK"]
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_SANDBOX")}
    env.update({"NO_COLOR": "1", "TERM": "dumb"})
    code, out = _run(cmd, timeout=timeout, env=env)
    if code == 124:
        verdict = "TIMEOUT"
    elif "trains on request data" in out:
        verdict = "PRIVACY_BLOCKED"
    elif "Provider request failed with HTTP 400" in out:
        verdict = "HTTP_400"
    elif "not a valid model" in out.lower():
        verdict = "INVALID_MODEL"
    elif '"type":"text"' in out:
        verdict = "OK"
    elif code == 0:
        verdict = "OK_UNKNOWN"
    else:
        verdict = f"ERROR({code})"
    return {"model": model, "verdict": verdict, "exit": code}


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenCode HTTP 400 원인 진단/처방")
    ap.add_argument("--probe", action="store_true", help="설정된 모델을 실제 1줄 호출로 검증(느림)")
    ap.add_argument("--json", metavar="PATH", default=None, help="결과 JSON 저장 경로")
    ap.add_argument("--report", action="store_true",
                    help="reports/opencode_400_diagnosis.md 생성")
    args = ap.parse_args()

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    binary = resolve_opencode()
    report: dict = {"generated_at": now, "opencode_binary": binary}

    print("=" * 74)
    print(" OpenCode HTTP 400 진단  |  " + now)
    print("=" * 74)

    if not binary:
        print("❌ opencode 바이너리를 찾지 못했습니다. OPENCODE_PATH 를 지정하세요.")
        return 1
    print(f"• opencode 바이너리 : {binary}")

    vcode, vout = _run([binary, "--version"], timeout=20)
    print(f"• 버전              : {vout.strip().splitlines()[0] if vout.strip() else '?'}")
    report["version"] = vout.strip()

    auth = get_auth(binary)
    report["auth"] = auth
    print("\n[1] 인증")
    print("  " + (auth.replace("\n", "\n  ") if auth else "(없음)"))

    models = get_live_models(binary)
    report["live_model_count"] = len(models)
    print(f"\n[2] 라이브 모델 카탈로그 : {len(models)}개")

    env_models = read_env_models(ENV_PATH)
    report["configured_models"] = env_models
    catalog = set(models)
    print(f"\n[3] 봇 설정 모델 검증 ({ENV_PATH})")
    invalid = []
    if not env_models:
        print("  (설정 모델을 찾지 못했습니다)")
    for key, model in env_models.items():
        if model.startswith("opencode-go/") or model.startswith("opencode/"):
            ok = (not catalog) or (model in catalog)
            print(f"  - {key:16s} {model:42s} {'✅ 카탈로그에 존재' if ok else '❌ 카탈로그에 없음'}")
            if not ok:
                invalid.append(model)
        else:
            print(f"  - {key:16s} {model:42s} (OpenCode 모델 아님 — AGY/기타 엔진)")
    report["invalid_models"] = invalid

    loginfo = scan_log()
    report["log"] = loginfo
    print(f"\n[4] opencode 로그 분석 ({loginfo['size_mb']} MB)")
    if not loginfo["exists"]:
        print("  로그 파일이 없습니다.")
    elif not loginfo["counts"]:
        print("  최근 로그에서 400/정체 패턴이 발견되지 않았습니다. 👍")
    else:
        for label, cnt in sorted(loginfo["counts"].items(), key=lambda x: -x[1]):
            print(f"  - {label}: {cnt}회")
        if loginfo["sessions_400"]:
            print("  ⚠️ 400 이 반복된 세션(재사용하면 재발):")
            for sid, cnt in list(loginfo["sessions_400"].items())[:10]:
                print(f"      {sid}  ×{cnt}")
    report["db_size_mb"] = round(OPENCODE_DB.stat().st_size / 1e6, 1) if OPENCODE_DB.is_file() else 0
    if report["db_size_mb"] > 200:
        print(f"  ⚠️ opencode.db 가 {report['db_size_mb']} MB 입니다(세션 누적). "
              f"`opencode session list` 확인 후 정리 권장.")

    if args.probe:
        print("\n[5] 설정 모델 실제 호출 검증 (--probe)")
        probes = []
        for key, model in env_models.items():
            if not (model.startswith("opencode-go/") or model.startswith("opencode/")):
                continue
            r = probe_model(binary, model)
            probes.append({**r, "env_key": key})
            icon = {"OK": "✅", "OK_UNKNOWN": "✅"}.get(r["verdict"], "❌")
            print(f"  {icon} {key:16s} {model:42s} → {r['verdict']}")
        report["probes"] = probes

    print("\n" + "=" * 74)
    print(" 처방 (우선순위)")
    print("=" * 74)
    if loginfo.get("sessions_400"):
        print(" 1) [가장 흔함] 400 이 반복된 세션을 비우세요.")
        print("    • 봇: `!clear` 로 세션 초기화 (engine_conversations 의 opencode:* 제거)")
        print("    • 또는 opencode 측 세션 삭제: `opencode session delete <ses_...>`")
        print("    • 근본 원인: 봇이 `--session` 으로 거대한 QA/감사 리포트를 계속 누적 → 컨텍스트 한도 초과")
    if loginfo["counts"] and any(k.startswith("B.") for k in loginfo["counts"]):
        print(" 2) [프라이버시] 학습 엔드포인트 미허용 모델이 있습니다.")
        print("    • opencode.ai → 워크스페이스 → Privacy settings → "
              "'Allow paid endpoints that train on request data' 허용")
        print("    • 또는 해당 모델을 학습 미사용 모델로 교체(예: opencode-go/deepseek-v4.1-flash)")
    if invalid:
        print(f" 3) [모델 ID] 카탈로그에 없는 모델: {', '.join(invalid)}  → 교체 필요")
    if loginfo["counts"] and any(k.startswith("D.") for k in loginfo["counts"]):
        print(" 4) [서비스 정체] `opencode service` / 고아 프로세스 정리 후 재시작")
    print("\n ℹ️ 이 진단은 읽기 전용입니다(삭제/변경 없음).")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 JSON 저장: {args.json}")

    if args.report:
        out = HERE / "reports" / "opencode_400_diagnosis.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# OpenCode HTTP 400 진단 리포트", "",
            f"- 생성 시각: {now}",
            f"- opencode: `{binary}` ({report.get('version','?').splitlines()[:1]})",
            f"- 라이브 모델 수: {report['live_model_count']}",
            "", "## 반복 400 세션", "",
        ]
        if loginfo.get("sessions_400"):
            for sid, cnt in loginfo["sessions_400"].items():
                lines.append(f"- `{sid}` ×{cnt}")
        else:
            lines.append("- (없음)")
        lines += ["", "## 원인별 발생 횟수", ""]
        if loginfo.get("counts"):
            for label, cnt in sorted(loginfo["counts"].items(), key=lambda x: -x[1]):
                lines.append(f"- {label}: {cnt}회")
        else:
            lines.append("- (없음)")
        lines += ["", "## 설정 모델", ""]
        for k, m in env_models.items():
            flag = "❌ 카탈로그 없음" if m in invalid else "✅"
            lines.append(f"- `{k}` = `{m}` {flag}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"📝 리포트 저장: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
