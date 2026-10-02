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
    RESEARCH_MAX_ROUNDS,
)
from session_manager import ChannelSession
from agent_runner import AgentRunner
from thermal_guard import ThermalGuard
from trio_agent_runner import QuotaManager, TrioAgentRunner, compact_agent_context
from round_control import continual_round_sequence
from discord_utils import (
    async_prepare_message_payload,
    safe_send_discord_message
)
from model_manager import GlobalModelManager
from output_guard import REWRITE_ONLY_NOTE
from convergence_policy import (
    ConvergenceTelemetry,
    ConvergenceTracker,
    build_blocking_contract_instruction,
    resolve_blocking_count,
)
import role_authority

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
        max_rounds: int = RESEARCH_MAX_ROUNDS
    ) -> str:
        session.is_busy = True
        latest_analyst_output = ""
        latest_qa_output = ""
        latest_auditor_output = ""
        channel_id = getattr(main_channel, "id", None)
        # 🎯 [수렴 계약] 새 미션 시작 시 해당 채널의 차단 결함 추이 기록을 초기화한다.
        ConvergenceTracker.reset(channel_id or 0)

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
            # 라운드 상한 도달 시 곧바로 종료하지 않고 예/아니요로 계속 여부를 확인한다.
            async for round_num, max_rounds in continual_round_sequence(
                1, max_rounds, channel=main_channel, batch_size=RESEARCH_MAX_ROUNDS
            ):
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

                # 🛡️ [출력 가드] Analyst 보고가 비한국어 문장이면 한국어 재작성 1회 (판정 태그 강제 없음).
                latest_analyst_output = await TrioAgentRunner.guard_output(
                    latest_analyst_output,
                    approved=None,
                    kind=None,
                    repair_note=REWRITE_ONLY_NOTE,
                    rerun=(lambda _p: AgentRunner.run_prompt(
                        session=session, prompt=_p, model=CODER_MODEL)),
                )

                # 🛡️ [역할 권한 물리 강제] Analyst(Coder 계열)가 QA 판정 태그를 선언하면 제거한다.
                latest_analyst_output, _removed_analyst_verdicts = role_authority.strip_coder_verdict_tags(latest_analyst_output)
                if _removed_analyst_verdicts:
                    logger.warning(
                        f"ResearchRunner: Analyst 출력에서 QA 전용 판정 태그 {_removed_analyst_verdicts} 제거(역할 전도 차단)."
                    )

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
                    f"4. **[🗣️ 충분한 검증 의견 개진]**: 인사말/수다는 배제하되 분량 제한 없이, 검증 근거·판정 이유·잔여 리스크를 충분히 구체적으로 서술하십시오.\n"
                    f"5. **주의: 개발 코드/무인 운영 잣대로 평가하지 마십시오. 오직 '정보의 정확성과 유용성'만 평가하십시오.**\n"
                    f"6. **[🚫 환각·골대이동 금지]**: 지적 대상이 Analyst 산출물/워크스페이스 파일에 **실제로 존재함을 확인한 뒤에만** 반려하십시오. "
                    f"'브라우저 미연결로 재실행 불가', '해시·타임스탬프·파일 포맷', '문서 스타일', '환경 제약'처럼 정보의 정확성과 무관한 항목은 "
                    f"반려 사유가 아니라 [차기 백로그]입니다. 검증 불가 항목은 `BLOCKED`로만 기록하고 반려로 승격하지 마십시오. "
                    f"이미 승인한 항목을 다시 반려하거나 새로운 기준을 소급 적용하지 마십시오.\n\n"
                    f"{build_blocking_contract_instruction()}\n\n"
                    f"**[검증 판정 규칙]:**\n"
                    f"1. 가격/스펙의 명백한 오류 또는 핵심 정보 누락(차단 결함)이 있을 때만:\n"
                    f"   - 첫 줄에 `[STATUS: REJECTED]`, 둘째 줄에 `[BLOCKING_DEFECTS: N]`을 표기하고, 보강 지침을 구체적 근거와 함께 제시하십시오.\n"
                    f"2. 데이터가 정확하고 사용자의 의도를 충실히 만족한다면:\n"
                    f"   - 첫 줄에 `[STATUS: APPROVED]`, 둘째 줄에 `[BLOCKING_DEFECTS: 0]`을 표기하고, 검증 통과 근거와 잔여 리스크를 서술하십시오.\n"
                    f"3. 비차단 항목(스타일/문서/포맷/환경 제약)은 [차기 백로그]로만 기록하며 반려 사유로 삼지 마십시오."
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

                # 🛡️ [출력 가드] 판정 태그 첫 줄 강제 + 비한국어 응답 한국어 재작성.
                latest_qa_output = await TrioAgentRunner.guard_output(
                    latest_qa_output,
                    approved=TrioAgentRunner.is_qa_approved(latest_qa_output),
                    kind="qa",
                    rerun=(lambda _p: TrioAgentRunner.run_qa_turn(session=session, prompt=_p)),
                )

                qa_approved = TrioAgentRunner.is_qa_approved(latest_qa_output)

                # 🎯 수렴 계약 (Convergence & Honesty Contract)
                #   Fact Checker 반려가 "비차단 항목만 잔존"인지 "차단 결함 정체"인지 기계 판정한다.
                #   마커([BLOCKING_DEFECTS: N])가 없으면 기존 동작을 그대로 보존한다(보수적 폴백).
                if not qa_approved:
                    try:
                        _blk_count, _blk_src = resolve_blocking_count(latest_qa_output)
                        if _blk_count is not None:
                            ConvergenceTracker.record(channel_id or 0, _blk_count)
                        _force_accept = ConvergenceTracker.should_force_accept(channel_id or 0)
                        _escalate = (not _force_accept) and ConvergenceTracker.should_escalate(channel_id or 0)
                        ConvergenceTelemetry.record(
                            channel_id or 0, "fact_checker", _blk_count, _blk_src,
                            "force_accept" if _force_accept else ("escalate" if _escalate else "observed"),
                        )
                        if _force_accept:
                            logger.info(
                                "ResearchRunner: Convergence Contract — Fact Checker rejected but "
                                "[BLOCKING_DEFECTS: 0]; accepting with non-blocking backlog."
                            )
                            session.set_status("busy", "📉 비차단 항목만 잔존 → 수렴 승인 처리")
                            qa_approved = True
                            latest_qa_output = role_authority.format_system_gate_override(
                                approved=True,
                                title="✅ **[시스템 게이트 수렴 승인 — 비차단 항목만 잔존]**",
                                blocking_defects=0,
                                detail=(
                                    "Fact Checker가 반려를 선언했으나 `[BLOCKING_DEFECTS: 0]` — 남은 항목은 정보의 "
                                    "정확성과 무관한 비차단(스타일/문서/포맷/환경 제약) 항목뿐입니다. 수렴 계약에 따라 "
                                    "승인 처리하고 잔여 항목은 [차기 백로그]로 이관합니다."
                                ),
                                original=latest_qa_output,
                            )
                            await send_transit_notice("📉 **[Fact Checker 반려 무효 — 비차단 항목만 잔존]** 차단 결함 0건 → 수렴 승인 처리합니다.")
                        elif _escalate:
                            _trend = ConvergenceTracker.describe(channel_id or 0)
                            logger.warning(
                                f"ResearchRunner: Convergence Contract — blocking defects not decreasing. {_trend}"
                            )
                            session.set_status("warning", "🛑 차단 결함 수렴 실패 (No-Progress)")
                            _conv_notice = (
                                f"🚨 **[리서치 수렴 실패 차단 — 차단 결함 감소 정지]**\n{_trend}\n"
                                f"차단 결함이 2회 연속 줄지 않아 상한까지 라운드를 소진하기 전에 중단합니다. "
                                f"사용자 개입 또는 범위 축소가 필요합니다."
                            )
                            await send_transit_notice(_conv_notice)
                            return _conv_notice
                    except Exception as _conv_err:
                        logger.debug(f"ResearchRunner convergence contract evaluation skipped: {_conv_err}")

                qa_chunks, qa_file_attachments = await async_prepare_message_payload(
                    latest_qa_output,
                    workspace_root=session.workspace,
                    excluded_hashes=getattr(session, "user_reference_hashes_set", set()),
                    excluded_dhashes=getattr(session, "user_reference_dhashes_set", set())
                )
                for idx, chunk in enumerate(qa_chunks):
                    files_payload = qa_file_attachments if idx == 0 else []
                    await safe_send_discord_message(qa_target_channel, chunk, files=files_payload)

                if not qa_approved:
                    feedback_for_analyst = f"[Fact Checker({qa_display_name}) 반려 사유]\n{latest_qa_output}"
                    session.set_status("busy", f"⚠️ Fact Checker 반려 ➔ Analyst 보강 중 ({round_num}R)")
                    await send_transit_notice(f"❌ **[Fact Checker 라운드 {round_num} 반려]** Analyst에게 데이터 보강을 지시합니다.")
                    continue

                await send_transit_notice(f"✅ **[Fact Checker 라운드 {round_num} 승인]** Chief Auditor에게 최종 감사를 요청합니다.")
                await ThermalGuard.check_round_transition_cooldown(main_channel)

                # -------------------------------------------------------------
                # Step 3: Chief Auditor Turn (config.AUDITOR_MODEL) - One-Shot Fast Approval
                # -------------------------------------------------------------
                auditor_display = GlobalModelManager.get_role_model_display("auditor")
                session.set_busy(True, f"⚖️ Chief Auditor 최종 승인 검토 중 ({round_num}/{max_rounds}R)")
                if on_status_update:
                    await on_status_update(
                        f"⚖️ **[라운드 {round_num}/{max_rounds}] Chief Auditor({auditor_display})가 실전 구매 적합성 최종 감사를 시작합니다...**"
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
                    f"3. 하단에 `---\n### 🛡️ 3중 리서치 팩트체크 검증 요약`을 콤팩트하게 덧붙여주십시오."
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

                # 🛡️ [출력 가드] 감사 판정 태그 첫 줄 강제 + 비한국어 응답 한국어 재작성.
                latest_auditor_output = await TrioAgentRunner.guard_output(
                    latest_auditor_output,
                    approved=auditor_passed,
                    kind="auditor",
                    rerun=(lambda _p: TrioAgentRunner.run_auditor_turn(session=session, prompt=_p)),
                )

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
                    # 📊 수렴 계약 계측: 마커 방출률·수렴/에스컬레이션을 물리 리포트로 남긴다.
                    try:
                        ConvergenceTelemetry.export(
                            channel_id or 0, session.workspace, milestone=prompt[:60]
                        )
                    except Exception as _tel_err:
                        logger.debug(f"Convergence telemetry export skipped: {_tel_err}")
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
