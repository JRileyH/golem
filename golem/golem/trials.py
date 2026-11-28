"""Trials: one request run two ways on one project, side by side, for Riggy to pick one.

A trial's two arms, a and b, are jobs: the same request, each in a mode of its own (modes.py),
so a trial pits harnesses, models or prompt sets against each other. Riggy starts one from a
project's request box ("Compare with"). Each arm:

- starts from the same commit of main, and works on its own branch, in a tree of its own
- takes no turns on its project, and nothing waits for it (turns.py, shipments.py)
- runs in a worker slot of its own, beside the workers setting's, so neither arm's step waits
  for the other's to free one (Scheduler.fill)
- opens its PR and runs CI like any change, but merges and deploys nothing: its work waits on
  its PR

At the end of its workflow, an arm waits at its pick (a block golem puts in the arm's copy of
the workflow, `with_pick`). The Trials page shows the arms side by side: what each did, how long
it took and what it cost, its PR and its CI. Riggy picks one, or neither, with a note: the
trial's verdict. The arm he picks ships the usual way, from its push on (catching up with main
first: Scheduler.needs_sync), and is from then on a change like any other. The other closes its
PR. Picking while an arm is still working stops it.

Pure, but for `csv`, which reads the arms' logs (compare.py).
"""

from __future__ import annotations

import csv as _csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime

from . import engine, ledger, modes, plan
from .store import ACTIVE, Job, Step, Store, Trial

ARMS = ("a", "b")
NEITHER = "neither"
PICK = "pick"  # the id of the block an arm waits at


def with_pick(snap: dict) -> dict:
    """An arm's copy of its workflow: everything that went to End goes to a pick first, which
    leaves for End once Riggy's picked (its usual way on: nothing more is predicted), or for the
    arm he picked, back to its last push first, to ship it. Only workflows that ship can be run
    two ways."""
    nodes, edges = snap["nodes"], snap["edges"]
    ends = [nid for nid, n in nodes.items() if n["type"] == "end"]
    pushes = [nid for nid in engine.happy_path(snap) if nodes[nid]["type"] == "push"]
    if not ends or not pushes:
        raise engine.GraphError("only a workflow that ships can be run two ways")
    for outs in edges.values():
        for port, to in outs.items():
            if to in ends:
                outs[port] = PICK
    nodes[PICK] = {"id": PICK, "type": "pick", "ref": PICK, "path": "", "name": PICK}
    edges[PICK] = {"end": ends[0], "ship": pushes[-1]}
    return snap


def arm_name(job: Job) -> str:
    """"A", "B": the arm, as Riggy reads it."""
    return job.arm.upper()


def label(job: Job, first: bool = False) -> str:
    """The job as one of its trial's arms: "trial 3, arm A" ("Trial…", `first` in a sentence)."""
    if not job.trial:
        return ""
    return f"{'Trial' if first else 'trial'} {job.trial}, arm {arm_name(job)}"


def mode_name(job: Job) -> str:
    """The arm's mode by name: the one it kept when it started, else the one it was asked to run in."""
    kept = modes.kept((plan.snapshot(job) or {}).get("mode"))
    if kept:
        return kept.name
    found = modes.load().get(job.mode)
    return found.name if found else job.mode


def title(trial: Trial) -> str:
    return trial.title or plan.clean_title(trial.request) or f"Trial {trial.id}"


def at_pick(job: Job, steps: list[Step]) -> bool:
    """Whether the arm is waiting for Riggy's pick: its work done, its PR open."""
    step = plan.current(steps)
    return job.state == "waiting" and step is not None and step.kind == "pick"


def state(trial: Trial, arms: list[Job], steps: dict[int, list[Step]]) -> str:
    """Where the trial stands, in a word or two: "under way", "ready" (every arm still going is at
    its pick), "picked A", "picked neither", or "stopped" (no arm left to pick)."""
    if trial.pick:
        return f"picked {trial.pick.upper() if trial.pick in ARMS else trial.pick}"
    going = [j for j in arms if j.state in ACTIVE]
    if not going:
        return "stopped"
    return "ready" if all(at_pick(j, steps[j.id]) for j in going) else "under way"


def open_trial(store: Store, slug: str) -> Trial | None:
    """The project's trial still waiting for Riggy's pick, if any: one at a time on a project."""
    for trial in store.trials(slug, limit=20):
        if not trial.pick and any(j.state in ACTIVE for j in store.arms(trial.id)):
            return trial
    return None


@dataclass
class Side:
    """An arm, for the Trials page: what it ran in, where it is, its PR and CI, and its numbers."""

    job: Job
    steps: list[Step]
    mode: str  # its mode's name
    harness: str  # its mode's: claude | opencode
    model: str  # its model for every other step ("*"), as it ran: a free model by its name
    cost: ledger.Row
    ready_seconds: float | None  # from its first step to its pick; None until it gets there
    pr: str  # its PR's address, once it has one
    ci: str  # passed | failed | waiting | "" for none yet
    ci_link: str

    @property
    def ready(self) -> bool:
        return at_pick(self.job, self.steps)


def side(job: Job, steps: list[Step], runs: list[dict], now: datetime | None = None) -> Side:
    snap = plan.snapshot(job) or {}
    kept = modes.kept(snap.get("mode"))
    model = ""
    if kept and (every := kept.models.steps.get("*")):
        model = plan.vars_of(job).get("free_model") if every.model == modes.FREE else every.model
        model = "/".join(x for x in (model or every.model, every.effort) if x)
    started = [s.started_at for s in steps if s.started_at]
    picks = [s for s in steps if s.kind == "pick" and s.started_at]
    ready = ledger.seconds(min(started), picks[0].started_at) if started and picks else None
    pr = next((s.links["pr"] for s in reversed(steps) if s.kind == "pr" and s.links.get("pr")), "")
    checks = next((s for s in reversed(steps) if s.kind == "checks" and s.started_at), None)
    ci = ""
    if checks is not None:
        ci = {"pass": "passed", "fail": "failed"}.get(checks.port, "waiting" if checks.state in ("running", "pending", "waiting") else checks.state)
    harness = kept.harness if kept else (modes.load().get(job.mode).harness if job.mode in modes.load() else "claude")
    return Side(job, steps, mode_name(job), harness, model, ledger.row(job, steps, runs, now or datetime.now(UTC)), ready, pr, ci,
                checks.links.get("ci", "") if checks else "")  # fmt: skip


def csv(store: Store, settings, trial: Trial) -> str:
    """The trial's arms in compare.py's CSV: a row per job, step, run and message, side A then B."""
    from . import compare

    out = io.StringIO()
    w = _csv.DictWriter(out, fieldnames=list(compare.COLUMNS), restval="", lineterminator="\n")
    w.writeheader()
    for job in store.arms(trial.id):
        w.writerows(compare.job_rows(store, arm_name(job), job.slug, 1, job))
    return out.getvalue()
