A design pass for an existing, live project: a look of its own, picked by Riggy
from a few directions, then built and written down so every later change keeps
to it. His words are the request: how he wants it to feel, or just "give it a
design pass". Read `skills/design.md` first: it's how every step of this job is
done. Read `AGENTS.md`, `README.md` and `docs/` too: they say how this project
is built and tested, and `docs/design.md`, if there is one, is the look it has
now.

Decide anything the request leaves open, record it in `docs/decisions.md`, and
carry on. Use `blocked` only if the request is ambiguous in a way that changes
what gets built.

The project's mode sets the bar: in reckless mode keep tests light; in standard,
test the paths you touch; in rigorous, test thoroughly, including browser tests
for the screens that changed.

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

## guide

Write the design guide, `docs/design.md`, and the real assets it lists
(skills/design.md): from the direction Riggy picked (under "Done so far", with
where its files are), his answers to the questions (his feedback), and his words.
If he didn't pick one (the pick was skipped or left to you), go with the round's
recommendation (the doc's decisions page) and say so in your message. Add the line about it to `AGENTS.md`.
Don't restyle the app yet: the next step does.

## apply

Restyle the whole app from `docs/design.md` and its assets (skills/design.md):
tokens and fonts first, then every component, screen and state, the icons and
manifest. Check it in a real browser on a phone and wide, light and dark
(`golem-screenshot --dark`), beside the picked direction's pages in
`docs/design/rounds/`. Keep tests passing, and add or update them for what
changed. If building it showed the guide was wrong somewhere, fix the guide.

## review

You are a fresh pair of eyes on everything on this branch that isn't on `main`
yet. Review it with skills/review.md against `docs/design.md`, the direction
Riggy picked (its pages in `docs/design/rounds/`) and `AGENTS.md`: every screen
and state follows the guide, light and dark, on a phone and wide, and text
passes contrast. Fix what you find, one commit per fix, each message saying what
was wrong.

## second-pass

A second review, of the first review's fixes (`git log origin/main..HEAD`): are
they right, complete and tested, and did they break anything else? Then look at
every screen once more in the browser on a phone, light and dark. Fix what you
find.

## docs

1. Update `README.md` and `AGENTS.md` if the pass made them wrong.
2. Add anything a future worker should know to `docs/lessons.md`, and lessons
   for every project to the golem lessons file.

Message: what changed, in Riggy's terms ("Tree Cards looks like an old field
guide now: cream paper, ink green, a leaf mark"), not the code's. It's what he
reads when golem tells him the pass is live.
