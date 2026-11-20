"""Adopting a repo golem didn't make, end to end through the bot and scheduler, with GitHub,
Claude and the deployer faked (conftest.py)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from conftest import Golem
from test_adopt import golem_shaped

from golem.workspace import CI, About

ABOUT = About("mimic", "Tabletop data in, cards out", False, True, "# Mimic\n\nTabletop data in, visual outputs out.\n")
BRIEF = {"name": "Mimic", "summary": "Printable cards for a D&D table.", "tag": "none", "mode": "unspecified",
         "model": "unspecified", "effort": "unspecified"}  # fmt: skip
PAGE = "https://golem.unlaunched.dev/p/mimic"


def run(coro):
    return asyncio.run(coro)


PASSED = {"tests": {"result": "passed", "command": "uv run pytest", "detail": "559 passed"}, "missing": [], "needs": []}
STEPS = ["check it over", "set up", "adopt", "push", "PR", "CI", "merge", "deploy", "done"]


async def adopt(golem: Golem, words: str = "", brief: dict = BRIEF, about: About = ABOUT, then: list | None = None) -> None:
    golem.workspace.existing["mimic"] = about
    golem.harness.script = [brief, *(then or [])]
    await golem.say(golem.inbox, f"!adopt mimic {words}".strip())


def arm(tmp_path) -> Golem:
    """golem on an arm64 box, as the golem-shaped repo's workflow builds for."""
    return Golem(tmp_path, platform="linux/arm64")


def states(golem: Golem, job_id: int) -> list[tuple[str, str]]:
    from golem import plan

    return [(plan.label(s), s.state) for s in golem.store.steps(job_id)]


# --- the bot ---------------------------------------------------------------------


def test_github_is_checked_first_and_a_blocked_repo_makes_nothing(golem):
    async def go():
        await golem.say(golem.inbox, "!adopt mimic")
        assert golem.chat.texts(golem.inbox)[-1].startswith(
            "I didn't adopt mimic. What's in the way:\n- I can't see github.com/unlaunched/mimic. Is it in unlaunched?"
        )
        golem.workspace.existing["mimic"] = replace(ABOUT, archived=True, has_main=False)
        await golem.say(golem.inbox, "!adopt mimic")
        said = golem.chat.texts(golem.inbox)[-1]
        assert "- It's archived" in said and "- It has no main branch" in said
        assert said.endswith("Once that's changed, say !adopt mimic again.")
        await golem.say(golem.inbox, "!adopt golem")
        assert golem.chat.texts(golem.inbox)[-1].startswith("I didn't adopt golem. What's in the way:\n- golem is one of golem's own addresses.")
        await golem.say(golem.inbox, "!adopt")
        assert golem.chat.texts(golem.inbox)[-1] == "Usage: !adopt <repo> [what it is], for a repo in github.com/unlaunched that golem didn't make."
        assert golem.store.projects() == [] and golem.harness.calls == []  # nothing made, no model asked

    run(go())


def test_an_adopted_repo_becomes_a_project_with_his_words_and_the_usual_grace(golem):
    async def go():
        await adopt(golem, "cards for my table, rigorous", {**BRIEF, "mode": "rigorous"})
        project = golem.store.project("mimic")
        assert (project.name, project.summary, project.mode) == ("Mimic", "Printable cards for a D&D table.", "rigorous")
        job = golem.store.active_job("mimic")
        # A minute's grace (none in tests) to change the settings before anything starts, the check included.
        assert (job.kind, job.playbook, job.state, job.request) == ("adopt", "adopt", "parked", "!adopt mimic cards for my table, rigorous")
        assert golem.chat.texts(golem.inbox)[-1] == f"Adopting Mimic (mimic), mode rigorous, each step's default model and effort. Its conversation: {PAGE}"
        [intro] = golem.chat.texts("mimic")
        assert intro.startswith("Mimic\n\nPrintable cards for a D&D table.\n\nYou said: “cards for my table, rigorous”")
        assert "I'm taking on https://github.com/unlaunched/mimic, to live at https://mimic.unlaunched.dev/." in intro
        assert intro.endswith("Mode rigorous, each step's default model and effort. Starting in a minute: to change that first, say !mode, !model or !effort here (!help for the options).")
        [triage] = golem.harness.calls
        assert (triage.model, triage.label) == ("haiku", "adopt") and "Tabletop data in, visual outputs out." in triage.prompt

        await golem.say("mimic", "!mode standard")
        assert golem.chat.texts("mimic")[-1].endswith("Adopting it goes the same way in every mode; its changes after that follow standard rules.")
        assert golem.store.job(job.id).playbook == "adopt"  # not swapped for a new project's playbook
        await golem.say(golem.inbox, "!queue")
        assert golem.chat.texts(golem.inbox)[-1] == "#1 Mimic: adoption, starting shortly, check it over (1/9)"
        await golem.say(golem.inbox, "!adopt mimic")
        assert golem.chat.texts(golem.inbox)[-1] == f"Mimic (mimic) is already one of my projects: {PAGE}"

    run(go())


