"""Each job's cost, to see whether checklist mode (checklist.py) saves usage: the jobs' ledger.

A job's cost is its runs': what Claude Code says each would have cost at API prices, the fair
weight for how much of the allowance it took (insights.py). Beside it, its tokens, how long its
agents ran, how long it took from start to finish and how much of that was waiting on Riggy,
its cost by model, what went round again (fixes after failed CI or deploys), and for a job in
checklist mode, its items: how many, how many passed first time, went again, went to the
Build's model, or were given up on, and what the planning cost against the items.

Golem keeps no judgement of how good the work is: that's Riggy's to look at, on the sites.

Pure but for reading the store.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import median

from . import checklist, plan
from .store import ACTIVE, Job, Project, Step, Store

KINDS = {"new": "new project", "change": "change", "look": "question", "propose": "proposals", "adopt": "adoption", "triage": "triage"}
MODELS = ("haiku", "sonnet", "opus", "fable")  # the columns of cost by model, in the CSV


@dataclass
class Row:
    job: Job
    project: Project | None
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    runs: int = 0
    agent_seconds: float = 0.0  # how long its runs ran
    wall_seconds: float = 0.0  # from its first step to its last (to now, while it's under way)
    waiting_seconds: float = 0.0  # of that, waiting on Riggy: check-ins and picks
    by_model: dict[str, float] = field(default_factory=dict)  # cost, by model (haiku, opus…)
    fixes: int = 0  # fix steps: failed CI or a refused deploy, mended
    planning_cost: float = 0.0  # checklist mode: what the Builds that planned items cost
    items_cost: float = 0.0  # and the items' builds
    items: int = 0
    first_try: int = 0  # items whose first check passed
    again: int = 0  # items built more than once
    escalated: int = 0  # items that went to their Build's model
    gave_up: int = 0  # items still failing after their last try

    @property
    def kind(self) -> str:
        return KINDS.get(self.job.start, self.job.start)

    @property
    def mode(self) -> str:
        return self.job.mode or "usual"

    @property
    def twin(self) -> str:
        return self.project.twin if self.project else ""

    @property
    def done(self) -> bool:
        return self.job.state == "done"


def rows(store: Store, since: str = "") -> list[Row]:
    """Every job begun at or after `since` (an ISO time; "" for all), the latest first."""
    projects = {p.slug: p for p in store.projects()}
    out = []
    for job in store.jobs_begun(since):
        out.append(row(job, projects.get(job.slug), store.steps(job.id), store.runs(job.id)))
    return out


def row(job: Job, project: Project | None, steps: list[Step], runs: list[dict], now: datetime | None = None) -> Row:
    r = Row(job, project)
    by_id = {s.id: s for s in steps}
    for run in runs:
        cost = run["cost_usd"] or 0.0
        r.runs += 1
        r.cost += cost
        r.tokens_in += run["input_tokens"] or 0
        r.tokens_out += run["output_tokens"] or 0
        r.agent_seconds += _seconds(run["started_at"], run["ended_at"])
        model = (run["model"] or "").split("/")[0]
        r.by_model[model] = r.by_model.get(model, 0.0) + cost
        step = by_id.get(run["step_id"])
        if step is None:
            continue
        if step.item:
            r.items_cost += cost
        elif job.mode == checklist.MODE and step.kind == "agent" and step.name in _planners(steps):
            r.planning_cost += cost
    started = [s.started_at for s in steps if s.started_at]
    ended = [s.ended_at for s in steps if s.ended_at]
    if started:
        last = (now or datetime.now(UTC)).isoformat(timespec="seconds") if job.state in ACTIVE else max(ended or started)
        r.wall_seconds = _seconds(min(started), last)
    r.waiting_seconds = sum(_seconds(s.started_at, s.ended_at) for s in steps if s.kind in plan.ASKS)
    r.fixes = sum(1 for s in steps if s.kind == "agent" and s.name == "fix" and s.started_at)
    checks: dict[tuple[int, int], list[Step]] = {}
    for s in steps:
        if s.kind == "check" and s.item and s.state in ("done", "failed"):
            checks.setdefault((s.item.get("plan", 0), s.item.get("n", 0)), []).append(s)
    for tries in checks.values():
        r.items += 1
        if tries[0].state == "done":
            r.first_try += 1
        if len(tries) > 1:
            r.again += 1
        if any(int(t.item.get("try") or 1) == checklist.TRIES for t in tries):
            r.escalated += 1
        if tries[-1].state == "failed" and int(tries[-1].item.get("try") or 1) >= checklist.TRIES:
            r.gave_up += 1
    return r


def _planners(steps: list[Step]) -> set[str]:
    """The names of the steps that planned a checklist: those its items name as their plan."""
    plans = {s.item.get("plan") for s in steps if s.item}
    return {s.name for s in steps if s.id in plans}


# --- comparing the modes ----------------------------------------------------------------------


@dataclass
class Group:
    """Finished jobs of one kind in one mode, and what they usually cost."""

    kind: str
    mode: str
    rows: list[Row]

    @property
    def cost(self) -> float:
        return median(r.cost for r in self.rows) if self.rows else 0.0

    @property
    def agent_seconds(self) -> float:
        return median(r.agent_seconds for r in self.rows) if self.rows else 0.0

    @property
    def total(self) -> float:
        return sum(r.cost for r in self.rows)


def groups(found: list[Row]) -> list[Group]:
    """Finished jobs by kind (new project, change…) and mode, the usual way first in each kind."""
    out: dict[tuple[str, str], list[Row]] = {}
    for r in found:
        if r.done and r.runs:
            out.setdefault((r.kind, r.mode), []).append(r)
    order = list(KINDS.values())
    return [Group(k, m, rs) for (k, m), rs in sorted(out.items(), key=lambda x: (order.index(x[0][0]) if x[0][0] in order else 99, x[0][1] != "usual", x[0][1]))]


def twins(found: list[Row]) -> list[tuple[Row, Row]]:
    """Each pair built both ways: the usual project's first job and its checklist twin's, the newest pair first."""
    first: dict[str, Row] = {}
    for r in sorted(found, key=lambda r: r.job.id):
        if r.twin and r.job.start == "new" and r.job.slug not in first:
            first[r.job.slug] = r
    pairs = []
    for slug, r in first.items():
        other = first.get(r.twin)
        if other and r.mode == "usual" and other.mode == checklist.MODE:
            pairs.append((r, other))
    return sorted(pairs, key=lambda p: -p[0].job.id)


def saved(usual: float, other: float) -> str:
    """How much less (or more) the other cost, in words: "38% less", "12% more"."""
    if not usual:
        return ""
    change = (other - usual) / usual
    return f"{abs(round(100 * change))}% {'less' if change < 0 else 'more'}" if round(100 * change) else "the same"


# --- the CSV ----------------------------------------------------------------------------------

COLUMNS = (
    "job", "project", "twin", "kind", "workflow", "mode", "state", "begun", "title", "cost_usd", "tokens_in", "tokens_out", "runs",
    "agent_minutes", "wall_minutes", "waiting_minutes", *(f"cost_{m}" for m in MODELS), "cost_other_models", "fixes",
    "planning_cost_usd", "items_cost_usd", "items", "items_first_try", "items_again", "items_escalated", "items_gave_up",
)  # fmt: skip


def to_csv(found: list[Row]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUMNS)
    for r in found:
        other = sum(c for m, c in r.by_model.items() if m not in MODELS)
        w.writerow([
            r.job.id, r.job.slug, r.twin, r.kind, r.job.workflow, r.mode, r.job.state, r.job.created_at, plan.title(r.job),
            f"{r.cost:.4f}", r.tokens_in, r.tokens_out, r.runs, _minutes(r.agent_seconds), _minutes(r.wall_seconds),
            _minutes(r.waiting_seconds), *(f"{r.by_model.get(m, 0.0):.4f}" for m in MODELS), f"{other:.4f}", r.fixes,
            f"{r.planning_cost:.4f}", f"{r.items_cost:.4f}", r.items, r.first_try, r.again, r.escalated, r.gave_up,
        ])  # fmt: skip
    return out.getvalue()


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}"


def _seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    return max((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 0.0)
