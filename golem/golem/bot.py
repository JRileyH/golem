"""What Riggy's messages turn into, written in the web app (web.py hands them to `handle`).

- In golem's own conversation (the inbox), free text is a new idea: it's triaged into a
  brief, gets a project, its own conversation and a job.
- In a project's conversation, free text is about that project: feedback for the job that's
  waiting on him, a question (answered by a quick look, not a job that builds anything,
  even while a job is under way), a note for the job that's running, or else a new job:
  a change, or, if he asks to see options first, options to pick from and then his pick
  (options.py).
- `!commands` work anywhere; in a project's conversation they default to that project.

One message is handled at a time, so two can't both start a job.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from . import intake, options, plan, teardown
from .config import Settings
from .deployer_client import Deployer, DeployerError
from .harness import Harness
from .prompts import Prompts
from .scheduler import WORKER_ENV_DENY, Scheduler
from .store import ACTIVE, EFFORTS, MODELS, MODES, Job, Project, Store, later
from .talk import INBOX, STATUS, Talk
from .workspace import Workspace, WorkspaceError

log = logging.getLogger(__name__)

HELP = """Commands (in a project's conversation they apply to that project; elsewhere, put the project's slug first):
!queue — what's queued, running and waiting
!status [project] — a project's state, settings and links
!question <what> — just an answer: golem looks into it (the code, the site, its logs) and replies, building nothing; asking a question does the same
!options <what> — mock up a few options (A, B, C) to pick from before building any; asking to see options does the same
!mode reckless|standard|rigorous — how carefully it's built
!model fable|opus|sonnet|haiku|default — the Claude model for all its steps, or each step's default
!effort low|medium|high|xhigh|max|default — how hard they think
!rename <name> — change the project's name
!slug <new-slug> — move it to a new address and repo
!pause / !resume — hold a project while you tinker; with no project, in golem's own conversation, all work
!kill [job] — drop a job
!delete — delete a project for good: site, data, checkout and history (asks you to confirm)

