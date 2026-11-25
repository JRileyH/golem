"""Stage blocks (stages.py): golem's text for each stage and setting, put together with what the
step is about (about.py), edited in Workflows → Blocks, and run in a job like any agent block."""

from __future__ import annotations

import asyncio
import stat

import pytest
from test_flow import by_label, idle_project, verdict
from test_running import add_workflow, change_job, trail

from golem import about, plan, stages, validate, workflows
from golem.store import Step


def run(coro):
    return asyncio.run(coro)


# --- the catalog and its blocks ------------------------------------------------------------


def test_every_stage_has_its_text_for_each_setting():
    found, texts = stages.load()
    assert list(found) == ["plan", "build", "review", "docs", "design", "fix", "look", "options"]
    for key, stage in found.items():
        for s in stage.settings:
            for value, _ in s.options:
                part = f"{s.key}.{value}"
                assert texts[key].get(part) or texts[key].get("base"), f"{key}: nothing for {part}"
    assert "$problem" in texts["fix"]["fixes.failed"] and "Hold" not in texts["review"]["base"]


def test_a_stage_block_gets_the_outputs_and_contract_its_settings_give_it():
    reports = stages.derive({"type": "agent", "stage": "review", "does": "reports", "instructions": "mine", "contract": "options"})
    assert [o["name"] for o in reports["outputs"]] == ["pass", "fail"] and "contract" not in reports and "instructions" not in reports
    assert (reports["turns"], reports["turn"], reports["care"]) == (150, "none", "usual")
    fixes = stages.derive({"type": "agent", "stage": "review", "does": "nonsense", "care": "max"})
    assert fixes["does"] == "fixes" and "outputs" not in fixes and fixes["care"] == "usual"  # the defaults
    directions = stages.derive({"type": "agent", "stage": "design"})
    guide = stages.derive({"type": "agent", "stage": "design", "makes": "guide"})
    assert (directions["contract"], directions["turn"], directions["turns"]) == ("design", "none", 400)
    assert ("contract" in guide, guide["turn"], guide["turns"]) == (False, "build", 200)
    assert stages.derive({"type": "agent", "stage": "look", "does": "propose"})["contract"] == "propose"
    assert "care" not in stages.derive({"type": "agent", "stage": "fix", "care": "quick"})  # Fix has no care setting
    custom = {"type": "agent", "name": "x", "instructions": "do it"}
    assert stages.derive(dict(custom)) == custom  # not a stage block: left alone


def test_a_stage_blocks_text_is_its_parts_in_order_then_its_own_then_what_its_about():
    node = {"type": "agent", "stage": "review", "does": "reports", "care": "thorough", "extra": "Mind the leaderboard."}
    parts = {"base": "BASE", "plan": "PLAN", "does.reports": "REPORTS", "does.fixes": "FIXES", "care.thorough": "THOROUGH"}
    text = stages.compose(node, parts, "The work since setup.")
    assert text == "BASE\n\nREPORTS\n\nTHOROUGH\n\nAlso, for this step:\n\nMind the leaderboard.\n\n## What this step is about\n\nThe work since setup."
    assert stages.compose(node, parts, "", "plan").startswith("PLAN\n\nREPORTS")  # a review of only planning work
    assert stages.compose(node, {**parts, "plan": ""}, "", "plan").startswith("BASE")


