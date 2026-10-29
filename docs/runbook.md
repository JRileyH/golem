# Server runbook: bring golem up on a box

Any box with Docker and a public IP will do: a VPS, a Raspberry Pi 5, x86 or
ARM. golem takes ports 80 and 443 and gets its own certificates; it touches
nothing else on the box. The commands below run on the box, in the directory you
clone golem into (`~/golem` here).

## 0. Before you start

**The box:** Docker Engine with the compose plugin, on a 64-bit OS (on a Pi,
64-bit Raspberry Pi OS). Nothing else listening on 80 or 443. golem itself
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

Then describe a small idea on Home (or in golem's own conversation, at the top
of Projects). Each project gets its own page and conversation. To stop all work
without stopping golem, use "Pause all work" in Settings, or send `!pause` in
golem's own conversation.

## Updating

After pushing new golem commits:

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
GitHub (golem's token can't). If something can't be deleted it says so;
running it again retries.

## Removing golem

```sh
cd ~/golem
docker compose -p golem-<slug> down    # each deployed project (keeps its volumes)
docker compose down                     # the golem stack (keeps volumes: state)
docker network rm golem-apps golem-internal 2>/dev/null
```

To remove it entirely, also `docker compose down --volumes` (**deletes golem's
state**) and `rm -rf ~/golem`. To roll back just one change,
`git checkout <previous commit>` and `docker compose up -d --build`.
