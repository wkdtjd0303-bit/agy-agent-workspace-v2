#!/usr/bin/env python3
"""
Discord Bot Interactive Control Center (딸깍딸깍 TUI)
Unified manager for AGY Vibe Coding Bot & Surface Status Bot.
"""

import os
import sys
import json
import subprocess
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state" / "channel_sessions.json"
AGY_SERVICE = "agy-discord-bot.service"
SURFACE_SERVICE = "surface-status.service"

# Known channel names for display
CHANNEL_NICKNAMES = {
    "1538529408703266902": "ANY QUESTION",
    "1538693952310485062": "ministry",
    "1538368240089169960": "status (Surface Pro 7)",
    "1538794871437918339": "noti (Surface Pro 7)",
    "1539483630089273435": "!05_agy_agent",
    "1539527192663433308": "!8_idus_phonecase",
    "1539628752730984469": "rider",
    "1539826011322060841": "parking_lot",
    "1539993680788586496": "qa-tester",
    "1540141654193999912": "trade-toss",
    "1540200001731567626": "daangn-cli",
    "1540262967319334944": "businesscard",
}

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BLUE = "\033[94m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

def clear_screen():
    sys.stdout.write("\033[H\033[2J\033[3J")
    sys.stdout.flush()

def get_service_status(service_name: str):
    res = subprocess.run(
        ["systemctl", "--user", "is-active", service_name],
        capture_output=True,
        text=True
    )
    is_active = res.stdout.strip() == "active"
    details = {}
    if is_active:
        res_info = subprocess.run(
            ["systemctl", "--user", "show", service_name, "--property=MainPID,ActiveEnterTimestamp,MemoryCurrent"],
            capture_output=True,
            text=True
        )
        for line in res_info.stdout.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                details[k] = v
    return is_active, details

def format_memory(mem_bytes_str):
    try:
        b = int(mem_bytes_str)
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.1f} MB"
    except Exception:
        return "N/A"

def load_channels():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_channels(data):
    os.makedirs(STATE_FILE.parent, exist_ok=True)
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, STATE_FILE)

# -------------------------------------------------------------
# Directory Navigator (딸깍딸깍 폴더 브라우저)
# -------------------------------------------------------------
def browse_directory(start_path: str) -> str | None:
    current_path = os.path.abspath(os.path.expanduser(start_path))
    if not os.path.exists(current_path):
        current_path = os.path.expanduser("~/Desktop/autocoding")

    while True:
        clear_screen()
        print(f"\n{BOLD}{CYAN}📂 [디렉토리 탐색기]{RESET}")
        print(f"현재 경로: {BOLD}{YELLOW}{current_path}{RESET}")
        print("=" * 60)

        try:
            all_items = sorted(os.listdir(current_path))
            dirs = [d for d in all_items if os.path.isdir(os.path.join(current_path, d))]
        except Exception as e:
            print(f"{RED}오류 발생: {e}{RESET}")
            dirs = []

        print(f" {CYAN}0.{RESET} [..] ⬆️ 상위 폴더로 이동")
        for i, d in enumerate(dirs, 1):
            print(f" {CYAN}{i:2}.{RESET} [DIR]  {d}/")

        print("=" * 60)
        print(f" {GREEN}{BOLD}s.{RESET} ✅ {BOLD}현재 폴더로 설정 완료{RESET}")
        print(f" {DIM}q. ↩️ 취소하고 돌아가기{RESET}")
        print("=" * 60)

        choice = input("번호를 선택하거나 's'를 눌러 설정: ").strip()

        if choice.lower() in ('s', 'select', 'set', ''):
            return current_path
        elif choice.lower() == 'q':
            return None
        elif choice == '0':
            current_path = os.path.abspath(os.path.join(current_path, '..'))
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(dirs):
                current_path = os.path.abspath(os.path.join(current_path, dirs[idx]))
            else:
                input("\n⚠️ 잘못된 번호입니다. 엔터를 누르세요...")
        else:
            input("\n⚠️ 잘못된 입력입니다. 엔터를 누르세요...")

