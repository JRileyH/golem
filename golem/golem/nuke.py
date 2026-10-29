"""Start over with zero projects.

    python -m golem.nuke          say what would be deleted (exit 3 if nothing)
    python -m golem.nuke --yes    delete it; golem must be stopped

bin/nuke.sh runs this after asking. It deletes every project the way !delete does,
every other site the deployer still has (stopped ones and hello too) with its data,
and whatever else golem keeps per project: checkouts, run logs, the triage log,
lessons and proposals. It keeps the stack, golem's own conversation (new ideas and its
news) and .env. GitHub repos stay for Riggy to delete: golem's token can't.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

from . import teardown
from .config import Settings
from .deployer_client import Deployer, DeployerClient, DeployerError
from .store import Store
from .workspace import GitHubWorkspace, LocalWorkspace, Workspace, WorkspaceError, remove_tree

NOTHING = "Nothing to delete: golem has no projects, sites or files left."


def leftovers(settings: Settings) -> list[Path]:
    """What golem keeps besides its database: checkouts, run and triage logs, lessons, proposals."""
    paths = sorted(settings.workspace_dir.iterdir()) if settings.workspace_dir.is_dir() else []
    state = settings.state_dir
    return paths + [p for p in (state / "jobs", state / "triage", state / "proposals", state / "lessons.md") if p.exists()]


async def plan(settings: Settings, store: Store, deployer: Deployer) -> str:
    projects = store.projects()
    others = [slug for slug in await deployer.projects() if store.project(slug) is None]
    files = leftovers(settings)
    if not (projects or others or files):
        return NOTHING
    lines = ["This deletes, for good:"]
    if projects:
        lines.append(f"- {len(projects)} project(s), each with its site and data, checkout, jobs, run logs and conversation:")
        lines += [f"    {p.slug} ({p.name})" for p in projects]
    if others:
        lines.append(f"- {len(others)} more site(s) the deployer has, with their data: {', '.join(others)}")
    if files:
        lines.append(f"- everything in {settings.workspace_dir}, golem's run logs, triage log, lessons.md and proposals")
    lines += [
        "Kept: the stack, golem's own conversation, and .env.",
        f"Not touched: the GitHub repos. Delete them at https://github.com/orgs/{settings.org}/repositories",
    ]
    return "\n".join(lines)


async def wipe(
    settings: Settings,
    store: Store,
    workspace: Workspace,
    deployer: Deployer,
    say: Callable[[str], None] = print,
) -> list[str]:
    """Delete it all. Returns what couldn't be deleted; running it again retries those."""
    problems = []
    for project in store.projects():
        try:
            await teardown.delete_project(project, settings, store, workspace, deployer)
            say(f"deleted project {project.slug}")
        except (DeployerError, WorkspaceError) as e:
            problems.append(f"{project.slug}: {e}")
    for slug in await deployer.projects():
        if store.project(slug):
            continue  # its delete failed above
        try:
            await deployer.delete(slug)
            say(f"deleted site {slug}")
        except DeployerError as e:
            problems.append(f"{slug}: {e}")
    for path in leftovers(settings):
        try:
            if path.is_dir():
                await remove_tree(path)
            else:
                path.unlink(missing_ok=True)
        except (WorkspaceError, OSError) as e:
            problems.append(str(e))
    return problems


async def golem_running() -> bool:
    """The golem service answers on the internal network (this runs in a one-off container beside it)."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.get("http://golem:8000/healthz")
        return True
    except httpx.HTTPError:
        return False


async def main(argv: list[str]) -> int:
    if argv not in ([], ["--yes"]):
        print(__doc__, file=sys.stderr)
        return 2
    settings = Settings.from_env()
    admin_secret = os.environ.get("DEPLOYER_ADMIN_SECRET", "")
    if not admin_secret:
        print("DEPLOYER_ADMIN_SECRET isn't set: run bin/bootstrap.sh, then docker compose up -d", file=sys.stderr)
        return 1
    store = Store(settings.state_dir / "golem.db")
    deployer = DeployerClient(settings.deployer_url, os.environ.get("DEPLOYER_SECRET", ""), admin_secret)
    try:
        if not argv:
            text = await plan(settings, store, deployer)
            print(text)
            return 3 if text == NOTHING else 0
        if await golem_running():
            print("golem is running: stop it first (bin/nuke.sh does)", file=sys.stderr)
            return 1
        if settings.harness == "fake":
            workspace = LocalWorkspace(settings.workspace_dir)
        else:
            workspace = GitHubWorkspace(settings.workspace_dir, settings.org)
        problems = await wipe(settings, store, workspace, deployer)
    except (DeployerError, httpx.HTTPError) as e:
        print(f"Stopped: {e or type(e).__name__}", file=sys.stderr)
        return 1
    if problems:
        print("Couldn't delete:\n" + "\n".join(f"  {p}" for p in problems) + "\nRun it again to retry.", file=sys.stderr)
        return 1
    print(f"Done: golem has no projects. Delete the GitHub repos at https://github.com/orgs/{settings.org}/repositories")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
