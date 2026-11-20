"""A repo's past golem jobs, read back from GitHub when golem adopts it (adopt.py).

Each job golem ran opened an issue ("Worked on by golem (job 7)") with Riggy's request in it,
named for the job (a change's title), and its PRs list the steps that did the work ("- **change:**
what it did"). From those come a finished job for each issue: its request, its title, its steps,
how it ended and when. They're a record, shown with the project's jobs and given as context to
questions, proposals and its card, and golem numbers them afresh: the number in an issue ("job 7")
was an earlier golem's.

A job is its issue, which GitHub numbers once per repo. A PR golem opened for a job whose issue
never opened is a job of its own, with any other PRs that name the same job. Only golem's issues
count, so a repo golem never worked on has no past jobs, whatever else is in it. It's all read
by golem's own code, a hundred issues a request: no model reads it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .config import Settings
from .store import Job, Step

WORKED_ON = re.compile(r"Worked on by \w+ \(job (\d+)\)\.?\s*$")  # the end of a job's issue
OPENED_BY = re.compile(r"Opened by \w+ \(job (\d+)\)")  # in a PR golem opened
CLOSES = re.compile(r"\b(?:Closes|Part of) #(\d+)")  # the job's issue, in its PRs
WORK = re.compile(r"^- \*\*([\w-]+):\*\* ?(.*)$")  # a step's line in a PR golem opened


def records(settings: Settings, slug: str, items: list[dict]) -> list[tuple[Job, list[Step]]]:
    """The repo's past golem jobs with their steps, oldest first, from its issues and PRs as
    GitHub's issues API has them (workspace.ITEM). Each job's id is 0: it gets one when stored."""
    groups: dict[object, tuple[dict | None, list[dict]]] = {}  # the issue's number, or ("job", n): (issue, PRs)
    for issue in items:
        if not issue["pr"] and WORKED_ON.search(issue["body"] or ""):
            groups[issue["number"]] = (issue, [])
    for pr in sorted((i for i in items if i["pr"]), key=lambda i: i["number"]):
        body = pr["body"] or ""
        closes, opened = CLOSES.search(body), OPENED_BY.search(body)
        if closes and int(closes[1]) in groups:
            groups[int(closes[1])][1].append(pr)
        elif opened:  # its issue never opened: the PRs that name the job are all there is
            groups.setdefault(("job", int(opened[1])), (None, []))[1].append(pr)
    url = settings.repo_url(slug)
    found = []
    for issue, prs in sorted(groups.values(), key=lambda g: (g[0] or g[1][0])["created_at"]):
        request = (_request(issue["body"] or "") or issue["title"]) if issue else prs[-1]["title"]
        kind = "change" if found else "adopt" if request.startswith("!adopt ") else "new"  # the first made it golem's
        start, end = issue or prs[0], issue or prs[-1]
        job = Job(
            id=0, slug=slug, kind=kind, request=request, playbook={"new": "standard", "adopt": "adopt"}.get(kind, "feature"),
            phase=0, state=_state(issue, prs), priority=0, resume_session=None, attempts=0, retry_at=None,
            issue=issue["number"] if issue else None,
            created_at=_time(start["created_at"]), updated_at=_time(end["closed_at"] or end["created_at"]),
            title=issue["title"] if issue and kind == "change" else "",  # a change's issue is named for it; the first's, the project
        )  # fmt: skip
        steps = []
        if issue:
            links = {"repo": url, "issue": issue["html_url"]}
            steps.append(Step(0, 0, 0, "setup", "setup", state="done", summary="Repo and issue ready.", links=links))
        for pr in prs:
            steps += [Step(0, 0, 0, "agent", name, state="done", summary=said) for name, said in _work(pr["body"] or "")]
            links = {"pr": pr["html_url"]}
            steps.append(Step(0, 0, 0, "pr", "pr", state="done", summary=f"PR #{pr['number']}: {pr['title']}", links=links))
            if pr["merged_at"]:
                steps.append(Step(0, 0, 0, "merge", "merge", state="done", summary=f"Merged PR #{pr['number']}.", links=links))
        found.append((job, steps))
    return found


def _state(issue: dict | None, prs: list[dict]) -> str:
    """How a job ended. Its last PR closes its issue, or golem does when there's none to merge,
    so an open one never finished. (Reckless jobs from before golem closed their issues read as
    failed.)"""
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
