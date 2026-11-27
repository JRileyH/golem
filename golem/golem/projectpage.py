"""A project's page: its jobs on one side, the one being looked at on the other.

On the left, the way to ask for something (always a job of its own, with its workflow and mode
picked or left to golem), golem's proposals for the project while there are some, and its latest
jobs and any still under way, newest first, each drawn by its route; the rest are on its All jobs
page, to search (jobhistory.py). On the right, the job: where it is, what it's cost and what it
needs from Riggy (answered right there), a note for its next step, its steps as a list, its
activity (what each block did, notes, questions and replies), and the request and its runs.

On a phone it's one side at a time: the list at /p/<slug>, the job at /p/<slug>/jobs/<id>. The
page refreshes in parts (pages.region).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from . import plan, route
from .conversation import proposals_card, thread
from .ledger import Row
from .pages import (LaneData, _count, _hidden, _money, action_form, ask_card, badge, headline, region, request_card, route_list,
                    route_strip, trail, working)  # fmt: skip
from .store import ACTIVE, INTERNAL, LOOKS, Job, Project, Proposal, Said, Step
from .watch import span

JOBS_SHOWN = 12  # the latest jobs in the list (and any older one still under way); the rest are on the All jobs page


@dataclass
class Picks:
    """What a request on the page can ask for, rather than leave to golem."""

    workflows: list[tuple[str, str]]  # (key, name): changes, then those that only look
    modes: list[str]


def href(job: Job) -> str:
    """Where a job is shown: its project's page, open on it; golem's own jobs, on the Reports page."""
    return "/reports" if job.slug == INTERNAL else f"/p/{job.slug}/jobs/{job.id}"


def chosen(jobs: list[Job]) -> Job | None:
    """The job a project's page opens on: one waiting on Riggy, else one running, else the next
    to run, else the latest that changed it (else the latest). `jobs`: newest first."""
    for want in ("waiting", "running"):
        if found := next((j for j in jobs if j.state == want), None):
            return found
    return (next((j for j in reversed(jobs) if j.state in ACTIVE), None) or next((j for j in jobs if j.start not in LOOKS), None)
            or (jobs[0] if jobs else None))  # fmt: skip


def layout(project: Project, parts: dict[str, str], lane: LaneData | None, signed_in: bool, picks: Picks,
           focus: str, details: str, links: dict[str, str]) -> str:  # fmt: skip
    """The page around its parts. `focus`: which side a phone shows, "list" or "job"; `links`:
    where it's live and its repo, by name."""
    out = "".join(f"<a href='{escape(url)}' target=_blank rel=noopener>{escape(name)} ↗</a>" for name, url in links.items())
    left = (
        "<section class=jobs-pane aria-label=Jobs>"
        f"<header class=project-head><p>{escape(project.summary)}</p><p class=out>{out}</p></header>"
        + (ask_form(project, picks) if signed_in else "")
        + region("proposals", parts.get("proposals", ""))
        + region("jobs", parts["jobs"])
        + "</section>"
    )
    if lane is None or lane.job is None:
        right = "<section class=job-pane><p class=empty>No jobs yet." + (" Ask for something and it starts one." if signed_in else "") + "</p></section>"
    else:
        right = (
            f"<section class=job-pane aria-label='Job #{lane.job.id}'>"
            + region("head", parts["head"])
            + region("steps", parts["steps"])
            + region("activity", parts.get("activity", ""))
            + details
            + "</section>"
        )
    return f"<div class=panes data-focus={focus}>{left}{right}</div>"


# --- the left: asking, proposals, the jobs ----------------------------------------------------


