# Prompts

Templates the golem service fills in (`$name` placeholders, Python
`string.Template`; write a literal dollar sign as `$$`). Baked read-only into
the golem image.

- `summary.md`: a new project's summary and its first job's title, from the name
  Riggy gave it and what he said it's for (he names it himself, on the New
  project form).
- `adopt.md`: the brief for a repo golem adopts (the New project page), from what
  Riggy said about it, its README and its description; his words win.
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
- `triage.md`: golem's triage of the reports about itself (`golem/golem/triage.py`):
  the new reports, where golem's installed source, process files and job logs are, and
  every earlier proposal for golem with what Riggy decided and why.
- `report.md`: the standing order in every worker prompt above (`$report`): when and
  how to report something golem itself did that got in the worker's way, with
  `golem report` (`golem/golem/cli.py`, `golem/golem/reports.py`). Pasted as it is:
  no placeholders.
- `resolve.md`: the instructions of a `resolve` step, which golem inserts when a
  change's branch conflicts with main as it catches up (changes taking turns on
  one project, `golem/golem/turns.py`), or with another change shipping with it
  (`golem/golem/shipments.py`): merge them in, keeping both intents.
- `resume.md`: Riggy answered a `blocked` question; the same session carries on.
- `wrapup.md`: a step ran out of turns; commit and say whether it finished.
- `request.md`: which workflow a request on a project's page is for (by what each
  is for: a change, options first, a design pass, a question…), whether it asks
  for a mode, and a title for the job it starts ("Library View Redesign"). It's
  always a job of its own; what Riggy picks in the Ask box wins over this.
- `classify.md`: which of an Ask Riggy block's outputs his reply means.
- `pick.md`: what Riggy's reply to some options says: a pick, or other options.
- `card.md`: a live project's card on the projects page (tagline, description,
  kind), from its README and brief.

`request.md`, `pick.md`, `classify.md` and `card.md` are filled in by plain replacement, and
`summary.md` and `adopt.md` in one pass of it, not `string.Template`.
