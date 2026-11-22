"""Jobs walking workflows: blocks that choose their way on, script blocks, the loop guard, and
jobs from before workflows carrying on as workflows of their own (legacy.py)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from test_flow import by_label, idle_project, new_project, three_styles, verdict

from golem import plan, workflows
from golem.harness import RunResult
from golem.store import Step
from golem.workspace import CI

GOLDEN = json.loads((Path(__file__).parent / "golden" / "playbooks.json").read_text())


def run(coro):
    return asyncio.run(coro)


def add_workflow(golem, key: str, blocks: list, md: str = "", start: str = "change"):
    """A workflow of Riggy's, saved as the editor saves one: valid and signed."""
    subs = {wf.key: workflows.engine.outputs_of(golem.store.tab(wf.id, "").working) for wf in golem.store.workflows() if wf.start == "sub"}
    graph = workflows.build(blocks, md, start == "sub", subs)
    wf = golem.store.add_workflow(key, key, key.replace("_", " ").title(), start, "For tests.")
    golem.store.put_tab(wf.id, "", graph, graph, [], golem.scheduler.library.sign(graph))
    return wf


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
    {"use": "ship", "tab": "default"},
    "end",
    {"counter": "tries"},
    {"condition": "again", "all": [["counter", "tries", "<", 5]], "true": "build", "false": "ship"},
]  # fmt: skip
JUDGED_MD = "Text for every block.\n\n## build\n\nBuild it.\n\n## judge\n\nSay whether it works."


def test_an_agent_with_several_outputs_chooses_the_way_on(golem):
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
        assert golem.store.job(job.id).graph == ""  # finished: its copy is gone

    run(go())


def test_a_job_that_goes_round_too_often_asks_before_going_on(golem):
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
            "Reply to let it carry on (with a hint, if you have one), or `!kill` to stop it."
        )
        assert [c.label for c in golem.harness.calls].count("build") == 2  # it asked before the third
        await golem.say(room, "try a different approach")
        await golem.step()
        build = [c for c in golem.harness.calls if c.label == "build"]
        assert len(build) == 3 and "> try a different approach" in build[-1].prompt

    run(go())


# --- script blocks ----------------------------------------------------------------------

TESTED = [
    "setup",
    {"script": "tests", "command": "test -f ok.txt || (echo 'npm test: ok.txt is missing'; exit 3)", "fail": "mend"},
    {"use": "ship", "tab": "default"},
    "end",
    {"agent": "mend", "turns": 20, "done": "tests"},
]


def test_a_script_block_passes_on_exit_0_and_its_failure_goes_to_the_next_agent(golem):
    async def go():
        await idle_project(golem)
        add_workflow(golem, "tested", TESTED, "## mend\n\nMake the tests pass.")
        job = change_job(golem, "tested")

        def mend(call) -> dict:
            (call.cwd / "ok.txt").write_text("fixed")
            return verdict("done", "Added the missing file.")

        golem.harness.script = by_label({"mend": [mend]})
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        assert trail(golem, job.id)[:5] == ["set up", "tests✗", "mend", "tests#2", "push"]
        prompt = next(c for c in golem.harness.calls if c.label == "mend").prompt
        assert "## What golem's checks found" in prompt and "npm test: ok.txt is missing" in prompt and "exited 3" in prompt
        failed, passed = [s for s in golem.store.steps(job.id) if s.kind == "script"]
        assert (failed.port, passed.port, passed.visit) == ("fail", "pass", 2)
        log = next(golem.settings.job_logs(job.id).glob("*-tests.log")).read_text()
        assert log.startswith("$ test -f ok.txt") and "ok.txt is missing" in log

    run(go())


# --- jobs from before workflows ---------------------------------------------------------


def as_before(golem, job, book: str, mode: str, extra: list[Step] = ()) -> None:
    """Make a job look as it would in golem's database from before workflows: steps written from
    its playbook (the golden copy), no graph of its own. `extra`: steps golem had put in after
    the current one (a fix and the steps from the last push again)."""
    store = golem.store
    steps = store.steps(job.id)
    specs = GOLDEN[book]["modes"][mode][len(steps) :]
    store.db.execute("UPDATE steps SET node_id = '', port = '', visit = 0 WHERE job_id = ?", (job.id,))
    store.db.execute("UPDATE jobs SET graph = '', workflow = '', mode = '', graph_hash = '', vars = '{}' WHERE id = ?", (job.id,))
    rest = [Step(0, job.id, 0, s["kind"], s["name"], s["turns"] or None, s["optional"]) for s in specs]
    store.add_steps(job.id, [*extra, *rest])


