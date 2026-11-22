# Prompts

Templates the golem service fills in (`$name` placeholders, Python
`string.Template`; write a literal dollar sign as `$$`). Baked read-only into
the golem image.

- `intake.md`: triage of a dictated idea into a brief, with its tag (one of the
  few that exist, none, or rarely a new big-picture one), its first job's title,
  the mode it asks for, and which workflow for new projects it's for when there's
  more than one.
- `adopt.md`: the same brief for a repo golem adopts (`!adopt`), from what Riggy
  said about it, its README and its description; his words win.
- `step.md`: the wrapper around every agent block of a workflow, except in jobs
  that only look (a question's, proposals'): the project, the request, what's been
  done, the block's instructions, what comes after it, and how to finish.
- `question.md`: the wrapper around a question's `examine` step: the project,
  its latest jobs and conversation, and the question.
- `proposals.md`: the wrapper around a proposals job's `propose` step: the
  project, its latest jobs and conversation, this time's lens, and every earlier
  proposal with what Riggy decided and why.
- `survey.md`: the run inside an adoption's `vet` step (`golem/golem/adopt.py`):
  install and test the repo in golem's container, and say what it needs that
  golem can't give. It changes nothing; its answer is structured output.
- `resolve.md`: the instructions of a `resolve` step, which golem inserts when a
  change's branch conflicts with main as it catches up (changes taking turns on
  one project, `golem/golem/turns.py`): merge main in, keeping both intents.
- `resume.md`: Riggy answered a `blocked` question; the same session carries on.
- `wrapup.md`: a step ran out of turns; commit and say whether it finished.
- `request.md`: which workflow a message in a project's conversation is for (by what
  each is for: a change, options first, a design pass, a question…), whether it asks
  for a mode, and a title for the job it starts ("Library View Redesign").
- `classify.md`: which of an Ask Riggy block's outputs his reply means.
- `request-jobs.md`: added to `request.md` on a project that allows more than one
  change at once, with changes under way: the changes, and which of them the
  message is about, if any (otherwise golem starts it beside them).
- `pick.md`: what Riggy's reply to some options says: a pick, or other options.
- `card.md`: a live project's card on the projects page (tagline, description,
  kind), from its README and brief.

`request.md`, `request-jobs.md`, `pick.md`, `classify.md` and `card.md` are filled in by plain replacement, and
`intake.md` and `adopt.md` in one pass of it, not `string.Template`.
