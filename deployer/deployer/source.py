"""Where compose files come from: pushed commits only, never an agent's workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

import httpx

from .validate import Rejected

COMPOSE_PATH = "deploy/compose.yml"
PASSING = frozenset({"success", "neutral", "skipped"})
HINTS = {
    401: "DEPLOYER_GITHUB_TOKEN is invalid or expired",
    403: "DEPLOYER_GITHUB_TOKEN lacks a permission, or the rate limit was hit",
}


class GitHubError(Exception):
    """GitHub refused or failed a request: a configuration problem, not a bad project."""


class Source(Protocol):
    def compose_file(self, slug: str, sha: str) -> str: ...
    def failing_checks(self, slug: str, sha: str) -> list[str]: ...


class GitHubSource:
    """Reads <org>/<slug> through the GitHub API with a read-only token."""

    def __init__(self, org: str, token: str, client: httpx.Client | None = None):
        self.org = org
        self.client = client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )

    def compose_file(self, slug: str, sha: str) -> str:
        r = self.client.get(
            f"/repos/{self.org}/{slug}/contents/{COMPOSE_PATH}",
            params={"ref": sha},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if r.status_code == 404:
            raise Rejected([f"{COMPOSE_PATH} not found in {self.org}/{slug} at {sha}; is the commit pushed?"])
        _check(r, f"reading {COMPOSE_PATH}")
        return r.text

    def failing_checks(self, slug: str, sha: str) -> list[str]:
        # Workflow runs, not check runs: fine-grained tokens can't get the Checks
        # permission, but they can get Actions read.
        r = self.client.get(
            f"/repos/{self.org}/{slug}/actions/runs",
            params={"head_sha": sha, "per_page": 100},
            headers={"Accept": "application/vnd.github+json"},
        )
        _check(r, "reading workflow runs")
        latest: dict[int, dict] = {}
        for run in r.json()["workflow_runs"]:
            # A workflow started again for the same commit replaces its earlier run.
            if run["workflow_id"] not in latest or run["id"] > latest[run["workflow_id"]]["id"]:
                latest[run["workflow_id"]] = run
        if not latest:
            return ["no workflow runs on this commit yet; wait for CI to start"]
        return [
            f"{run['name']}: {run['conclusion'] or run['status']}"
            for run in latest.values()
            if run["status"] != "completed" or run["conclusion"] not in PASSING
        ]


def _check(r: httpx.Response, doing: str) -> None:
    if r.is_success:
        return
    try:
        detail = r.json().get("message", "")
    except ValueError:
        detail = ""
    parts = [f"GitHub returned {r.status_code} {doing}", detail, HINTS.get(r.status_code, "")]
    raise GitHubError("; ".join(p for p in parts if p))


class LocalSource:
    """Reads bare repos at <git_dir>/<slug>.git. Local mode only; there is no CI."""

    def __init__(self, git_dir: Path):
        self.git_dir = git_dir

    def compose_file(self, slug: str, sha: str) -> str:
        repo = self.git_dir / f"{slug}.git"
        if not repo.is_dir():
            raise Rejected([f"no repository for `{slug}`"])
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "--git-dir", str(repo), "show", f"{sha}:{COMPOSE_PATH}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise Rejected([f"{COMPOSE_PATH} not found in `{slug}` at {sha}; is the commit pushed?"])
        return result.stdout

    def failing_checks(self, slug: str, sha: str) -> list[str]:
        return []
