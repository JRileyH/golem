"""End-to-end flows through the bot and scheduler, with GitHub and Claude faked."""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import Golem

from golem import nuke, plan, scheduler
from golem.harness import WRAPUP_SCHEMA, RunResult
from golem.workspace import CI

TREES = {"name": "Tree Cards", "slug": "tree-cards", "summary": "A flashcard game for northeastern US trees."}
URL = "https://tree-cards.unlaunched.dev/"
CHECKIN = ["set up", "spec", "scaffold", "push", "PR", "CI", "merge", "deploy", "mvp", "push", "PR", "CI", "merge", "deploy"]


def run(coro):
    return asyncio.run(coro)


def verdict(kind: str, message: str = "ok", link: str = "") -> dict:
    return {"verdict": kind, "message": message, **({"link": link} if link else {})}


async def new_project(golem: Golem, script_after: list) -> str:
    """Dictate the trees idea in the inbox; returns the project's channel, its slug."""
    golem.harness.script = [TREES, *script_after]
    await golem.say(golem.inbox, "a flashcard game for identifying northeastern us trees")
    return golem.store.projects()[0].slug


def current(golem: Golem, job_id: int):
    return plan.current(golem.store.steps(job_id))


# --- new projects -------------------------------------------------------------


def test_inbox_idea_becomes_a_project_with_its_own_conversation(golem):
    async def go():
        room = await new_project(golem, [])
        project = golem.store.project("tree-cards")
        assert project.name == "Tree Cards" and room == "tree-cards"
        job = golem.store.active_job("tree-cards")
        # A minute's grace (none in tests) to change the settings before work starts.
        assert (job.kind, job.playbook, job.state) == ("new", "standard", "parked")
        assert job.request == "a flashcard game for identifying northeastern us trees"
        [reply] = golem.chat.texts(golem.inbox)
        assert reply.startswith("New project: Tree Cards (tree-cards), mode standard, each step's default model and effort.")
        assert reply.endswith("Its conversation: https://golem.unlaunched.dev/p/tree-cards")
        [intro] = golem.chat.texts(room)
        assert "A flashcard game for northeastern US trees." in intro
        assert "https://tree-cards.unlaunched.dev/" in intro
        assert "Mode standard, each step's default model and effort. Starting in a minute" in intro
        # Triage is a cheap structured call.
        triage = golem.harness.calls[0]
        assert (triage.model, triage.label) == ("haiku", "intake")
        assert "GOLEM_WEB_PASSWORD" not in triage.env

    run(go())


def test_slug_clashes_get_a_suffix(golem):
    async def go():
        golem.workspace.repos.add("tree-cards")  # a repo by that name already exists
        await new_project(golem, [])
        assert golem.store.project("tree-cards") is None
        assert golem.store.project("tree-cards-2").name == "Tree Cards"

    run(go())


def test_a_bad_triage_falls_back_to_the_dictation(golem):
    async def go():
        golem.harness.script = [RunResult("error", error="boom")]
        await golem.say(golem.inbox, "Pomodoro timer with sounds")
        assert golem.store.project("pomodoro-timer-with-sounds").name == "Pomodoro Timer With Sounds"

    run(go())


def test_triage_tags_a_new_idea_and_only_rarely_makes_a_tag(golem):
    async def go():
        golem.harness.script = [{**TREES, "tag": "Game"}]
        await golem.say(golem.inbox, "a flashcard game for identifying northeastern us trees")
        assert golem.store.project("tree-cards").tag == "Game"
        triage = golem.harness.calls[0]
        assert "- Game\n" in triage.prompt and "Pixel Art" in triage.prompt  # the tags there are, and what isn't one
        assert triage.schema["properties"]["tag"]["enum"] == ["Game", "none", "new"]

        # A new one, when triage picks it: said in the inbox, and offered to the next idea.
        golem.harness.script = [{"name": "Lint Bot", "slug": "lint-bot", "summary": "s", "tag": "new", "new_tag": "dev tool"}]
        await golem.say(golem.inbox, "a bot that lints my repos")
        assert golem.store.project("lint-bot").tag == "Dev Tool" and [t.name for t in golem.store.tags()] == ["Game", "Dev Tool"]
        assert golem.chat.texts(golem.inbox)[-1].endswith("/p/lint-bot. It's the first Dev Tool: a new tag (Settings has them)")
        # A new one that's an old one is the old one; one that's more than a sort of project is none.
        for slug, new_tag, tag in (("hoops", "Games", "Game"), ("garden", "Gardening Tips For Kids", ""), ("x-tool", "Dev Tools", "Dev Tool")):
            golem.harness.script = [{"name": slug, "slug": slug, "summary": "s", "tag": "new", "new_tag": new_tag}]
            await golem.say(golem.inbox, slug)
            assert golem.store.project(slug).tag == tag
        assert golem.harness.calls[-1].schema["properties"]["tag"]["enum"] == ["Game", "Dev Tool", "none", "new"]
        assert len(golem.store.tags()) == 2

    run(go())


def test_standard_steps_walk_to_the_mvp_checkin_and_on_to_a_deployed_done(golem):
    async def go():
        room = await new_project(
            golem,
            [
                verdict("done", "MVP: pick the tree from four photos."),
                verdict("done", "A styled placeholder page."),
                verdict("done", "Try the quiz: ten trees per round."),
            ],
        )
        await golem.finish()  # until the check-in, which waits on Riggy
        job = golem.store.active_job("tree-cards")
        assert job.state == "waiting"
        assert golem.labels(job.id) == CHECKIN
        assert (plan.label(current(golem, job.id)), current(golem, job.id).state) == ("your check-in", "waiting")
        assert job.issue == 1 and golem.workspace.issues == [("tree-cards", "Tree Cards")]

        # golem's own code pushed, opened PRs, waited for CI, merged and deployed: twice by now.
        did = [a for a in golem.workspace.actions if not a.startswith("CI on")]
        assert did == [
            "push golem/job-1", "open PR #1: Tree Cards: spec + scaffold", "merge PR #1",
            "push golem/job-1", "open PR #2: Tree Cards: mvp", "merge PR #2",
        ]  # fmt: skip
        assert len(golem.deployer.deploys) == 2
        assert golem.deployer.deploys[-1] == ("tree-cards", golem.workspace.main["tree-cards"])  # main, after the merge
        assert golem.workspace.bodies[0].startswith("Part of #1\n\n- **spec:** MVP: pick the tree from four photos.")

        texts = golem.chat.texts(room)
        assert "✓ spec: MVP: pick the tree from four photos.\nNext: scaffold." in texts
        assert "✓ scaffold: A styled placeholder page.\nNext: PR, CI, merge, deploy, then mvp." in texts
        assert "✓ PR: PR #1 is open.\nhttps://github.com/unlaunched/tree-cards/pull/1\nNext: CI, merge, deploy, then mvp." in texts
        deployed = [t for t in texts if t.startswith("✓ deploy:")]
        assert deployed[0].endswith(f" is live.\n{URL}\nNext: mvp.")
        assert deployed[1].endswith(f" is live.\n{URL}\nNext: your check-in, then buildout.")
        assert texts[-1] == f"Try the quiz: ten trees per round.\n{URL}\n\nTry it and reply here: say it looks good, or what to change."
        # Progress is posted as notices (no push); the check-in as a message (push).
        assert [n for r, _, n in golem.chat.sent if r == room][-1] is False

        # Nothing runs while the job waits on Riggy.
        assert golem.store.next_job(at="9999") is None

        await golem.say(room, "looks good but make the photos bigger")
        assert golem.chat.texts(room)[-1] == "Thanks, picking it back up. Starting now."
        golem.harness.script = [verdict("done"), verdict("done"), verdict("done", "Ten trees a round, with streaks.")]
        await golem.finish()

        buildout, review, docs = golem.harness.calls[-3:]
        assert "# Your step: buildout" in buildout.prompt
        assert "> looks good but make the photos bigger" in buildout.prompt
        assert "- your check-in: You said: “looks good but make the photos bigger”" in buildout.prompt
        assert "GOLEM_WEB_PASSWORD" not in buildout.env
        assert buildout.env["GOLEM_SLUG"] == "tree-cards"
        # The reply was delivered once; the next step doesn't see it again.
        assert "make the photos bigger\n" not in review.prompt.split("# Riggy's feedback")[1]

        job = golem.store.job(job.id)
        assert job.state == "done"
        steps = golem.store.steps(job.id)
        assert [s.state for s in steps] == ["done"] * 24
        assert [plan.label(s) for s in steps[-2:]] == ["deploy", "done"]
        assert len(golem.deployer.deploys) == 3
        assert golem.workspace.bodies[-1].startswith("Closes #1")
        assert golem.chat.texts(room)[-1] == f"Done: Ten trees a round, with streaks.\n{URL}"
        assert [r["phase"] for r in golem.store.runs(job.id)] == ["spec", "scaffold", "mvp", "buildout", "review", "docs"]
        assert "Finished job" in golem.chat.texts("status")[-1]

    run(go())


