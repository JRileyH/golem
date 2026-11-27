"""Checklist mode (checklist.py): a Build plans items with checks, cheaper runs build them, golem checks each."""

from __future__ import annotations

import asyncio
import json

from test_flow import by_label, idle_project, verdict
from test_web import app, new, sign_in  # noqa: F401  (app: the web app fixture)

from golem import checklist, plan, progress, route
from golem.glance import Glance
from golem.store import Step


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


def test_an_item_asks_nobody_its_check_decides_and_its_next_try_hears_what_it_said(golem):
    async def go():
        job = await checklist_job(golem, "add a file the check is strict about")
        golem.harness.script = by_label({
            "change": [planner(ITEMS[:1], ["tests/items.txt"])],
            "item": [verdict("blocked", "The check wants 0.6 and it measures 0.59: lower it, or retune?"), builds("a.txt")],
        })  # fmt: skip
        await golem.finish()
        assert golem.store.job(job.id).state == "done"  # never waited on Riggy
        built = [c for c in golem.harness.calls if c.label == "item"]
        assert built[0].schema == checklist.ITEM_SCHEMA and checklist.ITEM_SCHEMA["properties"]["verdict"]["enum"] == ["done"]
        assert "Never return `blocked`" in built[0].prompt
        assert "The last try said: The check wants 0.6 and it measures 0.59: lower it, or retune?" in built[1].prompt
        assert [s.state for s in golem.store.steps(job.id) if s.kind == "check"] == ["failed", "done"]
        assert not any("Question:" in t for t in golem.chat.texts("tree-cards"))

    run(go())


def test_the_headline_is_on_the_item_under_way_not_a_check_that_failed_before():
    def item(n: int, kind: str, state: str, pos: int) -> Step:
        return Step(pos, 1, pos, kind, kind, state=state, item={"plan": 9, "n": n, "of": 4, "title": f"Item {n}"})

    steps = [item(2, "agent", "done", 1), item(2, "check", "failed", 2), item(2, "agent", "done", 3), item(2, "check", "done", 4),
             item(3, "agent", "running", 5), item(3, "check", "pending", 6)]  # fmt: skip
    now = progress.current(progress.pieces(steps))
    assert now.step.id == 5 and now.name == "Item 3 (item 3 of 4)"
    steps[4] = item(3, "agent", "pending", 5)  # between items: the next, not the old failure
    assert progress.current(progress.pieces(steps)).step.id == 5
    assert progress.current(progress.pieces(steps[:2] + [item(2, "agent", "pending", 3)])).name == "Check Item 2 (item 2 of 4)"  # just failed


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


def test_a_checklist_shows_a_row_an_item_and_what_went_wrong_in_its_runs(golem, app):
    async def go():
        job = await checklist_job(golem)
        golem.harness.script = by_label({"change": [planner(ITEMS, ["tests/items.txt"])], "item": [builds("a.txt"), builds(), builds("b.txt")]})
        await golem.finish()
        return job

    job = run(go())
    second = [r for r in golem.store.runs(job.id) if r["phase"] == "item"][1]  # item 2's first try
    golem.store.db.execute("UPDATE runs SET glance = ? WHERE id = ?", (Glance(work=120, after=292, missing=["pkill", "ps"]).to_json(), second["id"]))
    sign_in(app)
    html = app.get(f"/p/tree-cards/jobs/{job.id}").text
    rows = html[html.index("<ol class=items>") : html.index("</ol>", html.index("<ol class=items>"))]
    assert rows.count("<li class='it done'>") == 2 and "> item<" not in rows  # named, not "item" and "check"
    assert "<b>1</b> File A</a><span class=meta>" in rows and "<b>2</b> File B</a><span class=meta>" in rows
    assert "2 tries</span>" in rows.split("<b>2</b>")[1] and "tries" not in rows.split("<b>2</b>")[0]
    assert "⚠ went on 4m after its verdict · not installed: pkill, ps" in rows  # a finished item says only what went wrong
    assert "work 2m" not in rows
