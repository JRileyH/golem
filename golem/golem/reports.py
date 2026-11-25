"""Reports about golem itself: what got in the way of the workers golem runs, for whoever
improves golem. Golem's main users are its own workers, and what costs them most is often
something no person watching would notice.

Two sources, one table (store `reports`):

- `agent`: a worker files one with `golem report` (cli.py) when golem (its instructions, tools,
  environment or flow, not the project being built) blocked it, cost it real time or turns, or
  told it something wrong. The command appends a line to `$GOLEM_REPORTS`; golem reads them in
  after the run and adds where they came from (Scheduler.take_reports).
- `golem`: golem files its own incidents, spending nothing: a failed try at a step, the loop
  guard stopping a job, a merge with main that keeps conflicting, a run out of turns. The same
  incident again (its `fingerprint`) counts up on its report rather than adding one.

A triage job turns new reports into proposals for golem itself (triage.py).

Standard library only: the `golem` command imports it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

KINDS = ("bug", "instructions", "environment", "friction", "wish")
# What each kind is, for `golem report --help` and the prompts' standing order (prompts/report.md).
KIND_HELP = {
    "bug": "golem or one of its tools did something wrong",
    "instructions": "a prompt, skill or workflow was unclear, wrong or contradicted another",
    "environment": "something missing or broken where you run: a tool, access, network, ports",
    "friction": "it worked, but golem made it cost far more time or turns than it should have",
    "wish": "a tool or ability golem doesn't give you that would have saved real time",
}
IMPACTS = ("blocked", "costly", "minor")  # worst first
SOURCES = ("agent", "golem")
STATES = ("new", "triaged", "accepted", "dismissed")
MAX_PER_RUN = 2  # a worker's reports kept from one run: more is a sign it's reporting the wrong things
SUMMARY_CHARS = 200
DETAIL_CHARS = 4000  # of a worker's detail
INCIDENT_CHARS = 8000  # of golem's own, the end of it: the whole is in the step's log


class Invalid(ValueError):
    """A report golem won't take, and why: said back to the worker who filed it."""


def make(kind: str, impact: str, summary: str, detail: str = "") -> dict:
    """A worker's report as golem keeps it, checked. Raises Invalid."""
    if kind not in KINDS:
        raise Invalid(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    if impact not in IMPACTS:
        raise Invalid(f"impact must be one of {', '.join(IMPACTS)}, not {impact!r}")
    summary = " ".join(str(summary).split())
    if not summary:
        raise Invalid("say in one line what got in your way")
    return {"kind": kind, "impact": impact, "summary": summary[:SUMMARY_CHARS], "detail": str(detail).strip()[:DETAIL_CHARS]}


def parse(line: str) -> dict | None:
    """A line of `$GOLEM_REPORTS`, checked as `make` checks it; None if it isn't a report."""
    try:
        raw = json.loads(line)
        return make(raw["kind"], raw["impact"], raw["summary"], raw.get("detail") or "")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def worst(a: str, b: str) -> str:
    return min(a, b, key=lambda i: IMPACTS.index(i) if i in IMPACTS else len(IMPACTS))


# --- keeping secrets out ----------------------------------------------------------

SECRET_NAMES = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|KEY|CREDENTIALS?)$")
SECRET_SHAPES = re.compile(
    r"\b(ghp_[A-Za-z0-9]{20,}|gh[ousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,})"
)


def redact(text: str, env: Mapping[str, str]) -> str:
    """`text` with the values of secret-looking environment variables and token-shaped strings
    taken out: a report is shown on golem's pages and pasted into prompts."""
    for name, value in env.items():
        if SECRET_NAMES.search(name) and len(value) >= 8:
            text = text.replace(value, f"[{name}]")
    return SECRET_SHAPES.sub("[token]", text)


# --- golem's own incidents ----------------------------------------------------------

_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_REQUEST = re.compile(r"\b[0-9A-F]{3,}(:[0-9A-F]{2,}){2,}\b")  # GitHub's request ids, B7DC:1F1A:1A7EF1E:…
_HEX = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)
_NUMBER = re.compile(r"\d+")


def normalize(error: str, slug: str = "") -> str:
    """An error with what changes from one time to the next taken out (the project, times, ids,
    commits, numbers), so the same trouble reads the same wherever it happens."""
    text = error.replace(slug, "<slug>") if slug else error
    for pattern, stand_in in ((_TIME, "<time>"), (_UUID, "<id>"), (_REQUEST, "<id>"), (_HEX, "<sha>"), (_NUMBER, "<n>")):
        text = pattern.sub(stand_in, text)
    return " ".join(text.split()).lower()[:300]


def fingerprint(*parts: str) -> str:
    """What makes two incidents the same one."""
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]
