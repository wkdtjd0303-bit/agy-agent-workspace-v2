import os
import sys
import pytest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from session_manager import ChannelSession, SessionManager
import hot_reload_bridge

@pytest.fixture(autouse=True)
def preserve_main_module():
    orig_main = sys.modules.get("__main__")
    yield
    if orig_main is not None:
        sys.modules["__main__"] = orig_main
    else:
        sys.modules.pop("__main__", None)

def test_hot_reload_bridge_session_class_sync():
    class OldClass:
        def __init__(self):
            self.mode = "old"

    class MockMain:
        def __init__(self):
            self.session_manager = MockSM()

    class MockSM:
        def __init__(self):
            self._sessions = {123: OldClass()}

    sys.modules["__main__"] = MockMain()

    # Before sync, sess is OldClass
    assert sys.modules["__main__"].session_manager._sessions[123].__class__ is OldClass

    # Run sync
    hot_reload_bridge.sync_in_memory_state()

    # After sync, sess.__class__ must be session_manager.ChannelSession
    import session_manager
    assert sys.modules["__main__"].session_manager._sessions[123].__class__ is session_manager.ChannelSession

    # Setting mode 2+ must work
    sess = sys.modules["__main__"].session_manager._sessions[123]
    sess.set_mode("2+")
    assert sess.mode == "pair_quad"

    # Setting mode 2 must work
    sess.set_mode("2")
    assert sess.mode == "pair"


def test_hot_reload_bridge_command_integrity_and_healing():
    from discord.ext import commands

    class MockBot:
        def __init__(self):
            self.all_commands = {}

        def add_command(self, cmd):
            self.all_commands[cmd.name] = cmd
            for a in cmd.aliases:
                self.all_commands[a] = cmd

        def get_command(self, name):
            return self.all_commands.get(name)

    mock_bot = MockBot()

    @commands.command(name="mode", aliases=["모드", "동작모드"])
    async def mode_cmd(ctx):
        pass

    @commands.command(name="pair", aliases=["trio", "협업", "페어", "3중"])
    async def pair_cmd(ctx):
        pass

    mock_bot.add_command(mode_cmd)
    mock_bot.add_command(pair_cmd)

    # Deliberately corrupt an entry with a bare callable (simulating past defect)
    async def bare_corrupted_func(ctx):
        pass

    mock_bot.all_commands["mode"] = bare_corrupted_func
    assert not hasattr(mock_bot.all_commands["mode"], "invoke")

    class MockMainWithBot:
        def __init__(self):
            self.bot = mock_bot
            self.mode_command = bare_corrupted_func
            self.pair_command = pair_cmd

    sys.modules["__main__"] = MockMainWithBot()

    # Run sync_in_memory_state
    hot_reload_bridge.sync_in_memory_state()

    # Verify mode and all its aliases were healed into Command objects with .invoke
    for alias in ["mode", "모드", "동작모드", "pair", "trio", "협업", "페어", "3중"]:
        cmd_obj = mock_bot.all_commands.get(alias)
        assert cmd_obj is not None, f"Command alias '{alias}' missing"
        assert hasattr(cmd_obj, "invoke"), f"Command alias '{alias}' is missing 'invoke' attribute: {type(cmd_obj)}"


def test_hot_reload_bridge_extended_qa_bots_initialization_and_safety():
    """Ensure that even if __main__ lacks extended_qa_bots or init_extended_qa_bots, sync_in_memory_state initializes them safely without NameError."""
    class MockMainWithoutExtendedQA:
        def __init__(self):
            self.session_manager = None
            self.qa2_bot = None

    mock_main = MockMainWithoutExtendedQA()
    sys.modules["__main__"] = mock_main

    assert not hasattr(mock_main, "extended_qa_bots")

    # Run sync_in_memory_state
    success = hot_reload_bridge.sync_in_memory_state()
    assert success is True

    # Check extended_qa_bots was initialized as a dict
    assert hasattr(mock_main, "extended_qa_bots")
    assert isinstance(mock_main.extended_qa_bots, dict)

    # Check init_extended_qa_bots function was compiled into main
    assert hasattr(mock_main, "init_extended_qa_bots")
    assert callable(mock_main.init_extended_qa_bots)


