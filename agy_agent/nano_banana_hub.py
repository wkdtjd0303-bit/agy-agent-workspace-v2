#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nano_banana_hub.py

Dedicated Nano Banana Visual Asset Studio & Channel Routing Hub.
Manages the cross-channel image asset generation lifecycle for Discord:
- Channel ID: 1549422783803691058 (#nano-banana-image)
- Role: Dedicated Image Generation Studio (이미지 생성 전용 공간)
- Actor: Coder Bot

Workflow:
1. Identify visual detail deficits during the Quality Enhancement Phase (buttons, icons, badges, splash).
2. Route/Dispatch asset generation requests to #nano-banana-image (1549422783803691058).
3. Post generated high-res visual assets and prompt provenance to #nano-banana-image for transparent gallery archiving.
4. Automatically place assets into native project directories (app/src/main/res/drawable/ or public/assets/).
5. Return to origin project channel and integrate the assets into source code (Compose, React, XML, HTML5 Canvas).
"""

import os
import re
import time
import logging
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

import discord

from config import NANO_BANANA_IMAGE_CHANNEL_ID
from hybrid_asset_pipeline import HybridAssetPipeline, AssetPlacementResult
from asset_taxonomy_manager import classify_asset_request, optimize_prompt_for_taxonomy, AssetGranularity

logger = logging.getLogger("NanoBananaHub")


class NanoBananaHub:
    """
    Central hub for coordinating Nano Banana visual asset creation in #nano-banana-image.
    """
    CHANNEL_ID: int = NANO_BANANA_IMAGE_CHANNEL_ID
    CHANNEL_NAME: str = "nano-banana-image"
    DEFAULT_WORKSPACE: str = str(Path.home() / "Desktop" / "autocoding" / "!94_nano_banana_image")

    @classmethod
    def is_nano_banana_channel(cls, channel_id: int) -> bool:
        """Returns True if the given channel is the dedicated #nano-banana-image channel."""
        return int(channel_id) == cls.CHANNEL_ID

    @classmethod
    def classify_and_optimize(
        cls,
        asset_name: str,
        category: str = "button",
        prompt_hint: str = "",
        aspect_ratio: str = "1:1"
    ) -> Dict[str, Any]:
        """
        Classifies asset request into Type A (Full Scene) or Type B (Modular Part)
        and crafts an optimized prompt for Nano Banana image generation.
        """
        spec = classify_asset_request(f"{category} {asset_name}")
        is_type_a = spec.get("granularity") == AssetGranularity.FULL_SCENE
        is_isolated = not is_type_a

        clean_name = HybridAssetPipeline.sanitize_asset_name(asset_name)
        opt_prompt = optimize_prompt_for_taxonomy(
            f"{clean_name} {prompt_hint}".strip(),
            asset_type=category
        )

        # Dimension calculation
        if aspect_ratio == "9:16":
            w, h = 450, 800
        elif aspect_ratio == "16:9":
            w, h = 800, 450
        elif aspect_ratio == "4:3":
            w, h = 640, 480
        else:
            w, h = 512, 512

        return {
            "asset_name": clean_name,
            "category": category,
            "is_type_a": is_type_a,
            "is_isolated": is_isolated,
            "granularity": "FULL_SCENE" if is_type_a else "MODULAR_PART",
            "optimized_prompt": opt_prompt,
            "aspect_ratio": aspect_ratio,
            "width": w,
            "height": h
        }

    @classmethod
    def generate_and_place_asset(
        cls,
        workspace: str,
        asset_name: str,
        category: str = "button",
        description: str = "",
        aspect_ratio: str = "1:1",
        platform_override: Optional[str] = None
    ) -> AssetPlacementResult:
        """
        Generates the visual asset, places it in the project's native resource folder,
        and generates drop-in source code integration snippets.
        """
        return HybridAssetPipeline.generate_and_place_asset(
            workspace=workspace,
            asset_name=asset_name,
            category=category,
            description=description,
            aspect_ratio=aspect_ratio,
            platform_override=platform_override
        )

    @classmethod
    async def dispatch_asset_to_channel(
        cls,
        bot_client: Optional[Any],
        placement_result: AssetPlacementResult,
        origin_channel_id: int,
        prompt_hint: str = ""
    ) -> bool:
        """
        Asynchronously sends the generated visual asset and metadata report to #nano-banana-image (1549422783803691058).
        """
        if not bot_client:
            logger.warning("[NanoBananaHub] No bot client available for dispatch.")
            return False

        try:
            target_channel = bot_client.get_channel(cls.CHANNEL_ID)
            if not target_channel and hasattr(bot_client, "fetch_channel"):
                try:
                    target_channel = await bot_client.fetch_channel(cls.CHANNEL_ID)
                except Exception:
                    target_channel = None

            if not target_channel:
                logger.error(f"[NanoBananaHub] Target channel <#{cls.CHANNEL_ID}> not accessible.")
                return False

            embed = discord.Embed(
                title=f"🍌 [나노바나나 에셋 스튜디오] {placement_result.asset_name}.png",
                description=(
                    f"**요청 출처**: <#{origin_channel_id}>\n"
                    f"**분류**: `{placement_result.granularity}` ({placement_result.category})\n"
                    f"**해상도**: `{placement_result.width}x{placement_result.height}` | **포맷**: `{placement_result.format.upper()}`\n"
                    f"**프로젝트 배치 경로**: `{placement_result.relative_path}`\n"
                    + (f"**프롬프트/설명**: {prompt_hint}\n" if prompt_hint else "")
                ),
                color=0xF59E0B,  # Warm banana amber
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="AGY Nano Banana Visual Asset Studio • Coder Bot")

            discord_files = []
            if placement_result.is_verified_on_disk and os.path.isfile(placement_result.absolute_path):
                discord_files.append(
                    discord.File(
                        fp=placement_result.absolute_path,
                        filename=f"{placement_result.asset_name}.png"
                    )
                )

            await target_channel.send(embed=embed, files=discord_files)
            logger.info(f"🎨 [NanoBananaHub] Dispatched '{placement_result.asset_name}.png' to <#{cls.CHANNEL_ID}>.")
            return True

        except Exception as e:
            logger.exception(f"[NanoBananaHub] Failed to dispatch asset to <#{cls.CHANNEL_ID}>: {e}")
            return False

    @classmethod
    def format_origin_report(
        cls,
        placement_result: AssetPlacementResult,
        origin_channel_id: int,
        prompt_hint: str = "",
        platform: str = "android"
    ) -> str:
        """
        Formats a comprehensive completion report to be returned to the active project channel.
        """
        snippet_key = "compose" if platform == "android" else "react_tsx"
        code_snip = placement_result.code_snippets.get(snippet_key, "")

        report = (
            f"🎨 **[나노바나나 에셋 스튜디오 교체 완료]**\n"
            f"• **제작 거점**: <#{cls.CHANNEL_ID}> (`#{cls.CHANNEL_NAME}`)\n"
            f"• **에셋 규격**: `{placement_result.asset_name}.png` ({placement_result.granularity} - {placement_result.category})\n"
            f"• **배치 위치**: `{placement_result.relative_path}`\n"
            f"• **물리 검증**: `{'✅ 디스크 저장 확인' if placement_result.is_verified_on_disk else '⚠️ 파일 미확인'}`\n\n"
            f"**💡 코드 교체 스니펫 ({snippet_key.upper()}):**\n"
            f"```kotlin\n{code_snip}\n```\n"
            f"✨ 기존의 텍스트/도형 플레이스홀더를 위 고화질 이미지 에셋으로 교체하여 UI 디테일을 보강했습니다."
        )
        return report

    @classmethod
    def verify_code_integration(cls, workspace: str, asset_name: str) -> bool:
        """Verifies if the generated asset identifier is referenced in the project's source code."""
        return HybridAssetPipeline.verify_code_integration(workspace, asset_name)
