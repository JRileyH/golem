## base

Add or update tests for the paths you touch, and keep all the project's tests
passing: `bin/test` runs them (if the project has none yet, add one as
skills/new-repo.md says). Check the result in a real browser as you go
(skills/browser.md), at phone and desktop sizes, and look at the screenshots:
fix what looks wrong or feels clumsy.

## builds.auto

Build what this step is about (below): Riggy's pick, his reply, the plan, or
his request.

1. Work out what Riggy wants. If it's a bug, reproduce it first.
2. For anything bigger than a small fix, with no plan before this step, write a
   short mini-spec in `docs/specs/` (what changes, what doesn't, how you'll know
   it works).
3. Make the change.

## pick

Build what Riggy picked from the options an earlier step made. His pick and
where its files are is under "Done so far"; what he said about it is under
"Riggy's feedback".

1. Start from the picked option's files in `docs/options/`: they're what he
   chose, so match them, and reuse the sprites, styles and code there rather
   than starting again. Make any changes he asked for ("B, but bigger eyes"), or
   combine options if that's what he picked. If he didn't pick one (the pick
   was skipped or left to you), build the strongest and say which in your
   message.
2. Carry it through the whole app: every screen, asset and state the choice
   touches, not just what the screenshots showed. For anything bigger than a
   small change, write a short mini-spec in `docs/specs/` first. Check the
   result next to the option's screenshots.
3. Record the choice in `docs/decisions.md` (which option, and his words).
   Leave `docs/options/` as it is: it's the record of what he was shown.

## builds.skeleton

A skeleton that deploys, so hosting is proven before features are built.

1. Follow skills/new-repo.md: app skeleton, `Dockerfile`, `deploy/compose.yml`,
   the build workflow, `AGENTS.md`, `CLAUDE.md`, `README.md`. A styled page with
   the project's name and a line about it is enough for the app itself.
2. Build it locally the way the Dockerfile does and check the page in the
   browser (skills/browser.md).

## builds.first-version

Build the first version, the MVP, from the plan (`docs/specs/mvp.md`), with
tests for its core paths. Riggy tries it on the live site next: this is what he
sees first.

Message: what to try and, briefly, what's deliberately not there yet.

## builds.rest

1. If Riggy asked for changes (under "Riggy's feedback"), make those first,
   ahead of the rest of the plan. Update the plan if his feedback changed it.
2. Build out the rest of the plan: the spec's "after the MVP" list that's worth
   doing, with tests for core paths.
3. Polish: go through every screen in the browser at phone size and fix rough
   edges (spacing, empty states, loading, feedback on every action).

## builds.everything

1. Write a short spec paragraph in `README.md` (what it is, what it does), under
   the live URL. Put Riggy's request word for word in `docs/brief.md`.
2. Set the repo up with skills/new-repo.md.
3. Build the whole thing in one pass, and fix what's broken or ugly in the
   browser at phone size.

Message: one or two sentences on what Riggy can do on the site.

## care.quick

Keep tests light: the image build is the only required check.

## care.thorough

Test thoroughly: unit tests for the logic, and browser tests (Playwright) for
the main and changed flows, running in CI. Smoke test the production build
locally: build it, serve it, and run the main flow in the browser.
