#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_screenshot_roundcycle.py

!mode 3/3+ 스크린샷 수명주기 실증 시나리오 (실제 Android 에뮬레이터 사용).

왜 필요한가
------------------------------------------------
REJECTED → Coder 재작업 → 빌드 → 재설치 → 앱 실행 → 캡처 → 공유 사이클에서
"이전 라운드 스크린샷을 이번 라운드 증거로 재사용"하는 문제가 반복되어 왔습니다.
이 스크립트는 그 문제를 **실제 screencap으로** 재현·검증합니다.

검증 항목
------------------------------------------------
  Round 1  앱 실행 후 캡처·공유            → 이번 라운드 캡처 1장만 공유
  Round 2  재작업 후 캡처 실패(증거 없음) → 공유 0장 + "이전 스크린샷 N장" 신선도 경고
  Round 3  재설치·재실행 후 새 캡처        → 새 캡처만 공유, Round 1 캡처 재사용 금지

사용법
------------------------------------------------
  1) 에뮬레이터를 먼저 콜드부트로 기동한다 (스냅샷 잔재로 부팅이 막히는 것을 방지):

     ~/Android/Sdk/emulator/emulator -avd TestAVD \\
         -no-window -no-audio -no-boot-anim -no-snapshot \\
         -gpu swiftshader_indirect -no-metrics &

  2) 이 스크립트를 실행한다:

     python agy_agent/scripts/verify_screenshot_roundcycle.py

  종료 코드 0 = 3개 항목 전부 PASS.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time

AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_ROOT not in sys.path:
    sys.path.insert(0, AGENT_ROOT)

import discord_utils  # noqa: E402
from discord_screenshot_gallery import (  # noqa: E402
    collect_candidate_screenshots,
    count_stale_screenshots,
    send_screenshot_gallery,
)

ADB = os.path.join(
    os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    or os.path.expanduser("~/Android/Sdk"),
    "platform-tools", "adb",
)
AVD = os.environ.get("AGY_VERIFY_AVD", "TestAVD")

SENT: set = set()
SEND_LOG: list = []


async def _fake_send(channel, content: str = "", **kw):
    """실제 디스코드는 호출하지 않고 첨부 파일명만 기록한다."""
    files = kw.get("files") or []
    SEND_LOG.append((content.splitlines()[0] if content else "", [f.filename for f in files]))
    return None


def sh(*args: str, timeout: int = 40) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ADB, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
    )


def capture(dest_dir: str, name: str):
    os.makedirs(dest_dir, exist_ok=True)
    p = os.path.join(dest_dir, name)
    r = subprocess.run([ADB, "exec-out", "screencap", "-p"], stdout=subprocess.PIPE, timeout=40)
    with open(p, "wb") as f:
        f.write(r.stdout)
    return p, os.path.getsize(p)


