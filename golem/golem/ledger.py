"""A job's cost, on its page (projectpage.head_part): to see whether checklist mode
(checklist.py) saves usage, with the two projects of an idea built both ways open side by side.

A job's cost is its runs': what Claude Code says each would have cost at API prices, the fair
weight for how much of the allowance it took (insights.py). Beside it, its tokens, how long its
agents ran, how long it took from start to finish and how much of that was waiting on Riggy,
its cost by model, what went round again (fixes after failed CI or deploys), and for a job in
checklist mode, its items: how many, how many passed first time, went again, went to the
Build's model, or were given up on, and what the planning cost against the items.

Golem keeps no judgement of how good the work is: that's Riggy's to look at, on the sites.

Pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import checklist, plan
from .store import ACTIVE, Job, Step


@dataclass
class Row:
    job: Job
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


def row(job: Job, steps: list[Step], runs: list[dict], now: datetime | None = None) -> Row:
    """The job's ledger, from its steps and runs (Store.steps, Store.runs)."""
    r = Row(job)
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


def _seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    return max((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 0.0)
