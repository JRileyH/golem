"""Worker adapters. An agent step is one headless run that ends with a JSON verdict.

`ClaudeHarness` runs the Claude Code CLI; `FakeHarness` returns scripted verdicts,
for tests and for exercising the bot without spending usage.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

# A worker can only say its step is done, or that it can't go on without Riggy. What
# happens next is up to golem: the job's steps are in its database, not in the worker's hands.
VERDICTS = ("done", "blocked")
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"enum": list(VERDICTS)},
        "message": {"type": "string", "description": "One or two sentences for Riggy, plain text"},
        "link": {"type": "string", "description": "URL the message is about, if any"},
    },
    "required": ["verdict", "message"],
    "additionalProperties": False,
}
# A step that ran out of turns gets a few more to commit and say whether it finished.
WRAPUP_SCHEMA = {
    "type": "object",
    "properties": {
        "finished": {"type": "boolean", "description": "Whether the step's work is complete"},
        "message": {"type": "string", "description": "What was done; if unfinished, what's left"},
    },
    "required": ["finished", "message"],
    "additionalProperties": False,
}


def wrapup_schema(step_schema: dict | None) -> dict:
    """The wrapup's schema for a step whose verdict carries more (an options step's options):
    those fields too, optional, so a step that finished as its turns ran out doesn't lose them."""
    extra = {k: v for k, v in ((step_schema or {}).get("properties") or {}).items() if k not in VERDICT_SCHEMA["properties"]}
    if not extra:
        return WRAPUP_SCHEMA
    return {**WRAPUP_SCHEMA, "properties": {**WRAPUP_SCHEMA["properties"], **extra}}


VERDICT_TOOL = "StructuredOutput"  # how Claude Code takes a run's verdict, as a tool call
# Seconds a run may go on once Claude Code has taken its verdict, before golem ends it. A run
# normally ends straight after; one that doesn't is waiting on something it left going (a watch
# on a background command woke it again and again to re-send the same verdict, for minutes).
VERDICT_GRACE = 60
# Tools a worker doesn't get: Monitor's watches keep a headless run alive after its verdict.
DISALLOWED_TOOLS = ("Monitor",)

# How Claude Code reports running out of subscription usage. Matched loosely on purpose:
# a false positive only parks a job for a while; a miss burns retries.
LIMIT_RE = re.compile(r"usage limit|limit reached|limit will reset|resets at|rate.?limit|out of (extra )?usage", re.I)


@dataclass
class RunResult:
    outcome: str  # verdict | max_turns | limit | error
    verdict: dict | None = None
    session_id: str | None = None
    error: str = ""
    usage: dict = field(default_factory=dict)  # num_turns, cost_usd, input_tokens, output_tokens


class Harness(Protocol):
    async def run(
        self,
        *,
        cwd: Path,
        prompt: str,
        model: str,
        max_turns: int,
        log_path: Path,
        env: dict[str, str],
        effort: str | None = None,
        resume: str | None = None,
        schema: dict | None = None,
        label: str = "",
    ) -> RunResult: ...


