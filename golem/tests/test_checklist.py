"""Checklist mode (checklist.py): a Build plans items with checks, cheaper runs build them, golem checks each."""

from __future__ import annotations

import asyncio
import json
import os

from test_flow import by_label, idle_project, verdict
from test_web import app, new, sign_in  # noqa: F401  (app: the web app fixture)

from golem import checklist, plan, route


def run(coro):
    return asyncio.run(coro)


def planner(items: list[dict], checks: list[str]):
    """A Build in checklist mode: writes its check files and returns its plan."""

    def plan_(call) -> dict:
        (call.cwd / "tests").mkdir(exist_ok=True)
        (call.cwd / "tests" / "items.txt").write_text("a.txt\nb.txt\n")
        return {**verdict("done", "Two small items."), "items": items, "checks": checks}

    return plan_


def builds(name: str = "", tamper: bool = False):
    """An item's build: makes `name` (or nothing), and changes the check file if told to."""

    def build(call) -> dict:
        if name:
            (call.cwd / name).write_text("ok\n")
        if tamper:
            (call.cwd / "tests" / "items.txt").write_text("anything passes\n")
        return verdict("done", f"Made {name or 'nothing'}.")

    return build


ITEMS = [
    {"title": "File A", "do": "Make a.txt.", "files": ["a.txt"], "check": "test -f a.txt"},
    {"title": "File B", "do": "Make b.txt.", "files": ["b.txt"], "check": "test -f b.txt && grep -q '^b.txt$' tests/items.txt"},
]


async def checklist_job(golem, request: str = "add the a and b files"):
    room = await idle_project(golem)
    job = await golem.ask(room, request, workflow="change")
    return golem.store.update_job(job.id, mode=checklist.MODE)


def test_a_build_in_checklist_mode_plans_items_that_cheaper_runs_build_and_golem_checks(golem):
    async def go():
        golem.store.put("guard_runs", "3")  # a checklist's item runs don't count towards the loop guard
        job = await checklist_job(golem)
        golem.harness.script = by_label({
            "change": [planner(ITEMS, ["tests/items.txt", "../outside.txt"])],
            "item": [builds("a.txt"), builds(tamper=True), builds(), builds("b.txt")],
        })  # fmt: skip
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        assert golem.labels(job.id)[:12] == ["set up", "change", "item", "check", "item", "check", "item", "check", "item", "check", "review", "docs"]
        steps = golem.store.steps(job.id)
        checks = [s for s in steps if s.kind == "check"]
        assert [s.state for s in checks] == ["done", "failed", "failed", "done"]
        assert checks[1].summary == "2/2 File B: its check failed (put back tests/items.txt, which it had changed), so it goes again"

        # The planner: the Build's model and text, its "how" swapped for planning, returning items.
        plan_call = next(c for c in golem.harness.calls if c.label == "change")
        assert plan_call.schema == checklist.SCHEMA and "This job is in checklist mode" in plan_call.prompt
        assert "Add or update tests for the paths you touch" not in plan_call.prompt  # the building is the items'
        # The items: the item default, then for an item's last try, the Build's own model.
        built = [c for c in golem.harness.calls if c.label == "item"]
        assert [(c.model, c.effort) for c in built] == [("haiku", "medium")] * 3 + [(plan_call.model, plan_call.effort)]
        assert "Build one item of this step's checklist: **File B**" in built[1].prompt and "→ 2. File B" in built[1].prompt
        assert "What failed last time" not in built[1].prompt and "## What failed last time" in built[2].prompt
        assert "`test -f b.txt" in built[2].prompt and "This is try 2 at this item" in built[2].prompt
        assert [s.summary for s in steps if s.kind == "agent" and s.name == "item"][0] == "1/2 File A: Made a.txt."
        items = [s for s in steps if s.item]
        assert all(s.turn == "build" for s in items) and {s.item["plan"] for s in items} == {next(s.id for s in steps if s.name == "change")}
        # The check files were kept as written, and put back before each check.
        where = checklist.folder(golem.settings.job_logs(job.id), items[0].item["plan"])
        assert json.loads((where / "plan.json").read_text())["checks"] == ["tests/items.txt"]  # outside the checkout: not kept
        assert (plan_call.cwd / "tests" / "items.txt").read_text() == "a.txt\nb.txt\n"
        # Drawn small: the checklist is one chip that fills.
        chips = route.chips(golem.store.job(job.id), plan.planned(golem.settings, golem.store, golem.store.job(job.id)))
        [chip] = [c for c in chips if c.label == "Checklist"]
        assert chip.container and chip.state == "done" and len(chip.steps) == 8

    run(go())


