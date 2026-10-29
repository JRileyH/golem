"""Upgrading golem itself from the web app: golem's side (the deployer's is in deployer/tests/test_upgrade.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from starlette.testclient import TestClient
from test_web import act, sign_in

from golem import upgrade
from golem.web import create_app

OLD, NEW = "a" * 40, "b" * 40


def behind(n: int = 2, **more) -> dict:
    commits = [{"sha": f"{i:040x}", "subject": f"Change {i}"} for i in range(n)]
    return {"enabled": True, "running": OLD, "checkout": OLD, "latest": NEW, "behind": n, "ahead": 0, "commits": commits,
            "branch": "main", "clean": True, "fetch_error": "", "upgrade": None, **more}  # fmt: skip


def finished(state: str, detail: str = "") -> dict:
    return {**behind(0, latest=OLD), "running": NEW, "upgrade": {"state": state, "from": OLD, "to": NEW, "started": "2026-10-29T10:00:00+00:00", "detail": detail}}


def run(coro):
    return asyncio.run(coro)


def news(golem) -> list[str]:
    return golem.chat.texts("status")


def test_pressing_upgrade_waits_for_the_running_step_then_says_how_it_went(golem):
    ups = golem.scheduler.upgrades

    async def go():
        assert ups.request() == "golem hasn't checked for a new version yet: check first."
        golem.deployer.versions = [behind(2)]
        v = await ups.check()
        assert golem.deployer.fetches == [True] and ups.behind() == 2 and v["checked"]
        assert ups.request().startswith(f"Upgrading to {NEW[:7]} once nothing's running")
        assert ups.state == "pending" and ups.holding() and ups.request() == "An upgrade is already on its way."

        # The loop starts the upgrade at a step boundary, and starts no new step meanwhile.
        golem.store.add_project("trees", "Trees", "Flashcards")
        job = golem.store.add_job("trees", "new", "a flashcard game", "standard")
        loop = asyncio.create_task(golem.scheduler.run_forever())
        await asyncio.sleep(0.05)
        loop.cancel()
        assert golem.deployer.upgrades == 1 and ups.state == "started"
        assert golem.store.steps(job.id) == [] and golem.store.job(job.id).state == "queued"
        assert news(golem)[-1] == f"Upgrading golem to {NEW[:7]} (2 new commits): it rebuilds and restarts, back in a few minutes."

        golem.deployer.versions = [{**behind(2), "upgrade": {"state": "running", "from": OLD, "to": NEW, "started": "s"}}]
        await ups.check(fetch=False)
        assert ups.state == "started"  # still under way
        golem.deployer.versions = [finished("done")]
        await ups.check()
        assert news(golem)[-1] == f"Upgraded golem from {OLD[:7]} to {NEW[:7]}." and ups.state == "" and ups.behind() == 0
        said = len(news(golem))
        await ups.check()
        assert len(news(golem)) == said  # told once

    run(go())


def test_upgrades_the_deployer_refuses_or_that_fail_say_why(golem):
    ups = golem.scheduler.upgrades

    async def go():
        golem.deployer.versions = [behind(1, clean=False, branch="mine")]
        await ups.check()
        assert ups.request() == "Can't upgrade: the checkout on the box is on mine, not main; the checkout on the box has local changes."
        golem.deployer.versions = [{"enabled": False, "reason": "GOLEM_HOST_DIR isn't set"}]
        await ups.check()
        assert ups.request() == "Upgrading from here is off: GOLEM_HOST_DIR isn't set." and ups.behind() == 0

        golem.deployer.versions = [behind(1)]
        await ups.check()
        ups.request()
        golem.deployer.upgrade_refused = "bin/bootstrap.sh needs a hand on the box"
        await ups.start()
        assert ups.state == "" and news(golem)[-1] == "Couldn't upgrade golem: bin/bootstrap.sh needs a hand on the box"

        golem.deployer.versions = [finished("rolled back", "golem was unhealthy on bbbbbbb, and aaaaaaa is running again")]
        await ups.check()
        assert news(golem)[-1] == "The upgrade to bbbbbbb was rolled back: golem was unhealthy on bbbbbbb, and aaaaaaa is running again"

    run(go())


def test_an_upgrade_that_never_comes_back_lets_work_carry_on(golem):
    ups = golem.scheduler.upgrades

    async def go():
        golem.store.put("upgrade", "started")
        golem.store.put("upgrade_at", (datetime.now(UTC) - upgrade.GIVE_UP - timedelta(minutes=1)).isoformat())
        golem.deployer.versions = [behind(1)]  # its upgrade.json never said it was running
        await ups.check()
        assert ups.state == "" and news(golem)[-1].startswith("The upgrade never finished or came back")

    run(go())


def test_upgrading_automatically_only_when_idle_and_never_retrying_a_failed_commit(golem):
    ups = golem.scheduler.upgrades

    async def go():
        golem.deployer.versions = [behind(1)]
        await ups.check()
        assert ups.state == ""  # off unless he says
        golem.store.put("auto_upgrade", "1")
        golem.store.add_project("trees", "Trees", "Flashcards")
        job = golem.store.add_job("trees", "new", "a flashcard game", "standard")
        await ups.check()
        assert ups.state == ""  # work is queued
        golem.store.update_job(job.id, state="waiting")  # waiting on Riggy holds nothing up
        await ups.check()
        assert ups.state == "pending"

        golem.store.put("upgrade", "")
        golem.deployer.versions = [{**behind(1), "upgrade": {"state": "failed", "from": OLD, "to": NEW, "started": "x", "detail": "build"}}]
        await ups.check()
        assert ups.state == ""  # that commit failed already: it waits for a newer one, or for Riggy
        golem.deployer.versions = [{**behind(2, latest="c" * 40), "upgrade": {"state": "failed", "from": OLD, "to": NEW, "started": "x"}}]
        await ups.check()
        assert ups.state == "pending"

    run(go())


def test_the_settings_tab_shows_whats_new_and_upgrades(golem):
    client = TestClient(create_app(golem.settings, golem.store, golem.scheduler), base_url="https://golem.unlaunched.dev")
    sign_in(client)
    html = client.get("/settings").text
    assert "Not checked for a new version yet." in html and "class=dot" not in html
    golem.deployer.versions = [behind(2)]
    assert "2%20new%20commits%20on%20GitHub" in act(client, action="check-version")
    html = client.get("/settings").text
    assert "Running <code>aaaaaaa</code>; GitHub's main has 2 new commits." in html and "Change 1" in html
    assert "value='upgrade'" in html and "class=dot" in html  # the Settings tab is marked
    assert "Upgrading%20to%20bbbbbbb" in act(client, action="upgrade")
    html = client.get("/settings").text
    assert "Upgrading once the running step finishes." in html and "value='upgrade'" not in html
    assert "upgrades%20itself" in act(client, action="auto-upgrade", value="on")
    assert golem.scheduler.upgrades.auto and "Upgrade automatically: on" in client.get("/settings").text
    act(client, action="auto-upgrade", value="off")
    assert not golem.scheduler.upgrades.auto
    client.cookies.clear()
    assert "class=dot" not in client.get("/settings").text and "Upgrade now" not in client.get("/settings").text
