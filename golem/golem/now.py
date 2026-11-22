"""Now: everything golem is doing, across projects, a job at a time.

What needs Riggy first (a job waiting on him, answered right there; golem's proposals, thumbed
right there), then what's running, what's up next, and what finished lately. Each job is drawn
by its route through its workflow (route.py). golem's own news, what's true right now (out of
usage, paused, upgrading), sits on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from . import plan, route
from .conversation import proposals_card
from .pages import LaneData, ask_card, badge, headline, route_strip, signin_card, when, _dt
from .store import Proposal, Project, Said
from .watch import span

RECENT = timedelta(days=7)  # finished jobs shown
RECENT_SHOWN = 6


@dataclass
class News:
    """What's true of golem right now, and what it said lately."""

    conditions: list[str]  # one line each: "Out of Claude usage: …"
    said: list[Said]  # its latest golem-wide messages, newest last


def page(active: list[LaneData], finished: list[LaneData], waiting: dict[str, tuple[Project, list[Proposal]]], news: News,
         flows: dict[str, str], signed_in: bool, now: datetime) -> str:  # fmt: skip
    """`active`: every unfinished job's lane, in the queue's order; `finished`: the latest finished
    ones; `waiting`: open proposals by project; `flows`: workflow names by key."""
    needs = [x for x in active if x.job.state == "waiting"]
    running = [x for x in active if x not in needs and _under_way(x) and not x.project.paused]
    later = [x for x in active if x not in needs and x not in running]
    recent = [x for x in finished if x.job.updated_at and now - _dt(x.job.updated_at) < RECENT][:RECENT_SHOWN]
    html = "" if signed_in else signin_card("/")
    html += _news(news)
    asks = "".join(_card(x, flows, signed_in, now, ask=True) for x in needs)
    thumbs = "".join(_proposals(project, items) for project, items in waiting.values()) if signed_in else ""
    if asks or thumbs:
        html += "<h2 class=section-title>Needs you</h2>" + asks + thumbs
    if running:
        html += "<h2 class=section-title>Running</h2>" + "".join(_card(x, flows, signed_in, now) for x in running)
    if later:
        html += "<h2 class=section-title>Up next</h2><ol class='jobs-list'>" + "".join(_row(x, flows, now) for x in later) + "</ol>"
    if recent:
        html += "<h2 class=section-title>Recently done</h2><ol class='jobs-list'>" + "".join(_row(x, flows, now) for x in recent) + "</ol>"
    if not (needs or running or later or thumbs):
        what = "Start one with <a href=/new>+ New project</a>, or ask for something on a project's page." if signed_in else "Sign in to start one."
        html += f"<p class=empty>Nothing under way. {what}</p>"
    return html


def _under_way(lane: LaneData) -> bool:
    return lane.job.state == "running" or any(s.started_at for s in lane.steps)


def _what(lane: LaneData, flows: dict[str, str]) -> str:
    job = lane.job
    key, mode = plan.workflow_for(job, lane.project)  # one that hasn't started says by its playbook
    name = flows.get(key) or plan.what(job).capitalize()
    return escape(name) + (f", {escape(mode)}" if mode else "")


def _card(lane: LaneData, flows: dict[str, str], signed_in: bool, now: datetime, ask: bool = False) -> str:
    p, job = lane.project, lane.job
    href = f"/p/{escape(p.slug)}/jobs/{job.id}"
    note = f"<p class=note>▸ {escape(lane.notes[-1])}</p>" if lane.notes and not ask else ""
    return (
        f"<section class='card job c{lane.color}' id='job-{job.id}'><header class=card-head>"
        f"<h2><a href='/p/{escape(p.slug)}'><span class=swatch></span>{escape(p.name)}</a>"
        f"<a class=job-title href='{href}'>{escape(plan.title(job))}</a></h2>{badge(lane)}</header>"
        f"<p class=sub>{_what(lane, flows)} · <a href='{href}'>job #{job.id}</a> · {headline(lane, now)}</p>"
        + route_strip(job, route.chips(job, lane.steps), ahead=4)
        + note
        + (ask_card(lane, signed_in, back="/", reply=True) if ask else "")
        + "</section>"
    )