def test_a_job_waiting_at_its_checkin_from_before_workflows_carries_on(golem):
    async def go():
        room = await new_project(golem, [verdict("done", "MVP plan."), verdict("done"), verdict("done", "Try the quiz.")])
        await golem.until("your check-in")
        await golem.step()
        job = golem.store.jobs()[0]
        assert golem.store.job(job.id).state == "waiting"
        as_before(golem, job, "standard", "standard")
        assert plan.snapshot(golem.store.job(job.id)) is None
        await golem.say(room, "looks good but make the photos bigger")
        golem.harness.script = by_label({"design": [{**verdict("done", "Keep it."), "skip": "He likes the look."}]})
        await golem.finish()
        job = golem.store.job(job.id)
        assert job.state == "done"
        prompts = {c.label: c.prompt for c in golem.harness.calls}
        assert "> looks good but make the photos bigger" not in prompts["design"].split("# Riggy's feedback")[1]
        assert "> looks good but make the photos bigger" in prompts["buildout"]
        assert "guide" not in prompts and "apply" not in prompts  # nothing to design: past the rest of the pass
        assert prompts["buildout"].count("Restyle") == 0 and "# Your step: buildout" in prompts["buildout"]

    run(go())


def test_a_job_with_a_fix_pending_from_before_workflows_fixes_and_ships_again(golem):
    async def go():
        await new_project(golem, [verdict("done"), verdict("done")])
        await golem.until("CI")
        job = golem.store.jobs()[0]
        checks = plan.current(golem.store.steps(job.id))
        golem.store.update_step(checks.id, state="failed", error="CI failed on PR #1", summary="CI failed on PR #1", ended_at="now")
        problem = "CI failed on PR #1 (commit abc1234):\nnpm test: 2 failing"
        again = [Step(0, job.id, 0, "agent", "fix", 150, problem=problem), *(Step(0, job.id, 0, k, k) for k in ("push", "pr", "checks"))]
        as_before(golem, job, "standard", "standard", again)
        golem.harness.script = [verdict("done", "Fixed the deck size.")]
        await golem.until("mvp")
        fix = golem.harness.calls[-1]
        assert "# Your step: fix" in fix.prompt and "npm test: 2 failing" in fix.prompt
        assert golem.labels(job.id)[3:] == ["push", "PR", "CI", "fix", "push", "PR", "CI", "merge", "deploy"]
        # And a failure from here goes round the fix loop it was given.
        golem.harness.script = [verdict("done"), verdict("done")]
        await golem.until("CI")
        golem.workspace.ci_results = [CI("failed", "build: failure")]
        await golem.step()
        assert plan.label(plan.current(golem.store.steps(job.id))) == "fix"

    run(go())


def test_a_pick_waiting_from_before_workflows_builds_what_he_picks(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"kind": "options"}], "options": [three_styles], "pick": [{"decision": "pick", "picked": ["B"]}]})
        await golem.say(room, "show me 3 art styles")
        await golem.until("your pick")
        await golem.step()
        job = golem.store.active_job("tree-cards")
        assert job.state == "waiting"
        as_before(golem, job, "options", "standard")
        await golem.say(room, "B please")
        await golem.finish()
        change = next(c for c in golem.harness.calls if c.label == "change")
        assert "You picked B (Pastel)" in change.prompt and golem.store.job(job.id).state == "done"

    run(go())


def test_a_job_from_before_steps_were_tracked_picks_up_at_its_phase(golem):
    golem.store.add_project("trees", "Trees", "s")
    job = golem.store.add_job("trees", "new", "idea", "standard")
    golem.store.update_job(job.id, phase=2)
    golem.store.start_run(job.id, "scaffold", "opus/high", "/nowhere")
    steps = golem.scheduler.ensure_started(golem.store.job(job.id))
    assert plan.label(plan.current(steps)) == "mvp" and plan.snapshot(golem.store.job(job.id))["legacy"]
    ahead = [plan.label(s) for s in plan.planned(golem.settings, golem.store, golem.store.job(job.id)) if s.state == "pending"]
    assert ahead[:3] == ["mvp", "push", "PR"] and ahead[-1] == "done"
