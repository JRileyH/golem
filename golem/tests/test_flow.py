"""End-to-end flows through the bot and scheduler, with GitHub and Claude faked."""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import REPO, Golem

from golem import nuke, plan, scheduler, turns
from golem.harness import WRAPUP_SCHEMA, RunResult
from golem.workspace import CI

TREES = {"name": "Tree Cards", "slug": "tree-cards", "summary": "A flashcard game for northeastern US trees.", "title": "Tree Flashcard Game"}
URL = "https://tree-cards.unlaunched.dev/"
CHECKIN = ["set up", "spec", "scaffold", "push", "PR", "CI", "merge", "deploy", "mvp", "push", "PR", "CI", "merge", "deploy"]


def run(coro):
    return asyncio.run(coro)


def verdict(kind: str, message: str = "ok", link: str = "") -> dict:
    return {"verdict": kind, "message": message, **({"link": link} if link else {})}


async def new_project(golem: Golem, script_after: list) -> str:
    """Start the trees idea on the New project form; returns the project's slug."""
    golem.harness.script = [TREES, *script_after]  # TREES answers the summary call
    await golem.new_project("a flashcard game for identifying northeastern us trees")
    return "tree-cards"


def current(golem: Golem, job_id: int):
    return plan.current(golem.store.steps(job_id))


# --- new projects -------------------------------------------------------------


def test_a_new_project_is_named_by_riggy_and_starts_at_once(golem):
    async def go():
        room = await new_project(golem, [])
        project = golem.store.project("tree-cards")
        assert (project.name, project.summary, project.mode, room) == ("Tree Cards", "A flashcard game for northeastern US trees.", "", "tree-cards")
        job = golem.store.active_job("tree-cards")
        # No grace: the form said everything it was for.
        assert (job.kind, job.workflow, job.mode, job.state, job.title) == ("new", "web_app", "", "queued", "Tree Flashcard Game")
        assert job.request == "a flashcard game for identifying northeastern us trees"
        assert [m.text for m in golem.store.job_messages(job.id)] == [job.request]  # its activity starts with what he said
        # golem only writes the summary and the job's title: a cheap structured call.
        call = golem.harness.calls[0]
        assert (call.model, call.label) == ("haiku", "summary") and "GOLEM_WEB_PASSWORD" not in call.env
        assert "Its name: Tree Cards" in call.prompt and "a flashcard game for identifying northeastern us trees" in call.prompt

    run(go())


def test_a_new_project_takes_the_workflow_mode_tag_and_engine_riggy_picked(golem):
    async def go():
        golem.harness.script = [TREES]
        job = await golem.new_project("trees, quickly", workflow="web_app", mode="reckless", tag="Game", model="opus", effort="max")
        project = golem.store.project("tree-cards")
        assert (job.workflow, job.mode, project.tag, project.model, project.effort, project.mode) == ("web_app", "reckless", "Game", "opus", "max", "")
        golem.harness.script = [{"summary": "s", "title": "t"}]
        other = await golem.new_project("birds", name="Birds", mode="", tag="Nope")
        assert other.mode == "" and golem.store.project("birds").tag == ""  # no mode, and only a tag there is
        golem.workspace.repos.add("fish")  # a repo by that name already exists: the form says so first
        with pytest.raises(ValueError):
            await golem.new_project("fish", name="Fish")

    run(go())


def test_a_bad_summary_call_falls_back_to_the_idea(golem):
    async def go():
        golem.harness.script = [RunResult("error", error="boom")]
        job = await golem.new_project("Pomodoro timer with sounds\n\nand a dark mode", name="Pomodoro")
        assert golem.store.project("pomodoro").summary == "Pomodoro timer with sounds" and job.title == "Pomodoro"  # its name

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
        assert deployed[1].endswith(f" is live.\n{URL}\nNext: your check-in, then design.")
        assert texts[-1] == (
            f"Try the quiz: ten trees per round.\n{URL}\n\nTry it and reply here: say it looks good, or what to change. "
            "Then I'll make a few design directions for its look, for you to pick from."
        )
        # Progress is posted as notices (no push); the check-in as a message (push).
        assert [n for r, _, n in golem.chat.sent if r == room][-1] is False

        # Nothing runs while the job waits on Riggy.
        assert golem.store.next_job(at="9999") is None

        assert await golem.reply(room, "looks good but make the photos bigger") == "Sent. Picking the job back up."
        assert golem.store.job(job.id).state == "queued"
        golem.harness.script = by_label({
            "design": [three_looks], "pick": [{"decision": "pick", "picked": ["A"]}],
            "docs": [verdict("done", "Ten trees a round, with streaks.")],
        })  # fmt: skip
        await golem.finish()  # the design pass's first round, up for his pick
        design = next(c for c in golem.harness.calls if c.label == "design")
        assert "# Your step: design" in design.prompt and "skills/design.md" in design.prompt
        assert "- your check-in: You said: “looks good but make the photos bigger”" in design.prompt
        assert "> looks good but make the photos bigger" not in design.prompt  # his reply is for the buildout
        assert golem.store.job(job.id).state == "waiting" and plan.label(current(golem, job.id)) == "your pick"
        await golem.reply(room, "A")
        await golem.finish()

        guide, apply = (next(c for c in golem.harness.calls if c.label == name) for name in ("guide", "apply"))
        assert "- your pick: You picked A (Field guide)." in guide.prompt and "Restyle the whole app" in apply.prompt
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
        assert [s.state for s in steps] == ["done"] * 33
        assert [plan.label(s) for s in steps[-2:]] == ["deploy", "done"]
        assert len(golem.deployer.deploys) == 4  # the skeleton, the MVP, its new look, and the rest
        assert golem.workspace.bodies[-1].startswith("Closes #1")
        assert golem.chat.texts(room)[-1] == f"Done: Ten trees a round, with streaks.\n{URL}"
        assert [r["phase"] for r in golem.store.runs(job.id)] == ["spec", "scaffold", "mvp", "design", "guide", "apply", "buildout", "review", "docs"]
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
        # No PR closed its issue, so golem does: GitHub's record says it finished (history.py reads it, if it's adopted again).
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
        rest = prompt.replace("$(", "")
        for name in ("$GOLEM_PROGRESS", "$GOLEM_PORT", "$TMPDIR"):  # environment variables too
            rest = rest.replace(name, "")
        assert "$" not in rest
        assert "only on your ports, `$GOLEM_PORT` to `$GOLEM_PORT+9`" in prompt
        assert "/proposals/`, named\n  `job-1-<what>.md`" in prompt
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
        upcoming = [plan.label(s) for s in plan.planned(golem.settings, golem.store, golem.store.job(job.id)) if s.state == "pending"]
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
        upcoming = [plan.label(s) for s in plan.planned(golem.settings, golem.store, golem.store.job(job.id)) if s.state == "pending"]
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


def tire_after(golem, fixes: int) -> None:
    """Ship's fix loop asks Riggy for a hint after this many fixes: its workflow, edited."""
    ship = golem.store.workflow(key="ship")
    graph = json.loads(json.dumps(golem.store.tab(ship.id, "").working))
    for n in graph["nodes"]:
        if n["id"] == "tired":
            n["rows"][0]["value"] = fixes
    golem.store.put_tab(ship.id, "", graph, graph, [], golem.scheduler.library.sign(graph))


def test_fixing_in_circles_asks_riggy_for_a_hint(golem):
    tire_after(golem, 1)

    async def go():
        room = await new_project(golem, [verdict("done"), verdict("done"), verdict("done")])
        await golem.until("deploy")
        golem.deployer.refuse = ["bad compose", "still bad compose"]
        await golem.step()
        await golem.until("deploy")
        await golem.step()
        await golem.step()  # Ship's hint block asks him
        job = golem.store.jobs()[0]
        assert job.state == "waiting"
        assert golem.chat.texts(room)[-1].startswith("I've tried to fix deploy 1 times and it still fails")
        await golem.reply(room, "use port 8080")
        golem.harness.script = [verdict("done")]
        await golem.until("fix")
        await golem.step()
        assert "# Your step: fix" in golem.harness.calls[-1].prompt and "> use port 8080" in golem.harness.calls[-1].prompt

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
        await golem.ask(room, "i dont like how the score works, count streaks instead")
        job = golem.store.active_job("tree-cards")
        assert (job.kind, job.workflow) == ("change", "change")
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


def test_a_change_goes_by_the_title_the_request_call_gave_it(golem):
    async def go():
        room = await idle_project(golem)
        words = "I'd like you to redo the library page so it's a grid of covers instead of a list"
        golem.harness.script = by_label({"request": [{"workflow": "change", "title": "“Library View Redesign.”"}]})
        await golem.ask(room, words)
        job = golem.store.active_job("tree-cards")
        assert (job.title, job.request) == ("Library View Redesign", words)  # tidied; his words kept as they were
        ask = next(c for c in golem.harness.calls if c.label == "request")
        assert ask.schema["required"] == ["workflow", "mode", "title"] and words in ask.prompt
        assert ask.schema["properties"]["workflow"]["enum"] == ["change", "design", "options", "question"]
        assert "- `question` (only looks: builds nothing): He wants to know something" in ask.prompt
        assert "- `rigorous`: He wants extra care taken" in ask.prompt

        await golem.step()
        assert golem.chat.texts(room)[-1] == "Working on it: Library View Redesign"
        assert golem.workspace.issues[-1] == ("tree-cards", "Library View Redesign")  # its body still quotes his words
        await golem.finish()
        assert any(a.startswith("open PR #1: Library View Redesign: ") for a in golem.workspace.actions)
        assert plan.title(golem.store.job(job.id)) == "Library View Redesign" and golem.store.job(job.id).state == "done"
        # Every later change keeps to the project's design guide, and keeps it up to date.
        prompts = {c.label: c.prompt for c in golem.harness.calls}
        assert "If the project has a design guide, `docs/design.md`,\n  everything you make that can be seen follows it" in prompts["change"]
        assert "Does it follow the design guide?" in (REPO / "skills/review.md").read_text() and "skills/review.md" in prompts["review"]
        assert "`docs/design.md` if it changed the look" in " ".join(prompts["docs"].split())

    run(go())


