"""OpenCode (opencode.ai) as a worker: any model its providers have, its free ones included.

A second harness beside Claude Code (harness.py), so trials can pit agents against each other.
`opencode run --format json` prints an event a line: each step's end (its tokens and cost), each
tool call once it's done, and what it writes. golem logs them as Claude Code's stream-json (an
assistant's tool_use, the user's tool_result, a closing result), so whatever reads a run's log
(watch, glance, live, compare) reads it as it would a Claude Code run's. A call shows once it's
done, not while it runs: OpenCode says nothing of a call before then.

OpenCode has no --json-schema, so the verdict is asked for as a JSON object at the end of its
reply, and asked for once more, tools off, if it isn't there. --max-turns is its agent's `steps`:
on its last step its tools are off and it says what's left, which is running out of turns.

What it reads is the project's (AGENTS.md, CLAUDE.md), never Claude Code's own files in the home
directory, and its sessions are the job's own (under GOLEM_TMP), so a wrapup can resume one.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .harness import (
    TOKEN_KEYS,
    VERDICT_SCHEMA,
    VERDICTS,
    _end,
    _parse,
    interpret,
    stop_leftovers,
)

FREE = "-free"  # how OpenCode Zen names its free models: opencode/<name>-free
# OpenCode's tools by Claude Code's names, so glance and the live tail know them (Bash's command
# is what tells a test run from other work).
TOOLS = {
    "bash": "Bash", "read": "Read", "write": "Write", "edit": "Edit", "multiedit": "MultiEdit", "patch": "Edit",
    "apply_patch": "Edit", "glob": "Glob", "grep": "Grep", "list": "LS", "webfetch": "WebFetch",
    "websearch": "WebSearch", "todowrite": "TodoWrite", "todoread": "TodoRead", "task": "Task", "skill": "Skill",
}  # fmt: skip
SAID_CHARS = 2000  # of its last words, kept in the closing result


class OpenCodeHarness:
    def __init__(self, binary: str = "opencode"):
        self.binary = binary

    async def free_models(self) -> list[str]:
        return await free_models(self.binary)

    async def run(self, *, cwd, prompt, model, max_turns, log_path, env, effort=None, resume=None, schema=None, label=""):
        wanted = schema or VERDICT_SCHEMA
        log_path.parent.mkdir(parents=True, exist_ok=True)
        seen = Seen(model)
        with gzip.open(log_path, "wt") as out:
            await self._call(seen, out, cwd, env, model, effort, max_turns, ask(prompt, wanted), resume)
            if not seen.error and seen.verdict(wanted, schema is None) is None and seen.steps < max_turns:
                await self._call(seen, out, cwd, env, model, effort, 1, again(wanted), seen.session_id)  # one step: tools off
            result = seen.result(wanted, schema is None, max_turns)
            out.write(json.dumps(result) + "\n")
        return interpret(result, seen.session_id, seen.returncode, seen.stderr, verdict_expected=schema is None)

    async def _call(self, seen: Seen, out, cwd: Path, env: dict, model: str, effort: str | None, steps: int, prompt: str, resume: str | None) -> None:
        args = [self.binary, "run", "--format", "json", "--auto", "-m", model]
        if effort:
            args += ["--variant", effort]  # a model with no such variant ignores it
        if resume:
            args += ["--session", resume]
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=environment(env, steps),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,  # a line can carry a whole file it read
            start_new_session=True,  # its own process group, so what it starts can be stopped with it
        )
        try:
            proc.stdin.write(prompt.encode())  # stdin, not an argument: Linux caps one argument at 128 kB
            await proc.stdin.drain()
            proc.stdin.close()
            stderr_task = asyncio.create_task(proc.stderr.read())
            while raw := await proc.stdout.readline():
                for line in seen.read(_parse(raw.decode(errors="replace"))):
                    out.write(json.dumps(line) + "\n")
                    out.flush()  # so the live tail can read a run while it goes
            await proc.wait()
            try:
                seen.stderr = (await asyncio.wait_for(stderr_task, 10)).decode(errors="replace")
            except TimeoutError:
                stderr_task.cancel()
            seen.returncode = proc.returncode
        except asyncio.CancelledError:
            if proc.returncode is None:
                await _end(proc)
            raise
        finally:
            stop_leftovers(proc.pid, env.get("GOLEM_TMP", ""))


def environment(env: dict[str, str], steps: int) -> dict[str, str]:
    """A worker's environment, for OpenCode: at most `steps` model calls, nothing shared or
    updated, the project's CLAUDE.md read as AGENTS.md is (Claude Code's own files in the home
    directory never), and its sessions and logs kept with the job."""
    home = Path(env.get("GOLEM_TMP") or tempfile.gettempdir()) / "opencode"
    config = {"agent": {"build": {"steps": steps}}, "instructions": ["CLAUDE.md"], "share": "disabled", "autoupdate": False}
    return {
        **env,
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "OPENCODE_CONFIG_CONTENT": json.dumps(config),
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_SHARE": "1",
    }


def ask(prompt: str, schema: dict) -> str:
    return f"{prompt}\n\n## Your verdict\n\nWhen you're done, end your reply with your verdict: {_shape(schema)}"


def again(schema: dict) -> str:
    return f"Your reply didn't end with your verdict. Reply with only your verdict: {_shape(schema)}"


def _shape(schema: dict) -> str:
    """The verdict asked for: an example to fill in, then the schema it fits. A weak model handed
    the schema alone answers with the schema, its values put in for the properties' rules."""
    example = json.dumps({k: _example(v) for k, v in (schema.get("properties") or {}).items()})
    return (
        f"one JSON object on a line of its own, with nothing after it, like this one with your own values:\n\n{example}\n\n"
        f"It fits this JSON schema (send an object like the one above, not the schema):\n\n{json.dumps(schema)}\n"
    )


