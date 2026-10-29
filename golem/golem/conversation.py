"""The web app's conversations: each project's page and golem's own (the inbox), phone first.

A project's page has its job's card (where it is, what's running, its pieces of work), then,
signed in, the conversation with golem about it, what golem is waiting on Riggy for, and
a box to write in that sticks to the bottom of the screen. What's written there goes to the
bot (web.py): it answers a question or a check-in, picks an option, becomes a note for the
running job, or starts a change, or options first. Its settings page has what the !commands
do: mode, model, effort, pausing, renaming, moving and deleting; the job's place in the
queue and stopping it; and its jobs.

Golem's own conversation is where new ideas are written, with its replies and its
golem-wide news.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape

from .pages import LaneData, _hidden, _pause, ask_card, job_controls, select_form, status_card
from .store import ACTIVE, EFFORTS, MODELS, MODES, Job, Project, Said
from .talk import INBOX, STATUS

URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")
# What the box at the bottom does, by what the project's job is doing.
COMPOSE = {
    "idle": ("Ask for a change, or to see some options first…", "Send"),
    "waiting": ("Your answer…", "Reply"),
    "busy": ("A note for the job's next step…", "Send"),
}


def project_page(lane: LaneData, said: list[Said], signed_in: bool, now: datetime, earlier: str = "") -> str:
    """The page's main part, refreshed in place every few seconds; the box to write in is separate (composer)."""
    top = status_card(lane, signed_in, now)
    if not signed_in:
        return top + "<p class=hint>Sign in (Settings) to see the conversation and write to golem.</p>"
    return top + thread(said, "Nothing said here yet.", earlier) + ask_card(lane, signed_in, back=f"/p/{lane.project.slug}#end", reply=False)


def settings_page(project: Project, lane: LaneData, jobs: list[Job], tags: list[str]) -> str:
    return settings_html(project, tags) + job_controls(lane) + jobs_html(jobs) + _danger(project)


def inbox_page(said: list[Said], signed_in: bool, earlier: str = "") -> str:
    head = (
        "<p class=sub>New ideas go here. golem names each one, gives it a page and its own conversation, "
        "and starts on it a minute later. Its news, like usage limits and finished jobs, shows here too.</p>"
    )
    if not signed_in:
        return head + "<p class=hint>Sign in (Settings) to see the inbox and write to golem.</p>"
    return head + thread(said, "Nothing yet. Describe an idea below.", earlier)


def thread(said: list[Said], empty: str, earlier: str = "") -> str:
    """Messages oldest first, Riggy's on the right; the page's script dates them on the viewer's clock.
    Only the latest are shown; `earlier`, when there are more, is the address that shows more of them."""
    if not said:
        return f"<section class=talk><p class=empty>{escape(empty)}</p></section>"
    items = []
    for m in said:
        classes = f"m {m.who}{' notify' if m.notify else ''}{' news' if m.channel == STATUS else ''}"
        image = f"<a href='{escape(m.image)}' target=_blank rel=noopener><img src='{escape(m.image)}' alt='' loading=lazy></a>" if m.image else ""
        text = f"<p>{_linked(m.text)}</p>" if m.text else ""
        items.append(
            f"<li class='{classes}' id=m{m.id}><div class=bubble>{image}{text}</div>"
            f"<time data-ts='{escape(m.at)}'>{escape(m.at[11:16])}</time></li>"
        )
    more = f"<a class=earlier href='{escape(earlier)}' data-earlier>Show earlier messages</a>" if earlier else ""
    return f"<section class=talk>{more}<ol class=messages>{''.join(items)}</ol></section>"


def composer(channel: str, state: str) -> str:
    """The box at the bottom. With nothing under way, it can also ask for options first rather
    than leaving that to golem to work out from the words."""
    if channel == INBOX:
        placeholder, label, extra = "Describe a new idea…", "Start it", ""
    else:
        placeholder, label = COMPOSE[state]
        extra = "<button name=command value=options class=quiet-button>Options first</button>" if state == "idle" else ""
    return (
        "<form method=post action=/say class=composer data-send>"
        + _hidden(channel=channel)
        + f"<textarea name=text rows=1 placeholder='{escape(placeholder)}' required aria-label='{escape(placeholder)}'></textarea>"
        + f"<div class=send>{extra}<button class=primary>{escape(label)}</button></div></form>"
    )


