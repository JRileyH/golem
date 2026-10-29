"""Project checkouts and the GitHub side of a job: the repo, its issue, the job's branch,
pull requests, CI and merging.

Done by golem rather than the worker, so they happen the same way every time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

# Workflow run conclusions that count as passing (the deployer agrees: deployer/source.py).
PASSING = frozenset({"success", "neutral", "skipped"})
# Never committed on a worker's behalf, even when a project has no .gitignore yet.
NEVER_COMMIT = (":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.venv/**", ":(exclude,glob)**/__pycache__/**")


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    sha: str  # the head commit


@dataclass(frozen=True)
class CI:
    state: str  # none (no workflow runs yet) | pending | passed | failed
    detail: str = ""  # which runs; for a failure, the failed steps' log
    url: str = ""


class Workspace(Protocol):
    def path(self, slug: str) -> Path: ...
    async def ensure_repo(self, slug: str, description: str) -> Path: ...
    async def ensure_main(self, slug: str, readme: str) -> None: ...
    async def sync_main(self, slug: str) -> None: ...
    async def open_issue(self, slug: str, title: str, body: str) -> int | None: ...
    async def repo_exists(self, slug: str) -> bool: ...
    async def move_repo(self, old: str, new: str, description: str) -> Path: ...
    async def remove(self, slug: str) -> None: ...
    async def start_branch(self, slug: str, branch: str) -> None: ...
    async def current_branch(self, slug: str) -> str: ...
    async def clean(self, slug: str) -> bool: ...
    async def discard(self, slug: str) -> None: ...
    async def commit_all(self, slug: str, message: str) -> bool: ...
    async def push_branch(self, slug: str, branch: str) -> str | None: ...
    async def push_main(self, slug: str, branch: str) -> str | None: ...
    async def pull_request(self, slug: str, branch: str) -> PullRequest | None: ...
    async def open_pr(self, slug: str, branch: str, title: str, body: str) -> PullRequest | None: ...
    async def merge(self, slug: str, pr: PullRequest, branch: str) -> str: ...
    async def main_sha(self, slug: str) -> str: ...
    async def ci(self, slug: str, sha: str) -> CI: ...


async def remove_tree(path: Path) -> None:
    """Delete a checkout (or any directory). Has to succeed: a stale checkout would be
    reused by the next project with the same slug."""
    try:
        await asyncio.to_thread(shutil.rmtree, path)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise WorkspaceError(f"removing {path}: {e}") from None


async def run(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


class GitHubWorkspace:
    def __init__(self, root: Path, org: str):
        self.root = root
        self.org = org

    def path(self, slug: str) -> Path:
        return self.root / slug

    async def repo_exists(self, slug: str) -> bool:
        code, _ = await run("gh", "repo", "view", f"{self.org}/{slug}", "--json", "name")
        return code == 0

    async def ensure_repo(self, slug: str, description: str) -> Path:
        repo = f"{self.org}/{slug}"
        if not await self.repo_exists(slug):
            code, out = await run("gh", "repo", "create", repo, "--private", "--description", description[:350])
            if code != 0:
                raise WorkspaceError(f"creating {repo}: {out[-300:]}")
        path = self.path(slug)
        if not (path / ".git").is_dir():
            code, out = await run("gh", "repo", "clone", repo, str(path))
            if code != 0:
                raise WorkspaceError(f"cloning {repo}: {out[-300:]}")
        return path

    async def move_repo(self, old: str, new: str, description: str) -> Path:
        """Copy every branch and tag of `old` into a new repo `new` and point the checkout at it.
        The old repo stays (deleting needs admin rights the token doesn't have)."""
        path = await self.ensure_repo(old, description)
        code, out = await run("git", "fetch", "--quiet", "--prune", "--tags", "origin", cwd=path)
        if code != 0:
            raise WorkspaceError(f"fetching {old}: {out[-300:]}")
        await run("git", "remote", "set-head", "origin", "--delete", cwd=path)
        repo = f"{self.org}/{new}"
        code, out = await run("gh", "repo", "create", repo, "--private", "--description", description[:350])
        if code != 0:
            raise WorkspaceError(f"creating {repo}: {out[-300:]}")
        url = f"https://github.com/{repo}.git"
        for refspec in ("refs/remotes/origin/*:refs/heads/*", "refs/tags/*:refs/tags/*"):
            code, out = await run("git", "push", "--quiet", url, refspec, cwd=path)
            if code != 0 and "src refspec" not in out:  # no tags is fine
                raise WorkspaceError(f"pushing to {repo}: {out[-300:]}")
        await run("git", "remote", "set-url", "origin", url, cwd=path)
        await run("gh", "repo", "edit", repo, "--default-branch", "main")
        target = self.path(new)
        path.rename(target)
        return target

    async def remove(self, slug: str) -> None:
        """Delete the checkout. The repo stays: the token can't delete repos."""
        await remove_tree(self.path(slug))

    async def sync_main(self, slug: str) -> None:
        """Start a job from the latest main. Best effort: an empty repo or local changes leave it alone."""
        path = self.path(slug)
        await run("git", "fetch", "--quiet", "origin", cwd=path)
        code, dirty = await run("git", "status", "--porcelain", cwd=path)
        if code != 0 or dirty:
            return
        for args in (("git", "checkout", "--quiet", "main"), ("git", "merge", "--quiet", "--ff-only", "origin/main")):
            code, out = await run(*args, cwd=path)
            if code != 0:
                log.info("sync %s: %s", slug, out[-200:])
                return

    async def open_issue(self, slug: str, title: str, body: str) -> int | None:
        code, out = await run(
            "gh", "issue", "create", "--repo", f"{self.org}/{slug}", "--title", title[:200], "--body", body
        )  # fmt: skip
        if code != 0:
            log.warning("issue for %s: %s", slug, out[-300:])
            return None
        try:
            return int(out.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            return None

    # --- the job's branch, from first commit to merge ---------------------------

    async def git(self, slug: str, *args: str) -> str:
        code, out = await run("git", *args, cwd=self.path(slug))
        if code != 0:
            raise WorkspaceError(f"git {args[0]} in {slug}: {out[-300:]}")
        return out

    async def has_main(self, slug: str) -> bool:
        await self.git(slug, "fetch", "--quiet", "--prune", "origin")
        code, _ = await run("git", "rev-parse", "--verify", "--quiet", "origin/main", cwd=self.path(slug))
        return code == 0

    async def ensure_main(self, slug: str, readme: str) -> None:
        """A new repo is empty: give it a first commit on main, so there's something to branch from."""
        if await self.has_main(slug):
            return
        path = self.path(slug)
        if not (path / "README.md").exists():
            (path / "README.md").write_text(readme)
        await self.git(slug, "add", "README.md")
        await self.git(slug, "commit", "--quiet", "-m", "Start the project")
        await self.git(slug, "push", "--quiet", "-u", "origin", "HEAD:main")

    async def start_branch(self, slug: str, branch: str) -> None:
        """Be on the job's branch: as it is, or new from wherever the checkout is (main, normally)."""
        if await self.git(slug, "branch", "--show-current") == branch:
            return
        code, _ = await run("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=self.path(slug))
        await self.git(slug, "checkout", "--quiet", *(() if code == 0 else ("-b",)), branch)

    async def current_branch(self, slug: str) -> str:
        return await self.git(slug, "branch", "--show-current") or "HEAD"

    async def clean(self, slug: str) -> bool:
        """Whether the checkout has nothing uncommitted (ignored files and installed dependencies aside)."""
        code, out = await run("git", "status", "--porcelain", "--", ".", *NEVER_COMMIT, cwd=self.path(slug))
        return code == 0 and not out

    async def discard(self, slug: str) -> None:
        """Put a clean checkout back as it was: after a question, nothing it touched stays."""
        await self.git(slug, "reset", "--quiet", "--hard", "HEAD")
        await self.git(slug, "clean", "--quiet", "-fd", "--", ".", *NEVER_COMMIT)  # installed dependencies stay

    async def commit_all(self, slug: str, message: str) -> bool:
        """Commit whatever the worker left uncommitted. Returns whether there was anything."""
        await self.git(slug, "add", "-A", "--", ".", *NEVER_COMMIT)
        code, _ = await run("git", "diff", "--cached", "--quiet", cwd=self.path(slug))
        if code == 0:
            return False
        await self.git(slug, "commit", "--quiet", "-m", message)
        return True

    async def ahead(self, slug: str, ref: str = "HEAD") -> int:
        """How many commits `ref` has that main doesn't."""
        await self.git(slug, "fetch", "--quiet", "--prune", "origin")
        return int(await self.git(slug, "rev-list", "--count", f"origin/main..{ref}"))

    async def push_branch(self, slug: str, branch: str) -> str | None:
        """Push the checkout's work to the job's branch on GitHub. None if there's nothing new for main."""
        if not await self.ahead(slug):
            return None
        await self.git(slug, "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}")
        return await self.git(slug, "rev-parse", "HEAD")

    async def push_main(self, slug: str, branch: str) -> str | None:
        """Put the work straight on main (reckless mode). None if there's nothing new."""
        if not await self.ahead(slug):
            return None
        await self.git(slug, "push", "--quiet", "origin", "HEAD:main")
        sha = await self.git(slug, "rev-parse", "HEAD")
        await self.back_to_main(slug, branch)
        return sha

    async def back_to_main(self, slug: str, branch: str) -> None:
        """After a merge: the checkout on the latest main, the job's branch gone, ready for the next."""
        await self.git(slug, "fetch", "--quiet", "--prune", "origin")
        await self.git(slug, "checkout", "--quiet", "-B", "main", "origin/main")
        await run("git", "branch", "-D", branch, cwd=self.path(slug))

    async def pull_request(self, slug: str, branch: str) -> PullRequest | None:
        code, out = await run(
            "gh", "pr", "list", "--repo", f"{self.org}/{slug}", "--head", branch, "--state", "open",
            "--json", "number,url,headRefOid", "--limit", "1",
        )  # fmt: skip
        if code != 0:
            raise WorkspaceError(f"listing PRs on {slug}: {out[-300:]}")
        prs = json.loads(out or "[]")
        return PullRequest(prs[0]["number"], prs[0]["url"], prs[0]["headRefOid"]) if prs else None

    async def open_pr(self, slug: str, branch: str, title: str, body: str) -> PullRequest | None:
        """The branch's open PR, opened now if need be. None if the branch has nothing for main."""
        pr = await self.pull_request(slug, branch)
        if pr:
            return pr
        code, _ = await run("git", "rev-parse", "--verify", "--quiet", f"origin/{branch}", cwd=self.path(slug))
        if code != 0 or not await self.ahead(slug, f"origin/{branch}"):
            return None
        code, out = await run(
            "gh", "pr", "create", "--repo", f"{self.org}/{slug}", "--head", branch, "--base", "main",
            "--title", title[:200], "--body", body,
        )  # fmt: skip
        if code != 0:
            raise WorkspaceError(f"opening a PR on {slug}: {out[-300:]}")
        return await self.pull_request(slug, branch)

    async def merge(self, slug: str, pr: PullRequest, branch: str) -> str:
        """Squash-merge exactly the commit CI checked. Returns the merge commit."""
        repo = f"{self.org}/{slug}"
        code, out = await run(
            "gh", "pr", "merge", str(pr.number), "--repo", repo, "--squash", "--match-head-commit", pr.sha
        )  # fmt: skip
        if code != 0:
            raise WorkspaceError(f"merging PR #{pr.number} on {slug}: {out[-300:]}")
        code, sha = await run("gh", "pr", "view", str(pr.number), "--repo", repo, "--json", "mergeCommit", "-q", ".mergeCommit.oid")
        if code != 0 or not sha:
            raise WorkspaceError(f"reading PR #{pr.number}'s merge commit on {slug}: {sha[-300:]}")
        await run("git", "push", "--quiet", "origin", "--delete", branch, cwd=self.path(slug))
        await self.back_to_main(slug, branch)
        return sha

    async def main_sha(self, slug: str) -> str:
        await self.git(slug, "fetch", "--quiet", "origin")
        return await self.git(slug, "rev-parse", "origin/main")

    async def ci(self, slug: str, sha: str) -> CI:
        """The workflow runs on a commit, judged the way the deployer judges them."""
        repo = f"{self.org}/{slug}"
        code, out = await run("gh", "api", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
        if code != 0 and "not accessible by personal access token" in out:
            raise WorkspaceError(
                "golem's GitHub token (GOLEM_GITHUB_TOKEN) can't read CI: give it Actions read-only "
                "on GitHub (Settings, Developer settings, its fine-grained token), then reply here"
            )
        if code != 0:
            raise WorkspaceError(f"reading CI on {slug}: {out[-300:]}")
        latest: dict[int, dict] = {}
        for r in json.loads(out)["workflow_runs"]:
            # A workflow started again for the same commit replaces its earlier run.
            if r["workflow_id"] not in latest or r["id"] > latest[r["workflow_id"]]["id"]:
                latest[r["workflow_id"]] = r
        runs = list(latest.values())
        if not runs:
            return CI("none")
        summary = ", ".join(f"{r['name']}: {r['conclusion'] or r['status']}" for r in runs)
        failed = [r for r in runs if r["status"] == "completed" and r["conclusion"] not in PASSING]
        if failed:
            _, log_text = await run("gh", "run", "view", str(failed[0]["id"]), "--repo", repo, "--log-failed")
            return CI("failed", f"{summary}\n\n{log_text[-4000:]}".strip(), failed[0]["html_url"])
        if any(r["status"] != "completed" for r in runs):
            return CI("pending", summary, runs[0]["html_url"])
        return CI("passed", summary, runs[0]["html_url"])


class LocalWorkspace:
    """No GitHub: plain directories. For tests and the fake harness."""

    def __init__(self, root: Path):
        self.root = root
        self.repos: set[str] = set()
        self.issues: list[tuple[str, str]] = []
        self.actions: list[str] = []
        self.bodies: list[str] = []  # of the PRs opened
        self.has_work = True
        self.ci_results: list[CI] = []
        self.branch: str | None = None
        self.commits = 0
        self.main: dict[str, str] = {}
        self.pushed: dict[tuple[str, str], str] = {}
        self.prs: dict[tuple[str, str], PullRequest] = {}

    def path(self, slug: str) -> Path:
        return self.root / slug

    async def repo_exists(self, slug: str) -> bool:
        return slug in self.repos

    async def ensure_repo(self, slug: str, description: str) -> Path:
        self.repos.add(slug)
        path = self.path(slug)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def move_repo(self, old: str, new: str, description: str) -> Path:
        self.repos.add(new)
        target = self.path(new)
        if self.path(old).exists():
            self.path(old).rename(target)
        else:
            target.mkdir(parents=True, exist_ok=True)
        return target

    async def remove(self, slug: str) -> None:
        await remove_tree(self.path(slug))

    async def sync_main(self, slug: str) -> None:
        pass

    async def open_issue(self, slug: str, title: str, body: str) -> int | None:
        self.issues.append((slug, title))
        return len(self.issues)

    # A pretend GitHub: every step's work is something to push, and CI passes unless
    # `ci_results` says otherwise. `actions` records what golem did, in order.

    def _sha(self) -> str:
        self.commits += 1
        return f"{self.commits:040x}"

    async def ensure_main(self, slug: str, readme: str) -> None:
        self.main.setdefault(slug, self._sha())

    async def start_branch(self, slug: str, branch: str) -> None:
        self.branch = branch

    async def current_branch(self, slug: str) -> str:
        return self.branch or "main"

    async def clean(self, slug: str) -> bool:
        return True

    async def discard(self, slug: str) -> None:
        self.actions.append("discard")

    async def commit_all(self, slug: str, message: str) -> bool:
        return False

    async def push_branch(self, slug: str, branch: str) -> str | None:
        if not self.has_work:
            return None
        self.actions.append(f"push {branch}")
        self.pushed[(slug, branch)] = self._sha()
        return self.pushed[(slug, branch)]

    async def push_main(self, slug: str, branch: str) -> str | None:
        if not self.has_work:
            return None
        self.actions.append("push main")
        self.main[slug] = self._sha()
        return self.main[slug]

    async def pull_request(self, slug: str, branch: str) -> PullRequest | None:
        pr = self.prs.get((slug, branch))
        return pr and PullRequest(pr.number, pr.url, self.pushed[(slug, branch)])

    async def open_pr(self, slug: str, branch: str, title: str, body: str) -> PullRequest | None:
        if (slug, branch) not in self.prs and (slug, branch) in self.pushed:
            number = len(self.bodies) + 1
            self.actions.append(f"open PR #{number}: {title}")
            self.bodies.append(body)
            self.prs[(slug, branch)] = PullRequest(number, f"https://github.com/unlaunched/{slug}/pull/{number}", "")
        return await self.pull_request(slug, branch)

    async def merge(self, slug: str, pr: PullRequest, branch: str) -> str:
        self.actions.append(f"merge PR #{pr.number}")
        del self.prs[(slug, branch)], self.pushed[(slug, branch)]
        self.main[slug] = self._sha()
        return self.main[slug]

    async def main_sha(self, slug: str) -> str:
        return self.main.setdefault(slug, self._sha())

    async def ci(self, slug: str, sha: str) -> CI:
        result = self.ci_results.pop(0) if self.ci_results else CI("passed", "build: success")
        self.actions.append(f"CI on {sha[-2:]}: {result.state}")
        return result


async def configure_git(email: str) -> None:
    """Commit identity, and git authenticating through gh's GH_TOKEN."""
    for args in (
        ("git", "config", "--global", "user.name", "golem"),
        ("git", "config", "--global", "user.email", email),
        ("git", "config", "--global", "init.defaultBranch", "main"),
        ("gh", "auth", "setup-git"),
    ):
        code, out = await run(*args)
        if code != 0:
            log.warning("%s: %s", " ".join(args[:3]), out[-200:])