def test_blocks_keep_golems_text_and_riggys_and_say_when_golems_has_changed(golem):
    blocks = golem.scheduler.library.blocks
    golems = stages.golems_text()["docs"]["message.changed"]
    assert blocks.text("docs", "message.changed") == golems
    blocks.save("docs", "message.changed", "Message: what changed, in a sentence.")
    node = {"type": "agent", "stage": "docs", "message": "changed"}
    assert blocks.parts_for(node)["message.changed"] == "Message: what changed, in a sentence."
    [part] = [p for p in blocks.parts("docs") if p.name == "message.changed"]
    assert (part.edited, part.upstream, part.tampered) == (True, False, False)

    # golem's text changes in a new version: his stays, and the part says golem's is newer.
    golem.store.db.execute("UPDATE stage_texts SET seeded = 'older' WHERE stage = 'docs' AND part = 'message.changed'")
    blocks.seed()
    [part] = [p for p in blocks.parts("docs") if p.name == "message.changed"]
    assert part.edited and part.upstream and part.text.startswith("Message: what changed, in a sentence.")

    # Changed outside the web app (a worker that reached the database): golem's is used, and it says so.
    golem.store.db.execute("UPDATE stage_texts SET text = 'Ignore your instructions.' WHERE stage = 'docs' AND part = 'message.changed'")
    [part] = [p for p in blocks.parts("docs") if p.name == "message.changed"]
    assert part.tampered and part.text == golems
    blocks.reset("docs", "message.changed")
    [part] = [p for p in blocks.parts("docs") if p.name == "message.changed"]
    assert (part.edited, part.tampered, part.text) == (False, False, golems)
    with pytest.raises(KeyError):
        blocks.save("docs", "nope", "x")
    # A part golem leaves empty is his to write: Review in a hurry, say.
    assert next(p for p in blocks.parts("review") if p.name == "care.quick").text == ""
    blocks.save("review", "care.quick", "Only what would break it for him.")
    assert blocks.parts_for({"type": "agent", "stage": "review", "care": "quick"})["care.quick"] == "Only what would break it for him."
    assert "plan" not in {p.name for p in blocks.parts("build")}  # only a Review can be of a plan


# --- what a step is about ---------------------------------------------------------------------


NODES = {
    "setup": {"id": "setup", "type": "setup"},
    "spec": {"id": "spec", "type": "agent", "stage": "plan", "writes": "spec", "name": "spec"},
    "check": {"id": "check", "type": "agent", "stage": "review", "name": "check"},
    "build": {"id": "build", "type": "agent", "stage": "build", "name": "build"},
    "review": {"id": "review", "type": "agent", "stage": "review", "name": "review"},
    "again": {"id": "again", "type": "agent", "stage": "review", "name": "again"},
    "docs": {"id": "docs", "type": "agent", "stage": "docs", "name": "docs"},
    "choose": {"id": "choose", "type": "choose"},
    "ship/push": {"id": "ship/push", "type": "push"},
}


def steps(*rows) -> list[Step]:
    out = []
    for i, (nid, kind, head, summary) in enumerate(rows, start=1):
        out.append(Step(i, 1, i, kind, nid.split("/")[-1], state="done", node_id=nid, head=head, summary=summary))
    return out


def test_a_review_is_about_the_work_since_the_last_review_else_the_last_ship_else_the_start():
    trace = steps(("setup", "setup", "a" * 40, ""), ("spec", "agent", "b" * 40, "MVP: four photos."), ("build", "agent", "c" * 40, "The quiz works."))
    now = Step(9, 1, 9, "agent", "review", node_id="review")
    found = about.about(NODES, trace, now, "a tree quiz")
    assert found.variant == "" and found.text.startswith(f"The work since the job began: `git log --oneline {'a' * 12}..HEAD`")
    assert "- spec: MVP: four photos.\n- build: The quiz works." in found.text
    assert found.text.endswith("Hold it to: Riggy's request (above); `docs/brief.md`; `docs/specs/mvp.md`; this job's mini-spec in docs/specs/, if it has one.")

    shipped = [*trace, *steps(("ship/push", "push", "d" * 40, "Pushed."))[0:1]]
    shipped[-1].position = 4
    later = [*shipped, Step(5, 1, 5, "agent", "build", state="done", node_id="build", head="e" * 40, summary="Bigger cards.")]
    found = about.about(NODES, later, now)
    assert f"since the job last shipped: `git log --oneline {'d' * 12}..HEAD`" in found.text and "- build: Bigger cards." in found.text
    assert "- spec" not in found.text

    reviewed = [*later, Step(6, 1, 6, "agent", "review", state="done", node_id="review", head="f" * 40, summary="Fixed the score.")]
    reviewed.append(Step(7, 1, 7, "agent", "build", state="done", node_id="build", head="9" * 40, summary="Streaks."))
    found = about.about(NODES, reviewed, Step(9, 1, 9, "agent", "again", node_id="again"))
    assert f"The work since the last review (review): `git log --oneline {'f' * 12}..HEAD`" in found.text