def test_a_change_in_reckless_mode_skips_the_review(golem):
    async def go():
        await new_project(golem, [])
        golem.store.update_project("tree-cards", mode="reckless")
        golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
        await golem.ask("tree-cards", "bigger photos")
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
        assert await golem.note(room, "use oak and maple first") == "Sent. The next agent step gets it."
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

        await golem.reply(room, "photos")
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


LOOKS = ("Field guide", "Arcade", "Museum label")


def three_looks(call) -> dict:
    """A design round (skills/design.md): a doc, its pages, and each direction's own pages and tokens."""
    done = call.cwd / f"docs/design/rounds/job-{call.env['GOLEM_JOB']}"
    base = f"docs/design/rounds/job-{call.env['GOLEM_JOB']}/round-{len(list(done.glob('round-*'))) + 1}"
    (call.cwd / base / "pages").mkdir(parents=True, exist_ok=True)
    (call.cwd / base / "doc.html").write_text("<!doctype html><section class=page><h1>Tree Cards: design pass</h1></section>")
    for n in (1, 2):
        (call.cwd / base / f"pages/page-0{n}.png").write_bytes(PNG)
    made = []
    for letter, title in zip("abc", LOOKS):
        (call.cwd / base / letter).mkdir(exist_ok=True)
        (call.cwd / base / letter / "page.png").write_bytes(PNG)
        (call.cwd / base / letter / "tokens.css").write_text(":root { --ink: #1c2023; }")
        made.append({"title": title, "description": f"Tree Cards as a {title.lower()}.",
                     "files": [f"{base}/{letter}/page.png", f"{base}/{letter}/tokens.css"]})  # fmt: skip
    return {
        **verdict("done", "Three looks for Tree Cards."), "options": made, "doc": f"{base}/doc.html",
        "pages": [f"{base}/pages/page-01.png", f"{base}/pages/page-02.png"],
        "questions": ["Wordmark: Fraunces or Oxanium?", "Icon: Leaf or Acorn?"],
        "recommendation": "A, with the Fraunces wordmark and the Leaf icon.",
    }  # fmt: skip


async def idle_project(golem: Golem) -> str:
    room = await new_project(golem, [])
    golem.store.update_job(golem.store.active_job("tree-cards").id, state="done")
    return room


def test_asking_to_see_options_shows_them_then_builds_the_pick(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label(
            {"request": [{"workflow": "options"}], "options": [three_styles], "pick": [{"decision": "pick", "picked": ["B"]}]}
        )
        await golem.ask(room, "i want a new art style, show me 3 that are more chibi")
        job = golem.store.active_job("tree-cards")
        assert (job.kind, job.playbook) == ("change", "options")
        ask = golem.harness.calls[-1]
        assert (ask.label, ask.model) == ("request", "haiku") and "show me 3 that are more chibi" in ask.prompt

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

        await golem.reply(room, "b but bigger eyes")
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
                "request": [{"workflow": "options"}],
                "options": [three_styles, three_styles],
                "pick": [{"decision": "more", "picked": []}, {"decision": "pick", "picked": ["C"]}],
            }
        )
        await golem.ask(room, "show me some logo options")
        job = golem.store.active_job("tree-cards")
        await golem.until("your pick")
        await golem.step()
        await golem.reply(room, "none of these, try something more pastel")
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
        await golem.reply(room, "C")
        await golem.finish()
        assert golem.labels(job.id)[:6] == ["set up", "options", "your pick", "options", "your pick", "change"]
        picks = [s.summary for s in golem.store.steps(job.id) if s.kind == "choose"]
        assert picks[1] == f"You picked C (Ink outline). You said: “C”. Files: docs/options/job-{job.id}/c/."

    run(go())


