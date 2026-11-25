# Golem — build brief for Claude Code

You are Claude Code, working with Riggy (full-stack engineer) to build **golem**: a self-hosted system that turns ideas dictated from his phone into deployed web projects, built by background agents. This brief is the complete design, settled over several planning sessions. Read all of it before writing any code.

**How we work on this repo.** golem is built hands-on, the way Riggy normally works: he reviews changes and handles deploys.

- Start by restating the Phase 1 plan in your own words and listing anything you'd verify first. Wait for his go.
- Work in small, reviewable steps, each ending in a commit with a clear message.
- Never touch the server without an explicit OK for that specific action. When something must run there, give Riggy the exact commands, or run them over SSH only after he says yes.
- Never print, log or commit secrets. Use `.env` files that are git-ignored, with a `.env.example` beside them.
- The deployer's validation is the security boundary of the whole system. Treat it like production code: small, readable, thoroughly tested.
- If something in this brief turns out to be wrong once you see real files, say so and propose a fix rather than working around it silently.

## What we're building

Riggy acts as product manager. He dictates an idea into golem's web app on his phone ("a flashcard game for identifying northeastern US trees"). golem queues it, and Claude Code workers spec it, build it, review it, push it to GitHub and deploy it to `<slug>.<domain>`, working for hours without him. They check in only at moments that matter (an MVP worth looking at, a genuine ambiguity, something irreversible) and otherwise decide, log the decision and move on. Later he can say "trees needs a quiz mode" and the feature ships to production without further input.

This runs alongside his hands-on workflow, not instead of it. Projects built here are personal tools: public, disposable data, no customers.

**Golem's process is its code.** How a request is worked through is a **workflow**: blocks wired output to input, from Start to End, kept as files in golem's repo (`workflows/`: the way it builds web apps and games, changes them, asks, adopts and proposes). They're nearly straight lines of stage blocks (plan, build, review, docs…), with golem's own loops where something can fail or Riggy picks: shipping retries with a fix, a pick can ask for another round. Changing one is changing golem: edit the file, run the tests, upgrade. What Riggy can change from the web app is what each stage block tells its agent. Every request is read for which workflow it's for, by what each says it's for. (Golem had a visual workflow editor for a while; nearly every workflow stayed a straight line, so it went.)

## The environment

**The server.** Any box with Docker and a public IP, x86 or ARM: a VPS, a Raspberry Pi 5. RAM is usually the scarce resource. golem touches nothing on the box it didn't make. By default it owns ports 80 and 443. On a box that already has a reverse proxy, `EDGE_PORT` puts it behind that proxy instead: plain HTTP on that port, and the proxy does HTTPS (`docs/behind-a-proxy.md`). Nothing about the host is assumed either way.

**DNS.** A wildcard record for `*.<domain>` points at the box (or the proxy in front of it), so new subdomains need no DNS changes. Any DNS provider: golem's certificates use Let's Encrypt's HTTP challenge, one per host. Behind a proxy, the proxy holds a wildcard certificate instead.