# -------------------------------------------------------------
# Submenu: Channel Directory Setup
# -------------------------------------------------------------
def menu_set_channel_workspace():
    while True:
        clear_screen()
        channels = load_channels()
        ch_list = list(channels.keys())

        print(f"\n{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
        print(f"   📁 {BOLD}채널별 작업 디렉토리 설정{RESET}")
        print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}\n")

        for idx, ch_id in enumerate(ch_list, 1):
            data = channels[ch_id]
            nick = CHANNEL_NICKNAMES.get(ch_id, "채널")
            ws = data.get("workspace", "N/A")
            print(f" {BOLD}{idx}.{RESET} [{nick}] {CYAN}{ch_id}{RESET}\n    └─ 현재 경로: {YELLOW}{ws}{RESET}\n")

        print(f" {BOLD}A.{RESET} ➕ 새로운 채널 ID 직접 입력하여 추가")
        print(f" {BOLD}0.{RESET} ↩️ 이전 메뉴로 돌아가기")
        print("─" * 65)

        sel = input("설정할 채널 번호를 선택하세요: ").strip()

        if sel == '0' or sel.lower() == 'q':
            break

        selected_ch_id = None
        current_ws = "~/Desktop/autocoding"

        if sel.lower() == 'a':
            new_id = input("\n등록할 디스코드 채널 ID를 입력하세요: ").strip()
            if not new_id:
                continue
            selected_ch_id = new_id
        elif sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(ch_list):
                selected_ch_id = ch_list[idx]
                current_ws = channels[selected_ch_id].get("workspace", current_ws)
            else:
                input("\n⚠️ 유효하지 않은 번호입니다. 엔터를 누르세요...")
                continue
        else:
            input("\n⚠️ 유효하지 않은 입력입니다. 엔터를 누르세요...")
            continue

        if selected_ch_id:
            # Launch directory browser
            chosen_dir = browse_directory(current_ws)
            if chosen_dir:
                if selected_ch_id not in channels:
                    channels[selected_ch_id] = {"conversation_id": None}
                channels[selected_ch_id]["workspace"] = chosen_dir
                save_channels(channels)

                print(f"\n{GREEN}✓ 채널 [{selected_ch_id}]의 작업 디렉토리가 변경되었습니다!{RESET}")
                print(f"  ➔ {YELLOW}{chosen_dir}{RESET}")
                print(f"{CYAN}>> AGY 봇 서비스를 재시작하여 적용합니다...{RESET}")
                subprocess.run(["systemctl", "--user", "restart", AGY_SERVICE])
                input("\n적용 완료! 엔터를 누르면 돌아갑니다...")

# -------------------------------------------------------------
# Submenu: Detailed Status
# -------------------------------------------------------------
def menu_show_status():
    clear_screen()
    print(f"\n{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
    print(f"   📊 {BOLD}디스코드 봇 상세 상태 대시보드{RESET}")
    print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}\n")

    # AGY Bot
    agy_active, agy_info = get_service_status(AGY_SERVICE)
    agy_badge = f"{GREEN}● RUNNING{RESET}" if agy_active else f"{RED}○ STOPPED{RESET}"
    print(f"{BOLD}1. AGY Vibe Coding Bot ({AGY_SERVICE}):{RESET} {agy_badge}")
    if agy_active:
        print(f"   • PID: {agy_info.get('MainPID', 'N/A')}")
        print(f"   • 메모리 사용: {format_memory(agy_info.get('MemoryCurrent', '0'))}")
        print(f"   • 시작 시점: {agy_info.get('ActiveEnterTimestamp', 'N/A')}")
    print()

    # Surface Status Bot
    surf_active, surf_info = get_service_status(SURFACE_SERVICE)
    surf_badge = f"{GREEN}● RUNNING{RESET}" if surf_active else f"{RED}○ STOPPED{RESET}"
    print(f"{BOLD}2. Surface Status Bot ({SURFACE_SERVICE}):{RESET} {surf_badge}")
    if surf_active:
        print(f"   • PID: {surf_info.get('MainPID', 'N/A')}")
        print(f"   • 메모리 사용: {format_memory(surf_info.get('MemoryCurrent', '0'))}")
        print(f"   • 시작 시점: {surf_info.get('ActiveEnterTimestamp', 'N/A')}")
    print()

    # Channels
    print(f"{BOLD}3. 채널별 매핑 및 세션 상태:{RESET}")
    channels = load_channels()
    for ch_id, data in channels.items():
        nick = CHANNEL_NICKNAMES.get(ch_id, "채널")
        ws = data.get("workspace", "N/A")
        conv = data.get("conversation_id")
        mode = data.get("mode", "auto")
        if mode == "auto":
            mode_label = "🤖 Auto"
        elif mode == "pair":
            mode_label = "👥 3-Agent Trio"
        else:
            mode_label = "👤 Single"
        conv_str = f"{conv[:8]}..." if conv else "대기 중 (신규)"
        print(f"   • [{nick}] {CYAN}{ch_id}{RESET} [{mode_label}]")
        print(f"     ├─ 경로: {YELLOW}{ws}{RESET}")
        print(f"     └─ 세션: {conv_str}")
    print()
    input("엔터를 누르면 메인 메뉴로 돌아갑니다...")

