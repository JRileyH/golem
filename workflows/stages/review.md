## base

You are a fresh pair of eyes on the work this step is about (below). Review it
with skills/review.md against what it says to hold it to and `AGENTS.md` only:
don't trust earlier reasoning, read the diff and use the app.

## plan

You are a fresh pair of eyes on the plan this step is about (below), with only
`docs/brief.md` for context. Check it against Riggy's request: does it deliver
what he asked for, is anything important missing, is anything there he didn't
want, is the quality bar specific enough to build against, is each later item
small enough to build in one go? If you change the plan, note the significant
changes at the end of it under "Review notes".

## does.fixes

Fix what you find, one commit per fix, each message saying what was wrong.

Message: the most important thing you fixed.

## does.reports

Don't fix anything: say what's wrong. Choose `pass` if it's good to go as it
is, or `fail` if something needs fixing, and put what needs fixing in your
message, most important first, concretely enough for the next step to fix it
from that alone.

## care.thorough

Be demanding: hold it to the plan's quality bar. Then use the changed flows once
more in the browser as a first-time visitor on a phone, and smoke test the
production build locally.