**GitHub.** An org (`GITHUB_ORG`, free plan is fine) holds the agent-built project repos. The golem repo itself lives elsewhere (Riggy's personal account), so tokens scoped to the org can't reach it. Free orgs get no branch protection on private repos and 2,000 Actions minutes a month.

**Riggy's laptop.** Used for local mode.

**Claude.** Riggy has a Claude subscription (moving to Max). Workers use the official Claude Code CLI headless, signed in with that subscription.

## Fixed decisions

These were argued through already. Don't reopen them without a concrete reason found in real files.

| Area | Decision |
| --- | --- |
| Harness | Official Claude Code CLI, headless, on Riggy's subscription. Third-party harnesses (OpenCode etc.) may not use Pro/Max subscriptions under Anthropic's terms. |
| Model-agnostic | Harnesses sit behind a small adapter interface so another CLI (e.g. Codex) can be added later. Not now. |
| Language | golem and the deployer are Python. |
| Chat | golem's own web app at `golem.<domain>`, installed to the phone's home screen, with Web Push notifications. |
| Packaging | One repo, one compose file: golem, deployer, Traefik. Runs on any box with Docker and a public IP. |
| Sandbox | The golem container is the sandbox. Workers run inside it. Only the deployer touches Docker. |
| Routing | One Traefik, golem's own, routing only from files the deployer writes: on 80/443 with a certificate per host, or on `EDGE_PORT` behind the box's own proxy. |
| Projects | Each project is its own private repo in the `GITHUB_ORG` org, its own compose stack, its own data. Agent picks the stack. Public at `<slug>.<domain>`, its image built for the server's architecture. Project data is disposable. |
| Process files | Golem's built-in workflows, skills and prompts live in the golem repo and are baked read-only into the golem image; agents can't edit them. Golem reads its workflows from those files; Riggy changes the text each stage block tells its agent in the web app (Settings → Blocks), kept in golem's database and signed, so text changed outside the web app isn't used. |
| Learning | Agents append to a writable lessons file that feeds every prompt, and report what golem itself got wrong (`golem report`); golem triages the reports into fixes for Riggy to make in golem's repo. |
| Scheduling | Priority queue, one worker by default (more in Settings). No work windows; a per-project pause instead. |
| Status | Each project's conversation in the web app, GitHub issues/PRs on an org Projects board, a status page at `golem.<domain>` (public), and markdown docs in each repo. |
| Budget | Never buy extra usage. On a usage-limit error, park the job and retry after the window resets. |

## Golem stack architecture

```
Internet → traefik (owns 80/443; :80 redirects to HTTPS)
   ├─ golem.<domain>, projects.<domain> → golem
   └─ <slug>.<domain> → project stacks
```

With `EDGE_PORT` set, the box's own proxy owns 80/443 and HTTPS and forwards `*.<domain>` to traefik's plain-HTTP port (`compose.http.yml`, `traefik/http.yml`). Everything behind traefik is the same. A proxy in Docker reaches it through `golem-edge`, a network with only traefik on it.

| Service | Role | Networks |
| --- | --- | --- |
| `golem` | Bot, queue, scheduler, web app. Runs Claude Code workers as subprocesses, with git, gh and per-project toolchains. One memory cap for all agent work. | `golem-internal` |
| `deployer` | The only container with the Docker socket. Small HTTP API on the internal network. | `golem-internal` |
| `traefik` | Routes golem's hosts and agent projects, with a Let's Encrypt certificate per host (HTTP challenge). **File provider only; no Docker socket.** | `golem-internal`, `golem-apps` |

**Why Traefik reads files, not Docker labels.** A proxy that read labels would let an agent's compose file claim any hostname. Instead:

- Agent compose files contain **no `traefik.*` labels at all**; the deployer rejects any.
- The deployer adds `traefik.enable=false` to every project container, so any other Traefik on the box that reads labels ignores them too.
- For each deployed project, the deployer writes one dynamic config file (`<slug>.yml`) into a volume Traefik watches, routing `<slug>.<domain>` to the project's public service.

Routing is then derived from the slug alone, and agents have no way to claim a hostname. Routes name no entrypoint and get Traefik's defaults: HTTPS, and an internal entrypoint (`:8080`, not published) the deployer checks sites through.

**Repo layout:**

```text
golem/
  compose.yml          the stack for a server
  compose.http.yml     override when EDGE_PORT is set: plain HTTP (own proxy, local mode)
  .env.example         base domain, harness and GitHub tokens
  AGENTS.md            how to work on this repo (CLAUDE.md points here)
  golem/              Python service + Dockerfile (also the worker image)
  deployer/            Python service + Dockerfile + tests
  traefik/             static config (server.yml, http.yml); dynamic/ is a volume
  workflows/           golem's built-in workflows: web_app, change, options, design, question, proposals, adopt, ship…
  skills/              deploy.md, new-repo.md, spec-writing.md, review.md
  prompts/             summaries, request and reply calls, phase wrappers
  bin/bootstrap.sh     first-time setup on a fresh box
```

**Volumes:** golem state (SQLite, logs, `lessons.md`), Claude Code credentials, agent workspace (a checkout per project, and a git worktree of it per job under way in `.jobs/`), toolchain cache (uv and npm), Traefik's routes and certificates. None of them need moving to another box: a fresh install with the same `.env` starts empty and takes nothing from the org on its own, and `!adopt` brings back each project Riggy wants, with its past jobs read from golem's issues and PRs on its repo (`history.py`).

## Deployer contract

The deployer is the only door from agents to Docker. Assume anything calling it may be a confused or overeager agent: workers run in the same container as golem and could reach its API directly. So the deployer trusts nothing in a request beyond the slug and commit, and enforces every rule itself.

**API** (internal network only, shared-secret header):

| Call | Does |
| --- | --- |
| `POST /deploy {slug, sha}` | Fetch `deploy/compose.yml` from `<org>/<slug>` at that commit, check it, validate it, deploy it, write the route, verify the URL responds |
| `POST /validate {slug, sha}` | The same fetch and validation, and nothing else: no checks, no Docker, no route. For golem to vet a repo before adopting it |
| `POST /stop {slug}` | `compose down` keeping volumes, remove the route |
| `POST /delete {slug}` | `compose down` with volumes, remove the route and deploy files |
| `GET /projects` | Every project with deploy files, running or stopped |
| `GET /status?slug=` | Running state, deployed commit, route |
| `GET /logs?slug=&service=&tail=` | Container logs, for agents debugging failed deploys |
| `GET /version` | golem's own version: the commit running, what GitHub's `main` has (`?fetch=1`), the last upgrade |
| `POST /upgrade` | Admin secret only. Fast-forward golem's checkout to GitHub's `main`, bootstrap, rebuild and restart the stack from a helper container, roll back if it isn't healthy |

Data is deleted only when Riggy asks (deleting a project in the web app, `bin/nuke.sh`): `/delete` needs a second secret that golem holds and workers never get. `/upgrade` needs it too: Riggy presses "Upgrade now" in the web app, or turns on automatic upgrades. The deployer mounts golem's checkout at its host path for it, and reads the golem repo with its own read-only token, which nothing else gets.

**Deploys come from pushed commits only.** The deployer fetches the compose file from GitHub itself with a read-only org token, never from the agent workspace, so nothing uncommitted can reach production. It also requires every check run on that commit to have passed. A project with fewer checks (at minimum, the image build) simply passes sooner, so no mode flag is needed or trusted.

**Validation: allow-list, not deny-list.** Reject anything not explicitly allowed, with a reason the agent can act on.

- Slug matches `^[a-z0-9][a-z0-9-]{1,30}$` and isn't reserved (`golem`, `www`, `status`, `inbox`, `traefik`, `deployer`, `api`, `mail`).
- Images come from `ghcr.io/<org>/` or a short allow-list of official images (postgres, redis, nginx and the like). No `build:`.
- Volumes are named only, with no `driver_opts` (a local driver with bind options is a disguised bind mount). No bind mounts, no external volumes or networks.
- Forbidden keys: `privileged`, `cap_add`, `devices`, `network_mode`, `pid`, `ipc`, `userns_mode`, `security_opt`, `sysctls`, `ports`, `container_name`, `env_file`, `extends`, `include`, file-based `secrets`/`configs`, and any `traefik.*` label.
- Every service sets a memory limit under a per-service cap; the project total stays under a per-project cap (start at 512 MB and 1 GB, configurable).
- Exactly one service carries `golem.public=true` and `golem.port=<port>`.

**How it deploys.** Compose project name `golem-<slug>`. The deployer generates an override file that attaches the public service to `golem-apps` with network alias `<slug>`, adds `traefik.enable=false` and `golem.slug` labels, and sets log rotation. It runs `pull` then `up -d --remove-orphans`, writes `dynamic/<slug>.yml` (route `<slug>.<domain>` → `http://<slug>:<port>`), then requests the site through Traefik's internal entrypoint and returns success, or failure with the relevant logs.

**Audit.** Every request, decision and rejection reason goes to an append-only JSON-lines log.

## Golem runtime behavior

Reference for Phase 2 onward; not needed to finish Phase 1, but Phase 1 shouldn't make any of it harder.

**Conversations,** in the web app.

The web app is built around jobs, not conversations. **Now** has every job across projects, what needs Riggy first (answered there), and golem's news (usage limits, upgrades, finished jobs). **Projects** opens each project's page: its jobs, each drawn by its route through its workflow, beside the one being looked at (its steps and activity). Riggy makes a project on the **New project** form: he names it and says what it's for, picks its workflow and tag (and a mode, once golem has any), and golem only writes its summary and the first job's title (a cheap call). On a project's page, **Ask for…** always starts a job of its own: a question (a quick read-only look and an answer, no build), a change, options first (a worker mocks up A, B, C with screenshots, golem shows them and waits, and his pick is what gets built), or a design pass (a doc with a few directions for the project's look, his pick built and written down as `docs/design.md`, which later work follows). The workflow is his pick, or else a cheap request call's, by what each is for. Each job has its own activity: what each block did, notes for its next step, questions and his replies; push notifications open the job and its block. Every change is a control (pause, stop, run next or last, mode, model, effort, rename, move, delete): there are no chat commands.

