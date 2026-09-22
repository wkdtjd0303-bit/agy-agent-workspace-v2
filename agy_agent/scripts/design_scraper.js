/**
 * scripts/design_scraper.js
 * 
 * Playwright-based High-Resolution Reference Design Scraper
 * Captures live search results (Google Images, Behance, Pinterest, Dribbble)
 * without any external paid API (SerpApi, etc.).
 * 
 * Safety:
 * - Strict 15s timeout
 * - try / finally block with unconditional browser.close()
 * - process.exit(0) upon completion
 */

const fs = require('fs');
const path = require('path');

async function scrapeDesignReference() {
  const query = process.argv[2] || 'site:behance.net modern web ui bento grid aesthetic';
  const outputPath = process.argv[3] || path.join(__dirname, '..', 'reports', 'gate2', 'design_reference.png');

  // Ensure output directory exists
  const outDir = path.dirname(path.resolve(outputPath));
  if (!fs.existsSync(outDir)) {
    fs.mkdirSync(outDir, { recursive: true });
  }

  let browser = null;
  try {
    let playwright;
    try {
      playwright = require('playwright');
    } catch (e) {
      console.log('Local playwright not found, checking global or fallback...');
      playwright = require('playwright-chromium');
    }

    browser = await playwright.chromium.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    });

    const context = await browser.newContext({
      viewport: { width: 1440, height: 1080 },
      deviceScaleFactor: 2, // High-DPI crisp capture for Vision AI
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    });

    const page = await context.newPage();

    const searchUrl = `https://www.google.com/search?tbm=isch&q=${encodeURIComponent(query)}&hl=ko`;
    console.log(`[DesignScraper] Navigating to: ${searchUrl}`);

    await page.goto(searchUrl, {
      waitUntil: 'domcontentloaded',
      timeout: 15000
    });

    // Handle Google Consent / Cookie dialog if present
    try {
      const consentBtn = await page.$('button:has-text("모두 동의"), button:has-text("Accept all"), button:has-text("I agree")');
      if (consentBtn) {
        await consentBtn.click({ timeout: 2000 });
        await page.waitForTimeout(1000);
      }
    } catch (e) {
      // Ignore consent button if absent
    }

    // Wait for thumbnail grid to render
    await page.waitForTimeout(2000);

    // Capture full-page or high-resolution viewport
    console.log(`[DesignScraper] Capturing high-resolution design reference to: ${outputPath}`);
    await page.screenshot({
      path: outputPath,
      fullPage: false // 1440x1080 hero grid capture
    });

    console.log(`[DesignScraper] Successfully saved design screenshot (${fs.statSync(outputPath).size} bytes)`);
  } catch (error) {
    console.error(`[DesignScraper Error] ${error.message}`);
    process.exit(1);
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
    process.exit(0);
  }
}

scrapeDesignReference();
