#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from telemetry_benchmark import QuantitativeBenchmarkEngine, QuantitativeMetric


class TestTelemetryBenchmark(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="agy_bench_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_quantitative_benchmarks_all_pass(self):
        """Verify that all 4 quantitative metrics pass with compliant values."""
        res = QuantitativeBenchmarkEngine.evaluate_benchmarks(
            measured_fps=59.8,
            measured_viewport_ratio=78.2,
            measured_pss_delta_mb=2.4,
            measured_48dp_compliance=100.0,
            workspace=self.test_dir
        )

        self.assertTrue(res["all_passed"])
        self.assertIn("전수 통과", res["summary"])

        metrics = res["metrics"]
        self.assertEqual(metrics["rendering_fps"]["measured_value"], 59.8)
        self.assertTrue(metrics["rendering_fps"]["passed"])

        self.assertEqual(metrics["viewport_visible_ratio"]["measured_value"], 78.2)
        self.assertTrue(metrics["viewport_visible_ratio"]["passed"])

        self.assertEqual(metrics["memory_pss_delta"]["measured_value"], 2.4)
        self.assertTrue(metrics["memory_pss_delta"]["passed"])

        self.assertEqual(metrics["touch_target_48dp"]["measured_value"], 100.0)
        self.assertTrue(metrics["touch_target_48dp"]["passed"])

        # Check Table Markdown formatting
        table_md = res["table_markdown"]
        self.assertIn("| 계측 항목 (Metric) | 실측치 (Measured) | 기준치 (Standard) | 판정 (Status) |", table_md)
        self.assertIn("렌더링 FPS", table_md)
        self.assertIn("59.8 FPS", table_md)
        self.assertIn("뷰포트 가시 비율 %", table_md)
        self.assertIn("78.2 %", table_md)
        self.assertIn("메모리 PSS 변화량", table_md)
        self.assertIn("+2.4 MB", table_md)
        self.assertIn("48dp 준수율", table_md)
        self.assertIn("100.0 %", table_md)

        # Check Gate 4 JSON persistence
        json_file = os.path.join(self.test_dir, "reports", "gate4", "quantitative_benchmarks.json")
        self.assertTrue(os.path.isfile(json_file))
        with open(json_file, "r", encoding="utf-8") as f:
            persisted = json.load(f)
        self.assertTrue(persisted["all_passed"])

    def test_quantitative_benchmarks_fail_on_substandard_values(self):
        """Verify that failing any metric flags all_passed=False and shows FAIL in table."""
        res = QuantitativeBenchmarkEngine.evaluate_benchmarks(
            measured_fps=48.0,            # < 55.0 FPS -> FAIL
            measured_viewport_ratio=52.0,  # < 65.0 % -> FAIL
            measured_pss_delta_mb=28.5,    # > 15.0 MB -> FAIL
            measured_48dp_compliance=88.0, # < 95.0 % -> FAIL
            workspace=self.test_dir
        )

        self.assertFalse(res["all_passed"])
        self.assertIn("기준 미달", res["summary"])

        metrics = res["metrics"]
        self.assertFalse(metrics["rendering_fps"]["passed"])
        self.assertFalse(metrics["viewport_visible_ratio"]["passed"])
        self.assertFalse(metrics["memory_pss_delta"]["passed"])
        self.assertFalse(metrics["touch_target_48dp"]["passed"])

        table_md = res["table_markdown"]
        self.assertIn("🚨 FAIL", table_md)

    def test_unmeasured_values_yield_not_measured(self):
        """Verify that missing/unmeasured metrics do NOT mock default pass values and return NOT_MEASURED."""
        res = QuantitativeBenchmarkEngine.evaluate_benchmarks(
            workspace=self.test_dir
        )

        self.assertFalse(res["all_passed"])
        self.assertTrue(res["has_unmeasured"])
        self.assertIn("미계측", res["summary"])

        metrics = res["metrics"]
        for key in ["rendering_fps", "viewport_visible_ratio", "memory_pss_delta", "touch_target_48dp"]:
            m = metrics[key]
            self.assertIsNone(m["measured_value"])
            self.assertIsNone(m["passed"])
            self.assertEqual(m["status"], "NOT_MEASURED")
            self.assertIn("N/A", m["formatted_measured"])
            self.assertEqual(m["badge"], "⚪ NOT MEASURED")

        table_md = res["table_markdown"]
        self.assertIn("⚪ NOT MEASURED", table_md)
        self.assertIn("N/A (미계측)", table_md)

    def test_partial_measured_values(self):
        """Verify that partial metrics measurement marks all_passed=False and shows both measured and unmeasured."""
        res = QuantitativeBenchmarkEngine.evaluate_benchmarks(
            measured_fps=60.0,
            # viewport, pss, 48dp omitted -> unmeasured
            workspace=self.test_dir
        )

        self.assertFalse(res["all_passed"])
        self.assertTrue(res["has_unmeasured"])
        self.assertEqual(res["metrics"]["rendering_fps"]["status"], "PASS")
        self.assertEqual(res["metrics"]["rendering_fps"]["badge"], "🟢 PASS")
        self.assertEqual(res["metrics"]["viewport_visible_ratio"]["status"], "NOT_MEASURED")
        self.assertEqual(res["metrics"]["viewport_visible_ratio"]["badge"], "⚪ NOT MEASURED")


if __name__ == "__main__":
    unittest.main()
