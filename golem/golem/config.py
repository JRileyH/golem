"""Golem settings, read once from the environment at startup."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path

from .store import EFFORTS, MODELS, MODES


@dataclass(frozen=True)
class Settings:
    base_domain: str
    edge_port: str  # Traefik's host port when it doesn't hold 80 and 443; only local links use it
    harness: str  # "claude" or "fake"
    org: str
    deployer_url: str
    state_dir: Path  # SQLite, run logs, lessons.md
    workspace_dir: Path  # one checkout per project
    process_dir: Path  # playbooks, skills and prompts, read-only
    # Signs Riggy in on the web app. No default: it's set in .env or golem won't start.
    web_password: str
    # What a new project gets unless the dictation or a command says otherwise.
    default_mode: str = "standard"
    default_model: str = "opus"
    default_effort: str = "high"
    # What CI builds project images for: this box's own architecture.
    platform: str = "linux/amd64"

    @classmethod
    def from_env(cls, env: dict[str, str] = os.environ) -> Settings:
        base_domain = env.get("BASE_DOMAIN", "")
        org = env.get("GITHUB_ORG", "")
        if not base_domain or not org:
            raise SystemExit("BASE_DOMAIN and GITHUB_ORG must be set")
        web_password = env.get("GOLEM_WEB_PASSWORD", "")
        if not web_password:
            raise SystemExit("GOLEM_WEB_PASSWORD must be set")
        harness = env.get("HARNESS", "claude")
        if harness not in ("claude", "fake"):
            raise SystemExit("HARNESS must be claude or fake")
        defaults = {
            "default_mode": _choice(env, "GOLEM_DEFAULT_MODE", "standard", MODES),
            "default_model": _choice(env, "GOLEM_MODEL", "opus", MODELS),
            "default_effort": _choice(env, "GOLEM_EFFORT", "high", EFFORTS),
        }
        return cls(
            base_domain=base_domain,
            edge_port=env.get("EDGE_PORT", ""),
            harness=harness,
            org=org,
            deployer_url=env.get("DEPLOYER_URL", "http://deployer:8000"),
            state_dir=Path(env.get("GOLEM_STATE_DIR", "/var/lib/golem")),
            workspace_dir=Path(env.get("GOLEM_WORKSPACE_DIR", "/workspace")),
            process_dir=Path(env.get("GOLEM_PROCESS_DIR", "/opt/golem")),
            web_password=web_password,
            platform=env.get("GOLEM_PLATFORM") or _platform(),
            **defaults,
        )

    def url(self, host: str) -> str:
        """Public URL for `<host>.<base domain>`: Traefik's port locally; on a server, https on
        the usual port, whether golem's Traefik holds 443 or a proxy in front of it does."""
        if self.base_domain == "localhost":
            port = self.edge_port.rpartition(":")[2]  # EDGE_PORT may be address:port
            return f"http://{host}.localhost" + (f":{port}" if port and port != "80" else "")
        return f"https://{host}.{self.base_domain}"

    def project_url(self, slug: str) -> str:
        return self.url(slug) + "/"

    def repo_url(self, slug: str) -> str:
        return f"https://github.com/{self.org}/{slug}"

    def job_logs(self, job_id: int) -> Path:
        """Where a job's runs are logged, one compressed event stream per run."""
        return self.state_dir / "jobs" / str(job_id)


def _platform() -> str:
    """The Docker platform of the machine golem runs on (a container shares the host's)."""
    return "linux/arm64" if platform.machine() in ("aarch64", "arm64") else "linux/amd64"


def _choice(env: dict[str, str], name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = env.get(name) or default
    if value not in allowed:
        raise SystemExit(f"{name} must be one of {', '.join(allowed)}")
    return value
