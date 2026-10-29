"""The live tail of a running agent step, for the status page.

Run logs hold everything a worker saw, and the page is public, so there are two views:
anyone sees what the worker says and which tools it uses on which files; signed in,
Riggy also sees commands and their output. Both have secret values masked.
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .watch import Tail, _clock, _output, _short, _time

KEEP = 40  # events kept per run, for the tail
# Inputs that say what a tool call is about without showing commands or what came back.
PUBLIC_KEYS = ("file_path", "path", "pattern", "skill", "description")
FULL_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "skill", "description")
SECRET_NAMES = re.compile(r"TOKEN|SECRET|PASSWORD|KEY", re.I)


@dataclass
class Line:
    at: str  # time into the run, "4:05"
    kind: str  # says | call | result | error
    text: str
    public: bool  # shown to anyone, or only once signed in
    nested: bool = False  # a subagent's


@dataclass
class Live:
    started: datetime
    tail: Tail
    lines: deque = field(default_factory=lambda: deque(maxlen=KEEP))


class LiveRuns:
    """Reads each running agent step's log as it grows, keeping its latest lines."""

    def __init__(self, env: dict[str, str] | None = None):
        self.runs: dict[int, Live] = {}
        env = os.environ if env is None else env
        self.secrets = sorted(
            {v for k, v in env.items() if SECRET_NAMES.search(k) and len(v) >= 8}, key=len, reverse=True
        )

    def lines(self, run: dict) -> list[Line]:
        live = self.runs.get(run["id"])
        if live is None:
            live = self.runs[run["id"]] = Live(_time(run["started_at"]), Tail(Path(run["log_path"])))
        for event in live.tail.events():
            live.lines.extend(self.describe(event, live.started))
        return list(live.lines)

    def transcript(self, run: dict) -> list[Line]:
        """Everything a run did, from the start: for a step's history."""
        started = _time(run["started_at"])
        return [line for event in Tail(Path(run["log_path"])).events() for line in self.describe(event, started)]

    def forget(self, keep: set[int]) -> None:
        """Drop runs that have ended."""
        for run_id in set(self.runs) - keep:
            del self.runs[run_id]

    def describe(self, event: dict, started: datetime) -> list[Line]:
        at = _time(event.get("timestamp"))
        stamp = _clock(at - started) if at and started else ""
        nested = bool(event.get("parent_tool_use_id"))
        content = (event.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        found = []
        for block in blocks:
            match event.get("type"), block.get("type"):
                case "assistant", "text" if str(block.get("text", "")).strip():
                    found.append(Line(stamp, "says", self.mask(_short(block["text"], 400)), True, nested))
                case "assistant", "tool_use" if block.get("name") != "StructuredOutput":
                    name, data = block.get("name", ""), block.get("input") or {}
                    public = _about(data, PUBLIC_KEYS)
                    full = _about(data, FULL_KEYS)
                    found.append(Line(stamp, "call", self.mask(f"{name} {public}".strip()), True, nested))
                    if full != public:
                        found.append(Line(stamp, "call", self.mask(f"{name} {full}".strip()), False, nested))
                case "user", "tool_result":
                    kind = "error" if block.get("is_error") else "result"
                    found.append(Line(stamp, kind, self.mask(_output(block.get("content"))), False, nested))
        return found

    def mask(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "•••")
        return text


def view(lines: list[Line], signed_in: bool, limit: int = 14) -> list[Line]:
    """The latest lines for this viewer. A call shown in full replaces its public version."""
    shown = []
    for line in lines:
        if line.public or signed_in:
            if signed_in and not line.public and line.kind == "call" and shown and shown[-1].kind == "call":
                shown[-1] = line
                continue
            shown.append(line)
    return shown[-limit:]


def _about(data: dict, keys: tuple[str, ...]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        if isinstance(data.get(key), str) and data[key].strip():
            return _short(data[key], 160)
    return ""
