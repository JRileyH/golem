"""Changes on one project shipping together, when the project allows more than one at once
(Project.parallel_jobs): one push, PR, CI run, merge and deploy for all of them, rather than
each taking its own turn at shipping (turns.py).

A change is ready to ship when its current step begins a run of ship steps that hasn't
started. It waits there while another change under way on the project is still on its way
to shipping (planning, building, reviewing, waiting for a turn), so that they go together.
The shipment starts once every change under way is ready or waiting on Riggy (who could take
days), or as many are ready as the project allows at once. A change Riggy asks for before then
joins it, and the others wait for it too; one asked for after it starts goes in the next. A
project has at most `parallel_jobs` changes under way, and one asked for beyond that waits for
one to finish (turns.behind), so a shipment can't wait forever.

The oldest change ready carries the shipment. Its catch-up with main (the `sync` golem puts in
before its push) merges the others' branches into its own, a conflict going to a `resolve`
step like any conflict with main, and its push, PR, CI, merge and deploy ship them all; a fix
after failed CI is its, on all their work. The others ride along: they wait at their first ship
step, holding nothing, and once the carrier's run of ship steps is over, theirs go by as done
with its PR and deploy, and each carries on from there (telling Riggy it's done). A carrier
stopped before it ships drops the shipment: the others are ready again, their branches as they
were. A rider stopped once it's been merged in still goes out with the rest.

Shipments are rows in the database (store.Shipment), with the step each job's run of ship
steps began at, so a restart loses none.
"""

from __future__ import annotations

from . import engine, plan, turns
from .store import ACTIVE, OPEN, Job, Project, Shipment, Step, Store


def cap(project: Project | None) -> int:
    """The most changes one shipment carries: as many as the project allows under way at once."""
    return max(project.parallel_jobs, 1) if project else 1


def together(store: Store, job: Job) -> bool:
    """Whether the job's changes can ship with others: in a tree of its own, on a project that
    allows more than one change at once."""
    project = store.project(job.slug)
    return bool(job.worktree) and project is not None and project.parallel_jobs > 1


def shipment_of(store: Store, job: Job) -> Shipment | None:
    """The shipment under way that carries the job, as its carrier or riding along."""
    return next((s for s in store.shipping(job.slug) if job.id in s.jobs), None)


def rides(store: Store, job: Job) -> Shipment | None:
    """The shipment under way the job rides along in: another job's ship steps ship its work."""
    found = shipment_of(store, job)
    return found if found is not None and found.carrier != job.id else None


def ready(store: Store, job: Job) -> bool:
    """Whether the change is at the start of a run of ship steps that hasn't started, and in no shipment."""
    if job.state not in ("queued", "parked", "running") or not together(store, job):
        return False
    want = turns.wants(store, job)
    return want is not None and want.kind == "ship" and not want.started and shipment_of(store, job) is None


def on_its_way(store: Store, job: Job) -> bool:
    """Whether a change under way is still working towards shipping: not at a ship step yet, not
    waiting on Riggy, and not waiting to start (turns.behind)."""
    if job.state not in ACTIVE or job.state == "waiting" or turns.behind(store, job) is not None:
        return False
    want = turns.wants(store, job)
    if want is not None and want.kind == "ship":
        return False  # ready, or shipping already
    return ships_ahead(store, job)


def ships_ahead(store: Store, job: Job) -> bool:
    """Whether the job comes to a ship step from where it is, on the usual way through its
    workflow. One that hasn't started, or from before workflows, is taken to."""
    snap = plan.snapshot(job)
    trace = store.steps(job.id)
    if snap is None or not trace:
        return True
    if any(s.state in OPEN and plan.turn_of(trace, s) == "ship" for s in trace):
        return True
    at = next((s for s in reversed(trace) if s.state in OPEN and s.node_id), None)
    if at is None:
        return plan.current(trace) is not None
    ahead = [at.node_id, *engine.predict(snap, plan.vars_of(job), trace, at.node_id)]
    return any(engine.turn_of(snap["nodes"][n]) == "ship" for n in ahead if n in snap["nodes"])


