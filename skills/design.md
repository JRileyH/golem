# Design passes

A design pass gives a project a look of its own, the way golem got its own: Riggy's
words about how it should feel, a design doc with a few directions to choose from,
his picks, then the look built for real and written down so every later change
keeps to it. Three kinds of step take part:

- **design** makes a round: the doc, with directions A, B, C… for him to pick from
  (round 1), or a focused sheet answering what he said about the last round.
- **guide** writes the design guide, `docs/design.md`, and the real assets.
- **apply** restyles the whole app from the guide.

Golem shows each round on his phone: each direction's pictures, the pages they
share, a link to the doc itself, your pick and your questions. His reply (a
letter, and answers to the questions) reaches the next step as his feedback.

## A round's files

Everything for round `k` goes in `docs/design/rounds/job-$GOLEM_JOB/round-k/`:

```
doc.html          the doc: one self-contained file (below)
pages/            golem-render-doc's pictures of every page
a/ b/ c/          each direction: its tokens.css, fonts.css, mockup screenshots,
                  and copies of its own pages' pictures, the ones to show first
```

Commit them; they stay as the record of what he was shown. Leave earlier rounds
as they are.

## The doc

Golem serves it from its own site in a sandbox: **one HTML file that runs nothing
and loads nothing.** Fonts and pictures go in as `data:` URIs, there's no
`<script>`, no `http-equiv`, no links to anything outside. Golem refuses a doc
that breaks this, or one over 10 MB. Anything that needs JavaScript to draw (a
canvas scene, a game screen) goes in as a screenshot.

Each page is a `<section class=page>`, portrait (4:5), 820px wide, with
`<meta name=viewport content="width=device-width, initial-scale=1">` and CSS that
lets pages shrink to a phone's width: he reads it on his phone, and Home shows the
top of a direction's first picture cropped square.

Round 1's pages, modeled on golem's own design pass:

1. **Cover**: the project's name, "Design pass", his words about how it should
   feel, and what's in the doc.
2. **Principles**: four to six rules every direction follows, each a line and a
   small demo. Include what to avoid ("the vibe, not the costume": no textures,
   cracks or parchment standing in for a feel that weight, proportion and color
   should carry). The project stays usable first.
3. **Each direction**, two or three pages, A, B, C in the order you'll return them:
   - its name, a one-line tagline and three or four mood words; its palette as
     named swatches ("Brick", "Slate mist"), light and dark, each with its hex,
     its role (surface, text, accent, states) and its contrast; its type: a
     display face and a reading face, shown at real sizes;
   - **the project's real screens** in it: its main screen on a phone in light
     and in dark, and wide; the screens that matter most, not a generic mockup;
   - its pieces where they matter: buttons, cards, inputs, states (empty,
     loading, error), and for anything with imagery (a game's sprites, an
     illustration style), a sample made in the style.
4. **Now**, as a reference beside the directions: today's look, never an option.
5. **Logo and wordmark**, when the project hasn't one he's happy with: two or
   three marks and wordmarks, at the sizes they're used (header, app icon, 32px,
   16px). They're choices by name, not letters: letters are the directions.
