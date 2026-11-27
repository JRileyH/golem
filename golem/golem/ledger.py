"""A job's cost, on its page (projectpage.head_part).

A job's cost is its runs': what Claude Code says each would have cost at API prices, the fair
weight for how much of the allowance it took (insights.py). Beside it, its tokens, how long its
agents ran, how long it took from start to finish and how much of that was waiting on Riggy,
its cost by model, and what went round again (fixes after failed CI or deploys).

compare.py lays any two projects' jobs side by side in a CSV, with these and more.

Golem keeps no judgement of how good the work is: that's Riggy's to look at, on the sites.

Pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import plan
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


def row(job: Job, steps: list[Step], runs: list[dict], now: datetime | None = None) -> Row:
    """The job's ledger, from its steps and runs (Store.steps, Store.runs)."""
    r = Row(job)
    for run in runs:
        cost = run["cost_usd"] or 0.0
        r.runs += 1
        r.cost += cost
        r.tokens_in += run["input_tokens"] or 0
        r.tokens_out += run["output_tokens"] or 0
        r.agent_seconds += seconds(run["started_at"], run["ended_at"])
        model = (run["model"] or "").split("/")[0]
        r.by_model[model] = r.by_model.get(model, 0.0) + cost
    started = [s.started_at for s in steps if s.started_at]
    ended = [s.ended_at for s in steps if s.ended_at]
    if started:
        last = (now or datetime.now(UTC)).isoformat(timespec="seconds") if job.state in ACTIVE else max(ended or started)
        r.wall_seconds = seconds(min(started), last)
    r.waiting_seconds = sum(seconds(s.started_at, s.ended_at) for s in steps if s.kind in plan.ASKS)
    r.fixes = sum(1 for s in steps if s.kind == "agent" and s.name == "fix" and s.started_at)
    return r


def seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    return max((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 0.0)
