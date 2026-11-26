"""The scheduler: worker slots, each running one job's current step.

A job walks its own copy of a workflow (engine.py), taken when it starts: each step is a
visit to a block, and the output the block leaves by decides the next; nothing else does.
Agent blocks are headless Claude Code runs that do one piece of work and commit it (and,
with several outputs, choose the way on). Every other block is golem's own code: setting
up the repo, pushing, pull requests, waiting for CI, merging, deploying, asking Riggy,
showing him options to pick from (options.py), scripts, counters, conditions and telling
him it's done. Jobs that only look (a question's, proposals') commit, push and deploy
nothing.

With nothing else to do, golem looks for improvements to propose on projects that want
them, and builds the ones Riggy accepts once their project is free (proposals.py).

An upgrade of golem itself waits for the running steps to finish, and holds new steps
until it's done (upgrade.py).

When CI fails or the deployer refuses a commit, the block leaves by its `fail` output: in
golem's built-ins, to a fix and back to the push (workflows/ship.yml). Each step's outcome
is saved, so a crash or a usage limit costs at most the step that was running. Catching up
with main before a turn at building or shipping (turns.py), and merging it in when that
conflicts, are steps golem puts in itself.

Adopting a repo golem didn't make starts with `vet` (adopt.py): golem's own checks and a
survey that changes nothing. Something blocking and golem forgets the project and says why;
otherwise the job goes on like any other that ships.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import traceback
from contextvars import ContextVar
from string import Template
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import about, adopt, checklist, engine, options, plan, proposals, reports, shipments, stages, teardown, triage, turns
from .workflows import Library
from .cards import Cards
from .config import Settings
from .deployer_client import Deployer, DeployerError
from .harness import VERDICT_SCHEMA, VERDICTS, WRAPUP_SCHEMA, Harness, RunResult, wrapup_schema
from .history import records
from .talk import STATUS, Talk
from .upgrade import Upgrades
from .prompts import Prompts
from .store import ACTIVE, INTERNAL, LOOKS, MAX_WORKERS, TRIAGE, Job, Project, Proposal, Shipment, Step, Store, later, now
from .watch import Steps, Tail, span
from .workspace import JOB_BRANCHES, Workspace, WorkspaceError

log = logging.getLogger(__name__)
# The job and step a worker slot is on: what golem says while it's on them is their activity.
DOING: ContextVar[tuple[int | None, int | None]] = ContextVar("doing", default=(None, None))

WRAPUP_TURNS = 15
FIX_TURNS = 150
TEST_TIMEOUT = 1200  # seconds a Test block gives bin/test
SCRIPT_TAIL = 4096  # bytes of a failed script's output the next agent block gets
SURVEY_TURNS = 100  # install, run the tests, read for what it needs
# With HARNESS=fake there's no worker to survey a repo golem adopts: its own checks still run.
FAKE_SURVEY = {"tests": {"result": "passed", "command": "", "detail": "not run (HARNESS=fake)"}, "missing": [], "needs": []}
# How long a step that failed waits before each try again; after the last, golem asks Riggy. Waiting
# frees its worker slot for other jobs, so a longer wait costs this job time, not the queue; it gives
# a blip at GitHub, Anthropic or npm time to pass.
ERROR_BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
MAX_ATTEMPTS = len(ERROR_BACKOFF) + 1  # failed attempts at one step before asking Riggy for help
RESOLVE_TURNS = 150
MAX_RESOLVES = 3  # resolve steps in one job (conflicts with main) before asking Riggy for a hint
LIMIT_RETRY = timedelta(minutes=30)
CI_POLL = timedelta(seconds=20)  # how often CI is checked while a step waits for it
CI_START_TIMEOUT = timedelta(minutes=10)  # no workflow run on a commit by then: the repo has no CI
CI_TIMEOUT = timedelta(minutes=60)  # CI started but still not finished: something's wrong at GitHub
IDLE_POLL_SECONDS = 30
# Each slot's localhost ports for dev servers: GOLEM_PORT to GOLEM_PORT+9 (worker_env), clear of
# the usual defaults (3000, 4173, 5173, 8080) so a worker that forgets still can't take another's.
PORTS = 20000
PORTS_PER_SLOT = 100
MONITOR_SECONDS = 15  # how often a running agent step is checked on
HEARTBEAT_SECONDS = 20 * 60  # no news from an agent step this long: say what it's on
# Nothing logged this long and the run is stopped and retried. Claude Code logs while it
# thinks, and its commands time out after ten minutes, so a live run is never this quiet.
STUCK_SECONDS = 30 * 60
# Never handed to workers: only Riggy deleting a project deletes its data, and only he
# changes jobs on the status page.
WORKER_ENV_DENY = frozenset({"DEPLOYER_ADMIN_SECRET", "GOLEM_WEB_PASSWORD"})
# Golem's own steps whose results are posted in the project's conversation (for a pick, how his
# reply was read). The rest (setup, push, CI, merge) show on the status page; failures are
# always posted.
ANNOUNCED = {"pr", "deploy", "choose"}
# Steps that work in the job's tree (made by setup, vet or refresh, the first step).
USES_TREE = ("agent", "test", "check", "push", "merge", "deploy", "alert", "sync")
TREE_STEPS = ("setup", "vet", "agent", "test", "check", "sync", "push", "merge")  # steps after which the branch's commit is kept


@dataclass
class Running:
    """A job whose current step is running: its project, the worker slot it has, its task."""

    slug: str
    slot: int
    task: asyncio.Task


@dataclass
class Outcome:
    """How a step went.

    done, skipped: on to the next step.  wait: check again shortly (CI is running).
    fix: something the project's code has to fix; a fix step and a retry follow.
    error: try the step again later, then ask Riggy.  limit: out of Claude usage.
    blocked: the worker asked Riggy a question.  checkin: the step waits for his reply (a
    check-in, or his pick of some options).  refused: golem won't adopt the repo; `problem`
    says why, and the project goes.  resolve: main moved under the job and conflicts with its
    work; a resolve step merges them, and the catch-up goes again.
    """

    state: str
    summary: str = ""
    links: dict[str, str] = field(default_factory=dict)
    problem: str = ""
    session_id: str | None = None
    port: str = ""  # the block's output it leaves by; "" for its usual one (its first)
    counters: dict[str, int] = field(default_factory=dict)  # counters it sets, at its level (vet's gaps)
    context: str | None = None  # what it found for the next agent block, if anything
    said: str = ""  # what golem tells Riggy about it, where that isn't the usual
    detail: str = ""  # for an error: all a failed command printed, for the step's log


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        chat: Talk,
        harness: Harness,
        workspace: Workspace,
        prompts: Prompts,
        deployer: Deployer,
    ):
        self.settings = settings
        self.store = store
        self.chat = chat
        self.harness = harness
        self.workspace = workspace
        self.prompts = prompts
        self.deployer = deployer
        self.cards = Cards(settings, store, harness)
        self.upgrades = Upgrades(store, deployer, chat)
        # The workflows jobs run (workflows.py), signed with a secret workers never get.
        self.library = Library(store, settings.process_dir / "workflows", settings.web_password)
        self.running: dict[int, Running] = {}  # by job id
        self.shipping_out: set[int] = set()  # shipments whose riders are being seen out (ship_out)
        self._wake = asyncio.Event()

    # --- control --------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self.store.get("scheduler_paused") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.put("scheduler_paused", "1" if paused else "0")
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    @property
    def workers(self) -> int:
        """How many steps may run at once, across projects (set on the settings page). One by default."""
        value = self.store.get("workers") or "1"
        return min(max(int(value), 1), MAX_WORKERS) if value.isdigit() else 1

    def running_jobs(self) -> list[Job]:
        """The jobs whose current step is running, in the order they started."""
        return [job for job in map(self.store.job, self.running) if job is not None]

    def kill(self, job_id: int) -> bool:
        """Stop a job for good. Returns False if it had already finished."""
        job = self.store.job(job_id)
        if job is None or job.state in ("done", "failed", "killed"):
            return False
        self.store.update_job(job_id, state="killed")
        if job_id in self.running:
            self.running[job_id].task.cancel()
        for s in self.store.shipping(job.slug):  # it was shipping others' changes with its own: they're ready again
            if s.carrier == job_id:
                self.store.end_shipment(s.id, "dropped")
                log.info("shipment %s dropped: job %s stopped", s.id, job_id)
        self.wake()
        return True

    async def drop_project(self, slug: str) -> None:
        """Kill the project's unfinished jobs and wait for its running steps to stop."""
        tasks = {r.task for r in self.running.values() if r.slug == slug}
        for job in self.store.jobs(ACTIVE, limit=10_000):
            if job.slug == slug:
                self.kill(job.id)
        if tasks:
            await asyncio.wait(tasks)

    # --- loop -----------------------------------------------------------------

    async def run_forever(self) -> None:
        for job in self.store.requeue_interrupted():
            log.info("job %s was interrupted; it carries on from its current step", job.id)
        for job in self.store.jobs(ACTIVE, limit=10_000):  # under way from before workflows: a graph of their own
            if not job.graph and (self.store.steps(job.id) or self.store.runs(job.id)):
                try:
                    self.ensure_started(job)
                except Exception:
                    log.exception("job %s from before workflows couldn't carry on as one", job.id)
                    self.store.update_job(job.id, state="waiting")
        self.backfill = asyncio.create_task(self.missing_cards())
        self.versions = asyncio.create_task(self.upgrades.watch_forever())
        try:
            while True:
                await self.tidy()  # what jobs that ended left
                await self.ship_out()  # the jobs that rode along in a shipment that's over
                if self.upgrades.holding():  # nothing new starts; the upgrade goes once nothing's running
                    if self.upgrades.state == "pending" and not self.running:
                        await self.upgrades.start()
                    await self.idle()
                    continue
                if not self.paused:
                    await self.build_accepted()
                    if not self.fill() and (self.propose() or self.triage_reports()) is not None:
                        continue
                await self.idle()
        except asyncio.CancelledError:  # golem is shutting down; the jobs carry on when it starts
            for r in list(self.running.values()):
                r.task.cancel()
            raise

    def pick(self, at: str | None = None) -> Job | None:
        """The next job to start a step of: the highest priority queued one that can go. On a
        project that allows several changes at once, a job goes unless another has the turn its
        step needs (turns.py); otherwise a project's steps run one at a time."""
        busy = {r.slug for r in self.running.values()}
        for job in self.store.ready_jobs(at):
            if job.id in self.running or turns.behind(self.store, job) is not None:  # it waits to start: in order
                continue
            if shipments.rides(self.store, job):  # another job's ship steps are shipping its work (shipments.py)
                continue
            if self.side_by_side(job):
                if turns.blocked_by(self.store, job, set(self.running)) is None:
                    return job
            elif job.slug not in busy:
                return job
        return None

    def side_by_side(self, job: Job) -> bool:
        """Whether the job's steps can run beside others on its project: each job in a tree of
        its own, on a project that allows more than one change at once, with nothing from before
        trees under way there."""
        project = self.store.project(job.slug)
        if not job.worktree or project is None or project.parallel_jobs <= 1:
            return False
        others = self.store.change_jobs(job.slug) + [j for j in self.running_jobs() if j.slug == job.slug]
        return all(j.worktree for j in others)

    def fill(self) -> bool:
        """Start steps while there's a free worker slot and a job to give it. Whether any started.
        With fewer slots than are running (the setting was lowered), nothing new starts."""
        started = False
        while len(self.running) < self.workers and (job := self.pick()) is not None:
            self.dispatch(job)
            started = True
        return started

    def dispatch(self, job: Job) -> None:
        """Start the job's current step in the lowest free worker slot. Made ready here, before
        the next pick, so that pick sees the turn it takes."""
        self.prepare(job)
        taken = {r.slot for r in self.running.values()}
        slot = next(i for i in range(len(taken) + 1) if i not in taken)
        self.running[job.id] = Running(job.slug, slot, asyncio.create_task(self.work(job)))

    async def work(self, job: Job) -> None:
        """A step in its slot, giving the slot back when it ends however it ends."""
        try:
            await self.step(job)
        except asyncio.CancelledError:
            log.info("job %s stopped", job.id)
            raise
        except Exception as e:
            log.error("job %s crashed", job.id, exc_info=e)
            await self.retry_or_ask(job, f"golem error: {e!r}", traceback.format_exc())
        finally:
            self.running.pop(job.id, None)
            self.wake()

    async def idle(self) -> None:
        """Wait to be woken, or IDLE_POLL_SECONDS."""
        try:
            await asyncio.wait_for(self._wake.wait(), IDLE_POLL_SECONDS)
        except TimeoutError:
            pass
        self._wake.clear()

    def propose(self) -> Job | None:
        """With nothing else to do (a worker spare), a proposals job for the project most overdue for one."""
        project = proposals.due(self.store, datetime.now(UTC), spare=len(self.running) < self.workers)
        if project is None:
            return None
        job = proposals.start(self.store, project)
        log.info("job %s: %s on %s", job.id, job.request, project.slug)
        return job

    def triage_reports(self) -> Job | None:
        """With nothing else to do (a worker spare), golem's triage of the reports about itself, if
        it's on and they're due one (triage.py)."""
        if not triage.due(self.store, datetime.now(UTC), spare=len(self.running) < self.workers):
            return None
        job = triage.start(self.store)
        log.info("job %s: %s", job.id, job.request)
        return job

    async def build_accepted(self) -> None:
        """A change job for each proposal Riggy accepted, once its project has room for another
        change (one at a time, unless it allows more: turns.py)."""
        for p in self.store.unbuilt_proposals():
            project = self.store.project(p.slug)
            if project is None or not self.store.room_for_change(p.slug):
                continue
            wf = self.library.fallback("change")
            job = self.store.add_job(p.slug, "change", proposals.request(p), wf.key if wf else "feature", title=plan.clean_title(p.title))
            job = self.store.update_job(job.id, workflow=wf.key if wf else "", mode=plan.mode_for(project))
            if p.issue:  # the job works on the proposal's issue, and its PR closes it
                job = self.store.update_job(job.id, issue=p.issue)
            self.store.set_built_by(p.id, job.id)
            await self.say(project, f"Building the proposal you accepted: {p.title} (job {job.id}).", notice=True, job=job)
            self.wake()

    async def record_decision(self, p: Proposal) -> None:
        """Riggy's decision on the proposal's issue, closing it if he turned it down. Only a record."""
        if p.issue:
            await self.workspace.comment_issue(p.slug, p.issue, proposals.decision(p), close=p.state == "rejected")

    def ensure_started(self, job: Job) -> list[Step]:
        """The job's steps; the first time, its own copy of its workflow and its first step."""
        job = self.store.job(job.id) or job
        steps = self.store.steps(job.id)
        if job.state not in ACTIVE or steps:
            return steps
        job = self.pin(job)
        moved = engine.advance(plan.snapshot(job), plan.vars_of(job), [], None, None, job.id)
        return self.store.advance(job.id, None, {}, moved.rows, moved.vars)

    ensure_steps = ensure_started

    def pin(self, job: Job) -> Job:
        """The job's own copy of its workflow: nothing done to the workflow from now on changes it.
        One golem doesn't have (any more), its start type's fallback, and Riggy is told. A triage
        job took its copy when it was made, from golem's code (triage.py): no workflow is one."""
        if job.start == TRIAGE:
            if job.graph:
                return job
            snap = triage.snapshot()
            return self.store.update_job(job.id, workflow="triage", graph=json.dumps(snap), graph_hash=engine.digest(snap))
        project = self.store.project(job.slug)
        key, mode = plan.workflow_for(job, project)
        wf = self.library.get(key)
        if wf is None or wf.start != job.start:
            fallback = self.library.fallback(job.start)
            if fallback is None:
                raise engine.GraphError(f"golem has no workflow for {job.start} jobs")
            log.warning("job %s: no workflow %s for it; using %s", job.id, key, fallback.key)
            self.store.add_message(job.slug, "golem", f"Job {job.id} couldn't use {key or 'its workflow'}, so it's running {fallback.name}.", job_id=job.id)
            wf = fallback
        snap = self.library.pin(wf, job.start)
        return self.store.update_job(job.id, workflow=wf.key, mode=mode, graph=json.dumps(snap), graph_hash=engine.digest(snap))

    def insert(self, job_id: int, steps: list[Step], after: int) -> None:
        """Steps golem puts in where they're needed (a fix, a catch-up with main, another round),
        after the step at position `after`, each with the turn it takes (plan.turn_of): one put in
        is part of the turn of the step it comes before."""
        self.store.add_steps(job_id, steps, after=after)
        steps = self.store.steps(job_id)
        for s in steps:
            if not s.turn:
                self.store.update_step(s.id, turn=plan.turn_of(steps, s))

    def prepare(self, job: Job) -> Step | None:
        """The job's current step, ready to run: the job's steps written the first time, a catch-up
        with main put in where its turn at building or shipping begins (turns.py), the other changes
        ready to ship going with it if it's shipping (shipments.py), the job running and its step
        started. None if it has no step left."""
        steps = self.ensure_steps(job)
        step = plan.current(steps)
        if step is None:
            return None
        if self.needs_sync(job, steps, step):
            self.insert(job.id, [Step(0, job.id, 0, "sync", "sync", turn=plan.turn_of(steps, step))], after=step.position - 1)
            steps = self.store.steps(job.id)
            step = plan.current(steps)
            if plan.turn_of(steps, step) == "ship" and (found := shipments.form(self.store, self.store.job(job.id), step.id)):
                log.info("job %s ships jobs %s with it", job.id, ", ".join(map(str, found.riders)))
        self.store.update_job(job.id, state="running")
        if step.started_at is None:
            step = self.store.update_step(step.id, state="running", started_at=now())
        return step

    def needs_sync(self, job: Job, steps: list[Step], step: Step) -> bool:
        """Whether a change's branch should catch up with main before this step: the first of a
        turn at building or shipping, on a project where changes take turns. Others may have
        shipped since it branched; building and shipping start from what's live."""
        if job.kind != "change" or job.start in LOOKS or not job.worktree or step.started_at or step.kind == "sync":
            return False
        project = self.store.project(job.slug)
        if project is None or project.parallel_jobs <= 1 or plan.turn_of(steps, step) not in turns.TURNS:
            return False
        return plan.run_before(steps, step)[0].position == step.position  # a catch-up before it would be in its turn

    async def step(self, job: Job) -> None:
        """Do the job's current step and act on how it went."""
        project = self.store.project(job.slug)
        step = self.prepare(job)
        if step is None:
            self.store.update_job(job.id, state="done")
            return
        job = self.store.job(job.id)
        DOING.set((job.id, step.id))  # what's said from here on is this job's, at this step
        if await self.guarded(job, project, step):
            return
        try:
            if job.worktree and step.kind in USES_TREE:  # made by its first step; back if it was lost
                await self.workspace.ensure_tree(project.slug, job.id)
            outcome = await getattr(self, "do_" + step.kind.replace("-", "_"))(job, project, step)
        except (WorkspaceError, DeployerError) as e:
            outcome = Outcome("error", problem=str(e), detail=getattr(e, "detail", ""))
        if self.store.job(job.id).state == "killed":
            return
        if outcome.state in ("done", "skipped", "failed") and job.worktree and step.kind in TREE_STEPS:
            try:  # where the job's branch is now: what came after it is told from here (about.py)
                self.store.update_step(step.id, head=await self.workspace.head(project.slug, job=self.tree(job)))
            except WorkspaceError:
                pass
        await self.settle(self.store.job(job.id), project, self.store.step(step.id), outcome)

    async def settle(self, job: Job, project: Project, step: Step, outcome: Outcome) -> None:
        name = plan.label(step)
        match outcome.state:
            case "done" | "skipped" | "failed":  # on to the block its output is wired to
                state = outcome.state
                closed = {"state": state, "summary": outcome.summary, "links": outcome.links, "ended_at": now(),
                          "error": outcome.problem if state == "failed" else None}  # fmt: skip
                finished, steps = self.walk_on(job, step, outcome, closed)
                self.store.update_job(job.id, state="done" if finished else "queued", attempts=0, resume_session=None)
                quiet = job.start in LOOKS  # its last step says it
                nxt = plan.current(steps)
                if state == "failed" and not (nxt and nxt.kind == "checkin"):  # a check-in after says it itself
                    fixing = " Fixing it." if nxt and nxt.kind == "agent" and not step.item else ""  # a checklist's check says itself
                    await self.say(project, f"✗ {name}: {outcome.summary}.{fixing}", notice=True)
                elif state == "done" and not quiet and (step.kind == "agent" or step.kind in ANNOUNCED):
                    link = next(iter(outcome.links.values()), "")
                    ahead = "" if finished else f"\nNext: {plan.upcoming(plan.planned(self.settings, self.store, self.store.job(job.id)), step)}."
                    await self.say(project, _join(f"✓ {name}: {outcome.summary}", link) + ahead, notice=True)
            case "wait":
                self.store.update_step(step.id, summary=outcome.summary, links=outcome.links)
                self.store.update_job(job.id, state="parked", retry_at=later(CI_POLL), attempts=0)
            case "resolve":  # main moved under it and conflicts: a worker merges them, then it catches up again
                self.store.update_step(step.id, state="failed", summary=outcome.summary, error=outcome.problem, ended_at=now())
                steps = self.store.steps(job.id)
                tries = sum(1 for s in steps if s.kind == "agent" and s.name == "resolve")
                resolve = Step(0, job.id, 0, "agent", "resolve", RESOLVE_TURNS, problem=outcome.problem, turn=step.turn)
                self.insert(job.id, [resolve, Step(0, job.id, 0, "sync", "sync", turn=step.turn)], after=step.position)
                if tries < MAX_RESOLVES:
                    self.store.update_job(job.id, state="queued", attempts=0)
                    said = outcome.said or f"Main moved on under {plan.title(job)}: {outcome.summary}. Merging it in."
                    await self.say(project, said, notice=True)
                else:
                    self.store.update_job(job.id, state="waiting", attempts=0)
                    self.incident(
                        job, step, ("resolve",), "friction", "blocked",
                        f"Merging main into a change still conflicted after {tries} resolve steps", outcome.problem,
                    )  # fmt: skip
                    await self.say(
                        project,
                        f"I've tried to merge main into {plan.title(job)} {tries} times and it still conflicts: "
                        f"{outcome.summary}.\n\nReply with a hint and I'll try again, or stop the job.",
                    )
            case "checkin":
                self.store.update_step(step.id, state="waiting", summary=outcome.summary or "Waiting for your reply.")
                self.store.update_job(job.id, state="waiting", attempts=0)
            case "blocked":
                self.store.update_step(step.id, summary=f"Asked you: {outcome.summary}")
                self.store.update_job(job.id, state="waiting", attempts=0, resume_session=outcome.session_id)
                await self.say(project, _join(f"Question: {outcome.summary}", outcome.links.get("link", "")) + "\n\nReply here and I'll carry on.")
            case "limit":
                self.store.update_job(job.id, state="parked", retry_at=later(LIMIT_RETRY))
                if self.store.get("limit_notice") != "1":
                    self.store.put("limit_notice", "1")
                    await self.status(f"Hit the Claude usage limit during {project.name}; parked it and retrying every 30 minutes.")
                return
            case "error":
                self.store.update_step(step.id, state="pending", error=outcome.problem, started_at=None)
                if outcome.detail:  # the whole of what failed, each try, kept where its runs' logs are
                    with self.error_log(job, step).open("a") as out:
                        out.write(f"--- {now()}: {outcome.problem}\n{outcome.detail}\n\n")
                await self.retry_or_ask(job, outcome.problem, outcome.detail)
            case "refused":  # nothing was written to the repo: golem forgets it was ever asked
                await self.status(outcome.problem)  # golem's news, on Now: the project's page goes with it
                await teardown.forget_project(project, self.settings, self.store, self.workspace)
                log.info("didn't adopt %s: %s", project.slug, outcome.summary)
                return
        self.store.put("limit_notice", "0")
        await self.ship_out()  # past its run of ship steps, it's shipped what rode along with it

    async def skip_now(self, job: Job, step: Step, summary: str) -> None:
        """Riggy skipped a step from the status page: it's passed over by its `skip_to` output."""
        await self.settle(self.store.job(job.id), self.store.project(job.slug), step, Outcome("skipped", summary))

    def walk_on(self, job: Job, step: Step, outcome: Outcome, closed: dict) -> tuple[bool, list[Step]]:
        """Close a step and write what comes after it: for a block of the job's workflow, the
        block its output is wired to (engine.advance), in the same transaction. Whether the job's
        finished, and its steps."""
        block = plan.node(job, step)
        if block is None:  # golem put it in (a catch-up with main), or from before workflows: the next is written already
            self.store.update_step(step.id, **closed)
            steps = self.store.steps(job.id)
            return plan.current(steps) is None, steps
        snap, vars = plan.snapshot(job), plan.vars_of(job)
        outs = engine.ports(block)
        port = outcome.port or (block.get("skip_to") if outcome.state == "skipped" and outcome.summary == engine.SKIPPED else "") or (outs[0] if outs else "")
        closed["port"] = port
        for name, value in outcome.counters.items():
            vars["counters"][engine.counter_key(block, name)] = value
        if outcome.context is not None:
            vars["context"] = outcome.context
        if outcome.state == "failed":  # for a check-in after it to say, and the next agent to fix
            vars["context"] = outcome.problem
            vars["failure"] = {"label": plan.label(step), "summary": outcome.summary}
        if not outs:  # an End: nothing comes after it
            steps = self.store.advance(job.id, step.id, closed, [], vars)
            return True, steps
        trace = self.store.steps(job.id)
        for s in trace:  # the step closing, as it will be, for the counts the walk makes
            if s.id == step.id:
                s.state, s.port = outcome.state, port
        moved = engine.advance(snap, vars, trace, step.node_id, port, job.id, self.guard())
        steps = self.store.advance(job.id, step.id, closed, moved.rows, moved.vars)
        return moved.finished or plan.current(steps) is None, steps

    def guard(self) -> tuple[int, int]:
        """Visits to one block, and agent runs in a job, before golem stops to ask whether to go on."""
        visits, runs = self.store.get("guard_visits") or "", self.store.get("guard_runs") or ""
        return (int(visits) if visits.isdigit() else engine.GUARD_VISITS, int(runs) if runs.isdigit() else engine.GUARD_RUNS)

    async def guarded(self, job: Job, project: Project, step: Step) -> bool:
        """Whether the loop guard stopped the job before this step (engine.advance set it): it's
        been round a loop too often, so golem asks Riggy before it spends more. His reply lets it
        go round as many times again."""
        vars = plan.vars_of(job)
        guard = vars["guard"]
        if not step.node_id or guard.get("hit") != step.node_id:
            return False
        if guard.get("asked"):  # he's replied
            vars["guard"] = {"rounds": guard.get("rounds", 0) + 1, "hit": ""}
            self.store.update_job(job.id, vars=vars)
            return False
        vars["guard"] = {**guard, "asked": True}
        self.store.update_job(job.id, vars=vars, state="waiting", attempts=0)
        steps = self.store.steps(job.id)
        runs = sum(1 for s in steps if s.kind == "agent" and s.started_at and s.id != step.id and not s.item)
        lately = [f"- {plan.label(s)} ({s.state}): {s.error or s.summary or ''}"[:300] for s in steps if s.started_at][-8:]
        self.incident(
            job, step, ("guard", job.workflow, step.name), "friction", "blocked",
            f"The loop guard stopped a job at {plan.label(step)}: {step.visit} visits, {runs} agent runs",
            "The steps before it:\n" + "\n".join(lately),
        )  # fmt: skip
        await self.say(
            project,
            f"{plan.title(job)} has come round to {plan.label(step)} {step.visit} times ({runs} agent runs so far), "
            "more than I'll go without asking. Reply to let it carry on (with a hint, if you have one), or stop the job.",
        )
        return True

    async def retry_or_ask(self, job: Job, error: str, detail: str = "") -> None:
        """A step that failed: tried again a while later, or after the last try, Riggy asked.
        Each failure is one of golem's incidents (reports.py): `detail` is all that failed said."""
        job = self.store.job(job.id)
        if job is None or job.state == "killed":  # gone with its project, or stopped
            return
        attempts = job.attempts + 1
        project = self.store.project(job.slug)
        step = plan.current(self.store.steps(job.id))
        if step is not None:
            kind = "bug" if error.startswith("golem error:") else "friction" if step.kind == "agent" else "environment"
            self.incident(
                job, step, ("retry", step.kind, step.name, reports.normalize(error, job.slug)), kind,
                "minor" if attempts < MAX_ATTEMPTS else "blocked", f"{plan.label(step)} failed: {_first_line(error, 160)}",
                f"{error}\n\n{detail}".strip(),
            )  # fmt: skip
        if attempts < MAX_ATTEMPTS:
            wait = ERROR_BACKOFF[attempts - 1]
            self.store.update_job(job.id, state="parked", attempts=attempts, retry_at=later(wait), resume_session=None)
            log.warning("job %s attempt %s failed: %s", job.id, attempts, error)
            return
        if job.start in ("propose", TRIAGE):  # nobody asked for it: no reason to interrupt Riggy, just stop
            self.store.update_job(job.id, state="failed", attempts=0, resume_session=None)
            what = "triaging the reports about golem" if job.start == TRIAGE else "looking for proposals"
            await self.say(project, f"I couldn't finish {what}: {error[:300]}", notice=True)
            return
        # Stuck after retries is one of the few reasons to interrupt Riggy.
        self.store.update_job(job.id, state="waiting", attempts=0, resume_session=None)
        name = plan.label(step) if step else "the job"
        # Only a worker reads a hint; golem's own steps (merge, deploy...) just go again.
        ask = "Reply with a hint and I'll try again" if step and step.kind == "agent" else "Reply to try again"
        await self.say(project, f"I'm stuck on {name} after {MAX_ATTEMPTS} tries: {error[:400]}\n\n{ask}, or stop the job.")

    # --- agent steps ----------------------------------------------------------

    async def do_agent(self, job: Job, project: Project, step: Step) -> Outcome:
        """One headless run that does the step's work on the job's branch and commits it. A
        question's or a proposal's looks at the checkout as it is (main, or a job's work under
        way), and anything it changes there is put back."""
        block = plan.node(job, step) or {}
        looking, tree = job.start in LOOKS, self.tree(job)
        if project.slug == INTERNAL:  # golem's own job: no repo, a scratch directory (checkout)
            branch, was_clean = "", False
        elif looking:
            branch = await self.workspace.current_branch(project.slug, job=tree)
            was_clean = await self.workspace.clean(project.slug, job=tree)
        else:
            branch = self.branch(job)
            await self.workspace.start_branch(project.slug, branch, job=tree)
        notes = self.store.pending_notes(job.id, step.id, step.node_id)
        feedback = [text for _, text in notes]
        intro, instructions = self.instructions(job, step, block)
        outputs = [o for o in block.get("outputs") or [] if not block.get("contract")]
        if job.resume_session:
            prompt, resume = self.prompts.resume(feedback), job.resume_session
        elif job.start == "propose":
            jobs, said = self.history(project, job)
            earlier = [p for p in self.store.proposals(project.slug, limit=proposals.PAST_SHOWN) if p.job_id != job.id]
            lens = proposals.lens(self.store, job)
            prompt, resume = self.prompts.proposals(project, job, intro, instructions, step, branch, lens, jobs, said, earlier), None
        elif job.start == "look":
            busy = self.store.active_job(project.slug, questions=False) is not None
            jobs, said = self.history(project, job)
            prompt, resume = self.prompts.question(project, job, intro, instructions, step, branch, busy, jobs, said, feedback), None
        elif job.start == TRIAGE:
            prompt, resume = self.triage_prompt(job, project), None
        else:
            lessons_path = self.lessons_path(project)
            lessons = lessons_path.read_text() if lessons_path.exists() else ""
            steps = plan.shown(self.store.steps(job.id))
            prompt, resume = self.prompts.step(project, job, intro, instructions, step, steps, branch, feedback, lessons, outputs,
                                               self.after(job, step, outputs), lessons_path), None  # fmt: skip

        contract = block.get("contract") or ""
        schema = {"options": options.SCHEMA, "design": options.DESIGN_SCHEMA, "propose": proposals.SCHEMA}.get(contract)
        planning = checklist.planning(job.mode, block, step)  # a Build in checklist mode plans its items
        if planning:
            schema = checklist.SCHEMA
        if job.start == TRIAGE:
            schema = triage.SCHEMA
        if outputs:  # its verdict is the output it takes, or blocked
            schema = {**VERDICT_SCHEMA, "properties": {**VERDICT_SCHEMA["properties"], "verdict": {"enum": [*(o["name"] for o in outputs), "blocked"]}}}
        turns = step.max_turns or FIX_TURNS
        result = await self.execute(job, project, step, step.name, turns, prompt, resume, schema, block=block)
        if result.outcome == "max_turns" and result.session_id:
            wrapped = wrapup_schema(schema)
            wrapup = await self.execute(
                job, project, step, f"{step.name}-wrapup", WRAPUP_TURNS, self.prompts.wrapup(bool(schema)), result.session_id, wrapped
            )
            said = str((wrapup.verdict or {}).get("message", "")).strip()
            self.incident(
                job, step, ("max_turns", job.workflow, step.name), "friction", "costly",
                f"{plan.label(step)} ran out of its {turns} turns", f"What it said it had left: {said or '(nothing)'}",
            )  # fmt: skip
            if wrapup.outcome != "verdict":
                result = wrapup
            elif wrapup.verdict.get("finished") is True:  # with what the step made, if it says
                made = {k: v for k, v in wrapup.verdict.items() if k not in WRAPUP_SCHEMA["properties"]}
                result = RunResult("verdict", {**made, "verdict": "done", "message": said}, session_id=wrapup.session_id)
            else:
                result = RunResult("error", error=f"ran out of turns: {said}")
        if looking and was_clean:
            await self.workspace.discard(project.slug, job=tree)
        if result.outcome == "limit":
            return Outcome("limit")
        if result.outcome != "verdict":
            return Outcome("error", problem=result.error or result.outcome)

        v = result.verdict
        allowed = [*(o["name"] for o in outputs), "blocked"] if outputs else VERDICTS
        if v.get("verdict") not in allowed:
            return Outcome("error", problem=f"unknown verdict {v.get('verdict')!r}")
        port = v["verdict"] if outputs and v["verdict"] != "blocked" else ""
        if contract in ("options", "design") and v["verdict"] == "done":
            problem = self.keep_options(job, project, step, v, contract)
            if problem:
                return Outcome("error", problem=problem)
            if contract == "design":
                port = "nothing" if options.design_of(self.store.step(step.id)).skip else "directions"
        self.store.consume_notes([i for i, _ in notes])
        message, link = str(v.get("message", "")).strip(), str(v.get("link") or "").strip()
        links = {"link": link} if link.startswith(("https://", "http://")) else {}
        if job.start == "propose":  # nobody is waiting to answer: a question back is nothing to propose
            made = self.store.add_proposals(project.slug, job.id, proposals.lens(self.store, job).name, proposals.collect(v.get("proposals")))
            return Outcome("done", message or f"Proposed {len(made)}.", links)
        if job.start == TRIAGE:  # nor here
            read = triage.shown(self.store, job)
            found, said = triage.collect(v.get("proposals"), v.get("notes"), read)
            made = self.store.add_triaged(job.id, triage.upto(job), found, said)
            await self.say(project, triage.announcement(len(read), made, f"{self.settings.url('golem')}/reports"), notice=True)
            return Outcome("done", message or f"Proposed {len(made)} fix(es) to golem.", links)
        if v["verdict"] == "blocked":
            return Outcome("blocked", message, links, session_id=result.session_id)
        if not looking:
            await self.workspace.commit_all(project.slug, f"{step.name}: work left uncommitted", job=tree)
        if planning:
            items, problem = self.plan_items(job, project, step, block, v)
            if problem:
                return Outcome("error", problem=problem)
            message = message or f"Planned {items} item(s)."
        if step.item:
            message = f"{checklist.name(step.item)}: {message}"
        passed_on = message if block.get("stage") and port == "fail" else None  # a Review that reports: what to fix, for the next block
        return Outcome("done", message, links, port=port, context=passed_on)

    def plan_items(self, job: Job, project: Project, step: Step, block: dict, v: dict) -> tuple[int, str]:
        """A checklist's plan (checklist.py): its items and check files kept, and a build and a
        check put in after the planner for each, building on the item default and, for an item's
        last try, on this Build block's model. How many items; or what's wrong with the plan."""
        items, checks, problem = checklist.collect(v)
        if problem:
            return 0, problem
        where = checklist.folder(self.settings.job_logs(job.id), step.id)
        kept = checklist.keep(where, self.checkout(project, job), items, checks)
        model, effort = self.store.defaults().get("item", ("", ""))
        up = plan.engine(self.settings, self.store, project, step, block)
        rows = checklist.item_steps(step, items, kept, (model or checklist.DEFAULT[0], effort or checklist.DEFAULT[1]), (up.model, up.effort))
        stale = [s.id for s in self.store.steps(job.id) if s.item.get("plan") == step.id and not s.started_at]  # put in before a crash
        self.store.drop_steps(job.id, stale)
        self.insert(job.id, [replace(r, job_id=job.id) for r in rows], after=step.position)
        return len(items), ""

    def triage_prompt(self, job: Job, project: Project) -> str:
        """A triage step's prompt (triage.py), the reports it reads written out in full where it works."""
        read = triage.shown(self.store, job)
        path = self.checkout(project, job) / "reports.json"
        triage.write_reports(path, read)
        earlier = [p for p in self.store.proposals(INTERNAL, limit=proposals.PAST_SHOWN) if p.job_id != job.id]
        lines = [triage.line(r) for r in read]
        return self.prompts.triage(lines, path, path.parent, triage.SOURCE, self.version(), earlier, triage.MAX_PER_RUN)

    def after(self, job: Job, step: Step, outputs: list[dict]) -> str:
        """For the worker: what comes after its step, as the job's workflow has it, so its
        instructions' words about the next steps can be checked against what's really next."""
        if outputs:
            return "What comes next depends on the verdict you choose (below)."
        ahead = plan.upcoming(plan.planned(self.settings, self.store, job), step)
        if ahead == "nothing":
            return "Yours is the job's last step."
        return f"After your step: {ahead}."

    def instructions(self, job: Job, step: Step, block: dict) -> tuple[str, str]:
        """The text every agent block of the job's workflow gets, and this step's own: its block's
        instructions, with what the blocks before it found (a failure to fix, an adoption's gaps)
        put in at $problem or added after. Steps golem puts in itself (merging main in, a fix
        from before workflows) have theirs in prompts/."""
        snap = plan.snapshot(job) or {}
        intros = snap.get("intros") or {}
        intro = intros.get("", "")
        if block.get("path") and intros.get(block["path"]):  # a sub-workflow's own text, after the job's
            intro = f"{intro}\n\n{intros[block['path']]}".strip()
        if not block:
            if step.item:  # a checklist item's build (checklist.py)
                where = checklist.folder(self.settings.job_logs(job.id), int(step.item.get("plan") or 0))
                return intro, self.prompts.item(step.item, checklist.items_of(where))
            if step.name == "resolve":  # merging main in, put in where a catch-up conflicted
                return intro, self.prompts.render(step.name, problem=step.problem or "")
            return intro, ""
        text = block.get("instructions") or ""
        if block.get("stage"):  # golem's text for its settings, taken when the job started, and what it's about now
            found = about.about(snap.get("nodes") or {}, self.store.steps(job.id), step, job.request)
            variant = (found.variant, checklist.MODE) if checklist.planning(job.mode, block, step) else found.variant
            text = stages.compose(block, block.get("parts") or self.library.blocks.parts_for(block), found.text, variant)
        if "$problem" in text:
            return intro, Template(text).safe_substitute(problem=step.problem or "(nothing reported)")
        if step.problem:
            text += f"\n\n## What golem's checks found\n\n{step.problem}"
        return intro, text

    def history(self, project: Project, job: Job) -> tuple[list[str], list[str]]:
        """For a job that only looks: the project's latest jobs, and its conversation lately."""
        jobs = []
        for j in [j for j in self.store.jobs(limit=300) if j.slug == project.slug and j.id != job.id][:5]:
            said = plan.last_said(self.store.steps(j.id))
            when = (j.updated_at or j.created_at)[:10]
            jobs.append(f"- #{j.id}, {j.playbook}, {j.state} {when}: “{_first_line(j.request, 200)}”" + (f" → {said}" if said else ""))
        said = [f"- {m.who}: {_first_line(m.text, 300)}" for m in self.store.messages(project.slug, limit=20) if m.text]
        return jobs, said

    def keep_options(self, job: Job, project: Project, step: Step, v: dict, contract: str) -> str:
        """Keep what a step that makes options returned with them (options.collect; a design
        round's doc and pages too, collect_design), or say what's wrong with it."""
        checkout, folder = self.checkout(project, job), options.folder(self.settings, job.id)
        design, problem = options.collect_design(v, checkout, folder, str(step.id)) if contract == "design" else (None, "")
        if problem:
            return problem
        made = [] if design and design.skip else options.collect(v.get("options"), checkout, folder, str(step.id))
        if len(made) < 2 and not (design and design.skip):
            return f"The {step.name} step returned fewer than two options with their files."
        self.store.update_step(step.id, options=[asdict(o) for o in made], **({"design": asdict(design)} if design else {}))
        return ""

    async def execute(
        self,
        job: Job,
        project: Project,
        step: Step,
        label: str,
        max_turns: int,
        prompt: str,
        resume: str | None,
        schema: dict | None = None,
        kind: str | None = None,
        block: dict | None = None,
    ) -> RunResult:
        """One run of the step, with the model and effort chosen for it as it starts (plan.engine):
        as a step of its own name, or of `kind` (a vet step's survey)."""
        project = self.store.project(project.slug)
        current = self.store.step(step.id) or step
        chosen = plan.engine(self.settings, self.store, project, replace(current, name=kind) if kind else current, block)
        log_path = self.settings.job_logs(job.id) / f"{len(self.store.runs(job.id)) + 1:03d}-{label}.jsonl.gz"
        self.settings.job_tmp(job.id).mkdir(parents=True, exist_ok=True)
        run_id = self.store.start_run(job.id, label, str(chosen), str(log_path), step.id)
        notes = self.progress_path(job)
        seen = notes.stat().st_size if notes.exists() else 0  # notes from earlier steps were passed on then
        filed = self.reports_path(job)
        filed_before = filed.stat().st_size if filed.exists() else 0  # earlier runs' reports were read then
        run = asyncio.create_task(
            self.harness.run(
                cwd=self.checkout(project, job),
                prompt=prompt,
                model=chosen.model,
                effort=chosen.effort,
                max_turns=max_turns,
                log_path=log_path,
                env={**self.worker_env(project, job), "GOLEM_RUN": str(run_id)},
                resume=resume,
                schema=schema,
                label=label,
            )
        )
        monitor = asyncio.create_task(self.monitor(job, project, step, label, log_path, seen, run))
        try:
            await asyncio.wait({run})
        except asyncio.CancelledError:
            run.cancel()
            await asyncio.wait({run})
            self.store.finish_run(run_id, "killed")
            self.take_reports(job, project, current, run_id, str(chosen), filed_before)
            raise
        finally:
            monitor.cancel()
        if run.cancelled():  # the monitor stopped it
            result = RunResult("error", error=f"no sign of life for {STUCK_SECONDS // 60} minutes")
        else:
            result = run.result()
        self.store.finish_run(run_id, result.outcome, session_id=result.session_id, **result.usage)
        self.take_reports(job, project, current, run_id, str(chosen), filed_before)
        return result

    def take_reports(self, job: Job, project: Project, step: Step, run_id: int, model: str, offset: int) -> None:
        """The reports about golem a run filed (`golem report`, past byte `offset` of the job's
        reports file), into the store with where they came from. At most reports.MAX_PER_RUN, and
        no secrets: they're shown on golem's pages and pasted into prompts."""
        lines, _ = _new_lines(self.reports_path(job), offset)
        made = [r for r in map(reports.parse, lines) if r]
        if len(made) < len(lines):
            log.warning("job %s run %s: %s line(s) in its reports file weren't reports", job.id, run_id, len(lines) - len(made))
        for r in made[: reports.MAX_PER_RUN]:
            self.store.add_report(
                r["kind"], r["impact"], reports.redact(r["summary"], os.environ), reports.redact(r["detail"], os.environ),
                job_id=job.id, step_id=step.id, run_id=run_id, slug=project.slug, label=plan.label(step), model=model,
                version=self.version(),
            )  # fmt: skip

    def incident(self, job: Job, step: Step, same: tuple[str, ...], kind: str, impact: str, summary: str, detail: str = "") -> None:
        """One of golem's own incidents, as a report about golem (reports.py): the same again (by
        `same`, what makes two of them one) counts up on its report. Costs no tokens. golem's own
        jobs (its triage) file none: they'd be reports about reading reports."""
        if job.slug == INTERNAL:
            return
        detail = reports.redact(detail, os.environ)[-reports.INCIDENT_CHARS :]
        self.store.file_incident(
            reports.fingerprint(*same), kind, impact, reports.redact(summary, os.environ)[: reports.SUMMARY_CHARS], detail,
            job_id=job.id, step_id=step.id or None, slug=job.slug, label=plan.label(step), version=self.version(),
        )  # fmt: skip

    def version(self) -> str:
        """The commit of golem that's running, as the deployer last said ("" if it can't say)."""
        return (self.upgrades.version() or {}).get("running", "")

    async def monitor(
        self, job: Job, project: Project, step: Step, label: str, log_path: Path, seen: int, run: asyncio.Task
    ) -> None:
        """While an agent step runs, keep Riggy posted: the worker's progress notes (past byte `seen`)
        as it writes them, what it's on when there's been no news for a while, and a restart if it stops."""
        notes = self.progress_path(job)
        tail, started = Tail(log_path), datetime.now(UTC)
        steps, news = Steps(started), started
        total = len(plan.planned(self.settings, self.store, job))
        while True:
            await asyncio.sleep(MONITOR_SECONDS)
            steps.lines(tail.events())
            now_ = datetime.now(UTC)
            new, seen = _new_lines(notes, seen)
            for note in new[-3:]:
                await self.say(project, f"▸ {_first_line(note, 300)}", notice=True)
                news = now_
            quiet = now_ - (tail.modified() or started)
            if quiet.total_seconds() >= STUCK_SECONDS:
                await self.say(project, f"No sign of life in {label} for {span(quiet)}, so I'm restarting it.", notice=True)
                run.cancel()
                return
            if (now_ - news).total_seconds() >= HEARTBEAT_SECONDS:
                said = f' Last said: "{steps.said}"' if steps.said else ""
                where = f"{label} (step {step.position}/{total})"
                await self.say(project, f"Still on {where}, {span(now_ - started)} in.{said} Now {steps.doing(now_)}.", notice=True)
                news = now_

    # --- golem's own steps ---------------------------------------------------

    async def do_vet(self, job: Job, project: Project, step: Step) -> Outcome:
        """An adoption's first step: golem's checklist (adopt.py), before anything is written to
        the repo. Waits while CI on main is running; the survey runs once golem's own checks have."""
        slug, s = project.slug, self.settings
        await self.workspace.ensure_repo(slug, project.summary)  # a clone: preflight saw the repo
        await self.workspace.sync_main(slug)
        if job.worktree:
            await self.workspace.ensure_tree(slug, job.id)
        sha = await self.workspace.main_sha(slug)
        result = await self.workspace.ci(slug, sha)
        links = {"repo": s.repo_url(slug), **({"ci": result.url} if result.url else {})}
        if result.state == "pending":
            waited = datetime.now(UTC) - datetime.fromisoformat(step.started_at)
            if waited >= CI_TIMEOUT:
                return Outcome("error", problem=f"CI on main still hasn't finished after {span(waited)}: {result.detail}")
            return Outcome("wait", f"Waiting for CI on main to finish before checking it over: {result.detail}", links)
        path = self.checkout(project, job)
        checks = [adopt.ci(result), *adopt.files(path, s.org, slug, s.platform, s.project_url(slug))]
        present = (path / "deploy" / "compose.yml").is_file()
        checks.append(adopt.compose(present, await self.deployer.validate(slug, sha) if present else None))
        found = await self.survey(job, project, step)
        if isinstance(found, Outcome):  # out of usage, or the run failed: the step goes again
            return found
        checks += adopt.survey(found)

        if adopt.decide(checks) == "refuse":
            return Outcome("refused", "Something's in the way: not adopted.", problem=adopt.refusal(slug, checks))
        # What the workflow's next blocks go by: how many gaps a worker has to fix (a condition
        # counts them), and what they are, for the agent block that fixes them.
        gaps, context = 0, ""
        if adopt.needs_worker(checks):
            gaps, context = sum(c.state == adopt.FIXABLE for c in checks), adopt.task(checks)
            fixing, nxt = gaps, "I'll fix what's marked →, then PR, CI, merge and deploy it."
        else:
            fixing = 0
            nxt = "I'll write its brief, then deploy it." if any(c.state == adopt.FIXABLE for c in checks) else "Next I deploy main as it is."
        await self.say(
            project,
            f"I checked it over, and I'm adopting it.\n\n{adopt.report(checks)}\n\n{nxt}\n\n"
            f"{plan.settings_line(self.store.project(slug))} (its settings change them).",
        )
        summary = f"Adopting it: {fixing} thing(s) to fix." if fixing else "Adopting it as it is."
        return Outcome("done", summary, links, counters={"gaps": max(gaps, 1) if context else 0}, context=context)

    async def survey(self, job: Job, project: Project, step: Step) -> dict | Outcome:
        """The survey: a worker run that builds and tests the repo here and reads it for what it needs
        (prompts/survey.md), and changes nothing. Its report, or how the run went wrong."""
        if self.settings.harness == "fake":
            return FAKE_SURVEY
        tree = self.tree(job)
        branch = await self.workspace.current_branch(project.slug, job=tree)
        was_clean = await self.workspace.clean(project.slug, job=tree)
        prompt = self.prompts.survey(project, branch, plan.adopt_words(job))
        result = await self.execute(job, project, step, "survey", SURVEY_TURNS, prompt, None, adopt.SURVEY_SCHEMA, kind="survey")
        if was_clean:
            await self.workspace.discard(project.slug, job=tree)
        if result.outcome == "limit":
            return Outcome("limit")
        if result.outcome == "max_turns":
            return Outcome("error", problem=f"the survey ran out of turns ({SURVEY_TURNS})")
        if result.outcome != "verdict" or not isinstance(result.verdict, dict):
            return Outcome("error", problem=f"the survey failed: {result.error or result.outcome}")
        return result.verdict

    async def do_setup(self, job: Job, project: Project, step: Step) -> Outcome:
        """The repo, with a first commit on main if it's new, the checkout up to date, and an issue.
        An adopted repo gets its past golem jobs back, and docs/brief.md from golem if it has none."""
        slug, url = project.slug, self.settings.project_url(project.slug)
        await self.workspace.ensure_repo(slug, project.summary)
        await self.workspace.ensure_main(slug, f"# {project.name}\n\nLive at {url}\n\n{project.summary}\n")
        await self.workspace.sync_main(slug)
        if job.worktree:  # the job's own, from the latest main
            await self.workspace.ensure_tree(slug, job.id)
        if job.kind == "adopt" and job.issue is None:  # before its own issue, which would look like one of them
            await self.bring_back_history(job, project)
            if closed := await self.workspace.close_leftover_prs(slug):  # or a new job could take one for its own
                await self.say(
                    project,
                    f"Closed {len(closed)} PR(s) an earlier golem left open on it ({', '.join(f'#{n}' for n in closed)}): "
                    "new jobs reuse its branch names. Their work stays on GitHub.",
                    notice=True,
                )
        if job.issue is None:
            title = project.name if job.kind in ("new", "adopt") else plan.title(job)
            head = "Adopted" if job.kind == "adopt" else "Requested"  # so a later golem can tell (history.py)
            body = f"{head}:\n\n> {job.request}\n\nWorked on by golem (job {job.id})."
            issue = await self.workspace.open_issue(slug, title, body)
            job = self.store.update_job(job.id, issue=issue)
        if job.kind == "adopt":
            await self.write_brief(job, project)
        if job.workflow == "options":
            await self.say(project, f"Making options: {plan.title(job)}", notice=True)
        elif job.workflow == "design":
            await self.say(project, f"Making design directions: {plan.title(job)}", notice=True)
        elif job.kind == "change":
            await self.say(project, f"Working on it: {plan.title(job)}", notice=True)
        links = {"repo": self.settings.repo_url(slug)}
        if job.issue:
            links["issue"] = f"{self.settings.repo_url(slug)}/issues/{job.issue}"
        return Outcome("done", "Repo, checkout and issue ready.", links)

    async def bring_back_history(self, job: Job, project: Project) -> None:
        """An adopted repo's past golem jobs, from its issues and PRs (history.py), as finished jobs
        under new numbers, once per adoption. Only a record: if GitHub can't be read, adopting it
        goes on without."""
        key = f"history_{job.id}"
        if self.store.get(key):  # an earlier try at this step got this far
            return
        try:
            found = records(self.settings, project.slug, await self.workspace.issues_and_prs(project.slug))
        except WorkspaceError as e:
            log.warning("history of %s: %s", project.slug, e)
            found = []
            await self.say(project, f"I couldn't read its earlier jobs from GitHub ({e}), so its history here starts now.", notice=True)
        if found:
            self.store.import_jobs(found)
            await self.say(project, f"Brought back its history: {len(found)} earlier job(s), from golem's issues and PRs on it.", notice=True)
        self.store.put(key, "1")

    async def write_brief(self, job: Job, project: Project) -> None:
        """docs/brief.md for an adopted repo without one, committed on the job's branch so it ships
        with the rest: the brief, and what Riggy said the repo is."""
        await self.workspace.start_branch(project.slug, self.branch(job), job=self.tree(job))
        path = self.checkout(project, job) / adopt.BRIEF
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(adopt.brief(project.name, project.summary, plan.adopt_words(job), self.settings.repo_url(project.slug), now()[:10]))
        await self.workspace.commit_all(project.slug, "Brief: what golem was asked to adopt", job=self.tree(job))

    async def do_refresh(self, job: Job, project: Project, step: Step) -> Outcome:
        """The first step of a job that only looks: the checkout up to date with main, unless a
        job's work is under way (then that's what a question is about, most likely). In a tree of
        its own, that's main or the work so far: what it touches there touches nothing else."""
        await self.workspace.ensure_repo(project.slug, project.summary)
        busy = self.store.active_job(project.slug, questions=False)
        if busy is None:
            await self.workspace.sync_main(project.slug)
        if job.worktree:
            await self.workspace.ensure_tree(project.slug, job.id, at=self.branch(busy) if busy else None)
        if job.start == "propose":
            await self.say(project, f"Nothing else on, so I'm looking at its {proposals.lens(self.store, job).name} for things to propose.", notice=True)
        else:
            await self.say(project, f"Looking into it: {plan.title(job)}", notice=True)
        return Outcome("done", "Looking at what's live." if busy is None else f"Looking at job {busy.id}'s work so far.")

    async def do_answer(self, job: Job, project: Project, step: Step) -> Outcome:
        """A question's last step: the answer, as a message that notifies him."""
        found = next((s for s in reversed(self.store.steps(job.id)) if s.kind == "agent" and s.state == "done"), None)
        answer = found.summary if found and found.summary else "I looked into it but came up without an answer. Try asking another way?"
        await self.say(project, _join(answer, found.links.get("link", "") if found else ""))
        return Outcome("done", "Answered you.")

    async def do_proposals(self, job: Job, project: Project, step: Step) -> Outcome:
        """A proposals job's last step: what it proposed, in the conversation to decide on on the
        project's page, and an issue for each on its repo as a record."""
        made, name = self.store.job_proposals(job.id), proposals.lens(self.store, job).name
        if not made:
            said = plan.last_said(self.store.steps(job.id))
            await self.say(project, f"I looked at its {name} and found nothing worth proposing." + (f" {said}" if said else ""), notice=True)
            return Outcome("done", "Nothing worth proposing.")
        page = f"{self.settings.url('golem')}/p/{project.slug}"
        for p in made:
            if p.issue is None:  # not opened by an earlier try at this step
                self.store.set_proposal_issue(p.id, await self.workspace.open_issue(project.slug, *proposals.issue(p, page)))
        await self.say(project, proposals.announcement(name, made, page))
        return Outcome("done", f"Proposed {len(made)} for you to decide on.")

    async def do_sync(self, job: Job, project: Project, step: Step) -> Outcome:
        """Catch the job's branch up with main where its turn at building or shipping begins
        (turns.py): another change may have shipped since it branched. Shipping others' changes
        with its own (shipments.py), it merges their branches in too; a conflict with any of
        them goes to one resolve step."""
        branch, tree = self.branch(job), self.tree(job)
        await self.workspace.start_branch(project.slug, branch, job=tree)
        await self.workspace.commit_all(project.slug, "Work left uncommitted", job=tree)
        merged, conflicts = await self.workspace.sync_branch(project.slug, job=tree)
        problems = []
        if conflicts:
            shipped = [f"#{j.id} “{plan.title(j)}”" for j in self.store.jobs(("done",), limit=20) if j.slug == project.slug][:3]
            problems.append(
                f"Main has {merged} commit(s) this job's branch ({branch}) doesn't, from other work on this project "
                f"that shipped since it branched{(' (' + ', '.join(shipped) + ')') if shipped else ''}. Merging main "
                f"in (`git fetch origin && git merge origin/main`) conflicts in:\n" + "\n".join(f"- {f}" for f in conflicts)
            )
        riders, clashes, gathered, clashing = self.riders(job), [], [], set(conflicts)
        for rider in riders:  # waiting at their first ship step: nothing of theirs is running
            theirs = self.branch(rider)
            await self.workspace.ensure_tree(project.slug, rider.id)
            await self.workspace.commit_all(project.slug, "Work left uncommitted", job=rider.id)
            found, files = await self.workspace.merge_branch(project.slug, theirs, job=tree)
            if files:
                clashes.append(rider)
                clashing |= set(files)
                problems.append(
                    f"Job {rider.id} (“{plan.title(rider)}”) is ready to ship too, so it ships with this job: golem merges "
                    f"its branch ({theirs}) into this one. It was asked for: “{_first_line(rider.request, 300)}”. Merging it "
                    f"in (`git merge {theirs}`) conflicts in:\n" + "\n".join(f"- {f}" for f in files)
                )
            elif found:
                gathered.append(rider)
        if problems:
            if not clashes:
                return Outcome("resolve", f"{len(conflicts)} file(s) conflict with what shipped since", problem="\n\n".join(problems))
            summary = f"{len(clashing)} file(s) conflict with what ships with it ({shipments.names(clashes)})"
            said = f"{plan.title(job)} ships with {shipments.names(riders)}, and merging them conflicts: {summary}. Merging them in."
            return Outcome("resolve", summary, problem="\n\n".join(problems), said=said)
        done = [f"merged in {merged} new commit(s) from main"] if merged else []
        if gathered:
            done.append(f"merged in {shipments.names(gathered)} to ship with it")
        if not done:
            return Outcome("skipped", "Up to date with main" + (f", and has {shipments.names(riders)} in already." if riders else "."))
        return Outcome("done", f"Caught up: {' and '.join(done)}." if gathered else f"Caught up with main: merged in {merged} new commit(s).")

    async def do_push(self, job: Job, project: Project, step: Step) -> Outcome:
        branch, tree = self.branch(job), self.tree(job)
        await self.workspace.commit_all(project.slug, "Work left uncommitted", job=tree)
        sha = await self.workspace.push_branch(project.slug, branch, job=tree)
        if sha is None:
            return Outcome("skipped", "Nothing new to push.")
        return Outcome("done", f"Pushed {sha[:7]} to {branch}.", {"branch": f"{self.settings.repo_url(project.slug)}/tree/{branch}"})

    async def do_pr(self, job: Job, project: Project, step: Step) -> Outcome:
        """The job's PR, opened if need be. Shipping others' changes with its own (shipments.py),
        it's all of theirs: each one's issue closed by it, and what each did."""
        riders = self.riders(job)
        parts = [self.pr_part(job, project, step)]
        for rider in riders:
            at = plan.current(self.store.steps(rider.id))
            parts.append(self.pr_part(rider, project, at, rider=True) if at else (plan.title(rider), "", ""))
        if not riders:
            title, issue, work = parts[0]
            body = "\n\n".join([p for p in (issue, work) if p] + [f"Opened by golem (job {job.id}); it merges this once CI passes."])
        else:
            jobs = [job, *riders]
            title = "; ".join(plan.title(j) for j in jobs)
            sections = [
                "\n\n".join(p for p in (f"### Job {j.id}: {plan.title(j)}", issue, work) if p) for j, (_, issue, work) in zip(jobs, parts)
            ]
            body = "\n\n".join([*sections, f"Opened by golem (job {job.id}), shipping {shipments.names(jobs)} together; it merges this once CI passes."])
        pr = await self.workspace.open_pr(project.slug, self.branch(job), title, body)
        if pr is None:
            return Outcome("skipped", "Nothing to merge.")
        return Outcome("done", f"PR #{pr.number} is open{self.along(riders)}.", {"pr": pr.url})

    def pr_part(self, job: Job, project: Project, step: Step, rider: bool = False) -> tuple[str, str, str]:
        """A job's part of a PR, from its steps up to `step` (its PR step, or for one riding along in
        another's shipment, its first ship step): a title, its issue's line, and what each step did."""
        steps = plan.planned(self.settings, self.store, job)
        work = _since_last(steps, step, "merge")
        names = " + ".join(s.name for s in work) or ("brief" if job.kind == "adopt" else "changes")
        title = project.name if job.kind in ("new", "adopt") else plan.title(job)
        later = [s for s in steps if s.kind == "pr" and s.state == "pending" and (s.position >= step.position if rider else s.position > step.position)]
        last = len(later) <= 1 if rider else not later  # riding along: the first of its own is this one
        issue = f"{'Closes' if last else 'Part of'} #{job.issue}" if job.issue else ""
        return f"{title}: {names}", issue, "\n".join(f"- **{s.name}:** {s.summary}" for s in work if s.summary)

    async def do_checks(self, job: Job, project: Project, step: Step) -> Outcome:
        pr = await self.workspace.pull_request(project.slug, self.branch(job))
        if pr is None:
            return Outcome("skipped", "No PR to check.", port="pass")
        return self.carrying(job, await self.wait_for_ci(project, step, pr.sha, f"PR #{pr.number}"))

    async def do_merge(self, job: Job, project: Project, step: Step) -> Outcome:
        pr = await self.workspace.pull_request(project.slug, self.branch(job))
        if pr is None:
            # An earlier try GitHub carried out though it answered with an error: finish it.
            number = _unmerged_pr(self.store.steps(job.id), step)
            sha = number and await self.workspace.merged(project.slug, number)
            if not sha:
                return Outcome("skipped", "Nothing to merge.")
            await self.workspace.after_merge(project.slug, self.branch(job), job=self.tree(job))
            url = f"{self.settings.repo_url(project.slug)}/pull/{number}"
            return Outcome("done", f"PR #{number} was merged already, as {sha[:7]}.", {"pr": url, "commit": self.commit_url(project, sha)})
        sha = await self.workspace.merge(project.slug, pr, self.branch(job), job=self.tree(job))
        return Outcome("done", f"Merged PR #{pr.number} as {sha[:7]}.", {"pr": pr.url, "commit": self.commit_url(project, sha)})

    async def do_deploy(self, job: Job, project: Project, step: Step) -> Outcome:
        """Deploy main once its CI passes; done only when the deployer has the site answering.
        Skipped if this job already deployed that commit (nothing new since: a skipped design pass)."""
        sha = await self.workspace.main_sha(project.slug)
        deployed = [s for s in self.store.steps(job.id) if s.kind == "deploy" and s.state == "done" and s.position < step.position]
        if deployed and deployed[-1].links.get("commit") == self.commit_url(project, sha):
            return Outcome("skipped", f"{sha[:7]} is live already.", port="pass")
        ci = await self.wait_for_ci(project, step, sha, "main")
        if ci.state != "done":
            return self.carrying(job, ci)
        deployment = await self.deployer.deploy(project.slug, sha)
        if not deployment.ok:
            problem = f"The deployer couldn't deploy {sha[:7]}:\n{deployment.problem}"
            refused = Outcome("failed", f"the deployer refused {sha[:7]}", {"commit": self.commit_url(project, sha)}, problem, port="fail")
            return self.carrying(job, refused)
        url = self.settings.project_url(project.slug)
        if self.store.card(project.slug) is None:  # live for the first time: on the projects page
            await self.refresh_card(project, job)
        live = f"{sha[:7]} is live{self.along(self.riders(job))}."
        return Outcome("done", live, {"site": url, "commit": self.commit_url(project, sha)}, port="pass")

    async def do_checkin(self, job: Job, project: Project, step: Step) -> Outcome:
        """Ask Riggy and wait for his reply: by default, to try the live site. His reply goes to
        the block's `reply_to` (the buildout, past a design pass's steps, which see it under
        "Done so far"), else to the next agent. With several outputs, a cheap call says which his
        reply means."""
        block = plan.node(job, step) or {}
        if step.state == "waiting":  # he's replied (the bot queued the job again)
            replies = self.store.pending_notes(job.id, step.id, step.node_id)
            said = _first_line(replies[-1][1], 200) if replies else ""
            if replies and block.get("reply_to"):
                to = f"{block['path']}/{block['reply_to']}" if block.get("path") else block["reply_to"]
                self.store.aim_notes_at(job.id, [i for i, _ in replies], to)
            port = ""
            if len(block.get("outputs") or []) > 1:
                port = await options.read_choice(self.harness, self.settings, "\n\n".join(t for _, t in replies), block["outputs"])
            return Outcome("done", f"You said: “{said}”" if said else "You replied.", port=port)
        steps = plan.planned(self.settings, self.store, job)
        if block.get("message"):
            vars = plan.vars_of(job)
            failure = vars.get("failure") or {}
            here = block.get("path", "")
            counters = {k.removeprefix(f"{here}/") if here else k: v for k, v in vars["counters"].items() if (k.rsplit("/", 1)[0] if "/" in k else "") == here}
            text = Template(block["message"]).safe_substitute(
                failed=failure.get("label", "it"), failure=failure.get("summary", ""), last=plan.last_said(steps),
                url=self.settings.project_url(project.slug), **{k: str(v) for k, v in counters.items()},
            )  # fmt: skip
            await self.say(project, text)
            return Outcome("checkin")
        message = plan.last_said(steps) or "Something to try is live."
        then = next((s for s in steps if s.position > step.position and s.kind == "agent"), None)
        maker = then and (plan.node(job, then) or plan.snapshot(job)["nodes"].get(then.node_id, {})).get("contract") == "design"
        design = " Then I'll make a few design directions for its look, for you to pick from." if maker and then.state == "pending" else ""
        await self.say(
            project,
            f"{message}\n{self.settings.project_url(project.slug)}\n\n"
            f"Try it and reply here: say it looks good, or what to change.{design}",
        )
        return Outcome("checkin")

    async def do_choose(self, job: Job, project: Project, step: Step) -> Outcome:
        """Show Riggy the options the step before made and wait for his pick, which goes to the
        next step with where its files are; or, if he wants other options, another round. A
        design round comes with its doc, the pages its directions share, its pick and its questions."""
        made, shown = options.shown(self.store.steps(job.id), step)
        if not shown:
            return Outcome("skipped", "No options to pick from.")
        design = options.design_of(made)
        if step.state == "waiting":  # he's replied (the bot queued the job again)
            said = "\n\n".join(text for _, text in self.store.pending_notes(job.id))
            reply = await options.read_reply(self.harness, self.settings, said, shown, design.questions if design else [])
            if reply.more:  # back to the block that made them; his reply goes to its new round
                return Outcome("done", options.again(reply, shown, said, design is not None), port="more")
            return Outcome("done", options.picked(reply, shown, said), port="picked")
        for o in shown:  # after "✓ options: <what they are>"
            await self.show_option(project, job, o)
        if design:
            await self.show_design(project, job, step, design, shown)
            return Outcome("checkin", "Waiting for your pick.")
        letters = ", ".join(o.letter for o in shown[:-1]) + f" or {shown[-1].letter}"
        await self.say(
            project,
            f"Which one? Reply {letters}, with anything you'd change (“{shown[1].letter}, but bigger”), or ask for "
            f"other options. Side by side: {self.settings.url('golem')}/steps/{step.id}",
        )
        return Outcome("checkin", "Waiting for your pick.")

    async def show_design(self, project: Project, job: Job, step: Step, design: options.Design, shown: list[options.Option]) -> None:
        """The rest of a design round, after its directions: the pages they share, then the doc,
        the round's pick and its questions, in the message that asks for his."""
        folder = options.folder(self.settings, job.id)
        for i, page in enumerate(design.pages):
            await self.send_image(project, folder / page, "The pages they share" if i == 0 else f"Shared ({i + 1})")
        golem = self.settings.url("golem")
        letters = ", ".join(o.letter for o in shown[:-1]) + f" or {shown[-1].letter}"
        lines = [f"The whole doc: {golem}/jobs/{job.id}/options/{design.doc}"] if design.doc else []
        if design.recommendation:
            lines.append(f"My pick: {design.recommendation}")
        if design.questions:
            lines.append("Also to decide:\n" + "\n".join(f"{n}. {q}" for n, q in enumerate(design.questions, start=1)))
        answers = ", and your answers by number" if design.questions else ""
        lines.append(
            f"Which one? Reply {letters}{answers}, or ask for another round (“{shown[0].letter}, but show me more icons”). "
            f"Side by side: {golem}/steps/{step.id}"
        )
        await self.say(project, "\n\n".join(lines))

    async def show_option(self, project: Project, job: Job, option: options.Option) -> None:
        """An option in the project's conversation: its pictures, the first captioned with what it is."""
        caption = f"{option.letter}: {option.title}. {option.description}".strip()
        if not option.images:
            await self.say(project, caption, notice=True)
            return
        folder = options.folder(self.settings, job.id)
        for i, image in enumerate(option.images):
            await self.send_image(project, folder / image, caption if i == 0 else f"{option.letter} ({i + 1})")

    async def do_test(self, job: Job, project: Project, step: Step) -> Outcome:
        """A Test block: the project's own `bin/test`, run in the job's checkout (run)."""
        block = plan.node(job, step) or {}
        if not (self.checkout(project, job) / "bin" / "test").is_file():
            problem = ("This project has no `bin/test`. Add one: an executable script that runs the project's tests (as CI does) "
                       "and exits 0 when they pass.")  # fmt: skip
            return Outcome("failed", "No bin/test to run", problem=problem, port="fail")
        return await self.run(job, project, step, "bin/test", int(block.get("timeout") or TEST_TIMEOUT))

    async def do_check(self, job: Job, project: Project, step: Step) -> Outcome:
        """A checklist item's check (checklist.py): the plan's check files put back as its planner
        wrote them, then the item's command run in the job's checkout (run). If it fails, the
        item's next try goes in after it; after its last, golem gives up on the item and tells the
        next agent step, and the job carries on."""
        item = step.item
        where = checklist.folder(self.settings.job_logs(job.id), int(item.get("plan") or 0))
        put = checklist.restore(where, self.checkout(project, job))
        ran = await self.run(job, project, step, item.get("check") or "false", TEST_TIMEOUT)
        note = f" (put back {', '.join(put)}, which it had changed)" if put else ""
        if ran.state == "done":
            return Outcome("done", f"{checklist.name(item)}: its check passed{note}.", ran.links)
        if nxt := checklist.again(item, ran.problem):
            self.insert(job.id, [replace(s, job_id=job.id) for s in nxt], after=step.position)
            return Outcome("failed", f"{checklist.name(item)}: its check failed{note}, so it goes again", ran.links, ran.problem)
        self.tell_next_agent(job, step, checklist.gave_up(item, ran.problem))
        return Outcome("failed", f"{checklist.name(item)}: its check still fails after {item.get('try')} tries{note}; carrying on", ran.links, ran.problem)

    def tell_next_agent(self, job: Job, step: Step, text: str) -> None:
        """Something for the next agent block of the job's workflow, under what golem's checks
        found (instructions): on its step if that's written already, else in the job's context,
        which the next agent step written takes (engine.row)."""
        heading = "## Checklist items that didn't pass\n\nGolem gave up on these after their tries; their checks still fail. Fix them first.\n\n"
        nxt = next((s for s in self.store.steps(job.id) if s.position > step.position and s.state == "pending" and s.kind == "agent" and s.node_id), None)
        if nxt is not None:
            had = nxt.problem or ""
            self.store.update_step(nxt.id, problem=f"{had}\n\n{text}".strip() if heading.strip() in had else f"{had}\n\n{heading}{text}".strip())
            return
        vars = plan.vars_of(self.store.job(job.id))
        had = vars.get("context") or ""
        vars["context"] = f"{had}\n\n{text}".strip() if heading.strip() in had else f"{had}\n\n{heading}{text}".strip()
        self.store.update_job(job.id, vars=vars)

    async def run(self, job: Job, project: Project, step: Step, command: str, timeout: int) -> Outcome:
        """A command run in the job's checkout, as a worker would run it (its environment, ports
        and TMPDIR). Passes on exit 0; otherwise fails, and the end of what it printed goes to the
        next agent block. Its output is kept with the job's logs."""
        log_path = self.settings.job_logs(job.id) / f"{len(self.store.runs(job.id)) + 1:03d}-{_safe(step.name)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.job_tmp(job.id).mkdir(parents=True, exist_ok=True)
        await self.workspace.commit_all(project.slug, "Work left uncommitted", job=self.tree(job))
        proc = await asyncio.create_subprocess_shell(
            command, cwd=self.checkout(project, job), env=self.worker_env(project, job), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        )  # fmt: skip
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            code = proc.returncode
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, code = b"", None
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            raise
        text = out.decode(errors="replace")
        log_path.write_text(f"$ {command}\n{text}")
        tail = text[-SCRIPT_TAIL:]
        last = _first_line(tail.strip().splitlines()[-1] if tail.strip() else "", 160)
        links = {"log": f"{self.settings.url('golem')}/steps/{step.id}"}
        if code == 0:
            return Outcome("done", f"Passed{': ' + last if last else '.'}", links, port="pass")
        why = f"timed out after {timeout} seconds" if code is None else f"exited {code}"
        problem = f"`{command}` {why}. The end of what it printed:\n\n{tail.strip() or '(nothing)'}"
        return Outcome("failed", f"{step.name} {why}" + (f": {last}" if last else ""), links, problem, port="fail")

    async def do_alert(self, job: Job, project: Project, step: Step) -> Outcome:
        """The End of a job's workflow: Riggy told it's done, its issue closed if no PR did, and
        its card brought up to date. Live only if it deployed (or was live already): a workflow
        needn't deploy."""
        url = self.settings.project_url(project.slug)
        steps = self.store.steps(job.id)
        live = any(s.kind == "deploy" and s.state == "done" for s in steps) or self.store.card(project.slug) is not None
        message = plan.last_said(steps) or (f"{project.name} is live." if live else f"{plan.title(job)} is done.")
        link = url if live else (f"{self.settings.repo_url(project.slug)}/issues/{job.issue}" if job.issue else "")
        if job.issue and not any(s.kind == "pr" and s.state == "done" and s.links.get("pr") for s in steps):  # no PR closed it
            await self.workspace.close_issue(project.slug, job.issue, _join(f"Done: {message}", f"\n{url}" if live else ""))
        await self.say(project, _join(f"Done: {message}", link))
        await self.status(f"Finished job {job.id} on {project.name}" + (f": {url}" if live else "."), notice=True)
        if live:
            await self.refresh_card(project, job)  # what it is now, on the projects page
        return Outcome("done", "Told you it's live." if live else "Told you it's done.", {"site": url} if live else {})

    async def refresh_card(self, project: Project, job: Job | None = None) -> None:
        """The project's card on the projects page (cards.py), from what the job just shipped (its
        checkout, on main now) or the project's checkout. Never holds up or fails a step."""
        try:
            checkout = self.checkout(project, job) if job else self.workspace.path(project.slug)
            await self.cards.refresh(self.store.project(project.slug) or project, checkout)
        except Exception:
            log.exception("couldn't make the card for %s", project.slug)

    async def ship_out(self) -> None:
        """Shipments whose carrier is past its run of ship steps (shipments.py): once it shipped,
        the riders' ship steps go by as done with its PR and deploy, and each carries on from
        there; if it was stopped first, they're ready to ship again."""
        for s in self.store.shipping():
            state = shipments.over(self.store, s)
            if state == "shipping" or s.id in self.shipping_out:  # another step's settling is seeing to it
                continue
            self.shipping_out.add(s.id)
            try:
                if state == "shipped":
                    await self.ride_out(s)
                self.store.end_shipment(s.id, state)
            finally:
                self.shipping_out.discard(s.id)
            log.info("shipment %s (jobs %s): %s", s.id, ", ".join(map(str, s.jobs)), state)
            self.wake()

    async def ride_out(self, s: Shipment) -> None:
        """The riders' runs of ship steps, done: what the carrier's did, said as theirs. Each
        one's tree goes back to main, where its work is now, and its branch goes."""
        carrier = self.store.job(s.carrier)
        theirs: dict[str, Step] = {}  # the carrier's ship steps that went through, the last of each kind
        for x in shipments.run_from(self.store.steps(s.carrier), s.jobs[s.carrier]):
            if x.state == "done":
                theirs[x.kind] = x
        links = {k: v for kind in ("pr", "merge", "deploy") if kind in theirs for k, v in theirs[kind].links.items()}
        for rid in s.riders:
            rider = self.store.job(rid)
            if rider is None or rider.state not in ACTIVE:
                continue
            if rider.worktree:
                try:
                    await self.workspace.back_to_main(s.slug, self.branch(rider), job=rider.id)
                except WorkspaceError as e:
                    log.warning("job %s's tree after shipping with job %s: %s", rid, s.carrier, e)
            for _ in range(engine.INLINE_LIMIT):
                rider = self.store.job(rid)
                steps = self.store.steps(rid)
                at = plan.current(steps)
                if at is None or not shipments.in_run(steps, at, s.jobs[rid]):
                    break
                same = theirs.get(at.kind)
                summary = f"With job {s.carrier}: {same.summary}" if same and same.summary else f"Shipped with job {s.carrier}."
                when = now()
                closed = {"state": "done", "summary": summary, "links": links, "started_at": at.started_at or when, "ended_at": when, "error": None}
                finished, _ = self.walk_on(rider, at, Outcome("done", summary, links), closed)
                self.store.update_job(rid, state="done" if finished else "queued", attempts=0, resume_session=None)
                if finished:
                    break
        if carrier is not None:
            log.info("job %s shipped jobs %s with it", carrier.id, ", ".join(map(str, s.riders)))

    async def missing_cards(self) -> None:
        """Cards for live projects that don't have one yet: those live before cards existed."""
        for project, _ in self.store.live_projects():
            if self.store.card(project.slug) is None:
                await self.refresh_card(project)

    async def wait_for_ci(self, project: Project, step: Step, sha: str, what: str) -> Outcome:
        ci = await self.workspace.ci(project.slug, sha)
        waited = datetime.now(UTC) - datetime.fromisoformat(step.started_at)
        links = {"ci": ci.url} if ci.url else {}
        match ci.state:
            case "passed":
                return Outcome("done", f"CI passed on {what} ({sha[:7]}).", links, port="pass")
            case "failed":
                problem = f"CI failed on {what} (commit {sha[:7]}):\n{ci.detail}"
                return Outcome("failed", f"CI failed on {what}", links, problem, port="fail")
            case "none" if waited >= CI_START_TIMEOUT:
                problem = (
                    f"No CI workflow ran on {what} (commit {sha[:7]}) in {span(waited)}. The repo needs "
                    ".github/workflows/build.yml, running on pull requests and pushes to main (skills/new-repo.md)."
                )
                return Outcome("failed", f"no CI ran on {what}", links, problem, port="fail")
            case "pending" if waited >= CI_TIMEOUT:
                return Outcome("error", problem=f"CI on {what} still hasn't finished after {span(waited)}: {ci.detail}")
        return Outcome("wait", f"Waiting for CI on {what}: {ci.detail or 'not started yet'}", links)

    # --- helpers --------------------------------------------------------------

    def branch(self, job: Job) -> str:
        """Where a job's work goes between merges."""
        return f"{JOB_BRANCHES}{job.id}"

    def riders(self, job: Job) -> list[Job]:
        """The changes riding along in the shipment the job carries (shipments.py), oldest first:
        those still under way."""
        found = shipments.shipment_of(self.store, job)
        if found is None or found.carrier != job.id:
            return []
        return [r for r in map(self.store.job, found.riders) if r is not None and r.state in ACTIVE]

    @staticmethod
    def along(riders: list[Job]) -> str:
        """What a step shipping riders' work too adds to what it says: ", with jobs 13 and 14"; "" for none."""
        return f", with {shipments.names(riders)}" if riders else ""

    def carrying(self, job: Job, outcome: Outcome) -> Outcome:
        """A failure while shipping others' changes with the job's own: the fix that follows is
        told the branch has their work too."""
        riders = self.riders(job)
        if outcome.state != "failed" or not riders:
            return outcome
        theirs = ", ".join(f"{r.id} (“{plan.title(r)}”)" for r in riders)
        note = (
            f"This job's branch has the work of job{'s' if len(riders) > 1 else ''} {theirs} in it too: they're shipping "
            "together with this job, so what failed may be in theirs. Fix it wherever it is, keeping what each job was for."
        )
        return replace(outcome, problem=f"{outcome.problem}\n\n{note}")

    def tree(self, job: Job) -> int | None:
        """Which checkout the job works in, for the workspace's calls: its own tree (its id), or
        None for the project's checkout (a job from before trees)."""
        return job.id if job.worktree else None

    def checkout(self, project: Project, job: Job) -> Path:
        """Where the job's files are: its tree, or the project's checkout. golem's own jobs have
        no repo: a scratch directory of their own."""
        if project.slug == INTERNAL:
            scratch = self.settings.job_tmp(job.id) / "work"
            scratch.mkdir(parents=True, exist_ok=True)
            return scratch
        return self.workspace.tree_path(job.id) if job.worktree else self.workspace.path(project.slug)

    def commit_url(self, project: Project, sha: str) -> str:
        return f"{self.settings.repo_url(project.slug)}/commit/{sha}"

    def lessons_path(self, project: Project) -> Path:
        """The lessons file the project's workers read and add to: its own, for a twin built both
        ways (each started as a copy of golem's when the pair was made), else golem's."""
        return self.settings.lessons(project.slug if project.twin else "")

    def error_log(self, job: Job, step: Step) -> Path:
        """Where all that a step's failed commands printed is kept, each try after the last."""
        path = self.settings.job_logs(job.id) / f"{step.position:03d}-{_safe(step.name)}-errors.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def progress_path(self, job: Job) -> Path:
        """Where the worker appends progress notes for Riggy, one line each (prompts/step.md)."""
        return self.settings.job_logs(job.id) / "progress.txt"

    def reports_path(self, job: Job) -> Path:
        """Where the worker's `golem report` appends its reports about golem, a JSON line each (cli.py)."""
        return self.settings.job_logs(job.id) / "reports.jsonl"

    def worker_env(self, project: Project, job: Job) -> dict[str, str]:
        """What a worker is told. Workers on other projects may run beside it, so it gets ports
        and a TMPDIR of its own: its slot's GOLEM_PORT to GOLEM_PORT+9, and its job's directory."""
        env = {k: v for k, v in os.environ.items() if k not in WORKER_ENV_DENY}
        slot = self.running[job.id].slot if job.id in self.running else 0
        tmp = str(self.settings.job_tmp(job.id))
        env.update(
            GOLEM_SLUG=project.slug,
            GOLEM_JOB=str(job.id),
            GOLEM_PROGRESS=str(self.progress_path(job)),
            # golem's own jobs (its triage) file no reports about golem: they'd be about themselves.
            GOLEM_REPORTS="" if project.slug == INTERNAL else str(self.reports_path(job)),
            GOLEM_URL=self.settings.project_url(project.slug),
            GITHUB_ORG=self.settings.org,
            GOLEM_PLATFORM=self.settings.platform,
            DEPLOYER_URL=self.settings.deployer_url,
            GOLEM_SLOT=str(slot),
            GOLEM_PORT=str(PORTS + PORTS_PER_SLOT * slot),
            GOLEM_TMP=tmp,
            TMPDIR=tmp,
            # What a worker knows is its repo and golem's lessons (lessons_path): Claude Code's own
            # memory, shared by every job on a repo and kept where golem can't see it, stays off.
            CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        )
        return env

    async def tidy(self) -> None:
        """Remove the scratch directories and trees of jobs that are over. Best effort. What to keep
        is read before anything is awaited, so a tree made meanwhile is never among what goes."""
        keep = self.store.active_job_ids() | set(self.running)
        ids = {p: p.name.removeprefix("job-") for p in self.settings.tmp_dir.glob("job-*")}
        scratch = [p for p, n in ids.items() if n.isdigit() and int(n) not in keep]
        try:
            await self.workspace.prune_trees(keep)  # lists the trees before it awaits anything
        except WorkspaceError as e:
            log.warning("tidying job trees: %s", e)
        for path in scratch:
            await asyncio.to_thread(shutil.rmtree, path, True)

    async def say(self, project: Project, text: str, notice: bool = False, job: Job | int | None = None, step: int | None = None) -> None:
        """Something for Riggy about a project: about a job (and a step) if given, else the job
        and step this worker slot is on (DOING), so it's that job's activity."""
        doing_job, doing_step = DOING.get()
        job_id = (job.id if isinstance(job, Job) else job) if job is not None else doing_job
        step_id = step if step is not None else (doing_step if job_id == doing_job else None)
        channel = STATUS if project.slug == INTERNAL else project.slug  # golem's own jobs: golem's news, on Now
        await self.chat.send(channel, text, notice=notice, job=job_id, step=step_id)

    async def send_image(self, project: Project, path: Path, caption: str = "") -> None:
        """A picture for Riggy, about the job and step this worker slot is on."""
        job_id, step_id = DOING.get()
        await self.chat.send_image(project.slug, path, caption, job=job_id, step=step_id)

    async def status(self, text: str, notice: bool = False) -> None:
        await self.chat.send(STATUS, text, notice=notice)


