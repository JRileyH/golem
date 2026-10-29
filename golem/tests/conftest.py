from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from golem import plan
from golem.bot import Bot
from golem.config import Settings
from golem.deployer_client import DeployerError, Deployment
from golem.cards import Cards
from golem.harness import FakeHarness, RunResult
from golem.prompts import Prompts
from golem.scheduler import Scheduler
from golem.store import Store
from golem.talk import INBOX, Talk
from golem.workspace import LocalWorkspace

REPO = Path(__file__).resolve().parents[2]


class Said:
    """What golem said, read back from the conversation it keeps, by channel."""

    def __init__(self, store: Store):
        self.store = store

    def _golem(self) -> list:
        rows = self.store.db.execute("SELECT * FROM messages WHERE who = 'golem' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    @property
    def sent(self) -> list[tuple[str, str, bool]]:
        """(channel, text, notice), oldest first."""
        return [(m["channel"], m["text"], not m["notify"]) for m in self._golem()]

    @property
    def images(self) -> list[tuple[str, str, str]]:
        """(channel, file name, caption) of each picture."""
        return [(m["channel"], m["image"].rsplit("/", 1)[-1], m["text"]) for m in self._golem() if m["image"]]

    def texts(self, channel: str) -> list[str]:
        return [text for where, text, _ in self.sent if where == channel]


class FakeDeployer:
    def __init__(self):
        self.stopped: list[str] = []
        self.deleted: list[str] = []
        self.deployed: list[str] = []  # what /projects lists
        self.fail: str | None = None  # set to make deletes fail with this reason
        self.deploys: list[tuple[str, str]] = []
        self.refuse: list[str] = []  # problems for the next deploys, in order
        self.versions: list[dict] = [{"enabled": False, "reason": "not set up"}]  # /version, in turn; the last repeats
        self.fetches: list[bool] = []
        self.upgrades = 0
        self.upgrade_refused = ""  # set to make upgrades fail with this reason

    async def deploy(self, slug: str, sha: str) -> Deployment:
        self.deploys.append((slug, sha))
        if self.refuse:
            return Deployment(False, problem=self.refuse.pop(0))
        return Deployment(True, url=f"https://{slug}.unlaunched.dev/")

    async def stop(self, slug: str) -> None:
        self.stopped.append(slug)

    async def delete(self, slug: str) -> None:
        if self.fail:
            raise DeployerError(self.fail)
        self.deleted.append(slug)
        if slug in self.deployed:
            self.deployed.remove(slug)

    async def projects(self) -> list[str]:
        return list(self.deployed)

    async def version(self, fetch: bool = False) -> dict:
        self.fetches.append(fetch)
        return dict(self.versions.pop(0) if len(self.versions) > 1 else self.versions[0])

    async def upgrade(self) -> dict:
        if self.upgrade_refused:
            raise DeployerError(self.upgrade_refused)
        self.upgrades += 1
        return {"from": "a" * 40, "to": "b" * 40}


class Golem:
    """The bot and scheduler wired to fakes, as in production."""

    def __init__(self, tmp: Path, script=None, **overrides):
        self.settings = replace(
            Settings(
                base_domain="unlaunched.dev",
                edge_port="",
                harness="claude",
                org="unlaunched",
                deployer_url="http://deployer:8000",
                state_dir=tmp / "state",
                workspace_dir=tmp / "workspace",
                process_dir=REPO,
                web_password="pw",
            ),
            **overrides,
        )
        self.settings.state_dir.mkdir(parents=True)
        self.store = Store(self.settings.state_dir / "golem.db")
        self.talk = Talk(self.store)  # what golem said and heard, kept for the web app
        self.chat = Said(self.store)
        self.harness = FakeHarness(script)
        self.workspace = LocalWorkspace(self.settings.workspace_dir)
        self.prompts = Prompts(self.settings)
        self.deployer = FakeDeployer()
        self.scheduler = Scheduler(
            self.settings, self.store, self.talk, self.harness, self.workspace, self.prompts, self.deployer
        )
        self.bot = Bot(
            self.settings, self.store, self.talk, self.scheduler, self.harness, self.workspace, self.prompts, self.deployer
        )
        self.bot.start_delay = timedelta(0)  # no grace period in tests
        # Cards get their own scripted model and a pretend browser, out of the way of the job's script.
        self.card_calls: list = []
        self.shots: list[str] = []
        self.scheduler.cards = Cards(self.settings, self.store, FakeHarness(self.card), shoot=self.shoot)

    def card(self, call) -> RunResult:
        self.card_calls.append(call)
        card = {"tagline": "Name that tree", "description": "Flip cards until you know them.", "kind": "game"}
        return RunResult("verdict", verdict=card)

    async def shoot(self, url: str, path: Path) -> bool:
        self.shots.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8jpeg")
        return True

    async def say(self, channel: str, body: str) -> None:
        await self.bot.handle(channel, body)

    async def step(self) -> None:
        """One scheduler turn: the next job's current step."""
        job = self.store.next_job(at="9999")  # CI waits and retries are due at once
        assert job is not None, "nothing queued"
        await self.scheduler.step(job)

    async def until(self, name: str) -> None:
        """Turn the scheduler until the job's current step is `name` (and hasn't run yet)."""
        for _ in range(100):
            job = self.store.next_job(at="9999")
            current = job and plan.current(self.scheduler.ensure_steps(job))
            if current and plan.label(current) == name and current.started_at is None:
                return
            await self.step()
        raise AssertionError(f"never got to {name}")

    async def finish(self) -> None:
        """Turn the scheduler until nothing is queued."""
        for _ in range(100):
            if self.store.next_job(at="9999") is None:
                return
            await self.step()
        raise AssertionError("still going after 100 steps")

    def labels(self, job_id: int, states=("done", "skipped", "failed")) -> list[str]:
        return [plan.label(s) for s in self.store.steps(job_id) if s.state in states]

    inbox = INBOX


@pytest.fixture
def golem(tmp_path):
    return Golem(tmp_path)
