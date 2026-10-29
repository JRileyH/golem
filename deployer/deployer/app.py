"""HTTP API. Internal network only; every call but /healthz needs the shared secret.

/delete destroys data and /upgrade rebuilds golem from GitHub (upgrade.py), so they need a
second secret that only golem holds: workers call the API with the first one and never see
the admin secret.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .audit import Audit
from .deploy import Deployer, DeployFailed
from .source import GitHubError
from .upgrade import Upgrader
from .validate import Rejected

SECRET_HEADER = "X-Deployer-Secret"
ADMIN_SECRET_HEADER = "X-Deployer-Admin-Secret"


def create_app(deployer: Deployer, audit: Audit, secret: str, admin_secret: str = "", upgrader: Upgrader | None = None) -> Starlette:
    # One change at a time: deploys, stops and deletes are serialized.
    lock = asyncio.Lock()

    async def handle(
        request: Request,
        action: str,
        op: Callable[[], dict],
        fields: dict,
        mutates: bool,
        auth: tuple[str, str] = (SECRET_HEADER, secret),
    ) -> JSONResponse:
        header, expected = auth
        if not hmac.compare_digest(request.headers.get(header, "").encode(), expected.encode()):
            audit.write(action, "unauthorized", **fields)
            return JSONResponse({"ok": False, "reasons": [f"missing or wrong {header}"]}, 401)
        try:
            if mutates:
                async with lock:
                    result = await run_in_threadpool(op)
            else:
                result = await run_in_threadpool(op)
        except Rejected as e:
            audit.write(action, "rejected", **fields, reasons=e.reasons)
            return JSONResponse({"ok": False, "reasons": e.reasons}, 422)
        except DeployFailed as e:
            audit.write(action, "failed", **fields, error=e.message)
            return JSONResponse({"ok": False, "error": e.message, "logs": e.logs}, 502)
        except GitHubError as e:
            audit.write(action, "failed", **fields, error=str(e))
            return JSONResponse({"ok": False, "error": str(e)}, 502)
        except Exception as e:
            audit.write(action, "error", **fields, error=type(e).__name__)
            return JSONResponse({"ok": False, "error": f"internal error ({type(e).__name__})"}, 500)
        audit.write(action, "ok", **fields)
        return JSONResponse({"ok": True, **result})

    async def body(request: Request, keys: set[str]) -> dict:
        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or set(data) != keys:
            raise Rejected([f"body must be a JSON object with exactly: {', '.join(sorted(keys))}"])
        return data

    async def deploy(request: Request) -> JSONResponse:
        try:
            data = await body(request, {"slug", "sha"})
        except Rejected as e:
            return await handle(request, "deploy", _raise(e), {}, mutates=True)
        fields = {"slug": _short(data["slug"]), "sha": _short(data["sha"])}
        return await handle(request, "deploy", lambda: deployer.deploy(data["slug"], data["sha"]), fields, mutates=True)

    async def stop(request: Request) -> JSONResponse:
        try:
            data = await body(request, {"slug"})
        except Rejected as e:
            return await handle(request, "stop", _raise(e), {}, mutates=True)
        return await handle(request, "stop", lambda: deployer.stop(data["slug"]), {"slug": _short(data["slug"])}, mutates=True)

    async def delete(request: Request) -> JSONResponse:
        if not admin_secret:  # never compare against an empty secret
            audit.write("delete", "refused", reason="no admin secret")
            return JSONResponse({"ok": False, "reasons": ["deleting is off: DEPLOYER_ADMIN_SECRET is not set"]}, 403)
        auth = (ADMIN_SECRET_HEADER, admin_secret)
        try:
            data = await body(request, {"slug"})
        except Rejected as e:
            return await handle(request, "delete", _raise(e), {}, mutates=True, auth=auth)
        fields = {"slug": _short(data["slug"])}
        return await handle(request, "delete", lambda: deployer.delete(data["slug"]), fields, mutates=True, auth=auth)

    async def projects(request: Request) -> JSONResponse:
        return await handle(request, "projects", deployer.projects, {}, mutates=False)

    async def status(request: Request) -> JSONResponse:
        slug = request.query_params.get("slug")
        return await handle(request, "status", lambda: deployer.status(slug), {"slug": _short(slug)}, mutates=False)

    async def logs(request: Request) -> JSONResponse:
        q = request.query_params
        slug, service, tail = q.get("slug"), q.get("service"), q.get("tail")
        fields = {"slug": _short(slug), "service": _short(service)}
        return await handle(request, "logs", lambda: deployer.logs(slug, service, tail), fields, mutates=False)

    async def version(request: Request) -> JSONResponse:
        """golem's own version: what's running, what GitHub's main has (fetched if ?fetch=1), the last upgrade."""
        fetch = request.query_params.get("fetch") == "1"
        if upgrader is None:
            return await handle(request, "version", lambda: {"enabled": False, "reason": "no upgrader"}, {}, mutates=False)
        return await handle(request, "version", lambda: upgrader.version(fetch), {"fetch": fetch}, mutates=fetch)

    async def upgrade(request: Request) -> JSONResponse:
        if not admin_secret or upgrader is None:  # never compare against an empty secret
            audit.write("upgrade", "refused", reason="no admin secret")
            return JSONResponse({"ok": False, "reasons": ["upgrading is off: DEPLOYER_ADMIN_SECRET is not set"]}, 403)
        return await handle(request, "upgrade", upgrader.upgrade, {}, mutates=True, auth=(ADMIN_SECRET_HEADER, admin_secret))

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/deploy", deploy, methods=["POST"]),
            Route("/stop", stop, methods=["POST"]),
            Route("/delete", delete, methods=["POST"]),
            Route("/projects", projects, methods=["GET"]),
            Route("/status", status, methods=["GET"]),
            Route("/logs", logs, methods=["GET"]),
            Route("/version", version, methods=["GET"]),
            Route("/upgrade", upgrade, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def _raise(error: Exception) -> Callable[[], dict]:
    def op() -> dict:
        raise error

    return op


def _short(value: object) -> object:
    """Request values go into the audit log as given, but bounded."""
    if isinstance(value, str) and len(value) > 80:
        return value[:80] + "…"
    return value
