"""Any two projects side by side in one CSV (compare.py), for a model to compare how they went."""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import os
import sqlite3
import subprocess

import pytest
from test_checklist import ITEMS, builds, checklist_job, costed, planner
from test_flow import TREES

from golem import compare
from golem.store import Store

LOG = [  # a run's events, as Claude Code streams them
    {"type": "system", "subtype": "init", "claude_code_version": "2.9.1", "session_id": "s"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "name": "Bash", "input": {}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "no such file", "is_error": True}]}},
    {"type": "assistant", "parent_tool_use_id": "t1", "message": {"content": [{"type": "tool_use", "name": "Edit"}, {"type": "tool_use", "name": "Bash"}]}},
    {"type": "user", "message": {"content": "a plain prompt"}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "StructuredOutput", "input": {}}]}},
    {"type": "result", "subtype": "success", "duration_api_ms": 90000,
     "usage": {"input_tokens": 50, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 750, "output_tokens": 100},
     "modelUsage": {"claude-opus-5-5": {"costUSD": 0.9}, "claude-haiku-5-5": {"costUSD": 0.1}}},
]  # fmt: skip


def git(where, *args):
    subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   check=True, capture_output=True)  # fmt: skip


def two_projects(golem):
    """tree-cards: its first job, then a change in checklist mode; fish: its first job, then a change the usual way."""

    async def go():
        job = await checklist_job(golem)
        golem.harness.script = costed({"change": [planner(ITEMS, ["tests/items.txt"])], "item": [builds("a.txt"), builds(), builds(), builds("b.txt")]})
        await golem.finish()
        golem.harness.script = [TREES]
        await golem.new_project("a game about fish", name="Fish")
        golem.store.update_job(golem.store.active_job("fish").id, state="done")
        golem.harness.script = costed({})
        await golem.ask("fish", "make the fish blue", workflow="change")
        await golem.finish()
        return job

    return asyncio.run(go())


