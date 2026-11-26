"""Any two projects side by side in one CSV, for a model (or Riggy) to compare how they went.

    python -m golem.compare A B [-o FILE]   the CSV
    python -m golem.compare --columns       what each column means, a line each

`just compare A B` runs both on the server and writes them to var/compare/. Built both ways
(checklist.py), an idea is two projects, `<slug>` and `cl-<slug>`, but any two will do.

There's a row per project, per job and per step (`level`), in that order for A, then for B. A
step's agent runs are added up on its row, a job's on its row and a project's on its row. Besides
what golem keeps on each run (cost at API prices, turns, tokens), each run's log says what the
runs table doesn't: input from the cache and not, time waiting on the API, the tools it called and
how many came back as errors, and the models Claude Code itself used. A job's row has its ledger
(ledger.py): fixes, time waiting on Riggy, a checklist's planning against its items and how the
items went. Each project's checkout gives the size of what it built.

Golem keeps no judgement of how good the work is: that's for whoever reads the CSV, and the sites.
Reads only: the database, the run logs and the checkouts.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import checklist, ledger, plan
from .config import Settings
from .store import Job, Step, Store
from .watch import Tail

MODELS = ("haiku", "sonnet", "opus", "fable")  # golem's choices, a cost column each

COLUMNS = {
    "level": "project, job or step: what the row is about",
    "side": "A or B: which of the two projects, as given",
    "project": "the project's slug (its address)",
    "job_n": "the job's place among its project's jobs, oldest first: to pair it with the other side's",
    "job": "golem's job id",
    "step_n": "the step's place in its job",
    "step": "golem's step id",
    "name": "project: its name; job: its title; step: its name (a stage, or one of golem's own steps)",
    "kind": "project: its tag; job: where it came from (new, change, look = a question, propose, adopt); "
    "step: agent, check, or one of golem's own (setup, push, PR, CI, merge, deploy, checkin, choose…)",
    "role": "step: plan (a Build that planned a checklist), item (an item's build), check (golem running "
    "an item's check), ask (waiting on Riggy); blank for any other",
    "mode": "usual or checklist (project: the mode its requests run in unless they ask for another)",
    "vacuum": "project: 1 if it runs as on a fresh golem (lessons of its own starting empty, an empty Claude Code per job)",
    "state": "project: paused or blank; job: done, killed, running, waiting…; step: done, failed, skipped…",
    "started": "ISO time: project made; job's first step began; step began",
    "ended": "ISO time: job's last step ended; step ended",
    "wall_minutes": "job, step: from started to ended (a job still under way: to now)",
    "waiting_minutes": "project, job: of the wall time, waiting on Riggy (check-ins and picks)",
    "model": "project: its model/effort for every step ('' for each step's default); "
    "step: golem's choice of model/effort for each of its runs, in order",
    "workflow": "job: the workflow it ran",
    "node": "step: the block of the job's workflow it's a visit to",
    "visit": "step: which visit to its block (loops come back)",
    "turn": "step: the turn it takes on its project: build, ship or none",
    "port": "step: the output it left by",
    "max_turns": "step: its runs' turn cap",
    "outcomes": "step: each run's outcome, in order: verdict (finished), max_turns, limit, error, or running",
    "runs": "agent runs (headless Claude Code)",
    "cost_usd": "what Claude Code says the runs would have cost at API prices",
    "turns": "Claude Code turns, added up",
    "tokens_in": "input tokens, cached or not",
    "fresh_tokens_in": "input tokens not from the cache (from the run logs)",
    "cache_write_tokens": "input tokens written to the cache (from the run logs)",
    "cache_read_tokens": "input tokens read from the cache (from the run logs)",
    "tokens_out": "output tokens",
    "agent_minutes": "how long the runs ran",
    "api_minutes": "of that, waiting on the model's API (from the run logs)",
    **{f"cost_{m}": f"cost_usd of runs golem gave {m}" for m in MODELS},
    "cost_other": "cost_usd of runs on any other model",
    "claude_models": "cost by the models Claude Code says it used, a run's chores on a small model too (from the run logs)",
    "tool_calls": "tool calls the runs made, their subagents' too, the final verdict aside (from the run logs)",
    "tool_errors": "tool calls that came back as errors (from the run logs)",
    "tools": "tool calls by tool, most first (from the run logs)",
    "claude_code": "Claude Code's version (from the run logs)",
    "jobs": "project: how many jobs",
    "fixes": "project, job: fix steps, after a failed CI run or a refused deploy",
    "asks": "project, job: times it waited on Riggy",
    "planning_cost_usd": "checklist mode: what the Builds that planned items cost",
    "items_cost_usd": "checklist mode: what building the items cost",
    "items": "checklist mode: items checked",
    "items_first_try": "checklist mode: items whose first check passed",
    "items_again": "checklist mode: items built more than once",
    "items_escalated": f"checklist mode: items that went to their Build's own model for try {checklist.TRIES}",
    "items_gave_up": "checklist mode: items still failing after their last try, carried on without",
    "item_n": "step: which item of its plan",
    "item_of": "step: of how many",
    "item_try": f"step: which try at the item (try {checklist.TRIES} is on the Build's model)",
    "item_title": "step: the item as planned: its title",
    "item_do": "step: what to do",
    "item_files": "step: the files to touch or read first",
    "item_check": "step: the command that exits 0 once the item works",
    "repo_files": "project: files tracked in its checkout",
    "repo_lines": "project: lines in them, lockfiles and binary files aside",
    "repo_test_files": "project: tracked files that look like tests",
    "repo_test_lines": "project: lines in those",
    "repo_commits": "project: commits on its checkout's branch",
    "repo_types": "project: tracked files by extension, most first",
    "url": "project: where it's hosted",
    "text": "project: its summary; job: Riggy's request; step: what it did, or what it's waiting for",
    "problem": "step: what a fix step was to fix, or what failed on an item's try before",
    "error": "step: why its last attempt failed",
}  # fmt: skip

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock", "uv.lock", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum",
}  # fmt: skip
TESTS = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|(^|/)test_[^/]*$|[._-](test|spec)\.[^/]+$")
BIGGEST = 5 * 1024 * 1024  # a file this big isn't read for its lines


# --- a run's log ---------------------------------------------------------------------------------


@dataclass
class Log:
    """What a run's log says that the runs table doesn't: Claude Code's result line, and its calls."""

    fresh_in: int = 0
    cache_write: int = 0
    cache_read: int = 0
    api_ms: int = 0
    claude: Counter[str] = field(default_factory=Counter)  # cost by the model Claude Code used
    calls: Counter[str] = field(default_factory=Counter)  # tool calls by tool
    failed: int = 0  # tool results that were errors
    version: str = ""