def test_options_picked_on_the_ask_box_and_never_beside_another_job(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change", "title": "New Logo"}]})
        job = await golem.ask(room, "a new logo", workflow="options")
        assert (job.playbook, job.request, job.title) == ("options", "a new logo", "New Logo")  # his pick over the call's
        font = await golem.ask(room, "a new font", workflow="options")
        assert font.state == "queued" and turns.behind(golem.store, font).id == job.id  # it waits its turn
        assert golem.scheduler.pick(at="9999").id == job.id

    run(go())


def test_the_fake_harness_walks_an_options_job_with_placeholders(golem):
    async def go():
        room = await idle_project(golem)
        job = await golem.ask(room, "a new logo", workflow="options")
        golem.harness.script = None  # nothing scripted, as in local mode with HARNESS=fake
        await golem.until("your pick")
        made = next(s for s in golem.store.steps(job.id) if s.name == "options")
        assert [o["title"] for o in made.options] == ["(fake) A", "(fake) B"]

    run(go())


def test_a_design_round_keeps_its_doc_pages_questions_and_pick(golem):
    async def go():
        await idle_project(golem)
        job = golem.store.add_job("tree-cards", "change", "make it feel like an old field guide", "design")
        await golem.until("design")
        golem.harness.script = by_label({"design": [three_looks]})
        await golem.step()
        made = next(s for s in golem.store.steps(job.id) if s.name == "design")
        call = golem.harness.calls[-1]
        assert "options" in call.schema["properties"] and "doc" in call.schema["properties"]
        assert "skills/design.md" in call.prompt and "make it feel like an old field guide" in call.prompt
        assert [o["title"] for o in made.options] == list(LOOKS)
        assert [o["images"] for o in made.options] == [[f"{made.id}-{x}-1.png"] for x in "abc"]
        assert made.design == {
            "doc": f"{made.id}-doc.html", "source": f"docs/design/rounds/job-{job.id}/round-1/doc.html",
            "pages": [f"{made.id}-p-1.png", f"{made.id}-p-2.png"],
            "questions": ["Wordmark: Fraunces or Oxanium?", "Icon: Leaf or Acorn?"],
            "recommendation": "A, with the Fraunces wordmark and the Leaf icon.", "skip": "",
        }  # fmt: skip
        folder = golem.settings.job_logs(job.id) / "options"
        assert (folder / f"{made.id}-doc.html").read_text().startswith("<!doctype html>")
        assert plan.label(current(golem, job.id)) == "your pick"

        # A doc that would run something is sent back.
        golem.store.update_step(made.id, state="pending", options=[], design={})
        golem.harness.script = by_label({"design": [lambda c: {**three_looks(c), "doc": "evil.html"}]})
        (golem.workspace.tree_path(job.id) / "evil.html").write_text("<script>fetch('/act')</script>")  # in the job's tree
        await golem.step()
        made = golem.store.step(made.id)
        assert (made.state, made.error) == ("pending", "The design doc evil.html has a <script> or http-equiv: it has to run nothing (skills/design.md).")

    run(go())


async def design_up(golem: Golem, picks: list[dict]) -> tuple[str, object]:
    """A design pass on the idle trees project, its first round up for Riggy to pick from."""
    room = await idle_project(golem)
    golem.harness.script = by_label({"design": [three_looks, three_looks], "pick": picks})
    job = golem.store.add_job("tree-cards", "change", "make it feel like an old field guide", "design")
    await golem.until("your pick")
    await golem.step()
    return room, job


def test_a_design_pass_shows_its_doc_and_builds_the_pick(golem):
    async def go():
        room, job = await design_up(golem, [{"decision": "pick", "picked": ["A"]}])
        made, choose = next(s for s in golem.store.steps(job.id) if s.name == "design"), current(golem, job.id)
        assert golem.chat.images == [
            *((room, f"{made.id}-{x.lower()}-1.png", f"{x}: {t}. Tree Cards as a {t.lower()}.") for x, t in zip("ABC", LOOKS)),
            (room, f"{made.id}-p-1.png", "The pages they share"), (room, f"{made.id}-p-2.png", "Shared (2)"),
        ]  # fmt: skip
        assert golem.chat.texts(room)[-1] == (
            f"The whole doc: https://golem.unlaunched.dev/jobs/{job.id}/options/{made.id}-doc.html\n\n"
            "My pick: A, with the Fraunces wordmark and the Leaf icon.\n\n"
            "Also to decide:\n1. Wordmark: Fraunces or Oxanium?\n2. Icon: Leaf or Acorn?\n\n"
            "Which one? Reply A, B or C, and your answers by number, or ask for another round (“A, but show me more icons”). "
            f"Side by side: https://golem.unlaunched.dev/steps/{choose.id}"
        )
        assert (golem.store.job(job.id).state, choose.state) == ("waiting", "waiting")

        await golem.reply(room, "a, 1 oxanium, leaf icon")
        await golem.step()
        ask = golem.harness.calls[-1]
        assert ask.label == "pick" and "1. Wordmark: Fraunces or Oxanium?" in ask.prompt and "A: Field guide." in ask.prompt
        assert golem.chat.texts(room)[-1] == (
            f"✓ your pick: You picked A (Field guide). You said: “a, 1 oxanium, leaf icon”. "
            f"Files: docs/design/rounds/job-{job.id}/round-1/a/.\nNext: guide."
        )
        await golem.step()
        guide = golem.harness.calls[-1]
        assert guide.label == "guide" and "Write the design guide, `docs/design.md`" in guide.prompt
        assert "- your pick: You picked A (Field guide)." in guide.prompt and "> a, 1 oxanium, leaf icon" in guide.prompt
        await golem.finish()
        assert golem.labels(job.id) == [
            "set up", "design", "your pick", "guide", "apply", "review", "docs", "push", "PR", "CI", "merge", "deploy", "done",
        ]  # fmt: skip

    run(go())


def test_another_design_round_keeps_what_he_settled(golem):
    async def go():
        room, job = await design_up(golem, [{"decision": "more", "picked": ["A"]}, {"decision": "pick", "picked": ["B"]}])
        await golem.reply(room, "a but show me more icons")
        await golem.step()
        assert golem.chat.texts(room)[-1] == (
            f"✓ your pick: You settled on A (Field guide) (docs/design/rounds/job-{job.id}/round-1/a/) "
            "and asked for another round: “a but show me more icons”\nNext: design."
        )
        await golem.step()  # the second round, with what he said about the first
        call = golem.harness.calls[-1]
        assert call.label == "design" and "> a but show me more icons" in call.prompt and "options" in call.schema["properties"]
        assert "You settled on A (Field guide)" in call.prompt
        await golem.step()
        second = [s for s in golem.store.steps(job.id) if s.name == "design"][1]
        assert golem.chat.images[-1] == (room, f"{second.id}-p-2.png", "Shared (2)")
        assert second.design["source"] == f"docs/design/rounds/job-{job.id}/round-2/doc.html"
        await golem.reply(room, "B")
        await golem.finish()
        assert golem.labels(job.id)[:6] == ["set up", "design", "your pick", "design", "your pick", "guide"]

    run(go())


def test_a_design_pass_with_nothing_to_design_builds_nothing(golem):
    async def go():
        await idle_project(golem)
        job = golem.store.add_job("tree-cards", "change", "keep the look, just tidy it", "design")
        golem.harness.script = by_label({"design": [{**verdict("done", "He likes the look as it is."), "skip": "He said keep the look."}]})
        await golem.finish()
        steps = {s.name: s for s in golem.store.steps(job.id)}
        assert steps["design"].port == "nothing" and golem.store.job(job.id).state == "done"
        assert not {"choose", "guide", "apply"} & set(steps)  # the round's "nothing" output goes past them
        assert not any(c.label in ("guide", "apply") for c in golem.harness.calls)

        # Skipped from the status page instead: the same.
        job = golem.store.add_job("tree-cards", "change", "a design pass", "design")
        await golem.until("design")
        design = next(s for s in golem.store.steps(job.id) if s.name == "design")
        await golem.scheduler.skip_now(golem.store.job(job.id), design, "Skipped from the status page.")
        await golem.finish()
        names = [s.name for s in golem.store.steps(job.id)]
        assert "guide" not in names and "review" in names and golem.store.job(job.id).state == "done"

    run(go())


def test_the_fake_harness_walks_a_design_pass(golem):
    async def go():
        await idle_project(golem)
        job = golem.store.add_job("tree-cards", "change", "a design pass", "design")
        golem.harness.script = None  # nothing scripted, as in local mode with HARNESS=fake
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        design = next(s for s in golem.store.steps(job.id) if s.name == "design")
        assert design.design["skip"].startswith("(fake)") and "guide" not in [s.name for s in golem.store.steps(job.id)]

    run(go())


def test_a_change_request_is_not_options(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        await golem.ask(room, "show the score bigger")
        assert golem.store.active_job("tree-cards").workflow == "change"

    run(go())


def test_asking_for_a_new_look_starts_a_design_pass(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "design", "title": "Field Guide Redesign"}]})
        await golem.ask(room, "it looks generic, make it feel like an old field guide")
        job = golem.store.active_job("tree-cards")
        assert (job.kind, job.playbook, job.title) == ("change", "design", "Field Guide Redesign")
        await golem.step()
        assert golem.chat.texts(room)[-1] == "Making design directions: Field Guide Redesign"
        assert plan.label(current(golem, job.id)) == "design"

    run(go())


def test_a_design_pass_picked_on_the_ask_box_and_never_beside_another_job(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change", "title": "Cozy Redesign"}]})
        job = await golem.ask(room, "cozy, like a cabin", workflow="design")
        assert (job.playbook, job.request, job.title) == ("design", "cozy, like a cabin", "Cozy Redesign")
        warmer = await golem.ask(room, "warmer", workflow="design")
        assert turns.behind(golem.store, warmer).id == job.id  # it waits its turn

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
        golem.harness.script = by_label({"request": [{"workflow": "question"}], "examine": [verdict("done", answer, link)]})
        await golem.ask(room, "why does my score reset")
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
        golem.harness.script = by_label({"request": [{"workflow": "question"}]})
        await golem.ask(room, "what stack did you pick?")
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


def test_a_question_picked_on_the_ask_box_and_its_question_back(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"examine": [verdict("blocked", "Which score: the streak or the total?"), verdict("done", "The streak.")]})
        question = await golem.ask(room, "which score resets", workflow="question")
        assert question.playbook == "question"
        await golem.finish()
        assert golem.store.job(question.id).state == "waiting" and "Which score" in golem.chat.texts(room)[-1]
        await golem.reply(room, "the streak one")  # his reply to it, not a change
        assert golem.store.job(question.id).state == "queued" and golem.store.active_job("tree-cards", questions=False) is None
        await golem.finish()
        assert golem.chat.texts(room)[-1] == "The streak." and golem.harness.calls[-1].resume == "s"

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
        [offline] = golem.store.job_proposals(job.id)  # one a turn: a second is dropped
        assert (offline.title, offline.lens, offline.state) == ("Offline mode", "features", "open")
        assert golem.chat.texts(room)[-2] == "Nothing else on, so I'm looking at its features for things to propose."
        assert golem.workspace.issues[-1] == ("tree-cards", "Proposal: Offline mode") and offline.issue == len(golem.workspace.issues)
        said = golem.chat.sent[-1]
        assert said[0] == room and not said[2]  # it notifies him
        assert said[1].startswith("I looked at its features and have a proposal for you:\n\nOffline mode\nCache the deck")
        assert said[1].endswith("Thumbs up or down (and why) on its page: https://golem.unlaunched.dev/p/tree-cards")
        assert golem.scheduler.propose() is None  # not due again for a day

        long_ago(golem)
        second = golem.scheduler.propose()
        golem.harness.script = by_label({"propose": [proposing(BIGGER)]})
        await golem.finish()
        [bigger] = golem.store.job_proposals(second.id)
        assert bigger.lens == "code health" and golem.chat.texts(room)[-1].startswith("I looked at its code health and have a proposal")

        control = Control(golem.store, golem.scheduler)
        assert "takes that into account" in await control.decide(offline.id, False, "  it's a web  app, always online ")
        assert await control.decide(bigger.id, True, "") == f"Accepted “Bigger cards”: job {second.id + 1} builds it."
        built = golem.store.active_job("tree-cards")
        assert (built.workflow, built.request.splitlines()[0]) == ("change", "Bigger cards")
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
        assert "# This time's lens: user experience" in call.prompt  # the next lens in turn
        assert "- [code health] Bigger cards — accepted\n- [features] Offline mode — turned down: “it's a web app, always online”" in call.prompt
        assert golem.chat.texts(room)[-1].startswith("I looked at its user experience and found nothing worth proposing.")
        assert golem.store.job_proposals(again.id) == []

    run(go())


def test_proposals_skip_the_kinds_riggy_turned_off(golem):
    from golem import proposals

    async def go():
        await idle_project(golem)
        golem.store.update_project("tree-cards", proposals="daily", proposals_off="features,user experience")
        job = golem.scheduler.propose()
        assert job.request == "Look at its code health for things to propose"  # features is off
        golem.store.update_project("tree-cards", proposals_off="features,code health,user experience")
        golem.harness.script = by_label({"propose": [proposing()]})
        await golem.finish()
        call = [c for c in golem.harness.calls if c.label == "propose"][-1]
        assert "# This time's lens: code health" in call.prompt  # picked when it started, so it stays
        assert "in turn (code health, security, performance, tests, polish," in call.prompt  # only the kinds on

        long_ago(golem)
        assert golem.scheduler.propose().request == "Look at its security for things to propose"  # user experience is off
        await golem.finish()
        golem.store.update_project("tree-cards", proposals_off=",".join(x.name for x in proposals.LENSES))
        long_ago(golem)
        assert golem.scheduler.propose() is None  # every kind off: none due

    run(go())