def test_without_his_words_the_brief_comes_from_the_repo(golem):
    async def go():
        await adopt(golem)
        assert golem.chat.texts("mimic")[0].startswith("Mimic\n\nPrintable cards for a D&D table.\n\nFrom its README and description")
        assert "(nothing)" in golem.harness.calls[0].prompt

    run(go())


# --- the check, and what follows ------------------------------------------------


def test_a_repo_that_passes_everything_is_adopted_and_main_deployed(tmp_path):
    golem = arm(tmp_path)

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.has_work = False  # nothing to push: it's all there
        golem.store.set_default("survey", "model", "sonnet")
        await adopt(golem, then=[PASSED])
        await golem.finish()
        job = golem.store.jobs()[0]
        assert job.state == "done"
        assert states(golem, job.id) == [
            ("check it over", "done"), ("set up", "done"), ("adopt", "skipped"), ("push", "skipped"), ("PR", "skipped"),
            ("CI", "skipped"), ("merge", "skipped"), ("deploy", "done"), ("done", "done"),
        ]  # fmt: skip
        main = golem.workspace.main["mimic"]
        assert golem.deployer.validated == [("mimic", main)] and golem.deployer.deploys == [("mimic", main)]

        # The survey: one run that builds and tests it here, as its own kind of step.
        triage, survey = golem.harness.calls
        assert (survey.label, survey.model) == ("survey", "sonnet")
        assert survey.cwd == golem.workspace.path("mimic") and survey.schema["required"] == ["tests", "missing", "needs"]
        assert "# 1. Can a worker build and test it here?" in survey.prompt and "linux/arm64" in survey.prompt
        assert "discard" in golem.workspace.actions  # the checkout is put back

        checked = next(t for t in golem.chat.texts("mimic") if t.startswith("I checked it over"))
        assert checked == (
            "I checked it over, and I'm adopting it.\n\n✓ CI on main: build: success\n✓ Dockerfile\n✓ build workflow: build.yml\n"
            "✓ repo shape\n✓ docs/brief.md\n✓ deploy/compose.yml\n✓ tests in golem's container: uv run pytest: 559 passed\n\n"
            "Next I deploy main as it is.\n\nMode standard, each step's default model and effort (!mode, !model or !effort here to change them)."
        )
        # Its issue says golem worked on it: adopting it again brings it back as history (history.py). With no PR, alert closes it.
        assert golem.workspace.issues == [("mimic", "Mimic")]
        assert [(slug, n) for slug, n, _ in golem.workspace.closed] == [("mimic", 1)]
        assert golem.chat.texts("mimic")[-1].startswith("Done: ")
        assert golem.store.card("mimic") is not None  # on the projects page

    run(go())


