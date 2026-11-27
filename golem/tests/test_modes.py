"""Modes (modes.py): named sets of golem's parts, read from files, that a job runs in and keeps."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from conftest import REPO, add_prompts, add_workflow
from test_flow import idle_project, verdict
from test_running import change_job

from golem import modes, plan, stages
from golem.store import Project, Step


def test_golems_own_modes_and_what_each_is_made_of():
    found = modes.load(REPO / "modes")
    assert list(found) == ["claude-power", "opencode-free"] and modes.DEFAULT == "claude-power"
    power, free = found["claude-power"], found["opencode-free"]
    assert (power.harness, power.models.key, power.prompts.key) == ("claude", "claude-power", "golem")
    assert (free.harness, free.models.steps) == ("opencode", {"*": modes.Choice("free")})
    # Claude power says something for every kind of agent step golem has, as Settings' defaults did.
    names = {n["name"] for wf in __import__("golem.workflows", fromlist=["x"]).load_all(REPO / "workflows").values()
             for n in wf.graph["nodes"] if n["type"] == "agent" and n.get("name")}  # fmt: skip
    kinds = names | {"fix", "resolve", "survey", "triage"} | {f"stage:{k}" for k in stages.catalog()}
    assert kinds <= set(power.models.steps) | {k for k in kinds if not k.startswith("stage:") and _staged(k)}


def _staged(name: str) -> bool:
    """A stage block named for its stage (golem's workflows name a few that way): its stage's entry covers it."""
    return name in stages.catalog()


def test_a_step_runs_its_own_pick_else_its_modes_choice_for_its_name_its_stage_or_every_step(golem):
    project = Project("p", "P", "s", "", "", "", False, "2026-11-22")
    docs, spec, odd = Step(1, 1, 1, "agent", "docs"), Step(2, 1, 2, "agent", "spec"), Step(3, 1, 3, "agent", "odd")
    e = plan.engine(golem.settings, golem.store, project, docs)
    assert (str(e), e.model_from) == ("sonnet/medium", "Claude power mode, for the docs step")
    assert str(plan.engine(golem.settings, golem.store, project, spec)) == "opus/xhigh"
    assert plan.engine(golem.settings, golem.store, project, odd, {"stage": "build"}).model_from == "Claude power mode, for build steps"
    assert plan.engine(golem.settings, golem.store, project, odd).model_from == "Claude power mode, for every step"
    docs.model, docs.effort = "haiku", "low"
    e = plan.engine(golem.settings, golem.store, project, docs)
    assert (str(e), e.model_from, e.effort_from) == ("haiku/low", "this step", "this step")
    free = Project("p", "P", "s", "opencode-free", "", "", False, "2026-11-22")  # its project's default
    e = plan.engine(golem.settings, golem.store, free, spec)
    assert (e.model, e.effort, str(e)) == ("free", "", "free")  # settled on a model when its job's first step runs


def write(folder, files: dict[str, str]):
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    return folder


MODELS = "name: M\nharness: claude\nsteps:\n  '*': {model: opus}\n"


@pytest.mark.parametrize(("files", "problem"), [
    ({"m.yml": "name: M\nharness: claude\nmodels: x\n"}, "there's no model config x"),
    ({"m.yml": "name: M\nharness: claude\nmodels: c\ncolour: red\n", "models/c.yml": MODELS}, "golem doesn't know colour"),
    ({"m.yml": "name: M\nharness: opencode\nmodels: c\n", "models/c.yml": MODELS}, "its model config is for claude, not opencode"),
    ({"m.yml": "name: M\nharness: claude\nmodels: c\n", "models/c.yml": "name: C\nharness: claude\nsteps:\n  spec: {model: opus}\n"}, "every step"),
    ({"m.yml": "name: M\nharness: claude\nmodels: c\n", "models/c.yml": "name: C\nharness: claude\nsteps:\n  '*': {model: gpt-9}\n"}, "Claude Code's models"),
    ({"m.yml": "name: M\nharness: opencode\nmodels: c\n", "models/c.yml": "name: C\nharness: opencode\nsteps:\n  '*': {model: opus}\n"}, "provider/model"),
    ({"m.yml": "name: M\nharness: claude\nmodels: c\nprompts: p\n", "models/c.yml": MODELS, "prompts/p/stages/docs.md": "## nope\n\nx\n"}, "docs has no part nope"),
    ({"m.yml": "name: M\nharness: claude\nmodels: c\nprompts: q\n", "models/c.yml": MODELS}, "there's no prompt set q"),
])  # fmt: skip
def test_a_mode_golem_cant_run_is_refused_with_why(tmp_path, files, problem):
    with pytest.raises(ValueError, match=problem):
        modes.load(write(tmp_path / "modes", files))


def test_a_prompt_set_changes_only_its_parts_and_tells_every_step_its_instructions(own):
    golem = own

    async def go():
        await idle_project(golem)
        add_prompts(golem, "terse", {"review": "## care.thorough\n\nBe brief.\n"}, instructions="Write British English.")
        mode = modes.load()["terse"]
        assert mode.prompts.parts == {"review": {"care.thorough": "Be brief."}} and mode.prompts.instructions == "Write British English."
        golems = stages.golems_text()["review"]
        assert mode.prompts.stage_parts("review", golems) == {**golems, "care.thorough": "Be brief."}  # golem's in the rest
        job = change_job(golem, "change", "make the fish blue")
        golem.store.update_job(job.id, mode="terse")
        golem.harness.script = [verdict("done")]
        await golem.until("change")
        await golem.step()
        assert "Write British English." in golem.harness.calls[-1].prompt
        assert plan.snapshot(golem.store.job(job.id))["mode"]["prompts"] == "terse"

    asyncio.run(go())


def test_a_mode_can_run_a_workflow_of_its_own_in_place_of_golems(own):
    golem = own

    async def go():
        await idle_project(golem)
        add_workflow(golem, "lean", ["setup", {"stage": "build", "builds": "auto"}, {"use": "ship"}, "end"])
        folder = golem.settings.process_dir / "modes"
        (folder / "lean.yml").write_text("name: Lean\nharness: claude\nmodels: claude-power\nworkflows: {change: lean}\n")
        job = change_job(golem, "change", "make the fish blue")
        golem.store.update_job(job.id, mode="lean")
        golem.scheduler.ensure_started(job)
        assert golem.store.job(job.id).workflow == "lean"

    asyncio.run(go())


def test_what_riggy_set_before_modes_comes_out_as_a_modes_files(golem):
    store = golem.store
    store.db.execute("INSERT INTO defaults (name, model, effort) VALUES ('docs', 'haiku', 'low'), ('spec', 'opus', 'xhigh')")
    store.db.execute(
        "INSERT INTO stage_texts (stage, part, text, seeded, edited, upstream, sig, updated_at) VALUES "
        "('review', 'care.quick', 'Only what would break it.', 'x', 1, '', 's', 'now'), ('docs', 'base', 'golem''s', 'x', 0, '', 's', 'now')"
    )
    out = modes.export(store)
    assert "# modes/models/riggy.yml" in out and '"docs": {model: haiku, effort: low}' in out
    assert '"spec": {model: opus, effort: xhigh}' in out  # golem's own: there anyway, in the whole config
    assert "# modes/prompts/riggy/stages/review.md\n## care.quick\n\nOnly what would break it." in out and "docs.md" not in out
    store.db.execute("DELETE FROM defaults")
    store.db.execute("DELETE FROM stage_texts")
    assert modes.export(store) == ""


def test_golems_image_has_the_modes(tmp_path):
    assert "COPY modes /opt/golem/modes" in (REPO / "golem" / "Dockerfile").read_text()
    shutil.copytree(REPO / "modes", tmp_path / "modes")
    assert set(modes.load(tmp_path / "modes")) == set(modes.load(REPO / "modes"))
