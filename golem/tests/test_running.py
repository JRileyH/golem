"""Jobs walking workflows: blocks that choose their way on, the loop guard, and choosing a workflow."""

from __future__ import annotations

import asyncio

from conftest import add_workflow
from test_flow import by_label, idle_project, new_project, verdict

from golem import plan, workflows

def run(coro):
    return asyncio.run(coro)


def change_job(golem, key: str, request: str = "do the thing"):
    job = golem.store.add_job("tree-cards", "change", request, "feature")
    return golem.store.update_job(job.id, workflow=key)


def trail(golem, job_id: int) -> list[str]:
    """Each step that ran: its label, and its visit when it came round again."""
    return [plan.label(s) + (f"#{s.visit}" if s.visit > 1 else "") + ("✗" if s.state == "failed" else "")
            for s in plan.shown(golem.store.steps(job_id)) if s.state in ("done", "failed")]  # fmt: skip


# --- blocks that choose ------------------------------------------------------------------

JUDGED = [
    "setup",
    {"agent": "build", "turns": 10},
    {"agent": "judge", "turns": 10, "turn": "none", "fail": "tries",
     "outputs": [{"name": "pass", "about": "it does what was asked"}, {"name": "fail", "about": "it doesn't yet"}]},
    {"use": "ship"},
    "end",
    {"counter": "tries"},
    {"condition": "again", "all": [["counter", "tries", "<", 5]], "true": "build", "false": "ship"},
]  # fmt: skip
JUDGED_MD = "Text for every block.\n\n## build\n\nBuild it.\n\n## judge\n\nSay whether it works."


def test_an_agent_with_several_outputs_chooses_the_way_on(own):
    golem = own

    async def go():
        await idle_project(golem)
        add_workflow(golem, "judged", JUDGED, JUDGED_MD)
        job = change_job(golem, "judged")
        golem.harness.script = by_label({"judge": [verdict("fail", "The score is still wrong."), verdict("pass", "Works now.")]})
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        assert trail(golem, job.id)[:6] == ["set up", "build", "judge", "build#2", "judge#2", "push"]
        judge = next(c for c in golem.harness.calls if c.label == "judge")
        assert judge.schema["properties"]["verdict"]["enum"] == ["pass", "fail", "blocked"]
        assert "## How this step ends" in judge.prompt and "- `fail`: it doesn't yet" in judge.prompt
        build = next(c for c in golem.harness.calls if c.label == "build")
        assert "After your step: judge." in build.prompt and "What comes next depends on the verdict you choose" in judge.prompt
        assert "# Quality bar" not in build.prompt  # a workflow that doesn't build for the web: no web-app rules
        assert golem.store.job(job.id).graph  # finished: its copy stays, its record of how it went

    run(go())


def test_a_job_that_goes_round_too_often_asks_before_going_on(own):
    golem = own

    async def go():
        room = await idle_project(golem)
        golem.store.put("guard_visits", "2")
        add_workflow(golem, "judged", JUDGED, JUDGED_MD)
        job = change_job(golem, "judged", "make the score right")
        golem.harness.script = by_label({"judge": [verdict("fail")] * 9})
        for _ in range(12):
            if golem.store.job(job.id).state == "waiting":
                break
            await golem.step()
        assert golem.store.job(job.id).state == "waiting"
        assert golem.chat.texts(room)[-1] == (
            "make the score right has come round to build 3 times (4 agent runs so far), more than I'll go without asking. "
            "Reply to let it carry on (with a hint, if you have one), or stop the job."
        )
        assert [c.label for c in golem.harness.calls].count("build") == 2  # it asked before the third
        [incident] = golem.store.reports()  # golem's own report about it, for its triage
        assert (incident.source, incident.kind, incident.impact, incident.label) == ("golem", "friction", "blocked", "build")
        assert incident.summary == "The loop guard stopped a job at build: 3 visits, 4 agent runs"
        assert "- judge (done)" in incident.detail
        await golem.reply(room, "try a different approach")
        await golem.step()
        build = [c for c in golem.harness.calls if c.label == "build"]
        assert len(build) == 3 and "> try a different approach" in build[-1].prompt

    run(go())


# --- choosing workflows ----------------------------------------------------------------