With a new idea you can also say the settings ("rigorous, opus, max effort").
Each kind of step has a default model and effort, set in Settings.
Anything else: in golem's own conversation, a new idea; in a project's, feedback, a question, a change, or a request to see options."""

STARTING = "Starting now."
QUESTION_PRIORITY = 1000  # questions go ahead of other work: an answer shouldn't wait behind a build
SETTINGS = {"mode": MODES, "model": (*MODELS, "default"), "effort": (*EFFORTS, "default")}


class Bot:
    # New projects wait this long before work starts, so Riggy can change their settings.
    start_delay = timedelta(minutes=1)

    def __init__(
        self,
        settings: Settings,
        store: Store,
        chat: Talk,
        scheduler: Scheduler,
        harness: Harness,
        workspace: Workspace,
        prompts: Prompts,
        deployer: Deployer,
    ):
        self.settings = settings
        self.store = store
        self.chat = chat
        self.scheduler = scheduler
        self.harness = harness
        self.workspace = workspace
        self.prompts = prompts
        self.deployer = deployer
        self.lock = asyncio.Lock()

    async def handle(self, channel: str, text: str) -> None:
        """Something Riggy said in a conversation: a project's slug, or the inbox."""
        text = text.strip()
        if not text or channel == STATUS:
            return
        async with self.lock:
            await self.respond(channel, text)

    async def respond(self, channel: str, text: str) -> None:
        await self.chat.heard(channel, text)
        if text.startswith("!"):
            await self.command(channel, text)
            return
        project = self.store.project(channel) if channel != INBOX else None
        if project:
            await self.project_message(project, text)
        elif channel == INBOX:
            await self.new_idea(text)

    # --- new ideas ------------------------------------------------------------

    async def new_idea(self, text: str) -> None:
        brief = await self.triage(text)
        slug = await intake.unique_slug(brief.slug, self.slug_taken)
        mode = brief.mode or self.settings.default_mode
        project = self.store.add_project(
            slug,
            brief.name,
            brief.summary,
            mode=mode,
            model=brief.model or "",  # unless named, each step's default
            effort=brief.effort or "",
        )
        tag, tagged = self.store.tag(brief.tag) if brief.tag else None, ""
        if tag is None and brief.new_tag:  # triage's bar for a new tag is high: say so when it's met
            tag, tagged = self.store.add_tag(brief.tag), f". It's the first {brief.tag}: a new tag (Settings has them)"
        if tag:
            project = self.store.update_project(slug, tag=tag.name)
        url, repo = self.settings.project_url(slug), self.settings.repo_url(slug)
        job = self.store.add_job(slug, "new", text, mode)
        self.store.update_job(job.id, state="parked", retry_at=later(self.start_delay))

        await self.chat.send(
            slug,
            f"{brief.name}\n\n{brief.summary}\n\nYou said: “{text}”\n\n"
            f"It'll live at {url} (code: {repo}). I'll post progress here and check in when there's "
            "an MVP worth looking at. Reply here any time with feedback or changes.\n\n"
            f"{settings_line(project)}. Starting in a minute: to change that first, say "
            "!mode, !model or !effort here (!help for the options).",
        )
        await self.chat.send(
            INBOX,
            f"New project: {brief.name} ({slug}), {settings_line(project).lower()}. "
            f"Its conversation: {self.settings.url('golem')}/p/{slug}{tagged}",
        )
        self.scheduler.wake()

    async def slug_taken(self, slug: str) -> bool:
        return self.store.project(slug) is not None or await self.workspace.repo_exists(slug)

    async def triage(self, text: str) -> intake.Brief:
        if self.settings.harness == "fake":
            return intake.fallback_brief(text)
        return await intake.triage(
            self.harness,
            self.prompts.intake_path(),
            text,
            cwd=self.settings.state_dir / "triage",
            log_path=self.settings.state_dir / "triage" / "last.jsonl.gz",
            env={k: v for k, v in os.environ.items() if k not in WORKER_ENV_DENY},
            tags=[t.name for t in self.store.tags()],
        )

    # --- projects' conversations ----------------------------------------------

    async def project_message(self, project: Project, text: str) -> None:
        job = self.store.active_job(project.slug, questions=False)
        asked = next((j for j in self.store.jobs(("waiting",)) if j.slug == project.slug and j.playbook == "question"), None)
        for waiting in (j for j in (job, asked) if j and j.state == "waiting"):  # his reply to what golem asked
            self.store.add_note(waiting.id, text)
            self.store.update_job(waiting.id, state="queued")
            await self.chat.send(project.slug, f"Thanks, picking it back up. {self.queue_position(waiting)}", notice=True)
            self.scheduler.wake()
            return
        kind = await options.kind_of(self.harness, self.settings, text)
        if kind == "question":
            said = self.start_question(project, text)
            if said:
                await self.chat.send(project.slug, said, notice=True)
            return
        if job is None:
            if kind == "options":
                await self.chat.send(project.slug, self.start_options(project, text), notice=True)
                return
            job = self.store.add_job(project.slug, "change", text, "feature")
            position = self.queue_position(job)
            if position != STARTING:  # otherwise "Working on it" follows straight away
                await self.chat.send(project.slug, f"Got it. {position}", notice=True)
            self.scheduler.wake()
            return
        self.store.add_note(job.id, text)
        if job.state == "running" or self.store.steps(job.id):  # under way, maybe waiting on CI
            await self.chat.send(project.slug, "Noted. I'll work that in at the next step of the current job.", notice=True)
        else:
            await self.chat.send(project.slug, "Noted. I'll include that when the job starts.", notice=True)

    def start_question(self, project: Project, question: str) -> str:
        """A question: a quick look and an answer, ahead of other work. Says so only if it has to wait."""
        job = self.store.add_job(project.slug, "change", question, "question", priority=QUESTION_PRIORITY)
        self.scheduler.wake()
        position = self.queue_position(job)
        if position == STARTING:  # "Looking into it" follows straight away
            return ""
        current = self.scheduler.busy_with()
        if current and not self.scheduler.paused and not project.paused:
            return f"Good question. I'll look into it as soon as the step I'm on for {self.store.project(current.slug).name} finishes."
        return position

    def start_options(self, project: Project, request: str) -> str:
        job = self.store.add_job(project.slug, "change", request, "options")
        self.scheduler.wake()
        position = self.queue_position(job)
        return (
            "Got it: I'll make some options for you to pick from first, then build the one you pick. "
            "(Rather I just built it? !kill, then say it again.)" + ("" if position == STARTING else f" {position}")
        )

    def queue_position(self, job: Job) -> str:
        if self.scheduler.paused:
            return "Work is paused (!resume to start again)."
        if self.store.project(job.slug).paused:
            return "This project is paused (!resume to start again)."
        current = self.scheduler.busy_with()
        if current is None or current.id == job.id:
            return STARTING
        ahead = [j for j in self.store.jobs(("queued",)) if j.id != job.id and (j.priority, -j.id) >= (job.priority, -job.id)]
        busy = self.store.project(current.slug)
        others = f" and {len(ahead)} more queued" if ahead else ""
        return f"Queued: I'm on {busy.name}{others} first."

    # --- commands -------------------------------------------------------------

    async def command(self, channel: str, text: str) -> None:
        name, *args = text[1:].split() or [""]
        here = self.store.project(channel) if channel != INBOX else None
        reply = await self.run_command(name.lower(), args, here)
        if here and self.store.project(channel) is None:  # deleted, or moved to a new slug
            moved = self.store.project(args[-1].lower()) if name.lower() in ("slug", "move") and args else None
            channel = moved.slug if moved else INBOX
        if reply:  # "" when there's nothing to say yet (a question that starts straight away)
            await self.chat.send(channel, reply, notice=True)

    async def run_command(self, name: str, args: list[str], here: Project | None) -> str:
        match name:
            case "help":
                return HELP
            case "queue":
                return self.queue_text()
            case "status":
                project = self.pick_project(args, here)
                if project:
                    return self.status_text(project)
                return self.queue_text() if not args else f"No project called {args[0]}."
            case "pause" | "resume":
                paused = name == "pause"
                if not args and not here:
                    self.scheduler.set_paused(paused)
                    return "Paused all work. The current step finishes first." if paused else "Resumed."
                project = self.pick_project(args, here)
                if not project:
                    return f"No project called {args[0]}."
                self.store.set_paused(project.slug, paused)
                self.scheduler.wake()
                if paused:
                    return f"Paused {project.name}. A step already running finishes first; nothing new starts until !resume."
                return f"Resumed {project.name}."
            case "kill":
                job = self.pick_job(args, here)
                if job is None:
                    return "Which job? !kill <number> (see !queue)."
                if not self.scheduler.kill(job.id):
                    return f"Job {job.id} has already finished."
                return f"Killed job {job.id} ({self.store.project(job.slug).name})."
            case "mode" | "model" | "effort":
                return self.set_setting(name, args, here)
            case "question":
                project, words = (here, args) if here else (self.pick_project(args[:1], None), args[1:])
                if not project or not words:
                    return "Usage: !question <what> in the project's conversation, or !question <slug> <what>."
                return self.start_question(project, " ".join(words)) or ""
            case "options":
                project, words = (here, args) if here else (self.pick_project(args[:1], None), args[1:])
                if not project or not words:
                    return "Usage: !options <what to pick between> in the project's conversation, or !options <slug> <what>."
                job = self.store.active_job(project.slug, questions=False)
                if job:
                    return f"{project.name} has a job under way (#{job.id}). Ask again when it's done, or !kill it first."
                return self.start_options(project, " ".join(words))
            case "rename":
                project, words = (here, args) if here else (self.pick_project(args[:1], None), args[1:])
                if not project or not words:
                    return "Usage: !rename <new name> in the project's conversation, or !rename <slug> <new name>."
                new = " ".join(words)[:60]
                self.store.update_project(project.slug, name=new)
                return f"Renamed {project.name} to {new}. The address stays {project.slug}; !slug changes that."
            case "slug" | "move":
                project, rest = (here, args) if here else (self.pick_project(args[:1], None), args[1:])
                if not project or len(rest) != 1:
                    return "Usage: !slug <new-slug> in the project's conversation, or !slug <old-slug> <new-slug>."
                return await self.move(project, rest[0].lower())
            case "delete":
                return await self.delete(args, here)
            case _:
                return f"Unknown command !{name}.\n\n{HELP}"

    def pick_project(self, args: list[str], here: Project | None) -> Project | None:
        return self.store.project(args[0].lower()) if args else here

    def pick_job(self, args: list[str], here: Project | None) -> Job | None:
        if args:
            return self.store.job(int(args[0].lstrip("#"))) if args[0].lstrip("#").isdigit() else None
        return self.store.active_job(here.slug) if here else None

    def queue_text(self) -> str:
        jobs = self.store.jobs(ACTIVE)
        lines = [self.job_line(j) for j in jobs]
        head = "Work is paused.\n" if self.scheduler.paused else ""
        return head + ("\n".join(lines) if lines else "Nothing queued.")

    def job_line(self, job: Job) -> str:
        project = self.store.project(job.slug)
        steps = plan.planned(self.settings, self.store, job)
        step = plan.current(steps)
        what = "new project" if job.kind == "new" else job.playbook if job.playbook in ("options", "question") else "change"
        held = " (project paused)" if project.paused else ""
        state = job.state
        if state == "parked":  # a grace period before a new project starts, CI running, or a retry
            if not self.store.steps(job.id):
                state = "starting shortly"
            elif step and step.kind in ("checks", "deploy") and not step.error:
                state = "waiting for CI"
            else:
                state = "retrying soon"
        return f"#{job.id} {project.name}: {what}, {state}, {plan.where(steps)}{held}"

    def status_text(self, project: Project) -> str:
        job = self.store.active_job(project.slug)
        state = self.job_line(job) if job else "Idle."
        return (
            f"{project.name} ({project.slug})\n{settings_line(project)}\n{state}\n"
            f"{self.settings.project_url(project.slug)}\n{self.settings.repo_url(project.slug)}"
        )

    # --- settings -------------------------------------------------------------

    def set_setting(self, setting: str, args: list[str], here: Project | None) -> str:
        allowed = SETTINGS[setting]
        project, rest = (here, args) if here and len(args) == 1 else (self.pick_project(args[:1], None), args[1:])
        if not project or len(rest) != 1 or rest[0].lower() not in allowed:
            where = "" if here else " <project>"
            return f"Usage: !{setting}{where} {'|'.join(allowed)}"
        value = rest[0].lower()
        project = self.store.update_project(project.slug, **{setting: "" if value == "default" else value})
        reply = f"{project.name}: {settings_line(project).lower()}."
        job = self.store.active_job(project.slug, questions=False)
        if setting == "mode" and job and self.store.steps(job.id):
            reply += f" The job already under way keeps its steps; later jobs follow {value} rules."
        elif setting == "mode" and job and job.kind == "new" and job.playbook != value:
            self.store.update_job(job.id, playbook=value)
            reply += f" The new-project job will follow the {value} playbook."
        elif setting != "mode" and job:
            reply += " Takes effect from the next step."
        return reply

    async def move(self, project: Project, new: str) -> str:
        """A new slug means a new address and a new repo: golem moves the code, a job fixes the references."""
        old = project.slug
        if new == old:
            return f"{project.name} is already at {old}."
        if not intake.valid_slug(new):
            return f"{new} won't work as an address: 2 to 31 lowercase letters, digits and hyphens, not reserved."
        if await self.slug_taken(new):
            return f"{new} is taken (by a project or an existing repo)."
        job = self.store.active_job(old)
        if job and job.state == "running":
            return "A step is running. Try again when it finishes, or !pause the project first."
        try:
            await self.workspace.move_repo(old, new, project.summary)
        except WorkspaceError as e:
            return f"Couldn't move the repo: {e}"
        await self.deployer.stop(old)
        project = self.store.move_project(old, new)
        url, repo = self.settings.project_url(new), self.settings.repo_url(new)

        request = (
            f"This project moved from `{old}` to `{new}`. The code is now in {repo} (pushed by golem, with "
            f"the full history) and it will live at {url}. Update every reference to the old slug (the image "
            f"in deploy/compose.yml, README, AGENTS.md, docs, code), wait for CI on main, deploy, and check "
            f"the new URL works. The old site has been stopped."
        )
        if job:
            self.store.add_note(job.id, request)
            follow = "The current job will update the references and redeploy."
        else:
            self.store.add_job(new, "change", request, "feature")
            self.scheduler.wake()
            follow = "A job is updating the references and will redeploy."
        return (
            f"Moved {project.name} to {new}: {repo}, soon live at {url}. {follow} "
            f"The old repo {self.settings.repo_url(old)} is still there; delete it on GitHub when you like "
            "(my token can't)."
        )

    async def delete(self, args: list[str], here: Project | None) -> str:
        """All of a project but its repo. Riggy confirms by typing the slug."""
        if not args:
            if not here:
                return "Usage: !delete <slug>. It deletes the project for good."
            return (
                f"This deletes {here.name} for good: the site and its data, the code checkout, every job and "
                f"its logs, and this conversation. The repo stays on GitHub for you to delete. "
                f"To go ahead, say !delete {here.slug}"
            )
        project = self.store.project(args[0].lower())
        if not project:
            return f"No project called {args[0]}."
        await self.scheduler.drop_project(project.slug)
        try:
            await teardown.delete_project(project, self.settings, self.store, self.workspace, self.deployer)
        except (DeployerError, WorkspaceError) as e:
            return (
                f"Couldn't delete all of {project.name}: {e}. Its jobs are stopped; "
                f"say !delete {project.slug} to try again."
            )
        return (
            f"Deleted {project.name}: its site and data, checkout, jobs, logs and conversation are gone. "
            f"Delete the repo yourself on GitHub (Settings, at the bottom): {self.settings.repo_url(project.slug)}/settings"
        )


def settings_line(project: Project) -> str:
    if not project.model and not project.effort:
        return f"Mode {project.mode}, each step's default model and effort"
    model = f"model {project.model}" if project.model else "each step's default model"
    effort = f"{project.effort} effort" if project.effort else "default effort"
    return f"Mode {project.mode}, {model}, {effort}"
