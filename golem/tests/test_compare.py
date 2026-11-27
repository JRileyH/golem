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
from test_flow import TREES, idle_project
from test_ledger import costed

from golem import compare
from golem.store import Store

# A run's events, as Claude Code streams them: it hits its turn cap, a background task it started
# wakes it up, and it goes on to finish. Each result line has its own stretch's turns and tokens,
# but the cost and API time are running totals (as in cl-trees' review, job 84).
LOG = [
    {"type": "system", "subtype": "init", "claude_code_version": "2.9.1", "session_id": "s"},
    {"type": "assistant", "timestamp": "2026-11-26T10:00:00+00:00",
     "message": {"content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "bin/test"}}]}},
    {"type": "user", "timestamp": "2026-11-26T10:02:00+00:00",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "b1", "content": "1 failed", "is_error": True}]}},
    {"type": "result", "subtype": "error_max_turns", "num_turns": 5, "total_cost_usd": 0.6, "duration_api_ms": 60000,
     "usage": {"input_tokens": 40, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 600, "output_tokens": 80},
     "modelUsage": {"claude-opus-5-5": {"costUSD": 0.6}}},
    {"type": "system", "subtype": "task_notification"},
    {"type": "assistant", "parent_tool_use_id": "t1", "message": {"content": [{"type": "tool_use", "name": "Edit"}, {"type": "tool_use", "name": "Bash"}]}},
    {"type": "user", "message": {"content": "a plain prompt"}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "StructuredOutput", "input": {}}]}},
    {"type": "result", "subtype": "success", "num_turns": 2, "total_cost_usd": 1.0, "duration_api_ms": 90000,
     "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 150, "output_tokens": 20},
     "modelUsage": {"claude-opus-5-5": {"costUSD": 0.9}, "claude-haiku-5-5": {"costUSD": 0.1}}},
]  # fmt: skip


def git(where, *args):
    subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   check=True, capture_output=True)  # fmt: skip


