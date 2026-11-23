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

A review in this job checks the look above all: every screen and state follows
the guide, light and dark, on a phone and wide, and text passes contrast.

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
