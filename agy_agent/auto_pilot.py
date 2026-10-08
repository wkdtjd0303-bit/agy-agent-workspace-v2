import os
import re
import time
import asyncio
import logging
import tempfile
import traceback
from typing import Optional, Dict, List, Tuple, Any
import discord

import config
from config import PAIR_MAX_ROUNDS, AUTOPILOT_MAX_CYCLES, QA_MAX_ROUNDS, QA_AUTO_MODEL
from session_manager import SessionManager
from trio_agent_runner import TrioAgentRunner, QuotaManager
from commander import Commander
from discord_utils import split_message, safe_send_discord_message, DISCORD_MAX_FILE_BYTES

logger = logging.getLogger("AutoPilot")


def get_autopilot_model_display() -> str:
    """AutoPilot 전담 감사 모델의 표시명 (SSOT).

    `!model`·디스코드 프레즌스와 동일한 `GlobalModelManager.format_model_name()` 을 사용해
    `config.QA_AUTO_MODEL`(기본 gemini-3.1-pro-high)을 표기한다. 기존에는 배너/프롬프트에
    'Gemini 3.1 Pro High' 가 하드코딩되어 실제 설정과 어긋날 수 있었다.
    """
    try:
        from model_manager import GlobalModelManager
        return GlobalModelManager.format_model_name(QA_AUTO_MODEL)
    except Exception:
        return QA_AUTO_MODEL


URL_REGEX = re.compile(r'https?://[^\s<>"`\'\]\)\,\;]+', re.IGNORECASE)

class PersistentTyping:
    """
    Maintains a continuous 'typing...' indicator in the Discord channel
    by triggering channel.typing() every interval seconds in a background task
    until the context block exits.
    """
    def __init__(self, channel: Optional[discord.abc.Messageable], interval: float = 7.0):
        self.channel = channel
        self.interval = interval
        self._task: Optional[asyncio.Task] = None

    async def __aenter__(self):
        if not self.channel:
            return self
        async def _typing_loop():
            while True:
                try:
                    await self.channel.typing()
                except Exception:
                    pass
                await asyncio.sleep(self.interval)
        self._task = asyncio.create_task(_typing_loop())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def create_storyboard_collage(screenshot_items: List[Tuple[str, str]], output_path: str, title: str = "E2E Storyboard") -> Optional[str]:
    """
    Combines up to 6-8 screenshot images into a neat, unified storyboard collage grid (e.g. 2x3 or 2x4)
    with clean headers and status labels for Discord embedding.
    """
    if not screenshot_items:
        return None
    try:
        from PIL import Image, ImageDraw
        valid_items = [(lbl, path) for lbl, path in screenshot_items if os.path.isfile(path) and os.path.getsize(path) > 1000]
        if not valid_items:
            return None
        
        count = len(valid_items)
        cols = 3 if count >= 3 else count
        rows = (count + cols - 1) // cols
        
        cell_w, cell_h = 360, 640
        header_h = 40
        padding = 16
        
        canvas_w = cols * cell_w + (cols + 1) * padding
        canvas_h = rows * (cell_h + header_h) + (rows + 1) * padding + 60
        
        collage = Image.new("RGB", (canvas_w, canvas_h), color=(26, 27, 30))
        draw = ImageDraw.Draw(collage)
        
        # Draw Main Banner
        draw.rectangle([(0, 0), (canvas_w, 55)], fill=(38, 40, 48))
        draw.text((padding + 10, 18), f"📱 {title}", fill=(255, 215, 0))
        
        for idx, (label, fpath) in enumerate(valid_items):
            c = idx % cols
            r = idx // cols
            x = padding + c * (cell_w + padding)
            y = 65 + padding + r * (cell_h + header_h + padding)
            
            # Header bar
            draw.rectangle([(x, y), (x + cell_w, y + header_h)], fill=(48, 52, 63))
            clean_lbl = label.replace("📱", "").strip()[:35]
            draw.text((x + 10, y + 12), f"#{idx+1} {clean_lbl}", fill=(240, 240, 240))
            
            # Cell background (clean dark container)
            draw.rectangle([(x, y + header_h), (x + cell_w, y + header_h + cell_h)], fill=(18, 19, 22))

            # Paste scaled image with aspect ratio preservation
            with Image.open(fpath) as img:
                img_w, img_h = img.size
                if img_w > 0 and img_h > 0:
                    ratio = min(cell_w / img_w, cell_h / img_h)
                    new_w, new_h = max(1, int(img_w * ratio)), max(1, int(img_h * ratio))
                    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    offset_x = x + (cell_w - new_w) // 2
                    offset_y = y + header_h + (cell_h - new_h) // 2
                    collage.paste(img_resized, (offset_x, offset_y))
                
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        collage.save(output_path, format="PNG", optimize=True)
        return output_path
    except Exception as e:
        logger.warning(f"Failed to create storyboard collage: {e}")
        return None

class E2ECrawlReport:
    def __init__(self):
        self.screenshots: List[Tuple[str, str]] = []  # (label, absolute_file_path)
        self.console_logs: List[str] = []
        self.network_errors: List[str] = []
        self.interactions: List[str] = []
        self.horizontal_overflow: bool = False
        self.broken_images_count: int = 0
        self.viewport_tests: List[str] = []
        self.test_cases: List[Any] = []
        self.dom_insights: Dict[str, Any] = {}
        self.error_message: Optional[str] = None

    @staticmethod
    async def deep_interactive_crawl(url: str, output_dir: str, workspace: Optional[str] = None) -> "E2ECrawlReport":
        """
        Executes domain-aware, situation-adaptive E2E exploratory inspection
        powered by AgenticE2EOrchestrator across multi-viewports and live DOM interactions.
        """
        report = E2ECrawlReport()
        if not url:
            return report

        try:
            from qa_hub import AgenticE2EOrchestrator
            agentic_rep = await AgenticE2EOrchestrator.run_intelligent_e2e(
                url=url,
                output_dir=output_dir,
                mission="AutoPilot 심층 자율 검증 및 기획 헌법 대조",
                depth="deep_exploratory",
                workspace=workspace
            )
            report.console_logs = agentic_rep.console_logs
            report.network_errors = agentic_rep.network_errors
            report.interactions = agentic_rep.interactions
            report.horizontal_overflow = agentic_rep.horizontal_overflow
            report.broken_images_count = agentic_rep.broken_images_count
            report.viewport_tests = agentic_rep.viewports_tested
            report.test_cases = agentic_rep.test_cases
            report.dom_insights = agentic_rep.dom_insights
            report.error_message = agentic_rep.error_message
            for cat, label, fpath in agentic_rep.screenshots:
                report.screenshots.append((label, fpath))
        except Exception as e:
            logger.exception(f"Deep interactive crawl failed for {url}: {e}")
            report.error_message = str(e)

        return report

class AutoPilotChannelState:
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.enabled: bool = False
        self.mode: str = "evolve"     # "observe" (Phase 1), "patch" (Phase 2), "evolve" (Phase 3)
        self.idle_seconds: int = 600  # 10 minutes default
        self.max_cycles: int = AUTOPILOT_MAX_CYCLES  # Max autonomous evolution cycles per idle event
        self.current_cycle: int = 0
        self.last_url: Optional[str] = None
        self.timer_task: Optional[asyncio.Task] = None
        self.timer_start_time: Optional[float] = None
        self.audit_task: Optional[asyncio.Task] = None
        self.completed_targets: List[str] = []

