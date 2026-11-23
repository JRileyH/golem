Build the first version, the MVP, from the plan (`docs/specs/mvp.md`), with
tests for its core paths. Riggy tries it on the live site next: this is what he
sees first.

Add or update tests for the paths you touch, and keep all the project's tests
passing: `bin/test` runs them (if the project has none yet, add one as
skills/new-repo.md says). Check the result in a real browser as you go
(skills/browser.md), at phone and desktop sizes, and look at the screenshots:
fix what looks wrong or feels clumsy.

Test thoroughly: unit tests for the logic, and browser tests (Playwright) for
the main and changed flows, running in CI. Hold the screenshots to the quality
bar, and the spec's if there is one. Smoke test the production build locally:
build it, serve it, and run the main flow in the browser.

Message: what to try and, briefly, what's deliberately not there yet.

## What this step is about

The plan (`docs/brief.md`, `docs/specs/mvp.md`). It said: (what spec said)
