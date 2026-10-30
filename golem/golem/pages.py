"""The web app's HTML, phone first: the page around everything, with its tab bar (Home,
Projects, Settings), and the pieces the pages are made of (home.py, conversation.py).

A project's job shows as a card: where it is in a line of words, what the running step is
doing, and its pieces of work as a checklist (progress.py). Each piece opens a sheet: what
it did (with its runs and transcript), what it's doing (a live tail), or, before it starts,
when it should and what it will run on, with the changes allowed (control.py). What golem
is waiting on Riggy for shows as an ask: try the site, pick an option (side by side, a
button each), or answer. The insights page shows where the time and Claude usage go.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from . import options, plan, progress, pwa
from .control import SKIPPED
from .insights import Eta, Report, Tally, estimate
from .live import Line, view
from .store import ACTIVE, EFFORTS, MODELS, OPEN, Job, Project, Step
from .watch import span

SHIP = progress.SHIP  # shown together as one "ship" row in a job's full list of steps
ICONS = {"done": "✓", "skipped": "–", "running": "▶", "waiting": "?", "failed": "✗", "pending": ""}
MARKS = {"done": "✓", "skipped": "–", "now": "", "waiting": "?", "failed": "✕", "todo": ""}  # a piece of work's
TABS = (("home", "/", "Home"), ("projects", "/p", "Projects"), ("settings", "/settings", "Settings"))
# What golem's own steps do, for their sheets.
DOES = {
    "setup": "Makes sure the repo, the checkout and the job's issue exist.",
    "push": "Commits anything left and pushes the job's branch to GitHub.",
    "pr": "Opens a pull request for the work since the last merge.",
    "checks": "Waits for CI to pass on the pull request. If it fails, a fix step goes in and the shipping steps run again.",
    "merge": "Squash-merges the pull request into main.",
    "push-main": "Pushes the work straight to main (reckless mode: no pull request).",
    "deploy": "Waits for CI on main, then deploys it. Done only once the site answers; if the deployer refuses it, a fix step goes in.",
    "checkin": "Shows you the live site and waits for your reply. Skipping it means “looks good, carry on”.",
    "choose": "Shows you the options the step before made, in its conversation, and waits for your pick. "
    "Your pick and what you say about it go to the next step, which builds it; asking for other options "
    "makes another round. Skipping it lets the next step pick the strongest.",
    "alert": "Tells you it's live. Always the last step, after the last deploy.",
    "refresh": "Brings the checkout up to date with what's live, unless a job's work is under way in it.",
    "answer": "Sends you the answer. A question changes nothing: no commits, pushes or deploys.",
}


@dataclass
class LaneData:
    """Everything one project's lane shows, gathered by the web app."""

    project: Project
    job: Job | None  # the active job, or the latest finished one
    steps: list[Step]
    state: str  # a few words: "working", "waiting for CI", "waiting for you"…
    live: list[Line]  # the running agent step's latest lines
    notes: list[str]  # the worker's progress notes, latest last
    skippable: set[int]
    restorable: set[int]
    position: int | None  # place in the queue, 1 = next to run
    site: str
    repo: str
    color: int = 1  # the project's color, 1-8, by when it was made
    etas: dict[int, Eta] = field(default_factory=dict)  # by step position
    tallies: dict[int, Tally] = field(default_factory=dict)  # each agent step's runs, by step id
    known: dict[str, float] = field(default_factory=dict)  # seconds each kind of step usually takes


@dataclass
class SheetData:
    """One step, for its sheet."""

    lane: LaneData
    step: Step
    href: str
    engine: plan.Engine | None  # agent steps
    inherited: plan.Engine | None  # what it would run on without its own choice
    does: str  # what the step does, in a sentence or two
    runs: list[dict]  # its runs, oldest first
    transcripts: dict[int, list[Line]]  # by run id, for finished runs
    notes: list[str]  # context Riggy gave this step
    configurable: bool


# --- the page -------------------------------------------------------------------------


