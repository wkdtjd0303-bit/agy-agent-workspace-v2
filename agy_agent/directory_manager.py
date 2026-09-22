#!/usr/bin/env python3
"""
📁 directory_manager.py — Discord Interactive Directory & Trash Manager
Provides:
1. Interactive Discord UI (Buttons & Modals) for one-touch directory creation & deletion.
2. Global device path support across the entire Linux filesystem.
3. Trash-only deletion enforcement (zero permanent rm -rf, sending to OS Trash).
4. Automated Discord channel creation & instant session workspace binding.
"""

import os
import re
import sys
import time
import shutil
import logging
import discord
from typing import Optional, List, Tuple

from safe_trash import (
    resolve_device_path,
    is_protected_path,
    send_to_trash,
    create_device_folder,
    calculate_path_size,
    format_bytes,
    DEFAULT_AUTOCODING_BASE,
)
import config

logger = logging.getLogger("DirectoryManager")


def sanitize_channel_name(name: str) -> str:
    """Sanitizes a folder name into a valid Discord text channel name."""
    s = name.strip().lower()
    # Replace spaces and underscores with hyphens
    s = re.sub(r"[\s_]+", "-", s)
    # Remove characters not allowed in discord channel names (strip special chars like !)
    s = re.sub(r"[^a-z0-9가-힣-]", "", s)
    # Deduplicate hyphens
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = f"project-{int(time.time()) % 10000}"
    return s[:95]


