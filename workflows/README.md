# Workflows

How golem works through each kind of request: a list of blocks from Start to End, each
block's first output going to the next, with a few jumps where something can fail or
Riggy picks. These files are golem's: baked read-only into the golem image and read by
`golem/golem/workflows.py`. To change how golem works, change them here (with the tests
in `golem/tests/test_workflows.py`) and upgrade. A job runs a copy of its workflow taken
when it starts, so an upgrade never changes work under way, and keeps the copy as its
record.

Golem had a visual editor for these for a while, with tabs per mode and blocks you
could wire any way. Nearly every workflow stayed a straight line, so the editor, the
tabs and the database copies went; what Riggy changes from the web app is what each
stage block tells its agent (Settings → Blocks).

## What a workflow is

- **Start type**, where it can start: `new` (a new project, from the New project
  form), `change` (a request on a project's page), `look` (a question about a
  project: builds and pushes nothing), `propose` (golem's own look for improvements,
  when it's idle), `adopt` (adopting a repo, from the New project page), or `sub`
  (only ever a container inside other workflows).
- **What it's for** (`ingest`): a request left on Auto is read for which of the
  workflows for a project's page it's for, by these. One of each start type is the
  `fallback`, for when golem can't tell, and the one the New project form picks.
  Riggy can pick one himself in the Ask box, or on the form.

## Blocks

Each block has one way in and named outputs, each wired to one block; the first is
the usual way on.

- **Stage blocks** (`stages/`, `golem/golem/stages.py`): agent blocks that already know
  their job, set up with a few settings instead of a prompt. Plan (writes a spec or a
  mini-spec), Build (what it's about, a skeleton, the first version, the rest of the
  plan, or everything in one pass), Review (fixes what it finds, or reports `pass` or
  `fail`, its findings going on to the next block), Docs (says what changed, or what
  Riggy can do now), Design (directions to pick from, the guide, or a restyle), Fix
  (what failed, or an adopted repo's gaps), Look (answers, or proposes) and Options.
  Plan, Build, Review and Docs have a care setting (quick, usual, thorough). Each can
  add instructions of its own (`extra`), and say which earlier block it's about
  (`about`); otherwise golem works that out (`golem/golem/about.py`): a Review or Docs
  is about the work since the last of its stage, else since the job last shipped, else
  the whole job (right after a Review, that review's fixes; after only planning, the
  plan), and a Review holds it to the request and what earlier blocks made; a Build is
  about his reply to a check-in that sends it there, else Riggy's pick, his reply, the
  plan, or else the request. The text for each stage, setting and care is in
  `stages/<stage>.md`; Settings → Blocks changes it for every block of the stage, and a
  job keeps the text it started with. Contracts: Options and a Design round make things
  for a choose block to show (a design round finding nothing to design leaves by
  `nothing`); Look's `propose` makes proposals.
- **Test** (`do: test`) runs the project's `bin/test`: `pass` on exit 0, else `fail`
  with what it printed.
- **Ask Riggy** (`checkin`): posts a message and waits; his reply goes to `reply_to`
  (or the next agent). With several outputs, a cheap call reads which he means.
- **Riggy picks** (`choose`): shows the options the maker before it made; `more`
  goes back for another round.
- **Counter** and **Condition**: a count kept for the job (each container's its own),
  and true or false from counts or a block's visits. Ship's fix loop is the one that
  uses them.
- **Golem's own** (a bare name, or `do:`): setup, vet, refresh, push, pr, checks, merge,
  deploy, answer, proposals. `checks` and `deploy` pass or fail (the failure goes on
  to the next agent).
- **Container** (`use:`): a whole sub-workflow as one block, its outputs the
  sub-workflow's Output blocks. One can't contain itself.
- **End** tells Riggy it's done; a sub-workflow ends at its **Outputs** instead.

Agent blocks with instructions of their own (not a stage) are for workflows golem builds
in code, like triage's (`golem/golem/triage.py`). A loop guard asks Riggy before a job
goes round too often.

Golem's guarantees for each start type (a job that changes a project starts with setup;
one that only looks never pushes; every way through a question passes its answer) are
held by `golem/tests/test_workflows.py` over every workflow here, which also writes out
each one's usual way through.

## The files

`<key>.yml` is a workflow: its `key` (the file's name), `name`, `start`, `ingest`,
`fallback`, `md` and `blocks`, a list wired in order (a block's first output goes to the
next; its others name a block by id). `<key>.md` is the text every agent block of it
gets. A stage block is `{stage: review, does: reports, fail: fix}`: its settings by name,
its outputs wired as any block's. `stages/stages.yml` is the stages, their settings and
usuals; `stages/<stage>.md` their text, a `## <part>` per part.

- `web_app.yml`: new projects. A spec, a skeleton deployed, the MVP, your check-in, a
  design pass you pick the look in, the buildout, review and docs.
- `change.yml`, `options.yml`, `design.yml`: a project's changes, options to pick from
  first, a design pass.
- `question.yml`, `proposals.yml`, `adopt.yml`.
- `ship.yml`: push, PR, CI, merge and deploy, with the fix loop inside (a hint asked of
  Riggy after four fixes). `design_round.yml`: a design round and his pick.

`golem/tests/golden/composed/` is what each block is told, so a change to it shows as a
diff (`just compose-builtins`).
