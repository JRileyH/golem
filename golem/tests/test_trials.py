"""Trials (trials.py): one request run two ways on one project, and the arm Riggy picks ships."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import pytest
from conftest import REPO, Golem
from starlette.testclient import TestClient
from test_flow import JobGate, a_change, by_label, current, run, soon, steps_until

from golem import engine, plan, trials, turns, workflows
from golem.control import ControlError
from golem.store import Store
from golem.web import create_app

REQUEST = "add a quiz mode"


def trees(golem: Golem, parallel: int = 1) -> None:
    golem.store.add_project("trees", "Trees", "Tree flashcards.")
    golem.store.update_project("trees", parallel_jobs=parallel)


async def a_trial(golem: Golem, compare: str = "opencode-free", workflow: str = "change"):
    """The request asked for on trees' page, to compare with `compare`: the trial and its arms."""
    golem.harness.script = by_label({"request": [{"workflow": workflow, "title": "Quiz Mode"}]})
    job = await golem.bot.ask(golem.store.project("trees"), REQUEST, compare=compare)
    return golem.store.trial(job.trial), golem.store.arms(job.trial)


def ready(golem: Golem, job_id: int) -> bool:
    return trials.at_pick(golem.store.job(job_id), golem.store.steps(job_id))


def shipping(golem: Golem) -> list[str]:
    return [x for x in golem.workspace.actions if x.startswith(("push", "open PR", "merge PR", "close PR"))]


def test_a_trial_runs_one_change_two_ways_and_ships_the_arm_riggy_picks(golem):
    async def go():
        trees(golem)
        trial, (a, b) = await a_trial(golem)
        assert (a.arm, a.mode, b.arm, b.mode) == ("a", "claude-power", "b", "opencode-free")  # A in the project's mode, as it'd run
        assert a.trial == b.trial == trial.id and (trial.title, a.workflow, b.workflow) == ("Quiz Mode", "change", "change")
        assert [m.text for m in golem.store.job_messages(b.id)] == [REQUEST]
        made = {}
        tree = golem.workspace.ensure_tree

        async def ensure_tree(slug, job, at=None):
            made.setdefault(job, at)
            return await tree(slug, job, at)

        golem.workspace.ensure_tree = ensure_tree

        await golem.finish()  # each arm does its work, opens its PR, and waits
        assert ready(golem, a.id) and ready(golem, b.id)
        trial = golem.store.trial(trial.id)
        a, b = golem.store.job(a.id), golem.store.job(b.id)
        assert made[a.id] == made[b.id] == trial.base != ""  # from the same commit of main
        assert a.issue == b.issue == trial.issue and len(golem.workspace.issues) == 1
        assert shipping(golem) == [
            f"push golem/job-{a.id}", "open PR #1: Quiz Mode: change + review + docs",
            f"push golem/job-{b.id}", "open PR #2: Quiz Mode: change + review + docs",
        ]  # fmt: skip
        assert golem.deployer.deploys == []  # nothing near main
        held = {s.kind: s.summary for s in golem.store.steps(b.id) if s.kind in ("merge", "deploy")}
        assert held == {"merge": "Not merged: a trial's arm waits on its PR until you pick one.",
                        "deploy": "Not deployed: a trial's arm ships only if you pick it."}  # fmt: skip
        assert "as trial 1, arm A, in Claude power" in golem.workspace.bodies[0] and f"Closes #{trial.issue}" in golem.workspace.bodies[0]
        assert {r["model"] for r in golem.store.runs(b.id)} == {"opencode/fake-free"}  # B on OpenCode's free model
        assert golem.chat.texts("trees")[-1].endswith("Trial 1 is ready: compare its arms and pick one to ship.\nhttps://golem.unlaunched.dev/trials/1")
        assert golem.store.change_jobs("trees") == [] and [j.id for j in golem.store.change_jobs("trees", arms=True)] == [a.id, b.id]
        assert trials.state(trial, [a, b], {j.id: golem.store.steps(j.id) for j in (a, b)}) == "ready"

        with pytest.raises(ControlError, match="Pick A, B or neither"):
            await golem.control.pick_arm(trial.id, "c")
        said = await golem.control.pick_arm(trial.id, "a", "A's quiz reads better")
        assert said == "Trial 1: you picked arm A. “A's quiz reads better”"
        assert [j.id for j in golem.store.change_jobs("trees")] == [a.id]  # a change like any other now
        await golem.finish()
        a, b = golem.store.job(a.id), golem.store.job(b.id)
        assert a.state == b.state == "done"
        assert shipping(golem)[4:] == [f"push golem/job-{a.id}", "merge PR #1", "close PR #2: Closed: you picked arm A."]
        assert len(golem.deployer.deploys) == 1 and golem.workspace.closed == []  # A's PR closes the issue
        steps = [(s.kind, s.summary) for s in golem.store.steps(a.id)]
        after = steps[[k for k, _ in steps].index("pick") :]
        assert [k for k, _ in after] == ["pick", "sync", "push", "pr", "checks", "merge", "deploy", "pick", "alert"]  # caught up first
        assert after[0][1] == "You picked it: shipping it." and after[-2][1] == "Shipped: you picked it."
        assert golem.store.steps(b.id)[-1].summary == "Closed its PR: you picked arm A."
        trial = golem.store.trial(trial.id)
        assert (trial.pick, trial.note) == ("a", "A's quiz reads better") and trial.decided_at

    run(go())