def test_gaps_go_to_a_worker_and_ship_like_any_change(golem):
    async def go():
        repo = golem.workspace.path("mimic")
        (repo / "tests").mkdir(parents=True)
        (repo / "README.md").write_text("# Mimic\n\nTabletop data in.\n")
        (repo / "Dockerfile").write_text("FROM python:3.13-slim\n")
        await adopt(golem, "cards for my table", then=[PASSED, {"verdict": "done", "message": "It deploys through golem now."}])
        await golem.finish()
        job = golem.store.jobs()[0]
        assert [label for label, state in states(golem, job.id) if state == "done"] == STEPS
        assert golem.deployer.validated == []  # no deploy/compose.yml to ask about

        survey, work = golem.harness.calls[1], golem.harness.calls[-1]
        # What he said goes to the survey: something he's said he gives it himself isn't a need.
        assert "What Riggy said when he asked golem to adopt it:\n\n> cards for my table\n" in survey.prompt
        assert '"Riggy makes the API key on the box" is his choice' in " ".join(survey.prompt.split())
        assert "# Your step: adopt" in work.prompt and "Job: adopting a repo golem didn't make" in work.prompt
        found = work.prompt.split("## What golem's checks found\n\n", 1)[1]
        assert found.startswith("- build workflow: There's no GitHub Actions workflow")
        assert "- deploy/compose.yml: There's no deploy/compose.yml" in found and "docs/brief.md" not in found  # golem's to write
        assert "→ repo shape: Missing AGENTS.md" in next(t for t in golem.chat.texts("mimic") if t.startswith("I checked it over"))
        assert "I'll fix what's marked →, then PR, CI, merge and deploy it." in golem.chat.texts("mimic")[1]

        brief = (repo / "docs" / "brief.md").read_text()
        assert brief.startswith("# Mimic\n\nPrintable cards for a D&D table.\n\n## Where it came from\n\nAdopted by golem on ")
        assert brief.endswith("Riggy said:\n\n> cards for my table\n")
        assert "open PR #1: Mimic: adopt" in golem.workspace.actions
        assert golem.chat.texts("mimic")[-1] == "Done: It deploys through golem now.\nhttps://mimic.unlaunched.dev/"
        assert golem.workspace.closed == []  # its PR closed the issue

    run(go())


def test_a_missing_brief_alone_needs_no_worker(tmp_path):
    golem = arm(tmp_path)

    async def go():
        (golem_shaped(golem.workspace.path("mimic")) / "docs" / "brief.md").unlink()
        await adopt(golem, then=[PASSED])
        await golem.finish()
        job = golem.store.jobs()[0]
        assert ("adopt", "skipped") in states(golem, job.id) and job.state == "done"
        assert len(golem.harness.calls) == 2  # the triage and the survey, nothing else
        assert (golem.workspace.path("mimic") / "docs" / "brief.md").read_text().startswith("# Mimic\n")
        assert "open PR #1: Mimic: brief" in golem.workspace.actions
        assert "→ docs/brief.md: I'll write it from what you said and the README.\n" in golem.chat.texts("mimic")[1]
        assert "I'll write its brief, then deploy it." in golem.chat.texts("mimic")[1]

    run(go())


def test_something_blocking_found_in_the_check_and_golem_forgets_it(tmp_path):
    golem = arm(tmp_path)
    blocked = {
        "tests": {"result": "failed", "command": "uv run pytest", "detail": "25 failed: cannot load libpangoft2"},
        "missing": [{"what": "libpangoft2-1.0-0", "for": "WeasyPrint"}],
        "needs": [{"what": "a command run by hand in its container", "why": "API keys come from `mimic key`", "change": "make one on first start"}],
    }  # fmt: skip

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        (golem.workspace.path("mimic") / "AGENTS.md").unlink()
        await adopt(golem, then=[blocked])
        await golem.finish()
        assert golem.store.projects() == [] and golem.store.jobs() == []
        assert not golem.workspace.path("mimic").exists()  # the checkout too
        assert golem.workspace.issues == [] and golem.deployer.deploys == []  # nothing written to the repo, nothing deployed
        said = golem.chat.texts(golem.inbox)[-1]
        assert said.startswith("I didn't adopt mimic. What's in the way:\n- Building or testing it needs libpangoft2-1.0-0 (for WeasyPrint)")
        assert "\n- It needs a command run by hand in its container: API keys come from `mimic key` To change that: make one on first start\n" in said
        assert said.endswith("The rest I'd have fixed myself: repo shape.\n\nOnce that's changed, say !adopt mimic again.")

        # Changed, and asked again: a fresh start.
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.has_work = False
        await adopt(golem, then=[PASSED])
        await golem.finish()
        assert golem.store.project("mimic") and golem.store.jobs()[0].state == "done"

    run(go())


