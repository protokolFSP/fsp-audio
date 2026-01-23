// File: scripts/update_og_screenshot.mjs
import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

// ✅ İSTEDİĞİN GÖRÜNÜM (daha yakın / okunaklı)
const CROP_ZOOM = 0.76; // 0.72 daha yakın, 0.80 daha uzak
const X_BIAS = 0.0;     // 0 = soldan başla (sağ ikonlar biraz kesilsin)
const TRIM = 22;        // dış boşlukları temizler

const VIEWPORT = { width: 1200, height: 850 };
const DEVICE_SCALE = 2;

const OG_W = 1200;
const OG_H = 630;
const OG_RATIO = OG_W / OG_H;

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
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
  await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 90_000 });

  await page.waitForSelector("#list .itemRow", { timeout: 120_000 });

  await page.evaluate(() => {
    const scrollArea = document.getElementById("scrollArea");
    if (scrollArea) scrollArea.scrollTop = 0;
    const fp = document.getElementById("floatingPlayer");
    if (fp) fp.style.display = "none";
    window.scrollTo(0, 0);
  });

  await page.waitForTimeout(700);

  // Önce appScale; olmazsa scrollArea fallback
  let rawBuf;
  try {
    rawBuf = await page.locator("#appScale").screenshot({ type: "png" });
  } catch {
    rawBuf = await page.locator("#scrollArea").screenshot({ type: "png" });
  }

  // 1) Dış boşlukları trimle
  const trimmedBuf = await sharp(rawBuf).trim(TRIM).toBuffer();
  const meta = await sharp(trimmedBuf).metadata();
  if (!meta.width || !meta.height) throw new Error("No image metadata");

  // 2) OG oranında ama daha küçük bir alan seç (zoom-in)
  let cropW = Math.round(meta.width * CROP_ZOOM);
  let cropH = Math.round(cropW / OG_RATIO);

  // yükseklik yetmezse yükseklikten hesapla
  if (cropH > meta.height) {
    cropH = Math.round(meta.height * CROP_ZOOM);
    cropW = Math.round(cropH * OG_RATIO);
  }

  cropW = clamp(cropW, 1, meta.width);
  cropH = clamp(cropH, 1, meta.height);

  const maxX = meta.width - cropW;
  const x = clamp(Math.round(maxX * X_BIAS), 0, maxX);
  const y = 0; // üstten başla (ilk satırlar görünsün)

  // 3) Crop + export
  await sharp(trimmedBuf)
    .extract({ left: x, top: y, width: cropW, height: cropH })
    .resize(OG_W, OG_H, { fit: "fill" })
    .png({ compressionLevel: 9, adaptiveFiltering: true })
    .toFile(OUT_FILE);

  await browser.close();
  console.log(`Wrote ${OUT_FILE}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