def test_two_projects_side_by_side_a_row_per_project_job_and_step(golem):
    two_projects(golem)
    fish = golem.store.project_jobs("fish")[0]
    run = next(r for r in golem.store.runs(fish.id) if r["phase"] == "change")
    with gzip.open(run["log_path"], "wt") as out:  # what a real run would have logged
        out.writelines(json.dumps(e) + "\n" for e in LOG)
    checkout = golem.settings.workspace_dir / "fish"
    checkout.mkdir(parents=True, exist_ok=True)
    git(checkout, "init", "-q")
    (checkout / "app.py").write_text("a\nb\nc\n")
    (checkout / "tests").mkdir(exist_ok=True)
    (checkout / "tests" / "test_app.py").write_text("x\ny")
    (checkout / "package-lock.json").write_text("{}\n" * 100)  # a lockfile's lines aren't the work's
    (checkout / "logo.png").write_bytes(b"\x89PNG\0\0\n\n")
    os.symlink("/etc/hosts", checkout / "hosts")  # never followed
    git(checkout, "add", "app.py", "tests/test_app.py", "package-lock.json", "logo.png", "hosts")
    git(checkout, "commit", "-qm", "first")

    text = compare.table(golem.store, golem.settings, "tree-cards", "fish")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == list(compare.COLUMNS) and text.count("\n") == len(rows) + 1  # a line a row
    assert [(r["level"], r["side"], r["project"], r["job_n"]) for r in rows if r["level"] != "step"] == [
        ("project", "A", "tree-cards", ""), ("job", "A", "tree-cards", "1"), ("job", "A", "tree-cards", "2"),
        ("project", "B", "fish", ""), ("job", "B", "fish", "1"), ("job", "B", "fish", "2"),
    ]  # fmt: skip

    # The checklist job: its ledger, and a row for each step, the planner's, the items' and their checks'.
    job = next(r for r in rows if r["level"] == "job" and r["side"] == "A" and r["job_n"] == "2")
    assert (job["mode"], job["kind"], job["state"], job["text"]) == ("checklist", "change", "done", "add the a and b files")
    assert (job["runs"], job["cost_usd"], job["tokens_in"], job["cost_haiku"]) == ("7", "1.9500", "7000", "0.3000")
    assert (job["items"], job["items_first_try"], job["items_again"], job["items_escalated"], job["items_gave_up"]) == ("2", "1", "1", "1", "0")
    assert (job["planning_cost_usd"], job["items_cost_usd"]) == ("1.0000", "0.4000")
    steps = [r for r in rows if r["level"] == "step" and r["job"] == job["job"]]
    plan_row = next(r for r in steps if r["role"] == "plan")
    assert (plan_row["name"], plan_row["runs"], plan_row["cost_usd"], plan_row["outcomes"]) == ("change", "1", "1.0000", "verdict")
    items = [(r["role"], r["item_title"], r["item_try"], r["model"].split("/")[0] if r["role"] == "item" else r["state"]) for r in steps if r["role"] in ("item", "check")]
    assert items == [
        ("item", "File A", "1", "haiku"), ("check", "File A", "1", "done"), ("item", "File B", "1", "haiku"), ("check", "File B", "1", "failed"),
        ("item", "File B", "2", "haiku"), ("check", "File B", "2", "failed"), ("item", "File B", "3", plan_row["model"].split("/")[0]),
        ("check", "File B", "3", "done"),
    ]  # fmt: skip
    check = next(r for r in steps if r["role"] == "check")
    assert (check["item_check"], check["item_of"], check["runs"], check["cost_usd"]) == ("test -f a.txt", "2", "", "")  # golem's own: no runs

    # The usual change: what its run's log says that the runs table doesn't.
    change = next(r for r in rows if r["level"] == "step" and r["side"] == "B" and r["name"] == "change")
    assert (change["fresh_tokens_in"], change["cache_write_tokens"], change["cache_read_tokens"], change["api_minutes"]) == ("50", "200", "750", "1.5")
    assert (change["tool_calls"], change["tool_errors"], change["tools"]) == ("3", "1", "Bash 2, Edit 1")  # a subagent's too, the verdict aside
    assert (change["claude_models"], change["claude_code"]) == ("claude-opus-5-5 0.9000, claude-haiku-5-5 0.1000", "2.9.1")

    # The projects: everything added up, and what each checkout holds.
    a, b = (r for r in rows if r["level"] == "project")
    assert (a["name"], a["mode"], a["jobs"], a["items"], a["planning_cost_usd"], a["repo_files"]) == ("Tree Cards", "usual", "2", "2", "1.0000", "")
    assert float(a["cost_usd"]) == sum(float(r["cost_usd"]) for r in rows if r["level"] == "job" and r["side"] == "A")
    assert (b["name"], b["tool_calls"], b["items"], b["url"]) == ("Fish", "3", "", golem.settings.project_url("fish"))
    assert (b["repo_files"], b["repo_lines"], b["repo_test_files"], b["repo_test_lines"], b["repo_commits"]) == ("5", "5", "1", "2", "1")
    assert b["repo_types"].startswith(".py 2, ")


def test_the_command_says_what_its_columns_mean_and_what_it_cant_find(golem, tmp_path, capsys):
    two_projects(golem)
    assert compare.main(["tree-cards", "nowhere"], golem.settings) == 2
    assert capsys.readouterr().err == "No project at nowhere.\n"
    out = tmp_path / "both.csv"
    assert compare.main(["tree-cards", "fish", "-o", str(out)], golem.settings) == 0
    assert out.read_text() == compare.table(golem.store, golem.settings, "tree-cards", "fish")
    assert compare.main(["--columns"]) == 0
    said = capsys.readouterr().out.splitlines()
    assert len(said) == len(compare.COLUMNS) and said[0] == "level: project, job or step: what the row is about"


def test_it_reads_golems_database_as_it_is_and_never_writes_to_it(golem):
    """A golem of another version may be running on it, the experiment under way: no migrations."""
    two_projects(golem)
    db = golem.settings.state_dir / "golem.db"
    golem.store.db.execute("ALTER TABLE proposals DROP COLUMN card")  # as a golem from before proposal cards has it
    store = Store.read_only(db)
    assert [p.slug for p in store.projects()] == [p.slug for p in golem.store.projects()]
    assert compare.table(store, golem.settings, "tree-cards", "fish")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store.put("anything", "1")
    assert "card" not in {r[1] for r in golem.store.db.execute("PRAGMA table_info(proposals)")}  # left as it was
