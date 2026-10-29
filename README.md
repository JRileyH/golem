# golem

A self-hosted system that turns ideas dictated from a phone into deployed web
projects at `<slug>.<your domain>`, built by background Claude Code workers. It
runs on any box with Docker and a public IP (a VPS, a Raspberry Pi 5), x86 or ARM.

See `BRIEF.md` for the design and `AGENTS.md` for how to work on the repo.

## Using it

Everything happens in the web app at `https://golem.<your domain>`, made for a
phone.

- **The web app** has three tabs. Sign in under Settings with
  `GOLEM_WEB_PASSWORD` (tick "Keep me signed in" for 90 days on that device).
  - **Home:** what needs you, answered right there (try the site and say it
    looks good or what to change, pick option A, B or C, answer a question);
    what's being worked on, in words and dots, and when it's next live; what's
    up next; what finished lately; and a box for a new idea.
  - **Projects:** the conversations, like a messaging app: golem's own first
    (new ideas, and its news), then each project's, latest first. Each opens
    at its latest message, with the last 40 shown ("Show earlier messages" at
    the top brings back more); scrolled up, a ↓ button goes back to the latest. A project's
    page has its job as a card (where it is, what the worker's doing, and its
    pieces of work as a checklist: plan it, build the MVP, you try it, build the
    rest…; tap one for its details), then its whole conversation with golem and
    a box at the bottom to write in ("Options first" asks for options rather
    than leaving that to golem). Its **Settings** (top right) has mode, model,
    effort, pause, its job's place in the queue and stopping it, its jobs,
    rename, move and delete. Conversations are only shown signed in.
  - **Settings:** signing in and out, notifications, pausing all work, links to
    Insights and the public projects page, and the model and effort for each
    kind of step.
  - **Notifications:** in Settings, "Turn on for this device". You get what
    wants you: questions, check-ins, options to pick from, answers, "Done" and
    usage limits. On an iPhone, first add the site to the Home Screen (Share,
    then Add to Home Screen) and open it from there: iOS only lets sites notify
    that way. It's still the same site, not an app from the App Store. Golem
    makes its own push keys; there's nothing to configure.
- **golem's own conversation** (top of Projects; Home takes ideas too): dictate
  an idea ("a flashcard game for northeastern US trees"). golem names it, picks
  a slug, creates `<GITHUB_ORG>/<slug>`, and starts a conversation for that
  project. Its news (usage limits, finished jobs) shows here too.
- **How a job runs:** as a list of steps, written into golem's database from
  the mode's playbook when it starts (`playbooks/`). Agent steps (spec, mvp,
  review…) are Claude Code runs that each do one piece of work and commit it.
  Everything else is golem's own code, the same every time: pushing, opening a
  PR, waiting for CI, merging, deploying, the MVP check-in, and telling you it's
  done. Every playbook ends with deploy then alert, so "Done" means it's live.
  When CI fails or the deployer refuses a commit, golem adds a fix step and
  tries again; after four fixes it asks you for a hint.
