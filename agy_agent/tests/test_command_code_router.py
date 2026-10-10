import os
import sys
from pathlib import Path
import pytest

# Ensure agy_agent is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
AGY_AGENT_DIR = BASE_DIR / "agy_agent"
if str(AGY_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGY_AGENT_DIR))


def test_normalize_engine_id_command_code():
    from engine_manager import normalize_engine_id

    assert normalize_engine_id("cmdc") == "cmdc"
    assert normalize_engine_id("commandcode") == "cmdc"
    assert normalize_engine_id("command-code") == "cmdc"
    assert normalize_engine_id("command_code") == "cmdc"
    assert normalize_engine_id("COMMAND") == "cmdc"
    assert normalize_engine_id("CMDC") == "cmdc"


def test_command_code_adapter_registered():
    from engine_manager import EngineManager, CommandCodeAdapter

    adapter1 = EngineManager.get_adapter("cmdc")
    adapter2 = EngineManager.get_adapter("commandcode")
    assert adapter1 is not None
    assert adapter2 is not None
    assert isinstance(adapter1, CommandCodeAdapter)
    assert isinstance(adapter2, CommandCodeAdapter)
    assert adapter1.engine_id == "cmdc"
    assert adapter1.name == "Command Code CLI"
    assert adapter1.icon == "💻"


def test_command_code_adapter_build_command():
    from engine_manager import EngineManager

    adapter = EngineManager.get_adapter("cmdc")
    cmd = adapter.build_command(
        prompt="Implement user auth",
        workspace="/tmp/test_ws",
        model="claude-sonnet-5-5",
        session_id="test-session-1234",
    )
    assert any("cmdc" in str(part) for part in cmd)
    assert "-p" in cmd
    assert "-t" in cmd
    assert "--yolo" in cmd
    assert "--tools-all" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "-m" in cmd
    assert "claude-sonnet-5-5" in cmd
    assert "--session" in cmd
    assert "test-session-1234" in cmd


def test_model_manager_cmdc_presets():
    from model_manager import ENGINE_PRESETS, get_engine_models, GlobalModelManager

    assert "cmdc" in ENGINE_PRESETS
    assert "commandcode" in ENGINE_PRESETS
    models = get_engine_models("cmdc")
    assert len(models) >= 20
    model_ids = [m[0] for m in models]
    assert "deepseek/deepseek-v4.1-flash" in model_ids
    assert "claude-sonnet-5-5" in model_ids

    # Coherence check
    assert GlobalModelManager.is_model_compatible_with_engine("cmdc", "deepseek/deepseek-v4.1-flash") is True
    assert GlobalModelManager.is_model_compatible_with_engine("cmdc", "claude-sonnet-5-5") is True
    assert GlobalModelManager.is_model_compatible_with_engine("cmdc", "opencode-go/mimo-v2.6-flash") is False
    assert GlobalModelManager.is_model_compatible_with_engine("cmdc", "gemini-3.8-flash-high") is False
    assert GlobalModelManager.default_model_for_engine("cmdc") == "deepseek/deepseek-v4.1-flash"


def test_live_model_catalog_cmdc():
    from live_model_catalog import canonical_engine, LiveModelCatalog

    assert canonical_engine("commandcode") == "cmdc"
    assert canonical_engine("cmdc") == "cmdc"
    models = LiveModelCatalog.get_models("cmdc")
    assert len(models) >= 20


def test_telemetry_status_manager_cmdc_usage():
    import telemetry_status_manager as tsm

    usage = tsm.fetch_command_code_usage()
    if usage:
        assert "monthly_credits" in usage
        assert "five_hour" in usage
        assert "weekly" in usage
        assert "remaining_pct" in usage["five_hour"]
        assert "remaining_pct" in usage["weekly"]

    section = tsm.format_command_code_quota_section()
    if section:
        assert "COMMAND CODE" in section
        assert "5시간 한도" in section
        assert "주간 한도" in section
        assert "cmdc" in section


def test_command_code_zdr_enforced_by_default():
    """Command Code CLI 라우터는 기본적으로 ZDR(Zero Data Retention)을 강제해야 한다."""
    import os
    import config

    assert config.CMD_ZDR == "1"
    assert os.environ.get("CMD_ZDR") == "1"


def test_command_code_adapter_propagates_zdr_env():
    """어댑터가 cmdc 서브프로세스 환경에 CMD_ZDR=1 을 실제로 전달하는지 검증한다."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch
    from engine_manager import EngineManager

    adapter = EngineManager.get_adapter("cmdc")
    captured = {}

    class _FakeStdout:
        async def readline(self):
            return b""

    class _FakeProc:
        pid = None
        returncode = 0
        stdout = _FakeStdout()
        stderr = _FakeStdout()

        async def wait(self):
            return 0

    async def _fake_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    session = SimpleNamespace(
        workspace="/tmp/test_ws",
        current_process=None,
        engine_conversations={},
        touch=lambda *a, **k: None,
    )

    async def _run():
        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            return await adapter.execute(
                session=session,
                prompt="x",
                model="deepseek/deepseek-v4.1-flash",
            )

    asyncio.run(_run())

    assert captured.get("env", {}).get("CMD_ZDR") == "1", f"CMD_ZDR not propagated: {captured.get('env')}"
