"""Adopting a repo golem didn't make, end to end through the bot and scheduler, with GitHub,
Claude and the deployer faked (conftest.py)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from conftest import Golem

from golem.workspace import About

ABOUT = About("mimic", "Tabletop data in, cards out", False, True, "# Mimic\n\nTabletop data in, visual outputs out.\n")
BRIEF = {"name": "Mimic", "summary": "Printable cards for a D&D table.", "tag": "none", "mode": "unspecified",
         "model": "unspecified", "effort": "unspecified"}  # fmt: skip
PAGE = "https://golem.unlaunched.dev/p/mimic"


def run(coro):
    return asyncio.run(coro)


async def adopt(golem: Golem, words: str = "", brief: dict = BRIEF, about: About = ABOUT) -> None:
    golem.workspace.existing["mimic"] = about
    golem.harness.script = [brief]
    await golem.say(golem.inbox, f"!adopt mimic {words}".strip())


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
