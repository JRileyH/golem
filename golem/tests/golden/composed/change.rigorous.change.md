Build what this step is about (below): Riggy's pick, his reply, the plan, or
his request.

1. Work out what Riggy wants. If it's a bug, reproduce it first.
2. For anything bigger than a small fix, with no plan before this step, write a
   short mini-spec in `docs/specs/` (what changes, what doesn't, how you'll know
   it works).
3. Make the change.

Add or update tests for the paths you touch, and keep all the project's tests
passing: `bin/test` runs them (if the project has none yet, add one as
skills/new-repo.md says). Check the result in a real browser as you go
(skills/browser.md), at phone and desktop sizes, and look at the screenshots:
fix what looks wrong or feels clumsy.

Test thoroughly: unit tests for the logic, and browser tests (Playwright) for
the main and changed flows, running in CI. Hold the screenshots to the quality
bar, and the spec's if there is one. Smoke test the production build locally:
build it, serve it, and run the main flow in the browser.

## What this step is about

Riggy's request (above).
