// File: scripts/update_og_screenshot.mjs
import { chromium } from "playwright";
import sharp from "sharp";

const PAGE_URL = process.env.PAGE_URL ?? "https://protokolfsp.github.io/fsp-audio/";
const OUT_FILE = process.env.OUT_FILE ?? "og-home.png";

const OG_W = 1200;
const OG_H = 630;

const VIEWPORT = { width: 1400, height: 900 };
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
  url.searchParams.set("ogcap", String(Date.now())); // cache kır
  await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 90_000 });

  // Liste gelsin
  await page.waitForSelector("#list .itemRow", { timeout: 120_000 });

  // En üste al + floating player gizle
  await page.evaluate(() => {
    const scrollArea = document.getElementById("scrollArea");
    if (scrollArea) scrollArea.scrollTop = 0;
    const fp = document.getElementById("floatingPlayer");
    if (fp) fp.style.display = "none";
    window.scrollTo(0, 0);
  });

  await page.waitForTimeout(700);

  const scrollArea = page.locator("#scrollArea");
  const rows = page.locator("#list .itemRow");

  const scrollBox = await scrollArea.boundingBox();
  if (!scrollBox) throw new Error("scrollArea boundingBox not found");

  const count = await rows.count();
  const idx = count >= 3 ? 2 : Math.max(0, count - 1);
  const lastRow = rows.nth(idx);

  const lastBox = await lastRow.boundingBox();
  if (!lastBox) throw new Error("row boundingBox not found");

  // Clip alanı: scrollArea'nın üstünden başlayıp 3. satırın altına kadar
  const paddingBottom = 12;
  const clipX = scrollBox.x;
  const clipY = scrollBox.y;
  const clipW = scrollBox.width;
  const clipH = (lastBox.y + lastBo
