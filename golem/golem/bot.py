"""What Riggy starts from the web app (web.py): projects, jobs, and the few changes to a project
that need more than its settings.

- A new project, from the New project form: Riggy names it and says what it's for, which
  workflow and mode it starts with, and its tag; golem only writes its summary and its first
  job's title (intake.summarize), and the job starts straight away.
- Adopting a repo in the org that golem didn't make, from the same page: the quick checks
  answer at once (adopt.py); then it's a project like any other, its job checking it over.
- A request on a project's page: always a job of its own, queued behind any change under way
  (turns.py). The request call titles it and, unless Riggy picked them, says which workflow it's
  for (a change, options first, a design pass, only an answer…).
- Moving a project to a new address, and deleting one.

One at a time, so two can't both take the same address.
"""

from __future__ import annotations

import asyncio
import logging
import os

from . import adopt, checklist, intake, options, plan, teardown
from .config import Settings
from .deployer_client import Deployer, DeployerError
from .harness import Harness
from .prompts import Prompts
from .scheduler import WORKER_ENV_DENY, Scheduler
from .store import Job, Project, Store
from .talk import Talk
from .workflows import Builtin
from .workspace import About, Workspace, WorkspaceError

log = logging.getLogger(__name__)

QUESTION_PRIORITY = 1000  # questions go ahead of other work: an answer shouldn't wait behind a build


