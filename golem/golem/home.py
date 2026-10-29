"""The web app's top level, one tab each: Home, Projects and Settings.

Home is what Riggy came to see: what needs him (answered right there: try the site and
say how it is, pick an option, answer a question), what's being worked on and when it's
next live, what's coming up, and what finished lately; and a box for a new idea. Projects
is the conversations, like a messaging app's list: golem's own (new ideas and its news)
first, then each project's, latest first, opening its page (conversation.py). Settings has
signing in, notifications, pausing all work, the tags, and each kind of step's model and effort.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from . import pwa
from .insights import Report
from .pages import (LaneData, _dt, _hidden, _short, action_form, ask_card, badge, defaults_table, dots, headline,
                    signin_card, when)  # fmt: skip
from .store import Said, Tag
from .upgrade import SHOWN, Upgrades
from .watch import span

RECENT = timedelta(days=7)  # finished jobs shown on Home
RECENT_SHOWN = 5
COLORS = ("blue", "orange", "green", "yellow", "pink", "dark green", "purple", "red")  # c1-c8


def home(active: list[LaneData], idle: list[LaneData], signed_in: bool, now: datetime) -> str:
    """`active`: projects with a job under way, in the queue's order; `idle`: the others, with their last job."""
    needs = [lane for lane in active if lane.job.state == "waiting"]
    working = [lane for lane in active if lane not in needs and _started(lane) and not lane.project.paused]
    later = [lane for lane in active if lane not in needs and lane not in working]
    recent = sorted(
        (lane for lane in idle if lane.job and lane.job.updated_at and now - _dt(lane.job.updated_at) < RECENT),
        key=lambda lane: lane.job.updated_at, reverse=True,
    )[:RECENT_SHOWN]  # fmt: skip
    html = "" if signed_in else signin_card("/")
    if needs:
        html += "<h2 class=section-title>Needs you</h2>" + "".join(_needs(lane, signed_in) for lane in needs)
    if working:
        html += "<h2 class=section-title>Working on</h2>" + "".join(_glance(lane, now) for lane in working)
    if later:
        html += "<h2 class=section-title>Up next</h2><ol class=compact>" + "".join(_later(lane, now) for lane in later) + "</ol>"
    if recent:
        html += "<h2 class=section-title>Finished lately</h2><ol class=compact>" + "".join(_recent(lane, now) for lane in recent) + "</ol>"
    if not (needs or working or later):
        what = "Describe an idea below and golem starts on it." if signed_in else "Sign in to describe an idea."
        html += f"<p class=empty>Nothing under way. {what}</p>"
    return html


def _started(lane: LaneData) -> bool:
    return any(s.started_at for s in lane.steps)


def _needs(lane: LaneData, signed_in: bool) -> str:
    p = lane.project
    return (
        f"<section class='card c{lane.color}' id='p-{escape(p.slug)}'><header class=card-head>"
        f"<h2><a href='/p/{escape(p.slug)}'><span class=swatch></span>{escape(p.name)}</a></h2>{badge(lane)}</header>"
        f"<p class=sub>“{escape(_short(lane.job.request, 140))}”</p>"
        + ask_card(lane, signed_in, back="/", reply=True)
        + "</section>"
    )


def _glance(lane: LaneData, now: datetime) -> str:
    p = lane.project
    return (
        f"<a class='card glance c{lane.color}' id='p-{escape(p.slug)}' href='/p/{escape(p.slug)}'><header class=card-head>"
        f"<h2><span class=swatch></span>{escape(p.name)}</h2>{badge(lane)}</header>"
        f"<div class=line><span>{headline(lane, now)}</span></div>"
        f"<div class=line>{dots(lane)}<span>“{escape(_short(lane.job.request, 60))}”</span></div></a>"
    )


def _later(lane: LaneData, now: datetime) -> str:
    p, job = lane.project, lane.job
    first = next((s for s in lane.steps if s.state == "pending"), None)
    eta = lane.etas.get(first.position) if first else None
    at = "paused" if p.paused else when(eta.start, now) if eta else f"#{lane.position}" if lane.position else "queued"
    what = "new project" if job.kind == "new" else job.playbook if job.playbook in ("options", "question", "proposals") else "change"
    return (
        f"<li class=c{lane.color}><a href='/p/{escape(p.slug)}'><span class=swatch></span>{escape(p.name)}</a>"
        f"<span class=muted>{what}: {escape(_short(job.request, 80))}</span><span class=when>{at}</span></li>"
    )


