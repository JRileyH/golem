# Prompts

Templates the golem service fills in (`$name` placeholders, Python
`string.Template`; write a literal dollar sign as `$$`). Baked read-only into
the golem image.

- `intake.md`: triage of a dictated idea into a brief, with its tag (one of the
  few that exist, none, or rarely a new big-picture one) and its first job's
  title.
- `adopt.md`: the same brief for a repo golem adopts (`!adopt`), from what Riggy
  said about it, its README and its description; his words win.
- `step.md`: the wrapper around every agent step of a playbook, except a
  question's.
- `question.md`: the wrapper around a question's `examine` step: the project,
  its latest jobs and conversation, and the question.
- `proposals.md`: the wrapper around a proposals job's `propose` step: the
  project, its latest jobs and conversation, this time's lens, and every earlier
  proposal with what Riggy decided and why.
- `survey.md`: the run inside an adoption's `vet` step (`golem/golem/adopt.py`):
  install and test the repo in golem's container, and say what it needs that
  golem can't give. It changes nothing; its answer is structured output.
- `fix.md`: the instructions of a `fix` step, which golem inserts when CI fails
  or the deployer refuses a commit.
- `resolve.md`: the instructions of a `resolve` step, which golem inserts when a
  change's branch conflicts with main as it catches up (changes taking turns on
  one project, `golem/golem/turns.py`): merge main in, keeping both intents.
- `resume.md`: Riggy answered a `blocked` question; the same session carries on.
- `wrapup.md`: a step ran out of turns; commit and say whether it finished.
- `request.md`: whether a message in a project's conversation is a question to answer,
  wants options to pick from first, or a change made straight away, and a title
  for the job it starts ("Library View Redesign").
- `pick.md`: what Riggy's reply to some options says: a pick, or other options.
- `card.md`: a live project's card on the projects page (tagline, description,
  kind), from its README and brief.

`request.md`, `pick.md` and `card.md` are filled in by plain replacement, and
`intake.md` and `adopt.md` in one pass of it, not `string.Template`.
