"""Worker adapters. An agent step is one headless run that ends with a JSON verdict.

`ClaudeHarness` runs the Claude Code CLI; `FakeHarness` returns scripted verdicts,
for tests and for exercising the bot without spending usage.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
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
    def __init__(self, binary: str = "claude"):
        self.binary = binary

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
        )
        result: dict | None = None
        session_id = resume
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(proc.stderr.read())
            with gzip.open(log_path, "wt") as out:
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace")
                    out.write(line)
                    out.flush()  # so the live tail can read a run while it goes
                    event = _parse(line)
                    if event.get("session_id"):
                        session_id = event["session_id"]
                    if event.get("type") == "result":
                        result = event
            await proc.wait()
            stderr = (await stderr_task).decode(errors="replace")
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 10)
                except TimeoutError:
                    proc.kill()
            raise
        return interpret(result, session_id, proc.returncode, stderr, verdict_expected=schema is None)


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
    """Scripted results. With no script, every step answers `done`, so a job walks its steps."""

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
        if schema is not None:
            raise RuntimeError("FakeHarness needs a script for structured calls")
        verdict = {"verdict": "done", "message": f"(fake) {label} done"}
        return RunResult("verdict", verdict=verdict, session_id=f"fake-{len(self.calls)}", usage={"num_turns": 1})