def _safe(name: str) -> str:
    """A block's name as part of a file name."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:40] or "script"


def _since_last(steps: list[Step], step: Step, kind: str) -> list[Step]:
    """The agent steps done since the last `kind` step before `step`."""
    work = []
    for s in steps:
        if s.position >= step.position:
            break
        if s.kind == kind and s.state == "done":
            work = []
        elif s.kind == "agent" and s.state == "done":
            work.append(s)
    return work


def _unmerged_pr(steps: list[Step], step: Step) -> int | None:
    """The PR the job's last PR step before `step` opened, unless a merge was done since: the one
    a merge step is merging."""
    number = None
    for s in steps:
        if s.position >= step.position:
            break
        if s.kind == "pr" and s.state == "done" and s.links.get("pr"):
            tail = s.links["pr"].rstrip("/").rsplit("/", 1)[-1]
            number = int(tail) if tail.isdigit() else None
        elif s.kind == "merge" and s.state == "done":
            number = None
    return number


def _first_line(text: str, limit: int = 80) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Whole lines added to the file since `offset`, and the offset after them."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return [], offset
    complete = data[: data.rfind(b"\n") + 1]
    lines = [line.strip() for line in complete.decode(errors="replace").splitlines()]
    return [line for line in lines if line], offset + len(complete)


def _join(message: str, link: str) -> str:
    return f"{message}\n{link}" if link else message