def _recent(lane: LaneData, now: datetime) -> str:
    p, job = lane.project, lane.job
    word = {"done": "live", "killed": "stopped", "failed": "failed"}.get(job.state, job.state)
    return (
        f"<li class=c{lane.color}><a href='/p/{escape(p.slug)}'><span class=swatch></span>{escape(p.name)}</a>"
        f"<span class=muted>{word}: {escape(_short(job.request, 80))}</span>"
        f"<span class=when>{span(now - _dt(job.updated_at))} ago</span></li>"
    )


# --- projects ---------------------------------------------------------------------------


@dataclass
class Thread:
    """One conversation in the list."""

    href: str
    name: str
    color: int  # 0 for golem's own
    status: str  # a badge, or ""
    last: Said | None
    about: str  # what shows when there's no message to show
    at: str  # when it last moved, for the order


def projects(threads: list[Thread], signed_in: bool, now: datetime) -> str:
    rows = []
    for t in threads:
        text = (t.last.text or "A picture") if signed_in and t.last else t.about
        who = "You: " if signed_in and t.last and t.last.who == "riggy" else ""
        ago = f"{span(now - _dt(t.at))}" if t.at else ""
        rows.append(
            f"<li class=c{t.color}><a href='{escape(t.href)}'><span class=avatar>{escape(t.name[:1].upper())}</span>"
            f"<b>{escape(t.name)}</b><span class=when>{ago}</span>"
            f"<span class=last>{t.status}{escape(who + _short(text, 120))}</span></a></li>"
        )
    html = f"<ol class=threads>{''.join(rows)}</ol>"
    if not signed_in:
        html += "<p class=hint>Sign in (Settings) to see the conversations.</p>"
    return html + "<p class=hint><a href=/projects>The public page of live projects ↗</a></p>"


# --- settings ---------------------------------------------------------------------------


def settings(signed_in: bool, paused: bool, kinds: list[str], defaults: dict[str, tuple[str, str]],
             fallback: tuple[str, str], report: Report, tags: list[Tag], counts: dict[str, int],
             upgrades: Upgrades | None = None) -> str:  # fmt: skip
    if signed_in:
        account = (
            "<section class=card><h2>Signed in</h2><p class=sub>On this device. Sign out to stop it changing things.</p>"
            "<form method=post action=/logout class=row-form><input type=hidden name=back value=/settings>"
            "<button>Sign out</button></form></section>"
        )
        work = (
            "<section class=card><h2>All work</h2><p class=sub>"
            + ("Paused: nothing new starts until it's resumed." if paused else "Running: golem takes the next job when one finishes.")
            + "</p><div class=row-form>"
            + action_form("resume-all" if paused else "pause-all", "Resume all" if paused else "Pause all", back="/settings")
            + "</div></section>"
        )
        account += pwa.panel().replace("class=notify-panel", "class='card notify-panel'") + work
        account += version_card(upgrades) if upgrades else ""
    else:
        account = signin_card("/settings")
    more = (
        "<section class=card><ul class=links-list>"
        "<li><a href=/insights>Insights</a><span class=sub>Where the time and Claude usage go, by kind of step, project and model.</span></li>"
        "<li><a href=/projects>Public projects page ↗</a><span class=sub>A card for each live project, for anyone.</span></li>"
        "</ul></section>"
    )
    return account + more + tags_card(tags, counts, signed_in) + defaults_table(kinds, defaults, fallback, report, signed_in)