# -------------------------------------------------------------
# Submenu: Service Control (Start / Stop / Restart)
# -------------------------------------------------------------
def menu_service_control(action_name: str):
    clear_screen()
    print(f"\n{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
    print(f"   ⚡ {BOLD}봇 서비스 {action_name.upper()}{RESET}")
    print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}\n")

    print(f" {BOLD}1.{RESET} 🚀 전체 서비스 (AGY 봇 + Surface 상태 봇)")
    print(f" {BOLD}2.{RESET} 🤖 AGY 바이브 코딩 봇만 ({AGY_SERVICE})")
    print(f" {BOLD}3.{RESET} 🖥️ Surface 상태 봇만 ({SURFACE_SERVICE})")
    print(f" {BOLD}0.{RESET} ↩️ 취소")
    print("─" * 65)

    sel = input("선택: ").strip()
    if sel == '1':
        subprocess.run(["systemctl", "--user", action_name, AGY_SERVICE, SURFACE_SERVICE])
        print(f"\n{GREEN}✓ 전체 서비스 {action_name} 완료!{RESET}")
    elif sel == '2':
        subprocess.run(["systemctl", "--user", action_name, AGY_SERVICE])
        print(f"\n{GREEN}✓ AGY 봇 {action_name} 완료!{RESET}")
    elif sel == '3':
        subprocess.run(["systemctl", "--user", action_name, SURFACE_SERVICE])
        print(f"\n{GREEN}✓ Surface 상태 봇 {action_name} 완료!{RESET}")
    elif sel == '0':
        return
    input("\n엔터를 누르면 돌아갑니다...")

# -------------------------------------------------------------
# Submenu: Logs
# -------------------------------------------------------------
def menu_show_logs():
    clear_screen()
    print(f"\n{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
    print(f"   📜 {BOLD}실시간 로그 스트리밍{RESET}")
    print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}\n")

    print(f" {BOLD}1.{RESET} 🤖 AGY 바이브 코딩 봇 로그")
    print(f" {BOLD}2.{RESET} 🖥️ Surface 상태 봇 로그")
    print(f" {BOLD}0.{RESET} ↩️ 취소")
    print("─" * 65)

    sel = input("확인할 로그 번호: ").strip()
    target_srv = None
    if sel == '1':
        target_srv = AGY_SERVICE
    elif sel == '2':
        target_srv = SURFACE_SERVICE
    elif sel == '0':
        return

    if target_srv:
        print(f"\n{CYAN}>> {target_srv} 실시간 로그를 출력합니다. (종료: Ctrl+C){RESET}\n")
        try:
            subprocess.run(["journalctl", "--user", "-u", target_srv, "-f"])
        except KeyboardInterrupt:
            pass
        input("\n로그 출력을 종료했습니다. 엔터를 누르면 돌아갑니다...")

