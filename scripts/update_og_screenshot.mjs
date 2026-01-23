// File: scripts/update_og_screenshot.mjs
import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

// Daha net: biraz büyük render alıp kırpıyoruz
const VIEWPORT = { width: 1400, height: 900 };
const DEVICE_SCALE = 3; // 2 -> 3 daha net (dosya boyutu artar)

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DEVICE_SCALE,
  });

  const page = await context.newPage();

  // Cache kır (capture sırasında)
  const url = new URL(PAGE_URL);
  url.searchParams.set("ogcap", String(Date.now()));
  await page.goto(url.toString(), { waitUntil: "domcontentloaded" });

  // Liste dolsun
  await page.waitForSelector("#list .itemRow", { timeout: 45_000 });

  // En üste
  await page.evaluate(() => {
    const el = document.getElementById("scrollArea");
    if (el) el.scrollTop = 0;
    window.scrollTo(0, 0);
  });

  // Biraz boya
  await page.waitForTimeout(900);

  // ✅ Boşluk az olsun diye direkt içerik container'ını çek
  const target = page.locator("#appScale");
  await target.waitFor({ state: "visible", timeout: 10_000 });

  const buf = await target.screenshot({ type: "png" });

  // ✅ Crop/Netlik:
  // - trim(): dıştaki gereksiz beyaz/boş alanı kırpar
  // - resize cover (top): üst bölüm + ilk satırlar görünsün
  await sharp(buf)
    .trim(18) // daha agresif istersen 22-28 yap
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
