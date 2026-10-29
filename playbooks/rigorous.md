---
steps:
  - setup
  - {agent: spec, turns: 120}
  - {agent: spec-review, turns: 80, optional: true}
  - {agent: scaffold, turns: 200}
  - push
  - pr
  - checks
  - merge
  - deploy
  - {agent: mvp, turns: 500}
  - push
  - pr
  - checks
  - merge
  - deploy
  - {do: checkin, optional: true}
  - {agent: buildout, turns: 500}
  - {agent: review, turns: 200, optional: true}
  - {agent: second-pass, turns: 150, optional: true}
  - {agent: docs, turns: 100, optional: true}
  - push
  - pr
  - checks
  - merge
  - deploy
  - alert
---
Rigorous mode, new project: a reviewed spec, a skeleton deployed to prove
hosting, an MVP that Riggy tries, thorough tests, two review passes and docs.
Take the time to make it excellent: Riggy chose this mode because he wants the
best version of the idea, not the fastest.

Riggy is the product manager, not a reviewer of details. Decide colors, names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why).

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
