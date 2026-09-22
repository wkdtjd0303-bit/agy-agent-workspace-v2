#!/usr/bin/env python3
"""
scripts/design_scraper.py

Python Playwright High-Resolution Design Reference Scraper
Extracts live design reference grids without paid APIs (SerpApi, Google Custom Search API).
"""

import os
import sys
import json
import asyncio
import argparse
import urllib.parse
from playwright.async_api import async_playwright

async def scrape_design_reference(query: str, output_path: str, width: int = 1440, height: int = 1080) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://www.google.com/search?tbm=isch&q={encoded_query}&hl=ko"
    
    result = {
        "status": "FAILED",
        "query": query,
        "search_url": search_url,
        "output_path": os.path.abspath(output_path),
        "file_size": 0
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            # Navigate with strict timeout
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)

            # Accept consent if present
            try:
                consent_btn = await page.query_selector('button:has-text("모두 동의"), button:has-text("Accept all")')
                if consent_btn:
                    await consent_btn.click(timeout=2000)
                    await page.wait_for_timeout(1000)
            except Exception:
                pass

            # Settle thumbnail grid
            await page.wait_for_timeout(2000)

            # Capture screenshot
            await page.screenshot(path=output_path, full_page=False)

            if os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
                result["status"] = "SUCCESS"
                result["file_size"] = os.path.getsize(output_path)
        except Exception as e:
            result["error"] = str(e)
        finally:
            await browser.close()

    return result

def main():
    parser = argparse.ArgumentParser(description="Scrape reference UI design screenshots without paid APIs.")
    parser.add_argument("--query", "-q", default="site:behance.net modern app ui bento grid aesthetic", help="Search query")
    parser.add_argument("--output", "-o", default="reports/gate2/design_reference.png", help="Output image file path")
    parser.add_argument("--width", type=int, default=1440, help="Viewport width")
    parser.add_argument("--height", type=int, default=1080, help="Viewport height")
    args = parser.parse_args()

    res = asyncio.run(scrape_design_reference(args.query, args.output, args.width, args.height))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["status"] != "SUCCESS":
        sys.exit(1)

if __name__ == "__main__":
    main()
