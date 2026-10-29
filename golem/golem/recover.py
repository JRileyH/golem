"""Bringing projects back from GitHub when golem starts with a new database.

golem keeps nothing about its projects outside its database and the org's repos, so moving
it to a new box is a fresh install with the same .env. On an org golem has never used it
starts with nothing. On one it has, its first start rebuilds what the org remembers:

- a project for each repo with golem's issues in it: its name (the README's heading), its
  summary (the repo's description) and when it began. Settings are golem's defaults.
- a finished job for each of those issues, with its id ("Worked on by golem (job 7)"),
  Riggy's request, and steps from its PRs ("- **change:** what it did").
- then each project's main, deployed again, so its site is back up.

What only the database knew is gone: conversations, notes, run history, proposals, tags
and settings. Questions and proposals jobs never opened issues, so they're gone too.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import Settings
from .deployer_client import Deployer, DeployerError
from .store import Job, Project, Step, Store
from .talk import STATUS, Talk
from .workspace import Workspace, WorkspaceError, run

log = logging.getLogger(__name__)

# In kv: "pending" on a new database until it's rebuilt, then the slugs still to deploy (JSON).
KEY = "recover"
PENDING = "pending"
WORKED_ON = re.compile(r"Worked on by \w+ \(job (\d+)\)\.?\s*$")  # the end of a job's issue
OPENED_BY = re.compile(r"Opened by \w+ \(job (\d+)\)")  # in a PR golem opened
CLOSES = re.compile(r"\b(?:Closes|Part of) #(\d+)")  # the job's issue, in its PRs
WORK = re.compile(r"^- \*\*([\w-]+):\*\* ?(.*)$")  # a step's line in a PR golem opened
REDEPLOYED = "Deploy main again: golem moved to a new server."


@dataclass
class Repo:
    slug: str
    description: str
    created_at: str
    title: str  # the README's first heading, "" for none
    items: list[dict]  # its issues and PRs, as GitHub's issues API has them (GitHub.ITEM)


class GitHub:
    """What recovery reads from the org, through gh."""

    ITEM = (
        "{number, title, body, state, state_reason, created_at, closed_at, html_url,"
        " pr: (.pull_request != null), merged_at: .pull_request.merged_at}"
    )

    def __init__(self, org: str):
        self.org = org

    async def repos(self) -> list[dict]:
        """{name, description, createdAt} for each repo, archived ones aside."""
        code, out = await run("gh", "repo", "list", self.org, "--no-archived", "--limit", "1000", "--json", "name,description,createdAt")
        if code != 0:
            raise WorkspaceError(f"listing {self.org}'s repos: {out[-300:]}")
        return json.loads(out or "[]")

    async def items(self, slug: str) -> list[dict]:
        url = f"repos/{self.org}/{slug}/issues?state=all&per_page=100"
        code, out = await run("gh", "api", "--paginate", url, "--jq", f".[] | {self.ITEM}")
        if code != 0:
            raise WorkspaceError(f"reading {slug}'s issues: {out[-300:]}")
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    async def title(self, slug: str) -> str:
        code, out = await run("gh", "api", f"repos/{self.org}/{slug}/readme", "-H", "Accept: application/vnd.github.raw")
        return _heading(out) if code == 0 else ""


async def read(github: GitHub) -> list[Repo]:
    """The org's repos with golem's issues in them, oldest first. The rest weren't golem's."""
    found = []
    for r in sorted(await github.repos(), key=lambda r: r["createdAt"]):
        items = await github.items(r["name"])
        if any(not i["pr"] and WORKED_ON.search(i["body"] or "") for i in items):
            found.append(Repo(r["name"], r["description"] or "", _time(r["createdAt"]), await github.title(r["name"]), items))
    return found


def records(settings: Settings, repo: Repo) -> tuple[Project, list[tuple[Job, list[Step]]]]:
    """The project and its jobs, with their steps, as far as the repo's issues and PRs tell."""
    issues = {i["number"]: i for i in repo.items if not i["pr"]}
    prs = sorted((i for i in repo.items if i["pr"]), key=lambda i: i["number"])
    jobs: dict[int, tuple[dict | None, list[dict]]] = {}  # job id: its issue and its PRs
    numbers = {}  # issue number: job id
    for issue in issues.values():
        if m := WORKED_ON.search(issue["body"] or ""):
            jobs[int(m[1])] = (issue, [])
            numbers[issue["number"]] = int(m[1])
    for pr in prs:
        body = pr["body"] or ""
        closes, opened = CLOSES.search(body), OPENED_BY.search(body)
        job_id = numbers.get(int(closes[1])) if closes else None
        if job_id is None and opened:
            job_id = int(opened[1])
            jobs.setdefault(job_id, (None, []))  # its issue didn't open: the PR is all there is
        if job_id is not None:
            jobs[job_id][1].append(pr)
    first = min(jobs, default=None)  # the new project's job; the rest were changes
    named = jobs[first][0] if first is not None else None  # its issue is titled with the project's name
    name = repo.title or (named["title"] if named else "") or repo.slug
    project = Project(
        slug=repo.slug, name=name[:60], summary=repo.description, mode=settings.default_mode, model="", effort="",
        paused=False, created_at=repo.created_at,
    )  # fmt: skip
    url = settings.repo_url(repo.slug)
    found = []
    for job_id, (issue, job_prs) in sorted(jobs.items()):
        kind = "new" if job_id == first else "change"
        start, end = issue or job_prs[0], issue or job_prs[-1]
        job = Job(
            id=job_id, slug=repo.slug, kind=kind,
            request=(_request(issue["body"] or "") or issue["title"]) if issue else job_prs[-1]["title"],
            playbook="standard" if kind == "new" else "feature", phase=0, state=_state(issue, job_prs), priority=0,
            resume_session=None, attempts=0, retry_at=None, issue=issue["number"] if issue else None,
            created_at=_time(start["created_at"]), updated_at=_time(end["closed_at"] or end["created_at"]),
        )  # fmt: skip
        steps = []
        if issue:
            links = {"repo": url, "issue": issue["html_url"]}
            steps.append(Step(0, job_id, 0, "setup", "setup", state="done", summary="Repo and issue ready.", links=links))
        for pr in job_prs:
            steps += [Step(0, job_id, 0, "agent", name, state="done", summary=said) for name, said in _work(pr["body"] or "")]
            links = {"pr": pr["html_url"]}
            steps.append(Step(0, job_id, 0, "pr", "pr", state="done", summary=f"PR #{pr['number']}: {pr['title']}", links=links))
            if pr["merged_at"]:
                steps.append(Step(0, job_id, 0, "merge", "merge", state="done", summary=f"Merged PR #{pr['number']}.", links=links))
        found.append((job, steps))
    return project, found


