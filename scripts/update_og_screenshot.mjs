// File: scripts/update_og_screenshot.mjs
import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

const VIEWPORT = { width: 1600, height: 900 }; // sabit ekran
const DEVICE_SCALE = 2;

const CROP_FRACTION = 0.6; // %60 x %60 sol-üst
const OG_W = 1200;
const OG_H = 630;

async function main() {
  const browser = await chromium.launch({ args: ["--no-sandbox"] });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DEVICE_SCALE,
  });

  const page = await context.newPage();

  // cache kır
  const url = new URL(PAGE_URL);
  url.searchParams.set("ogcap", String(Date.now()));

  // sayfaya git (fail olursa bile screenshot alacağız)
  try {
    await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 90_000 });
  } catch {}

  // liste gelmeye çalışsın ama gelmezse yine de devam
  try {
    await page.waitForSelector("#list .itemRow", { timeout: 60_000 });
  } catch {}

  // üste al + floating player kapat
  try {
    await page.evaluate(() => {
      const scrollArea = document.getElementById("scrollArea");
      if (scrollArea) scrollArea.scrollTop = 0;
      const fp = document.getElementById("floatingPlayer");
      if (fp) fp.style.display = "none";
      window.scrollTo(0, 0);
    });
  } catch {}

  await page.waitForTimeout(600);

  // 1) tam sayfa ekran görüntüsü
  const fullPng = await page.screenshot({ type: "png" });

  // 2) sol-üst %60x%60 crop
  const meta = await sharp(fullPng).metadata();
  if (!meta.width || !meta.height) throw new Error("Screenshot metadata missing");

  const cropW = Math.max(1, Math.floor(meta.width * CROP_FRACTION));
  const cropH = Math.max(1, Math.floor(meta.height * CROP_FRACTION));_*
