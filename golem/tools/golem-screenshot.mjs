#!/usr/bin/env node
// golem-screenshot <url> [out-dir] [--dark]: phone and desktop screenshots of a page, plus
// any console errors, page errors and failed requests. With --dark, each again in dark mode
// (prefers-color-scheme: dark). For workers (skills/browser.md).
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium, devices } from "playwright";

const args = process.argv.slice(2);
const dark = args.includes("--dark");
// $TMPDIR/screens by default: each job has its own TMPDIR, so workers side by side keep theirs apart.
const [url, outDir = join(tmpdir(), "screens")] = args.filter((a) => a !== "--dark");
if (!url) {
  console.error("usage: golem-screenshot <url> [out-dir] [--dark]");
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });

const sizes = [
  ["phone", { ...devices["iPhone 13"] }],
  ["desktop", { viewport: { width: 1280, height: 800 } }],
];
const schemes = dark ? ["light", "dark"] : ["light"];
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const browser = await chromium.launch();
let problems = 0;
for (const scheme of schemes) {
  for (const [name, options] of sizes) {
    const page = await browser.newPage({ ...options, colorScheme: scheme });
    const notes = [];
    page.on("console", (m) => m.type() === "error" && notes.push(`console error: ${m.text()}`));
    page.on("pageerror", (e) => notes.push(`page error: ${e.message}`));
    page.on("requestfailed", (r) => notes.push(`request failed: ${r.url()} (${r.failure()?.errorText})`));
    page.on("response", (r) => r.status() >= 400 && notes.push(`HTTP ${r.status()}: ${r.url()}`));
    try {
      const response = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
      if (response && response.status() >= 400) notes.push(`page returned HTTP ${response.status()}`);
      await page.evaluate(() => document.fonts.ready);
    } catch (e) {
      notes.push(`could not load: ${e.message.split("\n")[0]}`);
    }
    const label = scheme === "dark" ? `${name}-dark` : name;
    const path = `${outDir}/${stamp}-${label}.png`;
    await page.screenshot({ path, fullPage: true });
    console.log(`${label}: ${path}`);
    for (const note of [...new Set(notes)]) console.log(`  ${note}`);
    problems += notes.length;
    await page.close();
  }
}
await browser.close();
console.log(problems ? `${problems} problem(s) found` : "no errors");
process.exit(problems ? 1 : 0);
