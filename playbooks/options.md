---
steps:
  - setup
  - {agent: options, turns: 200}
  - {do: choose, optional: true}
  - {agent: change, turns: 400}
  - {agent: review, turns: 150, modes: [standard, rigorous], optional: true}
  - {agent: second-pass, turns: 150, modes: [rigorous], optional: true}
  - {agent: docs, turns: 60, optional: true}
  - push
  - pr
  - checks
  - merge
  - deploy
  - alert
---
A change to an existing, live project that Riggy wants to choose before it's
built: first a few options for him to pick from, then the one he picks, built
properly. His words are the request. Read `AGENTS.md`, `README.md` and `docs/`
before touching anything: they say how this project is built and tested.

Decide anything the request leaves open, record it in `docs/decisions.md`, and
carry on. Use `blocked` only if the request is ambiguous in a way that changes
what gets built, or would destroy something irreversible (deleting data).

The project's mode sets the bar: in reckless mode keep tests light; in standard,
test the paths you touch; in rigorous, test thoroughly, including browser tests
for changed flows.

## options

Make the options Riggy asked to see. Golem shows them to him on his phone,
lettered A, B, C… in the order you return them, and the next step builds the
one he picks from your files. Don't change the app itself in this step.

1. Work out what he wants to choose between, and how many options: as many as
   he asked for, otherwise three (at most six). Make them really different from
   each other and from what's there now, each one something you'd be happy to
   build. If there's been a round before (see "Done so far" and his feedback),
   this round answers what he said about that one.
2. Make each option concrete enough to judge from a picture. For anything he'd
   see (an art style, characters, a layout, colors, a logo), make it for real
   with the project's own stack and assets where you can, beside the app rather
   than in it: a preview page in the option's folder that draws a real scene or
   screen, say. Show the same scene in each option, so they compare side by
   side. Render it and take screenshots
   at phone size (skills/browser.md): one to three per option, PNG or JPEG,
   under 2 MB each. Look at them, and redo anything that doesn't show the idea
   well. For things he can't see (rules, wording, how something works),
   describe each option plainly; a picture only where it helps.
3. Put each option's files in its own folder: `docs/options/job-<n>/a/`, `b/`,
   and so on, where `<n>` is this job's number (`$GOLEM_JOB`); for a later
   round, `docs/options/job-<n>/round-2/a/`. Keep its screenshots there with
   whatever the next step should reuse: the sprites, styles or code you made,
   and a preview page if you built one. Commit them. Leave earlier rounds as
   they are.

Return `done` with `options`, in order: for each, a short `title`, a
`description` (one or two plain sentences: what it is and why he might pick it)
and `files` (paths from the repo root, the screenshots he should see first).
Message: one sentence introducing the choice ("Three chibi takes on the hero and
the first level.").

## change

Build what Riggy picked from the options the step before made. His pick and
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
   small change, write a short mini-spec in `docs/specs/` first.
3. Add or update tests for the paths you touch, and check the result in a real
   browser (skills/browser.md) at phone size, next to the option's screenshots.
4. Record the choice in `docs/decisions.md` (which option, and his words).
   Leave `docs/options/` as it is: it's the record of what he was shown.

## review

You are a fresh pair of eyes on everything on this branch that isn't on `main`
yet. Review it with skills/review.md against Riggy's pick (its files in
`docs/options/`), what he said about it, the mini-spec if there is one, and
`AGENTS.md`. Fix what you find, one commit per fix, each message saying what was
wrong.

## second-pass

A second review, of the first review's fixes (`git log origin/main..HEAD`): are
they right, complete and tested, and did they break anything else? Then use the
changed flows once more in the browser on a phone. Fix what you find.

## docs

1. Update `README.md` and `AGENTS.md` if the change made them wrong.
2. Add anything a future worker should know to `docs/lessons.md`, and lessons
   for every project to the golem lessons file.

Message: what changed, in Riggy's terms ("the hero and enemies are chibi now,
in the pastel style you picked"), not the code's. It's what he reads when golem
tells him the change is live.
