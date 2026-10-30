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
| Process files | Playbooks, skills and prompts live in the golem repo and are baked read-only into the golem image. Agents can't edit them. |
| Learning | Agents append to a writable lessons file that feeds every prompt, and draft playbook edits for Riggy to apply by hand. |
| Scheduling | Priority queue, one worker by default. No work windows; a per-project pause instead. |
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
  playbooks/           reckless.md, standard.md, rigorous.md, feature.md
  skills/              deploy.md, new-repo.md, spec-writing.md, review.md
  prompts/             intake triage, reply triage, phase wrappers
  bin/bootstrap.sh     first-time setup on a fresh box
```

**Volumes:** golem state (SQLite, logs, `lessons.md`, `proposals/`), Claude Code credentials, agent workspace (repo checkouts), toolchain cache, Traefik's routes and certificates. None of them need moving to another box: a fresh install with the same `.env` rebuilds its projects and their jobs from the org's repos, issues and PRs, and deploys them again (`recover.py`). On a new org it starts with nothing.

## Deployer contract

The deployer is the only door from agents to Docker. Assume anything calling it may be a confused or overeager agent: workers run in the same container as golem and could reach its API directly. So the deployer trusts nothing in a request beyond the slug and commit, and enforces every rule itself.

**API** (internal network only, shared-secret header):

| Call | Does |
| --- | --- |
| `POST /deploy {slug, sha}` | Fetch `deploy/compose.yml` from `<org>/<slug>` at that commit, check it, validate it, deploy it, write the route, verify the URL responds |
| `POST /stop {slug}` | `compose down` keeping volumes, remove the route |
| `POST /delete {slug}` | `compose down` with volumes, remove the route and deploy files |
| `GET /projects` | Every project with deploy files, running or stopped |
| `GET /status?slug=` | Running state, deployed commit, route |
| `GET /logs?slug=&service=&tail=` | Container logs, for agents debugging failed deploys |
| `GET /version` | golem's own version: the commit running, what GitHub's `main` has (`?fetch=1`), the last upgrade |
| `POST /upgrade` | Admin secret only. Fast-forward golem's checkout to GitHub's `main`, bootstrap, rebuild and restart the stack from a helper container, roll back if it isn't healthy |

Data is deleted only when Riggy asks (`!delete`, `bin/nuke.sh`): `/delete` needs a second secret that golem holds and workers never get. `/upgrade` needs it too: Riggy presses "Upgrade now" in the web app, or turns on automatic upgrades. The deployer mounts golem's checkout at its host path for it, and reads the golem repo with its own read-only token, which nothing else gets.

**Deploys come from pushed commits only.** The deployer fetches the compose file from GitHub itself with a read-only org token, never from the agent workspace, so nothing uncommitted can reach production. It also requires every check run on that commit to have passed. Reckless-mode projects simply have fewer checks (at minimum, the image build), so no mode flag is needed or trusted.

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

| Conversation | Purpose |
| --- | --- |
| golem's own | Riggy dictates new ideas and cross-project commands; golem's news: budget warnings, queue changes, finished jobs |
| One per project | Check-ins, questions, Riggy's replies, feature requests |

Free text in golem's own conversation becomes a job: a cheap triage call turns dictation into a brief (name, slug, mode, priority, summary). Free text in a project's conversation is feedback, a question (a quick read-only look and an answer, no build), or a feature request, or a request to see options first (a worker mocks up A, B, C with screenshots, golem posts them and waits, and his pick is what gets built); replies like "looks good", "make it a quiz" or "shelve it" map to continue, revise or pause. Commands: `!queue`, `!status <project>`, `!pause` / `!resume` (scheduler), `!pause <project>` / `!resume <project>` (lock a project while Riggy tinkers), `!mode <project> <mode>`, `!priority`, `!kill <job>`, `!archive` / `!revive <project>`. Every message the bot sends links to something (preview URL, PR, status page).

**Jobs run as steps, driven by code.** A job (new project, feature, bug fix) is a list of steps written into SQLite from its playbook when it starts; golem works through them in order and nothing else decides what comes next. Agent steps are headless Claude Code runs with a turn cap that do one piece of work on the job's branch and commit it, ending with a JSON verdict: `done` or `blocked` (a question for Riggy). Every other step is golem's own code: setup (repo, first commit, issue), push, PR, CI, merge, deploy, the MVP check-in and the final alert. Every playbook starts with setup and ends with deploy and alert, so a finished job is a deployed one. When CI fails or the deployer refuses a commit, golem inserts a fix step and repeats the steps from the last push. Each step's outcome is saved, so a crash or usage limit costs at most one step. States: queued → running → waiting (on Riggy) or parked (on CI, a limit or a retry) → done, with killed as a side exit. A job waiting on Riggy or CI frees its worker slot.

**Worker adapter.** `run(project_dir, prompt, model, max_turns)`, `resume(session_id, prompt)`, `parse_result()` (verdict + usage), `detect_limit()`. The Claude Code adapter runs `claude -p` with streamed JSON output and pre-approved permissions (nobody is there to click allow). A `fake` adapter returns scripted verdicts for testing.

**Modes** (default standard; stored per project, inherited by later features):

| Phase | Reckless | Standard | Rigorous |
| --- | --- | --- | --- |
| Intake brief | Posted, non-blocking | Posted, non-blocking | Posted, non-blocking |
| Spec | Paragraph in README | `docs/specs/` | Spec + fresh-context spec review |
| MVP check-in | None | Blocking | Blocking |
| Tests | None beyond the image build | Core paths | Thorough |
| Code review | None | One fresh-context review | Review + second pass on fixes |
| Deploy | Straight from main | PR, checks, merge | PR, checks, preview smoke test, merge |
| Retro | Skipped | Lessons | Lessons + proposed playbook edits |

New project flow: intake → spec → scaffold and first deploy (proves hosting early) → build to MVP → check-in → build out → review → deploy → docs and retro. Feature flow: read the project's `AGENTS.md` and docs → mini-spec → build → review per mode → deploy → update docs; no check-ins unless genuinely ambiguous. A fresh-context reviewer gets only the spec, the diff and `AGENTS.md`.

**Check-ins.** Blocking only for: MVP reached (standard/rigorous), ambiguity that changes what gets built, something irreversible, budget 2–3x over the mode's expected range, or stuck after retries. Never for colors, naming, library choice and similar; those go in `docs/decisions.md`. Unanswered check-ins cost nothing; a reminder goes to #status after a day.

**Proposals.** Golem's own ideas, so not all input comes from Riggy. Each project's settings say how often (off by default; hourly, daily or weekly). When nothing is queued, running or parked, a read-only job looks a live project over through one lens at a time (features, code health, user experience, security, performance, tests, polish, reliability, accessibility, developer experience, in turn) and proposes up to three improvements. The settings list each lens as a kind of proposal with what it asks the worker to look at; all are on until Riggy unticks one, and the turn skips those. They're posted in its conversation and on its page, where Riggy gives each a thumbs up (it becomes a change job once the project is free) or down, with why if he likes. Each is also an issue on the project's repo, as a record: the decision is commented there, a thumbs down closes it and the change's PR closes an accepted one. Golem never reads anything back from GitHub. Every earlier proposal and his reasons go to the next run, to avoid repeats and learn his taste without copying it. A project with as many undecided proposals as its settings allow (five by default) gets no more until he decides.

**Every project repo:** `AGENTS.md` (stack, run, test, deploy, gotchas; `CLAUDE.md` points to it), `README.md` with the live URL, `docs/brief.md` (original dictation + brief), `docs/specs/`, `docs/decisions.md`, `docs/lessons.md`, `deploy/compose.yml`, and a GitHub Actions workflow that builds and pushes the image (cache dependencies to stay inside 2,000 minutes a month). One issue per job and a PR per change, on the org Projects board.

**Budget.** Background work, Riggy's hands-on Claude Code use and his chats share one pool. Keep a reserve so he's never locked out. One worker by default, two only when the queue is deep and usage is low. Small model for triage and mechanical steps, mid model for building, strongest only for specs and reviews in rigorous mode. Record token usage per run; after a few projects, each mode gets an expected range.

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