**Adopting a repo golem didn't make.** The New project page also takes on a repo in the org (its name, what it is, its tag), and from then on it's a project like any golem built: its jobs, changes, questions, proposals, deploys and card, and workers own its code. Its brief comes from what Riggy said, or else its README and description (a cheap call). Its job's first step, `vet`, checks it over before anything touches the repo: its CI, workflow, `deploy/compose.yml` by the deployer's own rules (`/validate`), the repo's shape, and a survey (a worker run that changes nothing) that builds and tests it in golem's container and says what it needs that golem can't give. Each check passes, is fixable (a worker can change it in the repo) or blocks (only Riggy can change it: a secret, a GitHub setting, a package in golem's image). Something blocking and golem leaves it alone and says what to change; otherwise an `adopt` step fixes the gaps (golem writes a missing `docs/brief.md` itself), then golem's usual PR, checks, merge, deploy and alert. A repo golem worked on before (after it's deleted, or on a new box) gets its past jobs back from golem's issues and PRs on it, under new numbers; job numbers mean nothing outside golem's database, so the adoption first closes PRs an earlier golem left open on its `golem/job-*` branches. This is how a project comes back: nothing does on its own.

**Jobs walk workflows, driven by code.** A job (new project, feature, bug fix, question) runs a workflow (`workflows/README.md`): a graph of blocks, each with one way in and named outputs wired to the next. When the job starts it takes its own flattened copy (containers opened in place, each stage block's text with it), so an upgrade that changes the workflow never touches work under way; the copy stays with it once it's over, as its record. Golem walks the copy by the output each block leaves by, and nothing else decides what comes next. Agent blocks are headless Claude Code runs with a turn cap that do one piece of work on the job's branch and commit it, ending with a JSON verdict: `done` (or, for a block with several outputs, the one it chooses) or `blocked` (a question for Riggy, after which the same session carries on). Golem's own blocks are its code: setup (repo, first commit, issue), push, PR, CI and deploy (which pass or fail), merge, asking Riggy, his pick between options, the project's tests (pass on exit 0), counters, conditions and End (telling him it's done). Golem's tests hold every workflow it has to its guarantees: every way through ends, a job that changes a project starts with setup, and one that only looks never pushes. In golem's built-ins, CI failing or the deployer refusing a commit goes to a fix and back to the push (`ship.yml`); after four fixes Riggy is asked for a hint. A loop guard asks him before a job goes round too often. Each visit to a block is saved, so a crash or usage limit costs at most one step. States: queued → running → waiting (on Riggy) or parked (on CI, a limit or a retry) → done, with killed as a side exit. A job waiting on Riggy or CI frees its worker slot. A step that fails tries again after 1, 5, then 15 minutes, its slot free meanwhile, before golem asks Riggy.