class ClaudeHarness:
    def __init__(self, binary: str = "claude", grace: float = VERDICT_GRACE):
        self.binary = binary
        self.grace = grace

    async def run(self, *, cwd, prompt, model, max_turns, log_path, env, effort=None, resume=None, schema=None, label=""):
        args = [
            self.binary,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--max-turns", str(max_turns),
            # Nobody is there to approve anything; the golem container is the sandbox.
            "--permission-mode", "bypassPermissions",
            "--json-schema", json.dumps(schema or VERDICT_SCHEMA),
            "--disallowedTools", ",".join(DISALLOWED_TOOLS),
        ]  # fmt: skip
        if effort:
            args += ["--effort", effort]
        if resume:
            args += ["--resume", resume]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,  # one stream-json line can carry a whole file
            start_new_session=True,  # its own process group, so what it starts can be stopped with it
        )
        try:
            return await self._talk(proc, prompt, log_path, resume, schema)
        finally:
            stop_leftovers(proc.pid, env.get("GOLEM_TMP", ""))

    async def _talk(self, proc, prompt: str, log_path: Path, resume: str | None, schema: dict | None) -> RunResult:
        result: dict | None = None
        session_id = resume
        taken = Taken()
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(proc.stderr.read())
            with gzip.open(log_path, "wt") as out:
                while True:
                    try:  # once its verdict is in, a run has `grace` seconds to end
                        raw = await asyncio.wait_for(proc.stdout.readline(), self.grace if taken.verdict is not None else None)
                    except TimeoutError:
                        if result is None:  # Claude Code hasn't said what the run cost: what golem saw stands in
                            log.warning("run %s went on %ss after its verdict: ended it (its cost is unknown)", session_id, self.grace)
                            result = taken.result(session_id)
                        await _end(proc)
                        break
                    if not raw:
                        break
                    line = raw.decode(errors="replace")
                    out.write(line)
                    out.flush()  # so the live tail can read a run while it goes
                    event = _parse(line)
                    if event.get("session_id"):
                        session_id = event["session_id"]
                    if event.get("type") == "result":
                        result = event
                    taken.read(event)
            await proc.wait()
            try:  # what it started may hold stderr open: it's stopped once the run's over (stop_leftovers)
                stderr = (await asyncio.wait_for(stderr_task, 10)).decode(errors="replace")
            except TimeoutError:
                stderr_task.cancel()
                stderr = ""
        except asyncio.CancelledError:
            if proc.returncode is None:
                await _end(proc)
            raise
        return interpret(result, session_id, proc.returncode, stderr, verdict_expected=schema is None)


async def _end(proc) -> None:
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except TimeoutError:
        proc.kill()