def read_log(path: str | None) -> Log:
    """A run's compressed event stream (ClaudeHarness), read once it's over or as far as it's got."""
    log = Log()
    if not path or not Path(path).is_file():
        return log
    for event in Tail(Path(path)).events():
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            log.version = str(event.get("claude_code_version") or "")
        content = (event.get("message") or {}).get("content") if isinstance(event.get("message"), dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if kind == "assistant" and block.get("type") == "tool_use" and block.get("name") != "StructuredOutput":
                log.calls[str(block.get("name") or "?")] += 1
            elif kind == "user" and block.get("type") == "tool_result" and block.get("is_error"):
                log.failed += 1
        if kind == "result":
            usage = event.get("usage") or {}
            log.fresh_in = int(usage.get("input_tokens") or 0)
            log.cache_write = int(usage.get("cache_creation_input_tokens") or 0)
            log.cache_read = int(usage.get("cache_read_input_tokens") or 0)
            log.api_ms = int(event.get("duration_api_ms") or 0)
            for model, used in (event.get("modelUsage") or {}).items():
                if isinstance(used, dict):
                    log.claude[model] += float(used.get("costUSD") or 0.0)
    return log


# --- adding up runs -------------------------------------------------------------------------------


@dataclass
class Sums:
    """What some agent runs came to: a step's, a job's or a project's."""

    runs: int = 0
    cost: float = 0.0
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    fresh_in: int = 0
    cache_write: int = 0
    cache_read: int = 0
    agent_seconds: float = 0.0
    api_ms: int = 0
    by_model: Counter[str] = field(default_factory=Counter)  # cost by golem's choice (haiku, opus…)
    claude: Counter[str] = field(default_factory=Counter)
    calls: Counter[str] = field(default_factory=Counter)
    failed: int = 0
    outcomes: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)  # golem's choice for each run: "haiku/medium"
    versions: set[str] = field(default_factory=set)

    def add(self, run: dict, log: Log) -> None:
        cost = run["cost_usd"] or 0.0
        self.runs += 1
        self.cost += cost
        self.turns += run["num_turns"] or 0
        self.tokens_in += run["input_tokens"] or 0
        self.tokens_out += run["output_tokens"] or 0
        self.agent_seconds += ledger.seconds(run["started_at"], run["ended_at"])
        self.by_model[(run["model"] or "").split("/")[0]] += cost
        self.outcomes.append(run["outcome"] or "running")
        self.engines.append(run["model"] or "")
        self.fresh_in += log.fresh_in
        self.cache_write += log.cache_write
        self.cache_read += log.cache_read
        self.api_ms += log.api_ms
        self.claude.update(log.claude)
        self.calls.update(log.calls)
        self.failed += log.failed
        if log.version:
            self.versions.add(log.version)

    def columns(self) -> dict[str, object]:
        other = sum(c for m, c in self.by_model.items() if m not in MODELS)
        return {
            "runs": self.runs, "cost_usd": _money(self.cost), "turns": self.turns, "tokens_in": self.tokens_in,
            "fresh_tokens_in": self.fresh_in, "cache_write_tokens": self.cache_write, "cache_read_tokens": self.cache_read,
            "tokens_out": self.tokens_out, "agent_minutes": _minutes(self.agent_seconds), "api_minutes": _minutes(self.api_ms / 1000),
            **{f"cost_{m}": _money(self.by_model.get(m, 0.0)) for m in MODELS}, "cost_other": _money(other),
            "claude_models": ", ".join(f"{m} {_money(c)}" for m, c in self.claude.most_common() if c),
            "tool_calls": sum(self.calls.values()), "tool_errors": self.failed,
            "tools": ", ".join(f"{t} {n}" for t, n in self.calls.most_common()), "claude_code": ", ".join(sorted(self.versions)),
        }  # fmt: skip


