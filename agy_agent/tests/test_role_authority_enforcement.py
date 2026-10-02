import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agy_agent"))

import role_authority as RA
from trio_agent_runner import TrioAgentRunner

_TRIO_SRC = os.path.join(os.path.dirname(__file__), "..", "agy_agent", "trio_agent_runner.py")


def _read_trio_src() -> str:
    with open(_TRIO_SRC, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. Coder 는 QA 판정 태그를 선언할 수 없다 (물리 정화 + 탐지)
# ---------------------------------------------------------------------------
def test_coder_verdict_tags_are_stripped_before_posting():
    raw = (
        "[STATUS: REJECTED]\n"
        "[BLOCKING_DEFECTS: 2]\n"
        "NPE 수정 완료\n"
        "- `app/Main.kt`: null check 추가\n"
    )
    clean, removed = RA.strip_coder_verdict_tags(raw)
    assert "[STATUS: REJECTED]" not in clean
    assert "[BLOCKING_DEFECTS: 2]" not in clean
    assert "NPE 수정 완료" in clean
    assert "null check 추가" in clean
    assert "[STATUS: REJECTED]" in removed
    assert "[BLOCKING_DEFECTS: 2]" in removed


def test_coder_inline_verdict_quotation_is_preserved():
    """본문 중 QA 판정 인용은 역할 전도가 아니며 제거 대상도 아니다."""
    raw = "QA가 [STATUS: REJECTED]를 선언하여 결함을 수정했습니다.\n파일: Main.kt"
    is_inv, _ = TrioAgentRunner.detect_coder_role_inversion(raw)
    assert is_inv is False
    clean, removed = RA.strip_coder_verdict_tags(raw)
    assert removed == []
    assert clean == raw


def test_trio_runner_delegates_detection_to_role_authority():
    """TrioAgentRunner 의 탐지 API 는 role_authority SSOT 로 위임되어야 한다."""
    coder_bad = "[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n승인합니다."
    assert TrioAgentRunner.detect_coder_role_inversion(coder_bad)[0] is True

    qa_bad = "요청하신 신규 기능을 구현 완료했습니다.\n코드를 모두 작성했습니다."
    assert TrioAgentRunner.detect_qa_role_inversion(qa_bad)[0] is True


# ---------------------------------------------------------------------------
# 2. 시스템 게이트 오버라이드는 "판정 주체"를 명시한다 (Coder 로 오인 금지)
# ---------------------------------------------------------------------------
def test_system_gate_override_declares_judging_authority():
    msg = RA.format_system_gate_override(
        approved=False,
        title="🚨 **[시스템 게이트 자동 반려 — Anti-Mocking]**",
        blocking_defects=2,
        detail="표절 적발",
    )
    lines = msg.splitlines()
    assert lines[0] == "[STATUS: REJECTED]"
    assert lines[1] == "[BLOCKING_DEFECTS: 2]"
    assert "판정 주체: 시스템 무결성 가디언" in lines[2]
    assert "품질 게이트" not in lines[2]  # Coder/QA 판정문과 구분되는 시스템 배지
    # QA 승인 파서는 시스템 반려를 반려(False)로 읽어야 한다.
    assert TrioAgentRunner.is_qa_approved(msg) is False


def test_auditor_system_gate_override_uses_audit_status():
    msg = RA.format_system_gate_override(
        approved=False,
        title="감사 거부",
        verdict_line_override="[STATUS: AUDIT_FAILED] [ACTION: STOP]",
    )
    assert msg.startswith("[STATUS: AUDIT_FAILED]")
    assert TrioAgentRunner.is_auditor_approved(msg) is False


# ---------------------------------------------------------------------------
# 3. [핵심 회귀] QA/시스템 판정문은 Coder 채널(main_channel)에 게시되지 않는다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pattern",
    [
        r"safe_send_discord_message\(\s*main_channel\s*,\s*latest_qa_output\s*\)",
        r"safe_send_discord_message\(\s*main_channel\s*,\s*current_ext_output\s*\)",
        r"safe_send_discord_message\(\s*main_channel\s*,\s*arbitration_text\s*\)",
    ],
)
def test_verdict_never_posted_on_coder_channel(pattern):
    src = _read_trio_src()
    assert re.search(pattern, src) is None, (
        "QA/시스템 판정문을 Coder 채널(main_channel)로 게시하면 사용자가 Coder 가 "
        f"판정하는 것으로 오인한다(역할 전도). 패턴 적발: {pattern}"
    )


