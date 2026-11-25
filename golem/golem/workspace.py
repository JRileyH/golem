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
# An issue or PR as golem reads it back (history.py), from GitHub's issues API.
ITEM = (
    "{number, title, body, state, state_reason, created_at, closed_at, html_url,"
    " pr: (.pull_request != null), merged_at: .pull_request.merged_at}"
)
JOB_BRANCHES = "golem/job-"  # each job's branch is golem/job-<its number> (scheduler.branch)
# Each job works in a git worktree of its own, <workspace>/.jobs/<job id> (tree_path): jobs on one
# project never touch each other's files. Never a slug, so never a project's checkout.
TREES = ".jobs"
LEFTOVER = (
    "Closed by golem, which has adopted this repo again: this PR was left open by an earlier job of "
    "golem's, and new jobs reuse branch names like its. Its commits stay here."
)


SAID_CHARS = 600  # of a failed command's output in its error's message: the end, where the reason usually is
DETAIL_CHARS = 20_000  # of it kept whole (WorkspaceError.detail), for the step's log


class WorkspaceError(Exception):
    """Why a checkout or GitHub call failed: a short message, and for a command that failed, all
    it printed (`detail`), kept with the job's logs."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def _failed(what: str, code: int | None, out: str) -> WorkspaceError:
    """A command that exited `code` while doing `what`."""
    return WorkspaceError(f"{what} (exit {code}): {out[-SAID_CHARS:]}", out[-DETAIL_CHARS:])


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


@dataclass(frozen=True)
class About:
    """A repo in the org, as GitHub has it: what golem looks at before adopting one (adopt.py)."""

    name: str  # its name exactly, capitals and all
    description: str
    archived: bool
    has_main: bool
    readme: str  # "" for none


class Workspace(Protocol):
    """A project's checkout (`path`), and each job's worktree of it (`tree_path`). Calls that work
    in a checkout take `job`: that job's tree, or with None the project's own checkout (jobs from
    before trees, and golem's own look at main)."""

    def path(self, slug: str) -> Path: ...
    def tree_path(self, job: int) -> Path: ...
    async def ensure_tree(self, slug: str, job: int, at: str | None = None) -> Path: ...
    async def drop_tree(self, slug: str, job: int) -> None: ...
    async def prune_trees(self, keep: set[int]) -> None: ...
    async def ensure_repo(self, slug: str, description: str) -> Path: ...
    async def ensure_main(self, slug: str, readme: str) -> None: ...
    async def sync_main(self, slug: str) -> None: ...
    async def open_issue(self, slug: str, title: str, body: str) -> int | None: ...
    async def comment_issue(self, slug: str, number: int, body: str, close: bool = False) -> None: ...
    async def close_issue(self, slug: str, number: int, body: str) -> None: ...
    async def repo_exists(self, slug: str) -> bool: ...
    async def about(self, slug: str) -> About | None: ...
    async def issues_and_prs(self, slug: str) -> list[dict]: ...
    async def close_leftover_prs(self, slug: str) -> list[int]: ...
    async def move_repo(self, old: str, new: str, description: str) -> Path: ...
    async def remove(self, slug: str) -> None: ...
    async def start_branch(self, slug: str, branch: str, *, job: int | None = None) -> None: ...
    async def current_branch(self, slug: str, *, job: int | None = None) -> str: ...
    async def clean(self, slug: str, *, job: int | None = None) -> bool: ...
    async def discard(self, slug: str, *, job: int | None = None) -> None: ...
    async def commit_all(self, slug: str, message: str, *, job: int | None = None) -> bool: ...
    async def push_branch(self, slug: str, branch: str, *, job: int | None = None) -> str | None: ...
    async def push_main(self, slug: str, branch: str, *, job: int | None = None) -> str | None: ...
    async def sync_branch(self, slug: str, *, job: int | None = None) -> tuple[int, list[str]]: ...
    async def merge_branch(self, slug: str, branch: str, *, job: int | None = None) -> tuple[int, list[str]]: ...
    async def back_to_main(self, slug: str, branch: str, *, job: int | None = None) -> None: ...
    async def pull_request(self, slug: str, branch: str) -> PullRequest | None: ...
    async def open_pr(self, slug: str, branch: str, title: str, body: str) -> PullRequest | None: ...
    async def merge(self, slug: str, pr: PullRequest, branch: str, *, job: int | None = None) -> str: ...
    async def merged(self, slug: str, number: int) -> str | None: ...
    async def after_merge(self, slug: str, branch: str, *, job: int | None = None) -> None: ...
    async def main_sha(self, slug: str) -> str: ...
    async def head(self, slug: str, *, job: int | None = None) -> str: ...
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
        self.locks: dict[str, asyncio.Lock] = {}

    def lock(self, slug: str) -> asyncio.Lock:
        """One per project: its jobs' trees share one repo (refs, the list of trees), so golem's
        own git calls on it go one at a time. Workers' git use isn't held to it: a clash there is
        a failed step, tried again."""
        return self.locks.setdefault(slug, asyncio.Lock())

    def path(self, slug: str) -> Path:
        return self.root / slug

    def tree_path(self, job: int) -> Path:
        return self.root / TREES / str(job)

    def where(self, slug: str, job: int | None) -> Path:
        """The job's tree, or with None the project's own checkout."""
        return self.path(slug) if job is None else self.tree_path(job)

    # --- each job's own worktree ---------------------------------------------------
    #
    # The project's checkout holds git's data and stays on main; a tree is on the job's branch,
    # or detached when the job has none (before its first agent step, after a merge). Never on
    # main: git won't check a branch out in two places.

    async def ensure_tree(self, slug: str, job: int, at: str | None = None) -> Path:
        """The job's tree: as it is, or made now on the job's branch if it has one here (the
        tree was lost), else detached at `at` if there's such a thing (another job's branch), or
        at origin/main."""
        path = self.tree_path(job)
        if (path / ".git").exists():
            return path
        await self.git(slug, "fetch", "--quiet", "--prune", "origin")
        await self.git(slug, "worktree", "prune")
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = f"{JOB_BRANCHES}{job}"
        code, _ = await run("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=self.path(slug))
        if code == 0:
            await self.git(slug, "worktree", "add", "--quiet", str(path), branch)
            return path
        if at:
            code, _ = await run("git", "rev-parse", "--verify", "--quiet", at, cwd=self.path(slug))
        await self.git(slug, "worktree", "add", "--quiet", "--detach", str(path), at if at and code == 0 else "origin/main")
        return path

    async def drop_tree(self, slug: str, job: int) -> None:
        """The job's tree goes (its branch stays, with whatever it pushed)."""
        path, repo = self.tree_path(job), (self.path(slug) / ".git").is_dir()
        async with self.lock(slug):
            if path.exists():
                code = (await run("git", "worktree", "remove", "--force", str(path), cwd=self.path(slug)))[0] if repo else 1
                if code != 0:  # no checkout any more (the project's going), or git couldn't
                    await remove_tree(path)
            if repo:
                await run("git", "worktree", "prune", cwd=self.path(slug))

    async def prune_trees(self, keep: set[int]) -> None:
        """Remove the trees of every job not in `keep`, then what git remembers of them."""
        trees = self.root / TREES
        gone = [p for p in (trees.iterdir() if trees.is_dir() else []) if p.name.isdigit() and int(p.name) not in keep]
        for path in gone:
            await remove_tree(path)
        if gone:
            for repo in self.root.iterdir():
                if (repo / ".git").is_dir():
                    async with self.lock(repo.name):
                        await run("git", "worktree", "prune", cwd=repo)

    async def repo_exists(self, slug: str) -> bool:
        code, _ = await run("gh", "repo", "view", f"{self.org}/{slug}", "--json", "name")
        return code == 0

    async def about(self, slug: str) -> About | None:
        """The repo's name, description, README and whether it's archived or has a main branch;
        None if golem's token can't see a repo by that name."""
        repo = f"{self.org}/{slug}"
        code, out = await run("gh", "repo", "view", repo, "--json", "name,description,isArchived")
        if code != 0:
            return None
        info = json.loads(out)
        main, _ = await run("gh", "api", "--silent", f"repos/{repo}/branches/main")
        code, readme = await run("gh", "api", f"repos/{repo}/readme", "-H", "Accept: application/vnd.github.raw")
        return About(info["name"], info.get("description") or "", bool(info.get("isArchived")), main == 0, readme if code == 0 else "")

    async def issues_and_prs(self, slug: str) -> list[dict]:
        """Every issue and PR on the repo, open or closed, as ITEM: for its past golem jobs."""
        url = f"repos/{self.org}/{slug}/issues?state=all&per_page=100"
        code, out = await run("gh", "api", "--paginate", url, "--jq", f".[] | {ITEM}")
        if code != 0:
            raise _failed(f"reading {slug}'s issues", code, out)
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    async def close_leftover_prs(self, slug: str) -> list[int]:
        """Close the open PRs on golem's job branches, left by jobs from before golem adopted the
        repo (again): a new job with the same number would take one for its own. Returns their numbers."""
        repo = f"{self.org}/{slug}"
        code, out = await run("gh", "pr", "list", "--repo", repo, "--state", "open", "--json", "number,headRefName", "--limit", "200")
        if code != 0:
            raise _failed(f"listing PRs on {slug}", code, out)
        closed = []
        for pr in json.loads(out or "[]"):
            if not pr["headRefName"].startswith(JOB_BRANCHES):
                continue
            code, said = await run("gh", "pr", "close", str(pr["number"]), "--repo", repo, "--comment", LEFTOVER)
            if code != 0:
                raise _failed(f"closing PR #{pr['number']} on {slug}", code, said)
            closed.append(pr["number"])
        return closed

    async def ensure_repo(self, slug: str, description: str) -> Path:
        repo = f"{self.org}/{slug}"
        if not await self.repo_exists(slug):
            code, out = await run("gh", "repo", "create", repo, "--private", "--description", description[:350])
            if code != 0:
                raise _failed(f"creating {repo}", code, out)
        path = self.path(slug)
        if not (path / ".git").is_dir():
            code, out = await run("gh", "repo", "clone", repo, str(path))
            if code != 0:
                raise _failed(f"cloning {repo}", code, out)
        return path

    async def move_repo(self, old: str, new: str, description: str) -> Path:
        """Copy every branch and tag of `old` into a new repo `new` and point the checkout at it.
        The old repo stays (deleting needs admin rights the token doesn't have)."""
        path = await self.ensure_repo(old, description)
        code, out = await run("git", "fetch", "--quiet", "--prune", "--tags", "origin", cwd=path)
        if code != 0:
            raise _failed(f"fetching {old}", code, out)
        await run("git", "remote", "set-head", "origin", "--delete", cwd=path)
        repo = f"{self.org}/{new}"
        code, out = await run("gh", "repo", "create", repo, "--private", "--description", description[:350])
        if code != 0:
            raise _failed(f"creating {repo}", code, out)
        url = f"https://github.com/{repo}.git"
        for refspec in ("refs/remotes/origin/*:refs/heads/*", "refs/tags/*:refs/tags/*"):
            code, out = await run("git", "push", "--quiet", url, refspec, cwd=path)
            if code != 0 and "src refspec" not in out:  # no tags is fine
                raise _failed(f"pushing to {repo}", code, out)
        await run("git", "remote", "set-url", "origin", url, cwd=path)
        await run("gh", "repo", "edit", repo, "--default-branch", "main")
        target = self.path(new)
        path.rename(target)
        await run("git", "worktree", "repair", cwd=target)  # its jobs' trees find it where it is now
        return target

    async def remove(self, slug: str) -> None:
        """Delete the checkout. The repo stays: the token can't delete repos."""
        await remove_tree(self.path(slug))

    async def sync_main(self, slug: str) -> None:
        """Start a job from the latest main. Best effort: an empty repo or local changes leave it alone."""
        path = self.path(slug)
        async with self.lock(slug):
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

    async def comment_issue(self, slug: str, number: int, body: str, close: bool = False) -> None:
        """Say something on an issue, closing it as not planned if `close`. Only a record: a failure is logged."""
        repo = ("--repo", f"{self.org}/{slug}")
        if close:
            args = ("gh", "issue", "close", str(number), *repo, "--reason", "not planned", "--comment", body)
        else:
            args = ("gh", "issue", "comment", str(number), *repo, "--body", body)
        code, out = await run(*args)
        if code != 0:
            log.warning("issue %s#%s: %s", slug, number, out[-300:])

    async def close_issue(self, slug: str, number: int, body: str) -> None:
        """Close an issue as done, saying how. Only a record: a failure is logged."""
        code, out = await run(
            "gh", "issue", "close", str(number), "--repo", f"{self.org}/{slug}", "--reason", "completed", "--comment", body
        )  # fmt: skip
        if code != 0:
            log.warning("closing %s#%s: %s", slug, number, out[-300:])

    # --- the job's branch, from first commit to merge ---------------------------

    async def git(self, slug: str, *args: str, job: int | None = None) -> str:
        async with self.lock(slug):
            code, out = await run("git", *args, cwd=self.where(slug, job))
        if code != 0:
            raise _failed(f"git {args[0]} in {slug}{f' (job {job})' if job else ''}", code, out)
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

    async def start_branch(self, slug: str, branch: str, *, job: int | None = None) -> None:
        """Be on the job's branch: as it is, or new from wherever the checkout is (main, normally)."""
        if await self.git(slug, "branch", "--show-current", job=job) == branch:
            return
        code, _ = await run("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=self.where(slug, job))
        await self.git(slug, "checkout", "--quiet", *(() if code == 0 else ("-b",)), branch, job=job)

    async def current_branch(self, slug: str, *, job: int | None = None) -> str:
        """The branch checked out; for a detached tree, the branch it's at: a job's (a question
        about its work), or main. "HEAD" if neither."""
        if name := await self.git(slug, "branch", "--show-current", job=job):
            return name
        at = await self.git(slug, "branch", "--all", "--points-at", "HEAD", "--format=%(refname:short)", job=job)
        names = [n.strip() for n in at.splitlines() if n.strip() and not n.startswith("(")]
        mine = [n for n in names if n.startswith(JOB_BRANCHES)]
        return mine[0] if mine else "main" if "origin/main" in names else "HEAD"

    async def clean(self, slug: str, *, job: int | None = None) -> bool:
        """Whether the checkout has nothing uncommitted (ignored files and installed dependencies aside)."""
        code, out = await run("git", "status", "--porcelain", "--", ".", *NEVER_COMMIT, cwd=self.where(slug, job))
        return code == 0 and not out

    async def discard(self, slug: str, *, job: int | None = None) -> None:
        """Put a clean checkout back as it was: after a question, nothing it touched stays."""
        await self.git(slug, "reset", "--quiet", "--hard", "HEAD", job=job)
        await self.git(slug, "clean", "--quiet", "-fd", "--", ".", *NEVER_COMMIT, job=job)  # installed dependencies stay

    async def commit_all(self, slug: str, message: str, *, job: int | None = None) -> bool:
        """Commit whatever the worker left uncommitted. Returns whether there was anything."""
        await self.git(slug, "add", "-A", "--", ".", *NEVER_COMMIT, job=job)
        code, _ = await run("git", "diff", "--cached", "--quiet", cwd=self.where(slug, job))
        if code == 0:
            return False
        await self.git(slug, "commit", "--quiet", "-m", message, job=job)
        return True

    async def ahead(self, slug: str, ref: str = "HEAD", *, job: int | None = None) -> int:
        """How many commits `ref` has that main doesn't."""
        await self.git(slug, "fetch", "--quiet", "--prune", "origin", job=job)
        return int(await self.git(slug, "rev-list", "--count", f"origin/main..{ref}", job=job))

    async def push_branch(self, slug: str, branch: str, *, job: int | None = None) -> str | None:
        """Push the checkout's work to the job's branch on GitHub. None if there's nothing new for main."""
        if not await self.ahead(slug, job=job):
            return None
        await self.git(slug, "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}", job=job)
        return await self.git(slug, "rev-parse", "HEAD", job=job)

    async def push_main(self, slug: str, branch: str, *, job: int | None = None) -> str | None:
        """Put the work straight on main (reckless mode). None if there's nothing new."""
        if not await self.ahead(slug, job=job):
            return None
        await self.git(slug, "push", "--quiet", "origin", "HEAD:main", job=job)
        sha = await self.git(slug, "rev-parse", "HEAD", job=job)
        await self.back_to_main(slug, branch, job=job)
        return sha

    async def back_to_main(self, slug: str, branch: str, *, job: int | None = None) -> None:
        """After a merge: the checkout on the latest main, the job's branch gone, ready for the next.
        A job's tree lands detached there: main itself stays the project's checkout's."""
        await self.git(slug, "fetch", "--quiet", "--prune", "origin", job=job)
        if job is None:
            await self.git(slug, "checkout", "--quiet", "-B", "main", "origin/main")
        else:
            await self.git(slug, "checkout", "--quiet", "--detach", "origin/main", job=job)
        async with self.lock(slug):
            await run("git", "branch", "-D", branch, cwd=self.where(slug, job))

    async def sync_branch(self, slug: str, *, job: int | None = None) -> tuple[int, list[str]]:
        """Merge the latest main into the checkout's branch: (how many commits main had that it
        didn't, the files they conflict in). A conflict is merged back out again, so the checkout
        is always left clean, and a resolve step can do the merge itself (turns.py)."""
        await self.git(slug, "fetch", "--quiet", "--prune", "origin", job=job)
        return await self._merge(slug, "origin/main", job)

    async def merge_branch(self, slug: str, branch: str, *, job: int | None = None) -> tuple[int, list[str]]:
        """Merge another job's branch into the checkout's, to ship them together (shipments.py):
        (how many commits it had that this didn't, the files they conflict in), the checkout left
        clean as sync_branch leaves it. (0, []) if there's no such branch."""
        code, _ = await run("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=self.path(slug))
        if code != 0:
            return 0, []
        return await self._merge(slug, branch, job)

    async def _merge(self, slug: str, ref: str, job: int | None) -> tuple[int, list[str]]:
        behind = int(await self.git(slug, "rev-list", "--count", f"HEAD..{ref}", job=job))
        if not behind:
            return 0, []
        async with self.lock(slug):
            code, out = await run("git", "merge", "--no-edit", "--quiet", ref, cwd=self.where(slug, job))
        if code == 0:
            return behind, []
        conflicts = (await self.git(slug, "diff", "--name-only", "--diff-filter=U", job=job)).splitlines()
        async with self.lock(slug):
            await run("git", "merge", "--abort", cwd=self.where(slug, job))
        if not conflicts:
            raise _failed(f"merging {ref} into {slug}{f' (job {job})' if job else ''}", code, out)
        return behind, conflicts

    async def pull_request(self, slug: str, branch: str) -> PullRequest | None:
        code, out = await run(
            "gh", "pr", "list", "--repo", f"{self.org}/{slug}", "--head", branch, "--state", "open",
            "--json", "number,url,headRefOid", "--limit", "1",
        )  # fmt: skip
        if code != 0:
            raise _failed(f"listing PRs on {slug}", code, out)
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
            raise _failed(f"opening a PR on {slug}", code, out)
        return await self.pull_request(slug, branch)

    async def merge(self, slug: str, pr: PullRequest, branch: str, *, job: int | None = None) -> str:
        """Squash-merge exactly the commit CI checked. Returns the merge commit."""
        repo = f"{self.org}/{slug}"
        code, out = await run(
            "gh", "pr", "merge", str(pr.number), "--repo", repo, "--squash", "--match-head-commit", pr.sha
        )  # fmt: skip
        if code != 0:
            sha = await self.merged(slug, pr.number)  # GitHub can merge and still answer with an error
            if not sha:
                raise _failed(f"merging PR #{pr.number} on {slug}", code, out)
        else:
            code, sha = await run("gh", "pr", "view", str(pr.number), "--repo", repo, "--json", "mergeCommit", "-q", ".mergeCommit.oid")
            if code != 0 or not sha:
                raise _failed(f"reading PR #{pr.number}'s merge commit on {slug}", code, sha)
        await self.after_merge(slug, branch, job=job)
        return sha

    async def merged(self, slug: str, number: int) -> str | None:
        """PR `number`'s merge commit, if it's merged; None if it isn't, or GitHub can't say."""
        code, out = await run("gh", "pr", "view", str(number), "--repo", f"{self.org}/{slug}", "--json", "state,mergeCommit")
        try:
            info = json.loads(out) if code == 0 else {}
        except ValueError:
            return None
        if info.get("state") != "MERGED":
            return None
        return (info.get("mergeCommit") or {}).get("oid") or None

    async def after_merge(self, slug: str, branch: str, *, job: int | None = None) -> None:
        """The merged branch deleted on GitHub (best effort), and the checkout back on main."""
        await run("git", "push", "--quiet", "origin", "--delete", branch, cwd=self.path(slug))
        await self.back_to_main(slug, branch, job=job)

    async def main_sha(self, slug: str) -> str:
        await self.git(slug, "fetch", "--quiet", "origin")
        return await self.git(slug, "rev-parse", "origin/main")

    async def head(self, slug: str, *, job: int | None = None) -> str:
        """The commit the job's tree (or the project's checkout) is at; "" if there's none yet."""
        code, out = await run("git", "rev-parse", "HEAD", cwd=self.where(slug, job))
        return out if code == 0 else ""

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
            raise _failed(f"reading CI on {slug}", code, out)
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
    """No GitHub: plain directories. For tests and the fake harness. A directory already under
    `root` counts as a repo in the org, so a fake golem can adopt a copy of a real one."""

    def __init__(self, root: Path):
        self.root = root
        self.repos: set[str] = set()
        self.existing: dict[str, About] = {}  # repos golem didn't make, as GitHub would describe them
        self.items: dict[str, list[dict] | Exception] = {}  # their issues and PRs (ITEM), or why they can't be read
        self.leftover: dict[str, list[int]] = {}  # open PRs on golem's job branches, from before
        self.issues: list[tuple[str, str]] = []
        self.comments: list[tuple[str, int, str, bool]] = []  # (slug, issue, body, closed it)
        self.closed: list[tuple[str, int, str]] = []  # (slug, issue, body): closed as done
        self.actions: list[str] = []
        self.bodies: list[str] = []  # of the PRs opened
        self.has_work = True
        self.ci_results: list[CI] = []
        self.syncs: list[tuple[int, list[str]]] = []  # what catching up with main finds, in turn; then nothing new
        self.merges: list[tuple[int, list[str]]] = []  # what merging another job's branch in finds, in turn; then a commit
        self.merge_errors: list[str | WorkspaceError] = []  # why the next merges of a PR fail, in turn; then they work
        self.merge_lands = False  # whether a merge that fails merged the PR anyway
        self.merged_prs: dict[tuple[str, int], str] = {}  # (slug, PR): its merge commit
        self.branch: str | None = None  # the last branch started, in any checkout
        self.branches: dict[int | None, str] = {}  # by job (None: the project's own checkout)
        self.trees: set[int] = set()  # jobs with a tree
        self.commits = 0
        self.main: dict[str, str] = {}
        self.pushed: dict[tuple[str, str], str] = {}
        self.prs: dict[tuple[str, str], PullRequest] = {}

    def path(self, slug: str) -> Path:
        return self.root / slug

    def tree_path(self, job: int) -> Path:
        return self.root / TREES / str(job)

    async def ensure_tree(self, slug: str, job: int, at: str | None = None) -> Path:
        """A copy of the project's directory, the first time: so a job sees the files a test put there."""
        path = self.tree_path(job)
        if not path.exists():
            if self.path(slug).is_dir():
                shutil.copytree(self.path(slug), path)
            else:
                path.mkdir(parents=True)
            if at and not at.startswith("origin/"):
                self.branches[job] = at
        self.trees.add(job)
        return path

    async def drop_tree(self, slug: str, job: int) -> None:
        await remove_tree(self.tree_path(job))
        self.trees.discard(job)

    async def prune_trees(self, keep: set[int]) -> None:
        for job in [j for j in self.trees if j not in keep]:
            await remove_tree(self.tree_path(job))
            self.trees.discard(job)

    async def repo_exists(self, slug: str) -> bool:
        return slug in self.repos or slug in self.existing or self.path(slug).is_dir()

    async def about(self, slug: str) -> About | None:
        if slug in self.existing:
            return self.existing[slug]
        if not await self.repo_exists(slug):
            return None
        readme = self.path(slug) / "README.md"
        return About(slug, "", False, True, readme.read_text(errors="replace") if readme.is_file() else "")

    async def issues_and_prs(self, slug: str) -> list[dict]:
        found = self.items.get(slug, [])
        if isinstance(found, Exception):
            raise found
        return found

    async def close_leftover_prs(self, slug: str) -> list[int]:
        closed = self.leftover.pop(slug, [])
        self.actions += [f"close leftover PR #{n}" for n in closed]
        return closed

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

    async def comment_issue(self, slug: str, number: int, body: str, close: bool = False) -> None:
        self.comments.append((slug, number, body, close))

    async def close_issue(self, slug: str, number: int, body: str) -> None:
        self.closed.append((slug, number, body))

    # A pretend GitHub: every step's work is something to push, and CI passes unless
    # `ci_results` says otherwise. `actions` records what golem did, in order.

    def _sha(self) -> str:
        self.commits += 1
        return f"{self.commits:040x}"

    async def ensure_main(self, slug: str, readme: str) -> None:
        self.main.setdefault(slug, self._sha())

    async def start_branch(self, slug: str, branch: str, *, job: int | None = None) -> None:
        self.branch = self.branches[job] = branch

    async def current_branch(self, slug: str, *, job: int | None = None) -> str:
        if job is not None:
            return self.branches.get(job, "main")  # a tree with no branch is at main
        return self.branch or "main"

    async def head(self, slug: str, *, job: int | None = None) -> str:
        return f"{self.commits:040x}"  # a commit for each commit made: what changed since is told by it

    async def clean(self, slug: str, *, job: int | None = None) -> bool:
        return True

    async def discard(self, slug: str, *, job: int | None = None) -> None:
        self.actions.append("discard")

    async def commit_all(self, slug: str, message: str, *, job: int | None = None) -> bool:
        return False

    async def push_branch(self, slug: str, branch: str, *, job: int | None = None) -> str | None:
        if not self.has_work:
            return None
        self.actions.append(f"push {branch}")
        self.pushed[(slug, branch)] = self._sha()
        return self.pushed[(slug, branch)]

    async def push_main(self, slug: str, branch: str, *, job: int | None = None) -> str | None:
        if not self.has_work:
            return None
        self.actions.append("push main")
        self.main[slug] = self._sha()
        self.branches.pop(job, None)
        return self.main[slug]

    async def sync_branch(self, slug: str, *, job: int | None = None) -> tuple[int, list[str]]:
        found = self.syncs.pop(0) if self.syncs else (0, [])
        self.actions.append(f"sync job {job}: {found[0]} new" + (f", conflicts in {', '.join(found[1])}" if found[1] else ""))
        return found

    async def merge_branch(self, slug: str, branch: str, *, job: int | None = None) -> tuple[int, list[str]]:
        found = self.merges.pop(0) if self.merges else (1, [])
        self.actions.append(f"merge {branch} into job {job}" + (f": conflicts in {', '.join(found[1])}" if found[1] else ""))
        return found

    async def back_to_main(self, slug: str, branch: str, *, job: int | None = None) -> None:
        self.actions.append(f"back to main: job {job}")
        self.branches.pop(job, None)

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

    async def merge(self, slug: str, pr: PullRequest, branch: str, *, job: int | None = None) -> str:
        if self.merge_errors and not self.merge_lands:
            why = self.merge_errors.pop(0)
            raise why if isinstance(why, WorkspaceError) else WorkspaceError(why)
        self.actions.append(f"merge PR #{pr.number}")
        del self.prs[(slug, branch)], self.pushed[(slug, branch)]
        self.main[slug] = self.merged_prs[(slug, pr.number)] = self._sha()
        if self.merge_errors:  # merged all the same, as GitHub sometimes does
            why = self.merge_errors.pop(0)
            raise why if isinstance(why, WorkspaceError) else WorkspaceError(why)
        self.branches.pop(job, None)
        return self.main[slug]

    async def merged(self, slug: str, number: int) -> str | None:
        return self.merged_prs.get((slug, number))

    async def after_merge(self, slug: str, branch: str, *, job: int | None = None) -> None:
        self.actions.append(f"delete {branch}")
        await self.back_to_main(slug, branch, job=job)

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