def settings_html(project: Project, tags: list[str]) -> str:
    """What the !commands do, as controls, and its tag. Each change is said in the conversation."""
    slug, back = project.slug, f"/p/{project.slug}/settings"
    mode = (
        f"<form method=post action=/say class=inline>{_hidden(channel=slug, command='mode', back=back)}"
        f"<select name=text aria-label=mode onchange='this.form.submit()'>"
        + "".join(f"<option{' selected' if project.mode == m else ''}>{m}</option>" for m in MODES)
        + "</select></form>"
    )
    engine = "".join(
        f"<span>{name.title()}</span>"
        + select_form(name, getattr(project, name), values, "each step's default", action="setting", slug=slug, back=back)
        for name, values in (("model", MODELS), ("effort", EFFORTS))
    )
    return (
        "<section class='card settings'><h2>How golem works on it</h2>"
        "<p class=sub>From the next job (mode) or the next step (model, effort).</p>"
        f"<div class=row-form><span>Mode</span>{mode}</div><div class=row-form>{engine}</div>"
        f"<div class=row-form>{_pause(project, back)}</div></section>"
        "<section class='card settings'><h2>Tag</h2><p class=sub>What sort of project it is, on the projects page. "
        "<a href=/settings#tags>The tags</a></p><div class=row-form>"
        + select_form("tag", project.tag, tuple(tags), "none", action="setting", slug=slug, back=back)
        + "</div></section>"
    )


def _danger(project: Project) -> str:
    slug = project.slug
    return (
        "<section class='card settings'><h2>Name and address</h2>"
        + _command(slug, "rename", "New name", project.name, "Rename")
        + _command(slug, "slug", "new-address", project.slug, "Move", note="A new address and repo; golem moves the code.")
        + "</section><section class='card settings'><h2>Delete</h2>"
        + _command(slug, "delete", f"Type {slug} to confirm", "", "Delete for good", danger=True,
                   note="The site and its data, the checkout, every job and its logs, and the conversation. "
                   "The repo stays on GitHub.")  # fmt: skip
        + "</section>"
    )


def jobs_html(jobs: list[Job]) -> str:
    if not jobs:
        return ""
    rows = "".join(f"<li><a href='/jobs/{j.id}'>#{j.id}</a> {escape(_job_words(j))}</li>" for j in jobs[:20])
    return f"<section class='card jobs'><h2>Jobs ({len(jobs)})</h2><ol>{rows}</ol></section>"


def _command(slug: str, command: str, placeholder: str, value: str, label: str, danger: bool = False, note: str = "") -> str:
    return (
        f"<form method=post action=/say class=row-form>{_hidden(channel=slug, command=command, back=f'/p/{slug}/settings')}"
        f"<input name=text placeholder='{escape(placeholder)}' value='{escape(value)}' required aria-label='{escape(label)}'>"
        f"<button{' class=danger' if danger else ''}>{escape(label)}</button>"
        + (f"<p class=hint>{escape(note)}</p>" if note else "")
        + "</form>"
    )


def _job_words(job: Job) -> str:
    what = "new project" if job.kind == "new" else job.playbook if job.playbook in ("options", "question") else "change"
    state = {"done": "done", "killed": "stopped", "failed": "failed"}.get(job.state, job.state)
    request = job.request if len(job.request) <= 90 else job.request[:89] + "…"
    return f"{what}, {state}: “{request}”"


def _linked(text: str) -> str:
    """Text as HTML with its line breaks, and its web addresses as links."""
    out, last = [], 0
    for match in URL_RE.finditer(text):
        out.append(escape(text[last : match.start()]))
        url = match.group(0)
        out.append(f"<a href='{escape(url)}' target=_blank rel=noopener>{escape(url)}</a>")
        last = match.end()
    out.append(escape(text[last:]))
    return "".join(out).replace("\n", "<br>")


def state_of(job: Job | None) -> str:
    """For the box at the bottom: idle, waiting (on Riggy) or busy."""
    if job is None or job.state not in ACTIVE:
        return "idle"
    return "waiting" if job.state == "waiting" else "busy"
