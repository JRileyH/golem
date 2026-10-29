"""golem: the bot, the scheduler and the web app, in one process."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from . import plan, recover
from .bot import Bot
from .config import Settings
from .deployer_client import DeployerClient, OfflineDeployer
from .harness import ClaudeHarness, FakeHarness
from .prompts import Prompts
from .push import Push
from .scheduler import Scheduler
from .store import Store
from .talk import Talk
from .web import create_app
from .workspace import GitHubWorkspace, LocalWorkspace, configure_git

log = logging.getLogger("golem")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per request otherwise
    settings = Settings.from_env()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    db = settings.state_dir / "golem.db"
    fresh = not db.exists()
    store = Store(db)
    store.suggest_defaults(plan.SUGGESTED_KEY, plan.SUGGESTED)

    if settings.harness == "fake":
        harness, workspace = FakeHarness(), LocalWorkspace(settings.workspace_dir)
    else:
        harness, workspace = ClaudeHarness(), GitHubWorkspace(settings.workspace_dir, settings.org)
        await configure_git(f"golem@{settings.base_domain}")
        if fresh:  # a new install: bring back whatever the org has of golem's (recover.py)
            store.put(recover.KEY, recover.PENDING)

    push = Push(settings, store)
    chat = Talk(store, push.notify)  # every message is kept for the web app; those that want Riggy are pushed
    prompts = Prompts(settings)
    deployer = DeployerClient(
        settings.deployer_url, os.environ.get("DEPLOYER_SECRET", ""), os.environ.get("DEPLOYER_ADMIN_SECRET", "")
    )
    # With the fake harness there's no real code to deploy: the pretend GitHub's commits don't exist.
    deploys = OfflineDeployer(settings.project_url("{slug}")) if settings.harness == "fake" else deployer
    scheduler = Scheduler(settings, store, chat, harness, workspace, prompts, deploys)
    bot = Bot(settings, store, chat, scheduler, harness, workspace, prompts, deployer)
    await recover.rebuild(settings, store, chat, recover.GitHub(settings.org))

    async def work() -> None:
        await recover.redeploy(settings, store, chat, workspace, deploys)
        await scheduler.run_forever()

    server = uvicorn.Server(uvicorn.Config(create_app(settings, store, scheduler, bot, push), host="0.0.0.0", port=8000, access_log=False))
    log.info("started: the web app is at %s", settings.url("golem"))
    await asyncio.gather(server.serve(), work())


if __name__ == "__main__":
    asyncio.run(main())