def ask_form(project: Project, picks: Picks) -> str:
    """A request: a job of its own. A workflow on Auto is golem's pick (the request call), as the
    words say; the mode on Auto, the project's default (only while golem has modes: plan.MODES)."""
    flows = "".join(f"<option value='{escape(k)}'>{escape(n)}</option>" for k, n in picks.workflows)
    modes = "".join(f"<option value='{escape(m)}'>{escape(m.capitalize())}</option>" for m in picks.modes)
    mode = f"<label>Mode <select name=mode><option value=auto>Auto</option><option value=''>Usual</option>{modes}</select></label>" if modes else ""
    return (
        f"<form method=post action='/p/{escape(project.slug)}/ask' class='card ask-form' data-send>"
        "<label class=ask-label for=ask-text>Ask for…</label>"
        "<textarea id=ask-text name=text rows=2 required placeholder='A change, or a question about it…'></textarea>"
        "<div class=ask-row>"
        f"<label>Workflow <select name=workflow><option value=''>Auto</option>{flows}</select></label>"
        + mode
        + "<button class=primary>Ask</button></div></form>"
    )


def proposals_part(project: Project, waiting: list[Proposal], back: str) -> str:
    if not waiting:
        return ""
    oldest = sorted(waiting, key=lambda p: p.id)
    titles = ", ".join(p.title for p in oldest[:3]) + ("…" if len(waiting) > 3 else "")
    return (
        f"<details class='card proposals-here' id=proposals data-key=proposals-{escape(project.slug)}><summary><b>golem proposes {len(waiting)}</b> "
        f"<span class=muted>{escape(titles)}</span></summary>{proposals_card(waiting, back)}</details>"
    )


def jobs_part(lanes: list[LaneData], selected: int | None, flows: dict[str, str], now: datetime, total: int, everything: str) -> str:
    """The jobs, newest first: each its title, workflow, where it is and its route. `total`: how
    many it has; `everything`: where to search them all (jobhistory.py), when they aren't all here."""
    if not lanes:
        return "<p class=empty>No jobs yet.</p>"
    rows = []
    for x in lanes:
        job = x.job
        on = job.id == selected
        rows.append(
            f"<li class='job-row{' on' if on else ''}'{' aria-current=true' if on else ''}><div class=row-head>"
            f"<a class=job-title href='{href(job)}'>{escape(plan.title(job))}</a>{badge(x)}</div>"
            f"<p class=muted>{what(x, flows)} · #{job.id} · {headline(x, now)}</p>"
            + route_strip(job, route.chips(job, x.steps), small=True, ahead=3)
            + "</li>"
        )
    more = f"<p class=hint><a href='{escape(everything)}'>Search all {total} jobs ›</a></p>" if everything else ""
    return f"<h2 class=section-title>Jobs</h2><ol class=job-rows>{''.join(rows)}</ol>{more}"


def what(lane: LaneData, flows: dict[str, str]) -> str:
    return workflow_words(lane.job, lane.project, flows)


def workflow_words(job: Job, project: Project | None, flows: dict[str, str]) -> str:
    """The job's workflow and mode, in words."""
    key, mode = plan.workflow_for(job, project)
    return escape(flows.get(key) or plan.what(job).capitalize()) + (f", {escape(mode)}" if mode else "")


# --- the right: the job ------------------------------------------------------------------------


def head_part(lane: LaneData, flows: dict[str, str], signed_in: bool, now: datetime, cost: Row | None = None) -> str:
    """Where the job is and what it needs: its title and state, what it's cost (ledger.py), what
    the running step is doing, what it's waiting on Riggy for (answered here), a note for its next
    step, and its controls."""
    job, back = lane.job, href(lane.job)
    active = job.state in ACTIVE
    answer = plan.last_said(lane.steps) if job.start == "look" and not active else ""
    note = ""
    if signed_in and active and job.state != "waiting":
        note = (
            f"<details class=add-note data-key=note-{job.id}><summary>Add a note</summary>"
            f"<form method=post action=/act class=reply data-send>{_hidden(action='note', job=job.id, back=back)}"
            "<textarea name=value rows=1 required placeholder='For the job’s next step…' aria-label='A note for the job’s next step'></textarea>"
            "<button class=primary>Send</button></form></details>"
        )
    controls = ""
    if signed_in and active:
        first = "" if lane.position == 1 else action_form("move", "Run next", job=job.id, value="first", back=back)
        controls = (
            "<div class='row-form job-controls'>" + first
            + action_form("move", "Run last", job=job.id, value="last", back=back)
            + action_form("kill", "Stop job", job=job.id, back=back, confirm=f"Stop job {job.id} for good?", danger=True)
            + "</div>"
        )  # fmt: skip
    return (
        f"<header class='card job-head c{lane.color}'><div class=card-head><h2>{escape(plan.title(job))}</h2>{badge(lane)}</div>"
        f"<p class=sub>{what(lane, flows)} · job #{job.id} · {headline(lane, now)}</p>"
        + (costs(cost, active) if cost else "")
        + (f"<p class=answer>{escape(answer)}</p>" if answer else "")
        + (working(lane, signed_in, now) if active else "")
        + ask_card(lane, signed_in, back=back, reply=True)
        + note
        + controls
        + "</header>"
    )


