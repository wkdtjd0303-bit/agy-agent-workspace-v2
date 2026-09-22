import unittest
import os
import tempfile
import asyncio
from discord_utils import cleanse_agent_output, extract_oversized_attachments, async_prepare_message_payload
from roadmap_manager import RoadmapManager, RoadmapMilestone, RoadmapTask
from autonomy_guard import CollaborationIntelligence

class TestCleanseAndAsyncCompletion(unittest.TestCase):
    def test_cleanse_agent_output_removes_english_monologues(self):
        sample = (
            "I have initiated the release APK build (./gradlew assembleRelease). I will process the output as soon as the build completes.\n"
            "The release build is packaging. Awaiting notification upon completion...\n"
            "I have launched ./gradlew test to verify the current unit test suite and build health. I will analyze the test results as soon as the execution finishes.\n"
            "I am waiting for the background test execution (./gradlew test) to complete. I will proceed with analysis and the requested implementation as soon as the results arrive.\n"
            "I have initiated the Kotlin compilation check for ErrorRecoveryCenterModal.kt in the background and am waiting for the compilation result.\n"
            "I have launched the compilation task and am awaiting its completion.\n"
            "I am waiting for the compilation background task to finish.\n"
            "I will now wait for the compilation to complete.\n"
            "I have triggered the build verification in the background and am waiting for it to finish.\n"
            "I am waiting for the background compilation task to complete.\n"
            "I will now wait for task completion.\n"
            "I will wait for the task to finish.\n"
            "### [진단 및 반려 사유 해결 보고]\n\n"
            "> **직접 답변 (Direct Answer)**:\n"
            "> 가디언(Autonomy Guardian) 및 시니어 QA가 지적한 필수 요구사항을 구현 완료했습니다.\n"
        )
        cleaned = cleanse_agent_output(sample)
        self.assertNotIn("I have initiated", cleaned)
        self.assertNotIn("I am waiting for", cleaned)
        self.assertNotIn("The release build is packaging", cleaned)
        self.assertNotIn("Awaiting notification upon completion", cleaned)
        self.assertNotIn("I will now wait", cleaned)
        self.assertIn("### [진단 및 반려 사유 해결 보고]", cleaned)
        self.assertIn("가디언(Autonomy Guardian) 및 시니어 QA가 지적한", cleaned)

    def test_meta_instructions_excluded_from_requirement_coverage(self):
        prompt = (
            "사령관이 직접 수립한 차기 마일스톤 육하원칙(5W1H) 전략 마스터플랜입니다.\n"
            "📋 이번에 만들 것\n"
            "• 비정상 입력/오류 시 사용자 친화적 에러 가이드 및 자동 복구\n"
            "• 앱 런타임 메모리 누수 방지 및 크래시 0건 내구성 확보\n"
            "• 로딩 스켈레톤 및 빈 화면(Empty State) 시각 피드백 완성\n"
            "🎯 완료 기준\n"
            "• 1. 컴파일/빌드 성공 및 정적 분석(Lint/Type) 에러 0건\n"
            "• 2. 상용 서비스 품질 & 예외 복원력 강화 명시된 기능 스펙 디스크 소스코드 상에 실체 구현 완료\n"
            "• 3. 콘솔 오류 및 런타임 크래시 0건 (무결점 작동)\n"
            "• 4. ⚖️ Auditor(감사관) 최종 전수 검증 통과 (PASS) 획득\n"
        )
        coder_output = (
            "비정상 입력 시 사용자 친화적 에러 가이드와 자동 복구 모달을 구현했습니다.\n"
            "메모리 누수 방지와 로딩 스켈레톤 및 빈 화면 Empty State 시각 피드백을 적용했습니다."
        )
        ok, missing, msg = CollaborationIntelligence.validate_requirement_coverage(prompt, coder_output)
        self.assertTrue(ok)
        self.assertEqual(len(missing), 0)

    def test_roadmap_manager_never_repeats_completed_milestones(self):
        with tempfile.TemporaryDirectory() as td:
            rm = RoadmapManager(td)
            rm.ensure_exists()
            # Mark all existing milestones as done
            for ms in rm._milestones:
                ms.mark_done()
            rm.save()

            # First auto expansion
            new_1 = rm.auto_expand_roadmap()
            self.assertTrue(len(new_1) > 0)
            titles_1 = [m.title for m in new_1]

            for m in new_1:
                m.mark_done()
            rm.save()

            # Second auto expansion
            new_2 = rm.auto_expand_roadmap()
            self.assertTrue(len(new_2) > 0)
            titles_2 = [m.title for m in new_2]

            # Titles in second expansion MUST NOT repeat titles in first expansion!
            for t2 in titles_2:
                self.assertNotIn(t2, titles_1)

if __name__ == "__main__":
    unittest.main()
