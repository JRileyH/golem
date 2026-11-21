# Checking the app in a real browser

Chromium and Playwright are installed. Use them to see what you built: you can
read PNG screenshots with your file-reading tool, and you should.

## Quick look: `golem-screenshot`

```sh
golem-screenshot http://localhost:5173/          # a dev or preview server
golem-screenshot "$GOLEM_URL"                   # the live site
```

It loads the page at phone (390×844) and desktop (1280×800) sizes, waits for the
network to settle, saves full-page screenshots to `/tmp/screens/`, and prints
their paths plus any console errors, page errors and failed requests. Read both
screenshots. Errors printed here are bugs.

Add `--dark` to shoot each size again in dark mode (`prefers-color-scheme:
dark`), for anything with a dark theme: four screenshots, `-dark` in the names.

## A design doc: `golem-render-doc`

```sh
golem-render-doc docs/design/rounds/job-$GOLEM_JOB/round-1/doc.html docs/design/rounds/job-$GOLEM_JOB/round-1/pages
```

Renders a design round's doc the way golem shows it to Riggy, in the same
sandbox, and saves a picture of each `<section class=page>` as `page-01.png`,
`page-02.png`… It prints each picture's path with the page's heading, the fonts
that loaded, and anything the doc tried to load or run (golem blocks those, so
he wouldn't see them either). `--width` sets the page width in CSS pixels (820),
`--scale` the pixel density (2). How to make the doc: `skills/design.md`.

## Driving a flow: write a Playwright script

For anything interactive (playing a round, submitting a form), write a short
script and run it with `node`. `playwright` resolves from anywhere.

```js
// /tmp/flow.mjs
import { chromium, devices } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ ...devices["iPhone 13"] });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

await page.goto(process.argv[2]);
await page.getByRole("button", { name: /start/i }).click();
await page.screenshot({ path: "/tmp/screens/after-start.png" });
// ...each step of the flow, with a screenshot where it matters

console.log(errors.length ? errors.join("\n") : "no errors");
await browser.close();
```

```sh
mkdir -p /tmp/screens && node /tmp/flow.mjs http://localhost:5173/
```

Keep scripts that are worth keeping as the project's browser tests (with
`@playwright/test` in the repo, so CI runs them).

## Serving the app locally

- Vite: `npm run dev -- --host 127.0.0.1 &` (port 5173), or `npm run build &&
  npx vite preview --port 4173 &` for the production build.
- Static files: `python -m http.server 8080 -d dist &`.
- Stop what you started when you're done (`kill %1`).
