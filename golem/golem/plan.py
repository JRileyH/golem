"""A job's steps: what it will do when it starts, where it is, and what comes next.

Shared by the scheduler (which writes the steps when a job starts), the bot and the
status page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import engine as graphs
from .config import Settings
from .store import ACTIVE, OPEN, TRIAGE, Job, Project, Step, Store

# How golem's own steps read in messages and on the status page.
LABELS = {
    "setup": "set up",
    "push": "push",
    "pr": "PR",
    "checks": "CI",
    "merge": "merge",
    "deploy": "deploy",
    "checkin": "your check-in",
    "choose": "your pick",
    "alert": "done",
    "refresh": "catch up",
    "answer": "your answer",
    "proposals": "proposals for you",
    "vet": "check it over",
    "sync": "catch up with main",
    "test": "test",
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
    "review": "finish", "second-pass": "finish", "docs": "finish", "alert": "finish", "test": "finish",
    "checkin": "wait", "choose": "wait",
    "push": "ship", "pr": "ship", "checks": "ship", "merge": "ship", "deploy": "ship",
    "refresh": "look", "examine": "look", "answer": "look", "propose": "look", "proposals": "look", "triage": "look",
}  # fmt: skip
# Steps golem puts in where they're needed: each is part of the section it's in (the class of the
# next step that has one). A fix after failed CI is shipping; a catch-up with main before
# building is building, and before pushing, shipping.
INSERTED = ("fix", "resolve", "sync")
# Which built-in workflow a job runs that doesn't name one, by its playbook (golem's own jobs, and
# those from before workflows, as their history shows).
BOOKS = {"standard": "web_app", "rigorous": "web_app", "reckless": "web_app", "feature": "change", "options": "options",
         "design": "design", "question": "question", "proposals": "proposals", "adopt": "adopt"}  # fmt: skip
NEW_BOOKS = ("standard", "rigorous", "reckless")


def workflow_for(job: Job, project: Project | None) -> tuple[str, str]:
    """The workflow (by key) and mode a job runs: its own, or for a job that hasn't said, the
    one its playbook became."""
    if job.workflow:
        return job.workflow, job.mode
    if job.playbook in NEW_BOOKS:
        return "web_app", "" if job.playbook == "standard" else job.playbook
    mode = project.mode if project and project.mode not in ("", "standard") else ""
    return BOOKS.get(job.playbook, "change"), mode


# Golem's modes: ways of working a job can be in, each with what it means. "" is the usual way.
MODES: dict[str, str] = {}


def mode_for(project: Project | None, asked: str = "") -> str:
    """The mode a job runs in: what Riggy picked for it, else its project's default (his
    setting), else none."""
    if asked in MODES:
        return asked
    return project.mode if project and project.mode in MODES else ""


def snapshot(job: Job) -> dict | None:
    """The job's own copy of its workflow, once it has one."""
    return json.loads(job.graph) if job.graph else None


def vars_of(job: Job) -> dict:
    """The job's counters, settings for blocks still to come, and loop guard (graphs.blank_vars)."""
    found = json.loads(job.vars or "{}") or {}
    return {**graphs.blank_vars(), **found}


def node(job: Job, step: Step) -> dict | None:
    """The block of the job's workflow a step is a visit to; None for a step golem put in
    (a catch-up with main) or one from before workflows."""
    snap = snapshot(job) if step.node_id else None
    return snap["nodes"].get(step.node_id) if snap else None


def planned(settings: Settings, store: Store, job: Job) -> list[Step]:
    """The job's steps so far and those likely to come, in order: what's been done, what's under
    way, and the blocks after it on the usual way through its workflow (id 0: not written yet).
    Counters and conditions are left out: they're golem's bookkeeping, not steps."""
    steps = shown(store.steps(job.id))
    if job.state not in ACTIVE:
        return steps
    snap, vars = snapshot(job), vars_of(job)
    if snap is None:
        snap = preview(settings, store, job)
        if snap is None:
            return steps
    trace = store.steps(job.id)
    at = next((s for s in reversed(trace) if s.state in OPEN and s.node_id), None)
    ahead = graphs.predict(snap, vars, trace, at.node_id if at else None)
    last = max((s.position for s in trace), default=0)
    for i, nid in enumerate(ahead, start=last + 1):
        step = graphs.row(snap, nid, trace, vars, job.id)
        step.position, step.problem = i, None
        settings_ = vars["nodes"].get(nid, {})
        step.model, step.effort = settings_.get("model"), settings_.get("effort")
        if settings_.get("skip"):
            step.state, step.summary = "skipped", graphs.SKIPPED
        steps.append(step)
    return steps


