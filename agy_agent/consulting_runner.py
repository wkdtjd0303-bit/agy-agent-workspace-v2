import logging
from typing import Optional, Callable, Any
import discord

from config import (
    CODER_MODEL,
    AUDITOR_MODEL,
    ADMIN_CHANNEL_ID,
    NOTIFICATION_CHANNEL_ID,
)
from session_manager import ChannelSession
from agent_runner import AgentRunner
from thermal_guard import ThermalGuard
from trio_agent_runner import QuotaManager, compact_agent_context
from discord_utils import (
    async_prepare_message_payload,
    safe_send_discord_message
)
from model_manager import GlobalModelManager

logger = logging.getLogger("ConsultingRunner")

READ_ONLY_GUARD_HEADER = (
    "[🚨 READ-ONLY ARCHITECTURAL CONSULTING GUARDRAIL - 엄격한 읽기 전용 가드레일]\n"
    "1. 본 작업은 소스코드 직접 수정 없이, 코드베이스를 정밀 탐색하여 사용자의 질문과 요구사항에 대한 '최적의 기술 방향성/아키텍처'를 모색하는 3중 읽기/자문 전용 모드(!mode read)입니다.\n"
    "2. `write_to_file`, `replace_file_content` 등의 쓰기/수정 도구를 호출하여 실제 파일이나 코드를 생성/수정해서는 절대로 안 됩니다 (Write Protected).\n"
    "3. `view_file`, `grep_search`, `find_by_name`, `list_dir` 등의 읽기 전용 도구를 적극 활용하여 기존 소스코드와 프로젝트 파일들을 깊이 있게 분석하십시오.\n"
    "4. 분석 결과, 접근 방식별 장단점 비교(Trade-offs), 추천 아키텍처, 단계별 실행 마일스톤을 명확한 마크다운 텍스트로 브리핑하십시오.\n\n"
)


