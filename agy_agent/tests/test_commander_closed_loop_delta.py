#!/usr/bin/env python3
"""
Unit tests for Commander Closed-Loop Delta Analysis & 4-Stage Thinking Protocol.
Tests:
1. Milestone5W1HPlan delta_analysis field and Discord formatting (Embed & Message).
2. Heuristic adaptive planning automatically populates delta_analysis based on disk reality.
3. Async adaptive planning parses delta_analysis from LLM JSON response.
4. "사과 피하기 게임" (Avoid Falling Apples Game) 4-phase closed-loop progression verification.
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

from commander import Commander, Milestone5W1HPlan


class TestCommanderClosedLoopDelta(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_closed_loop_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_milestone_delta_analysis_discord_formatting(self):
        """Milestone5W1HPlan includes delta_analysis in both text message and embed."""
        plan = Milestone5W1HPlan(
            version="v1.2",
            title="사과 스폰 및 자연 낙하 물리 엔진",
            domain="game",
            why="플레이어 이동이 검증되었으므로 상단에서 떨어지는 사과 오브젝트를 구현합니다.",
            what=["사과 엔티티 풀링", "Y축 등속 낙하 물리", "화면 하단 이탈 시 메모리 해제"],
            how="requestAnimationFrame 델타 타임 기반 위치 갱신",
            who_where="Coder: apple.js, game.js",
            when_acceptance=["사과 객체 y좌표 프레임당 증가 검증", "메모리 누수 0건"],
            delta_analysis="직전 상태: 플레이어 좌우 이동 완료. 현재 결손: 낙하 장애물 엔티티 부재."
        )

        msg = plan.to_discord_message()
        self.assertIn("Delta 분석 (닫힌 루프 제어)", msg)
        self.assertIn("현재 결손: 낙하 장애물 엔티티 부재", msg)

        embed = plan.to_discord_embed()
        # If discord library is available or mock dict
        if hasattr(embed, "fields"):
            field_names = [f.name for f in embed.fields]
            self.assertIn("🔍 Delta 분석 (닫힌 루프 제어)", field_names)

    def test_heuristic_adaptive_plan_populates_delta_analysis(self):
        """Adaptive heuristic plan populates delta_analysis grounded in codebase reality."""
        goal = "사과 피하기 게임을 만들어줘"
        reality = Commander.inspect_codebase_reality(self.test_dir)
        plan = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal,
            reality=reality,
            domain="game",
            target_version="v1.0",
            workspace=self.test_dir
        )

        self.assertTrue(bool(plan.delta_analysis))
        self.assertIn("디스크 실사 분석 기반 결손", plan.delta_analysis)
        self.assertIn("v1.0", plan.version)

    async def test_async_adaptive_plan_parses_delta_analysis(self):
        """Async adaptive planner parses delta_analysis from model response."""
        mock_llm_json = """{
            "title": "사과 낙하 물리 및 스포너 시스템",
            "delta_analysis": "플레이어 제어는 완료되었으나 화면에 떨어지는 사과 오브젝트가 없는 결손 해소",
            "why": "피해야 할 장애물이 있어야 게임 루프가 성립됩니다.",
            "what": [
                "무작위 X좌표 사과 생성 스포너",
                "초당 300px 등속 낙하 물리",
                "바닥 도달 시 재활용 풀링"
            ],
            "how": "오브젝트 풀 패턴 및 델타타임 오일러 적분",
            "who_where": "src/apple.js, src/game.js",
            "when_acceptance": [
                "사과 생성 및 Y좌표 증가 단위 테스트 통과",
                "화면 밖 이탈 시 배열 누수 방지"
            ],
            "quality_contracts": [
                {"id": "QC-G-01", "category": "gameplay", "description": "사과 낙하 속도", "verification_rule": "speed > 0"}
            ]
        }"""

        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=mock_llm_json)):
            plan = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal="사과 피하기 게임 만들어줘",
                workspace=self.test_dir,
                domain="game"
            )

            self.assertEqual(plan.title, "사과 낙하 물리 및 스포너 시스템")
            self.assertEqual(plan.delta_analysis, "플레이어 제어는 완료되었으나 화면에 떨어지는 사과 오브젝트가 없는 결손 해소")
            self.assertEqual(len(plan.what), 3)
            self.assertEqual(plan.quality_contracts[0]["id"], "QC-G-01")

    async def test_async_adaptive_plan_parses_thinking_tag(self):
        """Async adaptive planner parses structured <thinking> tag before JSON."""
        mock_raw_llm = """<thinking>