def test_proposals_wait_until_golem_and_the_project_are_free(golem):
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
        golem.store.update_project("tree-cards", proposals_max=2)
        golem.harness.script = by_label({"propose": [proposing(OFFLINE), proposing(BIGGER)]})
        await golem.finish()
        long_ago(golem)
        assert golem.scheduler.propose() is not None
        await golem.finish()
        open_ = golem.store.proposals("tree-cards", ("open",))
        assert len(open_) == 2
        long_ago(golem)
        assert golem.scheduler.propose() is None
        golem.store.update_project("tree-cards", proposals_max=3)
        assert golem.scheduler.propose() is not None
        golem.harness.script = by_label({"propose": [proposing()]})
        await golem.finish()

        # Accepted while a change is under way: built once it's done.
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        await golem.ask(room, "make the cards flip faster")
        change = golem.store.active_job("tree-cards")
        await Control(golem.store, golem.scheduler).decide(open_[0].id, True, "yes please")
        assert golem.store.proposal(open_[0].id).built_by is None
        await golem.finish()
        assert golem.store.job(change.id).state == "done"
        await golem.scheduler.build_accepted()
        built = golem.store.active_job("tree-cards")
        assert built.id != change.id and built.request.endswith("Riggy accepted it: “yes please”)")

    run(go())


def time_passes(golem: Golem, by: timedelta) -> None:
    """As if `by` went by since the proposals jobs started and their proposals were made and decided."""
    db = golem.store.db

    def back(at: str | None) -> str | None:
        return at and (datetime.fromisoformat(at) - by).isoformat(timespec="seconds")

    for row in db.execute("SELECT id, created_at FROM jobs WHERE playbook = 'proposals'").fetchall():
        db.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (back(row["created_at"]), row["id"]))
    for row in db.execute("SELECT id, created_at, decided_at FROM proposals").fetchall():
        db.execute("UPDATE proposals SET created_at = ?, decided_at = ? WHERE id = ?", (back(row["created_at"]), back(row["decided_at"]), row["id"]))


def test_a_projects_turn_for_proposals_comes_round_every_interval_after_its_last():
    from golem.proposals import turn

    hour, last = timedelta(hours=1), datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    assert turn(None, hour, last) == last  # never had one: now
    assert turn(last, hour, last + timedelta(minutes=59)) is None
    assert turn(last, hour, last + hour) == last + hour
    assert turn(last, hour, last + timedelta(hours=3, minutes=20)) == last + timedelta(hours=3)


def test_proposals_come_one_a_turn_and_a_turn_at_the_limit_passes(golem):
    from golem.control import Control

    async def go():
        await idle_project(golem)
        golem.store.update_project("tree-cards", proposals="hourly", proposals_max=3)
        sound, dark = {**BIGGER, "title": "Sound"}, {**OFFLINE, "title": "Dark mode"}
        golem.harness.script = by_label({"propose": [proposing(OFFLINE, BIGGER, sound), proposing(BIGGER), proposing(sound), proposing(dark)]})
        def waiting():
            return golem.store.proposals("tree-cards", ("open",))

        for hour in range(3):  # one an hour, not all three at once
            assert golem.scheduler.propose() is not None
            await golem.finish()
            assert len(waiting()) == hour + 1
            assert golem.scheduler.propose() is None  # not its turn yet
            time_passes(golem, timedelta(hours=1))
        assert [p.title for p in waiting()] == ["Sound", "Bigger cards", "Offline mode"]
        assert golem.scheduler.propose() is None  # three wait on him: this turn passes
        time_passes(golem, timedelta(days=2, minutes=30))
        assert golem.scheduler.propose() is None  # and so do the turns he missed while away

        await Control(golem.store, golem.scheduler).decide(waiting()[-1].id, False, "")
        assert golem.scheduler.propose() is None  # the next comes at the next turn, not at once
        time_passes(golem, timedelta(minutes=30))
        assert golem.scheduler.propose() is not None
        await golem.finish()
        assert [p.title for p in waiting()] == ["Dark mode", "Sound", "Bigger cards"] and golem.scheduler.propose() is None

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
        job = await golem.ask(room, "a new logo", workflow="options")
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


def test_an_options_step_that_finishes_as_its_turns_run_out_keeps_its_options(golem):
    async def go():
        room = await idle_project(golem)
        job = await golem.ask(room, "a new hero", workflow="options")
        await golem.until("options")

        def script(call):
            if call.label == "options":  # makes them, then runs out of turns before its verdict
                three_styles(call)
                return RunResult("max_turns", session_id="s-1")
            assert call.label == "options-wrapup" and "options" in call.schema["properties"]
            assert "also give what the step said to return" in call.prompt
            return RunResult("verdict", {**three_styles(call), "finished": True, "message": "Three heroes."}, session_id="s-1")

        golem.harness.script = script
        await golem.step()
        made = next(s for s in golem.store.steps(job.id) if s.name == "options")
        assert (made.state, made.summary, len(made.options)) == ("done", "Three heroes.", 3)
        assert plan.label(current(golem, job.id)) == "your pick"

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
        assert await golem.control.kill(job.id) == f"Stopped job {job.id}."
        for _ in range(50):
            if not golem.scheduler.running:
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
        assert still[0] == 'Still on spec (step 2/33), 0s in. Last said: "Reading the brief." Now running Bash npm test.'
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


def test_pausing_a_project_or_all_work(golem):
    async def go():
        await new_project(golem, [])
        job = golem.store.active_job("tree-cards")
        await golem.control.pause("tree-cards", True)
        assert golem.store.project("tree-cards").paused and golem.store.next_job() is None
        await golem.control.pause("tree-cards", False)
        assert golem.store.next_job().id == job.id
        golem.control.pause_all(True)
        assert golem.scheduler.paused
        golem.control.pause_all(False)
        assert not golem.scheduler.paused

    run(go())


# --- settings, names and slugs ------------------------------------------------


def test_settings_picked_for_a_new_project_pick_the_mode_model_and_effort(golem):
    async def go():
        golem.harness.script = [{**TREES, "title": "Leaf Guessing Game"}]
        await golem.new_project("a game where you guess the leaf", name="Leaf Peeper", mode="rigorous", model="fable", effort="max")
        project = golem.store.project("leaf-peeper")
        assert (project.name, project.mode, project.model, project.effort) == ("Leaf Peeper", "", "fable", "max")  # its default is Riggy's to set
        job = golem.store.active_job("leaf-peeper")
        assert (job.workflow, job.mode) == ("web_app", "rigorous")
        await golem.until("spec")
        golem.harness.script = [verdict("done")]
        await golem.step()
        call = golem.harness.calls[-1]
        assert (call.model, call.effort) == ("fable", "max")
        assert "# Your step: spec" in call.prompt and "**rigorous mode**" in call.prompt
        job = golem.store.active_job("leaf-peeper")
        assert len(plan.planned(golem.settings, golem.store, job)) == 35
        assert golem.store.runs()[0]["model"] == "fable/max"

    run(go())


def test_unpicked_settings_use_the_defaults(tmp_path):
    golem = Golem(tmp_path, default_mode="reckless", default_model="sonnet", default_effort="xhigh")

    async def go():
        golem.harness.script = [TREES]
        await golem.new_project("trees")
        project = golem.store.project("tree-cards")
        assert (project.mode, project.model, project.effort) == ("", "", "")  # each step's default; golem's default mode
        assert golem.store.active_job("tree-cards").mode == "reckless"
        await golem.until("build")
        await golem.step()
        assert (golem.harness.calls[-1].model, golem.harness.calls[-1].effort) == ("sonnet", "xhigh")

    run(go())


def test_a_projects_settings(golem):
    from golem.control import ControlError

    async def go():
        await new_project(golem, [])
        job = golem.store.active_job("tree-cards")
        said = await golem.control.setting("tree-cards", "mode", "rigorous")
        assert said == f"Tree Cards: requests run in rigorous mode unless they ask for another; job {job.id}, not started yet, too."
        assert golem.store.job(job.id).mode == "rigorous" and golem.store.project("tree-cards").mode == "rigorous"
        await golem.control.setting("tree-cards", "effort", "max")
        await golem.control.setting("tree-cards", "model", "sonnet")
        assert (golem.store.project("tree-cards").model, golem.store.project("tree-cards").effort) == ("sonnet", "max")
        for name, value in (("model", "gpt"), ("mode", "fancy")):
            with pytest.raises(ControlError):
                await golem.control.setting("tree-cards", name, value)

        await golem.until("spec")
        golem.harness.script = [verdict("done")]
        await golem.step()
        assert (golem.harness.calls[-1].model, golem.harness.calls[-1].effort) == ("sonnet", "max")

        # Once the job has started, it carries on as it is.
        await golem.control.setting("tree-cards", "mode", "reckless")
        assert golem.store.job(job.id).mode == "rigorous" and golem.store.project("tree-cards").mode == "reckless"
        await golem.control.setting("tree-cards", "model", "")
        await golem.control.setting("tree-cards", "mode", "")
        assert (golem.store.project("tree-cards").model, golem.store.project("tree-cards").mode) == ("", "")
        assert golem.chat.texts("tree-cards") == [m for m in golem.chat.texts("tree-cards") if "from the status page" not in m]  # the page says it

    run(go())