**Parallel workers.** Settings says how many steps run at once across projects (1 to 8, one by default). Each job works in a git worktree of its own (`/workspace/.jobs/<job>`, on its branch; the project's checkout stays on main), so a question looking at a change's work can't touch its files. Workers beside each other share golem's one memory cap (`GOLEM_MEM_LIMIT`; the setting shows what's left each) and the machine, so each gets its own ports (`GOLEM_PORT` to `+9`) and `TMPDIR`, and golem stops whatever a run leaves running when it ends. An upgrade waits for every running step. A spare worker can look for proposals while other projects are worked on.

**Changes at once.** Each project's settings say how many changes can be under way on it (1 to 4, one by default). With one, a project's steps run one at a time and a change waits for the one under way. With more, its steps run side by side and changes take turns only where they'd step on each other (`turns.py`): one at a time builds (scaffold, mvp, buildout, change, guide, apply), and one at a time ships (push to deploy); setting up, specs, options and design rounds, review, docs and waiting overlap. A change holds a turn while that run of its steps is under way: waiting on Riggy lets a build turn go but keeps a ship turn, and turns are read from the steps, so a restart loses none. A change wants only the turn its current step needs and holds only that one, so turns can't deadlock (a fix after failed CI is part of shipping). Changes ready to ship go out together (`shipments.py`): one that's ready waits while another under way is still on its way to shipping (but not while that one waits on Riggy), then the oldest ready carries them all. Its catch-up with main merges the others' branches in, its push, PR, CI, merge and deploy ship every one, and the others' ship steps go by as done. A change asked for before a shipment starts joins it; one asked for after waits for the next. A shipment carries at most as many changes as the project allows at once, so none waits forever; stopping its carrier drops it, and the others go in the next. Before a change builds and before it ships, golem catches its branch up with main (`sync`); a conflict puts in a `resolve` step, where a worker merges main in keeping both changes' intent, and after three Riggy is asked for a hint. A change beyond as many as the project allows waits for one under way to finish (`turns.behind`). New projects and adoptions run alone, and so does anything from before trees.

**Worker adapter.** `run(project_dir, prompt, model, max_turns)`, `resume(session_id, prompt)`, `parse_result()` (verdict + usage), `detect_limit()`. The Claude Code adapter runs `claude -p` with streamed JSON output and pre-approved permissions (nobody is there to click allow). A `fake` adapter returns scripted verdicts for testing.

**Modes.** Ways of working a job can be in, golem's own (`plan.MODES`; none yet). A job's mode is Riggy's pick, on the New project form or in the Ask box, else its project's default (only he sets it), else the usual way. Golem once shipped reckless and rigorous modes, each a whole copy of every workflow; they went with the editor.

New project flow: intake → spec → scaffold and first deploy (proves hosting early) → build to MVP → check-in → design pass (directions → his pick → `docs/design.md` and assets → the MVP restyled and deployed) → build out → review → deploy → docs and retro. Feature flow: read the project's `AGENTS.md` and docs → mini-spec → build → review → deploy → update docs; no check-ins unless genuinely ambiguous. A fresh-context reviewer gets only the spec, the diff and `AGENTS.md`.

**Check-ins.** Blocking only for: MVP reached, ambiguity that changes what gets built, something irreversible, budget 2–3x over the expected range, or stuck after retries. Never for naming, library choice and similar; those go in `docs/decisions.md`. The look is his to pick, in the design pass. Unanswered check-ins cost nothing; a reminder goes to #status after a day.

**Proposals.** Golem's own ideas, so not all input comes from Riggy. Each project's settings say how often (off by default; hourly, daily or weekly). When nothing is queued, running or parked, a read-only job looks a live project over through one lens at a time (features, code health, user experience, security, performance, tests, polish, reliability, accessibility, developer experience, in turn) and proposes the one improvement it thinks best: one a turn, so Riggy gets a steady trickle to decide on, like a daily note, rather than a pile. The settings list each lens as a kind of proposal with what it asks the worker to look at; all are on until Riggy unticks one, and the turn skips those. They're posted in its conversation and on its page, where Riggy gives each a thumbs up (it becomes a change job once the project is free) or down, with why if he likes. Each is also an issue on the project's repo, as a record: the decision is commented there, a thumbs down closes it and the change's PR closes an accepted one. Golem never reads anything back from GitHub. Every earlier proposal and his reasons go to the next run, to avoid repeats and learn his taste without copying it. While a project has as many undecided proposals as its settings allow (five by default), its turns pass: deciding on one brings the next at the following turn, not straight away, and turns missed while he's away never pile up.

**Reports about golem.** Golem's main users are its own workers, and what costs them most often goes unnoticed by any person. So every worker prompt carries a short standing order (`prompts/report.md`): when golem itself (its instructions, tools, environment or flow, not the project) blocked a worker, cost it real time or turns, or told it something wrong, it says so with `golem report KIND IMPACT "summary"`, at most two a step. The `golem` command (`cli.py`) appends to a file golem reads in after the run, adding where it came from. Golem files its own incidents too, spending nothing: each failed try at a step (with all it printed, kept in the step's log), the loop guard stopping a job, merges with main that keep conflicting, runs out of turns; the same trouble anywhere counts up on one report. The Reports page lists them. Golem's triage (off until Riggy turns it on) reads them when it's idle and they're due, with golem's installed source, process files and the jobs' logs, and proposes up to three fixes to golem, each citing its reports. Riggy takes one (and gets a prompt to paste into a Claude Code session in golem's repo) or turns it down with why, which later triage sees. Golem never changes itself: no token it holds can write to its repo. Triage runs on golem's own project (`golem`, reserved and hidden): every job, run and proposal needs one.

