Adopting a repo golem didn't make. It already does something, and from now on golem
owns it like a project it built: workers make every change to it, golem deploys it,
and Riggy won't edit it by hand again. Golem checked it over before taking it on;
what those checks found to fix is under "What golem's checks found".

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

## adopt

Bring the repo in: give it the shape every golem project has (skills/new-repo.md) and
make it deploy through golem (skills/deploy.md), fixing each gap golem's checks found.

1. Read the README, any docs and the code first: what it does, and how it's built, run,
   tested and deployed today.
2. Fix every gap listed. Change how it's built, tested and deployed as much as that
   needs, but not what it does: no new features, no redesign.
   - **Deploying:** golem gives it HTTPS at its live URL, so drop any proxy of its own
     (Caddy, an nginx in front) and published ports; its one public service gets
     `golem.public` and `golem.port`. Data goes in named volumes. Settings go under
     `environment:` as plain values; anything that needs its public address gets the
     live URL.
   - **Files golem's way replaces** (compose files for running it by hand, a proxy's
     config): remove them, and say so in `docs/decisions.md`.
   - **`AGENTS.md`:** write it from what you learned (stack, run, test, deploy,
     gotchas), with the template in skills/new-repo.md. Keep anything still true from
     the docs it had.
   - **README:** the live URL first, then what it already said about the project,
     corrected where it's now wrong (how to run it, where it lives).
   - **`docs/specs/`:** with no spec there, write `current.md`: what the project does
     today, as skills/spec-writing.md would put it, for later changes to build on.
   - **`docs/decisions.md`:** a line for each choice you made bringing it in, and why.
     **`docs/lessons.md`:** anything a future worker on it should know.
3. Run its tests here, and look at it in the browser (skills/browser.md) as far as you
   can without Docker.

Use `blocked` only if a gap can't be fixed without something only Riggy can give (a
secret, an account, a decision about what the project is), and say what.

Message: what you changed to bring it in, in Riggy's terms, in a sentence or two.
