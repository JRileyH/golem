"""A new install on an org golem used before: projects and jobs back from GitHub (recover.py)."""

from __future__ import annotations

import asyncio
import json

import pytest

from golem import recover
from golem.talk import STATUS


def issue(number, job, request, state="closed", reason="completed", head="Requested in Matrix:"):
    return {
        "number": number, "title": request.splitlines()[0][:80], "pr": False, "merged_at": None,
        "body": f"{head}\n\n> {request}\n\nWorked on by office (job {job}).",
        "state": state, "state_reason": reason if state == "closed" else None,
        "created_at": f"2026-10-2{number % 10}T01:00:00Z", "closed_at": f"2026-10-2{number % 10}T02:00:00Z" if state == "closed" else None,
        "html_url": f"https://github.com/unlaunched/x/issues/{number}",
    }  # fmt: skip


def pr(number, body, merged=True, title="A change"):
    return {
        "number": number, "title": title, "pr": True, "body": body, "state": "closed", "state_reason": None,
        "merged_at": "2026-10-24T05:00:00Z" if merged else None, "created_at": "2026-10-24T04:00:00Z",
        "closed_at": "2026-10-24T05:00:00Z", "html_url": f"https://github.com/unlaunched/x/pull/{number}",
    }  # fmt: skip


BUGZ = [
    issue(1, 1, "A modern web based video game called bugz.\nWith five players."),
    pr(2, "The playable MVP from `docs/specs/mvp.md`.\n\nCloses #1\n\n🤖 Generated with Claude Code", title="MVP: five stops"),
    issue(4, 2, "Looks great! Get started on the multiplayer now"),
    pr(12, "Closes #4\n\n- **change:** The menu is now simpler.\nAnd trips have codes.\n- **review:** Reviewed it.\n\n"
       "Opened by office (job 2); it merges this once CI passes."),
    pr(13, "Closes #4\n\n- **fix:** Fixed the build.\n\nOpened by office (job 2); it merges this once CI passes."),
    pr(7, "Closes #4.\n\n- **Art:** grimdark, from a worker's own PR.", merged=False, title="The ashen road"),
    issue(11, 7, "I'd like to", state="open"),
    issue(14, 9, "Never mind this one", reason="not_planned"),
    pr(15, "Part of #99\n\n- **change:** Its issue never opened.\n\nOpened by golem (job 21); it merges this once CI passes."),
]  # fmt: skip


class FakeGitHub:
    def __init__(self, repos: dict[str, list[dict]], titles: dict[str, str] | None = None, fail: str = ""):
        self.repos_ = repos
        self.titles = titles or {}
        self.fail = fail

    async def repos(self):
        if self.fail:
            raise recover.WorkspaceError(self.fail)
        return [{"name": n, "description": f"{n}, a game", "createdAt": f"2026-10-0{i + 1}T00:00:00Z"} for i, n in enumerate(self.repos_)]

    async def items(self, slug):
        return self.repos_[slug]

    async def title(self, slug):
        return self.titles.get(slug, "")


def pending(golem) -> None:
    golem.store.put(recover.KEY, recover.PENDING)


def rebuild(golem, github) -> None:
    asyncio.run(recover.rebuild(golem.settings, golem.store, golem.talk, github))


def redeploy(golem) -> None:
    asyncio.run(recover.redeploy(golem.settings, golem.store, golem.talk, golem.workspace, golem.deployer))


def test_projects_and_jobs_come_back_with_their_ids(golem):
    pending(golem)
    github = FakeGitHub({"bugz": BUGZ, "hello": [], "typhoon-day": [issue(1, 9 + 3, "Typhoon Day")]}, {"bugz": "bugz: the game"})
    rebuild(golem, github)

    assert [p.slug for p in golem.store.projects()] == ["typhoon-day", "bugz"]  # hello has no golem issues
    bugz = golem.store.project("bugz")
    assert (bugz.name, bugz.summary, bugz.mode, bugz.model, bugz.created_at) == (
        "bugz: the game", "bugz, a game", "standard", "", "2026-10-01T00:00:00+00:00"
    )
    assert golem.store.project("typhoon-day").name == "Typhoon Day"  # no README heading: its first issue

    jobs = {j.id: j for j in golem.store.jobs(limit=100)}
    assert sorted(jobs) == [1, 2, 7, 9, 12, 21]
    assert (jobs[1].kind, jobs[1].playbook, jobs[1].state, jobs[1].issue) == ("new", "standard", "done", 1)
    assert jobs[1].request == "A modern web based video game called bugz.\nWith five players."
    assert (jobs[2].kind, jobs[2].playbook, jobs[2].request) == ("change", "feature", "Looks great! Get started on the multiplayer now")
    assert jobs[7].state == "failed"  # its issue is still open
    assert jobs[9].state == "killed"
    assert (jobs[21].state, jobs[21].issue, jobs[21].request) == ("done", None, "A change")

    steps = golem.store.steps(2)
    assert [(s.kind, s.name, s.state) for s in steps] == [
        ("setup", "setup", "done"),
        ("pr", "pr", "done"),  # the stale PR golem's worker opened by hand: no steps listed
        ("agent", "change", "done"), ("agent", "review", "done"), ("pr", "pr", "done"), ("merge", "merge", "done"),
        ("agent", "fix", "done"), ("pr", "pr", "done"), ("merge", "merge", "done"),
    ]  # fmt: skip
    assert steps[2].summary == "The menu is now simpler.\nAnd trips have codes."
    assert steps[4].links == {"pr": "https://github.com/unlaunched/x/pull/12"}
    assert [s.kind for s in golem.store.steps(1)] == ["setup", "pr", "merge"]

    # New jobs come after the old ones, so none takes an old job's branch.
    assert golem.store.add_job("bugz", "change", "more bugs", "feature").id == 22
    assert golem.chat.texts("bugz")[0].startswith("I moved to a new server and rebuilt this project from GitHub: 5 job(s)")
    assert "2 project(s) (bugz: the game, Typhoon Day) and their 6 jobs" in golem.chat.texts(STATUS)[0]
    assert [p.slug for p, _ in golem.store.live_projects()] == ["bugz", "typhoon-day"]  # their finished jobs


