# Writing a spec

A spec is for the next worker (who has none of your context) and for Riggy, who
reads it on a phone. Keep it short: one screen is ideal, two is the limit.

```markdown
# <name>: <what this spec covers>

## Goal
One or two sentences: who it's for and what they can do with it.

## MVP
The smallest version worth looking at. A short list of things a person can do,
written as behavior ("pick a tree from four photos and see if you were right"),
not as components.

## After the MVP
What comes next, in rough priority order. Each item small enough for one PR.

## Out of scope
Things someone might expect that we're deliberately not doing.

## How we'll know it works
The checks that prove the MVP: the main flow end to end, and the tests that cover it.
```

Guidelines:

- Decide, don't list options. Record non-obvious choices in `docs/decisions.md`.
- Prefer behavior that works without accounts, payments or email: these are
  personal tools.
- If the idea needs content (questions, images, data), say where it comes from:
  written by you, generated at build time, or a public dataset with its license.
- A mini-spec for a change to an existing project uses the same headings, minus
  "After the MVP", and lives at `docs/specs/<yyyy-mm-dd>-<short-name>.md`.