def page(title: str, body: str, signed_in: bool, paused: bool, flash: str = "", bottom: str = "",
         tab: str = "", up: tuple[str, str] | None = None, action: str = "", behind: bool = False) -> str:  # fmt: skip
    """The page around `body` (the part the script refreshes). The top level (Home, Projects,
    Settings) has the tab bar; a page below it has the way back (`up`: where, and its name)
    instead. `action` goes at the header's right. `bottom` sticks to the bottom of the screen,
    outside the refreshed part, so what's being written there is never lost. `behind` marks the
    Settings tab: a new version of golem is on GitHub."""
    left = f"<a href='{escape(up[0])}' class=up>‹ {escape(up[1])}</a>" if up else ""
    tabs = ""
    if up is None:
        tabs = "<nav class=tabs aria-label=Sections>" + "".join(
            f"<a href='{href}'{' class=on aria-current=page' if key == tab else ''}>{ICONS_SVG[key]}<span>{label}</span>"
            + ("<i class=dot aria-label='new version'></i>" if behind and key == "settings" else "")
            + "</a>"
            for key, href, label in TABS
        ) + "</nav>"
    banner = "<div class='banner paused'>Work is paused. Nothing new starts until it's resumed (Settings).</div>" if paused else ""
    flash_html = f"<div class=flash role=status>{escape(flash)}</div>" if flash else ""
    latest = "<button type=button class=to-latest hidden aria-label='To the latest message'>↓</button>" if bottom else ""
    dock = f"<div class=dock>{latest}{bottom}{tabs}</div>" if bottom or tabs else ""
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name=theme-color content="#0f172a">
<title>{escape(title if title == "golem" else f"{title} · golem")}</title><style>{STYLE}</style>
<link rel=manifest href=/manifest.webmanifest><link rel=apple-touch-icon href=/icon-180.png>
<meta name=apple-mobile-web-app-capable content=yes><meta name=apple-mobile-web-app-title content=golem>
<header class='top{' sub' if up else ''}'>{left}<h1>{escape(title)}</h1><div class=action>{action}</div></header>
{banner}{flash_html}<main id=lanes>{body}</main>{dock}
<dialog id=sheet aria-label='Step'><button class='quiet close' aria-label=Close>✕</button><div class=sheet-body></div></dialog>
<script>{SCRIPT}</script>{f"<script>{pwa.CLIENT}</script>" if signed_in else ""}</html>"""


# The tab bar's icons: a house, two speech bubbles, a gear.
ICONS_SVG = {
    "home": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z'/></svg>",
    "projects": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M4 4h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-4 3v-3H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z'/>"
    "<path d='M19 8h1a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-1v3l-4-3h-4a2 2 0 0 1-2-2v-.5h6a3 3 0 0 0 3-3z'/></svg>",
    "settings": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zm8.4 2.1-1.7-.3a7 7 0 0 0-.7-1.7l1-1.4"
    "-1.9-1.9-1.4 1a7 7 0 0 0-1.7-.7l-.3-1.7h-2.6l-.3 1.7a7 7 0 0 0-1.7.7l-1.4-1-1.9 1.9 1 1.4a7 7 0 0 0-.7 1.7l-1.7.3v2.6l1.7.3"
    "a7 7 0 0 0 .7 1.7l-1 1.4 1.9 1.9 1.4-1a7 7 0 0 0 1.7.7l.3 1.7h2.6l.3-1.7a7 7 0 0 0 1.7-.7l1.4 1 1.9-1.9-1-1.4a7 7 0 0 0 .7-1.7l1.7-.3z'/></svg>",
}


def signin_card(back: str) -> str:
    return (
        "<section class='card signin-card'><h2>Sign in</h2><p class=sub>To talk to golem, answer it and change things.</p>"
        f"<form method=post action=/login class=row-form><input type=hidden name=back value='{escape(back)}'>"
        "<input type=password name=password placeholder=Password autocomplete=current-password required aria-label=Password>"
        "<button class=primary>Sign in</button><label class=keep><input type=checkbox name=keep value=1 checked> "
        "Keep me signed in on this device</label></form></section>"
    )


# --- a project's job ------------------------------------------------------------------


def status_card(lane: LaneData, signed_in: bool, now: datetime) -> str:
    """Where a project's job is, in a line; what it's doing right now; and its pieces of work
    as a checklist (progress.py), each opening its sheet."""
    p, job = lane.project, lane.job
    head = (
        f"<header class=card-head><p class=headline>{badge(lane)} <span>{headline(lane, now)}</span></p>"
        f"<div class=out>{_out_links(lane)}</div></header>"
    )
    if job is None:
        return f"<section class='card status c{lane.color}' id='p-{escape(p.slug)}'>{head}<p class=sub>{escape(p.summary)}</p></section>"
    found = progress.pieces(lane.steps)
    active = job.state in ACTIVE
    what = "New project" if job.kind == "new" else {"options": "Options, then a change", "question": "Question", "proposals": "Proposals"}.get(job.playbook, "Change")
    done = sum(1 for x in found if x.state in ("done", "skipped"))
    summary = f"{done} of {len(found)} done" if active else f"How it went: {_n(len(found), 'piece')} of work"
    spent = _spent(lane)
    return (
        f"<section class='card status c{lane.color}' id='p-{escape(p.slug)}'>{head}"
        f"<p class=request><span class=muted>{what}:</span> “{escape(_short(job.request, 220))}”</p>"
        + (working(lane, signed_in, now) if active else "")
        + f"<details class=plan data-key=plan-{job.id}{' open' if active else ''}><summary>{summary}</summary>{checklist(lane, found, now)}</details>"
        + f"<p class=foot>{escape(p.mode)} · {escape(_engine_words(p))}{f' · {spent}' if spent else ''} · <a href='/jobs/{job.id}'>job #{job.id}</a></p>"
        + "</section>"
    )


def badge(lane: LaneData) -> str:
    tone = _tone(lane)
    if tone == "queued" and any(s.started_at for s in lane.steps):  # under way, between steps (waiting for CI)
        tone = "running"
    if tone == "finished":
        word = {"done": "Live", "killed": "Stopped", "failed": "Failed"}.get(lane.job.state, "Idle") if lane.job else "Idle"
    else:
        word = {"running": "Working", "waiting": "Needs you", "trouble": "Retrying", "queued": "Queued", "paused": "Paused"}[tone]
    return f"<span class='badge {tone}'>{word}</span>"


def headline(lane: LaneData, now: datetime) -> str:
    """Where the job is, in words: what's under way and when it's next live, or how it ended."""
    job = lane.job
    if job is None:
        return "No jobs yet."
    if job.state not in ACTIVE:
        word = {"done": "Done", "killed": "Stopped", "failed": "Failed"}.get(job.state, "Finished")
        return f"{word} {span(now - _dt(job.updated_at))} ago." if job.updated_at else f"{word}."
    if lane.project.paused:
        return "Paused: nothing starts until it's resumed."
    if job.state == "waiting":
        step = plan.current(lane.steps)
        asks = {"checkin": "Waiting for you to try it.", "choose": "Waiting for your pick."}
        return asks.get(step.kind if step else "", "Has a question for you." if job.resume_session else "Needs a hint from you.")
    piece = progress.current(progress.pieces(lane.steps))
    if piece is None:
        return escape(lane.state.capitalize()) + "."
    if piece.state == "todo":
        eta = lane.etas.get(piece.step.position)
        start = f", starting {when(eta.start, now)}" if eta else ""
        place = f"#{lane.position} in the queue" if lane.position and lane.position > 1 else "Next up"
        return f"{place}: {escape(piece.name.lower())}{start}."
    live = next((s for s in lane.steps if s.kind == "deploy" and s.state in OPEN), None)
    eta = lane.etas.get(live.position) if live else None
    then = f" Live {when(eta.end, now)}." if eta else ""
    return f"{escape(piece.name)}: {piece_meta(lane, piece, now)}.{then}"


def checklist(lane: LaneData, found: list[progress.Piece], now: datetime) -> str:
    rows = []
    for x in found:
        s = x.step
        tags = "<span class=opt>optional</span>" if s.optional and x.state == "todo" else ""
        tags += "<span class=live>live</span>" if x.live else ""
        why = f"<p class=why>{escape(_short(s.problem.splitlines()[0], 140))}</p>" if s.name == "fix" and s.problem else ""
        rows.append(
            f"<li class='piece {x.state}'><a href='{escape(step_href(lane.job, s))}' data-sheet><span class=mark>{MARKS[x.state]}</span>"
            f"<span class=name>{escape(x.name)}</span></a>{tags}<span class=meta>{piece_meta(lane, x, now)}</span>{why}</li>"
        )
    return f"<ol class=pieces>{''.join(rows)}</ol>"


def dots(lane: LaneData) -> str:
    """The pieces of work as a row of dots, for a glance."""
    found = progress.pieces(lane.steps)
    return "<span class=dots aria-hidden=true>" + "".join(f"<i class={x.state} title='{escape(x.name)}'></i>" for x in found) + "</span>"


def piece_meta(lane: LaneData, x: progress.Piece, now: datetime) -> str:
    s = x.step
    match x.state:
        case "done":
            took = sum(_took(lane, step) for step in x.steps if step.kind not in plan.ASKS) if s.kind not in plan.ASKS else _took(lane, s)
            return span(timedelta(seconds=took)) if took >= 1 else ""
        case "skipped":
            return "skipped"
        case "failed":
            return "failed: a fix follows"
        case "waiting":
            return "your turn"
        case "now":
            if x.shipping:
                return progress.SHIPPING.get(x.shipping.kind, "shipping")
            if not s.started_at:
                return "starting"
            usual = estimate(lane.known, s)
            about = f", usually {span(timedelta(seconds=usual))}" if s.kind == "agent" and plan.label(s) in lane.known else ""
            return f"{span(now - _dt(s.started_at))} so far{about}"
    eta = lane.etas.get(s.position)
    return when(eta.start, now) if eta else ""


def working(lane: LaneData, signed_in: bool, now: datetime) -> str:
    """What the running step is doing: what the worker last said, its ▸ notes, and a live tail."""
    step = plan.current(lane.steps)
    if step is None or lane.job.state == "waiting":
        return ""
    body = ""
    if step.kind == "agent" and step.started_at:
        said = next((line.text for line in reversed(lane.live) if line.kind == "says"), "")
        body = f"<p class=said>{escape(said)}</p>" if said else ""
        if lane.notes:
            body += "<ul class=notes>" + "".join(f"<li>{escape(n)}</li>" for n in lane.notes[-4:]) + "</ul>"
        body += tail(lane.live, signed_in, f"tail-{lane.job.id}", opened=False)
    if step.error:
        body += f"<p class=muted>Trying again: {escape(_short(step.error, 200))}</p>"
    return f"<div class=working>{body}</div>" if body else ""


def ask_card(lane: LaneData, signed_in: bool, back: str, reply: bool) -> str:
    """What golem is waiting on Riggy for, with what answers it: try the site and say how it
    is, pick an option, or answer a question. `reply` adds a box to answer in (the project's
    own page has its composer for that)."""
    job, step = lane.job, plan.current(lane.steps)
    if job is None or job.state != "waiting" or step is None:
        return ""
    if step.kind == "checkin":
        body = f"<p>{escape(plan.last_said(lane.steps) or 'It’s live: have a look.')}</p><div class=row-form><a class=button href='{escape(lane.site)}' target=_blank rel=noopener>Try it ↗</a>"
        body += (skip_button(job, step, "Looks good, carry on", back=back) if signed_in else "") + "</div>"
        hint, placeholder = "Or say what to change.", "Looks good, or what to change…"
    elif step.kind == "choose":
        made, shown = options.shown(lane.steps, step)
        body = f"<p>{escape(made.summary if made and made.summary else 'Pick one.')}</p>" + options_html(job, shown, signed_in, back)
        body += f"<div class=row-form>{skip_button(job, step, 'Let it pick', back=back)}</div>" if signed_in else ""
        hint, placeholder = "Or pick with what you'd change (“B, but bigger”), or ask for other options.", "B, but bigger… or: other options"
    else:
        body = f"<p>{escape(step.summary or 'It needs your help.')}</p>"
        if step.error:
            body += f"<p class=muted>{escape(_short(step.error, 300))}</p>"
        hint, placeholder = "", "Your answer or a hint…"
    if signed_in:
        body += (f"<p class=hint>{escape(hint)}</p>" if hint else "") + (reply_form(job, placeholder, back) if reply else "")
    return f"<section class='ask c{lane.color}'>{body}</section>"


def tail(lines: list[Line], signed_in: bool, key: str, limit: int = 14, title: str = "Live", opened: bool = True) -> str:
    shown = view(lines, signed_in, limit)
    if not shown:
        return ""
    rows = "".join(
        f"<div class='t {line.kind}{' nested' if line.nested else ''}'><time>{escape(line.at)}</time>"
        f"<span>{_mark(line)} {escape(line.text)}</span></div>"
        for line in shown
    )
    return f"<details class=tail data-key={key}{' open' if opened else ''}><summary>{escape(title)}</summary><div class=log>{rows}</div></details>"


def job_controls(lane: LaneData) -> str:
    """Changes to the job under way: its place in the queue, and stopping it."""
    p, job = lane.project, lane.job
    if job is None or job.state not in ACTIVE:
        return ""
    back = f"/p/{p.slug}/settings"
    first = "" if lane.position == 1 else action_form("move", "Run next", job=job.id, value="first", back=back)
    return (
        f"<section class=card><h2>This job</h2><p class=sub>#{job.id}: “{escape(_short(job.request, 120))}”</p><div class=row-form>"
        + first
        + action_form("move", "Run last", job=job.id, value="last", back=back)
        + action_form("kill", "Stop job", job=job.id, back=back, confirm=f"Stop job {job.id} on {p.name} for good?", danger=True)
        + "</div></section>"
    )

# --- a step's sheet -------------------------------------------------------------------


def sheet(d: SheetData, signed_in: bool, now: datetime) -> str:
    lane, s, job = d.lane, d.step, d.lane.job
    words = {"pending": "to come", "running": "now", "waiting": "waiting for you"}.get(s.state, s.state)
    kind = "Agent step" if s.kind == "agent" else "Golem's own step"
    head = (
        f"<header class='sheet-head c{lane.color}'><p class=sub><span class=swatch></span>{escape(lane.project.name)} · job #{job.id} · "
        f"step {s.position} of {len(lane.steps)}</p><h2>{escape(plan.label(s))} <span class='badge {_state_tone(s)}'>"
        f"{ICONS.get(s.state, '')} {escape(words)}</span></h2><p class=muted>{kind}{' · optional' if s.optional else ''}</p></header>"
    )
    body = f"<p>{escape(d.does)}</p>" if d.does else ""
    if s.name == "fix" and s.problem and signed_in:
        body += f"<details><summary>The problem it was given</summary><pre class=problem>{escape(_short(s.problem, 3000))}</pre></details>"
    facts = []
    if s.state in OPEN and not s.started_at:
        eta = lane.etas.get(s.position)
        usual = estimate(lane.known, s)
        if eta:
            facts.append(("Starts", f"{when(eta.start, now)}, if the queue goes as planned"))
        facts.append(("Usually takes", span(timedelta(seconds=usual)) if usual >= 1 else "a moment"))
    if s.started_at:
        facts.append(("Started", when(_dt(s.started_at), now)))
    if s.ended_at:
        facts.append(("Took", span(timedelta(seconds=_took(lane, s)))))
    elif s.started_at:
        facts.append(("So far", span(now - _dt(s.started_at))))
    if d.engine:
        facts.append(("Runs on", f"{d.engine} <span class=muted>(model from {escape(d.engine.model_from)}, effort from {escape(d.engine.effort_from)})</span>"))
    tally = lane.tallies.get(s.id)
    if tally and tally.runs:
        facts.append(("Usage", f"{_money(tally.cost)} · {_count(tally.tokens)} tokens · {tally.runs} run{'s' if tally.runs != 1 else ''}"))
    if facts:
        body += "<dl class=facts>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
    if s.summary and s.state != "pending":
        body += f"<p class=summary>{escape(s.summary)}</p>"
    if s.error:
        body += f"<p class=muted>Last try: {escape(_short(s.error, 400))}</p>"
    body += _links(s.links)
    made, shown = options.shown(lane.steps, s)
    if shown:
        pick = signed_in and s.kind == "choose" and s.state == "waiting" and job.state in ACTIVE
        title = "The options" if s.kind == "choose" else "Its options"
        body += f"<h3>{title}</h3>" + options_html(job, shown, pick, back=f"/p/{job.slug}")
    if d.notes:
        body += "<h3>Your context for it</h3><ul class=notes>" + "".join(f"<li>{escape(n)}</li>" for n in d.notes) + "</ul>"
    if s.state in ("running", "waiting") and s.kind == "agent":
        body += f"<div data-live>{tail(lane.live, signed_in, f'sheet-tail-{s.id}', limit=30)}</div>"
    for run in d.runs:
        lines = d.transcripts.get(run["id"])
        if lines:
            title = f"{run['phase']} on {run['model']}: {_outcome(run)}, {span(timedelta(seconds=_seconds(run)))}"
            body += tail(lines, signed_in, f"run-{run['id']}", limit=400, title=title).replace(" open>", ">", 1)
    if signed_in:
        body += sheet_controls(d)
    elif s.state == "pending":
        body += "<p class=hint>Sign in to change this step.</p>"
    return head + body


def sheet_controls(d: SheetData) -> str:
    lane, s, job = d.lane, d.step, d.lane.job
    back = f"/#{d.href}"
    ids = {"job": job.id, "step": s.id, "value": ""}
    if not s.id:
        ids["position"] = s.position
    out = []
    if d.configurable and d.inherited:
        pick = "".join(
            select_form(name, getattr(s, name) or "", values, f"default: {getattr(d.inherited, name)}",
                        action="step-setting", back=back, **ids)  # fmt: skip
            for name, values in (("model", MODELS), ("effort", EFFORTS))
        )
        out.append(f"<div class=row-form><span>This step only:</span>{pick}</div>")
        out.append(
            "<form method=post action=/act class=reply><input type=hidden name=action value=note>"
            + _hidden(job=job.id, step=s.id or "", position="" if s.id else s.position, back=back)
            + "<textarea name=value rows=2 placeholder='Context for this step only…' required></textarea><button>Add</button></form>"
        )
    if s.id in lane.skippable:
        out.append(skip_button(job, s, back=back))
    if s.id in lane.restorable:
        out.append(action_form("restore", "Undo skip", job=job.id, step=s.id, back=back))
    if job.state in ACTIVE and s.state in OPEN and lane.position != 1:
        out.append(action_form("move", f"Run {lane.project.name} next", job=job.id, value="first", back=back))
    if not out:
        return ""
    return "<section class='controls sheet-controls'>" + "".join(out) + "</section>"


# --- the job page's step lists --------------------------------------------------------


def steps_lists(lane: LaneData, signed_in: bool, now: datetime) -> str:
    current = plan.current(lane.steps)
    ahead = [s for s in lane.steps if s.state == "pending" and s is not current]
    ahead += [s for s in lane.steps if s.id in lane.restorable]
    done = [
        s for s in lane.steps
        if s.state in ("done", "failed") or (s.state == "skipped" and s.id not in lane.restorable)
        if current is None or s.position < current.position
    ]  # fmt: skip
    html = ""
    if ahead:
        rows = "".join(_row(g, lane, signed_in) for g in _groups(sorted(ahead, key=lambda s: s.position)))
        html += f"<section class=next><h3>Up next</h3><ol class=steps>{rows}</ol></section>"
    if done:
        rows = "".join(_row(g, lane, signed_in, now) for g in _groups(done))
        html += f"<details class=done data-key=done-{lane.job.id} open><summary>{len(done)} steps done</summary><ol class=steps>{rows}</ol></details>"
    return html


def _row(group: list[Step], lane: LaneData, signed_in: bool, now: datetime | None = None) -> str:
    first = group[0]
    if first.kind in SHIP:  # pushing, PR, CI, merge, deploy as one row
        chips = "".join(
            f"<a class='chip {s.state}' href='{escape(step_href(lane.job, s))}' data-sheet>{ICONS.get(s.state, '')} {escape(plan.label(s))}</a>"
            for s in group
        )
        links = "".join(_links(s.links) for s in group if s.state in ("done", "failed"))
        took = _took_row(group[0], group[-1]) if now else ""
        icon = ICONS.get("done" if all(s.state in ("done", "skipped") for s in group) else "pending", "")
        return (
            f"<li class='row ship {group[-1].state}'><div class=what><span class=icon>{icon}</span><b>ship</b>"
            f"<span class=chips>{chips}</span></div>{links}{took}</li>"
        )
    s = first
    tag = "<span class=opt>optional</span>" if s.optional and s.state == "pending" else ""
    button = ""
    if signed_in and s.id in lane.skippable:
        button = skip_button(lane.job, s, back=f"/jobs/{lane.job.id}")
    elif signed_in and s.id in lane.restorable:
        button = action_form("restore", "Undo skip", job=lane.job.id, step=s.id, back=f"/jobs/{lane.job.id}")
    summary = f"<p>{escape(_short(s.summary, 300))}</p>" if s.state != "pending" and s.summary else ""
    tally = lane.tallies.get(s.id)
    usage = f"<span class=took> · {_money(tally.cost)} · {_count(tally.tokens)} tokens</span>" if tally and tally.runs else ""
    return (
        f"<li class='row {s.state}{' code' if s.kind != 'agent' else ''}'><div class=what><span class=icon>{ICONS.get(s.state, '')}</span>"
        f"<a href='{escape(step_href(lane.job, s))}' data-sheet><b>{escape(plan.label(s))}</b></a>{tag}{button}</div>"
        f"{summary}{_links(s.links)}{_took_row(s, s) if now else ''}{usage}</li>"
    )


def _groups(steps: list[Step]) -> list[list[Step]]:
    groups: list[list[Step]] = []
    for s in steps:
        if s.kind in SHIP and groups and groups[-1][0].kind in SHIP and groups[-1][-1].position == s.position - 1:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


# --- insights -------------------------------------------------------------------------


def insights(r: Report, found: list[str], days: int | None) -> str:
    """Where the time and usage go, and the hot spots."""
    windows = "".join(
        f"<a href='/insights{f'?days={n}' if n else '?days=all'}'{' class=on' if n == days else ''}>{label}</a>"
        for n, label in ((7, "7 days"), (30, "30 days"), (None, "All time"))
    )
    t = r.total
    tiles = (
        f"<div class=tiles><div class=tile><span>Worker time</span><b>{span(timedelta(seconds=t.seconds))}</b></div>"
        f"<div class=tile><span>Usage</span><b>{_money(t.cost)}</b></div>"
        f"<div class=tile><span>Tokens</span><b>{_count(t.tokens)}</b></div>"
        f"<div class=tile><span>Agent runs</span><b>{t.runs}</b></div></div>"
    )
    if not t.seconds and not t.runs:
        return f"<section class=insights><nav class=windows>{windows}</nav><p class=empty>Nothing has run in this window yet.</p></section>"
    found_html = "<ul class=findings>" + "".join(f"<li>{escape(f)}</li>" for f in found) + "</ul>" if found else ""
    time_rows = [(k.key, k.seconds, span(timedelta(seconds=k.seconds)), _per(k)) for k in r.kinds if k.seconds]
    cost_rows = [(k.key, k.cost, _money(k.cost), _per_cost(k)) for k in sorted(r.kinds, key=lambda k: -k.cost) if k.cost]
    project_rows = [(k.key, k.seconds, span(timedelta(seconds=k.seconds)), _money(k.cost)) for k in r.projects]
    model_rows = [(k.key, k.cost, _money(k.cost), f"{_n(k.runs, 'run')} · {span(timedelta(seconds=k.seconds))}") for k in r.models]
    hot = ""
    if r.slowest:
        hot += "<h3>Slowest steps</h3><ol class=hot>" + "".join(
            f"<li><a href='/steps/{s.id}'>{escape(slug)} · {escape(plan.label(s))}</a> <span>{span(timedelta(seconds=sec))}</span></li>"
            for s, slug, sec in r.slowest
        ) + "</ol>"
    if r.costliest:
        hot += "<h3>Most usage</h3><ol class=hot>" + "".join(
            f"<li><a href='/steps/{s.id}'>{escape(slug)} · {escape(plan.label(s))}</a> <span>{_money(cost)}</span></li>"
            for s, slug, cost in r.costliest
        ) + "</ol>"
    waiting = ""
    if r.waiting.steps:
        waiting = (
            f"<p class=hint>Not counted above: {span(timedelta(seconds=r.waiting.seconds))} waiting for you to try "
            f"the site or pick an option ({r.waiting.steps} {'time' if r.waiting.steps == 1 else 'times'}).</p>"
        )
    return (
        f"<section class=insights><nav class=windows>{windows}</nav>{tiles}{found_html}"
        + bars("Where the time goes", "time", time_rows, t.seconds, "Wall-clock time by kind of step: agent runs, retries included, and golem's own steps such as waiting for CI.")
        + bars("Where the usage goes", "usage", cost_rows, t.cost, "Claude usage at API prices: the fair weight for how much of your allowance it took. Cached reading counts for little, haiku for less than opus.")
        + waiting
        + (f"<div class=two><div>{hot}</div><div>" if hot else "<div class=two><div>")
        + bars("By project", "time", project_rows, t.seconds, "", small=True)
        + bars("By model and effort", "usage", model_rows, t.cost, "", small=True)
        + "</div></div></section>"
    )


def bars(title: str, measure: str, rows: list[tuple[str, float, str, str]], whole: float, note: str, small: bool = False) -> str:
    """One measure as horizontal bars, biggest first, each with its value and share in words."""
    if not rows:
        return ""
    top = max(v for _, v, _, _ in rows) or 1
    items = "".join(
        f"<li title='{escape(name)}: {escape(value)} ({_share(v, whole)})'><span class=name>{escape(name)}</span>"
        f"<span class=bar><i style='width:{max(100 * v / top, 1.5):.1f}%'></i></span>"
        f"<span class=val>{escape(value)} <small>{_share(v, whole)}</small></span>"
        + (f"<span class=extra>{escape(extra)}</span>" if extra else "")
        + "</li>"
        for name, v, value, extra in rows
    )
    tag = "h3" if small else "h2"
    return f"<section class='bars {measure}'><{tag}>{escape(title)}</{tag}>{f'<p class=hint>{escape(note)}</p>' if note else ''}<ol>{items}</ol></section>"


def defaults_table(kinds: list[str], defaults: dict[str, tuple[str, str]], fallback: tuple[str, str], r: Report,
                   signed_in: bool) -> str:  # fmt: skip
    """Each kind of agent step's default model and effort, with how it's been doing."""
    by_kind = {k.key: k for k in r.kinds}
    every = defaults.get("*", ("", ""))
    rows = []
    for kind in ["*", *kinds]:
        model, effort = defaults.get(kind, ("", ""))
        t = by_kind.get(kind)
        how = " · ".join(x for x in (_per(t), _per_cost(t)) if x) if t and t.steps else "not run yet"
        if kind == "*":
            name, how = "Every step", "unless set below"
            unset = (f"{fallback[0]} (GOLEM_MODEL)", f"{fallback[1]} (GOLEM_EFFORT)")
        else:
            name, unset = kind, (f"default: {every[0] or fallback[0]}", f"default: {every[1] or fallback[1]}")
        suggested = plan.SUGGESTED.get(kind)
        if suggested and suggested != (model, effort):
            how += f" · suggested {'/'.join(suggested)}"
        if signed_in:
            picks = "".join(
                select_form(field, value, values, unset[i], action="default", kind=kind, back="/settings#defaults")
                for i, (field, value, values) in enumerate((("model", model, MODELS), ("effort", effort, EFFORTS)))
            )
        else:
            picks = f"<span>{escape(model or unset[0])}</span><span>{escape(effort or unset[1])}</span>"
        rows.append(f"<li><div><b>{escape(name)}</b> <span class=muted>{escape(how)}</span></div><div class=picks>{picks}</div></li>")
    return (
        "<section class=defaults id=defaults><h2>Defaults for each kind of step</h2>"
        "<p class=hint>The model and effort each kind of agent step runs on. A project's own choice (from your dictation, "
        "!model, or its settings) wins over these, and a single step's choice wins over both.</p>"
        f"<ol class=kinds>{''.join(rows)}</ol>"
        + (
            action_form("reset-defaults", "Reset to golem's suggestions", back="/settings#defaults",
                        confirm="Put every kind of step back on golem's suggested model and effort?")  # fmt: skip
            if signed_in
            else "<p class=hint>Sign in to change them.</p>"
        )
        + "</section>"
    )


# --- pieces ---------------------------------------------------------------------------


def step_href(job: Job, step: Step) -> str:
    """A step's sheet. Steps of a job that hasn't started aren't stored yet, so go by position."""
    return f"/steps/{step.id}" if step.id else f"/jobs/{job.id}/planned/{step.position}"


