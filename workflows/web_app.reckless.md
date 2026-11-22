Reckless mode, new project: one pass from idea to live site, straight to `main`
with no review. Riggy chose speed; still make it work well and look good.

Decide colors, names, libraries, layouts and similar yourself, or follow
`docs/design.md` if the project has one.

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

## build

1. Write a short spec paragraph in `README.md` (what it is, what it does), under
   the live URL. Put Riggy's request word for word in `docs/brief.md`.
2. Set the repo up with skills/new-repo.md. Tests are optional: the image build
   is the only required check.
3. Build the whole thing. Check it in a real browser (skills/browser.md) at phone
   size and fix what's broken or ugly.

Message: one or two sentences on what Riggy can do on the site. It's what he
reads when golem tells him the project is done.