def build_mkdir_control_embed(channel_id: int, current_workspace: str) -> discord.Embed:
    """Generates the interactive control panel embed for !mkdir."""
    p = config.COMMAND_PREFIX
    abs_ws = os.path.abspath(current_workspace)
    ws_exists = os.path.isdir(abs_ws)

    # Disk usage
    disk_info = "계측 불가"
    try:
        total, used, free = shutil.disk_usage(abs_ws if ws_exists else "/")
        disk_info = f"여유 {free / (1024 ** 3):.1f} GB / 전체 {total / (1024 ** 3):.1f} GB"
    except Exception:
        pass

    embed = discord.Embed(
        title="📁 [원터치 디렉토리 & 휴지통 안전 관리 제어판]",
        description=(
            "디바이스 전역의 폴더를 손쉽게 생성하고, 디스코드 채널을 즉시 연동하거나\n"
            "영구 삭제 위험 없이 **OS 휴지통으로 안전하게 삭제**할 수 있는 대화형 관리 시스템입니다.\n\n"
            f"📍 **현재 채널 작업 디렉토리 (CWD):**\n`{abs_ws}` "
            f"({'🟢 존재함' if ws_exists else '🔴 미존재'})\n"
            f"💾 **디스크 상태**: `{disk_info}`"
        ),
        color=discord.Color.blue()
    )

    embed.add_field(
        name="✨ 원터치 기능 버튼 가이드",
        value=(
            "• 📁 **[새 폴더 생성]** : 디바이스 전역 어디든 폴더 생성 및 현재/신규 채널 즉시 바인딩\n"
            "• 🗑️ **[휴지통 안전 삭제]** : 영구 삭제(`rm -rf`) 없이 OS 휴지통(`~/.local/share/Trash`)으로 이동\n"
            "• 📂 **[폴더 탐색 / 파일 목록]** : 현재 CWD 내부 파일 및 하위 폴더 현황 즉시 조회\n"
            "• 🔄 **[작업 CWD 변경]** : 이 채널의 작업 대상을 다른 폴더로 신속 전환\n"
            "• ➕ **[새 채널 & 폴더 생성]** : 폴더 생성 + 새 디스코드 채널 개설 + 작업 바인딩을 원샷 실행"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ 안전 권한 제어 (Trash-Only Mandate)",
        value=(
            "• 에이전트 및 명령어를 통한 삭제는 완전 영구 삭제가 원천 차단되며, **오직 휴지통 이동만 허용**됩니다.\n"
            "• 시스템 핵심 경로(`/`, `/home`, `/etc`, `!05_agy_agent` 등)는 자동 보호됩니다.\n"
            f"• CLI 빠른 호출: `{p}mkdir <경로>` (직접 생성 및 현재 채널 즉시 연결)"
        ),
        inline=False
    )

    embed.set_footer(text="AGY Directory & Safety System · 버튼을 누르면 입력창(Modal)이 나타납니다.")
    return embed


def build_directory_status_embed(channel_id: int, workspace: str) -> discord.Embed:
    """Generates detailed file/folder listing embed for the current directory."""
    abs_ws = os.path.abspath(workspace)
    if not os.path.exists(abs_ws):
        return discord.Embed(
            title="⚠️ 디렉토리 미존재",
            description=f"경로를 찾을 수 없습니다:\n`{abs_ws}`",
            color=discord.Color.red()
        )

    subdirs = []
    files = []
    try:
        entries = sorted(os.listdir(abs_ws))
        for e in entries:
            if e.startswith(".") and e not in [".env", ".gitignore"]:
                continue
            fp = os.path.join(abs_ws, e)
            if os.path.isdir(fp):
                subdirs.append(f"📁 {e}/")
            else:
                sz = os.path.getsize(fp)
                files.append(f"📄 {e} ({format_bytes(sz)})")
    except Exception as e:
        return discord.Embed(
            title="⚠️ 디렉토리 읽기 실패",
            description=f"내용을 읽는 중 오류가 발생했습니다: {str(e)}",
            color=discord.Color.red()
        )

    total_bytes, file_count = calculate_path_size(abs_ws)

    embed = discord.Embed(
        title=f"📂 디렉토리 현황: {os.path.basename(abs_ws) or '/'}",
        description=f"📍 **절대 경로**: `{abs_ws}`\n📦 **총 용량**: `{format_bytes(total_bytes)}` (전체 파일 {file_count}개)",
        color=discord.Color.green()
    )

    if subdirs:
        sub_sample = subdirs[:15]
        extra = f" (외 {len(subdirs) - 15}개)" if len(subdirs) > 15 else ""
        embed.add_field(name=f"📁 하위 폴더 ({len(subdirs)}개)", value="`" + "`, `".join(sub_sample) + "`" + extra, inline=False)
    else:
        embed.add_field(name="📁 하위 폴더", value="*하위 폴더 없음*", inline=False)

    if files:
        f_sample = files[:15]
        extra_f = f" (외 {len(files) - 15}개)" if len(files) > 15 else ""
        embed.add_field(name=f"📄 파일 ({len(files)}개)", value="`" + "`, `".join(f_sample) + "`" + extra_f, inline=False)
    else:
        embed.add_field(name="📄 파일", value="*파일 없음 (빈 폴더)*", inline=False)

    return embed


# ==============================================================================
# Modals
# ==============================================================================

class CreateFolderModal(discord.ui.Modal, title="📁 새 폴더 생성 및 디스코드 연동"):
    folder_path_input = discord.ui.TextInput(
        label="📁 생성할 폴더 경로 또는 이름",
        placeholder="예: !11_my_app 또는 /home/jangsung/Desktop/autocoding/!11_my_app",
        max_length=300,
        required=True
    )
    link_mode_input = discord.ui.TextInput(
        label="🔗 디스코드 채널 연동 옵션 (1 / 2 / 3)",
        placeholder="1: 현재 채널 CWD 즉시 연결 | 2: 새 채널 자동 생성 | 3: 폴더만 생성",
        default="1",
        max_length=5,
        required=False
    )
    initial_files_input = discord.ui.TextInput(
        label="📝 초기 생성 파일 (선택, 쉼표 구분)",
        placeholder="예: README.md, main.py, app.js (비워둘 수 있음)",
        required=False
    )

    def __init__(self, channel_id: int, base_dir: Optional[str] = None):
        super().__init__()
        self.channel_id = channel_id
        self.base_dir = base_dir

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)

        raw_path = self.folder_path_input.value.strip()
        link_mode = (self.link_mode_input.value or "1").strip()
        raw_files = self.initial_files_input.value.strip()
        init_files = [f.strip() for f in raw_files.split(",") if f.strip()] if raw_files else None

        success, abs_path, msg = create_device_folder(raw_path, self.base_dir, initial_files=init_files)
        if not success:
            await interaction.followup.send(f"⚠️ {msg}", ephemeral=True)
            return

        from session_manager import SessionManager
        sm = SessionManager()

        embed = discord.Embed(
            title="🎉 [새 폴더 생성 완료]",
            description=msg,
            color=discord.Color.green()
        )

        # Option 1: Link to current channel
        if link_mode == "1":
            session = sm.get_session(self.channel_id)
            session.set_workspace(abs_path)
            embed.add_field(
                name="🔗 현재 채널 연동 완료",
                value=f"이 채널(**<#{self.channel_id}>**)의 작업 디렉토리가 즉시 배정되었습니다.\n이제 바로 메시지를 작성하여 에이전트와 코딩을 진행할 수 있습니다!",
                inline=False
            )
            await interaction.followup.send(embed=embed)

        # Option 2: Create new Discord channel & link
        elif link_mode == "2":
            guild = interaction.guild
            if guild is not None:
                try:
                    category = getattr(interaction.channel, "category", None)
                    ch_name = sanitize_channel_name(os.path.basename(abs_path))
                    new_ch = await guild.create_text_channel(
                        name=ch_name,
                        category=category,
                        topic=f"📁 작업 디렉토리: {abs_path}"
                    )
                    new_session = sm.get_session(new_ch.id)
                    new_session.set_workspace(abs_path)

                    # Send welcome embed in the newly created channel
                    welcome_embed = discord.Embed(
                        title=f"🚀 [새 프로젝트 채널 연동 완료] #{ch_name}",
                        description=(
                            f"새로운 작업 공간이 성공적으로 개설되었습니다!\n\n"
                            f"📁 **작업 디렉토리**: `{abs_path}`\n"
                            f"👤 **개설자**: {interaction.user.mention}\n\n"
                            f"💡 **지금 바로 코딩 시작하기**:\n"
                            f"이 채널에 원하는 개발 요청이나 기획을 편하게 메시지로 입력하시면 Coder ↔ QA ↔ Auditor 에이전트가 즉시 작업을 시작합니다!"
                        ),
                        color=discord.Color.gold()
                    )
                    await new_ch.send(embed=welcome_embed)

                    embed.add_field(
                        name="🌐 새 디스코드 채널 개설 완료",
                        value=f"새 채널 **<#{new_ch.id}>** 이(가) 생성되었으며, 해당 채널의 작업 디렉토리가 바인딩되었습니다.\n👉 <#{new_ch.id}> 로 이동하여 바로 코딩을 시작하세요!",
                        inline=False
                    )
                    await interaction.followup.send(embed=embed)
                except Exception as ce:
                    session = sm.get_session(self.channel_id)
                    session.set_workspace(abs_path)
                    embed.add_field(
                        name="⚠️ 새 채널 생성 권한 부족 (현재 채널로 대체 연결)",
                        value=f"디스코드 채널 생성 중 권한 오류({ce})가 발생하여, 대신 현재 채널에 연결하였습니다.",
                        inline=False
                    )
                    await interaction.followup.send(embed=embed)
            else:
                session = sm.get_session(self.channel_id)
                session.set_workspace(abs_path)
                embed.add_field(
                    name="🔗 현재 채널 연동 완료",
                    value=f"DM 환경이므로 현재 채널의 작업 디렉토리로 바인딩되었습니다.",
                    inline=False
                )
                await interaction.followup.send(embed=embed)

        # Option 3: Directory creation only
        else:
            embed.add_field(
                name="ℹ️ 폴더 단독 생성",
                value="채널 연동 없이 디스크에 폴더만 안전하게 준비되었습니다.",
                inline=False
            )
            await interaction.followup.send(embed=embed)


