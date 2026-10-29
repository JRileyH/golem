#!/usr/bin/env node
// golem-screenshot <url> [out-dir]: phone and desktop screenshots of a page, plus
// any console errors, page errors and failed requests. For workers (skills/browser.md).
import { mkdirSync } from "node:fs";
import { chromium, devices } from "playwright";

const [url, outDir = "/tmp/screens"] = process.argv.slice(2);
if (!url) {
  console.error("usage: golem-screenshot <url> [out-dir]");
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });

const sizes = [
  ["phone", { ...devices["iPhone 13"] }],
  ["desktop", { viewport: { width: 1280, height: 800 } }],
];
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const browser = await chromium.launch();
let problems = 0;
for (const [name, options] of sizes) {
  const page = await browser.newPage(options);
  const notes = [];
  page.on("console", (m) => m.type() === "error" && notes.push(`console error: ${m.text()}`));
  page.on("pageerror", (e) => notes.push(`page error: ${e.message}`));
  page.on("requestfailed", (r) => notes.push(`request failed: ${r.url()} (${r.failure()?.errorText})`));
  page.on("response", (r) => r.status() >= 400 && notes.push(`HTTP ${r.status()}: ${r.url()}`));
  try {
    const response = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
    if (response && response.status() >= 400) notes.push(`page returned HTTP ${response.status()}`);
  } catch (e) {
    notes.push(`could not load: ${e.message.split("\n")[0]}`);
  }
  const path = `${outDir}/${stamp}-${name}.png`;
  await page.screenshot({ path, fullPage: true });
  console.log(`${name}: ${path}`);
  for (const note of [...new Set(notes)]) console.log(`  ${note}`);
  problems += notes.length;
  await page.close();
}
await browser.close();
console.log(problems ? `${problems} problem(s) found` : "no errors");
process.exit(problems ? 1 : 0);
