"""Conversations, and a project's settings, phone first.

A conversation is messages oldest first, Riggy's on the right (thread): golem's own (the
inbox, where new ideas are written, with its golem-wide news), a project's history (what was
said before jobs had their own activity), and each job's activity on its pane
(projectpage.py). golem's proposals for a project are here too, each to thumbs up or down
(proposals.py). A project's settings page has what the !commands do: mode, model, effort,
pausing, renaming, moving and deleting; how often golem proposes things, and which kinds; the
jobs' places in the queue and stopping them; and its jobs.
"""

from __future__ import annotations

import re
from html import escape

from . import plan
from .pages import LaneData, _hidden, _pause, job_controls, select_form
from .proposals import LENSES, WAITING, kinds
from .store import CHANGES_AT_ONCE, EFFORTS, EVERY, MODELS, Job, Project, Proposal, Said
from .talk import STATUS

URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")


def settings_page(project: Project, lane: LaneData, jobs: list[Job], tags: list[str], decided: list[Proposal] = (),
                  more: list[LaneData] = (), modes: list[str] = ()) -> str:  # fmt: skip
    controls = "".join(job_controls(x) for x in (lane, *more))
    return settings_html(project, tags, modes) + proposals_settings(project, decided) + controls + jobs_html(jobs) + _danger(project)


def proposals_card(waiting: list[Proposal], back: str) -> str:
    """golem's proposals awaiting Riggy, oldest first, each with a thumbs up (build it) or down and room for why."""
    if not waiting:
        return ""
    items = "".join(
        f"<li class=proposal><h4>{escape(p.title)}</h4><p class=muted>{escape(p.lens)}</p><p>{_linked(p.body)}</p>"
        f"<form method=post action=/act>{_hidden(action='proposal', proposal=p.id, back=back)}"
        f"<input name=reason placeholder='Why? (optional)' aria-label='Why, for {escape(p.title)}' maxlength=1000>"
        "<div class=row-form><button name=value value=accept class=primary>👍 Build it</button>"
        "<button name=value value=reject>👎 Not this</button></div></form></li>"
        for p in reversed(waiting)
    )
    return (
        "<section class='card proposals'><h2>golem proposes</h2><p class=sub>A thumbs up builds it. "
        f"Saying why, either way, steers what golem proposes next.</p><ol>{items}</ol></section>"
    )


def proposals_settings(project: Project, decided: list[Proposal]) -> str:
    """How often golem looks for improvements to propose, and what Riggy made of the latest."""
    slug, back = project.slug, f"/p/{project.slug}/settings"
    every = select_form("proposals", "" if project.proposals == "off" else project.proposals, EVERY[1:], "off",
                        action="setting", slug=slug, back=back)  # fmt: skip
    most = (
        f"<form method=post action=/act class=inline>{_hidden(action='setting', name='proposals_max', slug=slug, back=back)}"
        "<select name=value aria-label='proposals waiting' onchange='this.form.submit()'>"
        + "".join(f"<option{' selected' if n == project.proposals_max else ''}>{n}</option>" for n in WAITING)
        + "</select></form>"
    )
    on = kinds(project)
    boxes = "".join(
        f"<li><form method=post action=/act>{_hidden(action='proposal-kind', name=x.name, slug=slug, back=back)}"
        f"<label><input type=checkbox name=value value=on{' checked' if x in on else ''} onchange='this.form.submit()'>"
        f"<b>{escape(x.name.capitalize())}</b></label><span class=sub>{escape(x.focus)}</span></form></li>"
        for x in LENSES
    )
    rows = "".join(
        f"<li>{'👍' if p.state == 'accepted' else '👎'} {escape(p.title)}"
        + (f" <a href='/jobs/{p.built_by}'>#{p.built_by}</a>" if p.built_by else "")
        + (f"<span class=sub>“{escape(p.reason)}”</span>" if p.reason else "")
        + "</li>"
        for p in decided
    )
    return (
        "<section class='card settings'><h2>Proposals</h2><p class=sub>When there's nothing else to do, golem looks "
        "it over for one kind of improvement at a time (features, security, polish…) and proposes the best it finds "
        "for you to thumbs up or down, each also an issue on its repo as a record. One each time, so they come as a "
        "steady trickle: while as many wait on you as you allow, golem skips its turn, and the next comes the turn "
        f"after you decide on one.</p><div class=row-form><span>How often</span>{every}"
        f"<span>At most waiting on you</span>{most}</div>"
        "<p class=sub>What it looks for, one kind each time, in this order. Each kind's line is what golem "
        f"asks the worker to look at; untick the ones this project doesn't need.</p><ul class=lenses>{boxes}</ul>"
        + (f"<p class=sub>What you said lately:</p><ol class=links-list>{rows}</ol>" if rows else "")
        + "</section>"
    )


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