def test_a_request_can_pick_its_workflow(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "options", "title": "Typo Fixes"}]})
        job = await golem.ask(room, "fix the typo on the about page\nand the one in the footer", workflow="change")
        assert (job.workflow, job.title) == ("change", "Typo Fixes")  # his picks over the call's
        assert job.request == "fix the typo on the about page\nand the one in the footer"  # his lines kept
        ask = next(c for c in golem.harness.calls if c.label == "request")
        assert ask.schema["properties"]["workflow"]["enum"] == ["change", "design", "options", "question"]
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        asked = await golem.ask(room, "how big are the cards?", workflow="question")
        assert (asked.workflow, asked.start) == ("question", "look")
        golem.harness.script = by_label({"request": [{"workflow": "question"}]})
        odd = await golem.ask(room, "make a thing", workflow="web_app")  # not one for a request: the call's pick
        assert odd.workflow == "question"

    run(go())


# --- each job's own activity, and one change at a time ------------------------------------


def test_what_golem_says_about_a_job_is_that_jobs_activity(golem):
    async def go():
        await new_project(golem, [verdict("done", "MVP plan."), verdict("done"), verdict("done", "Try the quiz.")])
        await golem.until("your check-in")
        await golem.step()
        job = golem.store.jobs()[0]
        said = golem.store.job_messages(job.id)
        assert any(m.text.startswith("✓ spec: MVP plan.") for m in said)
        checkin = plan.current(golem.store.steps(job.id))
        asked = said[-1]
        assert asked.text.startswith("Try the quiz.") and asked.step_id == checkin.id and asked.notify
        assert all(m.job_id == job.id for m in said)
        # Each step that touched the job's tree says where its branch was after it.
        assert all(s.head for s in golem.store.steps(job.id) if s.state == "done" and s.kind in ("setup", "agent", "push"))

    run(go())


def test_a_second_change_waits_for_the_first_on_a_project_that_takes_one_at_a_time(golem):
    async def go():
        await idle_project(golem)
        first = change_job(golem, "change", "make the cards bigger")
        second = change_job(golem, "change", "add a timer")
        await golem.step()  # the first's setup
        golem.store.update_job(first.id, state="waiting")  # asking Riggy something
        assert golem.scheduler.pick(at="9999") is None  # the second waits its turn, in order
        from golem import turns

        assert turns.waiting_for(golem.store, golem.store.job(second.id)) == f"waiting for job {first.id} to finish (it's waiting on you)"
        golem.store.update_job(first.id, state="done")
        assert golem.scheduler.pick(at="9999").id == second.id

    run(go())


# --- a job drawn small (route.py) ------------------------------------------------------------

from golem import route  # noqa: E402


def test_a_jobs_route_is_its_blocks_in_order_loops_once_and_containers_as_one(own):
    golem = own

    async def go():
        await idle_project(golem)
        add_workflow(golem, "judged", JUDGED, JUDGED_MD)
        job = change_job(golem, "judged")
        golem.harness.script = by_label({"judge": [verdict("fail", "Not yet."), verdict("pass", "Works.")]})
        await golem.until("push")
        job = golem.store.job(job.id)
        chips = route.chips(job, plan.planned(golem.settings, golem.store, job))
        assert [(c.label, c.state, c.visits) for c in chips] == [
            ("set up", "done", 1), ("build", "done", 2), ("judge", "done", 2), ("Ship", "next", 1),
        ]  # counters and conditions left out; the end's alert too
        ship = chips[-1]
        assert ship.container and ship.fill == 0 and ship.last.name == "push"
        await golem.step()  # pushed
        ship = route.chips(golem.store.job(job.id), plan.planned(golem.settings, golem.store, golem.store.job(job.id)))[-1]
        assert 0 < ship.fill < 1 and ship.state == "next"

    run(go())


def test_a_job_not_started_is_all_still_to_come(golem):
    golem.store.add_project("trees", "Trees", "s")
    job = golem.store.add_job("trees", "new", "idea", "standard")
    chips = route.chips(job, plan.planned(golem.settings, golem.store, job))
    assert chips[0].label == "set up" and {c.state for c in chips} == {"todo"} and all(c.predicted for c in chips)
    assert [c.label for c in chips].count("Ship") == 4  # golem's Ship sub-workflow, four times, each one chip
