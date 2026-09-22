#!/usr/bin/env python3
"""
🧪 Test Suite for Directory Manager & Safe Trash System
Tests:
1. Universal device path resolution (absolute, tilde, relative, base dir).
2. Protected system paths security enforcement (cannot delete /, /home, etc.).
3. Safe trash movement using send2trash / gio trash (zero permanent rm -rf).
4. Directory creation anywhere on device with optional initial files.
5. Discord UI elements (DirectoryControlView, Modals, Embed builders).
6. !mkdir command integration & alias registration in bot.py.
7. AutonomySafetyLayer Guard 11 trash-only deletion enforcement.
"""

import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import safe_trash
from safe_trash import (
    resolve_device_path,
    is_protected_path,
    send_to_trash,
    create_device_folder,
    format_bytes,
    calculate_path_size,
    DEFAULT_AUTOCODING_BASE,
)
import directory_manager
from directory_manager import (
    sanitize_channel_name,
    build_mkdir_control_embed,
    build_directory_status_embed,
    DirectoryControlView,
    CreateFolderModal,
    TrashDeleteModal,
    SwitchCwdModal,
    CreateChannelAndFolderModal,
)
import bot
from autonomy_guard import AutonomySafetyLayer


class TestSafeTrashAndDirectoryManager(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_safetrash_")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Device Path Resolution Tests
    # -------------------------------------------------------------------------

    def test_resolve_device_path_absolute(self):
        """Absolute paths anywhere on the device must be respected and normalized."""
        abs_target = os.path.join(self.temp_dir, "my_custom_folder")
        resolved = resolve_device_path(abs_target)
        self.assertEqual(resolved, abs_target)

    def test_resolve_device_path_tilde(self):
        """Tilde paths must expand to user home directory."""
        resolved = resolve_device_path("~/some_project")
        user_home = os.path.abspath(os.path.expanduser("~"))
        self.assertTrue(resolved.startswith(user_home))
        self.assertTrue(resolved.endswith("some_project"))

    def test_resolve_device_path_relative(self):
        """Relative paths should resolve against base_dir if provided, or autocoding base."""
        rel_target = "my_sub_dir"
        resolved = resolve_device_path(rel_target, base_dir=self.temp_dir)
        self.assertEqual(resolved, os.path.join(self.temp_dir, "my_sub_dir"))

    def test_resolve_device_path_empty(self):
        """Empty path defaults to base directory or default autocoding base."""
        resolved = resolve_device_path("", base_dir=self.temp_dir)
        self.assertEqual(resolved, os.path.abspath(self.temp_dir))

    # -------------------------------------------------------------------------
    # 2. Critical System Directory Protection Tests
    # -------------------------------------------------------------------------

    def test_is_protected_path_system_roots(self):
        """System roots must be strictly protected from deletion."""
        protected_targets = ["/", "/home", "/etc", "/usr", "/bin", "/var", "/root"]
        for p in protected_targets:
            is_prot, msg = is_protected_path(p)
            self.assertTrue(is_prot, f"Path {p} should be protected!")
            self.assertIn("보호", msg)

    def test_is_protected_path_user_home_and_desktop(self):
        """User home root and Desktop root must be protected."""
        user_home = os.path.abspath(os.path.expanduser("~"))
        desktop_dir = os.path.abspath(os.path.join(user_home, "Desktop"))
        self.assertTrue(is_protected_path(user_home)[0])
        self.assertTrue(is_protected_path(desktop_dir)[0])

    def test_is_protected_path_autocoding_base_and_repo(self):
        """Autocoding hub root and !05_agy_agent repo itself must be protected."""
        self.assertTrue(is_protected_path(DEFAULT_AUTOCODING_BASE)[0])
        agent_repo = os.path.join(DEFAULT_AUTOCODING_BASE, "!05_agy_agent")
        self.assertTrue(is_protected_path(agent_repo)[0])

    def test_is_protected_path_normal_project_allowed(self):
        """Standard subproject directories inside temp or autocoding must be allowed."""
        safe_target = os.path.join(self.temp_dir, "valid_project")
        is_prot, _ = is_protected_path(safe_target)
        self.assertFalse(is_prot, f"Project path {safe_target} should NOT be protected!")

    # -------------------------------------------------------------------------
    # 3. Safe Trash Movement Tests (Zero Permanent rm -rf)
    # -------------------------------------------------------------------------

    def test_send_to_trash_folder_success(self):
        """Test moving a project directory to Trash via send2trash."""
        test_project = os.path.join(self.temp_dir, "test_project_to_trash")
        os.makedirs(test_project, exist_ok=True)
        with open(os.path.join(test_project, "main.py"), "w") as f:
            f.write("print('hello')")

        self.assertTrue(os.path.exists(test_project))

        success, msg, meta = send_to_trash(test_project)
        self.assertTrue(success, f"send_to_trash failed: {msg}")
        self.assertFalse(os.path.exists(test_project), "Original path must not exist after trashing!")
        self.assertIn("휴지통", msg)
        self.assertTrue("안전 보장" in msg or "수동 휴지통" in msg, f"Unexpected trash message: {msg}")

    def test_send_to_trash_protected_rejection(self):
        """Attempting to trash a protected directory must be blocked."""
        success, msg, _ = send_to_trash("/")
        self.assertFalse(success)
        self.assertIn("보호", msg)

    def test_send_to_trash_nonexistent_rejection(self):
        """Attempting to trash non-existent path must be cleanly handled."""
        non_existent = os.path.join(self.temp_dir, "does_not_exist_xyz")
        success, msg, _ = send_to_trash(non_existent)
        self.assertFalse(success)
        self.assertIn("존재하지 않습니다", msg)

    # -------------------------------------------------------------------------
    # 4. Universal Device Folder Creation Tests
    # -------------------------------------------------------------------------

    def test_create_device_folder_with_initial_files(self):
        """Creates folder with optional initial files."""
        target_dir = os.path.join(self.temp_dir, "new_project_1")
        success, abs_path, msg = create_device_folder(
            target_dir,
            initial_files=["README.md", "app.py"]
        )
        self.assertTrue(success)
        self.assertTrue(os.path.isdir(target_dir))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "README.md")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "app.py")))
        self.assertIn("성공적으로 준비되었습니다", msg)

    # -------------------------------------------------------------------------
    # 5. UI & Discord Component Tests
    # -------------------------------------------------------------------------

    def test_sanitize_channel_name(self):
        """Channel names must conform to Discord text channel naming rules."""
        self.assertEqual(sanitize_channel_name("!11_My Cool Project!"), "11-my-cool-project")
        self.assertEqual(sanitize_channel_name("  New Folder  "), "new-folder")
        self.assertEqual(sanitize_channel_name("프로젝트_테스트"), "프로젝트-테스트")

    def test_build_mkdir_control_embed(self):
        """Verify the control panel embed contains instructions and current CWD."""
        embed = build_mkdir_control_embed(123456, self.temp_dir)
        self.assertIn("원터치 디렉토리 & 휴지통 안전 관리", embed.title)
        self.assertIn(self.temp_dir, embed.description)
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("기능 버튼" in name for name in field_names))
        self.assertTrue(any("안전 권한 제어" in name for name in field_names))

    def test_build_directory_status_embed(self):
        """Verify the directory status listing shows contents and sizes."""
        sub = os.path.join(self.temp_dir, "sub_folder")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(self.temp_dir, "test.txt"), "w") as f:
            f.write("test content")

        embed = build_directory_status_embed(123456, self.temp_dir)
        self.assertIn("디렉토리 현황", embed.title)
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("하위 폴더" in name for name in field_names))
        self.assertTrue(any("파일" in name for name in field_names))

    def test_directory_control_view_components(self):
        """Verify DirectoryControlView has all 5 required buttons with appropriate IDs."""
        view = DirectoryControlView(channel_id=123, user_id=456, current_workspace=self.temp_dir)
        button_ids = [child.custom_id for child in view.children]
        self.assertIn("btn_mkdir_create", button_ids)
        self.assertIn("btn_mkdir_trash", button_ids)
        self.assertIn("btn_mkdir_channel", button_ids)
        self.assertIn("btn_mkdir_list", button_ids)
        self.assertIn("btn_mkdir_switch", button_ids)

    def test_modals_initialization(self):
        """Verify Modals can be instantiated without errors."""
        m_create = CreateFolderModal(channel_id=123, base_dir=self.temp_dir)
        self.assertIsNotNone(m_create.folder_path_input)

        m_trash = TrashDeleteModal(channel_id=123, base_dir=self.temp_dir)
        self.assertIsNotNone(m_trash.target_path_input)
        self.assertIsNotNone(m_trash.confirm_input)

        m_switch = SwitchCwdModal(channel_id=123)
        self.assertIsNotNone(m_switch.target_path_input)

        m_chan = CreateChannelAndFolderModal(channel_id=123)
        self.assertIsNotNone(m_chan.project_name_input)

    # -------------------------------------------------------------------------
    # 6. Bot Command & Rebinding Integration Tests
    # -------------------------------------------------------------------------

    def test_bot_mkdir_command_registered(self):
        """Ensure !mkdir and its aliases are properly registered in bot.all_commands."""
        bot.reload_helper_modules()
        cmd = bot.bot.all_commands.get("mkdir")
        self.assertIsNotNone(cmd, "!mkdir command not found in bot.all_commands")

        expected_aliases = ["폴더", "폴더생성", "폴더관리", "folder", "rmdir", "trash", "mk", "디렉토리생성", "디렉토리관리"]
        for alias in expected_aliases:
            self.assertIn(alias, cmd.aliases, f"Alias '{alias}' missing from mkdir_command")
            self.assertIn(alias, bot.bot.all_commands, f"Alias '{alias}' not registered in bot.all_commands")

    async def test_mkdir_command_execution_with_path(self):
        """Invoking !mkdir with path creates directory and sets session workspace."""
        ctx = MagicMock()
        ctx.channel.id = 7770001
        ctx.author.id = 8880001
        ctx.send = AsyncMock()

        target_new = os.path.join(self.temp_dir, "auto_created_ws")
        await bot.mkdir_command(ctx, path=target_new)

        self.assertTrue(os.path.isdir(target_new))
        ctx.send.assert_called_once()
        call_kwargs = ctx.send.call_args[1]
        self.assertIn("embed", call_kwargs)
        self.assertIn("view", call_kwargs)
        self.assertIn("새 디렉토리 생성 및 채널 연동 완료", call_kwargs["embed"].title)

    async def test_mkdir_command_execution_no_arg_shows_view(self):
        """Invoking !mkdir without arguments sends the interactive control panel view."""
        ctx = MagicMock()
        ctx.channel.id = 7770002
        ctx.author.id = 8880002
        ctx.send = AsyncMock()

        await bot.mkdir_command(ctx, path="")

        ctx.send.assert_called_once()
        call_kwargs = ctx.send.call_args[1]
        self.assertIn("embed", call_kwargs)
        self.assertEqual(call_kwargs["view"].__class__.__name__, "DirectoryControlView")
        self.assertIn("원터치 디렉토리 & 휴지통 안전 관리", call_kwargs["embed"].title)

    # -------------------------------------------------------------------------
    # 7. AutonomySafetyLayer Guard 11 Tests
    # -------------------------------------------------------------------------

    def test_autonomy_safety_layer_guard11_destructive_task_interception(self):
        """AutonomySafetyLayer must intercept dangerous permanent rm -rf tasks."""
        ch_id = 99990099
        is_safe, action, msg = AutonomySafetyLayer.check_safety_guards(
            channel_id=ch_id,
            workspace=self.temp_dir,
            current_task="sudo rm -rf /"
        )
        self.assertFalse(is_safe)
        self.assertEqual(action, "ESCALATE_TO_HUMAN")
        self.assertIn("휴지통 보존 삭제 헌법", msg)

    def test_autonomy_safety_layer_safe_trash_api(self):
        """AutonomySafetyLayer.safe_trash delegates to send_to_trash cleanly."""
        target_f = os.path.join(self.temp_dir, "agent_trash_test")
        os.makedirs(target_f, exist_ok=True)
        self.assertTrue(os.path.exists(target_f))

        success, msg, _ = AutonomySafetyLayer.safe_trash(target_f)
        self.assertTrue(success)
        self.assertFalse(os.path.exists(target_f))


if __name__ == "__main__":
    unittest.main()
