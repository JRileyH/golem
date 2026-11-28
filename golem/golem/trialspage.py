"""The Trials page (trials.py): every trial, the latest first, and one trial's arms side by side.

Each arm shows what it ran in (its mode, and its model for most steps), where it is, its PR and
CI, what its work came to (its last word), and its numbers: how long it took to be ready (from
its first step to its pick), how long its agents ran, what it cost at API prices, its tokens,
runs and fixes. Of each pair, the lower is marked. Signed in, Riggy picks one, or neither, with a
note: the trial's verdict, kept with it. The arms' every step, run and message download as
compare.py's CSV.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from . import plan, trials
from .pages import _count, _hidden, _money
from .store import ACTIVE, Job, Project, Trial
from .trials import Side
from .watch import span


def index(rows: list[tuple[Trial, Project | None, list[Job], str]], signed_in: bool) -> str:
    """Every trial, the latest first: its title and project, its arms' modes, and where it stands."""
    intro = (
        "<p class=hint>A trial runs one request two ways, each arm in a mode of its own, on a branch of its own, "
        "for you to pick the one to ship. Start one from a project's request box: <b>Compare with</b>.</p>"
    )
    if not rows:
        return f"<section class=card><h2>Trials</h2>{intro}<p class=empty>No trials yet.</p></section>"
    items = []
    for trial, project, arms, status in rows:
        vs = " vs ".join(f"{trials.arm_name(j)}: {escape(trials.mode_name(j))}" for j in arms)
        where = escape(project.name) if project else escape(trial.slug)
        items.append(
            f"<li><a href='/trials/{trial.id}'><b>Trial {trial.id}: {escape(trials.title(trial))}</b></a> "
            f"<span class='badge{' waiting' if status == 'ready' else ''}'>{escape(status)}</span>"
            f"<span class=sub>{where} · {vs} · {escape(trial.created_at[:10])}</span></li>"
        )
    return f"<section class=card><h2>Trials</h2>{intro}<ul class=links-list>{''.join(items)}</ul></section>"


def page(trial: Trial, project: Project | None, sides: list[Side], status: str, signed_in: bool, now: datetime) -> str:
    """One trial: what was asked, its arms side by side, and the pick (or the form to make it)."""
    where = f"<a href='/p/{escape(trial.slug)}'>{escape(project.name if project else trial.slug)}</a>"
    csv = f" · <a href='/trials/{trial.id}/csv' download>CSV of every step and run</a>" if signed_in else ""
    head = (
        f"<section class=card><h2>Trial {trial.id}: {escape(trials.title(trial))}</h2>"
        f"<p class=sub>{where} · started {escape(trial.created_at[:16].replace('T', ' '))} · {escape(status)}{csv}</p>"
        f"<p class=request>{escape(trial.request)}</p></section>"
    )
    arms = "<div class=arms>" + "".join(_arm(s, trial, sides, now) for s in sides) + "</div>"
    return head + arms + _verdict(trial, sides, signed_in)


