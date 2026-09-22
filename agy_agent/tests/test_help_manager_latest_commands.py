import unittest
import os
import sys
import discord

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import config
from help_manager import HelpManager, HelpInteractiveView, HelpCategorySelect

class TestHelpManagerLatestCommands(unittest.TestCase):
    def test_overview_embed(self):
        embed = HelpManager.get_overview_embed()
        self.assertIsInstance(embed, discord.Embed)
        self.assertIn("종합 도움말", embed.title)
        
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        # Verify Golden 5 is included
        self.assertIn("Golden 5", all_text)
        self.assertIn("@Antigravity", all_text)
        self.assertIn("!plan", all_text)
        self.assertIn("!stop", all_text)
        self.assertIn("!new", all_text)
        self.assertIn("!help", all_text)
        # Verify role models
        self.assertIn("Coder (초고속 개발", all_text)
        self.assertIn(config.CODER_MODEL, all_text)
        self.assertIn(config.QA_MODEL, all_text)

    def test_golden5_embed(self):
        embed = HelpManager.get_golden5_embed()
        self.assertIn("외워야 할 핵심 필수 명령어 5선", embed.title)
        self.assertGreaterEqual(len(embed.fields), 5)
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        self.assertIn("@Antigravity", all_text)
        self.assertIn("!plan", all_text)
        self.assertIn("!stop", all_text)
        self.assertIn("!done", all_text)
        self.assertIn("!reset", all_text)
        self.assertIn("!help", all_text)
        self.assertIn("!sim", all_text)
        self.assertIn("!apk", all_text)
        self.assertIn("!model", all_text)

    def test_dev_embed(self):
        embed = HelpManager.get_dev_embed()
        self.assertIn("코딩 & 기획", embed.title)
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        # Commands
        self.assertIn("!pair", all_text)
        self.assertIn("!single", all_text)
        self.assertIn("!single+", all_text)
        self.assertIn("!pair+", all_text)
        self.assertIn("!single_qa2", all_text)
        self.assertIn("!read", all_text)
        self.assertIn("!plan", all_text)
        self.assertIn("!setup", all_text)
        # Mode options
        self.assertIn("!mode auto", all_text)
        self.assertIn("!mode 3+", all_text)
        self.assertIn("!mode 3", all_text)
        self.assertIn("!mode 2+", all_text)
        self.assertIn("!mode 2", all_text)
        self.assertIn("!mode single_qa2", all_text)
        self.assertIn("!mode 1+", all_text)
        self.assertIn("!mode 1", all_text)
        self.assertIn("!mode read", all_text)
        self.assertIn("!mode all", all_text)
        # Engine & Identity
        self.assertIn("!engine doctor", all_text)
        self.assertIn("!identity", all_text)

    def test_auto_embed(self):
        embed = HelpManager.get_auto_embed()
        self.assertIn("기능 제거됨", embed.title)

    def test_qa_embed(self):
        embed = HelpManager.get_qa_embed()
        self.assertIn("QA 허브", embed.title)
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        self.assertIn("!qa", all_text)
        self.assertIn("!qa select qa2", all_text)
        self.assertIn("!qa reset", all_text)
        self.assertIn("!qasetup", all_text)
        self.assertIn("!e2e", all_text)
        self.assertIn("!sim", all_text)
        self.assertIn("!apk", all_text)
        self.assertIn("!android_test", all_text)
        self.assertIn("!win_test", all_text)

    def test_commander_embed(self):
        embed = HelpManager.get_commander_embed()
        self.assertIn("관제 사령관", embed.title)
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        self.assertIn("!commander", all_text)
        self.assertIn("!export", all_text)
        self.assertIn("!drive", all_text)
        self.assertIn("!zip", all_text)
        self.assertIn("!git", all_text)

    def test_system_embed(self):
        embed = HelpManager.get_system_embed()
        self.assertIn("시스템 진단", embed.title)
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        self.assertIn("!status", all_text)
        self.assertIn("!status_refresh", all_text)
        self.assertIn("!quota", all_text)
        self.assertIn("!temp", all_text)
        self.assertIn("!watchdog", all_text)
        self.assertIn("!selfhealing", all_text)
        self.assertIn("!model", all_text)
        self.assertIn("!identity", all_text)
        self.assertIn("!bots", all_text)
        self.assertIn("!pwd", all_text)
        self.assertIn("!cd", all_text)
        self.assertIn("!queue", all_text)
        self.assertIn("!reset", all_text)
        self.assertIn("!stop", all_text)
        self.assertIn("!done", all_text)

    def test_tips_embed(self):
        embed = HelpManager.get_tips_embed()
        self.assertIn("실전 사용 방법", embed.title)
        all_text = " ".join([f.name + " " + f.value for f in embed.fields])
        self.assertIn("!setup", all_text)
        self.assertIn("!plan", all_text)
        self.assertIn("!pair", all_text)
        self.assertIn("!mode 3", all_text)
        self.assertIn("!done", all_text)
        self.assertIn("!sim", all_text)
        self.assertIn("!apk", all_text)

    def test_normalize_page_aliases(self):
        normalize = HelpInteractiveView.normalize_page
        self.assertEqual(normalize("overview"), "overview")
        self.assertEqual(normalize("홈"), "overview")
        self.assertEqual(normalize("golden5"), "golden5")
        self.assertEqual(normalize("골든"), "golden5")
        self.assertEqual(normalize("핵심"), "golden5")
        self.assertEqual(normalize("core"), "golden5")
        self.assertEqual(normalize("dev"), "dev")
        self.assertEqual(normalize("기획"), "dev")
        self.assertEqual(normalize("plan"), "dev")
        self.assertEqual(normalize("mode"), "dev")
        self.assertEqual(normalize("auto"), "auto")
        self.assertEqual(normalize("loop"), "auto")
        self.assertEqual(normalize("qa"), "qa")
        self.assertEqual(normalize("e2e"), "qa")
        self.assertEqual(normalize("commander"), "commander")
        self.assertEqual(normalize("system"), "system")
        self.assertEqual(normalize("admin"), "system")
        self.assertEqual(normalize("tips"), "tips")

    def test_interactive_view_structure(self):
        view = HelpInteractiveView()
        # Row 0: Select with 8 options
        selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
        self.assertEqual(len(selects), 1)
        self.assertEqual(len(selects[0].options), 8)

        # Buttons in row 1 & row 2
        buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(buttons), 10)
        row1_buttons = [b for b in buttons if b.row == 1]
        row2_buttons = [b for b in buttons if b.row == 2]
        self.assertEqual(len(row1_buttons), 5)
        self.assertEqual(len(row2_buttons), 5)

if __name__ == "__main__":
    unittest.main()
