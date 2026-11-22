"""Changes on one project taking turns, when the project allows more than one at once
(Project.parallel_jobs).

Each change works in a git tree of its own (workspace.TREES), so most of one job overlaps with
another's: setting up, specs, options and design rounds, review and docs, waiting on Riggy or
CI. Two things go one job at a time per project (plan.CLASSES):

- building: writing the code. A job building on top of what another is still building would
  leave its conflicts for later; the next builds from what the last one shipped.
- shipping: from catching up with main, through the push, PR, CI and merge, to the deploy.
  Main moves only under the job shipping, so what CI checked is what's merged and deployed.

A job holds a turn while its current step is of that kind and that run of steps (plan.run_before)
has started. Parked (on CI, a retry, a usage limit) it keeps it. Waiting on Riggy it lets a
build turn go, as its work stays in its tree, and takes it back when it can; it keeps a ship
turn, or another job could merge between its catch-up with main and its own merge. Turns are
read from the steps in the database, so a restart loses none.

Never a deadlock: a job only ever wants the turn its current step needs, and only ever holds
that one, so none holds one turn while waiting for the other. Jobs after the same turn go in the
order their runs of steps started (then by number), and a running step is never cut short.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import plan
from .store import LOOKS, Job, Store

TURNS = ("build", "ship")


@dataclass(frozen=True)
class Turn:
    kind: str  # build | ship
    started: str  # when the first of its steps started; "" if none has yet


def wants(store: Store, job: Job) -> Turn | None:
    """The turn the job's current step needs, if any: only changes take turns."""
    if job.kind != "change" or job.start in LOOKS:
        return None
    steps = store.steps(job.id)
    step = plan.current(steps)
    if step is None or (kind := plan.turn_of(steps, step)) not in TURNS:
        return None
    started = [s.started_at for s in plan.run_before(steps, step) if s.started_at]
    return Turn(kind, min(started, default=""))


def holds(job: Job, turn: Turn) -> bool:
    """Whether the job has the turn it wants: under way, and not waiting on Riggy to build."""
    return bool(turn.started) and (turn.kind == "ship" or job.state != "waiting")


def blocked_by(store: Store, job: Job, running: set[int] = frozenset()) -> tuple[str, Job] | None:
    """The turn the job's current step needs and the other change on its project that has it,
    or None if it can go. A job ahead of it (under way first) or running a step now has it."""
    mine = wants(store, job)
    if mine is None:
        return None
    for other in store.change_jobs(job.slug):
        theirs = wants(store, other) if other.id != job.id else None
        if theirs is None or theirs.kind != mine.kind or not holds(other, theirs):
            continue
        if other.id in running or other.state == "running" or not mine.started or (theirs.started, other.id) < (mine.started, job.id):
            return mine.kind, other
    return None


def behind(store: Store, job: Job) -> Job | None:
    """For a change that hasn't started: the older change still under way that it waits for
    before it can, if any. A project takes one change at a time unless it allows more
    (parallel_jobs), and a new project, an adoption or a job from before trees runs alone; with
    room beside the others, changes take turns instead (blocked_by). Questions and proposals only
    look, so they wait for nothing; and a change under way already carries on."""
    if job.start in LOOKS or store.steps(job.id):
        return None
    older = [j for j in store.change_jobs(job.slug) if j.id < job.id]
    if not older:
        return None
    project = store.project(job.slug)
    allowed = max(project.parallel_jobs, 1) if project else 1
    alone = next((j for j in older if j.kind in ("new", "adopt") or not j.worktree), None)
    if alone or len(older) >= allowed:
        return alone or older[0]
    return None


def waiting_for(store: Store, job: Job) -> str:
    """For a queued change waiting its turn, who it's waiting for, in words; "" if it isn't."""
    if job.state == "queued" and (older := behind(store, job)) is not None:
        return f"waiting for job {older.id} to finish" + (" (it's waiting on you)" if older.state == "waiting" else "")
    held = blocked_by(store, job) if job.state == "queued" else None
    if held is None:
        return ""
    kind, other = held
    doing = "building" if kind == "build" else "shipping"
    return f"waiting for job {other.id} to finish {doing}" + (" (it's waiting on you)" if other.state == "waiting" else "")
