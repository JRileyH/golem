"""Deployer settings, read once from the environment at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .validate import Policy


@dataclass(frozen=True)
class Settings:
    secret: str
    base_domain: str
    git_source: str  # "github" or "local"
    org: str
    github_token: str  # fine-grained, read-only: contents and actions
    registry_user: str
    registry_token: str  # classic, read:packages only: ghcr.io doesn't accept fine-grained tokens
    require_checks: bool
    pull_policy: str  # "always" on a server; "missing" locally, where images are built by hand
    data_dir: Path  # project files, deploy state and the audit log
    routes_dir: Path  # Traefik's dynamic config
    git_dir: Path  # bare repos, local mode only
    traefik_url: str  # Traefik's internal entrypoint, for checking a site answers
    policy: Policy
    edge_port: str = ""  # local mode's Traefik isn't on 80; only used in reported URLs
    admin_secret: str = ""  # for /delete and /upgrade; golem holds it, workers never do. Empty turns them off.
    golem_dir: Path | None = None  # golem's own checkout, at its host path (upgrade.py); None: upgrades off
    golem_repo_token: str = ""  # read-only, the golem repo only; "" for a public repo

    @classmethod
    def from_env(cls, env: dict[str, str] = os.environ) -> Settings:
        secret = env.get("DEPLOYER_SECRET", "")
        if len(secret) < 32:
            raise SystemExit("DEPLOYER_SECRET must be set (at least 32 characters)")
        admin_secret = env.get("DEPLOYER_ADMIN_SECRET", "")
        if admin_secret and (len(admin_secret) < 32 or admin_secret == secret):
            raise SystemExit("DEPLOYER_ADMIN_SECRET must be at least 32 characters and differ from DEPLOYER_SECRET")
        git_source = env.get("DEPLOYER_GIT_SOURCE", "github")
        if git_source not in ("github", "local"):
            raise SystemExit("DEPLOYER_GIT_SOURCE must be github or local")
        github_token = env.get("DEPLOYER_GITHUB_TOKEN", "")
        registry_user = env.get("DEPLOYER_REGISTRY_USER", "")
        registry_token = env.get("DEPLOYER_REGISTRY_TOKEN", "")
        if git_source == "github":
            for name, value in [
                ("DEPLOYER_GITHUB_TOKEN", github_token),
                ("DEPLOYER_REGISTRY_USER", registry_user),
                ("DEPLOYER_REGISTRY_TOKEN", registry_token),
            ]:
                if not value:
                    raise SystemExit(f"{name} must be set when DEPLOYER_GIT_SOURCE=github")
        org = env.get("GITHUB_ORG", "")
        base_domain = env.get("BASE_DOMAIN", "")
        if not org or not base_domain:
            raise SystemExit("BASE_DOMAIN and GITHUB_ORG must be set")
        return cls(
            secret=secret,
            base_domain=base_domain,
            git_source=git_source,
            org=org,
            github_token=github_token,
            registry_user=registry_user,
            registry_token=registry_token,
            require_checks=env.get("DEPLOYER_REQUIRE_CHECKS", "true").lower() == "true",
            pull_policy="always" if git_source == "github" else "missing",
            data_dir=Path(env.get("DEPLOYER_DATA_DIR", "/data")),
            routes_dir=Path(env.get("DEPLOYER_ROUTES_DIR", "/routes")),
            git_dir=Path(env.get("DEPLOYER_GIT_DIR", "/git")),
            traefik_url=env.get("DEPLOYER_TRAEFIK_URL", "http://traefik:8080"),
            policy=Policy(
                org=org,
                service_mem_cap_mb=int(env.get("DEPLOYER_SERVICE_MEM_CAP_MB", "512")),
                project_mem_cap_mb=int(env.get("DEPLOYER_PROJECT_MEM_CAP_MB", "1024")),
            ),
            edge_port=env.get("EDGE_PORT", ""),
            admin_secret=admin_secret,
            golem_dir=Path(env["GOLEM_HOST_DIR"]) if env.get("GOLEM_HOST_DIR") else None,
            golem_repo_token=env.get("GOLEM_REPO_TOKEN", ""),
        )