def test_adopting_a_repo_golem_worked_on_brings_back_its_jobs(tmp_path):
    from test_history import BUGZ

    golem = arm(tmp_path)

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.has_work = False
        golem.workspace.items["mimic"] = BUGZ  # an earlier golem's five jobs on it
        await adopt(golem, then=[PASSED])
        await golem.finish()
        adoption, *past = golem.store.jobs(limit=50)  # the latest first, by when each began
        assert adoption.kind == "adopt" and adoption.state == "done"
        assert [j.issue for j in past] == [None, 14, 4, 11, 1] and past[-1].kind == "new"
        assert all(j.id > adoption.id for j in past)  # numbered afresh: the numbers in their issues were the old golem's
        assert [s.kind for s in golem.store.steps(past[-1].id)] == ["setup", "pr", "merge"]
        assert "Brought back its history: 5 earlier job(s), from golem's issues and PRs on it." in golem.chat.texts("mimic")
        assert golem.workspace.issues == [("mimic", "Mimic")]  # its own issue opened after: not taken for one of them

        # A question about it sees the adoption first, then what came before.
        question = golem.store.add_job("mimic", "change", "what's it for?", "question")
        jobs, _ = golem.scheduler.history(golem.store.project("mimic"), question)
        assert jobs[0].startswith(f"- #{adoption.id}, adopt, done") and "Looks great! Get started" in "\n".join(jobs)

        # Once per adoption, even if setup ran again.
        await golem.scheduler.bring_back_history(adoption, golem.store.project("mimic"))
        assert len(golem.store.jobs(limit=50)) == 7  # the five, the adoption and the question

    run(go())


def test_a_repo_golem_never_worked_on_brings_back_nothing(tmp_path):
    from test_history import issue, pr

    golem = arm(tmp_path)

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.items["mimic"] = [pr(1, "Closes #2"), {**issue(2, 1, "x"), "body": "A bug report from someone"}]
        await adopt(golem, then=[PASSED])
        await golem.finish()
        assert [j.kind for j in golem.store.jobs(limit=50)] == ["adopt"]
        assert not any(t.startswith("Brought back") for t in golem.chat.texts("mimic"))

    run(go())


def test_history_that_cant_be_read_doesnt_stop_the_adoption(tmp_path):
    from golem.workspace import WorkspaceError

    golem = arm(tmp_path)

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.items["mimic"] = WorkspaceError("reading mimic's issues: HTTP 502")
        await adopt(golem, then=[PASSED])
        await golem.finish()
        assert golem.store.jobs(limit=50)[0].state == "done"
        assert (
            "I couldn't read its earlier jobs from GitHub (reading mimic's issues: HTTP 502), so its history here starts now."
            in golem.chat.texts("mimic")
        )

    run(go())


def test_with_the_fake_harness_golems_own_checks_still_run(tmp_path):
    """HARNESS=fake has no worker to survey with, and says so; the rest of the check is real."""
    golem = Golem(tmp_path, platform="linux/arm64", harness="fake")

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.existing["mimic"] = ABOUT
        await golem.say(golem.inbox, "!adopt mimic")
        assert golem.store.project("mimic").name == "Mimic"  # from the README, no model asked
        await golem.step()
        checked = golem.chat.texts("mimic")[-1]
        assert "✓ build workflow: build.yml\n" in checked and "✓ tests in golem's container: not run (HARNESS=fake)\n" in checked
        assert golem.harness.calls == []

    run(go())


def test_the_check_waits_for_ci_on_main_before_the_survey(tmp_path):
    golem = arm(tmp_path)

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.workspace.ci_results = [CI("pending", "build: in_progress"), CI("failed", "build: failure\n\nnpm test: 2 failing")]
        await adopt(golem, then=[PASSED])
        await golem.step()
        job = golem.store.jobs()[0]
        assert job.state == "parked" and len(golem.harness.calls) == 1  # no survey yet
        assert golem.store.steps(job.id)[0].summary == "Waiting for CI on main to finish before checking it over: build: in_progress"
        await golem.step()
        assert len(golem.harness.calls) == 2
        work = next(s for s in golem.store.steps(job.id) if s.name == "adopt")
        assert work.problem == "- CI on main: CI fails on main's latest commit (build: failure)."

    run(go())


def test_the_deployers_own_rules_judge_the_compose_file(tmp_path):
    golem = arm(tmp_path)

    async def go():
        golem_shaped(golem.workspace.path("mimic"))
        golem.deployer.invalid = "service `mimic`: `ports` is not allowed\nservice `caddy`: bind mounts are not allowed (`./deploy/Caddyfile`)"
        await adopt(golem, then=[PASSED])
        await golem.step()
        job = golem.store.jobs()[0]
        work = next(s for s in golem.store.steps(job.id) if s.name == "adopt")
        assert work.problem == (
            "- deploy/compose.yml: The deployer refuses it: service `mimic`: `ports` is not allowed; "
            "service `caddy`: bind mounts are not allowed (`./deploy/Caddyfile`)."
        )
        assert golem.deployer.validated == [("mimic", golem.workspace.main["mimic"])]

    run(go())