1. Observation & Ground Truth: Coder implemented canvas and player movement. Exit code 0, 2 tests passed.
2. Delta & Blocker Analysis: Falling apple obstacle spawner is missing on disk.
3. Single-Action Scope Guard: Isolate to apple spawn and fall physics; prune sound and particle effects.
4. Verification Contract: Apple y-coordinate increment > 0, interval spawn <= 2s.
</thinking>
{
    "title": "사과 낙하 물리 및 스포너 시스템",
    "delta_analysis": "플레이어 이동 후 낙하 오브젝트 부재 결손 해소",
    "why": "피해야 할 사과가 존재해야 게임 플레이가 성립됩니다.",
    "what": ["상단 사과 스포너", "등속 낙하 물리", "오브젝트 풀링"],
    "how": "requestAnimationFrame 기반 델타 타임 적분",
    "who_where": "src/apple.js",
    "when_acceptance": ["사과 생성 검증", "낙하 속도 검증"]
}"""
        with patch.object(Commander, "_run_raw_llm_query", new=AsyncMock(return_value=mock_raw_llm)):
            plan = await Commander.plan_adaptive_next_milestone_async(
                root_user_goal="사과 피하기 게임 만들어줘",
                workspace=self.test_dir,
                domain="game"
            )

            self.assertIn("Observation & Ground Truth", plan.thinking_process)
            self.assertIn("Single-Action Scope Guard", plan.thinking_process)
            self.assertIn("사과 낙하 물리", plan.title)

    def test_falling_apple_closed_loop_progression(self):
        """
        Closed-loop control progression for '사과 피하기 게임을 만들어줘':
        1. Canvas / 60fps Loop
        2. Player movement / Input controls
        3. Falling apple spawner & physics
        4. Collision detection & Game Over
        5. Score HUD, persistence & instant retry
        6. Hardening & E2E
        """
        goal = "사과 피하기 게임을 만들어줘"

        # Step 1: Empty workspace -> Canvas MVP
        reality1 = Commander.inspect_codebase_reality(self.test_dir)
        plan1 = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal, reality=reality1, domain="game", target_version="v1.0", workspace=self.test_dir
        )
        self.assertEqual(plan1.title, "코어 캔버스 및 60fps 렌더링 루프 MVP")
        self.assertIn("1. [Observation & Ground Truth]", plan1.thinking_process)
        self.assertIn("2. [Delta & Blocker Analysis]", plan1.thinking_process)
        self.assertIn("3. [Single-Action Scope Guard]", plan1.thinking_process)
        self.assertIn("4. [Verification Contract]", plan1.thinking_process)

        # Step 2: Canvas exists -> Player Movement
        with open(os.path.join(self.test_dir, "canvas.js"), "w") as f:
            f.write("// canvas loop")
        reality2 = Commander.inspect_codebase_reality(self.test_dir)
        plan2 = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal, reality=reality2, domain="game", target_version="v1.1", workspace=self.test_dir
        )
        self.assertEqual(plan2.title, "플레이어 좌우 이동 물리 및 입력 제어 시스템")

        # Step 3: Player exists -> Apple Spawner & Fall
        with open(os.path.join(self.test_dir, "player.js"), "w") as f:
            f.write("// player movement")
        reality3 = Commander.inspect_codebase_reality(self.test_dir)
        plan3 = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal, reality=reality3, domain="game", target_version="v1.2", workspace=self.test_dir
        )
        self.assertEqual(plan3.title, "낙하 장애물(사과) 스폰 및 등속/가속 낙하 물리 엔진")

        # Step 4: Apple exists -> Collision Detection & Game Over
        with open(os.path.join(self.test_dir, "apple.js"), "w") as f:
            f.write("// apple spawner")
        reality4 = Commander.inspect_codebase_reality(self.test_dir)
        plan4 = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal, reality=reality4, domain="game", target_version="v1.3", workspace=self.test_dir
        )
        self.assertEqual(plan4.title, "정밀 바운딩 박스(AABB) 충돌 판정 및 게임오버 처리")

        # Step 5: Collision exists -> Score HUD & Instant Retry
        with open(os.path.join(self.test_dir, "collision.js"), "w") as f:
            f.write("// collision detection")
        reality5 = Commander.inspect_codebase_reality(self.test_dir)
        plan5 = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal, reality=reality5, domain="game", target_version="v1.4", workspace=self.test_dir
        )
        self.assertEqual(plan5.title, "실시간 스코어 HUD, 최고 기록 영속화 및 1초 재시작 루프")

        # Step 6: Score exists -> Production Hardening
        with open(os.path.join(self.test_dir, "score.js"), "w") as f:
            f.write("// score hud")
        reality6 = Commander.inspect_codebase_reality(self.test_dir)
        plan6 = Commander._build_heuristic_adaptive_plan(
            root_user_goal=goal, reality=reality6, domain="game", target_version="v1.5", workspace=self.test_dir
        )
        self.assertEqual(plan6.title, "상용 배포 품질 하드닝 및 E2E 회귀 검증")


if __name__ == "__main__":
    unittest.main()