**Every project repo:** `AGENTS.md` (stack, run, test, deploy, gotchas; `CLAUDE.md` points to it), `README.md` with the live URL, `docs/brief.md` (original dictation + brief), `docs/specs/`, `docs/decisions.md`, `docs/lessons.md`, `deploy/compose.yml`, and a GitHub Actions workflow that builds and pushes the image (cache dependencies to stay inside 2,000 minutes a month). One issue per job and a PR per change, on the org Projects board.

**Budget.** Background work, Riggy's hands-on Claude Code use and his chats share one pool. Keep a reserve so he's never locked out. One worker by default, two only when the queue is deep and usage is low. Small model for triage and mechanical steps, mid model for building, strongest for specs and reviews. Record token usage per run; after a few projects, each kind of job gets an expected range.

**Observability.** Every job's steps in the web app, as a checklist of its pieces of work with what's coming up when, with links to what each made; time and usage by kind of step, project and model, to find where to tune prompts, models and effort; every run's event stream saved and compressed per job, readable while it runs (each step's live tail); a hook logs every tool call (time, project, job, command or file) to an append-only log; job events in SQLite; git history. The status page links from each job to its logs. Recurring mistakes go to `lessons.md`.

## Local mode

The same compose file runs on a laptop, with `compose.http.yml` layered over it (as behind a proxy, with nothing in front) and a local env file.