def test_reckless_goes_straight_to_main(tmp_path):
    golem = Golem(tmp_path, default_mode="reckless")

    async def go():
        room = await new_project(golem, [verdict("done", "Flip cards to learn twenty trees.")])
        await golem.finish()
        job = golem.store.jobs()[0]
        assert job.state == "done"
        assert golem.labels(job.id) == ["set up", "build", "push to main", "deploy", "done"]
        assert [a for a in golem.workspace.actions if not a.startswith("CI on")] == ["push main"]
        assert golem.chat.texts(room)[-1] == f"Done: Flip cards to learn twenty trees.\n{URL}"
        # No PR closed its issue, so golem does: GitHub's record says it finished (recover.py reads it).
        assert golem.workspace.closed == [(room, job.issue, f"Done: Flip cards to learn twenty trees.\n\n{URL}")]

    run(go())


def test_a_worker_has_no_say_in_what_comes_next(golem):
    """Whatever a worker says, golem runs the next step; only its own last steps finish a job."""

    async def go():
        room = await new_project(golem, [])
        await golem.until("spec")
        golem.harness.script = [verdict("done", "All done, nothing more to do!")]
        await golem.step()
        job = golem.store.jobs()[0]
        assert job.state == "queued" and plan.label(current(golem, job.id)) == "scaffold"
        assert golem.harness.calls[-1].schema is None  # the verdict schema: done or blocked, nothing else
        # The old verdicts are gone: a worker can't skip ahead or stop for a review.
        golem.harness.script = [verdict("checkin", "Please review my PR")]
        await golem.step()
        assert golem.store.job(job.id).state == "parked"  # not a verdict: retried
        assert (current(golem, job.id).name, current(golem, job.id).error) == ("scaffold", "unknown verdict 'checkin'")
        assert not any("Please review my PR" in t for t in golem.chat.texts(room))

    run(go())


def test_workers_never_get_golem_secrets(golem, monkeypatch):
    monkeypatch.setenv("GOLEM_WEB_PASSWORD", "pw")
    monkeypatch.setenv("DEPLOYER_ADMIN_SECRET", "admin")
    monkeypatch.setenv("DEPLOYER_SECRET", "deploy")

    async def go():
        await new_project(golem, [verdict("done")])
        await golem.until("spec")
        await golem.step()
        triage, step = golem.harness.calls
        for call in (triage, step):
            assert "GOLEM_WEB_PASSWORD" not in call.env and "DEPLOYER_ADMIN_SECRET" not in call.env
        assert step.env["DEPLOYER_SECRET"] == "deploy"  # they can read their own project's status and logs

    run(go())


def test_step_prompts_are_fully_filled_in(golem):
    async def go():
        await new_project(golem, [verdict("done")])
        await golem.until("spec")
        await golem.step()
        prompt = golem.harness.calls[-1].prompt
        assert '>> "$GOLEM_PROGRESS"' in prompt  # an environment variable, not a placeholder
        assert "$" not in prompt.replace("$(", "").replace("$GOLEM_PROGRESS", "")
        assert "# Your step: spec" in prompt
        assert "on the branch `golem/job-1`" in prompt
        assert "Don't push, open or merge pull requests, or deploy" in prompt
        assert "> a flashcard game for identifying northeastern us trees" in prompt
        assert "https://github.com/unlaunched/tree-cards/issues/1" in prompt
        assert golem.workspace.branch == "golem/job-1"

    run(go())


# --- when something fails ------------------------------------------------------


def test_failed_ci_gets_a_fix_step_and_the_delivery_again(golem):
    async def go():
        room = await new_project(golem, [verdict("done"), verdict("done")])
        await golem.until("CI")
        failure = CI("failed", "build: failure\n\nnpm test: 2 failing", "https://github.com/unlaunched/tree-cards/actions/runs/7")
        golem.workspace.ci_results = [failure]
        await golem.step()
        job = golem.store.jobs()[0]
        assert golem.chat.texts(room)[-1] == "✗ CI: CI failed on PR #1. Fixing it."
        failed = [s for s in golem.store.steps(job.id) if s.state == "failed"]
        assert [(plan.label(s), s.links) for s in failed] == [("CI", {"ci": failure.url})]
        upcoming = [plan.label(s) for s in golem.store.steps(job.id) if s.state == "pending"]
        assert upcoming[:6] == ["fix", "push", "PR", "CI", "merge", "deploy"]

        golem.harness.script = [verdict("done", "The tests expected the old deck size.")]
        await golem.step()
        fix = golem.harness.calls[-1]
        assert "# Your step: fix" in fix.prompt and "npm test: 2 failing" in fix.prompt
        await golem.until("mvp")
        assert golem.labels(job.id)[3:] == ["push", "PR", "CI", "fix", "push", "PR", "CI", "merge", "deploy"]
        assert len(golem.workspace.bodies) == 1  # the same PR, fixed

    run(go())


def test_a_refused_deploy_is_fixed_and_merged_again(golem):
    async def go():
        await new_project(golem, [verdict("done"), verdict("done")])
        await golem.until("deploy")
        golem.deployer.refuse = ["ports: not allowed; the deployer routes traffic itself"]
        await golem.step()
        job = golem.store.jobs()[0]
        upcoming = [plan.label(s) for s in golem.store.steps(job.id) if s.state == "pending"]
        assert upcoming[:7] == ["fix", "push", "PR", "CI", "merge", "deploy", "mvp"]
        golem.harness.script = [verdict("done")]
        await golem.step()
        assert "ports: not allowed" in golem.harness.calls[-1].prompt
        await golem.until("mvp")
        assert len(golem.deployer.deploys) == 2 and len(golem.workspace.bodies) == 2

    run(go())


def test_waiting_for_ci_frees_the_worker_and_checks_again(golem):
    async def go():
        await new_project(golem, [verdict("done"), verdict("done")])
        await golem.until("CI")
        golem.workspace.ci_results = [CI("pending", "build: in_progress")]
        await golem.step()
        job = golem.store.jobs()[0]
        step = current(golem, job.id)
        assert (job.state, plan.label(step), step.summary) == ("parked", "CI", "Waiting for CI on PR #1: build: in_progress")
        assert golem.store.next_job() is None  # checked again in 20 seconds
        assert "waiting for CI, CI (6/24)" in golem.bot.job_line(job)
        await golem.step()
        assert golem.store.step(step.id).state == "done"

    run(go())


