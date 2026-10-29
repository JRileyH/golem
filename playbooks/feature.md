---
steps:
  - setup
  - {agent: change, turns: 400}
  - {agent: review, turns: 150, modes: [standard, rigorous], optional: true}
  - {agent: second-pass, turns: 150, modes: [rigorous], optional: true}
  - {agent: docs, turns: 60, optional: true}
  - push
  - pr
  - checks
  - merge
  - deploy
  - alert
---
A change to an existing, live project: a feature, a fix, or "I don't like how X
works, make it do Y". Riggy asked for it in the project's conversation; his words are the
request. Read `AGENTS.md`, `README.md` and `docs/` before touching code: they say
how this project is built and tested.

Decide anything the request leaves open, record it in `docs/decisions.md`, and
carry on. Use `blocked` only if the request is ambiguous in a way that changes
what gets built, or would destroy something irreversible (deleting data).

The project's mode sets the bar: in reckless mode keep tests light; in standard,
test the paths you touch; in rigorous, test thoroughly, including browser tests
for changed flows.

## change

1. Work out what Riggy wants. If it's a bug, reproduce it first.
2. For anything bigger than a small fix, write a short mini-spec in
   `docs/specs/` (what changes, what doesn't, how you'll know it works).
3. Make the change, and add or update tests for the paths it touches. Check the
   result in a real browser (skills/browser.md) at phone size.

## review

You are a fresh pair of eyes on everything on this branch that isn't on `main`
yet. Review it with skills/review.md against Riggy's request, the mini-spec if
there is one, and `AGENTS.md`. Fix what you find, one commit per fix, each
message saying what was wrong.

## second-pass

A second review, of the first review's fixes (`git log origin/main..HEAD`): are
they right, complete and tested, and did they break anything else? Then use the
changed flows once more in the browser on a phone. Fix what you find.

## docs

1. Update `README.md` and `AGENTS.md` if the change made them wrong.
2. Add anything a future worker should know to `docs/lessons.md`, and lessons
   for every project to the golem lessons file.

Message: what changed, in Riggy's terms ("the quiz now shows the answer after
each question"), not the code's. It's what he reads when golem tells him the
change is live.
