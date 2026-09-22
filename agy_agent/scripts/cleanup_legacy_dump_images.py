#!/usr/bin/env python3
"""
🧹 AGY Legacy Dump Images & Dead Screenshots Cleanup Tool
Safely purges unreferenced temporary screenshots and dumping images
across legacy projects without touching production assets or source code.
"""

import os
import glob
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LegacyCleanup")

BASE_DIR = "/home/jangsung/Desktop/autocoding"

def cleanup_project_dumps(dry_run: bool = False):
    total_freed_bytes = 0
    total_removed_files = 0

    # 1. ministry scratch temporary screenshots
    ministry_scratch = os.path.join(BASE_DIR, "!1_vibecoding", "ministry", "scratch")
    if os.path.isdir(ministry_scratch):
        for f in glob.glob(os.path.join(ministry_scratch, "*.png")):
            sz = os.path.getsize(f)
            total_freed_bytes += sz
            total_removed_files += 1
            if not dry_run:
                os.remove(f)
        logger.info(f"Cleaned ministry/scratch temporary screenshots")

    # 2. parking_lot root temporary test screenshots (preserve icons/logos)
    parking_lot_root = os.path.join(BASE_DIR, "!1_vibecoding", "parking_lot")
    if os.path.isdir(parking_lot_root):
        target_root_screens = [
            "ParkMemo_Screenshot_1.png",
            "ParkMemo_Screenshot_2.png",
            "apk_map_qa_verified.png",
            "parkmemo_10_logos_comparison_grid.png"
        ]
        for sname in target_root_screens:
            sp = os.path.join(parking_lot_root, sname)
            if os.path.isfile(sp):
                sz = os.path.getsize(sp)
                total_freed_bytes += sz
                total_removed_files += 1
                if not dry_run:
                    os.remove(sp)
        logger.info(f"Cleaned parking_lot root test screenshots")

    # 3. minder-note reports old screenshots (keep latest 10)
    minder_screens = os.path.join(BASE_DIR, "!1_vibecoding", "minder-note", "reports", "gate5", "screenshots")
    if os.path.isdir(minder_screens):
        all_imgs = sorted(glob.glob(os.path.join(minder_screens, "*.png")), key=os.path.getmtime, reverse=True)
        if len(all_imgs) > 10:
            for old_f in all_imgs[10:]:
                sz = os.path.getsize(old_f)
                total_freed_bytes += sz
                total_removed_files += 1
                if not dry_run:
                    os.remove(old_f)
            logger.info(f"Trimmed {len(all_imgs) - 10} old screenshots in minder-note (kept latest 10)")

    # 4. pdfediter_andriod reports old screenshots (keep latest 10)
    pdf_screens = os.path.join(BASE_DIR, "!1_vibecoding", "pdfediter_andriod", "reports", "gate5", "screenshots")
    if os.path.isdir(pdf_screens):
        all_imgs = sorted(glob.glob(os.path.join(pdf_screens, "*.png")), key=os.path.getmtime, reverse=True)
        if len(all_imgs) > 10:
            for old_f in all_imgs[10:]:
                sz = os.path.getsize(old_f)
                total_freed_bytes += sz
                total_removed_files += 1
                if not dry_run:
                    os.remove(old_f)
            logger.info(f"Trimmed {len(all_imgs) - 10} old screenshots in pdfediter_andriod (kept latest 10)")

    mb_freed = total_freed_bytes / (1024 * 1024)
    mode_str = "DRY-RUN (Simulated)" if dry_run else "EXECUTED"
    logger.info(f"[{mode_str}] Total cleaned: {total_removed_files} files, {mb_freed:.2f} MB freed")
    return total_removed_files, total_freed_bytes

if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    cleanup_project_dumps(dry_run=dry)