def test_no_ci_at_all_is_something_to_fix(golem, monkeypatch):
    monkeypatch.setattr(scheduler, "CI_START_TIMEOUT", timedelta(0))

    async def go():
        await new_project(golem, [verdict("done"), verdict("done")])
        await golem.until("CI")
        golem.workspace.ci_results = [CI("none")]
        await golem.step()
        fix = current(golem, golem.store.jobs()[0].id)
        assert fix.name == "fix" and ".github/workflows/build.yml" in fix.problem

    run(go())


def test_fixing_in_circles_asks_riggy_for_a_hint(golem, monkeypatch):
    monkeypatch.setattr(scheduler, "MAX_FIXES", 1)

    async def go():
        room = await new_project(golem, [verdict("done"), verdict("done"), verdict("done")])
        await golem.until("deploy")
        golem.deployer.refuse = ["bad compose", "still bad compose"]
        await golem.step()
        await golem.until("deploy")
        await golem.step()
        job = golem.store.jobs()[0]
        assert job.state == "waiting"
        assert golem.chat.texts(room)[-1].startswith("I've tried to fix deploy 1 times and it still fails")
        await golem.say(room, "use port 8080")
        golem.harness.script = [verdict("done")]
        await golem.step()
        assert "> use port 8080" in golem.harness.calls[-1].prompt

    run(go())


def test_nothing_to_merge_still_ends_deployed(golem):
    async def go():
        await new_project(golem, [verdict("done"), verdict("done")])
        golem.workspace.has_work = False
        await golem.until("mvp")
        job = golem.store.jobs()[0]
        states = [(plan.label(s), s.state) for s in golem.store.steps(job.id)][3:8]
        assert states == [("push", "skipped"), ("PR", "skipped"), ("CI", "skipped"), ("merge", "skipped"), ("deploy", "done")]
        assert len(golem.deployer.deploys) == 1

    run(go())


# --- project rooms ------------------------------------------------------------


def test_a_message_in_an_idle_project_room_is_a_change_request(golem):
    async def go():
        room = await new_project(golem, [])
        golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")

        said = len(golem.chat.texts(room))
        await golem.say(room, "i dont like how the score works, count streaks instead")
        job = golem.store.active_job("tree-cards")
        assert (job.kind, job.playbook) == ("change", "feature")
        assert len(golem.chat.texts(room)) == said  # no "Got it": "Working on it" follows at once

        golem.harness.script = [verdict("done", "Scores count streaks."), verdict("done"), verdict("done", "Scores now count streaks.")]
        await golem.step()
        assert golem.chat.texts(room)[-1] == "Working on it: i dont like how the score works, count streaks instead"
        await golem.finish()
        assert golem.labels(job.id) == ["set up", "change", "review", "docs", "push", "PR", "CI", "merge", "deploy", "done"]
        assert "✓ change: Scores count streaks.\nNext: review." in golem.chat.texts(room)
        assert golem.chat.texts(room)[-1] == f"Done: Scores now count streaks.\n{URL}"
        assert golem.workspace.issues[-1] == ("tree-cards", "i dont like how the score works, count streaks instead")
        assert golem.workspace.actions[-2:] == ["merge PR #1", "CI on " + golem.workspace.main["tree-cards"][-2:] + ": passed"]

    run(go())


def test_a_change_in_reckless_mode_skips_the_review(golem):
    async def go():
        await new_project(golem, [])
        golem.store.update_project("tree-cards", mode="reckless")
        golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
        await golem.say("tree-cards", "bigger photos")
        await golem.finish()
        job = golem.store.jobs()[0]
        assert golem.labels(job.id) == ["set up", "change", "docs", "push", "PR", "CI", "merge", "deploy", "done"]
        assert golem.workspace.closed == []  # its PR closes its issue

    run(go())


def test_messages_while_a_job_runs_are_notes_for_its_next_step(golem):
    async def go():
        room = await new_project(golem, [])
        await golem.until("spec")
        job = golem.store.active_job("tree-cards")
        golem.store.update_job(job.id, state="running")
        await golem.say(room, "use oak and maple first")
        assert golem.chat.texts(room)[-1] == "Noted. I'll work that in at the next step of the current job."
        assert golem.store.active_job("tree-cards").id == job.id  # no second job
        golem.store.update_job(job.id, state="queued")
        golem.harness.script = [verdict("done")]
        await golem.step()
        assert "> use oak and maple first" in golem.harness.calls[-1].prompt

    run(go())


def test_blocked_resumes_the_same_session_with_the_answer(golem):
    async def go():
        room = await new_project(golem, [])
        await golem.until("spec")
        golem.harness.script = [RunResult("verdict", verdict("blocked", "Photos or drawings?"), session_id="s-1")]
        await golem.step()
        job = golem.store.active_job("tree-cards")
        assert (job.state, job.resume_session) == ("waiting", "s-1")
        assert current(golem, job.id).summary == "Asked you: Photos or drawings?"
        assert golem.chat.texts(room)[-1].startswith("Question: Photos or drawings?")

        await golem.say(room, "photos")
        golem.harness.script = [verdict("done")]
        await golem.step()
        call = golem.harness.calls[-1]
        assert call.resume == "s-1"
        assert call.prompt.startswith("Riggy has replied to your question:\n\n> photos")
        job = golem.store.job(job.id)
        assert (plan.label(current(golem, job.id)), job.resume_session) == ("scaffold", None)

    run(go())


# --- options ------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x86\x00\x00\x03\x4c rest"
STYLES = ("Round and soft", "Pastel", "Ink outline")


def by_label(answers: dict[str, list]):
    """A harness script that answers each kind of call from its own list; agent steps say done."""

    def answer(call):
        queue = answers.get(call.label) or []
        item = queue.pop(0) if queue else verdict("done")
        return RunResult("verdict", verdict=item(call) if callable(item) else item, session_id="s", usage={"num_turns": 1})

    return answer


def three_styles(call) -> dict:
    """An options step: a screenshot and a stylesheet per style, committed under docs/options/."""
    made = []
    for letter, title in zip("abc", STYLES):
        folder = f"docs/options/job-{call.env['GOLEM_JOB']}/{letter}"
        (call.cwd / folder).mkdir(parents=True, exist_ok=True)
        (call.cwd / folder / "hero.png").write_bytes(PNG)
        (call.cwd / folder / "hero.css").write_text("/* the style */")
        made.append({"title": title, "description": f"The hero, {title.lower()}.", "files": [f"{folder}/hero.png", f"{folder}/hero.css"]})
    return {**verdict("done", "Three chibi takes on the hero."), "options": made}


async def idle_project(golem: Golem) -> str:
    room = await new_project(golem, [])
    golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
    return room


