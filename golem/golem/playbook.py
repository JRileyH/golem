"""Playbooks: markdown files listing a job's steps in front matter, with one `## <name>`
section of instructions per agent step.

    ---
    steps:
      - setup
      - {agent: spec, turns: 80}
      - {agent: review, turns: 150, modes: [standard, rigorous], optional: true}
      - push
      - pr
      - checks
      - merge
      - deploy
      - alert
    ---
    Text here is given to every agent step.

    ## spec
    What the spec step does.

An agent step is one headless Claude Code run that does a piece of work and commits it.
Every other step is golem's own code (CODE_STEPS), so pushing, pull requests, CI,
merging, deploying, asking Riggy and telling him happen the same way every time. A job
either ships something (setup first, deploy and alert last) or only answers a question
(refresh first, answer last: nothing is committed, pushed or deployed). `modes` limits a
step to projects in those modes; `optional` lets Riggy skip it from the status page
(a review, say, but never the work itself or golem's push-to-deploy steps). The model
and effort are chosen on the status page, per step, project or kind of step (plan.engine).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .store import MODES

SECTION_RE = re.compile(r"^## +(\S+) *$", re.M)
CODE_STEPS = ("setup", "push", "pr", "checks", "merge", "push-main", "deploy", "checkin", "choose", "alert", "refresh", "answer")
# How every job starts and ends, in every mode: shipping something, or answering a question.
SHAPES = ((("setup",), ("deploy", "alert")), (("refresh",), ("answer",)))
# Golem's only steps that can be optional: skipping a check-in means "looks good", skipping
# the pick between options leaves it to the next step.
SKIPPABLE = ("checkin", "choose")


@dataclass(frozen=True)
class StepSpec:
    kind: str  # "agent", or one of CODE_STEPS
    name: str  # an agent step's section; for code steps, the kind
    max_turns: int = 0
    instructions: str = ""
    modes: tuple[str, ...] = ()  # empty: every mode
    optional: bool = False  # Riggy may skip it


@dataclass(frozen=True)
class Playbook:
    name: str
    intro: str
    steps: tuple[StepSpec, ...]

    def for_mode(self, mode: str) -> list[StepSpec]:
        return [s for s in self.steps if not s.modes or mode in s.modes]

    def instructions(self, name: str) -> str:
        for step in self.steps:
            if step.kind == "agent" and step.name == name:
                return step.instructions
        raise KeyError(f"{self.name} has no agent step {name}")


def load(path: Path) -> Playbook:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing front matter")
    front, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(front)
    parts = SECTION_RE.split(body)
    intro, sections = parts[0].strip(), dict(zip(parts[1::2], (s.strip() for s in parts[2::2])))
    steps = [_step(path, entry, sections) for entry in meta["steps"]]
    extra = set(sections) - {s.name for s in steps if s.kind == "agent"}
    if extra:
        raise ValueError(f"{path}: sections without an agent step: {', '.join(sorted(extra))}")
    book = Playbook(path.stem, intro, tuple(steps))
    for mode in MODES:
        kinds = tuple(s.kind for s in book.for_mode(mode))
        if not any(kinds[: len(first)] == first and kinds[-len(last) :] == last for first, last in SHAPES):
            shapes = " or ".join(f"start with {', '.join(first)} and end with {', '.join(last)}" for first, last in SHAPES)
            raise ValueError(f"{path}: steps must {shapes}")
    return book


def _step(path: Path, entry: str | dict, sections: dict[str, str]) -> StepSpec:
    if isinstance(entry, str):
        entry = {"do": entry}
    modes = tuple(entry.get("modes") or ())
    optional = entry.get("optional") is True
    if set(modes) - set(MODES):
        raise ValueError(f"{path}: modes are {', '.join(MODES)}, not {', '.join(sorted(set(modes) - set(MODES)))}")
    if "agent" in entry:
        name = entry["agent"]
        if name not in sections:
            raise ValueError(f"{path}: agent step {name} has no '## {name}' section")
        return StepSpec("agent", name, int(entry["turns"]), sections[name], modes, optional)
    kind = entry.get("do")
    if kind not in CODE_STEPS:
        raise ValueError(f"{path}: unknown step {kind!r}; golem's steps are {', '.join(CODE_STEPS)}")
    if optional and kind not in SKIPPABLE:
        raise ValueError(f"{path}: {kind} can't be optional; of golem's steps only {', '.join(SKIPPABLE)} can")
    return StepSpec(kind, kind, modes=modes, optional=optional)
