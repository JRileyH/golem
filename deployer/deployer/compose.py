"""Running a validated project with docker compose, and routing it."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

from .validate import APPS_NETWORK, Project

LOGGING = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}


class ComposeError(Exception):
    def __init__(self, step: str, output: str):
        super().__init__(f"docker compose {step} failed")
        self.step = step
        self.output = output


def project_name(slug: str) -> str:
    return f"golem-{slug}"


def network_alias(slug: str) -> str:
    # Dotted, so it can't collide with any service or container name (see validate.SERVICE_NAME_RE).
    return f"{slug}.golem"


def override(slug: str, sha: str, org: str, project: Project) -> dict:
    """What the deployer layers over every project: ignored by any Traefik on the box that reads
    Docker labels, logs rotated,
    public service on the apps network, and the project's own images pinned to the commit."""
    services = {
        name: {
            "labels": {"traefik.enable": "false", "golem.slug": slug},
            "restart": "unless-stopped",
            "logging": LOGGING,
        }
        for name in project.services
    }
    # CI tags images with the commit sha. A compose file can't know its own
    # commit, so agents reference their images untagged and the deployer pins them.
    own = f"ghcr.io/{org}/{slug}"
    for name, image in project.images.items():
        if image == own or (image.startswith(own + "/") and ":" not in image and "@" not in image):
            services[name]["image"] = f"{image}:{sha}"
    services[project.public_service]["networks"] = {
        "default": {},
        APPS_NETWORK: {"aliases": [network_alias(slug)]},
    }
    return {"services": services, "networks": {APPS_NETWORK: {"external": True, "name": APPS_NETWORK}}}


def route(slug: str, port: int, base_domain: str) -> dict:
    """Traefik dynamic config: <slug>.<base_domain> -> the public service. Routes name no
    entrypoint: they get Traefik's default ones (HTTPS on a server, HTTP locally)."""
    return _route(slug, base_domain, f"http://{network_alias(slug)}:{port}")


def golem_route(base_domain: str) -> dict:
    """golem's own hosts: the status page at golem.<base_domain> and the projects page at
    projects.<base_domain>. Both are reserved slugs, so no project can claim them."""
    routes = _route("golem", base_domain, "http://golem:8000")
    routes["http"]["routers"]["projects"] = {"rule": f"Host(`projects.{base_domain}`)", "service": "golem"}
    return routes


def _route(name: str, base_domain: str, url: str) -> dict:
    return {
        "http": {
            "routers": {name: {"rule": f"Host(`{name}.{base_domain}`)", "service": name}},
            "services": {name: {"loadBalancer": {"servers": [{"url": url}]}}},
        }
    }


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False)


class Docker:
    def __init__(self, projects_dir: Path, pull_policy: str):
        self.projects_dir = projects_dir
        self.pull_policy = pull_policy

    def dir(self, slug: str) -> Path:
        return self.projects_dir / slug

    def login(self, registry: str, user: str, token: str) -> None:
        result = subprocess.run(
            ["docker", "login", registry, "--username", user, "--password-stdin"],
            input=token,
            capture_output=True,
            text=True,
            env=_clean_env(),
            timeout=30,
        )
        if result.returncode != 0:
            raise SystemExit(f"docker login {registry} failed")

    def up(self, slug: str, compose_text: str, override_data: dict) -> None:
        d = self.dir(slug)
        write_atomic(d / "compose.yml", compose_text)
        write_atomic(d / "override.yml", dump(override_data))
        if self.pull_policy == "always":
            self._check("pull", self._compose(slug, "pull"))
        self._check("up", self._compose(slug, "up", "--detach", "--remove-orphans"))

    def down(self, slug: str, volumes: bool = False) -> None:
        # Data goes only with volumes=True, from /delete. Every volume a project can declare
        # has no options (validate.py), so they are all golem-<slug>_* and nobody else's.
        self._check("down", self._compose(slug, "down", "--remove-orphans", *(["--volumes"] if volumes else [])))

    def ps(self, slug: str) -> list[dict]:
        result = self._compose(slug, "ps", "--all", "--format", "json")
        if result.returncode != 0:
            return []
        containers = []
        for line in result.stdout.splitlines():
            parsed = json.loads(line) if line.strip() else []
            containers.extend(parsed if isinstance(parsed, list) else [parsed])
        return [{"service": c.get("Service"), "state": c.get("State"), "status": c.get("Status")} for c in containers]

    def logs(self, slug: str, service: str | None, tail: int) -> str:
        args = ["logs", "--no-color", "--tail", str(tail)]
        if service:
            args.append(service)
        result = self._compose(slug, *args)
        return result.stdout + result.stderr

    def _compose(self, slug: str, *args: str) -> subprocess.CompletedProcess:
        d = self.dir(slug)
        files = [a for f in ("compose.yml", "override.yml") if (d / f).exists() for a in ("--file", str(d / f))]
        return subprocess.run(
            ["docker", "compose", "--project-name", project_name(slug), *files, *args],
            capture_output=True,
            text=True,
            env=_clean_env(),
            cwd=d if d.exists() else None,
            timeout=600,
        )

    @staticmethod
    def _check(step: str, result: subprocess.CompletedProcess) -> None:
        if result.returncode != 0:
            raise ComposeError(step, (result.stdout + result.stderr)[-4000:])


def _clean_env() -> dict[str, str]:
    """Compose runs with almost nothing in its environment, so a project can't read the deployer's tokens."""
    keep = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG")
    return {k: os.environ[k] for k in keep if k in os.environ}
