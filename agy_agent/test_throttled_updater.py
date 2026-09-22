import asyncio
import time
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock
import discord

# Ensure current dir is in sys.path
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from discord_utils import ThrottledStatusUpdater


class TestThrottledStatusUpdater(unittest.IsolatedAsyncioTestCase):
    async def test_throttling_rapid_calls(self):
        """Verify that 10 rapid calls in < 0.2s result in at most 2 edit calls (immediate + trailing)."""
        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.content = "Initial content"
        mock_msg.edit = AsyncMock()

        updater = ThrottledStatusUpdater(mock_msg, min_interval=0.5)

        # Call 10 times in rapid succession
        for i in range(10):
            await updater.update(f"Step {i}")
            await asyncio.sleep(0.01)

        # At this moment, only the first call should have been executed immediately
        self.assertEqual(mock_msg.edit.call_count, 1)
        first_call_content = mock_msg.edit.call_args[1].get("content")
        self.assertEqual(first_call_content, "Step 0")

        # Wait for delayed flush to complete (0.5s cooldown)
        await asyncio.sleep(0.6)

        # Now the trailing call should have executed with the LATEST text "Step 9"
        self.assertEqual(mock_msg.edit.call_count, 2)
        second_call_content = mock_msg.edit.call_args[1].get("content")
        self.assertEqual(second_call_content, "Step 9")

        await updater.delete()

    async def test_deduplication(self):
        """Verify identical content is not sent to Discord API."""
        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.content = "Existing text"
        mock_msg.edit = AsyncMock()

        updater = ThrottledStatusUpdater(mock_msg, min_interval=0.2)

        # Call with exact same content as mock_msg.content
        await updater.update("Existing text")
        await asyncio.sleep(0.05)
        self.assertEqual(mock_msg.edit.call_count, 0)

        # Update to new content
        await updater.update("New text")
        self.assertEqual(mock_msg.edit.call_count, 1)

        # Wait cooldown
        await asyncio.sleep(0.25)

        # Call with "New text" again
        await updater.update("New text")
        await asyncio.sleep(0.1)
        # Should still be 1 (deduplicated)
        self.assertEqual(mock_msg.edit.call_count, 1)

        await updater.delete()

    async def test_rate_limit_429_backoff(self):
        """Verify 429 HTTPException triggers dynamic backoff and does not raise."""
        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.content = "Initial"
        
        # Simulate 429 on first edit, then success
        resp_429 = MagicMock()
        resp_429.status = 429
        resp_429.reason = "Too Many Requests"
        err_429 = discord.HTTPException(resp_429, "rate limited")
        err_429.status = 429
        err_429.retry_after = 0.3

        mock_msg.edit = AsyncMock(side_effect=[err_429, None])

        updater = ThrottledStatusUpdater(mock_msg, min_interval=0.1)

        # First update triggers 429
        await updater.update("Prompt 1")
        self.assertEqual(mock_msg.edit.call_count, 1)
        self.assertGreater(updater._backoff_until, time.time())

        # Wait for backoff + delayed flush (0.3 + 0.5s = 0.8s)
        await asyncio.sleep(0.9)

        # Second call should have succeeded
        self.assertEqual(mock_msg.edit.call_count, 2)
        await updater.delete()

    async def test_delete_cancels_pending(self):
        """Verify delete() cancels pending flush tasks cleanly and calls message.delete()."""
        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.content = "Initial"
        mock_msg.edit = AsyncMock()
        mock_msg.delete = AsyncMock()

        updater = ThrottledStatusUpdater(mock_msg, min_interval=1.0)
        await updater.update("Step 1")
        self.assertEqual(mock_msg.edit.call_count, 1)

        # Rapidly send Step 2, which will be pending
        await updater.update("Step 2")
        self.assertIsNotNone(updater._flush_task)
        self.assertFalse(updater._flush_task.done())

        # Now delete immediately
        await updater.delete()

        # Flush task should be cancelled or done
        self.assertTrue(updater._is_closed)
        mock_msg.delete.assert_awaited_once()

        # Wait a bit to ensure Step 2 was never executed
        await asyncio.sleep(0.2)
        self.assertEqual(mock_msg.edit.call_count, 1)

    async def test_rapid_tools_simulation(self):
        """Simulate a coder running 20 fast tools in 0.5s."""
        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.content = "Init"
        mock_msg.edit = AsyncMock()

        updater = ThrottledStatusUpdater(mock_msg, min_interval=0.3)
        cb = updater.get_callback(prefix="👨‍💻 **[Coder 작업 중]**")

        # Simulate 20 tool invocations (view_file, grep_search, etc.)
        for i in range(20):
            await cb(f"tool_{i}")
            await asyncio.sleep(0.01)  # 10ms between tools

        # Immediate call handled tool_0
        self.assertEqual(mock_msg.edit.call_count, 1)
        self.assertIn("tool_0", mock_msg.edit.call_args[1].get("content"))

        # Wait for cooldown to flush trailing
        await asyncio.sleep(0.35)

        # Total edits should be at most 2 (call 0 and call 19)
        self.assertEqual(mock_msg.edit.call_count, 2)
        self.assertIn("tool_19", mock_msg.edit.call_args[1].get("content"))

        await updater.delete()

    async def test_dynamic_prefix(self):
        """Verify get_dynamic_callback dynamically resolves prefix."""
        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.content = "Init"
        mock_msg.edit = AsyncMock()

        current_mode = "mode_A"
        updater = ThrottledStatusUpdater(mock_msg, min_interval=0.1)
        cb = updater.get_dynamic_callback(lambda: f"[{current_mode}]")

        await cb("working")
        self.assertEqual(mock_msg.edit.call_count, 1)
        self.assertEqual(mock_msg.edit.call_args[1].get("content"), "[mode_A]\nworking")

        await asyncio.sleep(0.15)
        current_mode = "mode_B"
        await cb("finished")
        self.assertEqual(mock_msg.edit.call_count, 2)
        self.assertEqual(mock_msg.edit.call_args[1].get("content"), "[mode_B]\nfinished")

        await updater.delete()


if __name__ == "__main__":
    try:
        unittest.main(exit=False)
        sys.exit(0)
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