def waits_for(store: Store, job: Job) -> Job | None:
    """For a change ready to ship: the change its shipment waits for, still on its way to
    shipping, or else the older change ready that will carry it. None if it can go now."""
    others = [o for o in store.change_jobs(job.slug) if o.id != job.id]
    ready_ones = [job, *(o for o in others if ready(store, o))]
    if len(ready_ones) < cap(store.project(job.slug)):
        coming = next((o for o in others if on_its_way(store, o)), None)
        if coming is not None:
            return coming
    head = min(ready_ones, key=lambda j: j.id)
    return None if head.id == job.id else head


def held(store: Store, job: Job, want: turns.Turn) -> tuple[str, Job] | None:
    """For a change whose current step needs the turn to ship: what holds it up, if a shipment
    does. Riding along in one under way: its carrier ("riding"). Another under way: its carrier,
    as for any turn ("ship"). Ready, but others are on their way: the one it waits for
    ("together")."""
    live = next(iter(store.shipping(job.slug)), None)
    if live is not None:
        carrier = store.job(live.carrier)
        if carrier is None or carrier.id == job.id:
            return None
        if job.id in live.jobs:
            return "riding", carrier
        return None if want.started else ("ship", carrier)
    if want.started or not ready(store, job):
        return None
    other = waits_for(store, job)
    return ("together", other) if other is not None else None


def form(store: Store, carrier: Job, anchor: int) -> Shipment | None:
    """The shipment the carrier's run of ship steps, beginning at step `anchor`, carries: the
    other changes ready to ship, oldest first, up to as many as the project allows at once.
    None if there are none and it ships alone."""
    if not together(store, carrier) or shipment_of(store, carrier) is not None:
        return None
    room = cap(store.project(carrier.slug)) - 1
    riders = [o for o in store.change_jobs(carrier.slug) if o.id != carrier.id and ready(store, o)][:room]
    if not riders:
        return None
    jobs = {carrier.id: anchor}
    for r in riders:
        jobs[r.id] = plan.current(store.steps(r.id)).id
    return store.add_shipment(carrier.slug, carrier.id, jobs)


def run_from(steps: list[Step], anchor: int) -> list[Step]:
    """A job's run of ship steps that began at step `anchor`, in order, as far as it's written."""
    ordered = sorted((s for s in steps if s.kind not in engine.CONTROL), key=lambda s: s.position)
    start = next((i for i, s in enumerate(ordered) if s.id == anchor), None)
    if start is None:
        return []
    run = []
    for s in ordered[start:]:
        if plan.turn_of(ordered, s) != "ship":
            break
        run.append(s)
    return run


def in_run(steps: list[Step], step: Step, anchor: int) -> bool:
    """Whether `step` is in the job's run of ship steps that began at step `anchor`."""
    return any(s.id == step.id for s in run_from(steps, anchor))


def over(store: Store, s: Shipment) -> str:
    """Where a shipment under way stands: "shipping" while its carrier is still in that run of
    ship steps; "shipped" once it's past it; "dropped" if it was stopped first."""
    carrier = store.job(s.carrier)
    if carrier is None or carrier.state in ("killed", "failed"):
        return "dropped"
    steps = store.steps(carrier.id)
    at = plan.current(steps)
    if at is None:
        return "shipped"
    return "shipping" if in_run(steps, at, s.jobs[s.carrier]) else "shipped"


def gathered(store: Store, s: Shipment) -> bool:
    """Whether the carrier has merged the riders' branches into its own: its catch-up with main is done."""
    run = run_from(store.steps(s.carrier), s.jobs[s.carrier])
    return any(x.kind == "sync" and x.state in ("done", "skipped") for x in run)


def names(jobs: list[Job]) -> str:
    """Jobs by number, in words: "job 13", "jobs 13 and 14", "jobs 13, 14 and 15"."""
    ids = [str(j.id) for j in jobs]
    if len(ids) == 1:
        return f"job {ids[0]}"
    return f"jobs {', '.join(ids[:-1])} and {ids[-1]}"