async def rebuild(settings: Settings, store: Store, chat: Talk, github: GitHub) -> None:
    """On a new database, the org's projects and their jobs. Before anything else runs, so
    new jobs get ids after the old ones."""
    if store.get(KEY) != PENDING:
        return
    try:
        repos = await read(github)
    except WorkspaceError as e:
        log.error("couldn't read the org to rebuild from: %s", e)
        await chat.send(STATUS, f"I couldn't read github.com/{settings.org} to bring back my projects: {e}\nRestart me to try again.")
        return
    restored = []
    for repo in repos:
        if store.project(repo.slug):  # back already, from a start that didn't finish
            continue
        project, jobs = records(settings, repo)
        store.restore(project, jobs)
        restored.append((project, len(jobs)))
        log.info("rebuilt %s with %d jobs from GitHub", project.slug, len(jobs))
        await chat.send(
            project.slug,
            f"I moved to a new server and rebuilt this project from GitHub: {len(jobs)} job(s) from its issues and PRs. "
            "Its settings are the defaults, and our conversation here from before didn't come along.",
            notice=True,
        )
    store.put(KEY, json.dumps([p.slug for p, _ in restored]))
    if restored:
        names = ", ".join(p.name for p, _ in restored)
        await chat.send(
            STATUS,
            f"I'm on a new server, so I rebuilt what github.com/{settings.org} remembers: {len(restored)} project(s) "
            f"({names}) and their {sum(n for _, n in restored)} jobs, from the issues and PRs. Their settings are "
            "the defaults, and our conversations from before didn't come along. Deploying them again now.",
            notice=True,
        )


