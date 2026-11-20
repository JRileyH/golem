"""Golem's past jobs on a repo, read back from its issues and PRs (recover.py)."""

from __future__ import annotations

import pytest

from golem import recover


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


def records(golem, items, title=""):
    return recover.records(golem.settings, recover.Repo("bugz", "bugz, a game", "2026-10-01T00:00:00+00:00", title, items))


def test_jobs_come_back_from_their_issues_and_prs(golem):
    project, found = records(golem, BUGZ, "bugz: the game")
    assert (project.name, project.summary, project.mode) == ("bugz: the game", "bugz, a game", "standard")
    jobs = {job.id: (job, steps) for job, steps in found}
    assert sorted(jobs) == [1, 2, 7, 9, 21]
    first, change = jobs[1][0], jobs[2][0]
    assert (first.kind, first.playbook, first.state, first.issue) == ("new", "standard", "done", 1)
    assert first.request == "A modern web based video game called bugz.\nWith five players."
    assert (change.kind, change.playbook, change.request) == ("change", "feature", "Looks great! Get started on the multiplayer now")
    assert jobs[7][0].state == "failed"  # its issue is still open
    assert jobs[9][0].state == "killed"
    assert (jobs[21][0].state, jobs[21][0].issue, jobs[21][0].request) == ("done", None, "A change")

    steps = jobs[2][1]
    assert [(s.kind, s.name, s.state) for s in steps] == [
        ("setup", "setup", "done"),
        ("pr", "pr", "done"),  # the stale PR golem's worker opened by hand: no steps listed
        ("agent", "change", "done"), ("agent", "review", "done"), ("pr", "pr", "done"), ("merge", "merge", "done"),
        ("agent", "fix", "done"), ("pr", "pr", "done"), ("merge", "merge", "done"),
    ]  # fmt: skip
    assert steps[2].summary == "The menu is now simpler.\nAnd trips have codes."
    assert steps[4].links == {"pr": "https://github.com/unlaunched/x/pull/12"}
    assert [s.kind for s in jobs[1][1]] == ["setup", "pr", "merge"]
    assert records(golem, [issue(1, 12, "Typhoon Day")])[0].name == "Typhoon Day"  # no README heading: its first issue


def test_an_adopted_repos_first_job_is_its_adoption(golem):
    mimic = [
        issue(1, 4, "!adopt mimic cards for my D&D table", head="Requested:"),
        pr(2, "Closes #1\n\n- **adopt:** It deploys through golem now.\n\nOpened by golem (job 4); it merges this once CI passes."),
        issue(3, 6, "Add a dark theme", head="Requested:"),
    ]
    jobs = {job.id: (job, steps) for job, steps in records(golem, mimic)[1]}
    assert (jobs[4][0].kind, jobs[4][0].playbook, jobs[4][0].request) == ("adopt", "adopt", "!adopt mimic cards for my D&D table")
    assert [s.name for s in jobs[4][1]] == ["setup", "adopt", "pr", "merge"]
    assert (jobs[6][0].kind, jobs[6][0].playbook) == ("change", "feature")


def test_a_repo_golem_never_worked_on_has_no_jobs(golem):
    assert records(golem, [pr(1, "Closes #2"), {**issue(2, 1, "x"), "body": "A bug report"}])[1] == []


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