# --- what a checkout holds -------------------------------------------------------------------------


def repo(path: Path) -> dict[str, object]:
    """The size of what's in a project's checkout: its tracked files and their lines, those that look
    like tests, its commits and its kinds of file. Nothing if there's no checkout or git can't read it.
    Workers wrote these files, so a link is never followed and a huge file isn't read."""
    if not (path / ".git").exists():
        return {}  # not a checkout: git would find whatever repo is around it
    try:
        listed = subprocess.run(["git", "-C", str(path), "ls-files", "-z"], capture_output=True, check=True, timeout=60).stdout
        commits = subprocess.run(["git", "-C", str(path), "rev-list", "--count", "HEAD"], capture_output=True, check=False, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    names = [n for n in listed.decode(errors="replace").split("\0") if n]
    lines = test_files = test_lines = 0
    kinds: Counter[str] = Counter()
    for name in names:
        kinds[Path(name).suffix.lower() or Path(name).name] += 1
        found = path / name
        is_test = bool(TESTS.search(name))
        test_files += is_test
        try:
            if Path(name).name in LOCKFILES or found.is_symlink() or not found.is_file() or found.stat().st_size > BIGGEST:
                continue
            data = found.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        lines += n
        test_lines += n if is_test else 0
    return {
        "repo_files": len(names), "repo_lines": lines, "repo_test_files": test_files, "repo_test_lines": test_lines,
        "repo_commits": commits.decode().strip(), "repo_types": ", ".join(f"{k} {n}" for k, n in kinds.most_common(15)),
    }  # fmt: skip


# --- the rows ------------------------------------------------------------------------------------


def rows(store: Store, settings: Settings, slug: str, side: str) -> list[dict[str, object]]:
    """A project's rows: its own, then each job's (oldest first) followed by its steps'."""
    project = store.project(slug)
    if project is None:
        raise KeyError(slug)
    total, leds, asks, out = Sums(), [], 0, []
    jobs = list(reversed(store.project_jobs(slug)))
    for n, job in enumerate(jobs, 1):
        steps, runs = store.steps(job.id), store.runs(job.id)
        logs = {r["id"]: read_log(r["log_path"]) for r in runs}
        sums = Sums()
        for r in runs:
            sums.add(r, logs[r["id"]])
            total.add(r, logs[r["id"]])
        led = ledger.row(job, steps, runs)
        job_asks = sum(1 for s in steps if s.kind in plan.ASKS and s.started_at)
        leds.append(led)
        asks += job_asks
        out.append(_job_row(side, slug, n, job, steps, sums, led, job_asks))
        planners = {s.item.get("plan") for s in steps if s.item}
        for step in steps:
            mine = Sums()
            for r in runs:
                if r["step_id"] == step.id:
                    mine.add(r, logs[r["id"]])
            out.append(_step_row(side, slug, n, job, step, mine, planners))
    head = {
        "level": "project", "side": side, "project": slug, "name": project.name, "kind": project.tag,
        "mode": project.mode or "usual", "vacuum": int(project.vacuum), "state": "paused" if project.paused else "",
        "started": project.created_at, "waiting_minutes": _minutes(sum(r.waiting_seconds for r in leds)),
        "model": "/".join(x for x in (project.model, project.effort) if x), **total.columns(), "jobs": len(jobs),
        "fixes": sum(r.fixes for r in leds), "asks": asks, **_items(leds), **repo(settings.workspace_dir / slug),
        "url": settings.project_url(slug), "text": _flat(project.summary),
    }  # fmt: skip
    return [head, *out]


def _job_row(side: str, slug: str, n: int, job: Job, steps: list[Step], sums: Sums, led: ledger.Row, asks: int) -> dict:
    started = [s.started_at for s in steps if s.started_at]
    ended = [s.ended_at for s in steps if s.ended_at]
    return {
        "level": "job", "side": side, "project": slug, "job_n": n, "job": job.id, "name": plan.title(job), "kind": job.start,
        "mode": job.mode or "usual", "state": job.state, "started": min(started, default=""), "ended": max(ended, default=""),
        "wall_minutes": _minutes(led.wall_seconds), "waiting_minutes": _minutes(led.waiting_seconds), "workflow": job.workflow,
        **sums.columns(), "fixes": led.fixes, "asks": asks, **_items([led]), "text": _flat(job.request),
    }  # fmt: skip


def _step_row(side: str, slug: str, n: int, job: Job, step: Step, sums: Sums, planners: set) -> dict:
    item = step.item or {}
    if step.id in planners:
        role = "plan"
    elif item:
        role = "check" if step.kind == "check" else "item"
    else:
        role = "ask" if step.kind in plan.ASKS else ""
    took = ledger.seconds(step.started_at, step.ended_at)
    return {
        "level": "step", "side": side, "project": slug, "job_n": n, "job": job.id, "step_n": step.position, "step": step.id,
        "name": step.name, "kind": step.kind, "role": role, "mode": job.mode or "usual", "state": step.state,
        "started": step.started_at or "", "ended": step.ended_at or "", "wall_minutes": _minutes(took) if step.ended_at else "",
        "model": " ".join(sums.engines), "node": step.node_id, "visit": step.visit or "", "turn": step.turn, "port": step.port,
        "max_turns": step.max_turns or "", "outcomes": " ".join(sums.outcomes), **(sums.columns() if sums.runs else {}),
        **({"item_n": item.get("n", ""), "item_of": item.get("of", ""), "item_try": item.get("try", ""),
            "item_title": _flat(item.get("title")), "item_do": _flat(item.get("do")),
            "item_files": " ".join(item.get("files") or []), "item_check": _flat(item.get("check"))} if item else {}),
        "text": _flat(step.summary), "problem": _flat(step.problem or item.get("problem")), "error": _flat(step.error),
    }  # fmt: skip


def _items(leds: list[ledger.Row]) -> dict[str, object]:
    """How some jobs' checklists went, added up: nothing for jobs that planned none."""
    if not any(r.items or r.planning_cost for r in leds):
        return {}

    def total(name: str) -> float:
        return sum(getattr(r, name) for r in leds)

    return {
        "planning_cost_usd": _money(total("planning_cost")), "items_cost_usd": _money(total("items_cost")),
        "items": int(total("items")), "items_first_try": int(total("first_try")), "items_again": int(total("again")),
        "items_escalated": int(total("escalated")), "items_gave_up": int(total("gave_up")),
    }  # fmt: skip


def table(store: Store, settings: Settings, a: str, b: str) -> str:
    """The CSV: A's rows, then B's."""
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(COLUMNS), restval="", lineterminator="\n")
    w.writeheader()
    for side, slug in (("A", a), ("B", b)):
        w.writerows(rows(store, settings, slug, side))
    return out.getvalue()


