#!/usr/bin/env python3
"""
Git & GitHub Remote Operations Manager (AGY 3.0 Standard)
Provides real-time git inspection, commit history, remote repository tracking,
and interactive Discord UI views for !git and !github commands.
"""

import os
import subprocess
import time
from typing import Dict, Any, List, Optional
import discord

logger = None
try:
    import logging
    logger = logging.getLogger("GitManager")
except Exception:
    pass


class GitRepoManager:
    """Manages Git repository information, remote tracking, and embed formatting."""

    @staticmethod
    def get_repo_info(workspace: str) -> Dict[str, Any]:
        """Collects git metadata from the specified workspace directory."""
        info: Dict[str, Any] = {
            "is_git": False,
            "branch": "unknown",
            "commit_hash": "unknown",
            "commit_msg": "",
            "commit_author": "",
            "commit_time": "",
            "remote_url": "",
            "clean": True,
            "modified_count": 0,
            "untracked_count": 0,
            "ahead": 0,
            "behind": 0
        }

        if not workspace or not os.path.isdir(workspace):
            return info

        # Check if inside git work tree
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            if res.returncode != 0 or res.stdout.strip() != "true":
                return info
            info["is_git"] = True
        except Exception:
            return info

        # 1. Branch
        try:
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            info["branch"] = res.stdout.strip() or "detached HEAD"
        except Exception:
            pass

        # 2. Latest Commit info
        try:
            res = subprocess.run(
                ["git", "log", "-1", "--pretty=format:%h|%s|%an|%cr"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("|", 3)
                if len(parts) >= 4:
                    info["commit_hash"] = parts[0]
                    info["commit_msg"] = parts[1]
                    info["commit_author"] = parts[2]
                    info["commit_time"] = parts[3]
        except Exception:
            pass

        # 3. Remote URL
        try:
            res = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            if res.returncode == 0:
                raw_url = res.stdout.strip()
                # Normalize SSH URL to HTTPS for clickable Discord links
                if raw_url.startswith("git@github.com:"):
                    raw_url = "https://github.com/" + raw_url[len("git@github.com:"):]
                if raw_url.endswith(".git"):
                    raw_url = raw_url[:-4]
                info["remote_url"] = raw_url
        except Exception:
            pass

        # 4. Working tree status
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            if res.returncode == 0:
                lines = [line for line in res.stdout.splitlines() if line.strip()]
                info["clean"] = (len(lines) == 0)
                info["modified_count"] = sum(1 for line in lines if not line.startswith("??"))
                info["untracked_count"] = sum(1 for line in lines if line.startswith("??"))
        except Exception:
            pass

        # 5. Ahead / Behind
        try:
            res = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            if res.returncode == 0 and res.stdout.strip():
                counts = res.stdout.strip().split()
                if len(counts) >= 2:
                    info["ahead"] = int(counts[0])
                    info["behind"] = int(counts[1])
        except Exception:
            pass

        return info

    @staticmethod
    def get_recent_commits(workspace: str, limit: int = 5) -> List[Dict[str, str]]:
        """Retrieves recent commit log entries from the workspace."""
        commits = []
        if not workspace or not os.path.isdir(workspace):
            return commits

        try:
            res = subprocess.run(
                ["git", "log", f"-n{limit}", "--pretty=format:%h|%s|%an|%cr|%H"],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    parts = line.split("|", 4)
                    if len(parts) == 5:
                        commits.append({
                            "hash": parts[0],
                            "msg": parts[1],
                            "author": parts[2],
                            "time": parts[3],
                            "full_hash": parts[4]
                        })
        except Exception:
            pass

        return commits

    @classmethod
    def build_status_embed(cls, workspace: str) -> discord.Embed:
        """Constructs an informative, beautiful Discord embed displaying Git status."""
        info = cls.get_repo_info(workspace)
        ws_name = os.path.basename(workspace) if workspace else "알 수 없음"

        if not info["is_git"]:
            embed = discord.Embed(
                title="📦 [Git 저장소 상태]",
                description=f"경로 `{workspace}`는 Git 버전 관리 저장소가 아닙니다.",
                color=discord.Color.light_grey()
            )
            return embed

        status_color = discord.Color.green() if info["clean"] and info["ahead"] == 0 else discord.Color.gold()
        if info["behind"] > 0:
            status_color = discord.Color.orange()

        embed = discord.Embed(
            title=f"🐙 [Git 저장소 실시간 동기화 상태] ({ws_name})",
            color=status_color
        )

        remote_display = f"[{info['remote_url']}]({info['remote_url']})" if info["remote_url"] else "원격 저장소 미설정"
        embed.add_field(
            name="🌐 원격 저장소 (Remote)",
            value=remote_display,
            inline=False
        )

        branch_icon = "🌿" if info["branch"] in ("main", "master") else "🔀"
        embed.add_field(
            name="🌿 현재 브랜치",
            value=f"{branch_icon} `{info['branch']}`",
            inline=True
        )

        sync_status = "🟢 최신 동기화 완료 (Up to date)"
        if info["ahead"] > 0 and info["behind"] > 0:
            sync_status = f"⚠️ 푸시 대기 {info['ahead']}개 / 풀 대기 {info['behind']}개"
        elif info["ahead"] > 0:
            sync_status = f"📤 원격 푸시 대기 ({info['ahead']}개 커밋)"
        elif info["behind"] > 0:
            sync_status = f"📥 원격 풀 대기 ({info['behind']}개 커밋)"

        embed.add_field(
            name="🔄 원격 동기화",
            value=sync_status,
            inline=True
        )

        clean_status = "✨ 작업 트리 깨끗함 (Clean)" if info["clean"] else f"📝 수정됨: `{info['modified_count']}개`, 미추적: `{info['untracked_count']}개`"
        embed.add_field(
            name="📂 작업 폴더 상태",
            value=clean_status,
            inline=True
        )

        latest_desc = (
            f"• **해시**: `{info['commit_hash']}`\n"
            f"• **메시지**: {info['commit_msg']}\n"
            f"• **작성자**: {info['commit_author']} ({info['commit_time']})"
        ) if info["commit_hash"] != "unknown" else "커밋 기록 없음"

        embed.add_field(
            name="📌 최신 커밋 (HEAD)",
            value=latest_desc,
            inline=False
        )

        embed.set_footer(text=f"작업 디렉토리: {workspace}")
        return embed

    @classmethod
    def build_commits_embed(cls, workspace: str, limit: int = 5) -> discord.Embed:
        """Constructs a Discord embed listing recent Git commits with direct clickable GitHub links."""
        info = cls.get_repo_info(workspace)
        commits = cls.get_recent_commits(workspace, limit=limit)
        ws_name = os.path.basename(workspace) if workspace else "알 수 없음"

        embed = discord.Embed(
            title=f"📜 [최근 커밋 이력] ({ws_name} · {info['branch']})",
            color=discord.Color.blue()
        )

        if not commits:
            embed.description = "커밋 이력이 존재하지 않습니다."
            return embed

        lines = []
        for i, c in enumerate(commits, 1):
            if info["remote_url"] and "github.com" in info["remote_url"]:
                commit_link = f"[{c['hash']}]({info['remote_url']}/commit/{c['full_hash']})"
            else:
                commit_link = f"`{c['hash']}`"

            lines.append(f"**{i}.** {commit_link} — {c['msg']}\n└ *by {c['author']} ({c['time']})*")

        embed.description = "\n\n".join(lines)
        if info["remote_url"]:
            embed.set_footer(text=f"저장소: {info['remote_url']}")
        return embed


class GitStatusInteractiveView(discord.ui.View):
    """Interactive Discord UI View with buttons for refreshing git status, viewing commits, and opening repo."""

    def __init__(self, workspace: str, repo_url: str = ""):
        super().__init__(timeout=180.0)
        self.workspace = workspace
        self.repo_url = repo_url or "https://github.com/wkdtjd0303-bit/agy_agent"
        # Add link button if remote URL is valid HTTPS
        if self.repo_url and self.repo_url.startswith("http"):
            self.add_item(discord.ui.Button(label="🌐 GitHub 바로가기", url=self.repo_url, row=0))

    @discord.ui.button(label="🔄 상태 새로고침", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = GitRepoManager.build_status_embed(self.workspace)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📜 최근 커밋 5건", style=discord.ButtonStyle.secondary, emoji="📜", row=0)
    async def btn_commits(self, interaction: discord.Interaction, button: discord.ui.Button):
        commits_embed = GitRepoManager.build_commits_embed(self.workspace, limit=5)
        await interaction.response.edit_message(embed=commits_embed, view=self)