# -------------------------------------------------------------
# Submenu: Reset Session
# -------------------------------------------------------------
def menu_reset_session():
    clear_screen()
    channels = load_channels()
    ch_list = list(channels.keys())

    print(f"\n{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
    print(f"   🧹 {BOLD}채널 대화 세션 초기화 (새 대화 시작){RESET}")
    print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}\n")

    for idx, ch_id in enumerate(ch_list, 1):
        nick = CHANNEL_NICKNAMES.get(ch_id, "채널")
        conv = channels[ch_id].get("conversation_id")
        conv_str = f"세션: {conv[:8]}..." if conv else "세션 없음"
        print(f" {BOLD}{idx}.{RESET} [{nick}] {CYAN}{ch_id}{RESET} ({conv_str})")

    print(f" {BOLD}A.{RESET} 🔄 모든 채널 세션 일괄 초기화")
    print(f" {BOLD}0.{RESET} ↩️ 취소")
    print("─" * 65)

    sel = input("초기화할 채널 번호 선택: ").strip()
    if sel == '0':
        return
    elif sel.lower() == 'a':
        for ch_id in channels:
            channels[ch_id]["conversation_id"] = None
        save_channels(channels)
        print(f"\n{GREEN}✓ 모든 채널의 대화 세션이 초기화되었습니다.{RESET}")
    elif sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(ch_list):
            target = ch_list[idx]
            channels[target]["conversation_id"] = None
            save_channels(channels)
            print(f"\n{GREEN}✓ 채널 [{target}]의 대화 세션이 초기화되었습니다.{RESET}")
        else:
            print(f"\n{RED}잘못된 번호입니다.{RESET}")
    input("\n엔터를 누르면 돌아갑니다...")

# -------------------------------------------------------------
# Main Interactive Menu Loop
# -------------------------------------------------------------
def main_interactive():
    while True:
        clear_screen()
        agy_active, _ = get_service_status(AGY_SERVICE)
        surf_active, _ = get_service_status(SURFACE_SERVICE)

        agy_status_str = f"{GREEN}● RUNNING{RESET}" if agy_active else f"{RED}○ STOPPED{RESET}"
        surf_status_str = f"{GREEN}● RUNNING{RESET}" if surf_active else f"{RED}○ STOPPED{RESET}"

        print(f"\n{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
        print(f"   🤖 {BOLD}Discord Bot Interactive Control Center (`discord`){RESET}")
        print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}")
        print(f"  • AGY 바이브 코딩 봇 : {agy_status_str}")
        print(f"  • Surface 상태 알림 봇: {surf_status_str}")
        print(f"{BOLD}{CYAN}═════════════════════════════════════════════════════════════════{RESET}\n")

        print(f" {BOLD}{CYAN}1.{RESET} 📁 {BOLD}채널별 작업 디렉토리 변경{RESET} (폴더 탐색기 딸깍딸깍 🖱️)")
        print(f" {BOLD}{CYAN}2.{RESET} 📊 {BOLD}봇 및 채널 상세 상태 확인{RESET}")
        print(f" {BOLD}{CYAN}3.{RESET} 🔄 {BOLD}봇 서비스 재시작{RESET} (Restart)")
        print(f" {BOLD}{CYAN}4.{RESET} 📜 {BOLD}실시간 로그 스트리밍 확인{RESET} (Live Logs)")
        print(f" {BOLD}{CYAN}5.{RESET} 🧹 {BOLD}채널 대화 세션 초기화{RESET} (Reset Context)")
        print(f" {BOLD}{CYAN}6.{RESET} ⏹️ {BOLD}봇 서비스 중지 / 시작{RESET} (Stop / Start)")
        print(f" {BOLD}{CYAN}0.{RESET} 🚪 {BOLD}종료{RESET} (Exit)")
        print("\n" + "─" * 65)

        choice = input(f"{BOLD}선택할 번호를 입력하세요: {RESET}").strip()

        if choice == '1':
            menu_set_channel_workspace()
        elif choice == '2':
            menu_show_status()
        elif choice == '3':
            menu_service_control("restart")
        elif choice == '4':
            menu_show_logs()
        elif choice == '5':
            menu_reset_session()
        elif choice == '6':
            action = input("\n[1] 중지(Stop)  [2] 시작(Start)  [0] 취소: ").strip()
            if action == '1':
                menu_service_control("stop")
            elif action == '2':
                menu_service_control("start")
        elif choice == '0' or choice.lower() in ('q', 'exit'):
            clear_screen()
            print(f"\n{GREEN}Discord Bot Control Center를 종료합니다.{RESET}\n")
            break
        else:
            input("\n⚠️ 잘못된 입력입니다. 엔터를 누르세요...")

if __name__ == "__main__":
    main_interactive()
