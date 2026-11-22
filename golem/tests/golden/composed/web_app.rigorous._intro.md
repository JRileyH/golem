Rigorous mode, new project: a reviewed spec, a skeleton deployed to prove
hosting, an MVP that Riggy tries, a design pass he picks the look in, thorough
tests, two review passes and docs.
Take the time to make it excellent: Riggy chose this mode because he wants the
best version of the idea, not the fastest.

Riggy is the product manager, not a reviewer of details. Decide names,
libraries, layouts and similar yourself and record the choice in
`docs/decisions.md` (one line each: decision, why). The look is his to pick: until
the design pass after the MVP, keep it simple and tidy; from then on, anything
visible follows `docs/design.md`.

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
