"""A job's steps as the pieces of work a person would name: plan it, build the MVP, you try
it, build the rest, review, docs.

Golem's shipping steps (push, PR, CI, merge, deploy) belong to the piece of work they ship:
it's live once its deploy is done, and while they run the piece says what it's waiting on
("waiting for CI"). Setting up and the closing alert aren't work Riggy asked for: setting up
shows only while it runs or if it fails (as does bringing a question's checkout up to date),
and the alert not at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import plan
from .store import OPEN, Step

SHIP = {"push", "pr", "checks", "merge", "push-main", "deploy"}
NAMES = {
    "spec": "Plan it",
    "spec-review": "Check the plan",
    "scaffold": "Set up hosting",
    "mvp": "Build the MVP",
    "buildout": "Build the rest",
    "build": "Build it",
    "change": "Make the change",
    "options": "Make the options",
    "review": "Review",
    "second-pass": "Second review",
    "docs": "Write the docs",
    "fix": "Fix what broke",
    "checkin": "You try it",
    "choose": "You pick one",
    "setup": "Get ready",
    "refresh": "Catch up",
    "examine": "Look into it",
    "answer": "Answer you",
}
SHIPPING = {"push": "pushing", "push-main": "pushing", "pr": "opening a PR", "checks": "waiting for CI",
            "merge": "merging", "deploy": "deploying"}  # fmt: skip


@dataclass
class Piece:
    step: Step  # the work: an agent step, Riggy's turn, or setting up
    ship: list[Step] = field(default_factory=list)  # the steps that ship it, in order
    state: str = "todo"  # done | now | waiting | failed | skipped | todo

    @property
    def name(self) -> str:
        return NAMES.get(self.step.name if self.step.kind == "agent" else self.step.kind, self.step.name.replace("-", " ").capitalize())

    @property
    def live(self) -> bool:
        """Its deploy is done: what it made is on the site."""
        return any(s.kind == "deploy" and s.state == "done" for s in self.ship)

    @property
    def shipping(self) -> Step | None:
        """The shipping step under way, once the work itself is done."""
        return next((s for s in self.ship if s.state in OPEN and s.started_at), None)

    @property
    def steps(self) -> list[Step]:
        return [self.step, *self.ship]


def pieces(steps: list[Step]) -> list[Piece]:
    current = plan.current(steps)
    out: list[Piece] = []
    for s in steps:
        if s.kind in SHIP and out:
            out[-1].ship.append(s)
        elif s.kind == "alert":
            continue
        elif s.kind in ("setup", "refresh"):
            if s.state in ("running", "failed"):
                out.append(Piece(s))
        else:
            out.append(Piece(s))
    for p in out:
        p.state = _state(p, current)
    return out


def current(found: list[Piece]) -> Piece | None:
    """The piece under way or waiting, else the next to do."""
    return next((p for p in found if p.state in ("now", "waiting", "failed")), None) or next(
        (p for p in found if p.state == "todo"), None
    )


def _state(p: Piece, current: Step | None) -> str:
    s = p.step
    if s.state == "waiting":
        return "waiting"
    if s.state == "running" or (current is not None and current in p.ship):
        return "now"
    if s.state == "failed":
        return "failed"
    if s.state == "pending":
        return "todo"
    if any(x.state == "pending" for x in p.ship):
        return "todo"
    return s.state  # done or skipped
