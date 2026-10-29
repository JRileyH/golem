#!/usr/bin/env node
// golem-card-shot <url> <out.jpg>: the top of a live site at desktop size, for its card on
// the projects page (golem/cards.py). Exits non-zero if the page doesn't load.
import { chromium } from "playwright";

const [url, out] = process.argv.slice(2);
if (!url || !out) {
  console.error("usage: golem-card-shot <url> <out.jpg>");
  process.exit(2);
}
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 750 }, colorScheme: "light" });
  const response = await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
  if (!response || response.status() >= 400) {
    console.error(`HTTP ${response ? response.status() : "no response"} from ${url}`);
    process.exit(1);
  }
  await page.waitForTimeout(1500); // let opening animations settle
  await page.screenshot({ path: out, type: "jpeg", quality: 82 });
} finally {
  await browser.close();
}
