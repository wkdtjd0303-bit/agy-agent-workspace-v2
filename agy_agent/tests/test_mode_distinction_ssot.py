#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
동작 모드 구분 SSOT 회귀 테스트
================================
사용자 보고: `!mode 2+` (8중 다중)로 세팅했는데 실제로는 `!mode 2` (3중:
Coder → QA → Auditor)로 동작했다.

원인은 두 가지였다.
  1. `+` 모드(pair_quad/mode3_plus)가 "심층 리서치"로 분류되는 프롬프트를 만나면
     확장 QA가 전혀 없는 ResearchRunner(3중)로 조용히 강등되었다.
  2. 모드 별칭/파이프라인 판정이 여러 모듈에 하드코딩되어 표시와 실행이 달라질 수 있었다.

본 테스트는 mode_registry 를 단일 진실 공급원으로 삼아
  - `!mode 2` / `!mode 2+` / `!mode 3` / `!mode 3+` 의 엄격한 구분,
  - 활성 확장 QA 0대일 때의 정직한 경고(무음 붕괴 금지),
  - 리서치 프롬프트에서도 `+` 모드의 확장 QA 파이프라인이 유지됨
을 고정한다.
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import mode_registry
from session_manager import ChannelSession


class TestModeRegistrySSOT(unittest.TestCase):
    def test_alias_normalization_strict_separation(self):
        cases = {
            "2": "pair",
            "pair": "pair",
            "2+": "pair_quad",
            "2plus": "pair_quad",
            "2플러스": "pair_quad",
            "pair+": "pair_quad",
            "pair_quad": "pair_quad",
            "3": "mode3",
            "mode3": "mode3",
            "자율3": "mode3",
            "3+": "mode3_plus",
            "3plus": "mode3_plus",
            "mode3+": "mode3_plus",
            "mode3_plus": "mode3_plus",
            # classify_mode 와 set_mode 사이의 과거 해석 차이 제거 확인
            "auto_multi": "mode3_plus",
            "multi_auto": "mode3_plus",
            "auto_trio": "mode3",
            "trio_auto": "mode3",
            # 레거시 무중단 호환
            "1+": "single",
            "consultant": "single",
            "qa2": "single_qa2",
        }
        for raw, expected in cases.items():
            self.assertEqual(mode_registry.normalize_mode(raw), expected, f"alias={raw}")

    def test_multi_vs_core_predicates(self):
        # 2+ / 3+ 만 다중 확장 QA 파이프라인
        self.assertTrue(mode_registry.is_multi_qa_mode("2+"))
        self.assertTrue(mode_registry.is_multi_qa_mode("3+"))
        self.assertFalse(mode_registry.is_multi_qa_mode("2"))
        self.assertFalse(mode_registry.is_multi_qa_mode("3"))
        # 3 / 3+ 만 무인 자율 순환
        self.assertTrue(mode_registry.is_autonomous_mode("3"))
        self.assertTrue(mode_registry.is_autonomous_mode("3+"))
        self.assertFalse(mode_registry.is_autonomous_mode("2"))
        self.assertFalse(mode_registry.is_autonomous_mode("2+"))
        # 2/2+/3/3+ 는 모두 다중 에이전트, 1/qa2/read 는 아님
        for raw in ("2", "2+", "3", "3+"):
            self.assertTrue(mode_registry.is_multi_agent_mode(raw), f"multi_agent={raw}")
        for raw in ("1", "qa2", "read", "single"):
            self.assertFalse(mode_registry.is_multi_agent_mode(raw), f"multi_agent={raw}")

    def test_classify_mode_uses_registry_ssot(self):
        """classify_mode / get_mode_description 도 registry 별칭을 그대로 반영해야 한다.

        (기존에는 각 함수가 자체 튜플을 하드코딩해 `2+` 계열 드리프트가 발생했다.)
        """
        from mode_classifier import classify_mode, get_mode_description
        # registry 만 아는 별칭도 모드 분류에 반영되어야 한다.
        cases = {
            "2+": "pair_quad",
            "pair_plus": "pair_quad",
            "quad_pair": "pair_quad",
            "3+": "mode3_plus",
            "auto_multi": "mode3_plus",
            "multi_auto": "mode3_plus",
            "auto_trio": "mode3",
            "trio_auto": "mode3",
        }
        for raw, expected in cases.items():
            mode, _reason = classify_mode("일반 개발 작업", raw)
            self.assertEqual(mode, expected, f"classify_mode alias={raw}")
        # 표시(설명) 문자열도 2/2+/3/3+ 가 서로 달라야 한다.
        descs = {m: get_mode_description(m) for m in ("2", "2+", "3", "3+")}
        self.assertEqual(len(set(descs.values())), 4, f"모드 설명이 겹침: {descs}")
        self.assertIn("!mode 2+", descs["2+"])
        self.assertIn("!mode 3+", descs["3+"])
        self.assertIn("!mode 2)", descs["2"])
        self.assertIn("!mode 3)", descs["3"])

    def test_session_set_mode_uses_registry(self):
        sess = ChannelSession(channel_id=1, workspace="/tmp")
        for raw in ("2+", "2plus", "pair+"):
            sess.set_mode(raw)
            self.assertEqual(sess.mode, "pair_quad")
        for raw in ("3+", "3plus", "mode3+"):
            sess.set_mode(raw)
            self.assertEqual(sess.mode, "mode3_plus")
        sess.set_mode("2")
        self.assertEqual(sess.mode, "pair")
        sess.set_mode("3")
        self.assertEqual(sess.mode, "mode3")


