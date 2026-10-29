"""Where the time and Claude usage go, by kind of step, project and model; how long each kind
of step usually takes; and from that, when the steps still to come should happen.

Time is wall-clock: an agent step's runs (retries and wrap-ups included), or how long one
of golem's own steps took (waiting for CI, deploying). Usage is what Claude Code says a run
would cost at API prices. It's the fair weight for how much of the Claude allowance a run
used: cached reading counts for little, and haiku for less than opus. Tokens are as
reported, cached input included. Check-ins are time waiting on Riggy, kept apart.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from . import plan
from .store import OPEN, Job, Step, Store
from .watch import span

# Minutes a step takes before golem has timed one of its kind.
GUESS = {"agent": 20, "fix": 15, "setup": 1, "push": 0.5, "pr": 0.5, "checks": 4, "merge": 0.5,
         "push-main": 0.5, "deploy": 5, "checkin": 0, "choose": 0, "alert": 0.1}  # fmt: skip
RECENT = 20  # a kind's estimate is the median of its latest this many steps
SHIPPING = {"CI", "deploy", "PR", "merge", "push", "push to main", "set up", "done"}  # golem's steps, by label
UNFINISHED = {"error", "limit", "max_turns", "killed"}  # run outcomes that didn't finish the step


def kind(step: Step) -> str:
    """What steps are grouped by: an agent step's name (spec, docs, fix…), else golem's label (CI, deploy…)."""
    return plan.label(step)


@dataclass
class Tally:
    key: str
    steps: int = 0
    seconds: float = 0.0
    cost: float = 0.0
    tokens: int = 0
    runs: int = 0
    unfinished: int = 0  # runs that ended without finishing their step
    unfinished_cost: float = 0.0

    def add_run(self, run: dict) -> None:
        self.runs += 1
        self.seconds += _seconds(run["started_at"], run["ended_at"])
        cost = run["cost_usd"] or 0.0
        self.cost += cost
        self.tokens += (run["input_tokens"] or 0) + (run["output_tokens"] or 0)
        if run["outcome"] in UNFINISHED:
            self.unfinished += 1
            self.unfinished_cost += cost

    def add(self, other: Tally) -> None:
        for name in ("steps", "seconds", "cost", "tokens", "runs", "unfinished", "unfinished_cost"):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def per_step(self) -> float:
        return self.seconds / self.steps if self.steps else 0.0

    @property
    def cost_per_step(self) -> float:
        return self.cost / self.steps if self.steps else 0.0


@dataclass
class Report:
    total: Tally  # the workers' and golem's time, without waiting for Riggy
    kinds: list[Tally]  # by kind of step, most time first
    projects: list[Tally]
    models: list[Tally]  # agent runs by model/effort
    waiting: Tally  # check-ins and picks: waiting for Riggy to try the site or pick an option
    slowest: list[tuple[Step, str, float]]  # single steps: (step, slug, seconds)
    costliest: list[tuple[Step, str, float]]  # (step, slug, cost)
    agent_kinds: set[str] = field(default_factory=set)


def report(store: Store, since: str = "") -> Report:
    """Everything that ran since `since` (an ISO time; "" for all of it)."""
    kinds: dict[str, Tally] = {}
    projects: dict[str, Tally] = {}
    models: dict[str, Tally] = {}
    one: dict[int, Tally] = {}  # by step id
    waiting = Tally("waiting for you")
    steps = {s.id: (s, slug) for s, slug in store.finished_steps(since)}
    agent_kinds = set()

    def get(table: dict, key) -> Tally:
        if key not in table:
            table[key] = Tally(str(key))
        return table[key]

    for run in store.finished_runs(since):
        name = run["step_name"] or run["phase"].removesuffix("-wrapup")
        agent_kinds.add(name)
        for t in (get(kinds, name), get(projects, run["slug"]), get(models, run["model"])):
            t.add_run(run)
        if run["step_id"]:
            get(one, run["step_id"]).add_run(run)
    for step, slug in steps.values():
        if step.state == "skipped":
            continue
        if step.kind == "agent":
            if step.state == "done":
                get(kinds, step.name).steps += 1
                get(projects, slug).steps += 1
            continue
        took = _seconds(step.started_at, step.ended_at)
        if step.kind in plan.ASKS:
            waiting.steps += 1
            waiting.seconds += took
            continue
        for t in (get(kinds, kind(step)), get(projects, slug)):
            t.steps += 1
            t.seconds += took
        get(one, step.id).seconds += took

    total = Tally("everything")
    for t in kinds.values():
        total.add(t)
    singles = [(steps[i][0], steps[i][1], t) for i, t in one.items() if i in steps]
    return Report(
        total=total,
        kinds=sorted(kinds.values(), key=lambda t: -t.seconds),
        projects=sorted(projects.values(), key=lambda t: -t.seconds),
        models=sorted(models.values(), key=lambda t: -t.cost),
        waiting=waiting,
        slowest=[(s, slug, t.seconds) for s, slug, t in sorted(singles, key=lambda x: -x[2].seconds)[:5]],
        costliest=[(s, slug, t.cost) for s, slug, t in sorted(singles, key=lambda x: -x[2].cost)[:5] if t.cost],
        agent_kinds=agent_kinds,
    )


def findings(r: Report) -> list[str]:
    """The few things worth knowing first, in words."""
    if not r.total.seconds:
        return []
    found = []
    by_cost = sorted((t for t in r.kinds if t.cost), key=lambda t: -t.cost)
    if by_cost and r.total.cost:
        t = by_cost[0]
        found.append(
            f"{_steps(t.key)} use {_pct(t.cost, r.total.cost)} of the Claude usage"
            + (f" (about ${t.cost_per_step:.2f} a step)." if t.steps else ".")
        )
    if r.kinds:
        t = r.kinds[0]
        found.append(f"{_steps(t.key)} take {_pct(t.seconds, r.total.seconds)} of the time" + (f" ({span(timedelta(seconds=t.per_step))} a step)." if t.steps else "."))
    shipping = sum(t.seconds for t in r.kinds if t.key in SHIPPING)
    if shipping / r.total.seconds >= 0.2:
        found.append(f"Golem's own steps (mostly waiting for CI and deploys) are {_pct(shipping, r.total.seconds)} of the time.")
    fix = next((t for t in r.kinds if t.key == "fix"), None)
    if fix and fix.steps:
        found.append(
            f"{fix.steps} fix step{'s' if fix.steps != 1 else ''} (failed CI or refused deploys) took "
            f"{_pct(fix.seconds, r.total.seconds)} of the time and {_pct(fix.cost, r.total.cost)} of the usage."
        )
    if r.total.unfinished:
        found.append(
            f"{r.total.unfinished} run{'s' if r.total.unfinished != 1 else ''} ended without finishing "
            f"{'its' if r.total.unfinished == 1 else 'their'} step "
            f"(errors, limits, out of turns), using {_pct(r.total.unfinished_cost, r.total.cost)} of the usage."
        )
    return found


# --- estimates and the forecast -------------------------------------------------------


def estimates(store: Store) -> dict[str, float]:
    """Seconds each kind of step usually takes: the median of its latest finished steps."""
    took: dict[int, float] = defaultdict(float)
    for run in store.finished_runs():
        if run["step_id"]:
            took[run["step_id"]] += _seconds(run["started_at"], run["ended_at"])
    samples: dict[str, list[float]] = defaultdict(list)
    for step, _ in store.finished_steps():
        if step.state != "done" or step.kind in plan.ASKS:
            continue
        seconds = took.get(step.id, 0.0) if step.kind == "agent" else _seconds(step.started_at, step.ended_at)
        if seconds > 0:
            samples[kind(step)].append(seconds)
    return {k: median(v[-RECENT:]) for k, v in samples.items()}


def estimate(known: dict[str, float], step: Step) -> float:
    if kind(step) in known:
        return known[kind(step)]
    minutes = GUESS.get(step.name) if step.kind == "agent" else None
    return 60 * (minutes or GUESS.get(step.kind, GUESS["agent"]))


@dataclass
class Eta:
    start: datetime
    end: datetime


def forecast(order: list[tuple[Job, list[Step]]], known: dict[str, float], now: datetime) -> dict[int, dict[int, Eta]]:
    """When each step still to come should start and end: {job id: {position: Eta}}.

    One worker takes the jobs in `order` (the queue's), each until it's done or reaches
    something Riggy has to answer: a check-in, options to pick from or a question. A running step has its usual
    time left, or a minute if it's over."""
    t = now
    found: dict[int, dict[int, Eta]] = {}
    for job, steps in order:
        etas = found[job.id] = {}
        if job.state == "waiting":
            continue
        for step in steps:
            if step.state not in OPEN:
                continue
            if step.state == "waiting":
                break
            if step.kind in plan.ASKS:
                etas[step.position] = Eta(t, t)
                break
            took = estimate(known, step)
            if step.started_at:
                took = max(took - (now - datetime.fromisoformat(step.started_at)).total_seconds(), 60)
            etas[step.position] = Eta(t, t + timedelta(seconds=took))
            t += timedelta(seconds=took)
    return found


def per_step(store: Store, job_id: int) -> dict[int, Tally]:
    """Each of a job's steps' runs, added up, by step id."""
    tallies: dict[int, Tally] = {}
    for run in store.runs(job_id):
        if run["step_id"] and run["ended_at"]:
            tallies.setdefault(run["step_id"], Tally(str(run["step_id"]))).add_run(run)
    return tallies


def _seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    return max((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 0.0)


def _steps(key: str) -> str:
    return f"The {key} steps"


def _pct(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"
