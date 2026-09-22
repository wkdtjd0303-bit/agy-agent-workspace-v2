#!/usr/bin/env python3
"""
Unit tests for git_manager.py (GitRepoManager & GitStatusInteractiveView)
"""

import os
import sys
import unittest

# Ensure agy_agent path is loaded
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from git_manager import GitRepoManager, GitStatusInteractiveView
import discord


class TestGitManager(unittest.TestCase):
    def setUp(self):
        self.workspace = PARENT_DIR

    def test_get_repo_info_real_workspace(self):
        info = GitRepoManager.get_repo_info(self.workspace)
        self.assertTrue(info["is_git"])
        self.assertIn("main", info["branch"])
        self.assertTrue(len(info["commit_hash"]) >= 7)
        self.assertIn("wkdtjd0303-bit/agy_agent", info["remote_url"])

    def test_get_recent_commits(self):
        commits = GitRepoManager.get_recent_commits(self.workspace, limit=3)
        self.assertTrue(len(commits) >= 1)
        first = commits[0]
        self.assertIn("hash", first)
        self.assertIn("msg", first)
        self.assertIn("author", first)
        self.assertIn("time", first)

    def test_build_status_embed(self):
        embed = GitRepoManager.build_status_embed(self.workspace)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("Git 저장소 실시간 동기화 상태", embed.title)
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("원격 저장소" in n for n in field_names))
        self.assertTrue(any("현재 브랜치" in n for n in field_names))
        self.assertTrue(any("원격 동기화" in n for n in field_names))

    def test_build_commits_embed(self):
        embed = GitRepoManager.build_commits_embed(self.workspace, limit=3)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("최근 커밋 이력", embed.title)
        self.assertTrue(len(embed.description) > 0)

    def test_interactive_view_initialization(self):
        info = GitRepoManager.get_repo_info(self.workspace)
        view = GitStatusInteractiveView(self.workspace, repo_url=info["remote_url"])
        self.assertIsInstance(view, discord.ui.View)
        # Should have at least refresh and commits buttons
        labels = [item.label for item in view.children if hasattr(item, "label")]
        self.assertIn("🔄 상태 새로고침", labels)
        self.assertIn("📜 최근 커밋 5건", labels)
        self.assertIn("🌐 GitHub 바로가기", labels)


if __name__ == "__main__":
    unittest.main()
