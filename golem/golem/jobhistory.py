"""A project's jobs, all of them, to find one: by words from its title or request, or its number,
and by how it went and its workflow; the latest first, a page at a time.

The project's page lists only its latest jobs and any still under way (projectpage.JOBS_SHOWN);
this is where the rest are, each a row that opens it there. The rows are a part of the page
(pages.region), so its refresh keeps the search they were found by; the form isn't, so what's
being typed in it is never lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlencode

from . import plan
from .projectpage import href, workflow_words
from .store import ACTIVE, LOOKS, Job, Project

PER_PAGE = 50
STATES = {"": "Any", "active": "Under way", "done": "Done", "failed": "Failed", "killed": "Stopped"}


@dataclass
class Search:
    """What the page was asked for, from its address."""

    text: str = ""  # words that must all be in its title or request, or its number ("#12" or "12")
    state: str = ""  # one of STATES
    workflow: str = ""  # a workflow's key; "" for any
    page: int = 1

    @classmethod
    def of(cls, params) -> Search:
        state, number = params.get("state", ""), params.get("page", "")
        return cls(
            text=" ".join(params.get("q", "").split())[:200],
            state=state if state in STATES else "",
            workflow=params.get("workflow", "")[:100],
            page=min(int(number), 10_000) if number.isdigit() and int(number) > 0 else 1,
        )

    def narrowed(self) -> bool:
        return bool(self.text or self.state or self.workflow)

    def query(self, **changed) -> str:
        """Its address's query, with `changed`: what a link to another of its pages needs."""
        values = {"q": self.text, "state": self.state, "workflow": self.workflow, "page": self.page} | changed
        kept = {k: v for k, v in values.items() if v and (k != "page" or v > 1)}
        return f"?{urlencode(kept)}" if kept else ""


def matches(job: Job, project: Project, search: Search) -> bool:
    if search.state == "active" and job.state not in ACTIVE or search.state not in ("", "active") and job.state != search.state:
        return False
    if search.workflow and plan.workflow_for(job, project)[0] != search.workflow:
        return False
    number = search.text.removeprefix("#")
    if number.isdigit() and int(number) == job.id:
        return True
    words = f"{plan.title(job)}\n{job.request}".lower()
    return all(w in words for w in search.text.lower().split())


def form(project: Project, jobs: list[Job], search: Search, flows: dict[str, str]) -> str:
    """The search: words, then how it went and its workflow (each searches as it's picked). Its
    workflows are those the project's jobs ran."""
    keys = {plan.workflow_for(j, project)[0] for j in jobs} | ({search.workflow} if search.workflow else set())
    named = sorted(((flows.get(k) or k.replace("_", " ").capitalize(), k) for k in keys), key=lambda x: x[0].lower())
    states = "".join(f"<option value='{k}'{' selected' if k == search.state else ''}>{escape(v)}</option>" for k, v in STATES.items())
    workflows = "<option value=''>Any</option>" + "".join(
        f"<option value='{escape(k)}'{' selected' if k == search.workflow else ''}>{escape(name)}</option>" for name, k in named
    )
    where = f"/p/{escape(project.slug)}/jobs"
    clear = f"<a class=clear href='{where}'>Clear</a>" if search.narrowed() else ""
    return (
        f"<form method=get action='{where}' class='card job-search' role=search><div class=search-row>"
        f"<input type=search name=q value='{escape(search.text)}' placeholder='Words from its title or request, or its #' aria-label='Search the jobs'>"
        "<button class=primary>Search</button></div><div class=ask-row>"
        f"<label>State <select name=state onchange='this.form.submit()'>{states}</select></label>"
        f"<label>Workflow <select name=workflow onchange='this.form.submit()'>{workflows}</select></label>{clear}</div></form>"
    )


def results(project: Project, jobs: list[Job], search: Search, flows: dict[str, str], now: datetime) -> str:
    """The jobs the search found, the latest first, a page of them, with the way to the others."""
    if not jobs:
        return "<p class=empty>No jobs yet.</p>"
    found = [j for j in jobs if matches(j, project, search)]
    if not found:
        return "<p class=empty>No jobs match.</p>"
    pages = -(-len(found) // PER_PAGE)
    at = min(search.page, pages)
    shown = found[(at - 1) * PER_PAGE : at * PER_PAGE]
    of = f" of {len(jobs)}" if search.narrowed() else ""
    count = f"{len(found)} job{'s' if len(found) != 1 else ''}{of}"
    if pages > 1:
        first = (at - 1) * PER_PAGE + 1
        count = f"{first}–{first + len(shown) - 1} of {len(found)}{' found' if search.narrowed() else ' jobs'}"
    where = f"/p/{escape(project.slug)}/jobs"
    newer = f"<a href='{where}{escape(search.query(page=at - 1))}'>‹ Newer</a>" if at > 1 else ""
    older = f"<a class=older href='{where}{escape(search.query(page=at + 1))}'>Older ›</a>" if at < pages else ""
    pager = f"<nav class=pager aria-label='More jobs'>{newer}{older}</nav>" if newer or older else ""
    rows = "".join(row(j, project, search, flows, now) for j in shown)
    return f"<p class=hint>{count}</p><ol class='job-rows found'>{rows}</ol>{pager}"


def row(job: Job, project: Project, search: Search, flows: dict[str, str], now: datetime) -> str:
    """A job: its title, how it went, its workflow, number and when it began; and, if the search's
    words are in its request but not its title, where in the request."""
    title = plan.title(job)
    words = search.text.lower().split()
    excerpt = _excerpt(job.request, words) if words and not all(w in title.lower() for w in words) else ""
    return (
        f"<li class=job-row><div class=row-head><a class=job-title href='{href(job)}'>{escape(title)}</a>{badge(job)}</div>"
        f"<p class=muted>{workflow_words(job, project, flows)} · #{job.id} · {_day(job.created_at, now)}</p>"
        + (f"<p class=excerpt>{escape(excerpt)}</p>" if excerpt else "")
        + "</li>"
    )


def badge(job: Job) -> str:
    """How it went, by its state alone (pages.badge has more to go on, for a job being looked at)."""
    if job.state in ACTIVE:
        tone, word = {"running": ("running", "Working"), "waiting": ("waiting", "Needs you")}.get(job.state, ("queued", "Queued"))
    else:  # a question or a look for proposals ships nothing
        tone, word = "finished", {"done": "Done" if job.start in LOOKS else "Live", "killed": "Stopped", "failed": "Failed"}.get(job.state, "Over")
    return f"<span class='badge {tone}'>{word}</span>"


def _excerpt(request: str, words: list[str], around: int = 60, size: int = 160) -> str:
    flat = " ".join(request.split())
    low = flat.lower()
    at = min((low.find(w) for w in words if w in low), default=-1)
    if at < 0:
        return ""
    start = max(at - around, 0)
    return ("…" if start else "") + flat[start : start + size] + ("…" if start + size < len(flat) else "")


def _day(stamp: str, now: datetime) -> str:
    """The day it began: "3 Nov", or "3 Nov 2025" in another year (UTC's day)."""
    try:
        at = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    return f"{at.day} {at:%b}" + (f" {at.year}" if at.year != now.year else "")
