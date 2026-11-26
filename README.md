# golem

A self-hosted system that turns ideas dictated from a phone into deployed web
projects at `<slug>.<your domain>`, built by background Claude Code workers. It
runs on any box with Docker and a public IP (a VPS, a Raspberry Pi 5), x86 or ARM.

See `BRIEF.md` for the design and `AGENTS.md` for how to work on the repo.

## Using it

Everything happens in the web app at `https://golem.<your domain>`, made for a
phone.

- **The web app** has three tabs: Now, Projects and Settings, with
  **+ New project** in the header (the sidebar, on a wide screen). Sign in under
  Settings with `GOLEM_WEB_PASSWORD` (tick "Keep me signed in" for 90 days on
  that device). Everywhere, a job is drawn by its **route**: a strip of the
  blocks of its workflow, lit as each went (✓ done, ▶ running, ? waiting on
  you, ✗ failed), a loop once with ×N, a sub-workflow like Ship as one chip that
  fills as it goes, and the next few blocks faded. Tap a block for its details.
  - **Now:** every job, across projects. What needs you first, answered right
    there (try the site and say it looks good or what to change, pick option A,
    B or C, answer a question), and golem's proposals to thumb up or down; then
    what's running, what's up next, and what finished lately (with a question's
    answer). golem's news (usage limits, upgrades, finished jobs) is on top.
  - **Projects:** a card for each project: its screenshot once it's live, what
    it is, its tag and site, and its latest job's route. A project's page has its
    jobs on the left: **Ask for…** (a change, or a question), golem's proposals
    for it, and its latest 12 jobs (and any still under way), newest first; its
    **All jobs** tab has the rest, to search by words or number and filter by
    how they went and their workflow. On the right, the job you picked (the
    one waiting on you, else the one running, else the latest): where it is and
    what it needs from you, a note for its next step, run it next or last or
    stop it; its steps, with how long each took and what it said; its activity
    (what each block did, notes, questions and your replies); and the request
    and its runs. On a phone it's one side at a time. Its **Settings** tab has
    the model,
    effort, pause, how many changes at once, proposals, tag, its jobs, rename,
    move and delete; **History** has what was said before jobs had their own
    activity.
  - **Settings:** signing in and out, notifications, pausing all work, workers,
    golem's version, links to Insights, Reports, **Blocks** (the text of golem's
    stage blocks: Plan, Build, Review, Docs…, to read and change) and the public
    projects page, the tags, and the model and effort for each kind of step.
  - **Notifications:** in Settings, "Turn on for this device". You get what
    wants you: questions, check-ins, options to pick from, answers, "Done" and
    usage limits, each opening its job (and its block). On an iPhone, first add
    the site to the Home Screen (Share, then Add to Home Screen) and open it
    from there: iOS only lets sites notify that way. It's still the same site,
    not an app from the App Store. Golem makes its own push keys; there's
    nothing to configure.
- **A new project:** + New project. Name it (its address, and its repo
  `<GITHUB_ORG>/<slug>`, follow the name until you change them), say what it's
  for ("a flashcard game for northeastern US trees"), and pick its workflow (the
  fallback for new projects is picked), its mode and tag; model and effort are under
  More. golem writes its summary and its first job's title, and the job starts
  straight away.
- **How a job runs:** it walks a workflow: blocks wired output to input, from
  Start to End, golem's own files (`workflows/`: to change one, change it there
  and upgrade). Agent blocks are Claude Code runs that each do one piece of work
  and commit it, and choose the way on where a block has several outputs. Golem's own
  blocks are its code, the same every time: setting up, pushing, opening a PR,
  waiting for CI, merging, deploying, asking you, and telling you it's done. In
  golem's built-ins, when CI fails or the deployer refuses a commit a fix runs and
  it ships again; after four fixes it asks you for a hint.