class Bot:
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
        self.library = scheduler.library
        self.lock = asyncio.Lock()

    # --- new projects ---------------------------------------------------------

    async def new_project(self, name: str, idea: str, *, slug: str, workflow: str = "", mode: str | None = None, tag: str = "",
                          model: str = "", effort: str = "", vacuum: bool = False) -> Job:  # fmt: skip
        """A project as Riggy named it on the New project form, and its first job, which starts
        straight away: the form already said all a minute's grace was for. `workflow`: one for new
        projects by key, else the fallback; `mode` one of golem's (plan.MODES), or "" for none. The
        project's own default mode stays unset: only Riggy sets that, in its settings. Raises
        ValueError if the address can't be had (web.py checks first, and says why)."""
        async with self.lock:
            if not intake.valid_slug(slug) or await self.slug_taken(slug):
                raise ValueError(f"{slug} can't be had.")
            wf = self.library.get(workflow)
            if wf is None or wf.start != "new":
                wf = self.library.fallback("new")
            about = await self.summarize(name, idea)
            project = self.store.add_project(slug, name.strip()[:60], about.summary, mode="", model=model, effort=effort)
            if tag and (found := self.store.tag(tag)):
                project = self.store.update_project(slug, tag=found.name)
            if vacuum:
                project = self.store.update_project(slug, vacuum=1)
            title = about.title or project.name  # without a model: what he called it, not his first line cut short
            job = self.start_job(project, wf, plan.mode_for(project, mode or ""), idea.strip(), title, kind="new")
            await self.chat.heard(slug, idea.strip(), job=job.id)
            return job

    async def new_twins(self, name: str, idea: str, *, slug: str, workflow: str = "", tag: str = "", model: str = "",
                        effort: str = "") -> tuple[Job, Job]:  # fmt: skip
        """The same idea built both ways, to compare them (checklist.py): a project the usual way at
        `slug`, and one in checklist mode at `cl-<slug>`, hosted alike. Both run in a vacuum, as on
        a fresh golem: nothing golem has learnt, and nothing from each other. Raises ValueError if
        either address can't be had."""
        twin = checklist.twin_slug(slug)
        if not intake.valid_slug(twin) or await self.slug_taken(twin):
            raise ValueError(f"{twin} can't be had.")
        made = []
        for each, mode, label in ((slug, "", name), (twin, checklist.MODE, f"{name} (checklist)"[:60])):
            own = self.settings.lessons(each)  # lessons of its own, from nothing
            own.parent.mkdir(parents=True, exist_ok=True)
            own.write_text("")
            made.append(await self.new_project(label, idea, slug=each, workflow=workflow, mode=mode, tag=tag, model=model, effort=effort,
                                               vacuum=True))  # fmt: skip
        return made[0], made[1]

    async def summarize(self, name: str, idea: str) -> intake.Summary:
        if self.settings.harness == "fake":
            return intake.Summary(intake.idea_summary(idea), "")
        return await intake.summarize(
            self.harness,
            self.prompts.dir / "summary.md",
            name,
            idea,
            cwd=self.settings.state_dir / "triage",
            log_path=self.settings.state_dir / "triage" / "summary.jsonl.gz",
            env={k: v for k, v in os.environ.items() if k not in WORKER_ENV_DENY},
        )

    async def slug_taken(self, slug: str) -> bool:
        return self.store.project(slug) is not None or await self.workspace.repo_exists(slug)

    # --- adopting a repo golem didn't make (adopt.py) ---------------------------

    async def adopt(self, repo: str, words: str = "", *, name: str = "", mode: str | None = None, tag: str = "") -> tuple[Job | None, str]:
        """A repo in the org golem didn't make, taken on: the quick checks answer at once (a
        refusal says what's in the way, and nothing's made); then it's a project like a new one,
        its job checking it over (vet), tidying what's missing and deploying it. `name` "" names
        it from its README; what Riggy says it is (`words`) wins over its README for the summary."""
        slug, words = repo.strip().lower(), words.strip()
        async with self.lock:
            if existing := self.store.project(slug):
                return None, f"{existing.name} ({slug}) is already one of golem's projects."
            about = await self.workspace.about(slug) if intake.valid_slug(slug) else None
            if blocked := adopt.preflight(slug, about, False, self.settings.org):
                return None, adopt.refusal(slug, blocked)
            brief = await self.adopt_brief(slug, words, about)
            project = self.store.add_project(slug, (name.strip() or brief.name)[:60], brief.summary, mode="")
            if tag and (found := self.store.tag(tag)):
                project = self.store.update_project(slug, tag=found.name)
            wf = self.builtin("adopt", "adopt")
            job = self.start_job(project, wf, plan.mode_for(project, mode or ""), words or plan.adopting(slug), brief.title, kind="adopt")
            if words:
                await self.chat.heard(slug, words, job=job.id)
            return job, f"Adopting {project.name}: first golem checks it over."

    async def adopt_brief(self, slug: str, words: str, about: About) -> intake.Brief:
        if self.settings.harness == "fake":
            return intake.adopt_fallback(slug, words, about)
        return await intake.adopt_triage(
            self.harness,
            self.prompts.dir / "adopt.md",
            slug,
            words,
            about,
            cwd=self.settings.state_dir / "triage",
            log_path=self.settings.state_dir / "triage" / "adopt.jsonl.gz",
            env={k: v for k, v in os.environ.items() if k not in WORKER_ENV_DENY},
            tags=[t.name for t in self.store.tags()],
        )

    # --- workflows (workflows.py) -------------------------------------------------

    def kinds(self) -> list[options.Kind]:
        """The workflows a project's request can be for: changes, and those that only look."""
        return [options.Kind(w.key, w.start == "look", w.ingest) for w in self.library.choices(("change", "look"))]

    def builtin(self, key: str, start: str) -> Builtin | None:
        """One of golem's own workflows by key, if it's there, else the start type's fallback."""
        wf = self.library.get(key)
        return wf if wf and wf.start == start else self.library.fallback(start)

    def start_job(self, project: Project, wf: Builtin, mode: str, request: str, title: str = "", priority: int = 0, kind: str = "") -> Job:
        """A job for a request, running a workflow in a mode."""
        kind = kind or {"new": "new", "adopt": "adopt"}.get(wf.start, "change")
        job = self.store.add_job(project.slug, kind, request, wf.key, priority=priority, title=title, start=wf.start)
        job = self.store.update_job(job.id, workflow=wf.key, mode=mode)
        self.scheduler.wake()
        return job

    # --- requests on a project's page -------------------------------------------

    async def ask(self, project: Project, text: str, *, workflow: str = "", mode: str | None = None) -> Job:
        """What Riggy asked for on a project's page: always a job of its own, queued behind any
        change under way (turns.behind). The request call titles it and, unless he picked them,
        says which workflow it's for. `mode` None for the project's default, "" for none."""
        fallback = self.library.fallback("change")
        asked = await options.read_request(self.harness, self.settings, text, self.kinds(), fallback.key if fallback else "")
        picked = self.library.get(workflow)
        if picked is None or picked.start not in ("change", "look"):
            picked = self.library.get(asked.workflow) or fallback
        mode = plan.mode_for(project) if mode is None else plan.mode_for(None, mode)
        async with self.lock:
            job = self.start_job(project, picked, mode, text, asked.title, priority=QUESTION_PRIORITY if picked.start == "look" else 0)
        await self.chat.heard(project.slug, text, job=job.id)
        return job

    # --- a project's address, and deleting it -----------------------------------

    async def move(self, project: Project, new: str) -> str:
        """A new slug means a new address and a new repo: golem moves the code, a job fixes the references."""
        old, new = project.slug, new.strip().lower()
        if new == old:
            return f"{project.name} is already at {old}."
        if not intake.valid_slug(new):
            return f"{new} won't work as an address: 2 to 31 lowercase letters, digits and hyphens, not reserved."
        if await self.slug_taken(new):
            return f"{new} is taken (by a project or an existing repo)."
        job = self.store.active_job(old)
        if any(j.slug == old for j in self.scheduler.running_jobs()) or (job and job.state == "running"):
            return "A step is running. Try again when it finishes, or pause the project first."
        try:
            await self.workspace.move_repo(old, new, project.summary)
        except WorkspaceError as e:
            return f"Couldn't move the repo: {e}"
        await self.deployer.stop(old)
        project = self.store.move_project(old, new)
        if (lessons := self.settings.lessons(old)).exists():  # a project in a vacuum's own lessons go with it
            lessons.rename(self.settings.lessons(new))
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
            self.start_job(project, self.library.fallback("change"), plan.mode_for(project), request,
                           f"Move to {new}")  # fmt: skip
            follow = "A job is updating the references and will redeploy."
        return (
            f"Moved {project.name} to {new}: {repo}, soon live at {url}. {follow} "
            f"The old repo {self.settings.repo_url(old)} is still there; delete it on GitHub when you like "
            "(golem's token can't)."
        )

    async def delete(self, project: Project) -> str:
        """All of a project but its repo (web.py has Riggy type its slug to confirm)."""
        await self.scheduler.drop_project(project.slug)
        try:
            await teardown.delete_project(project, self.settings, self.store, self.workspace, self.deployer)
        except (DeployerError, WorkspaceError) as e:
            return f"Couldn't delete all of {project.name}: {e}. Its jobs are stopped; delete it again to try again."
        return (
            f"Deleted {project.name}: its site and data, checkout, jobs, logs and history are gone. "
            f"Delete the repo yourself on GitHub (Settings, at the bottom): {self.settings.repo_url(project.slug)}/settings"
        )
