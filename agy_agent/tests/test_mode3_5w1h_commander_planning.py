#!/usr/bin/env python3
"""
Unit tests for Mode 3 Commander 5W1H Strategic Milestone Planning & Visual Asset Authorization.
Validates:
1. 5W1H Plan Structure: why, what, how, who_where, when_acceptance, visual_asset_spec.
2. Visual Asset Generation Authorization:
   - GUI/Mobile/Web/Game apps proactively authorize generate_image with Rule 8 Type A/Type B specs.
   - CLI/Daemon/Backend API projects explicitly designate visual assets as not needed.
3. Discord Embed generation with 5W1H fields and Commander branding.
4. Clean Context Coder Directive generation containing 5W1H details and Commander's 3 obedience mandates.
5. Mode 3 First-Responder routing to commander_bot before Coder begins.
6. Async AI synthesis and graceful smart heuristic fallback.
"""

import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from commander import Commander, Milestone5W1HPlan
from session_manager import ChannelSession


class TestMode35W1HCommanderPlanning(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_mode3_5w1h_")
        self.session = ChannelSession(channel_id=99992222, workspace=self.test_dir, mode="mode3")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_heuristic_5w1h_plan_structure_and_keys(self):
        """Validates that 5W1H plan contains all 6 structural pillars."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="알PDF 코어 뷰어 및 주석 에디터",
            milestone_version="v1.0",
            tasks=["PDF 렌더링 캔버스 구현", "형광펜/서명 툴바 추가"],
            domain="mobile_app",
            workspace=self.test_dir
        )

        self.assertEqual(type(plan).__name__, "Milestone5W1HPlan")
        self.assertEqual(plan.version, "v1.0")
        self.assertEqual(plan.domain, "mobile_app")

        # 1. 왜 (Why)
        self.assertTrue(len(plan.why) > 20)
        self.assertIn("코어", plan.why)

        # 2. 무엇을 (What)
        self.assertEqual(len(plan.what), 2)
        self.assertIn("PDF 렌더링 캔버스 구현", plan.what[0])

        # 3. 어떻게 (How)
        self.assertTrue(len(plan.how) > 20)
        self.assertIn("Compose", plan.how)
        self.assertIn("48dp", plan.how)

        # 4. 누가·어디서 (Who & Where)
        self.assertIn("Coder", plan.who_where)
        self.assertIn("QA", plan.who_where)
        self.assertIn("Auditor", plan.who_where)
        self.assertIn("app/src/main", plan.who_where)

        # 5. 완료기준 (When & Acceptance)
        self.assertTrue(len(plan.when_acceptance) >= 4)
        self.assertTrue(any("컴파일" in c or "빌드" in c for c in plan.when_acceptance))
        self.assertTrue(any("Auditor" in c or "감사관" in c for c in plan.when_acceptance))

        # 6. 시각 에셋 기획 (Visual Asset Plan)
        self.assertTrue(plan.visual_approved)
        self.assertIn("Rule 8", plan.visual_asset_spec)
        self.assertIn("Type A", plan.visual_asset_spec)
        self.assertIn("Type B", plan.visual_asset_spec)

    def test_mobile_app_visual_asset_authorization(self):
        """Mobile apps must proactively authorize Rule 8 Type A (Splash) & Type B (Icons)."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="스마트 가계부 모바일 앱",
            milestone_version="v2.0",
            tasks=["소비 통계 차트 화면 구현"],
            domain="mobile_app",
            workspace=self.test_dir
        )
        self.assertTrue(plan.visual_approved)
        self.assertIn("res/drawable", plan.visual_asset_spec)
        self.assertIn("스플래시", plan.visual_asset_spec)
        self.assertIn("런처 아이콘", plan.visual_asset_spec)
        self.assertIn("painterResource", plan.visual_asset_spec)

    def test_saas_web_visual_asset_authorization(self):
        """Web services must proactively authorize Rule 8 Type A (Hero) & Type B (Empty State/Logo)."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="SaaS 관리자 대시보드",
            milestone_version="v1.0",
            tasks=["대시보드 메인 레이아웃 구현"],
            domain="saas_web",
            workspace=self.test_dir
        )
        self.assertTrue(plan.visual_approved)
        self.assertIn("히어로 배너", plan.visual_asset_spec)
        self.assertIn("Empty State", plan.visual_asset_spec)
        self.assertIn("assets/images", plan.visual_asset_spec)

    def test_game_visual_asset_authorization(self):
        """Game projects must proactively authorize Rule 8 Type A (Title bg) & Type B (Sprites)."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="2D 아케이드 플랫포머",
            milestone_version="v2.0",
            tasks=["플레이어 및 적 캐릭터 구현"],
            domain="game",
            workspace=self.test_dir
        )
        self.assertTrue(plan.visual_approved)
        self.assertIn("스프라이트", plan.visual_asset_spec)
        self.assertIn("타이틀 배경", plan.visual_asset_spec)
        self.assertIn("60fps", plan.visual_asset_spec)

    def test_cli_and_daemon_visual_asset_skip(self):
        """CLI tools and background daemons must explicitly designate visual assets as not needed."""
        cli_plan = Commander.plan_milestone_5w1h(
            milestone_title="로그 분석 고성능 CLI",
            milestone_version="v1.0",
            tasks=["로그 스트림 파싱 파이프라인"],
            domain="cli_agent",
            workspace=self.test_dir
        )
        self.assertFalse(cli_plan.visual_approved)
        self.assertIn("시각 에셋 불필요", cli_plan.visual_asset_spec)
        self.assertIn("ANSI 터미널", cli_plan.visual_asset_spec)

        daemon_plan = Commander.plan_milestone_5w1h(
            milestone_title="배치 동기화 데몬",
            milestone_version="v2.0",
            tasks=["PID 파일 잠금 및 무인 배치"],
            domain="daemon",
            workspace=self.test_dir
        )
        self.assertFalse(daemon_plan.visual_approved)
        self.assertIn("시각 에셋 불필요", daemon_plan.visual_asset_spec)

    def test_backend_api_visual_asset_skip(self):
        """Backend APIs must explicitly designate visual assets as not needed."""
        api_plan = Commander.plan_milestone_5w1h(
            milestone_title="인증 및 결제 마이크로서비스 API",
            milestone_version="v1.0",
            tasks=["JWT 토큰 발급 및 결제 엔드포인트"],
            domain="backend_api",
            workspace=self.test_dir
        )
        self.assertFalse(api_plan.visual_approved)
        self.assertIn("시각 에셋 불필요", api_plan.visual_asset_spec)
        self.assertIn("OpenAPI/Swagger", api_plan.visual_asset_spec)

    def test_to_discord_embed_contains_all_5w1h_fields(self):
        """to_discord_embed must render rich Discord Embed containing all 5W1H sections."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="실시간 주차 메모 앱",
            milestone_version="v1.0",
            tasks=["주차 위치 사진 등록", "층수/구역 텍스트 메모 저장"],
            domain="mobile_app",
            workspace=self.test_dir
        )
        embed = plan.to_discord_embed(mode_label="3", engine_disp="Gemini 3.7 Flash (Middle)")

        self.assertIn("실시간 주차 메모 앱", embed.title)
        self.assertIn("v1.0", embed.title)

        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("이번에 만들 것" in n for n in field_names))
        self.assertTrue(any("완료 기준" in n for n in field_names))
        self.assertTrue(any("디자인" in n or "에셋" in n for n in field_names))

    def test_build_directive_for_coder_clean_context_and_obedience(self):
        """Coder directive must contain full 5W1H plan and Commander's 3 obedience mandates."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="코어 결제 모듈",
            milestone_version="v1.0",
            tasks=["PG사 결제 웹훅 처리", "결제 영수증 PDF 생성"],
            domain="saas_web",
            workspace=self.test_dir,
            macro_memory_text="• (회차 0): 이전 준비 완료"
        )
        directive = plan.build_directive_for_coder(
            macro_memory_text="• (회차 0): 이전 준비 완료",
            user_prompt="결제 모듈 깔끔하게 짜줘"
        )

        # 5W1H sections present
        self.assertIn("[1. 왜 (Why - 구현 목적 & 전략 가치)]", directive)
        self.assertIn("[2. 무엇을 (What - 집중 구현 스펙)]", directive)
        self.assertIn("[3. 어떻게 (How - 아키텍처 & 기술 방법론)]", directive)
        self.assertIn("[4. 누가·어디서 (Who & Where - 작업 주체 & 타깃 경로)]", directive)
        self.assertIn("[5. 완료기준 (When & Acceptance - 기계적 인수 기준)]", directive)

        # Obedience mandates present
        self.assertIn("컨텍스트 청정화 및 명령 순응", directive)
        self.assertIn("가시적 실체 구현", directive)
        self.assertIn("빌드/테스트 완결", directive)

        # Visual authorization present
        self.assertIn("시각 디자인 에셋 생성", directive)

        # Macro memory injected
        self.assertIn("• (회차 0): 이전 준비 완료", directive)

    async def test_async_plan_milestone_5w1h_ai_synthesis(self):
        """Async planner parses JSON from LLM and falls back safely to heuristic."""
        mock_ai_json = """{
            "why": "사용자의 모바일 편의성을 위해 실시간 주차 위치를 1초 만에 기록하는 MVP를 완성하기 위함.",
            "what": ["주차 위치 GPS/층수 기록", "영수증 사진 촬영 썸네일 표시"],
            "how": "Jetpack Compose Column/Card 기반 65% 이상 뷰포트 레이아웃 및 Room DB 로컬 영속화.",
            "who_where": "Coder: MainActivity.kt 및 ParkingViewModel.kt 구현 / QA: UI 터치 검증",
            "when_acceptance": [
                "1. 컴파일 에러 0건 및 정상 APK 빌드 완료",
                "2. 층수/구역 데이터가 재기동 후에도 100% 보존",
                "3. Auditor 최종 승인 통과"
            ],
            "visual_asset_spec": "🎨 [사령관 승인] 9:16 스플래시 배경 및 1:1 주차 핀 아이콘 (res/drawable)",
            "visual_approved": true
        }"""

        with patch.object(Commander, "_run_raw_llm_query", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_ai_json
            ai_plan = await Commander.plan_milestone_5w1h_async(
                milestone_title="주차 메모 MVP",
                milestone_version="v1.0",
                tasks=["위치 기록", "메모 저장"],
                domain="mobile_app",
                workspace=self.test_dir
            )

            self.assertEqual(ai_plan.source, "ai")
            self.assertIn("1초 만에 기록하는 MVP", ai_plan.why)
            self.assertEqual(len(ai_plan.what), 2)
            self.assertIn("Room DB", ai_plan.how)
            self.assertTrue(ai_plan.visual_approved)
            self.assertIn("주차 핀 아이콘", ai_plan.visual_asset_spec)

    def test_atomic_scope_and_substance_over_breadth_mandates_in_directive(self):
        """Directive must enforce atomic scope and substance over breadth (anti-stub/zero-mock)."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="본문 텍스트 검색",
            milestone_version="v2.1",
            tasks=["실시간 검색 인덱싱"],
            domain="mobile_app",
            workspace=self.test_dir
        )
        directive = plan.build_directive_for_coder()
        self.assertIn("단일 원자성(Atomic Scope) 및 스코프 과밀 방지", directive)
        self.assertIn("내실과 깊이 우선 (Substance over Breadth - Zero-Stub/Zero-Mock)", directive)
        self.assertTrue(any("스텁/TODO/가짜 목업" in c for c in plan.when_acceptance))
    def test_user_feedback_overrides_static_roadmap_and_generic_search(self):
        """User feedback on logo, color, buttons, and PDF text editing must override generic roadmap."""
        user_feedback = (
            "로고, 앱 이름, 앱 디자인이 별로야\n"
            "특히 컬러는 첨부파일 참고해줘\n"
            "그리고 edit기능에선 기존 pdf에 들어있던 텍스트 문구를 수정할 수 있는 기능이 들어있우면 좋겠어. "
            "Ocr방식말고 진짜로 pdf에 기존에 들어있던 글자 수정말야.\n"
            "그리고 버튼들 가독성이 좀 떨어지는 것 같아.\n"
            "이런 것들을 반영해서 퀄리티를 높여줘"
        )
        plan = Commander.plan_milestone_5w1h(
            milestone_title="실시간 본문 텍스트 검색 & 뷰포트 하이라이트 (Phase 2: 검색 코어)",
            milestone_version="v5.2",
            tasks=["실시간 본문 전문 검색 쿼리 및 매칭 단어 뷰포트 하이라이트/스크롤 연동"],
            domain="saas_web",
            workspace=self.test_dir,
            user_prompt=user_feedback
        )

        # Title must reflect user feedback rather than the old search milestone
        self.assertIn("사용자 피드백", plan.title)
        self.assertIn("PDF 원본 텍스트 직접 편집", plan.title)
        self.assertNotIn("실시간 본문 텍스트 검색", plan.title)

        # Why must reflect user concerns
        self.assertIn("피드백", plan.why)
        self.assertIn("로고/앱 이름/디자인 시각 완성도", plan.why)
        self.assertIn("버튼 가독성", plan.why)
        self.assertIn("PDF 원본 문서 내 텍스트를 OCR 없이 직접 수정", plan.why)

        # What must cover color, buttons, in-place PDF editing
        what_str = " ".join(plan.what)
        self.assertIn("첨부파일 컬러 팔레트", what_str)
        self.assertIn("로고 및 앱 이름 리뉴얼", what_str)
        self.assertIn("버튼 시인성 및 가독성 고대비 최적화", what_str)
        self.assertIn("원본 텍스트 스트림 직접 수정", what_str)
        self.assertIn("No-OCR", what_str)

        # Visual assets must be authorized for logo / theme renewal
        self.assertTrue(plan.visual_approved)
        self.assertIn("첨부파일 컬러 팔레트", plan.visual_asset_spec)

    def test_to_discord_message_clean_formatting(self):
        """to_discord_message renders clean plain Discord markdown without embed forms."""
        plan = Commander.plan_milestone_5w1h(
            milestone_title="테스트 마일스톤",
            milestone_version="v1.0",
            tasks=["핵심 로직 구현", "엣지 케이스 방어"],
            domain="mobile_app",
            workspace=self.test_dir
        )
        msg = plan.to_discord_message(mode_label="3", engine_disp="Gemini 3.7 Flash (Middle)")

        self.assertIsInstance(msg, str)
        self.assertIn("👑 **[Commander 사령관 마일스톤 기획 — v1.0: 테스트 마일스톤]**", msg)
        self.assertIn("💡 **기획 의도**:", msg)
        self.assertIn("📋 **이번에 만들 것**", msg)
        self.assertIn("🎯 **완료 기준**", msg)
        self.assertIn("!stop", msg)
        # Should NOT be an embed object, should be clean text
        self.assertFalse(hasattr(msg, "fields"))


if __name__ == "__main__":
    unittest.main()