def test_rename(golem):
    async def go():
        await new_project(golem, [])
        assert golem.control.rename("tree-cards", "  Leaf   Peeper ") == "Renamed Tree Cards to Leaf Peeper. Its address stays tree-cards."
        assert golem.store.project("tree-cards").name == "Leaf Peeper"

    run(go())


def test_slug_moves_the_project_and_queues_the_reference_fixes(golem):
    async def go():
        await new_project(golem, [])
        old_job = golem.store.jobs()[0]
        golem.store.update_job(old_job.id, state="done")

        reply = await golem.bot.move(golem.store.project("tree-cards"), "leaf-peeper")
        assert reply.startswith("Moved Tree Cards to leaf-peeper: https://github.com/unlaunched/leaf-peeper")
        assert "https://github.com/unlaunched/tree-cards is still there" in reply
        assert golem.store.project("tree-cards") is None
        project = golem.store.project("leaf-peeper")
        assert project.name == "Tree Cards"
        assert golem.store.job(old_job.id).slug == "leaf-peeper"  # history moves with it
        assert golem.deployer.stopped == ["tree-cards"]
        assert golem.workspace.path("leaf-peeper").is_dir() and not golem.workspace.path("tree-cards").exists()

        fix = golem.store.active_job("leaf-peeper")
        assert (fix.kind, fix.workflow) == ("change", "change")
        assert "moved from `tree-cards` to `leaf-peeper`" in fix.request

    run(go())


async def a_run(golem: Golem) -> None:
    """Work the new project's job as far as its first agent run."""
    await golem.until("spec")
    golem.harness.script = [verdict("done")]
    await golem.step()


def test_deleting_takes_everything_but_the_repo(golem):
    async def go():
        room = await new_project(golem, [])
        await a_run(golem)  # a log and a checkout
        job = golem.store.jobs()[0]
        golem.store.add_note(job.id, "use oak first")
        logs = golem.settings.job_logs(job.id)
        assert logs.is_dir() and golem.workspace.path("tree-cards").is_dir()

        said = await golem.bot.delete(golem.store.project("tree-cards"))
        assert golem.deployer.deleted == ["tree-cards"]
        assert not golem.workspace.path("tree-cards").exists() and not logs.exists()
        assert golem.store.project("tree-cards") is None
        assert golem.store.jobs() == [] and golem.store.runs() == [] and golem.store.pending_notes(job.id) == []
        assert golem.store.steps(job.id) == []
        assert golem.store.messages(room) == []
        assert said == (
            "Deleted Tree Cards: its site and data, checkout, jobs, logs and history are gone. Delete the repo yourself "
            "on GitHub (Settings, at the bottom): https://github.com/unlaunched/tree-cards/settings"
        )
        assert golem.store.next_job() is None

    run(go())


def test_delete_stops_a_running_step_first(golem):
    started = asyncio.Event()

    class Hangs:
        async def run(self, **kwargs):
            started.set()
            await asyncio.sleep(3600)

    async def go():
        await new_project(golem, [])
        golem.scheduler.harness = Hangs()
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await asyncio.wait_for(started.wait(), 5)
        [running] = golem.scheduler.running.values()
        task = running.task
        await golem.bot.delete(golem.store.project("tree-cards"))
        assert task.done()  # stopped before anything was deleted
        assert golem.store.project("tree-cards") is None and golem.store.runs() == []
        assert golem.deployer.deleted == ["tree-cards"]
        loop.cancel()

    run(go())


def test_a_failed_delete_keeps_the_project_and_can_be_retried(golem):
    async def go():
        await new_project(golem, [])
        golem.deployer.fail = "deleting is off: DEPLOYER_ADMIN_SECRET is not set"
        assert await golem.bot.delete(golem.store.project("tree-cards")) == (
            "Couldn't delete all of Tree Cards: deleting is off: DEPLOYER_ADMIN_SECRET is not set. "
            "Its jobs are stopped; delete it again to try again."
        )
        assert golem.store.project("tree-cards") is not None
        assert golem.store.jobs()[0].state == "killed"

        golem.deployer.fail = None
        await golem.bot.delete(golem.store.project("tree-cards"))
        assert golem.store.project("tree-cards") is None

    run(go())


def test_nuke_says_what_will_go_then_deletes_it_all(golem):
    async def go():
        await new_project(golem, [])
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
            "Kept: the stack, golem's news, and .env.\n"
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
        # golem itself carries on as before.
        assert (golem.settings.state_dir / "golem.db").exists()
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
        await new_project(golem, [])
        golem.workspace.repos.add("taken")
        project = golem.store.project("tree-cards")
        assert "won't work as an address" in await golem.bot.move(project, "Golem!")
        assert await golem.bot.move(project, "taken") == "taken is taken (by a project or an existing repo)."
        golem.store.update_job(golem.store.active_job("tree-cards").id, state="running")
        assert (await golem.bot.move(project, "oaks")).startswith("A step is running.")
        assert golem.store.project("tree-cards") is not None

    run(go())


# --- the conversation -----------------------------------------------------------


def test_everything_said_is_kept_by_project_and_job(golem):
    async def go():
        room = await new_project(golem, [])
        first = golem.store.active_job("tree-cards")
        assert [(m.who, m.job_id) for m in golem.store.messages(room)] == [("riggy", first.id)]  # the idea, the job's first words
        golem.store.update_job(first.id, state="done")
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        job = await golem.ask(room, "make the cards bigger")
        said = golem.store.messages("tree-cards")[-1]
        assert (said.who, said.text, said.job_id) == ("riggy", "make the cards bigger", job.id)
        assert job.request == "make the cards bigger"
        assert [m.text for m in golem.store.job_messages(job.id)] == ["make the cards bigger"]

        golem.store.move_project("tree-cards", "trees")
        assert golem.store.messages("tree-cards") == [] and len(golem.store.messages("trees")) == 2

    run(go())


# --- parallel workers -----------------------------------------------------------


class Gate:
    """Agent steps that run until the test lets their project go, to watch steps run side by side."""

    def __init__(self):
        self.started: list[tuple[str, str]] = []  # (slug, label), in the order they started
        self.envs: list[dict] = []
        self.go: dict[str, asyncio.Event] = {}

    def let_go(self, slug: str) -> None:
        self.go.setdefault(slug, asyncio.Event()).set()

    async def run(self, *, env, label, **kwargs):
        slug = env["GOLEM_SLUG"]
        self.started.append((slug, label))
        self.envs.append(env)
        await self.go.setdefault(slug, asyncio.Event()).wait()
        return RunResult("verdict", verdict=verdict("done"), session_id="s")

    def slugs(self) -> list[str]:
        return [slug for slug, _ in self.started]


async def soon(check, seconds: float = 5) -> None:
    """Wait until `check()` is true."""
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


def a_change(golem: Golem, slug: str, request: str = "make it better", **job):
    """A live project with a change asked for."""
    if golem.store.project(slug) is None:
        golem.store.add_project(slug, slug.title(), f"The {slug} project.")
    return golem.store.add_job(slug, "change", request, job.pop("playbook", "feature"), **job)


def test_one_worker_by_default_and_more_run_steps_of_different_projects_at_once(golem):
    async def go():
        gate = golem.scheduler.harness = Gate()
        trees, birds = a_change(golem, "trees"), a_change(golem, "birds")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: gate.started)
        await asyncio.sleep(0.05)
        assert gate.slugs() == ["trees"] and golem.scheduler.workers == 1

        golem.store.put("workers", "2")
        golem.scheduler.wake()
        await soon(lambda: len(gate.started) == 2)
        assert gate.slugs() == ["trees", "birds"]
        assert {r.slug: r.slot for r in golem.scheduler.running.values()} == {"trees": 0, "birds": 1}
        # Side by side, each has ports and a TMPDIR of its own.
        tmp = golem.settings.tmp_dir
        own = {e["GOLEM_SLUG"]: (e["GOLEM_SLOT"], e["GOLEM_PORT"], e["TMPDIR"], e["GOLEM_TMP"]) for e in gate.envs}
        assert own == {
            "trees": ("0", "20000", str(tmp / f"job-{trees.id}"), str(tmp / f"job-{trees.id}")),
            "birds": ("1", "20100", str(tmp / f"job-{birds.id}"), str(tmp / f"job-{birds.id}")),
        }
        assert (tmp / f"job-{trees.id}").is_dir() and (tmp / f"job-{birds.id}").is_dir()

        gate.let_go("trees")
        gate.let_go("birds")
        await soon(lambda: golem.store.job(trees.id).state == golem.store.job(birds.id).state == "done")
        await soon(lambda: not list(tmp.glob("job-*")))  # gone with the jobs
        loop.cancel()

    run(go())


def test_a_project_runs_one_step_at_a_time_however_many_workers(golem):
    async def go():
        gate = golem.scheduler.harness = Gate()
        golem.store.put("workers", "3")
        change = a_change(golem, "trees")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: gate.started)
        question = a_change(golem, "trees", "how big are the cards?", playbook="question", priority=10)
        birds = a_change(golem, "birds")
        golem.scheduler.wake()
        await soon(lambda: len(gate.started) == 2)
        await asyncio.sleep(0.05)
        assert gate.slugs() == ["trees", "birds"]  # the question waits for the trees step
        assert golem.store.job(question.id).state == "queued"

        gate.let_go("trees")
        await soon(lambda: golem.store.job(question.id).state == "done")
        assert gate.started[2] == ("trees", "examine")  # it came first, between the change's steps
        await soon(lambda: golem.store.job(change.id).state == "done")
        assert golem.store.job(birds.id).state == "running"
        loop.cancel()

    run(go())


