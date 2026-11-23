A skeleton that deploys, so hosting is proven before features are built.

1. Follow skills/new-repo.md: app skeleton, `Dockerfile`, `deploy/compose.yml`,
   the build workflow, `AGENTS.md`, `CLAUDE.md`, `README.md`. A styled page with
   the project's name and a line about it is enough for the app itself.
2. Build it locally the way the Dockerfile does and check the page in the
   browser (skills/browser.md).

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

The plan (`docs/brief.md`, `docs/specs/mvp.md`). It said: (what spec said)
