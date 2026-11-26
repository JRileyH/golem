"""A run at a glance (glance.py): where its time went and what went wrong, from its events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from golem import glance
from golem.glance import Glance

T0 = datetime(2026, 11, 26, 17, 0, tzinfo=UTC)


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def call(t: float, key: str, name: str, **data) -> dict:
    return {"type": "assistant", "timestamp": at(t), "message": {"content": [{"type": "tool_use", "id": key, "name": name, "input": data}]}}


def answer(t: float, key: str, text: str = "ok", error: bool = False) -> dict:
    return {"type": "user", "timestamp": at(t), "message": {"content": [{"type": "tool_result", "tool_use_id": key, "content": text, "is_error": error}]}}


# The checklist item from 2026-11-26: built for 2½ minutes, fumbled stopping a server, ran the
# whole Playwright suite into Claude Code's time limit, watched it, gave its verdict at 5:41, and
# went on to 10:33 re-sending it each time a watch woke it.
ITEM = [
    {"type": "system", "subtype": "init", "timestamp": at(0)},
    call(10, "e1", "Edit", file_path="src/App.svelte"), answer(20, "e1"),
    call(154, "b1", "Bash", command='pkill -f "vite preview"'), answer(155, "b1", "snapshot.sh: line 159: pkill: command not found", True),
    call(155, "b2", "Bash", command="ps -eo pid,args"), answer(158, "b2", "/bin/bash: line 1: ps: command not found", True),
    call(168, "b3", "Bash", command="CI=1 npx playwright test --reporter=line"),
    answer(288, "b3", "Command did not complete within its 120s timeout and was moved to the background (ID: b9j)."),
    call(291, "m1", "Monitor", command="until grep -q passed out", description="the suite"), answer(291, "m1", "Monitor started"),
    call(311, "s1", "Bash", command="sleep 5"), answer(316, "s1", ""),
    call(335, "k1", "Bash", command="kill 205028"), answer(337, "k1"),
    call(341, "v1", "StructuredOutput", verdict="blocked", message="0.59 against 0.6"), answer(341, "v1", "Structured output provided successfully"),
    call(592, "v2", "StructuredOutput", verdict="blocked", message="0.59 against 0.6"), answer(592, "v2", "Structured output provided successfully"),
    call(633, "v3", "StructuredOutput", verdict="blocked", message="0.59 against 0.6"), answer(633, "v3", "Structured output provided successfully"),
    {"type": "result", "subtype": "success", "timestamp": at(633)},
]


def test_where_a_runs_time_went_and_what_went_wrong():
    g = glance.of(ITEM)
    assert (g.tests, g.after) == (120, 292)  # the suite; 5:41 to 10:33
    assert g.waiting == 5  # the sleep (the Monitor call itself returned at once)
    assert g.work == 341 - 120 - 5
    assert g.test_runs == 1 and g.missing == ["pkill", "ps"] and g.timeouts == 1
    assert (g.slowest, g.slowest_public, g.slowest_seconds) == ("CI=1 npx playwright test --reporter=line", "Bash", 120)
    assert glance.times(g) == "tests 2m · waiting 5s · after its verdict 4m · work 3m"
    assert glance.warnings(g) == ["went on 4m after its verdict", "1 command hit the time limit", "not installed: pkill, ps"]
    assert glance.slowest(g, True) == "slowest: CI=1 npx playwright test --reporter=line (2m)" and glance.slowest(g, False) == "slowest: Bash (2m)"
    assert glance.doing(g, True) == ""  # it's over


def test_a_running_run_says_what_its_on_and_counts_it_up_to_now():
    reading = glance.Reading()
    for event in ITEM[:8]:  # into the suite, not back yet
        reading.read(event)
    g = reading.glance(T0 + timedelta(seconds=228))
    assert g.running and g.tests == 60 and g.after == 0
    assert glance.doing(g, True) == "now: CI=1 npx playwright test --reporter=line for 1m"
    assert glance.doing(g, False) == "now: Bash for 1m"  # commands are for Riggy signed in
    reading.read(ITEM[8])
    assert glance.doing(reading.glance(T0 + timedelta(seconds=300)), True) == "now: thinking for 12s"


def test_a_glance_keeps_and_steps_add_up_their_runs():
    g = glance.of(ITEM)
    back = Glance.from_json(g.to_json())
    assert back == g
    assert Glance.from_json("") is None and Glance.from_json("[1]") is None
    both = glance.merged([g, Glance(work=60, timeouts=1, missing=["lsof"])])
    assert both.work == g.work + 60 and both.timeouts == 2 and both.missing == ["lsof", "pkill", "ps"]
    assert glance.merged([]) is None


def test_secrets_are_masked_and_tests_told_from_other_commands():
    events = [
        {"type": "system", "subtype": "init", "timestamp": at(0)},
        call(1, "a", "Bash", command="curl -H 'token: s3cr3t-value' https://x"), answer(100, "a"),
        call(100, "b", "Bash", command="bin/test"), answer(130, "b", "Timed out 5000ms waiting for expect", True),  # a test's words: not a timeout
        {"type": "system", "subtype": "compact_boundary", "timestamp": at(131)},
    ]
    g = glance.of(events, mask=lambda text: text.replace("s3cr3t-value", "•••"))
    assert "s3cr3t" not in g.slowest and g.tests == 30 and g.timeouts == 0
    assert glance.warnings(g) == ["its context filled and was compacted"]