6. **App icon**, for anything that installs or has a favicon worth caring about.
7. **Decisions**: numbered questions besides the direction ("Wordmark: Oxanium or
   Zilla Slab?", "Icon: Granite or Kiln?"), your pick with why, and what will
   change in the code.

Three directions unless he asked for another number. Make them really different
from each other and from now, each one you'd be happy to build, all within what
he said. If his words don't say much about the feel, the directions are where you
explore it: three distinct moods, named.

### The real screens

Run the app (on your own port: skills/browser.md), then shoot it with each
direction's CSS laid over it, so the mockups are its own screens and the app
itself doesn't change:

```js
// $TMPDIR/mock.mjs <url> <direction.css> <out.png> [light|dark] [width] [height]
import { chromium } from "playwright";
const [url, css, out, scheme = "light", w = "390", h = "844"] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 2, colorScheme: scheme });
await page.goto(url, { waitUntil: "networkidle" });
await page.addStyleTag({ path: css });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: out });
await browser.close();
```

A direction's CSS is its fonts (`@font-face` with `data:` URIs) and overrides of
the app's own variables and classes. If the app has no variables, override its
selectors; that file is what `apply` starts from if he picks it. Shoot "Now" the
same way with no CSS.

### Colors

Every text color passes WCAG AA on the surface it sits on, in light and dark:
4.5:1 for text, 3:1 for large text and icons. Check each pair:

```python
def lum(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

def contrast(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
```

Dark mode is its own palette, tuned, not the light one inverted.

### Fonts

Only fonts with an open license (OFL, Apache). Get them from npm, which ships the
woff2 files and the license: `npm install @fontsource-variable/<family>` (or
`@fontsource/<family>`) in a scratch folder. Subset what goes in the doc to Latin
to keep it small:

```sh
uvx --from 'fonttools[woff]' pyftsubset font.woff2 --unicodes='U+0000-00FF,U+2010-205E,U+20AC' --flavor=woff2 --output-file=font.sub.woff2
base64 -w0 font.sub.woff2   # for url(data:font/woff2;base64,…)
```

Two families per direction at most: one for display, one for reading.

### Rendering and checking

Stop the dev server first: golem and every worker share 1.5 GB, and Chromium
needs some. Then:

```sh
golem-render-doc docs/design/rounds/job-$GOLEM_JOB/round-1/doc.html docs/design/rounds/job-$GOLEM_JOB/round-1/pages
```

It renders the doc in the sandbox golem serves it in and prints each page's
picture, the fonts that loaded and anything blocked. Get it to no problems, then
look at every picture yourself and fix what doesn't read or doesn't show the idea
well. Pictures over 5 MB aren't shown: use `--scale 1` or shrink the mockups.
Copy each direction's own pages into its folder.

### What to return

`done`, with:

- `options`: the directions, in the doc's order. For each, a `title` (its name),
  a `description` (one or two plain sentences: the feel, and why he might pick
  it) and `files`: its folder's files, its pages' pictures first (golem shows the
  first three).
- `doc`: the doc's path.
- `pages`: pictures of the pages every direction shares, in order (principles,
  logo, icon, decisions), at most four.
- `questions`: the numbered decisions besides the direction, each a short
  question naming its choices.
- `recommendation`: your pick in a sentence ("B, with the Oxanium wordmark and
  the Kiln icon").
- `message`: one sentence introducing the round ("Three looks for Tree Cards,
  from field guide to arcade.").

If there's nothing to design (he said to keep the look as it is), return `done`
with `skip`: why, in a sentence, and no options. Golem skips the rest of the pass.

## Later rounds

His reply asked for another round. Read his feedback and what's settled:

- **Different directions**: a new doc like round 1, answering what he said about
  the last one ("warmer", "less retro").
- **More on one piece** ("B, but show me more icons"): a focused sheet. The
  settled direction stays; the piece he asked about gets two to four variants,
  lettered as the options, beside the current one as "Now", each at every size
  it's used: a mark big, as the app icon, at 32px and 16px; a palette on a real
  screen in light and dark. One or two pages, and the questions only about what's
  still open.

## Logo, wordmark and icons

- **Mark**: a hand-written SVG of a few simple shapes, one color, so it works on
  any background. It has to read at 16px: big simple forms, filling most of the
  tile, beat detail.
- **Wordmark**: the name set in the display face, then turned into outlines
  (fontTools' `SVGPathPen`, via `uvx --from fonttools python …`), so the SVG
  doesn't depend on the font being there.
- **Icons**: render the SVGs with Chromium (`page.setContent` with the SVG at the
  size, then `page.screenshot`, `omitBackground` for transparency): a 180px
  `apple-touch-icon`, 192px and 512px for the web manifest (with a maskable
  version whose mark sits inside the middle 80%), and `favicon.svg` plus a 32px
  PNG.
- **Imagery** (sprites, illustrations, backgrounds): made in code or SVG in the
  direction's palette and shapes, consistent with each other.

## The guide: `docs/design.md`

The guide step writes it from the round he picked, his answers and his words. It's
what every later step follows when it makes anything visible, so it's specific:

1. **The feel**: his words, and the direction's name, tagline and mood words.
2. **Principles**: the rules, including what not to do.
3. **Color**: every color by name, with its hex in light and dark and its role
   (surface, card, text, muted text, accent, states), and the contrast rules.
4. **Type**: the families and what each is for, the sizes and weights used, where
   the font files are.
5. **Shape**: corners, borders, shadows or edges, spacing.
6. **Components**: how buttons, cards, inputs, badges and states look, and which
   classes or components make them.
7. **Imagery**: the style of any art, and how to make more that fits.
8. **Logo and icons**: the files, where each is used, and how not to use them.
9. **Decisions**: what he picked and answered, in his words, with the date.
10. **How to apply it**: what a new screen or feature has to do to fit in.
11. **Assets**: a table of every file (tokens, fonts and licenses, SVGs, icons)
    and its path.

Keep one copy of each asset, wherever the app serves its files from, and list it
in the table; don't keep a second copy elsewhere that drifts. The design tokens go
in one stylesheet (CSS custom properties for colors in light and dark, the font
stacks, radii, spacing) that the app loads first. Add a line to `AGENTS.md`:
"Anything visible follows `docs/design.md`."

## Applying it

The apply step builds the guide into the app: tokens first, then the fonts
(self-hosted, from the app's own files: nothing loads from Google or any CDN),
then every component, screen and state (empty, loading, error, not just the happy
path), the favicon, icons, web manifest and `theme-color` (light and dark). Check
it with `golem-screenshot --dark` on a phone and wide, light and dark, beside the
picked direction's pages, and fix anything that doesn't match or fails contrast.
If building it showed the guide was wrong somewhere, fix the guide too.
