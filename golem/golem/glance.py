"""A run at a glance: where its time went and what went wrong, read from its log.

Riggy should be able to tell what a worker is doing, and what's slowing it, without reading its
log: its time in a few buckets (tests, waiting, after its verdict, the rest of its work), what
it's on now, and a short list of things that shouldn't happen (a tool that isn't installed, a
command that hit its time limit, a run that went on after its verdict). Worked out once a run
ends and kept with it (store: runs.glance); a running step's, as its log grows (live.py).

Pure: events in (Claude Code's stream-json, as watch.py reads them), a Glance out.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta

from .watch import _short, _time, span

VERDICT_TOOL = "StructuredOutput"
WAITING_TOOLS = {"Monitor", "TaskOutput", "BashOutput", "TaskStop", "KillShell", "KillBash"}
TESTS = re.compile(
    r"\bbin/test\b|\bplaywright\b|\bvitest\b|\bjest\b|\bpytest\b|\b(?:npm|pnpm|yarn|bun) (?:run )?test\b|\bnpm t\b|\bgo test\b|\bcargo test\b|\bnode --test\b"
)
SLEEP = re.compile(r"^\s*sleep\s+[\d.]+\s*$")
MISSING = re.compile(r"([\w.+-]+): command not found|\bsh: \d+: ([\w.+-]+): not found")  # bash's words, and dash's
TIMED_OUT = re.compile(r"^Command timed out after|did not complete within its \S+ timeout", re.M)  # Claude Code's words, not a test's
PUBLIC_KEYS = ("file_path", "path", "pattern", "skill", "description")  # what anyone may see (live.py)
FULL_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "skill", "description")
AFTER_SHOWN = 30  # seconds after its verdict before it's worth a warning
CALL_CHARS = 90


@dataclass
class Glance:
    tests: float = 0.0  # seconds running tests (bin/test, Playwright, a test runner)
    waiting: float = 0.0  # sleeping, or watching or polling something it left running
    after: float = 0.0  # going on after Claude Code took its verdict
    work: float = 0.0  # everything else: reading, writing, building, thinking
    test_runs: int = 0
    slowest: str = ""  # its longest call, in full (signed in)
    slowest_public: str = ""
    slowest_seconds: float = 0.0
    missing: list[str] = field(default_factory=list)  # commands that weren't installed
    timeouts: int = 0  # commands that hit their time limit
    compacted: int = 0  # times its context was compacted
    limited: int = 0  # usage limit warnings
    out_of_turns: int = 0
    now: str = ""  # while it runs: the call it's on, in full ("" thinking)
    now_public: str = ""
    now_seconds: float = 0.0  # how long it's been on it (or thinking)
    running: bool = False

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in asdict(self).items() if v not in (0, 0.0, "", [], False)})

    @classmethod
    def from_json(cls, text: str | None) -> Glance | None:
        try:
            data = json.loads(text or "")
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def merged(glances: list[Glance]) -> Glance | None:
    """A step's runs as one (a run that ran out of turns and its wrapup, say)."""
    if not glances:
        return None
    out = Glance()
    for g in glances:
        for name in ("tests", "waiting", "after", "work", "test_runs", "timeouts", "compacted", "limited", "out_of_turns"):
            setattr(out, name, getattr(out, name) + getattr(g, name))
        out.missing = sorted(set(out.missing) | set(g.missing))
        if g.slowest_seconds > out.slowest_seconds:
            out.slowest, out.slowest_public, out.slowest_seconds = g.slowest, g.slowest_public, g.slowest_seconds
        if g.running:
            out.now, out.now_public, out.now_seconds, out.running = g.now, g.now_public, g.now_seconds, True
    return out