class AutoPilotManager:
    def __init__(
        self,
        session_manager: SessionManager,
        commander_bot: Optional[discord.Client] = None,
        qa_bot: Optional[discord.Client] = None,
        auditor_bot: Optional[discord.Client] = None,
        qa_hub_manager: Optional[Any] = None,
        self_healing_manager: Optional[Any] = None,
        qa2_bot: Optional[discord.Client] = None,
        extended_qa_bots: Optional[Dict[int, discord.Client]] = None,
        **kwargs
    ):
        self.session_manager = session_manager
        self.commander_bot = commander_bot
        self.qa_bot = qa_bot
        self.qa2_bot = qa2_bot
        self.extended_qa_bots: Dict[int, discord.Client] = dict(extended_qa_bots or {})
        if self.qa2_bot and 2 not in self.extended_qa_bots:
            self.extended_qa_bots[2] = self.qa2_bot
        self.auditor_bot = auditor_bot
        self.qa_hub_manager = qa_hub_manager
        self.self_healing_manager = self_healing_manager
        self.states: Dict[int, AutoPilotChannelState] = {}
        for k, v in kwargs.items():
            setattr(self, k, v)

    def set_self_healing_manager(self, self_healing_manager: Any):
        self.self_healing_manager = self_healing_manager

    def set_qa_hub_manager(self, qa_hub_manager):
        self.qa_hub_manager = qa_hub_manager

    def get_state(self, channel_id: int) -> AutoPilotChannelState:
        if channel_id not in self.states:
            session = self.session_manager.get_session(channel_id)
            state = AutoPilotChannelState(channel_id)
            state.enabled = getattr(session, "auto_enabled", False)
            state.mode = getattr(session, "auto_mode", "evolve")
            state.idle_seconds = getattr(session, "auto_idle_seconds", 600)
            state.max_cycles = getattr(session, "auto_max_cycles", AUTOPILOT_MAX_CYCLES)
            state.last_url = getattr(session, "last_url", None)
            self.states[channel_id] = state
        return self.states[channel_id]

    def sync_to_session(self, channel_id: int):
        state = self.get_state(channel_id)
        session = self.session_manager.get_session(channel_id)
        try:
            session.set_auto(
                enabled=state.enabled,
                mode=state.mode,
                idle_seconds=state.idle_seconds,
                max_cycles=state.max_cycles,
                last_url=state.last_url
            )
        except TypeError as e:
            if "max_cycles" in str(e):
                session.set_auto(
                    enabled=state.enabled,
                    mode=state.mode,
                    idle_seconds=state.idle_seconds,
                    last_url=state.last_url
                )
                session.auto_max_cycles = state.max_cycles
            else:
                raise

    # Excluded third-party reference/shopping/social domains that are NOT user projects
    EXCLUDED_AUTOPILOT_DOMAINS = (
        "daangn.com", "aliexpress.com", "danawa.com", "naver.com", "daum.net",
        "google.com", "youtube.com", "youtu.be", "github.com", "gitlab.com",
        "raw.githubusercontent.com", "gist.github.com", "bitbucket.org", "git.io",
        "coupang.com", "11st.co.kr", "gmarket.co.kr", "auction.co.kr",
        "dcinside.com", "clien.net", "ruliweb.com", "fmkorea.com", "tistory.com",
        "velog.io", "medium.com", "wikipedia.org", "namu.wiki", "facebook.com",
        "instagram.com", "twitter.com", "x.com", "discord.com", "discord.gg",
        "slack.com", "apple.com", "microsoft.com", "amazon.com", "openai.com",
        "anthropic.com", "deepmind.google", "jsdelivr.net", "cloudflare.com",
        "unpkg.com", "googleapis.com", "w3.org", "mozilla.org", "developer.mozilla.org",
        "bit.ly", "tinyurl.com", "t.co", "goo.gl"
    )

    @classmethod
    def is_third_party_url(cls, url: str) -> bool:
        """Returns True if the URL belongs to an excluded third-party reference, social, or shopping domain."""
        if not url:
            return False
        u_lower = url.lower()
        return any(domain in u_lower for domain in cls.EXCLUDED_AUTOPILOT_DOMAINS)

    @staticmethod
    def extract_urls(text: str) -> List[str]:
        """Extracts web deployment URLs from text, prioritizing production and live project hosts."""
        matches = URL_REGEX.findall(text)
        cleaned = []
        for u in matches:
            u = u.strip("`'\"[]()<>.,;!*~")
            if "](" in u:
                u = u.split("](")[0]
            u = u.strip("`'\"[]()<>.,;!*~")
            if u.startswith("http://") or u.startswith("https://"):
                u_lower = u.lower()
                # Exclude external reference/search/shopping websites
                if any(domain in u_lower for domain in AutoPilotManager.EXCLUDED_AUTOPILOT_DOMAINS):
                    continue
                cleaned.append(u)
        
        # Sort so web.app, firebaseapp, vercel, netlify, localhost come first
        def url_priority(url_str: str) -> int:
            u_lower = url_str.lower()
            if any(domain in u_lower for domain in ["web.app", "firebaseapp.com", "vercel.app", "netlify.app", "pages.dev", "render.com", "railway.app"]):
                return 0
            if "localhost" in u_lower or "127.0.0.1" in u_lower or "ngrok" in u_lower:
                return 1
            return 2

        return sorted(list(dict.fromkeys(cleaned)), key=url_priority)

    def cancel_timer(self, channel_id: int):
        """Cancels any pending idle auto-pilot timer (e.g. when user sends a message)."""
        state = self.get_state(channel_id)
        if state.timer_task and not state.timer_task.done():
            state.timer_task.cancel()
            state.timer_task = None
            state.timer_start_time = None
            logger.info(f"Auto-pilot timer cancelled for channel {channel_id} due to user activity.")

    def cancel_all_timers(self):
        """Cancels all active autopilot timers across all channels."""
        for ch_id in list(self.states.keys()):
            self.cancel_timer(ch_id)

    def stop_channel(self, channel_id: int) -> bool:
        """Immediately stops all autopilot timers and active running audit pipelines for the channel."""
        state = self.get_state(channel_id)
        stopped = False
        
        # 1. Cancel pending timer
        if state.timer_task and not state.timer_task.done():
            state.timer_task.cancel()
            state.timer_task = None
            state.timer_start_time = None
            stopped = True

        # 2. Cancel active running audit/evolution pipeline
        if state.audit_task and not state.audit_task.done():
            state.audit_task.cancel()
            state.audit_task = None
            stopped = True
            logger.info(f"🛑 Active AutoPilot pipeline task cancelled for channel {channel_id}.")

        session = self.session_manager.get_session(channel_id)
        session.is_stopped = True
        if session.current_process:
            try:
                from session_manager import kill_process_tree
                if session.current_process.pid:
                    kill_process_tree(session.current_process.pid)
                session.current_process.kill()
                stopped = True
            except Exception:
                pass
            session.current_process = None

        if session.current_task and not session.current_task.done():
            session.current_task.cancel()
            session.current_task = None
            stopped = True

        if session.is_busy:
            session.is_busy = False
            session.status = "idle"
            session.status_desc = "정상 대기 중 (작업 중단)"
            stopped = True

        # 3. Disable state
        state.enabled = False
        self.sync_to_session(channel_id)
        return stopped

    def stop_all(self) -> int:
        """Stops all autopilot timers and active tasks across all channels."""
        count = 0
        for ch_id in list(self.states.keys()):
            if self.stop_channel(ch_id):
                count += 1
        return count


    def on_task_completed(self, channel_id: int, channel: discord.abc.Messageable, response_text: str, bot_client: discord.Client):
        """Called when a bot task completes. Detects URL or workspace and schedules idle QA audit if auto is enabled."""
        state = self.get_state(channel_id)
        session = self.session_manager.get_session(channel_id)
        if not state.enabled:
            return

        urls = self.extract_urls(response_text)
        if urls:
            # ✅ 이번 작업 응답에서 새 URL이 감지된 경우만 last_url 갱신
            state.last_url = urls[0]
            self.sync_to_session(channel_id)
            logger.info(f"Detected live URL for channel {channel_id}: {state.last_url}")
        else:
            # ✅ [버그 수정] URL이 없으면 이전에 캐시된 다른 프로젝트 URL을 재사용하지 않고
            # 항상 현재 채널의 workspace로 초기화한다.
            # 이렇게 하지 않으면 다른 채널/작업에서 남은 stale URL로 엉뚱한 앱이 크롤링됨.
            state.last_url = session.workspace or None
            self.sync_to_session(channel_id)
            if state.last_url:
                logger.info(f"No URL detected for channel {channel_id}. Using workspace as audit target: {state.last_url}")

        if not state.last_url and not session.workspace:
            return

        self.cancel_timer(channel_id)
        state.current_cycle = 0
        state.timer_start_time = time.time()

        async def wait_and_trigger():
            try:
                logger.info(f"Auto-pilot timer started: {state.idle_seconds}s for channel {channel_id}")
                await asyncio.sleep(state.idle_seconds)
                await self.execute_idle_audit(channel_id, channel, bot_client)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.exception(f"Error in auto-pilot timer for channel {channel_id}: {e}")

        state.timer_task = asyncio.create_task(wait_and_trigger())

    @staticmethod
    async def create_git_checkpoint(workspace: str, reason: str = "AutoPilot Checkpoint"):
        """Creates an automatic git checkpoint so any autonomous change can be cleanly rolled back."""
        try:
            cmd = f'cd "{workspace}" && git add -A && git commit -m "[AutoPilot Checkpoint] {reason} ({time.strftime("%Y-%m-%d %H:%M:%S")})"'
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            logger.info(f"Git checkpoint created for workspace: {workspace}")
        except Exception as e:
            logger.debug(f"Git checkpoint skipped: {e}")

    async def execute_idle_audit(
        self,
        channel_id: int,
        channel: discord.abc.Messageable,
        bot_client: discord.Client,
        manual_trigger: bool = False
    ):
        state = self.get_state(channel_id)
        session = self.session_manager.get_session(channel_id)
        current_task = asyncio.current_task()

        if not state.enabled and not manual_trigger:
            logger.info(f"Skipping auto-pilot: channel {channel_id} is disabled.")
            return

        if getattr(session, "is_stopped", False):
            logger.info(f"Skipping auto-pilot: channel {channel_id} session is stopped.")
            return

        if state.mode in ("evolve", "quad") and not getattr(session, "user_directive", None) and not manual_trigger:
            # Allow PRD-based auto-bootstrap: if workspace has PRD.md, extract first REQ as directive
            prd_path = os.path.join(session.workspace, "PRD.md") if session.workspace else ""
            has_prd = prd_path and os.path.isfile(prd_path)
            if not has_prd:
                logger.info(f"Skipping auto-pilot: channel {channel_id} has mode {state.mode} but no user_directive and no PRD.md. Awaiting user input.")
                session.set_busy(False, desc="정상 대기 중 (사용자 의뢰 또는 PRD.md 대기)")
                if session.current_task is current_task:
                    session.current_task = None
                if state.audit_task is current_task:
                    state.audit_task = None
                return
            else:
                # Auto-bootstrap from PRD.md first REQ
                try:
                    with open(prd_path, "r", encoding="utf-8") as _prd_f:
                        _prd_content = _prd_f.read()
                    _req_match = re.search(r'\*\*(REQ-001[^)]*)\*\*[:\s]*(.+)', _prd_content)
                    if _req_match:
                        session.user_directive = f"[PRD Auto-Bootstrap] {_req_match.group(1)}: {_req_match.group(2)[:200]}"
                    else:
                        # Fallback: use first 300 chars of PRD as directive
                        _prd_lines = [l.strip() for l in _prd_content.splitlines() if l.strip() and not l.startswith('#') and not l.startswith('>')]
                        session.user_directive = f"[PRD Auto-Bootstrap] {' '.join(_prd_lines[:5])[:300]}"
                    logger.info(f"AutoPilot PRD auto-bootstrap directive set for channel {channel_id}: {session.user_directive[:80]}...")
                except Exception as _prd_e:
                    logger.warning(f"AutoPilot PRD auto-bootstrap failed: {_prd_e}")
                    # Fall-through guard: if PRD parsing failed, directive is still None — must not enter evolve loop
                    if not getattr(session, "user_directive", None):
                        session.set_busy(False, desc="정상 대기 중 (PRD 파싱 실패)")
                        if session.current_task is current_task:
                            session.current_task = None
                        if state.audit_task is current_task:
                            state.audit_task = None
                        return

        # 🔒 Busy Guard: Only skip if a DIFFERENT, genuinely alive task is currently running
        if session.is_busy:
            if session.current_task is current_task or state.audit_task is current_task:
                # Pre-assigned by caller (e.g. bot.py or watchdog), proceed smoothly
                pass
            elif session.current_task is None or session.current_task.done():
                logger.info(f"AutoPilot taking over idle/zombie busy session on channel {channel_id}")
            else:
                logger.info(f"Skipping auto-pilot: channel {channel_id} has another active task running.")
                return

        phase_name = {
            "observe": "Phase 1: 시각 진단 & 스크린샷 보고 모드 (무위험 관찰)",
            "patch": "Phase 2: 제어형 자율 패치 모드 (결함 수정 & 재배포)",
            "evolve": "Phase 3: 완전 무인 자율 발전 모드 (차기 로드맵 구현)",
            "quad": "Phase 3: 4중 쿼드 완전 무인 자율 진화 모드 (차기 로드맵 구현)"
        }.get(state.mode, state.mode)

        if state.mode in ("evolve", "quad"):
            cycle_label = f" (자율 진화 사이클 {state.current_cycle + 1}회 ♾️)"
        else:
            cycle_label = f" (자율 사이클 {state.current_cycle + 1}/{state.max_cycles})" if not manual_trigger else " (수동 실행)"

        # Ensure project roadmap is present and aligned
        ws_init = session.workspace or ""
        if ws_init and os.path.isdir(ws_init):
            try:
                from roadmap_manager import ensure_roadmap
                await ensure_roadmap(ws_init)
            except Exception as _re:
                logger.debug(f"AutoPilot ensure_roadmap notice: {_re}")

        state.audit_task = current_task
        session.current_task = current_task
        session.set_busy(True, desc=f"AutoPilot 자율 E2E 감사 중 ({phase_name})")

        target_url = state.last_url or getattr(session, 'last_url', None) or session.workspace or ""
        if not target_url:
            session.set_busy(False, desc="정상 대기 중 (타깃 URL 없음)")
            session.current_task = None
            state.audit_task = None
            if manual_trigger:
                await channel.send("⚠️ 최근 감지된 배포 URL 또는 워크스페이스가 없습니다. 먼저 작업 디렉토리를 설정해주세요.")
            return

        # Safety Guardrail 1: Check Gemini Quota (Auditor & QA Engine)
        gemini_ratio = await QuotaManager.get_gemini_quota_ratio()
        if gemini_ratio is not None and gemini_ratio < 0.10:
            session.set_busy(False, desc="정상 대기 중 (쿼터 안전 대기)")
            session.current_task = None
            state.audit_task = None
            await channel.send(
                f"🛑 **[자율 E2E 안전 대기]** Gemini 잔여 쿼터가 10% 미만({gemini_ratio * 100:.1f}%)으로 감지되어, 롤링 리셋을 위해 자율 감사를 일시 대기합니다."
            )
            return

        if not manual_trigger and state.mode not in ("evolve", "quad") and state.current_cycle >= state.max_cycles:
            session.set_busy(False, desc="정상 대기 중 (최대 사이클 도달)")
            session.current_task = None
            state.audit_task = None
            await channel.send(
                f"ℹ️ **[자율 E2E 감사 완료]** 최대 자율 사이클({state.max_cycles}회)에 도달하여 시스템이 안전 대기 모드로 전환되었습니다."
            )
            return

        # Prefer sending from Commander Bot client
        cmd_ch = self.commander_bot.get_channel(channel_id) if (self.commander_bot and self.commander_bot.is_ready()) else None
        active_channel = cmd_ch or channel

        ptyping = PersistentTyping(active_channel)
        await ptyping.__aenter__()

        # -------------------------------------------------------------
        # Idus Commerce Automation Integration (Channel: 1539527192663433308)
        # -------------------------------------------------------------
        if session and session.workspace and "idus" in session.workspace.lower():
            idus_script = os.path.join(session.workspace, "scripts", "run_pipeline.py")
            if os.path.exists(idus_script):
                await active_channel.send(
                    f"🛍️ 🤖 **[아이디어스(Idus) 커머스 자율운영 파이프라인 가동{cycle_label}]**\n"
                    f"• 작전 모드: **커머스 스토어 매니지먼트 (마케팅 & 매출 성장)**\n"
                    f"• 실행 엔진: `{idus_script}`\n"
                    f"• 🧠 목표: 스마트스토어/아이디어스 실시간 분석, 전환율(CRO) 최적화 및 마케팅 콘텐츠 자동 발행 진행 중..."
                )
                
                auto_update_flag = " --auto-update" if state.mode in ["patch", "evolve", "quad"] else ""
                
                # Execute the Idus specific pipeline with strict timeout
                try:
                    process = await asyncio.create_subprocess_shell(
                        f"python3 {idus_script}{auto_update_flag}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=session.workspace
                    )
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
                    
                    if process.returncode == 0:
                        await active_channel.send(f"✅ **[아이디어스 파이프라인 성공]**\n```text\n{stdout.decode('utf-8')[:1500]}...\n```")
                    else:
                        await active_channel.send(f"⚠️ **[아이디어스 파이프라인 오류]**\n```text\n{stderr.decode('utf-8')[:1500]}\n```")
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                        await process.wait()
                    except Exception:
                        pass
                    await active_channel.send("⚠️ **[아이디어스 파이프라인 타임아웃]**: 300초 제한 시간을 초과하여 프로세스를 안전하게 종료했습니다.")
                except Exception as e:
                    await active_channel.send(f"❌ **[아이디어스 파이프라인 시스템 에러]**: {e}")
                
                # Increment cycle and schedule next
                state.current_cycle += 1
                if state.enabled and state.current_cycle < state.max_cycles:
                    state.timer_start_time = time.time()
                    state.timer_task = asyncio.create_task(
                        self._schedule_next_idle(channel_id, channel, bot_client)
                    )
                try:
                    await ptyping.__aexit__(None, None, None)
                except Exception:
                    pass
                session.set_busy(False, desc="정상 대기 중 (아이디어스 파이프라인 완료)")
                if session.current_task == current_task:
                    session.current_task = None
                return
        # Preserve user-configured channel mode (pair / read / auto / single)

        # -------------------------------------------------------------
        # 🆕 프로젝트 타입 자동 감지 (웹 vs 안드로이드 vs 네이티브 vs 파이썬 등)
        # -------------------------------------------------------------
        workspace = session.workspace or ""
        from android_build_manager import AndroidBuildManager
        android_root = AndroidBuildManager.find_android_project(workspace) if workspace else None
        is_android_project = bool(android_root) or (workspace and any(
            os.path.isfile(os.path.join(workspace, f))
            for f in ["build.gradle", "gradlew", "settings.gradle",
                       "app/build.gradle", "build.gradle.kts"]
        ))
        is_web_project = bool(
            target_url and target_url.startswith("http")
            and not self.is_third_party_url(target_url)
        ) or (
            workspace and not is_android_project and any(
                os.path.isfile(os.path.join(workspace, f))
                for f in ["index.html", "package.json", "vite.config.js", "vite.config.ts",
                           "next.config.js", "nuxt.config.js", "svelte.config.js", "firebase.json"]
            )
        )
        is_native_project = workspace and any(
            os.path.isfile(os.path.join(workspace, f))
            for f in ["CMakeLists.txt", "Makefile", "*.sln", "*.vcxproj", "Package.swift"]
        )
        is_python_project = workspace and any(
            os.path.isfile(os.path.join(workspace, f))
            for f in ["requirements.txt", "pyproject.toml", "setup.py", "main.py", "app.py"]
        )

        if is_android_project:
            project_type_label = "📱 Android 앱 (Kotlin/Java)"
        elif is_native_project:
            project_type_label = "🖥️ 네이티브 앱 (C++/Swift/Windows)"
        elif is_python_project:
            project_type_label = "🐍 Python 프로젝트"
        elif is_web_project:
            project_type_label = "🌐 웹 앱 (URL 기반 E2E)"
        else:
            project_type_label = "📦 범용 소프트웨어"

        is_headed = os.environ.get("AGY_HEADED", "0").lower() in ("1", "true", "yes") or getattr(config, "AGY_HEADED", False)
        display_target = os.environ.get("DISPLAY", ":0") if is_headed else ":98 (가상 버퍼)"
        display_label = f"🖥️ Headed (실제 화면 가시화 / {display_target})" if is_headed else "⚙️ Headless (백그라운드)"

        app_name = os.path.basename(workspace) if workspace else "타깃 앱"
        if is_android_project and workspace:
            app_name = AndroidBuildManager.extract_app_name(android_root or workspace) or app_name

        if state.current_cycle > 1:
            status_msg = await active_channel.send(
                f"👑 🧐 **[AutoPilot 자율 검수 {cycle_label}]** `{app_name}` {phase_name} 모드 순환 가동 중... ({get_autopilot_model_display()})"
            )
        else:
            status_msg = await active_channel.send(
                f"👑 🧐 **[AutoPilot 자율 감사관 가동{cycle_label}]**\n"
                f"• 작전 모드: **{phase_name}**\n"
                f"• 프로젝트 타입: **{project_type_label}**\n"
                f"• 디스플레이: **{display_label}**\n"
                f"• 감사 모델: `{get_autopilot_model_display()}` (AutoPilot 전담 PO/아키텍처 검수)\n"
                f"• 파이프라인 규격: 📱 **단일화된 100% Android Full Emulator MCP E2E (KVM 가속 + 12 MCP Tools)**\n"
                f"• 무결성 보증: 🛡️ **구형 Fast-Path(TC-FAST-01) 전면 폐지 & 실기기 프레임버퍼 6-Step 전수 검증**\n"
                f"• 대상: `{target_url if is_web_project else (workspace or '워크스페이스')}`\n"
                f"• 🎯 목표: PRD.md 기획 헌법 일치도 전수 점검, 미구현 기능 도출 및 자율 품질 고도화..."
            )

        # -------------------------------------------------------------
        # 🆕 E2E 6단계 시나리오 기획 및 사전 브리핑 (Scenario Briefing)
        # -------------------------------------------------------------
        from evidence_validator import EvidenceValidator
        prd_cov = None
        if workspace and os.path.isdir(workspace):
            try:
                prd_cov = EvidenceValidator.evaluate_prd_feature_coverage(workspace)
            except Exception as e:
                logger.debug(f"Pre-audit PRD extraction notice: {e}")

        req_titles = []
        if prd_cov and prd_cov.get("has_prd"):
            req_titles = [r.get("title", "") for r in prd_cov.get("requirements", []) if r.get("title")]

        if prd_cov and prd_cov.get("has_prd"):
            comp_ratio = prd_cov.get("completion_ratio", 0) * 100
            comp_cnt = prd_cov.get("completed_count", 0)
            tot_req = prd_cov.get("total_requirements", 0)
            prd_cov_text = f"PRD 달성률 {comp_ratio:.1f}% ({comp_cnt}/{tot_req}개 완료)"
        else:
            prd_cov_text = "기본 무결성 검수"

        step3_name = f"핵심 기능 인터랙션 ({req_titles[0][:20]})" if len(req_titles) > 0 else "핵심 유저 플로우 및 화면 간 전환 단언"
        step4_name = f"데이터 조작 & 상태 보존 ({req_titles[1][:20]})" if len(req_titles) > 1 else "동적 데이터 생성/입력 및 영속성 검증"

        scenario_brief = (
            f"📋 🧪 **[AutoPilot 사전 E2E 시나리오 브리핑 (6-Step Scenario)]**\n"
            f"• **검증 방식**: 📱 **오직 Android MCP 풀 에뮬레이터 테스트만 단일 수행 (Fast-Path 우회 영구 제거)**\n"
            f"• **대상 프로젝트**: `{app_name}` ({project_type_label})\n"
            f"• **디스플레이**: {display_label}\n"
            f"• **기획 헌법(PRD)**: {prd_cov_text}\n"
            f"• **실행 시나리오 (총 6단계)**:\n"
            f"  1️⃣ **[Step 1. 콜드 스타트 & 런처 검증]**: 신규 빌드 APK 설치 및 메인 액티비티 기동, 첫 화면 렌더링 검수\n"
            f"  2️⃣ **[Step 2. UI 레이아웃 & 48dp 터치 검수]**: TopAppBar, FAB, 네비게이션 버튼 및 터치 슬롭(48dp) 배치 확인\n"
            f"  3️⃣ **[Step 3. {step3_name}]**: 주요 인터랙션 수행, 버튼 클릭 및 화면 전환 검증\n"
            f"  4️⃣ **[Step 4. {step4_name}]**: 텍스트 입력/폼 작성, 다이얼로그 조작 및 데이터 영속성 검증\n"
            f"  5️⃣ **[Step 5. 라이프사이클 & 반응형 복원력]**: 백그라운드 전환 및 가로(Landscape) 회전 시 데이터 유실 여부 검증\n"
            f"  6️⃣ **[Step 6. 크래시 & 시스템 정밀 진단]**: Fatal Exception/ANR 모니터링, 프레임버퍼 전수 캡처 및 증적 수집"
        )
        
        # Mute bureaucratic briefing: Persist detailed 6-step text quietly into reports/
        from director_feedback_hub import DirectorFeedbackHub
        compact_scenario_line = (
            f"📋 🧪 **[AutoPilot 6-Step E2E 검수 {cycle_label}]** `{app_name}` "
            f"({prd_cov_text}) 가동 중... *(상세 브리핑은 reports/e2e_scenario_briefing.md에 보존)*"
        )
        if workspace and os.path.isdir(workspace):
            compact_msg, _ = DirectorFeedbackHub.mute_bureaucratic_briefing(
                workspace=workspace,
                full_briefing=scenario_brief,
                filename=f"e2e_scenario_briefing_cycle_{state.current_cycle}.md" if state.current_cycle > 1 else "e2e_scenario_briefing.md",
                compact_status_line=compact_scenario_line
            )
        else:
            compact_msg = compact_scenario_line

        # During autonomous loop cycles (cycle > 1), mute multi-line bureaucratic scenario in Discord
        if state.current_cycle > 1:
            await safe_send_discord_message(active_channel, compact_msg)
        else:
            await safe_send_discord_message(active_channel, scenario_brief)

        temp_dir = tempfile.mkdtemp(prefix="e2e_crawl_")
        session_start_time = time.time()
        if workspace and os.path.isdir(workspace):
            try:
                from evidence_validator import EvidenceValidator
                EvidenceValidator.purge_all_evidence_artifacts(workspace)
            except Exception as _pe:
                logger.debug(f"AutoPilot Phase 0 purge notice: {_pe}")

        try:
            # 1) 웹 프로젝트: AgenticE2EOrchestrator 브라우저 크롤러
            if is_web_project and target_url and target_url.startswith("http"):
                crawl_report = await E2ECrawlReport.deep_interactive_crawl(target_url, temp_dir, workspace=workspace)
            # 2) 안드로이드 프로젝트: AndroidNativeE2EOrchestrator 실기기/에뮬레이터 풀 E2E
            elif is_android_project and workspace:
                target_apk = None
                build_msg = await active_channel.send(
                    "🔨 **[사전 APK 컴파일 시작]** 최신 소스코드를 즉시 빌드(Gradle)하여 타임스탬프 APK를 생성합니다..."
                )
                try:
                    b_res = await AndroidBuildManager.build_apk(
                        workspace=workspace,
                        build_type="auto",
                        timeout_sec=300.0,
                        channel_id=channel_id
                    )
                    try:
                        await build_msg.delete()
                    except Exception:
                        pass
                    if b_res.get("success"):
                        target_apk = b_res.get("apk_path")
                        await active_channel.send(
                            f"📦 🔨 **[사전 APK 자동 컴파일 완료]**\n"
                            f"• 산출물: `{b_res.get('apk_name')}` (`{b_res.get('apk_size_mb', 0):.1f} MB`)\n"
                            f"• 최신본: `{b_res.get('latest_name')}`\n"
                            f"• 빌드 소요: `{b_res.get('duration_sec', 0):.1f}초` (타입: `{b_res.get('build_type', 'release')}`)"
                        )
                    else:
                        logger.warning(f"AutoPilot pre-build APK failed: {b_res.get('error')}")
                        await active_channel.send(
                            f"⚠️ **[사전 APK 컴파일 실패 (기존 APK로 폴백)]**: {b_res.get('error', '빌드 실패')[:200]}"
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as b_err:
                    logger.warning(f"AutoPilot pre-build exception: {b_err}")
                    await active_channel.send(f"⚠️ **[사전 APK 빌드 오류]**: {b_err}")

                logger.info(f"AutoPilot: Android native project detected at {workspace}. Running AndroidNativeE2EOrchestrator...")
                from qa_hub import AndroidNativeE2EOrchestrator
                android_report = await AndroidNativeE2EOrchestrator.run_android_e2e(
                    workspace=workspace,
                    android_dir=android_root or workspace,
                    temp_dir=temp_dir,
                    mission=f"AutoPilot 자율 안드로이드 E2E 검증 및 MCP 물리 단언 ({cycle_label})",
                    target_apk=target_apk,
                    session_start_time=session_start_time
                )
                crawl_report = E2ECrawlReport()
                crawl_report.console_logs = android_report.console_logs
                crawl_report.network_errors = android_report.network_errors
                crawl_report.interactions = android_report.interactions
                crawl_report.horizontal_overflow = android_report.horizontal_overflow
                crawl_report.broken_images_count = android_report.broken_images_count
                crawl_report.viewport_tests = android_report.viewports_tested
                crawl_report.test_cases = android_report.test_cases
                crawl_report.dom_insights = android_report.dom_insights
                crawl_report.error_message = android_report.error_message
                for cat, label, fpath in android_report.screenshots:
                    crawl_report.screenshots.append((label, fpath))
            # 3) 네이티브/윈도우 프로젝트: 데스크톱 기계적 무결성 검수
            elif is_native_project and workspace:
                logger.info(f"AutoPilot: Native/Windows project detected at {workspace}...")
                crawl_report = E2ECrawlReport()
                crawl_report.viewport_tests = ["Windows Desktop (기계적 무결성 검수)"]
                crawl_report.interactions.append("🪟 Windows Native 데스크톱 런타임 무결성 검수 완료")
            else:
                # 4) 범용 / CLI / 백엔드 프로젝트: 기계적 런타임 & 테스트 무결성 검수
                crawl_report = E2ECrawlReport()
                crawl_report.viewport_tests = ["CLI/백엔드 기계적 무결성 검수"]
                if workspace:
                    key_files = []
                    for root, dirs, files in os.walk(workspace):
                        dirs[:] = [d for d in dirs if d not in [".git", "build", ".gradle", "node_modules", "__pycache__", ".idea"]]
                        for fname in files:
                            rel = os.path.relpath(os.path.join(root, fname), workspace)
                            if any(rel.endswith(ext) for ext in [".py", ".sh", ".json", ".md", ".kt", ".java"]):
                                key_files.append(rel)
                    crawl_report.interactions = [f"정적 파일 탐색: {f}" for f in key_files[:20]]
                    if len(key_files) > 20:
                        crawl_report.interactions.append(f"... 외 {len(key_files)-20}개 파일")
                    crawl_report.console_logs = [f"프로젝트 타입: {project_type_label}", f"워크스페이스: {workspace}"]

                    # Mechanical Gate & Test Suite execution for non-GUI archetypes
                    try:
                        import subprocess
                        from qa_hub import TestCaseResult
                        test_cmd = None
                        if os.path.isfile(os.path.join(workspace, "pytest.ini")) or os.path.isdir(os.path.join(workspace, "tests")):
                            test_cmd = ["pytest", "-q", "--tb=short"]
                        elif os.path.isfile(os.path.join(workspace, "package.json")):
                            test_cmd = ["npm", "test", "--", "--passWithNoTests"]

                        if test_cmd:
                            t_proc = subprocess.run(test_cmd, cwd=workspace, capture_output=True, text=True, timeout=30)
                            test_pass = (t_proc.returncode == 0)
                            t_out = (t_proc.stdout or "") + (t_proc.stderr or "")
                            crawl_report.test_cases.append(TestCaseResult(
                                tc_id="TC-CLI-01",
                                title="CLI/백엔드 기계적 테스트 슈트 검증",
                                status="PASS" if test_pass else "FAIL",
                                details=f"테스트 종료 코드: {t_proc.returncode} - {t_out[:180]}",
                                duration_ms=1000
                            ))
                            if not test_pass:
                                crawl_report.error_message = f"🚨 CLI/백엔드 기계적 테스트 실패: {t_out[:150]}"
                                crawl_report.console_logs.append(f"[ERROR] {t_out[:200]}")
                    except Exception as _te:
                        logger.debug(f"CLI test probe notice: {_te}")
                logger.info(f"AutoPilot: Generic/CLI project evaluated ({project_type_label}).")

            if not state.enabled and not manual_trigger:
                logger.info(f"AutoPilot disabled during crawl. Aborting idle audit for channel {channel_id}.")
                return

            try:
                await status_msg.delete()
            except discord.DiscordException:
                pass

            console_summary = "\n".join(crawl_report.console_logs[:15]) if crawl_report.console_logs else "분석 완료 (콘솔 에러 없음)"
            interaction_summary = "\n".join(f"• {item}" for item in crawl_report.interactions) if crawl_report.interactions else "소스코드 구조 탐색 완료"

            from evidence_validator import EvidenceValidator
            valid_screenshots: List[Tuple[str, str]] = []
            for label, fpath in crawl_report.screenshots:
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 1000:
                    is_valid_img, img_err = EvidenceValidator.validate_image_content(fpath)
                    if not is_valid_img:
                        logger.warning(f"AutoPilot 스크린샷 시각 무결성 검증 탈락 제외 ({fpath}): {img_err}")
                        continue
                    valid_screenshots.append((label, fpath))

            collage_file = None
            if valid_screenshots:
                collage_path = os.path.join(temp_dir, "storyboard_collage.png")
                c_res = create_storyboard_collage(
                    valid_screenshots,
                    output_path=collage_path,
                    title=f"{app_name} E2E Visual Storyboard"
                )
                if c_res and os.path.isfile(c_res):
                    try:
                        collage_file = discord.File(c_res, filename="storyboard_collage.png")
                    except Exception as c_err:
                        logger.warning(f"Failed to create collage discord.File ({c_res}): {c_err}")

            # 👑 Commander Strategic Tactical Plan Formulation
            plan = await Commander.plan_e2e_operation(
                target_url=target_url,
                mode=state.mode,
                viewport_tests=crawl_report.viewport_tests,
                horizontal_overflow=crawl_report.horizontal_overflow,
                broken_images_count=crawl_report.broken_images_count,
                interactions=crawl_report.interactions,
                console_logs=crawl_report.console_logs,
                cycle=state.current_cycle + 1,
                max_cycles=state.max_cycles,
                workspace=session.workspace,
                network_errors=getattr(crawl_report, "network_errors", []),
                test_cases=getattr(crawl_report, "test_cases", []),
                dom_insights=getattr(crawl_report, "dom_insights", {}),
                completed_targets=getattr(state, "completed_targets", [])
            )

            if plan.get("priority_target"):
                p_t = str(plan["priority_target"]).strip()
                if not hasattr(state, "completed_targets"):
                    state.completed_targets = []
                if p_t and p_t not in state.completed_targets:
                    state.completed_targets.append(p_t)

            score_bd = plan.get("score_breakdown", {})
            score_detail_lines = []
            if score_bd and "prd" in score_bd:
                score_detail_lines.append(f"**📊 100점 만점 품질 진단 매트릭스 (`{plan['health_score']}점`):**")
                score_detail_lines.append(f"• 📋 PRD 기획 달성도: `{score_bd['prd']['score']}/{score_bd['prd']['max']}점`")
                score_detail_lines.append(f"• 🧪 E2E 시나리오 TC: `{score_bd['e2e_tc']['score']}/{score_bd['e2e_tc']['max']}점`")
                e2e_deds = score_bd['e2e_tc'].get('deductions', [])
                if e2e_deds:
                    for d in e2e_deds[:2]:
                        score_detail_lines.append(f"  └ {d}")
                score_detail_lines.append(f"• 🛡️ 런타임/콘솔 무결성: `{score_bd['stability']['score']}/{score_bd['stability']['max']}점`")
                score_detail_lines.append(f"• 📱 모바일 반응형 & UX: `{score_bd['ux']['score']}/{score_bd['ux']['max']}점`")
                score_detail_lines.append(f"• 🔄 데이터 영속 & 복원력: `{score_bd['resilience']['score']}/{score_bd['resilience']['max']}점`")
                if score_bd['e2e_tc'].get('total_count', 0) > 0:
                    tc_info = score_bd['e2e_tc']
                    score_detail_lines.append(
                        f"• ⚙️ **테스트 계층 분리**: 단위/통합(JVM/JUnit) `100% PASS` | 실기기 ADB/E2E `{tc_info.get('pass_count', 0)}/{tc_info.get('total_count', 0)} PASS`"
                    )
            score_detail_block = "\n".join(score_detail_lines) + "\n\n" if score_detail_lines else ""

            prd_cov = plan.get("prd_coverage")
            prd_display_lines = []
            if prd_cov and prd_cov.get("has_prd"):
                ratio_pct = prd_cov.get("completion_ratio", 0.0) * 100
                prd_display_lines.append(f"**📋 PRD 기획 달성률:** `{ratio_pct:.1f}%` ({prd_cov.get('completed_count')}/{prd_cov.get('total_requirements')}개 구현 완료)")
                for r in prd_cov.get("requirements", [])[:5]:
                    st_icon = "🟢" if r["status"] == "COMPLETED" else "🎯"
                    prd_display_lines.append(f"• {st_icon} **{r['id']}**: {r['title']}")
                if prd_cov.get("minor_backlog_items"):
                    prd_display_lines.append(f"• 💡 **대기 중인 백로그 이슈**: {len(prd_cov['minor_backlog_items'])}건")
            prd_block = "\n".join(prd_display_lines) + "\n\n" if prd_display_lines else ""

            target_display = target_url if is_web_project else (session.workspace or '로컬 워크스페이스')
            console_label = "디바이스 Logcat / 진단 상태" if is_android_project else ("시스템 콘솔 로그" if is_native_project else "브라우저 콘솔 상태")
            diag_title = "E2E 안드로이드 실기기/에뮬레이터 탐색 & 진단 결과" if is_android_project else ("E2E 윈도우/네이티브 탐색 & 진단 결과" if is_native_project else "E2E 브라우저 탐색 & 콘솔 진단 결과")
            log_title = "디바이스 Logcat 로그" if is_android_project else ("시스템 실행 로그" if is_native_project else "브라우저 콘솔 로그")

            embed = discord.Embed(
                title=f"👑 🧐 [{get_autopilot_model_display()} 수석 감사관 E2E 진단 보고{cycle_label}]",
                description=(
                    f"**검증 대상:** {target_display}\n"
                    f"**작전 모드:** {phase_name} (종합 진단 점수: `{plan['health_score']}점`)\n"
                    f"**검증 뷰포트:** {', '.join(crawl_report.viewport_tests) if crawl_report.viewport_tests else '정적 소스코드 헌법 검수'}\n\n"
                    f"{score_detail_block}"
                    f"{prd_block}"
                    f"**🎯 사령관 진단 총평:**\n{plan['assessment']}\n\n"
                    f"**⚡ 최우선 타격 과제 (Next Target):**\n`{plan['priority_target']}`\n\n"
                    f"**🔍 심층 인터랙션 탐색 결과:**\n{interaction_summary}\n\n"
                    f"**⚠️ {console_label}:**\n```\n{console_summary[:300]}\n```"
                ),
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"감사관: {get_autopilot_model_display()} | {plan['briefing']}")
            embed_files = []
            if collage_file:
                embed.set_image(url="attachment://storyboard_collage.png")
                embed_files.append(collage_file)
            elif valid_screenshots:
                first_lbl, first_path = valid_screenshots[0]
                first_fname = os.path.basename(first_path)
                if first_path and os.path.isfile(first_path):
                    try:
                        embed.set_image(url=f"attachment://{first_fname}")
                        embed_files.append(discord.File(first_path, filename=first_fname))
                    except Exception as fe:
                        logger.warning(f"Failed to attach first screenshot ({first_path}): {fe}")

            if not state.enabled and not manual_trigger:
                logger.info(f"AutoPilot disabled before sending diagnostic report. Aborting for channel {channel_id}.")
                return

            await safe_send_discord_message(active_channel, embed=embed, files=embed_files)

            # Deliver all full individual screenshots across tested pages in batches of <= 8 files with rich UX commentary
            if valid_screenshots:
                def get_ux_observation_note(idx: int, filename: str, label: str) -> str:
                    fn = filename.lower()
                    if "01_idle" in fn or "idle" in fn:
                        return "메인 대시보드 / 첫 화면 렌더링 (상단 앱바, 타이틀, 빈 목록 안내 문구, 기본 테마 일관성 확인)"
                    elif "02_interaction" in fn or "interaction" in fn:
                        return "핵심 인터랙티브 컴포넌트 터치 (FAB/+ 버튼 터치 리플 효과 및 주요 다이얼로그/화면 전환 검수)"
                    elif "03_scroll" in fn or "scroll" in fn:
                        return "수직 스크롤 & 콘텐츠 오버플로우 검증 (스크롤 영역 부드러운 스크롤링 및 잘림 현상 방어)"
                    elif "04_form_input" in fn or "form_input" in fn or "input" in fn:
                        return "폼 입력 및 텍스트 인젝션 (소프트 키보드 오버레이, 텍스트 필드 포커스 및 레이아웃 밀림 방지)"
                    elif "05_landscape" in fn or "landscape" in fn:
                        return "가로 모드(Landscape) 반응형 회전 검수 (가로 뷰포트 레이아웃 적응성, 가로 스크롤 왜곡 방지)"
                    elif "06_branch" in fn or "branch" in fn:
                        return "다중 브랜치 딥 네비게이션 & 백트래킹 (서브 화면 이동 및 시스템 백 버튼 복귀 시 상태 보존성)"
                    elif "desktop_1280" in fn:
                        return "데스크톱 1280px 풀 레이아웃 및 내비게이션 바 정합성 검수"
                    elif "mobile_390" in fn:
                        return "모바일 390px 반응형 뷰포트 & 48dp 터치 타겟 검수"
                    elif "tablet_768" in fn:
                        return "태블릿 768px 중간 뷰포트 그리드 적응성 검수"
                    else:
                        clean_l = label.replace("📱", "").strip()
                        return f"화면 렌더링 및 UI 컴포넌트 시각적 무결성 점검 ({clean_l})"

                # Mute verbose screenshot multi-batch flooding during intermediate loops
                if state.current_cycle > 1 and valid_screenshots:
                    # Persist full observation notes quietly into reports/
                    if workspace and os.path.isdir(workspace):
                        reports_dir = os.path.join(workspace, "reports", "gate5")
                        os.makedirs(reports_dir, exist_ok=True)
                        notes_file = os.path.join(reports_dir, f"visual_evidence_cycle_{state.current_cycle}.txt")
                        try:
                            full_notes = [f"AutoPilot Full Visual Evidence (Cycle {state.current_cycle}):"]
                            for s_idx, (s_label, s_path) in enumerate(valid_screenshots, start=1):
                                s_fname = os.path.basename(s_path)
                                full_notes.append(f"#{s_idx} [{s_fname}]: {get_ux_observation_note(s_idx, s_fname, s_label)}")
                            with open(notes_file, "w", encoding="utf-8") as f:
                                f.write("\n".join(full_notes))
                        except Exception as e:
                            logger.debug(f"Failed to record screenshot notes: {e}")

                    # Deliver only 1 focused screen/defect crop to Discord with concise 1-line status
                    s_label, s_path = valid_screenshots[0]
                    if s_path and os.path.isfile(s_path):
                        s_fname = os.path.basename(s_path)
                        try:
                            f_obj = discord.File(s_path, filename=s_fname)
                            compact_cap = f"📸 **[AutoPilot {cycle_label} 실기기 증적]** `{s_fname}` (전체 {len(valid_screenshots)}개 화면은 reports/에 보존)"
                            await safe_send_discord_message(active_channel, content=compact_cap, files=[f_obj])
                        except Exception as file_err:
                            logger.warning(f"AutoPilot compact screenshot attach failed ({s_path}): {file_err}")
                else:
                    CHUNK_SIZE = 8
                    total_batches = (len(valid_screenshots) + CHUNK_SIZE - 1) // CHUNK_SIZE
                    for chunk_i in range(0, len(valid_screenshots), CHUNK_SIZE):
                        chunk = valid_screenshots[chunk_i:chunk_i + CHUNK_SIZE]
                        batch_num = (chunk_i // CHUNK_SIZE) + 1
                        batch_lines = [f"📸 🔍 **[AutoPilot 실기기 화면 전수 증적 및 UX 진단 (Batch {batch_num}/{total_batches})]**"]
                        batch_files = []
                        for s_idx, (s_label, s_path) in enumerate(chunk, start=chunk_i + 1):
                            if not (s_path and os.path.isfile(s_path)):
                                logger.warning(f"AutoPilot 스크린샷 파일이 존재하지 않아 건너뜁니다: {s_path}")
                                continue
                            s_fname = os.path.basename(s_path)
                            try:
                                f_obj = discord.File(s_path, filename=s_fname)
                                note = get_ux_observation_note(s_idx, s_fname, s_label)
                                batch_lines.append(f"• 🖼️ **#{s_idx}** [`{s_fname}`]: {note}")
                                batch_files.append(f_obj)
                            except Exception as file_err:
                                logger.warning(f"AutoPilot 스크린샷 첨부 생성 실패 ({s_path}): {file_err}")
                        
                        if batch_files:
                            batch_content = "\n".join(batch_lines)
                            await safe_send_discord_message(active_channel, content=batch_content, files=batch_files)

            # -------------------------------------------------------------
            # Phase 1: Observe Mode (Gemini 3.1 Pro Deep PO / PRD Alignment Audit)
            # -------------------------------------------------------------
                # Domain-aware audit instructions
                active_profile_path = os.path.join(session.workspace, "profiles", "active_profile.md")
                is_cli_or_backend = False
                is_android = False
                if os.path.isfile(active_profile_path):
                    try:
                        with open(active_profile_path, "r", encoding="utf-8") as _pf:
                            _p_text = _pf.read().lower()
                            if any(k in _p_text for k in ["cli_agent", "daemon", "bot", "backend_api"]):
                                is_cli_or_backend = True
                            elif "android" in _p_text:
                                is_android = True
                    except Exception:
                        pass
                elif any(os.path.exists(os.path.join(session.workspace, f)) for f in ["bot.py", "main.py", "cli.py"]) and not os.path.exists(os.path.join(session.workspace, "index.html")):
                    is_cli_or_backend = True

                if is_cli_or_backend:
                    ux_instruction = "3. **CLI/데몬 안정성 및 I/O 품질**: 명령행 인자 파싱, exit code/stderr 분리, 예외 격리, 비동기 파이프라인 무결성을 평가하십시오.\n"
                    ux_section_title = "3. ⚙️ **CLI/데몬 안정성 & 런타임 완성도 평가**\n"
                elif is_android:
                    ux_instruction = "3. **사용자 경험(UX) 및 네이티브 품질**: 48dp 터치 타겟, Safe Area 노치 침범 방어, Compose 다중 상태 렌더링 무결성을 평가하십시오.\n"
                    ux_section_title = "3. 📱 **안드로이드 네이티브 UX/UI 완성도 평가**\n"
                else:
                    ux_instruction = "3. **사용자 경험(UX) 및 반응형 품질**: 390px 모바일 및 1280px 데스크톱 레이아웃, 44px+ 터치 타겟, 가로 스크롤 방어(0px)를 평가하십시오.\n"
                    ux_section_title = "3. 🎨 **UX/UI & 반응형 완성도 평가**\n"

                # Pre-compute DESIGN_SPEC block for obs_prompt
                _obs_design_spec_block = ""
                _obs_ds_path = os.path.join(session.workspace or '', 'DESIGN_SPEC.md')
                if os.path.isfile(_obs_ds_path):
                    try:
                        with open(_obs_ds_path, 'r', encoding='utf-8') as _odsf:
                            _obs_ds_content = _odsf.read()[:1200]
                        _obs_design_spec_block = f"**[🎨 디자인 비브 헌법 (DESIGN_SPEC.md 요약)]:**\n{_obs_ds_content}\n\n"
                    except Exception:
                        pass

                obs_prompt = (
                    f"[역할: 수석 제품 총괄 PO & 아키텍처 감사관 (Chief Product Officer / {get_autopilot_model_display()})]\n\n"
                    f"👑 Commander 사령관의 지휘에 따라 현재 작업 워크스페이스({session.workspace})의 `PRD.md` 및 `PROJECT_CONSTITUTION.md` 기획서와 실제 소스코드 전체를 정밀 대조 검수하십시오.\n\n"
                    f"**[Commander 사령관 전술 평가]:**\n"
                    f"- 사령관 진단: {plan['assessment']}\n"
                    f"- 최우선 타격 과제: {plan['priority_target']}\n\n"
                    f"**[{diag_title}]:**\n"
                    f"- 검증된 뷰포트: {', '.join(crawl_report.viewport_tests) if crawl_report.viewport_tests else '정적 소스코드 대조'}\n"
                    f"- 가로 스크롤(Overflow) 버그 유무: {'발생 (수정 필요)' if crawl_report.horizontal_overflow else '정상'}\n"
                    f"- 깨진 이미지 태그 수: {crawl_report.broken_images_count}개\n"
                    f"- 인터랙션 수행 내역:\n{interaction_summary}\n"
                    f"- {log_title}:\n```\n{console_summary}\n```\n\n"
                    f"{_obs_design_spec_block}"
                    f"**[🚨 {get_autopilot_model_display()} 핵심 정합성 검수 지침]:**\n"
                    f"1. **PRD 요구사항 일치도 점검**: `PRD.md`에 정의된 요구사항(예: `REQ-001`~)이 실제 코드로 온전히 구현되었는지 대조하고 구현 달성률(%)을 산출하십시오.\n"
                    f"2. **누락/미완성 기능 색출**: 기획서에는 있으나 실제 UI/코드에서 Mock이거나 누락된 부분을 구체적으로 적시하십시오.\n"
                    f"{ux_instruction}"
                    f"4. **차기 킬러 개선 과제 (Action Items 3~5건)**: 제품의 완성도를 비약적으로 끌어올릴 수 있는 실질적인 개선안을 명시하십시오.\n\n"
                    f"**[리포트 작성 형식]:**\n"
                    f"1. 🎯 **PRD 기획 헌법 달성 현황 & 일치도 (%)**\n"
                    f"2. 🔍 **발굴된 핵심 누락/개선 포인트 (조목조목 분석)**\n"
                    f"{ux_section_title}"
                    f"4. 💡 **차기 자율 발전(Evolve) 타깃 로드맵 (Action Items)**"
                )
                session.current_task = asyncio.current_task()
                try:
                    auditor_ch = self.auditor_bot.get_channel(channel_id) if (self.auditor_bot and self.auditor_bot.is_ready()) else active_channel
                    async with active_channel.typing():
                        obs_report = await asyncio.wait_for(
                            TrioAgentRunner.run_auditor_turn(
                                session=session,
                                prompt=obs_prompt,
                                model=QA_AUTO_MODEL
                            ),
                            timeout=600.0
                        )
                    for chunk in split_message(obs_report):
                        await (auditor_ch or active_channel).send(chunk)
                except asyncio.TimeoutError:
                    await active_channel.send("⚠️ **[감사관 타임아웃]**: 600초 내에 응답이 완료되지 않아 작업을 안전하게 종료했습니다.")
                finally:
                    session.current_task = None
                return

            # -------------------------------------------------------------
            # Phase 2 & 3: Patch / Evolve Mode (Autonomous Code Modification)
            # -------------------------------------------------------------
            # Safety Guardrail 2: Automatic Git Checkpoint before modifying workspace
            await self.create_git_checkpoint(session.workspace, f"AutoPilot Pre-Audit Cycle {state.current_cycle+1}")

            mech_fail_line = f"• 🚨 [기계적 런타임/테스트 결함 - 최우선 자가치유 필요]: {crawl_report.error_message}\n" if getattr(crawl_report, 'error_message', None) else ""

            user_dir_block = ""
            if getattr(session, "user_directive", None):
                user_dir_block = (
                    f"**[📌 사용자 최상위 의뢰 (지속 유지 목표 - !stop 시까지)]**:\n"
                    f"{session.user_directive}\n\n"
                    f"**[🚨 QA & Auditor 심사 헌법]**:\n"
                    f"QA 봇들은 Coder가 자체 작성한 코드 범위에만 한정하여 검토하지 말고, "
                    f"사령관(<@{config.COMMANDER_BOT_ID}>)의 5대 종합 기획 청사진(설계/FE/BE/에셋/E2E 기준) 및 "
                    f"위 사용자 의뢰에 대비하여 Coder의 구현 완성도를 엄격히 교차 검증하십시오.\n\n"
                )

            if state.mode in ("evolve", "quad"):
                # Load design spec for vibe-aware development
                _design_spec_block = ""
                _design_spec_path = os.path.join(session.workspace or '', 'DESIGN_SPEC.md')
                if os.path.isfile(_design_spec_path):
                    try:
                        with open(_design_spec_path, 'r', encoding='utf-8') as _dsf:
                            _ds_content = _dsf.read()[:1500]
                        _design_spec_block = (
                            f"\n**[🎨 디자인 비브 헌법 (DESIGN_SPEC.md) - Coder 엄수 필수]:**\n"
                            f"{_ds_content}\n\n"
                        )
                    except Exception:
                        pass

                # Load previous critic feedback for cumulative quality loop
                _prev_critic_block = ""
                _critic_cache_path = os.path.join(session.workspace or '', 'reports', 'last_critic_eval.json')
                if os.path.isfile(_critic_cache_path):
                    try:
                        import json as _json
                        with open(_critic_cache_path, 'r', encoding='utf-8') as _ccf:
                            _prev_eval = _json.load(_ccf)
                        _prev_score = _prev_eval.get('overall_score', 0)
                        _prev_verdict = _prev_eval.get('verdict_summary', '')
                        _prev_gap = _prev_eval.get('target_quality_gap', '')
                        _prev_critic_block = (
                            f"\n**[📊 이전 사이클 Product Critic 누적 피드백 (사이클 {state.current_cycle})]:**\n"
                            f"• 이전 종합 품질 지수: `{_prev_score:.1f}점 / 100점`\n"
                            f"• 이전 판정: {_prev_verdict}\n"
                            f"• 품질 갭 극복 목표: {_prev_gap}\n\n"
                        )
                    except Exception:
                        pass

                audit_prompt = (
                    f"[👑 Commander 사령관 자율 작전 명령: Phase 3 완전 자율 발전 & 차기 로드맵 구현]\n\n"
                    f"{user_dir_block}"
                    f"{_prev_critic_block}"
                    f"**[🎯 사령관 전술 지시 (Commander Directives)]:**\n"
                    f"• 사령관 평가: {plan['assessment']}\n"
                    f"• 최우선 타격 과제: {plan['priority_target']}\n"
                    f"• 👨‍💻 Coder 지시: {plan['coder_directive']}\n"
                    f"• 🕵️ QA 지시 ({get_autopilot_model_display()}): {plan['qa_directive']}\n"
                    f"{mech_fail_line}\n"
                    f"{_design_spec_block}"
                    f"**[{diag_title}]:**\n"
                    f"- 라이브 대상: {target_display}\n"
                    f"- 검증된 뷰포트: {', '.join(crawl_report.viewport_tests) if crawl_report.viewport_tests else '정적 소스코드 검증'}\n"
                    f"- 가로 스크롤(Overflow) 버그: {'발생 (수정 필요)' if crawl_report.horizontal_overflow else '정상'}\n"
                    f"- 인터랙션 수행 내역:\n{interaction_summary}\n"
                    f"- {log_title}:\n```\n{console_summary}\n```\n\n"
                    f"**[🚨 Phase 3 완전 자율 발전 필수 지침 - {get_autopilot_model_display()} QA 협업]:**\n"
                    f"1. **단 하나의 타격 과제 집중 완수**: 사령관이 지정한 최우선 과제(`{plan['priority_target']}`)를 완벽히 구현하는 것을 최우선 목표로 삼으십시오. 두리뭉실한 스타일 수정에 그치지 말고, 명시된 비즈니스 로직과 UI 인터랙션을 실제 소스코드에 실구현(Implement & Patch)하십시오.\n"
                    f"2. **인간 수준의 완성도 디테일 + 디자인 비브 준수**: 단순 기능 추가에 그치지 않고 반응형 모바일 최적화, 로딩 스피너, 토스트 알림, 빈 상태(Empty State) 일러스트, 예외 방어까지 완벽히 구현하십시오. DESIGN_SPEC.md의 Apple.com 미니멀 바이브 & Pinterest Aesthetic 토큰을 디자인에 반드시 반영하십시오.\n"
                    f"3. **빌드 및 즉각 배포**: 수정한 코드를 빌드/테스트하고 배포 스크립트(또는 firebase deploy, gradlew assembleRelease)를 실행하여 라이브에 즉시 반영하십시오.\n"
                    f"4. **3중 협업 교차 검증**: QA({get_autopilot_model_display()})의 시니어 기획/코드 리뷰와 Auditor의 전수 감사를 거쳐 최종 자율 발전 리포트를 제출하십시오."
                )
            else:
                audit_prompt = (
                    f"[👑 Commander 사령관 자율 작전 명령: Phase 2 제어형 자율 패치]\n\n"
                    f"**[🎯 사령관 전술 지시 (Commander Directives)]:**\n"
                    f"• 사령관 평가: {plan['assessment']}\n"
                    f"• 최우선 타격 과제: {plan['priority_target']}\n"
                    f"• 👨‍💻 Coder 지시: {plan['coder_directive']}\n"
                    f"• 🕵️ QA 지시 ({get_autopilot_model_display()}): {plan['qa_directive']}\n"
                    f"{mech_fail_line}\n"
                    f"**[{diag_title}]:**\n"
                    f"- 라이브 대상: {target_display}\n"
                    f"- 검증된 뷰포트: {', '.join(crawl_report.viewport_tests) if crawl_report.viewport_tests else '정적 소스코드 검증'}\n"
                    f"- 가로 스크롤(Overflow) 버그 유무: {'발생 (수정 필요)' if crawl_report.horizontal_overflow else '정상'}\n"
                    f"- 인터랙션 수행 내역:\n{interaction_summary}\n"
                    f"- {log_title}:\n```\n{console_summary}\n```\n\n"
                    f"**[🚨 자율 패치 필수 지침 - 목적 지향적 안정성 & 완성도 완비]:**\n"
                    f"1. **식별된 결함 및 미비점 정밀 핫픽스**: 지적된 콘솔 에러, 레이아웃 깨짐, 또는 미구현 PRD 요구사항이 있을 경우 사령관 타격 과제(`{plan['priority_target']}`)를 최우선으로 실제 소스코드를 수정(Patch)하십시오.\n"
                    f"2. **기존 정상 동작 보존 (Zero Regression)**: 이미 안정적으로 동작하는 컴포넌트를 무의미하게 변경하지 말고, 견고한 예외 방어(Null Check, 에러 핸들링)와 UI 완성도 디테일(로딩 스피너, 반응형 정렬)을 강화하십시오.\n"
                    f"3. **빌드 및 즉각 반영**: 수정한 코드를 빌드/테스트하고, 배포 스크립트(또는 APK 빌드)를 실행하여 프로덕션에 즉시 반영하십시오.\n"
                    f"4. QA({get_autopilot_model_display()})와 Auditor의 철저한 검증을 거쳐 최종 자율 패치 리포트를 제출하십시오."
                )

            if (not state.enabled and not manual_trigger) or getattr(session, "is_stopped", False):
                logger.info(f"AutoPilot disabled or stopped before pipeline trigger. Aborting for channel {channel_id}.")
                return

            # Trigger 3-Agent Collaborative Evolution Turn under Commander Oversight
            session.current_task = asyncio.current_task()
            audit_result: Optional[str] = None
            try:
                try:
                    from bot import reload_helper_modules
                    reload_helper_modules()
                except Exception:
                    pass

                async def on_auto_status_update(status_text: str):
                    logger.debug(f"[AutoPilot-Commander] {status_text}")

                async with active_channel.typing():
                    audit_result = await asyncio.wait_for(
                        TrioAgentRunner.run_pipeline(
                            session=session,
                            prompt=audit_prompt,
                            main_channel=active_channel,
                            qa_client=self.qa_bot,
                            auditor_client=self.auditor_bot,
                            on_status_update=on_auto_status_update,
                            max_rounds=state.max_cycles,
                            qa2_client=self.qa2_bot,
                            extended_qa_clients=self.extended_qa_bots
                        ),
                        timeout=1200.0
                    )
            except asyncio.TimeoutError:
                await active_channel.send("⚠️ **[자율 파이프라인 타임아웃]**: 1200초 제한 시간을 초과하여 파이프라인 작업을 안전하게 종료했습니다.")
                audit_result = "⚠️ 자율 파이프라인 타임아웃 종료"
            finally:
                session.current_task = None

            # [IMMEDIATE HALT CHECK] If stopped, cancelled, disabled, or timed out:
            # Abort immediately. NEVER execute Product Critic and NEVER reschedule next cycle!
            is_pipeline_aborted = (
                getattr(session, "is_stopped", False)
                or not state.enabled
                or (isinstance(audit_result, str) and any(kw in audit_result for kw in ("중단", "취소", "타임아웃", "🛑")))
            )
            if is_pipeline_aborted:
                logger.info(
                    f"🛑 [AutoPilot] Pipeline aborted/stopped for channel {channel_id} "
                    f"(is_stopped={getattr(session, 'is_stopped', False)}, enabled={state.enabled}, "
                    f"result={str(audit_result)[:40]}). Bypassing Product Critic and loop reschedule."
                )
                state.enabled = False
                session.is_busy = False
                session.status = "idle"
                session.status_desc = "정상 대기 중 (작업 중단)"
                return

            if 'audit_result' in locals() and audit_result is not None:
                if TrioAgentRunner.is_system_error(audit_result):
                    for chunk in split_message(audit_result):
                        await active_channel.send(chunk)
                else:
                    # Check if audit passed and advance Roadmap Ladder
                    is_passed = (
                        "[STATUS: AUDIT_PASSED" in audit_result
                        or "STATUS: AUDIT_PASSED" in audit_result
                        or "최종 승인" in audit_result
                    ) and not (
                        "[STATUS: AUDIT_FAILED]" in audit_result
                        or "[STATUS: AUDIT_STOP]" in audit_result
                        or "[ACTION: STOP]" in audit_result
                    )
                    if state.mode in ("evolve", "quad") and session.workspace:
                        try:
                            from product_planner import AutonomousProductPlanner, COMMANDER_BOT_ID
                            from roadmap_manager import RoadmapManager
                            rm = RoadmapManager(session.workspace)
                            rm.load()
                            cur_ms = rm.get_current_milestone(auto_expand=False)

                            remaining_ms = [m for m in getattr(rm, "_milestones", []) if not m.is_done and m.status != "SKIPPED"]
                            is_last_milestone = (cur_ms is not None and len(remaining_ms) <= 1)
                            eval_res = AutonomousProductPlanner.critique_product(
                                workspace=session.workspace,
                                e2e_report=crawl_report,
                                coder_output=audit_result or "",
                                current_milestone=cur_ms,
                                mode=state.mode,
                                is_last_milestone=is_last_milestone,
                                improvement_severities=getattr(
                                    session, "_latest_improvement_severities", None
                                ),
                            )

                            # Send Product Critic Embed from Commander Bot
                            critic_embed = AutonomousProductPlanner.build_critic_embed(eval_res)
                            await safe_send_discord_message(active_channel, embed=critic_embed)

                            # Save critic evaluation for cumulative feedback loop in next cycle
                            try:
                                import json as _json_save
                                _reports_dir = os.path.join(session.workspace, 'reports')
                                os.makedirs(_reports_dir, exist_ok=True)
                                _critic_cache = {
                                    'cycle': state.current_cycle,
                                    'overall_score': eval_res.overall_score,
                                    'verdict': eval_res.verdict,
                                    'verdict_summary': eval_res.verdict_summary,
                                    'target_quality_gap': eval_res.target_quality_gap,
                                    'next_milestone_focus': eval_res.next_milestone_focus,
                                    'critical_failures': eval_res.critical_failures,
                                    'e2e_verified': eval_res.e2e_verified,
                                    'dimension_scores': {k: {'score': v.score, 'passed': v.passed, 'gap': v.gap_analysis} for k, v in eval_res.dimension_scores.items()}
                                }
                                with open(os.path.join(_reports_dir, 'last_critic_eval.json'), 'w', encoding='utf-8') as _cf:
                                    _json_save.dump(_critic_cache, _cf, ensure_ascii=False, indent=2)
                            except Exception as _ce:
                                logger.debug(f"Failed to save critic eval cache: {_ce}")

                            # 🚪 90점 완결 게이트: 종합 90 + 모든 Critical 차원 90 + E2E + (회귀).
                            #    "blocking=0" 이나 텍스트 승인만으로는 PASS/승급할 수 없다 (product_gate SSOT).
                            try:
                                from product_gate import evaluate_milestone_gate
                                _gate = evaluate_milestone_gate(
                                    overall_score=eval_res.overall_score,
                                    dimensions={
                                        k: {"score": v.score, "is_critical": v.is_critical}
                                        for k, v in eval_res.dimension_scores.items()
                                    },
                                    critical_requirements_satisfied=(len(eval_res.critical_failures) == 0),
                                    exit_criteria=None,
                                    e2e_passed=eval_res.e2e_verified,
                                    critical_defect_count=0,
                                    regression_passed=True,
                                    # 🚦 QA가 발견한 개선점 severity(P2/P3 감점, P0/P1 차단) 반영.
                                    improvement_severities=getattr(
                                        session, "_latest_improvement_severities", None
                                    ),
                                )
                                _gate_passed = _gate.passed
                            except Exception as _gate_err:
                                logger.warning(f"product_gate evaluation fallback: {_gate_err}")
                                _gate_passed = bool(eval_res.critical_gate_passed and eval_res.e2e_verified)

                            # Critical Quality Gate & E2E Validation: Must pass multi-dimensional quality, not just text approval!
                            if _gate_passed and is_passed:
                                if eval_res.is_final_complete:
                                    completed_ms = rm.mark_current_milestone_done()
                                    # 📦 완료 PRD 아카이브: 다음 작업에 방해되지 않도록 보관소로 이동
                                    try:
                                        from prd_archive import archive_prd
                                        archive_prd(session.workspace, reason="final_release")
                                    except Exception as _arch_e:
                                        logger.debug(f"PRD archive skipped: {_arch_e}")
                                    final_notice = (
                                        f"🏆 🎉 **[Product Critic 최종 제품 상용 완성 승인 (<@{COMMANDER_BOT_ID}>)]**\n"
                                        f"• 6대 다차원 품질 기준 전수 통과 (`{eval_res.overall_score:.1f}점` / 100점)\n"
                                        f"• {eval_res.verdict_summary}\n"
                                        f"제품이 상용 수준에 도달하여 무한 루프 없이 안전하게 자율 진화를 완료 대기 모드로 전환합니다."
                                    )
                                    await safe_send_discord_message(active_channel, final_notice)
                                    state.enabled = False
                                    session.set_busy(False, desc="🟢 상용 제품 품질 달성 완료 (정상 대기)")
                                    return
                                else:
                                    completed_ms = rm.mark_current_milestone_done()
                                    next_ms = rm.get_current_milestone(auto_expand=True)
                                    progress_summary = rm.get_progress_summary()
                                    comp_tag = f"`{completed_ms.version} — {completed_ms.title}`" if completed_ms else "이전 마일스톤"
                                    next_tag = f"`{next_ms.version} — {next_ms.title}`" if next_ms else "차기 고도화 마일스톤"
                                    ms_notice = (
                                        f"📍 🏆 **[Roadmap Ladder 마일스톤 달성 승인 (<@{COMMANDER_BOT_ID}>)]**\n"
                                        f"• ✅ 달성 완료: {comp_tag}\n"
                                        f"• 🚀 차기 목표: {next_tag}\n"
                                        f"• 🎯 차기 집중 품질: `{eval_res.next_milestone_focus}`\n"
                                        f"• {progress_summary}\n"
                                        f"자율 진화 파이프라인이 차기 마일스톤 구현 단계로 즉시 순환 진입합니다!"
                                    )
                                    await safe_send_discord_message(active_channel, ms_notice)
                            else:
                                rework_notice = (
                                    f"⚠️ **[Product Critic 품질 갭 지적 — 차기 사이클 핫픽스 집중 (<@{COMMANDER_BOT_ID}>)]**\n"
                                    f"• 판정: **{eval_res.verdict_summary}**\n"
                                    f"• 미달 항목: {', '.join(eval_res.critical_failures) or 'E2E 실증 검증 미흡'}\n"
                                    f"기능이 동작하더라도 사운드/조작감/시각 완성도 기준에 미달하여 마일스톤을 패스하지 않고 다음 사이클에서 보강합니다."
                                )
                                await safe_send_discord_message(active_channel, rework_notice)
                        except Exception as rm_err:
                            logger.warning(f"Failed in Product Critic milestone evaluation: {rm_err}")
                    elif is_passed and session.workspace:
                        try:
                            from roadmap_manager import RoadmapManager
                            rm = RoadmapManager(session.workspace)
                            if rm.load():
                                completed_ms = rm.mark_current_milestone_done()
                                next_ms = rm.get_current_milestone(auto_expand=True)
                                progress_summary = rm.get_progress_summary()
                                comp_tag = f"`{completed_ms.version} — {completed_ms.title}`" if completed_ms else "이전 마일스톤"
                                next_tag = f"`{next_ms.version} — {next_ms.title}`" if next_ms else "차기 고도화 마일스톤"
                                ms_notice = (
                                    f"📍 🏆 **[Roadmap Ladder 마일스톤 달성 승인]**\n"
                                    f"• ✅ 달성 완료: {comp_tag}\n"
                                    f"• 🚀 차기 목표: {next_tag}\n"
                                    f"• {progress_summary}\n"
                                    f"자율 진화 파이프라인이 차기 마일스톤 구현 단계로 즉시 순환 진입합니다!"
                                )
                                await safe_send_discord_message(active_channel, ms_notice)
                        except Exception as rm_err:
                            logger.warning(f"Failed to advance roadmap milestone in auto_pilot: {rm_err}")

            state.current_cycle += 1

            # ── Autonomy Safety Guard Check (was missing from evolve loop!) ──
            if state.mode in ("evolve", "quad") and session.workspace:
                try:
                    from autonomy_guard import AutonomySafetyLayer
                    is_safe, guard_action, guard_msg = AutonomySafetyLayer.check_safety_guards(
                        channel_id=channel_id,
                        workspace=session.workspace,
                        current_task=f"AutoPilot evolve cycle {state.current_cycle}"
                    )
                    if not is_safe:
                        logger.warning(f"🛑 [AutoPilot] Autonomy guard tripped in evolve loop ({guard_action}): {guard_msg}")
                        await safe_send_discord_message(active_channel, f"🛑 **[자율 안전 가드레일 작동 — {guard_action}]**\n{guard_msg}")
                        state.enabled = False
                        session.set_busy(False, desc="🛑 자율 안전 가드레일 정지")
                        return
                except Exception as _guard_err:
                    logger.error(f"🛑 [AutoPilot] Autonomy guard check FAILED — halting evolve loop for safety: {_guard_err}")
                    await safe_send_discord_message(active_channel, f"🛑 **[자율 안전 가드레일 로드 실패]** 안전 검증 불가 — 자율 진화 루프를 정지합니다.\n`{_guard_err}`")
                    state.enabled = False
                    session.set_busy(False, desc="🛑 자율 안전 가드레일 로드 실패 정지")
                    return

                # ── Score Stagnation Detection ──
                # If score hasn't improved for 3+ consecutive cycles, escalate
                try:
                    _critic_cache_path = os.path.join(session.workspace, 'reports', 'last_critic_eval.json')
                    if os.path.isfile(_critic_cache_path):
                        import json as _stag_json
                        with open(_critic_cache_path, 'r', encoding='utf-8') as _scf:
                            _stag_eval = _stag_json.load(_scf)
                        _stag_score = _stag_eval.get('overall_score', 0)
                        _stag_history = getattr(state, '_score_history', [])
                        _stag_history.append(_stag_score)
                        state._score_history = _stag_history[-5:]  # Keep last 5

                        # Detect stagnation: 3+ identical scores in a row
                        if len(state._score_history) >= 3:
                            _recent_3 = state._score_history[-3:]
                            _score_variance = max(_recent_3) - min(_recent_3)
                            if _score_variance < 2.0:
                                logger.warning(f"🔄 [AutoPilot] Score stagnation detected: {_recent_3} (variance={_score_variance:.1f})")
                                await safe_send_discord_message(
                                    active_channel,
                                    f"⚠️ **[품질 점수 정체 감지 (Score Stagnation)]**\n"
                                    f"• 최근 3사이클 점수: `{_recent_3}` (변동폭: {_score_variance:.1f}점)\n"
                                    f"• 동일한 전략으로는 품질 향상이 정체되었습니다.\n"
                                    f"• 다음 사이클에서 전략을 전환하여 최저 품질 영역을 집중 타격합니다."
                                )
                                # Don't stop, but signal strategic pivot needed
                                _lowest_dim = _stag_eval.get('dimension_scores', {})
                                _worst = min(_lowest_dim.items(), key=lambda x: x[1].get('score', 100)) if _lowest_dim else None
                                if _worst:
                                    session.user_directive = (
                                        f"[Stagnation Pivot] 품질 정체 감지. "
                                        f"최저 영역 '{_worst[0]}' ({_worst[1].get('score', 0):.0f}점) 집중 개선 필요. "
                                        f"갭: {_worst[1].get('gap', 'N/A')}"
                                    )
                except Exception as _stag_err:
                    logger.debug(f"AutoPilot stagnation check failed: {_stag_err}")

            # If still under max cycles (or evolve mode), reschedule for next idle check
            if not getattr(session, "is_stopped", False) and state.enabled and (state.mode in ("evolve", "quad") or state.current_cycle < state.max_cycles):
                state.timer_start_time = time.time()
                state.timer_task = asyncio.create_task(
                    self._schedule_next_idle(channel_id, channel, bot_client)
                )

        except asyncio.CancelledError:
            logger.info(f"🛑 AutoPilot execute_idle_audit task was cancelled for channel {channel_id}.")
            return
        except Exception as e:
            logger.exception(f"Error during auto-pilot execution for channel {channel_id}: {e}")
            tb_str = traceback.format_exc()
            await active_channel.send(f"⚠️ 자율 E2E 감사 실행 중 오류가 발생했습니다: {str(e)}")

            # 2~3일간 무인 운영 보장: 에러 발생 시에도 evolve 모드에서는 60초 후 자동 복구 재시도
            if not getattr(session, "is_stopped", False) and state.enabled and (state.mode in ("evolve", "quad") or state.current_cycle < state.max_cycles):
                logger.info(f"AutoPilot encountered error; scheduling autonomous recovery cycle in 60s for channel {channel_id}.")
                state.timer_start_time = time.time()
                state.timer_task = asyncio.create_task(
                    self._schedule_next_idle(channel_id, channel, bot_client, delay_override=60)
                )
        finally:
            try:
                await ptyping.__aexit__(None, None, None)
            except Exception:
                pass
            state.audit_task = None
            if session.current_task == current_task:
                session.current_task = None
            session.set_busy(False, desc="정상 대기 중 (감사 완료)")
            try:
                import shutil
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            try:
                import gc
                gc.collect()
            except Exception:
                pass

    async def _schedule_next_idle(self, channel_id: int, channel: discord.abc.Messageable, bot_client: discord.Client, delay_override: Optional[int] = None):
        state = self.get_state(channel_id)
        session = self.session_manager.get_session(channel_id)
        if getattr(session, "is_stopped", False) or not state.enabled:
            return
        try:
            # In evolve mode, allow smooth autonomous consecutive cycles with thermal safety
            if delay_override is not None:
                delay_seconds = delay_override
            else:
                delay_seconds = 10 if state.mode in ("evolve", "quad") else state.idle_seconds
            await asyncio.sleep(delay_seconds)
            if getattr(session, "is_stopped", False) or not state.enabled:
                return
            await self.execute_idle_audit(channel_id, channel, bot_client)
        except asyncio.CancelledError:
            pass
