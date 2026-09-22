#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vision_benchmark_integration.py

Comprehensive test suite verifying:
1. Multi-Agent Visual Feedback Loop (Peer review critique and conversational UI fixes)
2. Hybrid Visual Generator (Type A Full Scene & Type B Modular Parts with Discord payloads)
3. E2E Performance Benchmark Engine (p95/p99 tail latencies, slow frames, commit comparisons)
4. CrossPlatformE2ERunner unified integration.
"""

import os
import shutil
import tempfile
import pytest

from visual_feedback_loop import VisualFeedbackLoopEngine, VisualFeedbackSession
from hybrid_visual_generator import HybridVisualGenerator, GeneratedVisualAsset
from e2e_performance_benchmark import E2EPerformanceBenchmarkEngine, BenchmarkMetricSnapshot, BenchmarkComparisonResult
from cross_platform_e2e_runner import CrossPlatformE2ERunner


@pytest.fixture
def temp_workspace():
    tmp_dir = tempfile.mkdtemp(prefix="agy_test_ws_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_visual_feedback_loop_cycle(temp_workspace):
    # Create a dummy initial screenshot
    shot1 = os.path.join(temp_workspace, "ui_initial.png")
    with open(shot1, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x06\x00\x00\x00\x1f\xf3\xffa\x00\x00\x00\x00IEND\xaeB`\x82")

    shot2 = os.path.join(temp_workspace, "ui_remedy.png")
    with open(shot2, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x06\x00\x00\x00\x1f\xf3\xffa\x00\x00\x00\x00IEND\xaeB`\x82")

    session = VisualFeedbackLoopEngine.execute_peer_review_cycle(
        session_id="test_session_01",
        component_name="Pause / Game Over UI",
        worker_name="clone-코맥스",
        reviewer_name="clone-클로드",
        initial_shot_path=shot1,
        initial_specs=["기본 버튼 폰트 16px", "Xelu-dark 테마"],
        detected_defects=[
            "버튼 폰트가 너무 작아서 안 보임 (Pause 메뉴)",
            "Enter/Esc 텍스트 시인성 저하"
        ],
        remedy_bullets=[
            "ENTER / ESC 24px로 확대",
            "Xelu-dark 톤 유지",
            "게임패드 모드 무영향 검증"
        ],
        remedy_shot_path=shot2
    )

    assert session.session_id == "test_session_01"
    assert session.status == "APPROVED"
    assert session.final_verdict == "APPROVED"
    assert len(session.turns) == 4

    # Turn 1: Worker
    assert session.turns[0].speaker == "clone-코맥스"
    assert session.turns[0].role == "WORKER"
    assert "Pause / Game Over UI" in session.turns[0].content

    # Turn 2: Reviewer critique
    assert session.turns[1].speaker == "clone-클로드"
    assert session.turns[1].role == "REVIEWER"
    assert "버튼 폰트가 너무 작아서" in session.turns[1].content

    # Turn 3: Worker remedy
    assert session.turns[2].speaker == "clone-코맥스"
    assert "24px로 확대" in session.turns[2].content

    # Turn 4: Reviewer approval
    assert session.turns[3].speaker == "clone-클로드"
    assert "[STATUS: APPROVED]" in session.turns[3].content


def test_hybrid_visual_generator_type_b(temp_workspace):
    # Test Type B: Modular isolated part (e.g. joystick, logo)
    asset = HybridVisualGenerator.generate_asset(
        workspace=temp_workspace,
        asset_name="virtual_joystick_knob",
        category="joystick",
        width=256,
        height=256,
        is_type_a=False
    )

    assert asset.asset_type == "TYPE_B_MODULAR_PART"
    assert asset.category == "joystick"
    assert os.path.isfile(asset.file_path)
    assert os.path.exists(asset.file_path)

    # Check Discord payload
    payload = asset.discord_share_payload
    assert "virtual_joystick_knob" in payload["content"]
    assert len(payload["attachment_paths"]) == 1
    assert payload["attachment_paths"][0] == asset.file_path

    # Check code snippets
    assert "compose" in asset.code_snippets
    assert "react_tsx" in asset.code_snippets
    assert "html5_canvas" in asset.code_snippets
    assert "virtual_joystick_knob" in asset.code_snippets["compose"]


def test_hybrid_visual_generator_type_a(temp_workspace):
    # Test Type A: Full scene splash art
    asset = HybridVisualGenerator.generate_asset(
        workspace=temp_workspace,
        asset_name="splash_hero_bg",
        category="background",
        width=512,
        height=512,
        is_type_a=True
    )

    assert asset.asset_type == "TYPE_A_FULL_SCENE"
    assert os.path.isfile(asset.file_path)
    assert "splash_hero_bg" in asset.discord_share_payload["content"]


def test_e2e_performance_benchmark_engine():
    # Previous commit mock durations (higher latency)
    prev_durations = [7.0, 6.0, 8.5, 23.8, 34.3, 43.6, 5.8, 6.2, 22.0, 31.0]
    prev_subsystems = {
        "playerProjectiles": [150.0, 160.0, 180.0, 200.0, 250.0]
    }

    # Current commit mock durations (optimized, lower latency)
    curr_durations = [6.7, 5.9, 7.8, 21.4, 31.0, 39.7, 5.5, 5.9, 19.5, 28.0]
    curr_subsystems = {
        "playerProjectiles": [120.0, 130.0, 140.0, 160.0, 180.0]
    }

    prev_snap = E2EPerformanceBenchmarkEngine.analyze_frame_durations(
        commit_hash="73f28c989",
        label="이전",
        durations_ms=prev_durations,
        subsystem_durations=prev_subsystems
    )

    curr_snap = E2EPerformanceBenchmarkEngine.analyze_frame_durations(
        commit_hash="952d6bb44",
        label="현재",
        durations_ms=curr_durations,
        subsystem_durations=curr_subsystems
    )

    assert prev_snap.avg_loop_ms > 0
    assert curr_snap.avg_loop_ms > 0
    assert curr_snap.avg_loop_ms < prev_snap.avg_loop_ms

    # Compare commits
    comp = E2EPerformanceBenchmarkEngine.compare_benchmarks(
        prev=prev_snap,
        curr=curr_snap,
        description="batch removal"
    )

    assert comp.prev_commit == "73f28c989"
    assert comp.curr_commit == "952d6bb44"
    assert comp.tail_latency_target_met is True
    assert "avgLoop:" in comp.formatted_report
    assert "p95:" in comp.formatted_report
    assert "p99:" in comp.formatted_report
    assert "avgFps:" in comp.formatted_report
    assert "playerProjectiles:" in comp.formatted_report
    assert "✓✓" in comp.formatted_report

    discord_msg = E2EPerformanceBenchmarkEngine.generate_benchmark_discord_message(comp, bot_name="clone-클코")
    assert "clone-클코" in discord_msg
    assert "벤치마크 완료. 결과 분석합니다." in discord_msg


def test_cross_platform_e2e_runner_convenience_methods(temp_workspace):
    # 1. Visual Peer Review
    review_res = CrossPlatformE2ERunner.run_visual_peer_review(
        session_id="e2e_integration_test",
        component_name="ActionBar Navigation",
        worker_name="clover-원격코드",
        reviewer_name="clover-클코"
    )
    assert review_res.status == "APPROVED"
    assert len(review_res.turns) == 4

    # 2. Hybrid Visual Asset
    asset_res = CrossPlatformE2ERunner.generate_hybrid_visual_asset(
        workspace=temp_workspace,
        asset_name="badge_champion",
        category="seal"
    )
    assert os.path.isfile(asset_res.file_path)

    # 3. Commit Latency Benchmark
    bench_res = CrossPlatformE2ERunner.run_commit_latency_benchmark(
        prev_commit="111aaaa",
        curr_commit="222bbbb",
        prev_durations_ms=[10.0, 12.0, 25.0, 35.0],
        curr_durations_ms=[9.0, 11.0, 22.0, 30.0],
        description="optimizing loop"
    )
    assert bench_res.tail_latency_target_met is True
    assert "avgLoop:" in bench_res.formatted_report