def version_card(upgrades: Upgrades) -> str:
    """golem's own version: what's running, what's new on GitHub, and upgrading to it."""
    back, v, state = "/settings", upgrades.version(), upgrades.state
    check = action_form("check-version", "Check now", back=back)
    auto = action_form("auto-upgrade", "Upgrade automatically: on" if upgrades.auto else "Upgrade automatically: off",
                       value="off" if upgrades.auto else "on", back=back)  # fmt: skip
    head = "<section class='card version'><h2>golem</h2>"
    if v is None:
        return head + f"<p class=sub>Not checked for a new version yet.</p><div class=row-form>{check}</div></section>"
    if not v.get("enabled"):
        return head + f"<p class=sub>{escape(upgrades.blocked(v))}</p></section>"
    running, behind = str(v.get("running") or v.get("checkout") or "")[:7], v.get("behind", 0)
    when = f" Checked {escape(str(v.get('checked', ''))[:16].replace('T', ' '))} UTC." if v.get("checked") else ""
    if behind:
        said = f"Running <code>{escape(running)}</code>; GitHub's main has {behind} new commit{'s' if behind != 1 else ''}.{when}"
    else:
        said = f"Running <code>{escape(running)}</code>, up to date.{when}"
    body = f"<p class=sub>{said}</p>"
    if behind:
        body += "<ul class=links-list>" + "".join(
            f"<li><code>{escape(c['sha'][:7])}</code>{escape(c['subject'])}</li>" for c in v.get("commits", [])[:SHOWN]
        ) + (f"<li class=sub>and {behind - SHOWN} more</li>" if behind > SHOWN else "") + "</ul>"
    if blocked := upgrades.blocked(v):
        body += f"<p class=hint>{escape(blocked)}</p>"
    last = v.get("upgrade") or {}
    if last.get("state") in ("failed", "rolled back"):
        body += f"<p class=hint>Last upgrade, to {escape(str(last.get('to', ''))[:7])}: {escape(last['state'])}. {escape(last.get('detail', ''))}</p>"
    if state == "pending":
        body += "<p class=hint>Upgrading once the running step finishes.</p>"
    elif state == "started":
        body += "<p class=hint>Upgrading: rebuilding and restarting, back in a few minutes.</p>"
    button = ""
    if behind and not blocked and not state:
        button = action_form("upgrade", "Upgrade now", back=back, confirm="Upgrade golem now? It restarts once nothing is running.")
    return head + body + f"<div class=row-form>{button}{check}{auto}</div></section>"


def tags_card(tags: list[Tag], counts: dict[str, int], signed_in: bool) -> str:
    """The sorts of project there are, each in its color on the projects page."""
    back = "/settings#tags"
    rows = []
    for t in tags:
        n = counts.get(t.name, 0)
        row = f"<span class=swatch></span><b>{escape(t.name)}</b><span>{n} project{'' if n == 1 else 's'}</span>"
        if signed_in:
            colors = "".join(f"<option value={i}{' selected' if i == t.color else ''}>{c}</option>" for i, c in enumerate(COLORS, 1))
            row += (
                f"<form method=post action=/act class=inline>{_hidden(action='tag-color', name=t.name, back=back)}"
                f"<select name=value aria-label='Color of {escape(t.name)}' onchange='this.form.submit()'>{colors}</select></form>"
                f"<form method=post action=/act class=inline>{_hidden(action='tag-rename', name=t.name, back=back)}"
                f"<input name=value value='{escape(t.name)}' required maxlength=20 aria-label='New name for {escape(t.name)}'>"
                "<button>Rename</button></form>"
                f"<form method=post action=/act class=inline onsubmit='return confirm({escape(json.dumps(f'Delete the {t.name} tag?'))})'>"
                f"{_hidden(action='tag-delete', name=t.name, back=back)}<button class=danger>Delete</button></form>"
            )
        rows.append(f"<div class='row-form c{t.color}'>{row}</div>")
    add = (
        f"<form method=post action=/act class=row-form>{_hidden(action='tag-add', back=back)}"
        "<input name=value placeholder='A new tag, like Dev Tool' required maxlength=20 aria-label='New tag'>"
        "<button>Add tag</button></form>"
        if signed_in
        else "<p class=hint>Sign in to change them.</p>"
    )
    return (
        "<section class='card settings' id=tags><h2>Tags</h2><p class=sub>The big-picture sorts of project, each in its "
        "color on the projects page. golem gives each new idea one of these, or none, and adds one only for a broad sort "
        "of project none of them covers. A topic or genre isn't a tag.</p>"
        + ("".join(rows) or "<p class=empty>No tags.</p>")
        + add
        + "</section>"
    )
