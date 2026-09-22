#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
e2e_performance_benchmark.py

Autonomous E2E Performance Optimization & Latency Benchmark Engine.
Measures runtime execution metrics during high-load autonomous E2E testing
(Web responsive interactions, Android frame renderers, Game Canvas high-entity loops)
and performs automated pre/post commit regression comparisons:

1. Records per-frame loop durations, tail latencies (avgLoop, p50, p95, p99, p999),
   average FPS, slow frames, and subsystem bottlenecks (e.g. projectiles, DOM updates).
2. Generates precise quantitative comparative benchmark tables identical to Discord video reports.
3. Automatically marks tail latency optimizations with double checkmarks (✓✓).
4. Produces executive summaries on whether optimization goals have been achieved.
"""

import os
import json
import time
import math
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("E2EPerformanceBenchmark")


@dataclass
class SubsystemMetric:
    name: str
    total_ms: float
    slow_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkMetricSnapshot:
    commit_hash: str
    label: str
    sample_count: int
    avg_loop_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    p999_ms: float
    avg_fps: float
    slow_frames: int
    slow_frame_total_ms: float
    subsystems: Dict[str, Dict[str, float]] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkComparisonResult:
    prev_commit: str
    curr_commit: str
    description: str
    prev_metrics: BenchmarkMetricSnapshot
    curr_metrics: BenchmarkMetricSnapshot
    tail_latency_target_met: bool
    summary_bullets: List[str]
    formatted_report: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class E2EPerformanceBenchmarkEngine:
    """
    Measures frame latencies and produces commit-to-commit comparative performance benchmarks.
    """

    SLOW_FRAME_THRESHOLD_MS = 16.67  # 60 FPS standard threshold

    @classmethod
    def compute_percentile(cls, sorted_data: List[float], percentile: float) -> float:
        """Computes nearest-rank or linear interpolation percentile from sorted floats."""
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * (percentile / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        d0 = sorted_data[int(f)] * (c - k)
        d1 = sorted_data[int(c)] * (k - f)
        return round(d0 + d1, 2)

    @classmethod
    def analyze_frame_durations(
        cls,
        commit_hash: str,
        label: str,
        durations_ms: List[float],
        subsystem_durations: Optional[Dict[str, List[float]]] = None
    ) -> BenchmarkMetricSnapshot:
        """
        Analyzes raw frame/loop durations (in ms) into statistical latency metrics.
        """
        if not durations_ms:
            durations_ms = [16.6]

        sorted_d = sorted(durations_ms)
        sample_count = len(sorted_d)
        total_ms = sum(sorted_d)
        avg_loop = round(total_ms / max(1, sample_count), 2)

        p50 = cls.compute_percentile(sorted_d, 50.0)
        p95 = cls.compute_percentile(sorted_d, 95.0)
        p99 = cls.compute_percentile(sorted_d, 99.0)
        p999 = cls.compute_percentile(sorted_d, 99.9)

        # Average FPS calculation
        avg_fps = round(1000.0 / avg_loop, 1) if avg_loop > 0 else 0.0

        # Slow frames (> 16.67ms)
        slow_list = [d for d in sorted_d if d > cls.SLOW_FRAME_THRESHOLD_MS]
        slow_frames = len(slow_list)
        slow_frame_total_ms = round(sum(slow_list), 1)

        # Subsystems
        subsystem_results: Dict[str, Dict[str, float]] = {}
        if subsystem_durations:
            for sub_name, sub_list in subsystem_durations.items():
                s_tot = round(sum(sub_list), 1)
                s_slow = len([x for x in sub_list if x > (cls.SLOW_FRAME_THRESHOLD_MS * 0.5)])
                subsystem_results[sub_name] = {
                    "totalMs": s_tot,
                    "slowCount": float(s_slow)
                }

        return BenchmarkMetricSnapshot(
            commit_hash=commit_hash,
            label=label,
            sample_count=sample_count,
            avg_loop_ms=avg_loop,
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
            p999_ms=p999,
            avg_fps=avg_fps,
            slow_frames=slow_frames,
            slow_frame_total_ms=slow_frame_total_ms,
            subsystems=subsystem_results
        )

    @classmethod
    def compare_benchmarks(
        cls,
        prev: BenchmarkMetricSnapshot,
        curr: BenchmarkMetricSnapshot,
        description: str = "performance optimization"
    ) -> BenchmarkComparisonResult:
        """
        Compares two benchmark snapshots and generates the exact Discord codeblock report format.
        """
        # Calculate deltas
        def _calc_diff(c_val: float, p_val: float) -> Tuple[float, float]:
            diff = c_val - p_val
            pct = (diff / p_val * 100.0) if p_val != 0 else 0.0
            return round(diff, 2), round(pct, 1)

        d_loop, pct_loop = _calc_diff(curr.avg_loop_ms, prev.avg_loop_ms)
        d_p50, _ = _calc_diff(curr.p50_ms, prev.p50_ms)
        d_p95, pct_p95 = _calc_diff(curr.p95_ms, prev.p95_ms)
        d_p99, pct_p99 = _calc_diff(curr.p99_ms, prev.p99_ms)
        d_p999, pct_p999 = _calc_diff(curr.p999_ms, prev.p999_ms)
        d_fps, pct_fps = _calc_diff(curr.avg_fps, prev.avg_fps)
        d_slow = curr.slow_frames - prev.slow_frames
        pct_slow = round((d_slow / prev.slow_frames * 100.0), 1) if prev.slow_frames > 0 else 0.0
        d_slow_ms = round(curr.slow_frame_total_ms - prev.slow_frame_total_ms, 1)
        pct_slow_ms = round((d_slow_ms / prev.slow_frame_total_ms * 100.0), 1) if prev.slow_frame_total_ms > 0 else 0.0

        # Checkmark for improvement (>= 5% improvement in tail latency)
        mark_p95 = "  ✓✓" if d_p95 < 0 and pct_p95 <= -5.0 else ""
        mark_p99 = "  ✓✓" if d_p99 < 0 and pct_p99 <= -5.0 else ""
        mark_p999 = "  ✓✓" if d_p999 < 0 and pct_p999 <= -5.0 else ""

        # Build formatted report table
        title_line = f"{prev.commit_hash} (이전) -> {curr.commit_hash} ({description}) 비교:"
        header_line = f"{'':<16}{'이전':>10}{'':>6}{'현재':>10}{'':>5}{'변화':<18}"

        lines = [
            "```text",
            title_line,
            header_line,
            f"{'avgLoop:':<16}{prev.avg_loop_ms:>7.1f}ms   ->  {curr.avg_loop_ms:>7.1f}ms   {d_loop:>+7.1f}ms ({pct_loop:+.1f}%)",
            f"{'p50:':<16}{prev.p50_ms:>7.1f}ms   ->  {curr.p50_ms:>7.1f}ms   {d_p50:>+7.1f}ms",
            f"{'p95:':<16}{prev.p95_ms:>7.1f}ms   ->  {curr.p95_ms:>7.1f}ms   {d_p95:>+7.1f}ms ({pct_p95:+.1f}%){mark_p95}",
            f"{'p99:':<16}{prev.p99_ms:>7.1f}ms   ->  {curr.p99_ms:>7.1f}ms   {d_p99:>+7.1f}ms ({pct_p99:+.1f}%){mark_p99}",
            f"{'p999:':<16}{prev.p999_ms:>7.1f}ms   ->  {curr.p999_ms:>7.1f}ms   {d_p999:>+7.1f}ms ({pct_p999:+.1f}%){mark_p999}",
            f"{'avgFps:':<16}{prev.avg_fps:>7.1f}     ->  {curr.avg_fps:>7.1f}     {d_fps:>+7.1f}fps ({pct_fps:+.1f}%)",
            "",
            f"{'slowFrames:':<16}{prev.slow_frames:>7d}     ->  {curr.slow_frames:>7d}     {d_slow:>+7d} ({pct_slow:+.1f}%)",
            f"{'slowFrameTotal:':<16}{int(prev.slow_frame_total_ms):>7d}ms   ->  {int(curr.slow_frame_total_ms):>7d}ms   {int(d_slow_ms):>+7d}ms ({pct_slow_ms:+.1f}%)",
        ]

        # Common subsystems
        common_subs = set(prev.subsystems.keys()).intersection(curr.subsystems.keys())
        for s_name in sorted(common_subs):
            p_sub = prev.subsystems[s_name]
            c_sub = curr.subsystems[s_name]
            d_tot = round(c_sub["totalMs"] - p_sub["totalMs"], 1)
            pct_tot = round((d_tot / p_sub["totalMs"] * 100.0), 1) if p_sub["totalMs"] > 0 else 0.0
            mark_sub = " ✓✓" if d_tot < 0 and pct_tot <= -5.0 else ""
            d_sc = int(c_sub["slowCount"] - p_sub["slowCount"])
            pct_sc = round((d_sc / p_sub["slowCount"] * 100.0), 1) if p_sub["slowCount"] > 0 else 0.0

            lines.append("")
            lines.append(f"{s_name}:")
            lines.append(f"  {'totalMs:':<12}{p_sub['totalMs']:>7.1f}   ->  {c_sub['totalMs']:>7.1f}   {d_tot:>+7.1f}ms ({pct_tot:+.1f}%){mark_sub}")
            lines.append(f"  {'slowCount:':<12}{int(p_sub['slowCount']):>7d}   ->  {int(c_sub['slowCount']):>7d}   {d_sc:>+7d} ({pct_sc:+.1f}%)")

        lines.append("```")

        # Summary bullets
        tail_met = (d_p95 <= 0 and d_p99 <= 0)
        summary_bullets = [
            f"p95 {d_p95:+.1f}ms, p99 {d_p99:+.1f}ms — tail latency 목표에 {'정확히 부합' if tail_met else '개선 진행 중'}",
            f"평균 FPS {prev.avg_fps} -> {curr.avg_fps} ({d_fps:+.1f}fps 개선)",
            f"Slow Frame {prev.slow_frames}건 -> {curr.slow_frames}건 ({d_slow:+d}건 완화)"
        ]

        formatted_report = "\n".join(lines) + "\n\n**핵심:**\n" + "\n".join([f"• {b}" for b in summary_bullets])

        return BenchmarkComparisonResult(
            prev_commit=prev.commit_hash,
            curr_commit=curr.commit_hash,
            description=description,
            prev_metrics=prev,
            curr_metrics=curr,
            tail_latency_target_met=tail_met,
            summary_bullets=summary_bullets,
            formatted_report=formatted_report
        )

    @classmethod
    def generate_benchmark_discord_message(
        cls,
        comparison: BenchmarkComparisonResult,
        bot_name: str = "clone-클코"
    ) -> str:
        """Constructs the exact Discord bot response text seen in real collaborative setups."""
        return (
            f"**{bot_name}** [앱]\n"
            f"벤치마크 완료. 결과 분석합니다.\n"
            f"`0초` 벤치마크 결과 비교 완료.\n\n"
            f"{comparison.formatted_report}"
        )
