"""
Command Code (cmdc) 라우터 진행 표기 회귀 테스트.

배경(실측): cmdc `--output-format json` 은 NDJSON 이벤트를
`{"type":"event","event":{"type":...}}` 로 내보내며 도구 수명주기는
`tool_queued → tool_running → tool_update → tool_completed` 다.
기존 어댑터 파서는 `tool_call`/`tool_start` 만 찾고 `message_update.content`를
문자열로만 가정해, 어떤 진행/도구 알림도 방출하지 못했다.
그 결과 디스코드 상태 메시지가 "작업 시작 중..." 에서 부동자세로 멈췄다.

이 테스트는 실제 캡처한 cmdc 스트림 형태를 주입하여:
1) 도구 이벤트가 진행 알림으로 변환되는지
2) 파트 리스트 content 에서 텍스트가 추출되는지
3) 최종 텍스트(result.finalText)가 정상 반환되는지
를 검증한다.
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
# 테스트가 <workspace>/tests/ 또는 <repo>/agy_agent/tests/ 어디에 있든
# engine_manager.py 가 있는 디렉토리를 sys.path 에 추가한다.
AGY_AGENT_DIR = BASE_DIR / "agy_agent"
if not (AGY_AGENT_DIR / "engine_manager.py").is_file():
    AGY_AGENT_DIR = BASE_DIR
if str(AGY_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGY_AGENT_DIR))
# 모의 서브프로세스 실행이라 실제 워크스페이스 경로는 문자열로만 쓰인다.
WORKSPACE = str(AGY_AGENT_DIR)


# 실제 cmdc 실행에서 캡처한 이벤트 시퀀스 (도구 호출 1회 + 최종 응답)
REAL_CMDC_STREAM = [
    {"type": "event", "event": {"type": "run_start", "sessionId": "3b4f8c3d-bd8f-4009-97ff-d102d4a72c04"}},
    {"type": "event", "event": {"type": "turn_start", "turnNumber": 1}},
    {"type": "event", "event": {"type": "thinking_start"}},
    {"type": "event", "event": {"type": "thinking_delta", "delta": "Run echo."}},
    {"type": "event", "event": {"type": "thinking_end", "text": "Run echo."}},
    {
        "type": "event",
        "event": {
            "type": "tool_queued",
            "toolCallId": "call_00_x",
            "toolName": "shell_command",
            "input": {"command": "echo CMDB_TOOL_PROBE"},
        },
    },
    {"type": "event", "event": {"type": "tool_running", "toolCallId": "call_00_x", "toolName": "shell_command"}},
    {
        "type": "event",
        "event": {"type": "tool_update", "toolCallId": "call_00_x", "toolName": "shell_command",
                  "partial": [{"type": "text", "text": "CMDB_TOOL_PROBE\n"}]},
    },
    {
        "type": "event",
        "event": {"type": "tool_completed", "toolCallId": "call_00_x", "toolName": "shell_command",
                  "result": [{"type": "text", "text": "CMDB_TOOL_PROBE\n"}], "deferred": False},
    },
    {
        "type": "event",
        "event": {
            "type": "message_update",
            "content": [
                {"type": "thinking", "thinking": "The command ran.", "signature": ""},
                {"type": "text", "text": "DONE"},
            ],
        },
    },
    {"type": "event", "event": {"type": "turn_end", "turnNumber": 2, "hadToolCalls": True}},
    {
        "type": "result",
        "subtype": "success",
        "sessionId": "3b4f8c3d-bd8f-4009-97ff-d102d4a72c04",
        "stopReason": "end_turn",
        "finalText": "DONE",
    },
]


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [json.dumps(ev).encode() for ev in lines]

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeStderr:
    async def readline(self):
        return b""


class _FakeProc:
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()
        self.returncode = 0
        self.pid = 12345

    async def wait(self):
        return self.returncode


def test_cmdc_tool_events_produce_progress_notifications():
    from engine_manager import EngineManager

    adapter = EngineManager.get_adapter("cmdc")
    notifications = []

    async def on_tool_update(msg):
        notifications.append(msg)

    fake_proc = _FakeProc(list(REAL_CMDC_STREAM))
    session = SimpleNamespace(
        workspace=WORKSPACE,
        current_process=None,
        engine_conversations={},
        touch=lambda *a, **k: None,
    )

    async def _run():
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            return await adapter.execute(
                session=session,
                prompt="echo CMDB_TOOL_PROBE",
                on_tool_update=on_tool_update,
                model="deepseek/deepseek-v4.1-flash",
            )

    rc, out, errs = asyncio.run(_run())

    assert rc == 0, f"expected rc=0, got {rc}, errs={errs}"
    assert "DONE" in out, f"final text not returned, got: {out!r}"

    joined = "\n".join(notifications)
    assert "shell_command" in joined, f"tool name never surfaced in progress: {notifications}"
    assert "작업 시작 중" in joined
    # 진행 표기가 초기 메시지 외에 실제 도구 상태로 갱신되었는지 확인
    assert any("도구" in n for n in notifications), f"no tool lifecycle progress emitted: {notifications}"


def test_cmdc_error_event_marks_session_not_found():
    from engine_manager import EngineManager

    adapter = EngineManager.get_adapter("cmdc")
    error_stream = [
        {"error": {"type": "invalid_request", "message": "Session not found: abc"}},
    ]
    fake_proc = _FakeProc(list(error_stream))
    fake_proc.returncode = 1
    session = SimpleNamespace(
        workspace=WORKSPACE,
        current_process=None,
        engine_conversations={},
        touch=lambda *a, **k: None,
    )

    async def _run():
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            return await adapter.execute(
                session=session,
                prompt="x",
                model="deepseek/deepseek-v4.1-flash",
            )

    rc, out, errs = asyncio.run(_run())

    # session_not_found 감지 시 저장소가 비워지고 새 세션으로 재시도된다.
    # 핵심은 오류가 stderr/에러 라인으로 표면화되는 것.
    assert any("Session not found" in e or "Command Code Error" in e for e in errs), f"error not surfaced: {errs}"


def test_cmdc_extract_text_from_part_list():
    from engine_manager import _cmdc_extract_text

    content = [
        {"type": "thinking", "thinking": "reasoning here"},
        {"type": "text", "text": "HELLO"},
        {"type": "text", "text": "_WORLD"},
    ]
    assert _cmdc_extract_text(content) == "HELLO_WORLD"
    assert _cmdc_extract_text("plain string") == "plain string"
    assert _cmdc_extract_text(None) == ""


def test_cmdc_event_hint_maps_real_tool_events():
    from engine_manager import _cmdc_event_hint

    assert "shell_command" in _cmdc_event_hint("tool_queued", {"toolName": "shell_command"})
    assert "shell_command" in _cmdc_event_hint("tool_running", {"toolName": "shell_command"})
    assert "shell_command" in _cmdc_event_hint("tool_completed", {"toolName": "shell_command"})
    assert _cmdc_event_hint("thinking_start", {}) == ""