async def redeploy(settings: Settings, store: Store, chat: Talk, workspace: Workspace, deployer: Deployer) -> None:
    """Deploy each rebuilt project's main again, one at a time, before the scheduler starts. One
    that comes up gets a finished job saying so; it's live, and gets its card."""
    try:
        todo = json.loads(store.get(KEY) or "[]")
    except ValueError:  # still pending: the rebuild didn't happen
        return
    up, down = [], []
    while todo:
        project = store.project(todo[0])
        if project:
            if problem := await _redeploy(settings, store, workspace, deployer, project):
                down.append(f"- {project.name}: {problem}")
            else:
                up.append(project.name)
        todo = todo[1:]
        store.put(KEY, json.dumps(todo))
    if up or down:
        lines = [f"Back up: {', '.join(up)}."] if up else []
        if down:
            lines += ["Didn't come back up (ask in its conversation for a fix):", *down]
        await chat.send(STATUS, "\n".join(lines), notice=not down)


async def _redeploy(settings: Settings, store: Store, workspace: Workspace, deployer: Deployer, project: Project) -> str:
    """What went wrong, or "" once it's live."""
    try:
        await workspace.ensure_repo(project.slug, project.summary)  # its checkout, for its card
        sha = await workspace.main_sha(project.slug)
        deployment = await deployer.deploy(project.slug, sha)
    except (WorkspaceError, DeployerError) as e:
        return str(e)[:300]
    if not deployment.ok:
        return (deployment.problem.strip().splitlines() or ["the deployer refused it"])[0][:300]
    job = store.add_job(project.slug, "change", REDEPLOYED, "feature")
    links = {"site": settings.project_url(project.slug), "commit": f"{settings.repo_url(project.slug)}/commit/{sha}"}
    store.add_steps(job.id, [Step(0, job.id, 0, "deploy", "deploy", state="done", summary=f"{sha[:7]} is live.", links=links)])
    store.update_job(job.id, state="done")
    return ""


def _state(issue: dict | None, prs: list[dict]) -> str:
    """How a job ended. Its last PR closes its issue, so an open one never finished (or was
    reckless: those push to main with no PR, and leave their issue open)."""
    if issue is None:
        return "done" if prs[-1]["merged_at"] else "failed"
    if issue["state"] == "open":
        return "failed"
    return "killed" if issue["state_reason"] == "not_planned" else "done"


def _request(body: str) -> str:
    """Riggy's words from a job's issue: "Requested:\\n\\n> <words>\\n\\nWorked on by golem (job 7).\""""
    text = WORKED_ON.sub("", body).strip()
    head, _, rest = text.partition("\n\n")
    if head.startswith("Requested"):
        text = rest
    return "\n".join(line[2:] if line.startswith("> ") else line for line in text.splitlines()).strip()


def _work(body: str) -> list[tuple[str, str]]:
    """(step name, what it did) for each step a PR golem opened lists."""
    found: list[list[str]] = []
    for line in body.splitlines():
        if OPENED_BY.search(line):
            break
        if m := WORK.match(line):
            found.append([m[1], m[2]])
        elif found:
            found[-1][1] += "\n" + line
    return [(name, said.strip()) for name, said in found]


def _heading(markdown: str) -> str:
    return next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), "")


def _time(stamp: str) -> str:
    """GitHub's "2026-10-24T05:41:30Z" the way golem writes times."""
    return datetime.fromisoformat(stamp).astimezone(UTC).isoformat(timespec="seconds")