def costs(r: Row, active: bool) -> str:
    """What the job's cost, in a line (ledger.py): usage at API prices, tokens, how long its agents
    ran, its cost by model, and fixes."""
    if not r.runs:
        return ""
    bits = [f"{_money(r.cost)} at API prices", f"{_count(r.tokens_in)} in / {_count(r.tokens_out)} out", f"agents {span(timedelta(seconds=r.agent_seconds))}"]
    if r.cost:
        bits.append(", ".join(f"{m} {round(100 * c / r.cost)}%" for m, c in sorted(r.by_model.items(), key=lambda x: -x[1]) if c))
    if r.fixes:
        bits.append(f"{r.fixes} fix{'es' if r.fixes != 1 else ''}")
    return f"<p class=costs><b>{'Cost so far' if active else 'Cost'}:</b> {escape(' · '.join(bits))}</p>"


def steps_part(lane: LaneData, signed_in: bool, now: datetime, trace: list[Step]) -> str:
    """Its route stood up; for a job from before workflows (it has no copy of one), the way it went."""
    if not lane.job.graph and lane.job.state not in ACTIVE:
        return f"<section class=card><h2>The way it went</h2><p class=muted>This job is from before workflows.</p>{trail(trace, lane.job)}</section>"
    return f"<section class='card steps-card'><h2>Steps</h2>{route_list(lane, route.chips(lane.job, lane.steps), signed_in, now)}</section>"


def activity_part(said: list[Said], signed_in: bool) -> str:
    if not signed_in:
        return "<p class=hint>Sign in (Settings) to see what golem and you said about this job.</p>"
    return "<h2 class=section-title>Activity</h2>" + thread(said, "Nothing said about this job yet.")


def details(job: Job, runs: list[dict]) -> str:
    """The request in full, and the job's agent runs: folded."""
    rows = "".join(
        f"<tr><td>{escape(r['phase'])}</td><td>{escape(r['model'])}</td><td>{escape(str(r['outcome'] or 'running'))}</td>"
        f"<td>{r['num_turns'] or ''}</td><td>{_tokens(r)}</td><td>{_cost(r)}</td><td>{r['started_at'][:16].replace('T', ' ')}</td></tr>"
        for r in runs
    )
    table = (
        "<h3 class=sub>Agent runs</h3><div class=runs><table><tr><th>Run</th><th>Model</th><th>Outcome</th>"
        f"<th>Turns</th><th>Tokens in/out</th><th>Usage</th><th>Started</th></tr>{rows}</table></div>"
        if rows
        else ""
    )
    return f"<details class=job-details data-key=details-{job.id}><summary>The request{' and its runs' if rows else ''}</summary>{request_card(job)}{table}</details>"


def _cost(run: dict) -> str:
    return f"${run['cost_usd']:.2f}" if run["cost_usd"] else ""


def _tokens(run: dict) -> str:
    if run["input_tokens"] is None and run["output_tokens"] is None:
        return ""
    return f"{run['input_tokens'] or 0:,} / {run['output_tokens'] or 0:,}"
