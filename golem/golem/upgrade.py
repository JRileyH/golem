"""Keeping golem itself up to date, from the web app (the deployer's upgrade.py does the work).

Every CHECK_EVERY golem asks the deployer what's running and what GitHub's `main` has, and
keeps the answer for the Settings tab (marked while golem is behind). Riggy presses
"Upgrade now", or turns on upgrading automatically (when golem is behind and has no work
queued or running). Either way the upgrade waits for a running step to finish, so no
agent's work is thrown away, and no new step starts meanwhile. The deployer then
fast-forwards golem's checkout, rebuilds and restarts the stack, and puts the old commit
back if the new one won't build or come up healthy. golem says how it went in its own
conversation once it's back (or, if nothing about golem itself changed, when it's done).

An automatic upgrade never retries a commit that failed or was rolled back: that waits for
a newer one, or for Riggy.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from .deployer_client import Deployer, DeployerError
from .store import Store, now
from .talk import STATUS, Talk

log = logging.getLogger(__name__)

CHECK_EVERY = timedelta(minutes=15)
WATCH_EVERY = timedelta(seconds=30)  # while an upgrade is under way
GIVE_UP = timedelta(minutes=45)  # an upgrade that neither finished nor came back by then
FINISHED = ("done", "failed", "rolled back")
SHOWN = 10  # of what's new, in the Settings tab


class Upgrades:
    def __init__(self, store: Store, deployer: Deployer, chat: Talk):
        self.store = store
        self.deployer = deployer
        self.chat = chat

    # --- what's known ------------------------------------------------------------

    def version(self) -> dict | None:
        """The deployer's last answer (deployer/deployer/upgrade.py `version`), with when it was asked."""
        raw = self.store.get("version")
        return json.loads(raw) if raw else None

    @property
    def state(self) -> str:
        """"" | pending (waiting for a running step) | started (the deployer is on it)."""
        return self.store.get("upgrade") or ""

    @property
    def auto(self) -> bool:
        return self.store.get("auto_upgrade") == "1"

    def holding(self) -> bool:
        """Whether no new step should start: an upgrade is waiting or under way."""
        return self.state in ("pending", "started")

    def behind(self) -> int:
        v = self.version() or {}
        return v.get("behind", 0) if v.get("enabled") else 0

    def blocked(self, v: dict) -> str:
        """Why the deployer would refuse to upgrade, as far as golem can tell; "" if it wouldn't."""
        if not v.get("enabled"):
            return f"Upgrading from here is off: {v.get('reason', 'unknown')}."
        problems = [
            v.get("fetch_error", ""),
            f"the checkout on the box is on {v.get('branch')}, not main" if v.get("branch") != "main" else "",
            "the checkout on the box has local changes" if not v.get("clean", True) else "",
            f"the checkout on the box has {v.get('ahead')} commit(s) GitHub doesn't" if v.get("ahead") else "",
        ]
        problems = [p for p in problems if p]
        return f"Can't upgrade: {'; '.join(problems)}." if problems else ""

    # --- asking the deployer -----------------------------------------------------

    async def check(self, fetch: bool = True) -> dict | None:
        """Ask what's running and what's new; tell Riggy how an upgrade went; start an automatic one."""
        try:
            v = await self.deployer.version(fetch)
        except DeployerError as e:
            log.warning("version check: %s", e)
            return self.version()
        v["checked"] = now()
        self.store.put("version", json.dumps(v))
        await self.settle(v)
        if self.auto and self.state == "" and self.due(v):
            self.store.put("upgrade", "pending")
            log.info("upgrading automatically to %s", v["latest"][:7])
        return v

    def due(self, v: dict) -> bool:
        """For an automatic upgrade: behind, nothing in the way, no work going, and not a commit that already failed."""
        last = v.get("upgrade") or {}
        if not v.get("behind") or self.blocked(v):
            return False
        if last.get("to") == v.get("latest") and last.get("state") in ("failed", "rolled back"):
            return False
        return not self.store.jobs(("queued", "running"), limit=1)

    async def settle(self, v: dict) -> None:
        """Once the deployer's upgrade has finished: say how it went, once, and let work carry on."""
        last = v.get("upgrade") or {}
        if last.get("state") in FINISHED and last.get("started") and self.store.get("upgrade_told") != last["started"]:
            self.store.put("upgrade_told", last["started"])
            await self.chat.send(STATUS, outcome(last))
            self.store.put("upgrade", "")
            return
        if self.state == "started" and last.get("state") != "running":
            since = datetime.fromisoformat(self.store.get("upgrade_at") or now())
            if datetime.now(UTC) - since > GIVE_UP:
                self.store.put("upgrade", "")
                await self.chat.send(STATUS, "The upgrade never finished or came back, so work carries on. Check the box: docker compose logs deployer.")

    def request(self) -> str:
        """Riggy pressed "Upgrade now": it starts at the next step boundary."""
        v = self.version()
        if v is None:
            return "golem hasn't checked for a new version yet: check first."
        if blocked := self.blocked(v):
            return blocked
        if not v.get("behind"):
            return "golem is up to date."
        if self.holding():
            return "An upgrade is already on its way."
        self.store.put("upgrade", "pending")
        return f"Upgrading to {v['latest'][:7]} once nothing's running: golem restarts and is back in a few minutes."

    async def start(self) -> None:
        """No step is running: ask the deployer to upgrade."""
        try:
            done = await self.deployer.upgrade()
        except DeployerError as e:
            self.store.put("upgrade", "")
            await self.chat.send(STATUS, f"Couldn't upgrade golem: {e}")
            return
        self.store.put("upgrade", "started")
        self.store.put("upgrade_at", now())
        v = self.version() or {}
        n = v.get("behind", 0)
        new = f" ({n} new commit{'s' if n != 1 else ''})" if n else ""
        await self.chat.send(STATUS, f"Upgrading golem to {str(done.get('to', ''))[:7]}{new}: it rebuilds and restarts, back in a few minutes.", notice=True)

    async def watch_forever(self) -> None:
        """Check every CHECK_EVERY, and every WATCH_EVERY while an upgrade is under way."""
        while True:
            try:
                await self.check(fetch=self.state != "started")
            except Exception:
                log.exception("version check failed")
            await asyncio.sleep((WATCH_EVERY if self.state == "started" else CHECK_EVERY).total_seconds())


def outcome(last: dict) -> str:
    """How an upgrade went, for golem's own conversation."""
    old, new, detail = str(last.get("from", ""))[:7], str(last.get("to", ""))[:7], last.get("detail", "")
    match last.get("state"):
        case "done":
            return f"Upgraded golem from {old} to {new}."
        case "rolled back":
            return f"The upgrade to {new} was rolled back: {detail}"
        case _:
            return f"The upgrade to {new} didn't happen: {detail}"
