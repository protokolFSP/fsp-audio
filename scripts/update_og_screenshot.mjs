import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

const VIEWPORT = { width: 1400, height: 900 };
const DEVICE_SCALE = 2;

async function gotoWithRetry(page, url, tries = 3) {
  let lastErr;
  for (let i = 0; i < tries; i++) {
    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 90_000 });
      return;
    } catch (e) {
      lastErr = e;
      await page.waitForTimeout(2000);
    }
  }
  throw lastErr;
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DEVICE_SCALE,
  });

  const page = await context.newPage();

  const url = new URL(PAGE_URL);
  url.searchParams.set("ogcap", String(Date.now()));
  await gotoWithRetry(page, url.toString(), 3);

  await page.waitForSelector("#list .itemRow", { timeout: 120_000 });

  await page.evaluate(() => {
    const scrollArea = document.getElementById("scrollArea");
    if (scrollArea) scrollArea.scrollTop = 0;
    const fp = document.getElementById("floatingPlayer");
    if (fp) fp.style.display = "none";
    window.scrollTo(0, 0);
  });

  await page.waitForTimeout(800);

  const target = page.locator("#appScale");
  const buf = await target.screenshot({ type: "png" });

  await sharp(buf)
    .trim(24) // netlik için biraz daha agresif crop
    .resize(1200, 630, { fit: "cover", position: "top" })
    .png({ compressionLevel: 9, adaptiveFiltering: true })
    .toFile(OUT_FILE);

  await browser.close();
  console.log(`Wrote ${OUT_FILE}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