def _example(rule) -> object:
    rule = rule if isinstance(rule, dict) else {}
    if rule.get("enum"):
        return rule["enum"][0]
    match rule.get("type"):
        case "boolean":
            return True
        case "integer" | "number":
            return 0
        case "array":
            return [_example(rule.get("items"))]
        case "object":
            return {k: _example(v) for k, v in (rule.get("properties") or {}).items()}
    return str(rule.get("description") or "...")[:80]


@dataclass
class Seen:
    """What golem has read of a run, over its calls: its session, steps, tokens, cost and last words."""

    model: str
    session_id: str | None = None
    steps: int = 0
    cost: float = 0.0
    tokens: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOKEN_KEYS, 0))
    said: str = ""  # the latest thing it wrote
    error: str = ""
    started: bool = False
    returncode: int | None = None
    stderr: str = ""

    def read(self, event: dict) -> list[dict]:
        """Claude Code's events for one of OpenCode's."""
        self.session_id = event.get("sessionID") or self.session_id
        at = _when(event.get("timestamp"))
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        found = []
        if not self.started and event:
            self.started = True
            found.append({"type": "system", "subtype": "init", "model": self.model, "session_id": self.session_id, "timestamp": at})
        match event.get("type"):
            case "step_finish":
                self.steps += 1
                usage = _usage(part.get("tokens") or {})
                for key in TOKEN_KEYS:
                    self.tokens[key] += usage[key]
                self.cost += float(part.get("cost") or 0)
                message = {"id": part.get("messageID"), "model": self.model, "usage": usage, "content": []}
                found.append({"type": "assistant", "message": message, "session_id": self.session_id, "timestamp": at})
            case "tool_use":
                found += _call(part, self.session_id)
            case "text" if str(part.get("text") or "").strip():
                self.said = str(part["text"])
                message = {"id": part.get("messageID"), "model": self.model, "content": [{"type": "text", "text": self.said}]}
                found.append({"type": "assistant", "message": message, "session_id": self.session_id, "timestamp": at})
            case "error":
                error = event.get("error") or {}
                self.error = str((error.get("data") or {}).get("message") or error.get("name") or "OpenCode failed")
        return found

    def verdict(self, schema: dict, expected: bool) -> dict | None:
        return fitted(last_object(self.said), schema, expected)

    def result(self, schema: dict, expected: bool, max_turns: int) -> dict:
        """The run's closing result, as Claude Code would have written it."""
        result = {
            "type": "result", "session_id": self.session_id, "num_turns": self.steps, "total_cost_usd": round(self.cost, 6),
            "usage": dict(self.tokens), "modelUsage": {self.model: {"costUSD": round(self.cost, 6)}}, "result": self.said[-SAID_CHARS:],
            "harness": "opencode",
        }  # fmt: skip
        if (verdict := self.verdict(schema, expected)) is not None:
            return {**result, "subtype": "success", "is_error": False, "structured_output": verdict}
        if self.error or not self.steps:
            why = self.error or f"opencode exited {self.returncode} without a reply: {self.stderr.strip()[-500:]}"
            return {**result, "subtype": "error_during_execution", "is_error": True, "result": why}
        if self.steps >= max_turns:
            return {**result, "subtype": "error_max_turns", "is_error": True}
        return {**result, "subtype": "success", "is_error": False, "structured_output": None}


