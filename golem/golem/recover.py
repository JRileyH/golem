"""Golem's past jobs on a repo, read back from GitHub.

Each job golem ran opened an issue ("Worked on by golem (job 7)") with Riggy's request in it,
and its PRs list the steps that did the work ("- **change:** what it did"). From those come a
finished job for each issue, with its request, its steps and how it ended.

A new install no longer rebuilds projects from the org on its own: it starts empty, and a repo
comes back only when Riggy adopts it (adopt.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import Settings
from .store import Job, Project, Step
from .workspace import WorkspaceError, run

WORKED_ON = re.compile(r"Worked on by \w+ \(job (\d+)\)\.?\s*$")  # the end of a job's issue
OPENED_BY = re.compile(r"Opened by \w+ \(job (\d+)\)")  # in a PR golem opened
CLOSES = re.compile(r"\b(?:Closes|Part of) #(\d+)")  # the job's issue, in its PRs
WORK = re.compile(r"^- \*\*([\w-]+):\*\* ?(.*)$")  # a step's line in a PR golem opened


@dataclass
class Repo:
    slug: str
    description: str
    created_at: str
    title: str  # the README's first heading, "" for none
    items: list[dict]  # its issues and PRs, as GitHub's issues API has them (GitHub.ITEM)


class GitHub:
    """What's read from the org, through gh."""

    ITEM = (
        "{number, title, body, state, state_reason, created_at, closed_at, html_url,"
        " pr: (.pull_request != null), merged_at: .pull_request.merged_at}"
    )

    def __init__(self, org: str):
        self.org = org

    async def items(self, slug: str) -> list[dict]:
        url = f"repos/{self.org}/{slug}/issues?state=all&per_page=100"
        code, out = await run("gh", "api", "--paginate", url, "--jq", f".[] | {self.ITEM}")
        if code != 0:
            raise WorkspaceError(f"reading {slug}'s issues: {out[-300:]}")
        return [json.loads(line) for line in out.splitlines() if line.strip()]


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
        request = (_request(issue["body"] or "") or issue["title"]) if issue else job_prs[-1]["title"]
        kind = "change" if job_id != first else "adopt" if request.startswith("!adopt ") else "new"
        start, end = issue or job_prs[0], issue or job_prs[-1]
        job = Job(
            id=job_id, slug=repo.slug, kind=kind, request=request,
            playbook={"new": "standard", "adopt": "adopt"}.get(kind, "feature"), phase=0, state=_state(issue, job_prs), priority=0,
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


def _state(issue: dict | None, prs: list[dict]) -> str:
    """How a job ended. Its last PR closes its issue, or golem does when there's no PR
    (reckless), so an open one never finished. (Reckless jobs from before golem closed
    their issues come back failed.)"""
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
    """(step name, what it did) for each step a PR golem opened lists. A worker's own PR may
    have lines like them that aren't steps."""
    if not OPENED_BY.search(body):
        return []
    found: list[list[str]] = []
    for line in body.splitlines():
        if OPENED_BY.search(line):
            break
        if m := WORK.match(line):
            found.append([m[1], m[2]])
        elif found:
            found[-1][1] += "\n" + line
    return [(name, said.strip()) for name, said in found]


def _time(stamp: str) -> str:
    """GitHub's "2026-10-24T05:41:30Z" the way golem writes times."""
    return datetime.fromisoformat(stamp).astimezone(UTC).isoformat(timespec="seconds")
