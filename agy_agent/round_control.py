"""Round-cap continuation controller.

[배경/요구사항]
멀티 에이전트 파이프라인(3중/다중 협업, 심층 리서치 등)은 설정된 라운드 상한에
도달하면 지금까지 아무 안내 없이 곧바로 종료(스탑)했다. 사용자는 상한 도달 시
"20회가 끝났습니다. 이어서 진행하시겠습니까?" 라고 Discord 예/아니요 버튼으로
물어보고, '예'를 누르면 다음 라운드 배치(batch)를 이어서 진행하기를 원한다.

[구현]
- :func:`continual_round_sequence` 는 ``range(1, max_rounds + 1)`` 를 그대로
  대체하는 async generator 다. 상한에 도달하면 사용자에게 계속 여부를 묻고,
  승인되면 상한을 ``batch_size`` 만큼 연장해 계속 yield 한다. 거절/타임아웃/실패
  시에는 기존 ``range`` 와 동일하게 시퀀스를 종료한다.
- :func:`ask_round_continuation` 은 예/아니요 버튼 View 를 띄우고 응답을 기다린다.
- 단위 테스트(PYTEST_CURRENT_TEST)에서는 절대 사람 입력을 기다리지 않도록
  즉시 ``False`` 를 반환한다(무한 대기/행 방지). ``AGY_FORCE_ROUND_CONTINUE_PROMPT=1``
  로 명시적으로 강제할 수 있다.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Awaitable, Callable, Optional, Tuple

import discord

import config

logger = logging.getLogger("RoundControl")


class RoundContinueView(discord.ui.View):
    """예/아니요 버튼 View. 결과는 ``value`` 에 True/False 로 담긴다."""

    def __init__(self, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.value: Optional[bool] = None

    @discord.ui.button(label="예 (계속 진행)", style=discord.ButtonStyle.success)
    async def _yes(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: D401
        self.value = True
        try:
            await interaction.response.defer()
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="아니요 (중단)", style=discord.ButtonStyle.secondary)
    async def _no(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa: D401
        self.value = False
        try:
            await interaction.response.defer()
        except Exception:
            pass
        self.stop()


def _continuation_disabled() -> bool:
    """상한 도달 확인 프롬프트를 건너뛸지 여부."""
    try:
        if not getattr(config, "ROUND_CONTINUE_ENABLED", True):
            return True
    except Exception:
        pass
    # 단위 테스트는 사람 상호작용을 기다리면 안 된다.
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("AGY_FORCE_ROUND_CONTINUE_PROMPT") != "1":
        return True
    return False


async def ask_round_continuation(
    channel,
    *,
    completed_rounds: int,
    batch_size: int,
    timeout_sec: Optional[float] = None,
) -> bool:
    """라운드 상한 도달 시 사용자에게 계속 진행 여부를 예/아니요로 묻는다.

    Returns:
        True  → '예' 선택 (계속 진행)
        False → '아니요' 선택 / 타임아웃 / 전송 실패 / 테스트 모드
    """
    if _continuation_disabled():
        return False
    if channel is None or not hasattr(channel, "send"):
        return False
    if timeout_sec is None:
        try:
            timeout_sec = float(getattr(config, "ROUND_CONTINUE_TIMEOUT_SEC", 300))
        except Exception:
            timeout_sec = 300.0
    batch_size = max(1, int(batch_size or 1))

    view = RoundContinueView(timeout=timeout_sec)
    prompt_text = (
        f"🔁 **[최대 라운드({completed_rounds}회) 도달]**\n\n"
        f"**{completed_rounds}회가 끝났습니다. 이어서 진행하시겠습니까?**\n"
        f"• **예** → 다음 **{batch_size}회** 라운드를 즉시 이어서 진행합니다.\n"
        f"• **아니요** → 현재 상태에서 안전하게 종료합니다.\n"
        f"*(응답이 없으면 {int(timeout_sec)}초 후 자동 종료됩니다)*"
    )

    sent = None
    try:
        sent = await channel.send(prompt_text, view=view)
    except Exception as e:
        logger.debug(f"Round continuation prompt could not be sent: {e}")
        return False

    try:
        await asyncio.wait_for(view.wait(), timeout=timeout_sec + 5.0)
    except asyncio.TimeoutError:
        logger.info(f"Round continuation prompt timed out after {timeout_sec:.0f}s — stopping safely.")
    except Exception as e:
        logger.debug(f"Round continuation wait error: {e}")

    # 버튼 비활성화(재클릭 방지)
    try:
        for child in view.children:
            try:
                child.disabled = True
            except Exception:
                pass
        if sent is not None:
            await sent.edit(view=view)
    except Exception:
        pass

    return view.value is True


async def continual_round_sequence(
    start: int,
    cap: int,
    *,
    channel=None,
    batch_size: Optional[int] = None,
    timeout_sec: Optional[float] = None,
    ask_fn: Optional[Callable[[], Awaitable[bool]]] = None,
) -> AsyncIterator[Tuple[int, int]]:
    """``(round_num, current_cap)`` 튜플을 yield 하는 async generator.

    ``range(start, cap + 1)`` 와 동일하게 동작하되, 상한 도달 시 ``ask_fn``(또는
    :func:`ask_round_continuation`)으로 계속 여부를 확인한다. 승인되면 상한을
    ``batch_size`` 만큼 연장하고 계속 진행하며, 거절되면 종료한다.
    """
    batch = int(batch_size or cap or 1)
    if batch < 1:
        batch = 1
    current_cap = int(cap)
    round_num = int(start)

    while True:
        if round_num > current_cap:
            if ask_fn is not None:
                try:
                    proceed = bool(await ask_fn())
                except Exception as e:
                    logger.debug(f"Round continuation ask_fn error: {e}")
                    proceed = False
            else:
                try:
                    proceed = await ask_round_continuation(
                        channel,
                        completed_rounds=current_cap,
                        batch_size=batch,
                        timeout_sec=timeout_sec,
                    )
                except Exception as e:
                    logger.debug(f"Round continuation ask error: {e}")
                    proceed = False
            if not proceed:
                return
            current_cap += batch
            logger.info(f"Round cap extended {current_cap - batch} → {current_cap} (user chose to continue).")

        yield round_num, current_cap
        round_num += 1