def _arm(s: Side, trial: Trial, sides: list[Side], now: datetime) -> str:
    job = s.job
    other = next((x for x in sides if x.job.id != job.id), None)
    picked = trial.pick == job.arm
    harness = "OpenCode" if s.harness == "opencode" else "Claude Code"
    facts = [
        ("Where", escape(_where(s, trial))),
        ("Ready in", _better(s.ready_seconds, other.ready_seconds if other else None, _took)),
        ("Agents ran", _better(s.cost.agent_seconds or None, other.cost.agent_seconds or None if other else None, _took)),
        ("Cost", _better(s.cost.cost if s.cost.runs else None, other.cost.cost if other and other.cost.runs else None, _money)
         + " <span class=muted>at API prices</span>"),
        ("Tokens", _better(s.cost.tokens_in + s.cost.tokens_out or None, (other.cost.tokens_in + other.cost.tokens_out or None) if other else None, _count)
         + f" <span class=muted>{_count(s.cost.tokens_in)} in / {_count(s.cost.tokens_out)} out</span>"),
        ("Runs", f"{s.cost.runs}" + (f", {s.cost.fixes} fix{'es' if s.cost.fixes != 1 else ''}" if s.cost.fixes else "")),
        ("PR", f"<a href='{escape(s.pr)}' target=_blank rel=noopener>#{escape(s.pr.rsplit('/', 1)[-1])} ↗</a>" if s.pr else "<span class=muted>not yet</span>"),
        ("CI", (f"<a href='{escape(s.ci_link)}' target=_blank rel=noopener>{escape(s.ci)} ↗</a>" if s.ci_link else escape(s.ci)) if s.ci else "<span class=muted>not yet</span>"),
    ]  # fmt: skip
    said = plan.last_said(s.steps)
    return (
        f"<section class='card arm{' picked' if picked else ''}'>"
        f"<div class=card-head><h2>{trials.arm_name(job)} · {escape(s.mode)}</h2>{'<span class=badge>picked</span>' if picked else ''}</div>"
        f"<p class=sub>{harness}{' · ' + escape(s.model) if s.model else ''} · <a href='/p/{escape(job.slug)}/jobs/{job.id}'>job #{job.id}</a></p>"
        "<dl class=facts>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>"
        + (f"<p class=summary>{escape(said)}</p>" if said else "")
        + "</section>"
    )


def _where(s: Side, trial: Trial) -> str:
    job = s.job
    if job.state not in ACTIVE:
        return {"done": "Done", "killed": "Stopped", "failed": "Failed"}.get(job.state, job.state)
    if s.ready:
        return "Done: waiting for your pick"
    step = plan.current(s.steps)
    if trial.pick and step and step.kind == "pick":  # picked, about to go on
        return "Shipping it next" if trial.pick == job.arm else "Closing its PR"
    if trial.pick == job.arm and step:
        return f"Shipping: {plan.label(step)}"
    if job.state == "waiting":
        return f"Needs you, at {plan.label(step)}" if step else "Needs you"
    return f"On {plan.label(step)}" if step else "Starting"


def _took(seconds: float) -> str:
    return span(timedelta(seconds=seconds))


def _better(mine: float | None, theirs: float | None, show) -> str:
    """A number, marked when it's the lower of the pair (less time, cost or tokens)."""
    if mine is None:
        return "<span class=muted>not yet</span>"
    text = escape(show(mine))
    return f"<b class=better title='the lower of the two'>{text}</b>" if theirs is not None and mine < theirs else text


def _verdict(trial: Trial, sides: list[Side], signed_in: bool) -> str:
    """Riggy's pick and note, once made; before, the form to make it (signed in)."""
    if trial.pick:
        which = f"arm {trial.pick.upper()}" if trial.pick in trials.ARMS else "neither"
        when = escape((trial.decided_at or "")[:16].replace("T", " "))
        note = f"<p class=summary>{escape(trial.note)}</p>" if trial.note else ""
        return f"<section class=card><h2>Your pick</h2><p>You picked {which} on {when}.</p>{note}</section>"
    if not signed_in:
        return ""
    buttons = "".join(
        f"<button name=value value={s.job.arm} class=primary{'' if s.ready else ' disabled'}>Pick {trials.arm_name(s.job)}</button>"
        for s in sides
        if s.job.state in ACTIVE
    )
    waiting = [trials.arm_name(s.job) for s in sides if s.job.state in ACTIVE and not s.ready]
    hint = f"Arm {' and '.join(waiting)} isn't done yet: you can pick an arm once it's waiting for you. Picking now stops the other." if waiting else (
        "The arm you pick ships the usual way: it catches up with main, then its PR merges and deploys. The other's PR closes.")  # fmt: skip
    return (
        "<section class=card id=pick><h2>Pick one</h2>"
        f"<form method=post action=/act class=pick-form>{_hidden(action='trial-pick', trial=trial.id, back=f'/trials/{trial.id}')}"
        "<textarea name=note rows=2 placeholder='What made the difference (kept with the trial)…' aria-label='A note on your pick'></textarea>"
        f"<div class=row-form>{buttons}<button name=value value=neither>Neither</button></div></form>"
        f"<p class=hint>{escape(hint)}</p></section>"
    )