def test_killing_or_deleting_one_of_two_running_projects_leaves_the_other(golem):
    async def go():
        gate = golem.scheduler.harness = Gate()
        golem.store.put("workers", "2")
        trees, birds, fish = a_change(golem, "trees"), a_change(golem, "birds"), a_change(golem, "fish")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: len(gate.started) == 2)
        bird_task = golem.scheduler.running[birds.id].task

        assert golem.scheduler.kill(trees.id)
        await soon(lambda: trees.id not in golem.scheduler.running)
        assert golem.store.job(trees.id).state == "killed" and not bird_task.done()
        await soon(lambda: len(gate.started) == 3)  # its slot goes to the next project
        assert gate.started[2][0] == "fish"

        await golem.scheduler.drop_project("fish")  # waits only for its own step
        assert golem.store.job(fish.id).state == "killed" and not bird_task.done()
        gate.let_go("birds")
        await soon(lambda: golem.store.job(birds.id).state == "done")
        loop.cancel()

    run(go())


def test_an_upgrade_waits_for_every_running_step_and_starts_nothing_new(golem):
    async def go():
        gate = golem.scheduler.harness = Gate()
        golem.store.put("workers", "2")
        trees, birds = a_change(golem, "trees"), a_change(golem, "birds")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: len(gate.started) == 2)
        golem.store.put("upgrade", "pending")
        fish = a_change(golem, "fish")
        gate.let_go("trees")
        await soon(lambda: trees.id not in golem.scheduler.running)
        await asyncio.sleep(0.05)
        assert golem.deployer.upgrades == 0 and golem.scheduler.running.keys() == {birds.id}
        assert golem.store.job(fish.id).state == "queued" and golem.store.job(trees.id).state == "queued"

        gate.let_go("birds")
        await soon(lambda: golem.deployer.upgrades == 1)
        assert not golem.scheduler.running and golem.store.steps(fish.id) == []
        loop.cancel()

    run(go())


def test_with_two_workers_a_second_project_runs_beside_the_first_and_a_third_waits(golem):
    async def go():
        gate = golem.scheduler.harness = Gate()
        golem.store.put("workers", "2")
        a_change(golem, "trees")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: len(gate.started) == 1)
        a_change(golem, "fish")  # a worker is free for it
        golem.scheduler.wake()
        await soon(lambda: len(gate.started) == 2)

        birds = a_change(golem, "birds")
        golem.scheduler.wake()
        await asyncio.sleep(0.05)
        assert len(gate.started) == 2 and golem.store.job(birds.id).state == "queued"  # both workers are busy
        trees = golem.store.project("trees")
        assert await golem.bot.move(trees, "tree-cards") == "A step is running. Try again when it finishes, or pause the project first."
        loop.cancel()

    run(go())


def test_a_spare_worker_looks_for_proposals_while_another_project_is_worked_on(golem):
    async def go():
        await idle_project(golem)
        golem.store.update_project("tree-cards", proposals="hourly")
        gate = golem.scheduler.harness = Gate()
        a_change(golem, "birds")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: gate.started)
        await asyncio.sleep(0.05)
        assert golem.store.proposal_jobs("tree-cards") == []  # the only worker is busy

        golem.store.put("workers", "2")
        golem.scheduler.wake()
        await soon(lambda: ("tree-cards", "propose") in gate.started)
        assert len(golem.store.proposal_jobs("tree-cards")) == 1
        loop.cancel()

    run(go())


# --- each job's own tree ----------------------------------------------------------


def test_a_job_works_in_a_tree_of_its_own_and_a_question_in_another(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change"}], "change": [verdict("blocked", "Which trees?")]})
        await golem.ask(room, "make the cards bigger")
        job = golem.store.active_job("tree-cards", questions=False)
        await golem.until("change")
        await golem.step()
        assert golem.store.job(job.id).state == "waiting"
        await golem.reply(room, "oaks and maples")  # his answer resumes the same session, in the same tree
        await golem.step()
        runs = [c for c in golem.harness.calls if c.label == "change"]
        assert [c.cwd for c in runs] == [golem.workspace.tree_path(job.id)] * 2 and runs[1].resume == "s"

        # A question while the change is under way looks at its work so far, in a tree of its own.
        golem.harness.script = by_label({"request": [{"workflow": "question"}]})
        await golem.ask(room, "how big are the cards now?")
        question = newest_job(golem)
        await golem.step()
        await golem.step()
        examine = golem.harness.calls[-1]
        assert examine.label == "examine" and examine.cwd == golem.workspace.tree_path(question.id)
        assert f"on `golem/job-{job.id}` (a job is under way" in examine.prompt

        await golem.finish()
        assert golem.workspace.trees == {job.id, question.id}  # until golem tidies up
        await golem.scheduler.tidy()
        assert golem.workspace.trees == set() and not any((golem.settings.workspace_dir / ".jobs").iterdir())

    run(go())


def test_trees_go_with_a_killed_job_and_a_deleted_project(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        await golem.ask(room, "make the cards bigger")
        job = golem.store.active_job("tree-cards", questions=False)
        await golem.until("change")
        assert golem.workspace.tree_path(job.id).is_dir()
        await golem.control.kill(job.id)
        await golem.scheduler.tidy()
        assert not golem.workspace.tree_path(job.id).exists()

        await golem.ask(room, "make the cards smaller")
        again = golem.store.active_job("tree-cards", questions=False)
        await golem.until("change")
        await golem.bot.delete(golem.store.project("tree-cards"))
        assert golem.store.project("tree-cards") is None and not golem.workspace.tree_path(again.id).exists()

    run(go())


def test_a_job_from_before_trees_finishes_in_the_projects_checkout(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        await golem.ask(room, "make the cards bigger")
        job = golem.store.update_job(golem.store.active_job("tree-cards", questions=False).id, worktree=0)
        await golem.finish()
        assert [c.cwd for c in golem.harness.calls if c.label == "change"] == [golem.workspace.path("tree-cards")]
        assert job.id not in golem.workspace.trees and golem.store.job(job.id).state == "done"

    run(go())


# --- changes on one project, taking turns -----------------------------------------


class JobGate:
    """Agent runs held until the test lets that job's step go (by job id and label); others go at once."""

    def __init__(self, *held: tuple[int, str]):
        self.held = {key: asyncio.Event() for key in held}
        self.started: list[tuple[int, str]] = []
        self.calls: list = []

    def let_go(self, job: int, label: str) -> None:
        self.held[(job, label)].set()

    async def run(self, *, env, label, **kwargs):
        key = (int(env["GOLEM_JOB"]), label)
        self.started.append(key)
        self.calls.append({"env": env, "label": label, **kwargs})
        if key in self.held:
            await self.held[key].wait()
        return RunResult("verdict", verdict=verdict("done", f"{label} done"), session_id="s")


def side_by_side(golem: Golem, changes: int = 2, workers: int = 3):
    golem.store.put("workers", str(workers))
    golem.store.add_project("trees", "Trees", "Tree flashcards.")
    golem.store.update_project("trees", parallel_jobs=changes)
    return [golem.store.add_job("trees", "change", f"change {n}", "feature", title=f"Change {n}") for n in range(1, changes + 1)]


def test_two_changes_overlap_take_turns_building_and_ship_together(golem):
    async def go():
        a, b = side_by_side(golem)
        gate = golem.scheduler.harness = JobGate((a.id, "change"), (a.id, "review"))
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: (a.id, "change") in gate.started)
        await soon(lambda: plan.label(current(golem, b.id)) == "change" and golem.store.job(b.id).state == "queued")
        await asyncio.sleep(0.05)
        assert [s for s in gate.started if s[0] == b.id] == []  # set up beside A, then waits for A's build
        assert current(golem, b.id).started_at is None

        gate.let_go(a.id, "change")  # A reviews; B builds meanwhile
        await soon(lambda: (b.id, "change") in gate.started)
        assert (a.id, "review") in gate.started and a.id in golem.scheduler.running

        # B gets to shipping first and waits for A, still reviewing, so they go out together.
        await soon(lambda: turns.waiting_for(golem.store, golem.store.job(b.id)) == f"waiting for job {a.id} so they ship together")
        await asyncio.sleep(0.05)
        assert golem.deployer.deploys == [] and plan.label(current(golem, b.id)) == "push"
        gate.let_go(a.id, "review")
        await soon(lambda: golem.store.job(a.id).state == golem.store.job(b.id).state == "done")
        actions = [x for x in golem.workspace.actions if x.startswith(("push", "open PR", "merge", "back to main"))]
        assert actions == [
            f"merge golem/job-{b.id} into job {a.id}", f"push golem/job-{a.id}", "open PR #1: Change 1; Change 2", "merge PR #1",
            f"back to main: job {b.id}",
        ]  # fmt: skip
        assert len(golem.deployer.deploys) == 1
        loop.cancel()

    run(go())


def a_change_beside(golem: Golem, n: int, slug: str = "trees"):
    """Another change asked for on the project, the scheduler woken for it."""
    job = golem.store.add_job(slug, "change", f"change {n}", "feature", title=f"Change {n}")
    golem.scheduler.wake()
    return job


def test_a_change_ready_while_another_ships_waits_however_long_its_ci_takes(golem, monkeypatch):
    monkeypatch.setattr(scheduler, "CI_POLL", timedelta(0))  # check CI again straight away
    async def go():
        [a] = side_by_side(golem, changes=1)
        golem.store.update_project("trees", parallel_jobs=2)
        golem.scheduler.harness = JobGate()
        golem.workspace.ci_results = [CI("pending", "build: in_progress")] * 5000  # A's PR, until the test says
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: (at := current(golem, a.id)) is not None and plan.label(at) == "CI")
        b = a_change_beside(golem, 2)  # asked for once A is shipping: it waits for the next shipment
        await soon(lambda: turns.waiting_for(golem.store, golem.store.job(b.id)) == f"waiting for job {a.id} to finish shipping")
        golem.workspace.ci_results.clear()
        await soon(lambda: golem.store.job(a.id).state == golem.store.job(b.id).state == "done")
        actions = [x for x in golem.workspace.actions if x.startswith(("push", "open PR", "merge"))]
        assert actions == [
            f"push golem/job-{a.id}", "open PR #1: Change 1: change + review + docs", "merge PR #1",
            f"push golem/job-{b.id}", "open PR #2: Change 2: change + review + docs", "merge PR #2",
        ]  # fmt: skip
        assert golem.store.shipments_of(a.id) == golem.store.shipments_of(b.id) == []  # each went alone
        loop.cancel()

    run(go())


def test_a_fix_while_shipping_runs_while_a_change_asked_for_since_builds(golem):
    async def go():
        [a] = side_by_side(golem, changes=1)
        golem.store.update_project("trees", parallel_jobs=2)
        gate = golem.scheduler.harness = JobGate((a.id, "fix"))
        golem.workspace.ci_results = [CI("failed", "npm test: 1 failing")]  # A's PR
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: (w := turns.wants(golem.store, golem.store.job(a.id))) is not None and w.kind == "ship" and bool(w.started))
        b = a_change_beside(golem, 2)
        gate.held[(b.id, "change")] = asyncio.Event()
        await soon(lambda: (a.id, "fix") in gate.started and (b.id, "change") in gate.started)  # A has the ship turn: its fix is part of it
        assert {a.id, b.id} <= set(golem.scheduler.running)
        gate.let_go(a.id, "fix")
        await soon(lambda: golem.store.job(a.id).state == "done")
        assert b.id in golem.scheduler.running and gate.started.count((b.id, "change")) == 1  # B building all along
        gate.let_go(b.id, "change")
        await soon(lambda: golem.store.job(b.id).state == "done")
        loop.cancel()

    run(go())


