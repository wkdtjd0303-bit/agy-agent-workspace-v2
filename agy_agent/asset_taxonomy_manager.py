#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGY Visual Asset Taxonomy & Modular Generation Architecture
============================================================
Defines clear asset granularity types and prompt engineering rules for AI image generation.
Distinguishes between Full-Cut Illustrations (Scenes, Splash, Banners) and
Modular Isolated Parts (UI Controls, Joystick, Game Sprites, Seals, Badges).
"""

from enum import Enum
from typing import Dict, Any, List, Optional


class AssetGranularity(Enum):
    FULL_SCENE = "FULL_SCENE"           # Full-cut illustration, Background, Splash screen, Title Hero
    MODULAR_PART = "MODULAR_PART"       # Individual isolated item, Joystick, Character sprite, Seal, Icon
    SPRITE_ATLAS = "SPRITE_ATLAS"       # Multiple state icons, Animation frame sequence, Tileset


class AssetDomain(Enum):
    GAME = "GAME"                       # Game components (Joystick, Player, Obstacle, Coin)
    APP_UI = "APP_UI"                   # App controls (Badge, Seal, Custom Button, Logo)
    CREATIVE = "CREATIVE"               # Editorial, Marketing, Empty State Hero Illustration


ASSET_SPECS: Dict[str, Dict[str, Any]] = {
    # 1. Modular Game Controls
    "joystick": {
        "granularity": AssetGranularity.MODULAR_PART,
        "domain": AssetDomain.GAME,
        "description": "Virtual analog joystick base and knob",
        "recommended_aspect_ratio": "1:1",
        "prompt_template": "Isolated 2D game UI joystick knob and base, clean centered design, solid neutral background for transparent cutoff, crisp edges, high contrast UI asset",
        "isolated": True
    },
    "game_character_sprite": {
        "granularity": AssetGranularity.MODULAR_PART,
        "domain": AssetDomain.GAME,
        "description": "Individual game character or player sprite",
        "recommended_aspect_ratio": "1:1",
        "prompt_template": "Single 2D game character sprite, full body isolated on pure solid white/black background, no background elements, crisp contours, ready for sprite extraction",
        "isolated": True
    },
    "game_item": {
        "granularity": AssetGranularity.MODULAR_PART,
        "domain": AssetDomain.GAME,
        "description": "In-game collectible, weapon, coin, powerup",
        "recommended_aspect_ratio": "1:1",
        "prompt_template": "Single isolated game item or icon, centered, clean solid background, vivid lighting, sharp game asset styling",
        "isolated": True
    },
    # 2. App & Document UI Elements
    "corporate_seal": {
        "granularity": AssetGranularity.MODULAR_PART,
        "domain": AssetDomain.APP_UI,
        "description": "Official company / executive stamp seal",
        "recommended_aspect_ratio": "1:1",
        "prompt_template": "Official traditional seal stamp impression in vermilion red ink, authentic weathered textured stamp mark on pure solid background",
        "isolated": True
    },
    "security_badge": {
        "granularity": AssetGranularity.MODULAR_PART,
        "domain": AssetDomain.APP_UI,
        "description": "Metallic verification contract badge / emblem",
        "recommended_aspect_ratio": "1:1",
        "prompt_template": "Premium metallic gold luxury security certificate shield badge, embossed ribbon, sharp vector-like clean edges on solid background",
        "isolated": True
    },
    # 3. Full-Cut Hero & Scenes
    "splash_screen": {
        "granularity": AssetGranularity.FULL_SCENE,
        "domain": AssetDomain.APP_UI,
        "description": "Full mobile splash / landing screen artwork",
        "recommended_aspect_ratio": "9:16",
        "prompt_template": "Full-screen immersive splash hero illustration, rich cinematic atmosphere, harmonious palette, centered branding space",
        "isolated": False
    },
    "game_background": {
        "granularity": AssetGranularity.FULL_SCENE,
        "domain": AssetDomain.GAME,
        "description": "Side-scrolling or top-down parallax game stage background",
        "recommended_aspect_ratio": "16:9",
        "prompt_template": "Seamless atmospheric game environment landscape background, layered depth, no UI elements, pure environment scenery",
        "isolated": False
    },
    "empty_state_hero": {
        "granularity": AssetGranularity.FULL_SCENE,
        "domain": AssetDomain.CREATIVE,
        "description": "Empty state / 404 / Welcome creative scene",
        "recommended_aspect_ratio": "4:3",
        "prompt_template": "Aesthetic minimalist modern empty state 3D illustration scene, gentle ambient lighting, clean negative space",
        "isolated": False
    }
}


def classify_asset_request(keyword: str) -> Dict[str, Any]:
    """
    Analyzes asset intent and returns optimal granularity, prompt advice, and isolation requirements.
    """
    kw_lower = keyword.lower()
    for key, spec in ASSET_SPECS.items():
        if key in kw_lower or kw_lower in key:
            return spec

    # Heuristic rules
    modular_keywords = ["조이스틱", "joystick", "버튼", "button", "아이템", "item", "캐릭터", "스프라이트", "sprite", "아이콘", "icon", "직인", "인감", "seal", "stamp", "뱃지", "badge", "코인", "coin"]
    full_scene_keywords = ["배경", "background", "스플래시", "splash", "타이틀", "title", "일러스트", "illustration", "포스터", "poster", "씬", "scene", "전체", "풀컷"]

    for mk in modular_keywords:
        if mk in kw_lower:
            return {
                "granularity": AssetGranularity.MODULAR_PART,
                "domain": AssetDomain.GAME if any(g in kw_lower for g in ["게임", "game", "조이스틱", "캐릭터"]) else AssetDomain.APP_UI,
                "description": f"Custom modular asset for '{keyword}'",
                "recommended_aspect_ratio": "1:1",
                "prompt_template": f"Single isolated {keyword} asset, centered, solid single-color background for easy masking/transparency, sharp clean edges, zero clutter",
                "isolated": True
            }

    for fk in full_scene_keywords:
        if fk in kw_lower:
            return {
                "granularity": AssetGranularity.FULL_SCENE,
                "domain": AssetDomain.CREATIVE,
                "description": f"Full-scene artwork for '{keyword}'",
                "recommended_aspect_ratio": "16:9",
                "prompt_template": f"Full panoramic detailed illustration of {keyword}, rich cinematic lighting, complete background scenery",
                "isolated": False
            }

    # Default to Modular Part for safety
    return {
        "granularity": AssetGranularity.MODULAR_PART,
        "domain": AssetDomain.APP_UI,
        "description": f"Standard isolated asset for '{keyword}'",
        "recommended_aspect_ratio": "1:1",
        "prompt_template": f"Isolated {keyword} on plain solid background, centered, high quality asset",
        "isolated": True
    }


def optimize_prompt_for_taxonomy(user_prompt: str, asset_type: Optional[str] = None) -> str:
    """
    Enriches image generation prompt with isolation and background keywords based on taxonomy.
    """
    spec = classify_asset_request(asset_type or user_prompt)
    if spec["granularity"] == AssetGranularity.MODULAR_PART:
        isolation_suffix = " --isolated single object, centered on clean solid neutral background for transparency cropping, crisp contours, no background clutter"
        if isolation_suffix not in user_prompt:
            return user_prompt.strip() + "," + isolation_suffix
    return user_prompt


def get_hybrid_asset_pipeline():
    """Returns the HybridAssetPipeline class for asset generation and code linkage."""
    from hybrid_asset_pipeline import HybridAssetPipeline
    return HybridAssetPipeline

