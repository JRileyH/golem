#!/usr/bin/env node
// golem-render-doc <doc.html> <out-dir> [--width 820] [--scale 2]: renders a design doc the way
// golem serves it to Riggy (in the same sandbox, web.DOC_POLICY), and saves a picture of each
// `section.page` as page-01.png, page-02.png… Prints the fonts that loaded and every problem:
// anything the doc tries to load or run, a font that failed. Exits 1 if there were any.
// For workers making a design round (skills/design.md).
import { mkdirSync, readFileSync, statSync } from "node:fs";
import { chromium } from "playwright";

// The same policy golem's web app serves the doc with (golem/golem/web.py DOC_POLICY).
const POLICY = "sandbox; default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'";
const MAX_BYTES = 5 * 1024 * 1024; // golem only shows pictures up to this size (options.MAX_IMAGE_BYTES)

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(name);
  return i === -1 ? fallback : Number(args.splice(i, 2)[1]);
};
const width = flag("--width", 820);
const scale = flag("--scale", 2);
const [src, out] = args;
if (!src || !out || !width || !scale) {
  console.error("usage: golem-render-doc <doc.html> <out-dir> [--width 820] [--scale 2]");
  process.exit(2);
}
mkdirSync(out, { recursive: true });
const html = readFileSync(src);
const URL = "https://golem.invalid/doc.html";

const browser = await chromium.launch();
const problems = [];
try {
  const context = await browser.newContext({ viewport: { width, height: Math.round(width * 1.25) }, deviceScaleFactor: scale });
  await context.route("**/*", (route) => {
    const url = route.request().url();
    if (url === URL) {
      return route.fulfill({ status: 200, body: html, headers: { "content-type": "text/html; charset=utf-8", "content-security-policy": POLICY } });
    }
    problems.push(`tries to load ${url.slice(0, 120)}: put it in the file as a data: URI`);
    return route.abort();
  });
  const page = await context.newPage();
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 300)}`));
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  await page.goto(URL, { waitUntil: "load", timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  const fonts = await page.evaluate(() => [...document.fonts].map((f) => [`${f.family.replace(/"/g, "")} ${f.weight} ${f.style}`, f.status]));
  const loaded = [...new Set(fonts.filter(([, s]) => s === "loaded").map(([f]) => f))].sort();
  for (const [f, s] of fonts) if (s === "error") problems.push(`font failed to load: ${f}`);
  console.log(`fonts loaded: ${loaded.join(", ") || "none (the system's fallbacks: embed the doc's fonts)"}`);
  const pages = await page.$$("section.page");
  if (!pages.length) problems.push("no section.page elements: each page of the doc is a <section class=page>");
  for (const [i, el] of pages.entries()) {
    const path = `${out}/page-${String(i + 1).padStart(2, "0")}.png`;
    await el.screenshot({ path });
    const heading = (await el.evaluate((e) => e.querySelector("h1, h2, h3")?.textContent || "")).trim().replace(/\s+/g, " ");
    const big = statSync(path).size > MAX_BYTES ? "  (over 5 MB: golem won't show it; try --scale 1)" : "";
    console.log(`${path}: ${heading.slice(0, 80)}${big}`);
  }
  await context.close();
} finally {
  await browser.close();
}
for (const p of [...new Set(problems)]) console.log(`  ${p}`);
console.log(problems.length ? `${problems.length} problem(s) found` : "no problems");
process.exit(problems.length ? 1 : 0);