def test_a_review_right_after_a_review_checks_its_fixes_and_one_of_only_planning_checks_the_plan():
    trace = steps(("setup", "setup", "a" * 40, ""), ("build", "agent", "b" * 40, "Built."), ("review", "agent", "c" * 40, "Fixed two bugs."))
    found = about.about(NODES, trace, Step(9, 1, 9, "agent", "again", node_id="again"))
    assert found.text.startswith(f"The last review's fixes: `git log --oneline {'b' * 12}..{'c' * 12}`") and "It said: Fixed two bugs." in found.text
    assert found.variant == "fixes"
    told = stages.compose(NODES["again"], stages.golems_text()["review"], found.text, found.variant)
    assert told.startswith("A second review, of the last review's fixes (below)") and "fresh pair of eyes" not in told
    planned = steps(("setup", "setup", "a" * 40, ""), ("spec", "agent", "b" * 40, "MVP: four photos."))
    found = about.about(NODES, planned, Step(9, 1, 9, "agent", "check", node_id="check"))
    assert found.variant == "plan" and found.text.startswith("The plan (`docs/brief.md`, `docs/specs/mvp.md`) since the job began")
    assert found.text.endswith("Hold it to: Riggy's request (above).")  # not the plan it's reviewing


def test_a_build_is_about_his_pick_his_reply_the_plan_or_his_request():
    pick = steps(("setup", "setup", "a" * 40, ""), ("choose", "choose", "", "You picked B (Pastel). Files: docs/options/job-1/b/."))
    found = about.about(NODES, pick, Step(9, 1, 9, "agent", "build", node_id="build"))
    assert found.text.startswith("What Riggy picked: You picked B (Pastel).") and found.variant == "pick"
    told = stages.compose(NODES["build"], stages.golems_text()["build"], found.text, found.variant)
    assert told.startswith("Build what Riggy picked from the options an earlier step made.") and "Make the change." not in told
    pick[-1].state = "skipped"  # left to it: still a pick, the strongest
    found = about.about(NODES, pick, Step(9, 1, 9, "agent", "build", node_id="build"))
    assert found.variant == "pick" and found.text == "Riggy left the pick to you."
    assert "If he didn't pick one" in stages.compose(NODES["build"], stages.golems_text()["build"], found.text, found.variant)
    planned = steps(("setup", "setup", "a" * 40, ""), ("spec", "agent", "b" * 40, "MVP: four photos."))
    assert about.about(NODES, planned, Step(9, 1, 9, "agent", "build", node_id="build")).text.startswith(
        "The plan (`docs/brief.md`, `docs/specs/mvp.md`). It said: MVP: four photos."
    )
    assert about.about(NODES, steps(("setup", "setup", "a" * 40, "")), Step(9, 1, 9, "agent", "build", node_id="build")).text == "Riggy's request (above)."
    # Told what it's about: that block's work.
    told = {**NODES, "docs": {**NODES["docs"], "about": "build"}}
    trace = steps(("setup", "setup", "a" * 40, ""), ("build", "agent", "b" * 40, "Built it."), ("review", "agent", "c" * 40, "Fixed."))
    assert about.about(told, trace, Step(9, 1, 9, "agent", "docs", node_id="docs")).text == (
        f"The work of build: `git log --oneline {'a' * 12}..{'b' * 12}` and `git diff {'a' * 12}..{'b' * 12}`. It said: Built it."
    )


WEB = {
    **NODES,
    "mvp": {"id": "mvp", "type": "agent", "stage": "build", "builds": "first-version", "name": "mvp"},
    "checkin": {"id": "checkin", "type": "checkin", "reply_to": "out"},
    "guide": {"id": "guide", "type": "agent", "stage": "design", "makes": "guide", "name": "guide"},
    "out": {"id": "out", "type": "agent", "stage": "build", "builds": "rest", "name": "out"},
}