def test_asking_to_see_options_shows_them_then_builds_the_pick(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label(
            {"request": [{"kind": "options"}], "options": [three_styles], "pick": [{"decision": "pick", "picked": ["B"]}]}
        )
        await golem.say(room, "i want a new art style, show me 3 that are more chibi")
        job = golem.store.active_job("tree-cards")
        assert (job.kind, job.playbook) == ("change", "options")
        ask = golem.harness.calls[-1]
        assert (ask.label, ask.model) == ("request", "haiku") and "show me 3 that are more chibi" in ask.prompt
        assert golem.chat.texts(room)[-1].startswith("Got it: I'll make some options for you to pick from first")

        await golem.until("your pick")
        assert golem.chat.texts(room)[-2:] == [
            "Making options: i want a new art style, show me 3 that are more chibi",
            "✓ options: Three chibi takes on the hero.\nNext: your pick, then change.",
        ]
        made = next(s for s in golem.store.steps(job.id) if s.name == "options")
        assert [o["images"] for o in made.options] == [[f"{made.id}-a-1.png"], [f"{made.id}-b-1.png"], [f"{made.id}-c-1.png"]]

        await golem.step()  # the pick: the options go up and golem waits
        choose = current(golem, job.id)
        assert golem.chat.images == [
            (room, f"{made.id}-{letter.lower()}-1.png", f"{letter}: {title}. The hero, {title.lower()}.")
            for letter, title in zip("ABC", STYLES)
        ]
        assert golem.chat.texts(room)[-1] == (
            "Which one? Reply A, B or C, with anything you'd change (“B, but bigger”), or ask for other options. "
            f"Side by side: https://golem.unlaunched.dev/steps/{choose.id}"
        )
        assert (golem.store.job(job.id).state, choose.state, choose.summary) == ("waiting", "waiting", "Waiting for your pick.")

        await golem.say(room, "b but bigger eyes")
        await golem.step()  # reads the reply
        pick = golem.harness.calls[-1]
        assert pick.label == "pick" and "B: Pastel. The hero, pastel." in pick.prompt and pick.prompt.rstrip().endswith("b but bigger eyes")
        said = f"You picked B (Pastel). You said: “b but bigger eyes”. Files: docs/options/job-{job.id}/b/."
        assert golem.chat.texts(room)[-1] == f"✓ your pick: {said}\nNext: change."

        await golem.step()  # builds it, with the pick and his words
        prompt = golem.harness.calls[-1].prompt
        assert f"- your pick: {said}" in prompt and "> b but bigger eyes" in prompt and "Build what Riggy picked" in prompt
        await golem.finish()
        assert golem.labels(job.id) == [
            "set up", "options", "your pick", "change", "review", "docs", "push", "PR", "CI", "merge", "deploy", "done"
        ]  # fmt: skip

    run(go())


def test_asking_for_other_options_makes_another_round(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label(
            {
                "request": [{"kind": "options"}],
                "options": [three_styles, three_styles],
                "pick": [{"decision": "more", "picked": []}, {"decision": "pick", "picked": ["C"]}],
            }
        )
        await golem.say(room, "show me some logo options")
        job = golem.store.active_job("tree-cards")
        await golem.until("your pick")
        await golem.step()
        await golem.say(room, "none of these, try something more pastel")
        await golem.step()
        assert golem.chat.texts(room)[-1] == (
            "✓ your pick: You asked for other options: “none of these, try something more pastel”\nNext: options."
        )
        await golem.step()  # the second round, with what he said about the first
        prompt = golem.harness.calls[-1].prompt
        assert golem.harness.calls[-1].label == "options" and "> none of these, try something more pastel" in prompt
        await golem.step()
        second = [s for s in golem.store.steps(job.id) if s.name == "options"][1]
        assert golem.chat.images[-1][1] == f"{second.id}-c-1.png"
        await golem.say(room, "C")
        await golem.finish()
        assert golem.labels(job.id)[:6] == ["set up", "options", "your pick", "options", "your pick", "change"]
        picks = [s.summary for s in golem.store.steps(job.id) if s.kind == "choose"]
        assert picks[1] == f"You picked C (Ink outline). You said: “C”. Files: docs/options/job-{job.id}/c/."

    run(go())


def test_options_on_command_and_never_beside_another_job(golem):
    async def go():
        room = await idle_project(golem)
        await golem.say(room, "!options a new logo")
        job = golem.store.active_job("tree-cards")
        assert (job.playbook, job.request) == ("options", "a new logo")
        assert all(call.label != "request" for call in golem.harness.calls)  # no need to ask the model
        await golem.say(room, "!options a new font")
        assert golem.chat.texts(room)[-1] == f"Tree Cards has a job under way (#{job.id}). Ask again when it's done, or !kill it first."
        await golem.say(golem.inbox, "!options")
        assert golem.chat.texts(golem.inbox)[-1].startswith("Usage: !options")

    run(go())


def test_a_change_request_is_not_options(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"kind": "change"}]})
        await golem.say(room, "show the score bigger")
        assert golem.store.active_job("tree-cards").playbook == "feature"

    run(go())


def newest_job(golem: Golem):
    return golem.store.jobs(limit=1)[0]


def test_a_question_is_answered_without_building_anything(golem):
    from golem.bot import QUESTION_PRIORITY

    answer = "It resets when a streak ends: see src/score.ts."
    link = "https://github.com/unlaunched/tree-cards/blob/main/src/score.ts"

    async def go():
        room = await idle_project(golem)
        before = len(golem.workspace.actions)
        golem.harness.script = by_label({"request": [{"kind": "question"}], "examine": [verdict("done", answer, link)]})
        await golem.say(room, "why does my score reset")
        job = newest_job(golem)
        assert (job.playbook, job.priority) == ("question", QUESTION_PRIORITY)
        assert golem.store.active_job("tree-cards", questions=False) is None  # no change under way
        await golem.finish()
        assert golem.store.job(job.id).state == "done" and golem.labels(job.id) == ["catch up", "examine", "your answer"]
        assert golem.chat.sent[-2:] == [(room, "Looking into it: why does my score reset", True), (room, f"{answer}\n{link}", False)]
        assert golem.workspace.actions[before:] == ["discard"]  # nothing pushed, merged or deployed; the checkout put back
        call = next(c for c in golem.harness.calls if c.label == "examine")
        assert "> why does my score reset" in call.prompt and "Change nothing" in call.prompt and "(what's live)" in call.prompt
        assert re.search(r"\$[a-z]", call.prompt) is None  # fully filled in

    run(go())


def test_a_question_while_a_job_is_under_way_comes_first_and_is_not_a_note(golem):
    async def go():
        room = await new_project(golem, [])
        job = golem.store.active_job("tree-cards")
        await golem.until("scaffold")
        golem.harness.script = by_label({"request": [{"kind": "question"}]})
        await golem.say(room, "what stack did you pick?")
        question = newest_job(golem)
        assert question.playbook == "question" and golem.store.pending_notes(job.id) == []
        await golem.step()  # the question goes ahead of the job's next step
        assert golem.labels(question.id) == ["catch up"] and golem.labels(job.id)[-1] == "spec"
        await golem.step()
        call = golem.harness.calls[-1]
        assert call.label == "examine" and "a job is under way" in call.prompt and "→ " not in call.prompt.split("newest first:")[1][:5]
        await golem.finish()
        assert golem.store.job(question.id).state == "done" and golem.store.job(job.id).state == "waiting"  # on to its check-in

    run(go())


def test_question_on_command_and_its_question_back(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"examine": [verdict("blocked", "Which score: the streak or the total?"), verdict("done", "The streak.")]})
        await golem.say(room, "!question which score resets")
        assert all(call.label != "request" for call in golem.harness.calls)  # no need to ask the model
        question = newest_job(golem)
        await golem.finish()
        assert golem.store.job(question.id).state == "waiting" and "Which score" in golem.chat.texts(room)[-1]
        await golem.say(room, "the streak one")  # his reply to it, not a change
        assert golem.store.job(question.id).state == "queued" and golem.store.active_job("tree-cards", questions=False) is None
        await golem.finish()
        assert golem.chat.texts(room)[-1] == "The streak." and golem.harness.calls[-1].resume == "s"
        await golem.say(golem.inbox, "!question")
        assert golem.chat.texts(golem.inbox)[-1].startswith("Usage: !question")

    run(go())


# --- proposals ------------------------------------------------------------------


OFFLINE = {"title": "Offline mode", "what": "Cache the deck in a service worker.", "why": "Hikes have no signal.", "size": "medium"}
BIGGER = {"title": "Bigger cards", "what": "Fill the screen on phones.", "why": "They're small on a phone.", "size": "small"}