def test_an_item_that_never_passes_is_left_and_the_next_agent_step_is_told(golem):
    async def go():
        job = await checklist_job(golem, "add a file that never comes")
        never = [{"title": "Never", "do": "Make never.txt.", "files": [], "check": "test -f never.txt"}]
        golem.harness.script = by_label({"change": [planner(never, ["tests/items.txt"])]})
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        checks = [s for s in golem.store.steps(job.id) if s.kind == "check"]
        assert [s.item["try"] for s in checks] == [1, 2, 3] and checks[-1].summary == "1/1 Never: its check still fails after 3 tries; carrying on"
        review = next(c for c in golem.harness.calls if c.label == "review")
        assert "## Checklist items that didn't pass" in review.prompt and "1/1 Never (its check: `test -f never.txt`)" in review.prompt
        assert "Fixing it." not in " ".join(golem.chat.texts("tree-cards"))

    run(go())


def test_a_plan_with_no_usable_items_is_tried_again(golem):
    async def go():
        job = await checklist_job(golem)
        golem.harness.script = by_label({"change": [{**verdict("done"), "items": [{"title": "", "do": "x", "files": [], "check": ""}], "checks": []}]})
        await golem.step()  # set up
        await golem.step()
        step = next(s for s in golem.store.steps(job.id) if s.name == "change")
        assert step.state == "pending" and "no items" in step.error and not any(s.item for s in golem.store.steps(job.id))

    run(go())


def test_items_are_built_on_the_item_default_riggy_can_change(golem):
    async def go():
        golem.store.set_default("item", "model", "sonnet")
        job = await checklist_job(golem)
        golem.harness.script = by_label({"change": [planner(ITEMS[:1], [])], "item": [builds("a.txt")]})
        await golem.finish()
        assert [(c.model, c.effort) for c in golem.harness.calls if c.label == "item"] == [("sonnet", "medium")]
        assert golem.store.job(job.id).state == "done"

    run(go())


def test_riggy_picks_checklist_mode_for_a_request_or_a_new_project(golem):
    from test_flow import TREES

    async def go():
        golem.harness.script = [TREES]
        job = await golem.new_project("a flashcard game for trees", mode=checklist.MODE)
        assert job.mode == "checklist" and golem.store.project("tree-cards").mode == ""
        golem.harness.script = by_label({"spec": [verdict("done", "The plan.")], "scaffold": [planner(ITEMS[:1], [])], "item": [builds("a.txt")]})
        await golem.until("item")  # the scaffold, a Build, planned its one item
        assert [s.summary for s in golem.store.steps(job.id) if s.name == "scaffold"] == ["Two small items."]
        await golem.control.setting("tree-cards", "mode", checklist.MODE)
        golem.store.update_job(job.id, state="done")
        asked = await golem.ask("tree-cards", "make it bigger", workflow="change")
        assert asked.mode == "checklist"  # the project's default
        assert (await golem.ask("tree-cards", "why is it slow?", workflow="question", mode="")).mode == ""

    run(go())


def test_an_idea_built_both_ways_is_two_projects_hosted_alike(golem, app):
    from test_flow import TREES

    sign_in(app)
    assert "name=both value=1" in app.get("/new").text
    golem.harness.script = [TREES, TREES]
    r = new(app, name="Tree Cards", slug="trees", idea="a flashcard game for trees", workflow="web_app", both="1", tag="")
    usual, other = golem.store.project("trees"), golem.store.project("cl-trees")
    assert (usual.vacuum, other.vacuum, other.name) == (True, True, "Tree Cards (checklist)")
    jobs = {j.slug: j for j in golem.store.jobs()}
    assert (jobs["trees"].mode, jobs["cl-trees"].mode) == ("", "checklist") and jobs["trees"].request == jobs["cl-trees"].request
    assert "both%20ways" in r.headers["location"] and "cl-trees" in r.headers["location"]
    assert golem.settings.project_url("cl-trees").startswith("https://cl-trees.")  # hosted as any project is
    golem.workspace.repos.add("cl-fish")
    html = new(app, name="Fish", slug="fish", idea="fish", both="1").text
    assert "its checklist one would be at cl-fish, and that&#x27;s taken" in html and golem.store.project("fish") is None
    html = new(app, name="Long", slug="a-very-long-address-of-29-chars", idea="x", both="1").text
    assert "pick a shorter address" in html


