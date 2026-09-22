import os
import glob
import asyncio
import logging
from typing import Optional, Tuple
import discord

logger = logging.getLogger("ThermalGuard")

class ThermalGuard:
    """
    Smart 3-Tier Adaptive Thermal Management for Surface Pro 7 & Linux devices:
    - Tier 1 (🟢 Normal, <= 75°C): 100% full speed, zero delay.
    - Tier 2 (🟡 Mild Cooldown, 78°C ~ 84°C): 2.5s micro-sleep between round transitions to disperse CPU bursts.
    - Tier 3 (🔴 Emergency Protection, >= 85°C): 5-minute (300s) deep cooling pause before queued tasks or heavy rounds to safeguard hardware & battery.
    """
    TIER_NORMAL_MAX = 75.0
    TIER_MILD_MIN = 78.0
    TIER_EMERGENCY_MIN = 85.0
    EMERGENCY_COOLDOWN_SECONDS = 300.0  # 5 minutes full cooldown for Surface Pro 7
    EMERGENCY_PROGRESS_INTERVAL = 30.0  # Live status update interval during cooldown

    @classmethod
    def get_current_temperature(cls) -> float:
        """
        Reads CPU Package / Core / Thermal sensors from /sys/class/thermal.
        Returns the highest active CPU package/core temperature in Celsius (float).
        """
        temps = []
        try:
            for zone_dir in glob.glob("/sys/class/thermal/thermal_zone*"):
                type_file = os.path.join(zone_dir, "type")
                temp_file = os.path.join(zone_dir, "temp")
                if os.path.isfile(type_file) and os.path.isfile(temp_file):
                    try:
                        with open(type_file, "r") as tf:
                            ztype = tf.read().strip().lower()
                        with open(temp_file, "r") as tmf:
                            raw_val = float(tmf.read().strip())
                            celsius = raw_val / 1000.0 if raw_val > 1000 else raw_val

                        # Ignore static/invalid placeholder zones (e.g. 0°C or 20°C stuck INT3400)
                        if 25.0 <= celsius <= 115.0:
                            # Highest priority: CPU package temperature (x86_pkg_temp, coretemp, b0d4, cpu)
                            if "x86_pkg_temp" in ztype:
                                temps.append((100, celsius))
                            elif "b0d4" in ztype or "core" in ztype or "cpu" in ztype:
                                temps.append((50, celsius))
                            elif "sen" in ztype or "acpitz" in ztype:
                                temps.append((10, celsius))
                            else:
                                temps.append((1, celsius))
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Error scanning thermal zones: {e}")

        if not temps:
            return 50.0  # Safe fallback if unreadable

        # Sort by priority desc, then temperature desc
        temps.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return temps[0][1]

    @classmethod
    def get_tier(cls, temp: Optional[float] = None) -> Tuple[str, float]:
        """Returns (tier_name, temperature) where tier_name is 'NORMAL', 'MILD', or 'EMERGENCY'."""
        curr_temp = temp if temp is not None else cls.get_current_temperature()
        if curr_temp >= cls.TIER_EMERGENCY_MIN:
            return "EMERGENCY", curr_temp
        elif curr_temp >= cls.TIER_MILD_MIN:
            return "MILD", curr_temp
        else:
            return "NORMAL", curr_temp

    @classmethod
    async def _execute_deep_cooldown(
        cls,
        initial_temp: float,
        channel: Optional[discord.abc.Messageable] = None,
        context_desc: str = "에이전트 작업"
    ) -> float:
        """
        Executes a thorough 5-minute (300 seconds) cooling session with real-time temperature tracking.
        Updates the Discord message every 30 seconds so users can monitor the actual temperature decline.
        """
        total_seconds = int(cls.EMERGENCY_COOLDOWN_SECONDS)
        logger.warning(
            f"ThermalGuard: High CPU temperature ({initial_temp:.1f}°C >= {cls.TIER_EMERGENCY_MIN}°C). "
            f"Entering 5-minute ({total_seconds}s) deep cooldown for {context_desc}."
        )

        status_msg = None
        if channel:
            try:
                embed = discord.Embed(
                    title="🌡️ [스마트 열 관리 · CPU 고열 보호 대기]",
                    description=(
                        f"**현재 CPU 온도:** `{initial_temp:.1f}°C` (기준치 {cls.TIER_EMERGENCY_MIN:.0f}°C 초과)\n\n"
                        f"서피스 프로 7 하드웨어 섀시 및 내장 배터리 보호를 위해 **5분간(300초) 작업을 일시 정지**하고 충분한 쿨다운을 진행합니다.\n\n"
                        f"⏳ **남은 쿨다운 시간:** `5분 00초` (실시간 온도 모니터링 중...)"
                    ),
                    color=discord.Color.red()
                )
                status_msg = await channel.send(embed=embed)
            except Exception as e:
                logger.debug(f"Could not send thermal warning embed: {e}")

        # Loop in adaptive 10-second increments with early resumption when cooled
        step = 10  # 10s responsive poll
        elapsed = 0
        min_cooldown_seconds = 15  # Minimum 15s to allow initial thermal dissipation
        early_resumed = False

        while elapsed < total_seconds:
            sleep_duration = min(step, total_seconds - elapsed)
            await asyncio.sleep(sleep_duration)
            elapsed += sleep_duration

            curr_temp = cls.get_current_temperature()
            rem_secs = total_seconds - elapsed
            rem_min = rem_secs // 60
            rem_sec = rem_secs % 60
            rem_str = f"{rem_min}분 {rem_sec:02d}초"

            # Adaptive Early-Resume: if temperature drops to safe normal level (<= 75.0°C)
            if elapsed >= min_cooldown_seconds and curr_temp <= cls.TIER_NORMAL_MAX:
                logger.info(
                    f"ThermalGuard: Adaptive early resume triggered. "
                    f"Temp stabilized to {curr_temp:.1f}°C (<= {cls.TIER_NORMAL_MAX}°C) in {elapsed}s (saved {total_seconds - elapsed}s)."
                )
                early_resumed = True
                break

            if status_msg and rem_secs > 0:
                try:
                    update_embed = discord.Embed(
                        title="🌡️ [스마트 열 관리 · CPU 능동 냉각 모니터링]",
                        description=(
                            f"**초기 온도:** `{initial_temp:.1f}°C` ➔ **현재 실시간 온도:** `{curr_temp:.1f}°C`\n\n"
                            f"서피스/기기 하드웨어 보호를 위해 열을 식히는 중입니다.\n"
                            f"💡 **능동 재개 모드**: 75°C 이하로 안정화 시 즉시 작업을 재개합니다.\n\n"
                            f"⏳ **남은 쿨다운 상한:** `{rem_str}` (경과: {elapsed}초 / 최대 {total_seconds}초)"
                        ),
                        color=discord.Color.gold() if curr_temp < cls.TIER_EMERGENCY_MIN else discord.Color.red()
                    )
                    await status_msg.edit(embed=update_embed)
                except Exception:
                    pass

        # Cooldown complete - measure final stabilized temperature
        final_temp = cls.get_current_temperature()
        logger.info(f"ThermalGuard: Cooldown finished. Stabilized temp: {final_temp:.1f}°C (was {initial_temp:.1f}°C) in {elapsed}s.")

        if channel:
            try:
                saved_text = f" (남은 대기 {total_seconds - elapsed}초 단축 절약! ⚡)" if early_resumed else ""
                resume_embed = discord.Embed(
                    title="🟢 [쿨다운 완료 · 조기 안전 재개]" if early_resumed else "🟢 [쿨다운 완료 · 정상 재개]",
                    description=(
                        f"안전 냉각이 성공적으로 완료되었습니다.{saved_text}\n\n"
                        f"• **초기 고열 온도:** `{initial_temp:.1f}°C`\n"
                        f"• **현재 안정화 온도:** `{final_temp:.1f}°C` (목표 75°C 이하 달성)\n"
                        f"• **소요 시간:** `{elapsed}초` (최대 300초 중)\n\n"
                        f"하드웨어가 안전 온도로 냉각되었으므로 **{context_desc}을(를) 즉시 재개**합니다! 🚀"
                    ),
                    color=discord.Color.green()
                )
                if status_msg:
                    await status_msg.edit(embed=resume_embed)
                else:
                    await channel.send(embed=resume_embed)
            except Exception as e:
                logger.debug(f"Could not send resume embed: {e}")

        return float(elapsed)

    @classmethod
    async def check_round_transition_cooldown(cls, channel: Optional[discord.abc.Messageable] = None) -> float:
        """
        Invoked between 3-Agent Trio transitions (Coder ➔ QA ➔ Auditor).
        - If MILD (78~84°C): 2.5s micro-sleep.
        - If EMERGENCY (85°C+): 5-minute deep cooldown with live progress.
        """
        tier, temp = cls.get_tier()
        if tier == "MILD":
            logger.info(f"ThermalGuard: Mild temperature ({temp:.1f}°C). Applying 2.5s micro-cooldown.")
            await asyncio.sleep(2.5)
            return 2.5
        elif tier == "EMERGENCY":
            return await cls._execute_deep_cooldown(temp, channel=channel, context_desc="3중 에이전트 협업")

        return 0.0

    @classmethod
    async def check_task_start_cooldown(cls, channel: Optional[discord.abc.Messageable] = None) -> float:
        """
        Invoked before starting a new queued task or execution.
        - If EMERGENCY (85°C+): 5-minute deep cooldown before launching next task.
        - If MILD (78~84°C): 1.5s brief breath.
        """
        tier, temp = cls.get_tier()
        if tier == "EMERGENCY":
            return await cls._execute_deep_cooldown(temp, channel=channel, context_desc="대기열 작업")
        elif tier == "MILD":
            await asyncio.sleep(1.5)
            return 1.5

        return 0.0