- **Asking for something:** on a project's page, Ask for… always starts a job
  of its own. Leave Workflow on Auto and a quick haiku call picks it from what
  you wrote, by what each is for (when it's not sure, it takes a question, the
  cheap mistake), and titles the job; or pick it. Its Mode is the project's
  default on Auto, or the one you pick. A change waits for one under way,
  unless the project allows more at once.
  - **Questions** ("why does the score reset?", "what's left on the spec?"):
    golem looks into it (the code, the docs, the live site, its logs) and
    answers in a few minutes, building nothing: no branch, commit, PR or
    deploy. A question goes ahead of other work, and can come in while a job is
    under way (it then looks at that job's work so far).
  - **Changes** ("I don't like how the score works, count streaks instead"):
    the job says when it starts, and again when it's live or if it needs an
    answer.
  - **Options first** ("I want a new art style, show me three that are more
    chibi", or the Options workflow): the job makes them before building
    anything. The worker mocks each option up for real, with screenshots and
    the files behind them in `docs/options/` on the job's branch; golem shows
    them as pictures (A, B, C…) and waits. Reply with a letter and anything
    you'd change ("B, but bigger eyes"), or ask for other options for another
    round. The next step builds your pick from its files.
  - **Design pass** ("make it feel like an old field guide", or the Design
    workflow): a worker makes a design doc (`skills/design.md`): principles, a
    few directions with named palettes, type and the project's real screens in
    light and dark, logo and icon choices, and its pick. Golem shows each
    direction's pages, the doc's link and its questions, and waits. Reply with a
    letter and your answers, or ask for another round ("A, but show me more
    icons"). Then a worker writes the design guide, `docs/design.md`, with the
    real assets, restyles the app from it, and every later change follows it.
- **A job's activity:** progress shows up as quiet notices; questions, check-ins
  and "Done" notify your phone.
  - Each agent step posts "✓ step: what was done" and what's next; so do PRs
    and deploys. Failures post "✗ …. Fixing it."
  - During an agent step, the worker posts a "▸" note at each milestone. After
    20 minutes without news, golem says what the worker is on ("Still on mvp
    (step 9/24), 45m in…"). A run that logs nothing for 30 minutes is stopped
    and retried.
  - When golem is waiting on you, your reply is the answer ("looks good",
    "make the photos bigger"), from the job's pane or from Now.
  - While it's working, a note (Add a note) goes to the job's next step.
- **Checklist mode:** an experiment in building with cheaper models. Each Build
  plans its work as a checklist on its usual model (items in order, each with a
  check whose tests it writes); golem builds each item on the `item` default
  (haiku at medium effort) and runs its check, trying a failed item again (its
  last try on the Build's model) before carrying on without it. Pick it for a
  new project, a request, or a project's default. **Build it both ways** on the
  New project form makes two projects from one idea, hosted alike: the usual
  way at its address, and as a checklist at `cl-` and its address. Both run in
  a vacuum, as on a fresh golem: lessons of their own starting empty, and each
  job a Claude Code of its own with nothing in it (no settings, memory or
  history). Each job's page says what it cost (usage at API prices, tokens,
  how long its agents ran, cost by model, fixes, and a checklist's items):
  open the two in two tabs. Golem records no judgement of quality; you look
  at the sites.
- **Settings,** per project, on its Settings tab:
  - **Default mode:** what its requests run in unless they pick another: the
    usual way, or checklist. Only you set it.
  - **Model and effort:** for all the project's agent steps, from the next
    one; "each step's default" goes back to each step's own.

  The model and effort are chosen per step, the most specific choice winning:
  one step's (set in its sheet in the web app), then the project's, then
  the default for that kind of step (set in the web app's Settings), then
  `GOLEM_MODEL` and `GOLEM_EFFORT` in `.env`. Golem starts each kind of step
  on its own suggestion (`SUGGESTED` in `golem/plan.py`): opus at xhigh for
  spec, mvp and review, sonnet for scaffold and docs, opus at high for the
  rest. It fills them in once, where you haven't set one, so your
  changes on Insights stay; each kind shows its suggestion while yours differs,
  and "Reset to golem's suggestions" puts them all back (projects' and single
  steps' own choices stay).
- **Adopting a repo golem didn't make:** on the New project page, "Adopt a repo
  golem didn't make": the repo, what it is (or its README says), and its name,
  mode and tag. Golem checks it over first: its CI, build workflow,
  `deploy/compose.yml` by the deployer's own rules, the shape every project has,
  and a worker run that builds and tests it in golem's container and says what
  it needs that golem can't give. Anything only you can change and it leaves the
  repo alone and says what; otherwise it fixes the gaps in a PR and deploys it,
  and from then on it's a project like any other ("Adopting a repo" in
  `docs/runbook.md`). It's also the only way a project comes back, after it's
  deleted or on a new box (which starts empty), with its past jobs read from its
  issues and PRs.
- **Moving and deleting a project:** its Settings tab. Moving gives it a new
  address and repo (golem moves the code; the old repo stays for you to delete).
  Deleting (type its slug to confirm) takes it all: the site and its data, the
  checkout, the job history and logs. The repo stays on GitHub for you to delete
  (golem's token can't). To delete every project at once, run `bin/nuke.sh`
  on the box ("Starting over" in `docs/runbook.md`).
- **Projects page:** `https://projects.<your domain>`, public: a card for each
  live project with a screenshot, its tag, a tagline, a description and a
  button that says what you'll do there (Play, Open, Use, Visit). Cards are
  written by a quick haiku call from the project's README and brief, with a
  screenshot of the live site, when a project first goes live and again at the
  end of every job. Projects live before this get theirs when golem starts.
- **Tags:** a few big-picture sorts of project, each in one color on the
  projects page, which filters by them. golem starts with "Game". You pick a
  project's tag when you make it, or on its Settings tab; Settings lists them
  to add, rename, recolor or delete.
- **Who sees what:** anyone can see what's under way, each job's route and
  steps, and each step's details; activity, asking, Blocks and every control
  need signing in. Signed in, you can skip optional steps
  (reviews, docs, the MVP check-in, your pick) or undo that, pick an option,
  give one upcoming step its own model, effort or context, run a job next or
  last, pause a project or all work, stop a job, and set a project's or a kind
  of step's model and effort. **Insights** (from Settings) shows where the time
  and Claude usage go by kind of step, project and model, and the slowest and
  costliest steps. Each agent step's sheet has its transcript, or its live tail
  while it runs ("Checking on a job" in `docs/runbook.md`).
- **Reports about golem:** workers report what golem itself got in their way
  with (`golem report`: a wrong skill, a missing tool, a step that cost far too
  many turns), and golem files its own incidents (failed tries at a step, the
  loop guard, runs out of turns). **Reports** (from Settings, signed in) lists
  them. Turn **Triage** on there and golem, when it's idle, reads them with its
  own source and the jobs' logs and proposes fixes to itself, on Now and
  Reports: take one for a prompt to paste into a Claude Code session in golem's
  repo, or turn it down saying why. Golem never changes itself.

## Local mode

Needs Docker and [just](https://github.com/casey/just).

```sh
just bootstrap local     # writes local settings into .env; add CLAUDE_CODE_OAUTH_TOKEN by hand
just up
just hello-local         # deploys examples/hello -> http://hello.localhost:8800
just claude-check        # Claude Code headless in the golem image
just test                # both test suites
```

Web app: http://golem.localhost:8800. Set `HARNESS=fake` in `.env` to exercise the
bot without spending Claude usage: every agent step then answers "done", and
GitHub and deploys are pretended.

## On a server

See `docs/runbook.md`. golem takes ports 80 and 443 by default; on a box that
already runs a reverse proxy, see `docs/behind-a-proxy.md`.
