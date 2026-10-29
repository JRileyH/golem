# Server runbook: bring golem up on a box

Any box with Docker and a public IP will do: a VPS, a Raspberry Pi 5, x86 or
ARM. golem takes ports 80 and 443 and gets its own certificates; it touches
nothing else on the box. Already run a reverse proxy on 80 and 443? golem can sit
behind it instead: [behind-a-proxy.md](behind-a-proxy.md) says what changes. The
commands below run on the box, in the directory you clone golem into (`~/golem`
here).

Moving golem from another box, or using an org it has used before? Read
[Moving to a new box](#moving-to-a-new-box) first: golem brings that org's
projects back on its first start.

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

On an org golem has used before, its first start also rebuilds the org's
projects and deploys them again (see [Moving to a new box](#moving-to-a-new-box)):

```sh
docker compose logs -f golem | grep -E 'rebuilt|started|ERROR'   # "rebuilt <slug> with N jobs from GitHub" per project
```

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

If golem rebuilt projects from the org, its own conversation (at the top of
Projects) says which, and which came back up. Then describe a small idea on Home
(or in golem's own conversation). Each project gets its own page and conversation. To stop all work
without stopping golem, use "Pause all work" in Settings, or send `!pause` in
golem's own conversation.

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
time and usage go; Settings sets the default model and effort for each kind of
step. Signed in, it can skip optional steps, give a step its own model, effort
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

`!kill` in the project's conversation (or Stop in its Settings) stops the job.

## Moving to a new box

Nothing needs copying but `.env`: golem rebuilds the rest from the org.

1. **Stop the old box** if it still runs (`docker compose down` there). Two
   golems on one org would both work on its repos and hand out the same job
   numbers.
2. **Point the DNS** (step 0) at the new box before golem starts: its first
   start deploys every project, and each needs a certificate.
3. **Set up the new box** from step 0. Copy the old `.env` over before
   `bin/bootstrap.sh server`, or fill in the same values: above all the same
   `GITHUB_ORG` and its tokens. `BASE_DOMAIN` may change; projects then live
   under the new domain.
4. **Start it** (step 3). With an empty state volume, golem's first start rebuilds
   what the org has of its own before the web app opens (`recover.py`). Each repo
   with golem's issues in it becomes a project. Each of those issues, with its
   PRs, becomes a finished job with its old number, and new jobs are numbered
   after them. Then, before any new work starts, it deploys each project's
   `main` again and says in its own conversation which came back up. Repos golem
   didn't make (`hello`, say) are left alone.
5. **Check** its own conversation in the web app. A project that didn't come
   back up says why: ask for a fix in its conversation. Turn notifications on
   again on each device (step 6): sign-ups don't come along.

What doesn't come along: conversations, notes, run history and cost, proposals,
tags, project settings (back to the defaults), lessons, and any data a project
kept in its volumes.

If it couldn't read the org (a wrong or missing `GOLEM_GITHUB_TOKEN`), it says
so in its own conversation and tries again on the next start: fix `.env`, then
`docker compose up -d`. The rebuild happens only on the first start with an
empty state volume. If golem first started with the wrong `GITHUB_ORG`, delete
that state and start again:

```sh
docker compose down
docker volume rm golem_golem-state
docker compose up -d
```

On an org golem has never used, it starts with nothing. Delete a project's repo
when you're done with it: on a fresh install, any repo still in the org comes
back.

## Starting over

`!delete` in a project's conversation deletes one project. To delete all of them:

```sh
cd ~/golem
bin/nuke.sh
```

It lists what will go and asks you to type `nuke`. Then it stops golem, deletes
every project the way `!delete` does (site, data, checkout, job history, run
logs, conversation), every other site the deployer has (`hello` too; redeploy it
with `just deploy hello <sha>` if you want it back), and golem's lessons and
proposals, and starts golem again. The stack, golem's own conversation, and
`.env` stay. GitHub repos stay too: delete them in your org's repositories on
GitHub (golem's token can't). golem keeps its database, so they don't come back
by themselves, but a fresh install on the org would bring back any still there.
If something can't be deleted it says so; running it again retries.

## Removing golem

```sh
cd ~/golem
docker compose -p golem-<slug> down    # each deployed project (keeps its volumes)
docker compose down                     # the golem stack (keeps volumes: state)
docker network rm golem-apps golem-internal golem-edge 2>/dev/null
```

To remove it entirely, also `docker compose down --volumes` (**deletes golem's
state**; installing again on the same org rebuilds its projects from GitHub)
and `rm -rf ~/golem`. To roll back just one change,
`git checkout <previous commit>` and `docker compose up -d --build`.
