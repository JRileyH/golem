"""The Reports page (/reports): what got in the way of golem's workers, about golem itself
(reports.py), from both sources: their own reports and golem's incidents. Riggy reads it to see
how many there are and whether they say anything, turns golem's triage of them on or off, and
takes or turns down the fixes to golem it proposes (triage.py): taking one opens its prompt
(/reports/proposals/<id>), to paste into a Claude Code session in golem's repo. Once a big
change to golem has made most of them out of date, he clears them all, triage too, after an
are-you-sure. Signed in only: they quote runs and errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlencode

from . import triage
from .pages import _hidden, action_form
from .store import Job, Proposal, Report
from .watch import span

STATES = (("new", "New"), ("triaged", "Triaged"), ("accepted", "Taken"), ("dismissed", "Dismissed"), ("", "All"))
SOURCES = (("", "Both"), ("agent", "Workers'"), ("golem", "Golem's"))
TONES = {"blocked": "trouble", "costly": "waiting", "minor": "finished"}  # the badges' colors
SHOWN = 200

ABOUT = (
    "<p class=hint>What got in the way of golem's workers, about golem itself, not the projects: their own "
    "reports (<code>golem report</code>, at most two a step) and golem's incidents (a failed try at a step, "
    "the loop guard, merges with main that keep conflicting, runs out of turns), counted up when the same "
    "trouble comes again.</p>"
)


@dataclass
class Triage:
    """What the page says about golem's triage: whether it's on, its latest jobs with what each
    cost, golem's proposals for itself awaiting Riggy, and those he's decided on."""

    on: bool
    jobs: list[tuple[Job, float, int | None]]  # the latest first: each with its cost and its triage step
    waiting: list[Proposal]
    decided: list[Proposal]


def page(rows: list[Report], state: str, source: str, now: datetime, t: Triage) -> str:
    filters = _filters("state", state, STATES, {"source": source}) + _filters("source", source, SOURCES, {"state": state})
    body = "<ol class=reports>" + "".join(_row(r, now) for r in rows) + "</ol>" if rows else "<p class=empty>No reports here.</p>"
    head = proposals_card(t.waiting, "/reports") + triage_card(t, now) + decided_card(t.decided)
    return f"<section class=reports-page>{head}<h2 class=section-title>Reports</h2>{ABOUT}{filters}{body}</section>"


def proposals_card(waiting: list[Proposal], back: str) -> str:
    """golem's proposals for itself awaiting Riggy, oldest first: take one (its prompt opens) or
    turn it down, saying why."""
    if not waiting:
        return ""
    items = "".join(
        f"<li class=proposal><h4>{escape(p.title)}</h4><p class=muted>{escape(p.lens)}</p><p class=fix-body>{escape(p.body)}</p>"
        f"<form method=post action=/act>{_hidden(action='proposal', proposal=p.id, back=back)}"
        f"<input name=reason placeholder='Why? (needed to turn it down)' aria-label='Why, for {escape(p.title)}' maxlength=1000>"
        "<div class=row-form><button name=value value=accept class=primary>👍 Take it</button>"
        "<button name=value value=reject>👎 Not this</button></div></form></li>"
        for p in sorted(waiting, key=lambda p: p.id)
    )
    return (
        "<section class='card proposals'><h2>golem proposes fixes to itself</h2><p class=sub>From its triage of the "
        "reports below. Taking one gives you a prompt to paste into a Claude Code session in golem's repo; golem "
        f"never changes itself. Saying why you turn one down steers what triage proposes next.</p><ol>{items}</ol></section>"
    )


CLEAR = (
    "Clear every report about golem and all its triage? This deletes every report, new or old, golem's "
    "proposals for itself (waiting or decided, so triage forgets why you turned any down) and its triage "
    "jobs. It can't be undone."
)


def triage_card(t: Triage, now: datetime) -> str:
    toggle = action_form("triage", "Triage: on" if t.on else "Triage: off", value="off" if t.on else "on", back="/reports")
    clear = action_form("reports-clear", "Clear all reports and triage", back="/reports", confirm=CLEAR, danger=True)
    how = (
        f"When golem has nothing else to do, at most every {_hours(triage.MIN_GAP)} hours, once {triage.BATCH} reports are "
        f"new or the oldest has waited {_hours(triage.WAIT)} hours, and while fewer than {triage.MAX_OPEN} of its proposals "
        "wait on you."
    )
    rows = "".join(
        (f"<li><a href='/steps/{step}'>job #{j.id}</a>" if step else f"<li>job #{j.id}")
        + f" {escape(j.state)} · {span(now - datetime.fromisoformat(j.created_at))} ago · ${cost:.2f}</li>"
        for j, cost, step in t.jobs
    )
    return (
        "<section class='card settings'><h2>Triage</h2><p class=sub>golem's own look at the new reports: what in golem "
        f"causes them, and what to change. {how}</p><div class=row-form>{toggle}{clear}</div>"
        + (f"<p class=sub>Lately:</p><ol class=links-list>{rows}</ol>" if rows else "")
        + "</section>"
    )


def decided_card(decided: list[Proposal]) -> str:
    if not decided:
        return ""
    rows = "".join(
        f"<li>{'👍' if p.state == 'accepted' else '👎'} "
        + (f"<a href='/reports/proposals/{p.id}'>{escape(p.title)}</a>" if p.state == "accepted" else escape(p.title))
        + (f"<span class=sub>“{escape(p.reason)}”</span>" if p.reason else "")
        + "</li>"
        for p in decided
    )
    return f"<section class=card><h2>What you decided</h2><ol class=links-list>{rows}</ol></section>"


def fix_page(p: Proposal, prompt: str) -> str:
    """A fix to golem Riggy took: its prompt, to copy into a Claude Code session in golem's repo."""
    copy = (
        "<button type=button class=primary onclick=\"navigator.clipboard.writeText(document.getElementById('fix').value)"
        ".then(() => this.textContent = 'Copied')\">Copy the prompt</button>"
    )
    state = {"open": "Not decided yet.", "accepted": "You took it.", "rejected": "You turned it down."}.get(p.state, "")
    return (
        f"<section class='card fix'><h2>{escape(p.title)}</h2><p class=sub>{state} Paste this into a Claude Code session "
        f"in golem's repo: it has the fix and the reports it answers.</p><div class=row-form>{copy}</div>"
        f"<textarea id=fix readonly rows=24 aria-label='The prompt'>{escape(prompt)}</textarea></section>"
    )


