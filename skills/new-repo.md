# Setting up a project repo

Every project repo has the same shape, so any worker can pick it up later.

| File | What |
| --- | --- |
| `AGENTS.md` | Stack, how to run, test and deploy, gotchas. Kept accurate. |
| `CLAUDE.md` | One line: `See AGENTS.md.` |
| `README.md` | Live URL first, then what the project does, in a few lines. |
| `Dockerfile` | Builds the image CI pushes. |
| `deploy/compose.yml` | What the deployer runs (rules in skills/deploy.md). |
| `.github/workflows/build.yml` | Tests, then builds and pushes the image. |
| `docs/brief.md` | Riggy's original request and the brief. |
| `docs/specs/` | Specs: `mvp.md`, then one per bigger change. |
| `docs/decisions.md` | One line per decision: what, and why. |
| `docs/lessons.md` | What a future worker on this project should know. |

## Stack

Pick the smallest stack that does the job well:

- **Static site** (plain HTML/CSS/JS, or a Vite build) served by `nginx:1.29-alpine`.
  Best for games, tools and anything that can keep state in the browser
  (`localStorage`).
- **Small server** (Node or Python) when it needs shared data or server work.
  SQLite in a named volume before reaching for Postgres.

Whatever you pick, the app listens on `0.0.0.0` on one port, given in
`golem.port`.

## Dockerfile

Multi-stage when there's a build step; small runtime image. For a Vite site
(`--platform=$BUILDPLATFORM` runs the build natively in CI; only the final
stage is for the server):

```dockerfile
FROM --platform=$BUILDPLATFORM node:24-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
```

Commit lockfiles (`package-lock.json`, `uv.lock`) so CI builds are reproducible.

## The build workflow

Images are built only in GitHub Actions, tagged with the commit sha, for the
server's architecture: write the value of `$GOLEM_PLATFORM` (e.g.
`linux/arm64`) in `platforms:`. Run the tests first, as their own job, so a
failing test blocks the image. Free private repos get 2,000 Actions minutes a month across all projects:
keep CI quick and cache dependencies.

```yaml
name: build

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read
  packages: write

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-node@v5
        with:
          node-version: 24
          cache: npm
      - run: npm ci
      - run: npm test

  image:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: docker/setup-qemu-action@v3  # the runner is x86; the server may not be
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        if: github.event_name == 'push'
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: <$GOLEM_PLATFORM>
          push: ${{ github.event_name == 'push' }}
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Drop the `test` job (and `needs`) only for a site with nothing to test. For a
Python project, swap the setup for `astral-sh/setup-uv@v6` and `uv run pytest`.
If a project needs a second image, add another build step tagged
`ghcr.io/${{ github.repository }}/<name>:${{ github.sha }}`.

## AGENTS.md template

```markdown
# <name>

<one line: what it is>. Live at <$GOLEM_URL>

- **Stack:** ...
- **Run locally:** ...
- **Test:** ...
- **Deploy:** golem merges to main and deploys the merge commit once the
  `build` workflow passes on it (see golem's deploy skill).
- **Gotchas:** ...
```
