import os
import sys
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from quality_state_manager import QualityStateManager, QualityContract
from commander import Commander, Milestone5W1HPlan


class TestQualityStateManager:

    def test_default_qcs_generation(self):
        game_qcs = QualityStateManager.generate_domain_default_qcs("game")
        assert len(game_qcs) >= 3
        ids = [q["id"] for q in game_qcs]
        assert "QC-G-01" in ids
        assert "QC-V-01" in ids

        mobile_qcs = QualityStateManager.generate_domain_default_qcs("mobile_app")
        assert any(q["category"] == "ux" for q in mobile_qcs)

        cli_qcs = QualityStateManager.generate_domain_default_qcs("cli_agent")
        assert any(q["category"] == "architecture" for q in cli_qcs)

    def test_parse_qa_qc_verdicts(self):
        expected = [
            {"id": "QC-G-01", "category": "gameplay", "description": "속도 가속도 공식"},
            {"id": "QC-V-01", "category": "visual", "description": "에셋 이미지 바인딩"},
            {"id": "QC-U-01", "category": "ux", "description": "화면 흔들림 피드백"}
        ]
        sample_qa_text = """
        [검증 결과 보고]
        • QC-G-01: PASS (코드 분석: CONFIG.baseSpeed와 elapsedTime 수식 확인 완료)
        • QC-V-01: FAIL (단색 사각형 fillRect만 사용되고 이미지 drawImage 누락)
        • [QC-U-01] PASS (충돌 시 triggerScreenShake 함수 호출 확인)
        """
        results = QualityStateManager.parse_qa_qc_verdicts(sample_qa_text, expected)
        assert len(results) == 3
        status_map = {r["id"]: r["status"] for r in results}
        assert status_map["QC-G-01"] == "PASS"
        assert status_map["QC-V-01"] == "FAIL"
        assert status_map["QC-U-01"] == "PASS"

    def test_evaluate_and_update_calculates_scores_programmatically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            qc_results_round1 = [
                {"id": "QC-G-01", "category": "gameplay", "status": "PASS"},
                {"id": "QC-G-02", "category": "gameplay", "status": "FAIL"},
                {"id": "QC-V-01", "category": "visual", "status": "FAIL"},
                {"id": "QC-U-01", "category": "ux", "status": "PASS"},
            ]
            # 4 QCs total: 2 passed -> 50.0%
            state1 = QualityStateManager.evaluate_and_update(
                workspace=tmpdir,
                milestone_version="v1.0",
                qc_results=qc_results_round1,
                functional_ac_passed=True
            )
            assert state1["overall_score"] == 50.0
            assert state1["delta"] == 50.0
            assert state1["categories"]["gameplay"]["score"] == 50.0
            assert state1["categories"]["visual"]["score"] == 0.0
            assert state1["categories"]["ux"]["score"] == 100.0
            assert state1["regression"]["has_regression"] is False

            # Round 2: visual improved (3 passed out of 4 -> 75.0%)
            qc_results_round2 = [
                {"id": "QC-G-01", "category": "gameplay", "status": "PASS"},
                {"id": "QC-G-02", "category": "gameplay", "status": "PASS"},
                {"id": "QC-V-01", "category": "visual", "status": "FAIL"},
                {"id": "QC-U-01", "category": "ux", "status": "PASS"},
            ]
            state2 = QualityStateManager.evaluate_and_update(
                workspace=tmpdir,
                milestone_version="v2.0",
                qc_results=qc_results_round2,
                functional_ac_passed=True
            )
            assert state2["overall_score"] == 75.0
            assert state2["previous_overall_score"] == 50.0
            assert state2["delta"] == 25.0
            assert len(state2["history"]) == 2

    def test_regression_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            qc_results = [
                {"id": "QC-G-01", "category": "gameplay", "status": "PASS"},
            ]
            # Functional AC failed -> regression must be True
            state = QualityStateManager.evaluate_and_update(
                workspace=tmpdir,
                milestone_version="v1.1",
                qc_results=qc_results,
                functional_ac_passed=False
            )
            assert state["regression"]["has_regression"] is True
            assert "핵심 기능 인수 기준" in state["regression"]["details"]

    def test_format_state_summary_for_commander(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            qc_results = [
                {"id": "QC-G-01", "category": "gameplay", "status": "PASS"},
                {"id": "QC-V-01", "category": "visual", "status": "FAIL"},
            ]
            QualityStateManager.evaluate_and_update(
                workspace=tmpdir,
                milestone_version="v1.0",
                qc_results=qc_results,
                functional_ac_passed=True
            )
            summary = QualityStateManager.format_state_summary_for_commander(tmpdir)
            assert "기계적 Quality State Store 현황" in summary
            assert "50.0%" in summary
            assert "VISUAL" in summary

    def test_milestone_5w1h_plan_contains_quality_contracts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = Commander.plan_milestone_5w1h(
                milestone_title="사과 피하기 핵심 프로토타입",
                milestone_version="v1.0",
                domain="game",
                workspace=tmpdir
            )
            assert len(plan.quality_contracts) >= 3
            assert any(q["id"] == "QC-G-01" for q in plan.quality_contracts)
            directive = plan.directive_for_coder
            assert "Quality Contract" in directive
            assert "QC-G-01" in directive
            discord_msg = plan.to_discord_message()
            assert "Quality Contract" in discord_msg

    def test_cli_agent_domain_qcs(self):
        cli_qcs = QualityStateManager.generate_domain_default_qcs("cli_agent")
        ids = [q["id"] for q in cli_qcs]
        assert "QC-A-01" in ids
        assert "QC-U-01" in ids
        assert "QC-P-01" in ids
        # Verify CLI rules focus on CLI parameters and terminal UX, not web viewports
        assert any("Argparse" in q["description"] or "명령행" in q["description"] for q in cli_qcs)
        assert any("진행률" in q["description"] or "터미널" in q["description"] for q in cli_qcs)
        assert any("Shutdown" in q["description"] or "SIGINT" in q["description"] for q in cli_qcs)

    def test_cloudcode_proxy_config_separation(self):
        import config
        from cloudcode_cache_proxy import get_proxy_url
        url = config.get_cloudcode_proxy_url(19876)
        assert url == "http://127.0.0.1:19876"
        url_custom = config.get_cloudcode_proxy_url(20000)
        assert url_custom == "http://127.0.0.1:20000"
        proxy_url = get_proxy_url(19876)
        assert proxy_url == "http://127.0.0.1:19876"

    def test_no_auto_pass_fraud_when_qa_only_writes_approved(self):
        """Verify LLM cannot self-certify 100% just by outputting [STATUS: APPROVED] without QC IDs."""
        expected = [
            {"id": "QC-G-01", "category": "gameplay", "description": "속도 공식", "level": 2},
            {"id": "QC-V-01", "category": "visual", "description": "이미지", "level": 2}
        ]
        qa_lazy_approval = "[STATUS: APPROVED]\n잘 만들어졌습니다. 아주 훌륭합니다!"
        results = QualityStateManager.parse_qa_qc_verdicts(qa_lazy_approval, expected)
        assert len(results) == 2
        # Must be FAIL because QA did not explicitly review each QC ID and level is 2 (no disk fallback)
        assert results[0]["status"] == "FAIL"
        assert results[1]["status"] == "FAIL"
        assert "자화자찬 방어 차단" in results[0]["evidence"]

    def test_mechanical_disk_fallback_verification(self):
        """Verify Level 1 mechanical QC can be proven by physical disk inspection when QA text omitted it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            game_file = os.path.join(tmpdir, "game.js")
            with open(game_file, "w", encoding="utf-8") as f:
                f.write("let speed = baseSpeed * (1 + elapsedTime * 0.05);\n")

            expected = [
                {
                    "id": "QC-G-01",
                    "category": "gameplay",
                    "description": "속도 공식",
                    "level": 1,
                    "verification_rule": "CONFIG 또는 낙하/이동 로직에 시간(elapsedTime) 또는 가속도 수식이 곱해져 있는가?"
                }
            ]
            qa_omitted_text = "기본 로직 검토 완료되었습니다."
            results = QualityStateManager.parse_qa_qc_verdicts(qa_omitted_text, expected, workspace=tmpdir)
            assert results[0]["status"] == "PASS"
            assert "기계적 디스크 검증" in results[0]["evidence"]

    def test_sync_to_working_memory(self):
        """Verify Quality State Store writes markdown table to WORKING_MEMORY.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wm_file = os.path.join(tmpdir, "WORKING_MEMORY.md")
            with open(wm_file, "w", encoding="utf-8") as f:
                f.write("# Project Memory\n\n## 1. Project Context\nInitial state.\n")

            state_data = {
                "version": "v2.0",
                "overall_score": 75.0,
                "delta": 25.0,
                "categories": {
                    "gameplay": {"passed": 3, "total": 4, "score": 75.0},
                    "visual": {"passed": 2, "total": 3, "score": 66.7},
                    "ux": {"passed": 1, "total": 1, "score": 100.0},
                    "performance": {"passed": 2, "total": 2, "score": 100.0},
                    "architecture": {"passed": 1, "total": 2, "score": 50.0}
                },
                "regression": {"has_regression": False},
                "active_qcs": [
                    {"id": "QC-G-01", "status": "PASS", "description": "가속도 수식", "evidence": "elapsedTime 발견"}
                ]
            }

            ok = QualityStateManager.sync_to_working_memory(tmpdir, state_data)
            assert ok is True

            with open(wm_file, "r", encoding="utf-8") as f:
                content = f.read()

            assert "## 📊 Quality State Store" in content
            assert "75.0%" in content
            assert "+25.0%" in content
            assert "GAMEPLAY" in content
            assert "QC-G-01" in content

    def test_hierarchical_domain_qcs_progression(self):
        """Verify QC generation dynamically specializes based on milestone loop version."""
        qcs_v1 = QualityStateManager.generate_domain_default_qcs("game", milestone_version="v1.0")
        assert any("입력" in q["description"] or "스폰" in q["description"] for q in qcs_v1)

        qcs_v2 = QualityStateManager.generate_domain_default_qcs("game", milestone_version="v2.0")
        assert any("명명 규칙" in q["description"] or "QC-A-01" == q["id"] for q in qcs_v2)
        assert any("손맛" in q["description"] or "shake" in q["verification_rule"] for q in qcs_v2)

        qcs_v3 = QualityStateManager.generate_domain_default_qcs("game", milestone_version="v3.0")
        assert any("스치기" in q["description"] or "Graze" in q["description"] for q in qcs_v3)
        assert any("황금 사과" in q["description"] or "콤보" in q["description"] for q in qcs_v3)