def about() -> str:
    """What each column means, a line each: to give a model with the CSV."""
    return "".join(f"{name}: {meaning}\n" for name, meaning in COLUMNS.items())


def _money(usd: float) -> str:
    return f"{usd:.4f}"


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}"


def _flat(text: object) -> str:
    """On one line, so each row is one: a model reads the CSV as text."""
    return " ".join(str(text or "").split())


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m golem.compare", description="Two projects side by side in one CSV.")
    parser.add_argument("a", nargs="?", help="a project's slug")
    parser.add_argument("b", nargs="?", help="the other's")
    parser.add_argument("-o", "--out", help="where to write it (standard output if not given)")
    parser.add_argument("--columns", action="store_true", help="say what each column means instead")
    args = parser.parse_args(argv)
    if args.columns:
        text = about()
    else:
        if not args.a or not args.b:
            parser.error("give two projects' slugs")
        settings = settings or Settings.from_env()
        db = settings.state_dir / "golem.db"
        if not db.is_file():
            print(f"No golem database at {db}.", file=sys.stderr)
            return 2
        store = Store(db)
        missing = [s for s in (args.a, args.b) if store.project(s) is None]
        if missing:
            print(f"No project at {' or '.join(missing)}.", file=sys.stderr)
            return 2
        text = table(store, settings, args.a, args.b)
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
