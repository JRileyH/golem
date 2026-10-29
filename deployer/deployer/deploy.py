"""The deploy, stop, delete, status and logs operations behind the API."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx

from . import compose
from .config import Settings
from .source import Source
from .validate import Rejected, check_sha, check_slug, validate_compose

MAX_LOG_LINES = 1000


class DeployFailed(Exception):
    def __init__(self, message: str, logs: str = ""):
        super().__init__(message)
        self.message = message
        self.logs = logs


@dataclass
class State:
    sha: str
    services: list[str]
    public_service: str
    port: int
    deployed_at: str


class Deployer:
    def __init__(
        self,
        settings: Settings,
        source: Source,
        docker: compose.Docker,
        verify: Callable[[str], str | None] | None = None,
    ):
        self.settings = settings
        self.source = source
        self.docker = docker
        self.verify = verify or self.verify_through_traefik

    def deploy(self, slug: str, sha: str) -> dict:
        check_slug(slug)
        check_sha(sha)
        text = self.source.compose_file(slug, sha)
        if self.settings.require_checks:
            failing = self.source.failing_checks(slug, sha)
            if failing:
                raise Rejected([f"checks have not all passed on {sha[:7]}: {', '.join(failing)}"])
        project = validate_compose(text, self.settings.policy)

        try:
            self.docker.up(slug, text, compose.override(slug, sha, self.settings.org, project))
        except compose.ComposeError as e:
            raise DeployFailed(str(e), e.output) from None

        # Record what's running before verifying, so /logs and /stop work on a broken deploy.
        state = State(sha, list(project.services), project.public_service, project.public_port, _now())
        compose.write_atomic(self.state_path(slug), json.dumps(asdict(state), indent=2))
        route = compose.route(slug, project.public_port, self.settings.base_domain)
        compose.write_atomic(self.route_path(slug), compose.dump(route))

        problem = self.verify(slug)
        if problem:
            raise DeployFailed(f"{self.url(slug)} did not respond: {problem}", self.docker.logs(slug, None, 50))
        return {"url": self.url(slug), "sha": sha}

    def stop(self, slug: str) -> dict:
        self.require_deployed(check_slug(slug))
        try:
            self.docker.down(slug)
        except compose.ComposeError as e:
            raise DeployFailed(str(e), e.output) from None
        self.route_path(slug).unlink(missing_ok=True)
        return {"stopped": slug}

    def delete(self, slug: str) -> dict:
        """Stop the project and delete its volumes, route and deploy files: all trace of it but the repo."""
        if not self.docker.dir(check_slug(slug)).is_dir():
            raise Rejected([f"`{slug}` has not been deployed"])
        try:
            self.docker.down(slug, volumes=True)
        except compose.ComposeError as e:
            raise DeployFailed(str(e), e.output) from None
        self.route_path(slug).unlink(missing_ok=True)
        shutil.rmtree(self.docker.dir(slug))
        return {"deleted": slug}

    def projects(self) -> dict:
        """Every project with deploy files, running or stopped."""
        root = self.docker.projects_dir
        return {"projects": sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []}

    def status(self, slug: str) -> dict:
        check_slug(slug)
        state = self.load_state(slug)
        return {
            "slug": slug,
            "deployed": asdict(state) if state else None,
            "route": self.url(slug) if self.route_path(slug).exists() else None,
            "containers": self.docker.ps(slug) if state else [],
        }

    def logs(self, slug: str, service: str | None, tail: str | None) -> dict:
        state = self.require_deployed(check_slug(slug))
        if service is not None and service not in state.services:
            raise Rejected([f"`{slug}` has no service `{service}` (services: {', '.join(state.services)})"])
        if tail is None:
            lines = 100
        elif tail.isdigit() and 1 <= int(tail) <= MAX_LOG_LINES:
            lines = int(tail)
        else:
            raise Rejected([f"tail must be a number from 1 to {MAX_LOG_LINES}"])
        return {"logs": self.docker.logs(slug, service, lines)}

    def verify_through_traefik(self, slug: str, attempts: int = 30, delay: float = 2) -> str | None:
        """Request the site through Traefik's internal entrypoint until it answers. Returns the
        last problem, or None."""
        problem = "no response"
        for _ in range(attempts):
            try:
                r = httpx.get(
                    self.settings.traefik_url,
                    headers={"Host": f"{slug}.{self.settings.base_domain}"},
                    timeout=5,
                )
                # 404 is what Traefik returns before it has loaded the route.
                if r.status_code < 500 and r.status_code != 404:
                    return None
                problem = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                problem = type(e).__name__
            time.sleep(delay)
        return problem

    def require_deployed(self, slug: str) -> State:
        state = self.load_state(slug)
        if state is None:
            raise Rejected([f"`{slug}` has not been deployed"])
        return state

    def load_state(self, slug: str) -> State | None:
        path = self.state_path(slug)
        return State(**json.loads(path.read_text())) if path.exists() else None

    def state_path(self, slug: str):
        return self.docker.dir(slug) / "deployed.json"

    def route_path(self, slug: str):
        return self.settings.routes_dir / f"{slug}.yml"

    def url(self, slug: str) -> str:
        """Traefik's port locally; on a server, https on the usual port, whether golem's
        Traefik holds 443 or a proxy in front of it does."""
        if self.settings.base_domain == "localhost":
            port = self.settings.edge_port.rpartition(":")[2]  # EDGE_PORT may be address:port
            return f"http://{slug}.localhost" + (f":{port}" if port else "")
        return f"https://{slug}.{self.settings.base_domain}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
