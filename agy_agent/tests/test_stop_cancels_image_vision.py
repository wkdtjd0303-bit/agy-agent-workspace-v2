#!/usr/bin/env python3
"""
tests/test_stop_cancels_image_vision.py

🛑 회귀 테스트: 첨부 이미지 시각 분석 중 `!stop` 을 내리면 즉시 취소되어야 한다.

배경(버그):
  `on_message` 의 첨부 이미지 비전 분석이 **인라인 await** 로 실행되어
  `session.current_task` 로 추적되지 않았다. 그래서 `!stop`(→ session.stop())은
  취소할 대상이 없다고 판단하고, "🖼️ [첨부 이미지 시각 분석 진행 중...]" 상태가
  계속 남은 채 분석이 끝까지 돌았다.

검증 항목:
  1. [메커니즘] ChannelSession.stop() 이 진행 중인 vision_task 를 실제로 취소한다.
  2. [배선] on_message 이미지 분기가 분석을 session.vision_task 로 등록하고,
     취소 시 후속 실행(execute_channel_prompt)으로 넘어가지 않고 정직하게 중단을 알린다.
"""

import os
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from PIL import Image

from session_manager import ChannelSession


def _write_png(path: str) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color=(30, 120, 200)).save(buf, "PNG")
    data = buf.getvalue()
    with open(path, "wb") as f:
        f.write(data)
    return data


class _FakeAttachment:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self._payload = payload

    async def save(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self._payload)


class TestStopCancelsImageVision(unittest.IsolatedAsyncioTestCase):
    async def _make_session(self, tmpdir: str) -> ChannelSession:
        return ChannelSession(channel_id=987654321, workspace=tmpdir)

    async def test_session_stop_cancels_inflight_vision_task(self):
        """1. 메커니즘: session.stop() 이 진행 중인 vision_task 를 취소한다."""
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="agy_vision_cancel_")
        sess = await self._make_session(tmpdir)

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def _long_running():
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "done"

        task = asyncio.ensure_future(_long_running())
        sess.vision_task = task
        await asyncio.wait_for(started.wait(), timeout=5)

        stopped = sess.stop()

        self.assertTrue(stopped, "진행 중인 vision_task 가 있으면 stop() 은 True 를 반환해야 한다")
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())
        self.assertTrue(cancelled.is_set(), "vision_task 내부에서 CancelledError 가 전파되어야 한다")
        self.assertIsNone(sess.vision_task)

    async def test_on_message_aborts_vision_analysis_on_stop(self):
        """2. 배선: on_message 이미지 분석이 !stop 시 취소되고 후속 실행이 일어나지 않는다."""
        import tempfile

        import bot as bot_module
        from vision_attachment_analyzer import VisionAttachmentAnalyzer

        tmpdir = tempfile.mkdtemp(prefix="agy_on_message_stop_")
        png_path = os.path.join(tmpdir, "incoming.png")
        payload = _write_png(png_path)

        sess = await self._make_session(tmpdir)

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def _blocking_analyze(*args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return {"success": True, "analysis": "should never be used", "vision_verified": True}

        status_msg = MagicMock()
        status_msg.edit = AsyncMock()

        msg = MagicMock()
        msg.author.bot = False
        msg.author.id = 4242
        msg.webhook_id = None
        msg.is_system = MagicMock(return_value=False)
        msg.channel.id = sess.channel_id
        msg.channel.category_id = 1
        msg.content = "이 화면 좀 분석해줘"
        msg.attachments = [_FakeAttachment("shot.png", payload)]
        msg.reply = AsyncMock(return_value=status_msg)

        with patch.object(bot_module, "ensure_commander_bot_started", lambda: None), \
             patch.object(bot_module, "ensure_image_bot_started", lambda: None), \
             patch.object(bot_module, "ensure_qa2_bot_started", lambda: None), \
             patch.object(bot_module, "check_and_apply_finish_project_freeze", AsyncMock()), \
             patch.object(bot_module, "execute_channel_prompt", AsyncMock()) as mock_exec, \
             patch.object(bot_module.session_manager, "get_session", return_value=sess), \
             patch.object(VisionAttachmentAnalyzer, "analyze_image_file", _blocking_analyze):

            task = asyncio.ensure_future(bot_module.on_message(msg))
            await asyncio.wait_for(started.wait(), timeout=5)

            self.assertIsNotNone(sess.vision_task, "분석 중에는 session.vision_task 가 등록되어 있어야 한다")
            self.assertFalse(sess.vision_task.done())

            # 사용자의 !stop → session_manager.stop_session → session.stop()
            stopped = sess.stop()
            self.assertTrue(stopped)

            await asyncio.wait_for(task, timeout=5)

        self.assertTrue(cancelled.is_set(), "on_message 의 vision 태스크가 취소되어야 한다")
        mock_exec.assert_not_awaited()
        self.assertTrue(status_msg.edit.await_count >= 1, "중단 상태를 사용자에게 알려야 한다")
        edited = status_msg.edit.await_args.kwargs.get("content", "")
        self.assertIn("중단됨", edited)


if __name__ == "__main__":
    unittest.main()