class TestResolveModeDisplayEqualsExecution(unittest.TestCase):
    def _session(self, mode, qas):
        sess = ChannelSession(channel_id=2, workspace="/tmp")
        sess.set_mode(mode)
        sess.get_active_extended_qas = lambda all_configs=None: list(qas)
        return sess

    def test_pair_plus_reports_multi_agents(self):
        qas = [{"index": i, "name": f"qa{i}"} for i in (2, 3, 4, 5, 6)]
        info = mode_registry.resolve_mode("2+", self._session("2+", qas))
        self.assertTrue(info["is_multi_qa"])
        self.assertFalse(info["is_autonomous"])
        self.assertEqual(info["extended_count"], 5)
        self.assertEqual(info["agent_count"], 8)
        self.assertEqual(mode_registry.describe_pipeline("2+", self._session("2+", qas)), "8중 다중")
        self.assertIn("qa2", info["chain"])
        self.assertEqual(info["warning"], "")

    def test_plain_pair_reports_three_agents(self):
        qas = [{"index": i, "name": f"qa{i}"} for i in (2, 3, 4, 5, 6)]
        info = mode_registry.resolve_mode("2", self._session("2", qas))
        self.assertFalse(info["is_multi_qa"])
        self.assertEqual(info["extended_count"], 0)
        self.assertEqual(info["agent_count"], 3)
        self.assertEqual(info["chain"], "Coder ➔ qa1 ➔ Auditor")

    def test_multi_mode_with_no_extended_qa_warns_instead_of_silent_collapse(self):
        info = mode_registry.resolve_mode("2+", self._session("2+", []))
        self.assertTrue(info["is_multi_qa"])
        self.assertEqual(info["extended_count"], 0)
        # 조용히 3중으로 붕괴하되, 반드시 사용자 경고를 남긴다.
        self.assertEqual(info["agent_count"], 3)
        self.assertTrue(info["warning"])
        self.assertIn("무결성 경고", info["warning"])

    def test_mode3_plus_is_multi_but_mode3_is_not(self):
        qas = [{"index": 2, "name": "qa2"}]
        info_3 = mode_registry.resolve_mode("3", self._session("3", qas))
        info_3p = mode_registry.resolve_mode("3+", self._session("3+", qas))
        self.assertFalse(info_3["is_multi_qa"])
        self.assertEqual(info_3["agent_count"], 3)
        self.assertTrue(info_3p["is_multi_qa"])
        self.assertEqual(info_3p["agent_count"], 4)


class TestResearchDiversionRespectsMultiQaMode(unittest.IsolatedAsyncioTestCase):
    """
    `!mode 2+` + 리서치성 프롬프트 → ResearchRunner(확장 QA 없음)로 강등되면 안 된다.
    """

    def _make_channel(self):
        ch = AsyncMock()
        ch.id = 424242
        ch.guild = None
        ch.send = AsyncMock()
        ch.typing = MagicMock()
        ch.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        ch.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        ch.reply = AsyncMock()
        return ch

    async def _run(self, mode, research_prompt):
        import trio_agent_runner
        from trio_agent_runner import TrioAgentRunner

        sess = ChannelSession(channel_id=424242, workspace="/tmp")
        sess.set_mode(mode)
        # 5대 확장 QA 활성 (pair_quad 기준)
        qas = [{"index": i, "name": f"qa{i}", "model": "dummy", "engine": "opencode"} for i in (2, 3, 4, 5, 6)]
        sess.get_active_extended_qas = lambda all_configs=None: list(qas)

        ch = self._make_channel()
        research_mock = AsyncMock(return_value="RESEARCH_RUNNER_INVOKED")

        with patch("config.get_extended_qa_configs", return_value=qas), \
             patch("research_runner.ResearchRunner.run_pipeline", research_mock), \
             patch.object(TrioAgentRunner, "is_system_error", staticmethod(lambda _t: True)), \
             patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as mock_prompt, \
             patch("trio_agent_runner.safe_send_discord_message", new_callable=AsyncMock), \
             patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock):
            mock_prompt.return_value = "🚨 Coder short-circuit"
            try:
                await TrioAgentRunner.run_pipeline(
                    session=sess,
                    prompt=research_prompt,
                    main_channel=ch,
                    max_rounds=1,
                )
            except Exception:
                # Coder 단계로 진입했음을 의미 (리서치 강등이 일어나지 않음)
                pass

        return research_mock

    async def test_pair_plus_research_prompt_keeps_multi_qa_pipeline(self):
        research_prompt = "우리 시스템의 다중 에이전트 구조를 분석하고 개선 방향을 조사해줘"
        from mode_classifier import classify_task_type
        self.assertEqual(classify_task_type(research_prompt), "research")

        research_mock = await self._run("2+", research_prompt)
        self.assertFalse(
            research_mock.called,
            "pair_quad(!mode 2+) + research 프롬프트에서 ResearchRunner 로 강등되어 확장 QA가 누락됨",
        )

    async def test_plain_pair_research_prompt_still_uses_research_runner(self):
        research_prompt = "우리 시스템의 다중 에이전트 구조를 분석하고 개선 방향을 조사해줘"
        research_mock = await self._run("2", research_prompt)
        self.assertTrue(
            research_mock.called,
            "!mode 2(비-플러스)는 리서치 프롬프트를 ResearchRunner 로 위임해야 한다",
        )


if __name__ == "__main__":
    unittest.main()