def preview(settings: Settings, store: Store, job: Job) -> dict | None:
    """The copy of its workflow a job that hasn't started will take, as it is now. A triage's is
    golem's code, never a workflow (triage.py)."""
    from .workflows import Library

    if job.start == TRIAGE:
        from .triage import snapshot

        return snapshot()
    key, _ = workflow_for(job, store.project(job.slug))
    library = Library(store, settings.process_dir / "workflows", settings.web_password)
    wf = library.get(key)
    if wf is None or wf.start != job.start:
        wf = library.fallback(job.start)
    return library.pin(wf, job.start) if wf else None


def block_of(settings: Settings, store: Store, job: Job, step: Step) -> dict | None:
    """The block a step is (or will be) a visit to: from the job's copy, or for a job that
    hasn't started, the copy it would take."""
    if not step.node_id:
        return None
    snap = snapshot(job) or (preview(settings, store, job) if job.state in ACTIVE else None)
    return snap["nodes"].get(step.node_id) if snap else None


def blocks_in_order(graph: dict) -> list[dict]:
    """A graph's blocks, in the order a job usually meets them (first outputs from Start), then the rest."""
    nodes, wired = graphs.by_id(graph), graphs.wires(graph)
    seen, order = set(), []
    try:
        at = graphs.entry(graph)["id"]
    except graphs.GraphError:
        at = None
    while at and at in nodes and at not in seen:
        seen.add(at)
        order.append(nodes[at])
        outs = wired.get(at, {})
        at = next(iter(outs.values()), None)
    return order + [n for n in graph.get("nodes") or [] if n["id"] not in seen]


def shown(steps: list[Step]) -> list[Step]:
    """Steps without golem's bookkeeping (counters, conditions)."""
    return [s for s in steps if s.kind not in graphs.CONTROL]


# Golem's suggested model and effort for each kind of agent step, filled in once where Riggy
# hasn't set them (Store.suggest_defaults); he changes them on the insights page. Opus thinks
# hardest where everything after depends on it (spec, mvp) and where finding what's wrong
# needs it (review); sonnet does the steps that follow a recipe or check a narrow diff.
SUGGESTED = {
    "spec": ("opus", "xhigh"),
    "scaffold": ("sonnet", "medium"),
    "mvp": ("opus", "xhigh"),
    "buildout": ("opus", "high"),
    "change": ("opus", "high"),
    "options": ("opus", "high"),
    "design": ("opus", "high"),  # a design round: taste, and a doc with real screens in it
    "guide": ("opus", "high"),  # the design guide every later step follows
    "apply": ("opus", "high"),  # restyling the whole app from the guide
    "fix": ("opus", "high"),
    "resolve": ("opus", "high"),  # merging main into a change: both intents have to survive
    "review": ("opus", "xhigh"),
    "docs": ("sonnet", "medium"),
    "examine": ("sonnet", "high"),  # a question: a quick look and an answer
    "propose": ("opus", "high"),  # finding what's worth doing, unprompted, takes judgment
    "triage": ("opus", "high"),  # what in golem causes its workers' trouble: reading its code and their logs
    "survey": ("sonnet", "high"),  # before an adoption: install, run the tests, read for needs
    "adopt": ("opus", "high"),  # bringing a repo golem didn't make into its shape
    # Stage blocks (stages.py), by stage, after a block's own name:
    "stage:plan": ("opus", "xhigh"), "stage:build": ("opus", "high"), "stage:review": ("opus", "xhigh"),
    "stage:docs": ("sonnet", "medium"), "stage:design": ("opus", "high"), "stage:fix": ("opus", "high"),
    "stage:look": ("sonnet", "high"), "stage:options": ("opus", "high"),
}  # fmt: skip
SUGGESTED_KEY = "suggested_defaults_2"  # a new key fills in the whole set again, where unset


@dataclass
class Engine:
    """The model and effort an agent step runs with, and where each came from."""

    model: str
    effort: str
    model_from: str  # step | project | <step name> default | default | golem
    effort_from: str

    def __str__(self) -> str:
        return f"{self.model}/{self.effort}"