- **A project's conversation:** progress shows up here as quiet notices; questions,
  the MVP check-in and "Done" arrive as messages that notify your phone.
  - Each agent step posts "✓ step: what was done" and what's next; so do PRs
    and deploys. Failures post "✗ …. Fixing it."
  - During an agent step, the worker posts a "▸" note at each milestone. After
    20 minutes without news, golem says what the worker is on ("Still on mvp
    (step 9/24), 45m in…"). A run that logs nothing for 30 minutes is stopped
    and retried.
  - When golem is waiting on you, your reply is the answer ("looks good",
    "make the photos bigger").
  - While it's working, what you say is passed to the next step of the job.
  - **Questions** ("why does the score reset?", "what's left on the spec?") or
    `!question <what>`: golem looks into it (the code, the docs, the live site,
    its logs) and answers in a few minutes, building nothing: no branch,
    commit, PR or deploy. A question goes ahead of other work, and can come in
    while a job is under way (it then looks at that job's work so far).
  - Otherwise, anything you say is a change request ("I don't like how the
    score works, count streaks instead"): it says when it starts, and again when
    it's live or if it needs an answer. A quick haiku call tells questions,
    options requests and changes apart, and golem says which it took; when
    it's not sure, it takes a question, the cheap mistake.
  - **Options first:** ask to see options ("I want a new art style, show me
    three that are more chibi") or say `!options <what>`, and the job makes them
    before building anything. The worker mocks each option up for
    real, with screenshots and the files behind them in `docs/options/` on the
    job's branch; golem posts them as pictures (A, B, C…) and waits. Reply with
    a letter and anything you'd change ("B, but bigger eyes"), or ask for other
    options for another round. The next step builds your pick from its files.
- **golem status:** usage-limit notices and finished jobs.
- **Settings,** per project, shown when it's created (work starts a minute later,
  so there's time to change them). Say them while dictating ("…called Leaf
  Peeper, rigorous, opus, max effort") or change them later in its conversation
  (or its Settings):
  - `!mode reckless|standard|rigorous`: one quick pass; spec, MVP check-in,
    review; or a reviewed spec, thorough tests and two reviews.
  - `!model fable|opus|sonnet|haiku|default` and
    `!effort low|medium|high|xhigh|max|default`: for all the project's agent
    steps, from the next one; `default` goes back to each step's default.

  The model and effort are chosen per step, the most specific choice winning:
  one step's (set in its sheet in the web app), then the project's, then
  the default for that kind of step (set in the web app's Settings), then
  `GOLEM_MODEL` and `GOLEM_EFFORT` in `.env`. Golem starts each kind of step
  on its own suggestion (`SUGGESTED` in `golem/plan.py`): opus at xhigh for
  spec, mvp and review, sonnet for scaffold, second-pass and docs, opus at high
  for the rest. It fills them in once, where you haven't set one, so your
  changes on Insights stay; each kind shows its suggestion while yours differs,
  and "Reset to golem's suggestions" puts them all back (projects' and single
  steps' own choices stay). The default mode is
  `GOLEM_DEFAULT_MODE`.
- **Commands,** in any conversation (in a project's they apply to that project;
  in golem's own put its slug first): `!help`, `!queue`, `!status`, `!question`, `!options`, `!mode`,
  `!model`, `!effort`, `!rename <name>`, `!slug <new-slug>` (moves the repo and
  address; the old repo stays for you to delete), `!pause`, `!resume`, `!kill`.
- **Deleting a project:** `!delete` in its conversation says what will go, then
  `!delete <slug>` takes it all: the site and its data, the checkout, the job
  history and logs, and the conversation. The repo stays on GitHub for you to delete
  (golem's token can't). To delete every project at once, run `bin/nuke.sh`
  on the box ("Starting over" in `docs/runbook.md`).
- **Projects page:** `https://projects.<your domain>`, public: a card for each
  live project with a screenshot, its tag, a tagline, a description and a
  button that says what you'll do there (Play, Open, Use, Visit). Cards are
  written by a quick haiku call from the project's README and brief, with a
  screenshot of the live site, when a project first goes live and again at the
  end of every job. Projects live before this get theirs when golem starts.
- **Tags:** a few big-picture sorts of project, each in one color on the
  projects page, which filters by them. golem starts with "Game". Triage
  tags each new idea with one of them or none, and adds a new tag only for a
  broad sort of project none of them covers ("Dev Tool", never "Gardening").
  Settings lists them to add, rename, recolor or delete; a project's settings
  page changes its tag.
- **Who sees what:** anyone can see what's under way and each step's details;
  conversations and every control need signing in. Signed in, you can skip
  optional steps (reviews, docs, the MVP check-in, your pick) or undo that,
  pick an option, give one upcoming step its own model, effort or context, run
  a job next or last, pause a project or all work, stop a job, and set a
  project's or a kind of step's model and effort. **Insights** (from Settings)
  shows where the time and Claude usage go by kind of step, project and model,
  and the slowest and costliest steps. Each agent step's sheet has its
  transcript, or its live tail while it runs ("Checking on a job" in
  `docs/runbook.md`).

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