def when(at: datetime, now: datetime) -> str:
    """A time to come or gone, in words; the page's script shows it on the viewer's clock."""
    delta = at - now
    words = f"in {span(delta)}" if delta.total_seconds() > 30 else ("now" if delta.total_seconds() > -60 else f"{span(-delta)} ago")
    return f"<time datetime='{at.isoformat()}' data-at='{at.isoformat()}'>{words}</time>"


def action_form(action: str, label: str, *, job: int | None = None, step: int | None = None, value: str = "",
                slug: str = "", back: str = "", confirm: str = "", danger: bool = False) -> str:  # fmt: skip
    hidden = _hidden(action=action, job=job, step=step, value=value, slug=slug, back=back)
    check = f" onsubmit='return confirm({escape(json.dumps(confirm))})'" if confirm else ""
    return f"<form method=post action=/act class=inline{check}>{hidden}<button{' class=danger' if danger else ''}>{escape(label)}</button></form>"


def select_form(name: str, current: str, values: tuple[str, ...], unset: str, **fields) -> str:
    """A select that saves on change. The first option is "" (`unset` says what that means)."""
    options = f"<option value=''{' selected' if not current else ''}>{escape(unset)}</option>" + "".join(
        f"<option{' selected' if current == v else ''}>{escape(v)}</option>" for v in values
    )
    fields = {k: v for k, v in fields.items() if k != "value"}
    return (
        f"<form method=post action=/act class=inline>{_hidden(name=name, **fields)}"
        f"<select name=value aria-label={name} onchange='this.form.submit()'>{options}</select></form>"
    )