def test_picking_an_arm_stops_the_other_still_working_and_neither_closes_both(golem):
    async def go():
        trees(golem)
        trial, (a, b) = await a_trial(golem)
        with pytest.raises(ControlError, match="isn't ready to pick"):
            await golem.control.pick_arm(trial.id, "a")
        await steps_until(golem, lambda: ready(golem, a.id))
        assert golem.store.steps(b.id) == []  # one worker's turns: B hasn't started
        await golem.control.pick_arm(trial.id, "a")
        await golem.finish()
        assert (golem.store.job(a.id).state, golem.store.job(b.id).state) == ("done", "killed") and len(golem.deployer.deploys) == 1
        assert trials.open_trial(golem.store, "trees") is None
        with pytest.raises(ControlError, match="decided already"):
            await golem.control.pick_arm(trial.id, "b")

        trial, (a, b) = await a_trial(golem, compare="claude-power")  # the same mode twice: how much runs vary
        assert (a.mode, b.mode) == ("claude-power", "claude-power") and trials.open_trial(golem.store, "trees") == trial
        await golem.finish()
        await golem.control.pick_arm(trial.id, "neither", "Both missed the point")
        await golem.finish()
        assert shipping(golem)[-2:] == ["close PR #2: Closed: you picked neither.", "close PR #3: Closed: you picked neither."]
        assert golem.workspace.closed == [("trees", golem.store.trial(trial.id).issue, "Neither arm of trial 2 was picked.\nBoth missed the point")]
        assert len(golem.deployer.deploys) == 1

    run(go())


class ArmGate(JobGate):
    async def free_models(self) -> list[str]:
        return ["opencode/fake-free"]


def test_a_trials_arms_run_beside_each_other_and_other_work_in_slots_of_their_own(golem):
    async def go():
        trees(golem)  # one change at a time, on one worker
        trial, (a, b) = await a_trial(golem)
        birds = a_change(golem, "birds")
        gate = ArmGate((a.id, "change"), (b.id, "change"), (birds.id, "change"))
        golem.scheduler.harness = golem.scheduler.agents["opencode"] = gate
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: {(a.id, "change"), (b.id, "change"), (birds.id, "change")} <= set(gate.started))
        assert len(golem.scheduler.running) == 3 and golem.scheduler.counted() == 1  # the arms beside the one worker's
        more = golem.store.add_job("trees", "change", "bigger cards", "feature")
        assert turns.behind(golem.store, more) is None and turns.wants(golem.store, golem.store.job(a.id)) is None  # nothing waits for them
        assert golem.scheduler.pick(full=True) is None  # the worker's slot is taken: it waits for that, not for the arms
        loop.cancel()

    run(go())


def test_a_question_has_nothing_to_compare(golem):
    async def go():
        trees(golem)
        golem.harness.script = by_label({"request": [{"workflow": "question", "title": "Why Slow"}]})
        job = await golem.bot.ask(golem.store.project("trees"), "why is it slow?", compare="opencode-free")
        assert job.trial is None and job.start == "look" and golem.store.trials() == []

    run(go())


