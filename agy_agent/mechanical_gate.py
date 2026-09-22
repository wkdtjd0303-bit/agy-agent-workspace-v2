#!/usr/bin/env python3
"""
🏛️ Mechanical Gate Engine (AGY 3.0 Platform Standard)
Enforces immutable, objective mechanical verification independent of AI verbal claims.
Constitutional Law:
  "PASS is NOT an AI's subjective claim. It requires automated, verifiable physical evidence.
   Even if the AI Auditor asserts PASS, if any Mechanical Gate check fails, the verdict is strictly FAIL."
"""

import os
import sys
import json
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("MechanicalGate")


@dataclass
class MechanicalCheck:
    """Individual mechanical validation check result."""
    name: str
    passed: bool
    details: str
    evidence_path: Optional[str] = None
    status: str = "PASS"  # "PASS", "FAIL", "UNVERIFIED", "BLOCKED"
    is_unverified: bool = False
    is_blocked: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.passed and self.status == "PASS":
            if self.is_unverified:
                self.status = "UNVERIFIED"
            elif self.is_blocked:
                self.status = "BLOCKED"
            else:
                self.status = "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "status": self.status,
            "is_unverified": self.is_unverified,
            "is_blocked": self.is_blocked,
            "details": self.details,
            "evidence_path": self.evidence_path,
            "metadata": self.metadata
        }


@dataclass
class MechanicalGateResult:
    """Consolidated result of a Mechanical Gate evaluation supporting 4-state machine."""
    gate_name: str
    verdict: str  # "PASS", "FAIL", "UNVERIFIED", "BLOCKED"
    ai_verdict: str  # Original verdict claimed by AI
    ai_overruled: bool  # True if AI said PASS but MechanicalGate forced FAIL/UNVERIFIED
    total_checks: int
    passed_checks: int
    failed_checks: int
    unverified_checks: int = 0
    blocked_checks: int = 0
    failures: List[Dict[str, Any]] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "verdict": self.verdict,
            "ai_verdict": self.ai_verdict,
            "ai_overruled": self.ai_overruled,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "unverified_checks": self.unverified_checks,
            "blocked_checks": self.blocked_checks,
            "failures": self.failures,
            "checks": self.checks,
            "summary": self.summary
        }