def wait_boot(max_s: int = 180) -> bool:
    for _ in range(max_s // 5):
        r = sh("shell", "getprop", "sys.boot_completed")
        if r.stdout.decode(errors="ignore").strip() == "1":
            return True
        time.sleep(5)
    return False


async def run_round(ws: str, shotdir: str, idx: int, do_capture: bool,
                    pre_capture_cmd=None, work_seconds: float = 2.5):
    """trio_agent_runner.run_pipeline 의 라운드 흐름을 그대로 모사한다.

    trio: for round_num ... _round_start_ts = time.time()
    → Coder 작업(빌드/재설치/앱 실행) → 캡처 → 게이트/갤러리 평가
    """
    discord_utils.safe_send_discord_message = _fake_send
    before = len(SEND_LOG)
    round_start = time.time()          # trio: _round_start_ts

    time.sleep(work_seconds)           # Coder 작업 (라운드 시작 이후에 캡처가 생성되어야 한다)

    if do_capture:
        if pre_capture_cmd:
            sh("shell", *pre_capture_cmd)
            time.sleep(2)
        path, size = capture(shotdir, f"round{idx}.png")
        print(f"    [캡처] {os.path.basename(path)}  {size:,} bytes")
    else:
        print("    [캡처] 수행하지 않음 (재작업 후 캡처 실패 상황)")

    cands = collect_candidate_screenshots(ws, min_mtime=round_start)
    n = await send_screenshot_gallery(
        None, cands, already_sent=SENT, max_files=4,
        title=f"🎨 [라운드 {idx}] 시각 증거 스크린샷", min_mtime=round_start,
    )
    stale = count_stale_screenshots(ws, round_start)
    sent_now = SEND_LOG[before:] if len(SEND_LOG) > before else []
    sent_files = sent_now[0][1] if sent_now else []
    return n, stale, [os.path.basename(c) for c in cands], sent_files


async def main() -> int:
    print("=" * 78)
    print("!mode 3 스크린샷 수명주기 실증 — 이전 라운드 재사용 여부 검증")
    print("=" * 78)
    print(f"adb : {ADB}")
    print(f"avd : {AVD}")

    if not os.path.isfile(ADB):
        print("❌ adb 없음 — Android SDK 경로 확인 필요")
        return 2
    if not wait_boot():
        print("❌ 에뮬레이터 기동/부팅 실패 — 먼저 에뮬레이터를 -no-snapshot 으로 띄우십시오.")
        return 2
    print("✅ 에뮬레이터 부팅 완료\n")

    ws = tempfile.mkdtemp(prefix="roundcycle_ws_")
    shotdir = os.path.join(ws, "reports", "gate5", "screenshots")

    # Round 1 — 앱 실행 후 캡처·공유
    sh("shell", "input", "keyevent", "KEYCODE_HOME")
    time.sleep(2)
    n1, s1, c1, f1 = await run_round(ws, shotdir, 1, do_capture=True)
    print(f"  → 공유 {n1}장 | 이번 라운드 후보 {c1} | 전송 파일 {f1} | 잔여 이전 스크린샷 {s1}장")
    ok1 = (n1 == 1 and f1 == ["round1.png"])

    # 라운드 회전: 실제 환경에서는 QA 검증 + 사령 중재 + 재작업 지시가 사이에 수행된다
    print("\n  [라운드 회전] QA 검증 → REJECTED → Coder 재작업 지시 … (에뮬레이션 4초)")
    await asyncio.sleep(4)

    # Round 2 — 캡처 실패
    n2, s2, c2, f2 = await run_round(ws, shotdir, 2, do_capture=False)
    print(f"  → 공유 {n2}장 | 이번 라운드 후보 {c2} | 전송 파일 {f2} | 잔여 이전 스크린샷 {s2}장")
    ok2 = (n2 == 0 and s2 >= 1 and c2 == [])

    print("\n  [라운드 회전] 수정 반영 → 빌드 → 재설치 → 앱 실행 … (에뮬레이션 4초)")
    await asyncio.sleep(4)

    # Round 3 — 재설치·재실행 후 새 캡처
    n3, s3, c3, f3 = await run_round(
        ws, shotdir, 3, do_capture=True,
        pre_capture_cmd=("am", "start", "-a", "android.settings.SETTINGS"),
    )
    print(f"  → 공유 {n3}장 | 이번 라운드 후보 {c3} | 전송 파일 {f3} | 잔여 이전 스크린샷 {s3}장")
    ok3 = (n3 == 1 and f3 == ["round3.png"] and "round1.png" not in f3)

    print("\n" + "=" * 78)
    print(f"[{'PASS' if ok1 else 'FAIL'}] Round 1 — 신규 캡처 공유")
    print(f"[{'PASS' if ok2 else 'FAIL'}] Round 2 — 캡처 없을 때 이전 스크린샷 재사용 금지(공유 0 + 경고)")
    print(f"[{'PASS' if ok3 else 'FAIL'}] Round 3 — 재설치 후 새 캡처만 공유(round1 재사용 없음)")
    print("=" * 78)
    print(f"누적 전송 기록: {[(t, fs) for t, fs in SEND_LOG]}")
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
