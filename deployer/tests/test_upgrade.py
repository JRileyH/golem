"""Upgrading golem itself: real git repos standing in for GitHub and the box's checkout, Docker faked."""

import json
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from deployer import upgrade
from deployer.upgrade import Upgrader, https_url
from deployer.validate import Rejected
from deployer.app import create_app
from deployer.audit import Audit
from test_api import ADMIN, ADMIN_SECRET, AUTH, SECRET

BOOTSTRAP = """#!/bin/sh
if [ -f missing ]; then echo "still to fill in by hand in .env: GOLEM_WEB_PASSWORD" >&2; fi
echo "ok: bootstrapped $1"
"""


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return p.stdout.strip()


def commit(cwd: Path, name: str) -> str:
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "--quiet", "-m", f"Add {name}")
    return git(cwd, "rev-parse", "HEAD")


class FakeDocker:
    """Docker's side: the helper's container, the rebuild and services' health, as scripted."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.builds = [(0, "built")]  # `compose up` results, in order; then it works
        self.healths: list[str] = ["healthy"]  # golem's and the deployer's health, each look; the last repeats

    def __call__(self, *args: str, timeout: int = 0, **_) -> tuple[int, str]:
        self.calls.append(args)
        if args[1:3] == ("inspect", "--format"):
            return 0, "sha256:deployer-image\n"
        if args[1] == "ps":
            return 0, ""
        if args[1] == "run":
            return 0, "helper-id"
        if "up" in args:
            return self.builds.pop(0) if self.builds else (0, "built")
        if "ps" in args:
            health = self.healths.pop(0) if len(self.healths) > 1 else self.healths[0]
            return 0, "\n".join(json.dumps({"Service": s, "State": "running", "Health": health}) for s in upgrade.SERVICES)
        return 1, f"unexpected: {args}"

    def ups(self) -> int:
        return sum(1 for c in self.calls if "up" in c)


@pytest.fixture
def box(tmp_path):
    """GitHub's golem repo (bare), someone pushing to it, and the box's checkout of it."""
    origin, dev, checkout = tmp_path / "golem.git", tmp_path / "dev", tmp_path / "box" / "golem"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(origin)], check=True)
    subprocess.run(["git", "clone", "--quiet", str(origin), str(dev)], check=True)
    git(dev, "checkout", "--quiet", "-b", "main")
    (dev / "bin").mkdir()
    (dev / "bin" / "bootstrap.sh").write_text(BOOTSTRAP)
    (dev / "bin" / "bootstrap.sh").chmod(0o755)
    git(dev, "add", ".")
    git(dev, "commit", "--quiet", "-m", "golem")
    git(dev, "push", "--quiet", "origin", "main")
    subprocess.run(["git", "clone", "--quiet", str(origin), str(checkout)], check=True)
    docker = FakeDocker()
    up = Upgrader(checkout, "", tmp_path / "data", "server", docker=docker)
    return up, dev, docker


def push(dev: Path, *names: str) -> str:
    for name in names:
        sha = commit(dev, name)
    git(dev, "push", "--quiet", "origin", "main")
    return sha


def test_github_remotes_are_fetched_over_https():
    assert https_url("git@github.com:JRileyH/golem.git") == "https://github.com/JRileyH/golem.git"
    assert https_url("ssh://git@github.com/JRileyH/golem") == "https://github.com/JRileyH/golem.git"
    assert https_url("https://github.com/JRileyH/golem.git") == "https://github.com/JRileyH/golem.git"
    assert https_url("/srv/git/golem.git") == "/srv/git/golem.git"


def test_off_without_a_checkout(tmp_path):
    up = Upgrader(None, "", tmp_path, "server", docker=FakeDocker())
    assert up.version(fetch=True)["enabled"] is False
    with pytest.raises(Rejected, match="upgrades are off"):
        up.upgrade()


def test_version_says_what_is_new_on_github(box):
    up, dev, _ = box
    running = up.running
    v = up.version()
    assert (v["enabled"], v["behind"], v["branch"], v["clean"], v["upgrade"]) == (True, 0, "main", True, None)
    latest = push(dev, "quiz.txt", "dark.txt")
    assert up.version()["behind"] == 0  # not fetched yet
    v = up.version(fetch=True)
    assert (v["running"], v["checkout"], v["latest"], v["behind"], v["ahead"], v["fetch_error"]) == (running, running, latest, 2, 0, "")
    assert [c["subject"] for c in v["commits"]] == ["Add dark.txt", "Add quiz.txt"]


def test_upgrading_fast_forwards_bootstraps_and_hands_the_rebuild_to_a_helper(box):
    up, dev, docker = box
    old, new = up.running, push(dev, "quiz.txt")
    assert up.upgrade() == {"from": old, "to": new}
    assert up.head() == new
    assert up.status()["state"] == "running" and (up.status()["from"], up.status()["to"]) == (old, new)
    run = next(c for c in docker.calls if c[1] == "run")
    assert run[run.index("--volumes-from") + 1] and "sha256:deployer-image" in run
    assert run[-3:] == (str(up.checkout), old, new) and "--rm" in run and upgrade.HELPER in run
    with pytest.raises(Rejected, match="already under way"):
        up.upgrade()


@pytest.mark.parametrize(
    "setup, reason",
    [
        (lambda up, dev: None, "already up to date"),
        (lambda up, dev: (push(dev, "a.txt"), (up.checkout / "bin" / "bootstrap.sh").write_text("edited")), "local changes"),
        (lambda up, dev: (push(dev, "a.txt"), git(up.checkout, "checkout", "--quiet", "-b", "mine")), "on mine, not main"),
        (lambda up, dev: (push(dev, "a.txt"), commit(up.checkout, "local.txt")), r"1 commit\(s\) GitHub's main doesn't"),
    ],
)
def test_only_a_clean_main_behind_github_is_upgraded(box, setup, reason):
    up, dev, docker = box
    setup(up, dev)
    before = up.head()
    with pytest.raises(Rejected, match=reason):
        up.upgrade()
    assert up.head() == before and not any(c[1] == "run" for c in docker.calls)


def test_a_bootstrap_that_needs_a_hand_changes_nothing(box):
    up, dev, docker = box
    old = up.running
    push(dev, "quiz.txt")
    (up.checkout / "missing").write_text("")  # untracked: the checkout is still clean
    with pytest.raises(Rejected, match="(?s)bootstrap.sh needs a hand.*GOLEM_WEB_PASSWORD"):
        up.upgrade()
    assert up.head() == old and up.status() is None and not any(c[1] == "run" for c in docker.calls)


def started(box):
    up, dev, docker = box
    old, new = up.running, push(dev, "quiz.txt")
    up.upgrade()
    return up, docker, old, new


def test_the_helper_rebuilds_and_waits_for_health(box, monkeypatch):
    up, docker, old, new = started(box)
    docker.healths = ["starting", "starting", "healthy"]
    waits = []
    up.finish(old, new, sleep=waits.append)
    assert up.status()["state"] == "done" and up.head() == new and docker.ups() == 1 and len(waits) == 2
    up_call = next(c for c in docker.calls if "up" in c)
    assert up_call[:4] == ("docker", "compose", "--project-directory", str(up.checkout)) and "--build" in up_call


def test_a_failed_build_leaves_the_old_stack_and_commit(box):
    up, docker, old, new = started(box)
    docker.builds = [(1, "failed to solve: npm ci exited 1")]
    up.finish(old, new, sleep=lambda s: None)
    status = up.status()
    assert (status["state"], up.head(), docker.ups()) == ("failed", old, 1)
    assert "the build failed, so nothing changed: failed to solve" in status["detail"]


def test_an_unhealthy_upgrade_is_rolled_back(box, monkeypatch):
    monkeypatch.setattr(upgrade, "HEALTH_TIMEOUT", 0)
    up, docker, old, new = started(box)
    docker.healths = ["unhealthy"]
    up.finish(old, new, sleep=lambda s: None)
    status = up.status()
    assert (status["state"], up.head(), docker.ups()) == ("rolled back", old, 2)
    assert status["detail"] == f"golem was unhealthy, deployer was unhealthy on {new[:7]}, and {old[:7]} is running again"


def test_the_api_needs_the_admin_secret_to_upgrade(tmp_path, box):
    up, dev, docker = box
    audit = Audit(tmp_path / "audit.jsonl")
    client = TestClient(create_app(None, audit, SECRET, ADMIN_SECRET, up))  # projects' calls aren't used here
    push(dev, "quiz.txt")
    r = client.get("/version?fetch=1", headers=AUTH)
    assert r.status_code == 200 and r.json()["behind"] == 1
    assert client.post("/upgrade", headers=AUTH).status_code == 401  # workers' secret isn't enough
    r = client.post("/upgrade", headers={**AUTH, **ADMIN})
    assert r.status_code == 200 and r.json()["to"] == up.head()
    r = client.post("/upgrade", headers={**AUTH, **ADMIN})
    assert r.status_code == 422 and r.json()["reasons"] == ["an upgrade is already under way"]

    off = TestClient(create_app(None, audit, SECRET, "", up))
    assert off.post("/upgrade", headers={**AUTH, **ADMIN}).status_code == 403
