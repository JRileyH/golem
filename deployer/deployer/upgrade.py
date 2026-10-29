"""Upgrading golem itself: its own checkout on the box, brought up to date with `main` on
GitHub and rebuilt, from the web app instead of a shell.

The checkout is mounted into the deployer at the same path it has on the host
(GOLEM_HOST_DIR), so the paths compose hands the Docker daemon are the host's. `version`
fetches `main` and says how far behind the checkout is. `upgrade` (admin secret only: golem
holds it, workers never do) does what docs/runbook.md says to do by hand: a fast-forward
of `main`, `bin/bootstrap.sh`, then `docker compose up -d --build`. That last part runs
in a helper container (`finish`, from this same image), because rebuilding the stack
replaces the deployer doing it. The helper waits for golem and the deployer to come up
healthy, and puts the previous commit back if they don't. What happened is kept in
`upgrade.json` for golem to read and tell Riggy.

Only `main` as it is on GitHub is ever deployed, and only by fast-forward: a checkout on
another branch, with local changes, or with commits GitHub doesn't have is refused.
Git runs as the checkout's owner, so the files it writes stay theirs.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .validate import Rejected

BRANCH = "main"
TRACKING = f"refs/remotes/origin/{BRANCH}"
HEALTH_TIMEOUT = 300  # seconds for golem and the deployer to come up healthy after a rebuild
HEALTH_POLL = 5
SERVICES = ("golem", "deployer")
HELPER = "golem-upgrade"  # the helper container's name: one upgrade at a time
SHOWN = 30  # commits listed as what's new
GITHUB_SSH = re.compile(r"^(?:ssh://)?git@github\.com[:/](.+?)(?:\.git)?/?$")

# Runs a command and returns (exit code, output). Docker commands go through one, so
# tests can stand in for Docker.
Run = Callable[..., tuple[int, str]]


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None, owner: tuple[int, int] | None = None,
        timeout: int = 1800) -> tuple[int, str]:  # fmt: skip
    """A command's exit code and output, run as `owner` (uid, gid) if given and we're root."""
    as_owner = {}
    if owner and os.geteuid() == 0 and owner[0] != 0:
        sock = Path("/var/run/docker.sock")
        groups = [sock.stat().st_gid] if sock.exists() else []  # bootstrap.sh checks the compose file
        as_owner = {"user": owner[0], "group": owner[1], "extra_groups": groups}
    try:
        p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, **as_owner)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def https_url(remote: str) -> str:
    """A GitHub remote as HTTPS, which a token works with; anything else as it is."""
    match = GITHUB_SSH.match(remote.strip())
    return f"https://github.com/{match.group(1)}.git" if match else remote.strip()


@dataclass
class Upgrader:
    checkout: Path | None  # the host path, mounted at the same path; None: upgrades are off
    token: str  # read access to the golem repo; "" for a public one
    data_dir: Path  # where upgrade.json is kept
    mode: str  # bootstrap.sh's: server or local
    docker: Run = run
    running: str = ""  # the commit the stack was built from: HEAD when the deployer started

    def __post_init__(self) -> None:
        if self.enabled and not self.running:
            self.running = self.head()

    # --- reading ----------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.checkout is not None and (self.checkout / ".git").exists()

    @property
    def status_path(self) -> Path:
        return self.data_dir / "upgrade.json"

    def git(self, *args: str) -> tuple[int, str]:
        """Git in the checkout, as its owner, with the token for GitHub if there is one."""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/tmp",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",  # owned by someone else, from inside a container
            "GIT_CONFIG_VALUE_0": "*",
        }
        if self.token:
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            env |= {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_1": f"Authorization: Basic {basic}"}  # fmt: skip
        return run("git", *args, cwd=self.checkout, env=env, owner=self.owner())

    def owner(self) -> tuple[int, int]:
        st = self.checkout.stat()
        return st.st_uid, st.st_gid

    def head(self, ref: str = "HEAD") -> str:
        code, out = self.git("rev-parse", "--verify", "--quiet", ref)
        return out if code == 0 else ""

    def fetch(self) -> str:
        """Bring the checkout's idea of GitHub's `main` up to date. Returns what went wrong, or ""."""
        code, remote = self.git("remote", "get-url", "origin")
        if code != 0:
            return "the checkout has no origin remote"
        code, out = self.git("fetch", "--quiet", "--no-tags", https_url(remote), f"+refs/heads/{BRANCH}:{TRACKING}")
        return "" if code == 0 else f"couldn't fetch {BRANCH}: {out[-300:]}"

    def version(self, fetch: bool = False) -> dict:
        """What's running, what GitHub has, and the last upgrade's outcome."""
        if not self.enabled:
            return {"enabled": False, "reason": "GOLEM_HOST_DIR isn't set to golem's checkout (bin/bootstrap.sh sets it)"}
        problem = self.fetch() if fetch else ""
        head, latest = self.head(), self.head(TRACKING)
        _, branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        _, changed = self.git("status", "--porcelain", "--untracked-files=no")
        commits, behind, ahead = [], "0", "0"
        if head and latest:
            _, log = self.git("log", "--format=%H%x09%s", f"-{SHOWN}", f"{head}..{latest}")
            commits = [dict(zip(("sha", "subject"), line.split("\t", 1))) for line in log.splitlines() if "\t" in line]
            _, behind = self.git("rev-list", "--count", f"{head}..{latest}")
            _, ahead = self.git("rev-list", "--count", f"{latest}..{head}")
        return {
            "enabled": True,
            "running": self.running,
            "checkout": head,
            "latest": latest,
            "behind": int(behind) if behind.isdigit() else 0,
            "ahead": int(ahead) if ahead.isdigit() else 0,
            "commits": commits,
            "branch": branch,
            "clean": not changed,
            "fetch_error": problem,
            "upgrade": self.status(),
        }

    def status(self) -> dict | None:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return None

    def write_status(self, **fields: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**(self.status() or {}), **fields}))
        tmp.replace(self.status_path)

    # --- upgrading --------------------------------------------------------------

    def upgrade(self) -> dict:
        """Fast-forward to GitHub's `main`, run bootstrap.sh, and leave the rebuild to the helper."""
        if not self.enabled:
            raise Rejected(["upgrades are off: GOLEM_HOST_DIR isn't set to golem's checkout"])
        if (self.status() or {}).get("state") == "running" or self.helper_running():
            raise Rejected(["an upgrade is already under way"])
        problem = self.fetch()
        v = self.version()
        reasons = [problem] if problem else []
        if v["branch"] != BRANCH:
            reasons.append(f"the checkout is on {v['branch']}, not {BRANCH}")
        if not v["clean"]:
            reasons.append("the checkout has local changes")
        if v["ahead"]:
            reasons.append(f"the checkout has {v['ahead']} commit(s) GitHub's {BRANCH} doesn't")
        if not reasons and not v["behind"]:
            reasons.append(f"already up to date with {BRANCH}")
        if reasons:
            raise Rejected(reasons)
        old, new = v["checkout"], v["latest"]
        code, out = self.git("merge", "--ff-only", "--quiet", TRACKING)
        if code != 0:
            raise Rejected([f"couldn't fast-forward to {new[:7]}: {out[-300:]}"])
        code, out = run("bin/bootstrap.sh", self.mode, cwd=self.checkout, env=self.bootstrap_env(), owner=self.owner())
        if code != 0 or "still to fill in" in out:
            self.git("reset", "--keep", old)
            raise Rejected([f"bin/bootstrap.sh needs a hand on the box, so nothing changed: {out[-500:]}"])
        self.write_status(state="running", started=_now(), ended="", detail="", **{"from": old, "to": new})
        code, out = self.start_helper(old, new)
        if code != 0:
            self.git("reset", "--keep", old)
            self.write_status(state="failed", ended=_now(), detail=f"couldn't start the upgrade: {out[-300:]}")
            raise Rejected([f"couldn't start the upgrade: {out[-300:]}"])
        return {"from": old, "to": new}

    def bootstrap_env(self) -> dict[str, str]:
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"}

    def helper_running(self) -> bool:
        code, out = self.docker("docker", "ps", "--quiet", "--filter", f"name=^{HELPER}$")
        return code == 0 and bool(out.strip())

    def start_helper(self, old: str, new: str) -> tuple[int, str]:
        """The rest, from a container outside the stack, which the rebuild leaves alone. It gets the
        deployer's own mounts (the Docker socket, the checkout, upgrade.json's volume) and image."""
        me = socket.gethostname()  # a container's hostname is its id
        code, image = self.docker("docker", "inspect", "--format", "{{.Image}}", me)
        if code != 0:
            return code, image
        return self.docker(
            "docker", "run", "--detach", "--rm", "--name", HELPER, "--volumes-from", me, "--env", f"DEPLOYER_DATA_DIR={self.data_dir}",
            image.strip(), "python", "-m", "deployer.upgrade", str(self.checkout), old, new,
        )  # fmt: skip

    # --- in the helper ----------------------------------------------------------

    def compose(self, *args: str, timeout: int = 3600) -> tuple[int, str]:
        return self.docker("docker", "compose", "--project-directory", str(self.checkout), *args, timeout=timeout)

    def finish(self, old: str, new: str, sleep: Callable[[float], None] = time.sleep) -> None:
        """Rebuild and restart the stack on `new`; back to `old` if it won't build or come up healthy."""
        code, out = self.compose("up", "--detach", "--build")
        if code != 0:  # images are built before anything is replaced: the old stack is still up
            self.git("reset", "--keep", old)
            self.write_status(state="failed", ended=_now(), detail=f"the build failed, so nothing changed: {out[-800:]}")
            return
        sick = self.wait_healthy(sleep)
        if not sick:
            self.write_status(state="done", ended=_now(), detail="")
            return
        self.git("reset", "--keep", old)
        code, out = self.compose("up", "--detach", "--build")
        back = f"and {old[:7]} is running again" if code == 0 else f"and putting {old[:7]} back failed too: {out[-400:]}"
        self.write_status(state="rolled back", ended=_now(), detail=f"{sick} on {new[:7]}, {back}")

    def wait_healthy(self, sleep: Callable[[float], None]) -> str:
        """"" once golem and the deployer are healthy; otherwise what wasn't, after HEALTH_TIMEOUT."""
        deadline = time.monotonic() + HEALTH_TIMEOUT
        while True:
            health = self.health()
            sick = [s for s in SERVICES if health.get(s) != "healthy"]
            if not sick:
                return ""
            if time.monotonic() >= deadline:
                return ", ".join(f"{s} was {health.get(s) or 'not running'}" for s in sick)
            sleep(HEALTH_POLL)

    def health(self) -> dict[str, str]:
        """{service: its health}, from `docker compose ps` (one JSON object a line, or a list)."""
        code, out = self.compose("ps", "--format", "json", *SERVICES, timeout=60)
        if code != 0:
            return {}
        try:
            rows = json.loads(out) if out.startswith("[") else [json.loads(line) for line in out.splitlines() if line.strip()]
        except ValueError:
            return {}
        return {r.get("Service", ""): r.get("Health") or r.get("State", "") for r in rows if isinstance(r, dict)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def main(argv: list[str]) -> None:
    """The helper: `python -m deployer.upgrade <checkout> <old sha> <new sha>`."""
    checkout, old, new = argv
    data_dir = Path(os.environ.get("DEPLOYER_DATA_DIR", "/data"))
    Upgrader(Path(checkout), "", data_dir, mode="", running=old).finish(old, new)


if __name__ == "__main__":
    main(sys.argv[1:])
