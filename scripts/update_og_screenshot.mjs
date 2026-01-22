import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";
const VIEWPORT = { width: 1400, height: 900 };

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 2 });
  const page = await context.newPage();

  const url = new URL(PAGE_URL);
  url.searchParams.set("ogcap", String(Date.now()));
  await page.goto(url.toString(), { waitUntil: "domcontentloaded" });

  await page.waitForSelector("#list .itemRow", { timeout: 45_000 });

  await page.evaluate(() => {
    const el = document.getElementById("scrollArea");
    if (el) el.scrollTop = 0;
    window.scrollTo(0, 0);
  });

  await page.waitForTimeout(800);

  const app = page.locator("#scrollArea");
  const buf = await app.screenshot({ type: "png" });

  await sharp(buf)
    .resize(1200, 630, { fit: "cover", position: "top" })
    .png({ quality: 90 })
    .toFile(OUT_FILE);

  await browser.close();
  console.log(`Wrote ${OUT_FILE}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