def test_hot_reload_bridge_universal_recompilation_and_command_callbacks():
    """Ensure hot_reload_bridge compiles all top-level functions in bot.py and updates command callbacks."""
    from discord.ext import commands

    class MockBot:
        def __init__(self):
            self.all_commands = {}

        def add_command(self, cmd):
            self.all_commands[cmd.name] = cmd
            for a in cmd.aliases:
                self.all_commands[a] = cmd

    mock_bot = MockBot()

    async def old_reset(ctx, *args):
        return "old_reset"

    async def old_auto(ctx, *args):
        return "old_auto"

    reset_cmd = commands.Command(old_reset, name="reset", aliases=["초기화", "리셋"])
    auto_cmd = commands.Command(old_auto, name="auto", aliases=["자동"])
    mock_bot.add_command(reset_cmd)
    mock_bot.add_command(auto_cmd)

    class MockMainWithBot:
        def __init__(self):
            self.bot = mock_bot
            self.commander_bot = None
            self.reset_command = old_reset
            self.auto_command = old_auto

    mock_main = MockMainWithBot()
    sys.modules["__main__"] = mock_main

    success = hot_reload_bridge.sync_in_memory_state()
    assert success is True

    # Check that reset_command in main was recompiled from bot.py and is no longer old_reset
    assert mock_main.reset_command is not old_reset
    assert mock_main.reset_command.callback is not old_reset
    assert mock_main.auto_command is not old_auto
    assert mock_main.auto_command.callback is not old_auto

    # Check that bot.all_commands['reset'] callback was updated to the newly compiled function
    assert mock_bot.all_commands["reset"].callback is mock_main.reset_command.callback
    assert mock_bot.all_commands["초기화"].callback is mock_main.reset_command.callback
    assert mock_bot.all_commands["auto"].callback is mock_main.auto_command.callback


def test_reload_helper_modules_dynamic_count():
    """Ensure reload_helper_modules dynamically discovers local modules and returns an accurate count."""
    import bot
    reloaded_count = bot.reload_helper_modules()
    assert reloaded_count >= 25


def test_hot_reload_bridge_evidence_validator_and_session_manager_sync():
    """Ensure hot_reload_bridge bridges EvidenceValidator and updates session_manager.__class__."""
    import hot_reload_bridge
    import evidence_validator
    import session_manager

    # Create dummy outdated SessionManager class
    class OutdatedSessionManager:
        def __init__(self):
            self._sessions = {}

    outdated_sm = OutdatedSessionManager()

    class MockMain:
        def __init__(self):
            self.session_manager = outdated_sm
            self.bot = None
            self.commander_bot = None

    mock_main = MockMain()
    sys.modules["__main__"] = mock_main

    success = hot_reload_bridge.sync_in_memory_state()
    assert success is True

    # 1. Verify EvidenceValidator was bridged into main
    assert hasattr(mock_main, "EvidenceValidator")
    assert mock_main.EvidenceValidator is evidence_validator.EvidenceValidator

    # 2. Verify main.session_manager class was synchronized to real SessionManager
    assert mock_main.session_manager.__class__ is session_manager.SessionManager
    assert hasattr(mock_main.session_manager, "reset_channel_session")


def test_bot_reload_helper_modules_evidence_validator_sync():
    """Ensure bot.reload_helper_modules populates EvidenceValidator into globals()."""
    import bot
    import evidence_validator

    bot.reload_helper_modules()
    assert hasattr(bot, "EvidenceValidator")
    assert bot.EvidenceValidator is evidence_validator.EvidenceValidator


def test_reload_helper_modules_includes_model_manager_and_auto_scheduler():
    """Ensure reload_helper_modules includes model_manager and auto_scheduler."""
    import bot
    import sys
    import model_manager
    import auto_scheduler

    bot.reload_helper_modules()
    assert "model_manager" in sys.modules
    assert "auto_scheduler" in sys.modules


def test_hot_reload_bridge_rebinds_model_command():
    """Ensure model_command and all its aliases are bound properly after sync."""
    import hot_reload_bridge
    from discord.ext import commands

    class MockBot:
        def __init__(self):
            self.all_commands = {}

        def add_command(self, cmd):
            self.all_commands[cmd.name] = cmd
            for a in cmd.aliases:
                self.all_commands[a] = cmd

        def get_command(self, name):
            return self.all_commands.get(name)

    class MockMain:
        def __init__(self):
            self.bot = MockBot()
            self.session_manager = None

    mock_main = MockMain()
    sys.modules["__main__"] = mock_main

    success = hot_reload_bridge.sync_in_memory_state()
    assert success is True
    assert "model" in mock_main.bot.all_commands
    assert "모델" in mock_main.bot.all_commands
    assert "modelselect" in mock_main.bot.all_commands


def test_hot_reload_bridge_recompiles_classes_and_upgrades_views():
    """Ensure ClassDef nodes are extracted and active UI views get upgraded."""
    import hot_reload_bridge

    class MockBot:
        def __init__(self):
            self.all_commands = {}

    class MockMain:
        def __init__(self):
            self.bot = MockBot()
            self.session_manager = None

    mock_main = MockMain()
    sys.modules["__main__"] = mock_main

    success = hot_reload_bridge.sync_in_memory_state()
    assert success is True
    # AutoDashboardView should be compiled into mock_main
    assert "AutoDashboardView" in mock_main.__dict__
    view_cls = mock_main.__dict__["AutoDashboardView"]
    assert hasattr(view_cls, "refresh_view")