class TrashDeleteModal(discord.ui.Modal, title="🗑️ 휴지통 안전 삭제 (영구삭제 원천 차단)"):
    target_path_input = discord.ui.TextInput(
        label="🗑️ 삭제할 폴더 또는 파일 경로",
        placeholder="예: !11_my_app 또는 /home/jangsung/Desktop/autocoding/...",
        max_length=300,
        required=True
    )
    confirm_input = discord.ui.TextInput(
        label="⚠️ 확인 문구 ('삭제' 또는 '휴지통' 입력)",
        placeholder="삭제",
        max_length=20,
        required=True
    )

    def __init__(self, channel_id: int, base_dir: Optional[str] = None):
        super().__init__()
        self.channel_id = channel_id
        self.base_dir = base_dir

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)

        confirm_val = self.confirm_input.value.strip()
        if confirm_val not in ["삭제", "휴지통", "delete", "trash", "yes", "확인"]:
            await interaction.followup.send(
                "⚠️ 삭제가 취소되었습니다. 안전 확인 문구('삭제' 또는 '휴지통')를 정확히 입력해야 합니다.",
                ephemeral=True
            )
            return

        raw_path = self.target_path_input.value.strip()
        resolved_path = resolve_device_path(raw_path, self.base_dir)

        success, msg, meta = send_to_trash(resolved_path, channel_id=self.channel_id)

        if success:
            embed = discord.Embed(
                title="🗑️ [OS 휴지통 안전 이동 완료]",
                description=msg,
                color=discord.Color.orange()
            )
            embed.set_footer(text="AGY SafeTrash Guard · 영구 삭제가 아니므로 안심하고 작업하세요.")
            await interaction.followup.send(embed=embed)
        else:
            embed = discord.Embed(
                title="⛔ [삭제 요청 반려]",
                description=msg,
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


class SwitchCwdModal(discord.ui.Modal, title="🔄 작업 디렉토리(CWD) 변경"):
    target_path_input = discord.ui.TextInput(
        label="🔄 이동할 작업 디렉토리 경로 (디바이스 전역)",
        placeholder="예: /home/jangsung/Desktop/autocoding/!01_vibecoding",
        max_length=300,
        required=True
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)

        raw_path = self.target_path_input.value.strip()
        abs_path = resolve_device_path(raw_path)

        try:
            os.makedirs(abs_path, exist_ok=True)
            from session_manager import SessionManager
            sm = SessionManager()
            session = sm.get_session(self.channel_id)
            session.set_workspace(abs_path)

            embed = discord.Embed(
                title="📁 [작업 디렉토리 변경 완료]",
                description=(
                    f"현재 채널(**<#{self.channel_id}>**)의 작업 디렉토리가 성공적으로 변경되었습니다.\n\n"
                    f"📍 **새 작업 경로**: `{session.workspace}`\n"
                    f"💡 이제 이 채널에 전송하는 모든 메시지는 위 디렉토리를 대상으로 동작합니다."
                ),
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"⚠️ 디렉토리 변경 실패: {str(e)}", ephemeral=True)


class CreateChannelAndFolderModal(discord.ui.Modal, title="➕ 새 채널 & 폴더 원클릭 동시 생성"):
    project_name_input = discord.ui.TextInput(
        label="✨ 새 프로젝트 / 폴더 이름",
        placeholder="예: !11_crypto_portfolio 또는 my_landing_page",
        max_length=100,
        required=True
    )
    base_dir_input = discord.ui.TextInput(
        label="📂 상위 기준 경로 (기본: autocoding 허브)",
        placeholder="/home/jangsung/Desktop/autocoding",
        default=DEFAULT_AUTOCODING_BASE,
        max_length=250,
        required=False
    )
    initial_desc_input = discord.ui.TextInput(
        label="💡 프로젝트 개요 또는 초기 메모 (선택)",
        placeholder="예: 차트 대시보드 및 알림 봇 개발",
        style=discord.TextStyle.paragraph,
        required=False
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)

        p_name = self.project_name_input.value.strip()
        base_dir = self.base_dir_input.value.strip() or DEFAULT_AUTOCODING_BASE
        desc = self.initial_desc_input.value.strip()

        success, abs_path, msg = create_device_folder(
            p_name,
            base_dir,
            initial_files=["README.md"]
        )
        if not success:
            await interaction.followup.send(f"⚠️ {msg}", ephemeral=True)
            return

        # If description was provided, write to README.md
        if desc:
            try:
                readme_path = os.path.join(abs_path, "README.md")
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(f"# {os.path.basename(abs_path)}\n\n{desc}\n")
            except Exception:
                pass

        guild = interaction.guild
        from session_manager import SessionManager
        sm = SessionManager()

        if guild is not None:
            try:
                category = getattr(interaction.channel, "category", None)
                ch_name = sanitize_channel_name(p_name)
                new_ch = await guild.create_text_channel(
                    name=ch_name,
                    category=category,
                    topic=f"📁 작업 디렉토리: {abs_path}"
                )
                new_session = sm.get_session(new_ch.id)
                new_session.set_workspace(abs_path)

                welcome_embed = discord.Embed(
                    title=f"🚀 [새 프로젝트 개설 완료] #{ch_name}",
                    description=(
                        f"새로운 개발 프로젝트 공간이 즉시 개설되었습니다!\n\n"
                        f"📁 **작업 디렉토리**: `{abs_path}`\n"
                        f"👤 **개설자**: {interaction.user.mention}\n"
                        f"{f'📝 **프로젝트 메모**: {desc}' if desc else ''}\n\n"
                        f"💡 **지금 바로 코딩 시작하기**:\n"
                        f"이 채널에 원하시는 기능, 버그 수정, 아키텍처 요청을 작성하시면 Coder ↔ QA ↔ Auditor가 즉각 자율 개발에 착수합니다!"
                    ),
                    color=discord.Color.gold()
                )
                await new_ch.send(embed=welcome_embed)

                result_embed = discord.Embed(
                    title="🎉 [프로젝트 생성 & 채널 개설 완료]",
                    description=(
                        f"📁 **디스크 디렉토리**: `{abs_path}`\n"
                        f"🌐 **디스코드 채널**: <#{new_ch.id}>\n\n"
                        f"👉 **<#{new_ch.id}>** 채널로 이동하시면 곧바로 작업 메시지를 입력하실 수 있습니다!"
                    ),
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=result_embed)
            except Exception as e:
                # Fallback to current channel
                session = sm.get_session(self.channel_id)
                session.set_workspace(abs_path)
                await interaction.followup.send(
                    f"📁 디렉토리는 생성되었으나 채널 생성 권한이 부족하여 현재 채널(**<#{self.channel_id}>**)에 연결했습니다:\n`{abs_path}`",
                    ephemeral=False
                )
        else:
            session = sm.get_session(self.channel_id)
            session.set_workspace(abs_path)
            await interaction.followup.send(
                f"📁 폴더 생성 및 현재 채널 연결 완료:\n`{abs_path}`",
                ephemeral=False
            )


# ==============================================================================
# Interactive Control View (Buttons)
# ==============================================================================

class DirectoryControlView(discord.ui.View):
    def __init__(self, channel_id: int, user_id: int, current_workspace: str):
        super().__init__(timeout=300)
        self.channel_id = channel_id
        self.user_id = user_id
        self.current_workspace = current_workspace

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "⚠️ 이 제어판을 호출한 사용자만 버튼을 누를 수 있습니다. 직접 `!mkdir`을 입력해 제어판을 호출하세요.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="새 폴더 생성", style=discord.ButtonStyle.primary, emoji="📁", custom_id="btn_mkdir_create", row=0)
    async def btn_create_folder(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateFolderModal(channel_id=self.channel_id, base_dir=self.current_workspace)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="휴지통 안전 삭제", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="btn_mkdir_trash", row=0)
    async def btn_trash_folder(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TrashDeleteModal(channel_id=self.channel_id, base_dir=self.current_workspace)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="새 채널 & 폴더 동시 개설", style=discord.ButtonStyle.success, emoji="➕", custom_id="btn_mkdir_channel", row=0)
    async def btn_create_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateChannelAndFolderModal(channel_id=self.channel_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="현재 폴더 탐색", style=discord.ButtonStyle.secondary, emoji="📂", custom_id="btn_mkdir_list", row=1)
    async def btn_list_dir(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_directory_status_embed(self.channel_id, self.current_workspace)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="작업 CWD 변경", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="btn_mkdir_switch", row=1)
    async def btn_switch_cwd(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SwitchCwdModal(channel_id=self.channel_id)
        await interaction.response.send_modal(modal)
