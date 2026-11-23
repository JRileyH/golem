A second review, of the last review's fixes (below): are they right, complete
and tested, and did they break anything else? Then use the changed flows once
more in the browser on a phone.

Fix what you find, one commit per fix, each message saying what was wrong.

Be demanding: compare it to the quality bar, and the spec's if there is one.
Smoke test the production build locally: build it, serve it, and run the main
flow in the browser.

Message: the most important thing you fixed.

Also, for this step:

This is a new project, so use the whole app once more, as a first-time
visitor would.

## What this step is about

The last review's fixes: `git log --oneline e8965a551f72..61e62b213a1a` and `git diff e8965a551f72..61e62b213a1a`. It said: (what review said)

Hold it to: Riggy's request (above); `docs/brief.md`; `docs/specs/mvp.md`; what he picked, with its files in `docs/design/rounds/` (under "Done so far"), and what he said about it; `docs/design.md`.
