#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""라운드 상한 도달 시 '이어서 진행하시겠습니까?' 예/아니요 확인 로직 단위 테스트."""
import asyncio
import os

import config
from round_control import ask_round_continuation, continual_round_sequence


def _collect(cap, *, batch_size=3, ask_fn=None):
    async def _run():
        out = []
        async for rn, cur_cap in continual_round_sequence(
            1, cap, batch_size=batch_size, ask_fn=ask_fn
        ):
            out.append((rn, cur_cap))
        return out

    return asyncio.run(_run())


def test_sequence_matches_range_when_under_cap():
    """상한 미도달 시에는 range(1, cap+1) 와 완전히 동일하게 동작해야 한다."""
    out = _collect(3, ask_fn=None)
    assert out == [(1, 3), (2, 3), (3, 3)]


def test_sequence_stops_when_user_declines():
    async def deny():
        return False

    out = _collect(2, batch_size=2, ask_fn=deny)
    assert out == [(1, 2), (2, 2)]


def test_sequence_extends_when_user_approves():
    calls = {"n": 0}

    async def approve_once():
        calls["n"] += 1
        return calls["n"] == 1  # 첫 배치만 승인, 두 번째 배치 후 종료

    out = _collect(3, batch_size=3, ask_fn=approve_once)
    assert out == [
        (1, 3), (2, 3), (3, 3),
        (4, 6), (5, 6), (6, 6),
    ]
    assert calls["n"] == 2


def test_sequence_ask_failure_is_fail_safe():
    async def boom():
        raise RuntimeError("interaction failed")

    out = _collect(2, batch_size=2, ask_fn=boom)
    assert out == [(1, 2), (2, 2)]


def test_ask_returns_false_in_test_environment(monkeypatch):
    """단위 테스트에서는 절대 사람 입력을 기다리지 않고 즉시 False."""
    monkeypatch.delenv("AGY_FORCE_ROUND_CONTINUE_PROMPT", raising=False)
    result = asyncio.run(
        ask_round_continuation(object(), completed_rounds=20, batch_size=20)
    )
    assert result is False


def test_research_round_cap_is_twenty_by_default():
    """환경변수 미설정 시 심층 리서치/협업 상한 기본값은 20회."""
    if "RESEARCH_MAX_ROUNDS" not in os.environ:
        assert config.RESEARCH_MAX_ROUNDS == 20
    if "PAIR_MAX_ROUNDS" not in os.environ:
        assert config.PAIR_MAX_ROUNDS == 20
    if "QA_MAX_ROUNDS" not in os.environ:
        assert config.QA_MAX_ROUNDS == 20
    assert config.ROUND_CONTINUE_ENABLED is True
