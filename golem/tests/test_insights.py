"""Where the time and usage go, how long steps usually take, and when what's queued should happen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from golem import insights
from golem.store import Step

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def ran(store, job, step, start, end, cost=0.0, outcome="verdict", model="opus/high"):
    run = store.start_run(job.id, step.name, model, "/nowhere", step.id)
    store.finish_run(run, outcome, cost_usd=cost, input_tokens=1000, output_tokens=100)
    store.db.execute("UPDATE runs SET started_at = ?, ended_at = ? WHERE id = ?", (at(start), at(end), run))


def steps_of(store, job, *specs):
    store.add_steps(job.id, [Step(0, job.id, 0, kind, name) for kind, name in specs])
    return store.steps(job.id)


def test_where_the_time_and_usage_go(golem):
    store = golem.store
    store.add_project("trees", "Trees", "s")
    job = store.add_job("trees", "new", "r", "standard")
    spec, docs, checks, checkin, fix = steps_of(
        store, job, ("agent", "spec"), ("agent", "docs"), ("checks", "checks"), ("checkin", "checkin"), ("agent", "fix")
    )
    ran(store, job, spec, 0, 10, cost=1.0)
    ran(store, job, docs, 10, 15, cost=0.5, outcome="error")  # a failed try counts too
    ran(store, job, docs, 15, 45, cost=2.5, model="haiku/low")
    ran(store, job, fix, 100, 110, cost=1.0)
    for step, start, end in ((spec, 0, 10), (docs, 10, 45), (checks, 45, 65), (checkin, 65, 245), (fix, 100, 110)):
        store.update_step(step.id, state="done", started_at=at(start), ended_at=at(end))

    r = insights.report(store)
    assert [(t.key, t.steps, round(t.seconds / 60)) for t in r.kinds] == [("docs", 1, 35), ("CI", 1, 20), ("spec", 1, 10), ("fix", 1, 10)]
    assert (round(r.total.seconds / 60), r.total.cost, r.total.unfinished) == (75, 5.0, 1)
    assert (r.waiting.steps, round(r.waiting.seconds / 60)) == (1, 180)  # check-ins are kept apart
    assert [(t.key, t.cost) for t in r.models] == [("opus/high", 2.5), ("haiku/low", 2.5)]
    assert r.slowest[0][0].name == "docs" and r.costliest[0][0].name == "docs"
    assert insights.findings(r) == [
        "The docs steps use 60% of the Claude usage (about $3.00 a step).",
        "The docs steps take 47% of the time (35m a step).",
        "Golem's own steps (mostly waiting for CI and deploys) are 27% of the time.",
        "1 fix step (failed CI or refused deploys) took 13% of the time and 20% of the usage.",
        "1 run ended without finishing its step (errors, limits, out of turns), using 10% of the usage.",
    ]
    assert insights.report(store, since=at(60)).total.cost == 1.0  # only the fix ran after that


def test_estimates_and_the_forecast(golem):
    store = golem.store
    store.add_project("a", "A", "s")
    store.add_project("b", "B", "s")
    done = store.add_job("a", "new", "r", "standard")
    old = steps_of(store, done, ("agent", "spec"), ("checks", "checks"))
    ran(store, done, old[0], 0, 30)
    store.update_step(old[0].id, state="done", started_at=at(0), ended_at=at(30))
    store.update_step(old[1].id, state="done", started_at=at(30), ended_at=at(36))
    known = insights.estimates(store)
    assert known == {"spec": 1800.0, "CI": 360.0}
    assert insights.estimate(known, Step(0, 0, 0, "agent", "docs")) == 20 * 60  # never timed: a guess

    a = store.add_job("a", "change", "r", "feature")
    a_steps = steps_of(store, a, ("agent", "spec"), ("checks", "checks"), ("checkin", "checkin"), ("agent", "docs"))
    store.update_step(a_steps[0].id, state="running", started_at=at(100))
    b = store.add_job("b", "new", "r", "standard")
    b_steps = steps_of(store, b, ("agent", "spec"), ("alert", "alert"))
    now = T0 + timedelta(minutes=110)
    etas = insights.forecast([(store.job(a.id), store.steps(a.id)), (b, b_steps)], known, now)
    # spec has 20 of its usual 30 minutes left, then CI; at the check-in A waits for Riggy and B goes.
    assert {p: (e.start - now, e.end - now) for p, e in etas[a.id].items()} == {
        1: (timedelta(0), timedelta(minutes=20)),
        2: (timedelta(minutes=20), timedelta(minutes=26)),
        3: (timedelta(minutes=26), timedelta(minutes=26)),
    }
    assert etas[b.id][1].start - now == timedelta(minutes=26) and etas[b.id][2].start - now == timedelta(minutes=56)
    # A step over its usual time has a minute left.
    late = insights.forecast([(store.job(a.id), store.steps(a.id))], known, now + timedelta(hours=2))
    assert late[a.id][1].end - late[a.id][1].start == timedelta(minutes=1)


def test_a_pick_between_options_waits_like_a_check_in(golem):
    store = golem.store
    store.add_project("trees", "Trees", "s")
    job = store.add_job("trees", "change", "show me logos", "options")
    made, choose, change = steps_of(store, job, ("agent", "options"), ("choose", "choose"), ("agent", "change"))
    etas = insights.forecast([(job, [made, choose, change])], {}, T0)
    assert list(etas[job.id]) == [1, 2] and etas[job.id][2].start == etas[job.id][2].end  # then it's up to Riggy
    store.update_step(made.id, state="done", started_at=at(0), ended_at=at(10))
    store.update_step(choose.id, state="done", started_at=at(10), ended_at=at(70))
    r = insights.report(store)
    assert (r.waiting.steps, round(r.waiting.seconds / 60), round(r.total.seconds / 60)) == (1, 60, 0)