def proposing(*made: dict, message: str = "Played a few rounds on a phone.") -> dict:
    return {**verdict("done", message), "proposals": list(made)}


def long_ago(golem: Golem) -> None:
    """As if the project's proposals jobs ran long enough ago to be due again."""
    golem.store.db.execute("UPDATE jobs SET created_at = '2026-01-01T00:00:00+00:00' WHERE playbook = 'proposals'")


def test_with_nothing_else_to_do_golem_proposes_and_riggy_decides(golem):
    from golem import proposals
    from golem.control import Control, ControlError

    async def go():
        room = await idle_project(golem)
        assert golem.store.project("tree-cards").proposals == "off" and golem.scheduler.propose() is None  # off by default
        golem.store.update_project("tree-cards", proposals="daily")
        job = golem.scheduler.propose()
        assert (job.playbook, job.priority, job.request) == ("proposals", proposals.PRIORITY, "Look at its features for things to propose")
        assert golem.scheduler.propose() is None  # one at a time

        before = len(golem.workspace.actions)
        golem.harness.script = by_label({"propose": [proposing(OFFLINE, BIGGER)]})
        await golem.finish()
        assert golem.store.job(job.id).state == "done" and golem.labels(job.id) == ["catch up", "propose", "proposals for you"]
        assert golem.workspace.actions[before:] == ["discard"]  # nothing pushed, merged or deployed; the checkout put back
        call = next(c for c in golem.harness.calls if c.label == "propose")
        assert call.schema == proposals.SCHEMA and "# This time's lens: features" in call.prompt
        assert "(none yet: these are the first)" in call.prompt and re.search(r"\$[a-z]", call.prompt) is None
        offline, bigger = golem.store.job_proposals(job.id)
        assert (offline.title, offline.lens, offline.state) == ("Offline mode", "features", "open")
        assert golem.chat.texts(room)[-2] == "Nothing else on, so I'm looking at its features for things to propose."
        assert golem.workspace.issues[-2:] == [("tree-cards", "Proposal: Offline mode"), ("tree-cards", "Proposal: Bigger cards")]
        assert (offline.issue, bigger.issue) == (len(golem.workspace.issues) - 1, len(golem.workspace.issues))
        said = golem.chat.sent[-1]
        assert said[0] == room and not said[2]  # it notifies him
        assert said[1].startswith("I looked at its features and have 2 proposals for you:\n\n1. Offline mode\nCache the deck")
        assert said[1].endswith("Thumbs up or down (and why) on its page: https://golem.unlaunched.dev/p/tree-cards")
        assert golem.scheduler.propose() is None  # not due again for a day

        control = Control(golem.store, golem.scheduler)
        assert "takes that into account" in await control.decide(offline.id, False, "  it's a web  app, always online ")
        assert await control.decide(bigger.id, True, "") == f"Accepted “Bigger cards”: job {job.id + 1} builds it."
        built = golem.store.active_job("tree-cards")
        assert (built.playbook, built.request.splitlines()[0]) == ("feature", "Bigger cards")
        assert "Riggy accepted it." in built.request and "Fill the screen on phones." in built.request
        assert golem.store.proposal(bigger.id).built_by == built.id and built.issue == bigger.issue  # its PR closes it
        assert golem.workspace.comments == [
            ("tree-cards", offline.issue, "👎 Turned down: “it's a web app, always online”", True),
            ("tree-cards", bigger.issue, "👍 Accepted. golem builds it next; its PR closes this.", False),
        ]
        assert golem.chat.sent[-1] == (room, f"Building the proposal you accepted: Bigger cards (job {built.id}).", True)
        heard = [m.text for m in golem.store.messages(room) if m.who == "riggy"]
        assert heard[-2:] == ["👎 Offline mode: it's a web app, always online", "👍 Bigger cards"]
        with pytest.raises(ControlError, match="decided already"):
            await control.decide(bigger.id, False, "")

        issues = len(golem.workspace.issues)
        await golem.finish()  # the accepted one is built and deployed
        assert len(golem.workspace.issues) == issues and f"Closes #{bigger.issue}" in golem.workspace.bodies[-1]
        long_ago(golem)
        again = golem.scheduler.propose()
        golem.harness.script = by_label({"propose": [proposing()]})
        await golem.finish()
        call = [c for c in golem.harness.calls if c.label == "propose"][-1]
        assert "# This time's lens: code health" in call.prompt  # the next lens in turn
        assert "- [features] Bigger cards — accepted\n- [features] Offline mode — turned down: “it's a web app, always online”" in call.prompt
        assert golem.chat.texts(room)[-1].startswith("I looked at its code health and found nothing worth proposing.")
        assert golem.store.job_proposals(again.id) == []

    run(go())


def test_proposals_wait_until_golem_and_the_project_are_free(golem):
    from golem import proposals
    from golem.control import Control

    async def go():
        room = await new_project(golem, [])
        golem.store.update_project("tree-cards", proposals="hourly")
        assert golem.scheduler.propose() is None  # not live yet
        golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
        golem.store.set_paused("tree-cards", True)
        assert golem.scheduler.propose() is None
        golem.store.set_paused("tree-cards", False)
        golem.store.add_project("other", "Other", "Something else.")
        other = golem.store.add_job("other", "new", "something else", "standard")
        assert golem.scheduler.propose() is None  # golem has other work
        golem.store.update_job(other.id, state="waiting")  # waiting on Riggy doesn't hold the worker
        assert golem.scheduler.propose() is not None

        # Proposals are only made while fewer await him than the project allows: 5 unless he says.
        assert golem.store.project("tree-cards").proposals_max == 5
        golem.harness.script = by_label({"propose": [proposing(OFFLINE, BIGGER, {**BIGGER, "title": "Sound"})] * 2})
        await golem.finish()
        long_ago(golem)
        assert golem.scheduler.propose() is not None
        await golem.finish()
        open_ = golem.store.proposals("tree-cards", ("open",))
        assert len(open_) == 6
        long_ago(golem)
        assert golem.scheduler.propose() is None
        golem.store.update_project("tree-cards", proposals_max=8)
        assert golem.scheduler.propose() is not None
        golem.harness.script = by_label({"propose": [proposing()]})
        await golem.finish()

        # Accepted while a change is under way: built once it's done.
        golem.harness.script = by_label({"request": [{"kind": "change"}]})
        await golem.say(room, "make the cards flip faster")
        change = golem.store.active_job("tree-cards")
        await Control(golem.store, golem.scheduler).decide(open_[0].id, True, "yes please")
        assert golem.store.proposal(open_[0].id).built_by is None
        await golem.finish()
        assert golem.store.job(change.id).state == "done"
        await golem.scheduler.build_accepted()
        built = golem.store.active_job("tree-cards")
        assert built.id != change.id and built.request.endswith("Riggy accepted it: “yes please”)")

    run(go())


def test_a_proposals_job_never_asks_riggy_anything(golem):
    async def go():
        room = await idle_project(golem)
        golem.store.update_project("tree-cards", proposals="weekly")
        job = golem.scheduler.propose()
        golem.harness.script = by_label({"propose": [verdict("blocked", "Should I look at the admin pages too?")]})
        await golem.finish()
        assert golem.store.job(job.id).state == "done"  # a question back is nothing to propose
        assert golem.chat.texts(room)[-1].startswith("I looked at its features and found nothing worth proposing.")

        long_ago(golem)
        job = golem.scheduler.propose()
        golem.harness.script = lambda call: RunResult("error", error="boom")
        await golem.finish()
        assert golem.store.job(job.id).state == "failed"  # not waiting on him: nobody asked for it
        assert golem.chat.texts(room)[-1] == "I couldn't finish looking for proposals: boom"

    run(go())