def settings_html(project: Project, tags: list[str], modes: list[str] = ()) -> str:
    """How golem works on it, and its tag. Its default mode is the mode its requests run in unless
    they ask for another: only Riggy sets it."""
    slug, back = project.slug, f"/p/{project.slug}/settings"
    mode = select_form("mode", project.mode, tuple(modes), "golem's default", action="setting", slug=slug, back=back)
    engine = "".join(
        f"<span>{name.title()}</span>"
        + select_form(name, getattr(project, name), values, "each step's default", action="setting", slug=slug, back=back)
        for name, values in (("model", MODELS), ("effort", EFFORTS))
    )
    return (
        "<section class='card settings'><h2>How golem works on it</h2>"
        "<p class=sub>From the next request (its default mode, which a request can ask to change) or the next step (model, effort).</p>"
        f"<div class=row-form><span>Default mode</span>{mode}</div><div class=row-form>{engine}</div>"
        f"<div class=row-form>{_pause(project, back)}</div></section>"
        + changes_card(project, back)
        + "<section class='card settings'><h2>Tag</h2><p class=sub>What sort of project it is, on the projects page. "
        "<a href=/settings#tags>The tags</a></p><div class=row-form>"
        + select_form("tag", project.tag, tuple(tags), "none", action="setting", slug=slug, back=back)
        + "</div></section>"
    )


def changes_card(project: Project, back: str) -> str:
    """How many changes golem works on here at once (turns.py)."""
    options = "".join(f"<option{' selected' if n == project.parallel_jobs else ''}>{n}</option>" for n in CHANGES_AT_ONCE)
    return (
        "<section class='card settings' id=changes><h2>Changes at once</h2><p class=sub>How many changes golem works "
        "on here at the same time. Each gets its own branch and checkout; they overlap at planning, review and docs, "
        "take turns at building, and catch up with what shipped before they do. They ship together: one that's done waits "
        "for the others still being worked on, so one PR, CI run and deploy ships them all, up to this many at a time. "
        "Parallel workers, in Settings, caps the total across projects.</p>"
        f"<form method=post action=/act class=row-form>{_hidden(action='setting', slug=project.slug, name='parallel_jobs', back=back)}"
        f"<label for=changes-at-once>At once</label><select id=changes-at-once name=value onchange='this.form.submit()'>{options}</select>"
        "</form></section>"
    )


def _danger(project: Project) -> str:
    slug = project.slug
    return (
        "<section class='card settings'><h2>Name and address</h2>"
        + _control(slug, "rename", "New name", project.name, "Rename")
        + _control(slug, "project-slug", "new-address", project.slug, "Move", note="A new address and repo; golem moves the code.")
        + "</section><section class='card settings'><h2>Delete</h2>"
        + _control(slug, "project-delete", f"Type {slug} to confirm", "", "Delete for good", danger=True,
                   note="The site and its data, the checkout, every job and its logs, and the conversation. "
                   "The repo stays on GitHub.")  # fmt: skip
        + "</section>"
    )


def jobs_html(jobs: list[Job]) -> str:
    if not jobs:
        return ""
    rows = "".join(f"<li><a href='/p/{escape(j.slug)}/jobs/{j.id}'>#{j.id}</a> {escape(_job_words(j))}</li>" for j in jobs[:20])
    return f"<section class='card jobs'><h2>Jobs ({len(jobs)})</h2><ol>{rows}</ol></section>"


def _control(slug: str, action: str, placeholder: str, value: str, label: str, danger: bool = False, note: str = "") -> str:
    return (
        f"<form method=post action=/act class=row-form>{_hidden(action=action, slug=slug, back=f'/p/{slug}/settings')}"
        f"<input name=value placeholder='{escape(placeholder)}' value='{escape(value)}' required aria-label='{escape(label)}'>"
        f"<button{' class=danger' if danger else ''}>{escape(label)}</button>"
        + (f"<p class=hint>{escape(note)}</p>" if note else "")
        + "</form>"
    )


def _job_words(job: Job) -> str:
    what = plan.what(job)
    state = {"done": "done", "killed": "stopped", "failed": "failed"}.get(job.state, job.state)
    return f"{what}, {state}: {plan.title(job)}"


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
