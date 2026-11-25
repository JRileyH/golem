# Server runbook: bring golem up on a box

Any box with Docker and a public IP will do: a VPS, a Raspberry Pi 5, x86 or
ARM. golem takes ports 80 and 443 and gets its own certificates; it touches
nothing else on the box. Already run a reverse proxy on 80 and 443? golem can sit
behind it instead: see [below](#already-running-a-reverse-proxy). The commands
below run on the box, in the directory you clone golem into (`~/golem` here).

Moving golem from another box, or using an org it has used before? Read
[Moving to a new box](#moving-to-a-new-box) first: golem brings that org's
projects back on its first start.

## Already running a reverse proxy?

If a proxy of yours (Caddy, nginx, Traefik...) already holds 80 and 443, on this
box or in front of it, golem sits behind it. Set it up as
[behind-a-proxy.md](behind-a-proxy.md) says, then follow the steps below with
these differences:

- **Step 0:** "nothing else listening on 80 or 443" doesn't apply. The wildcard
  record points at whatever reaches your proxy, and a home router forwards 80 and
  443 to the proxy, not to golem. Let's Encrypt never needs to reach golem: your
  proxy holds a wildcard certificate (DNS challenge), so a CDN proxy in front is
  fine too.
- **Step 2:** set `EDGE_PORT` before the second `bin/bootstrap.sh server`, which
  should then say "front door: plain HTTP on ...".
- **Step 3:** golem's Traefik gets no certificates: skip the `acme` grep and the
  wait. Start or reload your proxy after golem, since a proxy in Docker joins
  `golem-edge`, which golem creates.
- **Step 4:** first check golem answers on `EDGE_PORT` itself ("Checking it" in
  behind-a-proxy.md), then through the proxy. `nothing-here` needs no `-k`: the
  wildcard certificate covers it.
- **Moving to a new box:** projects need no certificates of their own, so DNS
  needn't move before golem starts; the new box's proxy needs the same forwarding.

## 0. Before you start

**The box:** Docker Engine with the compose plugin, on a 64-bit OS (on a Pi,
64-bit Raspberry Pi OS). Nothing else listening on 80 or 443 (or see
[behind-a-proxy.md](behind-a-proxy.md)). golem itself
takes up to about 2 GB of memory (`GOLEM_MEM_LIMIT` plus ~400 MB), and each
project up to `DEPLOYER_PROJECT_MEM_CAP_MB` more. Images need ~3 GB of disk.

```sh
docker compose version
free -m
df -h /
```

**DNS:** a wildcard `A` record (and `AAAA`, if the box has IPv6) for
`*.<your domain>` pointing at the box's public IP. If it sits behind a CDN
proxy, make the record DNS-only: Let's Encrypt must reach the box on port 80.
Behind a home router, forward 80 and 443 to it. Check from anywhere:
`python3 -c "import socket; print(socket.gethostbyname('x.<your domain>'))"`.

**A GitHub org** for the project repos (`GITHUB_ORG`), and tokens for it
(GitHub → Settings → Developer settings):

1. **Fine-grained token** for the deployer: resource owner your org, all
   repositories, **Contents: Read-only** and **Actions: Read-only** (Metadata
   is added automatically). Actions, not Checks: fine-grained tokens don't
   offer Checks, so the deployer reads workflow runs. Goes in `DEPLOYER_GITHUB_TOKEN`.
2. **Classic token** with only **`read:packages`**: ghcr.io doesn't accept
   fine-grained tokens. Goes in `DEPLOYER_REGISTRY_TOKEN`, with your GitHub
   username in `DEPLOYER_REGISTRY_USER`.
3. **Fine-grained token** for the workers: your org, all repositories,
   repository creation plus Contents, Pull requests, Issues and Workflows read
   and write, and Actions read-only (golem reads CI on PRs and main itself
   before merging and deploying). Goes in `GOLEM_GITHUB_TOKEN`.
4. **Fine-grained token** for upgrading golem from the web app (optional): resource
   owner whoever owns the golem repo (your account, not the org), only that repo,
   **Contents: Read-only**. Goes in `GOLEM_REPO_TOKEN`; only the deployer gets
   it. Leave it empty if the golem repo is public.

**hello on GitHub (optional, to prove hosting before any job):** `<org>/hello`
has the contents of `examples/hello` on `main`, with `your-org` replaced by your
org in `deploy/compose.yml`; its `build` workflow is green, and the package
`ghcr.io/<org>/hello` exists. Note the commit sha.

## 1. Get the code

```sh
git clone <golem repo URL> ~/golem
cd ~/golem
```

## 2. Configure

```sh
bin/bootstrap.sh server     # creates .env, generates the secrets it can
nano .env                   # fill in what it lists: BASE_DOMAIN, GITHUB_ORG,
                            # CLAUDE_CODE_OAUTH_TOKEN, GOLEM_WEB_PASSWORD
                            # and the tokens above
bin/bootstrap.sh server     # should end with "ok" and nothing left to fill in
```

Leave `EDGE_PORT` empty unless golem sits behind your own proxy: bootstrap says
which it's set up for ("front door: ...").

`GOLEM_WEB_PASSWORD` has no default and golem won't start without it. Make it
long: signed in, the web app can do everything, deleting projects included.

## 3. Build and start

```sh
docker compose up -d --build           # builds golem's images on the box, for its architecture
docker compose ps                      # golem, deployer and traefik up within a minute
docker compose logs traefik | grep -iE 'error|acme'
```

Traefik gets a Let's Encrypt certificate for each host the deployer routes:
`golem.` and `projects.` when the deployer starts, each project on its first
deploy. Allow a minute.

A new install starts with no projects, even on an org golem has used before: it
takes nothing from the org on its own. Adopting brings back the ones you want
(see [Moving to a new box](#moving-to-a-new-box)).

## 4. Verify the stack

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://golem.<your domain>/         # 200
curl -sSk -o /dev/null -w '%{http_code}\n' https://nothing-here.<your domain>/  # 404 (-k: no route, so no certificate)
just claude-check                                                               # "golem ok"
```

(`just` is optional: every recipe in `justfile` is plain `docker compose`.)

## 5. Deploy hello (optional)

```sh
just deploy hello <sha>       # the sha from step 0
curl -sS https://hello.<your domain>/ | grep '<h1>'
```

A deploy with failing or pending checks is refused with the workflow names; a
compose file that breaks a rule is refused with the rule.

## 6. The web app and push notifications

On the phone, open `https://golem.<your domain>`, add it to the Home Screen
(Share, then Add to Home Screen) and open it from there. Under Settings, sign in
with `GOLEM_WEB_PASSWORD` (tick "Keep me signed in"), then "Turn on for this
device" and "Send a test": the phone should show a notification from golem.

If golem rebuilt projects from the org, its news (on top of Now) says which, and
which came back up. Then start a small project with + New project: name it and
say what it's for. Each project gets its own page of jobs. To stop all work
without stopping golem, use "Pause all work" in Settings.

## Updating

**From the web app:** Settings → golem says what's running and what's new on
GitHub's `main` (it checks every 15 minutes; "Check now" checks straight away),
and the Settings tab gets a dot when golem is behind. "Upgrade now" waits for a
running step to finish, then does what's below on the box: a fast-forward of
`main`, `bin/bootstrap.sh`, and `docker compose up -d --build`. If the new
version won't build, nothing changes; if golem or the deployer doesn't come up
healthy within 5 minutes, the previous commit is put back and rebuilt. golem
says how it went in its own conversation. "Upgrade automatically" does the same
whenever golem is behind and has no work queued or running, and never retries a
commit that failed.

It needs the checkout on `main` with no local changes or commits of its own,
`GOLEM_HOST_DIR` (bootstrap sets it: run bootstrap again after moving the
checkout), and `GOLEM_REPO_TOKEN` unless the repo is public. Anyone who can push
to the golem repo's `main` can then change what runs on the box, with automatic
upgrades on without anyone pressing a button: keep it to you. To see an upgrade's
progress, `docker logs -f golem-upgrade` while it runs.

**By hand,** after pushing new golem commits:

```sh
cd ~/golem
git pull
bin/bootstrap.sh server     # generates any new secret; lists anything left to fill in
docker compose up -d --build
docker compose logs --since 2m golem | grep -E 'started|ERROR'   # "started: the web app is at ..."
```

The deployer writes the routes for golem's own hosts (golem. and projects.)
when it starts, so a change to them needs the deployer rebuilt too, which
`docker compose up -d --build` does.

A job carries on from its current step when golem comes back: an agent step
that was running starts again; golem's own steps pick up where they were.

## Checking on a job

The web app has three tabs. Home says what needs you, what's being worked on and
when it's next live, and what's up next. Projects lists the conversations; a
project's page (`/p/<slug>`) has its job as a card, with its pieces of work as a
checklist, each opening a sheet that holds what it did with links to the PRs,
commits, CI runs and site, its runs and transcript, or its live tail while it
runs. `/jobs/<n>` shows one job with every step; `/insights` shows where the
time and usage go; `/reports` (signed in) lists what got in the workers' way
about golem itself, with golem's triage of it and the fixes it proposes; Settings
sets the default model and effort for each kind of step.

When a step of golem's own fails (a merge, a push, a deploy call), all the
failing command printed is in `/var/lib/golem/jobs/<job>/NNN-<step>-errors.log`,
a section per try; the step's page and the chat show the end of it. Signed in, it can skip optional steps, give a step its own model, effort
or context, reorder the queue, pause, stop, and write to golem. Anyone can see
what's under way.

If notifications stop arriving on a device, turn them off and on again in
Settings ("Send a test" checks). Golem's push keys are in its database
(`vapid_private_key` in the `kv` table); if they're ever lost, every device has
to turn notifications on again.

The times shown ahead ("starting ~10:45", "Live ~11:27") are estimates: the
median of how long the last steps of each kind took, run one after another in
queue order. Usage is what Claude Code reports a run would cost at API prices,
the fair weight for how much of the Claude allowance it used.

In a step's transcript or live tail, each line is one thing the worker did,
timed from the start of the run: `→` a tool call (the command or file), `←` its
output (first and last line), `✗` a failed call, `says:` what the worker wrote,
`┆` a subagent's work, and `■` how the run ended. If a step looks stuck, on the
box:

```sh
docker compose top golem                                         # what's running in the container now
docker compose logs --since 1h golem | grep -E 'WARNING|ERROR'    # failed attempts and why
```

Stop job, on the job's pane (or on its project's Settings tab), stops the job.

## Adopting a repo golem didn't make

Golem can take on a repo you made yourself: "Adopt a repo golem didn't make" on
the New project page (the repo, what it is, and its name, mode and tag). From
then on it's a project like any other, and workers own its code.

- **Where it lives:** in `GITHUB_ORG`, named as its slug (lowercase letters,
  digits and hyphens), with a `main` branch, not archived. The tokens above
  reach all of the org's repos; if you limited golem's to selected repos, add
  this one to it.
- **What golem checks first** (`golem/golem/adopt.py`): CI on main; a
  Dockerfile; a workflow that tests, then builds and pushes the image the way
  `skills/new-repo.md` says; `deploy/compose.yml`, by the deployer's own rules;
  the repo's shape (AGENTS.md, CLAUDE.md, a README with the live URL first,
  `docs/`); and a worker run that installs and tests it in golem's container and
  reads it for anything golem can't give: secrets, files on the host, other
  ports, more memory than the caps, a step someone does by hand.
- **What happens:** anything only you can change (those needs, a package golem's
  image lacks, a GitHub setting) and golem leaves it alone and says what to
  change. Gaps a worker can fix, it fixes in a PR, then deploys main, as with any
  change. Its job's activity says which.
- **A repo golem worked on before** (adopted again after it was deleted, or on a
  new box) gets its past jobs back too: golem reads its issues and PRs and lists
  each job it ran there, with its request and what its steps did, under a new
  number. Only golem's own issues count. PRs that an earlier golem left open on
  its `golem/job-*` branches are closed (their commits stay on GitHub), since
  new jobs reuse those branch names.

## Moving to a new box

Nothing needs copying but `.env`. A new install starts empty and takes nothing
from the org on its own: you bring back the projects you want by adopting them.
The rest stay in the org as they are until you do.

1. **Stop the old box** if it still runs (`docker compose down` there). Two
   golems on one org would both work on the same repos.
2. **Point the DNS** (step 0) at the new box.
3. **Set up the new box** from step 0. Copy the old `.env` over before
   `bin/bootstrap.sh server`, or fill in the same values: above all the same
   `GITHUB_ORG` and its tokens. `BASE_DOMAIN` may change; projects then live
   under the new domain.
4. **Start it** (step 3), and turn notifications on again on each device (step
   6): sign-ups don't come along.
5. **Adopt** each project you want back, on the New project page ("Adopting a
   repo golem didn't make", above). It checks each one over before
   deploying it. If the new box has another architecture (an x86 VPS to a
   Raspberry Pi), the check finds each workflow still building for the old one,
   and a worker fixes it before the deploy.

What comes back with an adopted project: its code, and its past jobs from its
issues and PRs. What doesn't: activity, notes, run history and cost, proposals,
its settings (a new project's: you pick its mode and tag when you adopt it),
lessons, and any data it kept in its volumes.

## Starting over

Delete, on a project's Settings tab, deletes one project. To delete all of them:

```sh
cd ~/golem
bin/nuke.sh
```

It lists what will go and asks you to type `nuke`. Then it stops golem, deletes
every project the way deleting one does (site, data, checkout, job history, run
logs, activity), every other site the deployer has (`hello` too; redeploy it
with `just deploy hello <sha>` if you want it back), and golem's lessons, and
starts golem again. The stack, golem's news, the reports about golem and its
proposals for itself, and `.env` stay. GitHub repos stay too, until you delete them in your org's
repositories on GitHub (golem's token can't). Nothing brings them back on its
own; adopting one brings it back, with its past jobs. If something can't be
deleted it says so; running it again retries.

## Removing golem

```sh
cd ~/golem
docker compose -p golem-<slug> down    # each deployed project (keeps its volumes)
docker compose down                     # the golem stack (keeps volumes: state)
docker network rm golem-apps golem-internal golem-edge 2>/dev/null
```

To remove it entirely, also `docker compose down --volumes` (**deletes golem's
state**; installed again on the same org, it starts empty, and adopting brings
projects back)
and `rm -rf ~/golem`. To roll back just one change,
`git checkout <previous commit>` and `docker compose up -d --build`.