def test_guardian_overrides_use_system_gate_formatter():
    """모든 가디언 오버라이드는 시스템 게이트 포매터를 통해 판정 주체를 명시해야 한다."""
    src = _read_trio_src()
    assert src.count("role_authority.format_system_gate_override(") >= 5
    assert "post_verdict_message(" in src


def test_coder_post_text_sanitized_in_pipeline():
    """디스코드 게시 직전 Coder 본문에서 판정 태그를 재정화하는 코드가 존재해야 한다."""
    src = _read_trio_src()
    assert "strip_coder_verdict_tags(coder_post_text)" in src
    assert "strip_coder_verdict_tags(latest_coder_output)" in src


def test_verdict_fallback_routing_and_all_exit_sanitization():
    """[QA 패치 회귀 방지] (1) qa1 skip 경로 폴백이 main_channel이 되지 않고,
    (2) 불가피한 메인 봇 중계에는 판정 주체 라벨이 붙으며,
    (3) 조기 반환/비상 게시 경로에서도 Coder 판정 태그가 정화된다."""
    src = _read_trio_src()
    assert "qa_target_channel: Any = qa_channel or main_channel" in src
    assert "판정 중계 — 작성 주체: QA/Auditor" in src
    assert "strip_coder_verdict_tags(latest_coder_output)[0]" in src