@dataclass
class Taken:
    """A run's verdict once Claude Code has taken it (its StructuredOutput call answered without an
    error), and its turns and tokens so far: enough to stand in for the `result` event when golem
    ends a run that went on after its verdict."""

    verdict: dict | None = None
    calls: dict[str, dict] = field(default_factory=dict)  # verdicts sent, not yet answered: by call id
    usage: dict[str, dict] = field(default_factory=dict)  # each model turn's usage: by message id

    def read(self, event: dict) -> None:
        message = event.get("message") or {}
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if event.get("type") == "assistant" and not event.get("parent_tool_use_id"):
            if message.get("id") and isinstance(message.get("usage"), dict):
                self.usage[message["id"]] = message["usage"]
            for b in blocks:
                if b.get("type") == "tool_use" and b.get("name") == VERDICT_TOOL and isinstance(b.get("input"), dict):
                    self.calls[str(b.get("id"))] = b["input"]
        elif event.get("type") == "user":
            for b in blocks:
                if b.get("type") == "tool_result" and str(b.get("tool_use_id")) in self.calls and not b.get("is_error"):
                    self.verdict = self.calls.pop(str(b.get("tool_use_id")))

    def result(self, session_id: str | None) -> dict:
        totals = {k: sum(int(u.get(k) or 0) for u in self.usage.values()) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
        return {"type": "result", "subtype": "success", "is_error": False, "session_id": session_id, "num_turns": len(self.usage),
                "total_cost_usd": None, "usage": totals, "structured_output": self.verdict}  # fmt: skip


def stop_leftovers(group: int, tmp: str) -> None:
    """Stop what a run left running (a dev server it forgot, a watcher), so it holds no port or
    memory once the step is over: its process group, and on Linux any process whose environment
    has the run's GOLEM_TMP, for one that started a session of its own. Best effort."""
    try:
        os.killpg(group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    if not tmp:
        return
    marker = f"GOLEM_TMP={tmp}".encode()
    for proc in Path("/proc").glob("[0-9]*"):  # no /proc on a Mac: the process group has to do
        try:
            if int(proc.name) != os.getpid() and marker in (proc / "environ").read_bytes().split(b"\0"):
                os.kill(int(proc.name), signal.SIGTERM)
        except (OSError, ValueError):
            continue


def interpret(
    result: dict | None, session_id: str | None, returncode: int | None, stderr: str = "", verdict_expected: bool = True
) -> RunResult:
    """Read Claude Code's final `result` event. `verdict` holds the structured output: a step
    verdict normally, or whatever a custom schema asked for (`verdict_expected=False`)."""
    if result is None:
        tail = stderr.strip()[-500:]
        if LIMIT_RE.search(tail):
            return RunResult("limit", session_id=session_id, error=tail)
        return RunResult("error", session_id=session_id, error=f"claude exited {returncode} without a result: {tail}")
    usage = {
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "input_tokens": _tokens(result, "input"),
        "output_tokens": _tokens(result, "output"),
    }
    session_id = result.get("session_id") or session_id
    if result.get("subtype") == "error_max_turns":
        return RunResult("max_turns", session_id=session_id, usage=usage)
    text = str(result.get("result") or "")
    if result.get("is_error") or result.get("subtype") != "success":
        if result.get("api_error_status") == 429 or LIMIT_RE.search(text):
            return RunResult("limit", session_id=session_id, error=text[:500], usage=usage)
        return RunResult("error", session_id=session_id, error=(text or str(result.get("subtype")))[:500], usage=usage)
    verdict = result.get("structured_output")
    if not isinstance(verdict, dict) or (verdict_expected and verdict.get("verdict") not in VERDICTS):
        return RunResult("error", session_id=session_id, error="no verdict in the result", usage=usage)
    return RunResult("verdict", verdict=verdict, session_id=session_id, usage=usage)


def _parse(line: str) -> dict:
    try:
        event = json.loads(line)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def _tokens(result: dict, kind: str) -> int | None:
    usage = result.get("usage") or {}
    if kind == "output":
        return usage.get("output_tokens")
    parts = [usage.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    return sum(p for p in parts if p) if any(parts) else None


@dataclass
class FakeCall:
    cwd: Path
    prompt: str
    model: str
    max_turns: int
    resume: str | None
    label: str
    env: dict[str, str]
    effort: str | None = None
    schema: dict | None = None


class FakeHarness:
    """Scripted results. With no script, every step answers `done`, so a job walks its steps (an
    options step with two placeholder options); other structured calls need a script."""

    def __init__(self, script: list[RunResult | dict] | Callable[[FakeCall], RunResult] | None = None):
        self.script = script
        self.calls: list[FakeCall] = []

    async def run(self, *, cwd, prompt, model, max_turns, log_path, env, effort=None, resume=None, schema=None, label=""):
        call = FakeCall(cwd, prompt, model, max_turns, resume, label, env, effort, schema)
        self.calls.append(call)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(log_path, "wt") as out:
            out.write(json.dumps({"type": "fake", "label": label}) + "\n")
        if callable(self.script):
            return self.script(call)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, dict):
                return RunResult("verdict", verdict=item, session_id=f"fake-{len(self.calls)}", usage={"num_turns": 1})
            return item
        verdict = {"verdict": "done", "message": f"(fake) {label} done"}
        if schema is not None and "options" in schema.get("properties", {}) and not resume:
            # A step that makes options to pick from: two placeholders, so a job walks on. A design
            # round has nothing to show without a model, so it says there's nothing to design.
            made = [{"title": f"(fake) {x}", "description": f"(fake) option {x}", "files": []} for x in "AB"]
            more = {"skip": "(fake) no design without a model"} if "doc" in schema["properties"] else {"options": made}
            return RunResult("verdict", verdict={**verdict, **more}, session_id=f"fake-{len(self.calls)}", usage={"num_turns": 1})
        if schema is not None:
            raise RuntimeError("FakeHarness needs a script for structured calls")
        return RunResult("verdict", verdict=verdict, session_id=f"fake-{len(self.calls)}", usage={"num_turns": 1})