def ship_steps(golem: Golem, job_id: int) -> list[tuple[str, str, str]]:
    return [(plan.label(s), s.state, s.summary or "") for s in plan.shown(golem.store.steps(job_id)) if s.kind in plan.CLASSES and plan.CLASSES[s.kind] == "ship"]


async def steps_until(golem: Golem, check) -> None:
    """Turn the scheduler a step at a time until `check()` is true."""
    for _ in range(200):
        if check():
            return
        await golem.step()
    raise AssertionError("never happened")


def test_changes_ready_to_ship_go_out_together_in_one_pr_ci_run_and_deploy(golem):
    async def go():
        a, b, c = side_by_side(golem, changes=3)
        golem.store.update_job(b.id, issue=7)  # the shared PR closes it
        await golem.finish()
        assert [golem.store.job(j.id).state for j in (a, b, c)] == ["done"] * 3
        actions = [x for x in golem.workspace.actions if x.startswith(("push", "open PR", "merge", "back to main", "CI"))]
        assert actions == [
            f"merge golem/job-{b.id} into job {a.id}", f"merge golem/job-{c.id} into job {a.id}", f"push golem/job-{a.id}",
            "open PR #1: Change 1; Change 2; Change 3", actions[4], "merge PR #1", actions[6],
            f"back to main: job {b.id}", f"back to main: job {c.id}",
        ]  # fmt: skip
        assert actions[4].startswith("CI on") and actions[6].startswith("CI on")  # once on the PR, once on main
        assert len(golem.deployer.deploys) == 1

        [body] = golem.workspace.bodies
        assert f"### Job {b.id}: Change 2\n\nCloses #7\n\n- **change:** (fake) change done" in body
        assert f"### Job {c.id}: Change 3\n\nCloses #2\n\n- **change:**" in body  # the issue its setup opened
        assert body.endswith(f"Opened by golem (job {a.id}), shipping jobs {a.id}, {b.id} and {c.id} together; it merges this once CI passes.")

        # A's steps shipped all three; B's and C's went by as done, saying so, with its PR and deploy.
        sync = next(s for s in golem.store.steps(a.id) if s.kind == "sync" and s.turn == "ship")
        assert sync.summary == f"Caught up: merged in jobs {b.id} and {c.id} to ship with it."
        deploy = next(s for s in golem.store.steps(a.id) if s.kind == "deploy")
        assert deploy.summary.endswith(f"is live, with jobs {b.id} and {c.id}.")
        theirs = ship_steps(golem, b.id)
        assert [(label, state) for label, state, _ in theirs] == [("push", "done"), ("PR", "done"), ("CI", "done"), ("merge", "done"), ("deploy", "done")]
        assert theirs[1][2] == f"With job {a.id}: PR #1 is open, with jobs {b.id} and {c.id}."
        assert theirs[4][2] == f"With job {a.id}: {deploy.summary}"
        assert set(next(s for s in golem.store.steps(b.id) if s.kind == "deploy").links) >= {"pr", "site", "commit"}
        [shipment] = golem.store.shipments_of(c.id)
        assert (shipment.carrier, list(shipment.jobs), shipment.state) == (a.id, [a.id, b.id, c.id], "shipped")

        said = golem.chat.texts("trees")
        assert sum(t.startswith("✓ deploy:") for t in said) == 1 and sum(t.startswith("Done:") for t in said) == 3

    run(go())


def test_a_change_asked_for_before_a_shipment_starts_joins_it_and_one_after_waits_for_the_next(golem):
    async def go():
        a, b = side_by_side(golem, changes=2)
        golem.store.update_project("trees", parallel_jobs=4)
        await steps_until(golem, lambda: turns.waiting_for(golem.store, golem.store.job(a.id)) == f"waiting for job {b.id} so they ship together")
        c = a_change_beside(golem, 3)  # A is waiting for B: C goes in the same shipment, and A waits for it too
        await steps_until(golem, lambda: golem.store.shipping())
        [shipment] = golem.store.shipping()
        assert (shipment.carrier, list(shipment.jobs)) == (a.id, [a.id, b.id, c.id])
        d = a_change_beside(golem, 4)  # asked for once it's started: it waits for the next
        await golem.finish()
        assert [golem.store.job(j.id).state for j in (a, b, c, d)] == ["done"] * 4
        assert [x for x in golem.workspace.actions if x.startswith("open PR")] == [
            "open PR #1: Change 1; Change 2; Change 3", "open PR #2: Change 4: change + review + docs",
        ]  # fmt: skip
        assert len(golem.deployer.deploys) == 2 and golem.store.shipments_of(d.id) == []

    run(go())


def test_a_change_waiting_on_riggy_doesnt_hold_up_the_others_shipping(golem):
    async def go():
        a, b, c = side_by_side(golem, changes=3)
        golem.store.update_job(c.id, state="waiting")  # asked him something: it could be days
        await golem.finish()
        assert [golem.store.job(j.id).state for j in (a, b, c)] == ["done", "done", "waiting"]
        [shipment] = golem.store.shipments_of(b.id)
        assert list(shipment.jobs) == [a.id, b.id] and golem.store.shipments_of(c.id) == []

    run(go())


def test_changes_shipping_together_that_conflict_are_merged_by_one_resolve(golem):
    async def go():
        a, b = side_by_side(golem)
        golem.harness.script = by_label({})
        golem.workspace.merges = [(2, ["src/app.js", "README.md"])]  # B's branch into A's, the first time
        await golem.finish()
        assert golem.store.job(a.id).state == golem.store.job(b.id).state == "done"
        assert [(plan.label(s), s.state) for s in plan.shown(golem.store.steps(a.id)) if s.turn == "ship"][:4] == [
            ("catch up with main", "failed"), ("resolve", "done"), ("catch up with main", "done"), ("push", "done"),
        ]  # fmt: skip
        resolve = next(c for c in golem.harness.calls if c.label == "resolve")
        assert f"Job {b.id} (“Change 2”) is ready to ship too, so it ships with this job" in resolve.prompt
        assert f"(`git merge golem/job-{b.id}`) conflicts in:\n- src/app.js\n- README.md" in resolve.prompt
        assert "It was asked for: “change 2”" in resolve.prompt and resolve.cwd == golem.workspace.tree_path(a.id)
        assert (f"Change 1 ships with job {b.id}, and merging them conflicts: 2 file(s) conflict with what ships with it "
                f"(job {b.id}). Merging them in.") in golem.chat.texts("trees")  # fmt: skip
        assert len(golem.deployer.deploys) == 1

    run(go())


