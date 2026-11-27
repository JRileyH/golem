"""A job's cost, on its page (ledger.py)."""

from __future__ import annotations

import asyncio

from test_flow import by_label, idle_project
from test_web import app  # noqa: F401  (the web app fixture)

from golem import ledger
from golem.harness import RunResult

COSTS = {"change": 1.0, "review": 0.5}  # at API prices, by kind of run


def costed(answers: dict[str, list]):
    """by_label, with each run's usage: its cost by label, and some tokens."""
    inner = by_label(answers)

    def answer(call):
        r = inner(call)
        return RunResult(r.outcome, r.verdict, r.session_id, usage={"num_turns": 3, "cost_usd": COSTS.get(call.label, 0.05),
                                                                    "input_tokens": 1000, "output_tokens": 100})  # fmt: skip

    return answer


def test_a_jobs_page_says_what_it_cost(golem, app):  # noqa: F811
    async def go():
        room = await idle_project(golem)
        job = await golem.ask(room, "make the fish blue", workflow="change")
        golem.harness.script = costed({})
        await golem.finish()
        return job

    job = asyncio.run(go())
    r = ledger.row(golem.store.job(job.id), golem.store.steps(job.id), golem.store.runs(job.id))
    assert (r.runs, round(r.cost, 2), r.tokens_in, r.tokens_out, r.by_model, r.fixes) == (3, 1.55, 3000, 300, {"opus": 1.55}, 0)  # change, review, docs
    html = app.get(f"/p/tree-cards/jobs/{job.id}").text
    assert "<p class=costs><b>Cost:</b> $1.55 at API prices · 3k in / 300 out · agents 0s · opus 100%</p>" in html
    assert app.get("/insights/jobs").status_code == 404  # no page comparing jobs: two tabs do
