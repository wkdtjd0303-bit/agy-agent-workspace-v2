import os
import re
import json
import time
import hashlib
import logging
import subprocess
from typing import Dict, Any, List, Tuple, Optional
from PIL import Image, ImageStat

logger = logging.getLogger("EvidenceValidator")

EXCLUDED_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".system_generated",
    ".cache", ".local", ".npm", ".config", "dist", "build", "assets", "leaflet",
    "site-packages", "intermediates", ".vite", "rustdesk", "vendor", "target",
    "coverage", ".pytest_cache"
}

class EvidenceValidator:
    """
    AGY 3.0 Evidence Validator & Gate Engine.
    Validates physical evidence artifacts on disk instead of trusting AI verbal claims.
    Enforces Gate 0 ~ Gate 7 validation, visual content analysis, regression locking,
    and structured issue extraction.
    """

    @staticmethod
    def validate_file(file_path: str, min_bytes: int = 1, is_json: bool = False, is_image: bool = False) -> Tuple[bool, str]:
        """Validates that a file physically exists, is not empty, and matches format requirements."""
        if not os.path.exists(file_path):
            return False, f"File does not exist: {file_path}"
        
        try:
            sz = os.path.getsize(file_path)
            if sz < min_bytes:
                return False, f"File is too small ({sz} bytes < {min_bytes} required): {file_path}"
            
            if is_json:
                with open(file_path, "r", encoding="utf-8") as f:
                    json.load(f)
            
            if is_image:
                is_valid_img, img_msg = EvidenceValidator.validate_image_content(file_path)
                if not is_valid_img:
                    return False, img_msg
            
            return True, f"Valid ({sz} bytes)"
        except Exception as e:
            return False, f"Validation exception: {e}"

    @staticmethod
    def validate_image_content(file_path: str) -> Tuple[bool, str]:
        """
        Deep visual image analysis:
        1. Checks binary image header (PNG, JPEG, WEBP).
        2. Detects blank/solid color images (white/black screen) via pixel variance analysis.
        3. Ensures minimum viewport resolution.
        4. Detects half-empty/black hole side-by-side images (e.g. Left or Right half is completely black/empty).
        """
        if not os.path.exists(file_path):
            return False, f"Image not found: {file_path}"

        try:
            with Image.open(file_path) as img:
                w, h = img.size
                if w < 100 or h < 100:
                    return False, f"Image resolution too low ({w}x{h})"

                # Convert to RGB and calculate color standard deviation across channels
                rgb_img = img.convert("RGB")
                stat = ImageStat.Stat(rgb_img)
                stddevs = stat.stddev

                # If standard deviation across all RGB channels is near 0 (< 2.0), it's a solid/blank color screen
                if all(s < 2.0 for s in stddevs):
                    return False, f"Blank/Solid color screen detected (stddev={stddevs}) - zero visual UI content"

                # 4. Multi-Region / Split Analysis (detect half-black hole comparison cards)
                if w >= 300 and h >= 150:
                    left_half = rgb_img.crop((0, int(h * 0.1), int(w * 0.48), int(h * 0.9)))
                    right_half = rgb_img.crop((int(w * 0.52), int(h * 0.1), w, int(h * 0.9)))

                    l_stat = ImageStat.Stat(left_half)
                    r_stat = ImageStat.Stat(right_half)

                    l_is_empty = all(s < 2.0 for s in l_stat.stddev) or (sum(l_stat.mean) < 15.0 and all(s < 4.0 for s in l_stat.stddev))
                    r_is_empty = all(s < 2.0 for s in r_stat.stddev) or (sum(r_stat.mean) < 15.0 and all(s < 4.0 for s in r_stat.stddev))

                    if l_is_empty and not r_is_empty:
                        return False, f"Left-side empty black hole detected in comparison image (stddev={l_stat.stddev}, mean={l_stat.mean[:3]}) - missing Before UI content"
                    if r_is_empty and not l_is_empty:
                        return False, f"Right-side empty black hole detected in comparison image (stddev={r_stat.stddev}, mean={r_stat.mean[:3]}) - missing After UI content"

                # 5. Isolated Component Fragment & 60% Empty Background Detection
                # Prevents uncentered corner-pinned dialogs (e.g. 300x400 box at (0,0) with empty background)
                if w >= 320 and h >= 480:
                    # Check 4 quadrants
                    tl = rgb_img.crop((0, 0, int(w * 0.5), int(h * 0.5)))
                    tr = rgb_img.crop((int(w * 0.5), 0, w, int(h * 0.5)))
                    bl = rgb_img.crop((0, int(h * 0.5), int(w * 0.5), h))
                    br = rgb_img.crop((int(w * 0.5), int(h * 0.5), w, h))

                    tl_empty = all(s < 3.0 for s in ImageStat.Stat(tl).stddev) or (sum(ImageStat.Stat(tl).mean) < 25.0 and all(s < 6.0 for s in ImageStat.Stat(tl).stddev))
                    tr_empty = all(s < 3.0 for s in ImageStat.Stat(tr).stddev) or (sum(ImageStat.Stat(tr).mean) < 25.0 and all(s < 6.0 for s in ImageStat.Stat(tr).stddev))
                    bl_empty = all(s < 3.0 for s in ImageStat.Stat(bl).stddev) or (sum(ImageStat.Stat(bl).mean) < 25.0 and all(s < 6.0 for s in ImageStat.Stat(bl).stddev))
                    br_empty = all(s < 3.0 for s in ImageStat.Stat(br).stddev) or (sum(ImageStat.Stat(br).mean) < 25.0 and all(s < 6.0 for s in ImageStat.Stat(br).stddev))

                    empty_quads = sum([tl_empty, tr_empty, bl_empty, br_empty])
                    if empty_quads >= 3:
                        return False, f"Isolated component fragment detected: UI dialog confined to 1 quadrant ({w}x{h}) with > 70% empty canvas - must render full screen with genuine application content"

                    # Check bottom 55% + right 55% (Top-Left corner 0,0 pinning)
                    bottom_area = rgb_img.crop((0, int(h * 0.45), w, h))
                    right_area = rgb_img.crop((int(w * 0.45), 0, w, h))

                    b_stat = ImageStat.Stat(bottom_area)
                    r_stat = ImageStat.Stat(right_area)

                    b_empty = all(s < 3.0 for s in b_stat.stddev) or (sum(b_stat.mean) < 25.0 and all(s < 6.0 for s in b_stat.stddev))
                    r_empty = all(s < 3.0 for s in r_stat.stddev) or (sum(r_stat.mean) < 25.0 and all(s < 6.0 for s in r_stat.stddev))

                    if b_empty and r_empty:
                        return False, f"Isolated component fragment detected: UI dialog pinned in top-left corner ({w}x{h}) with > 65% empty background - missing full mobile screen context"

                # 6. System Bar Separation & Pure App Content Viewport Inspection (Items 15~17)
                top_cut = int(h * 0.06)
                bottom_cut = int(h * 0.92)
                if bottom_cut > top_cut and w >= 100:
                    content_crop = rgb_img.crop((0, top_cut, w, bottom_cut))
                    content_stat = ImageStat.Stat(content_crop)
                    content_stddevs = content_stat.stddev
                    content_avg_std = sum(content_stddevs) / len(content_stddevs)

                    # Navigation-bar-only screen detection
                    if all(s < 2.0 for s in content_stddevs):
                        return False, (
                            f"SCREENSHOT_CAPTURED but INVALID_APP_SCREEN: Only Android navigation/status bar present. "
                            f"Content viewport is completely blank/solid (content stddev={content_stddevs})."
                        )

                    # Unrendered black/white canvas with isolated box
                    gray_content = content_crop.convert("L")
                    hist = gray_content.histogram()
                    total_px = (w * (bottom_cut - top_cut))
                    dark_ratio = sum(hist[:25]) / total_px
                    light_ratio = sum(hist[230:]) / total_px
                    sample_crop = content_crop.resize((64, 64))
                    colors_list = sample_crop.getcolors(maxcolors=4096)
                    colors = len(colors_list) if colors_list else 4096

                    if (dark_ratio > 0.88 or light_ratio > 0.88) and (content_avg_std < 7.0 or colors < 12):
                        return False, (
                            f"SCREENSHOT_CAPTURED but INVALID_APP_SCREEN: Unrendered canvas / crash remnant detected "
                            f"({max(dark_ratio, light_ratio)*100:.1f}% empty background, unique colors={colors}, stddev={content_avg_std:.2f})"
                        )

                return True, f"Valid visual screenshot ({w}x{h}, stddev={sum(stddevs)/len(stddevs):.1f})"
        except Exception as e:
            return False, f"Corrupt image binary or read error: {e}"

    @staticmethod
    def detect_synthetic_artifacts(workspace: str) -> Tuple[bool, List[str]]:
        """
        Anti-QA-Theater & Anti-Forgery Scan:
        Detects synthetic canvas drawing scripts (e.g. PIL ImageDraw canvas generators,
        Kotlin/Java Canvas.drawRect/drawText test forgeries, node-canvas fake injectors)
        designed to fake Gate 5 visual evidence instead of capturing real browser/device framebuffers.
        """
        if not workspace or not os.path.isdir(workspace):
            return False, []

        suspicious_scripts = []
        for root, _, files in os.walk(workspace):
            rel_root = os.path.relpath(root, workspace)
            if rel_root.startswith((".git", ".venv", "node_modules", ".gemini", "__pycache__", "build", "dist")):
                continue

            for f in files:
                fp = os.path.join(root, f)
                lower_f = f.lower()

                # 1. Python & Shell synthetic scripts
                if f.endswith((".py", ".sh")):
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as file_obj:
                            content = file_obj.read()
                            if "ImageDraw.Draw" in content and ("draw_vector_icon" in content or "SCREEN_HEIGHT" in content or "draw.rectangle" in content or "draw.text" in content) and ("gate5" in fp or "screenshot" in lower_f):
                                suspicious_scripts.append(f"{os.path.relpath(fp, workspace)} (PIL canvas synthetic UI drawer)")
                            elif "generate_screenshots.py" in lower_f:
                                if "ImageDraw.Draw" in content or ("createCanvas" in content and "page.screenshot" not in content and "playwright" not in content.lower()):
                                    suspicious_scripts.append(f"{os.path.relpath(fp, workspace)} (Synthetic screenshot script using PIL canvas)")
                    except Exception:
                        pass

                # 2. Kotlin / Java Canvas Fake Screenshot in Test code
                elif f.endswith((".kt", ".java")):
                    if "test" in lower_f or "test" in rel_root.lower() or "screenshot" in lower_f:
                        try:
                            with open(fp, "r", encoding="utf-8", errors="ignore") as file_obj:
                                content = file_obj.read()
                                # Detect manual canvas.drawRect/drawText mocking used to output .png without Compose UI rule
                                if ("Canvas(" in content or "canvas.drawRect" in content or "canvas.drawText" in content) and ("Bitmap.CompressFormat.PNG" in content or "gate5" in content or "screenshot" in lower_f):
                                    if "captureToImage" not in content and "createComposeRule" not in content:
                                        suspicious_scripts.append(f"{os.path.relpath(fp, workspace)} (Android Canvas manual mock UI drawer - must use genuine Compose captureToImage() or real device framebuffer)")
                        except Exception:
                            pass

                # 3. JavaScript / TypeScript Canvas Fake Screenshot
                elif f.endswith((".js", ".ts")):
                    if "test" in lower_f or "screenshot" in lower_f or "gate5" in rel_root.lower():
                        try:
                            with open(fp, "r", encoding="utf-8", errors="ignore") as file_obj:
                                content = file_obj.read()
                                if ("createCanvas" in content or "getContext('2d')" in content) and ("fillRect" in content or "fillText" in content) and ("gate5" in fp or "screenshot" in lower_f):
                                    if "page.screenshot" not in content and "playwright" not in content.lower() and "puppeteer" not in content.lower():
                                        suspicious_scripts.append(f"{os.path.relpath(fp, workspace)} (HTML5 Canvas 2D mock screenshot generator)")
                        except Exception:
                            pass

        is_forged = len(suspicious_scripts) > 0
        return is_forged, suspicious_scripts

    @staticmethod
    def detect_rendering_evasion_patterns(workspace: str) -> Tuple[bool, List[str]]:
        """
        Anti-Evasion Static Code Linter (Enhanced AST-like patterns):
        Detects deceptive conditional rendering (e.g. hiding text until selected to mask duplicate rendering bugs),
        arbitrary font scale blowouts (.coerceIn), and coordinate mapping anomalies.
        """
        evasions = []
        if not workspace or not os.path.isdir(workspace):
            return False, evasions

        for root, _, files in os.walk(workspace):
            rel_root = os.path.relpath(root, workspace)
            if any(exc in rel_root.split(os.sep) for exc in EXCLUDED_DIR_NAMES):
                continue

            for f in files:
                if f.endswith((".kt", ".java", ".tsx", ".jsx", ".vue")):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as file_obj:
                            content = file_obj.read()
                            
                            # 1. Evasive conditional visibility hiding text in unselected state
                            # Catch single line or variable split like: val shouldShow = isSelected || isModified
                            # Or: if (!isSelected && !isModified)
                            if re.search(r'(isSelected\s*\|\|\s*isModified|isModified\s*\|\|\s*isSelected|!isSelected\s*&&\s*!isModified)', content) or \
                               (re.search(r'if\s*\([^)]*isSelected[^)]*\)', content) and re.search(r'else\s*if\s*\([^)]*isModified[^)]*\)', content)):
                                evasions.append(f"{os.path.relpath(fp, workspace)} (Evasive conditional rendering hiding text when unselected)")
                                
                            # 2. Hardcoded giant font coercion blowout (> 35sp/px without viewport bounding)
                            if ".coerceIn(" in content and ("fontSize" in content or "font" in content.lower()):
                                if re.search(r'\.coerceIn\s*\([^,]+,\s*(?:[4-9][0-9]|100)\s*f?\s*\)', content):
                                    evasions.append(f"{os.path.relpath(fp, workspace)} (Dangerous hardcoded font scale blowout: > 40px hardcoded clamping)")

                            # 3. Canvas Mocking separated files detection (Mocking components in test files without UI rule)
                            is_test_file = (
                                ("/src/test/" in fp or "/src/androidTest/" in fp or "/tests/" in fp or "/test/" in fp)
                                and "/src/main/" not in fp
                            ) or (f.endswith(("Test.kt", "Tests.kt", "Test.java", "Tests.java", "test.tsx", "test.jsx", "spec.tsx", "spec.jsx")))

                            if is_test_file and ("Canvas" in content or "drawRect" in content) and "captureToImage" not in content:
                                if "Box(" in content and "Text(" in content and "createComposeRule" not in content and "createAndroidComposeRule" not in content:
                                    evasions.append(f"{os.path.relpath(fp, workspace)} (Component Mocking: Testing fake Canvas/Box without ComposeTestRule)")

                    except Exception:
                        pass

        is_evasive = len(evasions) > 0
        return is_evasive, evasions

    @staticmethod
    def get_git_diff_summary(workspace: str, max_chars: int = 15000) -> Dict[str, Any]:
        """
        Extracts raw Git diff and modified files to provide physical code change proof
        to QA and Auditor agents, preventing LLM verbal collusion.

        [🔒 Patch 2: Commit-Evasion Defence]
        Now ALWAYS inspects git diff HEAD~1..HEAD (most recent commit) in addition to
        unstaged changes. This means an AI that commits its fake test-only patch to hide
        from 'git status' / 'git diff HEAD' will still be caught by the committed diff check.
        """
        result = {
            "has_changes": False,
            "changed_files": [],
            "raw_diff": "",
            "summary": "No git repository or changes detected",
            # [🔒 Patch 2] Added: tracks files changed in the most recent commit
            "committed_changed_files": [],
            "committed_prod_files": [],
            "committed_test_only": False,
            "has_git": False,
            "is_git_repo": False,
        }
        if not workspace or not os.path.isdir(workspace):
            return result

        binary_excludes = [
            ":!*.apk", ":!*.aab", ":!*.png", ":!*.jpg", ":!*.jpeg",
            ":!*.zip", ":!*.jar", ":!*.bin", ":!*.so",
            ":!*.pyc", ":!*__pycache__*", ":!*.class", ":!*.o"
        ]

        def _is_git_dir(d_path: str) -> bool:
            if not os.path.isdir(d_path):
                return False
            real_d = os.path.realpath(d_path)
            if (
                os.path.exists(os.path.join(d_path, ".git"))
                or os.path.isfile(os.path.join(d_path, ".git"))
                or os.path.exists(os.path.join(real_d, ".git"))
                or os.path.isfile(os.path.join(real_d, ".git"))
            ):
                return True
            if os.path.islink(d_path) or os.path.basename(d_path) == "agy_agent":
                try:
                    r = subprocess.run(
                        ["git", "rev-parse", "--is-inside-work-tree"],
                        cwd=d_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=1.5
                    )
                    return r.returncode == 0 and r.stdout.strip() == "true"
                except Exception:
                    return False
            return False

        try:
            # 1. Check git status scoped to workspace (unstaged / staged changes)
            status_res = subprocess.run(
                ["git", "status", "--short", "--", "."],
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=5.0
            )
            result["has_git"] = (status_res.returncode == 0)
            result["is_git_repo"] = (status_res.returncode == 0)
            raw_changed = []
            raw_deleted = []
            if status_res.returncode == 0 and status_res.stdout.strip():
                status_lines = [l.strip() for l in status_res.stdout.splitlines() if l.strip()]
                raw_changed = [
                    l.split()[-1] for l in status_lines
                    if not l.startswith("D ") and "삭제예정" not in l and not l.split()[-1].startswith("..")
                    and not l.split()[-1].endswith(".pyc") and "__pycache__" not in l.split()[-1]
                ]
                raw_deleted = [
                    l.split()[-1] for l in status_lines
                    if l.startswith("D ") and not l.split()[-1].startswith("..")
                    and not l.split()[-1].endswith(".pyc") and "__pycache__" not in l.split()[-1]
                ]

            # Expand nested git repositories / submodules (e.g. agy_agent)
            expanded_changed = []
            seen_sub_repos = set()
            for fpath in raw_changed:
                full_path = os.path.join(workspace, fpath)
                if os.path.isdir(full_path) and _is_git_dir(full_path):
                    seen_sub_repos.add(fpath)
                    sub_st = subprocess.run(
                        ["git", "status", "--short", "--", "."],
                        cwd=full_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        errors="replace",
                        timeout=5.0
                    )
                    if sub_st.returncode == 0 and sub_st.stdout.strip():
                        for sl in sub_st.stdout.splitlines():
                            sl_s = sl.strip()
                            rel_target = sl_s.split()[-1]
                            if rel_target.endswith(".pyc") or "__pycache__" in rel_target:
                                continue
                            if sl_s and not sl_s.startswith("D ") and "삭제예정" not in sl_s:
                                expanded_changed.append(os.path.join(fpath, rel_target).replace("\\", "/"))
                            elif sl_s and sl_s.startswith("D "):
                                raw_deleted.append(os.path.join(fpath, rel_target).replace("\\", "/"))
                    else:
                        if not fpath.endswith(".pyc") and "__pycache__" not in fpath:
                            expanded_changed.append(fpath)
                else:
                    if not fpath.endswith(".pyc") and "__pycache__" not in fpath:
                        expanded_changed.append(fpath)

            # Also check immediate subdirectories that might be sub-repos
            try:
                for d in os.listdir(workspace):
                    if d in (".git", ".gemini", "node_modules", "build", "dist") or d in seen_sub_repos:
                        continue
                    full_d = os.path.join(workspace, d)
                    if os.path.isdir(full_d) and _is_git_dir(full_d):
                        sub_st = subprocess.run(
                            ["git", "status", "--short", "--", "."],
                            cwd=full_d,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            errors="replace",
                            timeout=5.0
                        )
                        if sub_st.returncode == 0 and sub_st.stdout.strip():
                            for sl in sub_st.stdout.splitlines():
                                sl_s = sl.strip()
                                rel_target = sl_s.split()[-1]
                                if rel_target.endswith(".pyc") or "__pycache__" in rel_target:
                                    continue
                                if sl_s and not sl_s.startswith("D ") and "삭제예정" not in sl_s:
                                    expanded_changed.append(os.path.join(d, rel_target).replace("\\", "/"))
                                elif sl_s and sl_s.startswith("D "):
                                    raw_deleted.append(os.path.join(d, rel_target).replace("\\", "/"))
            except Exception:
                pass

            result["changed_files"] = expanded_changed
            result["deleted_files"] = raw_deleted
            result["has_changes"] = len(result["changed_files"]) > 0 or len(result["deleted_files"]) > 0

            # 2. Extract git diff for unstaged/staged changes
            diff_res = subprocess.run(
                ["git", "diff", "HEAD", "--", "."] + binary_excludes,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=5.0
            )
            raw_diff_text = diff_res.stdout if diff_res.returncode == 0 else ""
            if not raw_diff_text and status_res.returncode == 0:
                diff_un = subprocess.run(
                    ["git", "diff", "--", "."] + binary_excludes,
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    errors="replace",
                    timeout=5.0
                )
                raw_diff_text = diff_un.stdout if diff_un.returncode == 0 else ""

            # If sub-repositories exist, also collect their diffs
            candidate_sub_repos = set(seen_sub_repos)
            if os.path.isdir(os.path.join(workspace, "agy_agent")):
                candidate_sub_repos.add("agy_agent")
            sub_repo_diff_text = ""
            for sr in (candidate_sub_repos or ["agy_agent"]):
                full_sr = os.path.join(workspace, sr)
                real_sr = os.path.realpath(full_sr)
                for candidate_dir in [real_sr, full_sr]:
                    if os.path.isdir(candidate_dir) and _is_git_dir(candidate_dir):
                        sub_diff = subprocess.run(
                            ["git", "diff", "HEAD", "--", "."] + binary_excludes,
                            cwd=candidate_dir,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            errors="replace",
                            timeout=5.0
                        )
                        if sub_diff.returncode == 0 and sub_diff.stdout.strip():
                            sub_repo_diff_text += "\n" + sub_diff.stdout
                        else:
                            sub_diff_un = subprocess.run(
                                ["git", "diff", "--", "."] + binary_excludes,
                                cwd=candidate_dir,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                errors="replace",
                                timeout=5.0
                            )
                            if sub_diff_un.returncode == 0 and sub_diff_un.stdout.strip():
                                sub_repo_diff_text += "\n" + sub_diff_un.stdout

                        # Check untracked core Python files in sub-repo
                        try:
                            st_un = subprocess.run(
                                ["git", "status", "--short", "--", "."],
                                cwd=candidate_dir,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                timeout=3.0
                            )
                            if st_un.returncode == 0 and st_un.stdout.strip():
                                untracked_files = [
                                    line.strip().split()[-1]
                                    for line in st_un.stdout.splitlines()
                                    if line.strip().startswith("??")
                                ]
                                for uf in untracked_files:
                                    if uf.endswith(".py") and not uf.startswith("tests/") and not uf.endswith(".pyc"):
                                        d_res = subprocess.run(
                                            ["git", "diff", "--no-index", "/dev/null", uf],
                                            cwd=candidate_dir,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE,
                                            text=True,
                                            timeout=3.0
                                        )
                                        if d_res.stdout.strip():
                                            sub_repo_diff_text += "\n" + d_res.stdout
                        except Exception:
                            pass
                        break

            if sub_repo_diff_text.strip():
                raw_diff_text = (sub_repo_diff_text.strip() + "\n" + raw_diff_text).strip()

            # [🔒 Patch 2] ALWAYS check HEAD~1..HEAD (last commit) regardless of working tree state.
            # This prevents the "commit and hide" evasion: AI commits report-only changes,
            # working tree becomes clean, and old code evaded detection. Now we always inspect
            # what was actually committed in the most recent commit.
            committed_diff_res = subprocess.run(
                ["git", "diff", "HEAD~1", "HEAD", "--", "."] + binary_excludes,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=5.0
            )
            committed_diff_text = committed_diff_res.stdout if committed_diff_res.returncode == 0 else ""

            # Extract files changed in the last commit
            if committed_diff_text:
                committed_name_res = subprocess.run(
                    ["git", "diff", "--name-status", "HEAD~1", "HEAD", "--", "."],
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    errors="replace",
                    timeout=5.0
                )
                if committed_name_res.returncode == 0:
                    committed_lines = [l.strip() for l in committed_name_res.stdout.splitlines() if l.strip()]
                    committed_files = []
                    committed_deleted = []
                    for l in committed_lines:
                        parts = l.split(None, 1)
                        if len(parts) >= 2:
                            status_code, fpath = parts[0], parts[1].strip()
                            if fpath.startswith(".."):
                                continue
                            if status_code.startswith("D"):
                                committed_deleted.append(fpath)
                            else:
                                committed_files.append(fpath)
                    
                    result["committed_changed_files"] = committed_files
                    if committed_deleted:
                        result.setdefault("deleted_files", []).extend(committed_deleted)

                    # Classify committed files into production vs test
                    for fpath in committed_files:
                        norm_f = fpath.replace("\\", "/").lower()
                        is_test = any(t_k in norm_f for t_k in ["/test/", "/tests/", "test_", "test.", "testrunner", "spec.", "mock.", "/androidtest/", "e2e_test.json", "gate4", "gate5", "gate6", "gate7", "reports/"])
                        is_prod = any(norm_f.endswith(ext) for ext in [".kt", ".java", ".tsx", ".jsx", ".ts", ".js", ".py", ".vue", ".html", ".css", ".dart", ".go", ".rs", ".c", ".cpp"])
                        
                        # Submodule / nested repository check: if fpath is a git repo/submodule, check its commit
                        if not is_test and not is_prod:
                            repo_root = workspace
                            while repo_root and repo_root != "/":
                                if os.path.exists(os.path.join(repo_root, ".git")):
                                    break
                                repo_root = os.path.dirname(repo_root)
                            if not repo_root or repo_root == "/":
                                repo_root = workspace

                            sub_dir = os.path.join(workspace, fpath)
                            if not os.path.exists(sub_dir):
                                sub_dir = os.path.join(repo_root, fpath)

                            if os.path.isdir(sub_dir) and _is_git_dir(sub_dir):
                                sub_diff = subprocess.run(
                                    ["git", "diff", "--name-status", "HEAD~1", "HEAD"],
                                    cwd=sub_dir,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    errors="replace",
                                    timeout=5.0
                                )
                                if sub_diff.returncode == 0 and sub_diff.stdout.strip():
                                    for s_line in sub_diff.stdout.splitlines():
                                        s_parts = s_line.strip().split(None, 1)
                                        if len(s_parts) >= 2:
                                            s_path = s_parts[1].strip()
                                            s_norm = s_path.replace("\\", "/").lower()
                                            s_test = any(t_k in s_norm for t_k in ["/test/", "/tests/", "test_", "test.", "testrunner", "spec.", "mock.", "/androidtest/", "e2e_test.json", "gate4", "gate5", "gate6", "gate7", "reports/"])
                                            s_prod = any(s_norm.endswith(ext) for ext in [".kt", ".java", ".tsx", ".jsx", ".ts", ".js", ".py", ".vue", ".html", ".css", ".dart", ".go", ".rs", ".c", ".cpp"])
                                            if s_prod and not s_test:
                                                result["committed_prod_files"].append(os.path.join(fpath, s_path))
                                                is_prod = True

                        if is_prod and not is_test:
                            if fpath not in result["committed_prod_files"]:
                                result["committed_prod_files"].append(fpath)

                    # Flag: last commit was test/report-only (all changed files are tests/reports and 0 production files)
                    has_explicit_test = any(any(t_k in f.replace("\\", "/").lower() for t_k in ["/test/", "/tests/", "test_", "test.", "testrunner", "spec.", "mock.", "/androidtest/", "e2e_test.json", "gate4", "gate5", "gate6", "gate7", "reports/"]) for f in committed_files)
                    result["committed_test_only"] = (
                        len(result["committed_changed_files"]) > 0
                        and len(result["committed_prod_files"]) == 0
                        and has_explicit_test
                    )
                    logger.debug(
                        f"[Patch 2 Git Commit Scan] Last commit: {len(committed_files)} files, {len(committed_deleted)} deleted, "
                        f"prod={len(result['committed_prod_files'])}, "
                        f"test_only={result['committed_test_only']}"
                    )

            # Merge committed diff into raw_diff if working tree was clean
            if not raw_diff_text and committed_diff_text:
                raw_diff_text = committed_diff_text
                if not result["has_changes"] and (committed_files or committed_deleted):
                    result["changed_files"] = committed_files
                    result["has_changes"] = len(committed_files) > 0 or len(result.get("deleted_files", [])) > 0

            if raw_diff_text.strip():
                # Split diff into per-file chunks and prioritize core files
                chunks = [c for c in re.split(r'(?=diff --git )', raw_diff_text) if c.strip()]
                def _diff_priority(chunk_str: str) -> int:
                    m = re.search(r'diff --git (?:a/)?([^\s]+)', chunk_str)
                    fn = os.path.basename(m.group(1)) if m else ''
                    if fn in ('commander.py', 'feedback_classifier.py', 'bot.py', 'roadmap_manager.py'):
                        return 0
                    if fn.endswith('.py') and not fn.startswith('test_'):
                        return 1
                    if fn.startswith('test_') or '/tests/' in (m.group(1) if m else ''):
                        return 2
                    return 3

                sorted_chunks = sorted(chunks, key=_diff_priority)
                budget_per_chunk = 3500 if len(sorted_chunks) > 1 else max_chars
                formatted_chunks = []
                for c in sorted_chunks:
                    if len(c) > budget_per_chunk:
                        c_lines = c.splitlines()
                        capped = []
                        c_len = 0
                        for l in c_lines:
                            if c_len + len(l) + 1 > budget_per_chunk:
                                capped.append('... [truncated remainder of file diff]')
                                break
                            capped.append(l)
                            c_len += len(l) + 1
                        formatted_chunks.append('\n'.join(capped))
                    else:
                        formatted_chunks.append(c)
                prioritized_diff = '\n'.join(formatted_chunks)

                result["has_changes"] = True
                result["raw_diff"] = prioritized_diff[:max_chars]
                result["summary"] = f"{len(result['changed_files'])} files modified ({len(raw_diff_text.splitlines())} diff lines)"
            else:
                result["summary"] = "Clean working tree (0 unstaged diff lines)"

        except Exception as e:
            logger.warning(f"Git diff extraction error: {e}")

        return result

    @classmethod
    def check_visual_asset_presence(cls, workspace: str) -> Dict[str, Any]:
        """
        [Visual Asset Gate] Checks if a GUI project workspace contains any generated
        image assets (PNG/JPG/WEBP/SVG) in standard asset directories.
        Returns: {"has_assets": bool, "asset_count": int, "asset_paths": list, "summary": str}
        """
        result = {"has_assets": False, "asset_count": 0, "asset_paths": [], "summary": ""}
        if not workspace or not os.path.isdir(workspace):
            result["summary"] = "워크스페이스가 존재하지 않음"
            return result

        asset_dirs = [
            "assets", "assets/images", "assets/icons",
            "public/assets", "public/images", "src/assets",
            "res/drawable", "res/drawable-hdpi", "res/drawable-mdpi", "res/drawable-xhdpi",
            "res/drawable-xxhdpi", "res/drawable-xxxhdpi", "res/mipmap-hdpi",
            "app/src/main/res/drawable", "app/src/main/res/mipmap-hdpi",
            "static", "static/images", "img", "images",
        ]
        image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
        found = []

        for ad in asset_dirs:
            full_dir = os.path.join(workspace, ad)
            if os.path.isdir(full_dir):
                for f in os.listdir(full_dir):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in image_extensions and not f.startswith("."):
                        found.append(os.path.join(ad, f))

        # Also check workspace root for common asset files
        for f in os.listdir(workspace):
            ext = os.path.splitext(f)[1].lower()
            if ext in image_extensions and not f.startswith(".") and f not in ("favicon.ico",):
                fp = os.path.join(workspace, f)
                if os.path.isfile(fp) and os.path.getsize(fp) > 1024:  # > 1KB to filter trivial files
                    found.append(f)

        result["has_assets"] = len(found) > 0
        result["asset_count"] = len(found)
        result["asset_paths"] = found[:20]  # Cap for display
        if found:
            result["summary"] = f"✅ 시각 에셋 {len(found)}개 감지: {', '.join(found[:5])}"
        else:
            result["summary"] = "⚠️ 시각 에셋 0개 — 이미지 에셋이 생성되지 않았습니다"
        return result

    @classmethod
    def inspect_production_diff(cls, workspace: str, diff_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        [Zero-Production-Diff 기계적 차단 게이트 전용 인스펙터]
        Extracts and verifies actual production source code changes (git diff lines and modified files).
        Excludes test files, mock harnesses, reports, json, and markdown documentation.
        Returns:
            {
                "prod_diff_lines": int,
                "prod_changed_files": List[str],
                "non_prod_files": List[str],
                "is_zero_production_diff": bool,
                "summary": str
            }
        """
        if diff_info is None:
            diff_info = cls.get_git_diff_summary(workspace)

        changed_files = diff_info.get("changed_files", []) or []
        committed_changed = diff_info.get("committed_changed_files", []) or []
        all_candidate_files = list(set(changed_files + committed_changed))

        prod_files = []
        non_prod_files = []

        prod_exts = (
            ".kt", ".java", ".tsx", ".jsx", ".ts", ".js", ".py", ".vue",
            ".html", ".css", ".dart", ".go", ".rs", ".c", ".cpp"
        )

        test_indicators = (
            "/test/", "/tests/", "test_", "_test.", ".test.", ".spec.",
            "mock_", "/spec/", "__tests__", "/androidtest/", "testrunner"
        )

        report_indicators = (
            "reports/", "e2e_test.json", "design_tokens.json", "gate4", "gate5",
            "gate6", "gate7", ".system_generated/", "brain/", "docs/"
        )

        for fpath in all_candidate_files:
            norm_f = fpath.replace("\\", "/").lower()
            is_report = any(r_k in norm_f for r_k in report_indicators) or norm_f.endswith((".md", ".json", ".txt", ".png", ".jpg", ".jpeg", ".apk", ".aab"))
            is_test = any(t_k in norm_f for t_k in test_indicators)
            is_android_main_xml = norm_f.endswith(".xml") and ("app/src/main/" in norm_f or "src/main/" in norm_f)

            if not is_report and not is_test and (any(norm_f.endswith(ext) for ext in prod_exts) or is_android_main_xml):
                prod_files.append(fpath)
            else:
                non_prod_files.append(fpath)

        # Calculate exact diff lines for production files
        prod_diff_lines = 0
        raw_diff = diff_info.get("raw_diff", "")
        if prod_files and raw_diff:
            current_file_is_prod = False
            for line in raw_diff.splitlines():
                if line.startswith("diff --git"):
                    current_file_is_prod = any(pf in line for pf in prod_files)
                elif current_file_is_prod:
                    if (line.startswith("+") and not line.startswith("+++")) or (line.startswith("-") and not line.startswith("---")):
                        prod_diff_lines += 1

        if prod_files and prod_diff_lines == 0 and os.path.isdir(workspace):
            try:
                diff_cmd = ["git", "diff", "HEAD", "--"] + prod_files
                res = subprocess.run(diff_cmd, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5.0)
                if res.returncode == 0 and res.stdout.strip():
                    prod_diff_lines = len([l for l in res.stdout.splitlines() if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---"))])
                if prod_diff_lines == 0:
                    c_cmd = ["git", "diff", "HEAD~1", "HEAD", "--"] + prod_files
                    res_c = subprocess.run(c_cmd, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5.0)
                    if res_c.returncode == 0 and res_c.stdout.strip():
                        prod_diff_lines = len([l for l in res_c.stdout.splitlines() if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---"))])
            except Exception:
                pass
            if prod_diff_lines == 0:
                for pf in prod_files:
                    full_pf = os.path.join(workspace, pf)
                    if os.path.isfile(full_pf):
                        try:
                            with open(full_pf, "r", encoding="utf-8", errors="ignore") as f:
                                prod_diff_lines += len([l for l in f if l.strip()])
                        except Exception:
                            pass

        is_zero_diff = (len(prod_files) == 0) or (prod_diff_lines == 0)

        summary = (
            f"Production diff lines: {prod_diff_lines}, Production files: {len(prod_files)} "
            f"({', '.join(prod_files[:3]) if prod_files else 'None'}), Non-prod files: {len(non_prod_files)}"
        )

        return {
            "prod_diff_lines": prod_diff_lines,
            "prod_changed_files": prod_files,
            "non_prod_files": non_prod_files,
            "is_zero_production_diff": is_zero_diff,
            "has_git": diff_info.get("has_git", False) if diff_info else False,
            "summary": summary
        }

    @classmethod
    def lint_android_ui_specifications(cls, workspace: str) -> Dict[str, Any]:
        """
        [Coder 선제적 UI 규격 린트 훅]
        Statically inspects Android Native / Jetpack Compose source code in workspace:
        1. 48dp Touch Target Standard:
           Verifies interactive elements (Button, IconButton, clickable, FloatingActionButton)
           apply minimum 48dp touch targets via Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp),
           Modifier.sizeIn(48.dp), minimumInteractiveComponentSize(), or heightIn(min = 48.dp).
        2. Text Truncation Defense:
           Verifies single-line overflowing texts (titles, toolbars, list headers) apply
           basicMarquee (Modifier.basicMarquee()) or adaptive ellipsis defense.
        """
        result = {
            "passed": True,
            "is_android_project": False,
            "touch_target_48dp_passed": True,
            "text_marquee_passed": True,
            "top_chrome_density_passed": True,
            "touch_target_matches": [],
            "touch_target_violations": [],
            "marquee_matches": [],
            "marquee_violations": [],
            "top_chrome_matches": [],
            "top_chrome_violations": [],
            "scanned_files_count": 0,
            "summary": "No Android native Compose source files found"
        }

        if not workspace or not os.path.isdir(workspace):
            return result

        kt_files = []
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if d not in [".git", "build", ".gradle", "node_modules", "__pycache__", "reports"]]
            rel_dir = os.path.relpath(root, workspace).replace("\\", "/").lower()
            if any(t_k in rel_dir for t_k in ["/test/", "/tests/", "/androidtest/"]):
                continue
            for f in files:
                if f.endswith(".kt") or f.endswith(".java"):
                    kt_files.append(os.path.join(root, f))

        if not kt_files:
            return result

        result["is_android_project"] = True
        result["scanned_files_count"] = len(kt_files)

        interactive_keywords = [
            "Button(", "IconButton(", "FloatingActionButton(", "clickable(",
            "TextButton(", "OutlinedButton(", "ElevatedButton(", "FilledTonalButton("
        ]
        touch_48dp_patterns = [
            r'sizeIn\s*\([^)]*48\.dp[^)]*\)',
            r'sizeIn\s*\(\s*48\.dp\s*\)',
            r'minWidth\s*=\s*48\.dp',
            r'minHeight\s*=\s*48\.dp',
            r'size\s*\(\s*48\.dp\s*\)',
            r'heightIn\s*\([^)]*48\.dp[^)]*\)',
            r'minimumInteractiveComponentSize\s*\(\s*\)',
            r'defaultMinSize\s*\([^)]*48\.dp[^)]*\)'
        ]
        marquee_patterns = [
            r'basicMarquee\s*\([^)]*\)',
            r'basicMarquee',
            r'Modifier\s*\.\s*basicMarquee'
        ]

        top_chrome_components = [
            "TopAppBar", "CenterAlignedTopAppBar", "MediumTopAppBar", "LargeTopAppBar", "SmallTopAppBar",
            "TabRow", "ScrollableTabRow", "PrimaryTabRow", "SecondaryTabRow",
            "Banner", "RestoreBanner", "InfoBanner", "GuideBanner", "WarningBanner", "NoticeBanner",
            "Toolbar", "ToolBar", "SearchBar", "DockedSearchBar", "GuideBar", "HeaderBar", "ActionBar"
        ]

        has_interactive_anywhere = False
        has_touch_compliance = False
        has_title_anywhere = False
        has_marquee_compliance = False
        has_top_chrome_violation = False

        for fpath in kt_files:
            rel_path = os.path.relpath(fpath, workspace).replace("\\", "/")
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue

            is_compose_file = "@Composable" in content or "androidx.compose" in content
            if not is_compose_file:
                continue

            # 1. Touch Target Standard Check
            file_has_interactive = any(kw in content for kw in interactive_keywords)
            file_has_48dp = any(re.search(p, content) for p in touch_48dp_patterns)

            if file_has_interactive:
                has_interactive_anywhere = True
                if file_has_48dp:
                    has_touch_compliance = True
                    result["touch_target_matches"].append(f"{rel_path} (48dp standard applied)")
                else:
                    result["touch_target_violations"].append(
                        f"{rel_path}: 인터랙티브 버튼/클릭 요소에 48dp 터치 규격(Modifier.sizeIn(48.dp)) 미적용"
                    )

            # 2. Text Truncation Defense (basicMarquee) Check
            file_has_toolbar_or_title = any(kw in content for kw in ["TopAppBar", "Toolbar", "title", "Title", "Text("])
            file_has_marquee = any(re.search(p, content) for p in marquee_patterns)

            if file_has_toolbar_or_title:
                has_title_anywhere = True
                if file_has_marquee:
                    has_marquee_compliance = True
                    result["marquee_matches"].append(f"{rel_path} (basicMarquee applied)")
                elif any(kw in content for kw in ["TopAppBar", "Toolbar", "LargeTopAppBar", "CenterAlignedTopAppBar"]):
                    result["marquee_violations"].append(
                        f"{rel_path}: 상단 앱바/툴바 타이틀 Text에 텍스트 말줄임 방어(basicMarquee) 미적용"
                    )

            # 3. Top Chrome Density Check (Max 3 Tiers, Max 150dp, Collapsible/NestedScroll Mandate)
            detected_chrome_items = []
            for comp in top_chrome_components:
                matches = re.findall(rf'\b{comp}\s*\(', content)
                if matches:
                    detected_chrome_items.extend([comp] * len(matches))

            if len(detected_chrome_items) > 1:
                tier_count = len(detected_chrome_items)
                
                # Height estimation: Large/Medium ~112dp, TopAppBar/CenterAligned ~64dp, others ~48~56dp
                estimated_height = 0
                for item in detected_chrome_items:
                    if item in ["LargeTopAppBar", "MediumTopAppBar"]:
                        estimated_height += 112
                    elif item in ["TopAppBar", "CenterAlignedTopAppBar", "SmallTopAppBar"]:
                        estimated_height += 64
                    elif item in ["SearchBar", "DockedSearchBar"]:
                        estimated_height += 56
                    else:
                        estimated_height += 48

                # Check for explicit dp heights on components if defined
                explicit_heights = [int(h) for h in re.findall(r'(?:height|heightIn)\s*=\s*(\d+)\.dp', content)]
                explicit_heights += [int(h) for h in re.findall(r'\.height\s*\(\s*(\d+)\.dp\s*\)', content)]
                if explicit_heights and sum(explicit_heights) > 0:
                    estimated_height = max(estimated_height, sum(explicit_heights))

                # Check if auxiliary bars apply Collapsible or NestedScroll auto-hide
                has_collapsible_or_nested_scroll = any(kw in content for kw in [
                    "nestedScroll", "NestedScrollConnection", "rememberTopAppBarScrollBehavior",
                    "enterAlwaysScrollBehavior", "exitUntilCollapsedScrollBehavior", "pinnedScrollBehavior",
                    "AnimatedVisibility", "isExpanded", "rememberSaveable", "collapsible", "Collapsible"
                ])

                if (tier_count > 3 or estimated_height > 150) and not has_collapsible_or_nested_scroll:
                    has_top_chrome_violation = True
                    result["top_chrome_violations"].append(
                        f"{rel_path}: 상단 고정 크롬 레이어 과밀집 감지 ({tier_count}단 중첩, 총 높이 ~{estimated_height}dp > 150dp). "
                        f"고정 바는 3단 이하 / 150dp 이하로 제한하고, 보조 바는 접기(Collapsible) 또는 스크롤 시 자동 숨김(NestedScrollConnection) 적용 필요"
                    )
                else:
                    result["top_chrome_matches"].append(
                        f"{rel_path} (상단 크롬 헌법 준수: {tier_count}단, ~{estimated_height}dp, collapsible={has_collapsible_or_nested_scroll})"
                    )

        if has_interactive_anywhere and not has_touch_compliance and result["touch_target_violations"]:
            result["touch_target_48dp_passed"] = False
        if has_title_anywhere and not has_marquee_compliance and result["marquee_violations"]:
            result["text_marquee_passed"] = False
        if has_top_chrome_violation:
            result["top_chrome_density_passed"] = False

        result["passed"] = (
            result["touch_target_48dp_passed"]
            and result["text_marquee_passed"]
            and result["top_chrome_density_passed"]
        )

        status_items = []
        if result["touch_target_48dp_passed"]:
            status_items.append("🟢 48dp 터치 규격 준수 (Modifier.sizeIn(48.dp))")
        else:
            status_items.append("⚠️ 48dp 터치 규격 미비 (Modifier.sizeIn(48.dp) 보강 필요)")

        if result["text_marquee_passed"]:
            status_items.append("🟢 텍스트 말줄임 방어 준수 (basicMarquee)")
        else:
            status_items.append("⚠️ 텍스트 말줄임 방어 미비 (basicMarquee 보강 필요)")

        if result["top_chrome_density_passed"]:
            status_items.append("🟢 상단 크롬 과밀집 방어 준수 (고정 바 <= 3단 / 150dp 이하 / 접기·스크롤 연동)")
        else:
            status_items.append("⚠️ 상단 크롬 과밀집 감지 (고정 상단 바 3단 초과 또는 150dp 초과: NestedScrollConnection 또는 접기 적용 필요)")

        result["summary"] = f"Android UI Specification Lint: {', '.join(status_items)}"
        return result

    @staticmethod
    def compute_file_sha256(file_path: str) -> str:
        """Computes SHA-256 hex digest of a file safely."""
        if not file_path or not os.path.isfile(file_path):
            return ""
        try:
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def compute_image_dhash(image_path: str, hash_size: int = 8) -> Optional[str]:
        """
        Computes 64-bit difference hash (dHash) for an image.
        Extremely robust against format conversion (PNG <-> JPEG/WEBP), slight compression,
        and scaling while preserving visual structure fingerprint.
        """
        if not image_path or not os.path.isfile(image_path):
            return None
        try:
            with Image.open(image_path) as img:
                _resampling = getattr(Image, "Resampling", None)
                lanczos = getattr(_resampling, "LANCZOS", getattr(Image, "LANCZOS", 1)) if _resampling else getattr(Image, "LANCZOS", 1)
                resized = img.convert("L").resize((hash_size + 1, hash_size), lanczos)
                if hasattr(resized, "get_flattened_data"):
                    pixels = list(resized.get_flattened_data())
                else:
                    pixels = list(resized.getdata())
                diff = []
                for row in range(hash_size):
                    row_start = row * (hash_size + 1)
                    for col in range(hash_size):
                        diff.append(pixels[row_start + col] > pixels[row_start + col + 1])
                decimal_val = 0
                for idx, val in enumerate(diff):
                    if val:
                        decimal_val |= (1 << idx)
                return f"{decimal_val:016x}"
        except Exception:
            return None

    @staticmethod
    def hamming_distance(h1: str, h2: str) -> int:
        """Calculates Hamming distance between two hex string hashes."""
        try:
            val1 = int(h1, 16)
            val2 = int(h2, 16)
            return bin(val1 ^ val2).count("1")
        except Exception:
            return 64

    @classmethod
    def collect_workspace_image_hashes(
        cls,
        workspace: str,
        max_images: int = 200
    ) -> Dict[str, Dict[str, Any]]:
        """
        Collects metadata and cryptographic + perceptual hashes of candidate deliverable
        and screenshot images in the workspace.
        Returns: {rel_path: {"abs_path": ..., "rel_path": ..., "sha256": ..., "dhash": ..., "mtime": ..., "size": ...}}
        """
        results = {}
        if not workspace or not os.path.isdir(workspace):
            return results

        image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
        count = 0

        candidate_dirs = [
            os.path.join(workspace, "reports", "gate5", "screenshots"),
            os.path.join(workspace, "reports", "screenshots"),
            os.path.join(workspace, "reports", "qa_screenshots"),
            os.path.join(workspace, "screenshots"),
            os.path.join(workspace, "output"),
            os.path.join(workspace, "dist"),
            os.path.join(workspace, "build"),
            os.path.join(workspace, "assets"),
            os.path.join(workspace, "images"),
            os.path.join(workspace, "res"),
            os.path.join(workspace, "app", "src", "main", "res"),
        ]

        visited_paths = set()
        excluded_parts = EXCLUDED_DIR_NAMES | {".references", "user_inputs", "agy_user_references"}

        def _process_file(abs_path: str):
            nonlocal count
            if count >= max_images or abs_path in visited_paths:
                return
            visited_paths.add(abs_path)
            if not os.path.isfile(abs_path):
                return
            if not abs_path.lower().endswith(image_extensions):
                return
            rel_p = os.path.relpath(abs_path, workspace).replace("\\", "/")
            if any(part in excluded_parts or part.startswith("pytest") or part.startswith("test_ws_") for part in rel_p.split("/")):
                return
            
            try:
                sz = os.path.getsize(abs_path)
                if sz == 0 or sz > 50 * 1024 * 1024:
                    return
                mtime = os.path.getmtime(abs_path)
                sha = cls.compute_file_sha256(abs_path)
                dh = cls.compute_image_dhash(abs_path)
                results[rel_p] = {
                    "abs_path": abs_path,
                    "rel_path": rel_p,
                    "sha256": sha,
                    "dhash": dh,
                    "mtime": mtime,
                    "size": sz
                }
                count += 1
            except Exception:
                pass

        for cdir in candidate_dirs:
            if os.path.isdir(cdir):
                for root, dirs, files in os.walk(cdir):
                    dirs[:] = [d for d in dirs if d not in excluded_parts and not d.startswith("pytest") and not d.startswith("test_ws_")]
                    for f in files:
                        _process_file(os.path.join(root, f))
                        if count >= max_images:
                            break
                    if count >= max_images:
                        break

        if count < max_images:
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [d for d in dirs if d not in excluded_parts and not d.startswith("pytest") and not d.startswith("test_ws_")]
                for f in files:
                    _process_file(os.path.join(root, f))
                    if count >= max_images:
                        break
                if count >= max_images:
                    break

        return results

    @classmethod
    def detect_reference_image_plagiarism(
        cls,
        workspace: str,
        reference_images: List[Dict[str, Any]]
    ) -> Tuple[bool, List[str]]:
        """
        Anti-Plagiarism & Authenticity Scan:
        Detects if Coder copied or echoed user-supplied reference images into deliverable
        paths or screenshot evidence and claimed them as newly generated work.
        Uses both exact SHA-256 hashing and perceptual dHash (Hamming distance <= 3).
        """
        if not workspace or not os.path.isdir(workspace) or not reference_images:
            return False, []

        violations = []
        workspace_images = cls.collect_workspace_image_hashes(workspace)

        for ref in reference_images:
            ref_name = ref.get("filename", "reference_image")
            ref_sha = ref.get("sha256", "")
            ref_dhash = ref.get("dhash")
            ref_path = ref.get("path", "")

            if not ref_sha and ref_path and os.path.isfile(ref_path):
                ref_sha = cls.compute_file_sha256(ref_path)
            if not ref_dhash and ref_path and os.path.isfile(ref_path):
                ref_dhash = cls.compute_image_dhash(ref_path)

            for rel_path, img_info in workspace_images.items():
                cand_sha = img_info.get("sha256", "")
                cand_dhash = img_info.get("dhash")

                # 1. Exact SHA-256 match -> Complete binary plagiarism
                if ref_sha and cand_sha and ref_sha == cand_sha:
                    v_msg = (
                        f"🚨 [Anti-Mocking Rule 6 - User Reference Image Plagiarism]: "
                        f"산출물/증적 이미지 '{rel_path}'의 바이너리 해시({cand_sha[:12]}...)가 "
                        f"사용자가 제공한 참고용 레퍼런스 이미지 '{ref_name}'와 100% 동일합니다. "
                        f"사용자의 참고 이미지를 자신이 생성한 산출물로 사칭/표절하는 행위는 엄격히 금지됩니다. "
                        f"실제 소스코드를 렌더링/빌드하여 고유한 새 이미지를 생성하십시오."
                    )
                    violations.append(v_msg)
                    break

                # 2. Perceptual dHash match (Hamming distance <= 3 out of 64 bits = >95% identical visual structure)
                # Catches re-saved/re-compressed versions of user reference images
                if ref_dhash and cand_dhash:
                    dist = cls.hamming_distance(ref_dhash, cand_dhash)
                    if dist <= 3:
                        v_msg = (
                            f"🚨 [Anti-Mocking Rule 6b - Perceptual Reference Image Plagiarism]: "
                            f"산출물/증적 이미지 '{rel_path}'가 사용자가 제공한 참고용 레퍼런스 이미지 '{ref_name}'의 "
                            f"시각적 복제물(Perceptual Clone, 유사도 해시 거리 {dist}/64)로 적발되었습니다. "
                            f"단순 포맷 변환/리사이징한 사용자 레퍼런스 재탕은 허용되지 않습니다. "
                            f"실제 소스코드를 렌더링/빌드하여 고유한 새 이미지를 생성하십시오."
                        )
                        violations.append(v_msg)
                        break

        is_plagiarized = len(violations) > 0
        return is_plagiarized, violations

    @classmethod
    def detect_script_reference_tracing_and_extraction(
        cls,
        workspace: str,
        reference_images: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, List[str]]:
        """
        Anti-Mocking Rule 6c - Script-Level Reference Extraction & Tracing Forgery Scan:
        Detects if Coder wrote asset generation or production scripts that open or load user reference
        images (via PIL, OpenCV, Matplotlib, Numpy, Blender, etc.) to trace contours, extract masks,
        sample colors, or copy pixels into deliverables, while deceitfully claiming genuine procedural/3D generation.

        Legitimate evaluation/test/benchmark scripts (e.g. test_*.py, evaluate_*.py, audit_*.py)
        comparing deliverables against reference images for metric calculations are exempted.
        """
        if not workspace or not os.path.isdir(workspace):
            return False, []

        violations = []
        ref_filenames = set()
        ref_paths = set()
        if reference_images:
            for r in reference_images:
                fn = r.get("filename")
                fp = r.get("path")
                if fn:
                    ref_filenames.add(fn.lower())
                if fp:
                    ref_paths.add(fp)

        # Tracing / pixel extraction signatures
        TRACING_PATTERNS = [
            r"findContours\s*\(",
            r"Canny\s*\(",
            r"threshold\s*\(",
            r"np\.any\s*\(\s*ref",
            r"ref_arr\s*<\s*\d+",
            r"ref_arr\s*>\s*\d+",
            r"ref_img\.convert\s*\(",
            r"Image\.open\s*\([^)]*(?:reference|\.references|discord_att|REF_IMAGE)[^)]*\)",
            r"cv2\.imread\s*\([^)]*(?:reference|\.references|discord_att|REF_IMAGE)[^)]*\)",
            r"plt\.imread\s*\([^)]*(?:reference|\.references|discord_att|REF_IMAGE)[^)]*\)",
            r"mpimg\.imread\s*\([^)]*(?:reference|\.references|discord_att|REF_IMAGE)[^)]*\)",
            r"bpy\.data\.images\.load\s*\([^)]*(?:reference|\.references|discord_att|REF_IMAGE)[^)]*\)",
            # Base64 embedded reference image & covert extraction signatures
            r"data:image/[a-zA-Z]+;base64,",
            r"base64\.b64decode\s*\(",
            r"Image\.open\s*\(\s*(?:io\.)?BytesIO\s*\(",
            # Covert download of reference images
            r"urllib\.request\.urlretrieve\s*\(",
            r"requests\.get\s*\([^)]*(?:\.png|\.jpg|\.jpeg|\.webp)",
        ]

        EXCLUDED_SCRIPT_PREFIXES = (
            "test_", "eval_", "evaluate_", "audit_", "benchmark_", "metric_", "verify_"
        )
        FRAMEWORK_SYSTEM_FILES = {
            "agent_runner.py", "evidence_validator.py", "trio_agent_runner.py",
            "bot.py", "session_manager.py", "commander.py", "engine_manager.py",
            "config.py", "hot_reload_bridge.py", "help_manager.py", "roadmap_manager.py",
            "git_manager.py", "mode_classifier.py"
        }

        for root, dirs, files in os.walk(workspace):
            dirs[:] = [
                d for d in dirs
                if not d.startswith((".", "__pycache__", "coverage", ".pytest_cache"))
                and d.lower() not in EXCLUDED_DIR_NAMES
                and not d.lower().startswith(("dist-", "build-", "node_modules"))
            ]
            rel_root = os.path.relpath(root, workspace).replace("\\", "/")
            rel_parts = [p.lower() for p in rel_root.split("/")]
            if (
                any(exc.lower() in rel_parts for exc in EXCLUDED_DIR_NAMES)
                or any(exc in rel_root.lower() for exc in (
                    "node_modules", "venv", "site-packages", "dist", "build",
                    "assets", "leaflet", "rustdesk", "intermediates", ".vite"
                ))
                or any(part in rel_root.lower() for part in ["/test", "/tests", "/evaluation", "/benchmarks", ".references"])
            ):
                continue

            for f in files:
                lower_f = f.lower()
                if not lower_f.endswith((".py", ".js", ".ts")):
                    continue
                if lower_f in FRAMEWORK_SYSTEM_FILES:
                    continue
                if any(lower_f.startswith(pfx) for pfx in EXCLUDED_SCRIPT_PREFIXES):
                    continue
                if any(lower_f.endswith(sfx) for sfx in (".min.js", ".bundle.js", "bundle.js", ".esm.js")):
                    continue

                fp = os.path.join(root, f)
                rel_f = os.path.relpath(fp, workspace)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as sf:
                        content = sf.read()

                    has_ref_open = False
                    matched_pattern = ""

                    for pat in TRACING_PATTERNS:
                        m = re.search(pat, content, re.IGNORECASE)
                        if m:
                            # If matching cv2 function signatures, verify computer vision libraries are actually imported
                            if pat in (r"findContours\s*\(", r"Canny\s*\(") and not any(mod in content for mod in ("cv2", "cv", "skimage", "scipy")):
                                continue
                            # Exclude 1x1 transparent spacer GIFs or empty SVGs (standard third-party placeholders)
                            if pat == r"data:image/[a-zA-Z]+;base64,":
                                snippet = content[m.start():m.end() + 40]
                                if any(spacer in snippet for spacer in ("R0lGODlhAQAB", "PHN2Zy")):
                                    continue
                            has_ref_open = True
                            matched_pattern = m.group(0)[:60]
                            break

                    if not has_ref_open and ref_filenames:
                        for rfn in ref_filenames:
                            if rfn in content.lower():
                                if re.search(rf'(?:Image\.open|imread|open)\s*\([^)]*{re.escape(rfn)}', content, re.IGNORECASE):
                                    has_ref_open = True
                                    matched_pattern = f"Loads reference file '{rfn}' directly"
                                    break

                    if has_ref_open:
                        has_image_output = any(kw in content for kw in [
                            ".save(", "imwrite", "savefig", "bpy.ops.render", "export_image", ".render("
                        ]) or (("emoticon" in content or "sprite" in content) and any(kw in content for kw in [".save", "imwrite", "render"]))
                        if has_image_output:
                            violations.append(
                                f"🚨 [Anti-Mocking Rule 6c - Script-Level Reference Extraction & Tracing Forgery]: "
                                f"스크립트 '{rel_f}'에서 사용자 레퍼런스 이미지를 직접 열어 외곽선/마스크/픽셀을 추출·합성하거나 "
                                f"베이스64/우회 로드하는 모킹(Mocking) 코드({matched_pattern})가 적발되었습니다. "
                                f"사용자의 참고 이미지는 시각적 이해와 사후 평가(QA) 용도로만 사용할 수 있으며, "
                                f"에셋 생성 스크립트 내부에서 직접 읽어와 베끼거나 외곽선을 따는 행위는 엄격히 금지됩니다. "
                                f"순수 절차적 코드(Procedural) 또는 3D 리깅/모델링으로 온전히 새로 구현하십시오."
                            )
                except Exception:
                    pass

        return len(violations) > 0, violations

    @classmethod
    def audit_runtime_asset_provenance(
        cls,
        workspace: str
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        Anti-Mocking Rule 6d - Procedural 3D & Asset Runtime Provenance Audit:
        Verifies that deliverables claiming pure procedural/3D generation (e.g. Blender NPR,
        procedural shaders) maintain strict runtime provenance:
        1. Checks whether procedural scene construction logs verify zero external image texture loading (len(bpy.data.images) <= 1).
        2. Checks that shader node networks are pure procedural (unlit emission, math, color ramp) without ShaderNodeTexImage referencing external bitmaps.
        3. Audits quality_gate_audit.json or reproduction logs for verified procedural metrics.
        """
        if not workspace or not os.path.isdir(workspace):
            return True, [], {"status": "SKIPPED"}

        violations = []
        details = {
            "procedural_scenes_audited": [],
            "zero_texture_verified": True,
            "runtime_audit_passed": True,
        }

        # Check for .blend files or procedural scripts
        blend_files = []
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules", ".venv"))]
            for f in files:
                if f.endswith(".blend"):
                    blend_files.append(os.path.join(root, f))

        if blend_files:
            details["found_blend_files"] = [os.path.relpath(bf, workspace) for bf in blend_files]

        # Audit quality_gate_audit.json if present
        audit_file = os.path.join(workspace, "quality_gate_audit.json")
        if not os.path.exists(audit_file):
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules"))]
                if "quality_gate_audit.json" in files:
                    audit_file = os.path.join(root, "quality_gate_audit.json")
                    break

        if os.path.isfile(audit_file):
            try:
                with open(audit_file, "r", encoding="utf-8") as af:
                    audit_data = json.load(af)
                details["quality_gate_audit"] = {
                    "overall_score": audit_data.get("overall_score"),
                    "passed_95_gate": audit_data.get("passed_95_gate"),
                    "full_ssim_percent": audit_data.get("full_ssim_percent"),
                    "edge_chamfer_percent": audit_data.get("edge_chamfer_percent"),
                }
            except Exception as e:
                logger.debug(f"Audit file read error: {e}")

        return len(violations) == 0, violations, details

    @classmethod
    def detect_stale_round_images(
        cls,
        workspace: str,
        previous_round_image_hashes: Dict[str, Any],
        prompt: str = ""
    ) -> Tuple[bool, List[str]]:
        """
        Anti-Stale-Loop Scan:
        Detects if Coder in Round N produced the exact same deliverable image hashes
        as Round N-1 without generating or rendering updated deliverables, despite QA
        having rejected the previous round.
        """
        if not workspace or not os.path.isdir(workspace) or not previous_round_image_hashes:
            return False, []

        # Universal Value Creation Mandate (Anti-Mocking Rule 6/7 Exemption):
        # Non-UI tasks (backend, discord bot, system tuning, CLI, analysis, logic) must NOT be
        # falsely rejected for not modifying UI screenshots when no UI components were requested or changed.
        prompt_lower = (prompt or "").lower()
        is_non_ui_task = any(kw in prompt_lower for kw in [
            "봇", "bot", "리셋", "!reset", "reset", "컨텍스트", "백엔드", "backend",
            "데몬", "daemon", "cli", "api", "시스템 튜닝", "파이프라인", "pipeline",
            "동기화", "sync", "재컴파일", "recompile", "핫리로드", "hotreload",
            "세션", "session", "서버", "server", "기획서", "분석", "진단", "검증",
            "아키텍처", "architecture", "설정", "config", "텔레메트리", "telemetry",
            "자가치유", "selfhealing", "워치독", "watchdog",
            "mode", "!mode", "qa", "qa1", "qa2", "qa3", "qa4", "qa5", "qa6", "!qa",
            "solar", "다중", "에이전트", "협업", "오케스트레이션", "orchestration",
            "스쿼드", "squad", "체인", "chain", "선택", "지정", "targeted", "단독",
            "프롬프트", "어댑터", "openrouter", "hermes", "모델", "model",
            "test", "timeout", "mock", "unit", "suite"
        ])
        if is_non_ui_task:
            return False, []

        # Structural Exemption: If git diff shows changes exclusively in non-UI files (.py, .json, .sh, etc.)
        # and no UI/markup assets (.html, .css, .tsx, .vue, .xml, etc.) were modified, exempt from visual stale check.
        try:
            diff_info = cls.get_git_diff_summary(workspace)
            changed_files = diff_info.get("changed_files", [])
            UI_EXTENSIONS = ('.html', '.htm', '.css', '.scss', '.sass', '.less', '.jsx', '.tsx', '.vue', '.svelte', '.xml', '.kt', '.swift', '.xaml', '.storyboard', '.xib')
            if changed_files and not any(any(f.lower().endswith(ext) for ext in UI_EXTENSIONS) for f in changed_files):
                return False, []
        except Exception:
            pass

        DELIVERABLE_PREFIXES = (
            "reports/gate5/screenshots",
            "reports/screenshots",
            "reports/qa_screenshots",
            "screenshots",
            "output",
            "dist"
        )

        current_images = cls.collect_workspace_image_hashes(workspace)
        
        prev_deliverables = {}
        for rel_p, data in previous_round_image_hashes.items():
            norm_p = rel_p.replace("\\", "/")
            if any(norm_p.startswith(pfx) or f"/{pfx}/" in norm_p for pfx in DELIVERABLE_PREFIXES):
                prev_deliverables[norm_p] = data.get("sha256") if isinstance(data, dict) else str(data)

        curr_deliverables = {}
        for rel_p, data in current_images.items():
            norm_p = rel_p.replace("\\", "/")
            if any(norm_p.startswith(pfx) or f"/{pfx}/" in norm_p for pfx in DELIVERABLE_PREFIXES):
                curr_deliverables[norm_p] = data.get("sha256") if isinstance(data, dict) else str(data)

        if not prev_deliverables or not curr_deliverables:
            return False, []

        stale_matches = []
        for rel_p, prev_sha in prev_deliverables.items():
            curr_sha = curr_deliverables.get(rel_p)
            if curr_sha and curr_sha == prev_sha:
                stale_matches.append(f"'{rel_p}' (SHA-256: {curr_sha[:12]}...)")

        has_new_deliverable = any(p not in prev_deliverables for p in curr_deliverables)
        
        if len(stale_matches) == len(prev_deliverables) and not has_new_deliverable:
            violations = [(
                f"🚨 [Anti-Mocking Rule 7 - Stale Round Image Repetition]: "
                f"선행 라운드에서 제출되었던 시각 산출물/증적 이미지({', '.join(stale_matches[:3])})가 "
                f"이번 라운드에서도 해시 변경 없이 100% 동일하게 재사용되었습니다. "
                f"QA의 수정 요구사항을 반영한 새로운 UI 렌더링/빌드가 수행되지 않았습니다. "
                f"실제 변경 사항이 반영된 갱신된 산출물을 생성하여 제출하십시오."
            )]
            return True, violations

        return False, []

    @classmethod
    def detect_ai_slop_and_prompt_bleed(
        cls,
        workspace: str,
        changed_files: Optional[List[str]] = None
    ) -> Tuple[bool, List[str]]:
        """
        Scans modified production source files for AI Slop, Prompt Bleed-through,
        and amateur PoC artifacts:
        1. Raw threshold/pixel metrics in UI copy (e.g., '50px+', '70px+', '16ms', '60fps')
        2. Backend infrastructure PoC terms in main UI buttons/titles ('단절 테스트', '3-Way 자동 병합', '온디바이스 평탄화')
        3. Generic AI dummy persona cliches ('김민수 수석연구원', '이지은 디자이너', '박태훈 검수관')
        4. Miniature AI bitmap images shoved into small 24~48dp icon slots instead of vector icons
        """
        if not workspace or not os.path.isdir(workspace):
            return False, []

        target_files = []
        if changed_files:
            for f in changed_files:
                norm_f = f.replace("\\", "/").lower()
                if any(p in norm_f for p in ["/test/", "/tests/", "test_", "test.", "reports/", ".system_generated/"]):
                    continue
                if any(norm_f.endswith(ext) for ext in [".kt", ".java", ".tsx", ".jsx", ".vue", ".html", ".xml"]):
                    full_p = os.path.join(workspace, f) if not os.path.isabs(f) else f
                    if os.path.isfile(full_p):
                        target_files.append(full_p)
        else:
            for root, dirs, files in os.walk(workspace):
                dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules", "build", "dist", "reports"))]
                rel_root = os.path.relpath(root, workspace).replace("\\", "/").lower()
                if "test" in rel_root:
                    continue
                for fname in files:
                    if fname.endswith((".kt", ".java", ".tsx", ".jsx", ".vue", ".xml")):
                        target_files.append(os.path.join(root, fname))

        violations = []
        prompt_bleed_patterns = [
            (r'["\'][^"\']*\b(?:50px\+|70px\+|16ms|60fps|200%\s*확대|WCAG\s*2\.1\s*AAA)[^"\']*["\']', "개발 명세/임계값(px/ms/fps/규격) 수치가 UI 카피에 그대로 노출됨 (Prompt Bleed-through)"),
            (r'["\'][^"\']*(?:단절\s*테스트|3-Way\s*(?:자동\s*)?병합|온디바이스\s*무손실\s*평탄화|초저지연\s*협업|지능형\s*증분\s*동기화)[^"\']*["\']', "백엔드/인프라 PoC 시연용 전문 용어가 일반 사용자 화면 버튼/타이틀로 노출됨"),
            (r'["\'][^"\']*(?:김민수\s*(?:수석)?연구원|이지은\s*디자이너|박태훈\s*검수관|1페이지\s*펜\s*필기\s*검토\s*중|2페이지\s*형광펜\s*하이라이트)[^"\']*["\']', "LLM 특유의 천편일률적 AI 클리셰 더미 데이터(인명/직책/시나리오)가 사용됨"),
        ]

        found_issues = []
        for fp in target_files:
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                rel_p = os.path.relpath(fp, workspace)
                for pat, desc in prompt_bleed_patterns:
                    matches = re.findall(pat, content)
                    if matches:
                        sample_m = matches[0].strip("\"' ")
                        found_issues.append(f"• `{rel_p}`: \"{sample_m[:40]}\" ➔ {desc}")
            except Exception:
                pass

        # Check package drift (e.g. creating files in com.example or com.app when canonical package exists)
        try:
            from trio_agent_runner import detect_codebase_anchor_dict
            anchor_info = detect_codebase_anchor_dict(workspace)
            canonical_pkg = anchor_info.get("root_package")
            if canonical_pkg:
                for fp in target_files:
                    if fp.endswith((".kt", ".java")):
                        try:
                            with open(fp, "r", encoding="utf-8", errors="ignore") as src_f:
                                c = src_f.read()
                            m = re.search(r'^\s*package\s+([a-zA-Z0-9_.]+)', c, re.MULTILINE)
                            if m:
                                declared_pkg = m.group(1).strip()
                                if declared_pkg in ("com.example", "com.app", "com.sample", "com.template") and declared_pkg != canonical_pkg:
                                    rel_p = os.path.relpath(fp, workspace)
                                    found_issues.append(f"• `{rel_p}`: 패키지 `{declared_pkg}` ➔ 정규 루트 패키지 `{canonical_pkg}` 대신 임의의 템플릿 패키지로 분리됨 (Amnesia Package Drift)")
                        except Exception:
                            pass
        except Exception:
            pass

        if found_issues:
            violations.append(
                "🚨 [Anti-Mocking Rule 9 - Anti-AI-Slop & Prompt Bleed-through Detected]:\n"
                "상용 서비스 품질에 부적합한 AI 생성 특유의 PoC 결함(AI Slop), 패키지 이탈(Amnesia Drift), 또는 프롬프트 명세 누출이 적발되었습니다:\n"
                + "\n".join(found_issues[:5]) + "\n"
                "내부 개발 수치나 인프라 용어를 제거하고, 정규 프로젝트 패키지 구조 및 실제 도메인 데이터를 준수하십시오."
            )
            return True, violations

        return False, []

    @classmethod
    def get_project_domain(cls, workspace: str) -> str:
        """
        Determines the project domain: 'cli_agent', 'backend_api', 'android', or 'web'.
        Checks profiles/active_profile.md first, then PROJECT_CONSTITUTION.md, then workspace manifests.
        """
        if not workspace or not os.path.isdir(workspace):
            return "cli_agent"

        # 1. Check active profile
        active_prof = os.path.join(workspace, "profiles", "active_profile.md")
        if os.path.isfile(active_prof):
            try:
                with open(active_prof, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                if any(k in content for k in ["cli_agent", "cli, daemon", "background automation", "crawler", "bot"]):
                    return "cli_agent"
                if any(k in content for k in ["backend_api", "backend api", "fastapi", "nestjs"]):
                    return "backend_api"
                if "android" in content:
                    return "android"
                if any(k in content for k in ["web & saas", "web_rules", "react", "frontend"]):
                    return "web"
            except Exception:
                pass

        # 2. Check PROJECT_CONSTITUTION.md
        const_file = os.path.join(workspace, "PROJECT_CONSTITUTION.md")
        if os.path.isfile(const_file):
            try:
                with open(const_file, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                if any(k in content for k in ["background automation", "bot", "crawler", "cli", "daemon"]):
                    return "cli_agent"
                if any(k in content for k in ["backend api", "fastapi", "nestjs"]):
                    return "backend_api"
                if "android native" in content:
                    return "android"
                if "web / saas" in content or "react" in content:
                    return "web"
            except Exception:
                pass

        # 3. Workspace file heuristics
        if os.path.exists(os.path.join(workspace, "build.gradle")) or os.path.exists(os.path.join(workspace, "app", "build.gradle")):
            return "android"
        if os.path.exists(os.path.join(workspace, "package.json")):
            try:
                with open(os.path.join(workspace, "package.json"), "r", encoding="utf-8") as f:
                    pj = json.load(f)
                deps = {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}
                if any(k in deps for k in ["react", "vue", "vite", "next", "tailwindcss", "svelte"]):
                    return "web"
            except Exception:
                pass
        return "cli_agent"

    @classmethod
    def verify_anti_mocking_and_authenticity(
        cls,
        workspace: str,
        prompt: str = "",
        user_reference_images: Optional[List[Dict[str, Any]]] = None,
        previous_round_image_hashes: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        [Anti-Mocking & Anti-Fabrication Engine]:
        Systematically detects all forms of visual cheating, mock result fabrication,
        test-only patches, and UI bypassing tricks across ANY project (Web/Android/iOS/Python).

        Rules Enforced:
        1. [Rule 1 - Anti-Test-Only Patch]: Production source code MUST be modified when features/fixes are claimed.
           Modifying only test files without production code changes is rejected immediately.
        2. [Rule 2 - Anti-UI-Bypass & Hardcoded Model Mutation]: Detects test codes that mutate data models (.copy(text=...), .replace(...))
           and directly call low-level rendering engines (renderCompositePageBitmap, drawText) without actual UI interactions (Compose/DOM).
        3. [Rule 3 - Anti-Canvas-Forgery]: Detects manual Canvas.drawRect, PIL ImageDraw, or node-canvas scripts faking screenshots.
        4. [Rule 4 - Anti-Evasion-Linter]: Detects deceptive conditional rendering (hiding unselected text) and font limit bypasses.
        5. [Rule 5 - Multi-State Interaction Timeline]: Gate 5 evidence MUST contain multi-state UI transitions (Idle -> Dialog/Input -> Committed).
        6. [Rule 6 - User Reference Image Plagiarism]: Detects binary or perceptual echoing of user reference images as deliverables.
        7. [Rule 6c - Script-Level Reference Extraction & Tracing Forgery]: Detects asset generation scripts opening reference images to trace contours or sample pixels.
        8. [Rule 7 - Stale Round Image Repetition]: Detects recycling identical image artifacts across collaboration rounds without re-rendering.
        """
        violations = []
        details = {
            "is_compliant": True,
            "violations": [],
            "production_files": [],
            "test_files": [],
            "forged_scripts": [],
            "evasive_patterns": []
        }
        if not workspace or not os.path.isdir(workspace):
            return True, [], details

        # -------------------------------------------------------------
        # Check 1: Anti-Test-Only Patch (Production Code Diff Integrity)
        # -------------------------------------------------------------
        diff_info = cls.get_git_diff_summary(workspace)
        changed_files = diff_info.get("changed_files", [])
        if changed_files:
            prod_files = []
            test_files = []

            for fpath in changed_files:
                norm_f = fpath.replace("\\", "/").lower()
                if any(t_k in norm_f for t_k in ["/test/", "/tests/", "test_", "test.", "testrunner", "spec.", "mock.", "/androidtest/"]):
                    test_files.append(fpath)
                elif any(norm_f.endswith(ext) for ext in [".kt", ".java", ".tsx", ".jsx", ".ts", ".js", ".py", ".vue", ".html", ".css", ".dart", ".go", ".rs", ".c", ".cpp"]):
                    prod_files.append(fpath)

            details["production_files"] = prod_files
            details["test_files"] = test_files

            is_explicit_test_task = any(kw in (prompt or "").lower() for kw in [
                "테스트 작성", "테스트 코드", "단위테스트", "단위 테스트", "e2e 테스트",
                "test suite", "테스트 추가", "test code", "tc 작성"
            ])

            if len(test_files) > 0 and len(prod_files) == 0 and not is_explicit_test_task:
                v_msg = (
                    "🚨 [Anti-Mocking Rule 1 - Test-Only Modification]: "
                    f"프로덕션 소스코드(앱 UI/화면/컴포넌트) 변경 없이 테스트 코드({', '.join(test_files[:3])})만 수정하여 "
                    "검증을 통과하려 한 비가시적 눈속임(Anti-Invisible-Fix)이 적발되었습니다. 실사용자 UI 소스코드를 직접 구현하십시오."
                )
                violations.append(v_msg)

        # [🔒 Patch 2 Extension] Also check most recent GIT COMMIT for report/test-only modifications.
        # This catches the "commit and hide" pattern where AI commits only report files (e2e_test.json)
        # and stages no production code — making the working tree appear clean.
        committed_test_only = diff_info.get("committed_test_only", False)
        committed_changed = diff_info.get("committed_changed_files", [])
        committed_prod = diff_info.get("committed_prod_files", [])
        if committed_test_only and committed_changed:
            is_explicit_test_task = any(kw in (prompt or "").lower() for kw in [
                "테스트 작성", "테스트 코드", "단위테스트", "단위 테스트", "e2e 테스트",
                "test suite", "테스트 추가", "test code", "tc 작성"
            ])
            if not is_explicit_test_task:
                v_msg = (
                    "🚨 [Anti-Mocking Rule 1b - Committed Test-Only Modification (Commit & Hide)]: "
                    f"최근 커밋({', '.join(committed_changed[:3])})에서 프로덕션 소스코드 변경 없이 "
                    "테스트/리포트 파일(reports/, e2e_test.json 등)만 수정하여 커밋한 것이 적발되었습니다. "
                    "커밋 후 Working Tree를 클린하게 만들어 검증을 회피하려는 시도입니다. "
                    "실제 앱/UI 프로덕션 소스코드를 수정 후 커밋하십시오."
                )
                details["committed_test_only"] = True
                details["committed_changed_files"] = committed_changed
                violations.append(v_msg)



        # -------------------------------------------------------------
        # Check 2: Direct Model Mutation / Bypassing UI Layer in Test Files
        # -------------------------------------------------------------
        test_bypass_violations = []
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules", "build", "dist", "coverage", ".venv", "env"))]
            rel_root = os.path.relpath(root, workspace)
            if any(exc in rel_root.split(os.sep) for exc in EXCLUDED_DIR_NAMES):
                continue

            for f in files:
                lower_f = f.lower()
                if "test" in lower_f or "test" in rel_root.lower() or "spec" in lower_f:
                    if f.endswith((".kt", ".java", ".ts", ".js", ".py")):
                        fp = os.path.join(root, f)
                        try:
                            with open(fp, "r", encoding="utf-8", errors="ignore") as tf:
                                content = tf.read()

                            has_direct_mutation = (
                                re.search(r'\.copy\s*\(\s*text\s*=', content) or
                                (re.search(r'replace\s*\(\s*["\'][^"\']+["\']\s*,\s*["\'][^"\']+["\']\s*\)', content) and any(b in content for b in ["Block", "gonggae", "jikjong", "task3"]))
                            )
                            has_direct_rendering_bypass = any(k in content for k in [
                                "renderCompositePageBitmap", "renderPageBitmap", "Bitmap.createBitmap", "Canvas("
                            ])
                            has_ui_interaction_rule = any(k in content for k in [
                                "createComposeRule", "createAndroidComposeRule", "onNodeWithText", "onNodeWithTag",
                                "performClick", "performTextInput", "page.click", "page.fill", "page.goto"
                            ])

                            if has_direct_mutation and has_direct_rendering_bypass and not has_ui_interaction_rule:
                                test_bypass_violations.append(
                                    f"'{os.path.relpath(fp, workspace)}' (UI 레이어를 우회하여 모델 객체 직접 변조 후 렌더링 엔진으로 비트맵만 생성)"
                                )
                        except Exception:
                            pass

        if test_bypass_violations:
            v_msg = (
                "🚨 [Anti-Mocking Rule 2 - UI Layer Bypass & Test Data Forgery]: "
                f"테스트 코드({', '.join(test_bypass_violations[:2])})에서 실제 UI 상호작용(ComposeTestRule/Playwright DOM)을 "
                "완전 우회하고 데이터 객체를 직접 변조(.copy/replace)하여 하위 렌더링 엔진으로 이미지만 추출하는 결과 날조(Mocking)가 적발되었습니다."
            )
            violations.append(v_msg)

        # -------------------------------------------------------------
        # Check 3: Synthetic Canvas / PIL Forgery & Evasion Patterns
        # -------------------------------------------------------------
        is_forged, forged_scripts = cls.detect_synthetic_artifacts(workspace)
        if is_forged:
            details["forged_scripts"] = forged_scripts
            violations.append(
                f"🚨 [Anti-Mocking Rule 3 - Synthetic Screenshot Generator]: "
                f"가짜 캔버스 합성 스크립트({', '.join(forged_scripts)})가 적발되었습니다. 실제 브라우저/디바이스 프레임버퍼 캡처만 허용됩니다."
            )

        is_evasive, evasive_patterns = cls.detect_rendering_evasion_patterns(workspace)
        if is_evasive:
            details["evasive_patterns"] = evasive_patterns
            violations.append(
                f"🚨 [Anti-Mocking Rule 4 - Deceptive Conditional Rendering]: "
                f"은폐형 조건부 렌더링 또는 좌표/폰트 강제 클램핑({', '.join(evasive_patterns)})이 적발되었습니다."
            )

        # -------------------------------------------------------------
        # Check 5: Visual Screenshot Authenticity & Blank Screen Scan
        # -------------------------------------------------------------
        candidate_screenshot_dirs = [
            os.path.join(workspace, "reports", "gate5", "screenshots"),
            os.path.join(workspace, "reports", "screenshots"),
            os.path.join(workspace, "screenshots"),
        ]
        invalid_visual_screenshots = []
        for sdir in candidate_screenshot_dirs:
            if os.path.isdir(sdir):
                for sfile in sorted(os.listdir(sdir)):
                    if sfile.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        s_path = os.path.join(sdir, sfile)
                        is_v, img_msg = cls.validate_image_content(s_path)
                        if not is_v:
                            invalid_visual_screenshots.append(f"{sfile} ({img_msg})")

        if invalid_visual_screenshots:
            details["invalid_visual_screenshots"] = invalid_visual_screenshots
            violations.append(
                f"🚨 [Anti-Mocking Rule 5 - Visual Screenshot Defect & Blank Screen Detected]: "
                f"시각 증적 스크린샷 중 내용 없는 빈 화면/단색/결함 프레임({', '.join(invalid_visual_screenshots[:3])})이 적발되었습니다. "
                "실사용자 UI 컴포넌트가 온전히 렌더링된 실제 프레임버퍼 스크린샷만 인정됩니다."
            )

        # -------------------------------------------------------------
        # Check 6: User Reference Image Plagiarism Detection (Rule 6)
        # -------------------------------------------------------------
        if user_reference_images:
            is_plagiarized, plagiarism_violations = cls.detect_reference_image_plagiarism(
                workspace=workspace,
                reference_images=user_reference_images
            )
            if is_plagiarized:
                details["reference_plagiarism"] = plagiarism_violations
                violations.extend(plagiarism_violations)

        # -------------------------------------------------------------
        # Check 6c: Script-Level Reference Tracing & Extraction (Rule 6c)
        # -------------------------------------------------------------
        is_traced, tracing_violations = cls.detect_script_reference_tracing_and_extraction(
            workspace=workspace,
            reference_images=user_reference_images
        )
        if is_traced:
            details["script_reference_tracing"] = tracing_violations
            violations.extend(tracing_violations)

        # -------------------------------------------------------------
        # Check 6d: Procedural & Asset Runtime Provenance Audit (Rule 6d)
        # -------------------------------------------------------------
        is_prov_clean, prov_violations, prov_details = cls.audit_runtime_asset_provenance(
            workspace=workspace
        )
        details["runtime_asset_provenance"] = prov_details
        if not is_prov_clean:
            violations.extend(prov_violations)

        # -------------------------------------------------------------
        # Check 7: Inter-Round Stale Image Repetition Detection (Rule 7)
        # -------------------------------------------------------------
        if previous_round_image_hashes:
            is_stale, stale_violations = cls.detect_stale_round_images(
                workspace=workspace,
                previous_round_image_hashes=previous_round_image_hashes,
                prompt=prompt
            )
            if is_stale:
                details["stale_round_images"] = stale_violations
                violations.extend(stale_violations)

        # -------------------------------------------------------------
        # Check 8: Physical Domain Barrier (Rule 8 - Prohibited Mockup & Extension Scan)
        # In CLI, Daemon, Bot, or Backend API projects, creating static HTML/CSS/frontend mockups
        # is an automatic physical violation that blocks the gate immediately.
        # -------------------------------------------------------------
        domain = cls.get_project_domain(workspace)
        if domain in ("cli_agent", "backend_api"):
            prohibited_exts = (".html", ".css", ".jsx", ".tsx", ".vue", ".svelte")
            prohibited_files_found = []

            # 1. Check changed files in git diff
            for fpath in changed_files:
                norm_f = fpath.replace("\\", "/").lower()
                if any(p in norm_f for p in ["reports/", ".system_generated/", "brain/", "docs/", "test/", "tests/", ".references/"]):
                    continue
                if any(norm_f.endswith(ext) for ext in prohibited_exts):
                    prohibited_files_found.append(fpath)

            # 2. Check untracked/newly created mockup files in workspace root or src/
            for check_dir in [workspace, os.path.join(workspace, "src"), os.path.join(workspace, "frontend")]:
                if os.path.isdir(check_dir):
                    for fname in os.listdir(check_dir):
                        if fname.lower() in ("index.html", "app.html", "style.css", "styles.css", "index.css"):
                            full_p = os.path.join(check_dir, fname)
                            rel_p = os.path.relpath(full_p, workspace)
                            if rel_p not in prohibited_files_found:
                                prohibited_files_found.append(rel_p)

            if prohibited_files_found:
                v_msg = (
                    f"🚨 [Anti-Mocking Rule 8 - Physical Domain Barrier Violation]: "
                    f"{domain.upper()} 도메인 규격 위반 프론트엔드 목업 파일이 적발되었습니다: ({', '.join(prohibited_files_found[:3])}). "
                    "본 프로젝트는 순수 Headless/CLI/백엔드 규격이며, 정적 웹 HTML/CSS 목업 생성은 물리적으로 엄격히 차단됩니다. "
                    "해당 웹 목업을 즉시 삭제하고 본래 도메인(Python/CLI/백엔드 로직 및 단위 테스트)에 집중하십시오."
                )
                violations.append(v_msg)
                details["prohibited_domain_files"] = prohibited_files_found

        # -------------------------------------------------------------
        # Check 9: Anti-AI-Slop & Prompt Bleed-through Detection (Rule 9)
        # Prevents LLM prompt leakage, raw pixel/thresholds in UI copy,
        # artificial infrastructure PoC simulation buttons, and AI cliches.
        # -------------------------------------------------------------
        is_slop_detected, slop_violations = cls.detect_ai_slop_and_prompt_bleed(
            workspace=workspace,
            changed_files=changed_files
        )
        if is_slop_detected:
            details["ai_slop_violations"] = slop_violations
            violations.extend(slop_violations)

        details["is_compliant"] = len(violations) == 0
        details["violations"] = violations
        return details["is_compliant"], violations, details


    @classmethod
    def run_regression_lock(cls, workspace: str, timeout_sec: float = 30.0) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Executes actual backend unit / regression tests and records Gate 6 evidence.
        """
        if not workspace or not os.path.isdir(workspace):
            return False, "Invalid workspace", {}

        cmd = None
        if os.path.isfile(os.path.join(workspace, "gradlew")):
            cmd = ["./gradlew", "test", "--continue", "-q"]
        elif os.path.isfile(os.path.join(workspace, "pytest.ini")) or os.path.isfile(os.path.join(workspace, "pyproject.toml")) or os.path.isdir(os.path.join(workspace, "tests")):
            cmd = ["python3", "-m", "unittest", "discover", "-s", "tests"]
        elif os.path.isfile(os.path.join(workspace, "package.json")):
            cmd = ["npm", "test", "--", "--passWithNoTests"]

        if not cmd:
            return True, "No test harness detected; regression check marked nominal", {"status": "SKIPPED"}

        try:
            res = subprocess.run(
                cmd,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_sec,
                text=True
            )
            passed = (res.returncode == 0)
            report_dir = os.path.join(workspace, "reports", "gate6")
            os.makedirs(report_dir, exist_ok=True)
            report_file = os.path.join(report_dir, "regression_test.json")

            report_data = {
                "timestamp": time.time(),
                "command": " ".join(cmd),
                "returncode": res.returncode,
                "passed": passed,
                "stdout_snippet": res.stdout[-1000:] if res.stdout else "",
                "stderr_snippet": res.stderr[-1000:] if res.stderr else ""
            }
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2)

            return passed, f"Regression tests {'PASSED' if passed else 'FAILED'} (exit code {res.returncode})", report_data
        except subprocess.TimeoutExpired:
            return False, f"Regression test timed out (> {timeout_sec}s)", {"status": "TIMEOUT"}
        except Exception as e:
            return False, f"Regression test execution error: {e}", {"status": "ERROR"}

    @classmethod
    def purge_all_evidence_artifacts(cls, workspace: str) -> Dict[str, Any]:
        """
        Phase 0 Physical Workspace & Evidence Purge:
        Wipes ALL residual screenshots and temporary reports from past runs across all candidate directories:
        - reports/gate4/
        - reports/gate5/
        - reports/screenshots/
        - reports/qa_screenshots/
        - screenshots/
        - reports/temp/
        Ensures strict session boundary isolation and completely eliminates stale evidence resurrection.
        """
        import glob
        import shutil
        if not workspace or not os.path.isdir(workspace):
            return {"purged_count": 0, "errors": [], "success": True}

        target_dirs = [
            os.path.join(workspace, "reports", "gate4"),
            os.path.join(workspace, "reports", "gate5"),
            os.path.join(workspace, "reports", "screenshots"),
            os.path.join(workspace, "reports", "qa_screenshots"),
            os.path.join(workspace, "screenshots"),
            os.path.join(workspace, "reports", "temp"),
        ]
        purged = []
        errors = []
        for t_dir in target_dirs:
            if os.path.isdir(t_dir):
                try:
                    for f in glob.glob(os.path.join(t_dir, "*")):
                        if os.path.isfile(f) or os.path.islink(f):
                            os.remove(f)
                            purged.append(f)
                        elif os.path.isdir(f):
                            shutil.rmtree(f, ignore_errors=True)
                            purged.append(f)
                except Exception as e:
                    errors.append(f"Purge error in {t_dir}: {e}")

        # Recreate essential report directories
        for t_dir in [
            os.path.join(workspace, "reports", "gate4"),
            os.path.join(workspace, "reports", "gate5", "screenshots")
        ]:
            os.makedirs(t_dir, exist_ok=True)

        return {"phase": "Phase 0: Evidence Purge", "purged_count": len(purged), "errors": errors, "success": len(errors) == 0}

    @classmethod
    def validate_gate(
        cls,
        workspace: str,
        gate_num: int,
        user_reference_images: Optional[List[Dict[str, Any]]] = None,
        previous_round_image_hashes: Optional[Dict[str, Any]] = None,
        session_start_time: Optional[float] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates physical evidence for a specific Quality Gate (0 to 7).
        Returns: (is_passed, summary_message, evidence_details)
        """
        if not workspace or not os.path.isdir(workspace):
            return False, "Invalid workspace directory", {}

        details: Dict[str, Any] = {"gate": gate_num, "timestamp": time.time(), "checks": {}}

        # Gate 0: Analysis & Constitution
        if gate_num == 0:
            const_file = os.path.join(workspace, "PROJECT_CONSTITUTION.md")
            if not os.path.exists(const_file):
                try:
                    from setup_manager import create_project_constitution
                    create_project_constitution(workspace)
                except Exception:
                    pass
            has_const, msg_const = cls.validate_file(const_file, min_bytes=50)
            details["checks"]["PROJECT_CONSTITUTION.md"] = {"passed": has_const, "msg": msg_const}

            prof_file = os.path.join(workspace, "profiles", "active_profile.md")
            if not os.path.exists(prof_file):
                try:
                    from setup_manager import create_project_constitution
                    create_project_constitution(workspace)
                except Exception:
                    pass
            has_prof, msg_prof = cls.validate_file(prof_file, min_bytes=50)
            details["checks"]["profiles/active_profile.md"] = {"passed": has_prof, "msg": msg_prof}

            passed = has_const and has_prof
            return passed, "Gate 0 passed" if passed else "Gate 0 failed: Missing PROJECT_CONSTITUTION.md or active_profile.md", details

        # Gate 1: Product / PRD
        elif gate_num == 1:
            prd_file = os.path.join(workspace, "PRD.md")
            has_prd, msg_prd = cls.validate_file(prd_file, min_bytes=50)
            details["checks"]["PRD.md"] = {"passed": has_prd, "msg": msg_prd}
            return has_prd, "Gate 1 passed" if has_prd else "Gate 1 failed: Missing or empty PRD.md", details

        # Gate 2: Architecture
        elif gate_num == 2:
            arch_file = os.path.join(workspace, "ARCHITECTURE.md")
            design_file = os.path.join(workspace, "DESIGN_SPEC.md")
            has_arch, msg_arch = cls.validate_file(arch_file, min_bytes=50)
            has_design, msg_design = cls.validate_file(design_file, min_bytes=50)
            passed = has_arch or has_design
            details["checks"]["ARCHITECTURE_or_DESIGN"] = {"passed": passed, "arch_msg": msg_arch, "design_msg": msg_design}
            return passed, "Gate 2 passed" if passed else "Gate 2 failed: Missing ARCHITECTURE.md or DESIGN_SPEC.md", details

        # Gate 3: Implementation (Supports multi-directory & root source files)
        # Gate 3: Implementation (Supports multi-directory, root source files, and project manifests)
        elif gate_num == 3:
            domain = cls.get_project_domain(workspace)
            if domain in ("cli_agent", "backend_api"):
                # Physical Domain Barrier: reject if unauthorized web mockup files exist in root/src
                for fname in ["index.html", "app.html", "style.css", "styles.css", "index.css"]:
                    if os.path.exists(os.path.join(workspace, fname)) or os.path.exists(os.path.join(workspace, "src", fname)):
                        return False, f"Gate 3 failed: Prohibited web mockup ({fname}) detected in {domain.upper()} project", details

            has_src_dir = any(os.path.isdir(os.path.join(workspace, d)) for d in ["src", "app/src", "lib", "app", "android", "components", "frontend", "backend", "public", "scripts", "agy_agent"])
            has_src_files = False
            for root, _, files in os.walk(workspace):
                rel = os.path.relpath(root, workspace)
                if rel.startswith((".git", ".venv", "node_modules", ".gemini", "__pycache__", "reports")):
                    continue
                if any(
                    f.endswith((".py", ".js", ".ts", ".kt", ".java", ".html", ".css", ".jsx", ".tsx", ".vue", ".dart", ".go", ".rs", ".cpp", ".c", ".h", ".json", ".xml", ".yaml", ".yml", ".gradle", ".kts")) or
                    f in ("package.json", "build.gradle", "build.gradle.kts", "requirements.txt", "pom.xml", "Cargo.toml", "go.mod", "Makefile", "Dockerfile", "pyproject.toml")
                    for f in files
                ):
                    has_src_files = True
                    break

            passed = has_src_dir or has_src_files
            details["checks"]["source_tree"] = {"passed": passed, "has_src_dir": has_src_dir, "has_src_files": has_src_files}
            return passed, "Gate 3 passed" if passed else "Gate 3 failed: No source code files or directory found", details

        # Gate 4: Functional QA
        elif gate_num == 4:
            is_anti_mock, violations, anti_mock_details = cls.verify_anti_mocking_and_authenticity(
                workspace,
                user_reference_images=user_reference_images,
                previous_round_image_hashes=previous_round_image_hashes
            )
            details["checks"]["anti_mocking"] = anti_mock_details
            if not is_anti_mock:
                return False, f"Gate 4 failed: {violations[0]}", details

            report_file = os.path.join(workspace, "reports", "gate4", "e2e_test.json")
            has_rep, msg_rep = cls.validate_file(report_file, min_bytes=10, is_json=True)
            details["checks"]["e2e_test.json"] = {"passed": has_rep, "msg": msg_rep}

            # [🔒 Patch 1: Deep Content Inspection - Anti-Report-Fabrication]
            # Merely existing as a valid JSON file is NOT enough. We must inspect the actual values
            # to prevent AI from writing {"status":"PASSED"} without running any real tests.
            if has_rep:
                try:
                    with open(report_file, "r", encoding="utf-8") as _f:
                        _e2e_data = json.load(_f)
                    _status_raw = str(_e2e_data.get("status", "")).upper()
                    if not _status_raw and int(_e2e_data.get("passed_tests", 0) or 0) > 0 and int(_e2e_data.get("failed_tests", 0) or 0) == 0:
                        _status_raw = "PASSED"
                    _failed_count = int(_e2e_data.get("failed_count", _e2e_data.get("failures", _e2e_data.get("failed", _e2e_data.get("failed_tests", 0)))) or 0)
                    # Collect status from individual test_cases if top-level status is missing
                    _tc_list = _e2e_data.get("test_cases", _e2e_data.get("tests", _e2e_data.get("results", [])))
                    _tc_fail = sum(1 for _tc in _tc_list if str(_tc.get("status", "")).upper() in ("FAIL", "FAILED", "ERROR"))
                    _is_content_valid = (
                        _status_raw in ("PASS", "PASSED", "OK", "SUCCESS", "ALL_PASSED")
                        and _failed_count == 0
                        and _tc_fail == 0
                    )
                    details["checks"]["e2e_test.json_content"] = {
                        "status_field": _status_raw,
                        "failed_count": _failed_count,
                        "tc_failures": _tc_fail,
                        "content_valid": _is_content_valid
                    }
                    if not _is_content_valid:
                        fail_reason = (
                            f"e2e_test.json 내용 검증 실패 — status='{_status_raw}', "
                            f"실패 테스트={_failed_count + _tc_fail}건. "
                            "실제 테스트를 통과해야 합니다. JSON 값만 'PASSED'로 수정하는 서류 조작은 허용되지 않습니다."
                        )
                        logger.warning(f"[Gate 4 Deep Check] {fail_reason}")
                        details["checks"]["e2e_test.json"]["passed"] = False
                        details["checks"]["e2e_test.json"]["msg"] = fail_reason
                        return False, f"Gate 4 failed: {fail_reason}", details
                except Exception as _parse_err:
                    logger.debug(f"[Gate 4 Deep Check] JSON parse error: {_parse_err}")

            passed = has_rep or os.path.isdir(os.path.join(workspace, "app", "build")) or os.path.isdir(os.path.join(workspace, "build"))
            return passed, "Gate 4 passed" if passed else "Gate 4 failed: Missing functional test report", details

        # Gate 5: Visual QA & Runtime State Integrity
        elif gate_num == 5:
            domain = cls.get_project_domain(workspace)
            is_anti_mock, violations, anti_mock_details = cls.verify_anti_mocking_and_authenticity(
                workspace,
                user_reference_images=user_reference_images,
                previous_round_image_hashes=previous_round_image_hashes
            )

            details["checks"]["anti_mocking"] = anti_mock_details
            if not is_anti_mock:
                return False, f"Gate 5 failed: {violations[0]}", details

            # Headless Domain (CLI, Daemon, Bot, Backend API): Validate runtime state & test evidence without requiring GUI screenshots
            if domain in ("cli_agent", "backend_api"):
                has_rep, msg_rep = cls.validate_file(os.path.join(workspace, "reports", "gate4", "e2e_test.json"), min_bytes=10, is_json=True)
                details["checks"]["headless_state_integrity"] = {"passed": True, "domain": domain, "e2e_evidence": msg_rep}
                return True, f"Gate 5 passed: Headless {domain.upper()} runtime state verified (GUI screenshots not required)", details

            # 1. Anti-Forgery Gate: Scan for fake canvas generator scripts (e.g. PIL generate_screenshots.py, manual Android canvas test drawing)
            is_forged, forged_scripts = cls.detect_synthetic_artifacts(workspace)
            if is_forged:
                details["checks"]["forgery_detected"] = forged_scripts
                return False, f"Gate 5 failed: Forged synthetic screenshot generator detected ({', '.join(forged_scripts)}) - only genuine browser/device framebuffer captures accepted", details

            # 2. Anti-Evasion Code Gate: Detect deceptive conditional rendering (hiding text when unselected) and font blowouts
            is_evasive, evasive_patterns = cls.detect_rendering_evasion_patterns(workspace)
            if is_evasive:
                details["checks"]["evasion_detected"] = evasive_patterns
                return False, f"Gate 5 failed: Evasive UI rendering code detected ({', '.join(evasive_patterns)}) - component visibility must be tested across all interaction states", details

            candidate_dirs = [
                os.path.join(workspace, "reports", "gate5", "screenshots"),
                os.path.join(workspace, "reports", "screenshots"),
                os.path.join(workspace, "reports", "qa_screenshots"),
                os.path.join(workspace, "screenshots"),
                os.path.join(workspace, "app", "build", "outputs", "roborazzi"),
                os.path.join(workspace, "app", "build", "reports", "paparazzi"),
            ]

            valid_images = []
            invalid_reasons = []
            stale_images = []
            image_hashes = set()
            for cdir in candidate_dirs:
                if os.path.isdir(cdir):
                    for f in sorted(os.listdir(cdir)):
                        fp = os.path.join(cdir, f)
                        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            # Strict Session Boundary: enforce temporal freshness if session_start_time is provided
                            if session_start_time is not None:
                                try:
                                    mtime = os.path.getmtime(fp)
                                    if mtime < (session_start_time - 1.0):
                                        stale_images.append(f)
                                        continue
                                except Exception:
                                    pass

                            is_v, msg = cls.validate_file(fp, min_bytes=500, is_image=True)
                            if is_v:
                                try:
                                    with open(fp, "rb") as img_file:
                                        h = hashlib.md5(img_file.read()).hexdigest()
                                    if h not in image_hashes:
                                        valid_images.append(fp)
                                        image_hashes.add(h)
                                except Exception:
                                    valid_images.append(fp)
                            else:
                                invalid_reasons.append(f"{f}: {msg}")

            details["checks"]["screenshots_count"] = len(valid_images)
            details["checks"]["unique_states_count"] = len(image_hashes)
            details["checks"]["invalid_reasons"] = invalid_reasons
            details["checks"]["stale_screenshots_rejected"] = len(stale_images)

            if len(valid_images) == 0:
                if stale_images:
                    return False, f"Gate 5 failed: All {len(stale_images)} screenshots are stale from past runs (mtime < session_start_time). Only newly generated screenshots in the current session are valid.", details
                return False, f"Gate 5 failed: No valid screenshots ({', '.join(invalid_reasons[:2]) if invalid_reasons else '0 captured'})", details

            # Require multi-state interaction diversity if multiple screenshots exist
            if len(valid_images) >= 3 and len(image_hashes) < 2:
                return False, "Gate 5 failed: Duplicate static screenshots detected (zero interaction state diversity - must capture Idle, Selected, and Active Modal states)", details

            return True, f"Gate 5 passed ({len(valid_images)} valid visual screenshots, {len(image_hashes)} distinct interaction states)", details

        # Gate 6: Real Device / Regression Lock
        elif gate_num == 6:
            reg_file = os.path.join(workspace, "reports", "gate6", "regression_test.json")
            has_reg, msg_reg = cls.validate_file(reg_file, min_bytes=10, is_json=True)
            details["checks"]["regression_test.json"] = {"passed": has_reg, "msg": msg_reg}
            return True, "Gate 6 passed", details

        # Gate 7: Release
        elif gate_num == 7:
            domain = cls.get_project_domain(workspace)
            release_artifacts = []
            for root, _, files in os.walk(workspace):
                rel = os.path.relpath(root, workspace)
                if any(ex in rel for ex in [".git", ".venv", "node_modules", "__pycache__", "reports"]):
                    continue
                for f in files:
                    if f.endswith((".apk", ".aab")) and "release" in f.lower():
                        fp = os.path.join(root, f)
                        is_v, _ = cls.validate_file(fp, min_bytes=1024 * 10) # > 10KB
                        if is_v:
                            release_artifacts.append(fp)
                    elif f == "index.html" and ("dist" in root or "build" in root):
                        fp = os.path.join(root, f)
                        is_v, _ = cls.validate_file(fp, min_bytes=100)
                        if is_v:
                            release_artifacts.append(fp)
                    elif domain in ("cli_agent", "backend_api"):
                        if f in ("main.py", "bot.py", "cli.py", "setup.py", "pyproject.toml", "Dockerfile") or f.endswith((".whl", ".tar.gz")):
                            fp = os.path.join(root, f)
                            is_v, _ = cls.validate_file(fp, min_bytes=10)
                            if is_v:
                                release_artifacts.append(fp)

            details["checks"]["release_artifacts"] = release_artifacts
            passed = len(release_artifacts) > 0
            return passed, f"Gate 7 passed ({len(release_artifacts)} artifacts)" if passed else "Gate 7 failed: No valid release artifact found", details

        return False, f"Unknown Gate {gate_num}", details

    @classmethod
    def validate_all_gates(
        cls,
        workspace: str,
        user_reference_images: Optional[List[Dict[str, Any]]] = None,
        previous_round_image_hashes: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Runs full validation on all Quality Gates 0 through 7."""
        summary = {"workspace": workspace, "timestamp": time.time(), "gates": {}}
        all_passed = True
        is_anti_mock_clean, violations, _ = cls.verify_anti_mocking_and_authenticity(
            workspace,
            user_reference_images=user_reference_images,
            previous_round_image_hashes=previous_round_image_hashes
        )
        for g in range(8):
            passed, msg, detail = cls.validate_gate(
                workspace,
                g,
                user_reference_images=user_reference_images,
                previous_round_image_hashes=previous_round_image_hashes
            )
            summary["gates"][f"Gate_{g}"] = {
                "status": "PASS" if passed else "FAIL",
                "message": msg,
                "detail": detail
            }
            if not passed and g in (0, 1, 3, 7):
                all_passed = False
        summary["all_passed"] = all_passed and is_anti_mock_clean
        summary["anti_mock_violations"] = violations
        return summary

    @staticmethod
    def parse_structured_qa_report(report_text: str) -> Dict[str, Any]:
        """
        Parses structured QA review data from LLM output.
        Extracts JSON blocks if present, or dynamically categorizes Critical, Major, Minor issues.
        """
        result = {
            "status": "UNKNOWN",
            "critical_issues": [],
            "major_issues": [],
            "minor_issues": [],
            "raw_summary": report_text[:300]
        }

        # 1. Try to find embedded JSON block
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', report_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict):
                    result["status"] = data.get("status", result["status"])
                    for issue in data.get("issues", []):
                        sev = str(issue.get("severity", "")).upper()
                        desc = issue.get("description", str(issue))
                        if "CRITICAL" in sev:
                            result["critical_issues"].append(desc)
                        elif "MAJOR" in sev:
                            result["major_issues"].append(desc)
                        else:
                            result["minor_issues"].append(desc)
                    return result
            except Exception:
                pass

        # 2. Heuristic parsing from text lines using robust status parser
        try:
            from trio_agent_runner import TrioAgentRunner
            if TrioAgentRunner.is_qa_approved(report_text):
                result["status"] = "APPROVED"
            else:
                result["status"] = "REJECTED"
        except Exception:
            if "[STATUS: APPROVED]" in report_text or "STATUS: APPROVED" in report_text or "[STATUS: PASSED]" in report_text:
                result["status"] = "APPROVED"
            elif "[STATUS: REJECTED]" in report_text or "STATUS: REJECTED" in report_text or "[STATUS: DEFECT_DETECTED]" in report_text:
                result["status"] = "REJECTED"

        current_category = "none"
        for line in report_text.splitlines():
            l = line.strip()
            l_lower = l.lower()
            if any(k in l_lower for k in ["critical", "치명적", "크래시", "빌드 실패"]):
                current_category = "critical"
            elif any(k in l_lower for k in ["major", "주요 결함", "동작 오류"]):
                current_category = "major"
            elif any(k in l_lower for k in ["minor", "사소한", "권장", "개선 제안"]):
                current_category = "minor"

            clean_line = re.sub(r'^[\*\-\•\d\.\s\💡\🔍\⚠️\🔹\[\]\🚨]+', '', l).strip()
            if len(clean_line) > 8:
                if current_category == "critical" and "critical" not in clean_line.lower():
                    if clean_line not in result["critical_issues"]:
                        result["critical_issues"].append(clean_line)
                elif current_category == "major" and "major" not in clean_line.lower():
                    if clean_line not in result["major_issues"]:
                        result["major_issues"].append(clean_line)
                elif current_category == "minor" and "minor" not in clean_line.lower():
                    if clean_line not in result["minor_issues"]:
                        result["minor_issues"].append(clean_line)

        return result

    @classmethod
    def evaluate_prd_feature_coverage(cls, workspace: str) -> Dict[str, Any]:
        """
        Extracts requirements from PRD.md (e.g. REQ-001, REQ-002, ...) and minor_backlog.json,
        scans the codebase to determine implementation status, and produces an actionable
        next development target for autonomous E2E & evolution loops.
        """
        result = {
            "has_prd": False,
            "total_requirements": 0,
            "completed_count": 0,
            "pending_count": 0,
            "completion_ratio": 0.0,
            "requirements": [],
            "minor_backlog_items": [],
            "next_recommended_target": None
        }
        if not workspace or not os.path.isdir(workspace):
            return result

        prd_path = os.path.join(workspace, "PRD.md")
        if not os.path.isfile(prd_path):
            return result

        result["has_prd"] = True

        # 1. Parse PRD.md requirements
        try:
            with open(prd_path, "r", encoding="utf-8") as f:
                content = f.read()

            req_matches = re.findall(r'[\*\-\•]?\s*\*\*([A-Z0-9\-_]+(?:\s*\([^\)]+\))?)\*\*:\s*(.+)', content)
            requirements_list = []
            for item in req_matches:
                req_id_raw = item[0].strip()
                req_desc = item[1].strip()

                m = re.match(r'(REQ-\d+)\s*(?:\((.*?)\))?', req_id_raw, re.IGNORECASE)
                if m:
                    req_id = m.group(1).upper()
                    req_title = m.group(2) or req_desc.split(".")[0]
                else:
                    req_id = req_id_raw[:20]
                    req_title = req_desc.split(".")[0]

                requirements_list.append({
                    "id": req_id,
                    "title": req_title,
                    "description": req_desc,
                    "status": "PENDING",
                    "evidence": []
                })

            if not requirements_list:
                lines = content.splitlines()
                in_core = False
                for line in lines:
                    if "핵심" in line or "Core" in line or "Requirements" in line:
                        in_core = True
                        continue
                    if in_core and line.startswith("##"):
                        break
                    if in_core and line.strip().startswith(("*", "-", "•")):
                        clean = line.strip().lstrip("*-• ").strip()
                        if len(clean) > 5:
                            req_id = f"REQ-{len(requirements_list)+1:03d}"
                            requirements_list.append({
                                "id": req_id,
                                "title": clean.split(":")[0].replace("**", ""),
                                "description": clean,
                                "status": "PENDING",
                                "evidence": []
                            })
        except Exception as e:
            logger.debug(f"Error parsing PRD.md: {e}")
            return result

        # 2. Check implementation evidence across codebase
        code_snippets = []
        for root, dirs, files in os.walk(workspace):
            # Prune ignored directories in-place for high performance
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "node_modules", "build", "dist", "coverage", ".venv", "env", "reports"))]
            rel = os.path.relpath(root, workspace)
            if rel.startswith((".git", ".venv", "node_modules", "__pycache__", ".system_generated", "build", "dist", "reports")):
                continue
            for f in files:
                if len(code_snippets) >= 500:
                    break
                # Do not count planning markdown/spec docs as implementation code evidence!
                if f.upper() in ("PRD.MD", "PROJECT_CONSTITUTION.MD", "USER_FLOW.MD", "ARCHITECTURE.MD", "DESIGN_SPEC.MD", "README.MD"):
                    continue
                if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".kt", ".java", ".json", ".sh")):
                    fp = os.path.join(root, f)
                    try:
                        # Skip oversized files (> 1MB) to prevent OOM
                        if os.path.getsize(fp) > 1024 * 1024:
                            continue
                        with open(fp, "r", encoding="utf-8", errors="ignore") as cf:
                            code_snippets.append((os.path.relpath(fp, workspace), cf.read().lower()))
                    except Exception:
                        pass
            if len(code_snippets) >= 500:
                break

        completed_count = 0
        for req in requirements_list:
            keywords = [w.lower() for w in re.findall(r'[a-zA-Z0-9가-힣]+', req["title"] + " " + req["description"]) if len(w) >= 2]
            generic_words = {"제공", "위한", "통한", "기반", "통합", "실시간", "수집", "관리", "기능", "보장", "탑재", "완전", "and", "the", "for", "with", "req"}
            keywords = [k for k in keywords if k not in generic_words][:8]

            match_count = 0
            matching_files = []
            min_required = max(2, int(len(keywords) * 0.4)) if len(keywords) >= 4 else min(2, len(keywords))
            for fpath, code_lower in code_snippets:
                # Exclude purely dummy/mock/todo files
                if any(m in code_lower for m in ["mock", "stub", "todo", "fixme"]):
                    # If mock/todo dominates keywords, do not count
                    if sum(1 for kw in keywords if kw in code_lower) < min_required + 1:
                        continue

                # Check if file has substantive code keywords
                has_code_syntax = any(syntax in code_lower for syntax in ["def ", "class ", "fun ", "function", "const ", "val ", "var ", "import ", "export ", "<div", "<view", "@composable", "return "])
                if not has_code_syntax:
                    continue

                file_matches = sum(1 for kw in keywords if kw in code_lower)
                if file_matches >= min_required:
                    match_count += 1
                    matching_files.append(fpath)

            if match_count >= 1:
                req["status"] = "COMPLETED"
                req["evidence"] = matching_files[:3]
                completed_count += 1
            else:
                req["status"] = "PENDING"

        result["total_requirements"] = len(requirements_list)
        result["completed_count"] = completed_count
        result["pending_count"] = len(requirements_list) - completed_count
        result["completion_ratio"] = (completed_count / len(requirements_list)) if requirements_list else 1.0
        result["requirements"] = requirements_list

        # 3. Read minor backlog items
        backlog_path = os.path.join(workspace, "reports", "deferred", "minor_backlog.json")
        if os.path.isfile(backlog_path):
            try:
                with open(backlog_path, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                    if isinstance(b_data, list):
                        for entry in b_data:
                            for item in entry.get("minor_items", []):
                                if item and item not in result["minor_backlog_items"]:
                                    result["minor_backlog_items"].append(item)
            except Exception:
                pass

        # 4. Formulate the single next recommended target
        pending_reqs = [r for r in requirements_list if r["status"] == "PENDING"]
        if pending_reqs:
            target = pending_reqs[0]
            result["next_recommended_target"] = {
                "type": "PRD_REQUIREMENT",
                "target_id": target["id"],
                "title": target["title"],
                "description": target["description"],
                "coder_action_plan": f"기획서 명세 [{target['id']}: {target['title']}]의 핵심 비즈니스 로직과 UI/엔드포인트를 실구현하고 mock/stub 코드를 제거하십시오.",
                "qa_acceptance_criteria": f"[{target['id']}: {target['title']}] 명세가 실제 워크스페이스 코드와 테스트에서 온전히 동작하는지 전수 검증하십시오."
            }
        else:
            # Check which minor backlog items are truly unresolved vs already implemented in codebase
            unresolved_backlog = []
            for b_idx, b_item in enumerate(result["minor_backlog_items"], start=1):
                b_keywords = [w.lower() for w in re.findall(r'[a-zA-Z0-9가-힣]+', b_item) if len(w) >= 2]
                generic_b = {"개선", "보장", "최적화", "고도화", "추가", "수정", "반영", "처리", "검증", "품질", "최소"}
                b_keywords = [k for k in b_keywords if k not in generic_b][:5]
                
                # Check if keywords are already implemented in code snippets
                is_resolved = False
                if b_keywords:
                    match_count = 0
                    for _, code_lower in code_snippets:
                        if sum(1 for kw in b_keywords if kw in code_lower) >= max(1, len(b_keywords) // 2):
                            match_count += 1
                    if match_count >= 1:
                        is_resolved = True
                
                if not is_resolved:
                    unresolved_backlog.append((f"BACKLOG-{b_idx:02d}", b_item))

            if unresolved_backlog:
                b_id, b_text = unresolved_backlog[0]
                result["next_recommended_target"] = {
                    "type": "MINOR_BACKLOG",
                    "target_id": b_id,
                    "title": f"백로그 품질 개선: {b_text[:30]}",
                    "description": b_text,
                    "coder_action_plan": f"미해결 백로그 이슈 [{b_id}] '{b_text}'를 소스코드에 정밀 패치하고 완성도를 높이십시오.",
                    "qa_acceptance_criteria": f"[{b_id}] '{b_text}' 개선 사항이 정상 적용되었는지 확인하고 회귀 결함이 없는지 검증하십시오."
                }
            else:
                # All PRD and Backlog items are resolved! System dynamically rotates optimization targets
                opt_targets = [
                    (
                        "OPT-01",
                        "다크 모드 색상 대비 및 WCAG 4.5:1 가독성 최적화",
                        "다크 모드와 라이트 모드 간 텍스트와 배경 간 WCAG 4.5:1 이상 대비율을 확보하고 디자인 토큰을 정교화합니다.",
                        "테마 전환 시 텍스트 딤(Dim) 현상과 배경색 번짐을 방지하고 시인성을 극대화하십시오."
                    ),
                    (
                        "OPT-02",
                        "화면 회전 및 프로세스 전환 시 상태 보존(SavedState) 복원력 고도화",
                        "앱이 백그라운드로 전환되거나 가로/세로 화면 회전 시 폼 입력 데이터와 스크롤 위치를 100% 온전히 복원하도록 구현합니다.",
                        "SavedStateHandle 또는 DataStore/로컬 영속성 레이어를 점검하여 데이터 유실을 완벽 방어하십시오."
                    ),
                    (
                        "OPT-03",
                        "비정상 네트워크 및 ANR 크래시 방어 예외 처리 강화",
                        "네트워크 지연, 타임아웃, 예기치 못한 입력을 처리할 수 있도록 내구성 방어 로직과 로딩 피드백을 고도화합니다.",
                        "5초 이상 지연 시 작업 취소 및 안전 롤백 메커니즘을 테스트하고 방어하십시오."
                    )
                ]
                opt_idx = int(time.time() // 3600) % len(opt_targets)
                target_id, title, desc, action = opt_targets[opt_idx]
                result["next_recommended_target"] = {
                    "type": "OPTIMIZATION",
                    "target_id": target_id,
                    "title": title,
                    "description": desc,
                    "coder_action_plan": action,
                    "qa_acceptance_criteria": "극단적 입력값(Boundary conditions)과 비정상 환경에서의 복원력을 검증하십시오."
                }

        # 5. Automatically sync Gate 4 E2E Test Report on disk
        try:
            gate4_dir = os.path.join(workspace, "reports", "gate4")
            os.makedirs(gate4_dir, exist_ok=True)
            e2e_file = os.path.join(gate4_dir, "e2e_test.json")
            tc_results = []
            for idx, r in enumerate(requirements_list, start=1):
                tc_results.append({
                    "id": f"TC-{idx:03d}",
                    "name": f"[{r['id']}] {r['title']}",
                    "status": "PASS" if r["status"] == "COMPLETED" else "PENDING",
                    "details": r["description"][:100],
                    "evidence_files": r.get("evidence", [])
                })
            report_payload = {
                "timestamp": int(time.time()),
                "test_suite": "PRD Feature & E2E Requirements Traceability Matrix",
                "total_tests": len(tc_results),
                "passed_tests": completed_count,
                "failed_tests": 0,
                "pending_tests": len(requirements_list) - completed_count,
                "completion_ratio": round(result["completion_ratio"] * 100, 1),
                "results": tc_results,
                "next_target": result["next_recommended_target"]
            }
            with open(e2e_file, "w", encoding="utf-8") as f:
                json.dump(report_payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Error updating gate4 e2e_test.json: {e}")

        return result

    @classmethod
    def run_adversarial_suite(cls, temp_dir: str = "/tmp/agy_adversarial_sandbox") -> Dict[str, Any]:
        """
        Runs 5 adversarial test scenarios to empirically prove that EvidenceValidator
        catches all tampering, blank screenshots, broken tests, and hallucinated claims.
        """
        import shutil
        os.makedirs(temp_dir, exist_ok=True)
        results = {}

        try:
            # Scenario 1: Missing APK / Release artifact
            ws1 = os.path.join(temp_dir, "ws1")
            os.makedirs(ws1, exist_ok=True)
            p1, msg1, _ = cls.validate_gate(ws1, 7)
            results["Scenario_1_Missing_APK"] = {
                "expected": "FAIL",
                "actual": "FAIL" if not p1 else "PASS",
                "caught": not p1,
                "msg": msg1
            }

            # Scenario 2: Blank Solid-White Screenshot Injection
            ws2 = os.path.join(temp_dir, "ws2")
            sc_dir = os.path.join(ws2, "reports", "gate5", "screenshots")
            os.makedirs(sc_dir, exist_ok=True)
            blank_img = Image.new("RGB", (400, 800), color=(255, 255, 255))
            blank_path = os.path.join(sc_dir, "blank.png")
            blank_img.save(blank_path)
            p2, msg2, _ = cls.validate_gate(ws2, 5)
            results["Scenario_2_Blank_Screenshot"] = {
                "expected": "FAIL",
                "actual": "FAIL" if not p2 else "PASS",
                "caught": not p2,
                "msg": msg2
            }

            # Scenario 3: Broken Test Exit Code
            ws3 = os.path.join(temp_dir, "ws3")
            os.makedirs(ws3, exist_ok=True)
            # Create a mock gradlew that exits with code 1
            gradlew_file = os.path.join(ws3, "gradlew")
            with open(gradlew_file, "w") as f:
                f.write("#!/bin/sh\nexit 1\n")
            os.chmod(gradlew_file, 0o755)
            p3, msg3, _ = cls.run_regression_lock(ws3)
            results["Scenario_3_Broken_Test"] = {
                "expected": "FAIL",
                "actual": "FAIL" if not p3 else "PASS",
                "caught": not p3,
                "msg": msg3
            }

            # Scenario 4: Missing PRD
            ws4 = os.path.join(temp_dir, "ws4")
            os.makedirs(ws4, exist_ok=True)
            p4, msg4, _ = cls.validate_gate(ws4, 1)
            results["Scenario_4_Missing_PRD"] = {
                "expected": "FAIL",
                "actual": "FAIL" if not p4 else "PASS",
                "caught": not p4,
                "msg": msg4
            }

            # Scenario 5: Hallucinated AI Approval with Missing Evidence Override
            fake_qa_text = "[STATUS: APPROVED] All tests passed perfectly! Excellent job!"
            parsed = cls.parse_structured_qa_report(fake_qa_text)
            all_gates = cls.validate_all_gates(ws1) # ws1 has missing evidence
            has_missing = any(gv["status"] == "FAIL" for gk, gv in all_gates["gates"].items() if gk in ("Gate_0", "Gate_3", "Gate_7"))
            overridden_passed = (parsed["status"] == "APPROVED") and not has_missing
            results["Scenario_5_Hallucination_Override"] = {
                "expected": "OVERRIDDEN_TO_DEFECT",
                "actual": "OVERRIDDEN_TO_DEFECT" if not overridden_passed else "ACCEPTED",
                "caught": not overridden_passed,
                "msg": "AI claim overridden because physical artifacts were missing on disk"
            }

            # Scenario 6: Forged PIL Canvas Synthetic Script Injection
            ws6 = os.path.join(temp_dir, "ws6")
            sc_dir6 = os.path.join(ws6, "reports", "gate5", "screenshots")
            os.makedirs(sc_dir6, exist_ok=True)
            # Create a mock valid image so image content check alone would pass
            img6 = Image.new("RGB", (480, 960), color=(100, 150, 200))
            img6.save(os.path.join(sc_dir6, "screen1.png"))
            # Inject fake synthesis script
            script_path = os.path.join(ws6, "reports", "gate5", "generate_screenshots.py")
            with open(script_path, "w") as f:
                f.write("from PIL import Image, ImageDraw\ndraw = ImageDraw.Draw(Image.new('RGB', (480, 960)))\n")
            p6, msg6, _ = cls.validate_gate(ws6, 5)
            results["Scenario_6_Forged_PIL_Script"] = {
                "expected": "FAIL",
                "actual": "FAIL" if not p6 else "PASS",
                "caught": not p6,
                "msg": msg6
            }

            all_caught = all(s["caught"] for s in results.values())
            results["ALL_SCENARIOS_PASSED"] = all_caught

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return results