| Setting | Server | Local |
| --- | --- | --- |
| Base domain | yours | `localhost` (`anything.localhost` resolves with no DNS setup) |
| Traefik | 80/443, Let's Encrypt | plain HTTP on `EDGE_PORT` |
| Harness | `claude` | `claude` or `fake` |
| Git source for the deployer | the GitHub org | a local bare git repo |
| Checks before deploy | required | off |

The phone can't reach `localhost`, so push notifications are tested on a server. Images built locally only run locally; GitHub Actions builds project images for the server's architecture, which golem detects and hands workers as `GOLEM_PLATFORM`.

## Phase 1, foundations (done)

Goal: the golem stack runs, serves its hosts over HTTPS, and can put a project live through the deployer. The golem service itself is only a skeleton in this phase; its behavior is Phase 2.

**Deliverables**

1. **Repo skeleton** per the layout above: `AGENTS.md`, `README.md`, `.env.example`, `.gitignore`, and a `justfile` or `Makefile` for common commands.
2. **`compose.yml`** with all three services, the `golem-internal` and `golem-apps` networks and memory limits on everything, and `compose.local.yml` for local mode.
3. **Traefik:** static config (HTTPS with a Let's Encrypt certificate per host, file provider watching the dynamic volume), and a plain-HTTP one for local mode.
4. **Golem image skeleton:** Python, Node, git, gh and the Claude Code CLI installed; a placeholder entrypoint with a health endpoint. Prove Claude Code runs headless inside it on Riggy's subscription.
5. **Deployer v0:** the API, validation, override generation, route files and audit log from the contract above. A test suite with one fixture per rule (a valid compose file, plus one bad file per forbidden thing) asserting the exact rejection reason.
6. **A `hello` project:** the smallest possible static site with a GitHub Actions workflow that builds and pushes its image. Locally it deploys from a bare git repo; on a server from `<org>/hello`.
7. **`bin/bootstrap.sh` and a server runbook:** the exact commands Riggy runs to bring the stack up on a box, and how to remove it.

**Done when**

- Locally, `hello.localhost` is served after a deploy request to the deployer, and every validator fixture is rejected with the right reason.
- On a server, `hello.<domain>` is served with a valid certificate, deployed through the deployer from a commit with passing checks.
- Riggy opens `golem.<domain>` from his phone's home screen and gets a push notification from golem.

## Later phases

| Phase | Scope | Gate |
| --- | --- | --- |
| 2. golem MVP | Chat bot, SQLite queue and registry, Claude Code and fake adapters, phase verdicts, standard playbook, intake brief and MVP check-in | A dictated toy idea reaches a live URL without Riggy opening a terminal |
| 3. Shakedown | 3–5 small real projects in standard mode; tune playbook, deploy skill and check-ins; audit hook, session logs, status page | Two projects shipped with no hands-on fixes |
| 4. Modes, features, budget | Reckless and rigorous playbooks, feature flow for existing projects, budget envelopes, pause on usage limit | A feature request to an existing project ships unattended |
| 5. Ongoing | Lessons file and drafted playbook edits; a supervised mode that ends at an opened PR (for projects Riggy also works on by hand); a second harness if wanted | — |

## Verify first, and what only Riggy can do

**Check these before building on them; tell Riggy if any differ from this brief.**

- [ ] The current, sanctioned way to authenticate Claude Code headless inside a container with a subscription, from Anthropic's Claude Code docs.
- [ ] Where you're running: Riggy's laptop or the server, and which Docker runtime it uses.

The host is Riggy's: don't change its Docker daemon config, packages or other containers.

**Only Riggy can:**

- Create the `golem` repo in his personal account (or authorize `gh` to).
- Create the GitHub tokens for the org: read-only ones for the deployer, and the workers' token (create repos, write contents, issues and PRs). See `docs/runbook.md`.
- Sign in to Claude Code inside the golem container.
- Add the web app to his phone's home screen and confirm push notifications.
- Approve every action on the server.