def test_a_fix_after_ci_fails_on_a_shipment_hears_whose_work_is_in_it(golem):
    async def go():
        a, b = side_by_side(golem)
        golem.harness.script = by_label({})
        golem.workspace.ci_results = [CI("failed", "npm test: 1 failing")]  # the shipment's PR
        await golem.finish()
        fix = next(c for c in golem.harness.calls if c.label == "fix")
        assert f"This job's branch has the work of job {b.id} (“Change 2”) in it too" in fix.prompt
        assert golem.store.job(a.id).state == golem.store.job(b.id).state == "done" and len(golem.deployer.deploys) == 1

    run(go())


def test_stopping_the_change_carrying_a_shipment_leaves_the_others_to_ship_without_it(golem):
    async def go():
        a, b, c = side_by_side(golem, changes=3)
        await steps_until(golem, lambda: (at := current(golem, a.id)) is not None and plan.label(at) == "CI")
        assert await golem.control.kill(a.id) == f"Stopped job {a.id}."
        assert [s.state for s in golem.store.shipments_of(b.id)] == ["dropped"]
        await golem.finish()
        assert [golem.store.job(j.id).state for j in (a, b, c)] == ["killed", "done", "done"]
        dropped, shipped = golem.store.shipments_of(c.id)
        assert (dropped.carrier, dropped.state, shipped.carrier, list(shipped.jobs), shipped.state) == (a.id, "dropped", b.id, [b.id, c.id], "shipped")
        assert [x for x in golem.workspace.actions if x.startswith("open PR")][-1] == "open PR #2: Change 2; Change 3"
        assert len(golem.deployer.deploys) == 1

    run(go())


def test_a_change_stopped_once_merged_into_a_shipment_still_goes_out_with_it(golem):
    async def go():
        a, b, c = side_by_side(golem, changes=3)
        await steps_until(golem, lambda: (at := current(golem, a.id)) is not None and plan.label(at) == "CI")
        said = await golem.control.kill(b.id)
        assert said == f"Stopped job {b.id}. Its work was already merged in to ship with job {a.id}'s, so it still goes out with it."
        await golem.finish()
        assert [golem.store.job(j.id).state for j in (a, b, c)] == ["done", "killed", "done"]
        assert [s.state for s in golem.store.shipments_of(b.id)] == ["shipped"] and len(golem.deployer.deploys) == 1
        assert ship_steps(golem, b.id)[0][1] == "pending"  # stopped: nothing of its own went by

    run(go())


def test_a_change_catches_up_with_main_before_it_builds_and_ships_and_merges_conflicts(golem):
    async def go():
        [job] = side_by_side(golem, changes=2)[:1]
        golem.store.update_job(job.id + 1, state="killed")  # just one change here
        golem.harness.script = by_label({})
        golem.workspace.syncs = [(0, []), (2, ["src/app.js", "README.md"]), (2, [])]  # building, shipping, after the merge
        await golem.finish()
        assert golem.store.job(job.id).state == "done"
        assert [(plan.label(s), s.state) for s in golem.store.steps(job.id)][:9] == [
            ("set up", "done"), ("catch up with main", "skipped"), ("change", "done"), ("review", "done"), ("docs", "done"),
            ("catch up with main", "failed"), ("resolve", "done"), ("catch up with main", "done"), ("push", "done"),
        ]  # fmt: skip
        resolve = next(c for c in golem.harness.calls if c.label == "resolve")
        assert "Other work on this project shipped while this job was under way" in resolve.prompt
        assert "- src/app.js\n- README.md" in resolve.prompt and resolve.cwd == golem.workspace.tree_path(job.id)
        assert "Main moved on under Change 1: 2 file(s) conflict with what shipped since. Merging it in." in golem.chat.texts("trees")

    run(go())


def test_merging_in_circles_asks_riggy_for_a_hint(golem):
    async def go():
        [job] = side_by_side(golem, changes=2)[:1]
        golem.store.update_job(job.id + 1, state="killed")
        golem.harness.script = by_label({})
        golem.workspace.syncs = [(0, [])] + [(1, ["src/app.js"])] * 4
        await golem.finish()
        assert golem.store.job(job.id).state == "waiting"
        assert sum(s.name == "resolve" for s in golem.store.steps(job.id)) == 4
        assert golem.chat.texts("trees")[-1].startswith("I've tried to merge main into Change 1 3 times and it still conflicts")

    run(go())


def test_one_change_at_a_time_never_catches_up_by_itself(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        await golem.ask(room, "make the cards bigger")
        await golem.finish()
        job = golem.store.jobs()[0]
        assert "catch up with main" not in golem.labels(job.id) and not any(a.startswith("sync") for a in golem.workspace.actions)

    run(go())


async def two_at_once(golem: Golem) -> tuple[str, object]:
    """Tree Cards, allowing two changes at once, with one under way (on its change step)."""
    room = await idle_project(golem)
    golem.store.update_project("tree-cards", parallel_jobs=2)
    golem.harness.script = by_label({"request": [{"workflow": "change", "title": "Bigger Cards"}]})
    first = await golem.ask(room, "make the cards bigger")
    await golem.until("change")
    return room, first


def test_asking_beside_a_change_under_way_starts_one_beside_it_while_there_is_room(golem):
    async def go():
        room, first = await two_at_once(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change", "title": "Round Timer"}]})
        timer = await golem.ask(room, "add a timer to each round")
        asked = golem.harness.calls[-1]
        assert "to" not in asked.schema["properties"] and "$under_way" not in asked.prompt  # always a job of its own
        assert (timer.title, timer.workflow, timer.state) == ("Round Timer", "change", "queued")
        assert turns.behind(golem.store, timer) is None  # room for two: it starts beside the first, and they take turns
        golem.harness.script = by_label({"request": [{"workflow": "change", "title": "Card Backs"}]})
        backs = await golem.ask(room, "nicer card backs")
        assert turns.behind(golem.store, backs).id == first.id  # not a third at once: it waits
        # A note goes to the job it's for, from that job's pane.
        await golem.note(room, "make the timer red when it's nearly up", job=timer.id)
        assert golem.store.pending_notes(timer.id) and not golem.store.pending_notes(first.id)

    run(go())


def test_with_one_change_at_a_time_asking_queues_behind_it(golem):
    async def go():
        room = await idle_project(golem)
        golem.harness.script = by_label({"request": [{"workflow": "change"}, {"workflow": "change"}]})
        first = await golem.ask(room, "make the cards bigger")
        await golem.until("change")
        timer = await golem.ask(room, "and a timer")
        assert len(golem.store.change_jobs("tree-cards")) == 2 and turns.behind(golem.store, timer).id == first.id
        assert turns.waiting_for(golem.store, timer) == f"waiting for job {first.id} to finish"

    run(go())


def test_an_accepted_proposal_starts_beside_a_change_when_there_is_room(golem):
    from golem.control import Control

    async def go():
        room, first = await two_at_once(golem)
        p = golem.store.add_proposals("tree-cards", first.id, "features", [("Offline mode", "Cache the deck.")])[0]
        said = await Control(golem.store, golem.scheduler).decide(p.id, True, "")
        built = golem.store.job(golem.store.proposal(p.id).built_by)
        assert said == f"Accepted “Offline mode”: job {built.id} builds it."  # not once Bigger Cards is done
        assert built.title == "Offline mode" and built.state == "queued" and golem.store.job(first.id).state != "done"

    run(go())


def test_a_new_project_allows_nothing_beside_it(golem):
    async def go():
        room = await new_project(golem, [])
        golem.store.update_project("tree-cards", parallel_jobs=3)
        assert not golem.store.room_for_change("tree-cards")
        golem.harness.script = by_label({"request": [{"workflow": "change"}]})
        timer = await golem.ask(room, "and a timer")
        assert turns.behind(golem.store, timer).id == golem.store.active_job("tree-cards").id  # it runs alone
        assert golem.scheduler.pick(at="9999").id != timer.id

    run(go())


def test_with_two_changes_waiting_a_reply_goes_to_the_one_asked_first(golem):
    async def go():
        room, first = await two_at_once(golem)
        second = golem.store.add_job("tree-cards", "change", "add a timer", "feature", title="Round Timer")
        for job in (second, first):
            golem.store.update_job(job.id, state="waiting")
        await golem.reply(room, "oaks and maples")
        assert golem.store.job(first.id).state == "queued" and golem.store.job(second.id).state == "waiting"

    run(go())


def test_beside_a_running_change_a_question_starts_now_and_a_change_says_what_it_waits_for(golem):
    async def go():
        a, b = side_by_side(golem)
        gate = golem.scheduler.harness = JobGate((a.id, "change"))
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await soon(lambda: (a.id, "change") in gate.started)
        await soon(lambda: plan.label(current(golem, b.id)) == "change" and golem.store.job(b.id).state == "queued")
        assert turns.waiting_for(golem.store, golem.store.job(b.id)) == f"waiting for job {a.id} to finish building"
        golem.harness.script = by_label({"request": [{"workflow": "question"}]})
        question = await golem.ask("trees", "how big are the cards?")
        await soon(lambda: golem.store.steps(question.id) and golem.store.steps(question.id)[0].started_at)  # starts beside it
        loop.cancel()

    run(go())
