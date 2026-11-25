"""The Reports page (/reports): what got in the way of golem's workers, about golem itself
(reports.py), from both sources: their own reports and golem's incidents. Riggy reads it to see
how many there are and whether they say anything. Signed in only: they quote runs and errors.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from urllib.parse import urlencode

from .store import Report
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


def page(rows: list[Report], state: str, source: str, now: datetime) -> str:
    filters = _filters("state", state, STATES, {"source": source}) + _filters("source", source, SOURCES, {"state": state})
    body = "<ol class=reports>" + "".join(_row(r, now) for r in rows) + "</ol>" if rows else "<p class=empty>No reports here.</p>"
    return f"<section class=reports-page>{ABOUT}{filters}{body}</section>"


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