def reply_form(job: Job, placeholder: str, back: str) -> str:
    return (
        f"<form method=post action=/act class=reply data-send>{_hidden(action='note', job=job.id, back=back)}"
        f"<textarea name=value rows=1 placeholder='{escape(placeholder)}' required aria-label='{escape(placeholder)}'></textarea>"
        "<button class=primary>Send</button></form>"
    )


def skip_button(job: Job, step: Step, label: str = "Skip", back: str = "") -> str:
    return action_form("skip", label, job=job.id, step=step.id, back=back or f"/p/{job.slug}",
                       confirm="" if step.kind in plan.ASKS else f"Skip {plan.label(step)}?")  # fmt: skip


def options_html(job: Job, shown: list[options.Option], pick: bool, back: str) -> str:
    """Options side by side, each with its pictures (tap one for the whole thing) and, while
    Riggy's pick is awaited, a button to pick it: the same as replying with its letter."""
    items = []
    for o in shown:
        urls = [option_image(job, name) for name in o.images]
        shots = "".join(
            f"<a href='{escape(url)}' target=_blank rel=noopener><img src='{escape(url)}' loading=lazy "
            f"alt='{escape(o.letter)}: {escape(o.title)}{f' ({i})' if i > 1 else ''}'></a>"
            for i, url in enumerate(urls, start=1)
        )
        shots = f"<div class=shots>{shots}</div>" if shots else ""
        button = action_form("note", f"Pick {o.letter}", job=job.id, value=o.letter, back=back) if pick else ""
        where = f"<p class=files>{escape(o.where)}</p>" if o.where else ""
        items.append(
            f"<li class=option>{shots}<div class=option-body><h4><span class=letter>{escape(o.letter)}</span>{escape(o.title)}</h4>"
            f"<p>{escape(o.description)}</p>{where}{button}</div></li>"
        )
    return f"<ol class=options>{''.join(items)}</ol>"