def test_requests_phrased_as_questions_are_changes():
    from golem.options import asks_a_question

    assert asks_a_question("why does the score reset") and asks_a_question("is it live yet?")
    assert not asks_a_question("can you add a quiz mode?") and not asks_a_question("how about adding dark mode?")
    assert not asks_a_question("the score is broken")


def test_an_options_step_without_options_tries_again(golem):
    async def go():
        room = await idle_project(golem)
        await golem.say(room, "!options a new logo")
        job = golem.store.active_job("tree-cards")
        await golem.until("options")
        golem.harness.script = by_label({"options": [verdict("done", "Here are some ideas.")]})
        await golem.step()
        step = current(golem, job.id)
        assert (step.name, step.state, step.error) == ("options", "pending", "The options step returned fewer than two options with their files.")

    run(go())


# --- failures -----------------------------------------------------------------


def test_usage_limit_parks_the_job_and_says_so_once(golem):
    async def go():
        await new_project(golem, [])
        await golem.until("spec")
        golem.harness.script = [RunResult("limit", error="usage limit reached"), RunResult("limit")]
        status = "status"
        await golem.step()
        job = golem.store.active_job("tree-cards")
        assert job.state == "parked" and job.attempts == 0
        assert golem.store.next_job() is None
        soon = (datetime.now(UTC) + timedelta(minutes=31)).isoformat(timespec="seconds")
        assert golem.store.next_job(at=soon).id == job.id
        await golem.scheduler.step(golem.store.job(job.id))
        notices = [t for t in golem.chat.texts(status) if "usage limit" in t]
        assert len(notices) == 1

    run(go())


def test_repeated_errors_ask_riggy_for_help(golem):
    async def go():
        room = await new_project(golem, [])
        await golem.until("spec")
        golem.harness.script = [RunResult("error", error="npm exploded")] * 3
        job = golem.store.active_job("tree-cards")
        for attempt in (1, 2):
            await golem.scheduler.step(golem.store.job(job.id))
            assert (golem.store.job(job.id).state, golem.store.job(job.id).attempts) == ("parked", attempt)
        await golem.scheduler.step(golem.store.job(job.id))
        assert golem.store.job(job.id).state == "waiting"
        assert golem.chat.texts(room)[-1].startswith("I'm stuck on spec after 3 tries: npm exploded")
        assert current(golem, job.id).error == "npm exploded"

    run(go())


def test_running_out_of_turns_gets_a_wrapup_in_the_same_session(golem):
    async def go():
        await new_project(golem, [])
        await golem.until("spec")
        golem.harness.script = [
            RunResult("max_turns", session_id="s-9"),
            {"finished": False, "message": "The spec has no out-of-scope list yet."},
        ]
        await golem.step()
        wrapup = golem.harness.calls[-1]
        assert wrapup.resume == "s-9" and wrapup.max_turns == 15 and wrapup.schema == WRAPUP_SCHEMA
        assert wrapup.prompt.startswith("You've used up this step's turns.")
        job = golem.store.active_job("tree-cards")
        step = current(golem, job.id)
        assert (step.name, step.error) == ("spec", "ran out of turns: The spec has no out-of-scope list yet.")

        # The next attempt is told, and carries on from the commits.
        golem.harness.script = [RunResult("max_turns", session_id="s-10"), {"finished": True, "message": "Spec written."}]
        await golem.step()
        assert "didn't finish: ran out of turns: The spec has no out-of-scope list yet." in golem.harness.calls[-2].prompt
        assert plan.label(current(golem, job.id)) == "scaffold"
        assert [r["phase"] for r in golem.store.runs(job.id)] == ["spec", "spec-wrapup", "spec", "spec-wrapup"]

    run(go())


def test_interrupted_jobs_carry_on_from_their_step(golem):
    async def go():
        await new_project(golem, [])
        await golem.until("mvp")
        job = golem.store.active_job("tree-cards")
        golem.store.update_job(job.id, state="running")
        assert [j.id for j in golem.store.requeue_interrupted()] == [job.id]
        assert plan.label(current(golem, golem.store.next_job().id)) == "mvp"

    run(go())


def test_jobs_from_before_steps_pick_up_where_they_were(golem):
    async def go():
        await new_project(golem, [])
        job = golem.store.active_job("tree-cards")
        # Phase 3 of the old standard playbook was buildout; it had run before.
        golem.store.update_job(job.id, phase=3, state="queued")
        golem.store.start_run(job.id, "buildout", "opus/high", "log")
        steps = golem.scheduler.ensure_steps(golem.store.job(job.id))
        assert [s.state for s in steps[:15]] == ["done"] * 15 and steps[0].summary == plan.LEGACY_DONE
        assert plan.label(current(golem, job.id)) == "buildout"

    run(go())


def test_kill_stops_the_running_step(golem):
    started = asyncio.Event()

    class Hangs:
        calls = []

        async def run(self, **kwargs):
            started.set()
            await asyncio.sleep(3600)

    async def go():
        await new_project(golem, [])
        golem.scheduler.harness = Hangs()
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await asyncio.wait_for(started.wait(), 5)
        job = golem.store.active_job("tree-cards")
        await golem.say("tree-cards", "!kill")
        assert golem.chat.sent[-1][1] == f"Killed job {job.id} (Tree Cards)."
        for _ in range(50):
            if golem.scheduler.current is None:
                break
            await asyncio.sleep(0.01)
        assert golem.store.job(job.id).state == "killed"
        assert golem.store.runs(job.id)[-1]["outcome"] == "killed"
        loop.cancel()

    run(go())


class Worker:
    """An agent step run by `act(log_path, env)`, which writes what a worker would."""

    def __init__(self, act):
        self.act = act

    async def run(self, *, log_path, env, **kwargs):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return await self.act(log_path, env)


def log_event(log, event: dict) -> None:
    log.write(json.dumps(event) + "\n")
    log.flush()


def test_a_running_step_passes_on_progress_and_says_what_it_is_on(golem, monkeypatch):
    monkeypatch.setattr(scheduler, "MONITOR_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "HEARTBEAT_SECONDS", 0.15)

    async def act(log_path, env):
        with gzip.open(log_path, "wt") as log:
            text = {"type": "text", "text": "Reading the brief."}
            log_event(log, {"type": "assistant", "message": {"content": [text]}})
            with open(env["GOLEM_PROGRESS"], "a") as notes:
                notes.write("The spec is drafted; checking it against the idea.\n")
            command = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}}
            log_event(log, {"type": "assistant", "message": {"content": [command]}})
            await asyncio.sleep(0.4)
        return RunResult("verdict", verdict=verdict("done", "Spec written."))

    async def go():
        room = await new_project(golem, [])
        await golem.until("spec")
        golem.scheduler.harness = Worker(act)
        await golem.step()
        texts = golem.chat.texts(room)
        assert "▸ The spec is drafted; checking it against the idea." in texts
        still = [t for t in texts if t.startswith("Still on")]
        assert still[0] == 'Still on spec (step 2/24), 0s in. Last said: "Reading the brief." Now running Bash npm test.'
        assert texts[-1].startswith("✓ spec: Spec written.")

    run(go())


