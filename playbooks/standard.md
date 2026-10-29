---
steps:
  - setup
  - {agent: spec, turns: 80}
  - {agent: scaffold, turns: 200}
  - push
  - pr
  - checks
  - merge
  - deploy
  - {agent: mvp, turns: 400}
  - push
  - pr
  - checks
  - merge
  - deploy
  - {do: checkin, optional: true}
  - {agent: buildout, turns: 400}
  - {agent: review, turns: 150, optional: true}
  - {agent: docs, turns: 80, optional: true}
  - push
  - pr
  - checks
  - merge
  - deploy
  - alert
---
Standard mode, new project: a spec, a skeleton deployed to prove hosting, an MVP
that Riggy tries, the rest of the spec, a review with fresh eyes, and docs.

Riggy is the product manager, not a reviewer of details. Decide colors, names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why).

## spec

Turn the request into a spec. The repo is new: it has only a README.

1. Write `docs/brief.md`: Riggy's request word for word, then the brief (name,
   one-paragraph summary, who it's for).
2. Write `docs/specs/mvp.md` with skills/spec-writing.md: the MVP, what comes
   after it, what's out of scope, and the bar for "good" (see the quality bar).
3. Choose the stack and record it in `docs/decisions.md` (skills/new-repo.md).

Message: the MVP in one sentence. Use `blocked` only if the request could mean
two very different products.

## scaffold

A skeleton that deploys, so hosting is proven before features are built.

1. Follow skills/new-repo.md: app skeleton, `Dockerfile`, `deploy/compose.yml`,
   the build workflow, `AGENTS.md`, `CLAUDE.md`, `README.md`. A styled page with
   the project's name and a line about it is enough for the app itself.
2. Build it locally the way the Dockerfile does and check the page in the browser
   (skills/browser.md).

## mvp

Build the MVP from `docs/specs/mvp.md`. Riggy tries it on the live site next.

1. Build the MVP, with tests for its core paths.
2. Check it in a real browser as you go (skills/browser.md), at phone and
   desktop sizes. Look at the screenshots. Fix what looks wrong or feels clumsy:
   this is what Riggy sees first.

Message: what to try and, briefly, what's deliberately not there yet.

## buildout

Riggy has tried the MVP. His reply is under "Riggy's feedback".

1. If he asked for changes, make those first, ahead of the rest of the spec.
   Update the spec if his feedback changed it.
2. Build out the rest of the spec's "after the MVP" list that's worth doing,
   with tests for core paths.
3. Polish: go through every screen in the browser at phone size and fix rough
   edges (spacing, empty states, loading, feedback on every action).

## review

You are a fresh pair of eyes on everything on this branch that isn't on `main`
yet. Review it with skills/review.md, against `docs/specs/mvp.md` and `AGENTS.md`
only: don't trust earlier reasoning, read the diff and use the app. Fix what you
find.

Message: the most important thing you fixed.

## docs

The last step before this ships.

1. `README.md` leads with the live URL and what the project does; `AGENTS.md`
   is accurate (stack, run, test, deploy, gotchas).
2. Retro: add anything a future worker on this project should know to
   `docs/lessons.md`. Add lessons that apply to every project (a deploy gotcha,
   a CI trick) to the golem lessons file.

Message: one or two sentences on what Riggy can do on the site now. It's what
he reads when golem tells him the project is done.
