"""A repo's past golem jobs, read back from its issues and PRs (history.py)."""

from __future__ import annotations

import pytest

from golem import history


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


def records(golem, items):
    return history.records(golem.settings, "bugz", items)


def by_issue(found) -> dict:
    return {job.issue: (job, steps) for job, steps in found}


def test_jobs_come_back_from_their_issues_and_prs_oldest_first(golem):
    found = records(golem, BUGZ)
    assert [job.issue for job, _ in found] == [1, 11, 4, 14, None]  # by when each began
    assert {job.id for job, _ in found} == {0}  # numbered when they're stored
    jobs = by_issue(found)
    first, change = jobs[1][0], jobs[4][0]
    assert (first.kind, first.playbook, first.state, first.created_at) == ("new", "standard", "done", "2026-10-21T01:00:00+00:00")
    assert first.request == "A modern web based video game called bugz.\nWith five players."
    assert (change.kind, change.playbook, change.request) == ("change", "feature", "Looks great! Get started on the multiplayer now")
    assert jobs[11][0].state == "failed"  # its issue is still open
    assert jobs[14][0].state == "killed"
    assert (jobs[None][0].state, jobs[None][0].request) == ("done", "A change")  # its issue never opened: its PR says it all

    steps = jobs[4][1]
    assert [(s.kind, s.name, s.state) for s in steps] == [
        ("setup", "setup", "done"),
        ("pr", "pr", "done"),  # the stale PR golem's worker opened by hand: no steps listed
        ("agent", "change", "done"), ("agent", "review", "done"), ("pr", "pr", "done"), ("merge", "merge", "done"),
        ("agent", "fix", "done"), ("pr", "pr", "done"), ("merge", "merge", "done"),
    ]  # fmt: skip
    assert steps[2].summary == "The menu is now simpler.\nAnd trips have codes."
    assert steps[4].links == {"pr": "https://github.com/unlaunched/x/pull/12"}
    assert [s.kind for s in jobs[1][1]] == ["setup", "pr", "merge"]


def test_an_adopted_repos_first_job_is_its_adoption(golem):
    mimic = [
        issue(1, 4, "!adopt mimic cards for my D&D table", head="Requested:"),
        pr(2, "Closes #1\n\n- **adopt:** It deploys through golem now.\n\nOpened by golem (job 4); it merges this once CI passes."),
        issue(3, 6, "Add a dark theme", head="Requested:"),
    ]
    jobs = by_issue(records(golem, mimic))
    assert (jobs[1][0].kind, jobs[1][0].playbook, jobs[1][0].request) == ("adopt", "adopt", "!adopt mimic cards for my D&D table")
    assert [s.name for s in jobs[1][1]] == ["setup", "adopt", "pr", "merge"]
    assert (jobs[3][0].kind, jobs[3][0].playbook) == ("change", "feature")
    # One adopted from the New project page says so at the top of its issue.
    jobs = by_issue(records(golem, [issue(1, 4, "cards for my D&D table", head="Adopted:")]))
    assert (jobs[1][0].kind, jobs[1][0].request) == ("adopt", "cards for my D&D table")


def test_a_job_is_its_issue_whatever_number_an_earlier_golem_gave_it(golem):
    """Two golems (or one, before and after deleting the project) can both have had a job 7 on a repo."""
    twice = [issue(1, 7, "First go"), issue(2, 7, "Second go"), pr(3, "Closes #2\n\nOpened by golem (job 7).")]
    jobs = by_issue(records(golem, twice))
    assert [j.request for j, _ in jobs.values()] == ["First go", "Second go"]
    assert [s.kind for s in jobs[1][1]] == ["setup"] and [s.kind for s in jobs[2][1]] == ["setup", "pr", "merge"]


def test_a_repo_golem_never_worked_on_has_no_jobs(golem):
    assert records(golem, [pr(1, "Closes #2"), {**issue(2, 1, "x"), "body": "A bug report"}]) == []


@pytest.mark.parametrize(
    "body, words",
    [
        ("Requested:\n\n> Make it blue\n\nWorked on by golem (job 3).", "Make it blue"),
        ("Requested in Matrix:\n\n> one\n> two\n\nWorked on by office (job 3).", "one\ntwo"),
        ("Requested:\n\n> first line\nsecond line\n\nWorked on by golem (job 3).", "first line\nsecond line"),
    ],
)
def test_request_from_issue(body, words):
    assert history._request(body) == words
