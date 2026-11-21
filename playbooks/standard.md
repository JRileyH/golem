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
  - {agent: design, turns: 400, optional: true}
  - {do: choose, optional: true}
  - {agent: guide, turns: 200}
  - {agent: apply, turns: 400}
  - push
  - pr
  - checks
  - merge
  - deploy
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
that Riggy tries, a design pass he picks the look in, the rest of the spec, a
review with fresh eyes, and docs.

Riggy is the product manager, not a reviewer of details. Decide names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why). The look is his to pick: until
the design pass after the MVP, keep it simple and tidy; from then on, anything
visible follows `docs/design.md`.

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
