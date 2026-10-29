# Deploying a project

A project goes live at `$GOLEM_URL` through the **deployer**, a
separate service that fetches `deploy/compose.yml` from GitHub at a commit, checks
it against strict rules, and runs it. You can't run Docker yourself, and you don't
deploy: golem does, as its own steps.

## What golem does after your step

1. Pushes your branch, opens a PR and waits for CI on it.
2. Merges the PR once CI passes, and waits for CI on the merge commit: the build
   workflow pushes `ghcr.io/<org>/<slug>:<sha>` from `main` only (`<org>` is
   `$GITHUB_ORG`).
3. Asks the deployer to deploy that commit. The deployer refuses commits whose
   workflow runs haven't all passed, and checks the site answers once it's up.

If CI fails or the deployer refuses, golem gives the problem to a `fix` step and
goes round again. What you control is what makes this work: a `Dockerfile` that
builds, a workflow that tests and pushes the image (skills/new-repo.md), and a
`deploy/compose.yml` that follows the rules below.

To see the live site's state: `golem-deploy status` (what's deployed, at which
commit) and `golem-deploy logs [service] [lines]`. `curl -sS "$GOLEM_URL"` shows
what visitors get.

## deploy/compose.yml rules

The deployer allows only what's listed here; anything else is rejected with a reason.

- **Images:** your own, from `ghcr.io/<org>/<slug>` (or
  `ghcr.io/<org>/<slug>/<name>` for extra images), written **without a tag**:
  the deployer pins them to the commit it deploys. Or an official image with a
  tag: `postgres`, `redis`, `nginx`, `caddy`, `httpd`, `mariadb`, `mysql`,
  `mongo`, `memcached` (e.g. `postgres:17-alpine`). No `build:`.
- **Exactly one public service** with labels `golem.public: "true"` and
  `golem.port: "<port it listens on>"`. Traffic for the URL goes there. No
  `ports:`; services reach each other by service name on the project network.
- **Memory:** every service sets `mem_limit` (e.g. `64m`), at most 512 MB each and
  1 GB for the whole project. Static sites need ~32m; small Node/Python apps 128–256m.
- **Volumes:** named only, declared at the top level with no options
  (`volumes: {data: {}}`), mounted as `data:/app/data`. No bind mounts.
- **Allowed service keys:** `image`, `command`, `entrypoint`, `environment`,
  `expose`, `healthcheck`, `depends_on`, `labels`, `mem_limit`, `deploy`
  (resources only), `networks`, `volumes`, `configs` (inline `content:` only),
  `read_only`, `restart`, `init`, `user`, `working_dir`, `stop_signal`,
  `stop_grace_period`.
- **Not allowed:** `ports`, `build`, `container_name`, `env_file`, `privileged`,
  `cap_add`, `devices`, `network_mode`, `extends`, `include`, `secrets`, any
  `traefik.*` label, YAML anchors/aliases, duplicate keys.
- **No `$`** variable interpolation: write a literal dollar sign as `$$`.

Minimal example (a static site):

```yaml
services:
  web:
    image: ghcr.io/<org>/<slug>
    mem_limit: 32m
    labels:
      golem.public: "true"
      golem.port: "80"
```

With a database:

```yaml
services:
  app:
    image: ghcr.io/<org>/<slug>
    mem_limit: 256m
    environment:
      DATABASE_URL: postgres://app:app@db:5432/app
    depends_on: [db]
    labels:
      golem.public: "true"
      golem.port: "3000"
  db:
    image: postgres:17-alpine
    mem_limit: 256m
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    volumes:
      - db:/var/lib/postgresql/data
volumes:
  db: {}
```

Data is disposable and the database is only reachable inside the project, so plain
passwords like this are fine. Prefer SQLite in a named volume when it's enough.

## When a deploy failed (in a fix step)

- **A rule was broken:** the deployer's reasons say which; fix
  `deploy/compose.yml` to follow the rules above.
- **Image not found:** CI didn't push it. The workflow must push
  `ghcr.io/${{ github.repository }}:${{ github.sha }}` on pushes to `main`.
- **No matching manifest / exec format error:** the image was built for another
  architecture. Set the workflow's `platforms:` to `$GOLEM_PLATFORM`
  (skills/new-repo.md).
- **Didn't respond:** the app crashed or listens on another port or interface. It
  must listen on `0.0.0.0` at `golem.port`. The logs in the problem (or
  `golem-deploy logs`) say which.
