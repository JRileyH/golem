## fixes.failed

Something failed before this step, and golem can't go on until it's fixed:

$problem

1. Find the cause. Reproduce it locally if you can: run the tests, or build the
   app the way the Dockerfile and the CI workflow do.
2. Fix the cause and commit. Don't skip, delete or weaken tests or the workflow
   to get a green build.
3. If the deployer rejected `deploy/compose.yml`, its rules are in
   skills/deploy.md. If the site didn't answer after deploying, the logs above
   (or `golem-deploy logs`) say why.

## fixes.gaps

Bring the repo in: give it the shape every golem project has (skills/new-repo.md)
and make it deploy through golem (skills/deploy.md), fixing each gap golem's
checks found:

$problem

1. Read the README, any docs and the code first: what it does, and how it's
   built, run, tested and deployed today.
2. Fix every gap listed. Change how it's built, tested and deployed as much as
   that needs, but not what it does: no new features, no redesign.
   - **Deploying:** golem gives it HTTPS at its live URL, so drop any proxy of
     its own (Caddy, an nginx in front) and published ports; its one public
     service gets `golem.public` and `golem.port`. Data goes in named volumes.
     Settings go under `environment:` as plain values; anything that needs its
     public address gets the live URL.
   - **Files golem's way replaces** (compose files for running it by hand, a
     proxy's config): remove them, and say so in `docs/decisions.md`.
   - **`AGENTS.md`:** write it from what you learned (stack, run, test, deploy,
     gotchas), with the template in skills/new-repo.md. Keep anything still true
     from the docs it had.
   - **README:** the live URL first, then what it already said about the
     project, corrected where it's now wrong (how to run it, where it lives).
   - **`docs/specs/`:** with no spec there, write `current.md`: what the project
     does today, as skills/spec-writing.md would put it, for later changes to
     build on.
   - **`docs/decisions.md`:** a line for each choice you made bringing it in,
     and why. **`docs/lessons.md`:** anything a future worker on it should know.
3. Run its tests here, and look at it in the browser (skills/browser.md) as far
   as you can without Docker.

Use `blocked` only if a gap can't be fixed without something only Riggy can
give (a secret, an account, a decision about what the project is), and say what.