def _hours(delta) -> int:
    return int(delta.total_seconds() // 3600)


def _filters(name: str, current: str, choices: tuple[tuple[str, str], ...], keep: dict[str, str]) -> str:
    links = "".join(
        f"<a href='/reports?{urlencode({**keep, name: value})}'{' class=on' if value == current else ''}>{label}</a>"
        for value, label in choices
    )
    return f"<nav class=windows aria-label='{name}'>{links}</nav>"


def _row(r: Report, now: datetime) -> str:
    when = span(now - datetime.fromisoformat(r.last_seen)) + " ago"
    times = f" · {r.count} times since {span(now - datetime.fromisoformat(r.first_seen))} ago" if r.count > 1 else ""
    where = [escape(x) for x in (r.slug, r.label) if x]
    if r.job_id and r.slug:
        where.append(f"<a href='/p/{escape(r.slug)}/jobs/{r.job_id}'>job #{r.job_id}</a>")
    where += [escape(x) for x in (r.model, f"golem {r.version[:7]}" if r.version else "") if x]
    detail = f"<details><summary>Detail</summary><pre class=log>{escape(r.detail)}</pre></details>" if r.detail else ""
    note = f"<p class=hint>Triage: {escape(r.note)}</p>" if r.note else ""
    return (
        f"<li class='card report'><div class=report-head><span class='badge {TONES.get(r.impact, 'finished')}'>{escape(r.impact)}</span>"
        f"<b>{escape(r.kind)}</b><span class=muted>{'a worker' if r.source == 'agent' else 'golem'} · {when}{times}</span></div>"
        f"<p class=report-summary>{escape(r.summary)}</p>"
        + (f"<p class=sub>{' · '.join(where)}</p>" if where else "")
        + detail
        + note
        + "</li>"
    )
