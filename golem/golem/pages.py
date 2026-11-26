"""The web app's HTML, phone first: the page around everything, and the pieces the pages are
made of (home.py, conversation.py). Every page has the same way around: the tab bar (Home,
Projects, Settings) on a phone, and on a wide screen a sidebar with those and every project,
each a click from its conversation or its settings. A project's pages share a row of tabs
of their own: its conversation, its settings, and the job being looked at.

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

from . import brand, options, plan, progress, pwa, stages
from .control import SKIPPED
from .insights import Eta, Report, Tally, estimate
from .live import Line, view
from .store import ACTIVE, EFFORTS, LOOKS, MODELS, OPEN, Job, Project, Step
from .watch import span

ICONS = {"done": "✓", "skipped": "–", "running": "▶", "waiting": "?", "failed": "✗", "pending": ""}
TABS = (("now", "/", "Now"), ("projects", "/p", "Projects"), ("settings", "/settings", "Settings"))
# Which tab a page is under: any other (a project's, a job's, a new project) is under Projects.
TAB_OF = {"now": "now", "settings": "settings", "insights": "settings", "reports": "settings"}
# What golem's own steps do, for their sheets.
DOES = {
    "setup": "Makes sure the repo, the checkout and the job's issue exist.",
    "push": "Commits anything left and pushes the job's branch to GitHub.",
    "pr": "Opens a pull request for the work since the last merge.",
    "checks": "Waits for CI to pass on the pull request. If it fails, a fix step goes in and the shipping steps run again.",
    "merge": "Squash-merges the pull request into main.",
    "deploy": "Waits for CI on main, then deploys it. Done only once the site answers; if the deployer refuses it, a fix step goes in.",
    "checkin": "Shows you the live site and waits for your reply. Skipping it means “looks good, carry on”.",
    "choose": "Shows you the options the step before made, in its conversation, and waits for your pick. "
    "Your pick and what you say about it go to the next step, which builds it; asking for other options "
    "makes another round. Skipping it lets the next step pick the strongest.",
    "alert": "Tells you it's live. Always the last step, after the last deploy.",
    "refresh": "Brings the checkout up to date with what's live, unless a job's work is under way in it.",
    "test": "Runs the project's bin/test in the job's checkout: passes if it exits 0. If it fails, what it printed goes to the next block (a Fix, usually).",
    "check": "Runs a checklist item's check in the job's checkout, its planner's test files put back first: passes if it exits 0. "
             "If it fails, the item is built again with what failed (its last try on its Build's model), then golem carries on without it.",
    "answer": "Sends you the answer. A question changes nothing: no commits, pushes or deploys.",
    "sync": "Brings the job's branch up to date with main where its turn at building or shipping begins, when "
    "changes on a project take turns. If what shipped since conflicts with it, a resolve step goes in to merge them.",
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
    skippable: set[int]  # positions of the steps Riggy can skip
    restorable: set[int]  # and of those whose skip he can undo
    position: int | None  # place in the queue, 1 = next to run
    site: str
    repo: str
    color: int = 1  # the project's color, 1-8, by when it was made
    etas: dict[int, Eta] = field(default_factory=dict)  # by step position
    tallies: dict[int, Tally] = field(default_factory=dict)  # each agent step's runs, by step id
    known: dict[str, float] = field(default_factory=dict)  # seconds each kind of step usually takes


@dataclass
class NavProject:
    """A project in the sidebar."""

    slug: str
    name: str
    color: int
    state: str  # "waiting" (needs Riggy), "running" or ""


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


def page(title: str, body: str, signed_in: bool, paused: bool, flash: str = "", bottom: str = "", here: str = "",
         up: tuple[str, str] | None = None, subnav: str = "", nav: list[NavProject] = (), behind: bool = False) -> str:  # fmt: skip
    """The page around `body` (the part the script refreshes). `here` is where it is: "home",
    "projects", "settings", "insights", "inbox" or a project's slug; the tab bar and sidebar
    light it up. A page below the top level also has the way back (`up`: where, and its name);
    `subnav` is a row of tabs under the header (a project's: project_tabs). `bottom` sticks to the
    bottom of the screen, outside the refreshed part, so what's being written there is never
    lost. `behind` marks Settings: a new version of golem is on GitHub."""
    left = f"<a href='{escape(up[0])}' class=up>‹ {escape(up[1])}</a>" if up else ""
    tab, needs = TAB_OF.get(here, "projects"), sum(1 for p in nav if p.state == "waiting")
    marks = {"now": _count_mark(needs), "settings": "<i class=dot aria-label='new version'></i>" if behind else ""}
    tabs = "<nav class=tabs aria-label=Sections>" + "".join(
        f"<a href='{href}'{' class=on aria-current=page' if key == tab else ''}>{ICONS_SVG[key]}<span>{label}</span>{marks.get(key, '')}</a>"
        for key, href, label in TABS
    ) + "</nav>"
    new = "<a class=new-project href=/new aria-label='New project'><b>+</b><span> New project</span></a>" if signed_in else ""
    banner = "<div class='banner paused'>Work is paused. Nothing new starts until it's resumed (Settings).</div>" if paused else ""
    flash_html = f"<div class=flash role=status>{escape(flash)}</div>" if flash else ""
    latest = "<button type=button class=to-latest hidden aria-label='To the latest message'>↓</button>" if bottom else ""
    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name=theme-color content="#283035" media="(prefers-color-scheme: light)">
<meta name=theme-color content="#0d1012" media="(prefers-color-scheme: dark)">
{FONT_PRELOADS}<title>{escape("Golem" if title == "golem" else f"{title} · Golem")}</title><style>{STYLE}</style>
<link rel=icon href=/favicon.svg type="image/svg+xml">
<link rel=manifest href=/manifest.webmanifest>{pwa.touch_icon()}
<meta name=apple-mobile-web-app-capable content=yes><meta name=apple-mobile-web-app-title content=Golem>
<div class=bar><header class=top>{left}<h1>{brand.LOCKUP if title == "golem" else escape(title)}</h1>{new}</header>{subnav}</div>
{banner}{flash_html}<main id=lanes>{body}</main><div class=dock>{latest}{bottom}{tabs}</div>
{sidebar(nav, here, signed_in, marks)}
<dialog id=sheet aria-label='Step'><button class='quiet close' aria-label=Close>✕</button><div class=sheet-body></div></dialog>
<script>{SCRIPT}</script>{f"<script>{pwa.CLIENT}</script>" if signed_in else ""}</html>"""




def region(name: str, html: str) -> str:
    """A part of a page that refreshes on its own: the page's script asks for the page's parts
    (?parts=a,b) and swaps each that changed, unless something in it is being typed in."""
    return f"<div data-part={name}>{html}</div>"


def sidebar(nav: list[NavProject], here: str, signed_in: bool, marks: dict[str, str]) -> str:
    """The way around on a wide screen: the top level, a new project, then every project, each with
    a way straight to its settings. After `main` in the page, so the page's own content comes first
    to a screen reader; the style puts it on the left."""
    top = "".join(
        f"<a href='{href}' class='item{' on' if key == TAB_OF.get(here, 'projects' if here else '') else ''}'"
        f"{' aria-current=page' if key == TAB_OF.get(here, '') else ''}>{ICONS_SVG[key]}<span>{label}</span>{marks.get(key, '')}</a>"
        for key, href, label in TABS
    )
    new = f"<a href=/new class='item new{' on' if here == 'new' else ''}'><b class=plus>+</b><span>New project</span></a>" if signed_in else ""
    rows = []
    for p in nav:
        state = f"<i class='state {p.state}' aria-label='{'needs you' if p.state == 'waiting' else 'working'}'></i>" if p.state else ""
        gear = (
            f"<a class=gear href='/p/{escape(p.slug)}/settings' aria-label='Settings for {escape(p.name)}' title=Settings>{ICONS_SVG['settings']}</a>"
            if signed_in
            else ""
        )
        rows.append(
            f"<li class='c{p.color}{' on' if p.slug == here else ''}'><a href='/p/{escape(p.slug)}'><span class=swatch></span>"
            f"<span class=name>{escape(p.name)}</span>{state}</a>{gear}</li>"
        )
    return (
        f"<nav class=side aria-label=golem><a class=brand href='/'>{brand.LOCKUP}</a>{top}{new}"
        f"<h2>Projects</h2><ul class=side-projects>{''.join(rows) or '<li class=empty>None yet.</li>'}</ul></nav>"
    )


def project_tabs(project: Project, color: int, here: str, signed_in: bool, history: bool = False) -> str:
    """A project's own row of tabs, on each of its pages: its jobs (the latest, then all of them to
    search), its settings (signed in), and, if anything was said before jobs had their own
    activity, that (signed in). `here`: "jobs", "all", "settings" or "history"."""
    tabs = [("jobs", f"/p/{project.slug}", "Jobs"), ("all", f"/p/{project.slug}/jobs", "All jobs")]
    if signed_in:
        tabs.append(("settings", f"/p/{project.slug}/settings", "Settings"))
        if history:
            tabs.append(("history", f"/p/{project.slug}/history", "History"))
    return f"<nav class='subnav c{color}' aria-label='{escape(project.name)}'>" + "".join(
        f"<a href='{escape(href)}'{' class=on aria-current=page' if key == here else ''}>{escape(label)}</a>" for key, href, label in tabs
    ) + "</nav>"


def _count_mark(n: int) -> str:
    return f"<b class=count aria-label='{n} need{'s' if n == 1 else ''} you'>{n}</b>" if n else ""


# The tab bar's icons: a house, two speech bubbles, a gear; and the sidebar's bars, for insights.
ICONS_SVG = {
    "insights": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M4 20V11h3.5v9zm6.25 0V4h3.5v16zM16.5 20v-6H20v6z'/></svg>",
    "home": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z'/></svg>",
    # Now: a pulse, what's happening.
    "now": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M2 13h4.2l2.3-6 4 12 3-9 1.6 3H22v2h-5.9l-.6-1.2-3.1 9.3-4-12.1L7.6 15H2z'/></svg>",
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


def badge(lane: LaneData) -> str:
    tone = _tone(lane)
    if tone == "queued" and any(s.started_at for s in lane.steps):  # under way, between steps (waiting for CI)
        tone = "running"
    if tone == "finished":
        live = "Done" if lane.job and lane.job.start in LOOKS else "Live"  # a question or a look for proposals ships nothing
        word = {"done": live, "killed": "Stopped", "failed": "Failed"}.get(lane.job.state, "Idle") if lane.job else "Idle"
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
    turn = lane.state.startswith(("waiting for job", "shipping with job"))  # waiting its turn (turns.py, shipments.py)
    if turn and piece.step.state in ("done", "skipped") and not piece.shipping:  # its work's done: it waits to ship it
        return f"{escape(lane.state.capitalize())}."
    if piece.state == "todo" and turn:
        return f"{escape(lane.state.capitalize())}, then {escape(piece.name.lower())}."
    if piece.state == "todo":
        eta = lane.etas.get(piece.step.position)
        start = f", starting {when(eta.start, now)}" if eta else ""
        place = f"#{lane.position} in the queue" if lane.position and lane.position > 1 else "Next up"
        return f"{place}: {escape(piece.name.lower())}{start}."
    live = next((s for s in lane.steps if s.kind == "deploy" and s.state in OPEN), None)
    eta = lane.etas.get(live.position) if live else None
    then = f" Live {when(eta.end, now)}." if eta else ""
    return f"{escape(piece.name)}: {piece_meta(lane, piece, now)}.{then}"


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
            if s.state in ("done", "skipped"):  # its work's done and its shipping hasn't started: waiting its turn
                return "ready to ship"
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
        design = options.design_of(made)
        body = f"<p>{escape(made.summary if made and made.summary else 'Pick one.')}</p>" + options_html(job, shown, signed_in, back)
        body += design_html(job, design) if design else ""
        skip = "Go with your pick" if design and design.recommendation else "Let it pick"
        body += f"<div class=row-form>{skip_button(job, step, skip, back=back)}</div>" if signed_in else ""
        if design:
            hint, placeholder = "Or reply with a direction and your answers by number, or ask for another round.", "A, and your answers… or: another round"
        else:
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


def request_card(job: Job) -> str:
    """Where the job came from: the words that started it, in full, as they were said. Its title
    (plan.title) is what the rest of the app calls it."""
    return f"<section class=card><h2>The request</h2><p class=asked>{escape(job.request.strip())}</p></section>"


def job_controls(lane: LaneData) -> str:
    """Changes to the job under way: its place in the queue, and stopping it."""
    p, job = lane.project, lane.job
    if job is None or job.state not in ACTIVE:
        return ""
    back = f"/p/{p.slug}/settings"
    first = "" if lane.position == 1 else action_form("move", "Run next", job=job.id, value="first", back=back)
    return (
        f"<section class=card><h2>This job</h2><p class=sub>#{job.id}: {escape(plan.title(job))}</p><div class=row-form>"
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
    if s.name in ("fix", "resolve") and s.problem and signed_in:
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
        design = options.design_of(made)
        title = ("The directions" if design else "The options") if s.kind == "choose" else ("Its directions" if design else "Its options")
        body += f"<h3>{title}</h3>" + options_html(job, shown, pick, back=f"/p/{job.slug}") + (design_html(job, design) if design else "")
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
    if s.position in lane.skippable:
        out.append(skip_button(job, s, back=back))
    if s.position in lane.restorable:
        out.append(action_form("restore", "Undo skip", job=job.id, step=s.id, position=s.position, back=back))
    if job.state in ACTIVE and s.state in OPEN and lane.position != 1:
        out.append(action_form("move", f"Run {lane.project.name} next", job=job.id, value="first", back=back))
    if not out:
        return ""
    return "<section class='controls sheet-controls'>" + "".join(out) + "</section>"


# --- the job page's step lists --------------------------------------------------------


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
            name, how, base = "Every step", "unless set below", fallback
        else:
            name, base = kind, (every[0] or fallback[0], every[1] or fallback[1])
            if kind.startswith("stage:") and (stage := stages.catalog().get(kind[6:])):
                name = f"{stage.label} blocks"
        unset = (f"default: {base[0]}", f"default: {base[1]}")
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
        "<p class=hint>The model and effort each kind of agent step runs on. A project's own choice (in its settings) "
        "wins over these, and a single step's choice wins over both.</p>"
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


ROUTE_MARKS = {"done": "✓", "now": "▶", "waiting": "?", "failed": "✗", "skipped": "↷", "next": "•", "todo": ""}


def route_strip(job: Job, chips: list, small: bool = False, ahead: int | None = None) -> str:
    """A job's route through its workflow, as chips (route.py): where it's been, lit by how each
    went, then what's likely next, faded. A loop says ×N; a container fills as it goes. Each chip
    opens its block's sheet. `small`: a compact strip for lists. `ahead`: show at most this many
    of the blocks still to come, and how many more there are."""
    items = []
    later = [c for c in chips if c.predicted]
    hidden = later[ahead:] if ahead is not None else []
    for c in chips:
        if c in hidden:
            continue
        again = f"<small>×{c.visits}</small>" if c.visits > 1 else ""
        fill = f" style='--fill:{round(c.fill * 100)}%'" if c.container else ""
        kind = " box" if c.container else ""
        items.append(
            f"<li class='rc {c.state}{kind}'{fill}><a href='{escape(step_href(job, c.last))}' data-sheet title='{escape(c.label)}: {c.state}'>"
            f"<i aria-hidden=true>{ROUTE_MARKS.get(c.state, '')}</i>{escape(c.label)}{again}</a></li>"
        )
    if hidden:
        items.append(f"<li class='rc todo more'><a href='/p/{escape(job.slug)}/jobs/{job.id}'>+{len(hidden)} more</a></li>")
    return f"<ol class='route{' small' if small else ''}' aria-label='Its way through its workflow'>{''.join(items)}</ol>" if items else ""


def route_list(lane: LaneData, chips: list, signed_in: bool, now: datetime) -> str:
    """A job's route stood up (route.py): a row a block, with how long it took or when it's due,
    what it said, its links, and skipping it or undoing that (signed in). A container's row has
    its steps as chips. Its pane's Steps, and a phone's way through a job."""
    job = lane.job
    back = f"/p/{job.slug}/jobs/{job.id}"
    rows = []
    for c in chips:
        again = f"<small>×{c.visits}</small>" if c.visits > 1 else ""
        ran = [s for s in c.steps if s.id and s.state != "pending"]
        said = next((s.summary for s in reversed(ran) if s.summary), "")
        if c.state == "failed" and ran and ran[-1].error:
            said = ran[-1].error.strip().splitlines()[0]
        inner = ""
        if c.container:
            inner = "<span class=chips>" + "".join(
                f"<a class='chip {s.state}' href='{escape(step_href(job, s))}' data-sheet>{ICONS.get(s.state, '')} {escape(plan.label(s))}</a>"
                for s in c.steps
            ) + "</span>"
        button = ""
        if signed_in and (s := next((s for s in c.steps if s.position in lane.skippable), None)):
            button = skip_button(job, s, back=back)
        elif signed_in and (s := next((s for s in c.steps if s.position in lane.restorable), None)):
            button = action_form("restore", "Undo skip", job=job.id, step=s.id, position=s.position, back=back)
        optional = "<span class=opt>optional</span>" if not c.container and c.steps[0].optional and c.state == "todo" else ""
        links = "".join(_links(s.links) for s in ran if s.state in ("done", "failed"))
        rows.append(
            f"<li class='rl {c.state}{' box' if c.container else ''}'><div class=what>"
            f"<a href='{escape(step_href(job, c.last))}' data-sheet><i aria-hidden=true>{ROUTE_MARKS.get(c.state, '')}</i><b>{escape(c.label)}</b>{again}</a>"
            f"{optional}<span class=meta>{_chip_meta(lane, c, now)}</span>{button}</div>"
            + (f"<p>{escape(_short(said, 300))}</p>" if said else "")
            + inner + links + "</li>"
        )
    return f"<ol class=route-list>{''.join(rows)}</ol>" if rows else ""


def _chip_meta(lane: LaneData, c, now: datetime) -> str:
    """A block's line in the route list: how long it took, how it's going, or when it's due."""
    ran = [s for s in c.steps if s.id]
    match c.state:
        case "done":
            took = sum(_took(lane, s) for s in ran if s.kind not in plan.ASKS)
            return span(timedelta(seconds=took)) if took >= 1 else ""
        case "skipped":
            return "skipped"
        case "failed":
            return "failed"
        case "waiting":
            return "your turn"
        case "now":
            live = next((s for s in ran if s.state == "running"), c.last)
            if c.container or live.kind != "agent":
                return escape(_doing(lane, live, now))
            if not live.started_at:
                return "starting"
            usual = estimate(lane.known, live)
            about = f", usually {span(timedelta(seconds=usual))}" if plan.label(live) in lane.known else ""
            return f"{span(now - _dt(live.started_at))} so far{about}"
        case "next":
            return "under way" if c.container and ran else "next"
    eta = lane.etas.get(c.steps[0].position)
    return when(eta.start, now) if eta else ""


def trail(steps: list[Step], job: Job) -> str:
    """The way a job went through its workflow, block by block: each step that ran or was passed
    over, and the output it left by where it chose one."""
    shown = [s for s in plan.shown(steps) if s.state in ("done", "failed", "skipped")]
    if not shown:
        return "<p class=muted>No steps were recorded.</p>"
    items = []
    for s in shown:
        how = f" <span class=muted>→ {escape(s.port)}</span>" if s.port and s.port not in ("next", "done", "replied") else ""
        again = f" <span class=muted>(×{s.visit})</span>" if s.visit > 1 else ""
        items.append(f"<li class='chip {s.state}'><a href='{escape(step_href(job, s))}' data-sheet>{escape(plan.label(s))}</a>{again}{how}</li>")
    return f"<ol class=trail>{''.join(items)}</ol>"


def step_href(job: Job, step: Step) -> str:
    """A step's sheet. Steps of a job that hasn't started aren't stored yet, so go by position."""
    return f"/steps/{step.id}" if step.id else f"/jobs/{job.id}/planned/{step.position}"


def when(at: datetime, now: datetime) -> str:
    """A time to come or gone, in words; the page's script shows it on the viewer's clock."""
    delta = at - now
    words = f"in {span(delta)}" if delta.total_seconds() > 30 else ("now" if delta.total_seconds() > -60 else f"{span(-delta)} ago")
    return f"<time datetime='{at.isoformat()}' data-at='{at.isoformat()}'>{words}</time>"


def action_form(action: str, label: str, *, job: int | None = None, step: int | None = None, value: str = "",
                slug: str = "", back: str = "", confirm: str = "", danger: bool = False, position: int | None = None) -> str:  # fmt: skip
    """A button that posts an action. A step still to come has no id yet: it goes by its position."""
    where = {"step": step} if step else {"position": position} if position else {}
    hidden = _hidden(action=action, job=job, value=value, slug=slug, back=back, **where)
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
    return action_form("skip", label, job=job.id, step=step.id, position=step.position, back=back or f"/p/{job.slug}",
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


def design_html(job: Job, design: options.Design) -> str:
    """The rest of a design round, under its directions: the doc, the pages they share, the
    round's pick and what else it asks."""
    doc = f"<a class=button href='{escape(option_image(job, design.doc))}' target=_blank rel=noopener>Open the doc ↗</a>" if design.doc else ""
    pages = "".join(
        f"<a href='{escape(option_image(job, name))}' target=_blank rel=noopener><img src='{escape(option_image(job, name))}' "
        f"loading=lazy alt='Shared page {i}'></a>"
        for i, name in enumerate(design.pages, start=1)
    )
    pick = f"<p><b>My pick:</b> {escape(design.recommendation)}</p>" if design.recommendation else ""
    asks = "".join(f"<li>{escape(q)}</li>" for q in design.questions)
    return (
        "<div class=design>"
        + (f"<div class=shared>{pages}</div>" if pages else "")
        + (f"<div class=row-form>{doc}</div>" if doc else "")
        + pick
        + (f"<p class=muted>Also to decide:</p><ol class=questions>{asks}</ol>" if asks else "")
        + "</div>"
    )


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
            "push": "pushing", "setup": "setting up",
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


# The two typefaces, served from /fonts/ (web.py) and kept by browsers for a year, so a changed
# file needs a new name: IBM Plex Sans (variable, 100-700) to read, and Oxanium (variable,
# 200-800), the wordmark's face, for titles. Latin only; anything else falls back to the
# system's. Both are OFL, licences alongside.
FONTS = Path(__file__).parent / "fonts"
FONT_FILES = ("plex-sans.woff2", "oxanium.woff2")
FONT_PRELOADS = "".join(f"<link rel=preload href=/fonts/{name} as=font type=font/woff2 crossorigin>" for name in FONT_FILES)

# Masonry: slate and brick. The fonts, the colors (light and dark) and the project colors, shared
# with the projects page (gallery.py). Every text color passes WCAG AA on the surface it's used on.
TOKENS = """
@font-face { font-family:"Oxanium"; font-weight:200 800; font-display:swap; src:url(/fonts/oxanium.woff2) format("woff2");
  unicode-range:U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
@font-face { font-family:"IBM Plex Sans"; font-weight:100 700; font-display:swap; src:url(/fonts/plex-sans.woff2) format("woff2");
  unicode-range:U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD; }
:root { color-scheme: light dark; --radius:2px; --display:Oxanium, "DIN Alternate", Bahnschrift, system-ui, sans-serif;
  --body:"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif; --mono:ui-monospace, SFMono-Regular, Menlo, monospace;
  --bg:#dde0df; --card:#f2f3f2; --ink:#1c2023; --muted:#596166; --line:#c6cbca; --edge:#aab0af; --stone:#e0e3e2;
  --nav:#283035; --nav-ink:#eef1f1; --nav-muted:#9aa4aa; --accent:#9a3925; --accent-ink:#fff6f2; --ask-bg:#efe3c8;
  --lip:rgb(0 0 0 / .13); --lip-strong:rgb(0 0 0 / .28); --mark-ink:#fff;
  --rust:linear-gradient(135deg, #b34a30, #8c3220); --glow:#4a2b23;
  --band:linear-gradient(135deg, var(--nav) 30%, var(--glow) 72%, #8a3a2a);
  --good:#4a7a30; --warning:#b07a12; --critical:#a3283b; --running:#2c6b6e; --grey:#a3aaad;
  --run-bg:#d5e5e3; --run-fg:#1d5153; --wait-bg:#f0e1bf; --wait-fg:#6a4605; --bad-bg:#f1d4d8; --bad-fg:#87202f; --fin-bg:#dbe7d1; --fin-fg:#33551d;
  --s-run:#2c6b6e; --s-run-on:#fff; --s-wait:#c48a1a; --s-wait-on:#1f1503; --s-bad:#a3283b; --s-bad-on:#fff;
  --s-fin:#4a7a30; --s-fin-on:#fff; --s-q:#bfc3c2; --s-q-on:#1c2023;
  --c1:#3a6aa6; --c2:#c2652e; --c3:#2d8a7c; --c4:#c2961f; --c5:#b5577a; --c6:#5b8a2d; --c7:#7457ad; --c8:#c0433a; }
@media (prefers-color-scheme: dark) { :root { --bg:#14181a; --card:#1e2326; --ink:#e5e8e8; --muted:#99a2a7; --line:#323a3e; --edge:#090b0c;
  --stone:#2b3236; --nav:#0d1012; --nav-ink:#e5e8e8; --nav-muted:#7f898f; --accent:#d8684b; --accent-ink:#1a0c07; --ask-bg:#2c2617;
  --lip:rgb(0 0 0 / .35); --lip-strong:rgb(0 0 0 / .35); --mark-ink:#14181a;
  --rust:linear-gradient(135deg, #e47a5c, #d0603f); --glow:#2c1811;
  --band:linear-gradient(135deg, var(--nav) 30%, var(--glow) 72%, #7a3122);
  --good:#8cc26a; --warning:#e0ac3e; --critical:#ec7f8e; --running:#6fbdb8; --grey:#5a6266;
  --run-bg:#1d3637; --run-fg:#9ed2ce; --wait-bg:#3b2e15; --wait-fg:#ecc56a; --bad-bg:#42202a; --bad-fg:#f3a7b2; --fin-bg:#24331c; --fin-fg:#acd48e;
  --s-run:#4fa29d; --s-run-on:#0d1a1a; --s-wait:#e0ac3e; --s-wait-on:#1f1503; --s-bad:#e06a7a; --s-bad-on:#1d0a0e;
  --s-fin:#7fb85c; --s-fin-on:#0f1a0a; --s-q:#3a4045; --s-q-on:#d0d4d6;
  --c1:#5b8fd0; --c2:#df7f45; --c3:#3fae9d; --c4:#ddb03a; --c5:#d77799; --c6:#7fae47; --c7:#9a80d6; --c8:#e0665b; } }
@media (prefers-color-scheme: light) { .c4 { --mark-ink:#1f1503; } }
.c1 { --pc:var(--c1); } .c2 { --pc:var(--c2); } .c3 { --pc:var(--c3); } .c4 { --pc:var(--c4); }
.c5 { --pc:var(--c5); } .c6 { --pc:var(--c6); } .c7 { --pc:var(--c7); } .c8 { --pc:var(--c8); }
* { box-sizing:border-box; }
"""
# Ember, the app icon's gradient, recurs at its angle (135deg, lit from the top-left): --ember
# itself on golem's own face and the toast, --band (slate warming into it) along the header and
# down the sidebar, and --rust, brick lit the same way, on the main buttons and what Riggy says.
TOKENS += f":root {{ --ember:{brand.gradient(brand.EMBER)}; --ember-ink:{brand.EMBER_INK}; }}\n"

STYLE = TOKENS + """
body { margin:0; font:15px/1.45 var(--body); background:var(--bg); color:var(--ink); min-height:100vh; display:flex; flex-direction:column;
  -webkit-font-smoothing:antialiased; }
a { color:var(--accent); text-decoration:none; }
h1, h2, h3 { margin:0; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.bar { position:sticky; top:0; z-index:5; }
.top { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:.5rem;
  padding:max(.55rem, env(safe-area-inset-top)) max(1rem, env(safe-area-inset-right)) calc(.55rem + 3px) max(1rem, env(safe-area-inset-left));
  background:var(--nav) var(--band); color:var(--nav-ink); box-shadow:inset 0 -3px 0 var(--accent); }
.top h1 { grid-column:2; font:700 1.2rem/1.25 var(--display); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:60vw; }
.top h1 .lockup { display:block; height:1.4rem; width:auto; margin:.05rem 0 -.05rem; }
.top .up { grid-column:1; color:var(--nav-muted); font-size:.95rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.top .up:hover { color:var(--nav-ink); }
.subnav { display:flex; justify-content:center; gap:.25rem; overflow-x:auto; background:var(--card); box-shadow:0 2px 0 var(--edge);
  padding:0 max(.75rem, env(safe-area-inset-left)); scrollbar-width:none; }
.subnav a { padding:.6rem .85rem .45rem; color:var(--muted); font-size:.9rem; font-weight:600; white-space:nowrap; border-bottom:3px solid transparent; }
.subnav a.on { color:var(--ink); border-bottom-color:var(--pc, var(--accent)); }
.count { position:absolute; top:.05rem; left:calc(50% + .3rem); min-width:1.1rem; height:1.1rem; padding:0 .3rem; border-radius:1px;
  background:var(--s-wait); color:var(--s-wait-on); font-size:.68rem; font-weight:700; line-height:1.1rem; text-align:center; }
.side { display:none; }
@media (min-width:64rem) {
  body { padding-left:16rem; }
  .dock .tabs { display:none; }
  .flash { left:calc(50% + 8rem); }
  .side { display:flex; flex-direction:column; position:fixed; z-index:6; inset:0 auto 0 0; width:16rem; overflow-y:auto;
    padding:.6rem .6rem 1.5rem; background:var(--nav) linear-gradient(135deg, var(--nav) 62%, var(--glow)); color:var(--nav-muted); }
  .side .brand { color:var(--nav-ink); padding:.6rem .7rem 1rem; }
  .side .brand .lockup { display:block; height:1.35rem; width:auto; }
  .side .item, .side-projects a { position:relative; display:flex; align-items:center; gap:.6rem; padding:.45rem .7rem; border-radius:var(--radius); color:var(--nav-muted); }
  .side .item svg { width:20px; height:20px; fill:currentColor; flex:none; }
  .side .item:hover, .side-projects a:hover { background:rgb(255 255 255 / .06); color:var(--nav-ink); }
  .side .item.on, .side-projects li.on > a:first-child { background:rgb(255 255 255 / .09); color:var(--nav-ink); box-shadow:inset 3px 0 0 var(--accent); }
  .side .count { position:static; margin-left:auto; }
  .side .dot { width:.5rem; height:.5rem; border-radius:1px; background:var(--accent); margin-left:auto; }
  .side h2 { font-size:.7rem; text-transform:uppercase; letter-spacing:.1em; color:var(--nav-muted); margin:1.2rem .7rem .35rem; }
  .side-projects { list-style:none; padding:0; margin:0; }
  .side-projects li { display:flex; align-items:center; }
  .side-projects li > a:first-child { flex:1; min-width:0; }
  .side-projects .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .side-projects .swatch { margin:0; }
  .side-projects .gear { padding:.45rem .5rem; opacity:.55; } .side-projects .gear:hover { opacity:1; }
  .side-projects .gear svg { width:16px; height:16px; fill:currentColor; display:block; }
  .side .state { width:.5rem; height:.5rem; border-radius:1px; margin-left:auto; flex:none; }
  .side .state.waiting { background:var(--warning); } .side .state.running { background:var(--running); }
}
main { flex:1; width:100%; max-width:46rem; margin:0 auto; padding:1rem max(.75rem, env(safe-area-inset-left)) 1.5rem; }
.banner { width:100%; max-width:46rem; margin:.75rem auto 0; padding:.6rem 1rem; border-radius:var(--radius); }
.banner.paused { background:var(--wait-bg); color:var(--wait-fg); box-shadow:inset 4px 0 0 var(--warning); }
.flash { position:fixed; z-index:20; left:50%; top:max(3.6rem, calc(env(safe-area-inset-top) + 3rem)); transform:translateX(-50%);
  width:max-content; max-width:calc(100% - 2rem); padding:.6rem 1.1rem; border-radius:var(--radius);
  box-shadow:0 3px 0 rgb(0 0 0 / .3); transition:opacity .4s; background:var(--ember); color:#f2ede8; }
.dock { position:sticky; bottom:0; z-index:4; }
.to-latest { position:absolute; right:max(.9rem, env(safe-area-inset-right), calc((100% - 46rem) / 2)); bottom:calc(100% + .3rem); width:40px; height:40px; padding:0;
  border-radius:var(--radius); background:var(--card); color:var(--ink); font-size:1.15rem; box-shadow:0 0 0 1px var(--line), 0 3px 0 var(--edge); }
.to-latest[hidden] { display:none; }
.tabs { display:flex; justify-content:center; background:var(--card); border-top:1px solid var(--line);
  padding:.3rem max(.5rem, env(safe-area-inset-left)) max(.35rem, env(safe-area-inset-bottom)); }
.tabs a { position:relative; flex:1; max-width:9rem; display:flex; flex-direction:column; align-items:center; gap:.1rem; color:var(--muted);
  font-size:.72rem; font-weight:600; padding:.2rem 0; }
.tabs svg { width:24px; height:24px; fill:currentColor; } .tabs a.on { color:var(--accent); }
.tabs a.on::before { content:""; position:absolute; top:calc(-.3rem - 1px); left:28%; right:28%; height:3px; background:var(--accent); }
.tabs .dot { position:absolute; top:.35rem; right:calc(50% - 1.1rem); width:.5rem; height:.5rem; border-radius:1px; background:var(--accent); }
.swatch { display:inline-block; width:.8rem; height:.5rem; border-radius:1px; background:var(--pc, var(--grey)); margin-right:.45rem; vertical-align:.08em; flex-shrink:0; }
.section-title { display:flex; align-items:center; gap:.5rem; font:700 1.08rem/1.2 var(--display); color:var(--ink); margin:1.35rem 0 .6rem; }
.section-title::before { content:""; flex:none; width:.75rem; height:.5rem; background:var(--accent); }
.section-title:first-child { margin-top:.25rem; }
.hint { color:var(--muted); font-size:.8rem; margin:.4rem 0 0; }
.sub, .muted { color:var(--muted); font-size:.88em; margin:.15rem 0 0; }
.card { background:var(--card); border-radius:var(--radius); padding:.85rem 1rem; margin-bottom:.85rem; box-shadow:0 2px 0 var(--edge); scroll-margin-top:6rem; }
.card h2 { font:700 1.12rem/1.25 var(--display); } .card h2 a { color:inherit; }
.card-head { display:flex; justify-content:space-between; gap:.75rem; align-items:flex-start; }
.card-head h2 { display:flex; align-items:center; }
.out { display:flex; gap:.7rem; flex-shrink:0; font-size:.9em; }
.headline { margin:0; display:flex; gap:.5rem; align-items:baseline; flex-wrap:wrap; }
.headline > span:last-child { font-weight:600; }
.headline time { font-weight:700; }
.card.status { border-top:5px solid var(--pc, var(--grey)); }
.request { margin:.5rem 0 .2rem; font-size:.92em; } .request .muted { font-size:1em; }
.design { margin:.4rem 0 .2rem; } .design p { margin:.4rem 0; } .questions { margin:.2rem 0 .4rem; padding-left:1.3rem; }
.shared { display:flex; gap:.4rem; overflow-x:auto; padding-bottom:.3rem; }
.shared img { height:9rem; width:auto; display:block; border-radius:var(--radius); border:1px solid var(--line); }
.asked { white-space:pre-wrap; margin:.3rem 0 0; }
.foot { color:var(--muted); font-size:.8rem; margin:.5rem 0 0; }
.badge { display:inline-block; padding:.1rem .5rem; border-radius:1px; font-size:.75rem; font-weight:700; line-height:1.45; white-space:nowrap;
  background:var(--s-q); color:var(--s-q-on); }
.badge.running { background:var(--s-run); color:var(--s-run-on); } .badge.waiting { background:var(--s-wait); color:var(--s-wait-on); }
.badge.trouble { background:var(--s-bad); color:var(--s-bad-on); } .badge.finished { background:var(--s-fin); color:var(--s-fin-on); }
.plan { margin-top:.6rem; } .plan > summary { cursor:pointer; color:var(--muted); font-size:.85rem; font-weight:600; }
.pieces { list-style:none; padding:0; margin:.4rem 0 0; position:relative; }
.piece { display:flex; flex-wrap:wrap; align-items:center; gap:.15rem .5rem; padding:.32rem 0; position:relative; }
.piece > a { display:flex; align-items:center; gap:.6rem; color:var(--ink); flex:1 1 auto; min-width:0; }
.piece .mark { position:relative; z-index:1; flex:0 0 1.35rem; height:1.35rem; border-radius:1px; display:grid; place-items:center; font-size:.72rem;
  font-weight:700; color:var(--mark-ink); border:2px solid var(--grey); background:var(--card); }
.piece:not(:last-child)::before { content:""; position:absolute; left:calc(.675rem - 2px); top:1.2rem; bottom:-.45rem; width:4px; background:var(--line); }
.piece.done .mark { background:var(--pc, var(--good)); border-color:var(--pc, var(--good)); }
.piece.done:not(:last-child)::before { background:var(--pc, var(--good)); }
.piece.now .mark { border-color:var(--pc, var(--accent)); }
.piece.now .mark::after { content:""; width:.55rem; height:.55rem; border-radius:1px; background:var(--pc, var(--accent)); }
.piece.now .name { font-weight:700; }
.piece.waiting .mark { background:var(--warning); border-color:var(--warning); }
.piece.waiting .name { font-weight:700; }
.piece.failed .mark { background:var(--critical); border-color:var(--critical); }
.piece.skipped .mark { border-style:dashed; color:var(--muted); } .piece.skipped .name { text-decoration:line-through; color:var(--muted); }
.piece.todo .name { color:var(--muted); }
.piece .meta { margin-left:auto; color:var(--muted); font-size:.82rem; font-variant-numeric:tabular-nums; white-space:nowrap; }
.piece .why { flex-basis:100%; margin:0 0 0 1.95rem; color:var(--muted); font-size:.8rem; }
.live { font-size:.7rem; font-weight:700; color:var(--fin-fg); background:var(--fin-bg); border-radius:1px; padding:0 .4rem; }
.opt { font-size:.7rem; color:var(--muted); border:1px solid var(--line); border-radius:1px; padding:0 .35rem; }
.dots { display:inline-flex; gap:2px; align-items:center; flex:none; }
.dots i { width:13px; height:7px; border-radius:1px; background:var(--line); }
.dots i.done { background:var(--pc, var(--good)); }
.dots i.now { background:repeating-linear-gradient(135deg, var(--pc, var(--accent)) 0 2px, color-mix(in srgb, var(--pc, var(--accent)) 35%, var(--card)) 2px 4px); }
.dots i.waiting { background:var(--warning); } .dots i.failed { background:var(--critical); }
.dots i.skipped { background:transparent; outline:1px dashed var(--grey); outline-offset:-1px; }
.working { margin:.5rem 0 0; padding:.55rem .7rem; border-radius:var(--radius); background:color-mix(in srgb, var(--pc, var(--accent)) 9%, var(--card)); }
.working p { margin:.2rem 0; } .said { font-size:.98rem; }
.notes { margin:.3rem 0; padding-left:1.1rem; font-size:.9em; } .notes li::marker { content:"▸ "; color:var(--pc, var(--accent)); }
.ask { margin:.6rem 0 0; padding:.7rem .8rem .7rem .9rem; border-radius:0 var(--radius) var(--radius) 0; background:var(--ask-bg); border-left:4px solid var(--warning); }
.ask > p { margin:.3rem 0; }
.ask .option { flex-basis:min(12rem, 62%); } .ask .option .shots a:first-child img { aspect-ratio:1 / 1; }
.ask .option .shots a:not(:first-child) { display:none; }
.version li { font-size:.88rem; overflow-wrap:anywhere; } .version code { color:var(--muted); margin-right:.35rem; }
.proposals { margin-top:.6rem; } .proposals ol { list-style:none; padding:0; margin:.5rem 0 0; }
.proposal { padding:.6rem 0; border-top:1px solid var(--line); } .proposal:first-child { border-top:0; }
.proposal h4 { margin:0; font:700 1.02rem/1.3 var(--display); } .proposal p { margin:.25rem 0; font-size:.9rem; }
.proposal input { font:inherit; font-size:16px; padding:.4rem .6rem; border-radius:var(--radius); border:2px solid var(--line);
  background:var(--card); color:var(--ink); width:100%; box-sizing:border-box; margin-top:.3rem; }
.tail summary { cursor:pointer; color:var(--muted); font-size:.85em; }
.log { font:12px/1.5 var(--mono); background:#0d1012; color:#c9d1d4; border-radius:var(--radius); padding:.5rem .6rem; margin-top:.35rem;
  max-height:16rem; overflow:auto; }
.t { display:flex; gap:.5rem; } .t time { color:#7f898f; flex-shrink:0; min-width:2.6rem; }
.t span { overflow-wrap:anywhere; } .t.says span { color:#f1f4f4; } .t.error span { color:#f3a7b2; } .t.nested { padding-left:1rem; opacity:.85; }
.glance { display:block; color:var(--ink); }
.glance .card-head { align-items:center; }
.glance .line { display:flex; justify-content:space-between; align-items:center; gap:.6rem; margin-top:.35rem; font-size:.9rem; color:var(--muted); }
.glance .line b { color:var(--ink); font-weight:600; }
.glance .dots + span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.compact { list-style:none; padding:0; margin:0; background:var(--card); border-radius:var(--radius); box-shadow:0 2px 0 var(--edge); }
.compact li { display:flex; align-items:baseline; gap:.5rem; padding:.6rem 1rem; border-top:1px solid var(--line); }
.compact li:first-child { border-top:0; }
.compact li > a:first-child { color:var(--ink); font:700 1.02rem/1.3 var(--display); display:flex; align-items:baseline; }
.compact .muted { margin:0; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.compact .when { margin-left:auto; color:var(--muted); font-size:.82rem; white-space:nowrap; }
.threads { list-style:none; padding:0; margin:0; background:var(--card); border-radius:var(--radius); box-shadow:0 2px 0 var(--edge); }
.threads li { display:flex; align-items:center; border-top:1px solid var(--line); } .threads li:first-child { border-top:0; }
.threads .gear { padding:.7rem 1rem .7rem .4rem; color:var(--muted); } .threads .gear svg { width:20px; height:20px; fill:currentColor; display:block; }
.threads .gear:hover { color:var(--ink); }
.threads a.thread { flex:1; min-width:0; display:grid; grid-template-columns:auto 1fr auto; gap:.1rem .7rem; padding:.7rem 1rem; color:var(--ink); }
.threads .avatar { grid-row:1 / 3; width:2.4rem; height:2.4rem; border-radius:var(--radius); background:var(--pc, var(--nav)); color:var(--mark-ink);
  display:grid; place-items:center; font:700 1.2rem var(--display); align-self:center; box-shadow:inset 0 -3px 0 var(--lip-strong); }
.threads li.c0 .avatar { background:var(--ember); color:var(--ember-ink); } .threads .avatar .mark { width:1.55rem; height:1.55rem; }
.threads b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font:700 1.02rem/1.35 var(--display); }
.threads .when { color:var(--muted); font-size:.78rem; justify-self:end; }
.threads .last { grid-column:2 / 4; color:var(--muted); font-size:.88rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.threads .last .badge { margin-right:.35rem; }
.next h3, .done summary { font-size:.75rem; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); margin:.75rem 0 .3rem; }
.done summary { cursor:pointer; }
.steps { list-style:none; padding:0; margin:0; }
.row { padding:.45rem 0; border-top:1px solid var(--line); }
.row .what { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; } .row .what > a { color:var(--ink); }
.row p { margin:.2rem 0 0 1.4rem; color:var(--muted); font-size:.9em; }
.row .links, .row .took { margin-left:1.4rem; }
.icon { width:1rem; text-align:center; color:var(--good); } .row.failed .icon { color:var(--critical); } .row.skipped .icon { color:var(--grey); }
.row.pending b { font-weight:500; } .row.skipped b { text-decoration:line-through; color:var(--muted); }
.chips { display:inline-flex; flex-wrap:wrap; gap:.25rem; }
.chip { font-size:.78rem; padding:.05rem .4rem; border-radius:1px; border:1px solid var(--line); color:var(--muted); }
/* A job's route through its workflow (route.py) */
.route { display:flex; flex-wrap:wrap; align-items:center; gap:.3rem 0; list-style:none; padding:0; margin:.45rem 0 .2rem; }
.rc { display:flex; align-items:center; }
.rc + .rc::before { content:""; width:.55rem; height:2px; background:var(--line); }
.rc.done + .rc::before, .rc.skipped + .rc::before { background:var(--s-fin); }
.rc a { display:inline-flex; align-items:center; gap:.25rem; padding:.18rem .5rem; border-radius:var(--radius); font-size:.8rem; line-height:1.3;
  color:var(--ink); text-decoration:none; background:var(--bg); border:1px solid var(--line); white-space:nowrap; }
.rc a:hover { border-color:var(--accent); }
.rc i { font-style:normal; font-weight:700; font-size:.72rem; }
.rc small { font-size:.7rem; font-weight:700; color:var(--run-fg); }
.rc.done a { background:var(--fin-bg); color:var(--fin-fg); border-color:transparent; }
.rc.now a, .rc.next a { background:var(--run-bg); color:var(--run-fg); border-color:var(--s-run); }
.rc.now a { box-shadow:0 0 0 2px color-mix(in srgb, var(--s-run) 35%, transparent); }
.rc.waiting a { background:var(--wait-bg); color:var(--wait-fg); border-color:var(--s-wait); font-weight:700; }
.rc.failed a { background:var(--bad-bg); color:var(--bad-fg); border-color:transparent; }
.rc.skipped a { opacity:.6; border-style:dashed; }
.rc.todo a { color:var(--muted); border-style:dashed; opacity:.75; }
.rc.box a { background:linear-gradient(to right, var(--fin-bg) var(--fill, 0%), var(--bg) var(--fill, 0%)); border-color:var(--line); color:var(--ink); }
.rc.box.now a, .rc.box.next a { border-color:var(--s-run); } .rc.box.waiting a { border-color:var(--s-wait); } .rc.box.failed a { border-color:var(--s-bad); }
.rc.box.done a { background:var(--fin-bg); color:var(--fin-fg); border-color:transparent; }
.route.small .rc a { padding:.08rem .38rem; font-size:.72rem; } .route.small .rc + .rc::before { width:.35rem; }
/* Now and Projects (now.py): jobs, a card or a row each, drawn by their route */
.card.job .card-head h2, .project-card .card-head h2, .proposals-now .card-head h2 { display:flex; flex-wrap:wrap; align-items:baseline; gap:.15rem .55rem; }
.job-title { font:600 .95rem/1.3 var(--body); color:var(--ink); }
a.job-title:hover { color:var(--accent); }
.card.job .sub { margin:.15rem 0 0; }
.card.job .note { margin:.35rem 0 0; color:var(--muted); font-size:.88rem; }
.jobs-list { list-style:none; padding:0; margin:0 0 1rem; display:flex; flex-direction:column; gap:.45rem; }
.jobs-list > li { background:var(--card); border-radius:var(--radius); box-shadow:0 2px 0 var(--edge); padding:.55rem .8rem .45rem; }
.jobs-list .row-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:.15rem .6rem; }
.jobs-list .row-head > a:first-child { font:700 .95rem var(--display); color:var(--ink); display:inline-flex; align-items:center; gap:.4rem; }
.jobs-list .row-head .when { margin-left:auto; color:var(--muted); font-size:.82rem; }
.jobs-list .said { margin:.3rem 0 0; color:var(--muted); font-size:.88rem; }
main:has(> .project-grid) { max-width:min(100%, 78rem); }
.project-card .shot { display:block; margin:-.85rem -1rem .6rem; aspect-ratio:16/9; overflow:hidden; border-radius:var(--radius) var(--radius) 0 0; background:var(--bg); }
.project-card .shot img { width:100%; height:100%; object-fit:cover; object-position:top; display:block; }
.proposals-now summary { cursor:pointer; list-style:none; } .proposals-now summary::-webkit-details-marker { display:none; }
.proposals-now summary .count-pill { margin-left:auto; }
.project-grid { display:grid; gap:.9rem; grid-template-columns:repeat(auto-fill, minmax(min(100%, 21rem), 1fr)); align-items:start; }
.project-card .meta { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; font-size:.85rem; margin:.3rem 0; }
.project-card .latest { margin:.6rem 0 0; font-size:.9rem; }
.tag-chip { font-size:.72rem; font-weight:700; padding:.05rem .45rem; border-radius:1px; background:var(--pc, var(--line)); color:#fff; }
.news { margin:0 0 1rem; padding:.6rem .9rem; border-radius:var(--radius); background:var(--card); box-shadow:0 2px 0 var(--edge); font-size:.9rem; }
.news.live { background:var(--wait-bg); color:var(--wait-fg); } .news p { margin:.1rem 0; }
.news details summary { cursor:pointer; color:var(--muted); font-size:.85rem; } .news ul { margin:.4rem 0 0; padding-left:1rem; }
.news li .when { color:var(--muted); font-size:.8rem; margin-right:.3rem; }
/* A project's page (projectpage.py): its jobs beside the one being looked at */
main:has(> .panes) { max-width:min(100%, 96rem); }
.panes { display:grid; gap:1rem; align-items:start; }
.jobs-pane, .job-pane { min-width:0; }
@media (min-width:56rem) {
  .panes { grid-template-columns:minmax(19rem, 25rem) minmax(0, 1fr); }
}
@media (max-width:55.99rem) {
  .panes[data-focus=list] .job-pane, .panes[data-focus=job] .jobs-pane { display:none; }
}
.project-head { margin:0 0 .8rem; } .project-head p { margin:0 0 .3rem; color:var(--muted); font-size:.92rem; }
.project-head .out { display:flex; gap:.8rem; font-size:.88rem; }
@media (max-width:55.99rem) { .job-pane .steps-card > h2 { display:none; } }
.ask-form { display:flex; flex-direction:column; gap:.45rem; }
.ask-label { font:700 1.02rem var(--display); }
.ask-form textarea { font:inherit; font-size:16px; line-height:1.35; padding:.55rem .8rem; border-radius:var(--radius); border:1px solid var(--line);
  background:var(--bg); color:var(--ink); resize:vertical; min-height:3.2rem; }
.ask-row { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; }
.ask-row label { display:flex; align-items:center; gap:.3rem; font-size:.8rem; color:var(--muted); }
.ask-row select { max-width:9.5rem; min-height:34px; font-size:.82rem; }
.ask-row button { margin-left:auto; }
.proposals-here summary { cursor:pointer; }
.job-rows { list-style:none; padding:0; margin:0 0 1rem; display:flex; flex-direction:column; gap:.4rem; }
.job-row { background:var(--card); border-radius:var(--radius); box-shadow:0 2px 0 var(--edge); padding:.55rem .75rem .45rem; border-left:3px solid transparent; }
.job-row.on { border-left-color:var(--accent); background:color-mix(in srgb, var(--card) 88%, var(--accent)); }
.job-row .row-head { display:flex; justify-content:space-between; align-items:baseline; gap:.5rem; }
.job-row .muted { margin:.15rem 0 0; font-size:.82rem; }
/* All of a project's jobs, to search (jobhistory.py) */
.job-search { display:flex; flex-direction:column; gap:.45rem; }
.job-search .search-row { display:flex; gap:.4rem; }
.job-search input[type=search] { flex:1; font:inherit; font-size:16px; padding:.5rem .8rem; border-radius:var(--radius); border:1px solid var(--line);
  background:var(--bg); color:var(--ink); min-width:0; }
.job-search .clear { margin-left:auto; font-size:.85rem; }
.job-rows.found + .pager, .found .excerpt { font-size:.85rem; }
.found .excerpt { margin:.3rem 0 .1rem; color:var(--ink); overflow-wrap:anywhere; }
.pager { display:flex; gap:1rem; margin:0 0 1rem; } .pager .older { margin-left:auto; }
.job-head h2 { font:700 1.2rem/1.25 var(--display); margin:0; }
.job-head .sub { margin:.2rem 0 0; }
.job-head .answer { margin:.6rem 0 0; padding:.55rem .75rem; background:var(--bg); border-radius:var(--radius); white-space:pre-wrap; }
.job-head .add-note { margin:.6rem 0 0; } .job-head .add-note summary { cursor:pointer; color:var(--muted); font-size:.88rem; }
.job-controls { margin-top:.7rem; }
.route-list { list-style:none; padding:0; margin:0; }
.rl { padding:.45rem 0; border-top:1px solid var(--line); } .rl:first-child { border-top:0; }
.rl .what { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; }
.rl .what > a { display:inline-flex; align-items:center; gap:.35rem; color:var(--ink); text-decoration:none; }
.rl .what > a i { font-style:normal; font-weight:700; width:1.1rem; text-align:center; font-size:.8rem; }
.rl .what small { font-size:.72rem; font-weight:700; color:var(--run-fg); }
.rl .meta { color:var(--muted); font-size:.82rem; }
.rl .what form { margin-left:auto; } .rl button { min-height:30px; padding:.1rem .7rem .25rem; font-size:.8rem; }
.rl > p { margin:.2rem 0 0 1.55rem; color:var(--muted); font-size:.88rem; }
.rl > .chips, .rl > .links { margin:.3rem 0 0 1.55rem; }
.rl.done .what > a i { color:var(--fin-fg); } .rl.failed .what > a i { color:var(--bad-fg); }
.rl.now .what > a b, .rl.waiting .what > a b { color:var(--accent); }
.rl.todo .what > a b { font-weight:500; color:var(--muted); } .rl.skipped .what > a b { text-decoration:line-through; color:var(--muted); }
.job-details { margin:0 0 1rem; } .job-details > summary { cursor:pointer; color:var(--muted); font-size:.9rem; padding:.3rem 0; }
.job-pane .talk { margin-top:.2rem; }
/* The New project page (newproject.py) */
.new-form { display:flex; flex-direction:column; gap:.75rem; }
.new-form h2 { margin:0; font:700 1.15rem var(--display); } .new-form > .sub { margin:-.4rem 0 0; }
.new-form .field { display:flex; flex-direction:column; gap:.3rem; min-width:0; }
.new-form .field > span { font-size:.85rem; font-weight:600; }
.new-form input, .new-form textarea { font:inherit; font-size:16px; padding:.55rem .75rem; border-radius:var(--radius); border:1px solid var(--line);
  background:var(--bg); color:var(--ink); width:100%; box-sizing:border-box; }
.new-form textarea { resize:vertical; line-height:1.4; min-height:3rem; }
.slug-row { display:flex; flex-wrap:wrap; align-items:center; gap:.3rem .6rem; } .slug-row input { max-width:18rem; } .slug-row .muted { font-size:.82rem; }
.new-row { display:flex; flex-wrap:wrap; gap:.75rem; } .new-row .field { flex:1 1 10rem; } .new-row select { max-width:none; width:100%; }
.new-more > summary { cursor:pointer; color:var(--muted); font-size:.9rem; } .new-more .new-row { margin-top:.6rem; }
.check-row { display:flex; align-items:baseline; gap:.45rem; margin:.7rem 0; font-weight:600; } .check-row .muted { font-weight:400; font-size:.88rem; }
.new-form .error { color:var(--critical); font-weight:600; margin:0; font-size:.85rem; }
.card.adopt > summary { cursor:pointer; font-weight:700; } .card.adopt .new-form { margin-top:.75rem; }
/* Stage blocks (stages.py): what one is told, and Settings → Blocks */
pre.told { white-space:pre-wrap; word-break:break-word; font:.84rem/1.5 var(--mono); background:var(--bg);
  padding:.75rem .85rem; border-radius:var(--radius); margin:.5rem 0; max-height:70vh; overflow:auto; }
.block-part h3 { display:flex; flex-wrap:wrap; align-items:baseline; gap:.2rem .5rem; font-size:.95rem; margin:0 0 .5rem; }
.block-part h3 .muted { font-weight:400; font-size:.85rem; }
.block-part textarea { width:100%; box-sizing:border-box; font:.85rem/1.5 var(--mono); padding:.6rem .7rem;
  border-radius:var(--radius); border:1px solid var(--line); background:var(--bg); color:var(--ink); resize:vertical; }
.block-part form.stack { display:flex; flex-direction:column; gap:.5rem; }
/* The header's way to a new project; on a wide screen the sidebar has it */
.top .new-project { grid-column:3; justify-self:end; display:inline-flex; align-items:center; gap:.2rem; padding:.3rem .7rem; border-radius:var(--radius);
  background:var(--rust, var(--accent)); color:#fff; font-weight:700; font-size:.88rem; white-space:nowrap; text-decoration:none; }
.top .new-project b { font-size:1.05rem; line-height:1; }
@media (max-width:30rem) { .top .new-project span { display:none; } }
@media (min-width:64rem) { .top .new-project { display:none; } }
.side .item.new { margin-top:.4rem; color:var(--nav-ink); } .side .item.new .plus { width:20px; text-align:center; font-size:1.2rem; line-height:1; }
.side-projects .empty { color:var(--nav-muted); font-size:.85rem; padding:.3rem .7rem; }
.chip.done { background:var(--fin-bg); color:var(--fin-fg); border-color:transparent; } .chip.failed { background:var(--bad-bg); color:var(--bad-fg); border-color:transparent; }
.chip.running { background:var(--run-bg); color:var(--run-fg); border-color:transparent; }
.links { display:flex; flex-wrap:wrap; gap:.25rem .7rem; font-size:.85em; margin-top:.2rem; }
.took { display:inline; font-size:.8em; color:var(--muted); } time.took { display:block; }
.controls { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; margin-top:.75rem; padding-top:.75rem; border-top:1px dashed var(--line); }
form.inline { display:inline; margin:0; }
button, .button, select { font:inherit; font-size:.88rem; font-weight:600; min-height:38px; padding:.35rem .85rem .45rem; border:0; border-radius:var(--radius);
  background:var(--stone); color:var(--ink); cursor:pointer; box-shadow:inset 0 -3px 0 var(--lip); }
button:active:not(:disabled), .button:active { padding-top:.45rem; padding-bottom:.35rem; box-shadow:inset 0 -1px 0 var(--lip); }
select { font-weight:500; padding-right:2.1rem; max-width:13rem; -webkit-appearance:none; appearance:none; background-repeat:no-repeat;
  background-image:linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%);
  background-size:5px 5px; background-position:right 19px top calc(50% - 1px), right 14px top calc(50% - 1px); }
.button { display:inline-flex; align-items:center; background:var(--accent) var(--rust); color:var(--accent-ink); box-shadow:inset 0 -3px 0 var(--lip-strong); }
button.danger { color:var(--critical); }
button.quiet, summary.quiet { background:transparent; border:0; box-shadow:none; cursor:pointer; color:inherit; font-size:.9rem; padding:.35rem .5rem; }
.row button { min-height:30px; padding:.1rem .7rem .25rem; font-size:.8rem; }
.row button:active:not(:disabled) { padding-top:.25rem; padding-bottom:.1rem; }
.row .what form { margin-left:auto; }
button { white-space:nowrap; }
input:not([type=checkbox]):focus-visible, textarea:focus-visible { outline:0; border-color:var(--accent); }
.reply { display:flex; gap:.4rem; margin:.5rem 0 0; width:100%; align-items:flex-end; }
.reply textarea, .composer textarea { flex:1; font:inherit; font-size:16px; line-height:1.35; padding:.55rem .8rem; border-radius:var(--radius);
  border:2px solid var(--line); background:var(--card); color:var(--ink); resize:none; max-height:40vh; min-height:42px; }
textarea[rows="1"]::placeholder { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.signin-card form { margin-top:.6rem; } .signin-card input[type=password] { font:inherit; font-size:16px; padding:.45rem .7rem; border-radius:var(--radius);
  border:2px solid var(--line); background:var(--card); color:var(--ink); flex:1 1 10rem; min-width:0; }
.keep { display:flex; gap:.35rem; align-items:center; font-size:.82rem; color:var(--muted); flex-basis:100%; }
.notify-panel h2 { font:700 1.12rem/1.25 var(--display); } .notify-panel .state { margin:.4rem 0; font-size:.9rem; }
.talk { margin:1rem 0 .5rem; }
.messages { list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:.25rem; }
.messages li { display:flex; flex-direction:column; align-items:flex-start; max-width:min(34rem, 88%); }
.messages li[data-day]::before { content:attr(data-day); align-self:center; margin:.9rem 0 .4rem; font-size:.75rem; font-weight:600;
  color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
.messages li.riggy { align-self:flex-end; align-items:flex-end; }
.messages li[data-day].riggy::before { align-self:flex-end; }
.bubble { background:var(--card); padding:.5rem .8rem; overflow-wrap:anywhere; clip-path:polygon(0 0, 100% 0, 100% 100%, 10px 100%, 0 calc(100% - 10px)); }
.bubble p { margin:0; } .bubble img { display:block; max-width:100%; max-height:22rem; border-radius:1px; margin:.1rem 0 .4rem; }
.riggy .bubble { background:var(--accent) var(--rust); color:var(--accent-ink); clip-path:polygon(0 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%); }
.riggy .bubble a { color:var(--accent-ink); text-decoration:underline; }
.notify .bubble { border-left:4px solid var(--warning); }
.news .bubble { background:transparent; clip-path:none; border:1px dashed var(--edge); color:var(--muted); font-size:.9em; }
.messages time { font-size:.7rem; color:var(--muted); margin:.15rem .4rem .35rem; }
.settings .row-form + .row-form { margin-top:.75rem; } .settings input { font:inherit; font-size:16px; padding:.4rem .6rem; border-radius:var(--radius);
  border:2px solid var(--line); background:var(--card); color:var(--ink); min-width:0; flex:1 1 10rem; }
.settings .hint { flex-basis:100%; margin:0; }
/* Tags in settings: every tag's row in one grid, so each column is as wide as its widest and they all line up. */
.tag-list { display:grid; grid-template-columns:max-content max-content max-content minmax(0, 1fr) max-content; gap:.6rem .75rem;
  align-items:center; margin:.75rem 0 1rem; }
.tag-list.readonly { grid-template-columns:max-content 1fr; }
.tag-list > .tag { display:contents; }
.tag-list b { display:flex; align-items:center; overflow-wrap:anywhere; }
.tag-list .uses { color:var(--muted); font-size:.88rem; }
.tag-list form { margin:0; }
.tag-list .rename { display:flex; gap:.4rem; min-width:0; }
@media (max-width:640px) {  /* each tag on two lines: name, count and color, then renaming and deleting it */
  .tag-list:not(.readonly) { grid-template-columns:minmax(0, 1fr) max-content max-content; gap:.45rem .5rem; }
  .tag-list:not(.readonly) .rename { grid-column:span 2; }
  .tag-list:not(.readonly) :is(select, .danger) { width:100%; }
  .tag-list:not(.readonly) > .tag + .tag > :nth-child(-n+3) { margin-top:.75rem; }
}
.adopt > summary { cursor:pointer; color:var(--muted); font-size:.9rem; font-weight:600; }
.lenses { list-style:none; padding:0; margin:.4rem 0 0; } .lenses li { padding:.45rem 0; border-top:1px solid var(--line); }
.lenses li:first-child { border-top:0; } .lenses label { display:flex; gap:.45rem; align-items:center; }
.lenses input[type=checkbox] { width:20px; height:20px; flex:none; margin:0; padding:0; accent-color:var(--accent); } .lenses .sub { display:block; margin:.15rem 0 0 1.75rem; }
.jobs ol { padding-left:1.1rem; margin:.5rem 0 0; } .jobs li { margin:.2rem 0; font-size:.9em; }
.links-list { list-style:none; padding:0; margin:0; } .links-list li { padding:.45rem 0; border-top:1px solid var(--line); }
.links-list li:first-child { border-top:0; } .links-list .sub { display:block; }
.dock .composer { padding:.9rem max(.75rem, env(safe-area-inset-left)) max(.6rem, env(safe-area-inset-bottom));
  background:linear-gradient(transparent, var(--bg) 30%); }
.dock .tabs { margin-top:0; } .dock .composer + .tabs { padding-top:.3rem; } .dock:has(.tabs) .composer { padding-bottom:.5rem; }
.composer { max-width:46rem; margin:0 auto; display:flex; gap:.4rem; align-items:flex-end; }
.composer textarea { box-shadow:0 2px 0 var(--edge); }
.composer .send { display:flex; gap:.3rem; flex-wrap:wrap; justify-content:flex-end; }
button.primary { background:var(--accent) var(--rust); color:var(--accent-ink); min-height:42px; box-shadow:inset 0 -3px 0 var(--lip-strong); }
button.quiet-button { min-height:42px; }
button:disabled { opacity:.6; cursor:progress; }
.empty { color:var(--muted); text-align:center; padding:1.5rem 0; }
.earlier { display:block; width:max-content; margin:0 auto .6rem; padding:.35rem .9rem .45rem; border-radius:var(--radius); background:var(--stone);
  box-shadow:inset 0 -3px 0 var(--lip); font-size:.82rem; font-weight:600; color:var(--muted); text-decoration:none; }
table { border-collapse:collapse; width:100%; font-size:.88em; } th, td { text-align:left; padding:.35rem .4rem; border-bottom:1px solid var(--line); }
td select { min-height:32px; font-size:.82rem; }
.runs { overflow-x:auto; background:var(--card); border-radius:var(--radius); padding:.5rem; }
dialog#sheet { border:0; padding:0; width:min(40rem, 100%); max-height:85vh; border-radius:var(--radius); background:var(--card); color:var(--ink);
  box-shadow:0 4px 0 var(--edge); }
dialog#sheet::backdrop { background:rgb(13 16 18 / .55); }
dialog#sheet .close { position:sticky; float:right; top:.4rem; right:.4rem; z-index:1; font-size:1.1rem; }
.sheet-body { padding:1rem 1.1rem 1.4rem; }
.sheet-head h2 { font:700 1.4rem/1.2 var(--display); margin:.15rem 0; }
.sheet-body h3 { font-size:.75rem; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); margin:1rem 0 .3rem; }
.sheet-body .tail { margin-top:.6rem; }
.facts { display:grid; grid-template-columns:max-content 1fr; gap:.25rem .9rem; margin:.75rem 0; font-size:.92em; }
.facts dt { color:var(--muted); } .facts dd { margin:0; }
.summary { background:var(--bg); border-radius:var(--radius); padding:.6rem .75rem; }
.problem { white-space:pre-wrap; font-size:12px; background:var(--bg); padding:.5rem; border-radius:var(--radius); max-height:14rem; overflow:auto; }
.row-form { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; width:100%; margin:.4rem 0 0; }
.row-form > span, .row-form label { color:var(--muted); font-size:.88rem; }
.options { list-style:none; display:flex; gap:.6rem; overflow-x:auto; padding:.15rem .1rem .5rem; margin:.5rem 0; scroll-snap-type:x mandatory;
  overscroll-behavior-x:contain; scrollbar-width:thin; }
.option { flex:0 0 min(15rem, 78%); scroll-snap-align:start; display:flex; flex-direction:column; background:var(--card); border:1px solid var(--line);
  border-radius:var(--radius); box-shadow:0 2px 0 var(--edge); overflow:hidden; }
.option .shots { display:grid; grid-template-columns:repeat(2, 1fr); gap:2px; background:var(--line); }
.option .shots a:first-child, .option .shots a:nth-child(2):last-child { grid-column:1 / -1; }
.option .shots img { display:block; width:100%; aspect-ratio:2 / 1; object-fit:cover; object-position:top; background:var(--bg); }
.option .shots a:first-child img { aspect-ratio:4 / 5; }
.option-body { display:flex; flex-direction:column; gap:.3rem; flex:1; padding:.6rem .7rem .7rem; }
.option h4 { margin:0; font:700 1.05rem/1.3 var(--display); display:flex; gap:.45rem; align-items:center; }
.option .letter { display:inline-grid; place-items:center; flex:0 0 1.6rem; height:1.6rem; border-radius:1px; background:var(--pc, var(--accent));
  color:var(--mark-ink); font:700 .9rem var(--body); }
.option p { margin:0; font-size:.88rem; } .option .files { color:var(--muted); font-size:.75rem; overflow-wrap:anywhere; }
.option form { margin-top:auto; padding-top:.3rem; }
.option form button { width:100%; background:var(--accent) var(--rust); color:var(--accent-ink); box-shadow:inset 0 -3px 0 var(--lip-strong); }
@media (max-width:640px) {
  dialog#sheet { width:100%; max-width:100%; margin:auto 0 0; border-radius:var(--radius) var(--radius) 0 0; max-height:88vh; box-shadow:none; }
  .card-head { flex-wrap:wrap; gap:.3rem .75rem; }
}
.windows { display:flex; gap:.3rem; margin-bottom:.75rem; }
.windows a { padding:.3rem .75rem .45rem; border-radius:var(--radius); background:var(--stone); color:var(--ink); font-size:.88rem; font-weight:600;
  box-shadow:inset 0 -3px 0 var(--lip); }
.windows a.on { background:var(--ink); color:var(--card); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(8rem, 1fr)); gap:.6rem; margin-bottom:.75rem; }
.tile { background:var(--card); border-radius:var(--radius); padding:.7rem .85rem; box-shadow:0 2px 0 var(--edge); }
.tile span { display:block; color:var(--muted); font-size:.8rem; } .tile b { font:700 1.5rem/1.2 var(--display); }
.findings { background:var(--card); border-radius:var(--radius); padding:.75rem 1rem .75rem 2rem; margin:0 0 1rem; box-shadow:0 2px 0 var(--edge); }
.findings li { margin:.2rem 0; }
.reports-page .windows { flex-wrap:wrap; }
.reports { list-style:none; padding:0; margin:0; }
.report-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:.2rem .5rem; }
.report-summary { margin:.35rem 0 0; font-weight:600; }
.report details { margin-top:.35rem; } .report summary { cursor:pointer; color:var(--muted); font-size:.85rem; }
.report pre.log { white-space:pre-wrap; overflow-wrap:anywhere; }
.fix-body { white-space:pre-wrap; }
.fix textarea { width:100%; margin-top:.6rem; font:12px/1.5 var(--mono); }
.bars { background:var(--card); border-radius:var(--radius); padding:.8rem 1rem; margin-bottom:1rem; box-shadow:0 2px 0 var(--edge); }
.bars h2, .defaults h2 { font-size:.75rem; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); margin-bottom:.4rem; }
.bars h3 { font:700 1rem/1.3 var(--display); margin-bottom:.3rem; }
.bars ol { list-style:none; padding:0; margin:.5rem 0 0; }
.bars li { display:grid; grid-template-columns:minmax(5rem, 9rem) 1fr auto; gap:.15rem .6rem; align-items:center; padding:.2rem 0; }
.bars .name { font-size:.9rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bars .bar { height:12px; display:block; }
.bars .bar i { display:block; height:100%; border-radius:0 1px 1px 0; background:var(--c1); }
.bars.usage .bar i { background:var(--c2); }
.bars .val { font-size:.85rem; font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }
.bars .val small { color:var(--muted); margin-left:.25rem; }
.bars .extra { grid-column:2 / 4; color:var(--muted); font-size:.78rem; margin-top:-.15rem; }
.bars li:hover .bar i { filter:brightness(1.1); }
.two { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit, minmax(16rem, 1fr)); }
.two > div > h3 { font:700 1rem/1.3 var(--display); margin:.25rem 0; }
.hot { padding-left:1.2rem; margin:.25rem 0 1rem; } .hot li { margin:.2rem 0; } .hot span { color:var(--muted); font-variant-numeric:tabular-nums; }
.defaults { margin-top:1rem; } .defaults > form { display:block; margin-top:.6rem; }
.kinds { list-style:none; padding:.25rem 1rem; margin:.5rem 0 0; background:var(--card); border-radius:var(--radius); box-shadow:0 2px 0 var(--edge); }
.kinds li { display:flex; justify-content:space-between; align-items:center; gap:.4rem 1rem; flex-wrap:wrap; padding:.55rem 0; border-top:1px solid var(--line); }
.kinds li:first-child { border-top:0; } .kinds .muted { font-size:.8rem; margin-left:.3rem; }
.picks { display:flex; gap:.4rem; flex-wrap:wrap; font-size:.8rem; color:var(--muted); }
.picks label, .row-form label { display:inline-flex; gap:.3rem; align-items:center; }
.picks select { min-height:32px; font-size:.82rem; }
.picks > span { color:var(--ink); font-size:.88rem; } .picks > span + span::before { content:"· "; color:var(--muted); }
@media (max-width:640px) { .bars li { grid-template-columns:minmax(3.5rem, 6rem) 1fr auto; } }
/* Lists of settings (Blocks, Modes), and the way an old job went */
.workflows { list-style:none; padding:0; margin:0; } .workflows li { padding:.6rem 0; border-top:1px solid var(--line); }
.workflows li:first-child { border-top:0; } .workflows p { margin:.2rem 0 0; }
.trail { list-style:none; padding:0; margin:.6rem 0 0; display:flex; flex-wrap:wrap; gap:.35rem; }
.stack { display:flex; flex-direction:column; gap:.5rem; margin:.6rem 0; } .stack label { display:flex; flex-direction:column; gap:.2rem; font-weight:600; font-size:.88rem; }
.stack input, .stack textarea { font:inherit; font-weight:400; }
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
  // A page in parts (pages.region) refreshes each on its own, in one request: a part being typed
  // in is left alone, and one that hasn't changed isn't touched. Any other page, all of main.
  async function refreshParts(parts) {
    const names = parts.filter(p => !typing(p)).map(p => p.dataset.part);
    if (!names.length) return;
    const q = new URLSearchParams(location.search); q.set('parts', names.join(','));
    try {
      const r = await fetch(location.pathname + '?' + q, {cache: 'no-store'});
      if (!r.ok) return;
      const got = await r.json();
      for (const p of parts) {
        const html = got[p.dataset.part];
        if (html === undefined || html === p._was || typing(p)) continue;
        keepOpen(p, () => { p.innerHTML = html; }); p._was = html;
        clocks(p); dates(p);
      }
    } catch (e) {}
  }
  let refresh = async function () {
    if (document.hidden) return;
    const parts = [...main.querySelectorAll('[data-part]')];
    if (parts.length) await refreshParts(parts);
    else if (!typing(main)) {
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
      history.replaceState(null, '', location.pathname + location.search + '#' + href);
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
  sheet.addEventListener('close', () => { delete sheet.dataset.href; history.replaceState(null, '', location.pathname + location.search); });
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
  refresh = async () => { const stay = atBottom(); await oldRefresh(); dates(main); if (stay && latest) toBottom(); placeLatest(); };
  clocks(document.body); dates(main);
  if (location.hash === '#end') { if (latest) toBottom(); history.replaceState(null, '', location.pathname + location.search); }
  placeLatest();
  main.querySelectorAll('.log').forEach(l => l.scrollTop = l.scrollHeight);
  setInterval(() => refresh(), 10000);
  const flash = document.querySelector('.flash');
  if (flash) { setTimeout(() => flash.style.opacity = 0, 5000); setTimeout(() => flash.remove(), 5500); }
  if (location.search.includes('msg=')) {
    const q = new URLSearchParams(location.search); q.delete('msg');
    history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
  }
  if (location.hash.startsWith('#/')) {
    const href = location.hash.slice(1);
    const node = main.querySelector(`a[href="${CSS.escape(href)}"]`);
    if (node) node.closest('.card')?.scrollIntoView();
    openSheet(href);
  }
})();
"""