def _call(part: dict, session_id: str | None) -> list[dict]:
    """A finished tool call as Claude Code logs one: the assistant's tool_use when it started,
    the user's tool_result when it ended."""
    state = part.get("state") or {}
    times = state.get("time") or {}
    key = str(part.get("callID") or part.get("id") or "")
    name = TOOLS.get(str(part.get("tool") or ""), str(part.get("tool") or "tool").capitalize())
    data = {_snake(k): v for k, v in (state.get("input") or {}).items()} if isinstance(state.get("input"), dict) else {}
    exit_code = (state.get("metadata") or {}).get("exit")
    failed = state.get("status") == "error" or exit_code not in (None, 0)
    output = str(state.get("output") or state.get("error") or "")
    use = {"type": "tool_use", "id": key, "name": name, "input": data}
    result = {"type": "tool_result", "tool_use_id": key, "content": output, "is_error": failed}
    return [
        {"type": "assistant", "message": {"id": part.get("messageID"), "content": [use]}, "session_id": session_id, "timestamp": _when(times.get("start"))},
        {"type": "user", "message": {"content": [result]}, "session_id": session_id, "timestamp": _when(times.get("end"))},
    ]


def _usage(tokens: dict) -> dict[str, int]:
    cache = tokens.get("cache") or {}
    return {
        "input_tokens": int(tokens.get("input") or 0),
        "cache_creation_input_tokens": int(cache.get("write") or 0),
        "cache_read_input_tokens": int(cache.get("read") or 0),
        "output_tokens": int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
    }


def last_object(text: str) -> dict | None:
    """The JSON object a reply ends with (in a ``` fence or not), if it ends with one."""
    tail = re.sub(r"\s*```\s*$", "", text.rstrip())
    decoder = json.JSONDecoder()
    at = len(tail)
    while (at := tail.rfind("{", 0, at)) >= 0:
        try:
            found, end = decoder.raw_decode(tail, at)
        except ValueError:
            continue
        if isinstance(found, dict) and not tail[end:].strip():
            return found
    return None


def fitted(found: dict | None, schema: dict, expected: bool) -> dict | None:
    """The object if it fits the schema's top level (what's required is there, an enum's value is
    one of its values), with what the schema doesn't know dropped if it allows nothing more."""
    if not isinstance(found, dict):
        return None
    if found.get("type") == "object" and isinstance(found.get("properties"), dict):  # its values put in the schema's shape
        found = found["properties"]
    properties = schema.get("properties") or {}
    if any(key not in found for key in schema.get("required") or ()):
        return None
    for key, rule in properties.items():
        if key in found and isinstance(rule, dict) and "enum" in rule and found[key] not in rule["enum"]:
            return None
    if expected and found.get("verdict") not in ((properties.get("verdict") or {}).get("enum") or VERDICTS):
        return None
    if schema.get("additionalProperties") is False:
        found = {k: v for k, v in found.items() if k in properties}
    return found


def _when(ms) -> str | None:
    try:
        return datetime.fromtimestamp(float(ms) / 1000, UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()  # filePath -> file_path, as Claude Code's tools say


async def free_models(binary: str = "opencode", env: dict[str, str] | None = None) -> list[str]:
    """OpenCode Zen's free models as they are now: they come and go, so the list is fetched fresh,
    never kept."""
    proc = await asyncio.create_subprocess_exec(
        binary, "models", "opencode", "--refresh", env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    return [line.strip() for line in out.decode(errors="replace").splitlines() if line.strip().endswith(FREE)]