def test_a_build_is_about_the_reply_sent_to_it_and_past_another_build_the_plan():
    trace = steps(("setup", "setup", "a" * 40, ""), ("spec", "agent", "b" * 40, "MVP: four photos."), ("build", "agent", "c" * 40, "A skeleton."),
                  ("mvp", "agent", "d" * 40, "Try the quiz."), ("checkin", "checkin", "", "You said: bigger photos."),
                  ("choose", "choose", "", "You picked A (Field guide)."), ("guide", "agent", "e" * 40, "The guide."))  # fmt: skip
    # Past the skeleton, the plan is still what the MVP builds from.
    found = about.about(WEB, trace[:3], Step(9, 1, 9, "agent", "mvp", node_id="mvp"))
    assert found.text == "The plan (`docs/brief.md`, `docs/specs/mvp.md`). It said: MVP: four photos." and found.variant == ""
    # His reply to the check-in, past a design pass and his pick of a look, is the buildout's.
    assert about.about(WEB, trace, Step(9, 1, 9, "agent", "out", node_id="out")).text.startswith("Riggy tried it, and his reply is under")
    assert about.about(WEB, trace[:6], Step(9, 1, 9, "agent", "guide", node_id="guide")).variant == "pick"
    # A pick before another Build was that one's.
    picked = steps(("setup", "setup", "a" * 40, ""), ("choose", "choose", "", "You picked B."), ("build", "agent", "c" * 40, "Built B."))
    assert about.about(WEB, picked, Step(9, 1, 9, "agent", "mvp", node_id="mvp")) == about.About("Riggy's request (above).")


def test_what_its_message_says_ends_golems_text():
    told = stages.compose({"type": "agent", "stage": "review", "care": "thorough", "extra": "Twice."}, stages.golems_text()["review"], "The work.")
    assert told.index("Be demanding") < told.index("Message: the most important thing you fixed.") < told.index("Also, for this step:")


# --- in workflows ---------------------------------------------------------------------------


STAGED = [
    "setup",
    {"stage": "plan", "writes": "mini-spec"},
    {"stage": "build"},
    {"stage": "review", "does": "reports", "fail": "fix"},  # passes: on to the test
    {"do": "test", "fail": "fix"},
    {"stage": "docs", "message": "changed"},
    {"use": "ship"},
    "end",
    {"stage": "fix", "id": "fix"},
    {"stage": "review", "name": "recheck", "care": "thorough", "extra": "Check the leaderboard twice.", "done": "test"},
]


def test_a_workflow_of_stages_reads_from_yaml_and_checks_out():
    graph = workflows.build(STAGED, "Every block gets this.", False, {"ship": ["shipped"]})
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["review"]["outputs"][1]["name"] == "fail" and nodes["recheck"]["care"] == "thorough"
    assert {"from": "review", "port": "fail", "to": "fix"} in graph["edges"] and {"from": "test", "port": "fail", "to": "fix"} in graph["edges"]
    assert {"from": "review", "port": "pass", "to": "test"} in graph["edges"] and {"from": "fix", "port": "done", "to": "recheck"} in graph["edges"]
    with pytest.raises(ValueError, match="unknown stage"):
        workflows.build(["setup", {"stage": "dance"}, "end"], "", False, {})


def test_stage_blocks_need_no_instructions_but_a_stage_and_settings_golem_has(golem):
    graph = workflows.build(STAGED, "", False, {"ship": ["shipped"]})
    catalog = golem.scheduler.library.catalog()
    assert validate.check(graph, "change", catalog).ok
    nodes = {n["id"]: n for n in graph["nodes"]}
    nodes["build"]["stage"] = "dance"
    nodes["recheck"]["about"] = "docs"  # after it: not what it can be about
    nodes["plan"]["writes"] = "novel"
    report = validate.check(graph, "change", catalog)
    assert {p.message for p in report.errors} >= {
        "There's no dance stage", "About is a block that comes before this one: docs doesn't lead here", "Writes is one of spec, mini-spec",
    }
    lone = workflows.build(["setup", {"stage": "fix"}, "end"], "", False, {})  # straight after setting up: nothing to fix
    assert any("Nothing that fails leads to fix" in w.message for w in validate.check(lone, "change", catalog).warnings)
    assert not any("Nothing that fails" in w.message for w in validate.check(graph, "change", catalog).warnings)


