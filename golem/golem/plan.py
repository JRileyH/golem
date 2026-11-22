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
    "proposals": "proposals for you",
    "vet": "check it over",
    "sync": "catch up with main",
}
TITLE_CHARS = 60  # a job's title (title)
# Golem's steps that wait for Riggy's reply: the time they take is his, not the work's.
ASKS = ("checkin", "choose")
# What each kind of step does to a project, by golem's step kind or an agent step's name: changes
# on one project take turns at "build" (writing the code) and "ship" (push to deploy), and
# overlap at the rest (turns.py). plan: specs, options, a design round, setup. finish: review,
# docs and the last word. wait: on Riggy. look: a job that changes nothing.
CLASSES = {
    "setup": "plan", "vet": "plan", "spec": "plan", "spec-review": "plan", "options": "plan", "design": "plan",
    "scaffold": "build", "mvp": "build", "buildout": "build", "build": "build", "change": "build", "adopt": "build",
    "guide": "build", "apply": "build",
    "review": "finish", "second-pass": "finish", "docs": "finish", "alert": "finish",
    "checkin": "wait", "choose": "wait",
    "push": "ship", "push-main": "ship", "pr": "ship", "checks": "ship", "merge": "ship", "deploy": "ship",
    "refresh": "look", "examine": "look", "answer": "look", "propose": "look", "proposals": "look",
}  # fmt: skip
# Steps golem puts in where they're needed: each is part of the section it's in (the class of the
# next step that has one). A fix after failed CI is shipping; a catch-up with main before
# building is building, and before pushing, shipping.
INSERTED = ("fix", "resolve", "sync")
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
    for s in steps:
        s.turn = turn_of(steps, s)
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
    "design": ("opus", "high"),  # a design round: taste, and a doc with real screens in it
    "guide": ("opus", "high"),  # the design guide every later step follows
    "apply": ("opus", "high"),  # restyling the whole app from the guide
    "fix": ("opus", "high"),
    "resolve": ("opus", "high"),  # merging main into a change: both intents have to survive
    "review": ("opus", "xhigh"),
    "second-pass": ("sonnet", "high"),
    "docs": ("sonnet", "medium"),
    "examine": ("sonnet", "high"),  # a question: a quick look and an answer
    "propose": ("opus", "high"),  # finding what's worth doing, unprompted, takes judgment
    "survey": ("sonnet", "high"),  # before an adoption: install, run the tests, read for needs
    "adopt": ("opus", "high"),  # bringing a repo golem didn't make into its shape
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


def settings_line(project: Project) -> str:
    """A project's settings in a line: "Mode standard, each step's default model and effort"."""
    if not project.model and not project.effort:
        return f"Mode {project.mode}, each step's default model and effort"
    model = f"model {project.model}" if project.model else "each step's default model"
    effort = f"{project.effort} effort" if project.effort else "default effort"
    return f"Mode {project.mode}, {model}, {effort}"


def what(job: Job) -> str:
    """What sort of job it is, in a word or two: "new project", "adoption", "question"…"""
    if job.kind in ("new", "adopt"):
        return "new project" if job.kind == "new" else "adoption"
    return job.playbook if job.playbook in ("options", "design", "question", "proposals") else "change"


def title(job: Job) -> str:
    """What the job is about in a few words ("Library View Redesign"): the one a model gave it
    from Riggy's words, or else his request's first line, cut short (an adoption's: what he
    said after `!adopt <slug>`, or the slug)."""
    if job.title:
        return job.title
    request = job.request.strip()
    if job.kind == "adopt" and request.startswith("!adopt "):
        request = (request.split(maxsplit=2)[2:] or [job.slug])[0]
    line = request.splitlines()[0] if request else ""
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"


def clean_title(value: object) -> str:
    """A model's title for a job, tidied: one line, no quotes or full stop, not too long; "" if none."""
    text = " ".join(str(value or "").split()).strip("\"'“”‘’ ").removesuffix(".").strip()
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "…"


def current(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state in OPEN), None)


def label(step: Step) -> str:
    return step.name if step.kind == "agent" else LABELS.get(step.kind, step.kind)


def step_class(steps: list[Step], step: Step) -> str:
    """What the step does to the project (CLASSES). One golem put in (INSERTED) is part of the
    section it's in: the class of the next step that has one. An agent step golem doesn't know
    counts as building, to be safe."""
    key = step.name if step.kind == "agent" else step.kind
    if key in INSERTED:
        later = sorted((s for s in steps if s.position > step.position), key=lambda s: s.position)
        return next((step_class(steps, s) for s in later if (s.name if s.kind == "agent" else s.kind) not in INSERTED), "finish")
    return CLASSES.get(key, "build" if step.kind == "agent" else "plan")


def turn_of(steps: list[Step], step: Step) -> str:
    """The turn a step takes on its project (turns.py): build, ship or none. Written on the step
    when it's added; worked out from its class (step_class) for steps from before."""
    if step.turn:
        return step.turn
    kind = step_class(steps, step)
    return kind if kind in ("build", "ship") else "none"


def run_before(steps: list[Step], step: Step) -> list[Step]:
    """The steps of `step`'s turn that lead up to it, in order, ending with it: its job's turn at
    building, say, from the catch-up with main through `step`."""
    ordered = sorted(steps, key=lambda s: s.position)
    i = next(i for i, s in enumerate(ordered) if s.position == step.position)
    turn, start = turn_of(ordered, step), i
    while start > 0 and turn_of(ordered, ordered[start - 1]) == turn:
        start -= 1
    return ordered[start : i + 1]


def section(steps: list[Step], step: Step) -> list[Step]:
    """The run of steps around `step`, in order, that are of its class: its job's turn at
    building, say, from the catch-up with main through every build step after it."""
    ordered = sorted(steps, key=lambda s: s.position)
    classes = [step_class(ordered, s) for s in ordered]
    i = next(i for i, s in enumerate(ordered) if s.position == step.position)
    start = end = i
    while start > 0 and classes[start - 1] == classes[i]:
        start -= 1
    while end + 1 < len(ordered) and classes[end + 1] == classes[i]:
        end += 1
    return ordered[start : end + 1]


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
        if s.kind not in ("push", "sync"):
            names.append(label(s))
    return ", ".join(names) or "nothing"


def last_said(steps: list[Step]) -> str:
    """The latest finished agent step's message: what the work so far amounts to, for Riggy."""
    for s in reversed(steps):
        if s.kind == "agent" and s.state == "done" and s.summary and s.summary != LEGACY_DONE:
            return s.summary
    return ""
