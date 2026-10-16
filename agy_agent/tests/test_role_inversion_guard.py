import pytest
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agy_agent"))

from trio_agent_runner import TrioAgentRunner
from engine_manager import HermesAdapter
from session_manager import ChannelSession


def test_detect_coder_role_inversion_status_tags():
    """Coder declaring QA verdict status tags must be detected as role inversion."""
    # 1. Coder outputting QA status approved
    output_approved = (
        "[STATUS: APPROVED]\n"
        "[BLOCKING_DEFECTS: 0]\n"
        "모든 기능이 정상적으로 구현되어 검증을 승인합니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(output_approved)
    assert is_inv is True
    assert "QA/감사관 판정 태그" in reason

    # 2. Coder outputting QA status rejected
    output_rejected = (
        "[STATUS: REJECTED]\n"
        "[BLOCKING_DEFECTS: 2]\n"
        "1. null pointer exception 발견\n2. 레이아웃 오버플로우"
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(output_rejected)
    assert is_inv is True
    assert "QA/감사관 판정 태그" in reason

    # 3. Coder outputting Auditor status passed
    output_auditor = (
        "최종 판정: [STATUS: AUDIT_PASSED]\n"
        "모든 품질 게이트를 충족했습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(output_auditor)
    assert is_inv is True
    assert "QA/감사관 판정 태그" in reason


def test_detect_coder_role_inversion_pure_inspection_reports():
    """Coder producing pure QA review reports without implementing code must be detected."""
    output_review = (
        "QA 검증 리포트\n"
        "코드 분석 결과 현재 프로젝트에 결함이 없음을 확인했습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(output_review)
    assert is_inv is True
    assert "QA 역할" in reason


def test_detect_coder_normal_output_passes():
    """Normal Coder implementation outputs must NOT be flagged as role inversion."""
    normal_output = (
        "요청하신 기능을 구현 완료했습니다.\n\n"
        "**[수정된 파일]**:\n"
        "- `app/src/main/MainActivity.kt`: 타이머 로직 추가\n\n"
        "**[변경 핵심 diff]**:\n"
        "```diff\n"
        "+ val timer = Timer()\n"
        "```\n"
        "단위 테스트를 통과하여 안전성을 확인했습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(normal_output)
    assert is_inv is False
    assert reason == ""


def test_detect_coder_role_inversion_ignores_quotes_and_codeblocks():
    """Coder quoting QA verdicts in quotes, inline code, or code blocks must NOT trigger role inversion."""
    # 1. Inline code mention
    inline_code_mention = (
        "QA가 `[STATUS: APPROVED]`를 내릴 때의 엣지 케이스를 패치했습니다.\n"
        "이전 `[STATUS: REJECTED]` 로그를 분석하여 수정 완료했습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(inline_code_mention)
    assert is_inv is False
    assert reason == ""

    # 2. Markdown blockquote (>)
    blockquote_mention = (
        "아래 QA 보고서 내용을 참고하여 수정을 진행했습니다:\n"
        "> [STATUS: REJECTED]\n"
        "> [BLOCKING_DEFECTS: 1]\n"
        "> 1. NullPointerException 발견\n"
        "해당 NPE 문제를 완전히 수정했습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(blockquote_mention)
    assert is_inv is False
    assert reason == ""

    # 3. Markdown codeblock (```...```)
    codeblock_mention = (
        "message.txt의 작업 지침을 인용합니다:\n"
        "```markdown\n"
        "1. QA는 `[STATUS: APPROVED]`를 줬는데 시스템 가디언이 반려함\n"
        "2. Coder가 [STATUS: REJECTED]를 보고서에서 언급함\n"
        "```\n"
        "위 2가지 이슈를 모두 해결했습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_coder_role_inversion(codeblock_mention)
    assert is_inv is False
    assert reason == ""


def test_detect_pending_subagent_work_short_circuits_on_turn_done():
    """When [TURN_DONE] is present, pending subagent detector must return False even with technical terms."""
    from trio_agent_runner import _has_pending_subagent_work

    report_with_technical_terms = (
        "이벤트 루프가 돌고 있는 스레드에서도 코루틴을 안전 실행하도록 구현 완료했습니다.\n"
        "사전 알림 대기 로직을 리팩터링하고 백그라운드 워커 큐를 최적화했습니다.\n\n"
        "[TURN_DONE]"
    )
    assert TrioAgentRunner.detect_pending_subagent_work(report_with_technical_terms) is False
    assert _has_pending_subagent_work(report_with_technical_terms) is False
    assert TrioAgentRunner._has_pending_subagent_work(report_with_technical_terms) is False


def test_get_fresh_evidence_validator():
    """get_fresh_evidence_validator must dynamically return the latest EvidenceValidator class."""
    fresh_cls = TrioAgentRunner.get_fresh_evidence_validator()
    assert fresh_cls is not None
    assert hasattr(fresh_cls, "validate_all_gates")
    assert hasattr(fresh_cls, "get_git_diff_summary")



def test_detect_qa_role_inversion_developer_mimicry():
    """QA omitting status verdict tags and declaring code implementation completion must be detected."""
    # QA pretending to be developer
    qa_coder_output = (
        "요청하신 신규 기능을 구현 완료했습니다.\n"
        "제가 코드를 모두 작성했습니다.\n"
        "파일을 수정하였으니 확인해보세요."
    )
    is_inv, reason = TrioAgentRunner.detect_qa_role_inversion(qa_coder_output)
    assert is_inv is True
    assert "QA 에이전트가 품질 검증 및 [STATUS: APPROVED/REJECTED] 판정 대신 직접 기능 개발" in reason


def test_detect_qa_normal_outputs_pass():
    """Normal QA review outputs must NOT be flagged as role inversion."""
    normal_qa_pass = (
        "[STATUS: APPROVED]\n"
        "[BLOCKING_DEFECTS: 0]\n"
        "전체 빌드가 통과하였고, 버튼 클릭 및 레이아웃이 요구사항을 충족합니다."
    )
    is_inv, reason = TrioAgentRunner.detect_qa_role_inversion(normal_qa_pass)
    assert is_inv is False

    normal_qa_reject = (
        "[STATUS: REJECTED]\n"
        "[BLOCKING_DEFECTS: 1]\n"
        "1. `MainActivity.kt`: line 42에서 NPE 발생 가능성이 있습니다."
    )
    is_inv, reason = TrioAgentRunner.detect_qa_role_inversion(normal_qa_reject)
    assert is_inv is False


def test_hermes_adapter_session_isolation_by_role():
    """HermesAdapter must isolate CLI conversation session names by role to prevent context contamination."""
    adapter = HermesAdapter()
    session = ChannelSession(channel_id=987654321, workspace="/tmp/test", conversation_id="conv-12345678")

    # 1. Coder role
    coder_cmd = adapter.build_command(
        prompt="Write code",
        workspace="/tmp/test",
        session=session,
        role="coder"
    )
    assert "--continue" in coder_cmd
    conv_idx = coder_cmd.index("--continue") + 1
    coder_conv_name = coder_cmd[conv_idx]
    assert "agy-channel-987654321-coder" in coder_conv_name

    # 2. QA role
    qa_cmd = adapter.build_command(
        prompt="Review code",
        workspace="/tmp/test",
        session=session,
        role="qa"
    )
    assert "--continue" in qa_cmd
    qa_conv_idx = qa_cmd.index("--continue") + 1
    qa_conv_name = qa_cmd[qa_conv_idx]
    assert "agy-channel-987654321-qa" in qa_conv_name

    # 3. Must be strictly different to prevent context bleed
    assert coder_conv_name != qa_conv_name


def test_role_explicit_dispatch_in_turns():
    """TrioAgentRunner run_coder_turn, run_qa_turn, and run_auditor_turn must pass explicit role and conv attr."""
    import asyncio
    async def _run():
        session = ChannelSession(channel_id=111, workspace="/tmp/dummy")

        with patch("trio_agent_runner.AgentRunner.run_prompt", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Mock response"

            # 1. Coder turn
            await TrioAgentRunner.run_coder_turn(session=session, prompt="Build app")
            assert mock_run.call_args.kwargs.get("role") == "coder"
            assert mock_run.call_args.kwargs.get("conversation_id_attr") == "conversation_id"

            # 2. QA turn
            await TrioAgentRunner.run_qa_turn(session=session, prompt="Check app")
            assert mock_run.call_args.kwargs.get("role") == "qa"
            assert mock_run.call_args.kwargs.get("conversation_id_attr") == "qa_conversation_id"

            # 3. Auditor turn
            await TrioAgentRunner.run_auditor_turn(session=session, prompt="Audit app")
            assert mock_run.call_args.kwargs.get("role") == "auditor"
            assert mock_run.call_args.kwargs.get("conversation_id_attr") == "auditor_conversation_id"

    asyncio.run(_run())