def test_a_step_that_stops_doing_anything_is_restarted(golem, monkeypatch):
    monkeypatch.setattr(scheduler, "MONITOR_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "STUCK_SECONDS", 0.2)

    async def act(log_path, env):
        with gzip.open(log_path, "wt") as log:
            log_event(log, {"type": "system", "subtype": "init"})
            await asyncio.sleep(3600)

    async def go():
        room = await new_project(golem, [])
        await golem.until("spec")
        golem.scheduler.harness = Worker(act)
        job = golem.store.active_job("tree-cards")
        await golem.step()
        assert golem.chat.texts(room)[-1] == "No sign of life in spec for 0s, so I'm restarting it."
        assert (golem.store.job(job.id).state, golem.store.job(job.id).attempts) == ("parked", 1)
        assert golem.store.runs(job.id)[-1]["outcome"] == "error"

    run(go())


# --- commands -----------------------------------------------------------------


def test_commands(golem):
    async def go():
        room = await new_project(golem, [])
        inbox = golem.inbox

        await golem.say(inbox, "!queue")
        job = golem.store.active_job("tree-cards")
        assert golem.chat.sent[-1] == (inbox, f"#{job.id} Tree Cards: new project, starting shortly, set up (1/24)", True)

        await golem.say(room, "!status")
        assert golem.chat.texts(room)[-1].startswith("Tree Cards (tree-cards)\nMode standard, each step's default model and effort\n#")

        await golem.say(room, "!pause")
        assert golem.store.project("tree-cards").paused
        assert golem.store.next_job() is None
        await golem.say(inbox, "!resume tree-cards")
        assert golem.store.next_job().id == job.id

        await golem.say(inbox, "!pause")
        assert golem.scheduler.paused
        await golem.say(inbox, "!queue")
        assert golem.chat.texts(inbox)[-1].startswith("Work is paused.")
        await golem.say(inbox, "!resume")
        assert not golem.scheduler.paused

        await golem.say(inbox, "!kill")
        assert golem.chat.texts(inbox)[-1] == "Which job? !kill <number> (see !queue)."
        await golem.say(inbox, "!nope")
        assert golem.chat.texts(inbox)[-1].startswith("Unknown command !nope.")

    run(go())


def test_golem_news_is_not_an_inbox(golem):
    async def go():
        await golem.say("status", "a todo app")
        assert golem.store.projects() == [] and golem.store.messages("status") == []

    run(go())


# --- settings, names and slugs ------------------------------------------------


def test_dictated_settings_pick_the_playbook_model_and_effort(golem):
    async def go():
        golem.harness.script = [{**TREES, "name": "Leaf Peeper", "slug": "leaf-peeper", "mode": "rigorous", "model": "fable", "effort": "max"}]
        await golem.say(golem.inbox, "make a game called Leaf Peeper, rigorous, fable, max effort")
        project = golem.store.project("leaf-peeper")
        assert (project.name, project.mode, project.model, project.effort) == ("Leaf Peeper", "rigorous", "fable", "max")
        assert golem.store.active_job("leaf-peeper").playbook == "rigorous"
        await golem.until("spec")
        golem.harness.script = [verdict("done")]
        await golem.step()
        call = golem.harness.calls[-1]
        assert (call.model, call.effort) == ("fable", "max")
        assert "# Your step: spec" in call.prompt and "**rigorous mode**" in call.prompt
        assert len(golem.store.steps(golem.store.active_job("leaf-peeper").id)) == 26
        assert golem.store.runs()[0]["model"] == "fable/max"

    run(go())


def test_unspecified_settings_use_the_defaults(tmp_path):
    golem = Golem(tmp_path, default_mode="reckless", default_model="sonnet", default_effort="xhigh")

    async def go():
        golem.harness.script = [{**TREES, "mode": "unspecified", "model": "unspecified", "effort": "nonsense"}]
        await golem.say(golem.inbox, "trees")
        project = golem.store.project("tree-cards")
        assert (project.mode, project.model, project.effort) == ("reckless", "", "")  # each step's default
        assert golem.store.active_job("tree-cards").playbook == "reckless"
        await golem.until("build")
        await golem.step()
        assert (golem.harness.calls[-1].model, golem.harness.calls[-1].effort) == ("sonnet", "xhigh")

    run(go())


def test_settings_commands(golem):
    async def go():
        room = await new_project(golem, [])
        job = golem.store.active_job("tree-cards")

        await golem.say(room, "!mode rigorous")
        assert golem.chat.texts(room)[-1] == (
            "Tree Cards: mode rigorous, each step's default model and effort. "
            "The new-project job will follow the rigorous playbook."
        )
        assert golem.store.job(job.id).playbook == "rigorous"

        await golem.say(golem.inbox, "!effort tree-cards max")
        assert golem.store.project("tree-cards").effort == "max"
        await golem.say(room, "!model sonnet")
        await golem.say(room, "!model gpt")
        assert golem.chat.texts(room)[-1] == "Usage: !model fable|opus|sonnet|haiku|default"
        await golem.say(golem.inbox, "!mode rigorous")
        assert golem.chat.texts(golem.inbox)[-1] == "Usage: !mode <project> reckless|standard|rigorous"

        await golem.until("spec")
        golem.harness.script = [verdict("done")]
        await golem.step()
        assert (golem.harness.calls[-1].model, golem.harness.calls[-1].effort) == ("sonnet", "max")

        # Once the job has started, its steps stay.
        await golem.say(room, "!mode reckless")
        assert golem.store.job(job.id).playbook == "rigorous"
        assert "keeps its steps" in golem.chat.texts(room)[-1]

        await golem.say(room, "!status")
        assert "Mode reckless, model sonnet, max effort" in golem.chat.texts(room)[-1]
        await golem.say(room, "!model default")
        assert golem.store.project("tree-cards").model == ""
        assert "mode reckless, each step's default model, max effort" in golem.chat.texts(room)[-1]

    run(go())


def test_rename(golem):
    async def go():
        room = await new_project(golem, [])
        await golem.say(room, "!rename Leaf Peeper")
        assert golem.store.project("tree-cards").name == "Leaf Peeper"
        await golem.say(golem.inbox, "!rename tree-cards Canopy Quiz")
        assert golem.store.project("tree-cards").name == "Canopy Quiz"

    run(go())


def test_slug_moves_the_project_and_queues_the_reference_fixes(golem):
    async def go():
        room = await new_project(golem, [])
        old_job = golem.store.jobs()[0]
        golem.store.update_job(old_job.id, state="done")

        await golem.say(room, "!slug leaf-peeper")
        reply = golem.chat.texts("leaf-peeper")[-1]  # the conversation moves with it
        assert reply.startswith("Moved Tree Cards to leaf-peeper: https://github.com/unlaunched/leaf-peeper")
        assert "https://github.com/unlaunched/tree-cards is still there" in reply
        assert golem.store.project("tree-cards") is None
        project = golem.store.project("leaf-peeper")
        assert project.name == "Tree Cards"
        assert golem.store.job(old_job.id).slug == "leaf-peeper"  # history moves with it
        assert golem.deployer.stopped == ["tree-cards"]
        assert golem.workspace.path("leaf-peeper").is_dir() and not golem.workspace.path("tree-cards").exists()

        fix = golem.store.active_job("leaf-peeper")
        assert (fix.kind, fix.playbook) == ("change", "feature")
        assert "moved from `tree-cards` to `leaf-peeper`" in fix.request

    run(go())


async def a_run(golem: Golem) -> None:
    """Work the new project's job as far as its first agent run."""
    await golem.until("spec")
    golem.harness.script = [verdict("done")]
    await golem.step()


def test_delete_asks_first_then_takes_everything_but_the_repo(golem):
    async def go():
        room = await new_project(golem, [])
        await a_run(golem)  # a log and a checkout
        job = golem.store.jobs()[0]
        golem.store.add_note(job.id, "use oak first")
        logs = golem.settings.job_logs(job.id)
        assert logs.is_dir() and golem.workspace.path("tree-cards").is_dir()

        await golem.say(room, "!delete")
        assert golem.chat.texts(room)[-1] == (
            "This deletes Tree Cards for good: the site and its data, the code checkout, every job and its logs, "
            "and this conversation. The repo stays on GitHub for you to delete. To go ahead, say !delete tree-cards"
        )
        assert golem.store.project("tree-cards") is not None and golem.deployer.deleted == []

        await golem.say(room, "!delete tree-cards")
        assert golem.deployer.deleted == ["tree-cards"]
        assert not golem.workspace.path("tree-cards").exists() and not logs.exists()
        assert golem.store.project("tree-cards") is None
        assert golem.store.jobs() == [] and golem.store.runs() == [] and golem.store.pending_notes(job.id) == []
        assert golem.store.steps(job.id) == []
        assert golem.store.messages(room) == []
        # The conversation is gone, so the answer comes in the inbox.
        assert golem.chat.texts(golem.inbox)[-1] == (
            "Deleted Tree Cards: its site and data, checkout, jobs, logs and conversation are gone. Delete the repo yourself "
            "on GitHub (Settings, at the bottom): https://github.com/unlaunched/tree-cards/settings"
        )
        assert golem.store.next_job() is None

    run(go())


def test_delete_from_the_inbox_needs_the_slug(golem):
    async def go():
        await new_project(golem, [])
        await golem.say(golem.inbox, "!delete")
        assert golem.chat.texts(golem.inbox)[-1] == "Usage: !delete <slug>. It deletes the project for good."
        await golem.say(golem.inbox, "!delete trees")
        assert golem.chat.texts(golem.inbox)[-1] == "No project called trees."
        assert golem.store.project("tree-cards") is not None
        await golem.say(golem.inbox, "!delete tree-cards")
        assert golem.store.project("tree-cards") is None
        assert golem.chat.texts(golem.inbox)[-1].startswith("Deleted Tree Cards:")

    run(go())


def test_delete_stops_a_running_step_first(golem):
    started = asyncio.Event()

    class Hangs:
        async def run(self, **kwargs):
            started.set()
            await asyncio.sleep(3600)

    async def go():
        room = await new_project(golem, [])
        golem.scheduler.harness = Hangs()
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await asyncio.wait_for(started.wait(), 5)
        task = golem.scheduler.current[1]
        await golem.say(room, "!delete tree-cards")
        assert task.done()  # stopped before anything was deleted
        assert golem.store.project("tree-cards") is None and golem.store.runs() == []
        assert golem.deployer.deleted == ["tree-cards"]
        loop.cancel()

    run(go())


def test_a_failed_delete_keeps_the_project_and_can_be_retried(golem):
    async def go():
        room = await new_project(golem, [])
        golem.deployer.fail = "deleting is off: DEPLOYER_ADMIN_SECRET is not set"
        await golem.say(room, "!delete tree-cards")
        assert golem.chat.texts(room)[-1] == (
            "Couldn't delete all of Tree Cards: deleting is off: DEPLOYER_ADMIN_SECRET is not set. "
            "Its jobs are stopped; say !delete tree-cards to try again."
        )
        assert golem.store.project("tree-cards") is not None
        assert golem.store.jobs()[0].state == "killed"

        golem.deployer.fail = None
        await golem.say(room, "!delete tree-cards")
        assert golem.store.project("tree-cards") is None

    run(go())


def test_nuke_says_what_will_go_then_deletes_it_all(golem):
    async def go():
        room = await new_project(golem, [])
        await a_run(golem)
        golem.deployer.deployed = ["hello", "tree-cards", "zz-old"]
        lessons = golem.settings.state_dir / "lessons.md"
        lessons.write_text("- a lesson\n")
        golem.workspace.path("zz-old").mkdir()

        assert await nuke.plan(golem.settings, golem.store, golem.deployer) == (
            "This deletes, for good:\n"
            "- 1 project(s), each with its site and data, checkout, jobs, run logs and conversation:\n"
            "    tree-cards (Tree Cards)\n"
            "- 2 more site(s) the deployer has, with their data: hello, zz-old\n"
            f"- everything in {golem.settings.workspace_dir}, golem's run logs, triage log, lessons.md and proposals\n"
            "Kept: the stack, golem's own conversation, and .env.\n"
            "Not touched: the GitHub repos. Delete them at https://github.com/orgs/unlaunched/repositories"
        )
        assert golem.store.project("tree-cards") and golem.deployer.deleted == []  # planning deletes nothing

        said = []
        problems = await nuke.wipe(golem.settings, golem.store, golem.workspace, golem.deployer, said.append)
        assert problems == []
        assert said == ["deleted project tree-cards", "deleted site hello", "deleted site zz-old"]
        assert golem.deployer.deleted == ["tree-cards", "hello", "zz-old"]
        assert golem.store.projects() == [] and golem.store.jobs() == [] and golem.store.runs() == []
        assert list(golem.settings.workspace_dir.iterdir()) == []
        assert not lessons.exists() and not (golem.settings.state_dir / "jobs").exists()
        # The bot itself carries on as before, with golem's own conversation.
        assert golem.chat.texts(golem.inbox) and (golem.settings.state_dir / "golem.db").exists()
        assert await nuke.plan(golem.settings, golem.store, golem.deployer) == nuke.NOTHING

    run(go())


def test_nuke_reports_what_it_could_not_delete(golem):
    async def go():
        await new_project(golem, [])
        golem.deployer.deployed = ["hello"]
        golem.deployer.fail = "deleting is off: DEPLOYER_ADMIN_SECRET is not set"
        problems = await nuke.wipe(golem.settings, golem.store, golem.workspace, golem.deployer, print)
        assert problems == [
            "tree-cards: deleting is off: DEPLOYER_ADMIN_SECRET is not set",
            "hello: deleting is off: DEPLOYER_ADMIN_SECRET is not set",
        ]
        assert golem.store.project("tree-cards") is not None  # a second run retries it

    run(go())


def test_slug_refuses_bad_or_taken_slugs_and_running_jobs(golem):
    async def go():
        room = await new_project(golem, [])
        golem.workspace.repos.add("taken")
        await golem.say(room, "!slug Golem")
        assert "won't work as an address" in golem.chat.texts(room)[-1]
        await golem.say(room, "!slug taken")
        assert golem.chat.texts(room)[-1] == "taken is taken (by a project or an existing repo)."
        golem.store.update_job(golem.store.active_job("tree-cards").id, state="running")
        await golem.say(room, "!slug oaks")
        assert golem.chat.texts(room)[-1].startswith("A step is running.")
        assert golem.store.project("tree-cards") is not None

    run(go())


# --- the conversation -----------------------------------------------------------


def test_everything_said_is_kept_by_channel(golem):
    async def go():
        room = await new_project(golem, [])
        inbox = [(m.who, m.text[:30]) for m in golem.store.messages("inbox")]
        assert inbox == [("riggy", "a flashcard game for identifyi"), ("golem", "New project: Tree Cards (tree-")]
        intro = golem.store.messages("tree-cards")[0]
        assert (intro.who, intro.notify) == ("golem", True) and intro.text.startswith("Tree Cards\n\nA flashcard game")

        await golem.say(room, "!status")
        said = golem.store.messages("tree-cards")[-2:]
        assert [(m.who, m.notify) for m in said] == [("riggy", False), ("golem", False)]  # a notice: not pushed

        golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
        golem.harness.script = by_label({"request": [{"kind": "change"}]})
        await golem.say(room, "make the cards bigger")
        said = golem.store.messages("tree-cards")[-1]
        assert (said.who, said.text) == ("riggy", "make the cards bigger")
        assert golem.store.active_job("tree-cards").request == "make the cards bigger"

        golem.store.move_project("tree-cards", "trees")
        assert golem.store.messages("tree-cards") == [] and len(golem.store.messages("trees")) == 4

    run(go())
