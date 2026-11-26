"""Insights → Each job's cost: the jobs' ledger (ledger.py), the usual way against checklist mode.

First what finished jobs of each kind usually cost in each mode, and what checklist mode saves
or doesn't; then each idea built both ways, its two first jobs side by side; then every job in
the window, newest first, with a CSV of the lot to take elsewhere. Costs only: how good the
work is, Riggy judges on the sites.
"""

from __future__ import annotations

from datetime import timedelta
from html import escape

from . import checklist, plan
from .ledger import Group, Row, groups, saved, twins
from .pages import _count, _money
from .projectpage import href
from .watch import span

SHOWN = 200  # jobs in the table; the CSV has them all


def page(found: list[Row], days: int | None) -> str:
    q = f"?days={days}" if days else "?days=all"
    windows = "".join(
        f"<a href='/insights/jobs{f'?days={n}' if n else '?days=all'}'{' class=on' if n == days else ''}>{label}</a>"
        for n, label in ((7, "7 days"), (30, "30 days"), (None, "All time"))
    )
    head = (
        f"<nav class=windows>{windows}</nav>"
        "<p class=hint>What each job cost: Claude usage at API prices (the fair weight for how much of your allowance it took), "
        f"its tokens and how long its agents ran. <a href='/insights/jobs.csv{q}' download>Download it all as CSV</a> · "
        "<a href=/insights>Where the time and usage go ›</a></p>"
    )
    if not found:
        return f"<section class=insights>{head}<p class=empty>No jobs in this window yet.</p></section>"
    return f"<section class=insights>{head}{compare(groups(found))}{pairs(twins(found))}{table(found)}</section>"


def compare(found: list[Group]) -> str:
    """Finished jobs by kind and mode: how many, and what they usually cost and took."""
    if not found:
        return ""
    rows = "".join(
        f"<tr><td>{escape(g.kind)}</td><td>{escape(g.mode)}</td><td>{len(g.rows)}</td><td>{_money(g.cost)}</td>"
        f"<td>{span(timedelta(seconds=g.agent_seconds))}</td><td>{_money(g.total)}</td></tr>"
        for g in found
    )
    said = []
    by_kind: dict[str, dict[str, Group]] = {}
    for g in found:
        by_kind.setdefault(g.kind, {})[g.mode] = g
    for kind, modes in by_kind.items():
        usual, other = modes.get("usual"), modes.get(checklist.MODE)
        if usual and other:
            said.append(
                f"A {kind} in checklist mode usually costs {_money(other.cost)}, {saved(usual.cost, other.cost)} than the usual way's "
                f"{_money(usual.cost)} ({len(other.rows)} against {len(usual.rows)} finished)."
            )
    found_html = "<ul class=findings>" + "".join(f"<li>{escape(s)}</li>" for s in said) + "</ul>" if said else ""
    return (
        "<h2>The usual way against checklist mode</h2>" + found_html
        + "<div class=runs><table><tr><th>Kind</th><th>Mode</th><th>Finished</th><th>Usual cost</th><th>Usual agent time</th>"
        f"<th>All of them</th></tr>{rows}</table></div>"
        "<p class=hint>“Usual” is the median, so one odd job doesn't sway it.</p>"
    )


def pairs(found: list[tuple[Row, Row]]) -> str:
    """Each idea built both ways: its two first jobs, side by side."""
    if not found:
        return ""
    rows = []
    for usual, other in found:
        state = "" if usual.done and other.done else " <span class=muted>(under way)</span>"
        rows.append(
            f"<tr><td><a href='{href(usual.job)}'>{escape(plan.title(usual.job))}</a>{state}</td>"
            f"<td>{_money(usual.cost)}</td><td><a href='{href(other.job)}'>{_money(other.cost)}</a></td><td>{saved(usual.cost, other.cost)}</td>"
            f"<td>{span(timedelta(seconds=usual.agent_seconds))} / {span(timedelta(seconds=other.agent_seconds))}</td>"
            f"<td>{_items(other)}</td></tr>"
        )
    return (
        "<h2>Built both ways</h2><div class=runs><table><tr><th>Idea</th><th>Usual</th><th>Checklist</th><th>Checklist cost</th>"
        f"<th>Agent time</th><th>Its items</th></tr>{''.join(rows)}</table></div>"
    )


def table(found: list[Row]) -> str:
    rows = "".join(
        f"<tr><td><a href='{href(r.job)}'>#{r.job.id}</a></td><td>{escape(r.job.slug)}</td><td>{escape(r.kind)}</td><td>{escape(r.mode)}</td>"
        f"<td>{escape(r.job.state)}</td><td>{_money(r.cost)}</td><td>{_models(r)}</td><td>{_count(r.tokens_in)} / {_count(r.tokens_out)}</td>"
        f"<td>{span(timedelta(seconds=r.agent_seconds))}</td><td>{span(timedelta(seconds=max(r.wall_seconds - r.waiting_seconds, 0)))}</td>"
        f"<td>{r.fixes or ''}</td><td>{_items(r)}</td></tr>"
        for r in found[:SHOWN]
    )
    more = f"<p class=hint>The latest {SHOWN} of {len(found)}: the CSV has them all.</p>" if len(found) > SHOWN else ""
    return (
        "<h2>Every job</h2><div class=runs><table><tr><th>Job</th><th>Project</th><th>Kind</th><th>Mode</th><th>State</th><th>Cost</th>"
        "<th>By model</th><th>Tokens in/out</th><th>Agent time</th><th>Time, not waiting on you</th><th>Fixes</th><th>Items</th></tr>"
        f"{rows}</table></div>{more}"
    )


def _models(r: Row) -> str:
    """Its cost by model, biggest first: "opus 72% · haiku 28%"."""
    if not r.cost:
        return ""
    return " · ".join(f"{escape(m)} {round(100 * c / r.cost)}%" for m, c in sorted(r.by_model.items(), key=lambda x: -x[1]) if c)


def _items(r: Row) -> str:
    """A checklist job's items: "8: 5 first time, 3 again (1 on its Build's model), 0 given up"."""
    if not r.items:
        return ""
    bits = f"{r.items}: {r.first_try} first time, {r.again} again"
    if r.escalated:
        bits += f" ({r.escalated} on its Build's model)"
    if r.gave_up:
        bits += f", {r.gave_up} given up"
    return escape(bits) + f" <span class=muted>· planning {_money(r.planning_cost)}, items {_money(r.items_cost)}</span>"
