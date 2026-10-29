"""The web app at golem.<base domain>, and the health check.

Three tabs (home.py): Home, what needs Riggy and what's being worked on; Projects, the
conversations; Settings. Anyone can look at what's under way: each project's job, its
pieces of work and each step's details (with the pictures of any options Riggy is picking
from), and where the time and usage go (insights.py).

Signed in with GOLEM_WEB_PASSWORD, it's how Riggy talks to golem. Each project has a
page with its conversation (conversation.py) and a box to write in; Home and golem's own
conversation take new ideas. What's written goes to the bot (`/say`, bot.py). The web app's
own controls are in control.py: skipping optional steps, reordering the queue, pausing,
stopping, models and effort (per step, project or kind of step) and notes.

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
from html import escape
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Host, Route, Router

from . import conversation, gallery, home, insights, options, pages, plan, pwa
from .cards import fallback
from .config import Settings
from .control import Control, ControlError
from .live import LiveRuns
from .pages import LaneData, SheetData
from .push import Push
from .scheduler import Scheduler
from .store import ACTIVE, Job, Project, Said, Step, Store
from .talk import INBOX, STATUS

if TYPE_CHECKING:
    from .bot import Bot

COOKIE = "golem_token"
TOKEN_SECONDS = 24 * 3600
KEEP_SECONDS = 90 * 24 * 3600  # on a device Riggy keeps signed in: his phone's home-screen app
# What the project page's controls can ask the bot to do, as !commands.
COMMANDS = {"options", "mode", "rename", "slug", "delete"}
MAX_FAILURES = 10  # wrong passwords in FAILURE_WINDOW before sign-in is refused for a while
FAILURE_WINDOW = 15 * 60
SHOWN = 40  # a conversation's latest messages on its page; "Show earlier messages" adds as many again


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
            skippable={s.id for s in steps if s.id and active and signed and control.skippable(job, s.id)},
            restorable={s.id for s in steps if s.id and active and signed and control.restorable(job, s.id)},
            position=position,
            site=settings.project_url(project.slug),
            repo=settings.repo_url(project.slug),
            color=ctx["colors"].get(project.slug, 1),
            etas=ctx["etas"].get(job.id, {}) if job else {},
            tallies=insights.per_step(store, job.id) if job else {},
            known=ctx["known"],
        )

    def queue_order() -> list[Job]:
        """Unfinished jobs in the order the worker will take them, the running one first."""
        jobs = store.jobs(ACTIVE)
        running = [j for j in jobs if j.state == "running"]
        ready = [j for j in jobs if j.state in ("queued", "parked") and not store.project(j.slug).paused]
        return running + ready

    def context(now: datetime) -> dict:
        """What every lane needs: the queue, colors, usual step times and the forecast."""
        order = queue_order()
        known = insights.estimates(store)
        planned = [(j, plan.planned(settings, store, j)) for j in order]
        etas = {} if scheduler.paused else insights.forecast(planned, known, now)
        return {"order": {j.id: i for i, j in enumerate(order, start=1)}, "colors": colors(), "known": known, "etas": etas}

    def lanes(signed: bool, now: datetime) -> tuple[list[LaneData], list[LaneData]]:
        """Projects with a job under way, in the queue's order, and the others with their last job."""
        ctx = context(now)
        order = ctx["order"]
        active, busy = [], set()
        jobs = sorted(store.jobs(ACTIVE), key=lambda j: (order.get(j.id, 999), j.id))
        for job in jobs:
            if job.slug in busy:
                continue
            busy.add(job.slug)
            active.append(lane(job, store.project(job.slug), order.get(job.id), signed, ctx))
        idle = []
        recent = store.jobs(limit=500)
        for project in store.projects():
            if project.slug not in busy:
                last = next((j for j in recent if j.slug == project.slug), None)
                idle.append(lane(last, project, None, signed, ctx))
        live.forget({r["id"] for j in jobs for r in store.runs(j.id)[-1:]})
        return active, idle

    def threads(signed: bool) -> list[home.Thread]:
        """Golem's own conversation, then each project's, the latest first; each opens at its latest message."""
        palette = colors()
        recent = store.jobs(limit=500)
        out = []
        for project in store.projects():
            job = store.active_job(project.slug) or next((j for j in recent if j.slug == project.slug), None)
            said = store.messages(project.slug, limit=1)
            last = said[-1] if said else None
            at = max(x for x in (last.at if last else "", job.updated_at if job else "", project.created_at) if x is not None)
            data = LaneData(project, job, plan.planned(settings, store, job) if job else [], _state(store, job, []) if job else "idle",
                            [], [], set(), set(), None, "", "", palette.get(project.slug, 1))  # fmt: skip
            out.append(home.Thread(f"/p/{project.slug}#end", project.name, data.color, pages.badge(data), last, project.summary, at))
        out.sort(key=lambda t: t.at, reverse=True)
        said = store.messages(INBOX, limit=1) + store.messages(STATUS, limit=1)
        last = max(said, key=lambda m: m.id) if said else None
        return [home.Thread("/inbox#end", "golem", 0, "", last, "New ideas, and golem's news.", last.at if last else ""), *out]

    def job_html(job: Job, signed: bool) -> str:
        project = store.project(job.slug)
        now = datetime.now(UTC)
        ctx = context(now)
        data = lane(job, project, ctx["order"].get(job.id), signed, ctx)
        body = pages.status_card(data, signed, now) + f"<section class=card><h2>Every step</h2>{pages.steps_lists(data, signed, now)}</section>"
        runs = "".join(
            f"<tr><td>{escape(r['phase'])}</td><td>{escape(r['model'])}</td><td>{escape(str(r['outcome'] or 'running'))}</td>"
            f"<td>{r['num_turns'] or ''}</td><td>{_tokens(r)}</td><td>{_cost(r)}</td><td>{r['started_at'][:16].replace('T', ' ')}</td></tr>"
            for r in store.runs(job.id)
        )
        return (
            body + "<h3 class=sub>Agent runs</h3><div class=runs><table><tr><th>Run</th><th>Model</th><th>Outcome</th>"
            f"<th>Turns</th><th>Tokens in/out</th><th>Usage</th><th>Started</th></tr>{runs}</table></div>"
        )

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
        if step.kind == "agent":
            engine = plan.engine(settings, store, project, step)
            inherited = plan.engine(settings, store, project, replace(step, model=None, effort=None))
        if step.kind == "agent":
            does = step.problem.splitlines()[0] if step.name == "fix" and step.problem else ""
            if not does:
                try:
                    does = _first_paragraph(plan.load(settings, job.playbook).instructions(step.name))
                except KeyError:
                    does = ""
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
                notes=store.step_notes(step.id) if step.id else [],
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

    def respond(request: Request, title: str, body: str, signed: bool, bottom: str = "", tab: str = "",
                up: tuple[str, str] | None = None, action: str = "") -> Response:  # fmt: skip
        if request.query_params.get("part") == "lanes":
            return HTMLResponse(body, headers={"Cache-Control": "no-store"})
        flash = request.query_params.get("msg", "")[:300]
        html = pages.page(title, body, signed, scheduler.paused, flash, bottom=bottom, tab=tab, up=up, action=action)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "harness": settings.harness})

    async def index(request: Request) -> Response:
        signed = signed_in(request)
        now = datetime.now(UTC)
        bottom = conversation.composer(INBOX, "idle") if signed and bot else ""
        return respond(request, "golem", home.home(*lanes(signed, now), signed, now), signed, bottom, tab="home")

    async def projects_list(request: Request) -> Response:
        signed = signed_in(request)
        return respond(request, "Projects", home.projects(threads(signed), signed, datetime.now(UTC)), signed, tab="projects")

    async def settings_page(request: Request) -> Response:
        signed = signed_in(request)
        since = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
        body = home.settings(signed, scheduler.paused, control.kinds(), store.defaults(),
                             (settings.default_model, settings.default_effort), insights.report(store, since),
                             store.tags(), store.tag_counts())  # fmt: skip
        return respond(request, "Settings", body, signed, tab="settings")

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
        body = f"<article class=card>{body}</article><p><a href='/jobs/{job.id}'>The whole job</a></p>"
        return respond(request, plan.label(step), body, signed, up=(f"/jobs/{job.id}", f"Job #{job.id}"))

    async def insights_page(request: Request) -> Response:
        signed = signed_in(request)
        raw = request.query_params.get("days", "30")
        days = int(raw) if raw.isdigit() and 0 < int(raw) <= 3650 else None
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
        report = insights.report(store, since)
        body = pages.insights(report, insights.findings(report), days)
        return respond(request, "Insights", body, signed, up=("/settings", "Settings"))

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
        """A picture of an option (options.collect made the copy). Only those names, and never sniffed as anything else."""
        name = request.path_params["name"]
        path = options.folder(settings, request.path_params["job_id"]) / name
        if not options.is_image(name) or not path.is_file():
            return Response("No such image.", 404)
        headers = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"}
        return FileResponse(path, media_type=options.IMAGE_TYPES[path.suffix], headers=headers)

    async def project_page(request: Request) -> Response:
        project = store.project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        signed = signed_in(request)
        bottom = conversation.composer(project.slug, conversation.state_of(store.active_job(project.slug))) if signed and bot else ""
        data, _ = project_lane(project, signed)
        said, earlier = _latest(request, store.messages(project.slug, limit=_shown(request) + 1)) if signed else ([], "")
        body = conversation.project_page(data, said, signed, datetime.now(UTC), earlier)
        action = f"<a href='/p/{escape(project.slug)}/settings' aria-label='Settings for {escape(project.name)}'>Settings</a>" if signed else ""
        return respond(request, project.name, body, signed, bottom, up=("/p", "Projects"), action=action)

    async def project_settings(request: Request) -> Response:
        project = store.project(request.path_params["slug"])
        if project is None:
            return HTMLResponse("No such project.", 404)
        signed = signed_in(request)
        if not signed:
            return _back({"back": f"/p/{project.slug}"}, "Sign in (Settings) to change a project.")
        data, jobs = project_lane(project, signed)
        page = conversation.settings_page(project, data, jobs, [t.name for t in store.tags()])
        return respond(request, f"{project.name}: settings", page, signed,
                       up=(f"/p/{project.slug}", project.name))  # fmt: skip

    async def inbox_page(request: Request) -> Response:
        signed = signed_in(request)
        shown = _shown(request) + 1
        said, earlier = _latest(request, sorted(store.messages(INBOX, shown) + store.messages(STATUS, shown), key=lambda m: m.id)) if signed else ([], "")
        bottom = conversation.composer(INBOX, "idle") if signed and bot else ""
        return respond(request, "golem", conversation.inbox_page(said, signed, earlier), signed, bottom, up=("/p", "Projects"))

    async def say(request: Request) -> Response:
        """Something written in a conversation, handed to the bot."""
        form = await _form(request)
        channel, command, text = form.get("channel", ""), form.get("command", ""), form.get("text", "").strip()
        back = "/inbox" if channel == INBOX else f"/p/{channel}"
        form["back"] = _safe(form.get("back", "")) or back + "#end"
        if not signed_in(request) or not _same_site(request):
            return _back(form, "Sign in first.")
        if not bot or (channel != INBOX and store.project(channel) is None):
            return _back({"back": "/"}, "golem can't take that here.")
        if not text or (command and command not in COMMANDS):
            return _back(form, "Write something first.")
        if command == "delete" and text != channel:
            return _back({"back": back}, f"To delete it, type {channel} exactly.")
        before = {p.slug for p in store.projects()}
        await bot.handle(channel, f"!{command} {text}" if command else text[:8000])
        made = [p for p in store.projects() if p.slug not in before]
        if channel == INBOX and made:  # a new idea became a project: on to its page
            return RedirectResponse(f"/p/{made[0].slug}#end", status_code=303)
        if store.project(channel) is None and channel != INBOX:  # deleted, or moved to a new slug
            moved = store.project(text.lower()) if command == "slug" else None
            return RedirectResponse(f"/p/{moved.slug}#end" if moved else "/inbox#end", status_code=303)
        return RedirectResponse(form["back"], status_code=303)

    # --- the installable app and its notifications (pwa.py, push.py) ---------

    async def manifest(request: Request) -> Response:
        return JSONResponse(pwa.MANIFEST, media_type="application/manifest+json")

    async def icon(request: Request) -> Response:
        size = request.path_params["size"]
        if size not in pwa.ICON_SIZES:
            return Response("No such icon.", 404)
        return Response(pwa.icon(size), media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})

    async def service_worker(request: Request) -> Response:
        return Response(pwa.SERVICE_WORKER, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

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
        job = store.job(request.path_params["job_id"])
        if job is None:
            return HTMLResponse("No such job.", 404)
        signed = signed_in(request)
        project = store.project(job.slug)
        return respond(request, f"Job #{job.id}", job_html(job, signed), signed, up=(f"/p/{project.slug}", project.name))

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
        form = await _form(request)
        if not signed_in(request) or not _same_site(request):
            return _back(form, "Sign in first.")
        action, job, step = form.get("action", ""), _int(form.get("job")), _int(form.get("step"))
        slug, value, position = form.get("slug", ""), form.get("value", ""), _int(form.get("position"))
        try:
            match action:
                case "skip":
                    message = await control.skip(job, step)
                case "restore":
                    message = await control.restore(job, step)
                case "move":
                    message = await control.move(job, value)
                case "pause" | "resume":
                    message = await control.pause(slug, action == "pause")
                case "pause-all" | "resume-all":
                    message = control.pause_all(action == "pause-all")
                case "kill":
                    message = await control.kill(job)
                case "setting":
                    message = await control.setting(slug, form.get("name", ""), value)
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
                case "note":
                    message = await control.note(job, value, step, position)
                case _:
                    message = "Unknown action."
        except ControlError as e:
            message = str(e)
        return _back(form, message)

    projects_host = Router(
        routes=[
            Route("/", projects_page),
            Route("/cards/{name}", card_image),
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
            Route("/sw.js", service_worker),
            Route("/push/key", push_key),
            Route("/push/{what}", push_change, methods=["POST"]),
            Route("/p", projects_list),
            Route("/p/{slug}", project_page),
            Route("/p/{slug}/settings", project_settings),
            Route("/settings", settings_page),
            Route("/inbox", inbox_page),
            Route("/say", say, methods=["POST"]),
            Route("/jobs/{job_id:int}", job_page),
            Route("/jobs/{job_id:int}/planned/{position:int}", planned_page),
            Route("/jobs/{job_id:int}/options/{name}", option_image),
            Route("/steps/{step_id:int}", step_page),
            Route("/insights", insights_page),
            Route("/login", login, methods=["POST"]),
            Route("/logout", logout, methods=["POST"]),
            Route("/act", act, methods=["POST"]),
        ]
    )


async def _form(request: Request) -> dict[str, str]:
    """A plain URL-encoded form, read without needing python-multipart."""
    body = (await request.body())[:20_000].decode(errors="replace")
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
    return "queued"


def _first_paragraph(text: str) -> str:
    return " ".join(text.strip().split("\n\n")[0].split())


def _cost(run: dict) -> str:
    return f"${run['cost_usd']:.2f}" if run["cost_usd"] else ""


def _tokens(run: dict) -> str:
    if run["input_tokens"] is None and run["output_tokens"] is None:
        return ""
    return f"{run['input_tokens'] or 0:,} / {run['output_tokens'] or 0:,}"
