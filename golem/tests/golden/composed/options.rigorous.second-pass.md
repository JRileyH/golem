A second review, of the last review's fixes (below): are they right, complete
and tested, and did they break anything else? Then use the changed flows once
more in the browser on a phone.

Fix what you find, one commit per fix, each message saying what was wrong.

Message: the most important thing you fixed.

Be demanding: compare it to the quality bar, and the spec's if there is one.
Smoke test the production build locally: build it, serve it, and run the main
flow in the browser.

## What this step is about

The last review's fixes: `git log --oneline 7550b672e162..61e62b213a1a` and `git diff 7550b672e162..61e62b213a1a`. It said: (what review said)

Hold it to: Riggy's request (above); what he picked, with its files in `docs/options/` (under "Done so far"), and what he said about it; this job's mini-spec in docs/specs/, if it has one.
