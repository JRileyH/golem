"""What Riggy can change from the status page, with the rules that keep a job sound.

- Skip an upcoming step the playbook marks optional (a review, docs, the MVP check-in,
  picking between options), or undo that while nothing after it has started. The work itself and golem's
  push-to-deploy steps can't be skipped, so a job still always ends deployed.
- Move a job to the front or the back of the queue. It takes the worker at the next
  step boundary: a running step isn't interrupted.
- Pause and resume a project, or all work; stop a job.
- Choose the model and effort: for an agent step that hasn't started, for a project, or
  as the default for a kind of step (all docs steps, say). The most specific wins
  (plan.engine).
- Send a job a note, as if said in its conversation: feedback for the next agent step, or the
  answer to a question, a check-in or some options (a pick), which picks the job back up. Or give one upcoming
  agent step some context, which only that step gets.
- Give a project a tag, or none; add, rename, recolor and delete the tags themselves.
- Say how often golem looks for improvements to propose for a project, and give each
  proposal a thumbs up (it becomes a change job) or down, with why (proposals.py).

Each change to a project is posted in its conversation, so it stays the record of the job.
"""

from __future__ import annotations

import re

from . import plan
from .scheduler import Scheduler
from .store import ACTIVE, EFFORTS, EVERY, MODELS, Job, Project, Step, Store, Tag

SKIPPED = "Skipped from the status page."
TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ]{0,19}")


class ControlError(Exception):
    """A change that isn't allowed, with a reason to show."""


