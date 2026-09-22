import os
import sys
import tempfile
import shutil
import unittest
import asyncio
from unittest.mock import patch, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from commander import Commander, CommanderDecision


class TestCommanderComprehensive(unittest.TestCase):
    """
    Extensive test suite for Commander AI & Heuristic Dispatcher:
    - Intent classification (Question/Single vs Code/Pair vs Research)
    - Manual mode overrides (read, pair, single)
    - Boundary and edge case inputs (empty, giant prompts, special unicode)
    - Strategic E2E planning and tactical directive formulation
    - Runtime telemetry and performance stats tracking
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_commander_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_decision_to_dict(self):
        dec = CommanderDecision(
            mode="pair",
            reason="코드 수정 요청",
            briefing="3중 협업부대 출동",
            task_type="coding",
            confidence=0.95,
            source="ai",
            latency_ms=12.5
        )
        d = dec.to_dict()
        self.assertEqual(d["mode"], "pair")
        self.assertEqual(d["reason"], "코드 수정 요청")
        self.assertEqual(d["task_type"], "coding")
        self.assertEqual(d["confidence"], 0.95)
        self.assertEqual(d["latency_ms"], 12.5)

    def test_analyze_intent_manual_modes(self):
        async def _run():
            # Manual read mode
            d_read = await Commander.analyze_intent("버그 수정해줘", session_mode="read")
            self.assertEqual(d_read.mode, "read")
            self.assertEqual(d_read.source, "manual")

            # Manual pair mode
            d_pair = await Commander.analyze_intent("오늘 날씨 어때?", session_mode="pair")
            self.assertEqual(d_pair.mode, "pair")
            self.assertEqual(d_pair.source, "manual")

            # Manual single mode
            d_single = await Commander.analyze_intent("새로운 기능 개발해줘", session_mode="single")
            self.assertEqual(d_single.mode, "single")
            self.assertEqual(d_single.source, "manual")

        asyncio.run(_run())

    def test_analyze_intent_empty_and_whitespace(self):
        async def _run():
            d_empty = await Commander.analyze_intent("", session_mode="auto")
            self.assertEqual(d_empty.mode, "single")
            self.assertEqual(d_empty.reason, "빈 프롬프트")

            d_spaces = await Commander.analyze_intent("    \n\t  ", session_mode="auto")
            self.assertEqual(d_spaces.mode, "single")
            self.assertEqual(d_spaces.reason, "빈 프롬프트")

        asyncio.run(_run())

    def test_analyze_intent_heuristics_fallback(self):
        async def _run():
            with patch.object(Commander, "_run_llm_inference", return_value=None):
                # Question heuristic
                d_q = await Commander.analyze_intent("WebSocket과 HTTP 통신의 차이점이 뭐야?", session_mode="auto")
                self.assertEqual(d_q.mode, "single")
                self.assertIn(d_q.task_type, ("question", "general"))

                # Coding heuristic
                d_c = await Commander.analyze_intent("로그인 화면에 카카오 간편 로그인 버튼 추가해줘", session_mode="auto")
                self.assertEqual(d_c.mode, "pair")
                self.assertEqual(d_c.task_type, "coding")

                # Research heuristic
                d_r = await Commander.analyze_intent("서피스 프로 7과 서피스 프로 8 중고 시세 및 스펙 비교 리서치해줘", session_mode="auto")
                self.assertEqual(d_r.mode, "pair")
                self.assertEqual(d_r.task_type, "research")

        asyncio.run(_run())

    def test_analyze_intent_extreme_length_prompt(self):
        async def _run():
            huge_prompt = "코드 수정해줘: " + ("const a = 1;\n" * 1000)
            with patch.object(Commander, "_run_llm_inference", return_value=None):
                d = await Commander.analyze_intent(huge_prompt, session_mode="auto")
                self.assertIn(d.mode, ("pair", "single"))
                self.assertGreater(d.latency_ms, 0)

        asyncio.run(_run())

    def test_parse_json_decision_valid_and_markdown(self):
        # Clean json
        raw = '{"mode": "pair", "reason": "UI 기능 추가", "task_type": "coding", "briefing": "협업 출동", "confidence": 0.9}'
        d = Commander._parse_json_decision(raw)
        self.assertIsNotNone(d)
        self.assertEqual(d.mode, "pair")
        self.assertEqual(d.task_type, "coding")

        # Markdown fenced json
        raw_md = 'Here is the analysis:\n```json\n{"mode": "single", "reason": "단순 질문", "task_type": "question", "confidence": 0.85}\n```'
        d_md = Commander._parse_json_decision(raw_md)
        self.assertIsNotNone(d_md)
        self.assertEqual(d_md.mode, "single")
        self.assertEqual(d_md.task_type, "question")

        # Corrupted / non-json string
        raw_bad = "I think you should use pair mode because of reasons."
        d_bad = Commander._parse_json_decision(raw_bad)
        self.assertIsNone(d_bad)

    def test_parse_json_decision_confidence_clamping(self):
        # 1. Negative confidence -> must clamp to 0.0
        raw_neg = '{"mode": "pair", "reason": "test", "confidence": -99.5}'
        d_neg = Commander._parse_json_decision(raw_neg)
        self.assertIsNotNone(d_neg)
        self.assertEqual(d_neg.confidence, 0.0)

        # 2. Over-1.0 confidence -> must clamp to 1.0
        raw_high = '{"mode": "single", "reason": "test", "confidence": 125.0}'
        d_high = Commander._parse_json_decision(raw_high)
        self.assertIsNotNone(d_high)
        self.assertEqual(d_high.confidence, 1.0)

        # 3. Normal confidence
        raw_norm = '{"mode": "pair", "reason": "test", "confidence": 0.85}'
        d_norm = Commander._parse_json_decision(raw_norm)
        self.assertIsNotNone(d_norm)
        self.assertEqual(d_norm.confidence, 0.85)

    def test_plan_e2e_operation_heuristic_priorities(self):
        async def _run():
            # 1. Overflow bug
            plan_overflow = await Commander.plan_e2e_operation(
                target_url="https://test.web.app",
                mode="evolve",
                viewport_tests=["390px"],
                horizontal_overflow=True,
                broken_images_count=0,
                interactions=[],
                console_logs=[],
                cycle=1,
                max_cycles=20,
                workspace=self.test_dir
            )
            self.assertIsNotNone(plan_overflow.get("priority_target"))
            self.assertLess(plan_overflow["health_score"], 90)

            # 2. Broken images bug
            plan_img = await Commander.plan_e2e_operation(
                target_url="https://test.web.app",
                mode="evolve",
                viewport_tests=["390px"],
                horizontal_overflow=False,
                broken_images_count=3,
                interactions=[],
                console_logs=[],
                cycle=1,
                max_cycles=20,
                workspace=self.test_dir
            )
            self.assertIsNotNone(plan_img.get("priority_target"))

            # 3. Console error bug
            plan_err = await Commander.plan_e2e_operation(
                target_url="https://test.web.app",
                mode="evolve",
                viewport_tests=["390px"],
                horizontal_overflow=False,
                broken_images_count=0,
                interactions=[],
                console_logs=["[ERROR] Uncaught TypeError: Cannot read property 'map' of undefined"],
                cycle=1,
                max_cycles=20,
                workspace=self.test_dir
            )
            self.assertIsNotNone(plan_err.get("priority_target"))

        asyncio.run(_run())

    def test_commander_stats_recording(self):
        initial_stats = Commander.get_stats()
        initial_total = initial_stats["total_analyzed"]

        dec = CommanderDecision(
            mode="pair",
            reason="테스트",
            briefing="브리핑",
            task_type="coding",
            confidence=0.9,
            source="ai",
            latency_ms=10.0
        )
        Commander._record_stats(dec)

        new_stats = Commander.get_stats()
        self.assertEqual(new_stats["total_analyzed"], initial_total + 1)
        self.assertIsNotNone(new_stats["last_decision"])

    def test_calculate_health_score_matrix(self):
        # 1. Perfect 100% Score
        prd_cov = {"has_prd": True, "completion_ratio": 1.0, "completed_count": 5, "total_requirements": 5}
        test_cases = [{"status": "PASS"}, {"status": "PASS"}, {"status": "PASS"}]
        score, breakdown = Commander.calculate_health_score(
            prd_coverage=prd_cov,
            viewport_tests=["390px", "1440px"],
            horizontal_overflow=False,
            broken_images_count=0,
            interactions=["새로고침 복원력 확인", "카오스 스트레스 통과", "버튼 인터랙션"],
            console_logs=[],
            network_errors=[],
            test_cases=test_cases,
            dom_insights={"smallTouchTargets": 0, "totalButtons": 10}
        )
        self.assertEqual(score, 100)
        self.assertEqual(breakdown["prd"]["score"], 30)
        self.assertEqual(breakdown["e2e_tc"]["score"], 25)
        self.assertEqual(breakdown["stability"]["score"], 20)
        self.assertEqual(breakdown["ux"]["score"], 15)
        self.assertEqual(breakdown["resilience"]["score"], 10)

        # 2. Defect Penalties (Overflow, Console Error, Broken Img, Pending PRD)
        prd_half = {"has_prd": True, "completion_ratio": 0.5, "completed_count": 1, "total_requirements": 2}
        tc_warn = [{"status": "PASS"}, {"status": "WARN"}, {"status": "FAIL"}]
        score_def, bd_def = Commander.calculate_health_score(
            prd_coverage=prd_half,
            viewport_tests=["390px"],
            horizontal_overflow=True,
            broken_images_count=2,
            interactions=["버튼 클릭"],
            console_logs=["[ERROR] Failed to load resource", "[WARNING] Deprecated API"],
            network_errors=["[HTTP 404] /missing.png"],
            test_cases=tc_warn,
            dom_insights={"smallTouchTargets": 5, "totalButtons": 10}
        )
        self.assertLess(score_def, 70)
        self.assertEqual(bd_def["prd"]["score"], 15)  # 50% of 30 = 15
        self.assertEqual(bd_def["ux"]["overflow"], True)
        self.assertEqual(bd_def["stability"]["console_errors"], 1)


if __name__ == "__main__":
    unittest.main()

