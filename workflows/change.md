A change to an existing, live project: a feature, a fix, or "I don't like how X
works, make it do Y". Riggy asked for it in the project's conversation; his words are the
request. Read `AGENTS.md`, `README.md` and `docs/` before touching code: they say
how this project is built and tested.

Decide anything the request leaves open, record it in `docs/decisions.md`, and
carry on. Use `blocked` only if the request is ambiguous in a way that changes
what gets built, or would destroy something irreversible (deleting data).

The project's mode sets the bar: in reckless mode keep tests light; in standard,
test the paths you touch; in rigorous, test thoroughly, including browser tests
for changed flows.

# Quality bar

Riggy uses these himself and shows them to friends, mostly on his phone. Build
something you'd be proud to show: it should look designed, not generated, and
feel good to use.

- **Phone first.** Every screen works and looks right at 390px wide, with touch
  targets big enough for a thumb; then make sure it's good on a desktop too.
- **Feels alive.** Every action gets immediate feedback (state changes,
  transitions, clear right/wrong states in games). No dead clicks, no layout jumps.
- **Looks deliberate.** If the project has a design guide, `docs/design.md`,
  everything you make that can be seen follows it: its colors, type, shapes,
  components and imagery, in light and dark. Without one, a small, consistent
  design: one typeface pairing, a real color palette, generous spacing. Either
  way, proper empty and loading states, no default browser styling, no lorem
  ipsum, no placeholder art left in.
- **Real content.** If the idea needs content (questions, words, images,
  levels), make enough of it, and make it correct. A quiz with five questions or
  a game with one level isn't done.
- **Games are fun.** Clear goal, quick start, a sense of progress (score,
  streaks, levels), something that makes you want another round.
- **Verify by looking.** Use the browser (skills/browser.md) and look at the
  screenshots yourself. Don't report something works until you've seen it.

## change

1. Work out what Riggy wants. If it's a bug, reproduce it first.
2. For anything bigger than a small fix, write a short mini-spec in
   `docs/specs/` (what changes, what doesn't, how you'll know it works).
3. Make the change, and add or update tests for the paths it touches. Check the
   result in a real browser (skills/browser.md) at phone size.

## review

You are a fresh pair of eyes on everything on this branch that isn't on `main`
yet. Review it with skills/review.md against Riggy's request, the mini-spec if
there is one, and `AGENTS.md`. Fix what you find, one commit per fix, each
message saying what was wrong.

## second-pass

A second review, of the first review's fixes (`git log origin/main..HEAD`): are
they right, complete and tested, and did they break anything else? Then use the
changed flows once more in the browser on a phone. Fix what you find.

## docs

1. Update `README.md` and `AGENTS.md` if the change made them wrong, and
   `docs/design.md` if it changed the look (a new component, color or screen
   pattern later work should reuse).
2. Add anything a future worker should know to `docs/lessons.md`, and lessons
   for every project to the golem lessons file.

Message: what changed, in Riggy's terms ("the quiz now shows the answer after
each question"), not the code's. It's what he reads when golem tells him the
change is live.
