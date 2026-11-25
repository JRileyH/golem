"""Reports about golem itself (reports.py): what's taken, how incidents count up, what's kept out."""

from __future__ import annotations

import json

import pytest

from golem import reports
from golem.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "golem.db")


def test_a_report_must_say_what_kind_how_bad_and_what():
    made = reports.make("instructions", "costly", "  skills/deploy.md\n says port 80,  compose says 8080 ", "x" * 9000)
    assert made["summary"] == "skills/deploy.md says port 80, compose says 8080"  # one line
    assert len(made["detail"]) == reports.DETAIL_CHARS
    with pytest.raises(reports.Invalid, match="kind must be one of bug, instructions"):
        reports.make("gripe", "minor", "x")
    with pytest.raises(reports.Invalid, match="impact must be one of blocked, costly, minor"):
        reports.make("bug", "huge", "x")
    with pytest.raises(reports.Invalid, match="one line"):
        reports.make("bug", "minor", "   ")


def test_a_line_that_isnt_a_report_is_none():
    good = json.dumps({"kind": "bug", "impact": "minor", "summary": "golem-deploy logs is empty"})
    assert reports.parse(good) == {"kind": "bug", "impact": "minor", "summary": "golem-deploy logs is empty", "detail": ""}
    for bad in ("not json", "[]", json.dumps({"kind": "bug"}), json.dumps({"kind": "nope", "impact": "minor", "summary": "s"})):
        assert reports.parse(bad) is None


def test_secrets_are_taken_out():
    env = {"GH_TOKEN": "ghs_abcdefghijklmnopqrstuvwxyz0123", "DEPLOYER_SECRET": "s3cr3t-value", "PATH": "/usr/bin", "SHORT_KEY": "abc"}
    text = "auth with ghs_abcdefghijklmnopqrstuvwxyz0123 and s3cr3t-value from /usr/bin abc; also ghp_" + "A" * 36
    assert reports.redact(text, env) == "auth with [GH_TOKEN] and [DEPLOYER_SECRET] from /usr/bin abc; also [token]"


def test_the_same_trouble_reads_the_same_wherever_it_happens():
    one = reports.normalize(
        "merging PR #53 on mimic (exit 1): GraphQL: Something went wrong while executing your query on "
        "2026-11-25T16:16:56Z. Please include `B7DC:1F1A:1A7EF1E:52AFE21:6B0709F5` when reporting this issue.",
        "mimic",
    )
    two = reports.normalize(
        "merging PR #7 on trees (exit 1): GraphQL: Something went wrong while executing your query on "
        "2026-12-01T09:00:00Z. Please include `0A1B:22CC:9F9F9F:1234567:ABCDEF12` when reporting this issue.",
        "trees",
    )
    assert one == two and "<slug>" in one and "<time>" in one
    assert reports.normalize("pushed 3f2a9c1 to main") == reports.normalize("pushed 9e8d7c6b5a to main")


def test_an_incident_counts_up_on_its_report_at_its_worst(store):
    first = store.file_incident("f1", "environment", "costly", "merging failed", "try 1", job_id=1, slug="mimic", label="merge")
    assert (first.source, first.count, first.state, first.fingerprint) == ("golem", 1, "new", "f1")
    again = store.file_incident("f1", "environment", "blocked", "merging failed", "try 2", job_id=4, slug="trees", label="merge")
    assert again.id == first.id and again.count == 2 and again.impact == "blocked"
    assert (again.detail, again.job_id, again.slug) == ("try 2", 4, "trees")  # the latest of where and what
    milder = store.file_incident("f1", "environment", "minor", "merging failed", "try 3")
    assert milder.impact == "blocked" and milder.count == 3 and milder.job_id == 4  # never milder; where stays if not said
    other = store.file_incident("f2", "bug", "minor", "something else")
    assert other.id != first.id


def test_once_riggy_took_a_fix_the_same_incident_again_is_news(store):
    first = store.file_incident("f1", "environment", "costly", "merging failed")
    store.db.execute("UPDATE reports SET state = 'accepted' WHERE id = ?", (first.id,))
    again = store.file_incident("f1", "environment", "costly", "merging failed")
    assert again.id != first.id and again.count == 1 and again.state == "new"
    store.db.execute("UPDATE reports SET state = 'dismissed' WHERE id = ?", (again.id,))
    assert store.file_incident("f1", "environment", "costly", "merging failed").id == again.id  # he said it's not worth it


def test_reports_are_listed_latest_first_and_new_ones_oldest_first(store):
    a = store.add_report("bug", "minor", "golem-deploy logs is empty", job_id=2, run_id=5, slug="trees", model="opus/high")
    b = store.file_incident("f1", "friction", "costly", "ran out of turns")
    assert (a.source, a.fingerprint, a.run_id, a.model) == ("agent", "", 5, "opus/high")
    assert [r.id for r in store.new_reports()] == [a.id, b.id]
    store.db.execute("UPDATE reports SET last_seen = '2000-01-01' WHERE id = ?", (a.id,))
    assert [r.id for r in store.reports()] == [b.id, a.id]
    assert [r.id for r in store.reports(source="agent")] == [a.id]
    assert store.reports(states=("triaged",)) == []
