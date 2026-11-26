"""A job drawn small: its route through its workflow, as a strip of chips.

The blocks it has been through, in order, lit by how each went, then the ones likely next,
faded. A block it came back to (a loop) is one chip that says how many times; a container
(Ship, a design round) is one chip that fills as the steps inside it run. Golem's bookkeeping
(counters, conditions) and the closing alert aren't shown, nor catching up with main unless
it's under way or failed. Each chip opens its block's sheet.

Pure: from a job's steps (plan.planned: its trace and what's predicted) and its copy of its
workflow (for containers' names). pages.route_strip and route_list draw it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import plan
from .store import Job, Step

# A chip's state, from its latest step's (or for a container, from all of its steps).
STATES = {"running": "now", "waiting": "waiting", "pending": "next", "done": "done", "failed": "failed", "skipped": "skipped"}
HIDDEN = ("alert",)  # the closing alert says nothing the route doesn't
SHOWN_WHEN = {"sync": ("running", "failed"), "resolve": ("running", "waiting", "failed")}  # golem's own catch-ups: only while they matter


@dataclass
class Chip:
    key: str  # its block's id at the workflow's top level (a container's for steps inside it)
    label: str
    state: str  # now | waiting | next | done | failed | skipped | todo
    steps: list[Step] = field(default_factory=list)  # its visits (a container's: every step inside it), in order
    container: bool = False
    predicted: bool = False  # not reached yet: what's likely next

    @property
    def visits(self) -> int:
        """How many times the job has been to it (a container: how many times it came in)."""
        if self.container:
            return 1
        return sum(1 for s in self.steps if s.id)

    @property
    def fill(self) -> float:
        """A container's progress: its steps done of those done and still to come."""
        done = sum(1 for s in self.steps if s.state in ("done", "skipped"))
        return done / len(self.steps) if self.steps else 0.0

    @property
    def last(self) -> Step:
        """The step a tap opens: under way, else the latest that ran, else the first to come."""
        live = [s for s in self.steps if s.state in ("running", "waiting", "pending") and s.id]
        ran = [s for s in self.steps if s.id]
        return (live or ran or self.steps)[-1] if (live or ran) else self.steps[0]


def chips(job: Job, steps: list[Step]) -> list[Chip]:
    """The job's route, as chips in the order it reached them, then those it likely will."""
    snap = plan.snapshot(job) or {}
    levels = snap.get("levels") or {}
    out: list[Chip] = []
    by_key: dict[str, Chip] = {}
    for s in steps:
        if s.kind in HIDDEN:
            continue
        if s.kind in SHOWN_WHEN and s.state not in SHOWN_WHEN[s.kind]:
            continue
        key = s.node_id.split("/", 1)[0] if s.node_id else f"checklist-{s.item['plan']}" if s.item else f"#{s.position}"
        inside = "/" in s.node_id or bool(s.item)  # a checklist's items and checks fill one chip, as a container's steps do
        chip = by_key.get(key)
        if chip is None:
            stem = key.rsplit("-", 1)[0] if key.rsplit("-", 1)[-1].isdigit() else key  # ship-2: the second Ship
            label = "Checklist" if s.item else (levels.get(key, {}).get("name") or stem.replace("_", " ").capitalize()) if inside else plan.label(s)
            chip = Chip(key, label, "", container=inside, predicted=not s.id)
            by_key[key] = chip
            out.append(chip)
        chip.steps.append(s)
        if s.id:
            chip.predicted = False
    for chip in out:
        chip.state = _state(chip)
    return out


def _state(chip: Chip) -> str:
    ran = [s for s in chip.steps if s.id]
    if not ran:
        return "skipped" if all(s.state == "skipped" for s in chip.steps) else "todo"
    if chip.container:
        states = {s.state for s in ran}
        for state in ("running", "waiting", "pending"):
            if state in states:
                return STATES[state]
        if any(not s.id for s in chip.steps):  # more of it to come: under way between its steps
            return "next" if ran[-1].state != "failed" else "failed"
        return STATES.get(ran[-1].state, "done")
    return STATES.get(ran[-1].state, "done")