def option_image(job: Job, name: str) -> str:
    return f"/jobs/{job.id}/options/{name}"


def read_notes(path: Path, keep: int = 4) -> list[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [line.strip() for line in lines if line.strip()][-keep:]


def _hidden(**fields) -> str:
    return "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items() if v not in (None, ""))


def _links(links: dict[str, str]) -> str:
    safe = [(k, v) for k, v in links.items() if v.startswith(("https://", "http://"))]
    if not safe:
        return ""
    return "<div class=links>" + "".join(f"<a href='{escape(v)}'>{escape(k)} ↗</a>" for k, v in safe) + "</div>"


def _out_links(lane: LaneData) -> str:
    return f"<a href='{escape(lane.site)}'>Site ↗</a><a href='{escape(lane.repo)}'>Code ↗</a>"


def _pause(p: Project, back: str) -> str:
    if p.paused:
        return action_form("resume", "Resume project", slug=p.slug, back=back)
    return action_form("pause", "Pause project", slug=p.slug, back=back)


def _tone(lane: LaneData) -> str:
    job, step = lane.job, plan.current(lane.steps)
    if job is None or job.state not in ACTIVE:
        return "finished"
    if lane.project.paused:
        return "paused"
    if job.state == "waiting":
        return "waiting"
    if step and step.error:
        return "trouble"
    return "running" if job.state == "running" else "queued"


def _state_tone(s: Step) -> str:
    return {"done": "finished", "failed": "trouble", "running": "running", "waiting": "waiting"}.get(s.state, "")


def _doing(lane: LaneData, s: Step, now: datetime) -> str:
    if s.kind == "agent":
        so_far = now - _dt(s.started_at) if s.started_at else timedelta(0)
        usual = estimate(lane.known, s)
        about = f" (usually {span(timedelta(seconds=usual))})" if plan.label(s) in lane.known else ""
        return f"{s.name}, {span(so_far)} in{about}"
    return {"checks": "waiting for CI", "deploy": "deploying", "merge": "merging", "pr": "opening a PR",
            "push": "pushing", "push-main": "pushing to main", "setup": "setting up",
            "checkin": "waiting for you to try it", "choose": "waiting for your pick"}.get(s.kind, plan.label(s))  # fmt: skip


def _waiting_words(lane: LaneData) -> str:
    step = plan.current(lane.steps)
    if step and step.kind == "checkin":
        return "is waiting for you to try it"
    if step and step.kind == "choose":
        return "is waiting for you to pick an option"
    return "has a question for you" if lane.job.resume_session else "needs a hint from you"


def _engine_words(p: Project) -> str:
    if not p.model and not p.effort:
        return "default models"
    return f"{p.model or 'default'}/{p.effort or 'default'}"


def _spent(lane: LaneData) -> str:
    runs = [t for t in lane.tallies.values() if t.runs]
    if not runs:
        return ""
    cost = sum(t.cost for t in runs)
    tokens = sum(t.tokens for t in runs)
    took = sum(_took(lane, s) for s in lane.steps if s.state in ("done", "failed") and s.kind not in plan.ASKS)
    return f"So far: {span(timedelta(seconds=took))} of work, {_money(cost)} of usage, {_count(tokens)} tokens."


def _took(lane: LaneData, s: Step) -> float:
    """Seconds a step took: an agent step's runs, or golem's step start to end."""
    tally = lane.tallies.get(s.id)
    if s.kind == "agent" and tally and tally.seconds:
        return tally.seconds
    if s.started_at and s.ended_at:
        return (_dt(s.ended_at) - _dt(s.started_at)).total_seconds()
    return 0.0


def _took_row(first: Step, last: Step) -> str:
    if not (first.started_at and last.ended_at):
        return ""
    return f"<time class=took>{last.ended_at[11:16]} · {span(_dt(last.ended_at) - _dt(first.started_at))}</time>"


def _seconds(run: dict) -> float:
    if not run.get("ended_at"):
        return 0.0
    return (_dt(run["ended_at"]) - _dt(run["started_at"])).total_seconds()


def _outcome(run: dict) -> str:
    return {"verdict": "finished", "max_turns": "out of turns", "limit": "hit the usage limit", None: "running"}.get(
        run["outcome"], str(run["outcome"])
    )


def _per(t: Tally | None) -> str:
    return f"{span(timedelta(seconds=t.per_step))} a step" if t and t.steps else ""


def _per_cost(t: Tally | None) -> str:
    return f"{_money(t.cost_per_step)} a step" if t and t.steps and t.cost else ""


def _share(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)}%" if whole else ""


def _money(cost: float) -> str:
    return f"${cost:,.2f}" if cost < 100 else f"${cost:,.0f}"


def _count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _mark(line: Line) -> str:
    return {"says": "💬", "call": "→", "result": "←", "error": "✗"}.get(line.kind, "")