def two_projects(golem):
    """tree-cards and fish: each its first job, then a change."""

    async def go():
        room = await idle_project(golem)
        job = await golem.ask(room, "add the a and b files", workflow="change")
        golem.harness.script = costed({})
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
    # It asked a question, and its session carried on 50 minutes later with his answer.
    again = golem.store.start_run(fish.id, "change", run["model"], "", run["step_id"])
    golem.store.finish_run(again, "verdict", num_turns=2, cost_usd=0.2, input_tokens=10, output_tokens=5)
    for run_id, began, ended in ((run["id"], "10:00", "10:10"), (again, "11:00", "11:05")):
        golem.store.db.execute("UPDATE runs SET started_at = ?, ended_at = ? WHERE id = ?", (f"2026-11-26T{began}:00+00:00", f"2026-11-26T{ended}:00+00:00", run_id))
    checkout = golem.settings.workspace_dir / "fish"
    checkout.mkdir(parents=True, exist_ok=True)
    git(checkout, "init", "-q", "-b", "main")
    (checkout / "README.md").write_text("# Fish\n")
    git(checkout, "add", "README.md")
    git(checkout, "commit", "-qm", "first")  # where the checkout stays: the jobs merge on GitHub
    git(checkout, "checkout", "-q", "-b", "work")
    (checkout / "app.py").write_text("a\nb\nc\n")
    (checkout / "tests").mkdir(exist_ok=True)
    (checkout / "tests" / "test_app.py").write_text("x\ny")
    (checkout / "package-lock.json").write_text("{}\n" * 100)  # a lockfile's lines aren't the work's
    (checkout / "logo.png").write_bytes(b"\x89PNG\0\0\n\n")
    os.symlink("/etc/hosts", checkout / "hosts")  # never followed
    git(checkout, "add", "app.py", "tests/test_app.py", "package-lock.json", "logo.png", "hosts")
    git(checkout, "commit", "-qm", "the work")
    git(checkout, "update-ref", "refs/remotes/origin/main", "work")  # as a job's fetch leaves it
    git(checkout, "checkout", "-q", "main")

    text = compare.table(golem.store, golem.settings, "tree-cards", "fish")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == list(compare.COLUMNS) and text.count("\n") == len(rows) + 1  # a line a row
    assert [(r["level"], r["side"], r["project"], r["job_n"]) for r in rows if r["level"] in ("project", "job")] == [
        ("project", "A", "tree-cards", ""), ("job", "A", "tree-cards", "1"), ("job", "A", "tree-cards", "2"),
        ("project", "B", "fish", ""), ("job", "B", "fish", "1"), ("job", "B", "fish", "2"),
    ]  # fmt: skip

    # A's change: its ledger, and a row for each step, its agents' and golem's own.
    job = next(r for r in rows if r["level"] == "job" and r["side"] == "A" and r["job_n"] == "2")
    assert (job["kind"], job["state"], job["text"]) == ("change", "done", "add the a and b files")
    assert (job["runs"], job["cost_usd"], job["tokens_in"], job["cost_opus"], job["cost_sonnet"]) == ("3", "1.5500", "3000", "1.5000", "0.0500")  # docs on sonnet (Claude power)
    steps = [r for r in rows if r["level"] == "step" and r["job"] == job["job"]]
    built = next(r for r in steps if r["name"] == "change")
    assert (built["kind"], built["runs"], built["cost_usd"], built["outcomes"]) == ("agent", "1", "1.0000", "verdict")
    setup = next(r for r in steps if r["kind"] == "setup")
    assert (setup["runs"], setup["cost_usd"]) == ("", "")  # golem's own: no runs

    # The usual change: what its run's log says that the runs table doesn't.
    change = next(r for r in rows if r["level"] == "step" and r["side"] == "B" and r["name"] == "change")
    # The first run's turns and tokens from both its stretches (golem kept 3 turns and 1000 tokens), its cost and API time
    # the last line's running totals; the second run has no log, so golem's record of it stands.
    assert (change["turns"], change["tokens_in"], change["tokens_out"], change["result_lines"], change["past_max_turns"]) == ("9", "1010", "105", "2", "1")
    assert (change["fresh_tokens_in"], change["cache_write_tokens"], change["cache_read_tokens"], change["api_minutes"]) == ("50", "200", "750", "1.5")
    assert (change["tool_calls"], change["tool_errors"], change["tools"]) == ("3", "1", "Bash 2, Edit 1")  # a subagent's too, the verdict aside
    assert (change["claude_models"], change["claude_code"]) == ("claude-opus-5-5 0.9000, claude-haiku-5-5 0.1000", "2.9.1")
    assert (change["tests_minutes"], change["test_runs"], change["warnings"]) == ("2.0", "1", "ran out of turns")  # where its time went (glance.py)
    assert "bin/test" in change["slowest_call"] and change["slowest_call"].endswith("(120s)")

    assert (change["runs"], change["between_runs_minutes"], change["outcomes"]) == ("2", "50.0", "verdict verdict")
    fish_job = next(r for r in rows if r["level"] == "job" and r["job"] == str(fish.id))
    assert fish_job["between_runs_minutes"] == "50.0" and next(r for r in rows if r["level"] == "project" and r["side"] == "B")["between_runs_minutes"] == "50.0"
    assert built["between_runs_minutes"] == ""  # one run: nothing between

    # Each step's runs follow it, and what golem said about it.
    at = rows.index(change)
    run_row = rows[at + 1]
    assert (run_row["level"], run_row["step"], run_row["run"], run_row["name"], run_row["state"]) == ("run", change["step"], str(run["id"]), "change", "verdict")
    assert (run_row["turns"], run_row["cost_usd"], run_row["model"]) == ("7", "1.0000", run["model"])
    said = [r for r in rows if r["level"] == "message" and r["job"] == str(fish.id)]
    assert said and all(r["name"] in ("golem", "riggy") and r["started"] and r["text"] for r in said)
    assert any(r["step"] == change["step"] for r in said)  # its "✓ change" news, after the step
    steps_and_runs = [r for r in rows if r["level"] in ("step", "run") and r["side"] == "A"]
    assert sum(float(r["cost_usd"] or 0) for r in steps_and_runs if r["level"] == "run") == float(job["cost_usd"])

    # The projects: everything added up, and what each checkout holds.
    a, b = (r for r in rows if r["level"] == "project")
    assert (a["name"], a["mode"], a["jobs"], a["repo_files"]) == ("Tree Cards", "usual", "2", "")
    assert float(a["cost_usd"]) == sum(float(r["cost_usd"]) for r in rows if r["level"] == "job" and r["side"] == "A")
    assert (b["name"], b["tool_calls"], b["url"]) == ("Fish", "3", golem.settings.project_url("fish"))
    assert (b["repo_files"], b["repo_lines"], b["repo_test_files"], b["repo_test_lines"], b["repo_commits"]) == ("6", "6", "1", "2", "2")
    assert b["repo_types"].startswith(".py 2, ") and b["repo_mb"] == "0.0"


def test_the_command_says_what_its_columns_mean_and_what_it_cant_find(golem, tmp_path, capsys):
    two_projects(golem)
    assert compare.main(["tree-cards", "nowhere"], golem.settings) == 2
    assert capsys.readouterr().err == "No project at nowhere.\n"
    out = tmp_path / "both.csv"
    assert compare.main(["tree-cards", "fish", "-o", str(out)], golem.settings) == 0
    assert out.read_text() == compare.table(golem.store, golem.settings, "tree-cards", "fish")
    assert compare.main(["--columns"]) == 0
    said = capsys.readouterr().out.splitlines()
    assert len(said) == len(compare.COLUMNS) and said[0].startswith("level: project, job, step, run or message: what the row is about")


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
