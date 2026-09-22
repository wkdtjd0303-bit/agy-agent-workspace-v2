"""
[DEPRECATED] Self-Healing System Module.
This feature has been decommissioned as requested.
All agent repair pipelines and admin channel auto-triggering have been removed.
This module now serves as a safe dormant stub to preserve interface compatibility.
"""
import time
import logging
from typing import Optional, Dict, Callable, Any
import discord

from config import ADMIN_CHANNEL_ID, ENABLE_SELF_HEALING
from agent_runner import AgentRunner
from discord_utils import safe_send_discord_message

logger = logging.getLogger("SelfHealing")

class SelfHealingManager:
    """
    Decommissioned stub for SelfHealingManager.
    Self-healing is permanently disabled (enabled = False).
    """
    def __init__(
        self,
        session_manager: Any = None,
        qa_bot: Optional[discord.Client] = None,
        auditor_bot: Optional[discord.Client] = None
    ):
        self.session_manager = session_manager
        self.qa_bot = qa_bot
        self.auditor_bot = auditor_bot
        self.enabled = False  # Decommissioned
        self.admin_channel_id = ADMIN_CHANNEL_ID
        self._retry_counts: Dict[str, int] = {}
        self._error_history: Dict[int, list] = {}
        self._max_errors_per_window = 10
        self._window_seconds = 60
        self._dispatch_callback: Optional[Callable] = None

    def set_on_complete_callback(self, cb: Callable):
        self._dispatch_callback = cb

    def _get_prompt_key(self, channel_id: int, prompt: str) -> str:
        clean_prompt = str(prompt or "").strip()[:100]
        return f"{channel_id}:{clean_prompt}"

    def is_flood_limited(self, channel_id: int) -> bool:
        now = time.time()
        timestamps = self._error_history.get(channel_id, [])
        timestamps = [t for t in timestamps if now - t < self._window_seconds]
        self._error_history[channel_id] = timestamps
        return len(timestamps) >= self._max_errors_per_window

    def record_error(self, channel_id: int):
        now = time.time()
        if channel_id not in self._error_history:
            self._error_history[channel_id] = []
        self._error_history[channel_id].append(now)

    def clear_channel_error(self, channel_id: int, prompt: Optional[str] = None):
        if prompt:
            key = self._get_prompt_key(channel_id, prompt)
            self._retry_counts.pop(key, None)
        else:
            to_remove = [k for k in self._retry_counts if k.startswith(f"{channel_id}:")]
            for k in to_remove:
                self._retry_counts.pop(k, None)
        self._error_history.pop(channel_id, None)

    async def handle_error(
        self,
        bot: discord.Client,
        failed_channel: discord.abc.Messageable,
        failed_prompt: str,
        error_detail: str
    ) -> bool:
        # Self-healing is decommissioned — unconditionally return False
        return False

    async def _run_self_healing_and_resume(self, *args, **kwargs):
        # Decommissioned no-op
        return