def engine(settings: Settings, store: Store, project: Project, step: Step, block: dict | None = None) -> Engine:
    """The most specific choice wins: this step's, the project's, the workflow block's own, the
    default for steps like it (by name: docs, spec, fix…), then for its stage's blocks, the
    default for every step, then GOLEM_MODEL and GOLEM_EFFORT."""
    defaults = store.defaults()
    named, every = defaults.get(step.name, ("", "")), defaults.get("*", ("", ""))
    block = block or {}
    stage = block.get("stage") or ""
    staged = defaults.get(f"stage:{stage}", ("", "")) if stage else ("", "")
    found = []
    for i, fallback in enumerate((settings.default_model, settings.default_effort)):
        choices = [
            (step.model if i == 0 else step.effort, "this step"),
            (project.model if i == 0 else project.effort, "the project"),
            (block.get("model" if i == 0 else "effort") or "", "its workflow"),
            (named[i], f"the {step.name} default"),
            (staged[i], f"the {stage} blocks' default"),
            (every[i], "the default"),
            (fallback, "the default"),
        ]
        found.append(next((value, source) for value, source in choices if value))
    (model, model_from), (effort, effort_from) = found
    return Engine(model, effort, model_from, effort_from)


def engine_words(project: Project) -> str:
    """A project's model and effort in words: "each step's default model and effort"."""
    if not project.model and not project.effort:
        return "each step's default model and effort"
    model = f"model {project.model}" if project.model else "each step's default model"
    effort = f"{project.effort} effort" if project.effort else "default effort"
    return f"{model}, {effort}"


def settings_line(project: Project) -> str:
    """A project's settings in a line: "No default mode, each step's default model and effort".
    Its default mode is what its requests run in unless they ask for another: Riggy's setting."""
    mode = f"Default mode {project.mode}" if project.mode else "No default mode"
    return f"{mode}, {engine_words(project)}"


def running(job: Job, wf: object | None) -> str:
    """What a job runs, in words: "Web app, rigorous mode", or "Change"."""
    name = getattr(wf, "name", "") or job.workflow or job.playbook
    return f"{name}, {job.mode} mode" if job.mode else name


def what(job: Job) -> str:
    """What sort of job it is, in a word or two: "new project", "adoption", "question"…"""
    if job.kind in ("new", "adopt"):
        return "new project" if job.kind == "new" else "adoption"
    return job.playbook if job.playbook in ("options", "design", "question", "proposals") else "change"


def adopting(slug: str) -> str:
    """An adopt job's request when Riggy didn't say what the repo is."""
    return f"Adopt {slug}."


def adopt_words(job: Job) -> str:
    """What Riggy said a repo he had golem adopt is: its job's request, unless he said nothing
    (`adopting`); for one from before the form, what followed `!adopt <slug>`."""
    request = job.request.strip()
    if request.startswith("!adopt "):
        return (request.split(maxsplit=2)[2:] or [""])[0]
    return "" if request == adopting(job.slug) else request


def title(job: Job) -> str:
    """What the job is about in a few words ("Library View Redesign"): the one a model gave it
    from Riggy's words, or else his request's first line, cut short (an adoption's: what he
    said the repo is, or its slug)."""
    if job.title:
        return job.title
    request = job.request.strip()
    if job.kind == "adopt":
        request = adopt_words(job) or job.slug
    line = request.splitlines()[0] if request else ""
    return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"


def clean_title(value: object) -> str:
    """A model's title for a job, tidied: one line, no quotes or full stop, not too long; "" if none."""
    text = " ".join(str(value or "").split()).strip("\"'“”‘’ ").removesuffix(".").strip()
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "…"


def current(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state in OPEN), None)


def label(step: Step) -> str:
    """What a step is called: an agent's name; golem's own by kind, unless its block has a name of its own."""
    if step.kind == "agent" or (step.kind in ("checkin", "choose", "condition") and step.name != step.kind):
        return step.name
    return LABELS.get(step.kind, step.kind)


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
    building, say, from the catch-up with main through `step`. Counters and conditions don't
    break a run: a fix loop's are part of its turn at shipping."""
    ordered = sorted((s for s in steps if s.kind not in graphs.CONTROL or s.position == step.position), key=lambda s: s.position)
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
        if s.kind == "agent" and s.state == "done" and s.summary:
            return s.summary
    return ""
