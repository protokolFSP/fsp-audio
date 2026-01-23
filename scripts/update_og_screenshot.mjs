// File: scripts/update_og_screenshot.mjs
import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

// Daha “yakın” görünmesi için: crop zoom ayarı
// 0.82 => ~%22 daha yakın. 0.78 daha yakın, 0.88 daha uzak.
const CROP_ZOOM = 0.82;

// Sağ ikonlar daha az önemli => crop'u biraz sola kaydır (0=sol, 0.5=ortala)
const X_BIAS = 0.25;

const VIEWPORT = { width: 1100, height: 780 };
const DEVICE_SCALE = 2;

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

  // Üste al + floating player'ı gizle (temiz crop)
  await page.evaluate(() => {
    const scrollArea = document.getElementById("scrollArea");
    if (scrollArea) scrollArea.scrollTop = 0;
    const fp = document.getElementById("floatingPlayer");
    if (fp) fp.style.display = "none";
    window.scrollTo(0, 0);
  });

  await page.waitForTimeout(700);

  // İçerik container'ını çek
  const buf = await page.locator("#appScale").screenshot({ type: "png" });

  const img = sharp(buf);
  const meta = await img.metadata();
  if (!meta.width || !meta.height) throw new Error("No image metadata");

  const targetW = 1200;
  const targetH = 630;
  const ratio = targetW / targetH;

  // Aynı ratio'da ama daha küçük bir alan kes => zoom-in
  let cropW = Math.round(meta.width * CROP_ZOOM);
  let cropH = Math.round(cropW / ratio);

  // Yükseklik yetmezse, yükseklik üzerinden hesapla
  if (cropH > meta.height) {
    cropH = Math.round(meta.height * CROP_ZOOM);
    cropW = Math.round(cropH * ratio);
  }

  cropW = clamp(cropW, 1, meta.width);
  cropH = clamp(cropH, 1, meta.height);

  // Biraz sola bias + üstten başla
  const maxX = meta.width - cropW;
  const x = clamp(Math.round(maxX * X_BIAS), 0, maxX);
  const y = 0;

  await sharp(buf)
    .extract({ left: x, top: y, width: cropW, height: cropH })
    .resize(targetW, targetH, { fit: "fill" })
    .png({ compressionLevel: 9, adaptiveFiltering: true })
    .toFile(OUT_FILE);

  await browser.close();
  console.log(`Wrote ${OUT_FILE}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
