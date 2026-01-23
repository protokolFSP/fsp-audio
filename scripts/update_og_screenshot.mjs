// File: scripts/update_og_screenshot.mjs
import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

const VIEWPORT = { width: 1600, height: 900 };
const DEVICE_SCALE = 2;

// ✅ sol-üstten, OG oranında crop
const CROP_WIDTH_FRACTION = 0.66; // genişlik %66
const OG_W = 1200;
const OG_H = 630;
const OG_RATIO = OG_W / OG_H;

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

async function main() {
  const browser = await chromium.launch({
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DEVICE_SCALE,
  });

  const page = await context.newPage();

  const url = new URL(PAGE_URL);
  url.searchParams.set("ogcap", String(Date.now()));

  await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 90_000 });
  await page.waitForSelector("#list .itemRow", { timeout: 120_000 });

  // üste al + floating player kapat
  await page.evaluate(() => {
    const scrollArea = document.getElementById("scrollArea");
    if (scrollArea) scrollArea.scrollTop = 0;
    const fp = document.getElementById("floatingPlayer");
    if (fp) fp.style.display = "none";
    window.scrollTo(0, 0);
  });

  await page.waitForTimeout(600);

  // ✅ İçeriği daha stabil yakalamak için scrollArea screenshot
  const fullPng = await page.locator("#scrollArea").screenshot({ type: "png" });

  const meta = await sharp(fullPng).metadata();
  if (!meta.width || !meta.height) throw new Error("Screenshot metadata missing");

  // OG oranında crop: W * fraction, H = W / ratio
  let cropW = Math.floor(meta.width * CROP_WIDTH_FRACTION);
  let cropH = Math.floor(cropW / OG_RATIO);

  // Yükseklik yetmezse, yükseklik üzerinden hesapla
  if (cropH > meta.height) {
    cropH = Math.floor(meta.height * 0.98);
    cropW = Math.floor(cropH * OG_RATIO);
  }

  cropW = clamp(cropW, 1, meta.width);
  cropH = clamp(cropH, 1, meta.height);

  await sharp(fullPng)
    .extract({ left: 0, top: 50, width: cropW, height: cropH }) // ✅ sol-üst
    .resize(OG_W, OG_H, { fit: "fill" }) // ✅ ratio aynı -> yanlardan kesmez
    .png({ compressionLevel: 9, adaptiveFiltering: true })
    .toFile(OUT_FILE);

  await context.close();
  await browser.close();

  console.log(`Wrote ${OUT_FILE}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