def _row(lane: LaneData, flows: dict[str, str], now: datetime) -> str:
    p, job = lane.project, lane.job
    href = f"/p/{escape(p.slug)}/jobs/{job.id}"
    if job.state in ("done", "failed", "killed"):
        word = {"done": "Done", "killed": "Stopped", "failed": "Failed"}[job.state]
        at = f"{word} {span(now - _dt(job.updated_at))} ago" if job.updated_at else word
        said = plan.last_said(lane.steps) if job.start == "look" else ""
        extra = f"<p class=said>{escape(said[:240])}</p>" if said else ""
    else:
        at, extra = headline(lane, now), ""
    return (
        f"<li class='c{lane.color}'><div class=row-head><a href='/p/{escape(p.slug)}'><span class=swatch></span>{escape(p.name)}</a>"
        f"<a class=job-title href='{href}'>{escape(plan.title(job))}</a><span class=muted>{_what(lane, flows)}</span>"
        f"<span class=when>{at}</span></div>{route_strip(job, route.chips(job, lane.steps), small=True, ahead=4)}{extra}</li>"
    )


def _proposals(project: Project, items: list[Proposal]) -> str:
    """A project's undecided proposals, folded to a line: open it to thumb each."""
    titles = ", ".join(p.title for p in items[:3]) + ("…" if len(items) > 3 else "")
    return (
        f"<details class='card proposals-now' data-key=proposals-{escape(project.slug)}><summary class=card-head><h2>"
        f"<a href='/p/{escape(project.slug)}'>{escape(project.name)}</a><span class=job-title>golem proposes {len(items)}: "
        f"<span class=muted>{escape(titles)}</span></span></h2><span class='badge waiting'>Thumbs</span></summary>"
        f"{proposals_card(items, '/')}</details>"
    )


def _news(news: News) -> str:
    if not news.conditions and not news.said:
        return ""
    lines = "".join(f"<p>{escape(c)}</p>" for c in news.conditions)
    said = "".join(f"<li><span class=when>{when(_dt(m.at), datetime.now(_dt(m.at).tzinfo))}</span> {escape(m.text[:300])}</li>" for m in news.said[::-1])
    older = f"<details class=news-said><summary>golem's news</summary><ul>{said}</ul></details>" if said else ""
    return f"<section class='news{' live' if news.conditions else ''}' aria-label=\"golem's news\">{lines}{older}</section>"


# --- projects -----------------------------------------------------------------------------


def projects(lanes: list[LaneData], flows: dict[str, str], tags: dict[str, int], images: dict[str, str], signed_in: bool, now: datetime) -> str:
    """Every project as a card: its screenshot if it's live, what it is, where, and its latest
    job's route."""
    if not lanes:
        what = "Start one with <a href=/new>+ New project</a>." if signed_in else ""
        return f"<p class=empty>No projects yet. {what}</p>"
    cards = []
    for x in lanes:
        p, job = x.project, x.job
        shot = f"<a class=shot href='/p/{escape(p.slug)}'><img src='{escape(images[p.slug])}' alt='' loading=lazy></a>" if p.slug in images else ""
        tag = f"<span class='tag-chip c{tags[p.tag]}'>{escape(p.tag)}</span>" if p.tag in tags else ""
        site = f"<a href='{escape(x.site)}' rel=noopener>{escape(x.site.removeprefix('https://').removeprefix('http://').rstrip('/'))} ↗</a>"
        latest = ""
        if job:
            href = f"/p/{escape(p.slug)}/jobs/{job.id}"
            latest = (
                f"<p class=latest><a class=job-title href='{href}'>{escape(plan.title(job))}</a> <span class=muted>{_what(x, flows)} · {headline(x, now)}</span></p>"
                + route_strip(job, route.chips(job, x.steps), small=True, ahead=4)
            )
        cards.append(
            f"<section class='card project-card c{x.color}'>{shot}<header class=card-head><h2><a href='/p/{escape(p.slug)}'>"
            f"<span class=swatch></span>{escape(p.name)}</a></h2>{badge(x)}</header>"
            f"<p class=sub>{escape(p.summary[:220])}</p><p class=meta>{tag}{site}</p>{latest}</section>"
        )
    return f"<div class=project-grid>{''.join(cards)}</div><p class=hint><a href=/projects>The public page of live projects ↗</a></p>"
