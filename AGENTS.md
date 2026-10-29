!! IMPORTANT !!
ALways attempt to accomplish your task with approved commands from local settings rather than using things that would prompt user approval to ensure no request for approval is required.

# Working on Golem

Golem turns ideas dictated into its web app into deployed web projects, built by
headless Claude Code workers. `BRIEF.md` is the full design; read it before
changing anything structural.

## Layout

| Path | What |
| --- | --- |
| `compose.yml` | The whole stack for a server: golem, deployer, traefik (owns 80/443 and TLS) |
| `compose.local.yml` | Local mode's override: plain HTTP on `EDGE_PORT` |
| `golem/` | Python service, Dockerfile and tests: bot, SQLite queue, scheduler, web app. Also the worker image. |
| `deployer/` | Python service, Dockerfile and tests. The only container with the Docker socket. |
| `traefik/` | Traefik static config: `server.yml`, and `local.yml` for local mode |
| `playbooks/`, `skills/`, `prompts/` | Process files baked read-only into the golem image |
| `bin/bootstrap.sh` | First-time setup on a fresh box |
| `docs/` | Runbooks |

## Rules

- **The deployer's validation is the security boundary.** Allow-list, never
  deny-list. Every rule has a fixture in `deployer/tests/fixtures/` and a test
  asserting its exact rejection reason. A new rule without a test isn't done.
- Never print, log or commit secrets. Secrets live in `.env` (git-ignored);
  document new variables in `.env.example`.
- golem runs on any box with a public IP and nothing else on ports 80 and
  443. It assumes nothing about the host beyond Docker, and touches only what it
  made: its own stack, the `golem-*` networks and the `golem-<slug>` projects.
- Workers run inside the golem container with no Docker access; the deployer
  is their only way to production. Process files (`playbooks/`, `skills/`,
  `prompts/`) are what workers follow: change them as carefully as code.
- Only Riggy can push to the golem repo. The web app's upgrades (and automatic ones)
  deploy whatever is on its `main` to the box with Docker access, so no token
  golem or a worker holds may ever write to it, and `GOLEM_REPO_TOKEN` (the
  deployer's alone) is read-only.
- Small steps, one commit each.

## Commands

Run `just` to list recipes. The common ones:

```sh
just test        # deployer and golem test suites
just up          # build and start the stack
just logs <svc>  # follow a service's logs
just down        # stop the stack (keeps volumes)
```