def test_built_both_ways_each_runs_in_a_vacuum_as_on_a_fresh_golem(golem):
    from test_flow import TREES

    async def go():
        golem.settings.lessons().write_text("- golem's lesson from before\n")
        golem.harness.script = [TREES, TREES]
        usual, other = await golem.bot.new_twins("Trees", "a flashcard game for trees", slug="trees")
        for slug in ("trees", "cl-trees"):
            assert golem.settings.lessons(slug).read_text() == ""  # nothing golem has learnt
        golem.settings.lessons("cl-trees").write_text("- learnt by the checklist one\n")
        await golem.until("spec")
        await golem.step()
        spec = golem.harness.calls[-1]
        assert "golem's lesson from before" not in spec.prompt and "learnt by the checklist one" not in spec.prompt
        assert str(golem.settings.lessons("trees")) in spec.prompt  # where its own go
        config = golem.settings.job_claude(usual.id)
        assert spec.env["CLAUDE_CONFIG_DIR"] == str(config) and config.is_dir()  # a Claude Code of its own, as freshly installed
        assert spec.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert spec.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1" and spec.env["BASH_DEFAULT_TIMEOUT_MS"] == "300000"
        assert golem.settings.lessons().read_text() == "- golem's lesson from before\n"  # golem's own, untouched
        golem.store.update_job(usual.id, state="done")
        await golem.scheduler.tidy()
        assert not config.exists()  # gone with the job

    run(go())


def test_a_usual_project_shares_golems_lessons_and_claude_code(golem):
    async def go():
        room = await idle_project(golem)
        golem.settings.lessons().write_text("- golem's lesson\n")
        await golem.ask(room, "make it bigger", workflow="change")
        golem.harness.script = by_label({})
        await golem.until("change")
        await golem.step()
        call = golem.harness.calls[-1]
        assert "golem's lesson" in call.prompt and call.env.get("CLAUDE_CONFIG_DIR") == os.environ.get("CLAUDE_CONFIG_DIR")  # golem's own

    run(go())


# --- the jobs' ledger (ledger.py) ---------------------------------------------------------------

from golem import ledger  # noqa: E402
from golem.harness import RunResult  # noqa: E402

COSTS = {"change": 1.0, "item": 0.1, "review": 0.5}  # at API prices, by kind of run


def costed(answers: dict[str, list]):
    """by_label, with each run's usage: its cost by label, and some tokens."""
    inner = by_label(answers)

    def answer(call):
        r = inner(call)
        return RunResult(r.outcome, r.verdict, r.session_id, usage={"num_turns": 3, "cost_usd": COSTS.get(call.label, 0.05),
                                                                    "input_tokens": 1000, "output_tokens": 100})  # fmt: skip

    return answer


def test_the_ledger_says_what_a_checklist_job_cost_and_how_its_items_went(golem, app):
    async def go():
        job = await checklist_job(golem)
        golem.harness.script = costed({
            "change": [planner(ITEMS, ["tests/items.txt"])],
            "item": [builds("a.txt"), builds(), builds(), builds("b.txt")],
        })  # fmt: skip
        await golem.finish()
        return job

    job = run(go())
    r = ledger.row(golem.store.job(job.id), golem.store.steps(job.id), golem.store.runs(job.id))
    assert (r.items, r.first_try, r.again, r.escalated, r.gave_up) == (2, 1, 1, 1, 0)
    assert round(r.planning_cost, 2) == 1.0 and round(r.items_cost, 2) == 0.4 and r.runs == 7  # plan, 4 builds, review, docs
    assert round(r.cost, 2) == 1.95 and r.tokens_in == 7000 and round(r.by_model["haiku"], 2) == 0.3
    html = app.get(f"/p/tree-cards/jobs/{job.id}").text  # on its page, to hold beside another's
    assert "<p class=costs><b>Cost:</b> $1.95 at API prices · 7k in / 700 out · agents" in html
    assert "2 items: 1 first time, 1 again (1 on its Build&#x27;s model) · planning $1.00, items $0.40" in html
    assert app.get("/insights/jobs").status_code == 404  # no page comparing them: two tabs do
