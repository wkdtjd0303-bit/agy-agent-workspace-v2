#!/usr/bin/env python3
"""
Unit tests for Code/Signal-Driven Gap Discovery + PRD-as-Journal.

Validates the mandate: "그때그때 코드를 읽으면서 필요한 개선을 찾아가고,
PRD는 이전에 이런 걸 했다 하는 일기장 용도".

Covered:
1. GapSignalScanner detects real disk signals (test failures, lint errors, crashes, stubs).
2. Signals are ranked by Value x Risk x Cost.
3. A clean workspace yields no signals.
4. RoadmapManager PRD journal is append-only and idempotent.
5. Commander prioritizes a high-severity disk signal over capability progression.
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from gap_signal_scanner import GapSignalScanner, GapSignal
from roadmap_manager import RoadmapManager
from commander import Commander


class TestGapSignalAndPrdJournal(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_gap_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, content: str):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # ── Scanner probes ──────────────────────────────────────────────────────
    def test_detects_test_failures(self):
        self._write(
            "app/build/test-results/testDebugUnitTest/TEST-app.xml",
            '<testsuites><testsuite name="app" tests="3" failures="1" errors="0"></testsuite></testsuites>',
        )
        signals = GapSignalScanner.scan(self.tmp)
        test_signals = [s for s in signals if s.area == "test"]
        self.assertTrue(test_signals)
        self.assertEqual(test_signals[0].severity, "high")

    def test_detects_lint_errors(self):
        self._write(
            "app/build/reports/lint-results-debug.xml",
            '<issues><issue id="X" severity="Error"/><issue id="Y" severity="Warning"/></issues>',
        )
        signals = GapSignalScanner.scan(self.tmp)
        lint = [s for s in signals if s.area == "lint"]
        self.assertTrue(lint)
        self.assertEqual(lint[0].severity, "high")
        self.assertIn("errors=1", lint[0].evidence)

    def test_detects_crash_log(self):
        self._write("reports/runtime.log", "E/AndroidRuntime: FATAL EXCEPTION: main\njava.lang.NullPointerException")
        signals = GapSignalScanner.scan(self.tmp)
        crash = [s for s in signals if s.area == "crash"]
        self.assertTrue(crash)
        self.assertEqual(crash[0].severity, "high")

    def test_detects_stub_markers(self):
        self._write("src/engine.py", "def run():\n    # TODO: implement\n    raise NotImplementedError\n")
        signals = GapSignalScanner.scan(self.tmp)
        stub = [s for s in signals if s.area == "stub"]
        self.assertTrue(stub)

    def test_clean_workspace_has_no_signals(self):
        self.assertEqual(GapSignalScanner.scan(self.tmp), [])

    def test_ranking_prefers_high_value_low_cost(self):
        low = GapSignal(area="stub", title="low", detail="", evidence="", severity="low",
                        value=0.3, risk=0.6, cost=0.6)
        high = GapSignal(area="crash", title="high", detail="", evidence="", severity="high",
                         value=0.95, risk=0.2, cost=0.3)
        ranked = GapSignalScanner.rank([low, high])
        self.assertEqual(ranked[0].title, "high")
        self.assertGreater(high.priority, low.priority)

    # ── PRD journal ─────────────────────────────────────────────────────────
    def test_prd_journal_is_append_only_and_idempotent(self):
        with open(os.path.join(self.tmp, "PRD.md"), "w", encoding="utf-8") as f:
            f.write("# PRD\n\n## 1. Overview\nOriginal plan body.\n")

        rm = RoadmapManager(self.tmp)
        self.assertTrue(rm.append_prd_journal_entry("v1.0", "코어 엔진 구축", evidence="build OK"))
        # Second identical entry must be ignored.
        self.assertFalse(rm.append_prd_journal_entry("v1.0", "코어 엔진 구축", evidence="build OK"))
        self.assertTrue(rm.append_prd_journal_entry("v1.1", "검색 기능", evidence="tests 12/12"))

        with open(os.path.join(self.tmp, "PRD.md"), "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(RoadmapManager.PRD_JOURNAL_HEADER, content)
        self.assertIn("Original plan body.", content)   # never rewrites history
        self.assertEqual(content.count("`v1.0`"), 1)
        self.assertIn("`v1.1`", content)

    # ── Commander prioritization ────────────────────────────────────────────
    async def test_commander_prioritizes_high_signal_over_capability(self):
        self._write("src/main.py", "print('app')\n")
        self._write("reports/crash.log", "FATAL EXCEPTION: main")

        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=None)):
            plan = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal="python CLI 도구를 만들어줘",
                workspace=self.tmp,
                domain="cli_agent",
            )

        self.assertEqual(plan.source, "signal")
        self.assertIn("크래시", plan.title)

    async def test_commander_uses_capability_when_no_high_signal(self):
        self._write("src/main.py", "print('app')\n")

        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=None)):
            plan = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal="python CLI 도구를 만들어줘",
                workspace=self.tmp,
                domain="cli_agent",
            )

        self.assertNotEqual(plan.source, "signal")


if __name__ == "__main__":
    unittest.main()
