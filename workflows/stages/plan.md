## base

Decide anything the request leaves open yourself, record it in
`docs/decisions.md`, and carry on.

## writes.spec

Turn the request into a spec. The repo is new: it has only a README.

1. Write `docs/brief.md`: Riggy's request word for word, then the brief (name,
   one-paragraph summary, who it's for).
2. Write `docs/specs/mvp.md` with skills/spec-writing.md: the MVP, what comes
   after it, what's out of scope, and the bar for "good" (see the quality bar).
3. Choose the stack and record it in `docs/decisions.md` (skills/new-repo.md).

Message: the MVP in one sentence. Use `blocked` only if the request could mean
two very different products.

## writes.mini-spec

Read `AGENTS.md`, `README.md` and `docs/` first: they say how this project is
built and tested. Work out what Riggy wants. If it's a bug, reproduce it first.
Then write a short mini-spec in `docs/specs/`: what changes, what doesn't, and
how you'll know it works.

Message: the change in one sentence. Use `blocked` only if the request is
ambiguous in a way that changes what gets built.

## care.thorough

Research what makes this kind of thing good: think about the best existing apps
or games like it and what makes them satisfying. Write the findings that matter
into the plan, with a concrete bar for "good": the details that make it
delightful.
