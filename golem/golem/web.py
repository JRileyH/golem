"""The web app at golem.<base domain>, and the health check.

Four tabs: Now, every job and what needs Riggy (now.py); Projects, a card each, each opening
its page of jobs beside the one being looked at (projectpage.py); Workflows (flows.py); and
Settings (home.py). Anyone can look at what's under way: each project's jobs, each job's
route, graph and steps, each step's details (with the pictures of any options Riggy is
picking from), and where the time and usage go (insights.py).

Signed in with GOLEM_WEB_PASSWORD, it's how Riggy works with golem: asking for something on a
project's page (always a job of its own: bot.ask), replying to what a job asks and adding
notes, and the web app's own controls (control.py): skipping optional steps, reordering the
queue, pausing, stopping, models and effort (per step, project or kind of step). golem's own
conversation still takes new ideas (`/say`, bot.py), until the New project form.

The sign-in cookie is an expiry signed with a key made from a random secret in the
database and the password, so changing the password signs everyone out. It lasts 90
days on a device Riggy says to keep, 24 hours otherwise. It's HttpOnly and SameSite=Lax
(so a notification's link opens signed in), and changes must come from this site's own
pages (Origin check).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import TYPE_CHECKING
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Host, Route, Router

from . import brand, config, conversation, engine, flows, gallery, home, insights, intake, options, pages, plan, pwa, turns, workflows
from . import now as now_page
from . import about, jobhistory, newproject, projectpage, reports, reportspage, stages, triage
from .cards import fallback
from .config import Settings
from .control import Control, ControlError
from .live import LiveRuns
from .pages import LaneData, NavProject, SheetData
from .push import Push
from .scheduler import Scheduler
from .store import ACTIVE, EFFORTS, INTERNAL, LOOKS, MODELS, TRIAGE, Job, Project, Said, Step, Store, Workflow
from .talk import STATUS

if TYPE_CHECKING:
    from .bot import Bot

COOKIE = "golem_token"
TOKEN_SECONDS = 24 * 3600
KEEP_SECONDS = 90 * 24 * 3600  # on a device Riggy keeps signed in: his phone's home-screen app
MAX_FAILURES = 10  # wrong passwords in FAILURE_WINDOW before sign-in is refused for a while
FAILURE_WINDOW = 15 * 60
SHOWN = 40  # a conversation's latest messages on its page; "Show earlier messages" adds as many again
# A design doc is a worker's HTML on golem's own site, where Riggy is signed in. In a sandbox its
# origin is no one's (a request from it says Origin: null, which _same_site refuses), and it can
# run nothing and load nothing but what's in the file. golem-render-doc renders it the same way.
DOC_POLICY = (
    "sandbox; default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class Auth:
    def __init__(self, settings: Settings, store: Store, clock=time.time):
        self.settings = settings
        self.store = store
        self.clock = clock
        self.failures: deque[float] = deque()

    def key(self) -> bytes:
        secret = self.store.get("web_secret")
        if not secret:
            secret = secrets.token_hex(32)
            self.store.put("web_secret", secret)
        return hashlib.sha256(f"{secret}:{self.settings.web_password}".encode()).digest()

    def token(self, seconds: int = TOKEN_SECONDS) -> str:
        expires = str(int(self.clock()) + seconds)
        return f"{expires}.{hmac.new(self.key(), expires.encode(), hashlib.sha256).hexdigest()}"

    def valid(self, token: str | None) -> bool:
        expires, _, signature = (token or "").partition(".")
        if not expires.isdigit() or int(expires) < self.clock():
            return False
        expected = hmac.new(self.key(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def check_password(self, password: str) -> bool | None:
        """True or False; None while too many wrong guesses are recent."""
        now = self.clock()
        while self.failures and self.failures[0] < now - FAILURE_WINDOW:
            self.failures.popleft()
        if len(self.failures) >= MAX_FAILURES:
            return None
        if hmac.compare_digest(password.encode(), self.settings.web_password.encode()):
            return True
        self.failures.append(now)
        return False


def create_app(
    settings: Settings, store: Store, scheduler: Scheduler, bot: Bot | None = None, push: Push | None = None
) -> Starlette:
    auth = Auth(settings, store)
    push = push or Push(settings, store)
    control = Control(store, scheduler)
    live = LiveRuns()
    secure = settings.base_domain != "localhost"

    def signed_in(request: Request) -> bool:
        return auth.valid(request.cookies.get(COOKIE))

    def visible_project(slug: str) -> Project | None:
        """One of Riggy's projects by slug; never golem's own (INTERNAL), which has no pages."""
        return store.project(slug) if slug != INTERNAL else None

    def colors() -> dict[str, int]:
        """Each project's color, 1-8, in the order they were made: it stays as others come and go."""
        made = sorted(store.projects(), key=lambda p: p.created_at)
        return {p.slug: i % 8 + 1 for i, p in enumerate(made)}

    def lane(job: Job | None, project: Project, position: int | None, signed: bool, ctx: dict) -> LaneData:
        steps = plan.planned(settings, store, job) if job else []
        current = plan.current(steps)
        lines, notes = [], []
        if job and current and current.kind == "agent" and job.state == "running":
            runs = store.runs(job.id)
            if runs and runs[-1]["outcome"] is None:
                lines = live.lines(runs[-1])
            notes = pages.read_notes(scheduler.progress_path(job))
        active = job is not None and job.state in ACTIVE
        return LaneData(
            project=project,
            job=job,
            steps=steps,
            state=_state(store, job, steps) if job else "idle",
            live=lines,
            notes=notes,
            skippable={s.position for s in steps if active and signed and control.skippable(job, s)},
            restorable={s.position for s in steps if active and signed and control.restorable(job, s)},
            position=position,
            site=settings.project_url(project.slug),
            repo=settings.repo_url(project.slug),
            color=ctx["colors"].get(project.slug, 1),
            etas=ctx["etas"].get(job.id, {}) if job else {},
            tallies=insights.per_step(store, job.id) if job else {},
            known=ctx["known"],
        )

    def queue_order() -> list[Job]:
        """Unfinished jobs in the order the workers will take them, the running ones first."""
        jobs = store.jobs(ACTIVE)
        running = [j for j in jobs if j.state == "running"]
        ready = [j for j in jobs if j.state in ("queued", "parked") and not store.project(j.slug).paused]
        return running + ready

    def context(now: datetime) -> dict:
        """What every lane needs: the queue, colors, usual step times and the forecast. A running
        job is first in the queue, and so is each beside it: the job after them is second."""
        order = queue_order()
        known = insights.estimates(store)
        planned = [(j, plan.planned(settings, store, j)) for j in order]
        beside = {p.slug for p in store.projects() if p.parallel_jobs > 1}  # their changes overlap: roughly
        etas = {} if scheduler.paused else insights.forecast(planned, known, now, scheduler.workers, beside)
        extra = max(sum(j.state == "running" for j in order) - 1, 0)
        places = {j.id: 1 if j.state == "running" else i - extra for i, j in enumerate(order, start=1)}
        return {"order": places, "colors": colors(), "known": known, "etas": etas}

    def sheet_html(job: Job, step: Step, signed: bool) -> str:
        project = store.project(job.slug)
        now = datetime.now(UTC)
        ctx = context(now)
        data = lane(job, project, ctx["order"].get(job.id), signed, ctx)
        if step.state in ("running", "waiting") and step.kind == "agent" and not data.live:
            runs = [r for r in store.runs(job.id) if r["step_id"] == step.id and r["outcome"] is None]
            data.live = live.lines(runs[-1]) if runs else []
        runs = [r for r in store.runs(job.id) if step.id and r["step_id"] == step.id]
        engine = inherited = None
        block = plan.block_of(settings, store, job, step)
        if step.kind == "agent":
            engine = plan.engine(settings, store, project, step, block)
            inherited = plan.engine(settings, store, project, replace(step, model=None, effort=None), block)
        if step.kind == "agent":
            does = step.problem.splitlines()[0] if step.name in ("fix", "resolve") and step.problem else ""
            if not does and block:
                does = _first_paragraph(block.get("instructions") or "")
        elif step.kind == "script" and block:
            does = f"Runs `{block.get('command', '')}` in the job's checkout: it passes if that exits 0."
        else:
            does = pages.DOES.get(step.kind, "")
        href = pages.step_href(job, step)
        return pages.sheet(
            SheetData(
                lane=data,
                step=step,
                href=href,
                engine=engine,
                inherited=inherited,
                does=does,
                runs=runs,
                transcripts={r["id"]: live.transcript(r) for r in runs if r["outcome"] is not None},
                notes=(store.step_notes(step.id) if step.id else []) + (store.node_notes(job.id, step.node_id) if step.node_id else []),
                configurable=control.configurable(job, step),
            ),
            signed,
            now,
        )

    def project_lane(project: Project, signed: bool) -> tuple[LaneData, list[Job]]:
        now = datetime.now(UTC)
        ctx = context(now)
        jobs = [j for j in store.jobs(limit=500) if j.slug == project.slug]
        job = store.active_job(project.slug) or (jobs[0] if jobs else None)
        return lane(job, project, ctx["order"].get(job.id) if job else None, signed, ctx), jobs

    def project_lanes(project: Project, signed: bool) -> tuple[list[LaneData], list[Job]]:
        """The project's lane, and one for each other change under way if it allows several at once."""
        main, jobs = project_lane(project, signed)
        if project.parallel_jobs <= 1:
            return [main], jobs
        ctx = context(datetime.now(UTC))
        more = [lane(j, project, ctx["order"].get(j.id), signed, ctx) for j in store.change_jobs(project.slug) if not main.job or j.id != main.job.id]
        return [main, *more], jobs

    def nav() -> list[NavProject]:
        """Every project for the sidebar, by name so each stays where it's looked for."""
        palette = colors()
        out = []
        for p in sorted(store.projects(), key=lambda p: p.name.lower()):
            job = store.active_job(p.slug)
            if p.parallel_jobs > 1:  # several changes: one waiting on Riggy shows first, then one working
                changes = store.change_jobs(p.slug)
                job = next((j for j in changes if j.state == "waiting"), None) or next((j for j in changes if j.state == "running"), job)
            state = job.state if job and job.state in ("waiting", "running") and not p.paused else ""
            out.append(NavProject(p.slug, p.name, palette.get(p.slug, 1), state))
        return out

    def respond(request: Request, title: str, body: str, signed: bool, bottom: str = "", here: str = "",
                up: tuple[str, str] | None = None, subnav: str = "", stage: str = "", parts: dict[str, str] | None = None) -> Response:  # fmt: skip
        """A page; or, for its script's refresh, the parts it asks for (a page in regions: pages.region)
        or the whole of its main part."""
        if parts is not None and "parts" in request.query_params:
            wanted = request.query_params["parts"].split(",")
            return JSONResponse({k: parts[k] for k in wanted if k in parts}, headers={"Cache-Control": "no-store"})
        if request.query_params.get("part") == "lanes":
            return HTMLResponse(body, headers={"Cache-Control": "no-store"})
        flash = request.query_params.get("msg", "")[:300]
        behind = signed and scheduler.upgrades.behind() > 0
        html = pages.page(title, body, signed, scheduler.paused, flash, bottom=bottom, here=here, up=up, subnav=subnav,
                          nav=nav(), behind=behind, stage=stage)  # fmt: skip
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def tabs(project: Project, here: str, signed: bool) -> str:
        """A project's tabs: its jobs, settings, and what was said before jobs had their own activity."""
        return pages.project_tabs(project, colors().get(project.slug, 1), here, signed, store.has_history(project.slug))

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "harness": settings.harness})

    def job_lanes(signed: bool, now: datetime) -> tuple[list[LaneData], list[LaneData]]:
        """A lane for every unfinished job, in the queue's order, and for the latest finished ones."""
        ctx = context(now)
        order = ctx["order"]
        active = sorted(store.jobs(ACTIVE, limit=1000), key=lambda j: (order.get(j.id, 999), j.id))
        lanes_ = [lane(j, store.project(j.slug), order.get(j.id), signed, ctx) for j in active if visible_project(j.slug)]
        recent = [j for j in store.jobs(limit=60) if j.state not in ACTIVE and visible_project(j.slug)][: now_page.RECENT_SHOWN]
        finished = [lane(j, store.project(j.slug), None, signed, ctx) for j in recent]
        live.forget({r["id"] for j in active for r in store.runs(j.id)[-1:]})
        return lanes_, finished

    def flow_names() -> dict[str, str]:
        return {w.key: w.name for w in store.workflows()}

    def news() -> now_page.News:
        """What's true of golem right now (paused is the page's own banner), and what it said lately."""
        conditions = []
        if store.get("limit_notice") == "1":
            conditions.append("Out of Claude usage: jobs are parked and retry every 30 minutes.")
        if scheduler.upgrades.holding():
            conditions.append("Upgrading golem: nothing new starts until it's back.")
        return now_page.News(conditions, store.messages(STATUS, limit=5))

    async def index(request: Request) -> Response:
        """Now: every job golem has, what needs Riggy first."""
        signed = signed_in(request)
        now = datetime.now(UTC)
        active, finished = job_lanes(signed, now)
        waiting = {}
        if signed:
            for p in store.projects():
                if open_ := store.proposals(p.slug, ("open",)):
                    waiting[p.slug] = (p, open_)
            if open_ := store.proposals(INTERNAL, ("open",)):  # golem's fixes to itself (triage.py)
                waiting[INTERNAL] = (store.project(INTERNAL), open_)
        body = now_page.page(active, finished, waiting, news(), flow_names(), signed, now)
        return respond(request, "golem", body, signed, here="now")

    async def projects_list(request: Request) -> Response:
        """Every project as a card, with its latest job's route: the busiest and latest first."""
        signed = signed_in(request)
        now = datetime.now(UTC)
        ctx = context(now)
        recent = store.jobs(limit=500)
        cards = []
        images = {}
        for project in store.projects():
            mine = [j for j in recent if j.slug == project.slug]
            job = store.active_job(project.slug, questions=False) or next((j for j in mine if j.start not in LOOKS), None) or (mine[0] if mine else None)
            cards.append(lane(job, project, ctx["order"].get(job.id) if job else None, signed, ctx))
            card = store.card(project.slug)
            if card and card.image:
                images[project.slug] = f"/cards/{card.image}?v={quote(card.updated_at)}"
        cards.sort(key=lambda x: (x.job is None or x.job.state not in ACTIVE, -(_dt_seconds(x.job.updated_at) if x.job else 0)))
        tags = {t.name: t.color for t in store.tags()}
        return respond(request, "Projects", now_page.projects(cards, flow_names(), tags, images, signed, now), signed, here="projects")

    async def settings_page(request: Request) -> Response:
        signed = signed_in(request)
        since = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
        body = home.settings(signed, scheduler.paused, control.kinds(), store.defaults(),
                             (settings.default_model, settings.default_effort), insights.report(store, since),
                             store.tags(), store.tag_counts(), scheduler.upgrades, scheduler.workers, config.memory_cap())  # fmt: skip
        return respond(request, "Settings", body, signed, here="settings")

    async def step_page(request: Request) -> Response:
        step = store.step(request.path_params["step_id"])
        if step is None:
            return HTMLResponse("No such step.", 404)
        return show_step(request, store.job(step.job_id), step)

    async def planned_page(request: Request) -> Response:
        """A step of a job that hasn't started, by position (it's stored once the job starts)."""
        job = store.job(request.path_params["job_id"])
        if job is None:
            return HTMLResponse("No such job.", 404)
        position = request.path_params["position"]
        step = next((s for s in plan.planned(settings, store, job) if s.position == position), None)
        if step is None:
            return HTMLResponse("No such step.", 404)
        return show_step(request, job, step)

    def show_step(request: Request, job: Job, step: Step) -> Response:
        signed = signed_in(request)
        body = sheet_html(job, step, signed)
        if request.query_params.get("part") == "sheet":
            return HTMLResponse(body, headers={"Cache-Control": "no-store"})
        body = f"<article class=card>{body}</article><p><a href='{projectpage.href(job)}'>The whole job</a></p>"
        project = visible_project(job.slug)
        if project is None:  # one of golem's own jobs (its triage): shown with its reports
            return respond(request, plan.label(step), body, signed, here="reports", up=("/reports", "Reports"))
        return respond(request, plan.label(step), body, signed, here=job.slug, up=(projectpage.href(job), f"Job #{job.id}"),
                       subnav=tabs(project, "", signed))  # fmt: skip

    async def insights_page(request: Request) -> Response:
        signed = signed_in(request)
        raw = request.query_params.get("days", "30")
        days = int(raw) if raw.isdigit() and 0 < int(raw) <= 3650 else None
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
        report = insights.report(store, since)
        body = pages.insights(report, insights.findings(report), days)
        return respond(request, "Insights", body, signed, here="insights", up=("/settings", "Settings"))

    async def reports_page(request: Request) -> Response:
        """Reports about golem itself, from its workers and its own incidents (reportspage.py)."""
        signed = signed_in(request)
        if not signed:
            body = pages.signin_card("/reports")
        else:
            state = request.query_params.get("state", "new")
            state = state if state in reports.STATES else ""
            source = request.query_params.get("source", "")
            source = source if source in reports.SOURCES else ""
            rows = store.reports((state,) if state else None, source, limit=reportspage.SHOWN)
            body = reportspage.page(rows, state, source, datetime.now(UTC), triage_info())
        return respond(request, "Reports", body, signed, here="reports", up=("/settings", "Settings"))

    def triage_info() -> reportspage.Triage:
        """golem's triage of the reports about itself, for the Reports page (triage.py)."""
        jobs = []
        for job in store.jobs_from(TRIAGE, limit=5)[::-1]:
            runs = store.runs(job.id)
            step = next((s.id for s in store.steps(job.id) if s.kind == "agent"), None)
            jobs.append((job, sum(r["cost_usd"] or 0 for r in runs), step))
        mine = store.proposals(INTERNAL, limit=40)
        return reportspage.Triage(
            store.get("triage") == triage.ON, jobs, [p for p in mine if p.state == "open"], [p for p in mine if p.state != "open"][:20]
        )

    async def fix_page(request: Request) -> Response:
        """A fix to golem Riggy took from its triage: the prompt for a Claude Code session in golem's repo."""
        p = store.proposal(request.path_params["proposal_id"])
        if p is None or p.slug != INTERNAL:
            return HTMLResponse("No such proposal.", 404)
        if not signed_in(request):
            body = pages.signin_card(f"/reports/proposals/{p.id}")
        else:
            prompt = triage.fix_prompt(p, store.proposal_reports(p.id), settings.url("golem"), scheduler.version())
            body = reportspage.fix_page(p, prompt)
        return respond(request, p.title, body, signed_in(request), here="reports", up=("/reports", "Reports"))

    async def projects_page(request: Request) -> Response:
        """Every live project as a card, for anyone (gallery.py)."""
        palette, tags = colors(), {t.name: t for t in store.tags()}
        entries = []
        for project, live_at in store.live_projects():
            card = store.card(project.slug) or fallback(project)
            image = f"/cards/{card.image}?v={quote(card.updated_at)}" if card.image else None
            entries.append(
                gallery.Entry(project, card, settings.project_url(project.slug), image, live_at, palette.get(project.slug, 1),
                              tags.get(project.tag))  # fmt: skip
            )
        html = gallery.page(entries, datetime.now(UTC), settings.org, list(tags.values()))
        return HTMLResponse(html, headers={"Cache-Control": "public, max-age=60"})

    async def card_image(request: Request) -> Response:
        name = request.path_params["name"]
        card = store.card(name.removesuffix(".jpg"))
        path = scheduler.cards.image_path(card) if card else None
        if path is None or path.name != name or not path.is_file():
            return Response("No such image.", 404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    async def option_image(request: Request) -> Response:
        """A picture of an option, or a design round's doc (options.collect and collect_design made
        the copies). Only those names, never sniffed as anything else; a doc in a sandbox (DOC_POLICY)."""
        name = request.path_params["name"]
        path = options.folder(settings, request.path_params["job_id"]) / name
        if options.is_doc(name) and path.is_file():
            headers = {"Content-Security-Policy": DOC_POLICY, "X-Content-Type-Options": "nosniff",
                       "Referrer-Policy": "no-referrer", "Cache-Control": "no-cache"}  # fmt: skip
            return FileResponse(path, media_type="text/html; charset=utf-8", headers=headers)
        if not options.is_image(name) or not path.is_file():
            return Response("No such image.", 404)
        headers = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"}
        return FileResponse(path, media_type=options.IMAGE_TYPES[path.suffix], headers=headers)

    def picks() -> projectpage.Picks:
        return projectpage.Picks([(w.key, w.name) for w in scheduler.library.choices(("change", "look"))], [m.name for m in store.modes()])

    def project_view(request: Request, project: Project, job: Job | None) -> Response:
        """A project's page, open on `job` (else the one it opens on: projectpage.chosen). A job
        asked for by its address is the side a phone shows; the project's address shows its list."""
        signed = signed_in(request)
        now = datetime.now(UTC)
        ctx = context(now)
        jobs = store.project_jobs(project.slug, limit=projectpage.JOBS_SHOWN)
        listed = {j.id for j in jobs}
        older = [j for j in store.jobs(ACTIVE, limit=1000) if j.slug == project.slug and j.id not in listed]  # still under way
        jobs = sorted(jobs + older, key=lambda j: (j.created_at, j.id), reverse=True)
        focus = "job" if job else "list"
        job = job or projectpage.chosen(jobs)
        lanes_ = [lane(j, project, ctx["order"].get(j.id), signed, ctx) for j in jobs]
        open_ = next((x for x in lanes_ if job and x.job.id == job.id), None) or (lane(job, project, ctx["order"].get(job.id), signed, ctx) if job else None)
        total = store.project_job_count(project.slug)
        flows_ = flow_names()
        back = projectpage.href(job) if job and focus == "job" else f"/p/{project.slug}"
        more = f"/p/{project.slug}/jobs" if total > len(jobs) else ""
        parts = {
            "jobs": projectpage.jobs_part(lanes_, job.id if job else None, flows_, now, total, more),
            "proposals": projectpage.proposals_part(project, store.proposals(project.slug, ("open",)), back) if signed else "",
        }
        details = ""
        if job:
            parts["head"] = projectpage.head_part(open_, flows_, signed, now)
            parts["steps"] = projectpage.steps_part(open_, signed, now, store.steps(job.id))
            parts["activity"] = projectpage.activity_part(store.job_messages(job.id) if signed else [], signed)
            details = projectpage.details(job, store.runs(job.id))
            live.forget({r["id"] for r in store.runs(job.id)[-1:]} if job.state in ACTIVE else set())
        links = {"Site": settings.project_url(project.slug), "Repo": settings.repo_url(project.slug)}
        body = projectpage.layout(project, parts, open_, signed, picks(), focus, details, links)
        up = (f"/p/{project.slug}", project.name) if focus == "job" else ("/p", "Projects")
        return respond(request, project.name, body, signed, here=project.slug, up=up, subnav=tabs(project, "jobs", signed), parts=parts)

    async def project_page(request: Request) -> Response:
        project = visible_project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        return project_view(request, project, None)

    async def project_job_page(request: Request) -> Response:
        project = visible_project(request.path_params["slug"])
        job = store.job(request.path_params["job_id"])
        if project is None or job is None or job.slug != project.slug:
            return HTMLResponse("No such job.", 404)
        return project_view(request, project, job)

    async def project_jobs_page(request: Request) -> Response:
        """All of a project's jobs, to search (jobhistory.py): its page lists only the latest."""
        project = visible_project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        signed = signed_in(request)
        jobs, search, flows_ = store.job_list(project.slug), jobhistory.Search.of(request.query_params), flow_names()
        found = jobhistory.results(project, jobs, search, flows_, datetime.now(UTC))
        body = jobhistory.form(project, jobs, search, flows_) + pages.region("found", found)
        return respond(request, f"{project.name}: all jobs", body, signed, here=project.slug, up=(f"/p/{project.slug}", project.name),
                       subnav=tabs(project, "all", signed), parts={"found": found})  # fmt: skip

    async def project_ask(request: Request) -> Response:
        """A request on a project's page: always a job of its own (bot.ask)."""
        form = await _form(request)
        project = visible_project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        form["back"] = f"/p/{project.slug}"
        if not signed_in(request) or not _same_site(request):
            return _back(form, "Sign in first.")
        if not bot:
            return _back(form, "golem can't take that here.")
        text = form.get("text", "").strip()[:8000]
        mode = form.get("mode", "auto")
        if not text:
            return _back(form, "Write something first.")
        if mode not in ("auto", "") and store.mode(mode) is None:
            return _back(form, f"There's no mode called {mode}.")
        job = await bot.ask(project, text, workflow=form.get("workflow", ""), mode=None if mode == "auto" else mode)
        return _back({"back": projectpage.href(job)}, f"Started job #{job.id}: {plan.title(job)}.")

    async def project_history(request: Request) -> Response:
        """What was said in a project's conversation, before jobs had their own activity: read-only."""
        project = visible_project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        signed = signed_in(request)
        if not signed:
            return _back({"back": f"/p/{project.slug}"}, "Sign in (Settings) to see what was said.")
        said, earlier = _latest(request, store.messages(project.slug, limit=_shown(request) + 1))
        body = "<p class=hint>Everything said about this project, as it was said. Each job's own activity is on its page.</p>"
        body += conversation.thread(said, "Nothing said here yet.", earlier)
        return respond(request, f"{project.name}: history", body, signed, here=project.slug, up=(f"/p/{project.slug}", project.name),
                       subnav=tabs(project, "history", signed))  # fmt: skip

    async def project_settings(request: Request) -> Response:
        project = visible_project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        signed = signed_in(request)
        if not signed:
            return _back({"back": f"/p/{project.slug}"}, "Sign in (Settings) to change a project.")
        data, jobs = project_lanes(project, signed)
        decided = store.proposals(project.slug, ("accepted", "rejected"), limit=20)
        page = conversation.settings_page(project, data[0], jobs, [t.name for t in store.tags()], decided, more=data[1:],
                                          modes=[m.name for m in store.modes()])  # fmt: skip
        return respond(request, f"{project.name}: settings", page, signed, here=project.slug, up=(f"/p/{project.slug}", project.name),
                       subnav=tabs(project, "settings", signed))  # fmt: skip

    # --- making a project (newproject.py) ------------------------------------

    def new_choices() -> newproject.Choices:
        flows_ = scheduler.library.choices(("new",))
        fallback = scheduler.library.fallback("new")
        return newproject.Choices([(w.key, w.name, " ".join(w.ingest.split())) for w in flows_], fallback.key if fallback else "",
                                  [m.name for m in store.modes()], plan.mode_for(store, None), [t.name for t in store.tags()], settings.org)  # fmt: skip

    def new_page(request: Request, new: newproject.Filled | None = None, adopting: newproject.Filled | None = None, status: int = 200) -> Response:
        signed = signed_in(request)
        if not signed:
            return _back({"back": "/settings"}, "Sign in to start a project.")
        response = respond(request, "New project", newproject.page(new_choices(), new, adopting), signed, here="new", up=("/p", "Projects"))
        response.status_code = status
        return response

    def picked_mode(value: str, errors: dict[str, str]) -> str | None:
        """A form's mode: None for golem's default ("auto"), "" for none, else one of the modes."""
        if value == "auto":
            return None
        if value and store.mode(value) is None:
            errors["mode"] = f"There's no mode called {value}."
        return value

    async def new_form(request: Request) -> Response:
        return new_page(request)

    async def new_create(request: Request) -> Response:
        """The New project form: a project as Riggy named it, and its first job (bot.new_project)."""
        form = await _form(request)
        if not signed_in(request) or not _same_site(request):
            return _back({"back": "/new"}, "Sign in first.")
        if not bot:
            return _back({"back": "/"}, "golem can't make projects here.")
        name, idea = " ".join(form.get("name", "").split())[:60], form.get("idea", "").strip()[:8000]
        slug = (form.get("slug", "").strip().lower() or intake.slugify(name)) if name or form.get("slug") else ""
        errors: dict[str, str] = {}
        if not name:
            errors["name"] = "Name it."
        if not idea:
            errors["idea"] = "Say what it's for."
        if slug and not intake.valid_slug(slug):
            errors["slug"] = "An address is 2 to 31 lowercase letters, digits and hyphens, and not a reserved word."
        elif slug and await bot.slug_taken(slug):
            errors["slug"] = f"{slug} is taken, by a project or a repo. {await intake.unique_slug(slug, bot.slug_taken)} is free."
        mode = picked_mode(form.get("mode", "auto"), errors)
        model, effort = form.get("model", ""), form.get("effort", "")
        if model and model not in MODELS or effort and effort not in EFFORTS:
            errors[""] = "Pick a model and effort from the lists."
        if errors:
            return new_page(request, newproject.Filled(form, errors), status=400)
        try:
            job = await bot.new_project(name, idea, slug=slug, workflow=form.get("workflow", ""), mode=mode, tag=form.get("tag", ""),
                                        model=model, effort=effort)  # fmt: skip
        except ValueError:
            return new_page(request, newproject.Filled(form, {"slug": f"{slug} was just taken. Try another."}), status=400)
        return _back({"back": projectpage.href(job)}, f"Started {name}: {plan.title(job)}.")

    async def new_adopt(request: Request) -> Response:
        """Adopting a repo golem didn't make (bot.adopt): what's in the way, if anything, comes back here."""
        form = await _form(request)
        if not signed_in(request) or not _same_site(request):
            return _back({"back": "/new"}, "Sign in first.")
        if not bot:
            return _back({"back": "/"}, "golem can't adopt repos here.")
        errors: dict[str, str] = {}
        repo = form.get("repo", "").strip().lower()
        if not repo:
            errors["repo"] = "Which repo?"
        mode = picked_mode(form.get("mode", "auto"), errors)
        if errors:
            return new_page(request, adopting=newproject.Filled(form, errors), status=400)
        job, said = await bot.adopt(repo, form.get("words", ""), name=form.get("name", ""), mode=mode, tag=form.get("tag", ""))
        if job is None:
            return new_page(request, adopting=newproject.Filled(form, {"": said}), status=400)
        return _back({"back": projectpage.href(job)}, said)

    async def inbox_page(request: Request) -> Response:
        """Where golem's own conversation was: what it says now is on Now."""
        return RedirectResponse("/", 302)

    async def say(request: Request) -> Response:
        """Where writing to golem went: requests are a project page's Ask box, new projects the New project form."""
        form = await _form(request)
        return _back({"back": _safe(form.get("back", "")) or "/"}, "Ask on a project's page, or start a project with New project.")

    # --- the installable app and its notifications (pwa.py, push.py) ---------

    async def manifest(request: Request) -> Response:
        return JSONResponse(pwa.manifest(), media_type="application/manifest+json")

    async def icon(request: Request) -> Response:
        size = request.path_params["size"]
        if size not in pwa.ICON_SIZES:
            return Response("No such icon.", 404)
        png = pwa.icon(size, light=request.url.path.endswith("-light.png"))
        # Kept for good at its versioned URL (pwa.version); an hour at any other.
        keep = "public, max-age=31536000, immutable" if request.query_params.get("v") == pwa.version() else "public, max-age=3600"
        return Response(png, media_type="image/png", headers={"Cache-Control": keep})

    async def favicon(request: Request) -> Response:
        return Response(brand.FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    async def font(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in pages.FONT_FILES:
            return Response("No such font.", 404)
        return FileResponse(pages.FONTS / name, media_type="font/woff2", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    async def static(request: Request) -> Response:
        """The graph's scripts (flows.py): by name only, cached for a year (their URLs carry a hash)."""
        name = request.path_params["name"]
        if name not in pages.STATIC or not (pages.STATIC_DIR / name).is_file():
            return Response("No such file.", 404)
        return FileResponse(pages.STATIC_DIR / name, media_type="text/javascript",
                            headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})  # fmt: skip

    async def workflows_list(request: Request) -> Response:
        signed = signed_in(request)
        if not signed:
            return RedirectResponse("/settings?msg=Sign%20in%20to%20see%20the%20workflows.", 303)
        return respond(request, "Workflows", flows.workflows_page(store, signed), signed, here="workflows", subnav=flows.tabs("workflows"))

    async def blocks_page(request: Request) -> Response:
        """Workflows → Blocks: golem's stage blocks and the text of each of their parts."""
        signed = signed_in(request)
        if not signed:
            return RedirectResponse("/settings?msg=Sign%20in%20to%20see%20the%20workflows.", 303)
        return respond(request, "Blocks", flows.blocks_page(scheduler.library.blocks), signed, here="workflows", subnav=flows.tabs("blocks"))

    async def stage_page(request: Request) -> Response:
        signed = signed_in(request)
        if not signed:
            return RedirectResponse("/settings?msg=Sign%20in%20to%20see%20the%20workflows.", 303)
        key = request.path_params["stage"]
        if key not in stages.catalog():
            return HTMLResponse("No such stage.", 404)
        title = f"{stages.catalog()[key].label} blocks"
        return respond(request, title, flows.stage_page(scheduler.library.blocks, key), signed, here="workflows",
                       up=("/workflows/blocks", "Blocks"), subnav=flows.tabs("blocks"))  # fmt: skip

    async def workflow_told(request: Request) -> Response:
        """What a block of a workflow tells its agent, as saved: the editor's "What it's told"."""
        signed = signed_in(request)
        if not signed:
            return HTMLResponse("Sign in first.", 403)
        wf, mode = one_workflow(request)
        if wf is None:
            return HTMLResponse("No such workflow.", 404)
        body = flows.told(store, scheduler.library.blocks, wf, mode, request.query_params.get("node", ""))
        if request.query_params.get("part") == "sheet":
            return HTMLResponse(body, headers={"Cache-Control": "no-store"})
        return respond(request, wf.name, f"<article class=card>{body}</article>", signed, here="workflows", up=(f"/workflows/{wf.id}", wf.name))

    async def modes_page(request: Request) -> Response:
        """The modes requests can ask for, what each is for, and golem's default."""
        signed = signed_in(request)
        if not signed:
            return RedirectResponse("/settings?msg=Sign%20in%20to%20see%20the%20workflows.", 303)
        return respond(request, "Modes", flows.modes_card(store), signed, here="workflows", subnav=flows.tabs("modes"))

    def one_workflow(request: Request) -> tuple[Workflow | None, str]:
        wf = store.workflow(request.path_params["workflow_id"])
        mode = request.query_params.get("mode", "")
        return wf, mode if wf and store.tab(wf.id, mode) else ""

    async def workflow_page(request: Request) -> Response:
        signed = signed_in(request)
        if not signed:
            return RedirectResponse("/settings?msg=Sign%20in%20to%20see%20the%20workflows.", 303)
        wf, mode = one_workflow(request)
        if wf is None:
            return HTMLResponse("No such workflow.", 404)
        src = f"/workflows/{wf.id}.json?edit=1" + (f"&mode={quote(mode)}" if mode else "")
        return respond(request, wf.name, flows.workflow_page(store, wf, mode), signed, here="workflows", up=("/workflows", "Workflows"),
                       stage=flows.stage(src, "edit"))  # fmt: skip

    async def workflow_json(request: Request) -> Response:
        if not signed_in(request):
            return JSONResponse({"error": "Sign in first."}, 403)
        wf, mode = one_workflow(request)
        if wf is None:
            return JSONResponse({"error": "No such workflow."}, 404)
        found = flows.editor_data(store, wf, mode) if request.query_params.get("edit") else flows.workflow_view(store, wf, mode)
        return JSONResponse(found, headers={"Cache-Control": "no-store"})

    async def workflow_save(request: Request) -> Response:
        """The editor's Save: the tab as Riggy left it, with what's wrong with it (it saves anyway;
        new jobs keep its last working state until it's fixed)."""
        if not signed_in(request) or not _same_site(request):
            return JSONResponse({"error": "Sign in first."}, 403)
        wf = store.workflow(request.path_params["workflow_id"])
        if wf is None:
            return JSONResponse({"error": "No such workflow."}, 404)
        body = await _json(request)
        if body is None:
            return JSONResponse({"error": "That's too big, or not JSON."}, 400)
        mode = str(body.get("mode") or "")
        tab = store.tab(wf.id, mode)
        if tab is None:
            return JSONResponse({"error": f"{wf.name} has no {mode or 'default'} tab."}, 404)
        if body.get("base") and body["base"] != engine.digest(tab.graph):
            return JSONResponse({"error": "It was saved somewhere else since you opened it: reload to see that, then make your change again.", "stale": True}, 409)
        try:
            graph = workflows.clean(body.get("graph"))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        report = scheduler.library.save(wf, mode, graph)
        saved = store.tab(wf.id, mode)
        return JSONResponse({"ok": report.ok, "problems": [p.as_dict() for p in report.errors],
                             "warnings": [p.as_dict() for p in report.warnings], "base": engine.digest(saved.graph),
                             "graph": saved.graph, "hints": about.hints(plan.blocks_in_order(saved.graph))})  # fmt: skip

    async def service_worker(request: Request) -> Response:
        return Response(pwa.service_worker(), media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    async def push_key(request: Request) -> Response:
        return JSONResponse({"key": push.public_key()})

    async def push_change(request: Request) -> Response:
        """Turning notifications on or off for a device, or sending it a test. Signed in, from this site."""
        if not signed_in(request) or not _same_site(request):
            return JSONResponse({"error": "Sign in first."}, 403)
        try:
            body = json.loads((await request.body())[:10_000] or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "Expected JSON."}, 400)
        match request.path_params["what"]:
            case "subscribe":
                endpoint, keys = body.get("endpoint"), body.get("keys") or {}
                if not (isinstance(endpoint, str) and endpoint.startswith("https://") and isinstance(keys, dict)
                        and isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str)):  # fmt: skip
                    return JSONResponse({"error": "That isn't a push subscription."}, 400)
                store.add_subscription(endpoint, {"p256dh": keys["p256dh"], "auth": keys["auth"]}, str(body.get("label", ""))[:200])
            case "unsubscribe":
                store.forget_subscription(str(body.get("endpoint", "")))
            case "test":
                sent = await push.to_all({"title": "golem", "body": "Notifications work.", "url": "/inbox", "tag": "test"})
                return JSONResponse({"sent": sent})
            case _:
                return JSONResponse({"error": "No such thing."}, 404)
        return JSONResponse({"ok": True})

    async def job_page(request: Request) -> Response:
        """Where a job's page was: it's its project's page, open on it, now."""
        job = store.job(request.path_params["job_id"])
        if job is None or store.project(job.slug) is None:
            return HTMLResponse("No such job.", 404)
        return RedirectResponse(projectpage.href(job), 302)

    async def login(request: Request) -> Response:
        form = await _form(request)
        if not _same_site(request):
            return HTMLResponse("Sign in from the status page.", 403)
        ok = auth.check_password(form.get("password", ""))
        if ok is None:
            return _back(form, "Too many wrong passwords. Try again in a while.")
        if not ok:
            return _back(form, "Wrong password.")
        seconds = KEEP_SECONDS if form.get("keep") else TOKEN_SECONDS
        response = _back(form, "Signed in for 90 days on this device." if form.get("keep") else "Signed in for 24 hours.")
        response.set_cookie(COOKIE, auth.token(seconds), max_age=seconds, httponly=True, samesite="lax", secure=secure)
        return response

    async def logout(request: Request) -> Response:
        response = _back(await _form(request), "Signed out.")
        response.delete_cookie(COOKIE, httponly=True, samesite="lax", secure=secure)
        return response

    async def act(request: Request) -> Response:
        form = await _form(request, 120_000)  # a block's text, URL-encoded, can be long
        if not signed_in(request) or not _same_site(request):
            return _back(form, "Sign in first.")
        action, job, step = form.get("action", ""), _int(form.get("job")), _int(form.get("step"))
        slug, value, position = form.get("slug", ""), form.get("value", ""), _int(form.get("position"))
        try:
            match action:
                case "skip":
                    message = await control.skip(job, step, position)
                case "restore":
                    message = await control.restore(job, step, position)
                case "move":
                    message = await control.move(job, value)
                case "pause" | "resume":
                    message = await control.pause(slug, action == "pause")
                case "pause-all" | "resume-all":
                    message = control.pause_all(action == "pause-all")
                case "workers":
                    message = control.workers(value)
                case "kill":
                    message = await control.kill(job)
                case "setting":
                    message = await control.setting(slug, form.get("name", ""), value)
                case "proposal-kind":
                    message = await control.proposal_kind(slug, form.get("name", ""), value == "on")
                case "step-setting":
                    message = await control.step_setting(job, step, position, form.get("name", ""), value)
                case "default":
                    message = control.default_setting(form.get("kind", ""), form.get("name", ""), value)
                case "reset-defaults":
                    message = control.reset_defaults()
                case "tag-add":
                    message = control.add_tag(value)
                case "tag-rename":
                    message = control.rename_tag(form.get("name", ""), value)
                case "tag-color":
                    message = control.tag_color(form.get("name", ""), value)
                case "tag-delete":
                    message = control.delete_tag(form.get("name", ""))
                case "mode-add":
                    message = control.add_mode(value, form.get("ingest", ""))
                case "mode-rename":
                    message = control.rename_mode(form.get("name", ""), value)
                case "mode-ingest":
                    message = control.mode_ingest(form.get("name", ""), value)
                case "mode-delete":
                    message = control.delete_mode(form.get("name", ""))
                case "fallback-mode":
                    message = control.fallback_mode(value)
                case "fallback-workflow":
                    message = control.fallback_workflow(form.get("start", ""), value)
                case "workflow-new":
                    wf = control.new_workflow(value, form.get("start", ""))
                    return RedirectResponse(f"/workflows/{wf.id}?msg=" + quote(f"Made {wf.name}: wire its blocks from Start to End."), 303)
                case "workflow-settings":
                    message = control.workflow_settings(_int(form.get("workflow")), form.get("name", ""), form.get("handle", ""), form.get("ingest", ""))
                case "workflow-archive":
                    message = control.archive_workflow(_int(form.get("workflow")), value == "on")
                case "workflow-reset":
                    message = control.reset_workflow(_int(form.get("workflow")))
                case "tab-add":
                    message = control.add_tab(_int(form.get("workflow")), value, form.get("from", ""))
                    wf = store.workflow(_int(form.get("workflow")))
                    if wf and store.tab(wf.id, value):
                        return RedirectResponse(f"/workflows/{wf.id}?mode={quote(value)}&msg=" + quote(message), 303)
                case "tab-delete":
                    message = control.drop_tab(_int(form.get("workflow")), value)
                case "note":
                    message = await control.note(job, value, step, position)
                case "rename":
                    message = control.rename(slug, value)
                case "block-text":
                    message = control.block_text(form.get("stage", ""), form.get("part", ""), value)
                case "block-reset":
                    message = control.block_reset(form.get("stage", ""), form.get("part", ""))
                case "project-slug" | "project-delete":
                    project = visible_project(slug)
                    if project is None or bot is None:
                        raise ControlError("That project isn't there.")
                    if action == "project-slug":
                        message = await bot.move(project, value)
                        if moved := store.project(value.strip().lower()) if store.project(slug) is None else None:
                            return _back({"back": f"/p/{moved.slug}/settings"}, message)
                    elif value.strip().lower() != slug:
                        raise ControlError(f"To delete it, type {slug} exactly.")
                    else:
                        message = await bot.delete(project)
                        if store.project(slug) is None:
                            return _back({"back": "/p"}, message)
                case "upgrade":
                    message = control.upgrade()
                case "check-version":
                    message = await control.check_version()
                case "auto-upgrade":
                    message = control.auto_upgrade(value == "on")
                case "proposal":
                    if value not in ("accept", "reject"):
                        raise ControlError("Thumbs up or down?")
                    message = await control.decide(_int(form.get("proposal")), value == "accept", form.get("reason", ""))
                    taken = store.proposal(_int(form.get("proposal")))
                    if taken and taken.slug == INTERNAL and taken.state == "accepted":  # a fix to golem: its prompt, to copy
                        return _back({"back": f"/reports/proposals/{taken.id}"}, message)
                case "triage":
                    message = control.set_triage(value == "on")
                case _:
                    message = "Unknown action."
        except ControlError as e:
            message = str(e)
        return _back(form, message)

    projects_host = Router(
        routes=[
            Route("/", projects_page),
            Route("/cards/{name}", card_image),
            Route("/fonts/{name}", font),
            Route("/favicon.svg", favicon),
            Route("/healthz", healthz),
        ]
    )
    return Starlette(
        routes=[
            # projects.<domain> shows only the projects page; everything else is golem.<domain>.
            Host(f"projects.{settings.base_domain}", projects_host),
            Route("/healthz", healthz),
            Route("/projects", projects_page),
            Route("/cards/{name}", card_image),
            Route("/", index),
            Route("/manifest.webmanifest", manifest),
            Route("/icon-{size:int}.png", icon),
            Route("/icon-{size:int}-light.png", icon),
            Route("/fonts/{name}", font),
            Route("/favicon.svg", favicon),
            Route("/sw.js", service_worker),
            Route("/push/key", push_key),
            Route("/push/{what}", push_change, methods=["POST"]),
            Route("/p", projects_list),
            Route("/p/{slug}", project_page),
            Route("/p/{slug}/settings", project_settings),
            Route("/p/{slug}/jobs", project_jobs_page),
            Route("/p/{slug}/jobs/{job_id:int}", project_job_page),
            Route("/p/{slug}/ask", project_ask, methods=["POST"]),
            Route("/p/{slug}/history", project_history),
            Route("/new", new_form),
            Route("/new", new_create, methods=["POST"]),
            Route("/new/adopt", new_adopt, methods=["POST"]),
            Route("/settings", settings_page),
            Route("/inbox", inbox_page),
            Route("/say", say, methods=["POST"]),
            Route("/jobs/{job_id:int}", job_page),
            Route("/jobs/{job_id:int}/planned/{position:int}", planned_page),
            Route("/jobs/{job_id:int}/options/{name}", option_image),
            Route("/steps/{step_id:int}", step_page),
            Route("/insights", insights_page),
            Route("/reports", reports_page),
            Route("/reports/proposals/{proposal_id:int}", fix_page),
            Route("/static/{name}", static),
            Route("/workflows", workflows_list), Route("/workflows/modes", modes_page), Route("/workflows/{workflow_id:int}", workflow_page),
            Route("/workflows/blocks", blocks_page), Route("/workflows/blocks/{stage}", stage_page),
            Route("/workflows/{workflow_id:int}/told", workflow_told),
            Route("/workflows/{workflow_id:int}.json", workflow_json),
            Route("/workflows/{workflow_id:int}/save", workflow_save, methods=["POST"]),
            Route("/login", login, methods=["POST"]),
            Route("/logout", logout, methods=["POST"]),
            Route("/act", act, methods=["POST"]),
        ]
    )


def _dt_seconds(at: str | None) -> float:
    try:
        return datetime.fromisoformat(at).timestamp() if at else 0.0
    except ValueError:
        return 0.0


async def _json(request: Request, cap: int = 600_000) -> dict | None:
    """A JSON body, if it is one and isn't too big."""
    raw = await request.body()
    if len(raw) > cap:
        return None
    try:
        found = json.loads(raw)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


async def _form(request: Request, cap: int = 20_000) -> dict[str, str]:
    """A plain URL-encoded form, read without needing python-multipart."""
    body = (await request.body())[:cap].decode(errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def _same_site(request: Request) -> bool:
    """A change has to come from this site's own page, not a form on someone else's."""
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return origin is None  # no Origin: a same-site navigation by an older browser
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1] == host


def _safe(back: str) -> str:
    """A path on this site to go back to, or ""."""
    return back if back.startswith("/") and not back.startswith("//") and "\\" not in back else ""


def _back(form: dict[str, str], message: str) -> RedirectResponse:
    back = _safe(form.get("back", "/")) or "/"
    path, _, anchor = back.partition("#")
    return RedirectResponse(f"{path}?msg={quote(message)}" + (f"#{anchor}" if anchor else ""), status_code=303)


def _int(value: str | None) -> int:
    return int(value) if value and value.isdigit() else 0


def _shown(request: Request) -> int:
    """How many of a conversation's latest messages to show: SHOWN, or more after "Show earlier messages"."""
    return min(max(_int(request.query_params.get("show")), SHOWN), 5000)


def _latest(request: Request, said: list[Said]) -> tuple[list[Said], str]:
    """The latest messages to show, from one more than that, and where to see earlier ones if there are any."""
    shown = _shown(request)
    return said[-shown:], f"?show={shown + SHOWN}" if len(said) > shown else ""


def _state(store: Store, job: Job, steps) -> str:
    step = plan.current(steps)
    project = store.project(job.slug)
    if job.state not in ACTIVE:
        return {"done": "done", "killed": "stopped", "failed": "failed"}.get(job.state, job.state)
    if project and project.paused:
        return "paused"
    match job.state:
        case "waiting" if step and step.kind == "checkin":
            return "waiting for you to try it"
        case "waiting" if step and step.kind == "choose":
            return "waiting for your pick"
        case "waiting":
            return "waiting for you"
        case "running":
            return f"working: {plan.label(step)}" if step else "working"
        case "parked" if not store.steps(job.id):
            return "starting shortly"
        case "parked" if step and step.state == "running" and not step.error:
            return "waiting for CI"
        case "parked":
            return "retrying soon"
    return turns.waiting_for(store, job) or "queued"


def _first_paragraph(text: str) -> str:
    return " ".join(text.strip().split("\n\n")[0].split())