class Control:
    def __init__(self, store: Store, scheduler: Scheduler):
        self.store = store
        self.scheduler = scheduler

    def _job(self, job_id: int) -> tuple[Job, Project]:
        job = self.store.job(job_id)
        if job is None or job.state not in ACTIVE:
            raise ControlError("That job has finished.")
        return job, self.store.project(job.slug)

    def _project(self, slug: str) -> Project:
        project = self.store.project(slug)
        if project is None:
            raise ControlError(f"No project called {slug}.")
        return project

    def step(self, job: Job, step_id: int, position: int = 0) -> Step:
        """A job's step by id, or by position for a job that hasn't started (its steps are written then)."""
        if not step_id and position:
            steps = self.scheduler.ensure_steps(job)
            step_id = next((s.id for s in steps if s.position == position), 0)
        step = self.store.step(step_id) if step_id else None
        if step is None or step.job_id != job.id:
            raise ControlError("No such step.")
        return step

    def configurable(self, job: Job, step: Step) -> bool:
        """Whether a step's model, effort and context can still change: an agent step not yet started."""
        return job.state in ACTIVE and step.kind == "agent" and step.state == "pending" and step.started_at is None

    def kinds(self) -> list[str]:
        """The kinds of agent step, by name, in the order the playbooks use them; fix last."""
        names: list[str] = []
        for book in ("standard", "rigorous", "reckless", "feature", "options", "question", "proposals"):
            for spec in plan.load(self.scheduler.settings, book).steps:
                if spec.kind == "agent" and spec.name not in names:
                    names.append(spec.name)
        return [*names, "fix"]

    def skippable(self, job: Job, step_id: int) -> bool:
        """Whether an upcoming step can be skipped: optional, and not under way."""
        step = self.store.step(step_id)
        if step is None or step.job_id != job.id or not step.optional:
            return False
        if step.kind in plan.ASKS and step.state == "waiting":  # skipping is "looks good", or "you pick"
            return True
        return step.state == "pending" and step.started_at is None

    def restorable(self, job: Job, step_id: int) -> bool:
        """Whether a skip can be undone: nothing after the step has started."""
        step = self.store.step(step_id)
        if step is None or step.job_id != job.id or step.state != "skipped" or step.summary != SKIPPED:
            return False
        later = [s for s in self.store.steps(job.id) if s.position > step.position]
        return all(s.state == "pending" and s.started_at is None for s in later)

    async def skip(self, job_id: int, step_id: int) -> str:
        job, project = self._job(job_id)
        if not self.skippable(job, step_id):
            raise ControlError("That step can't be skipped: only optional steps that haven't started can.")
        step = self.store.update_step(step_id, state="skipped", summary=SKIPPED)
        if job.state == "waiting" and step.kind in plan.ASKS:
            self.store.update_job(job.id, state="queued", attempts=0)
        self.scheduler.wake()
        what = {
            "checkin": "your check-in (carrying on as if you'd said it looks good)",
            "choose": "your pick (the next step picks the strongest option)",
        }.get(step.kind, step.name)
        await self.scheduler.say(project, f"Skipped {what}, from the status page.", notice=True)
        return f"Skipped {plan.label(step)}."

    async def restore(self, job_id: int, step_id: int) -> str:
        job, project = self._job(job_id)
        if not self.restorable(job, step_id):
            raise ControlError("That skip can't be undone any more: the steps after it have started.")
        step = self.store.update_step(step_id, state="pending", summary=None)
        await self.scheduler.say(project, f"Put {plan.label(step)} back in, from the status page.", notice=True)
        return f"Put {plan.label(step)} back."

    async def move(self, job_id: int, where: str) -> str:
        job, project = self._job(job_id)
        others = [j.priority for j in self.store.jobs(ACTIVE) if j.id != job.id]
        if where == "first":
            priority = max(others, default=job.priority) + 1
        elif where == "last":
            priority = min(others, default=job.priority) - 1
        else:
            raise ControlError("Move it first or last.")
        self.store.update_job(job.id, priority=priority)
        self.scheduler.wake()
        return f"{project.name} goes {where} in the queue, from the next step."

    async def pause(self, slug: str, paused: bool) -> str:
        project = self._project(slug)
        self.store.set_paused(slug, paused)
        self.scheduler.wake()
        word = "Paused" if paused else "Resumed"
        await self.scheduler.say(project, f"{word} {project.name}, from the status page.", notice=True)
        return f"{word} {project.name}."

    def pause_all(self, paused: bool) -> str:
        self.scheduler.set_paused(paused)
        return "Paused all work: a running step finishes first." if paused else "Resumed all work."

    async def kill(self, job_id: int) -> str:
        job, project = self._job(job_id)
        self.scheduler.kill(job.id)
        await self.scheduler.say(project, f"Stopped job {job.id}, from the status page.", notice=True)
        return f"Stopped job {job.id}."

    async def setting(self, slug: str, name: str, value: str) -> str:
        """A project's model or effort for all its agent steps, "" for each step's default; its tag, "" for
        none; or how often golem looks for improvements to propose, "" for never."""
        project = self._project(slug)
        if name == "proposals":
            every = value or "off"
            if every not in EVERY:
                raise ControlError(f"Proposals can't be {value}.")
            self.store.update_project(slug, proposals=every)
            said = "No proposals now" if every == "off" else f"Proposals {every}, when there's nothing else to do"
            await self.scheduler.say(project, f"{said}, from the status page.", notice=True)
            self.scheduler.wake()
            return f"{project.name}: {said.lower()}."
        if name == "tag":
            tag = self._tag(value) if value else None
            self.store.update_project(slug, tag=tag.name if tag else "")
            await self.scheduler.say(project, f"{f'Tagged {tag.name}' if tag else 'No tag now'}, from the status page.", notice=True)
            return f"{project.name}: {tag.name if tag else 'no tag'}."
        _check(name, value)
        self.store.update_project(slug, **{name: value})
        said = f"{name.title()} is now {value}" if value else f"{name.title()} is each step's default again"
        await self.scheduler.say(project, f"{said}, from the status page.", notice=True)
        return f"{project.name}: {name} {value or 'by step'}, from the next agent step."

    async def step_setting(self, job_id: int, step_id: int, position: int, name: str, value: str) -> str:
        """One upcoming agent step's model or effort; "" to go back to the defaults."""
        job, project = self._job(job_id)
        step = self.step(job, step_id, position)
        if not self.configurable(job, step):
            raise ControlError("Only agent steps that haven't started can change.")
        _check(name, value)
        step = self.store.update_step(step.id, **{name: value or None})
        engine = plan.engine(self.scheduler.settings, self.store, project, step)
        await self.scheduler.say(project, f"{step.name} will run on {engine}, from the status page.", notice=True)
        return f"{step.name} will run on {engine}."

    def default_setting(self, kind: str, name: str, value: str) -> str:
        """The model or effort for every step of one kind (or "*", all of them); "" to unset."""
        if kind != "*" and kind not in self.kinds():
            raise ControlError(f"No kind of step called {kind}.")
        _check(name, value)
        self.store.set_default(kind, name, value)
        what = "Every step" if kind == "*" else f"Every {kind} step"
        return f"{what}: {name} {value or 'unset'}, unless a project or step says otherwise. Starts with the next one."

    def reset_defaults(self) -> str:
        """Every kind of step's model and effort back to golem's suggestion."""
        self.store.reset_defaults(plan.SUGGESTED)
        return "Every kind of step is back to golem's suggestion. Projects' and steps' own choices stay."

    def _tag(self, name: str) -> Tag:
        tag = self.store.tag(name)
        if tag is None:
            raise ControlError(f"No tag called {name}.")
        return tag

    def add_tag(self, name: str) -> str:
        name = _tag_name(name)
        if same := self.store.tag(name):
            raise ControlError(f"There's a {same.name} tag already.")
        self.store.add_tag(name)
        return f"Added the {name} tag. golem uses it from the next new idea."

    def rename_tag(self, name: str, new: str) -> str:
        tag, new = self._tag(name), _tag_name(new)
        same = self.store.tag(new)
        if same and same.name != tag.name:
            raise ControlError(f"There's a {same.name} tag already.")
        self.store.update_tag(tag.name, new_name=new)
        return f"{tag.name} is now {new}, on its projects too."

    def tag_color(self, name: str, value: str) -> str:
        tag = self._tag(name)
        if not value.isdigit() or not 1 <= int(value) <= 8:
            raise ControlError("Pick one of the colors.")
        self.store.update_tag(tag.name, color=int(value))
        return f"{tag.name} has its new color on the projects page."

    def delete_tag(self, name: str) -> str:
        tag = self._tag(name)
        n = self.store.tag_counts().get(tag.name, 0)
        self.store.delete_tag(tag.name)
        return f"Deleted the {tag.name} tag." + (f" {n} project{'s have' if n != 1 else ' has'} none now." if n else "")

    async def decide(self, proposal_id: int, accepted: bool, reason: str) -> str:
        """Riggy's thumbs up or down on a proposal, with why if he says. Up queues it as a change
        once its project has no other change under way."""
        p = self.store.proposal(proposal_id)
        if p is None or p.state != "open":
            raise ControlError("That proposal has been decided already.")
        reason = " ".join(reason.split())[:1000]
        p = self.store.decide_proposal(p.id, accepted, reason)
        project = self._project(p.slug)
        await self.scheduler.chat.heard(project.slug, f"{'👍' if accepted else '👎'} {p.title}" + (f": {reason}" if reason else ""))
        if not accepted:
            return f"Turned down “{p.title}”. golem takes that into account next time."
        await self.scheduler.build_accepted()
        p = self.store.proposal(p.id)
        if p.built_by:
            return f"Accepted “{p.title}”: job {p.built_by} builds it."
        return f"Accepted “{p.title}”: it's built once {project.name}'s current job is done."

    async def note(self, job_id: int, text: str, step_id: int = 0, position: int = 0) -> str:
        job, project = self._job(job_id)
        text = text.strip()
        if not text:
            raise ControlError("Write something first.")
        if step_id or position:  # context for one upcoming step
            step = self.step(job, step_id, position)
            if not self.configurable(job, step):
                raise ControlError("That step has started: send a note to the job instead.")
            self.store.add_note(job.id, text[:4000], step.id)
            await self.scheduler.chat.heard(project.slug, f"For {step.name}: {text[:4000]}")
            return f"{step.name} gets that when it starts."
        self.store.add_note(job.id, text[:4000])
        await self.scheduler.chat.heard(project.slug, text[:4000])
        if job.state == "waiting":
            self.store.update_job(job.id, state="queued", attempts=0)
            self.scheduler.wake()
            return "Sent. Picking the job back up."
        return "Sent. The next agent step gets it."


def _tag_name(name: str) -> str:
    """A tag's name: a few words of letters and digits, as Riggy typed them."""
    name = " ".join(name.split())
    if not TAG_RE.fullmatch(name):
        raise ControlError("A tag is a word or two: letters, digits and spaces, at most 20 characters.")
    return name


def _check(name: str, value: str) -> None:
    allowed = {"model": MODELS, "effort": EFFORTS}.get(name)
    if allowed is None or (value and value not in allowed):
        raise ControlError(f"{name} can't be {value}.")
