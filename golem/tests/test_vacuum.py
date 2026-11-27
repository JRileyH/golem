"""A project in a vacuum (Project.vacuum): it runs as on a fresh golem, sharing nothing golem has learnt."""

from __future__ import annotations

import asyncio
import os

from test_flow import TREES, by_label, idle_project


def run(coro):
    return asyncio.run(coro)


def test_a_project_in_a_vacuum_runs_as_on_a_fresh_golem(golem):
    async def go():
        golem.settings.lessons().write_text("- golem's lesson from before\n")
        golem.harness.script = [TREES]
        job = await golem.bot.new_project("Trees", "a flashcard game for trees", slug="trees", vacuum=True)
        assert golem.store.project("trees").vacuum and golem.settings.lessons("trees").read_text() == ""  # nothing golem has learnt
        await golem.until("spec")
        await golem.step()
        spec = golem.harness.calls[-1]
        assert "golem's lesson from before" not in spec.prompt
        assert str(golem.settings.lessons("trees")) in spec.prompt  # where its own go
        config = golem.settings.job_claude(job.id)
        assert spec.env["CLAUDE_CONFIG_DIR"] == str(config) and config.is_dir()  # a Claude Code of its own, as freshly installed
        assert spec.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert spec.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1" and spec.env["BASH_DEFAULT_TIMEOUT_MS"] == "300000"
        assert golem.settings.lessons().read_text() == "- golem's lesson from before\n"  # golem's own, untouched
        golem.store.update_job(job.id, state="done")
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