def test_an_arms_copy_waits_at_a_pick_that_goes_back_to_its_last_push_to_ship(tmp_path):
    library = workflows.Library(Store(tmp_path / "golem.db"), REPO / "workflows", "secret")
    snap = trials.with_pick(library.pin(library.get("change"), "change"))
    assert snap["edges"]["ship/deploy"]["pass"] == "pick" and snap["edges"]["pick"] == {"end": "end", "ship": "ship/push"}
    assert engine.happy_path(snap)[-3:] == ["ship/deploy", "pick", "end"]
    with pytest.raises(engine.GraphError, match="only a workflow that ships"):
        trials.with_pick(library.pin(library.get("question"), "look"))
    with pytest.raises(ValueError, match="unknown block"):  # only golem puts a pick in
        workflows._block("pick", {})


@pytest.fixture
def app(golem):
    return TestClient(create_app(golem.settings, golem.store, golem.scheduler, golem.bot), base_url="https://golem.unlaunched.dev")


def sign_in(client) -> None:
    assert client.post("/login", data={"password": "pw", "back": "/"}, follow_redirects=False).status_code == 303


def test_a_trial_starts_from_the_request_box_and_is_picked_on_its_page(golem, app):
    trees(golem)
    sign_in(app)
    assert "Compare with <select name=compare><option value=''>Nothing</option>" in app.get("/p/trees").text
    golem.harness.script = by_label({"request": [{"workflow": "change", "title": "Quiz Mode"}]})
    r = app.post("/p/trees/ask", data={"text": REQUEST, "mode": "auto", "compare": "opencode-free"}, follow_redirects=False)
    [trial] = golem.store.trials()
    a, b = golem.store.arms(trial.id)
    assert r.headers["location"] == f"/trials/{trial.id}?msg=" + quote(f"Started trial {trial.id}: jobs #{a.id} and #{b.id}.")
    again = app.post("/p/trees/ask", data={"text": "x", "compare": "opencode-free"}, follow_redirects=False).headers["location"]
    assert "waiting%20for%20your%20pick" in again and len(golem.store.trials()) == 1  # one at a time on a project
    assert "no%20mode" in app.post("/p/trees/ask", data={"text": "x", "compare": "nope"}, follow_redirects=False).headers["location"]

    html = app.get(f"/trials/{trial.id}").text
    assert "A · Claude power" in html and "B · OpenCode free" in html and "under way" in html and REQUEST in html
    assert "Pick one" in html and "class=primary disabled>Pick A</button>" in html and f"/trials/{trial.id}/csv" in html
    assert "Trial 1: Quiz Mode" in app.get("/trials").text and "A: Claude power vs B: OpenCode free" in app.get("/trials").text
    assert f"<a href='/trials/{trial.id}'>trial 1, arm B</a>" in app.get(f"/p/trees/jobs/{b.id}").text
    assert "href=/trials>Trials ›</a>" in app.get("/settings").text

    asyncio.run(golem.finish())
    html = app.get(f"/trials/{trial.id}").text
    assert "ready" in html and "class=primary>Pick A</button>" in html and "class=primary>Pick B</button>" in html
    assert "Compare them" in app.get(f"/p/trees/jobs/{a.id}").text  # what the arm's pane waits for
    location = app.post("/act", data={"action": "trial-pick", "trial": trial.id, "value": "b", "note": "Free did fine",
                                      "back": f"/trials/{trial.id}"}, follow_redirects=False).headers["location"]  # fmt: skip
    assert "you%20picked%20arm%20B" in location
    html = app.get(f"/trials/{trial.id}").text
    assert "You picked arm B on" in html and "Free did fine" in html and "Pick one" not in html

    csv = app.get(f"/trials/{trial.id}/csv")
    assert csv.headers["content-type"].startswith("text/csv") and "\njob,A,trees,1," in csv.text and "\njob,B,trees,1," in csv.text
    app.cookies.clear()
    assert "Sign%20in" in app.get(f"/trials/{trial.id}/csv", follow_redirects=False).headers["location"]
    assert "Pick one" not in app.get(f"/trials/{trial.id}").text


def test_a_trials_arm_shows_where_it_is_on_its_job_page(golem):
    async def go():
        trees(golem)
        trial, (a, b) = await a_trial(golem)
        assert [plan.label(s) for s in plan.planned(golem.settings, golem.store, a)][-3:] == ["deploy", "trial pick", "done"]  # before it starts, too
        await golem.finish()
        assert plan.label(current(golem, a.id)) == "trial pick"

    run(go())