class Reading:
    """Reads a run's events as they come, and says at any point how it's gone so far."""

    def __init__(self, mask: Callable[[str], str] = lambda text: text):
        self.mask = mask
        self.started: datetime | None = None
        self.last: datetime | None = None  # the latest event
        self.verdict_at: datetime | None = None
        self.ended = False
        self.open: dict[str, tuple[str, str, str, datetime | None]] = {}  # calls under way: id -> (bucket, full, public, since)
        self.spans: list[tuple[float, float, str]] = []  # finished calls: (start, end, bucket), seconds into the run
        self.verdicts: set[str] = set()
        self.slowest: tuple[float, str, str] = (0.0, "", "")
        self.g = Glance()

    def read(self, event: dict) -> None:
        at = _time(event.get("timestamp"))
        if at:
            self.started = self.started or at
            self.last = at
        content = (event.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        nested = bool(event.get("parent_tool_use_id"))  # a subagent's: its time is in its parent's call
        match event.get("type"):
            case "assistant":
                for b in blocks:
                    if b.get("type") != "tool_use":
                        continue
                    name, data = str(b.get("name") or ""), b.get("input") if isinstance(b.get("input"), dict) else {}
                    if name == VERDICT_TOOL:
                        self.verdicts.add(str(b.get("id")))
                    elif not nested:
                        bucket = _bucket(name, data)
                        self.g.test_runs += bucket == "tests"
                        full, public = self.mask(_call(name, data, FULL_KEYS)), self.mask(_call(name, data, PUBLIC_KEYS))
                        self.open[str(b.get("id"))] = (bucket, full, public, at)
            case "user":
                for b in blocks:
                    if b.get("type") != "tool_result":
                        continue
                    key, text = str(b.get("tool_use_id")), _text(b.get("content"))
                    if key in self.verdicts and not b.get("is_error") and self.verdict_at is None:
                        self.verdict_at = at
                    self.g.missing = sorted(set(self.g.missing) | {a or b for a, b in MISSING.findall(text)})
                    self.g.timeouts += bool(TIMED_OUT.search(text))
                    if key in self.open:
                        bucket, full, public, since = self.open.pop(key)
                        if since and at:
                            self._span(since, at, bucket, full, public)
            case "system" if event.get("subtype") == "compact_boundary":
                self.g.compacted += 1
            case "rate_limit_event" if ((event.get("rate_limit_info") or {}).get("status") not in (None, "allowed")):
                self.g.limited += 1
            case "result":
                self.ended = True
                self.g.out_of_turns += event.get("subtype") == "error_max_turns"

    def _span(self, since: datetime, until: datetime, bucket: str, full: str, public: str) -> None:
        start, end = self._at(since), self._at(until)
        self.spans.append((start, end, bucket))
        if end - start > self.slowest[0]:
            self.slowest = (end - start, full, public)

    def _at(self, at: datetime) -> float:
        return (at - self.started).total_seconds() if self.started else 0.0

    def glance(self, now: datetime | None = None) -> Glance:
        """How it's gone: up to `now` for a run still going, else up to its last event."""
        g = Glance(**{f.name: getattr(self.g, f.name) for f in fields(Glance)})
        g.missing = list(self.g.missing)
        if self.started is None:
            return g
        end_at = now if now and not self.ended else self.last
        end = self._at(end_at) if end_at else 0.0
        spans = list(self.spans)
        for bucket, _, _, since in self.open.values():  # calls still going count up to now
            if since:
                spans.append((self._at(since), end, bucket))
        cut = self._at(self.verdict_at) if self.verdict_at else end
        tests = _union([(a, min(b, cut)) for a, b, k in spans if k == "tests" and a < cut])
        busy = _union([(a, min(b, cut)) for a, b, k in spans if k in ("tests", "waiting") and a < cut])
        g.tests, g.waiting = tests, max(busy - tests, 0.0)
        g.after = max(end - cut, 0.0) if self.verdict_at else 0.0
        g.work = max(cut - busy, 0.0)
        g.slowest_seconds, g.slowest, g.slowest_public = self.slowest
        if now and not self.ended:
            g.running = True
            if self.open:
                _, full, public, since = list(self.open.values())[-1]
                g.now, g.now_public = full, public
                g.now_seconds = (now - since).total_seconds() if since else 0.0
            elif self.last:
                g.now_seconds = (now - self.last).total_seconds()
            if g.now_seconds > g.slowest_seconds and g.now:
                g.slowest_seconds, g.slowest, g.slowest_public = g.now_seconds, g.now, g.now_public
        return g


def of(events: list[dict], mask: Callable[[str], str] = lambda text: text) -> Glance:
    """A finished run's glance, from all its events."""
    reading = Reading(mask)
    for event in events:
        reading.read(event)
    return reading.glance()


# --- in words ----------------------------------------------------------------------------


def times(g: Glance) -> str:
    """Where its time went, the buckets that had any: "tests 3m (2 runs) · after its verdict 4m · work 2m"."""
    parts = []
    if g.tests >= 1:
        parts.append(f"tests {_span(g.tests)}" + (f" ({g.test_runs} runs)" if g.test_runs > 1 else ""))
    if g.waiting >= 1:
        parts.append(f"waiting {_span(g.waiting)}")
    if g.after >= 1:
        parts.append(f"after its verdict {_span(g.after)}")
    if g.work >= 1:
        parts.append(f"work {_span(g.work)}")
    return " · ".join(parts)


def warnings(g: Glance) -> list[str]:
    """What went wrong that shouldn't have, most costly first."""
    found = []
    if g.after >= AFTER_SHOWN:
        found.append(f"went on {_span(g.after)} after its verdict")
    if g.out_of_turns:
        found.append("ran out of turns")
    if g.timeouts:
        found.append(f"{g.timeouts} command{'s' if g.timeouts > 1 else ''} hit the time limit")
    if g.missing:
        found.append(f"not installed: {', '.join(g.missing[:6])}")
    if g.limited:
        found.append("hit the usage limit")
    if g.compacted:
        found.append("its context filled and was compacted" + (f" ×{g.compacted}" if g.compacted > 1 else ""))
    return found


def doing(g: Glance, signed_in: bool) -> str:
    """While it runs: "now: npx playwright test for 2m", or "now: thinking for 20s"."""
    if not g.running:
        return ""
    what = (g.now if signed_in else g.now_public) or ("thinking" if not g.now else "a tool")
    return f"now: {what} for {_span(g.now_seconds)}"


def slowest(g: Glance, signed_in: bool) -> str:
    what = g.slowest if signed_in else g.slowest_public
    return f"slowest: {what} ({_span(g.slowest_seconds)})" if what and g.slowest_seconds >= 60 else ""


# --- reading calls ---------------------------------------------------------------------


def _bucket(name: str, data: dict) -> str:
    if name in WAITING_TOOLS:
        return "waiting"
    if name == "Bash":
        command = str(data.get("command") or "")
        if SLEEP.match(command):
            return "waiting"
        if TESTS.search(command):
            return "tests"
    return "work"


def _call(name: str, data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        if isinstance(data.get(key), str) and data[key].strip():
            return _short(data[key], CALL_CHARS) if name == "Bash" and key == "command" else _short(f"{name} {data[key]}", CALL_CHARS)
    return name


def _text(content) -> str:
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def _union(spans: list[tuple[float, float]]) -> float:
    total, reach = 0.0, None
    for a, b in sorted(s for s in spans if s[1] > s[0]):
        if reach is None or a > reach:
            total += b - a
            reach = b
        elif b > reach:
            total += b - reach
            reach = b
    return total


def _span(seconds: float) -> str:
    return span(timedelta(seconds=seconds))