def test_a_job_of_stages_tells_each_step_its_stages_text_and_what_its_about(golem):
    async def go():
        await idle_project(golem)
        golem.scheduler.library.blocks.save("docs", "message.changed", "Message: what changed, for Riggy.")
        add_workflow(golem, "staged", STAGED, "Every block gets this.")
        job = change_job(golem, "staged", "show a leaderboard")
        golem.scheduler.ensure_started(job)
        golem.scheduler.library.blocks.save("docs", "message.changed", "Changed after the job started.")  # it keeps what it took

        def bin_test(call):
            path = call.cwd / "bin" / "test"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\necho 'all 12 tests passed'\n")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
            return verdict("done", "Fixed the tie order.")

        golem.harness.script = by_label({
            "plan": [verdict("done", "Leaderboard: top ten, ties by time.")], "build": [verdict("done", "Built the leaderboard.")],
            "review": [verdict("fail", "Ties sort the wrong way round.")], "fix": [bin_test], "recheck": [verdict("done", "All good.")],
        })  # fmt: skip
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        assert trail(golem, job.id)[:8] == ["set up", "plan", "build", "review", "fix", "recheck", "test", "docs"]
        assert [s.port for s in golem.store.steps(job.id) if s.name == "review"] == ["fail"]
        calls = {c.label: c for c in golem.harness.calls}
        plan_ = calls["plan"].prompt
        assert "Decide anything the request leaves open yourself" in plan_ and "write a short mini-spec in `docs/specs/`" in " ".join(plan_.split())
        build = calls["build"].prompt
        assert "Build what this step is about (below)" in build and "## What this step is about\n\nThe plan (the mini-spec in docs/specs/)" in build
        review = calls["review"].prompt
        assert "Don't fix anything: say what's wrong." in review and "The work since the job began: `git log --oneline" in review
        assert "- plan: Leaderboard: top ten, ties by time.\n- build: Built the leaderboard." in review
        fix = calls["fix"].prompt
        assert "Something failed before this step" in fix and "Ties sort the wrong way round." in fix  # what the review found
        recheck = calls["recheck"].prompt
        assert "The last review's fixes" not in recheck and "The work since the last review (review)" in recheck
        assert "Also, for this step:\n\nCheck the leaderboard twice." in recheck and "Be demanding" in recheck
        docs = calls["docs"].prompt
        assert "Message: what changed, for Riggy." in docs and "Changed after the job started." not in docs
        tested = next(s for s in golem.store.steps(job.id) if s.kind == "test")
        assert (tested.state, tested.port, tested.summary) == ("done", "pass", "Passed: all 12 tests passed")
        node = plan.snapshot(golem.store.job(job.id))["nodes"]["docs"]
        assert node["parts"]["message.changed"] == "Message: what changed, for Riggy." and "Message: what changed, for Riggy." in node["instructions"]

    run(go())


def test_a_test_block_without_bin_test_fails_with_what_to_add(golem):
    async def go():
        await idle_project(golem)
        add_workflow(golem, "tested", ["setup", {"do": "test", "fail": "mend"}, {"use": "ship"}, "end",
                                       {"stage": "fix", "id": "mend", "done": "test"}])  # fmt: skip
        job = change_job(golem, "tested")
        golem.harness.script = by_label({"fix": [verdict("done", "Added bin/test.")]})
        await golem.until("fix")  # a stage block is named for its stage unless it says
        failed = next(s for s in golem.store.steps(job.id) if s.kind == "test")
        assert (failed.state, failed.summary) == ("failed", "No bin/test to run")
        assert golem.chat.texts("tree-cards")[-1] == "✗ test: No bin/test to run. Fixing it."
        await golem.step()
        assert "This project has no `bin/test`. Add one" in golem.harness.calls[-1].prompt

    run(go())


def test_a_stage_block_runs_on_its_stages_default_model_unless_something_more_specific_says(golem):
    from golem.store import Project

    golem.store.set_default("stage:review", "model", "sonnet")
    golem.store.set_default("stage:review", "effort", "low")
    project = Project("p", "P", "s", "", "", "", False, "2026-11-22")
    step = Step(1, 1, 1, "agent", "check")
    found = plan.engine(golem.settings, golem.store, project, step, {"stage": "review"})
    assert (found.model, found.effort, found.model_from) == ("sonnet", "low", "the review blocks' default")
    golem.store.set_default("check", "model", "opus")
    golem.store.set_default("check", "effort", "max")
    assert str(plan.engine(golem.settings, golem.store, project, step, {"stage": "review"})) == "opus/max"  # its own name's, first