def test_only_a_new_database_is_rebuilt(golem):
    rebuild(golem, FakeGitHub({"bugz": BUGZ}))
    assert golem.store.projects() == []


def test_an_org_golem_never_used_starts_empty(golem):
    pending(golem)
    rebuild(golem, FakeGitHub({"hello": [], "mine": [pr(1, "Closes #2")]}))
    assert golem.store.projects() == [] and golem.chat.sent == []
    redeploy(golem)
    assert golem.deployer.deploys == [] and golem.store.get(recover.KEY) == "[]"


def test_github_unreachable_tries_again_next_start(golem):
    pending(golem)
    rebuild(golem, FakeGitHub({}, fail="gh: bad credentials"))
    assert golem.store.get(recover.KEY) == recover.PENDING
    assert "Restart me to try again" in golem.chat.texts(STATUS)[0]
    redeploy(golem)
    assert golem.deployer.deploys == []

    rebuild(golem, FakeGitHub({"bugz": BUGZ}))
    assert [p.slug for p in golem.store.projects()] == ["bugz"]


def test_rebuilt_projects_are_deployed_again(golem):
    pending(golem)
    github = FakeGitHub({"typhoon-day": [issue(1, 12, "Typhoon Day", state="open")], "bugz": BUGZ}, {"bugz": "bugz"})
    rebuild(golem, github)
    assert [p.slug for p, _ in golem.store.live_projects()] == ["bugz"]  # typhoon-day's only job never finished

    golem.deployer.refuse = ["deploy/compose.yml: no such file at that commit\nmore detail"]  # typhoon-day, the older
    redeploy(golem)

    assert [slug for slug, _ in golem.deployer.deploys] == ["typhoon-day", "bugz"]
    redeployed = golem.store.jobs(limit=1)[0]
    assert (redeployed.id, redeployed.slug, redeployed.state, redeployed.request) == (22, "bugz", "done", recover.REDEPLOYED)
    [deploy] = golem.store.steps(redeployed.id)
    assert (deploy.kind, deploy.state, deploy.links["site"]) == ("deploy", "done", "https://bugz.unlaunched.dev/")
    assert golem.chat.texts(STATUS)[-1] == (
        "Back up: bugz.\nDidn't come back up (ask in its conversation for a fix):\n"
        "- Typhoon Day: deploy/compose.yml: no such file at that commit"
    )
    assert golem.store.get(recover.KEY) == "[]"

    redeploy(golem)  # the next start
    assert len(golem.deployer.deploys) == 2


def test_cards_follow_for_projects_that_came_back(golem):
    pending(golem)
    rebuild(golem, FakeGitHub({"bugz": BUGZ}))
    redeploy(golem)
    asyncio.run(golem.scheduler.missing_cards())
    assert golem.store.card("bugz").tagline == "Name that tree"


def test_a_restart_mid_redeploy_carries_on(golem):
    pending(golem)
    rebuild(golem, FakeGitHub({"bugz": BUGZ}))
    golem.store.put(recover.KEY, json.dumps(["deleted-since", "bugz"]))  # what was left when golem stopped
    redeploy(golem)
    assert [slug for slug, _ in golem.deployer.deploys] == ["bugz"]


@pytest.mark.parametrize(
    "body, words",
    [
        ("Requested:\n\n> Make it blue\n\nWorked on by golem (job 3).", "Make it blue"),
        ("Requested in Matrix:\n\n> one\n> two\n\nWorked on by office (job 3).", "one\ntwo"),
        ("Requested:\n\n> first line\nsecond line\n\nWorked on by golem (job 3).", "first line\nsecond line"),
    ],
)
def test_request_from_issue(body, words):
    assert recover._request(body) == words
