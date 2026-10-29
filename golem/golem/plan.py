"""A job's steps: what it will do when it starts, where it is, and what comes next.

Shared by the scheduler (which writes the steps when a job starts), the bot and the
status page.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import playbook as playbooks
from .config import Settings
from .store import ACTIVE, OPEN, Job, Project, Step, Store

# How golem's own steps read in messages and on the status page.
LABELS = {
    "setup": "set up",
    "push": "push",
    "pr": "PR",
    "checks": "CI",
    "merge": "merge",
    "push-main": "push to main",
    "deploy": "deploy",
    "checkin": "your check-in",
    "choose": "your pick",
    "alert": "done",
    "refresh": "catch up",
    "answer": "your answer",
}
# Golem's steps that wait for Riggy's reply: the time they take is his, not the work's.
ASKS = ("checkin", "choose")
# Before steps were tracked, a job was a list of phases and an index into it. Jobs still
# under way from then pick up at the agent step their phase became (ship became docs).
LEGACY_PHASES = {
    "reckless": ["build"],
    "standard": ["spec", "scaffold", "mvp", "buildout", "review", "docs"],
    "rigorous": ["spec", "spec-review", "scaffold", "mvp", "buildout", "review", "second-pass", "docs"],
    "feature": ["change", "review", "docs"],
}
LEGACY_DONE = "Done before golem tracked steps."


def load(settings: Settings, name: str) -> playbooks.Playbook:
    return playbooks.load(settings.process_dir / "playbooks" / f"{name}.md")


def planned(settings: Settings, store: Store, job: Job) -> list[Step]:
    """The job's steps; for a job that hasn't started, the steps it will get (id 0)."""
    steps = store.steps(job.id)
    if steps or job.state not in ACTIVE:  # a job that ended before golem tracked steps has none
        return steps
    project = store.project(job.slug)
    specs = load(settings, job.playbook).for_mode(project.mode)
    steps = [Step(0, job.id, i, s.kind, s.name, s.max_turns or None, s.optional) for i, s in enumerate(specs, start=1)]
    if job.phase or store.runs(job.id):
        names = LEGACY_PHASES.get(job.playbook, [])[job.phase :]
        resume = next((i for i, s in enumerate(steps) if s.kind == "agent" and s.name in names), 0)
        for s in steps[:resume]:
            s.state, s.summary = "done", LEGACY_DONE
    return steps


# Golem's suggested model and effort for each kind of agent step, filled in once where Riggy
# hasn't set them (Store.suggest_defaults); he changes them on the insights page. Opus thinks
# hardest where everything after depends on it (spec, mvp) and where finding what's wrong
# needs it (review); sonnet does the steps that follow a recipe or check a narrow diff.
SUGGESTED = {
    "spec": ("opus", "xhigh"),
    "spec-review": ("opus", "high"),
    "scaffold": ("sonnet", "medium"),
    "mvp": ("opus", "xhigh"),
    "buildout": ("opus", "high"),
    "build": ("opus", "high"),
    "change": ("opus", "high"),
    "options": ("opus", "high"),
    "fix": ("opus", "high"),
    "review": ("opus", "xhigh"),
    "second-pass": ("sonnet", "high"),
    "docs": ("sonnet", "medium"),
    "examine": ("sonnet", "high"),  # a question: a quick look and an answer
}
SUGGESTED_KEY = "suggested_defaults_1"  # a new key fills in the whole set again, where unset


@dataclass
class Engine:
    """The model and effort an agent step runs with, and where each came from."""

    model: str
    effort: str
    model_from: str  # step | project | <step name> default | default | golem
    effort_from: str

    def __str__(self) -> str:
        return f"{self.model}/{self.effort}"


def engine(settings: Settings, store: Store, project: Project, step: Step) -> Engine:
    """The most specific choice wins: this step's, the project's, the default for steps like it
    (by name: docs, spec, fix…), the default for every step, then GOLEM_MODEL and GOLEM_EFFORT."""
    defaults = store.defaults()
    named, every = defaults.get(step.name, ("", "")), defaults.get("*", ("", ""))
    found = []
    for i, fallback in enumerate((settings.default_model, settings.default_effort)):
        choices = [
            (step.model if i == 0 else step.effort, "this step"),
            (project.model if i == 0 else project.effort, "the project"),
            (named[i], f"the {step.name} default"),
            (every[i], "the default"),
            (fallback, "the default"),
        ]
        found.append(next((value, source) for value, source in choices if value))
    (model, model_from), (effort, effort_from) = found
    return Engine(model, effort, model_from, effort_from)


def current(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state in OPEN), None)


def label(step: Step) -> str:
    return step.name if step.kind == "agent" else LABELS.get(step.kind, step.kind)


def where(steps: list[Step]) -> str:
    """The current step and how far along the job is: "mvp (9/24)", or "finished"."""
    step = current(steps)
    if step is None:
        return "finished"
    return f"{label(step)} ({steps.index(step) + 1}/{len(steps)})"


def upcoming(steps: list[Step], after: Step) -> str:
    """What happens after `after`, up to the next agent step: "PR, CI, merge, deploy, then mvp"."""
    ahead = [s for s in steps if s.position > after.position and s.state in OPEN]
    names = []
    for s in ahead:
        if s.kind == "agent":
            return ", ".join(names + [f"then {s.name}"]) if names else s.name
        if s.kind != "push":
            names.append(label(s))
    return ", ".join(names) or "nothing"


def last_said(steps: list[Step]) -> str:
    """The latest finished agent step's message: what the work so far amounts to, for Riggy."""
    for s in reversed(steps):
        if s.kind == "agent" and s.state == "done" and s.summary and s.summary != LEGACY_DONE:
            return s.summary
    return ""
