"""The scheduler: worker slots, each running one job's current step.

A job is a list of steps, written into the database from its playbook when the job
starts. golem works through them in order and nothing else decides what comes next.
Agent steps are headless Claude Code runs that do one piece of work and commit it.
Every other step is golem's own code: setting up the repo, pushing, pull requests,
waiting for CI, merging, deploying, the MVP check-in, showing Riggy options to pick from
(options.py) and telling him it's done. Each playbook ends with deploy and alert, so a
finished job is a deployed one; except those that only look: a question's (refresh,
examine, answer) and proposals' (refresh, propose, proposals). Nothing is committed,
pushed or deployed for those.

With nothing else to do, golem looks for improvements to propose on projects that want
them, and builds the ones Riggy accepts once their project is free (proposals.py).

An upgrade of golem itself waits for the running steps to finish, and holds new steps
until it's done (upgrade.py).

When CI fails or the deployer refuses a commit, a `fix` agent step is inserted after the
failed one, followed by the steps from the last push again. Each step's outcome is
saved, so a crash or a usage limit costs at most the step that was running.

Adopting a repo golem didn't make starts with `vet` (adopt.py): golem's own checks and a
survey that changes nothing. Something blocking and golem forgets the project and says why;
otherwise the job goes on like any other that ships.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import adopt, options, plan, proposals, teardown, turns
from .cards import Cards
from .config import Settings
from .deployer_client import Deployer, DeployerError
from .harness import VERDICTS, WRAPUP_SCHEMA, Harness, RunResult, wrapup_schema
from .history import records
from .talk import INBOX, STATUS, Talk
from .upgrade import Upgrades
from .prompts import Prompts
from .store import ACTIVE, LOOKS, MAX_WORKERS, Job, Project, Proposal, Step, Store, later, now
from .watch import Steps, Tail, span
from .workspace import JOB_BRANCHES, Workspace, WorkspaceError

log = logging.getLogger(__name__)

WRAPUP_TURNS = 15
FIX_TURNS = 150
SURVEY_TURNS = 100  # install, run the tests, read for what it needs
# With HARNESS=fake there's no worker to survey a repo golem adopts: its own checks still run.
FAKE_SURVEY = {"tests": {"result": "passed", "command": "", "detail": "not run (HARNESS=fake)"}, "missing": [], "needs": []}
MAX_ATTEMPTS = 3  # failed attempts at one step before asking Riggy for help
MAX_FIXES = 4  # fix steps in one job before asking Riggy for a hint
RESOLVE_TURNS = 150
MAX_RESOLVES = 3  # resolve steps in one job (conflicts with main) before asking Riggy for a hint
LIMIT_RETRY = timedelta(minutes=30)
ERROR_RETRY = timedelta(minutes=1)
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
# Never handed to workers: only Riggy's !delete deletes a project's data, and only he
# changes jobs on the status page.
WORKER_ENV_DENY = frozenset({"DEPLOYER_ADMIN_SECRET", "GOLEM_WEB_PASSWORD"})
# Golem's own steps whose results are posted in the project's conversation (for a pick, how his
# reply was read). The rest (setup, push, CI, merge) show on the status page; failures are
# always posted.
ANNOUNCED = {"pr", "deploy", "choose"}
# Steps that work in the job's tree (made by setup, vet or refresh, the first step).
USES_TREE = ("agent", "push", "push-main", "merge", "deploy", "alert", "sync")
BUILDS_DESIGN = ("guide", "apply")  # agent steps that build a design pass's pick (playbooks/design.md)
DESIGN_PASS = ("design", *BUILDS_DESIGN)


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
        self.running: dict[int, Running] = {}  # by job id
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
        for job in self.store.jobs(ACTIVE):
            if self.store.runs(job.id):  # under way from before steps were tracked
                self.ensure_steps(job)
        self.backfill = asyncio.create_task(self.missing_cards())
        self.versions = asyncio.create_task(self.upgrades.watch_forever())
        try:
            while True:
                await self.tidy()  # what jobs that ended left
                if self.upgrades.holding():  # nothing new starts; the upgrade goes once nothing's running
                    if self.upgrades.state == "pending" and not self.running:
                        await self.upgrades.start()
                    await self.idle()
                    continue
                if not self.paused:
                    await self.build_accepted()
                    if not self.fill() and self.propose() is not None:
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
            if job.id in self.running:
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
            await self.retry_or_ask(job, f"golem error: {e!r}")
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

    async def build_accepted(self) -> None:
        """A change job for each proposal Riggy accepted, once its project has room for another
        change (one at a time, unless it allows more: turns.py)."""
        for p in self.store.unbuilt_proposals():
            project = self.store.project(p.slug)
            if project is None or not self.store.room_for_change(p.slug):
                continue
            job = self.store.add_job(p.slug, "change", proposals.request(p), "feature", title=plan.clean_title(p.title))
            if p.issue:  # the job works on the proposal's issue, and its PR closes it
                job = self.store.update_job(job.id, issue=p.issue)
            self.store.set_built_by(p.id, job.id)
            await self.say(project, f"Building the proposal you accepted: {p.title} (job {job.id}).", notice=True)
            self.wake()

    async def record_decision(self, p: Proposal) -> None:
        """Riggy's decision on the proposal's issue, closing it if he turned it down. Only a record."""
        if p.issue:
            await self.workspace.comment_issue(p.slug, p.issue, proposals.decision(p), close=p.state == "rejected")

    def ensure_steps(self, job: Job) -> list[Step]:
        """The job's steps, written from its playbook the first time."""
        steps = self.store.steps(job.id)
        if not steps:
            self.store.add_steps(job.id, plan.planned(self.settings, self.store, job))
            steps = self.store.steps(job.id)
        return steps

    def prepare(self, job: Job) -> Step | None:
        """The job's current step, ready to run: the job's steps written the first time, a catch-up
        with main put in where its turn at building or shipping begins (turns.py), the job running
        and its step started. None if it has no step left."""
        steps = self.ensure_steps(job)
        step = plan.current(steps)
        if step is None:
            return None
        if self.needs_sync(job, steps, step):
            self.store.add_steps(job.id, [Step(0, job.id, 0, "sync", "sync")], after=step.position - 1)
            step = plan.current(self.store.steps(job.id))
        self.store.update_job(job.id, state="running")
        if step.started_at is None:
            step = self.store.update_step(step.id, state="running", started_at=now())
        return step

    def needs_sync(self, job: Job, steps: list[Step], step: Step) -> bool:
        """Whether a change's branch should catch up with main before this step: the first of a
        turn at building or shipping, on a project where changes take turns. Others may have
        shipped since it branched; building and shipping start from what's live."""
        if job.kind != "change" or job.playbook in LOOKS or not job.worktree or step.started_at or step.kind == "sync":
            return False
        project = self.store.project(job.slug)
        if project is None or project.parallel_jobs <= 1 or plan.step_class(steps, step) not in turns.TURNS:
            return False
        return plan.section(steps, step)[0].position == step.position  # a catch-up before it would be in its section

    async def step(self, job: Job) -> None:
        """Do the job's current step and act on how it went."""
        project = self.store.project(job.slug)
        step = self.prepare(job)
        if step is None:
            self.store.update_job(job.id, state="done")
            return
        job = self.store.job(job.id)
        try:
            if job.worktree and step.kind in USES_TREE:  # made by its first step; back if it was lost
                await self.workspace.ensure_tree(project.slug, job.id)
            outcome = await getattr(self, "do_" + step.kind.replace("-", "_"))(job, project, step)
        except (WorkspaceError, DeployerError) as e:
            outcome = Outcome("error", problem=str(e))
        if self.store.job(job.id).state == "killed":
            return
        await self.settle(self.store.job(job.id), project, self.store.step(step.id), outcome)

    async def settle(self, job: Job, project: Project, step: Step, outcome: Outcome) -> None:
        name = plan.label(step)
        match outcome.state:
            case "done" | "skipped":
                self.store.update_step(
                    step.id, state=outcome.state, summary=outcome.summary, links=outcome.links, error=None, ended_at=now()
                )
                steps = self.store.steps(job.id)
                finished = plan.current(steps) is None
                self.store.update_job(job.id, state="done" if finished else "queued", attempts=0, resume_session=None)
                quiet = job.playbook in LOOKS  # its last step says it
                if outcome.state == "done" and not quiet and (step.kind == "agent" or step.kind in ANNOUNCED):
                    link = next(iter(outcome.links.values()), "")
                    nxt = "" if finished else f"\nNext: {plan.upcoming(steps, step)}."
                    await self.say(project, _join(f"✓ {name}: {outcome.summary}", link) + nxt, notice=True)
            case "wait":
                self.store.update_step(step.id, summary=outcome.summary, links=outcome.links)
                self.store.update_job(job.id, state="parked", retry_at=later(CI_POLL), attempts=0)
            case "fix":
                self.store.update_step(
                    step.id, state="failed", summary=outcome.summary, error=outcome.problem, links=outcome.links, ended_at=now()
                )
                steps = self.store.steps(job.id)
                fixes = sum(1 for s in steps if s.kind == "agent" and s.name == "fix")
                fix = Step(0, job.id, 0, "agent", "fix", FIX_TURNS, problem=outcome.problem)
                self.store.add_steps(job.id, [fix, *_retry(steps, step)], after=step.position)
                if fixes < MAX_FIXES:
                    self.store.update_job(job.id, state="queued", attempts=0)
                    await self.say(project, f"✗ {name}: {outcome.summary}. Fixing it.", notice=True)
                else:  # going round in circles: one of the few reasons to interrupt Riggy
                    self.store.update_job(job.id, state="waiting", attempts=0)
                    await self.say(
                        project,
                        f"I've tried to fix {name} {fixes} times and it still fails: {outcome.summary}.\n\n"
                        "Reply with a hint and I'll try again, or `!kill` to drop the job.",
                    )
            case "resolve":  # main moved under it and conflicts: a worker merges them, then it catches up again
                self.store.update_step(step.id, state="failed", summary=outcome.summary, error=outcome.problem, ended_at=now())
                steps = self.store.steps(job.id)
                tries = sum(1 for s in steps if s.kind == "agent" and s.name == "resolve")
                resolve = Step(0, job.id, 0, "agent", "resolve", RESOLVE_TURNS, problem=outcome.problem)
                self.store.add_steps(job.id, [resolve, Step(0, job.id, 0, "sync", "sync")], after=step.position)
                if tries < MAX_RESOLVES:
                    self.store.update_job(job.id, state="queued", attempts=0)
                    await self.say(project, f"Main moved on under {plan.title(job)}: {outcome.summary}. Merging it in.", notice=True)
                else:
                    self.store.update_job(job.id, state="waiting", attempts=0)
                    await self.say(
                        project,
                        f"I've tried to merge main into {plan.title(job)} {tries} times and it still conflicts: "
                        f"{outcome.summary}.\n\nReply with a hint and I'll try again, or `!kill` to drop the job.",
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
                await self.retry_or_ask(job, outcome.problem)
            case "refused":  # nothing was written to the repo: golem forgets it was ever asked
                await self.chat.send(INBOX, outcome.problem)  # where he asked, as his conversation goes
                await teardown.forget_project(project, self.settings, self.store, self.workspace)
                log.info("didn't adopt %s: %s", project.slug, outcome.summary)
                return
        self.store.put("limit_notice", "0")

    async def retry_or_ask(self, job: Job, error: str) -> None:
        job = self.store.job(job.id)
        if job is None or job.state == "killed":  # gone with its project, or stopped
            return
        attempts = job.attempts + 1
        project = self.store.project(job.slug)
        if attempts < MAX_ATTEMPTS:
            self.store.update_job(job.id, state="parked", attempts=attempts, retry_at=later(ERROR_RETRY), resume_session=None)
            log.warning("job %s attempt %s failed: %s", job.id, attempts, error)
            return
        if job.playbook == "proposals":  # nobody asked for it: no reason to interrupt Riggy, just stop
            self.store.update_job(job.id, state="failed", attempts=0, resume_session=None)
            await self.say(project, f"I couldn't finish looking for proposals: {error[:300]}", notice=True)
            return
        # Stuck after retries is one of the few reasons to interrupt Riggy.
        self.store.update_job(job.id, state="waiting", attempts=0, resume_session=None)
        step = plan.current(self.store.steps(job.id))
        name = plan.label(step) if step else "the job"
        await self.say(
            project,
            f"I'm stuck on {name} after {MAX_ATTEMPTS} tries: {error[:400]}\n\n"
            "Reply with a hint and I'll try again, or `!kill` to drop the job.",
        )

    # --- agent steps ----------------------------------------------------------

    async def do_agent(self, job: Job, project: Project, step: Step) -> Outcome:
        """One headless run that does the step's work on the job's branch and commits it. A
        question's or a proposal's looks at the checkout as it is (main, or a job's work under
        way), and anything it changes there is put back."""
        if step.name in BUILDS_DESIGN and (nothing := self.nothing_designed(job, step)):
            return Outcome("skipped", nothing)
        looking, tree = job.playbook in LOOKS, self.tree(job)
        if looking:
            branch = await self.workspace.current_branch(project.slug, job=tree)
            was_clean = await self.workspace.clean(project.slug, job=tree)
        else:
            branch = self.branch(job)
            await self.workspace.start_branch(project.slug, branch, job=tree)
        notes = self.store.pending_notes(job.id, step.id)
        feedback = [text for _, text in notes]
        if job.resume_session:
            prompt, resume = self.prompts.resume(feedback), job.resume_session
        elif job.playbook == "proposals":
            jobs, said = self.history(project, job)
            book = plan.load(self.settings, job.playbook)
            earlier = [p for p in self.store.proposals(project.slug, limit=proposals.PAST_SHOWN) if p.job_id != job.id]
            lens = proposals.lens(self.store, job)
            prompt, resume = self.prompts.proposals(project, job, book, step, branch, lens, jobs, said, earlier), None
        elif job.playbook == "question":
            busy = self.store.active_job(project.slug, questions=False) is not None
            jobs, said = self.history(project, job)
            book = plan.load(self.settings, job.playbook)
            prompt, resume = self.prompts.question(project, job, book, step, branch, busy, jobs, said, feedback), None
        else:
            book = plan.load(self.settings, job.playbook)
            lessons = self.lessons_path.read_text() if self.lessons_path.exists() else ""
            steps = self.store.steps(job.id)
            prompt, resume = self.prompts.step(project, job, book, step, steps, branch, feedback, lessons), None

        schema = options.MAKERS.get(step.name) if step.kind == "agent" else None  # what it made comes with its verdict
        schema = schema or {"propose": proposals.SCHEMA}.get(step.name)
        result = await self.execute(job, project, step, step.name, step.max_turns or FIX_TURNS, prompt, resume, schema)
        if result.outcome == "max_turns" and result.session_id:
            wrapped = wrapup_schema(schema)
            wrapup = await self.execute(
                job, project, step, f"{step.name}-wrapup", WRAPUP_TURNS, self.prompts.wrapup(bool(schema)), result.session_id, wrapped
            )
            said = str((wrapup.verdict or {}).get("message", "")).strip()
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
        if v.get("verdict") not in VERDICTS:
            return Outcome("error", problem=f"unknown verdict {v.get('verdict')!r}")
        if options.makes(step) and v["verdict"] == "done":
            problem = self.keep_options(job, project, step, v)
            if problem:
                return Outcome("error", problem=problem)
        self.store.consume_notes([i for i, _ in notes])
        message, link = str(v.get("message", "")).strip(), str(v.get("link") or "").strip()
        links = {"link": link} if link.startswith(("https://", "http://")) else {}
        if job.playbook == "proposals":  # nobody is waiting to answer: a question back is nothing to propose
            made = self.store.add_proposals(project.slug, job.id, proposals.lens(self.store, job).name, proposals.collect(v.get("proposals")))
            return Outcome("done", message or f"Proposed {len(made)}.", links)
        if v["verdict"] == "blocked":
            return Outcome("blocked", message, links, session_id=result.session_id)
        if not looking:
            await self.workspace.commit_all(project.slug, f"{step.name}: work left uncommitted", job=tree)
        return Outcome("done", message, links)

    def history(self, project: Project, job: Job) -> tuple[list[str], list[str]]:
        """For a job that only looks: the project's latest jobs, and its conversation lately."""
        jobs = []
        for j in [j for j in self.store.jobs(limit=300) if j.slug == project.slug and j.id != job.id][:5]:
            said = plan.last_said(self.store.steps(j.id))
            when = (j.updated_at or j.created_at)[:10]
            jobs.append(f"- #{j.id}, {j.playbook}, {j.state} {when}: “{_first_line(j.request, 200)}”" + (f" → {said}" if said else ""))
        said = [f"- {m.who}: {_first_line(m.text, 300)}" for m in self.store.messages(project.slug, limit=20) if m.text]
        return jobs, said

    def nothing_designed(self, job: Job, step: Step) -> str:
        """Why a step that builds a design pass's pick has nothing to build, or "": its design
        round was skipped, or found nothing to design."""
        made = next((s for s in reversed(self.store.steps(job.id)) if s.position < step.position and s.name == "design"), None)
        design = options.design_of(made)
        if design and design.skip:
            return f"Nothing to design: {design.skip}"
        if made is None or made.state != "done" or not made.options:
            return "No design round to build: it was skipped."
        return ""

    def keep_options(self, job: Job, project: Project, step: Step, v: dict) -> str:
        """Keep what a step that makes options returned with them (options.collect; a design
        round's doc and pages too, collect_design), or say what's wrong with it."""
        checkout, folder = self.checkout(project, job), options.folder(self.settings, job.id)
        design, problem = options.collect_design(v, checkout, folder, str(step.id)) if step.name == "design" else (None, "")
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
    ) -> RunResult:
        """One run of the step, with the model and effort chosen for it as it starts (plan.engine):
        as a step of its own name, or of `kind` (a vet step's survey)."""
        project = self.store.project(project.slug)
        current = self.store.step(step.id) or step
        engine = plan.engine(self.settings, self.store, project, replace(current, name=kind) if kind else current)
        log_path = self.settings.job_logs(job.id) / f"{len(self.store.runs(job.id)) + 1:03d}-{label}.jsonl.gz"
        self.settings.job_tmp(job.id).mkdir(parents=True, exist_ok=True)
        run_id = self.store.start_run(job.id, label, str(engine), str(log_path), step.id)
        notes = self.progress_path(job)
        seen = notes.stat().st_size if notes.exists() else 0  # notes from earlier steps were passed on then
        run = asyncio.create_task(
            self.harness.run(
                cwd=self.checkout(project, job),
                prompt=prompt,
                model=engine.model,
                effort=engine.effort,
                max_turns=max_turns,
                log_path=log_path,
                env=self.worker_env(project, job),
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
            raise
        finally:
            monitor.cancel()
        if run.cancelled():  # the monitor stopped it
            result = RunResult("error", error=f"no sign of life for {STUCK_SECONDS // 60} minutes")
        else:
            result = run.result()
        self.store.finish_run(run_id, result.outcome, session_id=result.session_id, **result.usage)
        return result

    async def monitor(
        self, job: Job, project: Project, step: Step, label: str, log_path: Path, seen: int, run: asyncio.Task
    ) -> None:
        """While an agent step runs, keep Riggy posted: the worker's progress notes (past byte `seen`)
        as it writes them, what it's on when there's been no news for a while, and a restart if it stops."""
        notes = self.progress_path(job)
        tail, started = Tail(log_path), datetime.now(UTC)
        steps, news = Steps(started), started
        total = len(self.store.steps(job.id))
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
        work = next((x for x in self.store.steps(job.id) if x.kind == "agent" and x.name == "adopt" and x.state == "pending"), None)
        if work and adopt.needs_worker(checks):
            self.store.update_step(work.id, problem=adopt.task(checks))
            fixing, nxt = sum(c.state == adopt.FIXABLE for c in checks), "I'll fix what's marked →, then PR, CI, merge and deploy it."
        else:
            if work:
                self.store.update_step(work.id, state="skipped", summary="Nothing for a worker to fix.", ended_at=now())
            fixing = 0
            nxt = "I'll write its brief, then deploy it." if any(c.state == adopt.FIXABLE for c in checks) else "Next I deploy main as it is."
        await self.say(
            project,
            f"I checked it over, and I'm adopting it.\n\n{adopt.report(checks)}\n\n{nxt}\n\n"
            f"{plan.settings_line(self.store.project(slug))} (!mode, !model or !effort here to change them).",
        )
        return Outcome("done", f"Adopting it: {fixing} thing(s) to fix." if fixing else "Adopting it as it is.", links)

    async def survey(self, job: Job, project: Project, step: Step) -> dict | Outcome:
        """The survey: a worker run that builds and tests the repo here and reads it for what it needs
        (prompts/survey.md), and changes nothing. Its report, or how the run went wrong."""
        if self.settings.harness == "fake":
            return FAKE_SURVEY
        tree = self.tree(job)
        branch = await self.workspace.current_branch(project.slug, job=tree)
        was_clean = await self.workspace.clean(project.slug, job=tree)
        prompt = self.prompts.survey(project, branch, _adopt_words(job))
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
            body = f"Requested:\n\n> {job.request}\n\nWorked on by golem (job {job.id})."
            issue = await self.workspace.open_issue(slug, title, body)
            job = self.store.update_job(job.id, issue=issue)
        if job.kind == "adopt":
            await self.write_brief(job, project)
        if job.playbook == "options":
            await self.say(project, f"Making options: {plan.title(job)}", notice=True)
        elif job.playbook == "design":
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
        with the rest: the brief, and what Riggy said after `!adopt <slug>`."""
        await self.workspace.start_branch(project.slug, self.branch(job), job=self.tree(job))
        path = self.checkout(project, job) / adopt.BRIEF
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(adopt.brief(project.name, project.summary, _adopt_words(job), self.settings.repo_url(project.slug), now()[:10]))
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
        if job.playbook == "proposals":
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
        (turns.py): another change may have shipped since it branched."""
        branch, tree = self.branch(job), self.tree(job)
        await self.workspace.start_branch(project.slug, branch, job=tree)
        await self.workspace.commit_all(project.slug, "Work left uncommitted", job=tree)
        merged, conflicts = await self.workspace.sync_branch(project.slug, job=tree)
        if conflicts:
            shipped = [f"#{j.id} “{plan.title(j)}”" for j in self.store.jobs(("done",), limit=20) if j.slug == project.slug][:3]
            problem = (
                f"Main has {merged} commit(s) this job's branch ({branch}) doesn't, from other work on this project "
                f"that shipped since it branched{(' (' + ', '.join(shipped) + ')') if shipped else ''}. Merging main "
                f"in conflicts in:\n" + "\n".join(f"- {f}" for f in conflicts)
            )
            return Outcome("resolve", f"{len(conflicts)} file(s) conflict with what shipped since", problem=problem)
        if not merged:
            return Outcome("skipped", "Up to date with main.")
        return Outcome("done", f"Caught up with main: merged in {merged} new commit(s).")

    async def do_push(self, job: Job, project: Project, step: Step) -> Outcome:
        branch, tree = self.branch(job), self.tree(job)
        await self.workspace.commit_all(project.slug, "Work left uncommitted", job=tree)
        sha = await self.workspace.push_branch(project.slug, branch, job=tree)
        if sha is None:
            return Outcome("skipped", "Nothing new to push.")
        return Outcome("done", f"Pushed {sha[:7]} to {branch}.", {"branch": f"{self.settings.repo_url(project.slug)}/tree/{branch}"})

    async def do_push_main(self, job: Job, project: Project, step: Step) -> Outcome:
        await self.workspace.commit_all(project.slug, "Work left uncommitted", job=self.tree(job))
        sha = await self.workspace.push_main(project.slug, self.branch(job), job=self.tree(job))
        if sha is None:
            return Outcome("skipped", "Nothing new to push.")
        return Outcome("done", f"Pushed {sha[:7]} to main.", {"commit": self.commit_url(project, sha)})

    async def do_pr(self, job: Job, project: Project, step: Step) -> Outcome:
        steps = self.store.steps(job.id)
        work = _since_last(steps, step, "merge")
        names = " + ".join(s.name for s in work) or ("brief" if job.kind == "adopt" else "changes")
        title = project.name if job.kind in ("new", "adopt") else plan.title(job)
        last = not any(s.kind == "pr" for s in steps if s.position > step.position and s.state == "pending")
        issue = f"{'Closes' if last else 'Part of'} #{job.issue}" if job.issue else ""
        body = "\n\n".join(
            [p for p in (issue, "\n".join(f"- **{s.name}:** {s.summary}" for s in work if s.summary)) if p]
            + [f"Opened by golem (job {job.id}); it merges this once CI passes."]
        )
        pr = await self.workspace.open_pr(project.slug, self.branch(job), f"{title}: {names}", body)
        if pr is None:
            return Outcome("skipped", "Nothing to merge.")
        return Outcome("done", f"PR #{pr.number} is open.", {"pr": pr.url})

    async def do_checks(self, job: Job, project: Project, step: Step) -> Outcome:
        pr = await self.workspace.pull_request(project.slug, self.branch(job))
        if pr is None:
            return Outcome("skipped", "No PR to check.")
        return await self.wait_for_ci(project, step, pr.sha, f"PR #{pr.number}")

    async def do_merge(self, job: Job, project: Project, step: Step) -> Outcome:
        pr = await self.workspace.pull_request(project.slug, self.branch(job))
        if pr is None:
            return Outcome("skipped", "Nothing to merge.")
        sha = await self.workspace.merge(project.slug, pr, self.branch(job), job=self.tree(job))
        return Outcome("done", f"Merged PR #{pr.number} as {sha[:7]}.", {"pr": pr.url, "commit": self.commit_url(project, sha)})

    async def do_deploy(self, job: Job, project: Project, step: Step) -> Outcome:
        """Deploy main once its CI passes; done only when the deployer has the site answering.
        Skipped if this job already deployed that commit (nothing new since: a skipped design pass)."""
        sha = await self.workspace.main_sha(project.slug)
        deployed = [s for s in self.store.steps(job.id) if s.kind == "deploy" and s.state == "done" and s.position < step.position]
        if deployed and deployed[-1].links.get("commit") == self.commit_url(project, sha):
            return Outcome("skipped", f"{sha[:7]} is live already.")
        ci = await self.wait_for_ci(project, step, sha, "main")
        if ci.state != "done":
            return ci
        deployment = await self.deployer.deploy(project.slug, sha)
        if not deployment.ok:
            problem = f"The deployer couldn't deploy {sha[:7]}:\n{deployment.problem}"
            return Outcome("fix", f"the deployer refused {sha[:7]}", {"commit": self.commit_url(project, sha)}, problem)
        url = self.settings.project_url(project.slug)
        if self.store.card(project.slug) is None:  # live for the first time: on the projects page
            await self.refresh_card(project, job)
        return Outcome("done", f"{sha[:7]} is live.", {"site": url, "commit": self.commit_url(project, sha)})

    async def do_checkin(self, job: Job, project: Project, step: Step) -> Outcome:
        """Show Riggy the live site and wait for his reply, which goes to the next step that
        builds (past a design pass's steps, which see it under "Done so far")."""
        if step.state == "waiting":  # he's replied (the bot queued the job again)
            replies = self.store.pending_notes(job.id)
            said = _first_line(replies[-1][1], 200) if replies else ""
            later = [s for s in self.store.steps(job.id) if s.position > step.position and s.kind == "agent"]
            builds = next((s for s in later if s.name not in DESIGN_PASS), None)
            if replies and builds and later[0].name in DESIGN_PASS:
                self.store.aim_notes([i for i, _ in replies], builds.id)
            return Outcome("done", f"You said: “{said}”" if said else "You replied.")
        steps = self.store.steps(job.id)
        message = plan.last_said(steps) or "Something to try is live."
        then = next((s for s in steps if s.position > step.position and s.kind == "agent"), None)
        design = (
            " Then I'll make a few design directions for its look, for you to pick from."
            if then and then.name == "design" and then.state == "pending"
            else ""
        )
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
            if reply.more:  # his reply goes to the new round's step, made like the last
                again = [
                    Step(0, job.id, 0, "agent", made.name, made.max_turns),
                    Step(0, job.id, 0, "choose", "choose", optional=step.optional),
                ]
                self.store.add_steps(job.id, again, after=step.position)
                return Outcome("done", options.again(reply, shown, said, design is not None))
            return Outcome("done", options.picked(reply, shown, said))
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
            await self.chat.send_image(project.slug, folder / page, "The pages they share" if i == 0 else f"Shared ({i + 1})")
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
            await self.chat.send_image(project.slug, folder / image, caption if i == 0 else f"{option.letter} ({i + 1})")

    async def do_alert(self, job: Job, project: Project, step: Step) -> Outcome:
        """The last step: only reached once the final deploy succeeded."""
        url = self.settings.project_url(project.slug)
        steps = self.store.steps(job.id)
        message = plan.last_said(steps) or f"{project.name} is live."
        if job.issue and not any(s.kind == "pr" and s.state == "done" for s in steps):  # no PR closed its issue (reckless, or nothing to merge)
            await self.workspace.close_issue(project.slug, job.issue, f"Done: {message}\n\n{url}")
        await self.say(project, _join(f"Done: {message}", url))
        await self.status(f"Finished job {job.id} on {project.name}: {url}", notice=True)
        await self.refresh_card(project, job)  # what it is now, on the projects page
        return Outcome("done", "Told you it's live.", {"site": url})

    async def refresh_card(self, project: Project, job: Job | None = None) -> None:
        """The project's card on the projects page (cards.py), from what the job just shipped (its
        checkout, on main now) or the project's checkout. Never holds up or fails a step."""
        try:
            checkout = self.checkout(project, job) if job else self.workspace.path(project.slug)
            await self.cards.refresh(self.store.project(project.slug) or project, checkout)
        except Exception:
            log.exception("couldn't make the card for %s", project.slug)

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
                return Outcome("done", f"CI passed on {what} ({sha[:7]}).", links)
            case "failed":
                problem = f"CI failed on {what} (commit {sha[:7]}):\n{ci.detail}"
                return Outcome("fix", f"CI failed on {what}", links, problem)
            case "none" if waited >= CI_START_TIMEOUT:
                problem = (
                    f"No CI workflow ran on {what} (commit {sha[:7]}) in {span(waited)}. The repo needs "
                    ".github/workflows/build.yml, running on pull requests and pushes to main (skills/new-repo.md)."
                )
                return Outcome("fix", f"no CI ran on {what}", links, problem)
            case "pending" if waited >= CI_TIMEOUT:
                return Outcome("error", problem=f"CI on {what} still hasn't finished after {span(waited)}: {ci.detail}")
        return Outcome("wait", f"Waiting for CI on {what}: {ci.detail or 'not started yet'}", links)

    # --- helpers --------------------------------------------------------------

    def branch(self, job: Job) -> str:
        """Where a job's work goes between merges."""
        return f"{JOB_BRANCHES}{job.id}"

    def tree(self, job: Job) -> int | None:
        """Which checkout the job works in, for the workspace's calls: its own tree (its id), or
        None for the project's checkout (a job from before trees)."""
        return job.id if job.worktree else None

    def checkout(self, project: Project, job: Job) -> Path:
        """Where the job's files are: its tree, or the project's checkout."""
        return self.workspace.tree_path(job.id) if job.worktree else self.workspace.path(project.slug)

    def commit_url(self, project: Project, sha: str) -> str:
        return f"{self.settings.repo_url(project.slug)}/commit/{sha}"

    @property
    def lessons_path(self) -> Path:
        return self.settings.state_dir / "lessons.md"

    def progress_path(self, job: Job) -> Path:
        """Where the worker appends progress notes for Riggy, one line each (prompts/step.md)."""
        return self.settings.job_logs(job.id) / "progress.txt"

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
            GOLEM_URL=self.settings.project_url(project.slug),
            GITHUB_ORG=self.settings.org,
            GOLEM_PLATFORM=self.settings.platform,
            DEPLOYER_URL=self.settings.deployer_url,
            GOLEM_SLOT=str(slot),
            GOLEM_PORT=str(PORTS + PORTS_PER_SLOT * slot),
            GOLEM_TMP=tmp,
            TMPDIR=tmp,
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

    async def say(self, project: Project, text: str, notice: bool = False) -> None:
        await self.chat.send(project.slug, text, notice=notice)

    async def status(self, text: str, notice: bool = False) -> None:
        await self.chat.send(STATUS, text, notice=notice)


def _retry(steps: list[Step], failed: Step) -> list[Step]:
    """Golem's steps from the last push up to and including the one that failed, to run again
    after the fix: pushing it, a PR, CI, merging and deploying, as far as the failure got.
    Steps skipped the first time (nothing to merge then) are repeated too: the fix has to land."""
    before = [s for s in steps if s.position <= failed.position]
    start = max((i for i, s in enumerate(before) if s.kind in ("push", "push-main")), default=None)
    if start is None:
        return [Step(0, failed.job_id, 0, failed.kind, failed.name)]
    return [Step(0, failed.job_id, 0, s.kind, s.name) for s in before[start:] if s.kind != "agent"]


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


def _adopt_words(job: Job) -> str:
    """What Riggy said after `!adopt <slug>`, from an adopt job's request."""
    words = job.request.split(maxsplit=2)[2:]
    return words[0] if words else ""


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
