"""Reading agent run logs: each step a worker took, while it runs or after.

Run logs are Claude Code's stream-json events, compressed and flushed after every event,
so a run can be read while it goes. The scheduler and the status page's live tail use this.
"""

from __future__ import annotations

import json
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The part of a tool call's input that says what it does, in order of preference.
SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "skill", "description", "prompt")
HIDDEN_TOOLS = {"StructuredOutput"}  # the verdict, shown when the run ends


class Tail:
    """Reads a run log as it grows: the harness flushes the compressed stream after every event."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip
        self.partial = b""

    def events(self) -> list[dict]:
        """The events logged since the last call."""
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return []
        self.offset += len(data)
        *lines, self.partial = (self.partial + self.inflate.decompress(data)).split(b"\n")
        return [e for e in (_parse(line) for line in lines) if e]

    def modified(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime, UTC)
        except FileNotFoundError:
            return None


class Steps:
    """Turns a run's events into one line per step, timed from the start of the run."""

    def __init__(self, started: datetime):
        self.started = started
        self.pending: dict[str, tuple[str, datetime | None]] = {}  # tool calls still running
        self.hidden: set[str] = set()  # calls of HIDDEN_TOOLS, whose results aren't shown either
        self.last: datetime | None = None  # the latest step
        self.said = ""  # the latest thing the worker wrote
        self.ended = False  # the run's result is in

    def lines(self, events: list[dict]) -> list[str]:
        return [line for event in events for line in self.describe(event)]

    def describe(self, event: dict) -> list[str]:
        at = _time(event.get("timestamp"))
        self.last = at or self.last
        content = (event.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        found = []
        match event.get("type"):
            case "assistant":
                for block in blocks:
                    if block.get("type") == "text" and str(block.get("text", "")).strip():
                        self.said = _short(block["text"], 300)
                        found.append("says: " + self.said)
                    elif block.get("type") == "tool_use" and block.get("name") in HIDDEN_TOOLS:
                        self.hidden.add(block.get("id"))
                    elif block.get("type") == "tool_use":
                        call = f"{block.get('name')} {_summary(block.get('input'))}".strip()
                        self.pending[block.get("id")] = (call, at)
                        found.append("→ " + call)
            case "user":
                for block in blocks:
                    if block.get("type") == "tool_result" and block.get("tool_use_id") not in self.hidden:
                        self.pending.pop(block.get("tool_use_id"), None)
                        found.append(("✗ " if block.get("is_error") else "← ") + _output(block.get("content")))
            case "result":
                self.ended = True
                found.append(_ending(event))
            case "system":
                if event.get("subtype") == "init":
                    found.append(f"started on {event.get('model')}")
                elif event.get("subtype") == "compact_boundary":
                    found.append("context compacted")
            case "rate_limit_event":
                info = event.get("rate_limit_info") or {}
                if info.get("status") not in (None, "allowed"):
                    found.append(f"Claude usage: {info['status']} ({info.get('rateLimitType', 'limit')})")
        stamp = _clock(at - self.started) if at else ""
        nested = "┆ " if event.get("parent_tool_use_id") else ""  # a subagent's step
        return [f"{stamp:>8}  {nested}{line}" for line in found]

    def doing(self, now: datetime) -> str:
        """What the worker is on, for how long."""
        if self.pending:
            call, at = list(self.pending.values())[-1]
            return f"running {call}" + (f" for {span(now - at)}" if at else "")
        return "thinking" + (f" for {span(now - self.last)}" if self.last else "")


# --- formatting --------------------------------------------------------------------


def _summary(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in SUMMARY_KEYS:
        if isinstance(data.get(key), str) and data[key].strip():
            return _short(data[key], 200)
    return _short(json.dumps(data), 200)


def _output(content) -> str:
    if isinstance(content, list):
        content = "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    lines = [line for line in str(content or "").splitlines() if line.strip()]
    if not lines:
        return "(no output)"
    if len(lines) == 1:
        return _short(lines[0], 160)
    return f"{_short(lines[0], 80)} … {_short(lines[-1], 80)} ({len(lines)} lines)"


def _ending(event: dict) -> str:
    turns = _turns(event.get("num_turns"))
    if event.get("subtype") == "success" and not event.get("is_error"):
        verdict = event.get("structured_output") or {}
        said = f": {verdict['verdict']}: {_short(str(verdict.get('message', '')), 300)}" if verdict.get("verdict") else ""
        return f"■ finished after {turns}{said}"
    return f"■ stopped after {turns}: {event.get('subtype')} {_short(str(event.get('result') or ''), 200)}".rstrip()


def _turns(count) -> str:
    return "1 turn" if count == 1 else f"{count} turns"


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _clock(delta: timedelta) -> str:
    """Time into the run: 4:05, or 1:02:03."""
    minutes, seconds = divmod(max(int(delta.total_seconds()), 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def span(delta: timedelta) -> str:
    """A duration in words: 40s, 14m, 2h05m."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _parse(line: bytes) -> dict:
    try:
        event = json.loads(line)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}