class MechanicalGate:
    """
    Automated, deterministic gate keeper.
    Never relies on LLM sentiment; inspects physical files, OS processes, checksums, and pixel buffers.
    """

    @staticmethod
    def assert_process_alive(pid: int) -> MechanicalCheck:
        """Verifies process is physically executing in OS table."""
        if pid <= 0:
            return MechanicalCheck(
                name="process_alive",
                passed=False,
                details=f"Invalid PID: {pid}",
                metadata={"pid": pid}
            )
        try:
            os.kill(pid, 0)
            return MechanicalCheck(
                name="process_alive",
                passed=True,
                details=f"Process PID {pid} is running in OS process table",
                metadata={"pid": pid}
            )
        except OSError as e:
            return MechanicalCheck(
                name="process_alive",
                passed=False,
                details=f"Process PID {pid} is dead or inaccessible: {e}",
                metadata={"pid": pid}
            )

    @staticmethod
    def assert_file_exists_and_not_empty(path: str, min_bytes: int = 1) -> MechanicalCheck:
        """Verifies physical file existence and non-zero byte size on filesystem."""
        if not path or not os.path.exists(path):
            return MechanicalCheck(
                name="file_exists",
                passed=False,
                details=f"File not found on disk: {path}",
                evidence_path=path
            )
        try:
            sz = os.path.getsize(path)
            if sz < min_bytes:
                return MechanicalCheck(
                    name="file_not_empty",
                    passed=False,
                    details=f"File is under min_bytes ({sz} < {min_bytes}): {path}",
                    evidence_path=path,
                    metadata={"size_bytes": sz}
                )
            return MechanicalCheck(
                name="file_exists_and_not_empty",
                passed=True,
                details=f"File verified on disk ({sz} bytes): {os.path.basename(path)}",
                evidence_path=path,
                metadata={"size_bytes": sz}
            )
        except Exception as e:
            return MechanicalCheck(
                name="file_check_error",
                passed=False,
                details=f"Error checking file {path}: {e}",
                evidence_path=path
            )

    @staticmethod
    def assert_sha256_checksum(path: str, expected_sha256: str) -> MechanicalCheck:
        """Computes and validates cryptographic SHA-256 hash."""
        if not path or not os.path.exists(path):
            return MechanicalCheck(
                name="sha256_verification",
                passed=False,
                details=f"Cannot compute hash: file does not exist ({path})",
                evidence_path=path
            )
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            computed = h.hexdigest()
            clean_expected = expected_sha256.strip().lower()
            if computed.lower() == clean_expected:
                return MechanicalCheck(
                    name="sha256_verification",
                    passed=True,
                    details=f"SHA-256 match verified: {computed[:16]}...",
                    evidence_path=path,
                    metadata={"sha256": computed}
                )
            else:
                return MechanicalCheck(
                    name="sha256_verification",
                    passed=False,
                    details=f"SHA-256 mismatch! Computed: {computed}, Expected: {clean_expected}",
                    evidence_path=path,
                    metadata={"computed": computed, "expected": clean_expected}
                )
        except Exception as e:
            return MechanicalCheck(
                name="sha256_verification",
                passed=False,
                details=f"Failed to calculate SHA-256: {e}",
                evidence_path=path
            )

    @staticmethod
    def assert_genuine_rendering(
        image_path: str,
        min_width: int = 100,
        min_height: int = 100,
        max_solid_stddev: float = 2.0
    ) -> MechanicalCheck:
        """
        Validates genuine framebuffer/UI screenshot:
        1. Confirms physical existence and image format (SCREENSHOT_CAPTURED).
        2. Ensures minimum viewport dimensions.
        3. Separates System Bars (Status Bar ~6%, Navigation Bar ~8%) to inspect Pure App Content Viewport.
        4. Detects Navigation-Bar-Only fake/dead renders (where nav bar has stddev > 2.0 but content is solid black/white).
        5. Detects unrendered canvas / crash remnant (e.g. 85%+ black background + isolated white box + nav bar).
        6. Enforces strict separation: SCREENSHOT_CAPTURED != VALID_APP_SCREEN.
        """
        if not image_path or not os.path.exists(image_path):
            return MechanicalCheck(
                name="genuine_rendering",
                passed=False,
                status="FAIL",
                details=f"Screenshot image not found on disk: {image_path}",
                evidence_path=image_path,
                metadata={"screenshot_captured": False, "valid_app_screen": False}
            )

        try:
            from PIL import Image, ImageStat
            with Image.open(image_path) as img:
                w, h = img.size
                if w < min_width or h < min_height:
                    return MechanicalCheck(
                        name="genuine_rendering",
                        passed=False,
                        status="FAIL",
                        details=f"Screenshot resolution too low ({w}x{h} < {min_width}x{min_height})",
                        evidence_path=image_path,
                        metadata={"screenshot_captured": True, "valid_app_screen": False, "width": w, "height": h}
                    )

                rgb_img = img.convert("RGB")
                full_stat = ImageStat.Stat(rgb_img)
                full_stddevs = full_stat.stddev

                # 1. Total solid blank check (whole image solid black or white)
                if all(s < max_solid_stddev for s in full_stddevs):
                    return MechanicalCheck(
                        name="genuine_rendering",
                        passed=False,
                        status="FAIL",
                        details=f"Blank/solid color screen detected (RGB stddev={full_stddevs}) - fake or dead UI",
                        evidence_path=image_path,
                        metadata={
                            "screenshot_captured": True,
                            "valid_app_screen": False,
                            "full_stddev": full_stddevs,
                            "rejection_reason": "TOTAL_SOLID_BLANK_SCREEN"
                        }
                    )

                # 2. System Bars Separation & Pure App Content Viewport Inspection
                # Status bar: top 0% ~ 6%, Navigation bar: bottom 92% ~ 100%
                top_cut = int(h * 0.06)
                bottom_cut = int(h * 0.92)
                if bottom_cut > top_cut and w >= 100:
                    content_crop = rgb_img.crop((0, top_cut, w, bottom_cut))
                    content_stat = ImageStat.Stat(content_crop)
                    content_stddevs = content_stat.stddev
                    content_avg_std = sum(content_stddevs) / len(content_stddevs)

                    # (A) Navigation-Bar-Only check:
                    # Content viewport is solid blank (< 2.0 stddev), but full image had stddev due to nav bar
                    if all(s < max_solid_stddev for s in content_stddevs):
                        return MechanicalCheck(
                            name="genuine_rendering",
                            passed=False,
                            status="FAIL",
                            details=(
                                f"SCREENSHOT_CAPTURED but INVALID_APP_SCREEN: Only Android navigation/status bar rendered. "
                                f"Pure content viewport is solid/empty (content stddev={content_stddevs})."
                            ),
                            evidence_path=image_path,
                            metadata={
                                "screenshot_captured": True,
                                "valid_app_screen": False,
                                "is_nav_bar_only": True,
                                "content_stddev": content_stddevs,
                                "rejection_reason": "NAV_BAR_ONLY_EMPTY_CONTENT"
                            }
                        )

                    # (B) Unrendered Canvas / Crash Remnant check:
                    # High background dominance (>= 85% black or white) with sparse unrendered box
                    gray_content = content_crop.convert("L")
                    histogram = gray_content.histogram()
                    total_pixels = (w * (bottom_cut - top_cut))
                    black_pixels = sum(histogram[:25])
                    white_pixels = sum(histogram[230:])
                    black_ratio = black_pixels / total_pixels
                    white_ratio = white_pixels / total_pixels
                    dominant_bg = max(black_ratio, white_ratio)

                    # Use NEAREST sampling to count true distinct UI colors without bicubic interpolation artifacts
                    sample_crop = content_crop.resize((64, 64), Image.Resampling.NEAREST)
                    colors_list = sample_crop.getcolors(maxcolors=256)
                    colors = len(colors_list) if colors_list else 256

                    # Detect if screen is 85%+ pure black or white canvas with sparse/isolated box
                    if (dominant_bg >= 0.85 and colors < 16) or (dominant_bg >= 0.95 and colors < 32) or (dominant_bg >= 0.88 and (content_avg_std < 7.0 or colors < 12)):
                        return MechanicalCheck(
                            name="genuine_rendering",
                            passed=False,
                            status="FAIL",
                            details=(
                                f"SCREENSHOT_CAPTURED but INVALID_APP_SCREEN: Detected unrendered canvas / crash remnant "
                                f"(dominant background {dominant_bg*100:.1f}%, unique colors={colors}, stddev={content_avg_std:.2f})"
                            ),
                            evidence_path=image_path,
                            metadata={
                                "screenshot_captured": True,
                                "valid_app_screen": False,
                                "is_unrendered_canvas": True,
                                "dominant_bg_ratio": dominant_bg,
                                "unique_colors": colors,
                                "rejection_reason": "UNRENDERED_SPARSE_CANVAS_OR_CRASH_REMNANT"
                            }
                        )

                return MechanicalCheck(
                    name="genuine_rendering",
                    passed=True,
                    status="PASS",
                    details=f"Genuine visual UI rendering confirmed ({w}x{h}, full stddev={round(sum(full_stddevs)/3, 2)})",
                    evidence_path=image_path,
                    metadata={
                        "screenshot_captured": True,
                        "valid_app_screen": True,
                        "width": w,
                        "height": h,
                        "stddev": full_stddevs
                    }
                )
        except Exception as e:
            return MechanicalCheck(
                name="genuine_rendering",
                passed=False,
                status="FAIL",
                details=f"Error inspecting screenshot: {e}",
                evidence_path=image_path,
                metadata={"screenshot_captured": False, "valid_app_screen": False}
            )

    @staticmethod
    def assert_action_screen_mutation(
        before_image_path: str,
        after_image_path: str,
        min_mutation_pct: float = 0.5
    ) -> MechanicalCheck:
        """
        Enforces Action ↔ Screenshot Causality (Items 21~26):
        1. Compares Before and After screenshots via pixel difference.
        2. Computes the UI mutation rate (percentage of altered pixels).
        3. Detects Action Stagnation: if mutation_pct == 0.0, the tap/scroll had no effect.
        """
        if not os.path.exists(before_image_path) or not os.path.exists(after_image_path):
            return MechanicalCheck(
                name="action_screen_mutation",
                passed=False,
                status="FAIL",
                details=f"Before/After screenshot missing: before={os.path.exists(before_image_path)}, after={os.path.exists(after_image_path)}"
            )

        try:
            from PIL import Image, ImageChops, ImageStat
            with Image.open(before_image_path) as img_b, Image.open(after_image_path) as img_a:
                if img_b.size != img_a.size:
                    # Dimensions changed (e.g. orientation rotation) -> distinct mutation!
                    return MechanicalCheck(
                        name="action_screen_mutation",
                        passed=True,
                        status="PASS",
                        details=f"Viewport resized between actions ({img_b.size} -> {img_a.size})",
                        metadata={"size_before": img_b.size, "size_after": img_a.size, "mutation_pct": 100.0}
                    )

                diff = ImageChops.difference(img_b.convert("RGB"), img_a.convert("RGB"))
                # Calculate bounding box of differences
                bbox = diff.getbbox()
                if not bbox:
                    return MechanicalCheck(
                        name="action_screen_mutation",
                        passed=False,
                        status="FAIL",
                        details="Action resulted in 0% UI mutation (exact duplicate screenshot). Action failed or had no effect.",
                        metadata={"mutation_pct": 0.0, "is_identical": True}
                    )

                # Compute percentage of non-zero pixels using histogram for high performance & Pillow compatibility
                gray_diff = diff.convert("L")
                diff_hist = gray_diff.histogram()
                changed_pixels = sum(diff_hist[9:])
                total_pixels = img_b.size[0] * img_b.size[1]
                mutation_pct = (changed_pixels / total_pixels) * 100.0

                if mutation_pct < min_mutation_pct:
                    return MechanicalCheck(
                        name="action_screen_mutation",
                        passed=False,
                        status="FAIL",
                        details=f"UI mutation rate too low ({mutation_pct:.3f}% < {min_mutation_pct}%). Likely negligible or stagnant action.",
                        metadata={"mutation_pct": mutation_pct, "changed_pixels": changed_pixels}
                    )

                return MechanicalCheck(
                    name="action_screen_mutation",
                    passed=True,
                    status="PASS",
                    details=f"Action caused genuine UI mutation ({mutation_pct:.2f}% of pixels altered)",
                    metadata={"mutation_pct": mutation_pct, "changed_pixels": changed_pixels}
                )
        except Exception as e:
            return MechanicalCheck(
                name="action_screen_mutation",
                passed=False,
                status="FAIL",
                details=f"Error verifying action screen mutation: {e}"
            )

    @staticmethod
    def assert_unique_screenshot(
        new_image_path: str,
        existing_screenshot_paths: List[str],
        max_similarity_pct: float = 99.5
    ) -> MechanicalCheck:
        """
        Prevents counting duplicate screenshots as new validations (Item 18):
        Ensures the newly captured screenshot represents a distinct screen state.
        """
        if not os.path.exists(new_image_path):
            return MechanicalCheck(
                name="unique_screenshot",
                passed=False,
                status="FAIL",
                details=f"Screenshot not found: {new_image_path}"
            )

        from PIL import Image, ImageChops
        try:
            with Image.open(new_image_path) as new_img:
                new_rgb = new_img.convert("RGB")
                total_px = new_img.size[0] * new_img.size[1]
                for prev_path in existing_screenshot_paths:
                    if prev_path == new_image_path or not os.path.exists(prev_path):
                        continue
                    with Image.open(prev_path) as prev_img:
                        if prev_img.size != new_img.size:
                            continue
                        diff = ImageChops.difference(new_rgb, prev_img.convert("RGB")).convert("L")
                        diff_px = sum(diff.histogram()[9:])
                        sim_pct = (1.0 - (diff_px / total_px)) * 100.0
                        if sim_pct >= max_similarity_pct:
                            return MechanicalCheck(
                                name="unique_screenshot",
                                passed=False,
                                status="FAIL",
                                details=f"Screenshot is a duplicate of {os.path.basename(prev_path)} ({sim_pct:.1f}% identical)",
                                metadata={"duplicate_of": prev_path, "similarity_pct": sim_pct}
                            )

            return MechanicalCheck(
                name="unique_screenshot",
                passed=True,
                status="PASS",
                details="Screenshot confirmed as unique visual state",
                evidence_path=new_image_path
            )
        except Exception as e:
            return MechanicalCheck(
                name="unique_screenshot",
                passed=False,
                status="FAIL",
                details=f"Error checking screenshot uniqueness: {e}"
            )

    @staticmethod
    def assert_intent_achievement(
        intent_name: str,
        requirements_evaluated: Dict[str, bool]
    ) -> MechanicalCheck:
        """
        Validates Intent Completion rather than superficial clicks (Items 51~60):
        e.g. 'PDF text addition & persistent disk saving & reload recovery'.
        """
        if not requirements_evaluated:
            return MechanicalCheck(
                name="intent_achievement",
                passed=False,
                status="UNVERIFIED",
                is_unverified=True,
                details=f"No requirements provided for intent '{intent_name}'"
            )

        failed_reqs = [k for k, v in requirements_evaluated.items() if not v]
        if failed_reqs:
            return MechanicalCheck(
                name="intent_achievement",
                passed=False,
                status="FAIL",
                details=f"Intent '{intent_name}' failed requirements: {', '.join(failed_reqs)}",
                metadata={"failed_requirements": failed_reqs, "all_requirements": requirements_evaluated}
            )

        return MechanicalCheck(
            name="intent_achievement",
            passed=True,
            status="PASS",
            details=f"Intent '{intent_name}' 100% achieved across {len(requirements_evaluated)} verified conditions",
            metadata={"all_requirements": requirements_evaluated}
        )

    @staticmethod
    def assert_action_trace_validity(trace: List[Dict[str, Any]], min_actions: int = 1) -> MechanicalCheck:
        """Verifies action trace is non-empty, contains valid action types, and legitimate execution records."""
        if not isinstance(trace, list) or len(trace) < min_actions:
            return MechanicalCheck(
                name="action_trace_validity",
                passed=False,
                details=f"Action trace too short or empty ({len(trace) if isinstance(trace, list) else 0} < {min_actions})"
            )
        valid_actions = {"tap", "swipe", "input_text", "key_back", "stroke", "joystick_drag", "click", "navigate"}
        recorded_actions = [a.get("action") for a in trace if isinstance(a, dict) and a.get("action")]
        if not recorded_actions:
            return MechanicalCheck(
                name="action_trace_validity",
                passed=False,
                details="Action trace contains no recognizable action types"
            )
        return MechanicalCheck(
            name="action_trace_validity",
            passed=True,
            details=f"Valid action trace confirmed ({len(recorded_actions)} actions: {set(recorded_actions)})",
            metadata={"actions_count": len(recorded_actions)}
        )

    @staticmethod
    def assert_no_critical_crashes(log_tail: str) -> MechanicalCheck:
        """Scans logcat or console output for fatal crashes and ANRs."""
        lowered = (log_tail or "").lower()
        fatal_keywords = ["fatal exception", "anr in", "sigsegv", "nullpointerexception", "segmentation fault", "process crashed"]
        detected = [kw for kw in fatal_keywords if kw in lowered]
        if detected:
            return MechanicalCheck(
                name="no_critical_crashes",
                passed=False,
                details=f"Critical crash signatures detected in log: {', '.join(detected)}",
                metadata={"detected_keywords": detected}
            )
        return MechanicalCheck(
            name="no_critical_crashes",
            passed=True,
            details="No critical crash or ANR signatures found in execution logs"
        )

    @classmethod
    def evaluate_gate(
        cls,
        gate_name: str,
        ai_verdict: str,
        checks: List[MechanicalCheck]
    ) -> MechanicalGateResult:
        """
        Evaluates gate status using a 4-state machine: PASS, FAIL, UNVERIFIED, BLOCKED.
        ENFORCES THE SUPREME MANDATE:
        1. If AI says PASS, but any single MechanicalCheck failed, AI is OVERRULED.
        2. If checks contain unverified or environment-blocked conditions, verdict is UNVERIFIED or BLOCKED.
        """
        total = len(checks)
        passed = sum(1 for c in checks if c.passed)
        failed = sum(1 for c in checks if not c.passed and not c.is_unverified and not c.is_blocked)
        unverified = sum(1 for c in checks if c.is_unverified or c.status == "UNVERIFIED")
        blocked = sum(1 for c in checks if c.is_blocked or c.status == "BLOCKED")
        failures = [c.to_dict() for c in checks if not c.passed]

        ai_clean = ai_verdict.strip().upper()
        ai_is_pass = (ai_clean == "PASS")

        if blocked > 0:
            final_verdict = "BLOCKED"
            ai_overruled = ai_is_pass
            summary = f"Gate {gate_name} BLOCKED: {blocked} checks blocked due to environment/precondition prerequisites."
        elif unverified > 0 and (failed == 0):
            final_verdict = "UNVERIFIED"
            ai_overruled = ai_is_pass
            summary = f"Gate {gate_name} UNVERIFIED: {unverified} checks could not be decisively verified by physical evidence."
        elif failed > 0:
            final_verdict = "FAIL"
            ai_overruled = ai_is_pass
            if ai_is_pass:
                summary = (
                    f"🚨 [AI OVERRULED] AI Auditor asserted PASS for {gate_name}, "
                    f"but Mechanical Gate strictly rejected it ({failed}/{total} mechanical checks failed)."
                )
            else:
                summary = f"Gate {gate_name} FAIL: AI asserted {ai_verdict}, and mechanical checks failed={failed}."
        elif total == 0:
            final_verdict = "UNVERIFIED"
            ai_overruled = ai_is_pass
            summary = f"Gate {gate_name} UNVERIFIED: No mechanical checks executed."
        else:
            final_verdict = "PASS"
            ai_overruled = False
            summary = f"Gate {gate_name} PASS: All {total} mechanical checks verified."

        return MechanicalGateResult(
            gate_name=gate_name,
            verdict=final_verdict,
            ai_verdict=ai_verdict,
            ai_overruled=ai_overruled,
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            unverified_checks=unverified,
            blocked_checks=blocked,
            failures=failures,
            checks=[c.to_dict() for c in checks],
            summary=summary
        )
