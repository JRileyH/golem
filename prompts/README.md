# Prompts

Templates the golem service fills in (`$name` placeholders, Python
`string.Template`; write a literal dollar sign as `$$`). Baked read-only into
the golem image.

- `intake.md`: triage of a dictated idea into a brief, with its tag (one of the
  few that exist, none, or rarely a new big-picture one).
- `step.md`: the wrapper around every agent step of a playbook, except a
  question's.
- `question.md`: the wrapper around a question's `examine` step: the project,
  its latest jobs and conversation, and the question.
- `fix.md`: the instructions of a `fix` step, which golem inserts when CI fails
  or the deployer refuses a commit.
- `resume.md`: Riggy answered a `blocked` question; the same session carries on.
- `wrapup.md`: a step ran out of turns; commit and say whether it finished.
- `request.md`: whether a message in a project's conversation is a question to answer,
  wants options to pick from first, or a change made straight away.
- `pick.md`: what Riggy's reply to some options says: a pick, or other options.
- `card.md`: a live project's card on the projects page (tagline, description,
  kind), from its README and brief.

`request.md`, `pick.md` and `card.md` are filled in by plain replacement, not
`string.Template`.
