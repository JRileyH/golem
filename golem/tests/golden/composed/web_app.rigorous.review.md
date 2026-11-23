You are a fresh pair of eyes on the work this step is about (below). Review it
with skills/review.md against what it says to hold it to and `AGENTS.md` only:
don't trust earlier reasoning, read the diff and use the app.

Fix what you find, one commit per fix, each message saying what was wrong.

Be demanding: compare it to the quality bar, and the spec's if there is one.
Smoke test the production build locally: build it, serve it, and run the main
flow in the browser.

Message: the most important thing you fixed.

## What this step is about

The work since the last review (spec-review): `git log --oneline 1136b91c6ed6..HEAD` and `git diff 1136b91c6ed6..HEAD`.

What each step since said:
- scaffold: (what scaffold said)
- mvp: (what mvp said)
- design: (what design said)
- guide: (what guide said)
- apply: (what apply said)
- buildout: (what buildout said)

Hold it to: Riggy's request (above); `docs/brief.md`; `docs/specs/mvp.md`; what he picked, with its files in `docs/design/rounds/` (under "Done so far"), and what he said about it; `docs/design.md`.