def _dt(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now(UTC)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


STYLE = """
:root { --bg:#f3f4f7; --card:#fff; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --blue:#2563eb; --grey:#9ca3af;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b; --radius:14px; --nav:#0f172a; color-scheme: light dark;
  --c1:#2a78d6; --c2:#eb6834; --c3:#1baf7a; --c4:#eda100; --c5:#e87ba4; --c6:#008300; --c7:#6250d6; --c8:#e34948; }
@media (prefers-color-scheme: dark) { :root { --bg:#0b1020; --card:#131a2e; --ink:#e5e7eb; --muted:#94a3b8; --line:#243049;
  --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#c98500; --c5:#d55181; --c6:#008300; --c7:#9085e9; --c8:#e66767; } }
.c1 { --pc:var(--c1); } .c2 { --pc:var(--c2); } .c3 { --pc:var(--c3); } .c4 { --pc:var(--c4); }
.c5 { --pc:var(--c5); } .c6 { --pc:var(--c6); } .c7 { --pc:var(--c7); } .c8 { --pc:var(--c8); }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background:var(--bg); color:var(--ink);
  min-height:100vh; display:flex; flex-direction:column; }
a { color:var(--blue); text-decoration:none; }
h1, h2, h3 { margin:0; }
.top { position:sticky; top:0; z-index:5; display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:.5rem;
  padding:max(.55rem, env(safe-area-inset-top)) max(1rem, env(safe-area-inset-right)) .55rem max(1rem, env(safe-area-inset-left));
  background:var(--nav); color:#fff; }
.top h1 { grid-column:2; font-size:1.05rem; font-weight:700; letter-spacing:.01em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:60vw; }
.top .up { grid-column:1; color:#cbd5e1; font-size:.95rem; white-space:nowrap; }
.top .action { grid-column:3; justify-self:end; display:flex; gap:.6rem; } .top .action a { color:#cbd5e1; font-size:.95rem; }
main { flex:1; width:100%; max-width:46rem; margin:0 auto; padding:1rem max(.75rem, env(safe-area-inset-left)) 1.5rem; }
.banner { width:100%; max-width:46rem; margin:.75rem auto 0; padding:.6rem 1rem; border-radius:var(--radius); }
.banner.paused { background:#fef3c7; color:#92400e; }
.flash { position:fixed; z-index:20; left:50%; top:max(3.6rem, calc(env(safe-area-inset-top) + 3rem)); transform:translateX(-50%);
  width:max-content; max-width:calc(100% - 2rem); padding:.6rem 1rem; border-radius:999px; box-shadow:0 6px 24px rgb(0 0 0 / .25);
  transition:opacity .4s; background:#dbeafe; color:#1e3a8a; }
.dock { position:sticky; bottom:0; z-index:4; }
.to-latest { position:absolute; right:max(.9rem, env(safe-area-inset-right), calc((100% - 46rem) / 2)); bottom:calc(100% + .3rem); width:40px; height:40px; padding:0;
  border-radius:50%; border:1px solid var(--line); background:var(--card); color:var(--ink); font-size:1.15rem; box-shadow:0 2px 10px rgb(0 0 0 / .15); }
.to-latest[hidden] { display:none; }
.tabs { display:flex; justify-content:center; background:var(--card); border-top:1px solid var(--line);
  padding:.3rem max(.5rem, env(safe-area-inset-left)) max(.35rem, env(safe-area-inset-bottom)); }
.tabs a { flex:1; max-width:9rem; display:flex; flex-direction:column; align-items:center; gap:.1rem; color:var(--muted); font-size:.72rem; font-weight:600; padding:.2rem 0; }
.tabs svg { width:24px; height:24px; fill:currentColor; } .tabs a.on { color:var(--blue); }
.swatch { display:inline-block; width:.7rem; height:.7rem; border-radius:50%; background:var(--pc, var(--grey)); margin-right:.4rem; vertical-align:baseline; flex-shrink:0; }
.section-title { font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:1.2rem 0 .45rem; font-weight:700; }
.section-title:first-child { margin-top:.2rem; }
.hint { color:var(--muted); font-size:.8rem; margin:.4rem 0 0; }
.sub, .muted { color:var(--muted); font-size:.88em; margin:.15rem 0 0; }
.card { background:var(--card); border-radius:var(--radius); padding:.85rem 1rem; margin-bottom:.75rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); scroll-margin-top:4rem; }
.card h2 { font-size:1.02rem; } .card h2 a { color:inherit; }
.card-head { display:flex; justify-content:space-between; gap:.75rem; align-items:flex-start; }
.card-head h2 { display:flex; align-items:center; }
.out { display:flex; gap:.7rem; flex-shrink:0; font-size:.9em; }
.headline { margin:0; display:flex; gap:.5rem; align-items:baseline; flex-wrap:wrap; }
.headline > span:last-child { font-weight:600; }
.headline time { font-weight:700; }
.card.status { border-top:4px solid var(--pc, var(--grey)); }
.request { margin:.5rem 0 .2rem; font-size:.92em; } .request .muted { font-size:1em; }
.foot { color:var(--muted); font-size:.8rem; margin:.5rem 0 0; }
.badge { display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.75rem; font-weight:700; background:var(--line); color:var(--ink); white-space:nowrap; }
.badge.running { background:#dbeafe; color:#1e40af; } .badge.waiting { background:#fef3c7; color:#92400e; }
.badge.trouble { background:#fee2e2; color:#991b1b; } .badge.paused, .badge.queued { background:var(--line); color:var(--muted); }
.badge.finished { background:#dcfce7; color:#166534; }
.plan { margin-top:.6rem; } .plan > summary { cursor:pointer; color:var(--muted); font-size:.85rem; font-weight:600; }
.pieces { list-style:none; padding:0; margin:.4rem 0 0; position:relative; }
.piece { display:flex; flex-wrap:wrap; align-items:center; gap:.15rem .5rem; padding:.32rem 0; position:relative; }
.piece > a { display:flex; align-items:center; gap:.6rem; color:var(--ink); flex:1 1 auto; min-width:0; }
.piece .mark { position:relative; z-index:1; flex:0 0 1.35rem; height:1.35rem; border-radius:50%; display:grid; place-items:center; font-size:.72rem;
  font-weight:800; color:#fff; border:2px solid var(--grey); background:var(--card); }
.piece:not(:last-child)::before { content:""; position:absolute; left:calc(.675rem - 1px); top:1.2rem; bottom:-.45rem; width:2px; background:var(--line); }
.piece.done .mark { background:var(--pc, var(--good)); border-color:var(--pc, var(--good)); }
.piece.done:not(:last-child)::before { background:var(--pc, var(--good)); }
.piece.now .mark { border-color:var(--pc, var(--blue)); box-shadow:0 0 0 4px color-mix(in srgb, var(--pc, var(--blue)) 25%, transparent); }
.piece.now .mark::after { content:""; width:.55rem; height:.55rem; border-radius:50%; background:var(--pc, var(--blue)); }
.piece.now .name { font-weight:700; }
.piece.waiting .mark { background:var(--warning); border-color:var(--warning); }
.piece.waiting .name { font-weight:700; }
.piece.failed .mark { background:var(--critical); border-color:var(--critical); }
.piece.skipped .mark { border-style:dashed; color:var(--muted); } .piece.skipped .name { text-decoration:line-through; color:var(--muted); }
.piece.todo .name { color:var(--muted); }
.piece .meta { margin-left:auto; color:var(--muted); font-size:.82rem; font-variant-numeric:tabular-nums; white-space:nowrap; }
.piece .why { flex-basis:100%; margin:0 0 0 1.95rem; color:var(--muted); font-size:.8rem; }
.live { font-size:.7rem; font-weight:700; color:#166534; background:#dcfce7; border-radius:999px; padding:0 .45rem; }
.opt { font-size:.7rem; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:0 .4rem; }
.dots { display:inline-flex; gap:3px; align-items:center; }
.dots i { width:8px; height:8px; border-radius:50%; border:1.5px solid var(--grey); }
.dots i.done { background:var(--pc, var(--good)); border-color:var(--pc, var(--good)); }
.dots i.now { background:var(--pc, var(--blue)); border-color:var(--pc, var(--blue)); box-shadow:0 0 0 2px color-mix(in srgb, var(--pc, var(--blue)) 30%, transparent); }
.dots i.waiting { background:var(--warning); border-color:var(--warning); } .dots i.failed { background:var(--critical); border-color:var(--critical); }
.dots i.skipped { border-style:dashed; }
.working { margin:.5rem 0 0; padding:.55rem .7rem; border-radius:10px; background:color-mix(in srgb, var(--pc, var(--blue)) 8%, transparent); }
.working p { margin:.2rem 0; } .said { font-size:.98rem; }
.notes { margin:.3rem 0; padding-left:1.1rem; font-size:.9em; } .notes li::marker { content:"▸ "; color:var(--pc, var(--blue)); }
.ask { margin:.6rem 0 0; padding:.7rem .8rem; border-radius:12px; background:rgb(217 119 6 / .09); border:1px solid rgb(217 119 6 / .3); }
.ask > p { margin:.3rem 0; }
.ask .option { flex-basis:min(12rem, 62%); } .ask .option .shots a:first-child img { aspect-ratio:1 / 1; }
.ask .option .shots a:not(:first-child) { display:none; }
.tabs a { position:relative; } .tabs .dot { position:absolute; top:.35rem; right:calc(50% - 1.1rem); width:.5rem; height:.5rem;
  border-radius:50%; background:var(--blue); }
.version li { font-size:.88rem; overflow-wrap:anywhere; } .version code { color:var(--muted); margin-right:.35rem; }
.proposals { margin-top:.6rem; } .proposals ol { list-style:none; padding:0; margin:.5rem 0 0; }
.proposal { padding:.6rem 0; border-top:1px solid var(--line); } .proposal:first-child { border-top:0; }
.proposal h4 { margin:0; font-size:.98rem; } .proposal p { margin:.25rem 0; font-size:.9rem; }
.proposal input { font:inherit; font-size:16px; padding:.4rem .6rem; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); width:100%; box-sizing:border-box; margin-top:.3rem; }
.tail summary { cursor:pointer; color:var(--muted); font-size:.85em; }
.log { font:12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background:#0f172a; color:#cbd5e1; border-radius:10px;
  padding:.5rem .6rem; margin-top:.35rem; max-height:16rem; overflow:auto; }
.t { display:flex; gap:.5rem; } .t time { color:#64748b; flex-shrink:0; min-width:2.6rem; }
.t span { overflow-wrap:anywhere; } .t.says span { color:#f1f5f9; } .t.error span { color:#fca5a5; } .t.nested { padding-left:1rem; opacity:.85; }
.glance { display:block; color:var(--ink); }
.glance .card-head { align-items:center; }
.glance .line { display:flex; justify-content:space-between; align-items:center; gap:.6rem; margin-top:.35rem; font-size:.9rem; color:var(--muted); }
.glance .line b { color:var(--ink); font-weight:600; }
.compact { list-style:none; padding:0; margin:0; background:var(--card); border-radius:var(--radius); box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.compact li { display:flex; align-items:baseline; gap:.5rem; padding:.6rem 1rem; border-top:1px solid var(--line); }
.compact li:first-child { border-top:0; } .compact li > a:first-child { color:var(--ink); font-weight:600; display:flex; align-items:baseline; }
.compact .muted { margin:0; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.compact .when { margin-left:auto; color:var(--muted); font-size:.82rem; white-space:nowrap; }
.threads { list-style:none; padding:0; margin:0; background:var(--card); border-radius:var(--radius); box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.threads li { border-top:1px solid var(--line); } .threads li:first-child { border-top:0; }
.threads a { display:grid; grid-template-columns:auto 1fr auto; gap:.1rem .7rem; padding:.7rem 1rem; color:var(--ink); }
.threads .avatar { grid-row:1 / 3; width:2.4rem; height:2.4rem; border-radius:50%; background:var(--pc, var(--nav)); color:#fff; display:grid;
  place-items:center; font-weight:800; font-size:1rem; align-self:center; }
.threads b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.threads .when { color:var(--muted); font-size:.78rem; justify-self:end; }
.threads .last { grid-column:2 / 4; color:var(--muted); font-size:.88rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.threads .last .badge { margin-right:.35rem; }
.next h3, .done summary { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:.75rem 0 .3rem; }
.done summary { cursor:pointer; }
.steps { list-style:none; padding:0; margin:0; }
.row { padding:.45rem 0; border-top:1px solid var(--line); }
.row .what { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; } .row .what > a { color:var(--ink); }
.row p { margin:.2rem 0 0 1.4rem; color:var(--muted); font-size:.9em; }
.row .links, .row .took { margin-left:1.4rem; }
.icon { width:1rem; text-align:center; color:var(--good); } .row.failed .icon { color:var(--critical); } .row.skipped .icon { color:var(--grey); }
.row.pending b { font-weight:500; } .row.skipped b { text-decoration:line-through; color:var(--muted); }
.chips { display:inline-flex; flex-wrap:wrap; gap:.25rem; }
.chip { font-size:.78rem; padding:.05rem .45rem; border-radius:999px; border:1px solid var(--line); color:var(--muted); }
.chip.done { background:#dcfce7; color:#166534; border-color:transparent; } .chip.failed { background:#fee2e2; color:#991b1b; border-color:transparent; }
.chip.running { background:#dbeafe; color:#1e40af; border-color:transparent; }
.links { display:flex; flex-wrap:wrap; gap:.25rem .7rem; font-size:.85em; margin-top:.2rem; }
.took { display:inline; font-size:.8em; color:var(--muted); } time.took { display:block; }
.controls { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; margin-top:.75rem; padding-top:.75rem; border-top:1px dashed var(--line); }
form.inline { display:inline; margin:0; }
button, .button, select { font:inherit; font-size:.88rem; min-height:38px; padding:.35rem .85rem; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); cursor:pointer; }
select { padding-right:.4rem; max-width:13rem; }
.button { display:inline-flex; align-items:center; background:var(--blue); color:#fff; border-color:var(--blue); }
button.danger { color:var(--critical); }
button.quiet, summary.quiet { background:transparent; border:0; cursor:pointer; color:inherit; font-size:.9rem; padding:.35rem .5rem; }
.row button { min-height:30px; padding:.1rem .7rem; font-size:.8rem; }
.row .what form { margin-left:auto; }
button { white-space:nowrap; }
.reply { display:flex; gap:.4rem; margin:.5rem 0 0; width:100%; align-items:flex-end; }
.reply textarea, .composer textarea { flex:1; font:inherit; font-size:16px; line-height:1.35; padding:.55rem .8rem; border-radius:20px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); resize:none; max-height:40vh; min-height:42px; }
.signin-card form { margin-top:.6rem; } .signin-card input[type=password] { font:inherit; font-size:16px; padding:.45rem .7rem; border-radius:10px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); flex:1 1 10rem; min-width:0; }
.keep { display:flex; gap:.35rem; align-items:center; font-size:.82rem; color:var(--muted); flex-basis:100%; }
.notify-panel h2 { font-size:1rem; } .notify-panel .state { margin:.4rem 0; font-size:.9rem; }
.talk { margin:1rem 0 .5rem; }
.messages { list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:.2rem; }
.messages li { display:flex; flex-direction:column; align-items:flex-start; max-width:min(34rem, 88%); }
.messages li[data-day]::before { content:attr(data-day); align-self:center; margin:.9rem 0 .4rem; font-size:.75rem; font-weight:600;
  color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
.messages li.riggy { align-self:flex-end; align-items:flex-end; }
.messages li[data-day].riggy::before { align-self:flex-end; }
.bubble { background:var(--card); border:1px solid var(--line); border-radius:16px 16px 16px 4px; padding:.5rem .75rem; overflow-wrap:anywhere;
  box-shadow:0 1px 2px rgb(0 0 0 / .05); }
.bubble p { margin:0; } .bubble img { display:block; max-width:100%; max-height:22rem; border-radius:10px; margin:.1rem 0 .4rem; }
.riggy .bubble { background:var(--blue); border-color:var(--blue); color:#fff; border-radius:16px 16px 4px 16px; }
.riggy .bubble a { color:#fff; text-decoration:underline; }
.notify .bubble { border-left:4px solid var(--warning); }
.news .bubble { background:transparent; border-style:dashed; box-shadow:none; color:var(--muted); font-size:.9em; }
.messages time { font-size:.7rem; color:var(--muted); margin:.1rem .4rem .35rem; }
.settings .row-form + .row-form { margin-top:.75rem; } .settings input { font:inherit; font-size:16px; padding:.4rem .6rem; border-radius:10px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); min-width:0; flex:1 1 10rem; }
.settings .hint { flex-basis:100%; margin:0; }
.lenses { list-style:none; padding:0; margin:.4rem 0 0; } .lenses li { padding:.45rem 0; border-top:1px solid var(--line); }
.lenses li:first-child { border-top:0; } .lenses label { display:flex; gap:.45rem; align-items:center; }
.lenses input[type=checkbox] { width:20px; height:20px; flex:none; margin:0; padding:0; } .lenses .sub { display:block; margin:.15rem 0 0 1.75rem; }
.jobs ol { padding-left:1.1rem; margin:.5rem 0 0; } .jobs li { margin:.2rem 0; font-size:.9em; }
.links-list { list-style:none; padding:0; margin:0; } .links-list li { padding:.45rem 0; border-top:1px solid var(--line); }
.links-list li:first-child { border-top:0; } .links-list .sub { display:block; }
.dock .composer { padding:.9rem max(.75rem, env(safe-area-inset-left)) max(.6rem, env(safe-area-inset-bottom));
  background:linear-gradient(transparent, var(--bg) 30%); }
.dock .tabs { margin-top:0; } .dock .composer + .tabs { padding-top:.3rem; } .dock:has(.tabs) .composer { padding-bottom:.5rem; }
.composer { max-width:46rem; margin:0 auto; display:flex; gap:.4rem; align-items:flex-end; }
.composer textarea { box-shadow:0 2px 10px rgb(0 0 0 / .08); }
.composer .send { display:flex; gap:.3rem; flex-wrap:wrap; justify-content:flex-end; }
button.primary { background:var(--blue); color:#fff; border-color:var(--blue); border-radius:20px; min-height:42px; }
button.quiet-button { border-radius:20px; min-height:42px; }
button:disabled { opacity:.6; cursor:progress; }
.empty { color:var(--muted); text-align:center; padding:1.5rem 0; }
.earlier { display:block; width:max-content; margin:0 auto .6rem; padding:.35rem .9rem; border:1px solid var(--line); border-radius:20px;
  font-size:.82rem; color:var(--muted); text-decoration:none; background:var(--card); }
table { border-collapse:collapse; width:100%; font-size:.88em; } th, td { text-align:left; padding:.35rem .4rem; border-bottom:1px solid var(--line); }
td select { min-height:32px; font-size:.82rem; }
.runs { overflow-x:auto; background:var(--card); border-radius:var(--radius); padding:.5rem; }
dialog#sheet { border:0; padding:0; width:min(40rem, 100%); max-height:85vh; border-radius:18px; background:var(--card); color:var(--ink);
  box-shadow:0 12px 48px rgb(0 0 0 / .35); }
dialog#sheet::backdrop { background:rgb(15 23 42 / .45); }
dialog#sheet .close { position:sticky; float:right; top:.4rem; right:.4rem; z-index:1; font-size:1.1rem; }
.sheet-body { padding:1rem 1.1rem 1.4rem; }
.sheet-head h2 { font-size:1.3rem; margin:.15rem 0; }
.sheet-body h3 { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:1rem 0 .3rem; }
.sheet-body .tail { margin-top:.6rem; }
.facts { display:grid; grid-template-columns:max-content 1fr; gap:.25rem .9rem; margin:.75rem 0; font-size:.92em; }
.facts dt { color:var(--muted); } .facts dd { margin:0; }
.summary { background:var(--bg); border-radius:10px; padding:.6rem .75rem; }
.problem { white-space:pre-wrap; font-size:12px; background:var(--bg); padding:.5rem; border-radius:8px; max-height:14rem; overflow:auto; }
.row-form { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; width:100%; margin:.4rem 0 0; }
.row-form > span, .row-form label { color:var(--muted); font-size:.88rem; }
.options { list-style:none; display:flex; gap:.6rem; overflow-x:auto; padding:.15rem .1rem .5rem; margin:.5rem 0; scroll-snap-type:x mandatory;
  overscroll-behavior-x:contain; scrollbar-width:thin; }
.option { flex:0 0 min(15rem, 78%); scroll-snap-align:start; display:flex; flex-direction:column; background:var(--card); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; }
.option .shots { display:grid; grid-template-columns:repeat(2, 1fr); gap:2px; background:var(--line); }
.option .shots a:first-child, .option .shots a:nth-child(2):last-child { grid-column:1 / -1; }
.option .shots img { display:block; width:100%; aspect-ratio:2 / 1; object-fit:cover; object-position:top; background:var(--bg); }
.option .shots a:first-child img { aspect-ratio:4 / 5; }
.option-body { display:flex; flex-direction:column; gap:.3rem; flex:1; padding:.6rem .7rem .7rem; }
.option h4 { margin:0; font-size:.98rem; display:flex; gap:.45rem; align-items:center; }
.option .letter { display:inline-grid; place-items:center; flex:0 0 1.6rem; height:1.6rem; border-radius:50%; background:var(--pc, var(--blue));
  color:#fff; font-size:.85rem; font-weight:700; }
.option p { margin:0; font-size:.88rem; } .option .files { color:var(--muted); font-size:.75rem; overflow-wrap:anywhere; }
.option form { margin-top:auto; padding-top:.3rem; } .option form button { width:100%; background:var(--blue); color:#fff; border-color:var(--blue); }
@media (max-width:640px) {
  dialog#sheet { width:100%; max-width:100%; margin:auto 0 0; border-radius:18px 18px 0 0; max-height:88vh; }
  .card-head { flex-direction:column; gap:.3rem; }
}
.windows { display:flex; gap:.3rem; margin-bottom:.75rem; }
.windows a { padding:.3rem .75rem; border-radius:999px; border:1px solid var(--line); background:var(--card); color:var(--ink); font-size:.88rem; }
.windows a.on { background:var(--ink); color:var(--card); border-color:var(--ink); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(8rem, 1fr)); gap:.6rem; margin-bottom:.75rem; }
.tile { background:var(--card); border-radius:var(--radius); padding:.7rem .85rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.tile span { display:block; color:var(--muted); font-size:.8rem; } .tile b { font-size:1.4rem; font-weight:650; }
.findings { background:var(--card); border-radius:var(--radius); padding:.75rem 1rem .75rem 2rem; margin:0 0 1rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.findings li { margin:.2rem 0; }
.bars { background:var(--card); border-radius:var(--radius); padding:.8rem 1rem; margin-bottom:1rem; box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.bars h2, .defaults h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:.4rem; }
.bars h3 { font-size:.95rem; margin-bottom:.3rem; }
.bars ol { list-style:none; padding:0; margin:.5rem 0 0; }
.bars li { display:grid; grid-template-columns:minmax(5rem, 9rem) 1fr auto; gap:.15rem .6rem; align-items:center; padding:.2rem 0; }
.bars .name { font-size:.9rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bars .bar { height:12px; display:block; }
.bars .bar i { display:block; height:100%; border-radius:0 4px 4px 0; background:var(--c1); }
.bars.usage .bar i { background:var(--c2); }
.bars .val { font-size:.85rem; font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }
.bars .val small { color:var(--muted); margin-left:.25rem; }
.bars .extra { grid-column:2 / 4; color:var(--muted); font-size:.78rem; margin-top:-.15rem; }
.bars li:hover .bar i { filter:brightness(1.1); }
.two { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit, minmax(16rem, 1fr)); }
.two > div > h3 { font-size:.95rem; margin:.25rem 0; }
.hot { padding-left:1.2rem; margin:.25rem 0 1rem; } .hot li { margin:.2rem 0; } .hot span { color:var(--muted); font-variant-numeric:tabular-nums; }
.defaults { margin-top:1rem; } .defaults > form { display:block; margin-top:.6rem; }
.kinds { list-style:none; padding:.25rem 1rem; margin:.5rem 0 0; background:var(--card); border-radius:var(--radius); box-shadow:0 1px 2px rgb(0 0 0 / .06); }
.kinds li { display:flex; justify-content:space-between; align-items:center; gap:.4rem 1rem; flex-wrap:wrap; padding:.55rem 0; border-top:1px solid var(--line); }
.kinds li:first-child { border-top:0; } .kinds .muted { font-size:.8rem; margin-left:.3rem; }
.picks { display:flex; gap:.4rem; flex-wrap:wrap; font-size:.8rem; color:var(--muted); }
.picks label, .row-form label { display:inline-flex; gap:.3rem; align-items:center; }
.picks select { min-height:32px; font-size:.82rem; }
.picks > span { color:var(--ink); font-size:.88rem; } .picks > span + span::before { content:"· "; color:var(--muted); }
@media (max-width:640px) { .bars li { grid-template-columns:minmax(3.5rem, 6rem) 1fr auto; } }
"""

# Refresh the page's main part every 10 seconds without losing what's open, scrolled or being typed; show
# times on the viewer's clock; open a step's sheet in place.
SCRIPT = """
(() => {
  const main = document.getElementById('lanes');
  const sheet = document.getElementById('sheet');
  const sheetBody = sheet.querySelector('.sheet-body');
  const typing = (root) => root.contains(document.activeElement) && ['TEXTAREA','INPUT','SELECT'].includes(document.activeElement.tagName);
    function clocks(root) {
    root.querySelectorAll('[data-at]').forEach(el => {
      const at = new Date(el.dataset.at), now = new Date();
      if (isNaN(at)) return;
      const opts = {hour: 'numeric', minute: '2-digit'};
      if (at - now > 18 * 3600e3) opts.weekday = 'short';
      const clock = at.toLocaleTimeString([], opts);
      if (!el.title) el.title = el.textContent;
      el.textContent = (at > now ? '~' : '') + clock;
    });
  }
  function keepOpen(root, fn) {
    const open = new Set([...root.querySelectorAll('details[data-key]')].filter(d => d.open).map(d => d.dataset.key));
    const closed = new Set([...root.querySelectorAll('details[data-key]')].filter(d => !d.open).map(d => d.dataset.key));
    const logs = {}; root.querySelectorAll('.log').forEach(l => { logs[l.closest('details').dataset.key] = l.scrollHeight - l.scrollTop - l.clientHeight < 20; });
    fn();
    root.querySelectorAll('details[data-key]').forEach(d => {
      if (open.has(d.dataset.key)) d.open = true;
      if (closed.has(d.dataset.key)) d.open = false;
    });
    root.querySelectorAll('.log').forEach(l => { if (logs[l.closest('details').dataset.key] !== false) l.scrollTop = l.scrollHeight; });
  }
  let shown = '';  // '&show=N' once earlier messages are shown, so refreshing keeps them
  let refresh = async function () {
    if (document.hidden) return;
    if (!typing(main)) {
      try {
        const r = await fetch(location.pathname + '?part=lanes' + shown, {cache: 'no-store'});
        if (r.ok) { const html = await r.text(); keepOpen(main, () => { main.innerHTML = html; }); clocks(main); }
      } catch (e) {}
    }
    if (sheet.open && sheet.dataset.href && sheetBody.querySelector('[data-live]') && !typing(sheet)) openSheet(sheet.dataset.href, true);
  };
  async function openSheet(href, again) {
    try {
      const r = await fetch(href + (href.includes('?') ? '&' : '?') + 'part=sheet', {cache: 'no-store'});
      if (!r.ok) return;
      const html = await r.text();
      keepOpen(sheetBody, () => { sheetBody.innerHTML = html; });
      clocks(sheetBody);
    } catch (e) { return; }
    sheet.dataset.href = href;
    if (!again) {
      if (!sheet.open) sheet.showModal();
      sheetBody.querySelectorAll('.log').forEach(l => l.scrollTop = l.scrollHeight);
      history.replaceState(null, '', location.pathname + '#' + href);
    }
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-sheet]');
    if (!a || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    openSheet(a.getAttribute('href'));
  });
  sheet.querySelector('.close').addEventListener('click', () => sheet.close());
  sheet.addEventListener('click', e => { if (e.target === sheet) sheet.close(); });
  sheet.addEventListener('close', () => { delete sheet.dataset.href; history.replaceState(null, '', location.pathname); });
  function dates(root) {
    let last = '';
    root.querySelectorAll('.messages time[data-ts]').forEach(el => {
      const at = new Date(el.dataset.ts);
      if (isNaN(at)) return;
      el.textContent = at.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
      const day = at.toDateString(), today = new Date().toDateString(), yesterday = new Date(Date.now() - 864e5).toDateString();
      if (day !== last) el.closest('li').dataset.day = day === today ? 'Today' : day === yesterday ? 'Yesterday'
        : at.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
      last = day;
    });
  }
  const atBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
  const toBottom = () => window.scrollTo(0, document.body.scrollHeight);
  document.querySelectorAll('form[data-send]').forEach(form => {
    const box = form.querySelector('textarea');
    const fit = () => { box.style.height = 'auto'; box.style.height = box.scrollHeight + 2 + 'px'; };
    box.addEventListener('input', fit);
    box.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) form.requestSubmit(); });
    form.addEventListener('submit', e => {
      const button = e.submitter || form.querySelector('button.primary');
      setTimeout(() => { form.querySelectorAll('button').forEach(b => b.disabled = true); button.textContent = 'Sending…'; });
    });
  });
  main.addEventListener('click', async e => {
    const a = e.target.closest('a[data-earlier]');
    if (!a) return;
    e.preventDefault();
    const show = new URL(a.href).searchParams.get('show'), below = document.body.scrollHeight - window.scrollY;
    try {
      const r = await fetch(location.pathname + '?part=lanes&show=' + show, {cache: 'no-store'});
      if (!r.ok) return;
      const html = await r.text();
      keepOpen(main, () => { main.innerHTML = html; });
    } catch (err) { return; }
    shown = '&show=' + show;
    clocks(main); dates(main);
    window.scrollTo(0, document.body.scrollHeight - below);  // the messages already read stay where they were
  });
  const latest = document.querySelector('.to-latest');  // shown in a conversation scrolled up from its latest message
  const placeLatest = () => { if (latest) latest.hidden = !main.querySelector('.messages') || atBottom(); };
  latest?.addEventListener('click', () => window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'}));
  window.addEventListener('scroll', placeLatest, {passive: true});
  const oldRefresh = refresh;
  refresh = async () => { const stay = atBottom(); await oldRefresh(); dates(main); if (stay) toBottom(); placeLatest(); };
  clocks(document.body); dates(main);
  if (location.hash === '#end') { toBottom(); history.replaceState(null, '', location.pathname); }
  placeLatest();
  main.querySelectorAll('.log').forEach(l => l.scrollTop = l.scrollHeight);
  setInterval(() => refresh(), 10000);
  const flash = document.querySelector('.flash');
  if (flash) { setTimeout(() => flash.style.opacity = 0, 5000); setTimeout(() => flash.remove(), 5500); }
  if (location.search.includes('msg=')) history.replaceState(null, '', location.pathname + location.hash);
  if (location.hash.startsWith('#/')) {
    const href = location.hash.slice(1);
    const node = main.querySelector(`a[href="${CSS.escape(href)}"]`);
    if (node) node.closest('.card')?.scrollIntoView();
    openSheet(href);
  }
})();
"""