# ---------------------------------------------------------------------------
# 4. [E2E] 파이프라인 실주행 — Coder 채널에 판정문이 게시되지 않고,
#    시스템 게이트는 '판정 주체'를 명시하여 QA 채널로 라우팅된다.
# ---------------------------------------------------------------------------
def test_pipeline_role_separation_end_to_end():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from session_manager import ChannelSession

    sent = []  # (channel, text)

    async def _capture_send(channel, content=None, *args, **kwargs):
        sent.append((channel, content))
        return MagicMock()

    class DummyTyping:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    main_ch = MagicMock()
    main_ch.id = 424242
    main_ch.guild = None
    main_ch.typing = MagicMock(return_value=DummyTyping())
    main_ch.send = AsyncMock()

    qa_ch = MagicMock()
    qa_ch.id = 424242
    qa_ch.typing = MagicMock(return_value=DummyTyping())
    qa_ch.send = AsyncMock()

    qa_client = MagicMock()
    qa_client.is_ready.return_value = True
    qa_client.get_channel.return_value = qa_ch

    session = ChannelSession(channel_id=424242, workspace=str(os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))), mode="mode3")

    coder_calls = []

    async def mock_coder(session, prompt, on_tool_update=None):
        coder_calls.append(prompt)
        if len(coder_calls) == 1:
            # 역할 전도 1: Coder 가 QA 판정 태그를 선언
            return "[STATUS: REJECTED]\n[BLOCKING_DEFECTS: 1]\n결함이 있어 반려합니다."
        # 복귀 후: 실제 구현 보고
        return "NPE 방어 로직을 실제 소스코드에 구현했습니다.\n- `app/Main.kt`: null check 추가"

    async def mock_qa(session, prompt, on_tool_update=None, model=None):
        return (
            "[STATUS: APPROVED]\n[BLOCKING_DEFECTS: 0]\n\n"
            "## QA 정밀 검증 보고서\n"
            "변경된 `app/Main.kt` 라인 42~80의 NPE 방어 로직과 `PageManager.kt` 의 회전 상태 전이를 "
            "심층 대조 검증했습니다. `fun restoreViewport()` 계열 함수의 null check 가 정상 삽입되었고, "
            "`const MAX_PAGE` 상수 범위를 벗어나는 입력에 대한 방어가 확인되었습니다. 빌드/컴파일이 "
            "성공적으로 완료되었으며, 핵심 기능(페이지 재정렬 및 상태 복원)이 런타임에서 정상 동작함을 "
            "실측 확인하여 프로덕션 승인합니다. 라인 45부터 라인 120까지의 메모리 누수 방어 로직과 "
            "60fps 렌더링 인터랙션 역시 요구사항을 충족합니다."
        )

    async def run_test():
        with patch.object(TrioAgentRunner, "run_coder_turn", side_effect=mock_coder), \
             patch.object(TrioAgentRunner, "run_qa_turn", side_effect=mock_qa), \
             patch("trio_agent_runner.safe_send_discord_message", side_effect=_capture_send), \
             patch.object(TrioAgentRunner, "run_auditor_turn", new_callable=AsyncMock) as mock_aud, \
             patch("trio_agent_runner.EvidenceValidator.inspect_production_diff", return_value={"is_zero_production_diff": False, "prod_changed_files": ["app/Main.kt"], "non_prod_files": [], "diff_lines": 12}), \
             patch("trio_agent_runner.EvidenceValidator.get_git_diff_summary", return_value={"has_changes": True, "changed_files": ["app/Main.kt"], "summary": "1 file changed", "raw_diff": "", "insertions": 5, "deletions": 1, "diff_lines": 12}), \
             patch("trio_agent_runner.EvidenceValidator.validate_all_gates", return_value={"all_passed": True, "gates": {"Gate_7": {"message": "PASS"}}}), \
             patch("trio_agent_runner.EvidenceValidator.verify_anti_mocking_and_authenticity", return_value=(False, ["🚨 [Anti-Mocking Rule 6] 레퍼런스 표절"], {})), \
             patch("trio_agent_runner.EvidenceValidator.collect_workspace_image_hashes", return_value={}), \
             patch("trio_agent_runner.QualityStateManager.evaluate_and_update", return_value={"regression": {"has_regression": False}, "overall_score": 100.0, "delta": 0.0}), \
             patch("trio_agent_runner.QualityStateManager.parse_qa_qc_verdicts", return_value={}), \
             patch("trio_agent_runner.EvidenceValidator.check_visual_asset_presence", return_value={"has_assets": True}), \
             patch("trio_agent_runner.ThermalGuard.check_round_transition_cooldown", new_callable=AsyncMock), \
             patch("vision_attachment_analyzer.VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa", new_callable=AsyncMock, return_value={"has_screenshots": True}), \
             patch("autonomy_guard.CollaborationIntelligence.evaluate_auditor_value_alignment", return_value=("PROCEED", "가치 부합 확인")), \
             patch("autonomy_guard.CollaborationIntelligence.validate_requirement_coverage", return_value=(True, [], "")), \
             patch("autonomy_guard.AutonomySafetyLayer.record_llm_call"), \
             patch("autonomy_guard.AutonomySafetyLayer.record_task_start"), \
             patch("autonomy_guard.AutonomySafetyLayer.check_safety_guards", return_value=(True, "", "")), \
             patch("autonomy_guard.AutonomySafetyLayer.reset_milestone"):
            mock_aud.return_value = "[STATUS: AUDIT_PASSED]\n[VALUE: HIGH]\n최종 감사 승인"
            await TrioAgentRunner.run_pipeline(
                session=session,
                prompt="마일스톤 구현",
                main_channel=main_ch,
                qa_client=qa_client,
                max_rounds=1,
            )

    asyncio.run(run_test())

    # (1) Coder 는 1차 역할 전도 감지 후 복귀 재시도되어야 한다.
    assert len(coder_calls) >= 2
    assert "판정" in coder_calls[1] and "[STATUS" in coder_calls[1]

    # (2) Coder 채널(main_channel)에는 '판정 주체' 표기가 없는 무주체 판정문(naked verdict)이
    #     단 하나도 게시되지 않아야 한다 — 이것이 사용자가 "Coder 가 판정한다"고 오인한 원인이다.
    main_posts = [text for (ch, text) in sent if ch is main_ch and isinstance(text, str)]
    naked_verdicts = [
        t for t in main_posts
        if t.splitlines() and RA.is_declaration_line(t.splitlines()[0]) and "판정 주체:" not in t
    ]
    assert naked_verdicts == [], f"Coder 채널에 무주체 판정문이 게시됨(역할 전도): {naked_verdicts}"

    # (3) 시스템 무결성 게이트 오버라이드는 '판정 주체'를 명시하여 QA 채널로 라우팅되어야 한다.
    gate_posts = [t for (ch, t) in sent if isinstance(t, str) and "판정 주체: 시스템 무결성 가디언" in t]
    assert gate_posts, "시스템 게이트 오버라이드가 판정 주체를 명시하여 게시되지 않았습니다."
    assert any(ch is qa_ch for (ch, t) in sent if isinstance(t, str) and "판정 주체: 시스템 무결성 가디언" in t), \
        "QA1 시스템 게이트 오버라이드가 QA 채널이 아닌 Coder 채널로 게시되었습니다."

