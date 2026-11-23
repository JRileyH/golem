1. If Riggy asked for changes (under "Riggy's feedback"), make those first,
   ahead of the rest of the plan. Update the plan if his feedback changed it.
2. Build out the rest of the plan: the spec's "after the MVP" list that's worth
   doing, with tests for core paths.
3. Polish: go through every screen in the browser at phone size and fix rough
   edges (spacing, empty states, loading, feedback on every action).

Add or update tests for the paths you touch, and keep all the project's tests
passing: `bin/test` runs them (if the project has none yet, add one as
skills/new-repo.md says). Check the result in a real browser as you go
(skills/browser.md), at phone and desktop sizes, and look at the screenshots:
fix what looks wrong or feels clumsy.

Test thoroughly: unit tests for the logic, and browser tests (Playwright) for
the main and changed flows, running in CI. Hold the screenshots to the quality
bar, and the spec's if there is one. Smoke test the production build locally:
build it, serve it, and run the main flow in the browser.

Also, for this step:

Build out the whole "after the MVP" list. In the polish, check keyboard and
touch use and accessibility basics too.

## What this step is about

Riggy tried it, and his reply is under "Riggy's feedback": what he asked for comes first.