class ConsultingRunner:
    """
    🔍 Dedicated 3-Agent Collaborative Pipeline for Read-Only Architectural Consulting & Direction Planning:
    
    1. 👨‍💻 Lead Architect (Gemini 3.7 Flash High) : Codebase exploration (view_file, grep_search), architectural analysis, structural trade-offs.
    2. 🕵️ Tech Reviewer & Critic (Gemini 3.1 Pro High) : Senior architect critique, edge-cases, tech debt risk check.
    3. ⚖️ Chief Strategist / PO (Gemini 3.1 Pro High) : Final consensus, optimal roadmap, and executive recommendation.
    """

    @staticmethod
    def is_system_error(text: str) -> bool:
        if not text or not isinstance(text, str):
            return False
        stripped = text.strip()
        if (
            stripped.startswith("🎉")
            or "아키텍처 자문 완료" in stripped
            or "최종 방향성 권고" in stripped
            or "[STATUS: CONSULTING_COMPLETE]" in stripped
            or "[STATUS: APPROVED]" in stripped
        ):
            return False
        error_prefixes = (
            "⚠️ 실행 중 오류가 발생했습니다",
            "⚠️ 오류 발생:",
            "⚠️ 자문 실행 중 오류 발생:",
        )
        return any(stripped.startswith(prefix) for prefix in error_prefixes)

    @classmethod
    async def run_pipeline(
        cls,
        session: ChannelSession,
        prompt: str,
        main_channel: discord.abc.Messageable,
        qa_client: Optional[discord.Client] = None,
        auditor_client: Optional[discord.Client] = None,
        on_status_update: Optional[Callable[[str], Any]] = None,
        max_rounds: int = 2
    ) -> str:
        session.is_busy = True
        latest_architect_output = ""
        latest_qa_output = ""
        latest_auditor_output = ""
        channel_id = getattr(main_channel, "id", None)

        noti_channel = None
        if hasattr(main_channel, "guild") and main_channel.guild:
            noti_channel = main_channel.guild.get_channel(NOTIFICATION_CHANNEL_ID)

        async def send_transit_notice(text: str):
            tag = f"📍 [<#{channel_id}>] " if channel_id else ""
            full_notice = f"{tag}{text}"

            if noti_channel:
                try:
                    import re
                    # Remove discord user mentions like <@1234> or <@!1234> to prevent tagging in noti channel
                    noti_text = re.sub(r"<@!?&?\d+>\s*", "", full_notice)
                    await noti_channel.send(noti_text)
                except Exception as e:
                    logger.debug(f"Could not send transit notice to noti channel: {e}")

            main_id = getattr(main_channel, "id", None)
            if main_channel and main_id != NOTIFICATION_CHANNEL_ID:
                try:
                    await main_channel.send(text)
                except Exception:
                    pass

        qa_channel = None
        if qa_client and channel_id and qa_client.is_ready():
            qa_channel = qa_client.get_channel(channel_id)
            if not qa_channel:
                try:
                    qa_channel = await qa_client.fetch_channel(channel_id)
                except Exception as e:
                    logger.debug(f"Could not fetch QA channel {channel_id}: {e}")

        auditor_channel = None
        if auditor_client and channel_id and auditor_client.is_ready():
            auditor_channel = auditor_client.get_channel(channel_id)
            if not auditor_channel:
                try:
                    auditor_channel = await auditor_client.fetch_channel(channel_id)
                except Exception as e:
                    logger.debug(f"Could not fetch Auditor channel {channel_id}: {e}")

        feedback_for_architect = ""

        try:
            for round_num in range(1, max_rounds + 1):
                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 1: Lead Architect Turn
                # -------------------------------------------------------------
                architect_display = GlobalModelManager.get_role_model_display("coder")
                session.set_busy(True, f"🏛️ Lead Architect 코드베이스 탐색 및 설계 분석 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    await on_status_update(
                        f"🏛️ **[라운드 {round_num}/{max_rounds}] Lead Architect({architect_display}) 코드베이스 탐색 및 아키텍처 방향성 도출 중...**"
                    )

                if round_num == 1 and not feedback_for_architect:
                    architect_prompt = (
                        f"{READ_ONLY_GUARD_HEADER}"
                        f"[역할: 수석 소프트웨어 아키텍트 (Lead Software Architect / {architect_display})]\n\n"
                        f"사용자가 코드 수정을 원하지 않고, 현재 프로젝트 구조를 바탕으로 최적의 기술 방향성과 설계를 모색해달라고 요청했습니다:\n"
                        f"{prompt}\n\n"
                        f"🚨 **[임무 지침 - 읽기 전용 다각도 아키텍처 탐색]**:\n"
                        f"1. **🎯 사용자 질문에 대한 직접 답변 최우선**: 사용자의 질문에 대한 구체적이고 전문적인 분석을 최상단에 제공하십시오.\n"
                        f"2. **코드베이스 정밀 탐색**: `find_by_name`, `list_dir`, `grep_search`, `view_file` 도구를 적극 사용하여 현재 워크스페이스(`{session.workspace}`)의 구조, 주요 컴포넌트, 상태 관리를 파악하십시오.\n"
                        f"3. **다각도 접근 방식 비교**: 최소 2~3가지 기술적 대안(Option A vs Option B)을 제시하고 장단점을 비교하십시오.\n"
                        f"4. **권장 아키텍처 청사진**: 가장 추천하는 최적의 설계안을 명확히 기술하십시오.\n"
                        f"5. **[📉 케이브맨 토큰 다이어트]**: 인사말/수다 배제, 500~800자 이내의 명사형 종결 개조식으로 압축 작성하십시오.\n"
                        f"6. **절대 파일 수정 금지**: `write_to_file`이나 `replace_file_content`를 절대 실행하지 마십시오."
                    )
                else:
                    architect_prompt = (
                        f"{READ_ONLY_GUARD_HEADER}"
                        f"[아키텍처 설계안 보강 및 리스크 대응 요청]\n\n"
                        f"Tech Reviewer(QA)로부터 아래와 같은 비판적 검토 의견이 전달되었습니다:\n"
                        f"────────────────────────────────────────\n"
                        f"{feedback_for_architect}\n"
                        f"────────────────────────────────────────\n\n"
                        f"지적된 기술 부채 및 엣지 케이스를 반영하여 아키텍처 설계안을 정교하게 보강하십시오.\n"
                        f"(※ 절대 실제 소스코드 파일을 수정/생성하지 마십시오.)"
                    )

                async def on_architect_tool(tool_status: str):
                    if on_status_update:
                        await on_status_update(f"🏛️ **[Architect 코드 탐색 중]** {tool_status}")

                async with main_channel.typing():
                    latest_architect_output = await AgentRunner.run_prompt(
                        session=session,
                        prompt=architect_prompt,
                        on_tool_update=on_architect_tool,
                        model=CODER_MODEL
                    )

                if cls.is_system_error(latest_architect_output):
                    return latest_architect_output

                # Post Architect response to main channel
                architect_chunks, architect_file_attachments = await async_prepare_message_payload(
                    latest_architect_output,
                    workspace_root=session.workspace,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(architect_chunks):
                    files_payload = architect_file_attachments if idx == 0 else []
                    await safe_send_discord_message(main_channel, chunk, files=files_payload)

                transit_msg = f"🏛️ **[Architect 라운드 {round_num} 설계 완료]** Tech Reviewer에게 비판적 리스크 검토를 요청합니다."
                await send_transit_notice(transit_msg)

                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 2: Tech Reviewer & Critic Turn (Gemini 3.1 Pro High)
                # -------------------------------------------------------------
                session.set_busy(True, f"🕵️ Tech Reviewer 아키텍처 심층 비판 검토 중 ({round_num}/{max_rounds}R)")
                effective_qa_model, qa_display_name, quota_notice = await QuotaManager.resolve_qa_model()

                if quota_notice:
                    await send_transit_notice(quota_notice)

                if on_status_update:
                    await on_status_update(
                        f"🕵️ **[라운드 {round_num}/{max_rounds}] Tech Reviewer({qa_display_name})가 아키텍처 리스크, 기술 부채 및 엣지 케이스를 심층 검토합니다...**"
                    )

                qa_target_channel = qa_channel or main_channel
                qa_status_msg = await qa_target_channel.send(
                    f"🕵️ **[라운드 {round_num}/{max_rounds}] Tech Reviewer({qa_display_name}) 기술 검토 로그...**"
                )

                async def on_qa_tool(tool_status: str):
                    try:
                        await qa_status_msg.edit(content=f"🕵️ **[Tech Reviewer({qa_display_name}) 검토 중]**\n{tool_status}")
                    except Exception:
                        pass
                    if on_status_update:
                        await on_status_update(f"🕵️ **[Tech Reviewer 검토 중]** {tool_status}")

                qa_prompt = (
                    f"{READ_ONLY_GUARD_HEADER}"
                    f"[역할: 시니어 기술 검토관 & 비판자 (Senior Tech Reviewer & Critic / {qa_display_name})]\n\n"
                    f"사용자의 방향성 모색 요청:\n"
                    f"{prompt}\n\n"
                    f"Lead Architect의 1차 아키텍처 제안 요약:\n"
                    f"{compact_agent_context(latest_architect_output, max_chars=2000)}\n\n"
                    f"🚨 **[임무 지침 - 비판적 시각(Devil's Advocate) 검토]**:\n"
                    f"1. 필요 시 `view_file` 등을 실행하여 실제 소스코드와 일치하는지 정밀 확인하십시오.\n"
                    f"2. Lead Architect의 설계에서 간과된 **잠재적 성능 병목, 보안 취약점, 모듈 간 강결합, 마이그레이션 난이도, 미래의 기술 부채**를 가차 없이 지적하십시오.\n"
                    f"3. '단순 칭찬'은 지양하고, 실제 운영 환경에서 발생할 수 있는 엣지 케이스와 구체적 보완책을 제시하십시오.\n"
                    f"4. **[📉 케이브맨 토큰 다이어트]**: 인사말/수다 배제, 3~5줄 내외의 명사형 종결 개조식으로 간결하게 작성하십시오.\n"
                    f"5. (절대 파일 수정/생성 금지)"
                )

                try:
                    async with qa_target_channel.typing():
                        latest_qa_output = await AgentRunner.run_prompt(
                            session=session,
                            prompt=qa_prompt,
                            on_tool_update=on_qa_tool,
                            model=effective_qa_model,
                            conversation_id_attr="qa_conversation_id"
                        )
                finally:
                    try:
                        await qa_status_msg.delete()
                    except Exception:
                        pass

                if cls.is_system_error(latest_qa_output):
                    return latest_qa_output

                qa_chunks, qa_file_attachments = await async_prepare_message_payload(
                    latest_qa_output,
                    workspace_root=session.workspace,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(qa_chunks):
                    files_payload = qa_file_attachments if idx == 0 else []
                    await safe_send_discord_message(qa_target_channel, chunk, files=files_payload)

                qa_notice = f"🕵️ **[Tech Reviewer 라운드 {round_num} 검토 완료]** Chief Strategist에게 최종 종합 및 실행 로드맵 수립을 요청합니다."
                await send_transit_notice(qa_notice)

                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 3: Chief Strategist / PO Turn (Gemini 3.1 Pro High)
                # -------------------------------------------------------------
                session.set_busy(True, f"⚖️ Chief Strategist 최종 방향성 및 로드맵 수립 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    await on_status_update(
                        f"⚖️ **[라운드 {round_num}/{max_rounds}] Chief Strategist(Gemini 3.1 Pro High)가 기술 제안과 비판 검토를 종합하여 최종 로드맵을 확정합니다...**"
                    )

                auditor_target_channel = auditor_channel or main_channel
                auditor_status_msg = await auditor_target_channel.send(
                    f"⚖️ **[라운드 {round_num}/{max_rounds}] Chief Strategist(Gemini 3.1 Pro High) 종합 심사 로그...**"
                )

                async def on_auditor_tool(tool_status: str):
                    try:
                        await auditor_status_msg.edit(content=f"⚖️ **[Chief Strategist 종합 중]**\n{tool_status}")
                    except Exception:
                        pass
                    if on_status_update:
                        await on_status_update(f"⚖️ **[Chief Strategist 종합 중]** {tool_status}")

                auditor_prompt = (
                    f"{READ_ONLY_GUARD_HEADER}"
                    f"[역할: 최고 기술 전략가 & 제품 총괄 (Chief Technology Strategist & PO / {AUDITOR_MODEL})]\n\n"
                    f"사용자의 방향성 모색 요청:\n"
                    f"{prompt}\n\n"
                    f"Lead Architect의 아키텍처 제안 요약:\n"
                    f"{compact_agent_context(latest_architect_output, max_chars=2000)}\n\n"
                    f"Tech Reviewer의 비판적 검토 및 리스크 분석 요약:\n"
                    f"{compact_agent_context(latest_qa_output, max_chars=2000)}\n\n"
                    f"🚨 **[임무 지침 - 최종 방향성 권고안 및 단계별 실행 로드맵 확정]**:\n"
                    f"1. **🎯 사용자 질문에 대한 직접 답변 최우선**: 최상단에 사용자 질문에 대한 명쾌한 답변을 1:1로 직접 작성하십시오.\n"
                    f"2. Architect의 제안과 Reviewer의 비판을 종합하여, **최종 결론 및 가장 추천하는 최적의 경로(Recommended Path)**를 명확히 작성하십시오.\n"
                    f"3. 실제 구현에 착수할 때 참고할 수 있도록 **구체적인 3단계 마일스톤 실행 로드맵(Phase 1: 기반 설계 ➔ Phase 2: 핵심 모듈 구현 ➔ Phase 3: 검증/배포)**을 체계적으로 정의하십시오.\n"
                    f"4. **[📉 케이브맨 토큰 다이어트 & 가독성]**: 인사말/수다 배제, 500~800자 이내의 명사형 종결 개조식으로 압축 작성하십시오.\n"
                    f"5. 하단에 `[STATUS: CONSULTING_COMPLETE]`를 표기하십시오.\n"
                    f"6. (절대 파일 수정/생성 금지)"
                )

                try:
                    async with auditor_target_channel.typing():
                        latest_auditor_output = await AgentRunner.run_prompt(
                            session=session,
                            prompt=auditor_prompt,
                            on_tool_update=on_auditor_tool,
                            model=AUDITOR_MODEL,
                            conversation_id_attr="auditor_conversation_id"
                        )
                finally:
                    try:
                        await auditor_status_msg.delete()
                    except Exception:
                        pass

                auditor_chunks, auditor_file_attachments = await async_prepare_message_payload(
                    latest_auditor_output,
                    workspace_root=session.workspace,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(auditor_chunks):
                    files_payload = auditor_file_attachments if idx == 0 else []
                    await safe_send_discord_message(auditor_target_channel, chunk, files=files_payload)

                # Check if consensus reached
                if "[STATUS: CONSULTING_COMPLETE]" in latest_auditor_output or "[STATUS: APPROVED]" in latest_auditor_output or round_num >= max_rounds:
                    auditor_display = GlobalModelManager.get_role_model_display("auditor")
                    final_summary = (
                        f"🎉 🔍 **[3중 읽기/기획 전용 아키텍처 자문 완료 ({round_num}R)]**\n\n"
                        f"**1. 🏛️ Lead Architect ({architect_display})**: 코드베이스 전수 탐색 및 대안별 설계 분석 완료\n"
                        f"**2. 🕵️ Tech Reviewer ({qa_display_name})**: 잠재적 기술 부채 및 엣지 케이스 심층 검토 완료\n"
                        f"**3. ⚖️ Chief Strategist ({auditor_display})**: 최종 추천 방향성 및 단계별 실행 로드맵 수립 완료\n\n"
                        f"*(본 작업은 파일 수정 없이 100% 읽기 전용으로 안전하게 완결되었습니다.)*"
                    )
                    if noti_channel:
                        try:
                            tag = f"📍 [<#{channel_id}>] " if channel_id else ""
                            await noti_channel.send(f"{tag}{final_summary}")
                        except Exception:
                            pass
                    return latest_auditor_output

                feedback_for_architect = f"Chief Strategist 심사 피드백:\n{latest_auditor_output[:1000]}"

            return latest_auditor_output

        finally:
            session.set_busy(False, "정상 대기 중")
