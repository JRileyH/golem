Rigorous mode, new project: a reviewed spec, a skeleton deployed to prove
hosting, an MVP that Riggy tries, a design pass he picks the look in, thorough
tests, two review passes and docs.
Take the time to make it excellent: Riggy chose this mode because he wants the
best version of the idea, not the fastest.

Riggy is the product manager, not a reviewer of details. Decide names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why). The look is his to pick: until
the design pass after the MVP, keep it simple and tidy; from then on, anything
visible follows `docs/design.md`.

# Quality bar

Riggy uses these himself and shows them to friends, mostly on his phone. Build
something you'd be proud to show: it should look designed, not generated, and
feel good to use.

- **Phone first.** Every screen works and looks right at 390px wide, with touch
  targets big enough for a thumb; then make sure it's good on a desktop too.
- **Feels alive.** Every action gets immediate feedback (state changes,
  transitions, clear right/wrong states in games). No dead clicks, no layout jumps.
- **Looks deliberate.** If the project has a design guide, `docs/design.md`,
  everything you make that can be seen follows it: its colors, type, shapes,
  components and imagery, in light and dark. Without one, a small, consistent
  design: one typeface pairing, a real color palette, generous spacing. Either
  way, proper empty and loading states, no default browser styling, no lorem
  ipsum, no placeholder art left in.
- **Real content.** If the idea needs content (questions, words, images,
  levels), make enough of it, and make it correct. A quiz with five questions or
  a game with one level isn't done.
- **Games are fun.** Clear goal, quick start, a sense of progress (score,
  streaks, levels), something that makes you want another round.
- **Verify by looking.** Use the browser (skills/browser.md) and look at the
  screenshots yourself. Don't report something works until you've seen it.

## spec

Turn the request into a thorough spec. The repo is new: it has only a README.

1. Write `docs/brief.md`: Riggy's request word for word, then the brief (name,
   one-paragraph summary, who it's for).
2. Research what makes this kind of thing good: think about the best existing
   apps or games like it and what makes them satisfying. Write the findings
   that matter into the spec.
3. Write `docs/specs/mvp.md` with skills/spec-writing.md: the MVP, what comes
   after it, what's out of scope, and a concrete bar for "good" (see the
   quality bar): the details that make it delightful.
4. Choose the stack and record it in `docs/decisions.md` (skills/new-repo.md).

Message: the MVP in one sentence. Use `blocked` only if the request could mean
two very different products.

## spec-review

You are a fresh pair of eyes on `docs/specs/mvp.md`, with only `docs/brief.md`
for context. Check it against Riggy's request: does the MVP deliver what he
asked for, is anything important missing, is anything there he didn't want, is
the quality bar specific enough to build against, is each "after the MVP" item
small enough to build in one go? Edit the spec to fix what you find, and note
the significant changes at the end under "Review notes".

Message: the most important change you made.

## scaffold

A skeleton that deploys, so hosting is proven before features are built.

1. Follow skills/new-repo.md: app skeleton, test setup, `Dockerfile`,
   `deploy/compose.yml`, the build workflow (tests must run in CI), `AGENTS.md`,
   `CLAUDE.md`, `README.md`. A styled page with the project's name and a line
   about it is enough for the app itself.
2. Build it locally the way the Dockerfile does and check the page in the browser
   (skills/browser.md).

## mvp

Build the MVP from `docs/specs/mvp.md`. Riggy tries it on the live site next.

1. Build the MVP with thorough tests: unit tests for the logic, and browser
   tests (Playwright) for the main flows, running in CI.
2. Check it in a real browser as you go (skills/browser.md), at phone and
   desktop sizes. Look at the screenshots and hold them to the spec's quality
   bar; fix what falls short.
3. Smoke test the production build locally: build it, serve it, run the main
   flow in the browser.

Message: what to try and, briefly, what's deliberately not there yet.

## design

Riggy has tried the MVP; next, the project gets a look of its own. Make round 1
of its design pass (skills/design.md): the doc, with directions for him to pick
from, built on his words in `docs/brief.md` and what the MVP is. If there's been
a round before (see "Done so far" and his feedback), this round answers what he
said about it. His reply to the check-in is for the buildout step, not this one:
don't act on it here, but design for the project it describes. Don't change the
app itself in this step. Return what the skill says to return.

## guide

Write the design guide, `docs/design.md`, and the real assets it lists
(skills/design.md): from the direction Riggy picked (under "Done so far", with
where its files are), his answers to the questions (his feedback), and his
words. If he didn't pick one (the pick was skipped or left to you), go with the
round's recommendation (the doc's decisions page) and say so in your message.
Add the line about it to `AGENTS.md`. Don't restyle the app yet: the next step
does.

## apply

Restyle the MVP from `docs/design.md` and its assets (skills/design.md): tokens
and fonts first, then every component, screen and state, the icons and
manifest. Check it in a real browser on a phone and wide, light and dark
(`golem-screenshot --dark`), beside the picked direction's pages in
`docs/design/rounds/`. Keep tests passing. If building it showed the guide was
wrong somewhere, fix the guide. The buildout comes next and follows the guide.

## buildout

Riggy has tried the MVP. His reply is under "Riggy's feedback".

1. If he asked for changes, make those first, ahead of the rest of the spec.
   Update the spec if his feedback changed it.
2. Build out the spec's "after the MVP" list, with thorough tests.
3. Polish: go through every screen in the browser at phone and desktop sizes and
   fix rough edges (spacing, empty states, loading, feedback on every action,
   keyboard and touch, accessibility basics).

## review

You are a fresh pair of eyes on everything on this branch that isn't on `main`
yet. Review it with skills/review.md, against `docs/specs/mvp.md` and `AGENTS.md`
only: don't trust earlier reasoning, read the diff and use the app. Be
demanding: compare it to the spec's quality bar. Fix what you find, one commit
per fix, each message saying what was wrong.

Message: the most important thing you fixed.

## second-pass

A second review, of the first review's fixes: the latest commits in
`git log origin/main..HEAD`, after the buildout's. Are they right, complete and
tested, and did they break anything else? Then use the whole app once more in
the browser as a first-time visitor on a phone, and smoke test the production
build locally. Fix what you find.

## docs

The last step before this ships.

1. `README.md` leads with the live URL and what the project does; `AGENTS.md`
   is accurate (stack, run, test, deploy, gotchas).
2. Retro: add anything a future worker on this project should know to
   `docs/lessons.md`. Add lessons that apply to every project to the golem
   lessons file. If a playbook or skill step was wrong or missing, write the
   edit you'd propose to a new file in the golem proposals directory, for
   Riggy to apply by hand.

Message: one or two sentences on what Riggy can do on the site now. It's what
he reads when golem tells him the project is done.
