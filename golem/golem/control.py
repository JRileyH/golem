"""What Riggy can change from the status page, with the rules that keep a job sound.

- Skip an upcoming step its workflow marks optional (a review, docs, the MVP check-in,
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
- Check for a new version of golem, upgrade to it (once the running step is done), and
  turn upgrading automatically on or off (upgrade.py).
- Turn golem's triage of the reports about itself on or off, and take each fix it proposes
  to golem (a prompt for Riggy to paste into a session in golem's repo) or turn it down, with
  why (triage.py).

Each change to a project is posted in its conversation, so it stays the record of the job.
"""

from __future__ import annotations

import re
from dataclasses import replace

from . import options, plan, shipments, stages, triage
from .proposals import LENSES, WAITING, kinds
from .scheduler import Scheduler
from .talk import STATUS
from .store import ACTIVE, CHANGES_AT_ONCE, EFFORTS, EVERY, INTERNAL, MAX_WORKERS, MODELS, Job, Project, Step, Store, Tag

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
        project = self.store.project(slug) if slug != INTERNAL else None  # golem's own isn't his to change
        if project is None:
            raise ControlError(f"No project called {slug}.")
        return project

    def step(self, job: Job, step_id: int, position: int = 0) -> Step:
        """A job's step by id, or by position: one still to come isn't written yet (id 0), so it's
        known by where it is on the job's way through its workflow (plan.planned)."""
        if not step_id and position:
            step = next((s for s in plan.planned(self.scheduler.settings, self.store, job) if s.position == position), None)
        else:
            step = self.store.step(step_id) if step_id else None
        if step is None or step.job_id != job.id:
            raise ControlError("No such step.")
        return step

    def _block_settings(self, job: Job, step: Step, **changes: object) -> None:
        """Riggy's settings for a block of the job's workflow still to come: kept with the job, and
        given to its step when it's written (engine.advance). None unsets one."""
        vars = plan.vars_of(job)
        mine = {**vars["nodes"].get(step.node_id, {}), **changes}
        vars["nodes"][step.node_id] = {k: v for k, v in mine.items() if v not in (None, "", False)}
        self.store.update_job(job.id, vars=vars)

    def configurable(self, job: Job, step: Step) -> bool:
        """Whether a step's model, effort and context can still change: an agent step not yet started."""
        return job.state in ACTIVE and step.kind == "agent" and step.state == "pending" and step.started_at is None

    def kinds(self) -> list[str]:
        """The kinds of agent run, by name: every agent block's in the workflows, built-ins first,
        each in the order its graph runs them; then those golem starts itself: resolve (turns.py),
        the survey an adoption's check runs (adopt.py), its triage of reports about itself
        (triage.py) and a checklist's items (checklist.py)."""
        names: list[str] = []
        for wf in self.scheduler.library.all():
            for node in plan.blocks_in_order(wf.graph):
                if node["type"] == "agent" and node.get("name") and node["name"] not in names:
                    names.append(node["name"])
        golems = ("fix", "resolve", "survey", "triage", "item")
        return [*names, *(n for n in golems if n not in names), *(f"stage:{k}" for k in stages.catalog())]

    def skippable(self, job: Job, step: Step) -> bool:
        """Whether an upcoming step can be skipped: optional, and not under way."""
        if step.job_id != job.id or not step.optional or job.state not in ACTIVE:
            return False
        if step.kind in plan.ASKS and step.state == "waiting":  # skipping is "looks good", or "you pick"
            return True
        return step.state == "pending" and step.started_at is None

    def restorable(self, job: Job, step: Step) -> bool:
        """Whether a skip can be undone: one still to come, or one nothing after has started."""
        if step.job_id != job.id or step.state != "skipped" or step.summary != SKIPPED or job.state not in ACTIVE:
            return False
        if not step.id:
            return bool(plan.vars_of(job)["nodes"].get(step.node_id, {}).get("skip"))
        later = [s for s in self.store.steps(job.id) if s.position > step.position]
        return all(s.state in ("pending", "skipped", "done") and s.started_at is None for s in later)

    async def skip(self, job_id: int, step_id: int, position: int = 0) -> str:
        job, project = self._job(job_id)
        step = self.step(job, step_id, position)
        if not self.skippable(job, step):
            raise ControlError("That step can't be skipped: only optional steps that haven't started can.")
        if not step.id:  # still to come: it'll be passed over when the job gets there
            self._block_settings(job, step, skip=True)
        else:
            await self.scheduler.skip_now(job, step, SKIPPED)
        self.scheduler.wake()
        design = options.design_of(options.shown(self.store.steps(job.id), step)[0]) if step.kind == "choose" else None
        what = {
            "checkin": "your check-in (carrying on as if you'd said it looks good)",
            "choose": f"your pick (going with mine: {design.recommendation.rstrip('.')})"
            if design and design.recommendation
            else "your pick (the next step picks the strongest option)",
        }.get(step.kind, step.name)
        await self.scheduler.say(project, f"Skipped {what}, from the status page.", notice=True, job=job, step=step.id or None)
        return f"Skipped {plan.label(step)}."

    async def restore(self, job_id: int, step_id: int, position: int = 0) -> str:
        job, project = self._job(job_id)
        step = self.step(job, step_id, position)
        if not self.restorable(job, step):
            raise ControlError("That skip can't be undone any more: the steps after it have started.")
        if not step.id:
            self._block_settings(job, step, skip=None)
        else:  # what walking on from it wrote goes, and it's the job's next step again
            self.store.drop_steps_after(job.id, step.position)
            step = self.store.update_step(step.id, state="pending", summary=None, port="", ended_at=None)
            if job.state == "done":
                self.store.update_job(job.id, state="queued")
        await self.scheduler.say(project, f"Put {plan.label(step)} back in, from the status page.", notice=True, job=job, step=step.id or None)
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

    def workers(self, value: str) -> str:
        """How many steps may run at once, across projects. Fewer than are running: those finish first."""
        if not value.isdigit() or not 1 <= int(value) <= MAX_WORKERS:
            raise ControlError(f"Pick 1 to {MAX_WORKERS} workers.")
        self.store.put("workers", str(int(value)))
        self.scheduler.wake()
        running = len(self.scheduler.running)
        if int(value) < running:
            return f"Up to {value} at once: the {running} steps running now finish first."
        return "One step at a time." if value == "1" else f"Up to {value} steps at once, across projects."

    async def kill(self, job_id: int) -> str:
        job, project = self._job(job_id)
        riding = shipments.rides(self.store, job)
        self.scheduler.kill(job.id)
        note = ""
        if riding is not None and shipments.gathered(self.store, riding):  # merged into the carrier's branch already
            note = f" Its work was already merged in to ship with job {riding.carrier}'s, so it still goes out with it."
        await self.scheduler.say(project, f"Stopped job {job.id}, from the status page.{note}", notice=True, job=job)
        return f"Stopped job {job.id}.{note}"

    async def setting(self, slug: str, name: str, value: str) -> str:
        """A project's model or effort for all its agent steps, "" for each step's default; its tag, "" for
        none; its default mode (plan.MODES), "" for none; how often golem looks for improvements to propose, ""
        for never; how many proposals may await Riggy before it stops; or how many changes may be
        under way at once (turns.py). The page's flash says what changed."""
        project = self._project(slug)
        if name == "mode":  # what its requests run in unless they ask for another: only Riggy sets it
            if value and value not in plan.MODES:
                raise ControlError(f"There's no mode called {value}.")
            self.store.update_project(slug, mode=value)
            said = f"{project.name}: requests run in {value + ' mode' if value else 'the usual way'} unless they ask for another"
            job = self.store.active_job(slug, questions=False)
            if job and not job.graph and not self.store.steps(job.id):  # not started: it runs in that mode too
                self.store.update_job(job.id, mode=value)
                said += f"; job {job.id}, not started yet, too"
            return said + "."
        if name == "parallel_jobs":
            if not value.isdigit() or int(value) not in CHANGES_AT_ONCE:
                raise ControlError(f"Pick {CHANGES_AT_ONCE[0]} to {CHANGES_AT_ONCE[-1]} changes at once.")
            self.store.update_project(slug, parallel_jobs=int(value))
            said = "One change at a time" if value == "1" else f"Up to {value} changes at once, taking turns building and shipping together"
            if len(under_way := self.store.change_jobs(slug)) > int(value):
                said += f" (the {len(under_way)} under way carry on)"
            self.scheduler.wake()
            return f"{project.name}: {said[0].lower()}{said[1:]}."
        if name == "proposals_max":
            if not value.isdigit() or int(value or 0) not in WAITING:
                raise ControlError(f"Pick how many: {', '.join(map(str, WAITING))}.")
            self.store.update_project(slug, proposals_max=int(value))
            self.scheduler.wake()
            return f"{project.name}: up to {value} proposals waiting on you."
        if name == "proposals":
            every = value or "off"
            if every not in EVERY:
                raise ControlError(f"Proposals can't be {value}.")
            self.store.update_project(slug, proposals=every)
            said = "No proposals now" if every == "off" else f"Proposals {every}, when there's nothing else to do"
            self.scheduler.wake()
            return f"{project.name}: {said.lower()}."
        if name == "tag":
            tag = self._tag(value) if value else None
            self.store.update_project(slug, tag=tag.name if tag else "")
            return f"{project.name}: {tag.name if tag else 'no tag'}."
        _check(name, value)
        self.store.update_project(slug, **{name: value})
        said = f"{name.title()} is now {value}" if value else f"{name.title()} is each step's default again"
        return f"{project.name}: {name} {value or 'by step'}, from the next agent step."

    async def proposal_kind(self, slug: str, name: str, on: bool) -> str:
        """Turn one kind of proposal (a lens) on or off for the project. One stays on: "How often"
        is what turns proposals off."""
        project = self._project(slug)
        if not any(x.name == name for x in LENSES):
            raise ControlError(f"There's no kind of proposal called {name}.")
        off = {x.name for x in LENSES} - {x.name for x in kinds(project)}
        off = off - {name} if on else off | {name}
        if len(off) == len(LENSES):
            raise ControlError("Leave one kind on: to stop proposals, set How often to off.")
        self.store.update_project(slug, proposals_off=",".join(x.name for x in LENSES if x.name in off))
        said = f"Proposals can look at its {name} again" if on else f"No proposals about its {name} now"
        await self.scheduler.say(project, f"{said}, from the status page.", notice=True)
        self.scheduler.wake()
        return f"{project.name}: {said[0].lower()}{said[1:]}."

    async def step_setting(self, job_id: int, step_id: int, position: int, name: str, value: str) -> str:
        """One upcoming agent step's model or effort; "" to go back to the defaults."""
        job, project = self._job(job_id)
        step = self.step(job, step_id, position)
        if not self.configurable(job, step):
            raise ControlError("Only agent steps that haven't started can change.")
        _check(name, value)
        if step.id:
            step = self.store.update_step(step.id, **{name: value or None})
        else:  # still to come: kept for its block, and given to it when it's written
            self._block_settings(job, step, **{name: value or None})
            step = replace(step, **{name: value or None})
        engine = plan.engine(self.scheduler.settings, self.store, project, step, plan.node(self.store.job(job.id), step))
        await self.scheduler.say(project, f"{step.name} will run on {engine}, from the status page.", notice=True, job=job, step=step.id or None)
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

    def block_text(self, stage: str, part: str, text: str) -> str:
        """A stage part's text, as Riggy wrote it: every block of the stage uses it from the next job."""
        found = stages.catalog().get(stage)
        if found is None:
            raise ControlError("There's no stage like that.")
        if not text.strip():
            raise ControlError("Write something, or put golem's text back.")
        try:
            self.scheduler.library.blocks.save(stage, part, text[:20_000])
        except KeyError:
            raise ControlError(f"{found.label} has no part {part}.") from None
        return f"Saved {found.label}'s {part}: new jobs use it."

    def block_reset(self, stage: str, part: str) -> str:
        found = stages.catalog().get(stage)
        if found is None:
            raise ControlError("There's no stage like that.")
        self.scheduler.library.blocks.reset(stage, part)
        return f"{found.label}'s {part} is golem's again."

    def rename(self, slug: str, name: str) -> str:
        """A project's name; its address stays (bot.move changes that)."""
        project = self._project(slug)
        name = " ".join(name.split())[:60]
        if not name:
            raise ControlError("Give it a name.")
        self.store.update_project(slug, name=name)
        return f"Renamed {project.name} to {name}. Its address stays {slug}."

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
        once its project has room for one (one at a time, unless it allows more)."""
        p = self.store.proposal(proposal_id)
        if p is None or p.state != "open":
            raise ControlError("That proposal has been decided already.")
        reason = " ".join(reason.split())[:1000]
        if p.slug == INTERNAL:  # a fix to golem itself (triage.py): Riggy makes it, so nothing's built
            if not accepted and not reason:
                raise ControlError("Say why you're turning it down: golem's triage learns from it.")
            p = self.store.decide_proposal(p.id, accepted, reason)
            self.store.decide_reports(p.id, "accepted" if accepted else "dismissed")
            await self.scheduler.chat.heard(STATUS, f"{'👍' if accepted else '👎'} {p.title}" + (f": {reason}" if reason else ""))
            if accepted:
                return f"Took “{p.title}”: paste its prompt into a Claude Code session in golem's repo."
            return f"Turned down “{p.title}”. golem's triage takes that into account next time."
        p = self.store.decide_proposal(p.id, accepted, reason)
        project = self._project(p.slug)
        await self.scheduler.chat.heard(project.slug, f"{'👍' if accepted else '👎'} {p.title}" + (f": {reason}" if reason else ""))
        await self.scheduler.record_decision(p)
        if not accepted:
            return f"Turned down “{p.title}”. golem takes that into account next time."
        await self.scheduler.build_accepted()
        p = self.store.proposal(p.id)
        if p.built_by:
            return f"Accepted “{p.title}”: job {p.built_by} builds it."
        return f"Accepted “{p.title}”: it's built once {project.name}'s current job is done."

    def upgrade(self) -> str:
        message = self.scheduler.upgrades.request()
        self.scheduler.wake()
        return message

    async def check_version(self) -> str:
        v = await self.scheduler.upgrades.check(fetch=True)
        if v is None:
            return "Couldn't reach the deployer to check."
        if blocked := self.scheduler.upgrades.blocked(v):
            return blocked
        n = v.get("behind", 0)
        return f"{n} new commit{'s' if n != 1 else ''} on GitHub." if n else "golem is up to date."

    def set_triage(self, on: bool) -> str:
        """Whether golem triages the reports about itself when it's idle (triage.py)."""
        self.store.put("triage", triage.ON if on else "off")
        if on:
            return "golem triages the reports about itself when it has nothing else to do."
        return "golem keeps the reports about itself, and doesn't triage them."

    def auto_upgrade(self, on: bool) -> str:
        self.store.put("auto_upgrade", "1" if on else "0")
        if on:
            return "golem upgrades itself when it's behind and has no work going."
        return "golem only upgrades when you say."

    async def note(self, job_id: int, text: str, step_id: int = 0, position: int = 0) -> str:
        job, project = self._job(job_id)
        text = text.strip()
        if not text:
            raise ControlError("Write something first.")
        if step_id or position:  # context for one upcoming step
            step = self.step(job, step_id, position)
            if not self.configurable(job, step):
                raise ControlError("That step has started: send a note to the job instead.")
            if step.id:
                self.store.add_note(job.id, text[:4000], step.id)
            else:  # still to come: for its block, whenever the job gets there
                self.store.add_note(job.id, text[:4000], node_id=step.node_id)
            await self.scheduler.chat.heard(project.slug, f"For {step.name}: {text[:4000]}", job=job.id, step=step.id or None)
            return f"{step.name} gets that when it starts."
        self.store.add_note(job.id, text[:4000])
        await self.scheduler.chat.heard(project.slug, text[:4000], job=job.id)
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
