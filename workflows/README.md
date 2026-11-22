# Workflows

How golem works through each kind of request: a graph of blocks wired output to input,
from Start to End. Golem's own are these files, baked read-only into the golem image
and seeded into its database on start (`golem/golem/workflows.py`); Riggy edits them,
and makes his own, on the Workflows page (Settings → Workflows), where each one is
drawn. A job runs a copy of its workflow taken when it starts, so editing one never
changes work under way, and lets go of the copy when it's over.

## What a workflow is

- **Start type**, where it can start: `new` (a new project, from the New project
  form), `change` (a request on a project's page), `look` (a question about a
  project: builds and pushes nothing), `propose` (golem's own look for improvements,
  when it's idle), `adopt` (adopting a repo, from the New project page), or `sub`
  (only ever a container inside other workflows).
- **What it's for** (its ingest prompt): a request left on Auto is read for which of
  the workflows for a project's page it's for, by these. One of each start type is
  the fallback, for when golem can't tell, and the one the New project form picks.
  Riggy can pick one himself in the Ask box, or on the form.
- **Tabs**: a default, and one for any mode that works differently. A request that asks
  for a mode ("be thorough": the modes and what each is for are on the Workflows page),
  or a project whose default mode it is, runs that mode's tab if the workflow has one,
  else its default. Containers open their sub-workflow's tab the same way unless they
  name one (`tab: default`).

## Blocks

Each block has one way in and named outputs, each wired to one block; the first is
the usual way on.

- **Agent**: a headless Claude Code run that does one piece of work and commits it,
  with its instructions, turns and (optionally) model and effort. With several
  outputs, its verdict chooses one (each says when to take it); `blocked` asks Riggy
  and carries on. Contracts: `options` and `design` make things for a choose block to
  show (a design round finding nothing to design leaves by `nothing`), `propose` makes
  proposals.
- **Script**: a command in the job's checkout; `pass` on exit 0, else `fail`, and the
  end of its output goes to the next agent.
- **Ask Riggy** (`checkin`): posts a message and waits; his reply goes to `reply_to`
  (or the next agent). With several outputs, a cheap call reads which he means.
- **Riggy picks** (`choose`): shows the options the maker before it made; `more`
  goes back for another round.
- **Counter** and **Condition**: a count kept for the job (each container's its own),
  and true or false from counts, a block's visits or the mode.
- **Golem's own**: setup, vet, refresh, push, push-main, pr, checks, merge, deploy,
  answer, proposals. `checks` and `deploy` pass or fail (the failure goes on to the
  next agent).
- **Container**: a whole sub-workflow as one block, its outputs the sub-workflow's
  Output blocks. Nest them as deep as you like; one can't contain itself.
- **End** tells Riggy it's done; a sub-workflow ends at its **Outputs** instead.

Golem checks a workflow before a job can run it (`golem/golem/validate.py`): every
output wired, every block reachable, every way through ending, and its start type's
rules, with containers opened (a change starts with setup; a question never pushes).
Saving one with problems keeps it as it is, marked, while new jobs keep using its last
working state. A loop guard asks Riggy before a job goes round too often.

## The files

`<key>.yml` is a workflow: its name, start type, what it's for, and each tab's blocks
as a list wired in order (a block's first output goes to the next; its others name a
block by id). `<key>.md` (or `md:`) is the text every agent block gets, then a
`## <name>` section of instructions per agent block.

- `web_app.yml`: new projects. Default (spec, scaffold and first deploy, MVP, your
  check-in, a design pass you pick the look in, the buildout, review, docs), rigorous
  (a spec review, more turns, a second review) and reckless (one pass, straight to
  main).
- `change.yml`, `options.yml`, `design.yml`: a project's changes, options to pick
  from first, a design pass; each with rigorous and reckless tabs.
- `question.yml`, `proposals.yml`, `adopt.yml`.
- `ship.yml`: push, PR, CI, merge and deploy, with the fix loop inside (a hint asked of
  Riggy after four fixes); its reckless tab pushes straight to main.
  `design_round.yml`: a design round and his pick.

They were golem's playbooks, word for word; `golem/tests/golden/playbooks.json` keeps the
steps each playbook gave a job, and the tests prove the built-ins walk the same.
