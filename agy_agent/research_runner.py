import asyncio
import logging
from typing import Optional, Callable, Any
import discord

from config import (
    CODER_MODEL,
    AUDITOR_MODEL,
    ADMIN_CHANNEL_ID,
    NOTIFICATION_CHANNEL_ID,
    AUDITOR_MENTION_USER_ID,
)
from session_manager import ChannelSession
from agent_runner import AgentRunner
from thermal_guard import ThermalGuard
from trio_agent_runner import QuotaManager, TrioAgentRunner, compact_agent_context
from discord_utils import (
    async_prepare_message_payload,
    safe_send_discord_message
)
from model_manager import GlobalModelManager

logger = logging.getLogger("ResearchRunner")

class ResearchRunner:
    """
    Dedicated 3-Agent Collaborative Pipeline for Research, Market Surveys,
    Hardware Comparisons, and Purchasing Guides:
    
    1. 👨‍💻 Lead Analyst (Gemini 3.7 Flash) : Real-time web search (search_web), live price gathering, structured doc creation (.md/.html).
    2. 🕵️ Fact Checker (Gemini 3.1 Pro High) : Price & spec accuracy verification.
    3. ⚖️ Chief Auditor (Gemini 3.1 Pro High) : Executive feasibility & actionable decision check (One-shot approval).
    """

    @staticmethod
    def is_system_error(text: str) -> bool:
        if not text or not isinstance(text, str):
            return False
        stripped = text.strip()
        if (
            stripped.startswith("🎉")
            or "심층 리서치 완료" in stripped
            or "최종 감사 승인" in stripped
            or "[STATUS: AUDIT_PASSED]" in stripped
            or "[STATUS: APPROVED]" in stripped
        ):
            return False
        error_prefixes = (
            "⚠️ 실행 중 오류가 발생했습니다",
            "⚠️ 오류 발생:",
            "⚠️ 리서치 실행 중 오류 발생:",
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
        max_rounds: int = 3
    ) -> str:
        session.is_busy = True
        latest_analyst_output = ""
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

        feedback_for_analyst = ""

        try:
            for round_num in range(1, max_rounds + 1):
                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 1: Lead Analyst Turn
                # -------------------------------------------------------------
                analyst_display = GlobalModelManager.get_role_model_display("coder")
                session.set_busy(True, f"📚 Lead Analyst 실시간 리서치 진행 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    await on_status_update(
                        f"📚 **[라운드 {round_num}/{max_rounds}] Lead Analyst({analyst_display}) 실시간 웹 검색 및 최신 시세 수집 중...**"
                    )

                if round_num == 1 and not feedback_for_analyst:
                    analyst_prompt = (
                        f"[역할: 수석 실시간 리서치 애널리스트 (Chief Live Research Analyst / {analyst_display})]\n\n"
                        f"사용자가 다음과 같은 심층 리서치/제품 비교/구매 가이드 작성을 요청했습니다:\n"
                        f"────────────────────────────────────────\n"
                        f"{prompt}\n"
                        f"────────────────────────────────────────\n\n"
                        f"🚨 **[필수 지침 - 실시간 웹 검색(search_web) 필수 & 최신 시장 가격 확인]**:\n"
                        f"1. **과거 기억에 의존한 가격 작성 절대 금지**: 내부 학습 데이터 속 옛날 가격(예: 1~2년 전 N100 미니PC 10만원대 등)을 쓰지 마십시오. 최근 램/SSD 부품값 상승, 환율, 국내 정발 가격 차이가 심하므로 반드시 실시간 검색으로 확인해야 합니다.\n"
                        f"2. **`search_web` 필수 다각도 검색**: 보고서를 쓰기 전에 반드시 `search_web` 도구를 3~5회 이상 실행하여 다나와, 네이버 쇼핑, 쿠팡, 알리익스프레스 등의 **현재 실제 최저가, 옵션별(RAM 8G vs 16G, SSD 256G vs 512G) 실구매가, 재고 현황**을 직접 확인하십시오.\n"
                        f"3. **사용자 예산(Budget) 정직한 반영 & 현실적 대안 제시**: 사용자가 제시한 예산 범위(예: 10~20만원)에서 지금 실제로 구매 가능한 옵션(알리 직구 특가, 중고 매물, 가성비 대체 모델 등)을 명확히 안내하고, 국내 신품 가격이 예산을 초과한다면 솔직하게 실시간 시세를 기재하십시오.\n"
                        f"4. **완성형 로컬 문서 영구 보관**: 상세 비교표, 제원, 실시간 가격 출처, 실전 체크리스트를 워크스페이스 내 파일(예: `수집/` 또는 루트 폴더에 `.md` 및 브라우저 열람용 `.html` 파일)로 완전히 작성하여 저장하십시오. (`write_to_file` 시 `ArtifactMetadata` 파라미터 제외 필수)\n"
                        f"5. **디스코드 출력 최적화**: 디스코드 채팅창에는 1,500자 이내의 깔끔한 핵심 요약과 로컬 파일 링크를 안내하여 메시지가 잘리지 않도록 단정하게 출력하십시오."
                    )
                else:
                    analyst_prompt = (
                        f"[실시간 리서치 팩트체크 및 최신 시세 보강 요청]\n\n"
                        f"시니어 팩트체커(QA)로부터 아래와 같은 피드백이 전달되었습니다:\n"
                        f"────────────────────────────────────────\n"
                        f"{feedback_for_analyst}\n"
                        f"────────────────────────────────────────\n\n"
                        f"반드시 `search_web` 도구를 실행하여 지적된 실시간 시장 가격, 재고, 옵션별 최저가를 다시 확인하고 로컬 파일(`.md`, `.html`)과 요약본을 갱신하십시오.\n"
                        f"(도구 주의: `replace_file_content` 사용 권장, `write_to_file` 호출 시 `ArtifactMetadata` 금지)"
                    )

                async def on_analyst_tool(tool_status: str):
                    if on_status_update:
                        await on_status_update(f"📚 **[Analyst 실시간 리서치 중]** {tool_status}")

                async with main_channel.typing():
                    latest_analyst_output = await AgentRunner.run_prompt(
                        session=session,
                        prompt=analyst_prompt,
                        on_tool_update=on_analyst_tool,
                        model=CODER_MODEL
                    )

                if cls.is_system_error(latest_analyst_output):
                    return latest_analyst_output

                # Post Analyst response to main channel
                analyst_chunks, analyst_file_attachments = await async_prepare_message_payload(
                    latest_analyst_output,
                    workspace_root=session.workspace,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(analyst_chunks):
                    files_payload = analyst_file_attachments if idx == 0 else []
                    await safe_send_discord_message(main_channel, chunk, files=files_payload)

                analyst_notice = f"📚 **[Analyst 라운드 {round_num} 리서치 완료]** Fact Checker에게 실시간 가격 및 스펙 팩트 검증을 요청합니다."
                await send_transit_notice(analyst_notice)

                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 2: Fact Checker Turn (Gemini 3.1 Pro High)
                # -------------------------------------------------------------
                session.set_busy(True, f"🕵️ Fact Checker 데이터 검증 중 ({round_num}/{max_rounds}R)")
                effective_qa_model, qa_display_name, quota_notice = await QuotaManager.resolve_qa_model()

                if quota_notice:
                    await send_transit_notice(quota_notice)

                if on_status_update:
                    await on_status_update(
                        f"🕵️ **[라운드 {round_num}/{max_rounds}] Fact Checker({qa_display_name})가 스펙/가격 팩트체크 및 데이터 정합성 검증을 시작합니다...**"
                    )

                qa_target_channel = qa_channel or main_channel
                qa_status_msg = await qa_target_channel.send(
                    f"🕵️ **[라운드 {round_num}/{max_rounds}] Fact Checker({qa_display_name}) 검증 로그...**"
                )

                async def on_qa_tool(tool_status: str):
                    try:
                        await qa_status_msg.edit(content=f"🕵️ **[Fact Checker({qa_display_name}) 검증 중]**\n{tool_status}")
                    except discord.DiscordException:
                        pass

                qa_prompt = (
                    f"[역할: 시니어 팩트 체커 & 실시간 데이터 감사관 (Senior Fact Checker / {qa_display_name})]\n\n"
                    f"당신은 리서치 보고서의 팩트 오류, 가짜/과거 시세, 허구의 스펙을 철저히 색출하는 수석 팩트 체커입니다.\n"
                    f"사용자의 원본 요청 대비 Analyst의 리서치 결과물과 워크스페이스 내 생성된 파일(`.md`, `.html`)을 면밀히 검증하십시오.\n\n"
                    f"**[사용자 원본 요청]:**\n{prompt}\n\n"
                    f"**[Analyst 리서치 요약]:**\n{compact_agent_context(latest_analyst_output, max_chars=1500)}\n\n"
                    f"🔍 **[리서치 팩트 검증 관점]:**\n"
                    f"1. **가격 현실성**: 제시된 가격이 현재 실제 구매 가능한 시세인가? 최근 부품값(RAM/SSD) 상승이나 옵션별 실구매가가 반영되었는가? (필요시 `search_web` 직접 실행 검증)\n"
                    f"2. **스펙 정확도**: RAM 확장 여부(온보드 vs SO-DIMM), SSD 규격, 네트워크 포트 등 하드웨어 제원에 오기가 없는가?\n"
                    f"3. **산출물 완성도**: 워크스페이스 내 파일(`.md`, `.html`)이 온전하게 작성 및 저장되었는가?\n"
                    f"4. **[📉 케이브맨 토큰 다이어트]**: 인사말/수다 배제, 3~5줄 내외의 명사형 종결 개조식으로 간결하게 작성하십시오.\n"
                    f"5. **주의: 개발 코드/무인 운영 잣대로 평가하지 마십시오. 오직 '정보의 정확성과 유용성'만 평가하십시오.**\n\n"
                    f"**[검증 판정 규칙]:**\n"
                    f"1. 가격/스펙에 명백한 오류가 있거나 핵심 정보가 누락된 경우에만:\n"
                    f"   - 반드시 첫 줄에 `[STATUS: REJECTED]`를 표기하고, 보강 지침을 간결하게 제시하십시오.\n"
                    f"2. 데이터가 정확하고 사용자의 의도를 충실히 만족한다면:\n"
                    f"   - 반드시 첫 줄에 `[STATUS: APPROVED]`를 표기하고, 3~5줄 내외의 핵심 검증 요약을 작성하십시오."
                )

                qa_proxy = ChannelSession(
                    channel_id=session.channel_id,
                    workspace=session.workspace,
                    conversation_id=session.qa_conversation_id,
                    qa_conversation_id=None,
                    auditor_conversation_id=None,
                    mode="single"
                )

                try:
                    async with qa_target_channel.typing():
                        latest_qa_output = await AgentRunner.run_prompt(
                            session=qa_proxy,
                            prompt=qa_prompt,
                            on_tool_update=on_qa_tool,
                            model=effective_qa_model
                        )
                finally:
                    try:
                        await qa_status_msg.delete()
                    except discord.DiscordException:
                        pass

                if qa_proxy.conversation_id:
                    session.set_qa_conversation_id(qa_proxy.conversation_id)

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

                qa_approved = TrioAgentRunner.is_qa_approved(latest_qa_output)

                if not qa_approved:
                    feedback_for_analyst = f"[Fact Checker({qa_display_name}) 반려 사유]\n{latest_qa_output}"
                    session.set_status("busy", f"⚠️ Fact Checker 반려 ➔ Analyst 보강 중 ({round_num}R)")
                    await send_transit_notice(f"❌ **[Fact Checker 라운드 {round_num} 반려]** Analyst에게 데이터 보강을 지시합니다.")
                    continue

                await send_transit_notice(f"✅ **[Fact Checker 라운드 {round_num} 승인]** Chief Auditor에게 최종 감사를 요청합니다.")
                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 3: Chief Auditor Turn (Gemini 3.1 Pro High) - One-Shot Fast Approval
                # -------------------------------------------------------------
                session.set_busy(True, f"⚖️ Chief Auditor 최종 승인 검토 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    await on_status_update(
                        f"⚖️ **[라운드 {round_num}/{max_rounds}] Chief Auditor(Gemini 3.1 Pro High)가 실전 구매 적합성 최종 감사를 시작합니다...**"
                    )

                auditor_target_channel = auditor_channel or main_channel
                auditor_status_msg = await auditor_target_channel.send(
                    f"⚖️ **[라운드 {round_num}/{max_rounds}] Chief Auditor 최종 감사 로그...**"
                )

                async def on_auditor_tool(tool_status: str):
                    try:
                        await auditor_status_msg.edit(content=f"⚖️ **[Chief Auditor 최종 검토 중]**\n{tool_status}")
                    except discord.DiscordException:
                        pass

                auditor_prompt = (
                    f"[역할: 수석 전략 기획 및 실전 의사결정 총괄 감사관 (Chief Strategic Auditor / {AUDITOR_MODEL})]\n\n"
                    f"당신은 사용자의 실질적인 구매 및 의사결정을 대변하는 최종 감사관입니다.\n"
                    f"Fact Checker(QA)가 이미 데이터 정합성을 검증하고 승인했습니다.\n"
                    f"당신은 '사용자가 지금 바로 구매/결정을 내리기에 유용한 최선의 추천과 실전 정보가 정리되었는가?'만 최종 확인하십시오.\n\n"
                    f"**[사용자 원본 요청]:**\n{prompt}\n\n"
                    f"**[Analyst 리서치 요약]:**\n{compact_agent_context(latest_analyst_output, max_chars=1500)}\n\n"
                    f"**[Fact Checker 검증 요약]:**\n{compact_agent_context(latest_qa_output, max_chars=800)}\n\n"
                    f"🚨 **[감사 판정 및 출력 양식]**:\n"
                    f"1. **🎯 사용자 질문에 대한 직접 답변 최우선**: 최상단에 사용자 질문에 대한 명쾌한 답변을 1:1로 직접 작성하십시오.\n"
                    f"2. 사용자 질문 의도에 부합하는 추천 모델, 가격, 스펙 비교, 실전 팁이 갖추어졌다면 **반드시 첫 줄에 `[STATUS: AUDIT_PASSED]`를 표기하여 즉시 승인하십시오.**\n"
                    f"3. **[📉 케이브맨 토큰 다이어트 & 가독성]**: 인사말/수다 배제, 500~800자 이내의 명사형 종결 개조식(핵심 추천 모델, 장단점 비교, 구매 가이드)으로 압축 작성하십시오.\n"
                    f"4. 하단에 `---\n### 🛡️ 3중 리서치 팩트체크 검증 요약`을 콤팩트하게 덧붙여주십시오."
                )

                auditor_proxy = ChannelSession(
                    channel_id=session.channel_id,
                    workspace=session.workspace,
                    conversation_id=session.auditor_conversation_id,
                    qa_conversation_id=None,
                    auditor_conversation_id=None,
                    mode="single"
                )

                try:
                    async with auditor_target_channel.typing():
                        latest_auditor_output = await AgentRunner.run_prompt(
                            session=auditor_proxy,
                            prompt=auditor_prompt,
                            on_tool_update=on_auditor_tool,
                            model=AUDITOR_MODEL
                        )
                finally:
                    try:
                        await auditor_status_msg.delete()
                    except discord.DiscordException:
                        pass

                if auditor_proxy.conversation_id:
                    session.set_auditor_conversation_id(auditor_proxy.conversation_id)

                if cls.is_system_error(latest_auditor_output):
                    return latest_auditor_output

                auditor_passed = TrioAgentRunner.is_auditor_approved(latest_auditor_output)

                # Prepend user mention on AUDIT_PASSED for push notification
                auditor_send_text = latest_auditor_output
                if auditor_passed and AUDITOR_MENTION_USER_ID:
                    user_mention = f"<@{AUDITOR_MENTION_USER_ID}>"
                    if user_mention not in auditor_send_text:
                        if "[STATUS: AUDIT_PASSED]" in auditor_send_text:
                            auditor_send_text = auditor_send_text.replace("[STATUS: AUDIT_PASSED]", f"{user_mention} [STATUS: AUDIT_PASSED]", 1)
                        elif "STATUS: AUDIT_PASSED" in auditor_send_text:
                            auditor_send_text = auditor_send_text.replace("STATUS: AUDIT_PASSED", f"{user_mention} [STATUS: AUDIT_PASSED]", 1)
                        else:
                            auditor_send_text = f"{user_mention} {auditor_send_text}"

                auditor_chunks, auditor_file_attachments = await async_prepare_message_payload(
                    auditor_send_text,
                    workspace_root=session.workspace,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(auditor_chunks):
                    files_payload = auditor_file_attachments if idx == 0 else []
                    await safe_send_discord_message(auditor_target_channel, chunk, files=files_payload)

                if auditor_passed:
                    session.set_status("idle", "🟢 최종 감사 승인 완료 (정상 대기)")
                    user_mention_str = f"<@{AUDITOR_MENTION_USER_ID}> " if AUDITOR_MENTION_USER_ID else ""
                    success_tag = f"{user_mention_str}📚 **[3중 에이전트 심층 리서치 완료 - 최종 승인]** (라운드 {round_num}/{max_rounds})"
                    await send_transit_notice(success_tag)
                    
                    # Clean up STATUS tags for the final executive brief
                    clean_auditor = latest_auditor_output.replace("[STATUS: AUDIT_PASSED]", "").replace("STATUS: AUDIT_PASSED", "").strip()
                    
                    final_brief = f"{success_tag}\n\n{clean_auditor}"
                    return final_brief
                else:
                    feedback_for_analyst = f"[Chief Auditor({AUDITOR_MODEL}) 반려 사유]\n{latest_auditor_output}"
                    session.set_status("busy", f"⚠️ Auditor 반려 ➔ Analyst 보강 중 ({round_num}R)")
                    await send_transit_notice(f"❌ **[Chief Auditor 라운드 {round_num} 반려]** Analyst에게 데이터 보강을 지시합니다.")

            # Max rounds reached
            session.set_status("warning", f"⚠️ 최대 라운드({max_rounds}회) 도달")
            return f"⚠️ **[3중 에이전트 심층 리서치 완료 - 최대 라운드({max_rounds}회) 도달]**"

        except asyncio.CancelledError:
            if session.current_process:
                session.current_process.terminate()
            session.set_status("idle", "정상 대기 중 (작업 취소)")
            raise
        except Exception as e:
            logger.exception("ResearchRunner 실행 중 오류 발생")
            session.set_status("error", f"⚠️ 오류 발생: {str(e)[:30]}")
            return f"⚠️ 리서치 실행 중 오류 발생: {str(e)}"
        finally:
            session.set_busy(False, session.status_desc or "정상 대기 중")
            session.current_process = None
